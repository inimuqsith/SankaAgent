"""Command /help: kartu command atau detail satu command."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


def _aliases(handler: Handler) -> str:
    return f" ({', '.join(handler.aliases)})" if handler.aliases else ""


def _detail(handler: Handler) -> str:
    usage = f"/{handler.name} {handler.usage}".strip()
    lines = [
        f"🧭 */{handler.name}*",
        f"• Fungsi: {handler.description}",
        f"• Pakai: {usage}",
    ]
    if handler.aliases:
        lines.append(f"• Alias: {', '.join('/' + alias for alias in handler.aliases)}")
    return "\n".join(lines)


async def help_command(ctx: CommandContext) -> str:
    store = ctx.agent.recipes
    name = ctx.args.strip().lstrip("/").split(" ", 1)[0].lower()
    if name:
        from . import REGISTRY  # lazy: package REGISTRY belum ada saat discovery

        handler = REGISTRY.get(name)
        if handler is None and store is not None:
            try:
                recipe = store.get(name)
            except Exception:
                recipe = None
            if recipe is not None:
                names = {role.name for role in ctx.roles}
                if not (names & set(recipe.roles)):
                    return f"🔒 Command /{name} tidak tersedia untuk role {ctx.role_names()}."
                usage = f"/{recipe.name} {recipe.usage}".rstrip()
                return f"🧪 */{recipe.name}*\n• Fungsi: {recipe.description}\n• Pakai: {usage}"
        if handler is None:
            return f"🤔 Command /{name} tidak dikenal. Ketik /help untuk daftar command."
        if all(item.name != handler.name for item in ctx.commands):
            return f"🔒 Command /{name} tidak tersedia untuk role {ctx.role_names()}."
        return _detail(handler)

    lines = ["🧭 *Command Sanka*"]
    for handler in ctx.commands:
        usage = f" {handler.usage}" if handler.usage else ""
        lines.append(f"/{handler.name}{usage}{_aliases(handler)} - {handler.description}")
    if store is not None:
        try:
            names = store.names()
        except Exception:
            names = []
        names_set = {role.name for role in ctx.roles}
        recipes = []
        for item in names:
            try:
                recipe = store.get(item)
            except Exception:
                continue
            if recipe is not None and names_set & set(recipe.roles):
                usage = f" {recipe.usage}" if recipe.usage else ""
                recipes.append(f"/{recipe.name}{usage} - {recipe.description}")
        if recipes:
            lines.append("🧪 *Resep user*")
            lines.extend(recipes)
    lines.append("Ketik /help <command> untuk detail, atau ngobrol biasa tanpa slash.")
    return "\n".join(lines)


COMMAND = Handler(
    name="help",
    description="🧭 Daftar command sesuai peranmu.",
    fn=help_command,
    aliases=("h", "bantuan"),
    usage="[command]",
)
