"""Command /approve: eksekusi approval pending tanpa LLM, dengan izin role asli."""

from __future__ import annotations

import time

from ..registry import Handler
from ..tools import execute
from . import CommandContext

CLIP = 8000


async def _approve(ctx: CommandContext) -> str:
    agent = ctx.agent
    raw = ctx.args.strip().lstrip("#")
    agent.approvals.expire()
    if raw and raw.lower() not in {"last", "terakhir"}:
        approval = agent.approvals.get(raw)
        if approval is None:
            return f"error: approval #{raw} tidak ada"
        if approval.chat_id != ctx.chat_id or approval.sender != ctx.sender:
            return "error: approval ini milik chat atau pengirim lain"
        if approval.status != "pending":
            return f"error: approval #{approval.id} sudah {approval.status}"
    else:
        approval = agent.approvals.latest(ctx.chat_id, ctx.sender)
        if approval is None:
            return "Tidak ada approval pending."
    if approval.expires_at < time.time():
        agent.approvals.mark(approval.id, "expired", ctx.sender)
        return f"error: approval #{approval.id} sudah kedaluwarsa"

    roles = tuple(
        role for name in approval.roles if (role := agent.config.roles.get(name)) is not None
    )
    if not roles:
        agent.approvals.mark(approval.id, "denied", ctx.sender, "role hilang")
        return "error: role pada approval ini sudah tidak ada"
    if approval.kind == "settings":
        try:
            summary = await agent.apply_settings_payload(approval.payload)
        except Exception as exc:
            agent.approvals.mark(approval.id, "failed", ctx.sender, str(exc))
            return f"error: {type(exc).__name__}: {exc}"
        agent.approvals.mark(approval.id, "approved", ctx.sender, str(summary)[:2000])
        return f"✅ Approval #{approval.id} diterapkan.\n{summary}"[:CLIP]

    specs = {handler.name: handler for handler in agent.tools.by_name.values()}
    if agent.hub is not None:
        specs.update({handler.name: handler for handler in agent.hub.specs(roles)})
    tool_ctx = agent.tool_context(
        ctx.chat_id,
        ctx.sender,
        roles,
        ctx.gateway,
        approved=True,
        approval_id=str(approval.id),
    )
    result = await execute(specs, approval.tool, approval.args, tool_ctx)
    agent.approvals.mark(approval.id, "approved", ctx.sender, result[:2000])
    try:
        agent.ledger.log_action(
            ctx.chat_id,
            ctx.chat_id,
            "approved",
            f"{approval.tool} approved #{approval.id}",
            source_name=ctx.gateway.chat_name(ctx.chat_id) if ctx.gateway else "",
            target_name=ctx.gateway.chat_name(ctx.chat_id) if ctx.gateway else "",
            request=approval.summary,
        )
    except Exception:
        pass
    return f"✅ Approval #{approval.id} dijalankan.\n{result}"[:CLIP]


COMMAND = Handler(
    name="approve",
    description="✅ Jalankan aksi sensitif yang menunggu approval.",
    fn=_approve,
    usage="[id|last]",
)
