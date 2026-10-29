"""Command /kirim: kirim file workspace ke chat ini."""

from __future__ import annotations

from ..registry import Handler
from ..tools import resolve_path
from . import CommandContext


async def _kirim(ctx: CommandContext) -> str:
    raw = ctx.args.strip()
    if not raw:
        return "📤 Gunakan /kirim <path> [caption]."
    target, _, caption = raw.partition(" ")
    try:
        path = resolve_path(ctx.agent.config, target)
    except OSError as exc:
        return f"error: {exc}"
    return await ctx.agent.send_file(
        ctx.chat_id, path, caption.strip(), gateway=ctx.gateway
    )


COMMAND = Handler(
    name="kirim",
    description="📤 Kirim file/foto/stiker dari workspace ke chat ini.",
    fn=_kirim,
    aliases=("sendfile", "file"),
    usage="<path> [caption]",
)
