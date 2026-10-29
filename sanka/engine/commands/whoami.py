"""Command /whoami: identitas pengirim, role, chat, dan surface."""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from . import CommandContext


def _chat_label(ctx: CommandContext, lang: str) -> str:
    kind = t(f"command.whoami.{_kind_key(ctx.chat_id)}", lang)
    name = ctx.gateway.chat_name(ctx.chat_id) if ctx.gateway is not None else ""
    if name:
        return t("command.whoami.chat_label", lang, kind=kind, name=name, id=ctx.chat_id)
    return t("command.whoami.chat_plain", lang, kind=kind, id=ctx.chat_id)


def _kind_key(chat_id: str) -> str:
    return "group" if chat_id.endswith("@g.us") else "dm"


async def whoami(ctx: CommandContext) -> str:
    lang = ctx.lang
    roles = ", ".join(role.name.capitalize() for role in ctx.roles) or "-"
    lines = [
        t("command.whoami.user", lang, who=ctx.sender or ctx.chat_id),
        t("command.whoami.role", lang, roles=roles),
        t("command.whoami.chat", lang, chat=_chat_label(ctx, lang)),
        t("command.whoami.surface", lang, surface=ctx.gateway.name if ctx.gateway is not None else "-"),
    ]
    handles = sorted(ctx.gateway.self_handles()) if ctx.gateway is not None else []
    if handles:
        lines.append(
            t(
                "command.whoami.bot",
                lang,
                name=ctx.agent.config.soul.name,
                handles=", ".join(handles),
            )
        )
    return "\n".join(lines)


COMMAND = Handler(
    name="whoami",
    description="👤 Identitas, role, chat, dan surface kamu.",
    fn=whoami,
    aliases=("id",),
)
