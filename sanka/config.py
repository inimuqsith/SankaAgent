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
    temperature: float = 0.9  # balasan utama


@dataclass(frozen=True)
class WhatsAppConfig:
    session_db: Path
    bot_name: str = "sanka"
    history_db: Path | None = None
    backlog_seconds: int = 600  # lebih tua dari ini = arsip, bukan antrean balasan
    quiet_seconds: int = 600  # durasi default /stop
    group_reply: str = "addressed"  # addressed = hanya bila dipanggil; model = percayakan keputusan


@dataclass(frozen=True)
class RoleConfig:
    name: str
    users: tuple[str, ...] = ()
    tools: tuple[str, ...] | None = None
    commands: tuple[str, ...] | None = None


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
class MemoryConfig:
    path: Path = Path(".sanka/memory.db")
    max_items: int = 20
    max_chars: int = 3000
    auto_extract: bool = True


@dataclass(frozen=True)
class SkillsConfig:
    dir: Path = Path(".sanka/skills")
    max_index_chars: int = 3000


@dataclass(frozen=True)
class VibeConfig:
    enabled: bool = True  # default aktif; fail-open kalau torch/model tidak ada
    model: str = "Aardiiiiy/EmoSense-ID-Indonesian-Emotion-Classifier"
    max_length: int = 128
    attack: float = 0.35
    baseline_influence: float = 0.05
    hold_seconds: float = 1.2
    neutral_floor: float = 0.35
    tick_seconds: float = 1.0
    state_path: Path = Path(".sanka/vibe.json")


@dataclass(frozen=True)
class AffectConfig:
    enabled: bool = False
    path: Path = Path(".sanka/affect.db")  # hubungan, orang, topik, drives
    state_dir: Path | None = None  # file state emotion-engine per chat; None = di samping DB
    volatility: str = "expressive"
    grudge_half_life_hours: float = 1.5  # kesal reda sendiri; 0 = mati
    vibe: VibeConfig = field(default_factory=VibeConfig)


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
class StyleConfig:
    enabled: bool = False
    path: Path = Path(".sanka/style.json")
    max_chars: int = 800


@dataclass(frozen=True)
class AmbientConfig:
    enabled: bool = False
    tick_seconds: int = 120
    digest_every: int = 12
    digest_cooldown: int = 900
    history_window: int = 30
    idle_minutes: int = 20
    proactive: bool = True
    chime_cooldown: int = 3600
    chime_per_day: int = 6
    chime_urge: float = 0.45  # ambang dorongan (emosi) untuk menyelituk
    chime_gap_seconds: float = 1800.0  # jeda minimum antar menyelituk di chat mana pun
    chime_per_day_global: int = 4  # jatah menyelituk 24 jam untuk semua chat
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
class Config:
    persona: str
    llm: LLMConfig
    workspace: Path
    whatsapp: WhatsAppConfig
    roles: dict[str, RoleConfig]
    default_role: str
    sandbox: SandboxConfig
    mcp: dict[str, MCPServerConfig]
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    affect: AffectConfig = field(default_factory=AffectConfig)
    humanize: HumanizeConfig = field(default_factory=HumanizeConfig)
    style: StyleConfig = field(default_factory=StyleConfig)
    ambient: AmbientConfig = field(default_factory=AmbientConfig)

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


LEGACY_DATA = Path("~/.sanka").expanduser()


def _expand(value: str, base: Path | None = None) -> Path:
    """Path relatif di-resolve dari folder config, bukan CWD; `~` tetap absolut."""
    path = Path(value).expanduser()
    return path if path.is_absolute() or base is None else base / path


def _reject_legacy(path: Path, key: str) -> Path:
    """Tolak path data lama agar proses tidak diam-diam membaca/membuat ~/.sanka lagi."""
    if path == LEGACY_DATA or path.is_relative_to(LEGACY_DATA):
        raise _fail(
            f"config: {key} masih menunjuk {path}; data runtime sekarang di .sanka/ "
            "relatif terhadap folder config"
        )
    return path


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


def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, (str, int)) and str(item))


def _optional_str_tuple(value: object) -> tuple[str, ...] | None:
    """Field tidak ditulis = None (pakai default handler); `[]` = () = tolak semua."""
    return None if value is None else _str_tuple(value)


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


def _section(raw: dict, key: str) -> dict:
    body = raw.get(key)
    return body if isinstance(body, dict) else {}


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
        url = body.get("url")
        if command is None and url is None:
            raise _fail(f"config: mcp server '{name}' butuh 'command' atau 'url'")
        if command is not None and url is not None:
            raise _fail(f"config: mcp server '{name}' pilih 'command' atau 'url', jangan keduanya")
        if url is not None:
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                raise _fail(f"config: mcp server '{name}' url harus diawali http:// atau https://")
        elif not isinstance(command, str) or not command:
            raise _fail(f"config: mcp server '{name}' butuh 'command'")
        # args/env hanya bermakna untuk stdio; mode url membuangnya agar tidak menyesatkan.
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
    search_raw = raw.get("web_search") if isinstance(raw.get("web_search"), dict) else {}
    memory_raw = raw.get("memory") if isinstance(raw.get("memory"), dict) else {}
    skills_raw = raw.get("skills") if isinstance(raw.get("skills"), dict) else {}
    affect_raw = _section(raw, "affect")
    humanize_raw = _section(raw, "humanize")
    style_raw = _section(raw, "style")
    ambient_raw = _section(raw, "ambient")

    base = target.resolve().parent
    workspace = _reject_legacy(
        _expand(str(raw.get("workspace") or ".sanka/workspace"), base), "workspace"
    )
    workspace.mkdir(parents=True, exist_ok=True)
    session_db = _reject_legacy(
        _expand(str(wa_raw.get("session_db") or ".sanka/whatsapp.db"), base), "whatsapp.session_db"
    )
    session_db.parent.mkdir(parents=True, exist_ok=True)
    history_db = _reject_legacy(
        _expand(str(wa_raw.get("history_db") or session_db.parent / "history.db"), base),
        "whatsapp.history_db",
    )
    history_db.parent.mkdir(parents=True, exist_ok=True)
    memory_db = _reject_legacy(
        _expand(str(memory_raw.get("path") or ".sanka/memory.db"), base), "memory.path"
    )
    affect_db = _reject_legacy(
        _expand(str(affect_raw.get("path") or ".sanka/affect.db"), base), "affect.path"
    )
    affect_state_dir = _reject_legacy(
        _expand(str(affect_raw.get("state_dir") or ".sanka/emotions"), base), "affect.state_dir"
    )
    affect_state_dir.mkdir(parents=True, exist_ok=True)
    vibe_raw = affect_raw.get("vibe") if isinstance(affect_raw.get("vibe"), dict) else {}
    vibe_state = _reject_legacy(
        _expand(str(vibe_raw.get("state_path") or ".sanka/vibe.json"), base), "affect.vibe.state_path"
    )
    vibe_state.parent.mkdir(parents=True, exist_ok=True)
    style_path = _reject_legacy(
        _expand(str(style_raw.get("path") or ".sanka/style.json"), base), "style.path"
    )
    skills_dir = _reject_legacy(
        _expand(str(skills_raw.get("dir") or ".sanka/skills"), base), "skills.dir"
    )
    host_ws_raw = sandbox_raw.get("host_workspace")
    host_workspace = _expand(str(host_ws_raw), base) if host_ws_raw else None
    # Path host tidak selalu terlihat dari container (daemon di host), jadi baru divalidasi
    # sebagai direktori saat tool dipanggil. Di sini cukup tolak path yang jelas salah.
    if host_workspace is not None and host_workspace.exists() and not host_workspace.is_dir():
        raise _fail(f"config: sandbox.host_workspace '{host_workspace}' bukan direktori")
    secure_db(session_db)
    secure_db(history_db)
    secure_db(affect_db)

    soul = base / "SOUL.md"
    persona = ""
    if soul.is_file():
        try:
            persona = soul.read_text(encoding="utf-8").strip()
        except OSError:
            log.warning("gagal membaca %s; fallback ke persona YAML", soul, exc_info=True)
    if not persona:
        persona = str(raw.get("persona") or "").strip()

    return Config(
        persona=persona,
        llm=LLMConfig(
            base_url=str(llm_raw.get("base_url") or "https://opencode.ai/inference/go/openai/v1"),
            model=str(llm_raw.get("model") or "deepseek-v4.1-flash"),
            api_key_env=api_key_env,
            max_turns=int(llm_raw.get("max_turns") or 20),
            timeout_seconds=float(llm_raw.get("timeout_seconds") or 120),
            temperature=_num(llm_raw.get("temperature"), 0.9),
        ),
        workspace=workspace,
        whatsapp=WhatsAppConfig(
            session_db=session_db,
            bot_name=str(wa_raw.get("bot_name") or "sanka"),
            history_db=history_db,
            backlog_seconds=int(_num(wa_raw.get("backlog_seconds"), 600)),
            quiet_seconds=int(_num(wa_raw.get("quiet_seconds"), 600)),
            group_reply=str(wa_raw.get("group_reply") or "addressed").strip().lower(),
        ),
        roles=roles,
        default_role=default_role,
        sandbox=SandboxConfig(
            runtime=str(sandbox_raw.get("runtime") or "").strip(),
            image=str(sandbox_raw.get("image") or "docker.io/library/python:3.12-slim"),
            timeout_seconds=float(sandbox_raw.get("timeout_seconds") or 120),
            network=str(sandbox_raw.get("network") or "none").strip(),
            host_workspace=host_workspace,
            env=_str_tuple(sandbox_raw.get("env")),
        ),
        mcp=_mcp_servers(raw.get("mcp")),
        web_search=WebSearchConfig(
            base_url=str(search_raw.get("base_url") or "").strip().rstrip("/"),
            timeout_seconds=float(search_raw.get("timeout_seconds") or 10),
        ),
        memory=MemoryConfig(
            path=memory_db,
            max_items=int(memory_raw.get("max_items") or 20),
            max_chars=int(memory_raw.get("max_chars") or 3000),
            auto_extract=bool(memory_raw.get("auto_extract", True)),
        ),
        skills=SkillsConfig(
            dir=skills_dir,
            max_index_chars=int(skills_raw.get("max_index_chars") or 3000),
        ),
        affect=AffectConfig(
            enabled=_bool(affect_raw.get("enabled"), False),
            path=affect_db,
            state_dir=affect_state_dir,
            volatility=str(affect_raw.get("volatility") or "expressive").strip() or "expressive",
            grudge_half_life_hours=_num(affect_raw.get("grudge_half_life_hours"), 1.5),
            vibe=VibeConfig(
                enabled=_bool(vibe_raw.get("enabled"), True),
                model=str(
                    vibe_raw.get("model") or "Aardiiiiy/EmoSense-ID-Indonesian-Emotion-Classifier"
                ),
                max_length=int(_num(vibe_raw.get("max_length"), 128)),
                attack=_num(vibe_raw.get("attack"), 0.35),
                baseline_influence=_num(vibe_raw.get("baseline_influence"), 0.05),
                hold_seconds=_num(vibe_raw.get("hold_seconds"), 1.2),
                neutral_floor=_num(vibe_raw.get("neutral_floor"), 0.35),
                tick_seconds=_num(vibe_raw.get("tick_seconds"), 1.0),
                state_path=vibe_state,
            ),
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
            enabled=_bool(style_raw.get("enabled"), False),
            path=style_path,
            max_chars=int(_num(style_raw.get("max_chars"), 800)),
        ),
        ambient=AmbientConfig(
            enabled=_bool(ambient_raw.get("enabled"), False),
            tick_seconds=int(_num(ambient_raw.get("tick_seconds"), 120)),
            digest_every=int(_num(ambient_raw.get("digest_every"), 12)),
            digest_cooldown=int(_num(ambient_raw.get("digest_cooldown"), 900)),
            history_window=int(_num(ambient_raw.get("history_window"), 30)),
            idle_minutes=int(_num(ambient_raw.get("idle_minutes"), 20)),
            proactive=_bool(ambient_raw.get("proactive"), True),
            chime_cooldown=int(_num(ambient_raw.get("chime_cooldown"), 3600)),
            chime_per_day=int(_num(ambient_raw.get("chime_per_day"), 6)),
            chime_urge=_num(ambient_raw.get("chime_urge"), 0.45),
            chime_gap_seconds=_num(ambient_raw.get("chime_gap_seconds"), 1800.0),
            chime_per_day_global=int(_num(ambient_raw.get("chime_per_day_global"), 4)),
            research_cooldown=int(_num(ambient_raw.get("research_cooldown"), 21600)),
            research_per_day=int(_num(ambient_raw.get("research_per_day"), 6)),
            research_results=int(_num(ambient_raw.get("research_results"), 5)),
        ),
    )
