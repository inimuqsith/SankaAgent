"""Database tunggal (Postgres di runtime, SQLite di test) lewat SQLAlchemy Core."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Connection, Engine, Table, create_engine, inspect, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import make_url

from .schema import engine_control, metadata, schema_meta

log = logging.getLogger(__name__)

SCHEMA_VERSION = 5
UPSERT_BATCH_ROWS = 500
MESSAGE_COLUMNS = (
    ("author", "VARCHAR(20) NOT NULL DEFAULT 'unknown'"),
    ("visibility", "INTEGER NOT NULL DEFAULT 2"),
    ("deleted", "BOOLEAN NOT NULL DEFAULT false"),
    ("deleted_by", "VARCHAR(160) NOT NULL DEFAULT ''"),
    ("deleted_admin", "BOOLEAN NOT NULL DEFAULT false"),
)
PEOPLE_COLUMNS = (
    ("gender", "VARCHAR(12) NOT NULL DEFAULT ''"),
    ("gender_confidence", "FLOAT NOT NULL DEFAULT 0"),
    ("gender_source", "VARCHAR(16) NOT NULL DEFAULT ''"),
)


def redact_dsn(url: str) -> str:
    """DSN tanpa password, aman untuk log/status/audit."""
    try:
        parsed = make_url(str(url))
        parsed = parsed.set(password=None)
        parsed = parsed.set(drivername=parsed.get_backend_name())
        return parsed.render_as_string(hide_password=True)
    except Exception:
        return "database"


class Database:
    def __init__(
        self,
        url: str,
        *,
        pool_size: int = 5,
        pool_timeout: float = 30.0,
    ) -> None:
        self.url = str(url)
        if not self.url:
            raise ValueError("database URL kosong")
        self.dialect = make_url(self.url).get_backend_name()
        self.label = redact_dsn(self.url)
        options: dict[str, Any] = {"pool_pre_ping": True, "future": True}
        if self.dialect != "sqlite":
            options.update(pool_size=int(pool_size), pool_timeout=float(pool_timeout))
        else:
            options["connect_args"] = {"check_same_thread": False}
        self.engine: Engine = create_engine(self.url, **options)

    # ------------------------------------------------------------- schema

    def create_all(self) -> None:
        metadata.create_all(self.engine)
        self._add_missing_columns()
        with self.connect() as conn:
            self.upsert(
                conn,
                schema_meta,
                [{"key": "version", "value": str(SCHEMA_VERSION)}],
                ["key"],
                update={"value": str(SCHEMA_VERSION)},
            )
            # Seed baris kontrol tanpa menimpa freeze yang sedang aktif.
            self.upsert(
                conn,
                engine_control,
                [{"id": 1, "frozen": False, "frozen_by": "", "frozen_at": 0.0}],
                ["id"],
                update={"id": engine_control.c.id},
            )

    def _add_missing_columns(self) -> list[str]:
        """Kolom baru pada tabel lama; tidak pernah membuat tabel dari nol."""
        inspector = inspect(self.engine)
        tables = set(inspector.get_table_names())
        added: list[str] = []
        for table, columns in (("messages", MESSAGE_COLUMNS), ("people", PEOPLE_COLUMNS)):
            if table not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            missing = [(name, ddl) for name, ddl in columns if name not in existing]
            if not missing:
                continue
            with self.engine.begin() as conn:
                for name, ddl in missing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
            added.extend(f"{table}.{name}" for name, _ in missing)
        return added

    def ensure_vector(self) -> bool:
        """Aktifkan pgvector best-effort; SQLite tidak butuh."""
        if self.dialect != "postgresql":
            return False
        try:
            with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            return True
        except Exception as exc:  # izin/pgvector hilang: Mem0 jadi unavailable, bukan crash
            log.warning("pgvector tidak bisa diaktifkan: %s", exc)
            return False

    def ensure(self) -> None:
        self.create_all()
        self.ensure_vector()

    # ---------------------------------------------------------- koneksi

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        with self.engine.begin() as conn:
            yield conn

    def fetch_all(self, statement) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().all()]

    def fetch_one(self, statement) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(statement).mappings().first()
            return dict(row) if row is not None else None

    def scalar(self, statement):
        with self.connect() as conn:
            return conn.execute(statement).scalar()

    def upsert(
        self,
        conn: Connection,
        table: Table,
        rows: Sequence[Mapping[str, Any]],
        index_elements: Sequence[str],
        update=None,
    ) -> int:
        if not rows:
            return 0
        items = list(rows)
        dialect = conn.dialect.name
        insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert
        total = 0
        for start in range(0, len(items), UPSERT_BATCH_ROWS):
            chunk = items[start : start + UPSERT_BATCH_ROWS]
            statement = insert_fn(table).values(chunk)
            if update is None:
                conflict = {
                    column.name: statement.excluded[column.name]
                    for column in table.columns
                    if column.name not in index_elements
                }
            elif callable(update):
                conflict = update(statement)
            else:
                conflict = update
            statement = statement.on_conflict_do_update(
                index_elements=list(index_elements), set_=conflict
            )
            total += int(conn.execute(statement).rowcount or 0)
        return total

    def dispose(self) -> None:
        with contextlib.suppress(Exception):
            self.engine.dispose()
