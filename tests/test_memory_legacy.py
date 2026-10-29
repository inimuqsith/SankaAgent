"""Kontrak migrasi memori lama: DM/LID jadi person, grup jadi chat, global shared."""

from __future__ import annotations

from sanka.admin import memory as memory_admin
from sanka.engine.schema import memory_legacy
from support import FakeMem0
from sqlalchemy import select


def test_scope_mapping() -> None:
    mapping = {"999@lid": "628999@s.whatsapp.net"}
    assert memory_admin.scope_for("global", "global", {}) == ("global", "", "", "shared")
    assert memory_admin.scope_for("628111@s.whatsapp.net", "628111@s.whatsapp.net", {}) == (
        "person",
        "628111",
        "628111@s.whatsapp.net",
        "private",
    )
    assert memory_admin.scope_for("999@lid", "999@lid", mapping) == (
        "person",
        "628999",
        "999@lid",
        "private",
    )
    assert memory_admin.scope_for("111@g.us", "111@g.us", {}) == (
        "chat",
        "",
        "111@g.us",
        "group",
    )


def legacy_db(tmp_path):
    from support import database

    db = database(tmp_path)
    rows = [
        {"id": 1, "scope": "global", "kind": "fact", "text": "panitia punya grup logistik",
         "source": "test", "valence": None, "arousal": None, "created_at": 1.0,
         "updated_at": 1.0, "superseded_by": None, "migrated_at": None},
        {"id": 2, "scope": "628111@s.whatsapp.net", "kind": "preference", "text": "suka kopi",
         "source": "test", "valence": 0.2, "arousal": 0.1, "created_at": 1.0,
         "updated_at": 1.0, "superseded_by": None, "migrated_at": None},
        {"id": 3, "scope": "111@g.us", "kind": "note", "text": "tiket konser",
         "source": "test", "valence": None, "arousal": None, "created_at": 1.0,
         "updated_at": 1.0, "superseded_by": None, "migrated_at": None},
        {"id": 4, "scope": "global", "kind": "fact", "text": "sudah digantikan",
         "source": "test", "valence": None, "arousal": None, "created_at": 1.0,
         "updated_at": 1.0, "superseded_by": 1, "migrated_at": None},
    ]
    with db.connect() as conn:
        conn.execute(memory_legacy.insert(), rows)
    return db


def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("K", "x")
    monkeypatch.setenv("SANKA_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    base = tmp_path
    (base / "config.yaml").write_text(
        "database:\n  url_env: SANKA_DATABASE_URL\nworkspace: \".sanka/workspace\"\n"
        "engine:\n  llm:\n    api_key_env: K\n  default_role: owner\n"
        "gateways: {}\n"
        "soul:\n  language: id\n",
        encoding="utf-8",
    )
    (base / "roles.yaml").write_text(
        'roles:\n  owner:\n    name: owner\n    tools: ["*"]\n', encoding="utf-8"
    )
    return base / "config.yaml"


def test_migrate_is_idempotent_and_skips_superseded(tmp_path, monkeypatch) -> None:
    db = legacy_db(tmp_path)
    target = config_dir(tmp_path, monkeypatch)
    fake = FakeMem0()

    async def build(*_args, **_kwargs):
        return fake

    monkeypatch.setattr(memory_admin, "build_memory", build)

    report = memory_admin.migrate(target)
    assert report["migrated"] == 3
    assert report["pending"] == 0
    rows = db.fetch_all(select(memory_legacy))
    migrated = {row["id"]: row["migrated_at"] for row in rows}
    assert migrated[4] is None and all(migrated[i] for i in (1, 2, 3))

    scopes = {item["scope"] for item in fake.items.values()}
    assert scopes == {"global", "person", "chat"}
    assert {item["audience"] for item in fake.items.values()} == {"shared", "private", "group"}

    again = memory_admin.migrate(target)
    assert again["migrated"] == 0
    assert again["pending"] == 0


def test_dry_run_does_not_touch_db_or_backend(tmp_path, monkeypatch) -> None:
    db = legacy_db(tmp_path)
    target = config_dir(tmp_path, monkeypatch)

    report = memory_admin.migrate(target, dry_run=True)

    assert report == {"dry_run": True, "pending": 3, "by_scope": {"global": 1, "person": 1, "chat": 1}}
    rows = db.fetch_all(select(memory_legacy))
    assert all(row["migrated_at"] is None for row in rows)
