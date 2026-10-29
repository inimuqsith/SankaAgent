"""Command /pending: daftar approval yang menunggu di chat ini."""

from __future__ import annotations

import time

from ..registry import Handler
from . import CommandContext


async def _pending(ctx: CommandContext) -> str:
    ctx.agent.approvals.expire()
    rows = ctx.agent.approvals.pending(ctx.chat_id, ctx.sender)
    if not rows:
        return "✅ Tidak ada approval pending di chat ini."
    lines = [f"🕓 Approval pending ({len(rows)}):"]
    for row in rows:
        left = max(0, int(row.expires_at - time.time()))
        lines.append(
            f"• #{row.id} [{row.kind}] {row.summary or row.tool} — {left}s left"
        )
    lines.append("Ketik /approve <id> atau /deny <id>.")
    return "\n".join(lines)


COMMAND = Handler(
    name="pending",
    description="🕓 Daftar approval yang menunggu di chat ini.",
    fn=_pending,
)
