"""Tool read: baca file teks di workspace dengan nomor baris."""

from __future__ import annotations

from ..registry import Handler
from . import OWNER_MEMBER, ToolContext, _int, _resolve


async def _read(args: dict, ctx: ToolContext) -> str:
    path = _resolve(ctx, args.get("path", ""))
    if not path.is_file():
        return f"error: file tidak ditemukan: {args.get('path')}"
    start = max(1, _int(args, "start", 1))
    limit = max(1, _int(args, "limit", 400))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return "(file kosong)"
    chosen = lines[start - 1 : start - 1 + limit]
    if not chosen:
        return f"(tidak ada baris di rentang {start}-{start + limit - 1}; file {len(lines)} baris)"
    return "\n".join(f"{start + i}: {line}" for i, line in enumerate(chosen))


TOOL = Handler(
    name="read",
    family="read",
    description="Baca file teks di workspace dengan nomor baris.",
    fn=_read,
    aliases=("cat",),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path file di dalam workspace (relatif atau absolut)."},
            "start": {"type": "integer", "description": "Baris awal (default 1)."},
            "limit": {"type": "integer", "description": "Jumlah baris (default 400)."},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    defaults=OWNER_MEMBER,
)
