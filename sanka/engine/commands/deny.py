"""Command /deny: batalkan approval pending tanpa menjalankannya."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


async def _deny(ctx: CommandContext) -> str:
    agent = ctx.agent
    raw = ctx.args.strip()
    key, _, reason = raw.partition(" ")
    if key.lower() in {"last", "terakhir", ""}:
        approval = agent.approvals.latest(ctx.chat_id, ctx.sender)
    else:
        approval = agent.approvals.get(key.lstrip("#"))
    if approval is None:
        return "Tidak ada approval pending."
    if approval.chat_id != ctx.chat_id or approval.sender != ctx.sender:
        return "error: approval ini milik chat atau pengirim lain"
    if approval.status != "pending":
        return f"error: approval #{approval.id} sudah {approval.status}"
    agent.approvals.mark(approval.id, "denied", ctx.sender, reason.strip())
    return f"🚫 Approval #{approval.id} dibatalkan."


COMMAND = Handler(
    name="deny",
    description="🚫 Batalkan aksi yang menunggu approval.",
    fn=_deny,
    usage="[id|last] [alasan]",
)
