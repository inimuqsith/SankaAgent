"""Registry tool: satu modul per tool di folder ini dengan `TOOL = Handler(...)`.

Semua eksekusi lewat `execute`: satu titik untuk permission, error, dan pemotongan output.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..config import LEGACY_DATA, Config, RoleConfig
from ..registry import Handler, Registry, allows, discover

log = logging.getLogger(__name__)

MAX_OUTPUT = 40_000
MAX_ERROR = 500
TRUNCATED = "\n...[dipotong: hasil melebihi 40000 karakter; persempit query/read range atau minta bagian lanjutan]"

FAMILIES = frozenset({"shell", "read", "write", "web_search", "sandbox", "mcp"})

OWNER_MEMBER = {"owner": True, "member": True}
OWNER_ONLY = {"owner": True}


class ToolContext:
    def __init__(
        self,
        config: Config,
        role: RoleConfig,
        allowed: tuple[str, ...],
        chat_id: str = "",
        history=None,
        memory=None,
        skills=None,
        recipes=None,
        send_file: Callable[[str, Path, str], Awaitable[str]] | None = None,
        send_message: Callable[[str, str], Awaitable[str]] | None = None,
        request: str = "",
        affect=None,
    ):
        self.config = config
        self.role = role
        self.allowed = allowed
        self.chat_id = chat_id
        self.history = history
        self.memory = memory
        self.skills = skills
        self.recipes = recipes
        self.send_file = send_file
        self.send_message = send_message
        self.request = request
        self.affect = affect
        self.used: list[str] = []
        self.sent: list[dict] = []


def _int(args: dict, key: str, default: int) -> int:
    try:
        return int(args.get(key, default))
    except (TypeError, ValueError):
        return default


def resolve_path(config: Config, raw: str) -> Path:
    """Resolve path terhadap workspace; tolak apa pun yang keluar dari workspace."""
    base = config.workspace.resolve()
    path = Path(str(raw)).expanduser()
    path = (path if path.is_absolute() else base / path).resolve()
    if path == LEGACY_DATA or path.is_relative_to(LEGACY_DATA):
        raise PermissionError("path lama ~/.sanka sudah dipindah ke .sanka di project")
    if not path.is_relative_to(base):
        raise PermissionError(f"path di luar workspace: {raw}")
    return path


def _resolve(ctx: ToolContext, raw: str) -> Path:
    return resolve_path(ctx.config, raw)


def truncate(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + TRUNCATED


async def execute(specs: dict[str, Handler], name: str, args: dict, ctx: ToolContext) -> str:
    """Titik masuk tunggal: cek izin, jalan, tangkap error, potong output."""
    spec = specs.get(name) or next((item for item in specs.values() if name in item.aliases), None)
    if spec is None:
        return f"error: tool tidak dikenal: {name}"
    if not allows(ctx.allowed, spec.name):
        return f"error: tool '{name}' tidak diizinkan untuk role '{ctx.role.name}'"
    if spec.fn is None:
        return f"error: tool '{name}' tidak punya implementasi"
    ctx.used.append(spec.name)  # percobaan kerja tetap dianggap kerja walau tool gagal
    try:
        result = await spec.fn(args, ctx)
    except PermissionError as exc:  # penolakan path tidak perlu traceback
        result = f"error: {exc}"[:MAX_ERROR]
    except Exception as exc:  # tool apa pun tidak boleh mematikan bot
        log.exception("tool %s gagal", name)
        result = f"error: {type(exc).__name__}: {exc}"[:MAX_ERROR]
    return truncate(str(result))


REGISTRY = Registry(
    discover(__name__, Path(__file__).parent, "TOOL", "tool", FAMILIES),
    "tool",
)
