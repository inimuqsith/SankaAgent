"""Command /freeze: minta instance ini masuk standby; instance lain bisa takeover."""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from . import CommandContext


async def freeze(ctx: CommandContext) -> str:
    agent = ctx.agent
    if agent.lease is None:
        return t("command.freeze.no_lease", ctx.lang)
    agent.request_freeze()
    return t("command.freeze.requested", ctx.lang)


COMMAND = Handler(
    name="freeze",
    description="🧊 Bekukan instance ini (owner/admin); instance lain bisa takeover.",
    fn=freeze,
)
