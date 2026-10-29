"""Impor folder SQLite lama (`.sanka`) ke database bersama, plus upgrade/check/lease."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from sanka.admin import database as admin_db
from sanka.engine.schema import (
    actions,
    chats,
    documents,
    engine_lease,
    memory_legacy,
    messages,
    people,
    recipes,
    skill_files,
    skill_usage,
    skills,
    state,
    topics,
)
from sanka.gateways.whatsapp.schema import wa_contacts, wa_lid_map
from support import database

HISTORY_DDL = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    chat_id TEXT NOT NULL,
    msg_id TEXT NOT NULL,
    ts REAL NOT NULL,
    sender_id TEXT NOT NULL DEFAULT '',
    sender_name TEXT NOT NULL DEFAULT '',
    sender_jid TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    outgoing INTEGER NOT NULL DEFAULT 0,
    data TEXT NOT NULL DEFAULT '{}',
    media_proto BLOB,
    quote_proto BLOB,
    UNIQUE(chat_id, msg_id)
);
CREATE TABLE chats (
    chat_id TEXT PRIMARY KEY,
    reset_ts REAL NOT NULL DEFAULT 0,
    name TEXT NOT NULL DEFAULT ''
);
CREATE TABLE contacts (
    jid TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    push_name TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE lid_map (lid TEXT PRIMARY KEY, pn TEXT NOT NULL);
"""

MEMORY_DDL = """
CREATE TABLE memory (
    id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    valence REAL,
    arousal REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    superseded_by INTEGER,
    UNIQUE(scope, kind, text)
);
CREATE TABLE actions (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    source TEXT NOT NULL,
    source_name TEXT NOT NULL DEFAULT '',
    target TEXT NOT NULL,
    target_name TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    request TEXT NOT NULL DEFAULT ''
);
"""

AFFECT_DDL = """
CREATE TABLE people (
  person_id TEXT PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  aliases TEXT NOT NULL DEFAULT '[]',
  role TEXT NOT NULL DEFAULT 'member',
  stage TEXT NOT NULL DEFAULT 'stranger',
  trust REAL NOT NULL DEFAULT 0.1,
  affection REAL NOT NULL DEFAULT 0,
  respect REAL NOT NULL DEFAULT 0.5,
  annoyance REAL NOT NULL DEFAULT 0,
  familiarity REAL NOT NULL DEFAULT 0,
  interactions INTEGER NOT NULL DEFAULT 0,
  appease REAL NOT NULL DEFAULT 0,
  last_seen REAL NOT NULL DEFAULT 0,
  window_start REAL NOT NULL DEFAULT 0,
  window_trust REAL NOT NULL DEFAULT 0,
  window_affection REAL NOT NULL DEFAULT 0,
  window_respect REAL NOT NULL DEFAULT 0,
  window_annoyance REAL NOT NULL DEFAULT 0,
  notes TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE topics (
  chat_id TEXT PRIMARY KEY,
  summary TEXT NOT NULL DEFAULT '',
  keywords TEXT NOT NULL DEFAULT '[]',
  unresolved TEXT NOT NULL DEFAULT '[]',
  pending INTEGER NOT NULL DEFAULT 0,
  last_message REAL NOT NULL DEFAULT 0,
  last_digest REAL NOT NULL DEFAULT 0,
  last_research REAL NOT NULL DEFAULT 0,
  research_day TEXT NOT NULL DEFAULT '',
  research_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE drives (
  chat_id TEXT PRIMARY KEY,
  energy REAL NOT NULL DEFAULT 0.6,
  boredom REAL NOT NULL DEFAULT 0.2,
  loneliness REAL NOT NULL DEFAULT 0,
  last_pulse REAL NOT NULL DEFAULT 0
);
CREATE TABLE chat_people (
  chat_id TEXT NOT NULL,
  person_id TEXT NOT NULL,
  last_seen REAL NOT NULL DEFAULT 0,
  interactions INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (chat_id, person_id)
);
"""

EMOTION = '{"identity": {"status": "bound"}, "mood": {"valence": 0.3}}'


def _sqlite(path: Path, ddl: str, statements: list[tuple[str, tuple]]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(ddl)
        for sql, params in statements:
            conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def legacy(tmp_path: Path) -> Path:
    folder = tmp_path / "legacy"
    folder.mkdir()
    _sqlite(
        folder / "history.db",
        HISTORY_DDL,
        [
            (
                "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    1,
                    "628111@s.whatsapp.net",
                    "AAA",
                    100.0,
                    "628111",
                    "Ana",
                    "628111@s.whatsapp.net",
                    "halo",
                    0,
                    '{"media": "image"}',
                    b"\x01\x02",
                    b"\x03",
                ),
            ),
            (
                "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    2,
                    "1203@g.us",
                    "BBB",
                    200.0,
                    "628222",
                    "Budi",
                    "628222@s.whatsapp.net",
                    "pagi",
                    1,
                    "",
                    None,
                    None,
                ),
            ),
            (
                "INSERT INTO chats VALUES (?,?,?)",
                ("628111@s.whatsapp.net", 0.0, "Ana"),
            ),
            ("INSERT INTO chats VALUES (?,?,?)", ("1203@g.us", 5.0, "Panitia")),
            (
                "INSERT INTO contacts VALUES (?,?,?,?)",
                ("628111@s.whatsapp.net", "Ana", "ana", 300.0),
            ),
            ("INSERT INTO lid_map VALUES (?,?)", ("999@lid", "628111@s.whatsapp.net")),
        ],
    )
    _sqlite(
        folder / "memory.db",
        MEMORY_DDL,
        [
            (
                "INSERT INTO memory VALUES (?,?,?,?,?,?,?,?,?,?)",
                (1, "global", "fact", "suka kopi", "turn", 0.5, 0.2, 10.0, 11.0, None),
            ),
            (
                "INSERT INTO memory VALUES (?,?,?,?,?,?,?,?,?,?)",
                (2, "chat:628111@s.whatsapp.net", "preference", "singkat", "turn", None, None, 12.0, 13.0, None),
            ),
            (
                "INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?)",
                (5, 400.0, "sanka", "Sanka", "1203@g.us", "Panitia", "send", "kirim", "{}"),
            ),
        ],
    )
    _sqlite(
        folder / "affect.db",
        AFFECT_DDL,
        [
            (
                "INSERT INTO people (person_id, name, aliases, notes, trust, interactions)"
                " VALUES (?,?,?,?,?,?)",
                ("628111", "Ana", '["Ana", "An"]', '["suka kopi"]', 0.7, 4),
            ),
            (
                "INSERT INTO topics (chat_id, summary, keywords, unresolved) VALUES (?,?,?,?)",
                ("628111@s.whatsapp.net", "kopi", '["kopi"]', '["less sugar"]'),
            ),
            (
                "INSERT INTO drives (chat_id, energy, boredom) VALUES (?,?,?)",
                ("628111@s.whatsapp.net", 0.5, 0.4),
            ),
            (
                "INSERT INTO chat_people VALUES (?,?,?,?)",
                ("628111@s.whatsapp.net", "628111", 100.0, 4),
            ),
        ],
    )
    (folder / "emotions").mkdir()
    (folder / "emotions" / "sanka.json").write_text(EMOTION, encoding="utf-8")
    (folder / "vibe.json").write_text('{"628111@s.whatsapp.net": {"current": 0.2}}', encoding="utf-8")
    (folder / "style.json").write_text('{"chats": {}}', encoding="utf-8")

    skill = folder / "skills" / "my-skill"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Does the thing.\n---\n# Steps\nDo it.\n",
        encoding="utf-8",
    )
    (skill / "references" / "x.txt").write_text("ref", encoding="utf-8")
    (skill / ".usage.json").write_text('{"uses": 3, "last": 1000.0}', encoding="utf-8")

    (folder / "recipes").mkdir()
    (folder / "recipes" / "hello.yaml").write_text(
        "kind: prompt\ndescription: hi\nprompt: halo\n", encoding="utf-8"
    )
    return folder


def _config(tmp_path: Path, name: str = "test.db") -> Path:
    db_path = tmp_path / name
    config = tmp_path / f"config-{Path(name).stem}.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "database": {
                    "url": f"sqlite+pysqlite:///{db_path}",
                    "url_env": "SANKA_TEST_IMPORT_DB",
                },
                "workspace": str(tmp_path / "workspace"),
            }
        ),
        encoding="utf-8",
    )
    return config


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    monkeypatch.delenv("SANKA_TEST_IMPORT_DB", raising=False)
    monkeypatch.delenv("SANKA_DATABASE_URL", raising=False)


def test_import_sqlite_maps_legacy_folder(tmp_path: Path, legacy: Path) -> None:
    db = database(tmp_path)
    counts = admin_db.import_sqlite(_config(tmp_path), legacy)

    assert counts["messages"] == 2
    assert counts["chats"] == 2
    assert counts["wa_contacts"] == 1
    assert counts["wa_lid_map"] == 1
    assert counts["memory_legacy"] == 2
    assert counts["actions"] == 1
    assert counts["people"] == 1
    assert counts["topics"] == 1
    assert counts["drives"] == 1
    assert counts["chat_people"] == 1
    assert counts["state"] == 3
    assert counts["skills"] == 1
    assert counts["skill_files"] == 1
    assert counts["skill_usage"] == 1
    assert counts["recipes"] == 1
    assert counts["approvals"] == 0

    rows = db.fetch_all(select(messages).order_by(messages.c.id))
    assert [row["id"] for row in rows] == [1, 2]
    assert rows[0]["sender_address"] == "628111@s.whatsapp.net"
    assert rows[0]["media_ref"] == b"\x01\x02"
    assert rows[0]["quote_media_ref"] == b"\x03"
    assert rows[0]["data"] == {"media": "image"}
    assert rows[0]["outgoing"] is False
    assert rows[1]["data"] == {}
    assert rows[1]["outgoing"] is True

    chat_rows = {row["chat_id"]: row for row in db.fetch_all(select(chats))}
    assert chat_rows["1203@g.us"]["kind"] == "group"
    assert chat_rows["628111@s.whatsapp.net"]["kind"] == "dm"

    memory_rows = db.fetch_all(select(memory_legacy))
    assert len(memory_rows) == 2
    assert all(row["migrated_at"] is None for row in memory_rows)
    assert {row["scope"] for row in memory_rows} == {"global", "chat:628111@s.whatsapp.net"}

    assert db.fetch_all(select(actions))[0]["id"] == 5

    person = db.fetch_one(select(people).where(people.c.person_id == "628111"))
    assert person is not None
    assert person["aliases"] == ["Ana", "An"]
    assert person["notes"] == ["suka kopi"]
    assert person["trust"] == pytest.approx(0.7)
    assert person["interactions"] == 4

    topic = db.fetch_one(select(topics))
    assert topic is not None
    assert topic["keywords"] == ["kopi"]
    assert topic["unresolved"] == ["less sugar"]

    states = {row["key"]: row["value"] for row in db.fetch_all(select(state))}
    assert states["emotion"] == EMOTION
    assert json.loads(states["vibe"]) == {"628111@s.whatsapp.net": {"current": 0.2}}
    assert json.loads(states["style"]) == {"chats": {}}

    skill = db.fetch_one(select(skills).where(skills.c.name == "my-skill"))
    assert skill is not None
    assert skill["description"] == "Does the thing."
    assert "Do it." in skill["body"]
    assert skill["archived"] is False
    files = db.fetch_all(select(skill_files))
    assert len(files) == 1
    assert files[0]["path"] == "references/x.txt"
    assert files[0]["content"] == b"ref"
    usage = db.fetch_one(select(skill_usage).where(skill_usage.c.skill == "my-skill"))
    assert usage is not None
    assert usage["uses"] == 3

    recipe = db.fetch_one(select(recipes).where(recipes.c.name == "hello"))
    assert recipe is not None
    assert recipe["body"].startswith("kind: prompt")

    assert db.fetch_one(select(wa_contacts).where(wa_contacts.c.jid == "628111@s.whatsapp.net"))
    assert db.fetch_one(select(wa_lid_map).where(wa_lid_map.c.lid == "999@lid"))


def test_import_is_idempotent(tmp_path: Path, legacy: Path) -> None:
    db = database(tmp_path)
    config = _config(tmp_path)
    first = admin_db.import_sqlite(config, legacy)
    second = admin_db.import_sqlite(config, legacy)

    assert first == second
    assert db.scalar(select(messages.c.id).order_by(messages.c.id).limit(1)) == 1
    for table, expected in (
        (messages, 2),
        (chats, 2),
        (people, 1),
        (memory_legacy, 2),
        (actions, 1),
        (skills, 1),
        (skill_files, 1),
        (recipes, 1),
        (state, 3),
    ):
        count = len(db.fetch_all(select(table)))
        assert count == expected, table.name


def test_dry_run_writes_nothing(tmp_path: Path, legacy: Path) -> None:
    db = database(tmp_path)
    counts = admin_db.import_sqlite(_config(tmp_path), legacy, dry_run=True)

    assert counts["messages"] == 2
    assert counts["skills"] == 1
    assert db.fetch_all(select(messages)) == []
    assert db.fetch_all(select(documents)) == []
    assert db.fetch_all(select(state)) == []


def test_replace_clears_data_but_keeps_documents(tmp_path: Path, legacy: Path) -> None:
    db = database(tmp_path)
    with db.connect() as conn:
        db.upsert(
            conn,
            documents,
            [{"name": "persona", "content": "soul", "updated_at": time.time()}],
            ["name"],
        )
        db.upsert(
            conn,
            messages,
            [
                {
                    "id": 999,
                    "chat_id": "x@s.whatsapp.net",
                    "msg_id": "ZZZ",
                    "ts": 1.0,
                    "text": "manual",
                    "data": {},
                }
            ],
            ["id"],
        )

    admin_db.import_sqlite(_config(tmp_path), legacy, replace=True)

    ids = [row["id"] for row in db.fetch_all(select(messages).order_by(messages.c.id))]
    assert ids == [1, 2]  # baris manual dibersihkan, baris lama masuk
    assert db.fetch_one(select(documents).where(documents.c.name == "persona")) is not None
    assert len(db.fetch_all(select(people))) == 1


def test_upgrade_check_and_lease(tmp_path: Path) -> None:
    config = _config(tmp_path)
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["engine"] = {"default_role": "guest"}
    raw["soul"] = {"name": "sanka", "language": "id"}
    raw["gateways"] = {}
    config.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("aku sanka", encoding="utf-8")
    (tmp_path / "roles.yaml").write_text(
        "roles:\n  guest:\n    name: guest\n", encoding="utf-8"
    )

    info = admin_db.upgrade(config)
    assert info["schema_version"] == "1"
    assert info["dialect"] == "sqlite"
    assert set(info["documents"]) == {"engine", "soul", "persona", "role:guest"}
    assert any(name.endswith("whatsapp.schema") for name in info["gateways"])

    data = admin_db.check(config)
    assert data["schema_version"] == "1"
    assert data["counts"]["documents"] == 4
    assert data["lease_active"] is False
    assert "Schema version: 1" in admin_db.format_check(data)

    db = database(tmp_path)
    assert admin_db.lease_active(db) is None
    now = time.time()
    with db.connect() as conn:
        db.upsert(
            conn,
            engine_lease,
            [
                {
                    "id": 1,
                    "instance_id": "me",
                    "host": "box",
                    "pid": 42,
                    "mode": "primary",
                    "started_at": now,
                    "heartbeat_at": now,
                }
            ],
            ["id"],
        )
    holder = admin_db.lease_active(db)
    assert holder is not None
    assert holder["host"] == "box"
    assert admin_db.lease_active(db, now=now + 3600) is None
