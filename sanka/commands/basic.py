"""Basic commands available in plan-1.

Service-backed and session-backed commands are intentionally absent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanka.commands.registry import (
    Command,
    CommandContext,
    CommandRegistry,
)
from sanka.commands.session import SessionControl, register_session_commands
from sanka.policy import ALL_ROLES, COMMITTEE_ROLES

if TYPE_CHECKING:
    from sanka.tools.recap import RecapService


def build_registry(
    control: SessionControl,
    recap_service: RecapService | None = None,
) -> CommandRegistry:
    """Build the deterministic command registry.

    ``/rekap`` is registered only when a recap service is supplied, so a
    deployment without Sheets credentials does not expose a broken command.
    """

    registry = CommandRegistry()

    def help_handler(context: CommandContext, arguments: str) -> str:
        return _render_help(registry.list_for(context.role))

    registry.register(
        Command("ping", "🏓 Cek Sanka hidup", ALL_ROLES, _ping)
    )
    registry.register(
        Command("help", "🧭 Daftar command sesuai peranmu", ALL_ROLES, help_handler)
    )
    registry.register(
        Command("whoami", "👤 Identitas dan peranmu", ALL_ROLES, _whoami)
    )
    registry.register(
        Command("status", "⚙️ Status ringkas Sanka", COMMITTEE_ROLES, _status)
    )
    register_session_commands(registry, control)
    if recap_service is not None:
        from sanka.commands.recap import register_recap_command

        register_recap_command(registry, recap_service)
    return registry


def _ping(context: CommandContext, arguments: str) -> str:
    return "🏓 Pong! Sanka hidup dan siap."


def _whoami(context: CommandContext, arguments: str) -> str:
    lines = [
        f"👤 Kamu: {context.sender_id}",
        f"🎭 Peran: {context.role.value.capitalize()}",
    ]
    if context.chat_id:
        chat_kind = "grup" if context.is_group else "pribadi"
        lines.append(f"💬 Chat: {chat_kind} ({context.chat_id})")
    return "\n".join(lines)


def _status(context: CommandContext, arguments: str) -> str:
    return (
        "⚙️ Mode: deterministic (command tanpa LLM)\n"
        f"🧠 Model: {context.config.llm.model}"
    )


def _render_help(commands: tuple[Command, ...]) -> str:
    lines = ["🧭 *Command Sanka*"]
    lines.extend(f"/{command.name} - {command.description}" for command in commands)
    lines.append("Ketik command di atas, atau ngobrol biasa tanpa slash.")
    return "\n".join(lines)
