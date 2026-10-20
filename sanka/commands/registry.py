"""Command registry: deterministic handlers, permission gated by role."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sanka.policy import Role

if TYPE_CHECKING:
    from sanka.config import SankaConfig

UNKNOWN_COMMAND_REPLY = "Command tidak dikenal. Ketik /help untuk daftar command."
FORBIDDEN_COMMAND_REPLY = "Command ini tidak tersedia untuk peranmu."

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Everything a handler may need about the caller and config."""

    role: Role
    config: SankaConfig
    sender_id: str
    chat_id: str = ""
    is_group: bool = False


Handler = Callable[[CommandContext, str], str]


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    description: str
    roles: frozenset[Role]
    handler: Handler

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.fullmatch(self.name):
            raise ValueError(f"invalid command name: {self.name!r}")
        if not self.roles:
            raise ValueError(f"command {self.name!r} must allow at least one role")


class CommandRegistry:
    """Lookup and dispatch table for slash commands."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        if command.name in self._commands:
            raise ValueError(f"duplicate command: {command.name}")
        self._commands[command.name] = command

    def get(self, name: str) -> Command | None:
        return self._commands.get(name.lstrip("/").lower())

    def list_for(self, role: Role) -> tuple[Command, ...]:
        """Commands available to ``role`` in registration order."""

        return tuple(
            command for command in self._commands.values() if role in command.roles
        )

    def dispatch(self, text: str, context: CommandContext) -> str:
        """Run the command in ``text`` and return the reply text.

        Unknown commands and forbidden commands return a short deterministic
        reply; handlers are never called for them.
        """

        raw = text.strip()
        if not raw.startswith("/"):
            return UNKNOWN_COMMAND_REPLY
        body = raw[1:].strip()
        if not body:
            return UNKNOWN_COMMAND_REPLY

        parts = body.split(maxsplit=1)
        # WhatsApp group syntax: /help@<bot-id>.
        name = parts[0].partition("@")[0].lower()
        arguments = parts[1].strip() if len(parts) > 1 else ""
        command = self._commands.get(name)
        if command is None:
            return UNKNOWN_COMMAND_REPLY
        if context.role not in command.roles:
            return FORBIDDEN_COMMAND_REPLY
        return command.handler(context, arguments)
