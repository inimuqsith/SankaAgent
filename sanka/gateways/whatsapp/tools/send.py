"""Tool send: kirim file workspace ke chat aktif."""

from __future__ import annotations

from ....engine.registry import Handler
from ....engine.tools import ToolContext, _resolve


async def _send(args: dict, ctx: ToolContext) -> str:
    if ctx.agent is None or ctx.gateway is None:
        return "error: pengiriman tidak tersedia"
    raw = str(args.get("path") or "").strip()
    if not raw:
        return "error: path kosong"
    path = _resolve(ctx, raw)
    if not path.is_file():
        return f"error: file tidak ditemukan: {raw}"
    caption = str(args.get("caption") or "").strip()[:1024]
    result = await ctx.agent.send_file(ctx.chat_id, path, caption, gateway=ctx.gateway)
    if result.startswith("ok:"):
        ctx.sent.append(
            {"tool": "send", "target": ctx.chat_id, "kind": "file", "summary": path.name}
        )
        if ctx.ledger is not None:
            try:
                ctx.ledger.log_action(
                    ctx.chat_id,
                    ctx.chat_id,
                    "file",
                    f"{path.name}{f' — {caption}' if caption else ''}",
                    source_name="chat",
                    target_name=ctx.gateway.chat_name(ctx.chat_id),
                    request=ctx.request,
                )
            except Exception:
                pass
    return result


TOOL = Handler(
    name="send",
    family="write",
    description="Kirim file dari workspace ke chat aktif (stiker/foto/dokumen dipilih otomatis).",
    fn=_send,
    aliases=("kirim_file",),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path inside the workspace."},
            "caption": {"type": "string", "description": "Optional caption (ignored for stickers)."},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)
