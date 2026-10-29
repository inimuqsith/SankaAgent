"""Skill lokal berbasis database: prosedur dengan progressive disclosure.

System prompt hanya memuat nama + deskripsi (indeks). Isi penuh dimuat lewat tool
`skill` saat tugas cocok. Format frontmatter mengikuti Hermes; indeks minimal
mengikuti L1 GenericAgent. File pendukung disimpan di tabel `skill_files`.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

import yaml
from sqlalchemy import select, update

from ..db import Database
from ..schema import skill_files, skill_usage, skills

log = logging.getLogger(__name__)

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_CONTENT = 18_000
MAX_DESCRIPTION = 200


def _clip(text: str, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Ambil YAML frontmatter; file tanpa frontmatter dianggap bukan skill."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    try:
        data = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return data, text[end + 4 :].lstrip("\n")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


class SkillStore:
    def __init__(self, db: Database, max_index_chars: int = 3000):
        self.db = db
        self.max_index_chars = max(200, int(max_index_chars))

    # ------------------------------------------------------------ query

    def names(self) -> list[str]:
        rows = self.db.fetch_all(
            select(skills.c.name).where(skills.c.archived.is_(False)).order_by(skills.c.name)
        )
        return [str(row["name"]) for row in rows]

    def get(self, name: str) -> Skill | None:
        slug = str(name or "").strip().lower()
        if not slug:
            return None
        row = self.db.fetch_one(
            select(skills.c.name, skills.c.description, skills.c.body).where(
                skills.c.name == slug, skills.c.archived.is_(False)
            )
        )
        if row is None:
            return None
        return Skill(
            name=str(row["name"]),
            description=str(row["description"] or ""),
            body=str(row["body"] or ""),
        )

    def index(self) -> str:
        rows = self.db.fetch_all(
            select(skills.c.name, skills.c.description)
            .where(skills.c.archived.is_(False))
            .order_by(skills.c.name)
        )
        if not rows:
            return ""
        usage = self._usage()
        lines: list[str] = []
        total = 0
        for row in rows:
            name = str(row["name"])
            line = f"- {name}: {_clip(row['description'], 120)}{self._usage_note(usage.get(name))}"
            if total + len(line) + 1 > self.max_index_chars:
                lines.append("- ...")
                break
            lines.append(line)
            total += len(line) + 1
        return "Local skills (procedures; load the full body with tool skill action=view):\n" + "\n".join(
            lines
        )

    # ----------------------------------------------------------- usage

    def _usage(self) -> dict[str, dict]:
        rows = self.db.fetch_all(select(skill_usage.c.skill, skill_usage.c.uses, skill_usage.c.last))
        return {
            str(row["skill"]): {"uses": int(row["uses"] or 0), "last": float(row["last"] or 0.0)}
            for row in rows
        }

    @staticmethod
    def _usage_note(item) -> str:
        if not isinstance(item, dict) or not int(item.get("uses") or 0):
            return ""
        try:
            when = datetime.fromtimestamp(float(item.get("last") or 0)).strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            when = "?"
        return f" (dipakai {int(item['uses'])}×, terakhir {when})"

    def _record_use(self, name: str) -> None:
        """Catat pemakaian supaya keputusan archive punya bukti, bukan tebakan."""
        now = time.time()
        with self.db.connect() as conn:
            self.db.upsert(
                conn,
                skill_usage,
                [{"skill": name, "uses": 1, "last": now}],
                ["skill"],
                {"uses": skill_usage.c.uses + 1, "last": now},
            )

    # ------------------------------------------------------------- api

    def view(self, name: str) -> str:
        skill = self.get(name)
        if skill is None:
            available = ", ".join(self.names()) or "(kosong)"
            raise KeyError(f"skill tidak ditemukan: {name}; tersedia: {available}")
        self._record_use(skill.name)
        return f'Skill "{skill.name}": {skill.description}\n\n{skill.body}'

    def read(self, name: str, file: str) -> str:
        skill = self.get(name)
        if skill is None:
            raise KeyError(f"skill tidak ditemukan: {name}")
        relative = str(file or "").strip()
        if not relative:
            raise ValueError("file kosong")
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts:
            raise PermissionError(f"file di luar skill: {relative}")
        clean = "/".join(part for part in path.parts if part not in ("", "."))
        if not clean:
            raise ValueError("file kosong")
        row = self.db.fetch_one(
            select(skill_files.c.content).where(
                skill_files.c.skill == skill.name, skill_files.c.path == clean
            )
        )
        if row is None:
            raise FileNotFoundError(f"file tidak ditemukan: {relative}")
        content = bytes(row["content"] or b"")
        if len(content) > MAX_CONTENT:
            raise ValueError(f"file terlalu besar: {relative}")
        return f"{clean}:\n{content.decode('utf-8', errors='replace')}"

    def save(self, name: str, description: str, body: str, *, overwrite: bool = False) -> str:
        slug = str(name or "").strip().lower()
        if not NAME.fullmatch(slug):
            raise ValueError("nama skill harus lowercase [a-z0-9_-], maksimal 64 karakter")
        desc = _clip(description, MAX_DESCRIPTION)
        text = str(body or "").strip()
        if not desc or not text:
            raise ValueError("description dan body wajib diisi")
        if len(text) > MAX_CONTENT:
            raise ValueError("body skill terlalu besar")
        now = time.time()
        with self.db.connect() as conn:
            existing = conn.execute(
                select(skills.c.archived).where(skills.c.name == slug)
            ).mappings().first()
            if existing is not None and not existing["archived"] and not overwrite:
                raise FileExistsError(f"skill sudah ada: {slug} (pakai overwrite=true)")
            self.db.upsert(
                conn,
                skills,
                [
                    {
                        "name": slug,
                        "description": desc,
                        "body": text,
                        "archived": False,
                        "created_at": now,
                        "updated_at": now,
                    }
                ],
                ["name"],
                {
                    "description": desc,
                    "body": text,
                    "archived": False,
                    "updated_at": now,
                },
            )
        return slug

    def archive(self, name: str) -> str:
        """Tandai skill pensiun (reversible); AI memutuskan skill tak berguna."""
        skill = self.get(name)
        if skill is None:
            raise KeyError(f"skill tidak ditemukan: {name}")
        with self.db.connect() as conn:
            conn.execute(
                update(skills)
                .where(skills.c.name == skill.name)
                .values(archived=True, updated_at=time.time())
            )
        return skill.name

    def close(self) -> None:
        """Tidak ada handle yang perlu ditutup; disediakan untuk simetri lifecycle."""
