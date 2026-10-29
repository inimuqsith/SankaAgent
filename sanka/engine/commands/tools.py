"""Command /tools: daftar tool yang diizinkan untuk role pengirim."""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from . import CommandContext

DESC_LIMIT = 110


def _clip(text: str) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= DESC_LIMIT else flat[: DESC_LIMIT - 1] + "…"


async def tools_command(ctx: CommandContext) -> str:
    lang = ctx.lang
    if not ctx.tools:
        return t("command.tools.none", lang)
    lines = [t("command.tools.title", lang, roles=ctx.role_names())]
    for handler in sorted(ctx.tools, key=lambda item: item.name):
        lines.append(
            t("command.tools.line", lang, name=handler.name, description=_clip(handler.description))
        )
    return "\n".join(lines)


COMMAND = Handler(
    name="tools",
    description="🧰 Daftar tool yang diizinkan untuk role ini.",
    fn=tools_command,
    aliases=("tool",),
)
