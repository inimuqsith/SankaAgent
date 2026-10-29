"""Dokumen engine/soul/persona/role di DB dengan mirror file sebagai bootstrap."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from sqlalchemy import select

from ..config import Paths
from ..db import Database
from ..schema import documents

log = logging.getLogger(__name__)

ROLES_PREFIX = "role:"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class DocumentStore:
    def __init__(self, db: Database, paths: Paths, *, sync: bool = True) -> None:
        self.db = db
        self.paths = paths
        self.sync = sync

    # ------------------------------------------------------------- baca

    def get(self, name: str) -> str | None:
        found = self.db.fetch_one(select(documents).where(documents.c.name == name))
        return str(found["content"]) if found is not None else None

    def names(self, prefix: str = "") -> list[str]:
        statement = select(documents.c.name).order_by(documents.c.name)
        if prefix:
            statement = statement.where(documents.c.name.startswith(prefix))
        return [str(row["name"]) for row in self.db.fetch_all(statement)]

    # ------------------------------------------------------------ tulis

    def put(self, name: str, content: str, actor: str = "") -> None:
        row = {"name": name, "content": str(content), "updated_at": time.time()}
        with self.db.connect() as conn:
            self.db.upsert(conn, documents, [row], ["name"])

    def delete(self, name: str) -> None:
        with self.db.connect() as conn:
            conn.execute(documents.delete().where(documents.c.name == name))

    # ---------------------------------------------------------- seed/sync

    def _file_for(self, name: str) -> Path | None:
        if name == "engine":
            return self.paths.engine
        if name == "soul":
            return self.paths.soul
        if name == "persona":
            return self.paths.soul_md
        if name.startswith(ROLES_PREFIX):
            return self.paths.roles / f"{name.split(':', 1)[1]}.yaml"
        return None

    def _sources(self) -> list[tuple[str, Path]]:
        found: list[tuple[str, Path]] = [
            ("engine", self.paths.engine),
            ("soul", self.paths.soul),
            ("persona", self.paths.soul_md),
        ]
        if self.paths.roles.is_dir():
            for path in sorted(self.paths.roles.glob("*.yaml")):
                found.append((f"{ROLES_PREFIX}{path.stem}", path))
        return found

    def seed(self) -> int:
        """Isi dokumen yang belum ada dari file; dokumen yang sudah ada tidak disentuh."""
        if not self.sync:
            return 0
        existing = set(self.names())
        saved = 0
        for name, path in self._sources():
            if name in existing or not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                log.warning("gagal membaca %s", path, exc_info=True)
                continue
            self.put(name, content, actor="seed")
            saved += 1
        if saved:
            log.info("dokumen di-seed dari file: %d", saved)
        return saved

    def export_to_files(self) -> int:
        """Tulis mirror file dari dokumen DB (dipakai `sanka config export` dan settings)."""
        if not self.sync:
            return 0
        saved = 0
        for name in self.names():
            path = self._file_for(name)
            content = self.get(name)
            if path is None or content is None:
                continue
            _atomic_write(path, content)
            saved += 1
        return saved

    def import_from_files(self) -> int:
        """Baca file host lalu upsert dokumen (dipakai `sanka config import`)."""
        if not self.sync:
            return 0
        saved = 0
        for name, path in self._sources():
            if not path.is_file():
                continue
            try:
                self.put(name, path.read_text(encoding="utf-8"), actor="import")
            except OSError:
                log.warning("gagal membaca %s", path, exc_info=True)
                continue
            saved += 1
        return saved
