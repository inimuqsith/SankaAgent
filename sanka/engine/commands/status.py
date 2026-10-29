"""Command /status: ringkasan runtime, lease, arsip, memori, approval, dan config."""

from __future__ import annotations

import time

from sqlalchemy import select

from ..i18n import t
from ..registry import Handler
from ..schema import config_changes, memory_legacy
from . import CommandContext


def _ago(stamp: float, lang: str) -> str:
    from ..runtime import human_duration

    if not stamp:
        return "-"
    return t("command.status.ago", lang, duration=human_duration(time.time() - float(stamp)))


def _chat_label(ctx: CommandContext, lang: str) -> str:
    kind = t(f"command.status.{'chat_group' if ctx.chat_id.endswith('@g.us') else 'chat_dm'}", lang)
    name = ctx.gateway.chat_name(ctx.chat_id) if ctx.gateway is not None else ""
    label = t("command.status.chat_label", lang, kind=kind, name=name) if name else kind
    return t("command.status.chat_roles", lang, label=label, roles=ctx.role_names())


def _last_change(agent, lang: str) -> str:
    try:
        found = agent.db.fetch_one(
            select(config_changes).order_by(config_changes.c.id.desc()).limit(1)
        )
    except Exception:
        return "-"
    if not found:
        return "-"
    return t(
        "command.status.changed",
        lang,
        ago=_ago(found["ts"], lang),
        actor=found["actor"] or "-",
    )


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
    lang = ctx.lang
    lines = [
        t("command.status.title", lang),
        t("command.status.mode", lang, value=runtime.mode),
        t("command.status.lease", lang, value=runtime.lease_label()),
        t("command.status.uptime", lang, value=runtime.uptime),
        t("command.status.surface", lang, value=runtime.surface()),
        t("command.status.model", lang, value=config.llm.model),
        t("command.status.database", lang, value=f"{agent.db.label} ({agent.db.dialect})"),
        t("command.status.chat", lang, value=_chat_label(ctx, lang)),
    ]
    if store is not None:
        try:
            total = store.message_count()
            window = len(agent.windows.get(ctx.chat_id))
            lines.append(t("command.status.archive", lang, total=total, window=window))
        except Exception:
            lines.append(t("command.status.archive_unreadable", lang))
    from ..memory import memory_status

    lines.append(t("command.status.memory", lang, value=memory_status(agent.memory)))
    lines.append(t("command.status.legacy", lang, value=_legacy_pending(agent)))
    lines.append(t("command.status.approvals", lang, value=agent.approvals.count_pending()))
    skills = len(agent.skills.names()) if agent.skills is not None else 0
    lines.append(t("command.status.skills", lang, value=skills))
    people = 0
    if agent.affect is not None:
        try:
            people = int(agent.affect.stats().get("people", 0))
        except Exception:
            people = 0
    lines.append(t("command.status.people", lang, people=people, actions=agent.ledger.count()))
    soul = config.soul
    privacy = config.memory.privacy
    mirror = soul.source or "-"
    lines.append(
        t(
            "command.status.soul",
            lang,
            chars=len(soul.persona),
            mirror=mirror,
            language=soul.language,
        )
    )
    lines.append(t("command.status.language", lang, language=soul.language))
    lines.append(
        t(
            "command.status.privacy",
            lang,
            enabled=privacy.enabled,
            auditor=privacy.auditor,
        )
    )
    lines.append(t("command.status.engine", lang, path=config.paths.config))
    lines.append(
        t(
            "command.status.config",
            lang,
            path=config.paths.config,
            roles=config.paths.roles,
            change=_last_change(agent, lang),
        )
    )
    return "\n".join(lines)


COMMAND = Handler(
    name="status",
    description="⚙️ Ringkasan mode, lease, model, arsip, memori, approval, dan config.",
    fn=status,
)
