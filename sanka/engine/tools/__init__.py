"""Registry tool: satu modul per tool dengan `TOOL = Handler(...)`; eksekusi satu pintu."""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import LEGACY_DATA, Config
from ..registry import Handler, Registry, allows, discover
from ..roles import Role
from ..safety import ActionRequest

log = logging.getLogger(__name__)

MAX_OUTPUT = 40_000
MAX_ERROR = 500
TRUNCATED = "\n...[dipotong: hasil melebihi 40000 karakter; persempit query/read range atau minta bagian lanjutan]"

FAMILIES = frozenset({"shell", "read", "write", "web_search", "sandbox", "mcp"})


class ToolContext:
    def __init__(
        self,
        config: Config,
        roles: tuple[Role, ...],
        allowed: tuple[str, ...],
        *,
        chat_id: str = "",
        sender: str = "",
        request: str = "",
        history=None,
        memory=None,
        ledger=None,
        skills=None,
        recipes=None,
        affect=None,
        agent=None,
        gateway=None,
        approved: bool = False,
        approval_id: str = "",
        person_id: str = "",
        is_dm: bool = False,
    ):
        self.config = config
        self.roles = tuple(roles) if isinstance(roles, (tuple, list)) else (roles,)
        self.allowed = allowed
        self.chat_id = chat_id
        self.sender = sender
        self.request = request
        self.history = history
        self.memory = memory
        self.ledger = ledger
        self.skills = skills
        self.recipes = recipes
        self.affect = affect
        self.agent = agent
        self.gateway = gateway
        self.approved = approved
        self.approval_id = approval_id
        self.person_id = person_id or sender
        self.is_dm = bool(is_dm)
        self.used: list[str] = []
        self.sent: list[dict] = []
        self.reads: set[str] = set()
        self.memory_hits: list = []

    @property
    def role(self) -> Role | None:
        return self.roles[0] if self.roles else None

    @property
    def memory_admin(self) -> bool:
        """Global memory (and its deletion) is limited to roles marked memory_admin."""
        return any(role.memory_admin for role in self.roles)

    def role_names(self) -> str:
        return ", ".join(role.name for role in self.roles) or "-"


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
    """Titik masuk tunggal: izin, safety/approval, jalan, tangkap error, potong output."""
    spec = specs.get(name) or next((item for item in specs.values() if name in item.aliases), None)
    if spec is None:
        return f"error: tool tidak dikenal: {name}"
    if not allows(ctx.allowed, spec.name):
        return f"error: tool '{name}' tidak diizinkan untuk role '{ctx.role_names()}'"
    if spec.fn is None:
        return f"error: tool '{name}' tidak punya implementasi"
    if ctx.agent is not None and not ctx.approved:
        decision = ctx.agent.safety.check(
            ActionRequest(
                tool=spec.name,
                args=dict(args),
                chat_id=ctx.chat_id,
                sender=ctx.sender,
                gateway=getattr(ctx.gateway, "name", "") or "",
                roles=tuple(role.name for role in ctx.roles),
            )
        )
        if decision.verdict == "deny":
            return f"error: {decision.reason or 'aksi ditolak safety policy'}"
        if decision.verdict == "pending":
            if ctx.approval_id:
                return (
                    f"pending approval #{ctx.approval_id} already waiting; "
                    "ask the sender to /approve it."
                )
            approval = await ctx.agent.approvals.create(
                kind="tool",
                chat_id=ctx.chat_id,
                sender=ctx.sender,
                gateway=getattr(ctx.gateway, "name", "") or "",
                tool=spec.name,
                args=dict(args),
                summary=f"{spec.name} {str(args)[:160]}",
                roles=tuple(role.name for role in ctx.roles),
            )
            ctx.approval_id = str(approval.id)
            return (
                f"pending approval #{approval.id}: {approval.summary}. "
                f"Tell the sender to run /approve {approval.id}."
            )
    ctx.used.append(spec.name)
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
