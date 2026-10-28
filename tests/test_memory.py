"""Kontrak memori: scope, dedupe, injeksi terbatas, dan izin tool."""

from __future__ import annotations

import asyncio
import re
import sqlite3
from pathlib import Path

import pytest

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
    assert store.count(["global"]) == 1
    assert store.count(["chat-a", "global"]) == 2
    assert store.search(["chat-a", "global"], "rapat")[0]["id"] == chat
    assert store.forget(["global"], item_id=first) == 1
    assert store.count(["global"]) == 0
    assert "suka kopi" not in store.context("chat-a")
    store.close()


def test_search_matches_any_term_not_all(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    store.add("chat", "fact", "rekap format tabel ringkas")
    store.add("chat", "fact", "jadwal ronda malam jumat")

    texts = [row["text"] for row in store.search(["chat"], "rekap ronda jumat")]

    assert "rekap format tabel ringkas" in texts
    assert "jadwal ronda malam jumat" in texts
    store.close()


def test_action_ledger_is_capped_and_rendered_newest_first(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    assert store.actions_context() == ""

    for index in range(7):
        store.log_action(
            "src@g.us",
            f"{index}@g.us",
            "teks",
            f"pesan {index}",
            source_name="Grup Sumber",
            target_name=f"Tujuan {index}",
            request=f"minta {index}",
        )

    context = store.actions_context()
    assert context.startswith("Aksi lintas chat terakhir")
    assert "Tujuan 6" in context and "Tujuan 1" not in context
    assert context.index("Tujuan 6") < context.index("Tujuan 5")
    assert 'permintaan: "minta 6"' in context
    assert len(store.recent_actions()) == 5
    newest = store.recent_actions(1)[0]
    assert (newest["source"], newest["target"], newest["kind"]) == ("src@g.us", "6@g.us", "teks")

    store.log_action("src@g.us", "dst@g.us", "teks", "token=rahasia", request="token=rahasia")
    sensitive = store.recent_actions(1)[0]
    assert sensitive["request"] == ""
    assert sensitive["summary"] == "[isi sensitif tidak dicatat]"
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

    first = asyncio.run(call(member, {"action": "add", "text": "lama faktual"}))
    first_id = int(re.search(r"#(\d+)", first).group(1))
    replaced = asyncio.run(
        call(member, {"action": "add", "text": "baru faktual", "supersedes": first_id})
    )
    assert "digantikan" in replaced
    listed = asyncio.run(call(member, {"action": "list"}))
    assert "baru faktual" in listed and "lama faktual" not in listed
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
        '{"scope":"chat","kind":"fact","text":"rapat jam 4","supersedes":"7"},'
        '{"scope":"x","kind":"fact","text":"buang"},{"scope":"chat","kind":"note","text":"buang"}]\n```'
    )
    assert parsed == [
        {"scope": "global", "kind": "fact", "text": "jadwal rapat"},
        {"scope": "chat", "kind": "preference", "text": "suka kopi"},
        {"scope": "chat", "kind": "fact", "text": "rapat jam 4", "supersedes": 7},
    ]
    assert parse_memory_items(
        '[{"scope":"chat","kind":"fact","text":"kopi","supersedes":true}]'
    ) == [{"scope": "chat", "kind": "fact", "text": "kopi"}]


def test_supersede_hides_old_entry_but_keeps_row(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = MemoryStore(path)
    old, _ = store.add("chat", "fact", "rapat jam 3")
    new, created = store.add("chat", "fact", "rapat jam 4", supersedes=old)

    assert created is True
    assert "rapat jam 4" in store.context("chat")
    assert "rapat jam 3" not in store.context("chat")
    assert [row["text"] for row in store.search(["chat"], "jam 3")] == ["rapat jam 4"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT superseded_by FROM memory WHERE id=?", (old,)).fetchone()[0] == new

    with pytest.raises(ValueError):  # sudah digantikan
        store.add("chat", "fact", "rapat jam 5", supersedes=old)
    with pytest.raises(ValueError):  # beda scope
        store.add("lain", "fact", "rapat jam 5", supersedes=new)
    with pytest.raises(ValueError):  # beda kind
        store.add("chat", "preference", "rapat jam 5", supersedes=new)
    with pytest.raises(ValueError):  # menggantikan dirinya sendiri
        store.add("chat", "fact", "rapat jam 4", supersedes=new)
    store.close()


def test_readding_superseded_text_revives_it(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    old, _ = store.add("chat", "fact", "rapat jam 3")
    new, _ = store.add("chat", "fact", "rapat jam 4", supersedes=old)
    assert [row["text"] for row in store.search(["chat"], "jam 3")] == ["rapat jam 4"]

    revived, created = store.add("chat", "fact", "rapat jam 3")
    assert created is False and revived == old
    assert "rapat jam 3" in store.context("chat")

    final, _ = store.add("chat", "fact", "rapat jam 3", supersedes=new)
    assert final == old
    assert [row["text"] for row in store.search(["chat"], "jam 4")] == ["rapat jam 3"]
    assert "rapat jam 3" in store.context("chat")
    store.close()


def test_existing_memory_db_gets_supersede_column(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE memory (id INTEGER PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL, "
            "text TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, "
            "updated_at REAL NOT NULL, UNIQUE(scope, kind, text));"
        )
        conn.execute(
            "INSERT INTO memory(scope, kind, text, created_at, updated_at) "
            "VALUES ('global', 'fact', 'fakta lama', 1, 1)"
        )
    store = MemoryStore(path)
    assert store.search(["global"], "lama")[0]["text"] == "fakta lama"
    new, _ = store.add("global", "fact", "fakta baru", supersedes=1)
    assert store.search(["global"], "lama") == []
    assert store.search(["global"], "baru")[0]["id"] == new
    store.close()


def test_reflect_stores_durable_memory_and_skips_secret(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    old, _ = store.add("global", "fact", "jadwal rapat lama")
    seen: list[tuple[str, str]] = []

    class ReflectLLM:
        async def reflect(
            self, user_text: str, answer: str, context: str = "", memory: str = ""
        ) -> list[dict]:
            seen.append((context, memory))
            return [
                {"scope": "global", "kind": "fact", "text": "jadwal rapat panitia", "supersedes": old},
                {"scope": "chat", "kind": "preference", "text": "suka kopi"},
                {"scope": "chat", "kind": "fact", "text": "rapat pukul 4", "supersedes": 9999},
                {"scope": "global", "kind": "fact", "text": "api key rahasia"},
            ]

    app = App(make_config(tmp_path), ReflectLLM(), None, Sessions(), memory=store)
    asyncio.run(
        app.reflect(
            "chat",
            "jadwal rapat panitia besok",
            "siap",
            context="Budi: rapat jam 3",
            memory="Global:\n- #1 [fact] 2026-10-20: jadwal rapat lama",
        )
    )
    asyncio.run(app.reflect("chat", "ok", "siap"))  # terlalu pendek: tidak memanggil LLM

    assert len(seen) == 1 and seen[0][0] == "Budi: rapat jam 3" and "#1" in seen[0][1]
    assert store.search(["global"], "panitia")[0]["text"] == "jadwal rapat panitia"
    assert store.search(["global"], "lama") == []
    assert store.search(["chat"], "kopi")[0]["text"] == "suka kopi"
    assert store.search(["chat"], "pukul")[0]["text"] == "rapat pukul 4"  # supersede invalid jatuh ke add biasa
    assert store.search(["global"], "rahasia") == []
    store.close()


def test_context_prefers_query_match_and_shows_date(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    store.add("chat-a", "fact", "jadwal rapat jam 3")
    store.add("chat-a", "preference", "suka kopi tanpa gula")

    block = store.context("chat-a", query="kopi")

    assert block.index("kopi") < block.index("rapat")
    assert re.search(r"\d{4}-\d{2}-\d{2}: suka kopi", block)
    store.close()


def test_mood_congruent_recall_orders_matching_memories(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    store.add("chat-a", "note", "kopi bikin sedih", valence=-0.8, arousal=0.3)
    store.add("chat-a", "note", "kopi bikin senang", valence=0.9, arousal=0.7)

    neg = store.context("chat-a", query="kopi", mood=(0.05, 0.3))
    pos = store.context("chat-a", query="kopi", mood=(0.95, 0.7))

    assert neg.index("kopi bikin sedih") < neg.index("kopi bikin senang")
    assert pos.index("kopi bikin senang") < pos.index("kopi bikin sedih")
    store.close()


def test_mood_is_optional_and_does_not_break_recency(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    store.add("chat-a", "note", "kopi pertama", valence=-0.8, arousal=0.3)
    store.add("chat-a", "note", "kopi kedua", valence=0.9, arousal=0.7)

    plain = store.context("chat-a", query="kopi")

    assert "kopi pertama" in plain and "kopi kedua" in plain
    assert plain.index("kopi kedua") < plain.index("kopi pertama")  # terbaru dulu
    store.close()


def test_entries_without_mood_still_ranked(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    store.add("chat-a", "note", "kopi tanpa mood")
    assert "kopi tanpa mood" in store.context("chat-a", query="kopi", mood=(0.2, 0.8))
    store.close()


def test_tool_add_tags_memory_with_current_pad(tmp_path: Path) -> None:
    from sanka.affect import AffectStore
    from sanka.config import AffectConfig

    config = make_config(tmp_path)
    store = MemoryStore(tmp_path / "memory.db")
    affect = AffectStore(tmp_path / "affect.db", AffectConfig(enabled=True))
    affect.turn_start("chat", "MSG-TOOL")
    pad = affect.pad("chat")
    ctx = ToolContext(
        config,
        RoleConfig("owner"),
        ("memory",),
        chat_id="chat",
        memory=store,
        affect=affect,
    )

    async def call() -> str:
        return await execute(TOOLS.by_name, "memory", {"action": "add", "text": "kopi pahit"}, ctx)

    assert "disimpan" in asyncio.run(call())
    row = store.entries(["chat"])[0]
    assert row["valence"] == pytest.approx(pad["pleasure"])
    assert row["arousal"] == pytest.approx(pad["arousal"])
    affect.close()
    store.close()
