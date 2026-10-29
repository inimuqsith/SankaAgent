"""Command /status: ringkasan runtime, lease, arsip, memori, approval, dan config."""

from __future__ import annotations

import time

from sqlalchemy import select

from ..registry import Handler
from ..schema import config_changes, memory_legacy
from . import CommandContext


def _ago(stamp: float) -> str:
    from ..runtime import human_duration

    return f"{human_duration(time.time() - float(stamp))} ago" if stamp else "-"


def _chat_label(ctx: CommandContext) -> str:
    kind = "group" if ctx.chat_id.endswith("@g.us") else "dm"
    name = ctx.gateway.chat_name(ctx.chat_id) if ctx.gateway is not None else ""
    roles = ctx.role_names()
    label = f'{kind} "{name}"' if name else kind
    return f"{label} (roles: {roles})"


def _last_change(agent) -> str:
    try:
        found = agent.db.fetch_one(
            select(config_changes).order_by(config_changes.c.id.desc()).limit(1)
        )
    except Exception:
        return "-"
    if not found:
        return "-"
    return f"{_ago(found['ts'])} by {found['actor'] or '-'}"


def _legacy_pending(agent) -> int:
    try:
        return len(
            agent.db.fetch_all(
                select(memory_legacy.c.id).where(memory_legacy.c.migrated_at.is_(None))
            )
        )
    except Exception:
        return 0


async def status(ctx: CommandContext) -> str:
    agent = ctx.agent
    config = agent.config
    runtime = agent.runtime
    store = agent.history
    lines = [
        "⚙️ *Status*",
        f"• Mode: {runtime.mode}",
        f"• Lease: {runtime.lease_label()}",
        f"• Uptime: {runtime.uptime}",
        f"• Surface: {runtime.surface()}",
        f"• Model: {config.llm.model}",
        f"• Database: {agent.db.label} ({agent.db.dialect})",
        f"• Chat: {_chat_label(ctx)}",
    ]
    if store is not None:
        try:
            total = store.message_count()
            window = len(agent.windows.get(ctx.chat_id))
            lines.append(f"• Archive: {total} messages ({window} in window)")
        except Exception:
            lines.append("• Archive: unreadable")
    from ..memory import memory_status

    lines.append(f"• Memory: {memory_status(agent.memory)}")
    lines.append(f"• Memory legacy pending: {_legacy_pending(agent)}")
    lines.append(f"• Approvals: {agent.approvals.count_pending()} pending")
    lines.append(f"• Skills: {len(agent.skills.names()) if agent.skills is not None else 0}")
    people = 0
    if agent.affect is not None:
        try:
            people = int(agent.affect.stats().get("people", 0))
        except Exception:
            people = 0
    lines.append(f"• People: {people}; Actions: {agent.ledger.count()}")
    soul = config.soul
    privacy = config.memory.privacy
    mirror = soul.source or "-"
    lines.append(
        f"• Soul: persona doc ({len(soul.persona)} chars), mirror {mirror}, language {soul.language}"
    )
    lines.append(f"• Language: {soul.language}")
    lines.append(
        f"• Privacy: enabled={privacy.enabled}; auditor={privacy.auditor}; levels 0-4"
    )
    lines.append(f"• Engine: doc `engine`; mirror {config.paths.config}")
    lines.append(
        f"• Config: {config.paths.config}; roles {config.paths.roles}; "
        f"last change {_last_change(agent)}"
    )
    return "\n".join(lines)


COMMAND = Handler(
    name="status",
    description="⚙️ Ringkasan mode, lease, model, arsip, memori, approval, dan config.",
    fn=status,
)
