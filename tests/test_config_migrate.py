"""Migration contract: legacy or split layout -> one config.yaml + roles.yaml."""

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


def split_folder(tmp_path: Path) -> Path:
    (tmp_path / "config.yaml").write_text(
        "workspace: .sanka/workspace\n"
        "engine_file: engine.yaml\n"
        "gateway_file: gateway.yaml\n"
        "roles_dir: roles\n",
        encoding="utf-8",
    )
    (tmp_path / "engine.yaml").write_text(
        "llm:\n  model: split-model\n  api_key_env: SANKA_TEST_KEY\n"
        "default_role: guest\n",
        encoding="utf-8",
    )
    (tmp_path / "gateway.yaml").write_text(
        "gateways:\n  cli:\n    enabled: true\n    role: guest\n", encoding="utf-8"
    )
    (tmp_path / "soul.yaml").write_text(
        "name: sanka\nlanguage: en\npronouns: aku\n", encoding="utf-8"
    )
    (tmp_path / "roles").mkdir()
    (tmp_path / "roles" / "guest.yaml").write_text("name: guest\n", encoding="utf-8")
    (tmp_path / "roles" / "admin.yaml").write_text(
        'name: admin\ntools: ["*"]\n', encoding="utf-8"
    )
    return tmp_path


def test_migrate_writes_combined_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    monkeypatch.setenv("SANKA_DATABASE_URL", "sqlite+pysqlite://")
    folder = old_folder(tmp_path)

    files, dropped, backup = admin_migrate.migrate(folder)

    for name in ("config.yaml", "roles.yaml"):
        assert (folder / name).is_file()
        assert name in files
    assert backup is not None and backup.is_dir()
    assert (backup / "config.yaml").is_file()
    assert "whatsapp.group_reply" in dropped
    assert not (folder / "engine.yaml").exists()

    raw = yaml.safe_load((folder / "config.yaml").read_text(encoding="utf-8"))
    assert raw["engine"]["llm"]["model"] == "m"
    assert "max_turns" not in raw["engine"]["llm"]
    assert raw["engine"]["llm"]["run_budget_seconds"] == 240
    assert raw["engine"]["reply"]["quiet_seconds"] == 300
    assert raw["engine"]["memory"]["auto_capture"] is False
    assert raw["engine"]["memory"]["max_items"] == 7
    assert "proactive" not in raw["engine"]["ambient"]
    assert raw["gateways"]["whatsapp"]["backlog_seconds"] == 120
    assert raw["gateways"]["whatsapp"]["enabled"] is True
    assert raw["soul"]["name"] == "sanka"
    assert raw["soul"]["language"] == "id"

    roles = yaml.safe_load((folder / "roles.yaml").read_text(encoding="utf-8"))["roles"]
    assert roles["owner"]["tools"] == ["*"]
    assert roles["owner"]["memory_admin"] is True
    assert "read" in roles["member"]["tools"]
    assert roles["member"]["commands"] == ["help"]
    assert roles["member"]["memory_admin"] is False

    config = load_config(folder / "config.yaml")
    assert config.default_role == "member"
    assert config.roles.for_address("628111")[0].name == "owner"
    assert config.soul.persona.startswith("Kamu Sanka yang tidak suka")
    assert config.reply.quiet_seconds == 300


def test_migrate_split_layout(tmp_path: Path) -> None:
    folder = split_folder(tmp_path)

    files, _dropped, backup = admin_migrate.migrate(folder)

    assert backup is not None and backup.is_dir()
    raw = yaml.safe_load(files["config.yaml"])
    assert raw["engine"]["llm"]["model"] == "split-model"
    assert raw["gateways"]["cli"]["enabled"] is True
    assert raw["soul"]["language"] == "en"
    roles = yaml.safe_load(files["roles.yaml"])["roles"]
    assert roles["guest"]["name"] == "guest"
    assert roles["admin"]["memory_admin"] is True


def test_dry_run_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_DATABASE_URL", "sqlite+pysqlite://")
    folder = old_folder(tmp_path)

    files, _dropped, backup = admin_migrate.migrate(folder, dry_run=True)

    assert backup is None
    assert "roles.yaml" in files
    assert not (folder / "roles.yaml").exists()
    assert yaml.safe_load((folder / "config.yaml").read_text(encoding="utf-8")).get("roles")


def test_old_config_rejected_by_loader(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    folder = old_folder(tmp_path)
    with pytest.raises(ConfigError, match="migrate"):
        load_config(folder / "config.yaml")


def test_existing_roles_need_force(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_DATABASE_URL", "sqlite+pysqlite://")
    folder = old_folder(tmp_path)
    (folder / "roles.yaml").write_text("roles: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="roles.yaml"):
        admin_migrate.migrate(folder)
    files, _dropped, backup = admin_migrate.migrate(folder, force=True)
    assert "roles.yaml" in files
    roles = yaml.safe_load((folder / "roles.yaml").read_text(encoding="utf-8"))["roles"]
    assert "member" in roles
    assert backup is not None and (backup / "roles.yaml").is_file()


def test_already_combined_is_noop(tmp_path: Path) -> None:
    folder = tmp_path / "combined"
    folder.mkdir()
    body = (
        "workspace: .sanka/workspace\n"
        "engine:\n  default_role: guest\n"
        "gateways: {}\n"
        "soul:\n  language: id\n"
    )
    (folder / "config.yaml").write_text(body, encoding="utf-8")

    files, dropped, backup = admin_migrate.migrate(folder)

    assert admin_migrate.ALREADY in dropped
    assert files["config.yaml"] == body
    assert backup is None
    assert (folder / "config.yaml").read_text(encoding="utf-8") == body


def test_init_scaffold_loads(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "x")
    monkeypatch.setenv("SANKA_DATABASE_URL", "sqlite+pysqlite://")
    written, skipped = admin_init.init(tmp_path / "sanka")
    assert skipped == []
    assert (tmp_path / "sanka" / "roles.yaml").is_file()
    assert not (tmp_path / "sanka" / "roles").exists()
    assert not (tmp_path / "sanka" / "engine.yaml").exists()
    assert admin_init.env_mode(tmp_path / "sanka" / ".env") == 0o600
    assert admin_init.env_mode(tmp_path / "sanka" / "roles.yaml") == 0o600

    config = load_config(tmp_path / "sanka" / "config.yaml", require_key=False)
    assert config.default_role == "guest"
    assert config.gateways["cli"].enabled is True
    assert config.gateways["whatsapp"].enabled is False
    assert config.memory.privacy.group_max_level == 2
    assert config.roles.get("owner").memory_admin is True

    again, skipped_again = admin_init.init(tmp_path / "sanka")
    assert again == []
    assert len(skipped_again) == len(written)
