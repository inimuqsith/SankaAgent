"""Command /kirim: kirim file workspace ke chat ini."""

from __future__ import annotations

from ..registry import Handler
from ..tools import resolve_path
from . import CommandContext


async def _kirim(ctx: CommandContext) -> str:
    raw = ctx.args.strip()
    if not raw:
        return "Gunakan /kirim <path> [caption]."
    target, _, caption = raw.partition(" ")
    try:
        path = resolve_path(ctx.app.config, target)
    except OSError as exc:
        return f"error: {exc}"
    return await ctx.app.send_file(ctx.chat_id, path, caption.strip())


COMMAND = Handler(
    name="kirim",
    description="Kirim file/foto/stiker dari workspace ke chat ini.",
    fn=_kirim,
    aliases=("sendfile", "file"),
    usage="<path> [caption]",
    defaults={"owner": True, "member": True},
)
