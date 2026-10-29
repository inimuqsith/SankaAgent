"""Command /freeze: minta instance ini masuk standby; instance lain bisa takeover."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


async def freeze(ctx: CommandContext) -> str:
    agent = ctx.agent
    if agent.lease is None:
        return "error: lease tidak aktif"
    agent.request_freeze()
    return (
        "🧊 Freeze diminta. Aku berhenti membalas sebentar lagi; instance lain bisa "
        "mengambil alih dengan `sanka console --takeover`."
    )


COMMAND = Handler(
    name="freeze",
    description="🧊 Bekukan instance ini (owner/admin); instance lain bisa takeover.",
    fn=freeze,
)
