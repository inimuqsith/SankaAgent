"""Command /help: kartu command ala WhatsApp dari metadata registry."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


def _aliases(handler: Handler) -> str:
    return f" ({', '.join(handler.aliases)})" if handler.aliases else ""


async def help_command(ctx: CommandContext) -> str:
    lines = ["🧭 *Command Sanka*"]
    for handler in ctx.commands:
        usage = f" {handler.usage}" if handler.usage else ""
        lines.append(f"/{handler.name}{usage}{_aliases(handler)} - {handler.description}")
    lines.append("Ketik command di atas, atau ngobrol biasa tanpa slash.")
    return "\n".join(lines)


COMMAND = Handler(
    name="help",
    description="🧭 Daftar command sesuai peranmu.",
    fn=help_command,
    aliases=("h", "bantuan"),
    defaults={"owner": True, "member": True},
)
