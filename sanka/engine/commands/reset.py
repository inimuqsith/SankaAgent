"""Command /reset: hapus konteks percakapan chat ini."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


async def reset(ctx: CommandContext) -> str:
    ctx.agent.windows.reset(ctx.chat_id)
    if ctx.agent.history is not None:
        try:
            ctx.agent.history.reset(ctx.chat_id)
        except Exception:
            pass
    return "🧹 Siap! Konteks percakapan di chat ini sudah direset. Arsip riwayat tetap tersimpan."


COMMAND = Handler(
    name="reset",
    description="🧹 Mulai percakapan baru di chat ini (arsip tetap).",
    fn=reset,
    aliases=("r", "clear", "new"),
)
