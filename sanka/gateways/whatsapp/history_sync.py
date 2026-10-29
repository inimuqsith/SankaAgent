"""History sync WhatsApp: kontak inline, nama grup, dan ingest WebMessageInfo."""

from __future__ import annotations

import hashlib
import logging

from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message
from neonize.proto.waHistorySync.WAWebProtobufsHistorySync_pb2 import HistorySync
from neonize.proto.waCompanionReg.WAWebProtobufsCompanionReg_pb2 import DeviceProps

from ...engine.context import ChatMessage
from ...engine.permissions import normalize
from . import directory
from .media import describe_media, event_time, message_text, quote_from_proto

log = logging.getLogger(__name__)

HISTORY_DAYS = 730
HISTORY_SIZE_MB = 5000
STORAGE_QUOTA_MB = 10240


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _read_varint(data: bytes, index: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, index
        shift += 7


def _replace_message_field(data: bytes, field: int, payload: bytes) -> bytes:
    """Ganti field length-delimited tanpa membuang field lain (unknown proto field)."""
    out = bytearray()
    index = 0
    found = False
    while index < len(data):
        start = index
        key, index = _read_varint(data, index)
        number, wire = key >> 3, key & 7
        if wire == 0:
            _, index = _read_varint(data, index)
        elif wire == 1:
            index += 8
        elif wire == 2:
            length, index = _read_varint(data, index)
            index += length
        elif wire == 5:
            index += 4
        else:
            raise ValueError(f"wire type tidak dikenal: {wire}")
        if number == field:
            found = True
            out += _varint((field << 3) | 2) + _varint(len(payload)) + payload
        else:
            out += data[start:index]
    if not found:
        out += _varint((field << 3) | 2) + _varint(len(payload)) + payload
    return bytes(out)


def history_props(require_full: bool) -> DeviceProps:
    """DeviceProps dengan kuota history sync; supportGroupHistory field 15."""
    config = DeviceProps.HistorySyncConfig(
        fullSyncDaysLimit=HISTORY_DAYS,
        fullSyncSizeMbLimit=HISTORY_SIZE_MB,
        storageQuotaMb=STORAGE_QUOTA_MB,
        inlineInitialPayloadInE2EeMsg=True,
        recentSyncDaysLimit=HISTORY_DAYS,
        supportCallLogHistory=False,
        supportBotUserAgentChatHistory=True,
        supportCagReactionsAndPolls=True,
        supportBizHostedMsg=True,
        supportRecentSyncChunkMessageCountTuning=True,
        supportHostedGroupMsg=True,
        supportFbidBotChatHistory=True,
        supportAddOnHistorySyncMigration=True,
        supportMessageAssociation=True,
    )
    props = DeviceProps(
        os="Neonize",
        platformType=DeviceProps.SAFARI,
        requireFullSync=require_full,
        historySyncConfig=config,
    )
    raw_config = config.SerializeToString() + b"\x78\x01"
    return DeviceProps.FromString(
        _replace_message_field(props.SerializeToString(), 5, raw_config)
    )


def _decode_ref(value) -> object | None:
    if not value:
        return None
    try:
        return Message.FromString(bytes(value))
    except Exception:
        return None


def _ingest_sync_contacts(db, sync: HistorySync) -> None:
    for mapping in sync.phoneNumberToLidMappings:
        directory.set_lid(db, mapping.lidJID, mapping.pnJID)
    for push in sync.pushnames:
        directory._upsert_contact(db, push.ID, "", push.pushname)
    for contact in sync.inlineContacts:
        name = contact.fullName or contact.firstName
        directory._upsert_contact(db, contact.pnJID, name, "")
        directory._upsert_contact(db, contact.lidJID, name, "")
    directory.refresh_sender_names(db)


def _sync_item(db, web, fallback_chat: str, bot_name: str, fallback_ts: int = 0):
    key = web.key
    chat_id = key.remoteJID or fallback_chat
    if not chat_id or not web.message.ListFields():
        return None
    msg_id = key.ID or f"hist-{hashlib.sha1(web.SerializeToString()).hexdigest()[:24]}"
    text = message_text(web.message)
    media = describe_media(web.message, msg_id)
    quote = quote_from_proto(web.message, msg_id)
    if not text.strip() and media is None and quote is None:
        return None
    from_me = bool(key.fromMe)
    if from_me:
        sender_jid = ""
    elif chat_id.endswith("@g.us"):
        sender_jid = key.participant or web.participant or ""
    else:
        sender_jid = chat_id
    sender_id = normalize(sender_jid) if sender_jid else ""
    sender = (
        directory.contact_name(db, sender_jid) if sender_jid else ""
    ) or (web.pushName or "").strip() or (bot_name if from_me else sender_id)
    return (
        chat_id,
        ChatMessage(
            id=msg_id,
            sender_name=sender,
            sender_id=sender_id,
            when=event_time(int(web.messageTimestamp or fallback_ts or 0)),
            text=text,
            media=media,
            quote=quote,
            outgoing=from_me,
            sender_jid=sender_jid,
        ),
    )


def ingest_sync(db, history, sync: HistorySync, bot_name: str) -> tuple[int, set[str], bool]:
    """Simpan satu event HistorySync; kembalikan (jumlah, chat, selesai)."""
    if sync is None:
        return 0, set(), False
    _ingest_sync_contacts(db, sync)
    chat_ids: set[str] = set()
    total = 0
    batch: list[tuple[str, ChatMessage]] = []
    for conversation in sync.conversations:
        fallback_chat = conversation.ID or ""
        fallback_ts = int(conversation.conversationTimestamp or 0)
        name = (conversation.name or conversation.displayName or "").strip()
        if name and fallback_chat:
            directory.set_chat_name(db, fallback_chat, name)
        for item in conversation.messages:
            parsed = _sync_item(db, item.message, fallback_chat, bot_name, fallback_ts)
            if parsed is None:
                continue
            chat_id, message = parsed
            batch.append((chat_id, message))
            chat_ids.add(chat_id)
            if len(batch) >= 500:
                total += history.add_many(batch)
                batch.clear()
    if batch:
        total += history.add_many(batch)
    done = bool(
        sync.syncType in (HistorySync.FULL, HistorySync.INITIAL_BOOTSTRAP) and sync.progress >= 100
    )
    if done:
        mark_full_sync(db)
    return total, chat_ids, done


def needs_full_sync(db) -> bool:
    from sqlalchemy import select

    from ...engine.schema import state

    found = db.fetch_one(select(state.c.value).where(state.c.key == "wa_full_sync_done"))
    return found is None or str(found["value"]) != "1"


def mark_full_sync(db) -> None:
    import time

    from ...engine.schema import state

    with db.connect() as conn:
        db.upsert(
            conn,
            state,
            [{"key": "wa_full_sync_done", "value": "1", "updated_at": time.time()}],
            ["key"],
            update={"value": "1", "updated_at": time.time()},
        )
