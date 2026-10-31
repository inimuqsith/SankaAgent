"""Tool google: Google Workspace lewat satu service account, identitas per pengirim."""

from __future__ import annotations

import logging
import weakref

from ..registry import Handler
from . import ToolContext

log = logging.getLogger(__name__)

_SERVICES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _service(ctx: ToolContext):
    from ...vendor.google_workspace.service import GoogleWorkspace

    agent = ctx.agent
    if agent is not None:
        cached = _SERVICES.get(agent)
        if cached is not None:
            return cached
        service = GoogleWorkspace(agent.db)
        _SERVICES[agent] = service
        return service
    from ...vendor.google_workspace.service import GoogleError

    raise GoogleError("database is not available for Google Workspace")


def _record(ctx: ToolContext, action: str, file_id: str) -> None:
    if ctx.ledger is None:
        return
    try:
        target_name = ctx.gateway.chat_name(ctx.chat_id) if ctx.gateway else ""
        ctx.ledger.log_action(
            ctx.chat_id,
            ctx.chat_id,
            "google",
            f"{action} {file_id or ''}".strip(),
            source_name="chat",
            target_name=target_name,
            request=ctx.request,
        )
    except Exception:
        log.exception("gagal mencatat aksi google chat=%s", ctx.chat_id)


async def _google(args: dict, ctx: ToolContext) -> str:
    from ...vendor.google_workspace.service import GoogleError

    action = str(args.get("action") or "").strip().lower()
    person_id = str(ctx.person_id or ctx.sender or "").strip()
    if not person_id:
        return "error: no sender identity"
    file_id = str(args.get("file_id") or "").strip()
    _record(ctx, action, file_id)
    try:
        service = _service(ctx)
        if action == "login":
            return await service.login(person_id, is_dm=ctx.is_dm)
        if action == "status":
            return await service.status(person_id, is_dm=ctx.is_dm)
        if action not in {"list", "read", "write"}:
            return "error: unknown action; use login, status, list, read, or write"
        identity = service.identity(person_id)
        if identity is None:
            return "error: Google account not connected; run action=login in a DM"
        email = str(identity["email"])
        if action == "list":
            try:
                limit = int(args.get("limit") or 10)
            except (TypeError, ValueError):
                limit = 10
            return await service.list_files(
                email, ctx.trusted, str(args.get("query") or ""), limit
            )
        if not file_id:
            return "error: file_id is required"
        if action == "read":
            return await service.read(
                file_id, email, ctx.trusted, str(args.get("range") or "")
            )
        return await service.write(
            file_id,
            email,
            ctx.trusted,
            str(args.get("range") or ""),
            args.get("values") or [],
        )
    except GoogleError as exc:
        return f"error: {exc}"
    except (OSError, ValueError) as exc:
        return f"error: {type(exc).__name__}: {exc}"


TOOL = Handler(
    name="google",
    family="mcp",
    description=(
        "Google Workspace through Sanka's one service account; identity-linked per sender."
    ),
    fn=_google,
    aliases=("gworkspace",),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["login", "status", "list", "read", "write"],
                "description": (
                    "login/status link a Google identity in a DM; list/read/write use it."
                ),
            },
            "file_id": {"type": "string", "description": "Drive file ID."},
            "range": {
                "type": "string",
                "description": "A1 range for Sheets (read default A1:Z1000; required for write).",
            },
            "values": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "string"}},
                "description": "Rows to write to a Sheet.",
            },
            "query": {"type": "string", "description": "Drive name filter."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
)
