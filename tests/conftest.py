"""Shared fixtures for the test suite."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from sanka.config import SankaConfig, load_config


def valid_config_dict() -> dict[str, Any]:
    return {
        "llm": {
            "provider": "opencode-go",
            "model": "deepseek-v4.1-flash",
            "base_url": "https://opencode.ai/inference/go/openai/v1",
            "api_key_env": "OPENCODE_GO_API_KEY",
            "timeout_seconds": 120,
            "max_turns": 40,
        },
        "access": {
            "owner_numbers": ["628111111111"],
            "committee_numbers": ["628222222222"],
            "allowed_group_ids": ["120363000000000000@g.us"],
            "bot_ids": ["628999999999"],
        },
        "workspace": {
            "service_account_env": "GOOGLE_SERVICE_ACCOUNT_JSON",
            "read_allowlist": [],
            "write_allowlist": [],
        },
        "tools": {
            "shell_enabled": False,
            "browser_enabled": False,
            "browser_profile_dir": "",
        },
        "limits": {
            "max_reply_chars": 3500,
            "rate_limit_per_minute": 6,
            "rate_limit_per_day": 200,
            "outbound_per_minute": 20,
            "session_idle_ttl_minutes": 60,
            "inbound_dedup_ttl_minutes": 15,
        },
        "gateway": {
            "bridge_url": "http://127.0.0.1:3000",
            "require_mention": True,
            "dm_policy": "open",
            "group_policy": "allowlist",
            "send_read_receipts": False,
            "listen_host": "127.0.0.1",
            "listen_port": 8099,
            "auth_token_env": "SANKA_GATEWAY_TOKEN",
            "request_timeout_seconds": 10,
            "max_inbound_age_seconds": 300,
        },
    }


def write_config(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def config(tmp_path: Path) -> SankaConfig:
    path = write_config(tmp_path / "sanka.yaml", valid_config_dict())
    return load_config(path)
