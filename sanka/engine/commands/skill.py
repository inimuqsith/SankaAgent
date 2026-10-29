"""Command /skill: daftar atau muat prosedur lokal dari DB."""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from . import CommandContext


async def _skill(ctx: CommandContext) -> str:
    lang = ctx.lang
    store = ctx.agent.skills
    if store is None:
        return t("command.skill.unavailable", lang)
    raw = ctx.args.strip()
    if not raw:
        return store.index() or t("command.skill.none", lang)
    action, _, rest = raw.partition(" ")
    action = action.lower()
    if action in {"list", "daftar"}:
        return store.index() or t("command.skill.none", lang)
    try:
        if action in {"read", "file"}:
            name, _, filename = rest.partition(" ")
            if not name or not filename:
                return t("command.skill.read_usage", lang)
            return store.read(name, filename)
        name = rest.strip() if action in {"view", "load", "show"} else raw
        return store.view(name)
    except (KeyError, ValueError, OSError) as exc:
        return f"error: {exc}"


COMMAND = Handler(
    name="skill",
    description="📚 Daftar atau muat prosedur lokal (SKILL.md).",
    fn=_skill,
    aliases=("skills", "sop"),
    usage="[list|view|read] [nama] [file]",
)
