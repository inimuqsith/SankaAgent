"""Kontrak harness ablasi: replay gerbang dari arsip tanpa menyentuh runtime."""

from __future__ import annotations

from datetime import datetime

from sanka.engine.ablate import classify, replay_metrics
from sanka.engine.context import ChatMessage, Quote

BOT = "sanka"


def message(
    sender: str,
    text: str,
    second: int,
    *,
    outgoing: bool = False,
    quote: Quote | None = None,
) -> ChatMessage:
    return ChatMessage(
        id=f"m{second}-{sender}",
        sender_name=sender,
        sender_id=sender,
        when=datetime(2024, 1, 1, 12, 0, second),
        text=text,
        outgoing=outgoing,
        quote=quote,
    )


def test_classify_replays_gate_and_reply_window() -> None:
    bot_quote = Quote(id="q", sender_name=BOT, sender_id="628567770837", text="tadi")
    messages = [
        message("p1", "sanka rapat jam tiga", 0),
        message(BOT, "rapatnya jam tiga", 1, outgoing=True),
        message("p1", "jam berapa ya tadi?", 2),
        message("p2", "eh tiket bola", 3),
        message(BOT, "oke", 4, outgoing=True),
        message("p3", "iya", 5, quote=bot_quote),
        message("p1", "sanka", 6),
    ]

    rows = classify(messages, bot_name=BOT)

    assert [row.message.sender_id for row in rows] == ["p1", "p1", "p2", "p3", "p1"]
    assert [row.called for row in rows] == [False, False, False, True, False]
    assert [row.replied for row in rows] == [True, False, True, False, False]


def test_replay_metrics_counts_false_positives_and_misses() -> None:
    bot_quote = Quote(id="q", sender_name=BOT, sender_id="628567770837", text="tadi")
    rows = classify(
        [
            message("p1", "sanka rapat jam tiga", 0),
            message(BOT, "rapatnya jam tiga", 1, outgoing=True),
            message("p1", "jam berapa ya tadi?", 2),
            message("p2", "eh tiket bola", 3),
            message(BOT, "oke", 4, outgoing=True),
            message("p3", "iya", 5, quote=bot_quote),
            message("p1", "sanka", 6),
        ],
        bot_name=BOT,
    )

    metrics = replay_metrics(rows)

    assert metrics == {
        "incoming": 5,
        "called": 1,
        "replied": 2,
        "false_positive": 2,
        "missed": 1,
        "unprompted_rate": 0.4,
    }


def test_last_incoming_without_reply_is_missed() -> None:
    bot_quote = Quote(id="q", sender_name=BOT, sender_id="628567770837", text="tadi")
    rows = classify([message("p1", "sanka tolong", 0, quote=bot_quote)], bot_name=BOT)

    metrics = replay_metrics(rows)

    assert metrics["incoming"] == 1
    assert metrics["replied"] == 0
    assert metrics["missed"] == 1


def test_replay_metrics_on_empty_history() -> None:
    assert replay_metrics([])["unprompted_rate"] == 0.0
