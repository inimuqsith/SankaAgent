"""Command /skill: daftar atau muat prosedur lokal dari DB."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


async def _skill(ctx: CommandContext) -> str:
    store = ctx.agent.skills
    if store is None:
        return "error: skill tidak tersedia"
    raw = ctx.args.strip()
    if not raw:
        return store.index() or "📚 Belum ada skill lokal."
    action, _, rest = raw.partition(" ")
    action = action.lower()
    if action in {"list", "daftar"}:
        return store.index() or "📚 Belum ada skill lokal."
    try:
        if action in {"read", "file"}:
            name, _, filename = rest.partition(" ")
            if not name or not filename:
                return "Gunakan /skill read <nama> <file>."
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
