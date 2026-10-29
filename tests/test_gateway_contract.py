"""Kontrak gateway: tools/commands digabung, duplikat ditolak, dan isolasi core."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from sanka.engine.registry import Handler, RegistryError
from support import FakeGateway, make_agent


def handler(name: str) -> Handler:
    async def fn(args, ctx):
        return "ok"

    return Handler(name=name, description="test", fn=fn, family="read", parameters={"type": "object", "properties": {}})


def test_gateway_tools_and_commands_are_merged(tmp_path) -> None:
    gateway = FakeGateway(tools=[handler("extra_tool")])
    env = make_agent(tmp_path, gateway=gateway)
    assert env.agent.tools.get("extra_tool") is not None
    assert "extra_tool" in env.agent.tools.names()


def test_duplicate_handler_names_raise(tmp_path) -> None:
    first = FakeGateway(name="a", tools=[handler("clash")])
    second = FakeGateway(name="b", tools=[handler("clash")])
    with pytest.raises(RegistryError):
        make_agent(tmp_path, gateways=[first, second])


def test_core_imports_without_neonize() -> None:
    root = Path(__file__).resolve().parents[1]
    code = "import sys; sys.modules['neonize']=None; import sanka.engine; print('ok')"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
