"""Command /cari: cari pesan lama di arsip chat ini tanpa LLM."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext

MAX_RESULTS = 10
TEXT_LIMIT = 180


def _clip(text: str) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= TEXT_LIMIT else flat[: TEXT_LIMIT - 1] + "…"


def _line(row) -> str:
    text = _clip(row.text) or ("[lampiran]" if row.media is not None else "(kosong)")
    who = row.sender_name or row.sender_id or "?"
    stamp = row.when.strftime("%Y-%m-%d %H:%M") if row.when else "?"
    return f"• {stamp} {who}: {text}"


async def cari(ctx: CommandContext) -> str:
    query = ctx.args.strip()
    if not query:
        return "🔎 Gunakan /cari <kata>."
    store = getattr(ctx.app.sessions, "store", None)
    if store is None:
        return "error: arsip tidak tersedia"
    try:
        rows = store.search(ctx.chat_id, query, limit=MAX_RESULTS)
    except Exception:  # noqa: BLE001
        return "error: pencarian arsip gagal"
    if not rows:
        return f'🔎 Tidak ada pesan yang cocok dengan "{query}".'
    lines = [f'🔎 *Hasil "{query}" di chat ini* (terbaru dulu)']
    lines.extend(_line(row) for row in rows)
    return "\n".join(lines)


COMMAND = Handler(
    name="cari",
    description="🔎 Cari pesan lama di arsip chat ini (tanpa LLM).",
    fn=cari,
    aliases=("search",),
    usage="<kata>",
    defaults={"owner": True, "member": True},
)
