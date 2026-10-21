"""Kontrak arsip: SQLite lintas restart, pencarian lama, reset, dan isolasi chat."""

from __future__ import annotations

import asyncio
import sqlite3
import stat
from datetime import datetime, timedelta
from pathlib import Path

from neonize.proto.waCommon.WACommon_pb2 import MessageKey
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message
from neonize.proto.waHistorySync.WAWebProtobufsHistorySync_pb2 import (
    Conversation,
    HistorySync,
    HistorySyncMsg,
    InlineContact,
)
from neonize.proto.waWeb.WAWebProtobufsWeb_pb2 import WebMessageInfo

from sanka.chat import ChatMessage, Sessions
from sanka.config import Config, LLMConfig, RoleConfig, SandboxConfig, WhatsAppConfig
from sanka.history import HistoryStore, history_props
from sanka.tools import REGISTRY as TOOLS
from sanka.tools import ToolContext, execute

CHAT = "120363000000000001@g.us"
OTHER = "120363000000000002@g.us"


def make_config(root: Path) -> Config:
    return Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=root / "workspace",
        whatsapp=WhatsAppConfig(session_db=root / "wa.db", history_db=root / "history.db"),
        roles={"owner": RoleConfig("owner")},
        default_role="owner",
        sandbox=SandboxConfig(),
        mcp={},
    )


def chat_message(message_id: str, text: str, when: datetime) -> ChatMessage:
    return ChatMessage(
        id=message_id,
        sender_name="Budi",
        sender_id="628222222222",
        when=when,
        text=text,
    )


def web_message(message_id: str, text: str, stamp: int, chat: str = CHAT) -> WebMessageInfo:
    return WebMessageInfo(
        key=MessageKey(
            remoteJID=chat,
            ID=message_id,
            fromMe=False,
            participant="628222222222@s.whatsapp.net",
        ),
        message=Message(conversation=text),
        messageTimestamp=stamp,
        pushName="Budi",
    )


def sync_with(items: list[WebMessageInfo]) -> HistorySync:
    return HistorySync(
        syncType=HistorySync.RECENT,
        conversations=[
            Conversation(
                ID=CHAT,
                name="Grup Uji",
                messages=[HistorySyncMsg(message=item) for item in items],
            )
        ],
    )


def test_restart_reloads_window_and_old_quote(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = HistoryStore(config.whatsapp.history_db, config.workspace)
    sessions = Sessions(store=store)
    base = datetime.now() - timedelta(minutes=20)
    for index in range(25):
        sessions.append(CHAT, chat_message(f"m{index}", f"pesan {index}", base + timedelta(seconds=index)))

    assert store.message_count() == 25
    assert [item.id for item in sessions.get(CHAT)][0] == "m5"
    store.close()

    reopened = HistoryStore(config.whatsapp.history_db, config.workspace)
    loaded = Sessions(store=reopened)
    window = loaded.get(CHAT)
    assert len(window) == 20
    assert window[0].id == "m5"
    assert loaded.find(CHAT, "m0").text == "pesan 0"
    reopened.close()


def test_history_sync_is_searchable_beyond_window(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = HistoryStore(config.whatsapp.history_db, config.workspace)
    base = int((datetime.now() - timedelta(hours=1)).timestamp())
    items = [web_message("h0", "rahasia lama", base)]
    items.extend(web_message(f"h{i}", f"biasa {i}", base + i) for i in range(1, 25))
    count, chats, _ = store.ingest_sync(sync_with(items), "sanka")

    assert count == 25
    assert chats == {CHAT}
    assert store.chat_name(CHAT) == "Grup Uji"
    sessions = Sessions(store=store)
    assert len(sessions.get(CHAT)) == 20
    assert "rahasia lama" not in "\n".join(item.text for item in sessions.get(CHAT))

    hits = store.search(CHAT, "rahasia")
    assert [item.text for item in hits] == ["rahasia lama"]
    assert hits[0].sender_name == "Budi"
    assert hits[0].sender_id == "628222222222"
    store.close()


def test_reset_clears_window_but_keeps_archive(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = HistoryStore(config.whatsapp.history_db, config.workspace)
    sessions = Sessions(store=store)
    sessions.append(CHAT, chat_message("old", "fakta lama", datetime.now() - timedelta(minutes=5)))
    sessions.reset(CHAT)
    assert sessions.get(CHAT) == []

    sessions.append(CHAT, chat_message("new", "pesan baru", datetime.now()))
    assert [item.id for item in sessions.get(CHAT)] == ["new"]
    assert [item.text for item in store.search(CHAT, "fakta")] == ["fakta lama"]
    assert sessions.find(CHAT, "old").text == "fakta lama"
    store.close()


def test_history_tool_is_scoped_to_current_chat(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = HistoryStore(config.whatsapp.history_db, config.workspace)
    store.set_chat_name(CHAT, "Grup Uji")
    store.add(CHAT, chat_message("a", "fakta grup ini", datetime.now()))
    store.add(OTHER, chat_message("b", "fakta grup lain", datetime.now()))
    role = config.roles["owner"]
    ctx = ToolContext(config, role, ("chat_history",), chat_id=CHAT, history=store)
    specs = {handler.name: handler for handler in TOOLS.by_name.values()}
    output = asyncio.run(execute(specs, "chat_history", {"query": "fakta"}, ctx))

    assert 'Arsip "Grup Uji"' in output
    assert "fakta grup ini" in output
    assert "fakta grup lain" not in output
    store.close()


def test_history_sync_inline_contact_names_senders(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = HistoryStore(config.whatsapp.history_db, config.workspace)
    web = web_message("c1", "halo dari kontak", int(datetime.now().timestamp()))
    web.key.participant = "999@lid"
    web.pushName = ""
    sync = HistorySync(
        syncType=HistorySync.RECENT,
        inlineContacts=[
            InlineContact(
                pnJID="628999999999@s.whatsapp.net",
                lidJID="999@lid",
                fullName="Budi Kontak",
            )
        ],
        conversations=[Conversation(ID=CHAT, messages=[HistorySyncMsg(message=web)])],
    )

    store.ingest_sync(sync, "sanka")

    stored = store.find(CHAT, "c1")
    assert stored is not None and stored.sender_name == "Budi Kontak"
    store.close()


def test_session_contacts_refresh_old_senders(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    history = HistoryStore(config.whatsapp.history_db, config.workspace)
    history.add(
        CHAT,
        ChatMessage(
            id="old",
            sender_name="999",
            sender_id="999",
            sender_jid="999@lid",
            when=datetime.now(),
            text="pesan lama",
        ),
    )
    history.close()

    session = tmp_path / "session.db"
    source = sqlite3.connect(session)
    source.execute(
        "CREATE TABLE whatsmeow_contacts "
        "(their_jid TEXT, first_name TEXT, full_name TEXT, push_name TEXT)"
    )
    source.execute("CREATE TABLE whatsmeow_lid_map (lid TEXT, pn TEXT)")
    source.execute(
        "INSERT INTO whatsmeow_contacts VALUES (?, ?, ?, ?)",
        ("628999999999@s.whatsapp.net", "", "Budi Kontak", "Budi"),
    )
    source.execute(
        "INSERT INTO whatsmeow_lid_map VALUES (?, ?)",
        ("999@lid", "628999999999@s.whatsapp.net"),
    )
    source.commit()
    source.close()

    reopened = HistoryStore(config.whatsapp.history_db, config.workspace, session)
    assert reopened.contact_name("999@lid") == "Budi Kontak"
    assert reopened.find(CHAT, "old").sender_name == "Budi Kontak"
    reopened.close()


def test_existing_chats_table_migrates_name_column(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    path = config.whatsapp.history_db
    legacy = sqlite3.connect(path)
    legacy.execute("CREATE TABLE chats (chat_id TEXT PRIMARY KEY, reset_ts REAL NOT NULL DEFAULT 0)")
    legacy.commit()
    legacy.close()

    store = HistoryStore(path, config.workspace)
    store.set_chat_name(CHAT, "Grup Lama")
    assert store.chat_name(CHAT) == "Grup Lama"
    store.reset(CHAT)
    assert store.chat_name(CHAT) == "Grup Lama"  # reset tidak menghapus nama
    store.close()


def test_archive_permissions_and_group_history_flag(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = HistoryStore(config.whatsapp.history_db, config.workspace)
    mode = stat.S_IMODE(config.whatsapp.history_db.stat().st_mode)
    assert mode == 0o600
    assert stat.S_IMODE(config.whatsapp.history_db.parent.stat().st_mode) & 0o077 == 0
    store.close()

    props = history_props(True)
    assert props.requireFullSync is True
    assert props.historySyncConfig.fullSyncDaysLimit == 730
    assert b"\x78\x01" in props.historySyncConfig.SerializeToString()
