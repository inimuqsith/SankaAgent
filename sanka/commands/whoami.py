"""Command /whoami: identitas pengirim, role, dan chat."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


def _chat_label(ctx: CommandContext) -> str:
    kind = "grup" if ctx.chat_id.endswith("@g.us") else "pribadi"
    try:
        name = ctx.app.sessions.chat_name(ctx.chat_id)
    except Exception:  # noqa: BLE001
        name = ""
    return f'{kind} "{name}" ({ctx.chat_id})' if name else f"{kind} ({ctx.chat_id})"


async def whoami(ctx: CommandContext) -> str:
    lines = [
        f"👤 Kamu: {ctx.sender or ctx.chat_id}",
        f"🎭 Peran: {str(ctx.role.name).capitalize()}",
        f"💬 Chat: {_chat_label(ctx)}",
    ]
    bots = sorted(getattr(ctx.app, "bot_digits", ()) or ())
    if bots:
        name = ctx.app.config.whatsapp.bot_name
        lines.append(f"🧭 Bot: {name} ({', '.join(bots)})")
    return "\n".join(lines)


COMMAND = Handler(
    name="whoami",
    description="👤 Identitas, role, dan chat kamu.",
    fn=whoami,
    aliases=("id",),
    defaults={"owner": True, "member": True},
)
