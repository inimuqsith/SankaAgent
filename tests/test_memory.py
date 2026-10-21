"""Kontrak memori: scope, dedupe, injeksi terbatas, dan izin tool."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sanka.commands import CommandContext
from sanka.commands.memori import COMMAND as MEMORI
from sanka.config import Config, LLMConfig, RoleConfig, SandboxConfig, WhatsAppConfig
from sanka.llm import parse_memory_items
from sanka.memory import MemoryStore
from sanka.tools import REGISTRY as TOOLS
from sanka.tools import ToolContext, execute
from sanka.wa import App, Sessions


def make_config(root: Path) -> Config:
    return Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=root / "workspace",
        whatsapp=WhatsAppConfig(session_db=root / "wa.db"),
        roles={"owner": RoleConfig("owner"), "member": RoleConfig("member")},
        default_role="member",
        sandbox=SandboxConfig(),
        mcp={},
    )


def test_scopes_dedupe_and_context(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    first, created = store.add("global", "preference", "suka kopi", "test")
    again, created_again = store.add("global", "preference", "suka kopi", "test")
    chat, _ = store.add("chat-a", "fact", "rapat jam 3")

    assert created is True and created_again is False
    assert first == again
    assert store.context("chat-a").count("suka kopi") == 1
    assert "rapat jam 3" in store.context("chat-a")
    assert "suka kopi" not in store.context("chat-a", include_global=False)
    assert store.search(["chat-a", "global"], "rapat")[0]["id"] == chat
    assert store.forget(["global"], item_id=first) == 1
    assert "suka kopi" not in store.context("chat-a")
    store.close()


def test_memory_tools_are_scope_separated(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    config = make_config(tmp_path)
    owner = ToolContext(config, RoleConfig("owner"), ("memory",), chat_id="chat", memory=store)
    member = ToolContext(config, RoleConfig("member"), ("memory",), chat_id="chat", memory=store)

    async def call(ctx: ToolContext, args: dict) -> str:
        return await execute(TOOLS.by_name, "memory", args, ctx)

    assert "disimpan" in asyncio.run(call(member, {"action": "add", "text": "fakta chat"}))
    assert "disimpan" in asyncio.run(
        call(member, {"action": "add", "scope": "global", "text": "fakta global"})
    )
    member_rows = asyncio.run(call(member, {"action": "list"}))
    assert "fakta chat" in member_rows
    assert "fakta global" not in member_rows
    assert "fakta global" in asyncio.run(call(member, {"action": "list", "scope": "global"}))
    assert "1 memori dihapus" in asyncio.run(
        call(owner, {"action": "forget", "query": "fakta chat"})
    )
    assert "1 memori dihapus" in asyncio.run(
        call(owner, {"action": "forget", "scope": "global", "query": "fakta global"})
    )
    assert "fakta chat" not in asyncio.run(call(member, {"action": "list"}))
    store.close()


def test_memori_command_add_search_forget(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")

    class FakeApp:
        memory = store

    def run(role: str, args: str) -> str:
        return asyncio.run(MEMORI.fn(CommandContext(FakeApp(), "chat", RoleConfig(role), args, (), ())))

    assert "disimpan" in run("member", "tambah suka teh")
    assert "suka teh" in run("member", "cari teh")
    assert "1 memori dihapus" in run("member", "hapus 1")
    assert "disimpan" in run("member", "global tambah fakta global")
    assert "fakta global" in run("member", "global cari global")
    assert "1 memori dihapus" in run("member", "global hapus fakta global")
    store.close()


def test_parse_memory_items_filters_invalid() -> None:
    parsed = parse_memory_items(
        '```json\n[{"scope":"global","kind":"fact","text":" jadwal rapat "},'
        '{"scope":"chat","kind":"preference","text":"suka kopi"},'
        '{"scope":"x","kind":"fact","text":"buang"},{"scope":"chat","kind":"note","text":"buang"}]\n```'
    )
    assert parsed == [
        {"scope": "global", "kind": "fact", "text": "jadwal rapat"},
        {"scope": "chat", "kind": "preference", "text": "suka kopi"},
    ]


def test_reflect_stores_durable_memory_and_skips_secret(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")

    class ReflectLLM:
        async def reflect(self, user_text: str, answer: str) -> list[dict]:
            return [
                {"scope": "global", "kind": "fact", "text": "jadwal rapat panitia"},
                {"scope": "chat", "kind": "preference", "text": "suka kopi"},
                {"scope": "global", "kind": "fact", "text": "api key rahasia"},
            ]

    app = App(make_config(tmp_path), ReflectLLM(), None, Sessions(), memory=store)
    asyncio.run(app.reflect("chat", "jadwal rapat panitia besok", "siap"))

    assert store.search(["global"], "jadwal")[0]["text"] == "jadwal rapat panitia"
    assert store.search(["chat"], "kopi")[0]["text"] == "suka kopi"
    assert store.search(["global"], "rahasia") == []
    store.close()
