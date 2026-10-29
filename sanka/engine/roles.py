"""Role modular: satu role = satu dokumen YAML; izin eksplisit, tanpa default handler."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class RoleError(ValueError):
    """Dokumen role melanggar kontrak."""


@dataclass(frozen=True)
class Role:
    name: str
    description: str = ""
    users: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    context: str = ""
    priority: int = 0


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
            raise RoleError("tidak ada role yang dimuat")
        return self.by_name[self.names()[0]]

    def for_address(self, address: str) -> tuple[Role, ...]:
        """Semua role yang memuat alamat ini, urut priority desc lalu nama."""
        from .permissions import normalize  # lokal: permissions mengimpor modul ini

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
        """Ganti hasil for_address dengan role bernama (dipakai gateway CLI)."""
        found: list[Role] = []
        for name in names:
            role = self.get(name)
            if role is None:
                raise RoleError(f"role '{name}' tidak ada")
            found.append(role)
        return tuple(found) or (self.default_role(),)


def _str_tuple(value: object, field_name: str, source: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RoleError(f"{source}: '{field_name}' harus list[str]")
    items: list[str] = []
    for item in value:
        if not isinstance(item, (str, int)) or not str(item).strip():
            raise RoleError(f"{source}: '{field_name}' harus list[str]")
        items.append(str(item))
    return tuple(items)


def parse_role(name: str, body: object, source: str) -> Role:
    if not isinstance(body, dict):
        raise RoleError(f"{source}: role harus mapping")
    declared = str(body.get("name") or "").strip()
    if not declared:
        raise RoleError(f"{source}: field 'name' wajib diisi")
    if declared != name:
        raise RoleError(f"{source}: name '{declared}' tidak sama dengan nama '{name}'")
    if not NAME.fullmatch(declared):
        raise RoleError(f"{source}: nama role tidak valid: '{declared}'")
    try:
        priority = int(body.get("priority") or 0)
    except (TypeError, ValueError) as exc:
        raise RoleError(f"{source}: 'priority' harus angka") from exc
    return Role(
        name=declared,
        description=str(body.get("description") or "").strip(),
        users=_str_tuple(body.get("users"), "users", source),
        tools=_str_tuple(body.get("tools"), "tools", source),
        commands=_str_tuple(body.get("commands"), "commands", source),
        context=str(body.get("context") or "").strip(),
        priority=priority,
    )


def load_roles(
    source: Path | Mapping[str, str],
    default_role: str,
) -> RoleRegistry:
    """Muat role dari folder `roles/*.yaml` atau mapping dokumen DB `role:<name>`."""
    by_name: dict[str, Role] = {}
    if isinstance(source, Mapping):
        items = [(str(name), str(text), f"dokumen role:{name}") for name, text in source.items()]
    else:
        folder = Path(source)
        items = []
        if folder.is_dir():
            for path in sorted(folder.glob("*.yaml")):
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError as exc:
                    raise RoleError(f"{path}: gagal dibaca: {exc}") from exc
                items.append((path.stem, text, str(path)))
    for name, text, label in items:
        try:
            body = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise RoleError(f"{label}: YAML tidak valid: {exc}") from exc
        role = parse_role(name, body, label)
        by_name[role.name] = role
    default = str(default_role or "").strip()
    if default not in by_name:
        raise RoleError(f"default_role '{default}' tidak ada di role yang dimuat")
    return RoleRegistry(by_name=by_name, default=default)
