"""Command /memori: add/search/list/delete person, chat, or global memory (no LLM)."""

from __future__ import annotations

from ..memory import MemoryHit
from ..memory.privacy import PrivacyPolicy, clamp_level
from ..registry import Handler
from . import CommandContext

SCOPES = ("person", "chat", "global")


def _line(hit: MemoryHit) -> str:
    mark = "" if hit.confirmed else "?"
    return f"• [{hit.scope}] L{hit.level}{mark} {hit.text}"


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
    config = ctx.config.memory.privacy
    scope, raw = _scope(ctx.args.strip())
    inbound = ctx.inbound
    requester = (inbound.person_id if inbound is not None else "") or ctx.sender
    is_dm = bool(inbound is not None and inbound.is_dm)
    memory_admin = any(role.memory_admin for role in ctx.roles)
    if scope == "person" and not requester:
        return "error: tidak ada pengirim"
    if scope == "chat" and not ctx.chat_id:
        return "error: tidak ada chat aktif"
    action, _, rest = raw.partition(" ")
    action = action.lower()
    chat_id = ctx.chat_id if scope == "chat" else ""
    person_id = requester if scope == "person" else ""
    policy = PrivacyPolicy(config)

    if not raw or action in {"daftar", "list"}:
        rows = await memory.list(
            scope=scope, person_id=person_id, chat_id=chat_id, limit=50
        )
        shown = [row for row in rows if policy.show(row, is_dm=is_dm)]
        label = {"person": "tentangmu", "chat": "chat ini", "global": "global"}[scope]
        return (
            "\n".join([f"🧠 Memori {label}:", *(_line(row) for row in shown)])
            if shown
            else "🧠 Belum ada memori."
        )

    if action in {"cari", "search"}:
        rows = await memory.search(
            rest.strip(),
            person_id=person_id,
            chat_id=chat_id,
            scopes=(scope,),
            limit=50,
        )
        shown = [row for row in rows if policy.show(row, is_dm=is_dm)]
        return (
            "\n".join(["🔎 Hasil memori:", *(_line(row) for row in shown)])
            if shown
            else "🔎 Tidak ada memori yang cocok."
        )

    if action in {"hapus", "forget", "lupakan"}:
        if scope == "global" and not memory_admin:
            return "error: memori global butuh memory_admin"
        target = rest.strip()
        if target.isdigit():
            removed = await memory.forget(
                target,
                requester_id=requester,
                chat_id=ctx.chat_id,
                allow_global=memory_admin,
            )
            return "🧹 1 memori dihapus." if removed else "🧹 Tidak ada memori yang dihapus (atau bukan milikmu)."
        rows = await memory.search(
            target, person_id=person_id, chat_id=chat_id, scopes=(scope,), limit=50
        )
        removed = 0
        for row in rows:
            if not policy.show(row, is_dm=is_dm):
                continue
            if await memory.forget(
                row.id,
                requester_id=requester,
                chat_id=ctx.chat_id,
                allow_global=memory_admin,
            ):
                removed += 1
        return f"🧹 {removed} memori dihapus." if removed else "🧹 Tidak ada memori yang dihapus."

    text = (rest if action in {"tambah", "add", "simpan"} else raw).strip()
    if not text:
        return "🧠 Gunakan /memori [person|chat|global] tambah <teks>, cari <kata>, hapus <id|kata>, atau daftar."
    if scope == "global" and not memory_admin:
        return "error: memori global butuh memory_admin"
    try:
        ids = await memory.add(
            text,
            scope=scope,
            kind="note",
            person_id=person_id,
            chat_id=chat_id,
            source="command",
            level=clamp_level(config.default_level, 2),
            owner_id=requester,
            subject_id=requester,
            confirmed=True,
        )
    except ValueError as exc:
        return f"error: {exc}"
    if not ids:
        return "error: memori tidak tersimpan"
    return f"✅ Memori {ids[0]} disimpan ({scope}/L{config.default_level}/note)."


COMMAND = Handler(
    name="memori",
    description="🧠 Tambah, cari, daftar, atau hapus memori person/chat/global.",
    fn=_memori,
    aliases=("memory", "ingat"),
    usage="[person|chat|global] [tambah|cari|hapus|daftar] <teks>",
)
