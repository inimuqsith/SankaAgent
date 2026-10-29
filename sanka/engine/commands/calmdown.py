"""Command /calmdown: owner menenangkan Sanka setelah kesal atau terluka."""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from . import CommandContext


async def calmdown(ctx: CommandContext) -> str:
    lang = ctx.lang
    affect = ctx.agent.affect
    if affect is None:
        return t("command.calmdown.unavailable", lang)
    target = ctx.args.strip()
    try:
        was_upset = affect.calm(ctx.chat_id, target)
    except Exception:
        return t("command.calmdown.failed", lang)
    if not was_upset:
        return t("command.calmdown.already", lang)
    if not target:
        return t("command.calmdown.done", lang)
    return t("command.calmdown.forgiven", lang, target=target)


COMMAND = Handler(
    name="calmdown",
    description="🧊 Tenangkan Sanka setelah kesal atau terluka (owner).",
    fn=calmdown,
    aliases=("tenang",),
    usage="/calmdown [nama atau nomor]",
)
