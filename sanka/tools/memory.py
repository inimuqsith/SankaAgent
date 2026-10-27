"""Tool memory: satu pintu untuk memori chat/global, termasuk lupa yang diputuskan AI."""

from __future__ import annotations

from ..registry import Handler
from . import OWNER_MEMBER, ToolContext, _int


def _line(row: dict) -> str:
    label = "global" if row["scope"] == "global" else "chat"
    return f"#{row['id']} [{label}/{row['kind']}] {row['text']}"


def _scopes(ctx: ToolContext, scope: str) -> list[str]:
    if scope == "global":
        return ["global"]
    if not ctx.chat_id:
        raise ValueError("tidak ada chat aktif")
    return [ctx.chat_id]


async def _memory(args: dict, ctx: ToolContext) -> str:
    if ctx.memory is None:
        return "error: memori tidak tersedia"
    action = str(args.get("action") or "search").strip().lower()
    scope = str(args.get("scope") or "chat").strip().lower()
    if scope not in {"chat", "global"}:
        return "error: scope harus 'chat' atau 'global'"
    try:
        scopes = _scopes(ctx, scope)
    except ValueError as exc:
        return f"error: {exc}"

    if action == "add":
        text = str(args.get("text") or "").strip()
        if not text:
            return "error: text kosong"
        kind = str(args.get("kind") or "note")
        raw = args.get("supersedes")
        try:
            supersedes = int(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            return "error: supersedes harus ID memori"
        pad = ctx.affect.pad(ctx.chat_id) if ctx.affect is not None and ctx.chat_id else None
        mood = (
            {"valence": pad["pleasure"], "arousal": pad["arousal"]}
            if pad is not None
            else {"valence": None, "arousal": None}
        )
        try:
            item_id, created = ctx.memory.add(
                scopes[0], kind, text, source="tool", supersedes=supersedes, **mood
            )
        except ValueError as exc:
            return f"error: {exc}"
        verb = "disimpan" if created else "diperbarui"
        note = f"; #{supersedes} digantikan" if supersedes is not None else ""
        return f"ok: memori #{item_id} {verb} ({scopes[0]}/{kind}){note}."

    if action in {"search", "list"}:
        query = str(args.get("query") or "").strip()
        rows = ctx.memory.search(scopes, query, _int(args, "limit", 10))
        if not rows:
            return "Tidak ada memori yang cocok." if query else "Belum ada memori."
        head = f"Memori ({len(rows)} hasil, terbaru dulu):" if query else f"Memori terbaru ({len(rows)}):"
        return "\n".join([head, *(_line(row) for row in rows)])

    if action == "forget":
        item_id = args.get("id")
        try:
            count = ctx.memory.forget(
                scopes,
                item_id=int(item_id) if item_id not in (None, "") else None,
                query=str(args.get("query") or ""),
            )
        except (TypeError, ValueError) as exc:
            return f"error: {exc}"
        return f"ok: {count} memori dihapus." if count else "Tidak ada memori yang dihapus."

    return "error: action harus add, search, list, atau forget"


TOOL = Handler(
    name="memory",
    family="write",
    description=(
        "Memori jangka panjang: add/search/list/forget untuk chat ini (scope=chat) atau lintas "
        "grup (scope=global). AI memutuskan sendiri apa yang layak disimpan/dilupakan. Saat fakta "
        "baru menggantikan entri lama, isi supersedes dengan ID dari blok memori (scope dan kind "
        "harus sama); entri lama ditandai digantikan, bukan dihapus."
    ),
    fn=_memory,
    aliases=("memori",),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "add, search, list, atau forget."},
            "scope": {"type": "string", "description": "chat (default) atau global lintas grup."},
            "text": {"type": "string", "description": "Teks memori saat action=add."},
            "kind": {"type": "string", "description": "fact, preference, rule, atau note."},
            "query": {"type": "string", "description": "Kata kunci search/forget."},
            "id": {"type": "integer", "description": "ID memori untuk forget."},
            "supersedes": {
                "type": "integer",
                "description": "ID memori aktif yang digantikan teks baru (scope dan kind sama).",
            },
            "limit": {"type": "integer", "description": "Jumlah hasil 1-20 (default 10)."},
        },
        "additionalProperties": False,
    },
    defaults=OWNER_MEMBER,
)
