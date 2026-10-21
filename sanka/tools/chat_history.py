"""Tool chat_history: cari arsip pesan lama chat ini, di luar 20 pesan payload."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..registry import Handler
from . import OWNER_MEMBER, ToolContext, _int

BODY_LIMIT = 600


def _time(value, *, end: bool) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"tanggal tidak valid: {value}") from exc
    if end and len(text) <= 10:
        parsed += timedelta(days=1)
        return parsed.timestamp() - 0.000001
    return parsed.timestamp()


def _body(entry) -> str:
    text = " ".join((entry.text or "").split())
    if text:
        return text if len(text) <= BODY_LIMIT else text[: BODY_LIMIT - 1] + "…"
    if entry.media is not None:
        return f"(lampiran {entry.media.kind}: {entry.media.filename})"
    return "(tanpa teks)"


async def _search(args: dict, ctx: ToolContext) -> str:
    if ctx.history is None:
        return "error: arsip riwayat tidak tersedia"
    if not ctx.chat_id:
        return "error: tidak ada chat aktif"
    try:
        before = _time(args.get("before"), end=True)
        after = _time(args.get("after"), end=False)
    except ValueError as exc:
        return f"error: {exc}"
    query = str(args.get("query") or "").strip()
    limit = max(1, min(_int(args, "limit", 20), 50))
    results = ctx.history.search(ctx.chat_id, query, before=before, after=after, limit=limit)
    if not results:
        return "Tidak ada pesan yang cocok di arsip chat ini."
    name = ctx.history.chat_name(ctx.chat_id)
    label = f' "{name}"' if name else " chat ini"
    header = (
        f"Arsip{label} ({len(results)} hasil, terbaru dulu):"
        if query
        else f"Pesan terbaru di arsip{label} ({len(results)}):"
    )
    lines = [header]
    for entry in results:
        who = entry.sender_name or entry.sender_id or "-"
        lines.append(f"[{entry.when:%Y-%m-%d %H:%M}] {who}: {_body(entry)}")
        if entry.quote is not None:
            quote = " ".join((entry.quote.text or "").split())
            if quote:
                lines.append(f"  ↳ membalas {entry.quote.sender_name or '-'}: {quote[:300]}")
    if len(results) >= limit:
        lines.append(f"(batas {limit} hasil; persempit query atau pakai before/after)")
    return "\n".join(lines)


TOOL = Handler(
    name="chat_history",
    family="read",
    description="Cari pesan lama di arsip chat ini (di luar 20 pesan payload), urut terbaru dulu.",
    fn=_search,
    aliases=("history", "search_history"),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Kata kunci teks/nama; kosong = pesan terbaru."},
            "before": {"type": "string", "description": "Batas atas waktu, ISO YYYY-MM-DD atau YYYY-MM-DDTHH:MM."},
            "after": {"type": "string", "description": "Batas bawah waktu, ISO YYYY-MM-DD atau YYYY-MM-DDTHH:MM."},
            "limit": {"type": "integer", "description": "Jumlah hasil 1-50 (default 20)."},
        },
        "additionalProperties": False,
    },
    defaults=OWNER_MEMBER,
)
