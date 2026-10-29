"""Command /reload: muat ulang dokumen config/soul/role tanpa restart."""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from . import CommandContext


async def reload(ctx: CommandContext) -> str:
    try:
        summary = await ctx.agent.reload()
    except Exception as exc:
        return f"error: {type(exc).__name__}: {exc}"
    return t("command.reload.done", ctx.lang, summary=summary).strip()


COMMAND = Handler(
    name="reload",
    description="♻️ Muat ulang config/soul/role dari dokumen DB tanpa restart.",
    fn=reload,
)
