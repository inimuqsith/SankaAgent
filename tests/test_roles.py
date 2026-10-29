"""Kontrak role modular: file/dokumen, union multi-role, dan default tertutup."""

from __future__ import annotations

import pytest

from sanka.engine.commands import REGISTRY as COMMANDS
from sanka.engine.permissions import command_patterns, tool_patterns, tools_for
from sanka.engine.roles import Role, RoleError, load_roles
from sanka.engine.tools import REGISTRY as TOOLS


def write(folder, name: str, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.yaml").write_text(body, encoding="utf-8")


def test_load_role_files_and_union(tmp_path) -> None:
    write(
        tmp_path,
        "owner",
        'name: owner\npriority: 100\nusers: ["628111"]\ntools: ["*"]\ncommands: ["*"]\n',
    )
    write(
        tmp_path,
        "panitia",
        'name: panitia\npriority: 10\nusers: ["628111"]\ntools: ["read"]\n'
        'commands: ["help"]\ncontext: "Panitia."\n',
    )
    write(tmp_path, "guest", "name: guest\n")

    registry = load_roles(tmp_path, "guest")

    assert registry.names() == ["guest", "owner", "panitia"]
    roles = registry.for_address("628111@s.whatsapp.net")
    assert [role.name for role in roles] == ["owner", "panitia"]
    assert tool_patterns(roles) == ("*", "read")
    assert command_patterns(roles) == ("*", "help")
    assert registry.for_address("628999")[0].name == "guest"


def test_name_must_match_document(tmp_path) -> None:
    write(tmp_path, "member", "name: other\ntools: []\n")
    with pytest.raises(RoleError, match="tidak sama"):
        load_roles(tmp_path, "member")


def test_bad_list_field_rejected(tmp_path) -> None:
    write(tmp_path, "member", 'name: member\ntools: "read"\n')
    with pytest.raises(RoleError, match="tools"):
        load_roles(tmp_path, "member")


def test_unknown_default_role_rejected(tmp_path) -> None:
    write(tmp_path, "member", "name: member\n")
    with pytest.raises(RoleError, match="default_role"):
        load_roles(tmp_path, "owner")


def test_role_documents_mapping(tmp_path) -> None:
    docs = {
        "member": 'name: member\ntools: ["read"]\ncommands: ["help"]\n',
        "guest": "name: guest\n",
    }
    registry = load_roles(docs, "guest")
    assert registry.get("member").tools == ("read",)
    assert registry.override(("member",))[0].name == "member"
    with pytest.raises(RoleError):
        registry.override(("hilang",))


def test_role_without_defaults_denies_tools() -> None:
    empty = Role("member")
    assert tool_patterns((empty,)) == ()
    assert tools_for(TOOLS, (empty,)) == []
    assert len(TOOLS.by_name) > 0 and len(COMMANDS.by_name) > 0
