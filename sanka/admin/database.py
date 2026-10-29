"""Admin database: upgrade, check, backup/restore, dan impor SQLite lama ke DB bersama.

Semua fungsi di sini berjalan di luar agent: tidak menyentuh lease kecuali untuk menolak
operasi destruktif saat primary masih hidup. Impor membaca folder `.sanka` lama
(`history.db`, `memory.db`, `affect.db`, state JSON, skills, recipes) secara read-only;
tidak pernah mengimpor `whatsapp.db`.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from ..engine.config import ConfigError, HostConfig, load_host, resolve_database_url
from ..engine.db import Database
from ..engine.schema import (
    actions,
    approvals,
    chat_people,
    chats,
    config_changes,
    documents,
    drives,
    engine_lease,
    memory_legacy,
    messages,
    people,
    recipes,
    schema_meta,
    skill_files,
    skill_usage,
    skills,
    state,
    topics,
)
from ..engine.stores.documents import DocumentStore
from ..engine.stores.skills import NAME as SKILL_NAME
from ..engine.stores.skills import parse_frontmatter
from ..gateways.whatsapp.schema import wa_contacts, wa_lid_map

log = logging.getLogger(__name__)

LEASE_TTL = 30.0
STATE_FILES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("emotion", ("emotions/sanka.json",)),
    ("vibe", ("vibe.json",)),
    ("style", ("style.json",)),
)
SKIP_PARTS = frozenset({"__pycache__", "node_modules", ".git", ".venv", "venv", "site-packages"})
SUPPORT_PARTS = frozenset({"references", "templates", "assets", "scripts"})
USAGE_FILE = ".usage.json"

# Tabel data yang boleh dikosongkan `--replace`; documents/engine_control/schema_meta/
# engine_lease sengaja tidak pernah disentuh.
DATA_TABLES = (
    messages,
    chats,
    people,
    topics,
    drives,
    chat_people,
    skills,
    skill_files,
    skill_usage,
    recipes,
    actions,
    memory_legacy,
    approvals,
    config_changes,
    state,
    wa_contacts,
    wa_lid_map,
)
COUNT_TABLES = (
    ("documents", documents),
    ("messages", messages),
    ("chats", chats),
    ("memory_legacy", memory_legacy),
    ("people", people),
    ("topics", topics),
    ("drives", drives),
    ("chat_people", chat_people),
    ("skills", skills),
    ("skill_files", skill_files),
    ("recipes", recipes),
    ("actions", actions),
    ("approvals", approvals),
    ("config_changes", config_changes),
    ("state", state),
)


# ------------------------------------------------------------------ helpers


def _host(config_path) -> tuple[HostConfig, object]:
    return load_host(config_path)


def _database(host: HostConfig) -> Database:
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError(
            "database URL kosong; isi database.url di config.yaml atau env "
            f"{host.database.url_env or 'SANKA_DATABASE_URL'}"
        )
    return Database(
        url,
        pool_size=host.database.pool_size,
        pool_timeout=host.database.pool_timeout,
    )


def _count(db: Database, table) -> int:
    return int(db.scalar(select(func.count()).select_from(table)) or 0)


def _lease_row(db: Database) -> dict | None:
    return db.fetch_one(select(engine_lease).where(engine_lease.c.id == 1))


def lease_active(db: Database, *, now: float | None = None, ttl: float = LEASE_TTL) -> dict | None:
    """Baris lease yang masih hidup (heartbeat segar); `None` bila kosong/kedaluwarsa."""
    row = _lease_row(db)
    if row is None:
        return None
    moment = time.time() if now is None else float(now)
    if float(row.get("heartbeat_at") or 0.0) < moment - float(ttl):
        return None
    return row


def _pg_dsn(url: str) -> str:
    """DSN untuk pg_dump/pg_restore: driver SQLAlchemy dibuang, password dipertahankan."""
    parsed = make_url(str(url))
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


def _sqlite_path(url: str) -> Path:
    database = make_url(str(url)).database or ""
    if not database or database == ":memory:":
        raise ConfigError("backup/restore SQLite butuh file database, bukan :memory:")
    return Path(database).expanduser()


# ------------------------------------------------------------------ upgrade


def reset_sequences(db: Database) -> list[str]:
    """Selaraskan sequence Postgres dengan id eksplisit hasil impor."""
    if db.dialect != "postgresql":
        return []
    done: list[str] = []
    for table in (messages, memory_legacy, actions, approvals, config_changes):
        with db.connect() as conn:
            conn.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence(:table, 'id'), "
                    f"GREATEST(COALESCE((SELECT MAX(id) FROM {table.name}), 0), 1), "
                    f"COALESCE((SELECT MAX(id) FROM {table.name}), 0) > 0)"
                ),
                {"table": table.name},
            )
        done.append(table.name)
    return done


def upgrade(config_path=None) -> dict:
    """Buat/naikkan schema, tabel gateway, lalu seed dokumen dari file."""
    host, paths = _host(config_path)
    db = _database(host)
    try:
        db.ensure()
        from ..gateways import ensure_schemas

        gateways = ensure_schemas(db)
        docs = DocumentStore(db, paths, sync=host.sync_documents)
        seeded = docs.seed()
        reset_sequences(db)
        found = db.fetch_one(select(schema_meta.c.value).where(schema_meta.c.key == "version"))
        log.info("db upgrade: %s (schema %s, dokumen di-seed %d)", db.label, (found or {}).get("value"), seeded)
        return {
            "database": db.label,
            "dialect": db.dialect,
            "schema_version": str(found["value"]) if found is not None else None,
            "documents": docs.names(),
            "seeded": seeded,
            "gateways": gateways,
        }
    finally:
        db.dispose()


# -------------------------------------------------------------------- check


def check(config_path=None) -> dict:
    """Ringkasan read-only: schema, jumlah baris, lease, approval, perubahan config."""
    host, _paths = _host(config_path)
    db = _database(host)
    try:
        try:
            found = db.fetch_one(
                select(schema_meta.c.value).where(schema_meta.c.key == "version")
            )
            counts = {name: _count(db, table) for name, table in COUNT_TABLES}
            pending_memory = int(
                db.scalar(
                    select(func.count())
                    .select_from(memory_legacy)
                    .where(memory_legacy.c.migrated_at.is_(None))
                )
                or 0
            )
            pending_approvals = int(
                db.scalar(
                    select(func.count())
                    .select_from(approvals)
                    .where(approvals.c.status == "pending")
                )
                or 0
            )
            lease = _lease_row(db)
            last_change = db.fetch_one(
                select(config_changes).order_by(config_changes.c.id.desc()).limit(1)
            )
        except SQLAlchemyError as exc:
            raise ConfigError(
                f"database belum siap ({db.label}): {exc}; jalankan `sanka db upgrade`"
            ) from exc
        return {
            "database": db.label,
            "dialect": db.dialect,
            "schema_version": str(found["value"]) if found is not None else None,
            "counts": counts,
            "memory_legacy_pending": pending_memory,
            "approvals_pending": pending_approvals,
            "lease": lease,
            "lease_active": lease_active(db) is not None,
            "last_config_change": last_change,
        }
    finally:
        db.dispose()


def format_check(data: dict) -> str:
    """Baris teks polos untuk CLI `sanka db check`."""
    lease = data.get("lease") or {}
    if lease and lease.get("host"):
        holder = (
            f"{lease.get('host')} pid {lease.get('pid')} "
            f"({'active' if data.get('lease_active') else 'stale'})"
        )
    else:
        holder = "-"
    last = data.get("last_config_change") or {}
    if last:
        last_text = f"{last.get('file')}:{last.get('path')} by {last.get('actor') or '?'}"
    else:
        last_text = "-"
    counts = data.get("counts") or {}
    lines = [
        "Database",
        f"• URL: {data.get('database')}",
        f"• Dialect: {data.get('dialect')}",
        f"• Schema version: {data.get('schema_version') or '-'}",
        f"• Lease: {holder}",
        f"• Approvals pending: {data.get('approvals_pending', 0)}",
        f"• Memory legacy pending: {data.get('memory_legacy_pending', 0)}",
        f"• Last config change: {last_text}",
        "Rows",
    ]
    for name in (
        "documents",
        "messages",
        "chats",
        "memory_legacy",
        "people",
        "topics",
        "skills",
        "recipes",
        "actions",
        "approvals",
        "config_changes",
    ):
        lines.append(f"• {name}: {counts.get(name, 0)}")
    return "\n".join(lines)


# ------------------------------------------------------------ backup/restore


def backup(config_path, out: Path) -> Path:
    """Postgres `pg_dump --format=custom`; SQLite copy lewat backup API. chmod 600."""
    host, _paths = _host(config_path)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL kosong; tidak ada yang bisa di-backup")
    target = Path(out).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    dialect = make_url(url).get_backend_name()
    if dialect == "sqlite":
        source = _sqlite_path(url)
        if not source.is_file():
            raise ConfigError(f"database SQLite tidak ditemukan: {source}")
        with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
    else:
        subprocess.run(
            ["pg_dump", "--format=custom", "--file", str(target), _pg_dsn(url)],
            check=True,
        )
    try:
        os.chmod(target, 0o600)
    except OSError:
        log.warning("tidak bisa chmod 600 %s", target)
    log.info("backup database -> %s", target)
    return target


def restore(config_path, source: Path, *, yes: bool = False, force: bool = False) -> None:
    """Pulihkan backup; menolak tanpa `yes` dan menolak saat lease masih hidup."""
    if not yes:
        raise ConfigError("restore menimpa database; jalankan dengan --yes")
    host, _paths = _host(config_path)
    db = _database(host)
    try:
        active = lease_active(db)
        if active is not None and not force:
            raise ConfigError(
                "lease masih dipegang "
                f"{active.get('host')} pid {active.get('pid')}; berhenti dulu atau pakai --force"
            )
        url = db.url
        dialect = db.dialect
        path = Path(source).expanduser()
        if not path.is_file():
            raise ConfigError(f"file backup tidak ditemukan: {path}")
        if dialect == "sqlite":
            target = _sqlite_path(url)
            db.dispose()
            shutil.copyfile(path, target)
        else:
            subprocess.run(
                [
                    "pg_restore",
                    "--clean",
                    "--if-exists",
                    "--no-owner",
                    "--dbname",
                    _pg_dsn(url),
                    str(path),
                ],
                check=True,
            )
        log.info("restore database %s <- %s", db.label, path)
    finally:
        db.dispose()


# --------------------------------------------------------------- SQLite import


def _json_value(value, default):
    """Kolom JSON lama berupa teks; nilai rusak/kosong kembali ke default."""
    if isinstance(value, (dict, list)):
        return value
    if value is None or value == "":
        return default
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    if not isinstance(parsed, type(default)):
        return default
    return parsed


def _legacy_connection(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        log.warning("tidak bisa membuka %s: %s", path, exc)
        return None
    conn.row_factory = sqlite3.Row
    return conn


def _legacy_rows(folder: Path, filename: str, table: str) -> list[dict]:
    """Baris tabel lama; file/tabel yang hilang atau rusak bukan error fatal."""
    conn = _legacy_connection(folder / filename)
    if conn is None:
        return []
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists is None:
            log.info("%s tidak punya tabel %s; dilewati", filename, table)
            return []
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
    except sqlite3.DatabaseError as exc:
        log.warning("gagal membaca %s:%s: %s", filename, table, exc)
        return []
    finally:
        conn.close()


def _b(value) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, memoryview):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace")
    return None


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _s(value, default: str = "") -> str:
    return default if value is None else str(value)


def _message_rows(legacy: Path) -> list[dict]:
    rows: list[dict] = []
    for row in _legacy_rows(legacy, "history.db", "messages"):
        rows.append(
            {
                "id": _i(row.get("id")),
                "chat_id": _s(row.get("chat_id")),
                "msg_id": _s(row.get("msg_id")),
                "ts": _f(row.get("ts")),
                "sender_id": _s(row.get("sender_id")),
                "sender_name": _s(row.get("sender_name")),
                "sender_address": _s(row.get("sender_jid") or row.get("sender_address")),
                "text": _s(row.get("text")),
                "outgoing": bool(row.get("outgoing")),
                "data": _json_value(row.get("data"), {}),
                "media_ref": _b(row.get("media_proto")),
                "quote_media_ref": _b(row.get("quote_proto")),
            }
        )
    return rows


def _chat_rows(legacy: Path) -> list[dict]:
    rows: list[dict] = []
    for row in _legacy_rows(legacy, "history.db", "chats"):
        chat_id = _s(row.get("chat_id"))
        if not chat_id:
            continue
        rows.append(
            {
                "chat_id": chat_id,
                "name": _s(row.get("name")),
                "reset_ts": _f(row.get("reset_ts")),
                "kind": "group" if chat_id.endswith("@g.us") else "dm",
            }
        )
    return rows


def _contact_rows(legacy: Path) -> list[dict]:
    return [
        {
            "jid": _s(row.get("jid")),
            "name": _s(row.get("name")),
            "push_name": _s(row.get("push_name")),
            "updated_at": _f(row.get("updated_at")),
        }
        for row in _legacy_rows(legacy, "history.db", "contacts")
        if _s(row.get("jid"))
    ]


def _lid_rows(legacy: Path) -> list[dict]:
    return [
        {"lid": _s(row.get("lid")), "pn": _s(row.get("pn"))}
        for row in _legacy_rows(legacy, "history.db", "lid_map")
        if _s(row.get("lid"))
    ]


def _memory_rows(legacy: Path) -> list[dict]:
    rows: list[dict] = []
    for row in _legacy_rows(legacy, "memory.db", "memory"):
        rows.append(
            {
                "id": _i(row.get("id")),
                "scope": _s(row.get("scope")),
                "kind": _s(row.get("kind")),
                "text": _s(row.get("text")),
                "source": _s(row.get("source")),
                "valence": None if row.get("valence") is None else _f(row.get("valence")),
                "arousal": None if row.get("arousal") is None else _f(row.get("arousal")),
                "created_at": _f(row.get("created_at")),
                "updated_at": _f(row.get("updated_at")),
                "superseded_by": None
                if row.get("superseded_by") is None
                else _i(row.get("superseded_by")),
                "migrated_at": None,
            }
        )
    return rows


def _action_rows(legacy: Path) -> list[dict]:
    return [
        {
            "id": _i(row.get("id")),
            "ts": _f(row.get("ts")),
            "source": _s(row.get("source")),
            "source_name": _s(row.get("source_name")),
            "target": _s(row.get("target")),
            "target_name": _s(row.get("target_name")),
            "kind": _s(row.get("kind"), "send"),
            "summary": _s(row.get("summary")),
            "request": _s(row.get("request")),
        }
        for row in _legacy_rows(legacy, "memory.db", "actions")
    ]


def _people_rows(legacy: Path) -> list[dict]:
    rows: list[dict] = []
    for row in _legacy_rows(legacy, "affect.db", "people"):
        person_id = _s(row.get("person_id"))
        if not person_id:
            continue
        rows.append(
            {
                "person_id": person_id,
                "name": _s(row.get("name")),
                "aliases": _json_value(row.get("aliases"), []),
                "role": _s(row.get("role")),
                "stage": _s(row.get("stage")),
                "trust": _f(row.get("trust")),
                "affection": _f(row.get("affection")),
                "respect": _f(row.get("respect")),
                "annoyance": _f(row.get("annoyance")),
                "familiarity": _f(row.get("familiarity")),
                "interactions": _i(row.get("interactions")),
                "appease": _f(row.get("appease")),
                "last_seen": _f(row.get("last_seen")),
                "window_start": _f(row.get("window_start")),
                "window_trust": _f(row.get("window_trust")),
                "window_affection": _f(row.get("window_affection")),
                "window_respect": _f(row.get("window_respect")),
                "window_annoyance": _f(row.get("window_annoyance")),
                "notes": _json_value(row.get("notes"), []),
            }
        )
    return rows


def _topic_rows(legacy: Path) -> list[dict]:
    rows: list[dict] = []
    for row in _legacy_rows(legacy, "affect.db", "topics"):
        chat_id = _s(row.get("chat_id"))
        if not chat_id:
            continue
        rows.append(
            {
                "chat_id": chat_id,
                "summary": _s(row.get("summary")),
                "keywords": _json_value(row.get("keywords"), []),
                "unresolved": _json_value(row.get("unresolved"), []),
                "pending": _i(row.get("pending")),
                "last_message": _f(row.get("last_message")),
                "last_digest": _f(row.get("last_digest")),
                "last_research": _f(row.get("last_research")),
                "research_day": _s(row.get("research_day")),
                "research_count": _i(row.get("research_count")),
            }
        )
    return rows


def _drive_rows(legacy: Path) -> list[dict]:
    return [
        {
            "chat_id": _s(row.get("chat_id")),
            "energy": _f(row.get("energy")),
            "boredom": _f(row.get("boredom")),
            "loneliness": _f(row.get("loneliness")),
            "last_pulse": _f(row.get("last_pulse")),
        }
        for row in _legacy_rows(legacy, "affect.db", "drives")
        if _s(row.get("chat_id"))
    ]


def _chat_people_rows(legacy: Path) -> list[dict]:
    return [
        {
            "chat_id": _s(row.get("chat_id")),
            "person_id": _s(row.get("person_id")),
            "last_seen": _f(row.get("last_seen")),
            "interactions": _i(row.get("interactions")),
        }
        for row in _legacy_rows(legacy, "affect.db", "chat_people")
        if _s(row.get("chat_id")) and _s(row.get("person_id"))
    ]


def _state_rows(legacy: Path) -> list[dict]:
    now = time.time()
    rows: list[dict] = []
    for key, candidates in STATE_FILES:
        path = next((legacy / item for item in candidates if (legacy / item).is_file()), None)
        if path is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("gagal membaca state %s: %s", path, exc)
            continue
        rows.append({"key": key, "value": text, "updated_at": now})
    return rows


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _usage_entries(data) -> dict[str, dict]:
    if not isinstance(data, dict):
        return {}
    if "uses" in data:  # .usage.json satu skill: {"uses": n, "last": ts}
        return {
            "": {"uses": _i(data.get("uses")), "last": _f(data.get("last"))},
        }
    found: dict[str, dict] = {}
    for name, value in data.items():
        if isinstance(value, dict):
            found[str(name)] = {
                "uses": _i(value.get("uses")),
                "last": _f(value.get("last")),
            }
        elif isinstance(value, (int, float)):
            found[str(name)] = {"uses": _i(value), "last": 0.0}
    return found


def _skill_dirs(legacy: Path) -> list[Path]:
    root = legacy / "skills"
    if not root.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(root.rglob("SKILL.md")):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in SKIP_PARTS or part.startswith(".") for part in relative.parts):
            continue
        if any(part in SUPPORT_PARTS for part in relative.parts[:-1]):
            continue
        found.append(path)
    return found


def _skill_rows(legacy: Path) -> tuple[list[dict], list[dict], list[dict]]:
    """(skills, skill_files, skill_usage) dari folder skills lama."""
    root = legacy / "skills"
    skill_rows: list[dict] = []
    file_rows: list[dict] = []
    usage_rows: list[dict] = []
    seen: set[str] = set()
    root_usage = _usage_entries(_read_json(root / USAGE_FILE)) if root.is_dir() else {}

    for skill_md in _skill_dirs(legacy):
        directory = skill_md.parent
        try:
            text = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("gagal membaca skill %s: %s", skill_md, exc)
            continue
        data, body = parse_frontmatter(text)
        name = str(data.get("name") or directory.name).strip().lower()
        if not SKILL_NAME.fullmatch(name) or name in seen:
            log.warning("skill %s nama '%s' tidak valid/duplikat; dilewati", skill_md, name)
            continue
        seen.add(name)
        try:
            stamp = skill_md.stat().st_mtime
        except OSError:
            stamp = time.time()
        description = str(data.get("description") or "").strip()
        description = " ".join(description.split())[:400]
        skill_rows.append(
            {
                "name": name,
                "description": description,
                "body": body,
                "archived": False,
                "created_at": stamp,
                "updated_at": stamp,
            }
        )
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path == skill_md:
                continue
            if path.name == USAGE_FILE:
                continue
            parts = path.relative_to(directory).parts
            if any(part in SKIP_PARTS or part.startswith(".") for part in parts[:-1]):
                continue
            try:
                content = path.read_bytes()
            except OSError as exc:
                log.warning("gagal membaca lampiran skill %s: %s", path, exc)
                continue
            file_rows.append(
                {
                    "skill": name,
                    "path": path.relative_to(directory).as_posix(),
                    "content": content,
                    "updated_at": _f(path.stat().st_mtime, time.time()),
                }
            )
        usage = _usage_entries(_read_json(directory / USAGE_FILE))
        item = usage.get("", usage.get(name))
        if item is None:
            item = root_usage.get(name)
        if item is not None:
            usage_rows.append({"skill": name, "uses": item["uses"], "last": item["last"]})
    return skill_rows, file_rows, usage_rows


def _recipe_rows(legacy: Path) -> list[dict]:
    root = legacy / "recipes"
    if not root.is_dir():
        return []
    now = time.time()
    rows: list[dict] = []
    for path in sorted(root.glob("*.yaml")):
        name = path.stem.strip().lower()
        if not name or len(name) > 80:
            log.warning("resep %s nama tidak valid; dilewati", path)
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("gagal membaca resep %s: %s", path, exc)
            continue
        rows.append({"name": name, "body": body, "updated_at": now})
    return rows


def _collect(legacy: Path) -> tuple[dict[str, list[dict]], list[tuple[object, list[str]]]]:
    """Semua baris yang bisa diimpor, plus daftar (tabel, index) untuk upsert."""
    skill_rows, file_rows, usage_rows = _skill_rows(legacy)
    found: dict[str, list[dict]] = {
        "messages": _message_rows(legacy),
        "chats": _chat_rows(legacy),
        "wa_contacts": _contact_rows(legacy),
        "wa_lid_map": _lid_rows(legacy),
        "memory_legacy": _memory_rows(legacy),
        "actions": _action_rows(legacy),
        "people": _people_rows(legacy),
        "topics": _topic_rows(legacy),
        "drives": _drive_rows(legacy),
        "chat_people": _chat_people_rows(legacy),
        "state": _state_rows(legacy),
        "skills": skill_rows,
        "skill_files": file_rows,
        "skill_usage": usage_rows,
        "recipes": _recipe_rows(legacy),
        "approvals": [],
        "config_changes": [],
    }
    plan: list[tuple[object, list[str]]] = [
        (messages, ["id"]),
        (chats, ["chat_id"]),
        (wa_contacts, ["jid"]),
        (wa_lid_map, ["lid"]),
        (memory_legacy, ["id"]),
        (actions, ["id"]),
        (people, ["person_id"]),
        (topics, ["chat_id"]),
        (drives, ["chat_id"]),
        (chat_people, ["chat_id", "person_id"]),
        (state, ["key"]),
        (skills, ["name"]),
        (skill_files, ["skill", "path"]),
        (skill_usage, ["skill"]),
        (recipes, ["name"]),
    ]
    return found, plan


def import_sqlite(
    config_path,
    folder,
    *,
    replace: bool = False,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """Impor folder `.sanka` lama (SQLite + file state/skill/resep) ke database bersama."""
    legacy = Path(folder).expanduser()
    if not legacy.is_dir():
        raise ConfigError(f"folder SQLite tidak ditemukan: {legacy}")
    host, _paths = _host(config_path)
    db = _database(host)
    try:
        active = lease_active(db)
        if active is not None and not force:
            raise ConfigError(
                "lease masih dipegang "
                f"{active.get('host')} pid {active.get('pid')}; berhenti dulu atau pakai --force"
            )
        found, plan = _collect(legacy)
        counts = {name: len(rows) for name, rows in found.items()}
        for name in sorted(counts):
            log.info("sqlite import %s: %d baris", name, counts[name])
        if dry_run:
            return counts
        db.ensure()
        with db.connect() as conn:
            if replace:
                for table in DATA_TABLES:
                    conn.execute(table.delete())
            for table, index in plan:
                name = table.name
                db.upsert(conn, table, found[name], index)
        reset_sequences(db)
        return counts
    finally:
        db.dispose()
