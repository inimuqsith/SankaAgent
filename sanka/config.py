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
    history_db: Path | None = None


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
            history_db=history_db,
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
    )
