"""Kontrak migrasi config lama -> host/engine/gateway/soul/roles."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sanka.admin import init as admin_init
from sanka.admin import migrate as admin_migrate
from sanka.engine.config import ConfigError, load_config

OLD = """\
llm:
  base_url: "http://x"
  model: m
  api_key_env: SANKA_TEST_KEY
  max_turns: 20
workspace: ".sanka/workspace"
whatsapp:
  session_db: ".sanka/whatsapp.db"
  bot_name: sanka
  backlog_seconds: 120
  quiet_seconds: 300
  group_reply: addressed
  thread_seconds: 900
roles:
  owner:
    users: ["628111"]
    tools: ["*"]
  member:
    commands: ["help"]
default_role: member
memory:
  path: ".sanka/memory.db"
  max_items: 7
  auto_extract: false
affect:
  enabled: true
  path: ".sanka/affect.db"
  state_dir: ".sanka/emotions"
ambient:
  proactive: false
  tick_seconds: 30
"""


def old_folder(tmp_path: Path) -> Path:
    (tmp_path / "SOUL.md").write_text(
        "Kamu Sanka yang tidak suka dengan keributan; untuk bilang jangan, cukup diam.",
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(OLD, encoding="utf-8")
    return tmp_path


def test_migrate_writes_split_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    monkeypatch.setenv("SANKA_DATABASE_URL", "sqlite+pysqlite://")
    folder = old_folder(tmp_path)

    files, dropped, backup = admin_migrate.migrate(folder)

    for name in ("engine.yaml", "gateway.yaml", "soul.yaml", "roles/owner.yaml", "roles/member.yaml"):
        assert (folder / name).is_file()
        assert name in files
    assert backup is not None and backup.is_file()
    assert "whatsapp.group_reply" in dropped
    engine = yaml.safe_load((folder / "engine.yaml").read_text(encoding="utf-8"))
    assert "max_turns" not in engine["llm"]
    assert engine["llm"]["run_budget_seconds"] == 240
    assert engine["reply"]["quiet_seconds"] == 300
    assert engine["memory"]["auto_capture"] is False
    assert engine["memory"]["max_items"] == 7
    assert "proactive" not in engine["ambient"]
    gateway = yaml.safe_load((folder / "gateway.yaml").read_text(encoding="utf-8"))
    assert gateway["gateways"]["whatsapp"]["backlog_seconds"] == 120
    assert gateway["gateways"]["whatsapp"]["enabled"] is True
    soul = yaml.safe_load((folder / "soul.yaml").read_text(encoding="utf-8"))
    assert soul["name"] == "sanka"
    assert soul["language"] == "id"
    owner = yaml.safe_load((folder / "roles/owner.yaml").read_text(encoding="utf-8"))
    assert owner["tools"] == ["*"]  # eksplisit dari config lama
    member = yaml.safe_load((folder / "roles/member.yaml").read_text(encoding="utf-8"))
    assert "read" in member["tools"]  # ekspansi snapshot handler lama
    assert member["commands"] == ["help"]

    config = load_config(folder / "config.yaml")
    assert config.default_role == "member"
    assert config.roles.for_address("628111")[0].name == "owner"
    assert config.soul.persona.startswith("Kamu Sanka yang tidak suka")
    assert config.reply.quiet_seconds == 300


def test_dry_run_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_DATABASE_URL", "sqlite+pysqlite://")
    folder = old_folder(tmp_path)

    files, _dropped, backup = admin_migrate.migrate(folder, dry_run=True)

    assert backup is None
    assert "engine.yaml" in files
    assert not (folder / "engine.yaml").exists()
    assert not (folder / "roles").exists()


def test_old_config_rejected_by_loader(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    folder = old_folder(tmp_path)
    with pytest.raises(ConfigError, match="migrate"):
        load_config(folder / "config.yaml")


def test_existing_new_config_needs_force(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_DATABASE_URL", "sqlite+pysqlite://")
    folder = old_folder(tmp_path)
    (folder / "engine.yaml").write_text("default_role: guest\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="sudah ada"):
        admin_migrate.migrate(folder)
    files, _dropped, _backup = admin_migrate.migrate(folder, force=True)
    assert "engine.yaml" in files
    assert yaml.safe_load((folder / "engine.yaml").read_text(encoding="utf-8"))["default_role"] == "member"


def test_init_scaffold_loads(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "x")
    monkeypatch.setenv("SANKA_DATABASE_URL", "sqlite+pysqlite://")
    written, skipped = admin_init.init(tmp_path / "sanka")
    assert skipped == []
    assert (tmp_path / "sanka" / "roles" / "guest.yaml").is_file()
    assert admin_init.env_mode(tmp_path / "sanka" / ".env") == 0o600

    config = load_config(tmp_path / "sanka" / "config.yaml", require_key=False)
    assert config.default_role == "guest"
    assert config.gateways["cli"].enabled is True
    assert config.gateways["whatsapp"].enabled is False

    again, skipped_again = admin_init.init(tmp_path / "sanka")
    assert again == []
    assert len(skipped_again) == len(written)
