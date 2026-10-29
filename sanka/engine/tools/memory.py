"""Memory tool: one door to person/chat/global memory through Mem0 (async, cross-chat)."""

from __future__ import annotations

from ..memory import MemoryHit
from ..memory.privacy import MAX_LEVEL, PrivacyPolicy, clamp_level
from ..registry import Handler
from . import ToolContext, _int

SCOPES = ("person", "chat", "global")


def _line(hit: MemoryHit) -> str:
    mark = "" if hit.confirmed else "?"
    return (
        f"#{hit.id[:8]} [{hit.scope}/L{hit.level}{mark}] "
        f"{hit.metadata.get('kind') or 'fact'} — {hit.text}"
    )


def _identity(ctx: ToolContext, scope: str) -> tuple[str, str]:
    person = ""
    chat = ""
    if scope == "person":
        person = ctx.person_id or ctx.sender
        if not person:
            raise ValueError("no sender identity")
    elif scope == "chat":
        if not ctx.chat_id:
            raise ValueError("no active chat")
        chat = ctx.chat_id
    return person, chat


def _owner(ctx: ToolContext) -> str:
    return ctx.person_id or ctx.sender


def _policy(ctx: ToolContext) -> PrivacyPolicy:
    return PrivacyPolicy(ctx.config.memory.privacy)


async def _memory(args: dict, ctx: ToolContext) -> str:
    if ctx.memory is None or not getattr(ctx.memory, "available", False):
        reason = getattr(ctx.memory, "reason", "not available")
        return f"error: memory is not available ({reason})"
    action = str(args.get("action") or "search").strip().lower()
    scope = str(args.get("scope") or "person").strip().lower()
    if scope not in SCOPES:
        return "error: scope must be 'person', 'chat', or 'global'"
    try:
        person, chat = _identity(ctx, scope)
    except ValueError as exc:
        return f"error: {exc}"
    limit = max(1, min(_int(args, "limit", 10), 20))
    config = ctx.config.memory.privacy

    if action == "add":
        text = str(args.get("text") or "").strip()
        if not text:
            return "error: text is empty"
        try:
            raw_level = int(args.get("level", config.default_level))
        except (TypeError, ValueError):
            return "error: level must be an integer 0-4"
        if raw_level > MAX_LEVEL:
            return "error: privacy level 5/credentials are never stored; use 0-4"
        level = clamp_level(raw_level, config.default_level)
        if scope == "global" and not ctx.memory_admin:
            return "error: global memory requires memory_admin"
        if level >= config.ask_level and args.get("confirmed") is not True:
            return (
                "error: ask the sender how private this is, then retry with confirmed=true"
            )
        kind = str(args.get("kind") or "note")
        supersedes = args.get("supersedes")
        owner = _owner(ctx)
        subject = owner
        raw_subject = str(args.get("subject") or "").strip()
        if raw_subject and ctx.affect is not None:
            try:
                subject = ctx.affect.person_by_name(ctx.chat_id, raw_subject) or owner
            except Exception:
                subject = owner
        elif raw_subject:
            subject = raw_subject
        try:
            ids = await ctx.memory.add(
                text,
                scope=scope,
                kind=kind,
                person_id=person,
                chat_id=chat,
                source="tool",
                supersedes=str(supersedes) if supersedes not in (None, "") else None,
                level=level,
                owner_id=owner,
                subject_id=subject,
                confirmed=True,
            )
        except ValueError as exc:
            return f"error: {exc}"
        if not ids:
            return "error: memory was not stored"
        note = f"; #{supersedes} superseded" if supersedes not in (None, "") else ""
        return f"ok: memory {ids[0][:8]} saved ({scope}/L{level}/{kind}){note}."

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
        policy = _policy(ctx)
        shown = [row for row in rows if policy.show(row, is_dm=ctx.is_dm)]
        ctx.memory_hits.extend(shown)
        if not shown:
            return "No matching memory." if query else "No memory yet."
        head = f"Memory {scope} ({len(shown)} results):"
        return "\n".join([head, *(_line(row) for row in shown)])

    if action == "forget":
        raw_id = args.get("id")
        query = str(args.get("query") or "").strip()
        allow_global = ctx.memory_admin
        if raw_id not in (None, ""):
            removed = await ctx.memory.forget(
                str(raw_id),
                requester_id=_owner(ctx),
                chat_id=ctx.chat_id,
                allow_global=allow_global,
            )
            return "ok: 1 memory deleted." if removed else "No memory was deleted."
        if not query:
            return "error: id or query is required for forget"
        rows = await ctx.memory.search(
            query, person_id=person, chat_id=chat, scopes=(scope,), limit=limit
        )
        policy = _policy(ctx)
        removed = 0
        for row in rows:
            if not policy.show(row, is_dm=ctx.is_dm):
                continue
            if await ctx.memory.forget(
                row.id,
                requester_id=_owner(ctx),
                chat_id=ctx.chat_id,
                allow_global=allow_global,
            ):
                removed += 1
        return f"ok: {removed} memory entries deleted." if removed else "No memory was deleted."

    if action == "veto":
        active = args.get("active")
        active = True if active is None else bool(active)
        subject = _owner(ctx)
        raw_id = args.get("id")
        query = str(args.get("query") or "").strip()
        if raw_id not in (None, ""):
            ok = await ctx.memory.veto(str(raw_id), subject_id=subject, active=active)
            if not ok:
                return "error: only the subject of a memory can veto it"
            return f"ok: memory {str(raw_id)[:8]} {'vetoed' if active else 'unvetoed'}."
        if not query:
            return "error: id or query is required for veto"
        rows = await ctx.memory.search(
            query, person_id=subject, chat_id=ctx.chat_id, scopes=SCOPES, limit=limit
        )
        policy = _policy(ctx)
        changed = 0
        for row in rows:
            if row.subject_id != subject or not policy.show(row, is_dm=True):
                continue
            if await ctx.memory.veto(row.id, subject_id=subject, active=active):
                changed += 1
        if not changed:
            return "error: only the subject of a memory can veto it"
        return f"ok: {changed} memory entries {'vetoed' if active else 'unvetoed'}."

    return "error: action must be add, search, list, forget, or veto"


TOOL = Handler(
    name="memory",
    family="write",
    description=(
        "Long-term memory across chats: add/search/list/forget/veto. scope=person stores facts "
        "about the sender and follows them into every chat; scope=chat keeps group-specific "
        "things in this chat; scope=global stores shared team facts and requires memory_admin. "
        "level is 0 public, 1 team, 2 personal, 3 sensitive/aib, 4 secret; level 5 and "
        "credentials are never stored. One short question before saving a level>=4 entry, then "
        "retry with confirmed=true. subject=<name> marks the person a third-party fact is "
        "about; only that subject can veto it. veto hides an entry from context and tool "
        "output; active=false restores it. Never reveal another chat's private memory to a "
        "third person. Pass supersedes=<id> when a new fact replaces an old entry."
    ),
    fn=_memory,
    aliases=("memori",),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "add, search, list, forget, or veto.",
            },
            "scope": {"type": "string", "description": "person (default), chat, or global."},
            "text": {"type": "string", "description": "Memory text for action=add."},
            "kind": {"type": "string", "description": "fact, preference, rule, or note."},
            "level": {
                "type": "integer",
                "description": "Privacy level 0-4 (default: memory.privacy.default_level).",
            },
            "subject": {
                "type": "string",
                "description": "Person the fact is about (name or id); defaults to the sender.",
            },
            "confirmed": {
                "type": "boolean",
                "description": "true only after the sender confirmed a level>=4 entry.",
            },
            "active": {
                "type": "boolean",
                "description": "veto=true (default) hides; false restores.",
            },
            "query": {"type": "string", "description": "Search/forget/veto keywords."},
            "id": {"type": "string", "description": "Memory ID to forget or veto."},
            "supersedes": {
                "type": "string",
                "description": "Active memory ID replaced by the new text (same scope).",
            },
            "limit": {"type": "integer", "description": "Result count 1-20 (default 10)."},
        },
        "additionalProperties": False,
    },
)
