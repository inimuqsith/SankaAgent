"""Command /kirim: kirim file workspace ke chat ini."""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from ..tools import resolve_path
from . import CommandContext


async def _kirim(ctx: CommandContext) -> str:
    lang = ctx.lang
    raw = ctx.args.strip()
    if not raw:
        return t("command.kirim.usage", lang)
    target, _, caption = raw.partition(" ")
    try:
        path = resolve_path(ctx.agent.config, target)
    except OSError as exc:
        return f"error: {exc}"
    result = await ctx.agent.send_file(ctx.chat_id, path, caption.strip(), gateway=ctx.gateway)
    if result.startswith("ok:"):
        return t("command.kirim.sent", lang, name=path.name)
    return t("command.kirim.failed", lang, detail=result.removeprefix("error: ").strip())


COMMAND = Handler(
    name="kirim",
    description="📤 Kirim file/foto/stiker dari workspace ke chat ini.",
    fn=_kirim,
    aliases=("sendfile", "file"),
    usage="<path> [caption]",
)
