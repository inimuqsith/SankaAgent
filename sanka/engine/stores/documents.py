"""Engine/soul/persona/role documents in the DB, mirrored into config.yaml and roles.yaml.

The DB is the source of truth; files are seeds plus mirrors. Engine and soul metadata live
in the `engine`/`soul` sections of config.yaml, personas in SOUL.md, and every role in
roles.yaml, so one config folder holds the whole soul.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import yaml
from sqlalchemy import select

from ..config import Paths
from ..db import Database
from ..schema import documents

log = logging.getLogger(__name__)

ROLES_PREFIX = "role:"
ROLE_MODE = 0o600  # roles.yaml holds phone numbers


def _dump(body: object) -> str:
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True)


def _atomic_write(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if mode is not None:
        try:
            tmp.chmod(mode)
        except OSError:
            pass
    os.replace(tmp, path)


def _yaml_dict(text: str, label: str) -> dict:
    try:
        body = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        log.warning("%s is not valid YAML: %s", label, exc)
        return {}
    return body if isinstance(body, dict) else {}


class DocumentStore:
    def __init__(self, db: Database, paths: Paths, *, sync: bool = True) -> None:
        self.db = db
        self.paths = paths
        self.sync = sync

    # ------------------------------------------------------------- read

    def get(self, name: str) -> str | None:
        found = self.db.fetch_one(select(documents).where(documents.c.name == name))
        return str(found["content"]) if found is not None else None

    def names(self, prefix: str = "") -> list[str]:
        statement = select(documents.c.name).order_by(documents.c.name)
        if prefix:
            statement = statement.where(documents.c.name.startswith(prefix))
        return [str(row["name"]) for row in self.db.fetch_all(statement)]

    # ------------------------------------------------------------ write

    def put(self, name: str, content: str, actor: str = "") -> None:
        row = {"name": name, "content": str(content), "updated_at": time.time()}
        with self.db.connect() as conn:
            self.db.upsert(conn, documents, [row], ["name"])

    def delete(self, name: str) -> None:
        with self.db.connect() as conn:
            conn.execute(documents.delete().where(documents.c.name == name))

    # ---------------------------------------------------------- seed/sync

    def _config(self) -> dict:
        path = self.paths.config
        if not path.is_file():
            return {}
        try:
            return _yaml_dict(path.read_text(encoding="utf-8"), "config.yaml")
        except OSError:
            log.warning("failed to read %s", path, exc_info=True)
            return {}

    def _roles_file(self) -> dict:
        path = self.paths.roles
        if not path.is_file():
            return {}
        try:
            body = _yaml_dict(path.read_text(encoding="utf-8"), "roles.yaml")
        except OSError:
            log.warning("failed to read %s", path, exc_info=True)
            return {}
        roles = body.get("roles")
        return roles if isinstance(roles, dict) else {}

    def seed(self) -> int:
        """Fill missing documents from files; existing documents are left untouched."""
        if not self.sync:
            return 0
        existing = set(self.names())
        saved = 0
        raw = self._config()
        for name in ("engine", "soul"):
            if name in existing:
                continue
            body = raw.get(name)
            if isinstance(body, dict):
                self.put(name, _dump(body), actor="seed")
                saved += 1
        if "persona" not in existing and self.paths.soul_md.is_file():
            try:
                self.put("persona", self.paths.soul_md.read_text(encoding="utf-8"), actor="seed")
                saved += 1
            except OSError:
                log.warning("failed to read %s", self.paths.soul_md, exc_info=True)
        for name, body in self._roles_file().items():
            document = f"{ROLES_PREFIX}{name}"
            if document in existing:
                continue
            self.put(document, _dump(body), actor="seed")
            saved += 1
        if saved:
            log.info("documents seeded from files: %d", saved)
        return saved

    def export_to_files(self) -> int:
        """Write file mirrors from DB documents (`sanka config export` and the settings tool)."""
        if not self.sync:
            return 0
        saved = 0
        raw = self._config()
        changed = False
        for name in ("engine", "soul"):
            text = self.get(name)
            if text is None:
                continue
            body = _yaml_dict(text, f"document {name}")
            raw[name] = body
            changed = True
        if changed:
            _atomic_write(self.paths.config, _dump(raw))
            saved += 1
        persona = self.get("persona")
        if persona is not None:
            _atomic_write(self.paths.soul_md, persona)
            saved += 1
        roles = {
            name.split(":", 1)[1]: _yaml_dict(self.get(name) or "", name)
            for name in self.names(ROLES_PREFIX)
            if ":" in name
        }
        if roles:
            _atomic_write(self.paths.roles, _dump({"roles": roles}), mode=ROLE_MODE)
            saved += 1
        return saved

    def import_from_files(self) -> int:
        """Read host files and upsert documents (`sanka config import`)."""
        if not self.sync:
            return 0
        saved = 0
        raw = self._config()
        for name in ("engine", "soul"):
            body = raw.get(name)
            if isinstance(body, dict):
                self.put(name, _dump(body), actor="import")
                saved += 1
        if self.paths.soul_md.is_file():
            try:
                self.put("persona", self.paths.soul_md.read_text(encoding="utf-8"), actor="import")
                saved += 1
            except OSError:
                log.warning("failed to read %s", self.paths.soul_md, exc_info=True)
        for name, body in self._roles_file().items():
            self.put(f"{ROLES_PREFIX}{name}", _dump(body), actor="import")
            saved += 1
        return saved
