"""Load .env plus the combined config.yaml (host, engine, gateways, soul) and roles.yaml.

Validation is strict and never touches the filesystem beyond reading; one file is the
source of truth for the host, and DB documents override its engine/soul/role seeds.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

from .roles import RoleError, RoleRegistry, load_roles
from .soul import Soul, load_soul

log = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Invalid configuration; the message is safe to show to the operator."""


DEFAULT_SENSITIVE_COMMANDS: tuple[str, ...] = (
    "rm *",
    "rmdir *",
    "shred *",
    "dd *",
    "truncate *",
    "wipefs *",
    "mkfs*",
    "fdisk *",
    "parted *",
    "shutdown *",
    "reboot *",
    "poweroff *",
    "halt *",
    "systemctl *",
    "service *",
    "kill *",
    "pkill *",
    "killall *",
    "chmod *",
    "chown *",
    "chgrp *",
    "mount *",
    "umount *",
    "git reset --hard*",
    "git clean -*f*",
    "git push *--force*",
    "git push -f*",
    "find * -delete*",
    "find * -exec rm*",
)


class Documents(Protocol):
    def get(self, name: str) -> str | None: ...

    def names(self, prefix: str = "") -> list[str]: ...


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = "https://opencode.ai/inference/go/openai/v1"
    model: str = "deepseek-v4.1-flash"
    api_key_env: str = "OPENCODE_GO_API_KEY"
    timeout_seconds: float = 120.0
    temperature: float = 0.9
    run_budget_seconds: int = 240


@dataclass(frozen=True)
class DatabaseConfig:
    url: str = ""
    url_env: str = "SANKA_DATABASE_URL"
    pool_size: int = 5
    pool_timeout: float = 30.0


@dataclass(frozen=True)
class SandboxConfig:
    runtime: str = ""  # kosong = autodetect podman lalu docker
    image: str = "docker.io/library/python:3.12-slim"
    timeout_seconds: float = 120.0
    network: str = "none"  # none | bridge | nama network lain
    host_workspace: Path | None = None  # source mount bila daemon ada di host
    env: tuple[str, ...] = ()  # variabel tambahan yang boleh masuk container


@dataclass(frozen=True)
class WebSearchConfig:
    base_url: str = ""  # kosong = hanya backend ddgs
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class EmbedderConfig:
    provider: str = "huggingface"
    model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    dimensions: int = 384


@dataclass(frozen=True)
class PrivacyConfig:
    """Disclosure policy limits: level 0-4, group vs DM ceilings, optional auditor."""

    enabled: bool = True
    default_level: int = 2
    ask_level: int = 4
    group_max_level: int = 2
    dm_max_level: int = 4
    auditor: bool = True


@dataclass(frozen=True)
class MemoryConfig:
    backend: str = "mem0"  # mem0 | null
    auto_capture: bool = True
    capture_min_chars: int = 8
    max_items: int = 12
    max_chars: int = 3000
    collection: str = "sanka_memories"
    extraction_model: str = ""  # empty = llm.model
    embedder: EmbedderConfig = field(default_factory=EmbedderConfig)
    telemetry: bool = False
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)


@dataclass(frozen=True)
class ReplyConfig:
    quiet_seconds: int = 600


@dataclass(frozen=True)
class ApprovalConfig:
    mode: str = "yolo"  # yolo | off | strict
    sensitive_tools: tuple[str, ...] = ()
    sensitive_commands: tuple[str, ...] = DEFAULT_SENSITIVE_COMMANDS
    settings: bool = True
    ttl_seconds: int = 900


@dataclass(frozen=True)
class SkillsConfig:
    max_index_chars: int = 3000


@dataclass(frozen=True)
class PromptConfig:
    salience: bool = True
    max_chars: int = 9000


@dataclass(frozen=True)
class HumanizeConfig:
    enabled: bool = False
    cpms: float = 25.0
    min_delay: float = 0.5
    max_delay: float = 5.0
    between_delay: float = 0.7
    max_bubbles: int = 3
    reaction_stickers: tuple[str, ...] = ()


@dataclass(frozen=True)
class VibeConfig:
    enabled: bool = True  # fail-open bila torch/model tidak ada
    model: str = "Aardiiiiy/EmoSense-ID-Indonesian-Emotion-Classifier"
    max_length: int = 128
    attack: float = 0.35
    baseline_influence: float = 0.05
    hold_seconds: float = 1.2
    neutral_floor: float = 0.35
    tick_seconds: float = 1.0


@dataclass(frozen=True)
class AffectConfig:
    enabled: bool = True
    volatility: str = "expressive"
    grudge_half_life_hours: float = 1.5
    vibe: VibeConfig = field(default_factory=VibeConfig)


@dataclass(frozen=True)
class StyleConfig:
    enabled: bool = True
    max_chars: int = 800


@dataclass(frozen=True)
class AmbientConfig:
    enabled: bool = True
    tick_seconds: int = 10
    digest_every: int = 12
    digest_cooldown: int = 900
    history_window: int = 30
    idle_minutes: int = 20
    research_cooldown: int = 21600
    research_per_day: int = 6
    research_results: int = 5


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: str | None = None  # stdio; None bila server memakai url
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None  # Streamable HTTP


@dataclass(frozen=True)
class GatewaySettings:
    name: str
    enabled: bool = False
    options: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Paths:
    config: Path
    roles: Path
    soul_md: Path
    workspace: Path


@dataclass(frozen=True)
class HostConfig:
    database: DatabaseConfig
    workspace: Path
    sandbox: SandboxConfig
    web_search: WebSearchConfig
    mcp: dict[str, MCPServerConfig]
    roles_file: Path
    sync_documents: bool


@dataclass(frozen=True)
class Config:
    paths: Paths
    database: DatabaseConfig
    documents: Documents | None
    llm: LLMConfig
    workspace: Path
    reply: ReplyConfig
    approval: ApprovalConfig
    roles: RoleRegistry
    default_role: str
    gateways: dict[str, GatewaySettings]
    soul: Soul
    sandbox: SandboxConfig
    mcp: dict[str, MCPServerConfig]
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
    humanize: HumanizeConfig = field(default_factory=HumanizeConfig)
    style: StyleConfig = field(default_factory=StyleConfig)
    affect: AffectConfig = field(default_factory=AffectConfig)
    ambient: AmbientConfig = field(default_factory=AmbientConfig)

    def api_key(self) -> str:
        return os.environ[self.llm.api_key_env]

    def database_url(self) -> str:
        return resolve_database_url(self.database)


def resolve_database_url(database: DatabaseConfig) -> str:
    """Effective URL: env `url_env` when set, otherwise `url`."""
    env_name = str(database.url_env or "").strip()
    if env_name and os.environ.get(env_name):
        return str(os.environ[env_name]).strip()
    return str(database.url or "").strip()


def load_env(path: str | Path = ".env") -> None:
    """Read simple KEY=VALUE lines from a file in the working directory. Missing is fine."""
    file = Path(str(path)).expanduser()
    if file.is_absolute() or file.name != str(path):
        raise ConfigError(f"env file must be a bare file name in the working directory: {path}")
    file = (Path.cwd() / file.name).resolve()
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


LEGACY_DATA = Path("~/.sanka").expanduser()
# Split config layouts that must be migrated into config.yaml + roles.yaml first.
LEGACY_KEYS = ("whatsapp", "roles", "persona", "engine_file", "gateway_file", "roles_dir", "soul_file")


def secure_db(path: Path) -> None:
    """The session DB holds credentials; never leave it world-readable."""
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    if path.exists():
        try:
            path.chmod(0o600)
        except OSError:
            pass


def drop_session(path: Path) -> None:
    """Delete a temporary session DB together with its WAL/SHM files."""
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(f"{path}{suffix}").unlink(missing_ok=True)
        except OSError:
            log.warning("failed to delete session %s", path, exc_info=True)


def _expand(value: str, base: Path | None = None) -> Path:
    """Relative paths resolve from the config folder, not the CWD; `~` stays absolute."""
    path = Path(value).expanduser()
    return path if path.is_absolute() or base is None else base / path


def _reject_legacy(path: Path, key: str) -> Path:
    """Reject the old data path so the process never silently reads/creates ~/.sanka."""
    if path == LEGACY_DATA or path.is_relative_to(LEGACY_DATA):
        raise ConfigError(
            f"{key} still points at {path}; runtime data now lives in .sanka/ relative "
            "to the config folder"
        )
    return path


def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, (str, int)) and str(item))


def _bool(value: object, default: bool) -> bool:
    """A YAML string `"false"` must be false, not truthy like `bool("false")`."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "1", "on"}:
        return True
    if text in {"false", "no", "0", "off"}:
        return False
    return default


def _num(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _section(raw: Mapping, key: str) -> dict:
    body = raw.get(key)
    return body if isinstance(body, dict) else {}


def _yaml_file(path: Path, label: str) -> dict:
    if not path.is_file():
        raise ConfigError(f"{label} not found: {path}")
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{label} is invalid: {exc}") from exc
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ConfigError(f"{label} must be a mapping: {path}")
    return body


def _mcp_servers(raw: object) -> dict[str, MCPServerConfig]:
    servers: object = (raw or {}).get("servers") if isinstance(raw, dict) else None
    if not isinstance(servers, dict):
        return {}
    result: dict[str, MCPServerConfig] = {}
    for name, body in servers.items():
        body = body if isinstance(body, dict) else {}
        command = body.get("command")
        url = body.get("url")
        if command is None and url is None:
            raise ConfigError(f"mcp server '{name}' needs 'command' or 'url'")
        if command is not None and url is not None:
            raise ConfigError(f"mcp server '{name}' takes 'command' or 'url', not both")
        if url is not None:
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                raise ConfigError(f"mcp server '{name}' url must start with http:// or https://")
        elif not isinstance(command, str) or not command:
            raise ConfigError(f"mcp server '{name}' needs 'command'")
        stdio = url is None
        env = body.get("env") if stdio and isinstance(body.get("env"), dict) else {}
        result[str(name)] = MCPServerConfig(
            name=str(name),
            command=command if stdio else None,
            args=_str_tuple(body.get("args")) if stdio else (),
            env={str(k): str(v) for k, v in env.items()},
            url=url,
        )
    return result


def _database(raw: Mapping) -> DatabaseConfig:
    body = _section(raw, "database")
    return DatabaseConfig(
        url=str(body.get("url") or "").strip(),
        url_env=str(body.get("url_env") or "SANKA_DATABASE_URL").strip() or "SANKA_DATABASE_URL",
        pool_size=max(1, int(_num(body.get("pool_size"), 5))),
        pool_timeout=_num(body.get("pool_timeout"), 30.0),
    )


def _sandbox(raw: Mapping, base: Path) -> SandboxConfig:
    body = _section(raw, "sandbox")
    host_ws_raw = body.get("host_workspace")
    host_workspace = _expand(str(host_ws_raw), base) if host_ws_raw else None
    # The host path is not always visible from the container (daemon on the host), so it
    # is validated as a directory when the tool runs. Here we only reject obvious misses.
    if host_workspace is not None and host_workspace.exists() and not host_workspace.is_dir():
        raise ConfigError(f"sandbox.host_workspace '{host_workspace}' is not a directory")
    return SandboxConfig(
        runtime=str(body.get("runtime") or "").strip(),
        image=str(body.get("image") or "docker.io/library/python:3.12-slim"),
        timeout_seconds=_num(body.get("timeout_seconds"), 120.0),
        network=str(body.get("network") or "none").strip(),
        host_workspace=host_workspace,
        env=_str_tuple(body.get("env")),
    )


def load_host(path: str | Path | None = None) -> tuple[HostConfig, Paths]:
    """Read config.yaml without creating directories or touching the DB."""
    target = Path(path or os.environ.get("SANKA_CONFIG") or "config.yaml")
    if not target.is_file():
        raise ConfigError(f"config not found: {target} (run `sanka init`)")
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"config YAML is invalid: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config is empty or not a mapping: {target}")
    legacy = [key for key in LEGACY_KEYS if key in raw]
    if legacy:
        raise ConfigError(
            "config.yaml uses the old split layout; run `sanka config migrate`"
        )
    base = target.resolve().parent
    workspace = _reject_legacy(
        _expand(str(raw.get("workspace") or ".sanka/workspace"), base), "workspace"
    )
    roles_file = _expand(str(raw.get("roles_file") or "roles.yaml"), base)
    search_raw = _section(raw, "web_search")
    host = HostConfig(
        database=_database(raw),
        workspace=workspace,
        sandbox=_sandbox(raw, base),
        web_search=WebSearchConfig(
            base_url=str(search_raw.get("base_url") or "").strip().rstrip("/"),
            timeout_seconds=_num(search_raw.get("timeout_seconds"), 10.0),
        ),
        mcp=_mcp_servers(raw.get("mcp")),
        roles_file=roles_file,
        sync_documents=_bool(raw.get("sync_documents"), True),
    )
    paths = Paths(
        config=target.resolve(),
        roles=roles_file,
        soul_md=base / "SOUL.md",
        workspace=workspace,
    )
    return host, paths


def _engine(raw: Mapping, documents: Documents | None) -> dict:
    """Engine document wins; otherwise the `engine` section of config.yaml."""
    text = documents.get("engine") if documents is not None else None
    if text:
        try:
            body = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigError(f"engine document is invalid: {exc}") from exc
        if body is None:
            return {}
        if not isinstance(body, dict):
            raise ConfigError("engine document must be a mapping")
        return body
    return _section(raw, "engine")


def _soul(raw: Mapping, paths: Paths, documents: Documents | None) -> Soul:
    """Soul document and persona document win over config.yaml `soul` and SOUL.md."""
    meta: str | None = documents.get("soul") if documents is not None else None
    if not meta:
        body = raw.get("soul")
        meta = yaml.safe_dump(body, sort_keys=False, allow_unicode=True) if isinstance(
            body, dict
        ) else None
    persona = documents.get("persona") if documents is not None else None
    source: Mapping[str, str | None] = {"soul": meta, "persona": persona}
    try:
        return load_soul(source, base=paths.config.parent)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _roles(
    raw: Mapping, paths: Paths, documents: Documents | None, default_role: str
) -> RoleRegistry:
    """Role documents win; otherwise read every role from `roles.yaml`."""
    source: Mapping[str, str | dict] = {}
    names = documents.names("role:") if documents is not None else []
    if names:
        source = {
            name.split(":", 1)[1]: documents.get(name) or ""
            for name in names
            if ":" in name
        }
    else:
        body = _roles_file(paths)
        if body:
            source = body
    if not source:
        raise ConfigError(f"no roles found: {paths.roles} (run `sanka init`)")
    try:
        return load_roles(source, default_role)
    except RoleError as exc:
        raise ConfigError(str(exc)) from exc


def _roles_file(paths: Paths) -> dict:
    """Read the `roles` mapping from roles.yaml; empty when the file is absent."""
    if not paths.roles.is_file():
        return {}
    raw = _yaml_file(paths.roles, "roles.yaml")
    body = raw.get("roles")
    if not isinstance(body, dict):
        raise ConfigError(f"roles.yaml must contain a 'roles' mapping: {paths.roles}")
    return body


def _gateways(raw: Mapping, *, required: bool = False) -> dict[str, GatewaySettings]:
    body = raw.get("gateways")
    if body is None:
        if required:
            raise ConfigError(
                "config.yaml is missing the 'gateways' section (run `sanka config migrate`)"
            )
        return {}
    if not isinstance(body, dict):
        raise ConfigError("config.yaml: 'gateways' must be a mapping")
    result: dict[str, GatewaySettings] = {}
    for name, value in body.items():
        value = value if isinstance(value, dict) else {}
        options = {str(key): item for key, item in value.items() if key != "enabled"}
        result[str(name)] = GatewaySettings(
            name=str(name),
            enabled=_bool(value.get("enabled"), False),
            options=options,
        )
    return result


def load_config(
    path: str | Path | None = None,
    *,
    documents: Documents | None = None,
    require_key: bool = True,
    require_gateway: bool = False,
) -> Config:
    host, paths = load_host(path)
    try:
        raw = yaml.safe_load(paths.config.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"config YAML is invalid: {exc}") from exc
    raw = raw if isinstance(raw, dict) else {}
    engine = _engine(raw, documents)
    llm_raw = _section(engine, "llm")
    api_key_env = str(llm_raw.get("api_key_env") or "OPENCODE_GO_API_KEY")
    if require_key and not os.environ.get(api_key_env):
        raise ConfigError(f"env {api_key_env} is not set (put it in .env)")
    default_role = str(engine.get("default_role") or "").strip()
    if not default_role:
        raise ConfigError("engine.default_role is required")
    roles = _roles(raw, paths, documents, default_role)
    gateways = _gateways(raw, required=require_gateway)
    for name, settings in gateways.items():
        if not settings.enabled:
            continue
        role = settings.options.get("role")
        if role is not None and roles.get(str(role)) is None:
            raise ConfigError(f"gateway '{name}': role '{role}' is not among the loaded roles")
    database = host.database
    url = resolve_database_url(database)
    if not url:
        raise ConfigError(
            f"database URL is empty; set database.url in config.yaml or env {database.url_env}"
        )
    database = DatabaseConfig(
        url=database.url,
        url_env=database.url_env,
        pool_size=database.pool_size,
        pool_timeout=database.pool_timeout,
    )

    reply_raw = _section(engine, "reply")
    memory_raw = _section(engine, "memory")
    approval_raw = _section(engine, "approval")
    skills_raw = _section(engine, "skills")
    prompt_raw = _section(engine, "prompt")
    humanize_raw = _section(engine, "humanize")
    style_raw = _section(engine, "style")
    affect_raw = _section(engine, "affect")
    ambient_raw = _section(engine, "ambient")
    vibe_raw = _section(affect_raw, "vibe")

    memory_backend = str(memory_raw.get("backend") or "mem0").strip().lower()
    if memory_backend not in {"mem0", "null"}:
        raise ConfigError("memory.backend must be 'mem0' or 'null'")
    approval_mode = str(approval_raw.get("mode") or "yolo").strip().lower()
    if approval_mode not in {"yolo", "off", "strict"}:
        raise ConfigError("approval.mode must be 'yolo', 'off', or 'strict'")
    approval_commands = approval_raw.get("sensitive_commands")
    embedder_raw = _section(memory_raw, "embedder")
    privacy_raw = _section(memory_raw, "privacy")

    def _level(key: str, default: int) -> int:
        try:
            value = int(_num(privacy_raw.get(key), float(default)))
        except (TypeError, ValueError):
            return default
        return value if 0 <= value <= 4 else default

    privacy = PrivacyConfig(
        enabled=_bool(privacy_raw.get("enabled"), True),
        default_level=_level("default_level", 2),
        ask_level=_level("ask_level", 4),
        group_max_level=_level("group_max_level", 2),
        dm_max_level=_level("dm_max_level", 4),
        auditor=_bool(privacy_raw.get("auditor"), True),
    )

    return Config(
        paths=paths,
        database=database,
        documents=documents,
        llm=LLMConfig(
            base_url=str(llm_raw.get("base_url") or "https://opencode.ai/inference/go/openai/v1"),
            model=str(llm_raw.get("model") or "deepseek-v4.1-flash"),
            api_key_env=api_key_env,
            timeout_seconds=_num(llm_raw.get("timeout_seconds"), 120.0),
            temperature=_num(llm_raw.get("temperature"), 0.9),
            run_budget_seconds=int(_num(llm_raw.get("run_budget_seconds"), 240)),
        ),
        workspace=host.workspace,
        reply=ReplyConfig(quiet_seconds=int(_num(reply_raw.get("quiet_seconds"), 600))),
        approval=ApprovalConfig(
            mode=approval_mode,
            sensitive_tools=_str_tuple(approval_raw.get("sensitive_tools")),
            sensitive_commands=(
                DEFAULT_SENSITIVE_COMMANDS
                if approval_commands is None
                else _str_tuple(approval_commands)
            ),
            settings=_bool(approval_raw.get("settings"), True),
            ttl_seconds=int(_num(approval_raw.get("ttl_seconds"), 900)),
        ),
        roles=roles,
        default_role=default_role,
        gateways=gateways,
        soul=_soul(raw, paths, documents),
        sandbox=host.sandbox,
        mcp=host.mcp,
        web_search=host.web_search,
        memory=MemoryConfig(
            backend=memory_backend,
            auto_capture=_bool(memory_raw.get("auto_capture"), True),
            capture_min_chars=int(_num(memory_raw.get("capture_min_chars"), 8)),
            max_items=int(_num(memory_raw.get("max_items"), 12)),
            max_chars=int(_num(memory_raw.get("max_chars"), 3000)),
            collection=str(memory_raw.get("collection") or "sanka_memories").strip()
            or "sanka_memories",
            extraction_model=str(memory_raw.get("extraction_model") or "").strip(),
            embedder=EmbedderConfig(
                provider=str(embedder_raw.get("provider") or "huggingface").strip(),
                model=str(
                    embedder_raw.get("model")
                    or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
                ).strip(),
                dimensions=int(_num(embedder_raw.get("dimensions"), 384)),
            ),
            telemetry=_bool(memory_raw.get("telemetry"), False),
            privacy=privacy,
        ),
        skills=SkillsConfig(
            max_index_chars=int(_num(skills_raw.get("max_index_chars"), 3000))
        ),
        prompt=PromptConfig(
            salience=_bool(prompt_raw.get("salience"), True),
            max_chars=int(_num(prompt_raw.get("max_chars"), 9000)),
        ),
        humanize=HumanizeConfig(
            enabled=_bool(humanize_raw.get("enabled"), False),
            cpms=_num(humanize_raw.get("cpms"), 25.0),
            min_delay=_num(humanize_raw.get("min_delay"), 0.5),
            max_delay=_num(humanize_raw.get("max_delay"), 5.0),
            between_delay=_num(humanize_raw.get("between_delay"), 0.7),
            max_bubbles=int(_num(humanize_raw.get("max_bubbles"), 3)),
            reaction_stickers=_str_tuple(humanize_raw.get("reaction_stickers")),
        ),
        style=StyleConfig(
            enabled=_bool(style_raw.get("enabled"), True),
            max_chars=int(_num(style_raw.get("max_chars"), 800)),
        ),
        affect=AffectConfig(
            enabled=_bool(affect_raw.get("enabled"), True),
            volatility=str(affect_raw.get("volatility") or "expressive").strip() or "expressive",
            grudge_half_life_hours=_num(affect_raw.get("grudge_half_life_hours"), 1.5),
            vibe=VibeConfig(
                enabled=_bool(vibe_raw.get("enabled"), True),
                model=str(
                    vibe_raw.get("model")
                    or "Aardiiiiy/EmoSense-ID-Indonesian-Emotion-Classifier"
                ),
                max_length=int(_num(vibe_raw.get("max_length"), 128)),
                attack=_num(vibe_raw.get("attack"), 0.35),
                baseline_influence=_num(vibe_raw.get("baseline_influence"), 0.05),
                hold_seconds=_num(vibe_raw.get("hold_seconds"), 1.2),
                neutral_floor=_num(vibe_raw.get("neutral_floor"), 0.35),
                tick_seconds=_num(vibe_raw.get("tick_seconds"), 1.0),
            ),
        ),
        ambient=AmbientConfig(
            enabled=_bool(ambient_raw.get("enabled"), True),
            tick_seconds=int(_num(ambient_raw.get("tick_seconds"), 10)),
            digest_every=int(_num(ambient_raw.get("digest_every"), 12)),
            digest_cooldown=int(_num(ambient_raw.get("digest_cooldown"), 900)),
            history_window=int(_num(ambient_raw.get("history_window"), 30)),
            idle_minutes=int(_num(ambient_raw.get("idle_minutes"), 20)),
            research_cooldown=int(_num(ambient_raw.get("research_cooldown"), 21600)),
            research_per_day=int(_num(ambient_raw.get("research_per_day"), 6)),
            research_results=int(_num(ambient_raw.get("research_results"), 5)),
        ),
    )
