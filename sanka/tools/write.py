"""Tool write: tulis file teks di workspace."""

from __future__ import annotations

from ..registry import Handler
from . import OWNER_ONLY, ToolContext, _resolve


async def _write(args: dict, ctx: ToolContext) -> str:
    path = _resolve(ctx, args.get("path", ""))
    content = str(args.get("content", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"ok: {len(content)} karakter ditulis ke {path}"


TOOL = Handler(
    name="write",
    family="write",
    description="Tulis file teks di workspace (menimpa bila ada).",
    fn=_write,
    aliases=("save",),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path file (relatif ke workspace atau absolut)."},
            "content": {"type": "string", "description": "Isi file penuh."},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
    defaults=OWNER_ONLY,
)
