"""Tool memory: satu pintu memori person/chat/global lewat Mem0 (async, lintas chat)."""

from __future__ import annotations

from ..memory import MemoryHit
from ..registry import Handler
from . import ToolContext, _int

SCOPES = ("person", "chat", "global")


def _line(hit: MemoryHit) -> str:
    return f"#{hit.id[:8]} [{hit.scope}/{hit.metadata.get('kind') or 'fact'}] {hit.text}"


def _identity(ctx: ToolContext, scope: str) -> tuple[str, str]:
    person = ""
    chat = ""
    if scope == "person":
        person = ctx.sender
        if not person:
            raise ValueError("tidak ada pengirim")
    elif scope == "chat":
        if not ctx.chat_id:
            raise ValueError("tidak ada chat aktif")
        chat = ctx.chat_id
    return person, chat


async def _memory(args: dict, ctx: ToolContext) -> str:
    if ctx.memory is None or not getattr(ctx.memory, "available", False):
        reason = getattr(ctx.memory, "reason", "tidak tersedia")
        return f"error: memori tidak tersedia ({reason})"
    action = str(args.get("action") or "search").strip().lower()
    scope = str(args.get("scope") or "person").strip().lower()
    if scope not in SCOPES:
        return "error: scope harus 'person', 'chat', atau 'global'"
    try:
        person, chat = _identity(ctx, scope)
    except ValueError as exc:
        return f"error: {exc}"
    limit = max(1, min(_int(args, "limit", 10), 20))

    if action == "add":
        text = str(args.get("text") or "").strip()
        if not text:
            return "error: text kosong"
        kind = str(args.get("kind") or "note")
        supersedes = args.get("supersedes")
        try:
            ids = await ctx.memory.add(
                text,
                scope=scope,
                kind=kind,
                person_id=person,
                chat_id=chat,
                source="tool",
                supersedes=str(supersedes) if supersedes not in (None, "") else None,
            )
        except ValueError as exc:
            return f"error: {exc}"
        if not ids:
            return "error: memori tidak tersimpan"
        note = f"; #{supersedes} digantikan" if supersedes not in (None, "") else ""
        return f"ok: memori {ids[0][:8]} disimpan ({scope}/{kind}){note}."

    if action in {"search", "list"}:
        query = str(args.get("query") or "").strip()
        if action == "search" and query:
            rows = await ctx.memory.search(
                query, person_id=person, chat_id=chat, scopes=(scope,), limit=limit
            )
        else:
            rows = await ctx.memory.list(
                scope=scope, person_id=person, chat_id=chat, limit=limit
            )
        if not rows:
            return "Tidak ada memori yang cocok." if query else "Belum ada memori."
        head = f"Memori {scope} ({len(rows)} hasil):"
        return "\n".join([head, *(_line(row) for row in rows)])

    if action == "forget":
        raw_id = args.get("id")
        query = str(args.get("query") or "").strip()
        if raw_id not in (None, ""):
            removed = await ctx.memory.forget(
                str(raw_id), person_id=person, chat_id=chat
            )
            return "ok: 1 memori dihapus." if removed else "Tidak ada memori yang dihapus."
        if not query:
            return "error: isi id atau query untuk forget"
        rows = await ctx.memory.search(
            query, person_id=person, chat_id=chat, scopes=(scope,), limit=limit
        )
        removed = 0
        for row in rows:
            if await ctx.memory.forget(row.id, person_id=person, chat_id=chat):
                removed += 1
        return f"ok: {removed} memori dihapus." if removed else "Tidak ada memori yang dihapus."

    return "error: action harus add, search, list, atau forget"


TOOL = Handler(
    name="memory",
    family="write",
    description=(
        "Long-term memory across chats: add/search/list/forget. scope=person stores facts about "
        "the sender and follows them into every chat; scope=chat keeps group-specific things in "
        "this chat; scope=global stores shared team facts. Never reveal another chat's private "
        "memory to a third person. Pass supersedes=<id> when a new fact replaces an old entry."
    ),
    fn=_memory,
    aliases=("memori",),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "add, search, list, or forget."},
            "scope": {"type": "string", "description": "person (default), chat, or global."},
            "text": {"type": "string", "description": "Memory text for action=add."},
            "kind": {"type": "string", "description": "fact, preference, rule, or note."},
            "query": {"type": "string", "description": "Search/forget keywords."},
            "id": {"type": "string", "description": "Memory ID to forget."},
            "supersedes": {
                "type": "string",
                "description": "Active memory ID replaced by the new text (same scope).",
            },
            "limit": {"type": "integer", "description": "Result count 1-20 (default 10)."},
        },
        "additionalProperties": False,
    },
)
