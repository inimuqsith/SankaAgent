"""Skill lokal: prosedur `SKILL.md` dengan progressive disclosure.

System prompt hanya memuat nama + deskripsi (indeks). Isi penuh dimuat lewat tool
`skill` saat tugas cocok. Format frontmatter mengikuti Hermes; indeks minimal
mengikuti L1 GenericAgent.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SKIP = frozenset({"__pycache__", "node_modules", ".git", ".venv", "venv", "site-packages"})
SUPPORT = frozenset({"references", "templates", "assets", "scripts"})
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
    path: Path

    @property
    def directory(self) -> Path:
        return self.path.parent


class SkillStore:
    def __init__(self, root: Path | str, max_index_chars: int = 3000):
        self.root = Path(root).expanduser()
        self.max_index_chars = max(200, int(max_index_chars))
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        self._skills: dict[str, Skill] = {}
        self._sig: tuple = ()
        self._refresh()

    def _signature(self) -> tuple:
        if not self.root.is_dir():
            return ()
        found = []
        for path in sorted(self.root.rglob("SKILL.md")):
            try:
                relative = path.relative_to(self.root)
            except ValueError:
                continue
            if any(part in SKIP or part.startswith(".") for part in relative.parts):
                continue
            if any(part in SUPPORT for part in relative.parts[:-1]):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            found.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(found)

    def _load(self) -> None:
        skills: dict[str, Skill] = {}
        for path in sorted(self.root.rglob("SKILL.md")):
            try:
                relative = path.relative_to(self.root)
            except ValueError:
                continue
            if any(part in SKIP or part.startswith(".") for part in relative.parts):
                continue
            if any(part in SUPPORT for part in relative.parts[:-1]):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                log.warning("gagal membaca skill %s: %s", path, exc)
                continue
            if len(text) > MAX_CONTENT:
                log.warning("skill %s terlalu besar (%d karakter); dilewati", path, len(text))
                continue
            data, body = parse_frontmatter(text)
            name = str(data.get("name") or path.parent.name).strip().lower()
            description = _clip(data.get("description") or "", MAX_DESCRIPTION)
            if not NAME.fullmatch(name) or not description or not body.strip():
                log.warning("skill %s tidak valid (name/description/body); dilewati", path)
                continue
            if name in skills:
                log.warning("skill duplikat %s dari %s; dilewati", name, path)
                continue
            skills[name] = Skill(name=name, description=description, body=body.strip(), path=path)
        self._skills = skills

    def _refresh(self) -> None:
        signature = self._signature()
        if signature != self._sig:
            self._load()
            self._sig = signature

    def names(self) -> list[str]:
        self._refresh()
        return sorted(self._skills)

    def get(self, name: str) -> Skill | None:
        self._refresh()
        return self._skills.get(str(name or "").strip().lower())

    def index(self) -> str:
        self._refresh()
        if not self._skills:
            return ""
        lines = []
        total = 0
        for skill in (self._skills[name] for name in sorted(self._skills)):
            line = f"- {skill.name}: {_clip(skill.description, 120)}"
            if total + len(line) + 1 > self.max_index_chars:
                lines.append("- ...")
                break
            lines.append(line)
            total += len(line) + 1
        return "Skill lokal (prosedur; muat penuh dengan tool skill action=view):\n" + "\n".join(lines)

    def view(self, name: str) -> str:
        skill = self.get(name)
        if skill is None:
            available = ", ".join(self.names()) or "(kosong)"
            raise KeyError(f"skill tidak ditemukan: {name}; tersedia: {available}")
        return f'Skill "{skill.name}": {skill.description}\nPath: {skill.path}\n\n{skill.body}'

    def read(self, name: str, file: str) -> str:
        skill = self.get(name)
        if skill is None:
            raise KeyError(f"skill tidak ditemukan: {name}")
        relative = str(file or "").strip()
        if not relative:
            raise ValueError("file kosong")
        base = skill.directory.resolve()
        target = (base / relative).resolve()
        if not target.is_relative_to(base):
            raise PermissionError(f"file di luar skill: {relative}")
        if not target.is_file():
            raise FileNotFoundError(f"file tidak ditemukan: {relative}")
        if target.stat().st_size > MAX_CONTENT:
            raise ValueError(f"file terlalu besar: {relative}")
        return f"{relative}:\n{target.read_text(encoding='utf-8', errors='replace')}"

    def save(self, name: str, description: str, body: str, *, overwrite: bool = False) -> Path:
        slug = str(name or "").strip().lower()
        if not NAME.fullmatch(slug):
            raise ValueError("nama skill harus lowercase [a-z0-9_-], maksimal 64 karakter")
        desc = _clip(description, MAX_DESCRIPTION)
        text = str(body or "").strip()
        if not desc or not text:
            raise ValueError("description dan body wajib diisi")
        if len(text) > MAX_CONTENT:
            raise ValueError("body skill terlalu besar")
        target = (self.root / slug / "SKILL.md").resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise PermissionError("path skill di luar direktori skill")
        if target.exists() and not overwrite:
            raise FileExistsError(f"skill sudah ada: {slug} (pakai overwrite=true)")
        target.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = yaml.safe_dump(
            {"name": slug, "description": desc},
            sort_keys=False,
            allow_unicode=True,
        ).strip()
        target.write_text(f"---\n{frontmatter}\n---\n\n{text}\n", encoding="utf-8")
        self._sig = ()
        self._refresh()
        return target

    def archive(self, name: str) -> Path:
        """Pindahkan skill ke `.archive/` (reversible); AI memutuskan skill tak berguna."""
        skill = self.get(name)
        if skill is None:
            raise KeyError(f"skill tidak ditemukan: {name}")
        root = self.root.resolve()
        folder = skill.directory.resolve()
        if not folder.is_relative_to(root) or folder == root:
            raise PermissionError(f"folder skill di luar root: {name}")
        archive_root = root / ".archive"
        archive_root.mkdir(parents=True, exist_ok=True)
        target = archive_root / f"{time.strftime('%Y%m%d-%H%M%S')}-{skill.name}"
        suffix = 1
        while target.exists():
            target = archive_root / f"{time.strftime('%Y%m%d-%H%M%S')}-{skill.name}-{suffix}"
            suffix += 1
        shutil.move(str(folder), str(target))
        self._sig = ()
        self._refresh()
        return target
