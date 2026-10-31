"""Kontrak lapisan karakter: pemicu ramping, dan draf asli menang saat ragu."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from support import build_config

from sanka.engine.character import (
    effective_limits,
    needs_polish,
    over_level,
    polish,
    regulate,
)
from sanka.engine.config import CharacterConfig

NATURAL = "capeknya di bagian mana?"


class StubLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0
        self.kwargs: dict = {}

    async def complete(self, system, user, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        return self.reply


def config_with(tmp_path, **character):
    settings = CharacterConfig(enabled=True, **character)
    return replace(build_config(tmp_path), character=settings)


def run_polish(config, llm, draft: str) -> str:
    return asyncio.run(
        polish(llm, config, draft, latest="halo", is_dm=True, tone="warm", session="s")
    )


def long_draft() -> str:
    return "Tentu! Berikut saran untuk acara kamu: susun rundown dulu, lalu cek konsumsi."


def test_short_natural_reply_needs_no_polish() -> None:
    assert needs_polish(NATURAL) is False
    assert needs_polish("") is False


def test_assistant_tell_markdown_and_lengths_trigger_polish() -> None:
    assert needs_polish("Tentu, aku bantu ya") is True
    assert needs_polish("- susun rundown\n- cek konsumsi") is True
    assert needs_polish("iya. kamu gimana? kabarin ya nanti sore. aku tunggu. jangan lupa.") is True
    assert needs_polish("a" * 241) is True
    assert needs_polish("x" * 401) is True


def test_disabled_layer_returns_the_draft_without_calling_the_model(tmp_path) -> None:
    llm = StubLLM("versi karakter")
    config = replace(config_with(tmp_path), character=CharacterConfig(enabled=False))

    assert run_polish(config, llm, long_draft()) == long_draft()
    assert llm.calls == 0


def test_natural_draft_is_returned_without_calling_the_model(tmp_path) -> None:
    llm = StubLLM("versi karakter")

    assert run_polish(config_with(tmp_path), llm, NATURAL) == NATURAL
    assert llm.calls == 0


def test_model_answer_replaces_a_robotic_draft(tmp_path) -> None:
    llm = StubLLM('FINAL: "rundown dulu, konsumsi belakangan"')

    assert run_polish(config_with(tmp_path), llm, long_draft()) == "rundown dulu, konsumsi belakangan"
    assert llm.calls == 1
    assert llm.kwargs["max_tokens"] == 500


def test_empty_answer_falls_back_to_the_draft(tmp_path) -> None:
    assert run_polish(config_with(tmp_path), StubLLM(""), long_draft()) == long_draft()


def test_answer_that_still_sounds_like_an_assistant_falls_back(tmp_path) -> None:
    llm = StubLLM("Tentu! Semoga membantu ya")

    assert run_polish(config_with(tmp_path), llm, long_draft()) == long_draft()


def test_answer_that_grows_the_reply_falls_back(tmp_path) -> None:
    llm = StubLLM("wkwk " + "panjang " * 120)

    assert run_polish(config_with(tmp_path), llm, long_draft()) == long_draft()


def test_fenced_draft_is_never_rewritten(tmp_path) -> None:
    llm = StubLLM("versi karakter")
    draft = "Tentu, ini contohnya:\n```\nsatu --- dua\n```\nsekian laporan lengkapnya ya"

    assert run_polish(config_with(tmp_path), llm, draft) == draft
    assert llm.calls == 0


def limits_config(tmp_path, **values):
    return replace(
        build_config(tmp_path),
        character=CharacterConfig(enabled=True, toxicity=3, adult_memes=3, **values),
    )


def run_regulate(config, llm, reply: str, *, target="male", confidence=0.9, **kwargs) -> str:
    return asyncio.run(
        regulate(
            llm,
            config,
            reply,
            target=target,
            confidence=confidence,
            latest="dasar goblok",
            session="s",
            **kwargs,
        )
    )


def test_limits_are_gentle_unless_the_target_is_confidently_male(tmp_path) -> None:
    settings = CharacterConfig(toxicity=3, adult_memes=3)

    assert effective_limits(settings, "male", 0.9) == (3, 3)
    assert effective_limits(settings, "male", 0.2) == (1, 0)
    assert effective_limits(settings, "female", 0.99) == (1, 0)
    assert effective_limits(settings, "unknown", 0.0) == (1, 0)
    assert effective_limits(settings, "", 0.0) == (1, 0)
    ungated = CharacterConfig(toxicity=3, adult_memes=3, gender_gate=False)
    assert effective_limits(ungated, "female", 0.0) == (3, 3)


def test_over_level_lists_markers_by_appearance() -> None:
    assert over_level("dasar goblok", toxicity=1, adult_memes=0) == ["goblok"]
    assert over_level("dasar goblok", toxicity=2, adult_memes=0) == []
    assert over_level("ngokang linggis", toxicity=4, adult_memes=2) == ["ngokang linggis"]
    assert over_level("jomok", toxicity=4, adult_memes=1) == []  # meme biasa, bukan dewasa
    assert over_level("jomok dulu, baru goblok", toxicity=0, adult_memes=3) == ["goblok"]
    assert over_level("", toxicity=0, adult_memes=0) == []


def test_regulate_within_level_never_calls_the_model(tmp_path) -> None:
    llm = StubLLM("bersih")

    assert run_regulate(limits_config(tmp_path), llm, "iya, goblok dikit gapapa") == (
        "iya, goblok dikit gapapa"
    )
    assert llm.calls == 0


def test_regulate_rewrites_an_over_level_reply_once(tmp_path) -> None:
    llm = StubLLM("yaelah, gitu doang")

    reply = run_regulate(
        limits_config(tmp_path), llm, "dasar goblok, jomok juga", target="unknown", confidence=0.0
    )

    assert reply == "yaelah, gitu doang"
    assert llm.calls == 1
    assert llm.kwargs["reasoning_effort"] == "minimal"
    assert llm.kwargs["temperature"] == 0.4


def test_regulate_keeps_the_reply_when_the_rewrite_still_trips(tmp_path) -> None:
    llm = StubLLM("dasar goblok lagi")

    assert (
        run_regulate(limits_config(tmp_path), llm, "dasar goblok", target="unknown", confidence=0.0)
        == "dasar goblok"
    )


def test_regulate_keeps_the_reply_when_the_model_fails(tmp_path) -> None:
    assert (
        run_regulate(limits_config(tmp_path), StubLLM(""), "dasar goblok", confidence=0.0)
        == "dasar goblok"
    )


def test_regulate_collapses_extra_questions_except_in_professional_mode(tmp_path) -> None:
    llm = StubLLM("capeknya di bagian mana?")
    reply = "capek ya? terus gimana?"

    assert run_regulate(limits_config(tmp_path), llm, reply) == "capeknya di bagian mana?"
    assert llm.calls == 1
    llm = StubLLM("capeknya di bagian mana?")
    assert (
        run_regulate(limits_config(tmp_path), llm, reply, is_professional=True) == reply
    )
    assert llm.calls == 0


def test_unsure_target_gets_no_curse_even_a_mild_one(tmp_path) -> None:
    settings = CharacterConfig(enabled=True, toxicity=3, adult_memes=0)

    # seruan seperti anjir/anjay bukan misuh: tetap boleh walau target belum pasti
    assert over_level("anjir, capek banget", toxicity=3, adult_memes=0, curses=False) == []
    assert over_level("anjay, gila", toxicity=3, adult_memes=0, curses=False) == []
    assert over_level("ngeselin banget", toxicity=3, adult_memes=0, curses=False) == []
    assert over_level("dasar goblok", toxicity=3, adult_memes=0, curses=False) == ["goblok"]

    llm = StubLLM("yaelah, capek banget ya. yang mana paling berat?")
    reply = asyncio.run(
        regulate(
            llm,
            replace(build_config(tmp_path), character=settings),
            "dasar goblok, capek banget",
            target="unknown",
            confidence=0.0,
            latest="capek banget",
            session="s",
        )
    )
    assert reply == "yaelah, capek banget ya. yang mana paling berat?"
    assert llm.calls == 1


def test_confident_male_keeps_a_mild_curse_inside_the_level(tmp_path) -> None:
    llm = StubLLM("harusnya nggak dipakai")
    settings = CharacterConfig(enabled=True, toxicity=3, adult_memes=0)
    reply = asyncio.run(
        regulate(
            llm,
            replace(build_config(tmp_path), character=settings),
            "anjir, capek banget",
            target="male",
            confidence=0.95,
            latest="capek banget",
            session="s",
        )
    )
    assert reply == "anjir, capek banget"
    assert llm.calls == 0
