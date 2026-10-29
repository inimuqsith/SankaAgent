"""Modular roles: one role per YAML block; explicit permissions, no handler defaults."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

import yaml

NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class RoleError(ValueError):
    """A role document breaks the contract."""


@dataclass(frozen=True)
class Role:
    name: str
    description: str = ""
    users: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    context: str = ""
    priority: int = 0
    memory_admin: bool = False


@dataclass(frozen=True)
class RoleRegistry:
    by_name: dict[str, Role] = field(default_factory=dict)
    default: str = ""

    def names(self) -> list[str]:
        return sorted(self.by_name)

    def get(self, name: str) -> Role | None:
        return self.by_name.get(str(name or "").strip().lower())

    def default_role(self) -> Role:
        role = self.by_name.get(self.default)
        if role is not None:
            return role
        if not self.by_name:
            raise RoleError("no roles loaded")
        return self.by_name[self.names()[0]]

    def for_address(self, address: str) -> tuple[Role, ...]:
        """Every role that lists this address, ordered by priority desc then name."""
        from .permissions import normalize  # local: permissions imports this module

        who = normalize(str(address or ""))
        matched = [
            role
            for role in self.by_name.values()
            if who and any(normalize(user) == who for user in role.users)
        ]
        if not matched:
            return (self.default_role(),)
        return tuple(sorted(matched, key=lambda role: (-role.priority, role.name)))

    def override(self, names: tuple[str, ...]) -> tuple[Role, ...]:
        """Replace the for_address result with named roles (used by the CLI gateway)."""
        found: list[Role] = []
        for name in names:
            role = self.get(name)
            if role is None:
                raise RoleError(f"role '{name}' does not exist")
            found.append(role)
        return tuple(found) or (self.default_role(),)


def _str_tuple(value: object, field_name: str, source: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RoleError(f"{source}: '{field_name}' must be a list[str]")
    items: list[str] = []
    for item in value:
        if not isinstance(item, (str, int)) or not str(item).strip():
            raise RoleError(f"{source}: '{field_name}' must be a list[str]")
        items.append(str(item))
    return tuple(items)


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "yes", "1", "on"}


def parse_role(name: str, body: object, source: str) -> Role:
    if not isinstance(body, dict):
        raise RoleError(f"{source}: role must be a mapping")
    declared = str(body.get("name") or "").strip()
    if not declared:
        raise RoleError(f"{source}: field 'name' is required")
    if declared != name:
        raise RoleError(f"{source}: name '{declared}' does not match key '{name}'")
    if not NAME.fullmatch(declared):
        raise RoleError(f"{source}: invalid role name: '{declared}'")
    try:
        priority = int(body.get("priority") or 0)
    except (TypeError, ValueError) as exc:
        raise RoleError(f"{source}: 'priority' must be a number") from exc
    return Role(
        name=declared,
        description=str(body.get("description") or "").strip(),
        users=_str_tuple(body.get("users"), "users", source),
        tools=_str_tuple(body.get("tools"), "tools", source),
        commands=_str_tuple(body.get("commands"), "commands", source),
        context=str(body.get("context") or "").strip(),
        priority=priority,
        memory_admin=_bool(body.get("memory_admin")),
    )


def load_roles(
    source: Mapping[str, object],
    default_role: str,
) -> RoleRegistry:
    """Load roles from one `name -> body` mapping (a YAML string or an already-parsed dict)."""
    by_name: dict[str, Role] = {}
    for name, value in source.items():
        label = f"role:{name}"
        if isinstance(value, str):
            try:
                body = yaml.safe_load(value)
            except yaml.YAMLError as exc:
                raise RoleError(f"{label}: invalid YAML: {exc}") from exc
        else:
            body = value
        role = parse_role(str(name), body, label)
        by_name[role.name] = role
    default = str(default_role or "").strip()
    if default not in by_name:
        raise RoleError(f"default_role '{default}' is not among the loaded roles")
    return RoleRegistry(by_name=by_name, default=default)
