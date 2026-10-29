"""Registry command: satu modul per command dengan `COMMAND = Handler(...)`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..registry import Handler, Registry, discover
from ..roles import Role


@dataclass(frozen=True)
class CommandContext:
    agent: Any
    gateway: Any
    roles: tuple[Role, ...]
    chat_id: str
    args: str
    commands: tuple[Handler, ...]
    tools: tuple[Handler, ...]
    sender: str = ""
    inbound: Any = None

    @property
    def role(self) -> Role | None:
        return self.roles[0] if self.roles else None

    @property
    def lang(self) -> str:
        return str(self.agent.soul.language or "id")

    def role_names(self) -> str:
        return ", ".join(role.name for role in self.roles) or "-"


REGISTRY = Registry(
    discover(__name__, Path(__file__).parent, "COMMAND", "command"),
    "command",
)
