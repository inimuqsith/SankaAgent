"""Kontrak arsip Database: upsert kaya, jendela reset, kutipan, dan seed."""

from __future__ import annotations

from datetime import datetime

from sanka.engine.context import ChatMessage, Media, Quote
from sanka.engine.stores.history import HistoryStore
from support import database


def message(
    mid: str,
    text: str = "",
    *,
    when: datetime | None = None,
    sender_name: str = "Budi",
    sender_jid: str = "628222@s.whatsapp.net",
    media: Media | None = None,
    quote: Quote | None = None,
    outgoing: bool = False,
) -> ChatMessage:
    return ChatMessage(
        id=mid,
        sender_name=sender_name,
        sender_id="628222",
        when=when or datetime(2026, 1, 1, 10),
        text=text,
        media=media,
        quote=quote,
        outgoing=outgoing,
        sender_jid=sender_jid,
    )


def test_recent_order_and_find(tmp_path) -> None:
    store = HistoryStore(database(tmp_path))
    store.add("chat", message("a", "satu", when=datetime(2026, 1, 1, 10)))
    store.add("chat", message("b", "dua", when=datetime(2026, 1, 1, 11)))

    assert [item.id for item in store.recent("chat", 10)] == ["a", "b"]
    assert store.find("chat", "a").text == "satu"
    assert store.find("chat", "hilang") is None


def test_upsert_keeps_richer_fields(tmp_path) -> None:
    store = HistoryStore(database(tmp_path))
    media = Media(kind="image", mimetype="image/png", filename="a.png", size=3, proto=None, ref=b"abc")
    store.add("chat", message("a", ""))
    store.add(
        "chat",
        message("a", "teks asli", media=media, quote=Quote("q", "Ani", "9", "halo")),
    )
    store.add("chat", message("a", ""))

    item = store.find("chat", "a")
    assert item.text == "teks asli"
    assert item.media is not None and item.media.filename == "a.png"
    assert item.media.ref == b"abc"
    assert item.quote is not None and item.quote.text == "halo"
    assert store.message_count() == 1


def test_reset_clears_window_but_keeps_archive(tmp_path) -> None:
    store = HistoryStore(database(tmp_path))
    store.add("chat", message("a", "lama", when=datetime(2026, 1, 1, 10)))
    store.reset("chat")
    store.add("chat", message("b", "baru", when=datetime.now()))

    assert [item.id for item in store.recent("chat", 10)] == ["b"]
    assert store.message_count() == 2
    assert [item.id for item in store.search("chat", "lama")] == ["a"]


def test_chat_name_and_recent_chats(tmp_path) -> None:
    store = HistoryStore(database(tmp_path))
    store.add("a@g.us", message("a", "x", when=datetime(2026, 1, 1, 10)))
    store.add("b@s.whatsapp.net", message("b", "y", when=datetime(2026, 1, 1, 11)))
    store.set_chat_name("a@g.us", "  Grup   Logistik ")
    store.set_chat_name("a@g.us", "")

    assert store.chat_name("a@g.us") == "Grup Logistik"
    assert store.chat_name("b@s.whatsapp.net") == ""
    assert store.recent_chats(10) == ["b@s.whatsapp.net", "a@g.us"]


def test_people_and_style_seed_skip_outgoing(tmp_path) -> None:
    store = HistoryStore(database(tmp_path))
    store.add("chat", message("a", "halo", when=datetime.now(), sender_name="Ani"))
    store.add("chat", message("b", "diam", when=datetime.now(), outgoing=True))

    people = store.people_seed()
    assert [row["text"] for row in people] == ["halo"]
    assert people[0]["sender_jid"] == "628222@s.whatsapp.net"
    assert [row["text"] for row in store.style_sample()] == ["halo"]
