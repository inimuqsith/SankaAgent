"""Configuration loading and validation.

The YAML file carries non-secret settings only (ADR-0014). Secrets are read
from the environment by a later plan, never from this file.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

import yaml

# Hosts the gateway may bind to. Anything else would expose the token-protected
# HTTP surface beyond the local machine (ADR-0018).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SHEET_ALIAS_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SPREADSHEET_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,100}$")
_SHEET_RANGE_PATTERN = re.compile(
    r"^[A-Za-z0-9 _-]{1,31}![A-Z]{1,3}[0-9]{1,7}(:[A-Z]{1,3}[0-9]{1,7})?$"
)
_SCOPE_PATTERN = re.compile(r"^https://www\.googleapis\.com/auth/[a-z0-9._-]+$")
_RECAP_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_RECAP_METRIC_OPS = frozenset({"count", "sum", "mean", "min", "max"})
_NUMERIC_METRIC_OPS = frozenset({"sum", "mean", "min", "max"})
DEFAULT_GOOGLE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/spreadsheets",
)
_REPO_ROOT = Path(__file__).resolve().parent.parent


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
class AllowlistedSheet:
    """A spreadsheet the tools may read, addressed only by ``alias``."""

    alias: str
    spreadsheet_id: str


@dataclass(frozen=True, slots=True)
class WorkspaceConfig:
    service_account_env: str
    scopes: tuple[str, ...]
    read_allowlist: tuple[AllowlistedSheet, ...]
    write_allowlist: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecapConfig:
    max_groups: int = 20


@dataclass(frozen=True, slots=True)
class SearchConfig:
    provider: str = "searxng"
    base_url: str = ""
    api_key_env: str = ""
    max_results: int = 5
    timeout_seconds: int = 10
    max_chars: int = 4000


@dataclass(frozen=True, slots=True)
class DocsConfig:
    dir: str = ""
    max_bytes: int = 5_000_000
    max_chars: int = 12_000


@dataclass(frozen=True, slots=True)
class SheetsConfig:
    timeout_seconds: int = 20
    max_cells: int = 20_000
    max_chars: int = 8_000


@dataclass(frozen=True, slots=True)
class ToolsConfig:
    shell_enabled: bool
    browser_enabled: bool
    browser_profile_dir: str
    max_calls_per_turn: int = 6
    recap: RecapConfig = field(default_factory=RecapConfig)
    web_search: SearchConfig = field(default_factory=SearchConfig)
    docs: DocsConfig = field(default_factory=DocsConfig)
    sheets: SheetsConfig = field(default_factory=SheetsConfig)


@dataclass(frozen=True, slots=True)
class RecapFilter:
    column: str
    equals: str


@dataclass(frozen=True, slots=True)
class RecapMetric:
    op: str
    column: str | None = None


@dataclass(frozen=True, slots=True)
class RecapDefinition:
    """A recap configured by the owner; never assembled from model output."""

    name: str
    title: str
    alias: str
    range: str
    header_row: int = 1
    filters: tuple[RecapFilter, ...] = ()
    group_by: tuple[str, ...] = ()
    metrics: tuple[RecapMetric, ...] = ()
    columns: tuple[str, ...] = ()


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
class GroupContextConfig:
    enabled: bool = False
    dir: str = "~/.local/share/sanka/group-context"
    capture_all_groups: bool = False
    capture_group_ids: tuple[str, ...] = ()
    retention_hours: int = 24
    max_messages: int = 200
    inject_messages: int = 30
    inject_max_chars: int = 3500


@dataclass(frozen=True, slots=True)
class AttachmentsConfig:
    """Inbound media storage limits (plan-12).

    An empty ``dir`` disables the feature. The directory must be absolute and
    outside the repository; it never comes from a message or model argument.
    """

    dir: str = ""
    max_bytes: int = 25_000_000
    max_per_message: int = 4
    max_image_bytes: int = 8_000_000
    retention_minutes: int = 1440
    max_total_bytes: int = 536_870_912


@dataclass(frozen=True, slots=True)
class SankaConfig:
    llm: LLMConfig
    access: AccessConfig
    workspace: WorkspaceConfig
    tools: ToolsConfig
    limits: LimitsConfig
    gateway: GatewayConfig
    recaps: Mapping[str, RecapDefinition] = field(
        default_factory=lambda: MappingProxyType({})
    )
    group_context: GroupContextConfig = field(default_factory=GroupContextConfig)
    attachments: AttachmentsConfig = field(default_factory=AttachmentsConfig)


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

    read_allowlist = _read_allowlist(workspace)
    workspace_config = WorkspaceConfig(
        service_account_env=_str(
            workspace, "service_account_env", "workspace.service_account_env"
        ),
        scopes=_google_scopes(workspace),
        read_allowlist=read_allowlist,
        write_allowlist=_str_tuple(
            workspace, "write_allowlist", "workspace.write_allowlist"
        ),
    )
    tools_config = ToolsConfig(
        shell_enabled=_bool(tools, "shell_enabled", "tools.shell_enabled"),
        browser_enabled=_bool(tools, "browser_enabled", "tools.browser_enabled"),
        browser_profile_dir=_str(
            tools,
            "browser_profile_dir",
            "tools.browser_profile_dir",
            allow_empty=True,
        ),
        max_calls_per_turn=_optional_int(
            tools, "max_calls_per_turn", "tools.max_calls_per_turn", 6
        ),
        recap=_recap_config(tools),
        web_search=_search_config(tools),
        docs=_docs_config(tools),
        sheets=_sheets_config(tools),
    )

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
        workspace=workspace_config,
        tools=tools_config,
        recaps=_recap_definitions(root, workspace_config.read_allowlist),
        group_context=_group_context_config(root),
        attachments=_attachments_config(root),
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


def _optional_section(
    section: Mapping[str, Any], key: str, path: str
) -> Mapping[str, Any]:
    if key not in section:
        return {}
    value = section[key]
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path} must be a mapping")
    return value


def _optional_int(
    section: Mapping[str, Any], key: str, path: str, default: int
) -> int:
    if key not in section:
        return default
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path} must be an integer")
    if value < 1:
        raise ConfigError(f"{path} must be a positive integer")
    return value


def _optional_bool(
    section: Mapping[str, Any], key: str, path: str, default: bool
) -> bool:
    if key not in section:
        return default
    value = section[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{path} must be a boolean")
    return value


def _optional_str(
    section: Mapping[str, Any],
    key: str,
    path: str,
    default: str,
    *,
    allow_empty: bool = False,
) -> str:
    if key not in section:
        return default
    value = section[key]
    if not isinstance(value, str):
        raise ConfigError(f"{path} must be a string")
    if not allow_empty and not value.strip():
        raise ConfigError(f"{path} must not be empty")
    return value


def _optional_str_tuple(
    section: Mapping[str, Any], key: str, path: str
) -> tuple[str, ...]:
    if key not in section:
        return ()
    value = section[key]
    if not isinstance(value, list):
        raise ConfigError(f"{path} must be a list of strings")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ConfigError(f"{path}[{index}] must be a string")
        items.append(item)
    return tuple(items)


def _group_context_config(root: Mapping[str, Any]) -> GroupContextConfig:
    section = _optional_section(root, "group_context", "group_context")
    capture_ids = _optional_str_tuple(
        section, "capture_group_ids", "group_context.capture_group_ids"
    )
    for index, chat_id in enumerate(capture_ids):
        if not chat_id.endswith("@g.us"):
            raise ConfigError(
                f"group_context.capture_group_ids[{index}] must be a group JID"
            )

    directory = _optional_str(
        section,
        "dir",
        "group_context.dir",
        "~/.local/share/sanka/group-context",
    )
    expanded = Path(directory).expanduser()
    if not expanded.is_absolute():
        raise ConfigError("group_context.dir must be an absolute path")
    if expanded.resolve() == _REPO_ROOT or _REPO_ROOT in expanded.resolve().parents:
        raise ConfigError("group_context.dir must be outside the repository")

    enabled = _optional_bool(section, "enabled", "group_context.enabled", False)
    capture_all = _optional_bool(
        section, "capture_all_groups", "group_context.capture_all_groups", False
    )
    if enabled and not capture_all and not capture_ids:
        raise ConfigError(
            "group_context.enabled requires capture_all_groups or capture_group_ids"
        )

    retention_hours = _optional_int(
        section, "retention_hours", "group_context.retention_hours", 24
    )
    if not 1 <= retention_hours <= 24 * 30:
        raise ConfigError("group_context.retention_hours must be between 1 and 720")

    max_messages = _optional_int(
        section, "max_messages", "group_context.max_messages", 200
    )
    if not 1 <= max_messages <= 2000:
        raise ConfigError("group_context.max_messages must be between 1 and 2000")

    inject_messages = _optional_int(
        section, "inject_messages", "group_context.inject_messages", 30
    )
    if not 1 <= inject_messages <= 200:
        raise ConfigError("group_context.inject_messages must be between 1 and 200")
    if inject_messages > max_messages:
        raise ConfigError(
            "group_context.inject_messages must not exceed max_messages"
        )

    inject_max_chars = _optional_int(
        section, "inject_max_chars", "group_context.inject_max_chars", 3500
    )
    if not 500 <= inject_max_chars <= 20000:
        raise ConfigError(
            "group_context.inject_max_chars must be between 500 and 20000"
        )

    return GroupContextConfig(
        enabled=enabled,
        dir=str(expanded),
        capture_all_groups=capture_all,
        capture_group_ids=capture_ids,
        retention_hours=retention_hours,
        max_messages=max_messages,
        inject_messages=inject_messages,
        inject_max_chars=inject_max_chars,
    )


def _attachments_config(root: Mapping[str, Any]) -> AttachmentsConfig:
    section = _optional_section(root, "attachments", "attachments")
    directory = _optional_str(
        section, "dir", "attachments.dir", "", allow_empty=True
    )
    if directory:
        expanded = Path(directory).expanduser()
        if not expanded.is_absolute():
            raise ConfigError("attachments.dir must be an absolute path")
        resolved = expanded.resolve()
        if resolved.is_relative_to(_REPO_ROOT):
            raise ConfigError("attachments.dir must be outside the repository")
        directory = str(expanded)

    max_bytes = _optional_int(
        section, "max_bytes", "attachments.max_bytes", 25_000_000
    )
    if not 1 <= max_bytes <= 100_000_000:
        raise ConfigError("attachments.max_bytes must be between 1 and 100000000")

    max_per_message = _optional_int(
        section, "max_per_message", "attachments.max_per_message", 4
    )
    if not 1 <= max_per_message <= 10:
        raise ConfigError("attachments.max_per_message must be between 1 and 10")

    max_image_bytes = _optional_int(
        section, "max_image_bytes", "attachments.max_image_bytes", 8_000_000
    )
    if not 1 <= max_image_bytes <= max_bytes:
        raise ConfigError("attachments.max_image_bytes must not exceed max_bytes")

    retention_minutes = _optional_int(
        section, "retention_minutes", "attachments.retention_minutes", 1440
    )
    if not 1 <= retention_minutes <= 10_080:
        raise ConfigError(
            "attachments.retention_minutes must be between 1 and 10080"
        )

    max_total_bytes = _optional_int(
        section,
        "max_total_bytes",
        "attachments.max_total_bytes",
        536_870_912,
    )
    if max_total_bytes < max_bytes:
        raise ConfigError("attachments.max_total_bytes must be at least max_bytes")

    return AttachmentsConfig(
        dir=directory,
        max_bytes=max_bytes,
        max_per_message=max_per_message,
        max_image_bytes=max_image_bytes,
        retention_minutes=retention_minutes,
        max_total_bytes=max_total_bytes,
    )


def _optional_env_name(
    section: Mapping[str, Any], key: str, path: str, default: str = ""
) -> str:
    value = _optional_str(section, key, path, default, allow_empty=True)
    if value and _ENV_NAME_PATTERN.fullmatch(value) is None:
        raise ConfigError(f"{path} must be an environment variable name")
    return value


def _optional_enum(
    section: Mapping[str, Any],
    key: str,
    path: str,
    allowed: set[str],
    default: str,
) -> str:
    if key not in section:
        return default
    return _enum(section, key, path, allowed)


def _optional_str_list(
    section: Mapping[str, Any], key: str, path: str
) -> tuple[str, ...]:
    if key not in section:
        return ()
    value = section[key]
    if not isinstance(value, list):
        raise ConfigError(f"{path} must be a list of strings")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ConfigError(f"{path}[{index}] must be a string")
        items.append(item)
    return tuple(items)


def _read_allowlist(workspace: Mapping[str, Any]) -> tuple[AllowlistedSheet, ...]:
    raw = _value(workspace, "read_allowlist", "workspace.read_allowlist")
    if not isinstance(raw, list):
        raise ConfigError(
            "workspace.read_allowlist must be a list of {alias, spreadsheet_id} entries"
        )
    entries: list[AllowlistedSheet] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        path = f"workspace.read_allowlist[{index}]"
        if isinstance(item, str):
            raise ConfigError(
                "workspace.read_allowlist changed shape in plan-5: replace the "
                "string list with alias entries, for example\n"
                "  - alias: kehadiran\n"
                '    spreadsheet_id: "<spreadsheet id>"'
            )
        if not isinstance(item, Mapping):
            raise ConfigError(
                f"{path} must be a mapping with alias and spreadsheet_id"
            )
        alias = item.get("alias")
        if (
            not isinstance(alias, str)
            or _SHEET_ALIAS_PATTERN.fullmatch(alias) is None
        ):
            raise ConfigError(
                f"{path}.alias must match ^[a-z][a-z0-9_-]{{0,31}}$"
            )
        if alias in seen:
            raise ConfigError(f"{path}.alias duplicates {alias!r}")
        spreadsheet_id = item.get("spreadsheet_id")
        if (
            not isinstance(spreadsheet_id, str)
            or _SPREADSHEET_ID_PATTERN.fullmatch(spreadsheet_id) is None
        ):
            raise ConfigError(
                f"{path}.spreadsheet_id must be 20-100 characters of "
                "A-Z, a-z, 0-9, _ or -"
            )
        seen.add(alias)
        entries.append(
            AllowlistedSheet(alias=alias, spreadsheet_id=spreadsheet_id)
        )
    return tuple(entries)


def _google_scopes(workspace: Mapping[str, Any]) -> tuple[str, ...]:
    if "scopes" not in workspace:
        return DEFAULT_GOOGLE_SCOPES
    raw = workspace["scopes"]
    if not isinstance(raw, list) or not raw:
        raise ConfigError(
            "workspace.scopes must be a non-empty list of Google OAuth scope URLs"
        )
    scopes: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or _SCOPE_PATTERN.fullmatch(item) is None:
            raise ConfigError(
                f"workspace.scopes[{index}] must be a "
                "https://www.googleapis.com/auth/... scope"
            )
        scopes.append(item)
    return tuple(scopes)


def _validate_search_base_url(value: str) -> None:
    if not value:
        return
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ConfigError(
            "tools.web_search.base_url must be an https:// URL "
            "(http:// is allowed only for loopback hosts)"
        )
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ConfigError(
            "tools.web_search.base_url must not contain credentials, a query, "
            "or a fragment"
        )
    if parts.scheme == "http" and parts.hostname not in LOOPBACK_HOSTS:
        raise ConfigError(
            "tools.web_search.base_url may use http:// only for a loopback host"
        )


def _validate_docs_dir(value: str) -> None:
    if not value:
        return
    path = Path(value)
    if not path.is_absolute():
        raise ConfigError("tools.docs.dir must be an absolute path")
    resolved = path.resolve()
    if resolved.is_relative_to(_REPO_ROOT):
        raise ConfigError("tools.docs.dir must be outside the repository")


def _search_config(tools: Mapping[str, Any]) -> SearchConfig:
    section = _optional_section(tools, "web_search", "tools.web_search")
    base_url = _optional_str(
        section,
        "base_url",
        "tools.web_search.base_url",
        "",
        allow_empty=True,
    )
    _validate_search_base_url(base_url)
    return SearchConfig(
        provider=_optional_enum(
            section,
            "provider",
            "tools.web_search.provider",
            {"searxng"},
            "searxng",
        ),
        base_url=base_url,
        api_key_env=_optional_env_name(
            section, "api_key_env", "tools.web_search.api_key_env"
        ),
        max_results=_optional_int(
            section, "max_results", "tools.web_search.max_results", 5
        ),
        timeout_seconds=_optional_int(
            section, "timeout_seconds", "tools.web_search.timeout_seconds", 10
        ),
        max_chars=_optional_int(
            section, "max_chars", "tools.web_search.max_chars", 4000
        ),
    )


def _docs_config(tools: Mapping[str, Any]) -> DocsConfig:
    section = _optional_section(tools, "docs", "tools.docs")
    directory = _optional_str(
        section, "dir", "tools.docs.dir", "", allow_empty=True
    )
    _validate_docs_dir(directory)
    return DocsConfig(
        dir=directory,
        max_bytes=_optional_int(
            section, "max_bytes", "tools.docs.max_bytes", 5_000_000
        ),
        max_chars=_optional_int(
            section, "max_chars", "tools.docs.max_chars", 12_000
        ),
    )


def _sheets_config(tools: Mapping[str, Any]) -> SheetsConfig:
    section = _optional_section(tools, "sheets", "tools.sheets")
    return SheetsConfig(
        timeout_seconds=_optional_int(
            section, "timeout_seconds", "tools.sheets.timeout_seconds", 20
        ),
        max_cells=_optional_int(
            section, "max_cells", "tools.sheets.max_cells", 20_000
        ),
        max_chars=_optional_int(
            section, "max_chars", "tools.sheets.max_chars", 8_000
        ),
    )


def _recap_config(tools: Mapping[str, Any]) -> RecapConfig:
    section = _optional_section(tools, "recap", "tools.recap")
    return RecapConfig(
        max_groups=_optional_int(
            section, "max_groups", "tools.recap.max_groups", 20
        )
    )


def _recap_filters(
    item: Mapping[str, Any], name: str
) -> tuple[RecapFilter, ...]:
    raw = item.get("filters", [])
    if not isinstance(raw, list):
        raise ConfigError(f"recaps.{name}.filters must be a list")
    filters: list[RecapFilter] = []
    for index, entry in enumerate(raw):
        path = f"recaps.{name}.filters[{index}]"
        if not isinstance(entry, Mapping):
            raise ConfigError(f"{path} must be a mapping")
        filters.append(
            RecapFilter(
                column=_str(entry, "column", f"{path}.column"),
                equals=_str(
                    entry, "equals", f"{path}.equals", allow_empty=True
                ),
            )
        )
    return tuple(filters)


def _recap_metrics(
    item: Mapping[str, Any], name: str
) -> tuple[RecapMetric, ...]:
    raw = item.get("metrics", [])
    if not isinstance(raw, list):
        raise ConfigError(f"recaps.{name}.metrics must be a list")
    metrics: list[RecapMetric] = []
    for index, entry in enumerate(raw):
        path = f"recaps.{name}.metrics[{index}]"
        if not isinstance(entry, Mapping):
            raise ConfigError(f"{path} must be a mapping")
        op = _enum(entry, "op", f"{path}.op", _RECAP_METRIC_OPS)
        column: str | None = None
        if op in _NUMERIC_METRIC_OPS:
            column = _str(entry, "column", f"{path}.column")
        elif "column" in entry:
            column = (
                _optional_str(
                    entry,
                    "column",
                    f"{path}.column",
                    "",
                    allow_empty=True,
                )
                or None
            )
        metrics.append(RecapMetric(op=op, column=column))
    return tuple(metrics)


def _recap_definitions(
    root: Mapping[str, Any],
    allowlist: tuple[AllowlistedSheet, ...],
) -> Mapping[str, RecapDefinition]:
    raw = root.get("recaps", {})
    if not isinstance(raw, Mapping):
        raise ConfigError("recaps must be a mapping of recap name to definition")
    aliases = {sheet.alias for sheet in allowlist}
    definitions: dict[str, RecapDefinition] = {}
    for name, item in raw.items():
        if (
            not isinstance(name, str)
            or _RECAP_NAME_PATTERN.fullmatch(name) is None
        ):
            raise ConfigError(
                "recap names must match ^[a-z][a-z0-9_-]{0,31}$"
            )
        path = f"recaps.{name}"
        if not isinstance(item, Mapping):
            raise ConfigError(f"{path} must be a mapping")
        title = _str(item, "title", f"{path}.title")
        alias = _str(item, "alias", f"{path}.alias")
        if alias not in aliases:
            raise ConfigError(
                f"{path}.alias must reference an alias in "
                "workspace.read_allowlist"
            )
        cell_range = _str(item, "range", f"{path}.range")
        if _SHEET_RANGE_PATTERN.fullmatch(cell_range) is None:
            raise ConfigError(
                f"{path}.range must be a single-tab A1 range such as "
                "'Sheet1!A1:F100'"
            )
        filters = _recap_filters(item, name)
        group_by = _optional_str_list(item, "group_by", f"{path}.group_by")
        if len(group_by) > 2:
            raise ConfigError(f"{path}.group_by allows at most 2 columns")
        metrics = _recap_metrics(item, name)
        columns = _optional_str_list(item, "columns", f"{path}.columns")
        if len(columns) != len(set(columns)):
            raise ConfigError(f"{path}.columns must not contain duplicates")
        if columns:
            referenced = [entry.column for entry in filters]
            referenced.extend(group_by)
            referenced.extend(
                metric.column for metric in metrics if metric.column
            )
            for column in referenced:
                if column not in columns:
                    raise ConfigError(
                        f"{path}: column {column!r} is not listed in columns"
                    )
        definitions[name] = RecapDefinition(
            name=name,
            title=title,
            alias=alias,
            range=cell_range,
            header_row=_optional_int(
                item, "header_row", f"{path}.header_row", 1
            ),
            filters=filters,
            group_by=group_by,
            metrics=metrics,
            columns=columns,
        )
    return MappingProxyType(definitions)
