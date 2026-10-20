"""Basic commands available in plan-1.

Service-backed and session-backed commands are intentionally absent.
"""

from __future__ import annotations

from sanka.commands.registry import (
    Command,
    CommandContext,
    CommandRegistry,
)
from sanka.policy import ALL_ROLES, COMMITTEE_ROLES


def build_registry() -> CommandRegistry:
    """Build the deterministic command registry for plan-1."""

    registry = CommandRegistry()

    def help_handler(context: CommandContext, arguments: str) -> str:
        return _render_help(registry.list_for(context.role))

    registry.register(
        Command("ping", "Cek Sanka hidup", ALL_ROLES, _ping)
    )
    registry.register(
        Command("help", "Daftar command sesuai peranmu", ALL_ROLES, help_handler)
    )
    registry.register(
        Command("whoami", "Tampilkan peranmu", ALL_ROLES, _whoami)
    )
    registry.register(
        Command("status", "Status ringkas Sanka", COMMITTEE_ROLES, _status)
    )
    return registry


def _ping(context: CommandContext, arguments: str) -> str:
    return "pong"


def _whoami(context: CommandContext, arguments: str) -> str:
    return f"Peran kamu: {context.role.value}."


def _status(context: CommandContext, arguments: str) -> str:
    return f"Mode: deterministic\nModel: {context.config.llm.model}"


def _render_help(commands: tuple[Command, ...]) -> str:
    lines = ["Command tersedia:"]
    lines.extend(f"/{command.name} - {command.description}" for command in commands)
    return "\n".join(lines)
