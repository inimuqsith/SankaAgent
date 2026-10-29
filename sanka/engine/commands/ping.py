"""Command /ping: cek bot hidup."""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from . import CommandContext


async def ping(ctx: CommandContext) -> str:
    return t("command.ping.pong", ctx.lang)


COMMAND = Handler(
    name="ping",
    description="🏓 Cek Sanka hidup.",
    fn=ping,
    aliases=("p",),
)
