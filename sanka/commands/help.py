"""Command /help: daftar command dan tool hasil baca metadata registry."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


def _aliases(handler: Handler) -> str:
    return f" ({', '.join('/' + alias for alias in handler.aliases)})" if handler.aliases else ""


async def help_command(ctx: CommandContext) -> str:
    lines = [f"Role: {ctx.role.name}"]
    for handler in ctx.commands:
        usage = f" {handler.usage}" if handler.usage else ""
        lines.append(f"/{handler.name}{usage}{_aliases(handler)} - {handler.description}")
    lines.append(f"Tool: {', '.join(handler.name for handler in ctx.tools) or '(tidak ada)'}")
    return "\n".join(lines)


COMMAND = Handler(
    name="help",
    description="Daftar command dan tool untuk role ini.",
    fn=help_command,
    aliases=("h", "bantuan"),
    defaults={"owner": True, "member": True},
)
