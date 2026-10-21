"""Muat .env, baca config.yaml, validasi ketat saat start."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    api_key_env: str
    max_turns: int = 20
    timeout_seconds: float = 120.0


@dataclass(frozen=True)
class WhatsAppConfig:
    session_db: Path
    require_mention: bool = True
    bot_name: str = "sanka"


@dataclass(frozen=True)
class RoleConfig:
    name: str
    users: tuple[str, ...] = ()
    tools: tuple[str, ...] | None = None
    commands: tuple[str, ...] | None = None


@dataclass(frozen=True)
class SandboxConfig:
    runtime: str = "podman"
    image: str = "docker.io/library/python:3.12-slim"
    timeout_seconds: float = 120.0


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    persona: str
    llm: LLMConfig
    workspace: Path
    whatsapp: WhatsAppConfig
    roles: dict[str, RoleConfig]
    default_role: str
    sandbox: SandboxConfig
    mcp: dict[str, MCPServerConfig]

    def api_key(self) -> str:
        return os.environ[self.llm.api_key_env]


def load_env(path: str = ".env") -> None:
    """Baca KEY=VALUE sederhana; baris kosong/komentar dilewati. File hilang bukan error."""
    file = Path(path)
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _fail(message: str) -> SystemExit:
    log.error(message)
    return SystemExit(message)


def _expand(value: str) -> Path:
    return Path(value).expanduser()


def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, (str, int)) and str(item))


def _optional_str_tuple(value: object) -> tuple[str, ...] | None:
    """Field tidak ditulis = None (pakai default handler); `[]` = () = tolak semua."""
    return None if value is None else _str_tuple(value)


def _roles(raw: object) -> dict[str, RoleConfig]:
    if not isinstance(raw, dict) or not raw:
        raise _fail("config: 'roles' wajib berisi minimal satu role")
    roles: dict[str, RoleConfig] = {}
    for name, body in raw.items():
        body = body if isinstance(body, dict) else {}
        roles[str(name)] = RoleConfig(
            name=str(name),
            users=_str_tuple(body.get("users")),
            tools=_optional_str_tuple(body.get("tools")),
            commands=_optional_str_tuple(body.get("commands")),
        )
    return roles


def _mcp_servers(raw: object) -> dict[str, MCPServerConfig]:
    servers: object = (raw or {}).get("servers") if isinstance(raw, dict) else None
    if not isinstance(servers, dict):
        return {}
    result: dict[str, MCPServerConfig] = {}
    for name, body in servers.items():
        body = body if isinstance(body, dict) else {}
        command = body.get("command")
        if not isinstance(command, str) or not command:
            raise _fail(f"config: mcp server '{name}' butuh 'command'")
        env = body.get("env") if isinstance(body.get("env"), dict) else {}
        result[str(name)] = MCPServerConfig(
            name=str(name),
            command=command,
            args=_str_tuple(body.get("args")),
            env={str(k): str(v) for k, v in env.items()},
        )
    return result


def load_config(path: str | None = None) -> Config:
    target = Path(path or os.environ.get("SANKA_CONFIG") or "config.yaml")
    if not target.is_file():
        raise _fail(f"config tidak ditemukan: {target} (salin dari config.example.yaml)")
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise _fail(f"config YAML tidak valid: {exc}") from exc
    if not isinstance(raw, dict):
        raise _fail(f"config kosong atau bukan mapping: {target}")

    llm_raw = raw.get("llm") if isinstance(raw.get("llm"), dict) else {}
    api_key_env = str(llm_raw.get("api_key_env") or "OPENCODE_GO_API_KEY")
    if not os.environ.get(api_key_env):
        raise _fail(f"env {api_key_env} belum diisi (taruh di .env)")

    roles = _roles(raw.get("roles"))
    default_role = str(raw.get("default_role") or "")
    if default_role not in roles:
        raise _fail(f"config: default_role '{default_role}' tidak ada di roles")

    wa_raw = raw.get("whatsapp") if isinstance(raw.get("whatsapp"), dict) else {}
    sandbox_raw = raw.get("sandbox") if isinstance(raw.get("sandbox"), dict) else {}

    workspace = _expand(str(raw.get("workspace") or "~/.sanka/workspace"))
    workspace.mkdir(parents=True, exist_ok=True)
    session_db = _expand(str(wa_raw.get("session_db") or "~/.sanka/whatsapp.db"))
    session_db.parent.mkdir(parents=True, exist_ok=True)

    return Config(
        persona=str(raw.get("persona") or "").strip(),
        llm=LLMConfig(
            base_url=str(llm_raw.get("base_url") or "https://opencode.ai/inference/go/openai/v1"),
            model=str(llm_raw.get("model") or "deepseek-v4.1-flash"),
            api_key_env=api_key_env,
            max_turns=int(llm_raw.get("max_turns") or 20),
            timeout_seconds=float(llm_raw.get("timeout_seconds") or 120),
        ),
        workspace=workspace,
        whatsapp=WhatsAppConfig(
            session_db=session_db,
            require_mention=bool(wa_raw.get("require_mention", True)),
            bot_name=str(wa_raw.get("bot_name") or "sanka"),
        ),
        roles=roles,
        default_role=default_role,
        sandbox=SandboxConfig(
            runtime=str(sandbox_raw.get("runtime") or "podman"),
            image=str(sandbox_raw.get("image") or "docker.io/library/python:3.12-slim"),
            timeout_seconds=float(sandbox_raw.get("timeout_seconds") or 120),
        ),
        mcp=_mcp_servers(raw.get("mcp")),
    )
