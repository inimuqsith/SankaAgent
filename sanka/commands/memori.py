"""Command /memori: tambah/cari/daftar memori chat atau global (tanpa hapus)."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


def _line(row: dict) -> str:
    label = "global" if row["scope"] == "global" else "chat"
    return f"• #{row['id']} [{label}/{row['kind']}] {row['text']}"


async def _memori(ctx: CommandContext) -> str:
    store = getattr(ctx.app, "memory", None)
    if store is None:
        return "error: memori tidak tersedia"
    raw = ctx.args.strip()
    scope = "chat"
    if raw.lower() == "global" or raw.lower().startswith("global "):
        scope = "global"
        raw = raw[6:].strip()
    if scope == "chat" and not ctx.chat_id:
        return "error: tidak ada chat aktif"
    action, _, rest = raw.partition(" ")
    action = action.lower()
    target = "global" if scope == "global" else ctx.chat_id

    if not raw or action in {"daftar", "list"}:
        rows = store.entries([target], "", 50)
        label = "global" if scope == "global" else "chat ini"
        return "\n".join([f"🧠 Memori {label}:", *(_line(row) for row in rows)]) if rows else "🧠 Belum ada memori."

    if action in {"cari", "search"}:
        rows = store.search([target], rest.strip(), 50)
        return "\n".join(["🔎 Hasil memori:", *(_line(row) for row in rows)]) if rows else "🔎 Tidak ada memori yang cocok."

    if action in {"hapus", "forget", "lupakan"}:
        item_id = int(rest) if rest.strip().isdigit() else None
        try:
            count = store.forget([target], item_id=item_id, query="" if item_id is not None else rest)
        except ValueError as exc:
            return f"error: {exc}"
        return f"🧹 {count} memori dihapus." if count else "🧹 Tidak ada memori yang dihapus."

    text = (rest if action in {"tambah", "add", "simpan"} else raw).strip()
    if not text:
        return "🧠 Gunakan /memori [global] tambah <teks>, cari <kata>, hapus <id|kata>, atau daftar."
    try:
        item_id, created = store.add(target, "note", text, source="command")
    except ValueError as exc:
        return f"error: {exc}"
    return f"✅ Memori #{item_id} {'disimpan' if created else 'diperbarui'} ({target}/note)."


COMMAND = Handler(
    name="memori",
    description="🧠 Tambah, cari, daftar, atau hapus memori chat/global.",
    fn=_memori,
    aliases=("memory", "ingat"),
    usage="[global] [tambah|cari|hapus|daftar] <teks>",
    defaults={"owner": True, "member": True},
)
