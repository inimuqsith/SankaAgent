"""Command /reset: hapus konteks percakapan chat ini."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


async def reset(ctx: CommandContext) -> str:
    ctx.app.sessions.reset(ctx.chat_id)
    return "Jendela konteks chat ini sudah direset; arsip riwayat tetap tersimpan."


COMMAND = Handler(
    name="reset",
    description="Hapus jendela konteks chat ini (arsip riwayat tetap).",
    fn=reset,
    aliases=("r", "clear"),
    defaults={"owner": True, "member": True},
)
