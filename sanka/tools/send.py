"""Tool send: kirim file dari workspace ke chat aktif sebagai dokumen WhatsApp."""

from __future__ import annotations

from ..registry import Handler
from . import OWNER_MEMBER, ToolContext, _resolve


async def _send(args: dict, ctx: ToolContext) -> str:
    if ctx.send_file is None:
        return "error: pengiriman file tidak tersedia"
    path = _resolve(ctx, args.get("path", ""))
    if not path.is_file():
        return f"error: file tidak ditemukan: {args.get('path')}"
    caption = str(args.get("caption") or "").strip()[:1024]
    return await ctx.send_file(ctx.chat_id, path, caption)


TOOL = Handler(
    name="send",
    family="write",
    description=(
        "Kirim file dari workspace ke chat ini. Stiker WebP (≤512 px, transparan/animasi) "
        "dikirim sebagai stiker, foto JPEG/PNG/WebP sebagai gambar, lainnya sebagai dokumen."
    ),
    fn=_send,
    aliases=("kirim", "file"),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path file di dalam workspace."},
            "caption": {
                "type": "string",
                "description": "Keterangan singkat (opsional); pada stiker dikirim sebagai pesan teks terpisah.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    defaults=OWNER_MEMBER,
)
