"""Command /help: kartu command atau detail satu command."""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from . import CommandContext


def _aliases(handler: Handler) -> str:
    return f" ({', '.join(handler.aliases)})" if handler.aliases else ""


def _description(handler: Handler, lang: str) -> str:
    return t(f"command.{handler.name}.description", lang, default=handler.description)


def _detail(handler: Handler, lang: str) -> str:
    usage = f"/{handler.name} {handler.usage}".strip()
    lines = [
        t(
            "command.help.detail",
            lang,
            name=handler.name,
            description=_description(handler, lang),
            usage=usage,
        )
    ]
    if handler.aliases:
        aliases = ", ".join("/" + alias for alias in handler.aliases)
        lines.append(t("command.help.alias", lang, aliases=aliases))
    return "\n".join(lines)


async def help_command(ctx: CommandContext) -> str:
    store = ctx.agent.recipes
    lang = ctx.lang
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
                    return t("command.help.denied", lang, name=name, roles=ctx.role_names())
                usage = f"/{recipe.name} {recipe.usage}".rstrip()
                return t(
                    "command.help.recipe_detail",
                    lang,
                    name=recipe.name,
                    description=recipe.description,
                    usage=usage,
                )
        if handler is None:
            return t("command.help.not_found", lang, name=name)
        if all(item.name != handler.name for item in ctx.commands):
            return t("command.help.denied", lang, name=name, roles=ctx.role_names())
        return _detail(handler, lang)

    lines = [t("command.help.title", lang)]
    for handler in ctx.commands:
        usage = f" {handler.usage}" if handler.usage else ""
        description = _description(handler, lang)
        lines.append(f"/{handler.name}{usage}{_aliases(handler)} - {description}")
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
            lines.append(t("command.help.recipes_title", lang))
            lines.extend(recipes)
    lines.append(t("command.help.footer", lang))
    return "\n".join(lines)


COMMAND = Handler(
    name="help",
    description="🧭 Daftar command sesuai peranmu.",
    fn=help_command,
    aliases=("h", "bantuan"),
    usage="[command]",
)
