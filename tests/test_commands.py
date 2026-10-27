"""Kontrak command operasional: whoami, status, tools, cari, dan help detail."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from sanka.commands import REGISTRY as COMMANDS
from sanka.commands.cari import cari
from sanka.commands.help import help_command
from sanka.commands.mood import mood
from sanka.commands.status import status
from sanka.commands.tools import tools_command
from sanka.commands.whoami import whoami
from sanka.config import RoleConfig
from sanka.registry import Handler


class FakeStore:
    def __init__(self, rows: list | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple] = []

    def message_count(self) -> int:
        return 42

    def search(self, chat_id: str, query: str, limit: int = 10) -> list:
        self.calls.append((chat_id, query, limit))
        return self.rows


class FakeSessions:
    def __init__(self, rows: list | None = None) -> None:
        self.store = FakeStore(rows)

    def chat_name(self, chat_id: str) -> str:
        return "Grup Uji"

    def get(self, chat_id: str) -> list:
        return [1, 2, 3]


class FakeMemory:
    def count(self, scopes: list[str]) -> int:
        return 7 if "global" in scopes else 3


class FakeSkills:
    def names(self) -> list[str]:
        return ["a", "b", "c"]


def app() -> SimpleNamespace:
    return SimpleNamespace(
        sessions=FakeSessions(),
        memory=FakeMemory(),
        skills=FakeSkills(),
        bot_digits={"628567770837"},
        config=SimpleNamespace(
            llm=SimpleNamespace(model="deepseek-v4.1-flash"),
            whatsapp=SimpleNamespace(bot_name="sanka"),
        ),
    )


def test_whoami_shows_sender_role_chat_and_bot() -> None:
    ctx = SimpleNamespace(
        app=app(),
        chat_id="1234567890@g.us",
        role=RoleConfig("owner"),
        args="",
        sender="628111111111@s.whatsapp.net",
    )
    text = asyncio.run(whoami(ctx))

    assert "👤 Kamu: 628111111111@s.whatsapp.net" in text
    assert "🎭 Peran: Owner" in text
    assert '💬 Chat: grup "Grup Uji" (1234567890@g.us)' in text
    assert "🧭 Bot: sanka (628567770837)" in text


def test_status_reports_runtime_store_memory_and_tools() -> None:
    tools = (
        Handler("read", "Baca file teks."),
        Handler("sandbox", "Jalankan perintah."),
        Handler("mcp__time__now", "Waktu sekarang."),
    )
    ctx = SimpleNamespace(
        app=app(),
        chat_id="1234567890@g.us",
        role=RoleConfig("member"),
        args="",
        tools=tools,
    )
    text = asyncio.run(status(ctx))

    assert "⚙️ *Status Sanka*" in text
    assert "• Model: deepseek-v4.1-flash" in text
    assert "• Arsip: 42 pesan (3 di jendela ini)" in text
    assert "• Memori: 3 chat, 7 global" in text
    assert "• Skill: 3" in text
    assert "• Tool: 3 (1 MCP)" in text


def test_tools_lists_only_allowed_handlers() -> None:
    ctx = SimpleNamespace(
        role=RoleConfig("member"),
        tools=(
            Handler("mcp__time__now", "Waktu sekarang."),
            Handler("read", "Baca file teks di workspace dengan nomor baris."),
        ),
    )
    text = asyncio.run(tools_command(ctx))

    assert text.splitlines()[0] == "🧰 *Tool role member*"
    assert "• mcp__time__now — Waktu sekarang." in text
    assert "• read — Baca file teks di workspace dengan nomor baris." in text
    assert "sandbox" not in text


def test_cari_formats_archive_hits_and_scopes_to_current_chat() -> None:
    rows = [
        SimpleNamespace(
            text="rapat jam 3 di basecamp",
            media=None,
            sender_name="Kak Fahri",
            sender_id="628222222222",
            when=datetime(2026, 10, 22, 3, 5),
        )
    ]
    fake_app = app()
    fake_app.sessions.store.rows = rows
    ctx = SimpleNamespace(app=fake_app, chat_id="123@g.us", args="rapat", role=RoleConfig("member"))
    text = asyncio.run(cari(ctx))

    assert fake_app.sessions.store.calls == [("123@g.us", "rapat", 10)]
    assert '🔎 *Hasil "rapat" di chat ini* (terbaru dulu)' in text
    assert "• 2026-10-22 03:05 Kak Fahri: rapat jam 3 di basecamp" in text


def test_help_detail_respects_role_and_accepts_alias() -> None:
    member_commands = (COMMANDS.get("memori"),)
    ctx = SimpleNamespace(app=None, role=RoleConfig("member"), args="ingat", commands=member_commands)
    text = asyncio.run(help_command(ctx))

    assert text.splitlines()[0] == "🧭 */memori*"
    assert "• Pakai: /memori [global] [tambah|cari|hapus|daftar] <teks>" in text
    assert "• Alias: /memory, /ingat" in text

    ctx.args, ctx.commands = "backfill", member_commands
    assert asyncio.run(help_command(ctx)) == "🔒 Command /backfill tidak tersedia untuk role member."

    ctx.args = "tidak-ada"
    assert "tidak dikenal" in asyncio.run(help_command(ctx))


class FakeAffect:
    def modes(self, chat_id: str, person_id: str) -> dict:
        return {
            "label": "jahil",
            "valence": 0.4,
            "energy": 0.5,
            "boredom": 0.2,
            "loneliness": 0.1,
            "level": 0,
            "crying": False,
            "relation": "dekat",
            "person_name": "Muqsith",
        }


def test_mood_reports_affect_state() -> None:
    fake_app = app()
    fake_app.affect = FakeAffect()
    ctx = SimpleNamespace(app=fake_app, chat_id="123@g.us", sender="628123", role=RoleConfig("owner"))

    lines = asyncio.run(mood(ctx)).splitlines()

    assert lines[0] == "💭 *Mood Sanka*"
    for fragment in (
        "• Lagi: jahil",
        "• Perasaan: senang",
        "• Status: nggak ngambek",
        "• Ke Muqsith: dekat",
    ):
        assert fragment in lines


def test_mood_without_affect_store() -> None:
    ctx = SimpleNamespace(app=app(), chat_id="123@g.us", sender="", role=RoleConfig("owner"))

    assert asyncio.run(mood(ctx)) == "state emosi belum aktif."
