"""Shared fixtures for the test suite."""

from __future__ import annotations

import ipaddress
import socket
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


class NetworkBlockedError(RuntimeError):
    """Raised when a test tries to reach a non-loopback network address."""


def _is_loopback_host(host: Any) -> bool:
    if host is None:
        return False
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check_address(address: Any) -> None:
    # AF_UNIX sockets use a filesystem path; they never leave the machine.
    if isinstance(address, (str, bytes)):
        return
    if isinstance(address, tuple) and address and _is_loopback_host(address[0]):
        return
    raise NetworkBlockedError(
        f"network access is blocked in tests (target={address!r})"
    )


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that opens a non-loopback socket.

    Tests use fakes and ``httpx.MockTransport``. Loopback stays available for
    the small number of tests that run the gateway against a local server.
    """

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        _check_address(address)
        return real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> Any:
        _check_address(address)
        return real_connect_ex(self, address)

    def guarded_create_connection(
        address: Any, *args: Any, **kwargs: Any
    ) -> socket.socket:
        _check_address(address)
        return real_create_connection(address, *args, **kwargs)

    def guarded_getaddrinfo(
        host: Any, port: Any, *args: Any, **kwargs: Any
    ) -> list[Any]:
        # ``host=None`` is a local bind/passive lookup.
        if host is not None and not _is_loopback_host(host):
            raise NetworkBlockedError(
                f"DNS access is blocked in tests (host={host!r})"
            )
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
