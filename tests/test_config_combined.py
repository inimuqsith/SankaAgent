"""Combined config contract: one config.yaml plus roles.yaml, DB documents override seeds."""

from __future__ import annotations

import pytest
import yaml

from sanka.engine.config import DEFAULT_SENSITIVE_COMMANDS, ConfigError, load_config, load_host
from sanka.engine.stores.documents import DocumentStore
from support import database

BASE = {
    "database": {"url": "sqlite+pysqlite://", "url_env": ""},
    "workspace": ".sanka/workspace",
    "roles_file": "roles.yaml",
    "engine": {
        "llm": {"api_key_env": "SANKA_TEST_KEY", "model": "seed-model", "base_url": "http://x"},
        "default_role": "guest",
        "memory": {
            "privacy": {
                "enabled": True,
                "default_level": 2,
                "ask_level": 3,
                "group_max_level": 1,
                "dm_max_level": 4,
                "auditor": False,
            }
        },
    },
    "gateways": {"cli": {"enabled": True, "role": "guest", "chat_id": "cli:local"}},
    "soul": {"name": "sanka", "language": "en", "pronouns": "aku"},
}

ROLES = {
    "roles": {
        "guest": {"name": "guest", "tools": ["read"], "commands": ["help"]},
        "owner": {
            "name": "owner",
            "tools": ["*"],
            "commands": ["*"],
            "memory_admin": True,
        },
    }
}


def write_folder(tmp_path, *, config=None, roles=None) -> None:
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(config if config is not None else BASE, sort_keys=False), encoding="utf-8"
    )
    if roles is not False:
        (tmp_path / "roles.yaml").write_text(
            yaml.safe_dump(roles if roles is not None else ROLES, sort_keys=False), encoding="utf-8"
        )


def test_load_config_reads_all_sections(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    write_folder(tmp_path)

    host, paths = load_host(tmp_path / "config.yaml")
    config = load_config(paths.config, require_key=False)

    assert host.roles_file == tmp_path / "roles.yaml"
    assert paths.roles == tmp_path / "roles.yaml"
    assert paths.soul_md == tmp_path / "SOUL.md"
    assert config.llm.model == "seed-model"
    assert config.default_role == "guest"
    assert config.soul.language == "en"
    assert config.gateways["cli"].enabled is True
    assert config.roles.get("owner").memory_admin is True
    assert config.memory.privacy.group_max_level == 1
    assert config.memory.privacy.auditor is False


def test_documents_override_config_sections(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    write_folder(tmp_path)
    docs = DocumentStore(database(tmp_path), load_host(tmp_path / "config.yaml")[1])
    docs.put("engine", "llm:\n  model: doc-model\n  api_key_env: SANKA_TEST_KEY\ndefault_role: owner\n")
    docs.put("soul", "name: sanka\nlanguage: id\npronouns: aku\n")
    docs.put("role:owner", "name: owner\ntools: ['*']\nmemory_admin: true\n")
    docs.put("role:guest", "name: guest\ntools: []\n")

    config = load_config(tmp_path / "config.yaml", documents=docs, require_key=False)

    assert config.llm.model == "doc-model"
    assert config.soul.language == "id"
    assert config.default_role == "owner"
    assert config.roles.names() == ["guest", "owner"]
    assert config.roles.get("guest").tools == ()
    assert config.roles.get("owner").memory_admin is True


def test_roles_file_needs_the_wrapper(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    write_folder(tmp_path, roles={"guest": {"name": "guest"}})
    with pytest.raises(ConfigError, match="roles"):
        load_config(tmp_path / "config.yaml", require_key=False)


def test_missing_roles_file_is_an_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    write_folder(tmp_path, roles=False)
    with pytest.raises(ConfigError, match="no roles"):
        load_config(tmp_path / "config.yaml", require_key=False)


def test_split_layout_rejected_with_migration_hint(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text(
        "database:\n  url: sqlite+pysqlite://\nengine_file: engine.yaml\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="sanka config migrate"):
        load_host(tmp_path / "config.yaml")


def test_privacy_levels_clamp_invalid_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    config = yaml.safe_load(yaml.safe_dump(BASE))
    config["engine"]["memory"]["privacy"] = {
        "enabled": "false",
        "default_level": 9,
        "ask_level": -1,
        "group_max_level": 2,
        "dm_max_level": 4,
        "auditor": True,
    }
    write_folder(tmp_path, config=config)

    loaded = load_config(tmp_path / "config.yaml", require_key=False)

    assert loaded.memory.privacy.enabled is False
    assert loaded.memory.privacy.default_level == 2
    assert loaded.memory.privacy.ask_level == 4


def test_approval_defaults_to_command_patterns(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    write_folder(tmp_path)

    loaded = load_config(tmp_path / "config.yaml", require_key=False)

    assert loaded.approval.sensitive_tools == ()
    assert loaded.approval.sensitive_commands == DEFAULT_SENSITIVE_COMMANDS


def test_approval_sensitive_commands_override_and_disable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    for value, expected in (([], ()), (["rm *"], ("rm *",))):
        config = yaml.safe_load(yaml.safe_dump(BASE))
        config["engine"]["approval"] = {"sensitive_commands": value}
        write_folder(tmp_path, config=config)

        loaded = load_config(tmp_path / "config.yaml", require_key=False)

        assert loaded.approval.sensitive_tools == ()
        assert loaded.approval.sensitive_commands == expected
