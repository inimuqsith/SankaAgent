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
