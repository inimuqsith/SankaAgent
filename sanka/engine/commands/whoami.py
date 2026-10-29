"""Command /whoami: identitas pengirim, role, chat, dan surface."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


def _chat_label(ctx: CommandContext) -> str:
    kind = "group" if ctx.chat_id.endswith("@g.us") else "dm"
    name = ctx.gateway.chat_name(ctx.chat_id) if ctx.gateway is not None else ""
    return f'{kind} "{name}" ({ctx.chat_id})' if name else f"{kind} ({ctx.chat_id})"


async def whoami(ctx: CommandContext) -> str:
    roles = ", ".join(role.name.capitalize() for role in ctx.roles) or "-"
    lines = [
        f"👤 Kamu: {ctx.sender or ctx.chat_id}",
        f"🎭 Peran: {roles}",
        f"💬 Chat: {_chat_label(ctx)}",
        f"📡 Surface: {ctx.gateway.name if ctx.gateway is not None else '-'}",
    ]
    handles = sorted(ctx.gateway.self_handles()) if ctx.gateway is not None else []
    if handles:
        lines.append(f"🧭 Bot: {ctx.agent.config.soul.name} ({', '.join(handles)})")
    return "\n".join(lines)


COMMAND = Handler(
    name="whoami",
    description="👤 Identitas, role, chat, dan surface kamu.",
    fn=whoami,
    aliases=("id",),
)
