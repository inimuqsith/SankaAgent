"""Kontrak gaya: profil acuan, agregat belajar, bubble pendek, dan batas balasan."""

from __future__ import annotations

from pathlib import Path

from sanka.config import HumanizeConfig, StyleConfig
from sanka.format import human_chunks, humanize_reply, reply_budget
from sanka.style import StyleStore


def make_store(tmp_path: Path) -> StyleStore:
    return StyleStore(tmp_path / "style.json", StyleConfig(enabled=True))


def test_seed_profile_mentions_short_style(tmp_path) -> None:
    store = make_store(tmp_path)

    text = store.profile()

    assert "median 34" in text
    assert "1-2 kalimat" in text
    store.close()


def test_learn_ignores_commands_and_media(tmp_path) -> None:
    store = make_store(tmp_path)

    store.learn("/help")
    store.learn("[Stiker]")
    store.learn("   ")
    assert store.stats()["global"] == 0

    store.learn("iyaa aku udah cek, aman kok", chat_id="c@g.us", person_id="628111")
    stats = store.stats()
    assert stats["global"] == 1
    assert stats["chats"] == {"c@g.us": 1}
    store.close()


def test_learn_persists_aggregates_only(tmp_path) -> None:
    store = make_store(tmp_path)
    store.learn("wkwk aja sih", chat_id="c@g.us")
    store.close()

    reloaded = make_store(tmp_path)
    assert reloaded.stats()["global"] == 1
    assert "wkwk aja sih" not in reloaded.path.read_text(encoding="utf-8")
    reloaded.close()


def test_human_chunks_short(tmp_path) -> None:
    parts = human_chunks("a" * 700, limit=220, max_parts=3)

    assert len(parts) == 3
    assert all(len(part) <= 220 for part in parts)
    assert parts[-1].endswith("…")


def test_human_chunks_keeps_fence() -> None:
    fence = "```\n" + "b" * 400 + "\n```"

    parts = human_chunks(fence, limit=220, max_parts=3)

    assert len(parts) == 1
    assert parts[0] == fence


def test_reply_budget() -> None:
    assert reply_budget("halo", is_command=False) == (220, 2)
    assert reply_budget("tolong jelaskan detail", is_command=False) == (420, 3)
    assert reply_budget("singkat aja", is_command=False) == (220, 2)
    assert reply_budget("halo", is_command=True) == (3500, 20)


def test_reply_budget_follows_humanize_config() -> None:
    humanize = HumanizeConfig(
        normal_chars=180, normal_parts=1, detail_chars=300, detail_parts=2
    )

    assert reply_budget("halo", is_command=False, humanize=humanize) == (180, 1)
    assert reply_budget("tolong jelaskan detail", is_command=False, humanize=humanize) == (300, 2)


def test_humanize_reply_splits_sentences() -> None:
    parts = humanize_reply("Halo! Apa kabar? " + "kata " * 100, limit=120, max_parts=2)

    assert 1 <= len(parts) <= 2
    assert all(len(part) <= 121 for part in parts)


def test_person_profile_learns_individual_style(tmp_path) -> None:
    store = make_store(tmp_path)
    for _ in range(10):
        store.learn("iyaa kok gitu sih wkwk", person_id="628111")
    for _ in range(10):
        store.learn("BAIK PAK, SIAP LAPORAN NYA SAYA KIRIM SEKARANG", person_id="628222")

    short = store.person_profile("628111")
    long = store.person_profile("628222")

    assert "median" in short and "huruf kecil" in short
    assert short != long
    assert store.person_profile("628999") == ""
    store.close()


def test_profile_uses_live_stats_after_enough_samples(tmp_path) -> None:
    store = make_store(tmp_path)
    for _ in range(25):
        store.learn("iyaa kok gitu sih, aku juga bingung wkwk", chat_id="c@g.us")

    text = store.profile("c@g.us")

    assert "median" in text and "partikel:" in text
    store.close()
