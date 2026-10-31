"""Tool write: tulis file teks di workspace."""

from __future__ import annotations

from ..registry import Handler
from . import ToolContext, _resolve_write


async def _write(args: dict, ctx: ToolContext) -> str:
    path = _resolve_write(ctx, args.get("path", ""))
    content = str(args.get("content", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"ok: {len(content)} karakter ditulis ke {path}"


TOOL = Handler(
    name="write",
    family="write",
    description=(
        "Write text under scratch/; public/ is read-only; trusted roles may write other "
        "workspace paths."
    ),
    fn=_write,
    aliases=("save",),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path inside the workspace (relative or absolute)."},
            "content": {"type": "string", "description": "Full file content."},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
)
