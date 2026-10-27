"""Kontrak vibe ruangan: VAD dari label Indonesia, dinamika attack/decay, dan blok prompt."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from sanka.config import (
    Config,
    LLMConfig,
    RoleConfig,
    SandboxConfig,
    VibeConfig,
    WhatsAppConfig,
)
from sanka.decision import build_context
from sanka.vibe import VibeRunner, VibeState, advance
from sanka.wa import system_prompt


def make_config() -> Config:
    return Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=Path("/tmp"),
        whatsapp=WhatsAppConfig(session_db=Path("/tmp/sanka-vibe.db")),
        roles={"member": RoleConfig("member")},
        default_role="member",
        sandbox=SandboxConfig(),
        mcp={},
    )


def test_vad_mapping_is_negative_for_anger() -> None:
    point = VibeRunner.vad({"anger": 0.9, "joy": 0.1}, 0.35)

    assert point is not None
    assert point[0] < 0.3 and point[1] > 0.7


def test_vad_neutral_floor_returns_none() -> None:
    assert VibeRunner.vad({"joy": 0.2, "anger": 0.2}, 0.35) is None


def test_attack_and_baseline_move_gradually(tmp_path) -> None:
    runner = VibeRunner(
        VibeConfig(
            attack=0.5,
            baseline_influence=0.1,
            hold_seconds=0,
            state_path=tmp_path / "vibe.json",
        ),
        classifier=lambda _text: {"joy": 0.9, "trust": 0.1},
    )
    runner._running = True

    asyncio.run(runner.observe("chat", "senang"))

    state = runner._state("chat")
    assert 0.5 < state.baseline[0] < 0.9  # mood dasar tidak melompat
    assert state.current[0] > state.baseline[0]


def test_describe_uses_words(tmp_path) -> None:
    runner = VibeRunner(VibeConfig(state_path=tmp_path / "vibe.json"))
    state = VibeState(current=[0.2, 0.8, 0.7], baseline=[0.2, 0.8, 0.7])

    text = runner._describe(state)

    assert "negatif, berenergi, percaya diri" in text
    assert "jangan sebut angka" in text


def test_describe_follows_current_not_slow_baseline(tmp_path) -> None:
    runner = VibeRunner(VibeConfig(state_path=tmp_path / "vibe.json"))
    state = VibeState(current=[0.1, 0.9, 0.2], baseline=[0.5, 0.4, 0.5])

    # baseline masih netral, tapi pesan barusan harus terbaca di blok prompt.
    assert "negatif, berenergi, tertekan" in runner._describe(state)


def test_advance_pulls_current_to_baseline() -> None:
    now = time.time()
    state = VibeState(
        current=[0.9, 0.9, 0.9],
        baseline=[0.8, 0.8, 0.8],
        last_tick=now - 120,
        hold_until=0.0,
    )

    advance(state, now, VibeConfig())

    assert state.current[0] < 0.9  # current mengejar baseline
    assert state.baseline[0] < 0.8  # baseline mengejar netral


def test_vibe_block_in_prompts() -> None:
    block = "Vibe ruangan: netral, tenang, seimbang."

    assert block in system_prompt(make_config(), "member", {"628"}, vibe=block)
    assert "Vibe ruangan" in build_context(vibe=block)


def test_observe_is_silent_when_not_running(tmp_path) -> None:
    runner = VibeRunner(VibeConfig(state_path=tmp_path / "vibe.json"), classifier=lambda _t: {})

    assert asyncio.run(runner.observe("chat", "halo")) == ""


def test_start_reports_availability(tmp_path) -> None:
    ready = VibeRunner(
        VibeConfig(state_path=tmp_path / "vibe.json"), classifier=lambda _t: {"joy": 1.0}
    )
    assert asyncio.run(ready.start()) is True  # classifier disuntik: tanpa torch
    asyncio.run(ready.close())
