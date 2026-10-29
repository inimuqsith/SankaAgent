"""Command /deny: batalkan approval pending tanpa menjalankannya."""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from . import CommandContext


async def _deny(ctx: CommandContext) -> str:
    agent = ctx.agent
    lang = ctx.lang
    raw = ctx.args.strip()
    key, _, reason = raw.partition(" ")
    if key.lower() in {"last", "terakhir", ""}:
        approval = agent.approvals.latest(ctx.chat_id, ctx.sender)
    else:
        approval = agent.approvals.get(key.lstrip("#"))
    if approval is None:
        return t("command.deny.none", lang)
    if approval.chat_id != ctx.chat_id or approval.sender != ctx.sender:
        return t("command.deny.foreign", lang)
    if approval.status != "pending":
        return t("command.deny.already", lang, id=approval.id, status=approval.status)
    agent.approvals.mark(approval.id, "denied", ctx.sender, reason.strip())
    return t("command.deny.done", lang, id=approval.id)


COMMAND = Handler(
    name="deny",
    description="🚫 Batalkan aksi yang menunggu approval.",
    fn=_deny,
    usage="[id|last] [alasan]",
)
