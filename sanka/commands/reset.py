"""Command /reset: hapus konteks percakapan chat ini."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


async def reset(ctx: CommandContext) -> str:
    ctx.app.sessions.reset(ctx.chat_id)
    return "🧹 Siap! Konteks percakapan di chat ini sudah direset. Arsip riwayat tetap tersimpan."


COMMAND = Handler(
    name="reset",
    description="🧹 Mulai percakapan baru di chat ini (arsip tetap).",
    fn=reset,
    aliases=("r", "clear", "new"),
    defaults={"owner": True, "member": True},
)
