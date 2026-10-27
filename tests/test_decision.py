"""Kontrak keputusan: parser ketat, konteks lengkap, dan mention wajib dibalas."""

from __future__ import annotations

import asyncio

from sanka import decision as decision_module
from sanka.decision import (
    Decision,
    build_context,
    decide,
    ensure_reply,
    ground_tone,
    parse_decision,
)

VALID = """```json
{"addressed":"me","act":"reply","mode":"daily","tone":"warm",
 "bubbles":2,"appraisal":{"label":"warmth","dP":0.2,"dA":0.1,"dD":0.0,"note":"dia nyapa"},
 "relationship":{"trust":0.05,"affection":0.1,"respect":0.0,"annoyance":0.0},"reason":"disapa"}
```"""


class FakeLLM:
    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.calls: list[dict] = []

    async def complete(self, system, user, *, max_tokens=300, temperature=0.2, session="") -> str:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "session": session,
            }
        )
        return self.answers.pop(0) if self.answers else ""


def test_parse_decision_valid() -> None:
    parsed = parse_decision(VALID)

    assert parsed is not None
    assert parsed.addressed == "me" and parsed.act == "reply" and parsed.mode == "daily"
    assert parsed.bubbles == 2
    assert parsed.appraisal.label == "warmth"
    assert parsed.appraisal.dP == 0.2
    assert parsed.relationship.affection == 0.1
    assert parsed.reason == "disapa"


def test_parse_decision_invalid() -> None:
    assert parse_decision("") is None
    assert parse_decision("bukan json") is None
    assert parse_decision('{"addressed":"me","act":"silence","mode":"daily","tone":"warm"}') is None
    assert parse_decision('{"addressed":"me","act":"reply","mode":"kerja","tone":"warm"}') is None
    assert parse_decision('{"addressed":"dia","act":"reply","mode":"daily","tone":"warm"}') is None


def test_parse_decision_clamps_and_truncates() -> None:
    raw = (
        '{"addressed":"me","act":"reply","mode":"daily","tone":"curt","bubbles":9,'
        '"appraisal":{"label":"bogus","dP":-9,"dA":9,"dD":0,"note":"' + "n" * 400 + '"},'
        '"relationship":{"trust":-9,"annoyance":9},"reason":"' + "r" * 400 + '"}'
    )

    parsed = parse_decision(raw)

    assert parsed is not None
    assert parsed.bubbles == 3
    assert parsed.appraisal.label == "neutral"
    assert parsed.appraisal.dP == -0.4 and parsed.appraisal.dA == 0.4
    assert len(parsed.appraisal.note) == 120
    assert parsed.relationship.trust == -0.15 and parsed.relationship.annoyance == 0.15
    assert len(parsed.reason) == 80


def test_context_includes_emotion_relationship_and_transcript() -> None:
    text = build_context(
        chat_id="120@g.us",
        chat_name="panitia",
        current="Fahri: kamu culun",
        transcript="Fahri: halo\nkamu (sanka): hai",
        emotion={
            "summary": "guarded, energized, balanced",
            "pulse": "strong hostility pulse",
            "pleasure": -0.3,
            "arousal": 0.6,
            "dominance": 0.5,
            "trust_tier": "New",
            "log": [{"appraisal": "hostility", "situation": "dihina", "relational_meaning": "sakit"}],
        },
        relationship="- Fahri (member): baru, kesal 0.3",
        is_mention=True,
    )

    assert "guarded, energized, balanced" in text
    assert "strong hostility pulse" in text
    assert "P -0.3" in text and "trust New" in text
    assert "hostility: dihina (sakit)" in text
    assert "Fahri (member): baru, kesal 0.3" in text
    assert "Fahri: halo" in text and "kamu (sanka): hai" in text
    assert "Fahri: kamu culun" in text
    assert "mention asli ke kamu" in text


def test_context_marks_who_and_other_chat_in_emotion_log() -> None:
    text = build_context(
        emotion={
            "summary": "guarded, steady, soft",
            "pleasure": -0.3,
            "arousal": 0.5,
            "dominance": 0.3,
            "log": [
                {
                    "appraisal": "hostility",
                    "situation": "dihina",
                    "relational_meaning": "sakit",
                    "person_name": "Fahri",
                },
                {
                    "appraisal": "warmth",
                    "situation": "halo",
                    "person_name": "Anda",
                    "other_chat": True,
                },
            ],
        }
    )

    assert "hostility [Fahri]: dihina (sakit)" in text
    assert "warmth [Anda, chat lain]: halo" in text


def test_decision_uses_one_llm_call_with_full_context() -> None:
    llm = FakeLLM(
        [
            (
                '{"addressed":"other","act":"ignore","mode":"daily","tone":"neutral","bubbles":1,'
                '"appraisal":{"label":"neutral","dP":0,"dA":0,"dD":0,"note":""},'
                '"relationship":{},"reason":"bukan buat aku"}'
            )
        ]
    )

    parsed = asyncio.run(
        decide(
            llm,
            chat_id="120@g.us",
            chat_name="panitia",
            current="Fahri: besok rapat jam 3",
            transcript="Fahri: besok rapat jam 3",
            emotion={"summary": "even, steady, balanced", "pulse": "subtle warmth pulse"},
            relationship="- Fahri (member): teman",
            soul="Kamu Sanka.",
        )
    )

    assert parsed is not None and parsed.act == "ignore" and parsed.addressed == "other"
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["session"] == "sanka-decision-120@g.us"
    assert "Kamu Sanka." in call["system"]
    assert "panitia" in call["user"] and "Fahri: besok rapat jam 3" in call["user"]


def test_decision_retries_once_when_empty_then_gives_up() -> None:
    llm = FakeLLM(["", ""])

    parsed = asyncio.run(
        decide(
            llm,
            chat_id="120@g.us",
            chat_name="panitia",
            current="Fahri: halo",
            transcript="Fahri: halo",
        )
    )

    assert parsed is None
    assert [call["max_tokens"] for call in llm.calls] == [4000, 8000]


def test_invalid_json_falls_back_to_ignore_without_regex() -> None:
    llm = FakeLLM(["maaf aku tidak bisa menjawab itu"])

    parsed = asyncio.run(
        decide(
            llm,
            chat_id="120@g.us",
            chat_name="grup",
            current="Fahri: wkwk",
            transcript="Fahri: wkwk",
        )
    )

    assert parsed is not None
    assert parsed.act == "ignore" and parsed.addressed == "unknown"
    assert parsed.reason == "invalid"


def test_direct_mention_is_forced_reply() -> None:
    ignored = Decision(
        addressed="other",
        act="ignore",
        mode="daily",
        tone="warm",
        bubbles=1,
        appraisal=parse_decision(VALID).appraisal,
        relationship=parse_decision(VALID).relationship,
        reason="bukan buat aku",
    )

    forced = ensure_reply(ignored)

    assert forced.act == "reply"
    assert forced.tone == "curt"
    assert forced.reason.endswith(";direct")
    assert ensure_reply(forced) == forced

    cold = ensure_reply(
        Decision(
            addressed="me",
            act="react",
            mode="daily",
            tone="hurt",
            bubbles=1,
            appraisal=ignored.appraisal,
            relationship=ignored.relationship,
            reason="",
        )
    )
    assert cold.act == "reply" and cold.tone == "hurt" and cold.reason == "direct"


def test_no_regex_mode_override() -> None:
    assert not hasattr(decision_module, "mode_for")
    raw = '{"addressed":"me","act":"reply","mode":"professional","tone":"neutral","bubbles":1}'

    parsed = parse_decision(raw)

    assert parsed is not None and parsed.mode == "professional"


def test_ground_tone_softens_cold_tone_when_mood_is_calm() -> None:
    decision = parse_decision(
        '{"addressed":"me","act":"reply","mode":"daily","tone":"hurt","bubbles":1,'
        '"appraisal":{"label":"neutral","dP":0,"dA":0,"dD":0},'
        '"relationship":{"trust":0,"affection":0,"respect":0,"annoyance":0},"reason":"x"}'
    )
    assert decision is not None

    grounded = ground_tone(decision, {"pleasure": 0.1, "arousal": 0.3}, annoyance=0.0)

    assert grounded.tone == "neutral"  # tidak boleh mengaku ngambek saat PAD netral


def test_ground_tone_keeps_cold_tone_when_actually_hurt_or_annoyed() -> None:
    decision = parse_decision(
        '{"addressed":"me","act":"reply","mode":"daily","tone":"angry","bubbles":1,'
        '"appraisal":{"label":"hostility","dP":-0.4,"dA":0.2,"dD":0},'
        '"relationship":{"trust":0,"affection":0,"respect":0,"annoyance":0.2},"reason":"x"}'
    )
    assert decision is not None

    assert ground_tone(decision, {"pleasure": -0.4}, annoyance=0.0).tone == "angry"
    assert ground_tone(decision, {"pleasure": 0.3}, annoyance=0.6).tone == "angry"  # orangnya menyulut


def test_ground_tone_leaves_warm_tones_alone() -> None:
    decision = parse_decision(
        '{"addressed":"me","act":"reply","mode":"daily","tone":"playful","bubbles":1,'
        '"appraisal":{"label":"warmth","dP":0.2,"dA":0,"dD":0},'
        '"relationship":{"trust":0,"affection":0,"respect":0,"annoyance":0},"reason":"x"}'
    )
    assert decision is not None

    assert ground_tone(decision, {"pleasure": -0.5}).tone == "playful"
