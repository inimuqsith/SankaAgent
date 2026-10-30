"""Kontrak Database: schema idempotent, upsert, state, dan pencarian LIKE."""

from __future__ import annotations

import time
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql

from sanka.engine.db import Database, redact_dsn
from sanka.engine.schema import documents, messages, schema_meta, state
from sanka.engine.stores.history import HistoryStore
from support import database


def test_create_all_idempotent_and_seeds_control(tmp_path) -> None:
    db = database(tmp_path)
    db.ensure()
    db.ensure()
    found = db.fetch_all(select(state))
    assert found == []
    control = db.fetch_all(select(documents))
    assert control == []


def test_upsert_insert_then_update(tmp_path) -> None:
    db = database(tmp_path)
    with db.connect() as conn:
        db.upsert(conn, documents, [{"name": "engine", "content": "a", "updated_at": 1.0}], ["name"])
    with db.connect() as conn:
        db.upsert(conn, documents, [{"name": "engine", "content": "b", "updated_at": 2.0}], ["name"])
    assert db.fetch_all(select(documents)) == [
        {"name": "engine", "content": "b", "updated_at": 2.0}
    ]


def test_upsert_partial_update_keeps_other_columns(tmp_path) -> None:
    db = database(tmp_path)
    with db.connect() as conn:
        db.upsert(conn, documents, [{"name": "soul", "content": "x", "updated_at": 1.0}], ["name"])
    with db.connect() as conn:
        db.upsert(
            conn,
            documents,
            [{"name": "soul", "content": "y", "updated_at": 1.0}],
            ["name"],
            update={"content": "y"},
        )
    row = db.fetch_one(select(documents).where(documents.c.name == "soul"))
    assert row["content"] == "y" and row["updated_at"] == 1.0


def test_upsert_chunks_postgres_parameter_limit() -> None:
    db = Database("sqlite+pysqlite://")
    rows = [
        {
            "id": i,
            "chat_id": "chat",
            "msg_id": f"m{i}",
            "ts": float(i),
            "sender_id": "",
            "sender_name": "",
            "sender_address": "",
            "text": "x",
            "outgoing": False,
            "author": "unknown",
            "visibility": 2,
            "data": {},
            "media_ref": None,
            "quote_media_ref": None,
        }
        for i in range(5500)
    ]
    seen: list[int] = []

    class FakeConnection:
        dialect = SimpleNamespace(name="postgresql")

        def execute(self, statement):
            compiled = statement.compile(dialect=postgresql.dialect())
            seen.append(len(compiled.params))
            return SimpleNamespace(rowcount=500)

    try:
        total = db.upsert(FakeConnection(), messages, rows, ["id"])
    finally:
        db.dispose()

    assert total == 5500
    assert len(seen) == 11
    assert max(seen) == len(messages.columns) * 500 == 7000
    assert max(seen) <= 65535


def test_state_roundtrip(tmp_path) -> None:
    db = database(tmp_path)
    with db.connect() as conn:
        db.upsert(
            conn,
            state,
            [{"key": "style", "value": '{"seeded": true}', "updated_at": 3.0}],
            ["key"],
        )
    row = db.fetch_one(select(state).where(state.c.key == "style"))
    assert row["value"] == '{"seeded": true}'


def test_search_is_ilike_per_term(tmp_path) -> None:
    db = database(tmp_path)
    store = HistoryStore(db)
    from sanka.engine.context import ChatMessage

    def message(mid: str, text: str, when: datetime) -> ChatMessage:
        return ChatMessage(id=mid, sender_name="Budi", sender_id="1", when=when, text=text)

    store.add("chat", message("a", "Rekap RAPAT logistik", datetime(2026, 1, 1, 9)))
    store.add("chat", message("b", "jadwal ronda", datetime(2026, 1, 1, 10)))
    store.add("chat", message("c", "rapat panitia", datetime(2026, 1, 1, 11)))

    hits = store.search("chat", "rapat logistik")
    assert [hit.id for hit in hits] == ["a"]
    assert [hit.id for hit in store.search("chat", "RAPAT")] == ["c", "a"]
    assert store.message_count() == 3


def test_search_time_window_and_other_chat_isolation(tmp_path) -> None:
    db = database(tmp_path)
    store = HistoryStore(db)
    from sanka.engine.context import ChatMessage

    def message(mid: str, text: str, stamp: float) -> ChatMessage:
        return ChatMessage(id=mid, sender_name="B", sender_id="1", when=datetime.fromtimestamp(stamp), text=text)

    now = time.time()
    store.add("a", message("1", "fakta lama", now - 100))
    store.add("a", message("2", "fakta baru", now))
    store.add("b", message("3", "fakta chat lain", now))
    assert [hit.id for hit in store.search("a", "fakta", after=now - 50)] == ["2"]
    assert [hit.id for hit in store.search("a", "fakta", before=now - 50)] == ["1"]
    assert {hit.id for hit in store.search("a", "")} == {"1", "2"}


def test_redact_dsn_hides_password() -> None:
    label = redact_dsn("postgresql+psycopg://sanka:secret@db:5432/sanka?sslmode=require")
    assert "secret" not in label
    assert label.startswith("postgresql://")
    assert "db:5432/sanka" in label


def test_label_of_sqlite_database(tmp_path) -> None:
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'x.db'}")
    assert db.dialect == "sqlite"
    assert str(tmp_path / "x.db") in db.label
    db.dispose()


def test_ensure_adds_missing_message_columns_and_bumps_version(tmp_path) -> None:
    db = database(tmp_path)
    with db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE messages DROP COLUMN author"))
        conn.execute(text("ALTER TABLE messages DROP COLUMN visibility"))
        conn.execute(text("UPDATE schema_meta SET value = '1' WHERE key = 'version'"))
        conn.execute(
            text(
                "INSERT INTO messages "
                "(chat_id, msg_id, ts, sender_id, sender_name, sender_address, text, outgoing, data) "
                "VALUES ('chat', 'm1', 1.0, '', 'Budi', '', 'halo', 0, '{}')"
            )
        )

    db.ensure()
    db.ensure()  # idempotent: kolom yang sudah ada tidak ditambah lagi

    row = db.fetch_one(select(messages))
    version = db.fetch_one(select(schema_meta).where(schema_meta.c.key == "version"))

    assert row is not None
    assert row["author"] == "unknown"
    assert row["visibility"] == 2
    assert row["text"] == "halo"
    assert version is not None and version["value"] == "2"
