"""Kontrak otonomi: tool terbatas, digest->memori, riset, dan chime yang tahu batas."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from sanka import ambient as ambient_module
from sanka.affect import AffectStore
from sanka.ambient import Ambient
from sanka.chat import ChatMessage
from sanka.config import (
    AffectConfig,
    AmbientConfig,
    Config,
    LLMConfig,
    RoleConfig,
    SandboxConfig,
    WhatsAppConfig,
)
from sanka.llm import parse_digest
from sanka.memory import MemoryStore

CHAT = "120363415422117951@g.us"
DM = "628222222222@s.whatsapp.net"
PERSON = "628222222222"


def make_config(tmp_path: Path) -> Config:
    return Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=tmp_path,
        whatsapp=WhatsAppConfig(session_db=tmp_path / "wa.db"),
        roles={"member": RoleConfig("member")},
        default_role="member",
        sandbox=SandboxConfig(),
        mcp={},
        ambient=AmbientConfig(enabled=True),
    )


def message(text: str, *, sender: str = "Fahri", outgoing: bool = False) -> ChatMessage:
    return ChatMessage(
        id=f"id-{text[:6]}-{sender}",
        sender_name=sender,
        sender_id="628222222222",
        when=datetime(2026, 1, 1, 10, 0),
        text=text,
        outgoing=outgoing,
    )


class FakeHistory:
    def __init__(self, rows: list[ChatMessage]) -> None:
        self.rows = rows

    def recent(self, chat_id: str, limit: int) -> list[ChatMessage]:
        return self.rows[-limit:]


class FakeLLM:
    def __init__(self, digest: dict | None = None) -> None:
        self.payload = digest or {}
        self.chime_calls = 0

    async def digest(self, chat_name: str, transcript: str, topic: str, people: str) -> dict:
        return self.payload

    async def research_summary(self, query: str, results: str) -> str:
        return "hasil riset singkat"

    async def chime(self, system: str, user: str) -> str:
        self.chime_calls += 1
        return "eh masih pada di sini?"


def test_ambient_tool_context_is_web_search_only(tmp_path) -> None:
    ambient = Ambient(make_config(tmp_path))

    ctx = ambient._tool_ctx(CHAT)

    assert ctx.allowed == ("web_search",)
    assert ctx.role.tools == ("web_search",)
    assert ctx.chat_id == CHAT


def test_digest_stores_topic_and_person_note(tmp_path) -> None:
    affect = AffectStore(tmp_path / "affect.db", AffectConfig(enabled=True))
    affect.observe(CHAT, PERSON, "Fahri", "halo", directed=False, is_dm=False)
    memory = MemoryStore(tmp_path / "memory.db")
    llm = FakeLLM(
        {
            "topic": "rapat logistik mingguan",
            "keywords": ["rapat", "logistik"],
            "facts": [
                {"kind": "fact", "text": "rapat pindah ke hari Jumat", "person": "Fahri"},
                {"kind": "rule", "text": "absen dikirim sebelum Jumat", "person": ""},
            ],
            "unresolved": ["jumat jam berapa?"],
        }
    )
    ambient = Ambient(make_config(tmp_path), llm, affect, None, memory, FakeHistory([message("rapat kapan?")]), None)

    asyncio.run(ambient._digest(CHAT))

    assert "rapat logistik mingguan" in affect.topic_block(CHAT)
    assert "jumat jam berapa?" in affect.topic_block(CHAT)
    people = affect.known_people(CHAT, 5)
    assert people[0]["notes"] == ["rapat pindah ke hari Jumat"]
    stored = memory.entries([CHAT])
    assert any("absen dikirim" in item["text"] and item["source"] == f"ambient:{CHAT}" for item in stored)
    assert ambient.busy == set()
    memory.close()
    affect.close()


def test_research_writes_memory_note(tmp_path, monkeypatch) -> None:
    affect = AffectStore(tmp_path / "affect.db", AffectConfig(enabled=True))
    affect.set_topic(CHAT, "rapat logistik", ["rapat"], ["jumat jam berapa?"])
    memory = MemoryStore(tmp_path / "memory.db")
    ambient = Ambient(make_config(tmp_path), FakeLLM(), affect, None, memory, FakeHistory([message("x")]), None)

    async def fake_execute(specs, name, args, ctx) -> str:
        assert name == "web_search"
        assert args["query"] == "jumat jam berapa?"
        return "hasil pencarian tentang jadwal"

    monkeypatch.setattr(ambient_module, "execute", fake_execute)

    asyncio.run(ambient._research(CHAT))

    stored = memory.entries([CHAT])
    assert stored and stored[0]["source"] == f"ambient:{CHAT}"
    assert stored[0]["text"].startswith("[riset ")
    assert affect.topic(CHAT)["research_count"] == 1
    memory.close()
    affect.close()


def test_research_skips_without_question(tmp_path, monkeypatch) -> None:
    affect = AffectStore(tmp_path / "affect.db", AffectConfig(enabled=True))
    memory = MemoryStore(tmp_path / "memory.db")
    ambient = Ambient(make_config(tmp_path), FakeLLM(), affect, None, memory, FakeHistory([]), None)

    async def fake_execute(specs, name, args, ctx) -> str:  # pragma: no cover - tidak dipanggil
        raise AssertionError("web_search tidak boleh dipanggil tanpa pertanyaan")

    monkeypatch.setattr(ambient_module, "execute", fake_execute)

    asyncio.run(ambient._research(CHAT))

    assert memory.entries([CHAT]) == []
    memory.close()
    affect.close()


def test_chime_skips_dm_and_sulk(tmp_path) -> None:
    affect = AffectStore(tmp_path / "affect.db", AffectConfig(enabled=True))
    sent: list[tuple[str, str]] = []

    async def send_text(chat_id: str, body: str) -> str:
        sent.append((chat_id, body))
        return "ok"

    llm = FakeLLM()
    ambient = Ambient(
        make_config(tmp_path), llm, affect, None, None, FakeHistory([message("sepi ya")]), None, None, send_text
    )

    ambient.note(DM, message("halo"))
    asyncio.run(ambient._chime(DM))
    assert sent == []

    ambient.note(CHAT, message("halo"))
    affect.observe(CHAT, PERSON, "Fahri", "goblok", directed=True, is_dm=False)
    asyncio.run(ambient._chime(CHAT))
    assert sent == []
    assert llm.chime_calls == 0
    affect.close()


def test_chime_sends_when_allowed(tmp_path) -> None:
    affect = AffectStore(tmp_path / "affect.db", AffectConfig(enabled=True))
    sent: list[tuple[str, str]] = []

    async def send_text(chat_id: str, body: str) -> str:
        sent.append((chat_id, body))
        return "ok"

    ambient = Ambient(
        make_config(tmp_path), FakeLLM(), affect, None, None, FakeHistory([message("sepi ya")]), None, None, send_text
    )
    ambient.note(CHAT, message("sepi ya"))

    asyncio.run(ambient._chime(CHAT))

    assert sent == [(CHAT, "eh masih pada di sini?")]
    assert affect.topic(CHAT)["chime_count"] == 1
    affect.close()


def test_digest_parsing_rejects_secrets_and_junk() -> None:
    data = parse_digest(
        '{"topic":"rapat","keywords":["rapat",3],"facts":['
        '{"kind":"fact","text":"api key panitia abc","person":""},'
        '{"kind":"fact","text":"rapat maju jam 3","person":"Fahri"},'
        '{"kind":"jadwal","text":"hari jumat","person":""}],'
        '"unresolved":["jam berapa?"]}'
    )

    assert data["topic"] == "rapat"
    assert data["keywords"] == ["rapat"]
    assert [fact["text"] for fact in data["facts"]] == ["rapat maju jam 3", "hari jumat"]
    assert data["facts"][1]["kind"] == "note"
    assert data["unresolved"] == ["jam berapa?"]
    assert parse_digest("bukan json") == {"topic": "", "keywords": [], "facts": [], "unresolved": []}
