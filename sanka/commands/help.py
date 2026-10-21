"""Command /help: kartu command atau detail satu command, gaya WhatsApp."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


def _aliases(handler: Handler) -> str:
    return f" ({', '.join(handler.aliases)})" if handler.aliases else ""


def _detail(handler: Handler) -> str:
    usage = f"/{handler.name} {handler.usage}".strip()
    lines = [
        f"🧭 */{handler.name}*",
        f"• Fungsi: {handler.description}",
        f"• Pakai: {usage}",
    ]
    if handler.aliases:
        lines.append(f"• Alias: {', '.join('/' + alias for alias in handler.aliases)}")
    return "\n".join(lines)


async def help_command(ctx: CommandContext) -> str:
    name = ctx.args.strip().lstrip("/").split(" ", 1)[0].lower()
    if name:
        from . import REGISTRY  # lazy: package REGISTRY belum ada saat discovery

        handler = REGISTRY.get(name)
        if handler is None:
            return f"🤔 Command /{name} tidak dikenal. Ketik /help untuk daftar command."
        if all(item.name != handler.name for item in ctx.commands):
            return f"🔒 Command /{name} tidak tersedia untuk role {ctx.role.name}."
        return _detail(handler)

    lines = ["🧭 *Command Sanka*"]
    for handler in ctx.commands:
        usage = f" {handler.usage}" if handler.usage else ""
        lines.append(f"/{handler.name}{usage}{_aliases(handler)} - {handler.description}")
    lines.append("Ketik /help <command> untuk detail, atau ngobrol biasa tanpa slash.")
    return "\n".join(lines)


COMMAND = Handler(
    name="help",
    description="🧭 Daftar command sesuai peranmu.",
    fn=help_command,
    aliases=("h", "bantuan"),
    usage="[command]",
    defaults={"owner": True, "member": True},
)
