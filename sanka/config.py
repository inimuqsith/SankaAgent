"""Configuration loading and validation.

The YAML file carries non-secret settings only (ADR-0014). Secrets are read
from the environment by a later plan, never from this file.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Hosts the gateway may bind to. Anything else would expose the token-protected
# HTTP surface beyond the local machine (ADR-0018).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class ConfigError(ValueError):
    """Raised when the configuration file is missing or invalid."""


@dataclass(frozen=True, slots=True)
class LLMConfig:
    provider: str
    model: str
    base_url: str
    api_key_env: str
    timeout_seconds: int
    max_turns: int


@dataclass(frozen=True, slots=True)
class AccessConfig:
    owner_numbers: tuple[str, ...]
    committee_numbers: tuple[str, ...]
    allowed_group_ids: tuple[str, ...]
    bot_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceConfig:
    service_account_env: str
    read_allowlist: tuple[str, ...]
    write_allowlist: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ToolsConfig:
    shell_enabled: bool
    browser_enabled: bool
    browser_profile_dir: str


@dataclass(frozen=True, slots=True)
class LimitsConfig:
    max_reply_chars: int
    rate_limit_per_minute: int
    rate_limit_per_day: int
    outbound_per_minute: int
    session_idle_ttl_minutes: int
    inbound_dedup_ttl_minutes: int


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    bridge_url: str
    require_mention: bool
    dm_policy: str
    group_policy: str
    send_read_receipts: bool
    listen_host: str
    listen_port: int
    auth_token_env: str
    request_timeout_seconds: int
    max_inbound_age_seconds: int


@dataclass(frozen=True, slots=True)
class SankaConfig:
    llm: LLMConfig
    access: AccessConfig
    workspace: WorkspaceConfig
    tools: ToolsConfig
    limits: LimitsConfig
    gateway: GatewayConfig


def load_config(path: str | Path) -> SankaConfig:
    """Load and validate a Sanka YAML config file.

    Raises:
        ConfigError: if the file cannot be read, is not valid YAML, or contains
            a missing/invalid key. Messages name the offending dotted key.
    """

    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read config file {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise ConfigError("config root must be a mapping")

    return _parse(raw)


def _parse(root: Mapping[str, Any]) -> SankaConfig:
    llm = _section(root, "llm")
    access = _section(root, "access")
    workspace = _section(root, "workspace")
    tools = _section(root, "tools")
    limits = _section(root, "limits")
    gateway = _section(root, "gateway")

    return SankaConfig(
        llm=LLMConfig(
            provider=_str(llm, "provider", "llm.provider"),
            model=_str(llm, "model", "llm.model"),
            base_url=_str(llm, "base_url", "llm.base_url"),
            api_key_env=_str(llm, "api_key_env", "llm.api_key_env"),
            timeout_seconds=_int(llm, "timeout_seconds", "llm.timeout_seconds"),
            max_turns=_int(llm, "max_turns", "llm.max_turns"),
        ),
        access=AccessConfig(
            owner_numbers=_str_tuple(access, "owner_numbers", "access.owner_numbers"),
            committee_numbers=_str_tuple(
                access, "committee_numbers", "access.committee_numbers"
            ),
            allowed_group_ids=_str_tuple(
                access, "allowed_group_ids", "access.allowed_group_ids"
            ),
            bot_ids=_str_tuple(access, "bot_ids", "access.bot_ids"),
        ),
        workspace=WorkspaceConfig(
            service_account_env=_str(
                workspace, "service_account_env", "workspace.service_account_env"
            ),
            read_allowlist=_str_tuple(
                workspace, "read_allowlist", "workspace.read_allowlist"
            ),
            write_allowlist=_str_tuple(
                workspace, "write_allowlist", "workspace.write_allowlist"
            ),
        ),
        tools=ToolsConfig(
            shell_enabled=_bool(tools, "shell_enabled", "tools.shell_enabled"),
            browser_enabled=_bool(tools, "browser_enabled", "tools.browser_enabled"),
            browser_profile_dir=_str(
                tools,
                "browser_profile_dir",
                "tools.browser_profile_dir",
                allow_empty=True,
            ),
        ),
        limits=LimitsConfig(
            max_reply_chars=_int(limits, "max_reply_chars", "limits.max_reply_chars"),
            rate_limit_per_minute=_int(
                limits, "rate_limit_per_minute", "limits.rate_limit_per_minute"
            ),
            rate_limit_per_day=_int(
                limits, "rate_limit_per_day", "limits.rate_limit_per_day"
            ),
            outbound_per_minute=_int(
                limits, "outbound_per_minute", "limits.outbound_per_minute"
            ),
            session_idle_ttl_minutes=_int(
                limits, "session_idle_ttl_minutes", "limits.session_idle_ttl_minutes"
            ),
            inbound_dedup_ttl_minutes=_int_in_range(
                limits,
                "inbound_dedup_ttl_minutes",
                "limits.inbound_dedup_ttl_minutes",
                1,
                1440,
            ),
        ),
        gateway=GatewayConfig(
            bridge_url=_str(gateway, "bridge_url", "gateway.bridge_url"),
            require_mention=_bool(
                gateway, "require_mention", "gateway.require_mention"
            ),
            dm_policy=_enum(
                gateway, "dm_policy", "gateway.dm_policy", {"open", "pairing"}
            ),
            group_policy=_enum(
                gateway,
                "group_policy",
                "gateway.group_policy",
                {"allowlist", "open"},
            ),
            send_read_receipts=_bool(
                gateway, "send_read_receipts", "gateway.send_read_receipts"
            ),
            listen_host=_loopback_host(gateway, "listen_host"),
            listen_port=_int_in_range(
                gateway, "listen_port", "gateway.listen_port", 1, 65535
            ),
            auth_token_env=_env_name(
                gateway, "auth_token_env", "gateway.auth_token_env"
            ),
            request_timeout_seconds=_int_in_range(
                gateway,
                "request_timeout_seconds",
                "gateway.request_timeout_seconds",
                1,
                60,
            ),
            max_inbound_age_seconds=_int_in_range(
                gateway,
                "max_inbound_age_seconds",
                "gateway.max_inbound_age_seconds",
                0,
                3600,
            ),
        ),
    )


def _section(root: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = root.get(key)
    if key not in root:
        raise ConfigError(f"missing required key: {key}")
    if not isinstance(value, Mapping):
        raise ConfigError(f"{key} must be a mapping")
    return value


def _value(section: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in section:
        raise ConfigError(f"missing required key: {path}")
    return section[key]


def _str(
    section: Mapping[str, Any], key: str, path: str, *, allow_empty: bool = False
) -> str:
    value = _value(section, key, path)
    if not isinstance(value, str):
        raise ConfigError(f"{path} must be a string")
    if not allow_empty and not value.strip():
        raise ConfigError(f"{path} must not be empty")
    return value


def _bool(section: Mapping[str, Any], key: str, path: str) -> bool:
    value = _value(section, key, path)
    if not isinstance(value, bool):
        raise ConfigError(f"{path} must be a boolean")
    return value


def _int(section: Mapping[str, Any], key: str, path: str) -> int:
    value = _value(section, key, path)
    # bool is a subclass of int; reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path} must be an integer")
    if value < 1:
        raise ConfigError(f"{path} must be a positive integer")
    return value


def _int_in_range(
    section: Mapping[str, Any], key: str, path: str, minimum: int, maximum: int
) -> int:
    value = _value(section, key, path)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{path} must be between {minimum} and {maximum}")
    return value


def _loopback_host(section: Mapping[str, Any], key: str) -> str:
    path = f"gateway.{key}"
    value = _str(section, key, path)
    if value not in LOOPBACK_HOSTS:
        raise ConfigError(
            f"{path} must be a loopback host (127.0.0.1, ::1, localhost)"
        )
    return value


def _env_name(section: Mapping[str, Any], key: str, path: str) -> str:
    value = _str(section, key, path)
    if _ENV_NAME_PATTERN.fullmatch(value) is None:
        raise ConfigError(f"{path} must be an environment variable name")
    return value


def _str_tuple(section: Mapping[str, Any], key: str, path: str) -> tuple[str, ...]:
    value = _value(section, key, path)
    if not isinstance(value, list):
        raise ConfigError(f"{path} must be a list of strings")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ConfigError(f"{path}[{index}] must be a string")
        items.append(item)
    return tuple(items)


def _enum(
    section: Mapping[str, Any], key: str, path: str, allowed: set[str]
) -> str:
    value = _str(section, key, path)
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ConfigError(f"{path} must be one of: {choices}")
    return value
