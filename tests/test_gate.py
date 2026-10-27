"""Kontrak gate: Sanka memutuskan sendiri, skor deterministik hanya prior/fallback."""

from __future__ import annotations

import asyncio

from sanka.config import GateConfig
from sanka.gate import ReplyGate, contentless

CHAT = "120363415422117951@g.us"
DM = "628222222222@s.whatsapp.net"
SENDER = "628222222222"


class FakeLLM:
    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.decide_calls = 0
        self.judge_calls = 0

    async def decide(self, system: str, user: str) -> str:
        self.decide_calls += 1
        return self.verdict

    async def judge(self, system: str, user: str) -> str:
        self.judge_calls += 1
        return self.verdict


def gate_of(llm=None, *, stickers: tuple[str, ...] = (), **kwargs) -> ReplyGate:
    options = {
        "enabled": True,
        "llm_decide": False,
        "llm_review": False,
        "reply_cooldown_seconds": 0.0,
    }
    options.update(kwargs)
    return ReplyGate(GateConfig(**options), llm, None, None, "sanka", stickers)


def decide(gate: ReplyGate, *, text="halo", is_dm=False, mentioned=False, replied=False, now=1000.0, **kwargs):
    return asyncio.run(
        gate.decide(
            chat_id=DM if is_dm else CHAT,
            text=text,
            is_dm=is_dm,
            mentioned=mentioned,
            replied_to_bot=replied,
            sender_id=SENDER,
            has_media=False,
            quote_sender_is_bot=False,
            now=now,
            **kwargs,
        )
    )


def test_dm_replies_when_llm_decide_off() -> None:
    decision = decide(gate_of(), text="halo", is_dm=True)

    assert decision.act == "reply" and decision.reason == "direct"


def test_mention_replies_when_llm_decide_off() -> None:
    decision = decide(gate_of(), text="halo", mentioned=True)

    assert decision.act == "reply" and decision.reason == "mention"


def test_llm_can_ignore_mention() -> None:
    llm = FakeLLM('{"act":"ignore","why":"bukan buat aku"}')

    decision = decide(gate_of(llm, llm_decide=True), text="wkwk boong", mentioned=True)

    assert decision.act == "ignore" and llm.decide_calls == 1


def test_llm_can_ignore_dm() -> None:
    llm = FakeLLM('{"act":"ignore","why":"males"}')

    decision = decide(gate_of(llm, llm_decide=True), text="halo", is_dm=True)

    assert decision.act == "ignore"


def test_ignore_cap_forces_reply() -> None:
    llm = FakeLLM('{"act":"ignore"}')
    gate = gate_of(llm, llm_decide=True, max_consecutive_ignores=2)

    decisions = [decide(gate, text="halo", is_dm=True, now=1000.0 + index) for index in range(3)]

    assert [item.act for item in decisions] == ["ignore", "ignore", "reply"]
    assert decisions[-1].reason == "ignore_cap"


def test_invalid_decision_falls_back_to_score() -> None:
    llm = FakeLLM("ngawur")

    decision = decide(gate_of(llm, llm_decide=True), text="halo", is_dm=True)

    assert decision.act == "reply" and decision.reason == "direct"


def test_reply_to_bot_banter_is_ignored() -> None:
    decision = decide(gate_of(), text="wkwk", replied=True)

    assert decision.act != "reply"


def test_direct_question_replies() -> None:
    decision = decide(gate_of(), text="kok gitu sih?", mentioned=True)

    assert decision.act == "reply"


def test_rate_limit_wins_even_for_mention() -> None:
    gate = gate_of(max_replies_per_10min=8, max_person_replies_per_10min=99)
    for index in range(8):
        moment = 1000.0 + index * 60
        assert decide(gate, text="halo", mentioned=True, now=moment).act == "reply"
        gate.record_sent(CHAT, SENDER, now=moment)

    decision = decide(gate, text="halo", mentioned=True, now=1000.0 + 8 * 60)

    assert decision.act == "ignore" and decision.reason == "rate_limit"


def test_person_rate_limit() -> None:
    gate = gate_of(max_person_replies_per_10min=2)
    for index in range(2):
        moment = 1000.0 + index * 60
        assert decide(gate, text="halo", is_dm=True, now=moment).act == "reply"
        gate.record_sent(DM, SENDER, now=moment)

    decision = decide(gate, text="halo", is_dm=True, now=1190.0)

    assert decision.act == "ignore" and decision.reason == "rate_limit"


def test_react_needs_sticker() -> None:
    without = decide(gate_of(reply_threshold=0.45, react_threshold=0.05), text="sanka gimana")
    with_sticker = decide(
        gate_of(reply_threshold=0.45, react_threshold=0.05, stickers=("stickers/a.webp",)),
        text="sanka gimana",
    )

    assert without.act == "ignore"
    assert with_sticker.act == "react"


def test_crisis_always_replies() -> None:
    decision = decide(gate_of(llm_decide=False), text="pengen mati aja", is_dm=True)

    assert decision.act == "reply" and decision.reason == "crisis"


def test_empty_message_is_ignored() -> None:
    decision = decide(gate_of(), text="", is_dm=True)

    assert decision.act == "ignore" and decision.reason == "empty"


def test_judge_reviews_borderline_score() -> None:
    llm = FakeLLM("ignore")
    gate = gate_of(llm, llm_review=True, review_low=0.0, review_high=1.0)

    decision = decide(gate, text="sanka gimana")

    assert llm.judge_calls == 1
    assert decision.act == "ignore"


def test_judge_review_is_rate_limited() -> None:
    llm = FakeLLM("ignore")
    gate = gate_of(llm, llm_review=True, max_reviews_per_minute=1, review_low=0.0, review_high=1.0)

    decide(gate, text="sanka gimana", now=1000.0)
    decide(gate, text="sanka gimana", now=1001.0)

    assert llm.judge_calls == 1


def test_contentless_detection() -> None:
    assert contentless("", has_media=True) is True
    assert contentless("🗿", has_media=False) is True
    assert contentless("halo", has_media=False) is False
