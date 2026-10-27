"""Kontrak otonomi: tool terbatas, digest->memori, riset, dan chime yang tahu batas."""

from __future__ import annotations

import asyncio
import time
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
    def __init__(self, rows: list[ChatMessage], chats: list[str] | None = None) -> None:
        self.rows = rows
        self.chats = chats or []

    def recent(self, chat_id: str, limit: int) -> list[ChatMessage]:
        return self.rows[-limit:]

    def recent_chats(self, limit: int = 50) -> list[str]:
        return self.chats[:limit]


class PulseAffect:
    """Affect tiruan: cukup mencatat detak dan menjawab state netral."""

    def __init__(self) -> None:
        self.pulses: list[str] = []

    def pulse(self, chat_id: str) -> dict:
        self.pulses.append(chat_id)
        return {}

    def topic(self, chat_id: str) -> dict:
        return {
            "summary": "",
            "unresolved": [],
            "last_message": 0.0,
            "last_digest": 0.0,
            "last_research": 0.0,
            "last_chime": 0.0,
            "research_day": "",
            "research_count": 0,
            "chime_day": "",
            "chime_count": 0,
        }

    def pending(self, chat_id: str) -> int:
        return 0

    def status(self, chat_id: str, person_id: str = "") -> dict:
        return {
            "summary": "even, steady, balanced",
            "pulse": "quiet none pulse",
            "pleasure": 0.0,
            "arousal": 0.4,
            "dominance": 0.5,
        }

    def drives(self, chat_id: str) -> dict:
        return {"energy": 0.6, "boredom": 0.0, "loneliness": 0.0}


class FakeLLM:
    def __init__(self, digest: dict | None = None) -> None:
        self.payload = digest or {}
        self.chime_calls = 0
        self.transcripts: list[str] = []

    async def digest(self, chat_name: str, transcript: str, topic: str, people: str) -> dict:
        self.transcripts.append(transcript)
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


def test_tick_pulses_affect(tmp_path) -> None:
    affect = PulseAffect()
    ambient = Ambient(
        make_config(tmp_path), FakeLLM(), affect, None, None, FakeHistory([message("sepi ya")]), None
    )
    ambient.note(CHAT, message("sepi ya"))

    asyncio.run(ambient._tick(now=1000.0))

    assert affect.pulses == [CHAT]


def test_prime_loads_history_chats(tmp_path) -> None:
    ambient = Ambient(
        make_config(tmp_path),
        FakeLLM(),
        None,
        None,
        None,
        FakeHistory([], chats=[CHAT, DM]),
        None,
    )

    ambient.prime()

    assert ambient.known_chats == {CHAT, DM}


def test_digest_stores_topic_and_person_note(tmp_path) -> None:
    affect = AffectStore(tmp_path / "affect.db", AffectConfig(enabled=True))
    affect.turn_end(CHAT, PERSON, "Fahri", "halo", "member", "628222222222@s.whatsapp.net", None, "M1")
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


def test_chime_skips_dm(tmp_path) -> None:
    affect = AffectStore(tmp_path / "affect.db", AffectConfig(enabled=True))
    sent: list[tuple[str, str]] = []

    async def send_text(chat_id: str, body: str) -> str:
        sent.append((chat_id, body))
        return "ok"

    llm = FakeLLM()
    ambient = Ambient(
        make_config(tmp_path),
        llm,
        affect,
        None,
        None,
        FakeHistory([message("sepi ya")]),
        sessions=None,
        send_text=send_text,
    )

    ambient.note(DM, message("halo"))
    asyncio.run(ambient._chime(DM))

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
        make_config(tmp_path),
        FakeLLM(),
        affect,
        None,
        None,
        FakeHistory([message("sepi ya")]),
        sessions=None,
        send_text=send_text,
    )
    ambient.note(CHAT, message("sepi ya"))

    asyncio.run(ambient._chime(CHAT))

    assert sent == [(CHAT, "eh masih pada di sini?")]
    assert affect.topic(CHAT)["chime_count"] == 1
    affect.close()


def test_digest_excludes_outgoing_messages(tmp_path) -> None:
    affect = AffectStore(tmp_path / "affect.db", AffectConfig(enabled=True))
    llm = FakeLLM({"topic": "logistik", "keywords": [], "facts": [], "unresolved": []})
    rows = [message("rapat jam 3"), message("1.3TB sisa", sender="Sanka", outgoing=True)]
    ambient = Ambient(
        make_config(tmp_path),
        llm,
        affect,
        None,
        None,
        FakeHistory(rows),
        sessions=None,
    )

    asyncio.run(ambient._digest(CHAT))

    assert llm.transcripts
    assert "1.3TB" not in llm.transcripts[0]
    assert "rapat jam 3" in llm.transcripts[0]
    affect.close()


class LonelyAffect(PulseAffect):
    """Perasaan yang mendorong menyelituk: sepi, gabut, gelisah, ada yang menggantung."""

    def __init__(self, *, quiet_seconds: float = 3600.0, chime_age: float = 7200.0) -> None:
        super().__init__()
        self.quiet_seconds = quiet_seconds
        self.chime_age = chime_age

    def status(self, chat_id: str, person_id: str = "") -> dict:
        return {
            "summary": "guarded",
            "pulse": "quiet none pulse",
            "pleasure": -0.4,
            "arousal": 0.6,
            "dominance": 0.4,
        }

    def drives(self, chat_id: str) -> dict:
        return {"energy": 0.4, "boredom": 1.0, "loneliness": 1.0}

    def pending(self, chat_id: str) -> int:
        return 0

    def topic(self, chat_id: str) -> dict:
        now = time.time()
        return {
            "summary": "logistik",
            "unresolved": [],
            "last_message": now - self.quiet_seconds,
            "last_digest": 0.0,
            "last_research": 0.0,
            "last_chime": now - self.chime_age,
            "research_day": "",
            "research_count": 0,
            "chime_day": "",
            "chime_count": 0,
        }


def chime_ambient(tmp_path, affect, rows: list[ChatMessage] | None = None) -> Ambient:
    return Ambient(
        make_config(tmp_path),
        FakeLLM(),
        affect,
        None,
        None,
        FakeHistory(rows or [message("sepi ya")]),
        sessions=None,
    )


def test_chime_candidate_follows_emotional_urge(tmp_path) -> None:
    ambient = chime_ambient(tmp_path, LonelyAffect())

    assert ambient._urge(CHAT) >= 0.45
    assert asyncio.run(ambient._chime_candidate(CHAT, time.time())) is True
    assert asyncio.run(ambient._chime_candidate(DM, time.time())) is False  # DM tidak pernah diselituki


def test_calm_room_keeps_sanka_quiet(tmp_path) -> None:
    # Sejam sepi dan cooldown lewat, tapi perasaannya betah: diam adalah jawaban yang benar.
    ambient = chime_ambient(tmp_path, PulseAffect())
    quiet = time.time() - 3600

    assert ambient._urge(CHAT) < 0.45
    assert asyncio.run(ambient._chime_candidate(CHAT, quiet + 3600)) is False


def test_chime_does_not_interrupt_busy_room(tmp_path) -> None:
    ambient = chime_ambient(tmp_path, LonelyAffect(quiet_seconds=300))

    assert asyncio.run(ambient._chime_candidate(CHAT, time.time())) is False


def test_chime_cooldown_and_cold_topic_are_rails(tmp_path) -> None:
    fresh = chime_ambient(tmp_path, LonelyAffect(chime_age=60))  # baru menyelituk semenit lalu
    cold = chime_ambient(tmp_path, LonelyAffect(quiet_seconds=20 * 3600))  # topik 20 jam, bukan sepi
    own = chime_ambient(tmp_path, LonelyAffect(), rows=[message("eh", outgoing=True)])

    assert asyncio.run(fresh._chime_candidate(CHAT, time.time())) is False
    assert asyncio.run(cold._chime_candidate(CHAT, time.time())) is False
    assert asyncio.run(own._chime_candidate(CHAT, time.time())) is False


def test_urge_is_zero_when_state_is_broken(tmp_path) -> None:
    class Broken(PulseAffect):
        def status(self, chat_id: str, person_id: str = "") -> dict:
            raise RuntimeError("state rusak")

    assert chime_ambient(tmp_path, Broken())._urge(CHAT) == 0.0


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
