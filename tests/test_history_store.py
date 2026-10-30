"""Kontrak arsip Database: upsert kaya, jendela reset, kutipan, dan seed."""

from __future__ import annotations

from datetime import datetime

from sanka.engine.context import ChatMessage, Media, Quote
from sanka.engine.schema import messages
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
    author: str = "unknown",
    visibility: int = 2,
    sender_id: str = "628222",
    view_once: bool = False,
    unreadable: bool = False,
) -> ChatMessage:
    return ChatMessage(
        id=mid,
        sender_name=sender_name,
        sender_id=sender_id,
        when=when or datetime(2026, 1, 1, 10),
        text=text,
        media=media,
        quote=quote,
        outgoing=outgoing,
        sender_jid=sender_jid,
        author=author,
        visibility=visibility,
        view_once=view_once,
        unreadable=unreadable,
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


def test_author_and_visibility_roundtrip(tmp_path) -> None:
    store = HistoryStore(database(tmp_path))
    store.add("chat", message("a", "halo", author="manual_owner", visibility=3))

    item = store.find("chat", "a")

    assert item is not None
    assert item.author == "manual_owner"
    assert item.visibility == 3


def test_merge_never_downgrades_author_or_visibility(tmp_path) -> None:
    store = HistoryStore(database(tmp_path))
    store.add("chat", message("a", "halo", sender_name="Owner", sender_id="628567770837"))
    store.add(
        "chat",
        message("a", "", sender_name="Owner manual", author="manual_owner", visibility=3),
    )
    store.add(
        "chat",
        message("a", "", sender_name="sanka", sender_id="628567770837", author="bot"),
    )
    store.add("chat", message("a", "", sender_name="", sender_id="", author="unknown"))
    store.add("chat", message("a", "", sender_name="x", visibility=2))

    item = store.find("chat", "a")

    assert item is not None
    assert item.author == "bot"
    assert item.sender_name == "sanka"
    assert item.sender_id == "628567770837"
    assert item.visibility == 3
    assert item.text == "halo"


def test_visibility_only_rises_with_set_visibility(tmp_path) -> None:
    store = HistoryStore(database(tmp_path))
    store.add("chat", message("a", "satu"))
    store.add("chat", message("b", "dua"))

    assert store.set_visibility("chat", ("a", "b", "hilang", ""), 3) == 2
    assert store.set_visibility("chat", ("a",), 1) == 0

    assert store.find("chat", "a").visibility == 3
    assert store.find("chat", "b").visibility == 3


def test_unknown_author_values_fall_back_to_unknown(tmp_path) -> None:
    store = HistoryStore(database(tmp_path))
    store.add("chat", message("a", "x"))
    with store.db.connect() as conn:
        conn.execute(messages.update().values(author="weird", visibility=9))

    item = store.find("chat", "a")

    assert item is not None
    assert item.author == "unknown"
    assert item.visibility == 4


def test_mark_deleted_keeps_the_archived_text(tmp_path) -> None:
    store = HistoryStore(database(tmp_path))
    store.add("chat", message("a", "isi lama"))

    assert store.mark_deleted("chat", "a", "628999@s.whatsapp.net", "Ani") is True
    item = store.find("chat", "a")

    assert item is not None
    assert item.deleted is True
    assert item.deleted_admin is True
    assert item.deleted_by == "Ani"
    assert item.text == "isi lama"
    assert store.mark_deleted("chat", "a", "628999@s.whatsapp.net", "Ani") is False


def test_author_deletion_is_not_admin_and_later_upsert_keeps_the_flag(tmp_path) -> None:
    store = HistoryStore(database(tmp_path))
    store.add("chat", message("a", "isi"))
    store.mark_deleted("chat", "a", "628222@s.whatsapp.net")
    store.add("chat", message("a", ""))

    item = store.find("chat", "a")

    assert item is not None
    assert item.deleted is True
    assert item.deleted_admin is False
    assert item.text == "isi"


def test_view_once_and_unreadable_flags_roundtrip(tmp_path) -> None:
    store = HistoryStore(database(tmp_path))
    store.add("chat", message("a", "sekali", view_once=True))
    store.add("chat", message("b", "", unreadable=True))

    once = store.find("chat", "a")
    blank = store.find("chat", "b")

    assert once.view_once is True and once.unreadable is False
    assert blank.unreadable is True and blank.view_once is False


def test_real_message_merge_replaces_the_unreadable_placeholder(tmp_path) -> None:
    store = HistoryStore(database(tmp_path))
    store.add("chat", message("a", "", unreadable=True))
    assert store.find("chat", "a").unreadable is True

    store.add("chat", message("a", "isi asli", view_once=True))
    item = store.find("chat", "a")

    assert item.text == "isi asli"
    assert item.view_once is True
    assert item.unreadable is False
