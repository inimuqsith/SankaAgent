"""Command /stop: owner menghentikan balasan otomatis di chat ini sementara."""

from __future__ import annotations

import time

from ..registry import Handler
from . import CommandContext

MAX_MINUTES = 1440


async def stop(ctx: CommandContext) -> str:
    raw = ctx.args.strip()
    if raw and not raw.isdigit():
        return "pakai /stop atau /stop <menit> (0 = aktif lagi)."
    minutes = int(raw) if raw else max(1, int(ctx.agent.config.reply.quiet_seconds // 60))
    if minutes > MAX_MINUTES:
        return f"maksimal {MAX_MINUTES} menit."
    if minutes <= 0:
        ctx.agent.quiet.pop(ctx.chat_id, None)
        return "ok, aku aktif lagi."
    ctx.agent.quiet[ctx.chat_id] = time.time() + minutes * 60
    return f"ok, aku diam {minutes} menit. Panggil /stop 0 kalau mau aku aktif lagi."


COMMAND = Handler(
    name="stop",
    description="🛑 Hentikan balasan otomatis Sanka di chat ini (owner).",
    fn=stop,
    aliases=("diam",),
    usage="/stop [menit, 0 = aktif lagi]",
)
