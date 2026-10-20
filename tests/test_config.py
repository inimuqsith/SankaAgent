"""Tests for config loading and fail-fast validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sanka.config import ConfigError, load_config
from tests.conftest import valid_config_dict, write_config


def test_loads_valid_config(config) -> None:
    assert config.llm.model == "deepseek-v4.1-flash"
    assert config.access.owner_numbers == ("628111111111",)
    assert config.limits.rate_limit_per_minute == 6
    assert config.tools.shell_enabled is False
    assert config.gateway.require_mention is True


def test_missing_section_names_key(tmp_path: Path) -> None:
    data = valid_config_dict()
    del data["limits"]
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError, match="limits"):
        load_config(path)


def test_missing_nested_key_names_dotted_path(tmp_path: Path) -> None:
    data = valid_config_dict()
    del data["llm"]["model"]
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError, match=r"llm\.model"):
        load_config(path)


@pytest.mark.parametrize("value", [0, -1, "six", True])
def test_invalid_rate_limit_fails_fast(tmp_path: Path, value: Any) -> None:
    data = valid_config_dict()
    data["limits"]["rate_limit_per_minute"] = value
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError, match=r"limits\.rate_limit_per_minute"):
        load_config(path)


def test_invalid_enum_fails_fast(tmp_path: Path) -> None:
    data = valid_config_dict()
    data["gateway"]["dm_policy"] = "everyone"
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError, match=r"gateway\.dm_policy"):
        load_config(path)


def test_missing_file_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read config file"):
        load_config(tmp_path / "nope.yaml")


def test_empty_base_url_fails_fast(tmp_path: Path) -> None:
    data = valid_config_dict()
    data["llm"]["base_url"] = ""
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError, match=r"llm\.base_url"):
        load_config(path)


def test_non_mapping_root_fails_fast(tmp_path: Path) -> None:
    path = tmp_path / "sanka.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="root must be a mapping"):
        load_config(path)


def test_loads_gateway_keys(config) -> None:
    assert config.access.bot_ids == ("628999999999",)
    assert config.limits.inbound_dedup_ttl_minutes == 15
    assert config.gateway.listen_host == "127.0.0.1"
    assert config.gateway.listen_port == 8099
    assert config.gateway.auth_token_env == "SANKA_GATEWAY_TOKEN"
    assert config.gateway.request_timeout_seconds == 10
    assert config.gateway.max_inbound_age_seconds == 300


def test_non_loopback_listen_host_fails_fast(tmp_path: Path) -> None:
    data = valid_config_dict()
    data["gateway"]["listen_host"] = "0.0.0.0"
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError, match=r"gateway\.listen_host"):
        load_config(path)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("listen_port", 0),
        ("listen_port", 65536),
        ("listen_port", True),
        ("request_timeout_seconds", 0),
        ("request_timeout_seconds", 61),
        ("max_inbound_age_seconds", -1),
        ("max_inbound_age_seconds", 3601),
    ],
)
def test_gateway_integer_ranges_fail_fast(
    tmp_path: Path, key: str, value: Any
) -> None:
    data = valid_config_dict()
    data["gateway"][key] = value
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError, match=rf"gateway\.{key}"):
        load_config(path)


def test_invalid_auth_token_env_fails_fast(tmp_path: Path) -> None:
    data = valid_config_dict()
    data["gateway"]["auth_token_env"] = "not-a-name"
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError, match=r"gateway\.auth_token_env"):
        load_config(path)


def test_zero_max_inbound_age_disables_check(tmp_path: Path) -> None:
    data = valid_config_dict()
    data["gateway"]["max_inbound_age_seconds"] = 0
    path = write_config(tmp_path / "sanka.yaml", data)

    assert load_config(path).gateway.max_inbound_age_seconds == 0


def test_zero_dedup_ttl_fails_fast(tmp_path: Path) -> None:
    data = valid_config_dict()
    data["limits"]["inbound_dedup_ttl_minutes"] = 0
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError, match=r"limits\.inbound_dedup_ttl_minutes"):
        load_config(path)


def test_group_context_defaults_to_disabled(config) -> None:
    assert config.group_context.enabled is False
    assert config.group_context.capture_all_groups is False


def test_enabled_group_context_requires_a_target(tmp_path: Path) -> None:
    data = valid_config_dict()
    data["group_context"] = {"enabled": True}
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError, match="capture_all_groups"):
        load_config(path)


def test_group_context_rejects_repository_directory(tmp_path: Path) -> None:
    data = valid_config_dict()
    data["group_context"] = {
        "enabled": True,
        "capture_all_groups": True,
        "dir": str(Path.cwd() / "data" / "group-context"),
    }
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError, match="outside the repository"):
        load_config(path)


def test_group_context_inject_messages_cannot_exceed_store(tmp_path: Path) -> None:
    data = valid_config_dict()
    data["group_context"] = {
        "enabled": True,
        "capture_all_groups": True,
        "max_messages": 100,
        "inject_messages": 150,
    }
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError, match="inject_messages"):
        load_config(path)


def test_attachments_default_to_disabled(config) -> None:
    assert config.attachments.dir == ""
    assert config.attachments.max_bytes == 25_000_000
    assert config.attachments.max_per_message == 4


def test_attachments_accept_absolute_directory_outside_repo(
    tmp_path: Path,
) -> None:
    data = valid_config_dict()
    directory = tmp_path / "media"
    data["attachments"] = {"dir": str(directory)}
    path = write_config(tmp_path / "sanka.yaml", data)

    config = load_config(path)

    assert config.attachments.dir == str(directory)
    assert config.attachments.retention_minutes == 1440


@pytest.mark.parametrize(
    "value, pattern",
    [
        ("relative/path", r"attachments\.dir"),
        ("/home/muqsith/Projects/SankaAgent/media", r"attachments\.dir"),
    ],
)
def test_attachments_reject_unsafe_directories(
    tmp_path: Path, value: str, pattern: str
) -> None:
    data = valid_config_dict()
    data["attachments"] = {"dir": value}
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError, match=pattern):
        load_config(path)


@pytest.mark.parametrize(
    "settings, pattern",
    [
        ({"max_bytes": 0}, r"attachments\.max_bytes"),
        ({"max_per_message": 11}, r"attachments\.max_per_message"),
        (
            {"max_bytes": 100, "max_image_bytes": 101},
            r"attachments\.max_image_bytes",
        ),
        ({"retention_minutes": 0}, r"attachments\.retention_minutes"),
        (
            {
                "max_bytes": 1000,
                "max_image_bytes": 1000,
                "max_total_bytes": 999,
            },
            r"attachments\.max_total_bytes",
        ),
    ],
)
def test_attachments_reject_invalid_limits(
    tmp_path: Path, settings: dict[str, Any], pattern: str
) -> None:
    data = valid_config_dict()
    data["attachments"] = settings
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError, match=pattern):
        load_config(path)
