"""Permission ala Discord: role dari nomor, pola glob, default dari registry handler."""

from __future__ import annotations

from .commands import REGISTRY as COMMANDS
from .config import Config, RoleConfig
from .registry import Handler, allows
from .tools import REGISTRY as TOOLS

__all__ = [
    "allows",
    "command_allowed",
    "commands_for",
    "normalize",
    "role_for",
    "tool_allowed",
    "tool_patterns",
    "tools_for",
]


def normalize(value: str) -> str:
    """Ambil digit saja; JID tanpa digit dipakai apa adanya (lowercase)."""
    head = value.split("@")[0].split(":")[0]
    digits = "".join(ch for ch in head if ch.isdigit())
    return digits or value.lower()


def role_for(sender_jid: str, config: Config) -> RoleConfig:
    who = normalize(sender_jid)
    for role in config.roles.values():
        if any(normalize(user) == who for user in role.users):
            return role
    return config.roles[config.default_role]


def command_patterns(role: RoleConfig) -> tuple[str, ...]:
    return COMMANDS.patterns(role.name, role.commands)


def tool_patterns(role: RoleConfig) -> tuple[str, ...]:
    return TOOLS.patterns(role.name, role.tools)


def command_allowed(role: RoleConfig, name: str) -> bool:
    return allows(command_patterns(role), name)


def tool_allowed(role: RoleConfig, name: str) -> bool:
    return allows(tool_patterns(role), name)


def commands_for(role: RoleConfig) -> list[Handler]:
    return COMMANDS.entries(command_patterns(role))


def tools_for(role: RoleConfig) -> list[Handler]:
    return TOOLS.entries(tool_patterns(role))
