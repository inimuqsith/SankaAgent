"""Kontrak vibe ruangan: VAD dari label Indonesia, dinamika attack/decay, dan blok prompt."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from sanka.engine.config import Config, VibeConfig
from sanka.engine.decision import build_context
from sanka.engine.stores.vibe import VibeRunner, VibeState, advance
from sanka.engine.prompts import system_prompt
from sanka.engine.roles import Role
from support import build_config, database, role


def make_config() -> Config:
    return build_config(Path("/tmp"), roles={"member": role("member")})


def test_vad_mapping_is_negative_for_anger() -> None:
    point = VibeRunner.vad({"anger": 0.9, "joy": 0.1}, 0.35)

    assert point is not None
    assert point[0] < 0.3 and point[1] > 0.7


def test_vad_neutral_floor_returns_none() -> None:
    assert VibeRunner.vad({"joy": 0.2, "anger": 0.2}, 0.35) is None


def test_attack_and_baseline_move_gradually(tmp_path) -> None:
    runner = VibeRunner(
        database(tmp_path),
        VibeConfig(
            attack=0.5,
            baseline_influence=0.1,
            hold_seconds=0,
        ),
        classifier=lambda _text: {"joy": 0.9, "trust": 0.1},
    )
    runner._running = True

    asyncio.run(runner.observe("chat", "senang"))

    state = runner._state("chat")
    assert 0.5 < state.baseline[0] < 0.9  # mood dasar tidak melompat
    assert state.current[0] > state.baseline[0]


def test_describe_uses_words(tmp_path) -> None:
    runner = VibeRunner(database(tmp_path), VibeConfig())
    state = VibeState(current=[0.2, 0.8, 0.7], baseline=[0.2, 0.8, 0.7])

    text = runner._describe(state)

    assert "negatif, berenergi, percaya diri" in text
    assert "jangan sebut angka" in text


def test_describe_follows_current_not_slow_baseline(tmp_path) -> None:
    runner = VibeRunner(database(tmp_path), VibeConfig())
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

    assert block in system_prompt(make_config(), (Role("member"),), vibe=block)
    assert "Vibe ruangan" in build_context(vibe=block)


def test_observe_is_silent_when_not_running(tmp_path) -> None:
    runner = VibeRunner(database(tmp_path), VibeConfig(), classifier=lambda _t: {})

    assert asyncio.run(runner.observe("chat", "halo")) == ""


def test_start_reports_availability(tmp_path) -> None:
    ready = VibeRunner(
        database(tmp_path), VibeConfig(), classifier=lambda _t: {"joy": 1.0}
    )
    assert asyncio.run(ready.start()) is True  # classifier disuntik: tanpa torch
    asyncio.run(ready.close())


def test_start_keeps_loaded_model(tmp_path) -> None:
    runner = VibeRunner(database(tmp_path), VibeConfig())
    sentinel = object()
    runner._model = sentinel

    assert asyncio.run(runner.start()) is True
    assert runner._model is sentinel  # model yang sudah ada tidak dimuat ulang
    asyncio.run(runner.close())


def test_state_persists_to_database(tmp_path) -> None:
    db = database(tmp_path)
    runner = VibeRunner(
        db,
        VibeConfig(attack=0.5, baseline_influence=0.1, hold_seconds=0),
        classifier=lambda _text: {"joy": 0.9, "trust": 0.1},
    )
    runner._running = True
    asyncio.run(runner.observe("chat", "senang"))
    expected = list(runner._state("chat").current)
    asyncio.run(runner.close())

    reloaded = VibeRunner(db, VibeConfig())

    assert reloaded._state("chat").current == pytest.approx(expected)
