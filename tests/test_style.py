"""Kontrak gaya: profil acuan, agregat belajar, bubble pendek, dan batas balasan."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from sanka.engine.config import StyleConfig
from sanka.engine.reply import FENCE, clean_prose, human_chunks


def humanize_reply(text: str, *, max_parts: int) -> list[str]:
    pieces = [
        piece if piece.startswith("```") else clean_prose(piece)
        for piece in FENCE.split(str(text or ""))
    ]
    return human_chunks("".join(pieces), max_parts=max_parts)
from sanka.engine.schema import state
from sanka.engine.stores.style import StyleStore
from support import database


def make_store(tmp_path: Path) -> StyleStore:
    return StyleStore(database(tmp_path), StyleConfig(enabled=True))


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
    db = database(tmp_path)
    store = StyleStore(db, StyleConfig(enabled=True))
    store.learn("wkwk aja sih", chat_id="c@g.us")
    store.close()

    stored = db.scalar(select(state.c.value).where(state.c.key == "style"))
    assert stored is not None
    assert "wkwk aja sih" not in stored
    reloaded = StyleStore(database(tmp_path), StyleConfig(enabled=True))
    assert reloaded.stats()["global"] == 1
    assert reloaded.stats()["chats"] == {"c@g.us": 1}
    reloaded.close()


def test_human_chunks_never_cuts_content() -> None:
    body = "a" * 700

    parts = human_chunks(body, max_parts=3)

    assert parts == [body]  # tanpa baris kosong: satu bubble, tanpa elipsis
    assert "…" not in "".join(parts)


def test_human_chunks_keeps_fence() -> None:
    fence = "```\n" + "b" * 400 + "\n```"

    parts = human_chunks(fence, max_parts=3)

    assert len(parts) == 1
    assert parts[0] == fence


def test_bubbles_follow_explicit_marker_from_model() -> None:
    text = "bohong apanya sih 😭\n---\nmasa aku ngambek terus, nanti kamu bosen loh"

    parts = humanize_reply(text, max_parts=3)

    assert parts == ["bohong apanya sih 😭", "masa aku ngambek terus, nanti kamu bosen loh"]


def test_blank_lines_do_not_split_messages() -> None:
    # Model suka memberi baris kosong sebelum daftar; itu bukan perintah memecah pesan.
    text = "Alur kurasi foto\n\n1. Kumpulkan\n2. Sortir\n\nHafal: kumpul -> pilih -> setor"

    assert humanize_reply(text, max_parts=3) == [text]


def test_marker_inside_fence_does_not_split() -> None:
    text = "contoh konfigurasi:\n```\na: 1\n---\nb: 2\n```\nselesai"

    parts = humanize_reply(text, max_parts=3)

    assert len(parts) == 1


def test_inline_marker_splits_like_a_line_marker() -> None:
    parts = humanize_reply("sedihnya karena apa? --- aku di sini kalau mau cerita", max_parts=3)

    assert parts == ["sedihnya karena apa?", "aku di sini kalau mau cerita"]


def test_inline_marker_inside_fence_stays_one_bubble() -> None:
    text = "```\nsatu --- dua\n```"

    parts = humanize_reply(text, max_parts=3)

    assert parts == ["```\nsatu --- dua\n```"]


def test_structured_list_stays_one_message() -> None:
    text = (
        "Alur kurasi foto HB 2026, versi simpel\n"
        "1. KUMPULKAN — semua foto masuk Drive\n"
        "2. SORTIR BARENG — pilih bareng kelompok\n"
        "3. SETOR — isi folder SORTIR disetor ke kurator"
    )

    parts = humanize_reply(text, max_parts=3)

    assert len(parts) == 1
    assert "3. SETOR" in parts[0]


def test_extra_bubbles_are_merged_not_dropped() -> None:
    text = "satu\n---\ndua\n---\ntiga\n---\nempat"

    parts = humanize_reply(text, max_parts=2)

    assert parts[0] == "satu"
    assert parts[1] == "dua\n\ntiga\n\nempat"  # digabung, bukan dipotong


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


def test_casual_blank_line_becomes_two_bubbles() -> None:
    # Kasus produksi: model menulis dua kalimat obrolan dengan baris kosong, tanpa penanda `---`.
    text = "iya, lumayan. udah nggak berat lagi rasanya 🙂\n\nkamu sendiri gimana? kelar juga kan kerjanya?"

    parts = humanize_reply(text, max_parts=3)

    assert parts == [
        "iya, lumayan. udah nggak berat lagi rasanya 🙂",
        "kamu sendiri gimana? kelar juga kan kerjanya?",
    ]


def test_long_paragraphs_are_not_shredded() -> None:
    text = ("a " * 200).strip() + "\n\n" + ("b " * 200).strip()

    assert len(humanize_reply(text, max_parts=3)) == 1  # terlalu panjang: biar utuh satu pesan


def test_explicit_marker_still_wins() -> None:
    text = "satu kalimat\n---\nkalimat kedua\n\nmasih kalimat kedua"

    parts = humanize_reply(text, max_parts=3)

    assert parts == ["satu kalimat", "kalimat kedua\n\nmasih kalimat kedua"]


def test_tool_call_markup_never_becomes_a_bubble() -> None:
    text = '<tool_call>{"name":"memory","arguments":"{}"}</tool_call>'

    assert human_chunks(text, max_parts=3) == []

    mixed = 'oke, aku catat ya\n<tool_call>{"name":"memory"}</tool_call>'
    assert humanize_reply(mixed, max_parts=3) == ["oke, aku catat ya"]
