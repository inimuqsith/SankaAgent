"""Command /tools: daftar tool yang diizinkan untuk role pengirim."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext

DESC_LIMIT = 110


def _clip(text: str) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= DESC_LIMIT else flat[: DESC_LIMIT - 1] + "…"


async def tools_command(ctx: CommandContext) -> str:
    if not ctx.tools:
        return "🧰 Tidak ada tool untuk role ini."
    lines = [f"🧰 *Tool role {ctx.role.name}*"]
    for handler in sorted(ctx.tools, key=lambda item: item.name):
        lines.append(f"• {handler.name} — {_clip(handler.description)}")
    return "\n".join(lines)


COMMAND = Handler(
    name="tools",
    description="🧰 Daftar tool yang diizinkan untuk role ini.",
    fn=tools_command,
    aliases=("tool",),
    defaults={"owner": True, "member": True},
)
