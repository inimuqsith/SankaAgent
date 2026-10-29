"""`sanka memory migrate`: pindahkan memory_legacy ke Mem0 dengan scope lintas chat."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from sqlalchemy import and_, select

from ..engine.config import ConfigError, load_config, load_host, resolve_database_url
from ..engine.db import Database
from ..engine.memory import build_memory
from ..engine.permissions import normalize
from ..engine.schema import memory_legacy
from ..gateways.whatsapp.schema import wa_lid_map

log = logging.getLogger(__name__)


def scope_for(scope: str, chat_id: str, lid_map: dict[str, str]) -> tuple[str, str, str, str]:
    """(scope, person_id, chat_id, audience) untuk satu baris memori lama."""
    if scope == "global":
        return "global", "", "", "shared"
    chat = str(chat_id or "")
    if chat.endswith("@lid"):
        mapped = lid_map.get(chat, "")
        person = normalize(mapped) if mapped else normalize(chat)
        return "person", person, chat, "private"
    if chat.endswith("@s.whatsapp.net"):
        return "person", normalize(chat), chat, "private"
    if chat:
        return "chat", "", chat, "group"
    return "global", "", "", "shared"


def _lid_map(db: Database) -> dict[str, str]:
    return {str(row["lid"]): str(row["pn"]) for row in db.fetch_all(select(wa_lid_map))}


def pending_rows(db: Database, limit: int | None = None) -> list[dict]:
    statement = (
        select(memory_legacy)
        .where(
            and_(
                memory_legacy.c.migrated_at.is_(None),
                memory_legacy.c.superseded_by.is_(None),
            )
        )
        .order_by(memory_legacy.c.id)
    )
    if limit:
        statement = statement.limit(max(1, int(limit)))
    return db.fetch_all(statement)


def count_pending(db: Database) -> int:
    rows = db.fetch_all(
        select(memory_legacy.c.id).where(memory_legacy.c.migrated_at.is_(None))
    )
    return len(rows)


async def migrate_async(
    config_path: str | Path | None = None,
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict:
    host, _paths = load_host(config_path)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL kosong; isi config.yaml atau SANKA_DATABASE_URL")
    db = Database(url, pool_size=host.database.pool_size, pool_timeout=host.database.pool_timeout)
    try:
        rows = pending_rows(db, limit)
        mapping = _lid_map(db)
        planned: dict[str, int] = {}
        for row in rows:
            scope, _person, _chat, _audience = scope_for(
                str(row["scope"]), str(row["scope"]), mapping
            )
            planned[scope] = planned.get(scope, 0) + 1
        if dry_run:
            return {"dry_run": True, "pending": len(rows), "by_scope": planned}
        config = load_config(config_path)
        backend = await build_memory(config, db=db)
        if not getattr(backend, "available", False):
            raise ConfigError(f"Mem0 tidak tersedia: {getattr(backend, 'reason', 'unknown')}")
        migrated = 0
        failed: list[str] = []
        try:
            for row in rows:
                scope, person, chat, audience = scope_for(
                    str(row["scope"]), str(row["scope"]), mapping
                )
                try:
                    saved = await backend.add(
                        str(row["text"]),
                        scope=scope,
                        kind=str(row["kind"]),
                        person_id=person,
                        chat_id=chat,
                        audience=audience,
                        source=str(row["source"] or "legacy"),
                        infer=False,
                        level=2,
                        owner_id=person,
                        subject_id=person,
                        confirmed=True,
                    )
                except Exception as exc:  # satu baris gagal tidak menghentikan batch
                    failed.append(f"#{row['id']}: {type(exc).__name__}: {exc}")
                    continue
                if not saved:
                    failed.append(f"#{row['id']}: memori tidak tersimpan")
                    continue
                with db.connect() as conn:
                    conn.execute(
                        memory_legacy.update()
                        .where(memory_legacy.c.id == int(row["id"]))
                        .values(migrated_at=time.time())
                    )
                migrated += 1
        finally:
            await backend.close()
        return {
            "pending": len(rows) - migrated,
            "migrated": migrated,
            "failed": failed,
            "by_scope": planned,
        }
    finally:
        db.dispose()


def migrate(
    config_path: str | Path | None = None,
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict:
    return asyncio.run(migrate_async(config_path, dry_run=dry_run, limit=limit))
