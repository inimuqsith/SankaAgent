"""Command /ping: cek bot hidup."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


async def ping(ctx: CommandContext) -> str:
    return "pong"


COMMAND = Handler(
    name="ping",
    description="Cek bot hidup.",
    fn=ping,
    aliases=("p",),
    defaults={"owner": True, "member": True},
)
