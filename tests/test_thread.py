"""Kontrak situasi percakapan: irisan kata kunci, ucapan lanjutan, orang, dan kedaluwarsa."""

from __future__ import annotations

from sanka.chat import followup_utterance, keyword_set
from sanka.thread import continues, open_thread


def test_open_thread_collects_keywords_from_text_and_reply() -> None:
    state = open_thread("p1", "Ana", "rapat jam tiga", "rapatnya jam tiga", seconds=900, now=100.0)

    assert state["person"] == "p1"
    assert state["name"] == "Ana"
    assert state["until"] == 1000.0
    assert state["keywords"] == {"rapat", "rapatnya", "tiga"}


def test_open_thread_without_seconds_or_person_is_empty() -> None:
    assert open_thread("p1", "Ana", "rapat", "", seconds=0, now=1.0) == {}
    assert open_thread("", "Ana", "rapat", "", seconds=900, now=1.0) == {}


def test_continues_on_keyword_overlap() -> None:
    state = open_thread("p1", "Ana", "rapat jam tiga", "", seconds=900, now=100.0)

    assert continues(state, person="p1", text="tiga orang datang", now=200.0) is True


def test_continues_on_followup_utterance() -> None:
    state = open_thread("p1", "Ana", "tiket bola", "", seconds=900, now=100.0)

    assert followup_utterance("iya?") is True
    assert continues(state, person="p1", text="iya?", now=200.0) is True


def test_continues_rejects_other_person_or_expired_state() -> None:
    state = open_thread("p1", "Ana", "rapat jam tiga", "", seconds=900, now=100.0)

    assert continues(state, person="p2", text="rapat jam tiga", now=200.0) is False
    assert continues(state, person="p1", text="rapat jam tiga", now=1001.0) is False
    assert continues(state, person="p1", text="   ", now=200.0) is False
    assert continues(None, person="p1", text="rapat jam tiga", now=200.0) is False


def test_keyword_set_skips_stopwords_and_short_words() -> None:
    assert keyword_set("yang aku mau itu kopi enak") == {"kopi", "enak"}
