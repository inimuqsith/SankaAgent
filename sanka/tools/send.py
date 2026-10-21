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
    return await ctx.send_file(path, caption)


TOOL = Handler(
    name="send",
    family="write",
    description="Kirim file dari workspace ke chat ini sebagai dokumen WhatsApp.",
    fn=_send,
    aliases=("kirim", "file"),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path file di dalam workspace."},
            "caption": {"type": "string", "description": "Keterangan singkat (opsional)."},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    defaults=OWNER_MEMBER,
)
