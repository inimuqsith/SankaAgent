"""Command /stop: owner menghentikan balasan otomatis di chat ini sementara."""

from __future__ import annotations

import time

from ..i18n import t
from ..registry import Handler
from . import CommandContext

MAX_MINUTES = 1440


async def stop(ctx: CommandContext) -> str:
    lang = ctx.lang
    raw = ctx.args.strip()
    if raw and not raw.isdigit():
        return t("command.stop.invalid", lang)
    minutes = int(raw) if raw else max(1, int(ctx.agent.config.reply.quiet_seconds // 60))
    if minutes > MAX_MINUTES:
        return t("command.stop.max", lang, max=MAX_MINUTES)
    if minutes <= 0:
        ctx.agent.quiet.pop(ctx.chat_id, None)
        return t("command.stop.active", lang)
    ctx.agent.quiet[ctx.chat_id] = time.time() + minutes * 60
    return t("command.stop.quiet", lang, minutes=minutes)


COMMAND = Handler(
    name="stop",
    description="🛑 Hentikan balasan otomatis Sanka di chat ini (owner).",
    fn=stop,
    aliases=("diam",),
    usage="/stop [menit, 0 = aktif lagi]",
)
