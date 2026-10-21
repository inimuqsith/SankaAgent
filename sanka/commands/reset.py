"""Command /reset: hapus konteks percakapan chat ini."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


async def reset(ctx: CommandContext) -> str:
    ctx.app.sessions.reset(ctx.chat_id)
    return "Konteks percakapan di chat ini sudah direset."


COMMAND = Handler(
    name="reset",
    description="Hapus konteks percakapan chat ini.",
    fn=reset,
    aliases=("r", "clear"),
    defaults={"owner": True, "member": True},
)
