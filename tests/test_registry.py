"""Kontrak discovery: handler bawaan, drop-in, agregasi error, dan izin eksplisit kosong."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sanka.commands import REGISTRY as COMMANDS
from sanka.config import RoleConfig
from sanka.permissions import commands_for, tool_patterns, tools_for
from sanka.registry import RegistryError, discover
from sanka.tools import FAMILIES
from sanka.tools import REGISTRY as TOOLS


def package(root: Path, name: str, files: dict[str, str]) -> Path:
    folder = root / name
    folder.mkdir()
    (folder / "__init__.py").write_text("", encoding="utf-8")
    for filename, body in files.items():
        (folder / filename).write_text(body, encoding="utf-8")
    return folder


def clear_modules(prefix: str) -> None:
    for name in [item for item in sys.modules if item == prefix or item.startswith(prefix + ".")]:
        del sys.modules[name]


def test_builtin_handlers_discovered() -> None:
    assert set(COMMANDS.names()) == {
        "backfill",
        "calmdown",
        "cari",
        "help",
        "kirim",
        "memori",
        "mood",
        "ping",
        "reset",
        "skill",
        "status",
        "tools",
        "whoami",
    }
    assert set(TOOLS.names()) == {
        "chat_history",
        "mcp",
        "memory",
        "read",
        "sandbox",
        "send",
        "send_to",
        "shell",
        "skill",
        "web_search",
        "write",
    }
    assert {handler.family for handler in TOOLS.by_name.values()} == FAMILIES


def test_drop_in_tool_discovered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = package(
        tmp_path,
        "plug_valid",
        {
            "echo.py": (
                "from sanka.registry import Handler\n"
                "\n"
                "async def fn(args, ctx):\n"
                '    return "ok"\n'
                "\n"
                'TOOL = Handler(name="echo", family="read", description="Uji.", fn=fn,\n'
                '               parameters={"type": "object", "properties": {}}, defaults={})\n'
            )
        },
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        found = discover("plug_valid", folder, "TOOL", "tool", FAMILIES)
    finally:
        clear_modules("plug_valid")
    assert [handler.name for handler in found] == ["echo"]


def test_invalid_modules_aggregate_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = package(
        tmp_path,
        "plug_invalid",
        {
            "bad_name.py": (
                "from sanka.registry import Handler\n"
                "\n"
                "async def fn(args, ctx):\n"
                '    return ""\n'
                "\n"
                'TOOL = Handler(name="Bad-Name", description="", fn=fn, family="nope", parameters={})\n'
            ),
            "no_handler.py": "VALUE = 1\n",
        },
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        with pytest.raises(RegistryError) as caught:
            discover("plug_invalid", folder, "TOOL", "tool", FAMILIES)
    finally:
        clear_modules("plug_invalid")
    message = str(caught.value)
    for fragment in ("bad_name.py", "Bad-Name", "deskripsi", "family", "no_handler.py", "tidak mendefinisikan TOOL"):
        assert fragment in message


def test_explicit_empty_permissions_deny_all() -> None:
    denied = RoleConfig("member", tools=(), commands=())
    assert tool_patterns(denied) == ()
    assert tools_for(denied) == []
    assert commands_for(denied) == []

    default = RoleConfig("member")
    assert set(tool_patterns(default)) == {
        "chat_history",
        "memory",
        "read",
        "send",
        "skill",
        "web_search",
        "mcp__*",
    }
    assert {handler.name for handler in tools_for(default)} == {
        "chat_history",
        "memory",
        "read",
        "send",
        "skill",
        "web_search",
    }
    assert set(COMMANDS.patterns("member", default.commands)) == {
        "cari",
        "help",
        "kirim",
        "memori",
        "mood",
        "ping",
        "reset",
        "skill",
        "status",
        "tools",
        "whoami",
    }
