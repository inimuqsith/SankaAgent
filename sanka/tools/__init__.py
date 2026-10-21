"""Registry tool: satu modul per tool di folder ini dengan `TOOL = Handler(...)`.

Semua eksekusi lewat `execute`: satu titik untuk permission, error, dan pemotongan output.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import Config, RoleConfig
from ..registry import Handler, Registry, allows, discover

log = logging.getLogger(__name__)

MAX_OUTPUT = 20_000
MAX_ERROR = 500
TRUNCATED = "\n...[dipotong]"

FAMILIES = frozenset({"shell", "read", "write", "web_search", "sandbox", "mcp"})

OWNER_MEMBER = {"owner": True, "member": True}
OWNER_ONLY = {"owner": True}


class ToolContext:
    def __init__(self, config: Config, role: RoleConfig, allowed: tuple[str, ...]):
        self.config = config
        self.role = role
        self.allowed = allowed


def _int(args: dict, key: str, default: int) -> int:
    try:
        return int(args.get(key, default))
    except (TypeError, ValueError):
        return default


def _resolve(ctx: ToolContext, raw: str) -> Path:
    path = Path(str(raw)).expanduser()
    return path if path.is_absolute() else ctx.config.workspace / path


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
    try:
        result = await spec.fn(args, ctx)
    except Exception as exc:  # tool apa pun tidak boleh mematikan bot
        log.exception("tool %s gagal", name)
        result = f"error: {type(exc).__name__}: {exc}"[:MAX_ERROR]
    return truncate(str(result))


REGISTRY = Registry(
    discover(__name__, Path(__file__).parent, "TOOL", "tool", FAMILIES),
    "tool",
)
