"""Muat .env dan config host/engine/gateway/soul; validasi ketat, tanpa efek samping file."""

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
    """Konfigurasi tidak valid; pesan siap ditampilkan ke operator."""


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
class MemoryConfig:
    backend: str = "mem0"  # mem0 | null
    auto_capture: bool = True
    capture_min_chars: int = 8
    max_items: int = 12
    max_chars: int = 3000
    collection: str = "sanka_memories"
    extraction_model: str = ""  # kosong = llm.model
    embedder: EmbedderConfig = field(default_factory=EmbedderConfig)
    telemetry: bool = False


@dataclass(frozen=True)
class ReplyConfig:
    quiet_seconds: int = 600


@dataclass(frozen=True)
class ApprovalConfig:
    mode: str = "yolo"  # yolo | off | strict
    sensitive_tools: tuple[str, ...] = ("shell", "sandbox")
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
    engine: Path
    gateway: Path
    soul: Path
    soul_md: Path
    roles: Path
    workspace: Path


@dataclass(frozen=True)
class HostConfig:
    database: DatabaseConfig
    workspace: Path
    sandbox: SandboxConfig
    web_search: WebSearchConfig
    mcp: dict[str, MCPServerConfig]
    gateway_file: Path
    engine_file: Path
    roles_dir: Path
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
    """URL efektif: env `url_env` bila diisi, selain itu `url`."""
    env_name = str(database.url_env or "").strip()
    if env_name and os.environ.get(env_name):
        return str(os.environ[env_name]).strip()
    return str(database.url or "").strip()


def load_env(path: str | Path = ".env") -> None:
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


LEGACY_DATA = Path("~/.sanka").expanduser()
LEGACY_KEYS = ("whatsapp", "roles", "persona")


def secure_db(path: Path) -> None:
    """DB sesi berisi kredensial; jangan biarkan world-readable."""
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
    """Hapus DB sesi sementara beserta WAL/SHM-nya."""
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(f"{path}{suffix}").unlink(missing_ok=True)
        except OSError:
            log.warning("gagal menghapus sesi %s", path, exc_info=True)


def _expand(value: str, base: Path | None = None) -> Path:
    """Path relatif di-resolve dari folder config, bukan CWD; `~` tetap absolut."""
    path = Path(value).expanduser()
    return path if path.is_absolute() or base is None else base / path


def _reject_legacy(path: Path, key: str) -> Path:
    """Tolak path data lama agar proses tidak diam-diam membaca/membuat ~/.sanka lagi."""
    if path == LEGACY_DATA or path.is_relative_to(LEGACY_DATA):
        raise ConfigError(
            f"{key} masih menunjuk {path}; data runtime sekarang di .sanka/ relatif folder config"
        )
    return path


def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, (str, int)) and str(item))


def _bool(value: object, default: bool) -> bool:
    """String `"false"` di YAML harus mati, bukan truthy seperti `bool("false")`."""
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
        raise ConfigError(f"{label} tidak ditemukan: {path}")
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{label} tidak valid: {exc}") from exc
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ConfigError(f"{label} harus mapping: {path}")
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
            raise ConfigError(f"mcp server '{name}' butuh 'command' atau 'url'")
        if command is not None and url is not None:
            raise ConfigError(f"mcp server '{name}' pilih 'command' atau 'url', jangan keduanya")
        if url is not None:
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                raise ConfigError(f"mcp server '{name}' url harus diawali http:// atau https://")
        elif not isinstance(command, str) or not command:
            raise ConfigError(f"mcp server '{name}' butuh 'command'")
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
    # Path host tidak selalu terlihat dari container (daemon di host), jadi baru divalidasi
    # sebagai direktori saat tool dipanggil. Di sini cukup tolak path yang jelas salah.
    if host_workspace is not None and host_workspace.exists() and not host_workspace.is_dir():
        raise ConfigError(f"sandbox.host_workspace '{host_workspace}' bukan direktori")
    return SandboxConfig(
        runtime=str(body.get("runtime") or "").strip(),
        image=str(body.get("image") or "docker.io/library/python:3.12-slim"),
        timeout_seconds=_num(body.get("timeout_seconds"), 120.0),
        network=str(body.get("network") or "none").strip(),
        host_workspace=host_workspace,
        env=_str_tuple(body.get("env")),
    )


def load_host(path: str | Path | None = None) -> tuple[HostConfig, Paths]:
    """Baca config.yaml host tanpa membuat direktori atau menghubungi DB."""
    target = Path(path or os.environ.get("SANKA_CONFIG") or "config.yaml")
    if not target.is_file():
        raise ConfigError(f"config tidak ditemukan: {target} (jalankan `sanka init`)")
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"config YAML tidak valid: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config kosong atau bukan mapping: {target}")
    legacy = [key for key in LEGACY_KEYS if key in raw]
    if legacy:
        raise ConfigError(
            f"config.yaml masih format lama ({', '.join(legacy)}); jalankan `sanka config migrate`"
        )
    base = target.resolve().parent
    workspace = _reject_legacy(
        _expand(str(raw.get("workspace") or ".sanka/workspace"), base), "workspace"
    )
    gateway_file = _expand(str(raw.get("gateway_file") or "gateway.yaml"), base)
    engine_file = _expand(str(raw.get("engine_file") or "engine.yaml"), base)
    roles_dir = _expand(str(raw.get("roles_dir") or "roles"), base)
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
        gateway_file=gateway_file,
        engine_file=engine_file,
        roles_dir=roles_dir,
        sync_documents=_bool(raw.get("sync_documents"), True),
    )
    paths = Paths(
        config=target.resolve(),
        engine=engine_file,
        gateway=gateway_file,
        soul=base / "soul.yaml",
        soul_md=base / "SOUL.md",
        roles=roles_dir,
        workspace=workspace,
    )
    return host, paths


def _engine(host: HostConfig, documents: Documents | None) -> dict:
    text = documents.get("engine") if documents is not None else None
    if not text:
        return _yaml_file(host.engine_file, "engine.yaml")
    try:
        body = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"dokumen engine tidak valid: {exc}") from exc
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ConfigError("dokumen engine harus mapping")
    return body


def _soul(paths: Paths, documents: Documents | None) -> Soul:
    if documents is not None:
        source: Mapping[str, str | None] = {
            "soul": documents.get("soul"),
            "persona": documents.get("persona"),
        }
    else:
        source = {}
    try:
        return load_soul(source, base=paths.config.parent)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _roles(
    paths: Paths, documents: Documents | None, default_role: str
) -> RoleRegistry:
    source: Path | Mapping[str, str] = paths.roles
    if documents is not None:
        names = documents.names("role:")
        if names:
            docs = {
                name.split(":", 1)[1]: documents.get(name) or ""
                for name in names
                if ":" in name
            }
            source = docs
    if not isinstance(source, Mapping) and not source.is_dir():
        raise ConfigError(f"role tidak ditemukan: {paths.roles} (jalankan `sanka init`)")
    try:
        return load_roles(source, default_role)
    except RoleError as exc:
        raise ConfigError(str(exc)) from exc


def _gateway_file(paths: Paths, *, required: bool) -> dict[str, GatewaySettings]:
    if not paths.gateway.is_file():
        if required:
            raise ConfigError(f"gateway.yaml tidak ditemukan: {paths.gateway} (jalankan `sanka init`)")
        return {}
    raw = _yaml_file(paths.gateway, "gateway.yaml")
    body = raw.get("gateways")
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ConfigError("gateway.yaml: 'gateways' harus mapping")
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
    engine = _engine(host, documents)
    llm_raw = _section(engine, "llm")
    api_key_env = str(llm_raw.get("api_key_env") or "OPENCODE_GO_API_KEY")
    if require_key and not os.environ.get(api_key_env):
        raise ConfigError(f"env {api_key_env} belum diisi (taruh di .env)")
    default_role = str(engine.get("default_role") or "").strip()
    if not default_role:
        raise ConfigError("engine.default_role wajib diisi")
    roles = _roles(paths, documents, default_role)
    gateways = _gateway_file(paths, required=require_gateway)
    for name, settings in gateways.items():
        if not settings.enabled:
            continue
        role = settings.options.get("role")
        if role is not None and roles.get(str(role)) is None:
            raise ConfigError(f"gateway '{name}': role '{role}' tidak ada di role yang dimuat")
    database = host.database
    url = resolve_database_url(database)
    if not url:
        raise ConfigError(
            f"database URL kosong; isi database.url di config.yaml atau env {database.url_env}"
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
        raise ConfigError("memory.backend harus 'mem0' atau 'null'")
    approval_mode = str(approval_raw.get("mode") or "yolo").strip().lower()
    if approval_mode not in {"yolo", "off", "strict"}:
        raise ConfigError("approval.mode harus 'yolo', 'off', atau 'strict'")
    embedder_raw = _section(memory_raw, "embedder")

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
            sensitive_tools=_str_tuple(approval_raw.get("sensitive_tools"))
            or ("shell", "sandbox"),
            settings=_bool(approval_raw.get("settings"), True),
            ttl_seconds=int(_num(approval_raw.get("ttl_seconds"), 900)),
        ),
        roles=roles,
        default_role=default_role,
        gateways=gateways,
        soul=_soul(paths, documents),
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
