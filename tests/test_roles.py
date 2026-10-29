"""Role contract: one roles.yaml mapping, multi-role union, closed defaults, memory_admin."""

from __future__ import annotations

import pytest

from sanka.engine.commands import REGISTRY as COMMANDS
from sanka.engine.permissions import command_patterns, tool_patterns, tools_for
from sanka.engine.roles import Role, RoleError, load_roles
from sanka.engine.tools import REGISTRY as TOOLS


def test_load_role_mapping_and_union() -> None:
    source = {
        "owner": 'name: owner\npriority: 100\nusers: ["628111"]\ntools: ["*"]\n'
        'commands: ["*"]\n',
        "panitia": 'name: panitia\npriority: 10\nusers: ["628111"]\ntools: ["read"]\n'
        'commands: ["help"]\ncontext: "Panitia."\n',
        "guest": "name: guest\n",
    }

    registry = load_roles(source, "guest")

    assert registry.names() == ["guest", "owner", "panitia"]
    roles = registry.for_address("628111@s.whatsapp.net")
    assert [role.name for role in roles] == ["owner", "panitia"]
    assert tool_patterns(roles) == ("*", "read")
    assert command_patterns(roles) == ("*", "help")
    assert registry.for_address("628999")[0].name == "guest"


def test_name_must_match_key() -> None:
    with pytest.raises(RoleError, match="does not match"):
        load_roles({"member": "name: other\ntools: []\n"}, "member")


def test_bad_list_field_rejected() -> None:
    with pytest.raises(RoleError, match="tools"):
        load_roles({"member": 'name: member\ntools: "read"\n'}, "member")


def test_unknown_default_role_rejected() -> None:
    with pytest.raises(RoleError, match="default_role"):
        load_roles({"member": "name: member\n"}, "owner")


def test_role_documents_mapping() -> None:
    docs = {
        "member": 'name: member\ntools: ["read"]\ncommands: ["help"]\n',
        "guest": "name: guest\n",
    }
    registry = load_roles(docs, "guest")
    assert registry.get("member").tools == ("read",)
    assert registry.override(("member",))[0].name == "member"
    with pytest.raises(RoleError):
        registry.override(("hilang",))


def test_memory_admin_parses_true_and_defaults_false() -> None:
    registry = load_roles(
        {
            "owner": "name: owner\nmemory_admin: true\n",
            "guest": "name: guest\n",
        },
        "guest",
    )
    assert registry.get("owner").memory_admin is True
    assert registry.get("guest").memory_admin is False


def test_role_without_defaults_denies_tools() -> None:
    empty = Role("member")
    assert tool_patterns((empty,)) == ()
    assert tools_for(TOOLS, (empty,)) == []
    assert len(TOOLS.by_name) > 0 and len(COMMANDS.by_name) > 0
