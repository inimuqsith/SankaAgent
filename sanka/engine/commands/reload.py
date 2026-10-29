"""Command /reload: muat ulang dokumen config/soul/role tanpa restart."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


async def reload(ctx: CommandContext) -> str:
    try:
        summary = await ctx.agent.reload()
    except Exception as exc:
        return f"error: {type(exc).__name__}: {exc}"
    return f"♻️ Reload selesai. {summary}".strip()


COMMAND = Handler(
    name="reload",
    description="♻️ Muat ulang config/soul/role dari dokumen DB tanpa restart.",
    fn=reload,
)
