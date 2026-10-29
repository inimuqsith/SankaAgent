"""Command /memori: tambah/cari/daftar/hapus memori person, chat, atau global (tanpa LLM)."""

from __future__ import annotations

from ..memory import MemoryHit
from ..registry import Handler
from . import CommandContext

SCOPES = ("person", "chat", "global")


def _line(hit: MemoryHit) -> str:
    return f"• [{hit.scope}] {hit.text}"


def _scope(raw: str) -> tuple[str, str]:
    head, _, rest = raw.partition(" ")
    if head.lower() in SCOPES:
        return head.lower(), rest.strip()
    return "person", raw.strip()


async def _memori(ctx: CommandContext) -> str:
    memory = ctx.agent.memory
    if memory is None or not getattr(memory, "available", False):
        reason = getattr(memory, "reason", "tidak tersedia")
        return f"error: memori tidak tersedia ({reason})"
    scope, raw = _scope(ctx.args.strip())
    if scope == "person" and not ctx.sender:
        return "error: tidak ada pengirim"
    if scope == "chat" and not ctx.chat_id:
        return "error: tidak ada chat aktif"
    action, _, rest = raw.partition(" ")
    action = action.lower()
    person = ctx.inbound.person_id if ctx.inbound is not None else ctx.sender
    chat_id = ctx.chat_id if scope == "chat" else ""
    person_id = person if scope == "person" else ""

    if not raw or action in {"daftar", "list"}:
        rows = await memory.list(
            scope=scope, person_id=person_id, chat_id=chat_id, limit=50
        )
        label = {"person": "tentangmu", "chat": "chat ini", "global": "global"}[scope]
        return "\n".join([f"🧠 Memori {label}:", *(_line(row) for row in rows)]) if rows else "🧠 Belum ada memori."

    if action in {"cari", "search"}:
        rows = await memory.search(
            rest.strip(),
            person_id=person_id,
            chat_id=chat_id,
            scopes=(scope,),
            limit=50,
        )
        return "\n".join(["🔎 Hasil memori:", *(_line(row) for row in rows)]) if rows else "🔎 Tidak ada memori yang cocok."

    if action in {"hapus", "forget", "lupakan"}:
        target = rest.strip()
        if target.isdigit():
            removed = await memory.forget(
                target, person_id=person_id, chat_id=chat_id
            )
            return "🧹 1 memori dihapus." if removed else "🧹 Tidak ada memori yang dihapus (atau bukan milikmu)."
        rows = await memory.search(
            target, person_id=person_id, chat_id=chat_id, scopes=(scope,), limit=50
        )
        removed = 0
        for row in rows:
            if await memory.forget(row.id, person_id=person_id, chat_id=chat_id):
                removed += 1
        return f"🧹 {removed} memori dihapus." if removed else "🧹 Tidak ada memori yang dihapus."

    text = (rest if action in {"tambah", "add", "simpan"} else raw).strip()
    if not text:
        return "🧠 Gunakan /memori [person|chat|global] tambah <teks>, cari <kata>, hapus <id|kata>, atau daftar."
    try:
        ids = await memory.add(
            text,
            scope=scope,
            kind="note",
            person_id=person_id,
            chat_id=chat_id,
            source="command",
        )
    except ValueError as exc:
        return f"error: {exc}"
    if not ids:
        return "error: memori tidak tersimpan"
    return f"✅ Memori {ids[0]} disimpan ({scope}/note)."


COMMAND = Handler(
    name="memori",
    description="🧠 Tambah, cari, daftar, atau hapus memori person/chat/global.",
    fn=_memori,
    aliases=("memory", "ingat"),
    usage="[person|chat|global] [tambah|cari|hapus|daftar] <teks>",
)
