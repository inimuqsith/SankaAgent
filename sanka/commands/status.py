"""Command /status: ringkasan runtime, arsip, memori, skill, dan tool."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


def _chat_label(ctx: CommandContext) -> str:
    kind = "grup" if ctx.chat_id.endswith("@g.us") else "pribadi"
    try:
        name = ctx.app.sessions.chat_name(ctx.chat_id)
    except Exception:  # noqa: BLE001
        name = ""
    return f'{kind} "{name}"' if name else kind


def _memori(app, chat_id: str) -> str:
    memory = getattr(app, "memory", None)
    if memory is None:
        return "tidak aktif"
    try:
        chat_count = memory.count([chat_id]) if chat_id else 0
        global_count = memory.count(["global"])
    except Exception:  # noqa: BLE001
        return "tidak terbaca"
    return f"{chat_count} chat, {global_count} global"


def _skill(app) -> str:
    skills = getattr(app, "skills", None)
    if skills is None:
        return "tidak aktif"
    try:
        return str(len(skills.names()))
    except Exception:  # noqa: BLE001
        return "tidak terbaca"


async def status(ctx: CommandContext) -> str:
    app = ctx.app
    lines = [
        "⚙️ *Status Sanka*",
        f"• Model: {app.config.llm.model}",
        f"• Role: {ctx.role.name}",
        f"• Chat: {_chat_label(ctx)}",
    ]
    store = getattr(app.sessions, "store", None)
    if store is not None:
        try:
            total = store.message_count()
            window = len(app.sessions.get(ctx.chat_id))
            lines.append(f"• Arsip: {total} pesan ({window} di jendela ini)")
        except Exception:  # noqa: BLE001
            lines.append("• Arsip: tidak terbaca")
    lines.append(f"• Memori: {_memori(app, ctx.chat_id)}")
    lines.append(f"• Skill: {_skill(app)}")
    mcp = sum(1 for handler in ctx.tools if handler.name.startswith("mcp__"))
    lines.append(f"• Tool: {len(ctx.tools)} ({mcp} MCP)")
    return "\n".join(lines)


COMMAND = Handler(
    name="status",
    description="⚙️ Ringkasan model, arsip, memori, skill, dan tool.",
    fn=status,
    defaults={"owner": True, "member": True},
)
