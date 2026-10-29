"""Command /pending: daftar approval yang menunggu di chat ini."""

from __future__ import annotations

import time

from ..i18n import t
from ..registry import Handler
from . import CommandContext


async def _pending(ctx: CommandContext) -> str:
    lang = ctx.lang
    ctx.agent.approvals.expire()
    rows = ctx.agent.approvals.pending(ctx.chat_id, ctx.sender)
    if not rows:
        return t("command.pending.none", lang)
    lines = [t("command.pending.title", lang, count=len(rows))]
    for row in rows:
        left = max(0, int(row.expires_at - time.time()))
        lines.append(
            t(
                "command.pending.line",
                lang,
                id=row.id,
                kind=row.kind,
                summary=row.summary or row.tool,
                left=left,
            )
        )
    lines.append(t("command.pending.footer", lang))
    return "\n".join(lines)


COMMAND = Handler(
    name="pending",
    description="🕓 Daftar approval yang menunggu di chat ini.",
    fn=_pending,
)
