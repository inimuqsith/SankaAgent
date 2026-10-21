"""Arsip pesan WhatsApp per chat di SQLite lokal; sumber konteks lintas restart."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from google.protobuf.message import DecodeError
from neonize.proto.waCompanionReg.WAWebProtobufsCompanionReg_pb2 import DeviceProps
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message
from neonize.proto.waHistorySync.WAWebProtobufsHistorySync_pb2 import HistorySync as HistorySyncProto

from .chat import ChatMessage, Media, Quote, describe_media, digits, event_time, message_text, quote_from_proto

log = logging.getLogger(__name__)

HISTORY_DAYS = 730
HISTORY_SIZE_MB = 5000
STORAGE_QUOTA_MB = 10240
SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    chat_id TEXT NOT NULL,
    msg_id TEXT NOT NULL,
    ts REAL NOT NULL,
    sender_id TEXT NOT NULL DEFAULT '',
    sender_name TEXT NOT NULL DEFAULT '',
    sender_jid TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    outgoing INTEGER NOT NULL DEFAULT 0,
    data TEXT NOT NULL DEFAULT '{}',
    media_proto BLOB,
    quote_proto BLOB,
    UNIQUE(chat_id, msg_id)
);
CREATE INDEX IF NOT EXISTS messages_chat_ts ON messages(chat_id, ts, id);
CREATE TABLE IF NOT EXISTS chats (
    chat_id TEXT PRIMARY KEY,
    reset_ts REAL NOT NULL DEFAULT 0,
    name TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
UPSERT = """
INSERT INTO messages(
    chat_id, msg_id, ts, sender_id, sender_name, sender_jid, text, outgoing, data, media_proto, quote_proto
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(chat_id, msg_id) DO UPDATE SET
    ts=excluded.ts,
    sender_id=excluded.sender_id,
    sender_name=CASE WHEN excluded.sender_name<>'' THEN excluded.sender_name ELSE messages.sender_name END,
    sender_jid=CASE WHEN excluded.sender_jid<>'' THEN excluded.sender_jid ELSE messages.sender_jid END,
    text=CASE WHEN excluded.text<>'' THEN excluded.text ELSE messages.text END,
    outgoing=excluded.outgoing,
    data=CASE WHEN excluded.data='{}' THEN messages.data ELSE excluded.data END,
    media_proto=COALESCE(excluded.media_proto, messages.media_proto),
    quote_proto=COALESCE(excluded.quote_proto, messages.quote_proto)
"""
TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text, sender_name) VALUES (new.id, new.text, new.sender_name);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text, sender_name) VALUES ('delete', old.id, old.text, old.sender_name);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text, sender_name) VALUES ('delete', old.id, old.text, old.sender_name);
    INSERT INTO messages_fts(rowid, text, sender_name) VALUES (new.id, new.text, new.sender_name);
END;
"""


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
    """Ganti field length-delimited tanpa membuang field lain (untuk unknown proto field)."""
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
    """DeviceProps dengan kuota history sync; supportGroupHistory disisipkan sebagai field 15.

    Proto Python Neonize 0.5.2 belum punya field itu, sedangkan binding Go-nya punya.
    """
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
    raw_config = config.SerializeToString() + b"\x78\x01"  # supportGroupHistory = true
    return DeviceProps.FromString(_replace_message_field(props.SerializeToString(), 5, raw_config))


def _proto_bytes(value) -> bytes | None:
    if value is None:
        return None
    try:
        return value.SerializeToString()
    except (AttributeError, TypeError):
        return None


def _load_proto(value: bytes | None):
    if not value:
        return None
    try:
        return Message.FromString(value)
    except (DecodeError, ValueError):
        return None


def _media_json(media: Media) -> dict:
    return {
        "kind": media.kind,
        "mimetype": media.mimetype,
        "filename": media.filename,
        "size": int(media.size or 0),
    }


def _media_from(value, proto) -> Media | None:
    if not isinstance(value, dict):
        return None
    return Media(
        kind=str(value.get("kind") or ""),
        mimetype=str(value.get("mimetype") or ""),
        filename=str(value.get("filename") or ""),
        size=int(value.get("size") or 0),
        proto=_load_proto(proto),
    )


def _data_json(message: ChatMessage) -> str:
    data: dict = {}
    if message.media is not None:
        data["media"] = _media_json(message.media)
    if message.quote is not None:
        quote = {
            "id": message.quote.id,
            "sender_name": message.quote.sender_name,
            "sender_id": message.quote.sender_id,
            "text": message.quote.text,
            "when": message.quote.when.timestamp() if message.quote.when else None,
        }
        if message.quote.media is not None:
            quote["media"] = _media_json(message.quote.media)
        data["quote"] = quote
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _fts_query(query: str) -> str:
    return " AND ".join(f'"{word}"*' for word in re.findall(r"\w+", query, re.UNICODE))


def _like_terms(query: str) -> list[str]:
    return re.findall(r"\w+", query, re.UNICODE) or [query.strip()]


class HistoryStore:
    """SQLite lokal: satu baris per (chat, message id), FTS5 untuk pencarian lama."""

    def __init__(self, path: Path | str, workspace: Path):
        self.path = Path(path).expanduser()
        self.workspace = Path(workspace)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._fts = False
        self._open()

    def _open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            self.path.parent.chmod(0o700)
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA secure_delete=ON")
        conn.executescript(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(chats)")}
        if "name" not in columns:
            conn.execute("ALTER TABLE chats ADD COLUMN name TEXT NOT NULL DEFAULT ''")
        self._conn = conn
        self._setup_fts()
        with suppress(OSError):
            self.path.chmod(0o600)

    def _setup_fts(self) -> None:
        conn = self._require()
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5("
                "text, sender_name, content='messages', content_rowid='id', "
                "tokenize='unicode61 remove_diacritics 2')"
            )
        except sqlite3.OperationalError:
            self._fts = False
            log.warning("FTS5 tidak tersedia; pencarian riwayat memakai LIKE")
            return
        self._fts = True
        conn.executescript(TRIGGERS)
        ready = conn.execute("SELECT value FROM meta WHERE key='fts_ready'").fetchone()
        if ready is None:
            conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('fts_ready', '1')")
        conn.commit()

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("riwayat sudah ditutup")
        return self._conn

    def _row(self, chat_id: str, message: ChatMessage) -> tuple:
        media = message.media
        quote = message.quote
        return (
            chat_id,
            message.id,
            message.when.timestamp(),
            message.sender_id,
            message.sender_name,
            message.sender_jid,
            message.text,
            int(bool(message.outgoing)),
            _data_json(message),
            _proto_bytes(media.proto) if media is not None else None,
            _proto_bytes(quote.media.proto) if quote is not None and quote.media is not None else None,
        )

    def add(self, chat_id: str, message: ChatMessage) -> None:
        with self._lock:
            self._require().execute(UPSERT, self._row(chat_id, message))
            self._require().commit()

    def add_many(self, items: list[tuple[str, ChatMessage]]) -> int:
        if not items:
            return 0
        with self._lock:
            self._require().executemany(UPSERT, [self._row(chat_id, message) for chat_id, message in items])
            self._require().commit()
        return len(items)

    def _decode(self, row: sqlite3.Row) -> ChatMessage:
        try:
            data = json.loads(row["data"] or "{}")
        except (TypeError, ValueError):
            data = {}
        quote_data = data.get("quote") if isinstance(data, dict) else None
        quote = None
        if isinstance(quote_data, dict):
            when = quote_data.get("when")
            quote = Quote(
                id=str(quote_data.get("id") or ""),
                sender_name=str(quote_data.get("sender_name") or ""),
                sender_id=str(quote_data.get("sender_id") or ""),
                text=str(quote_data.get("text") or ""),
                media=_media_from(quote_data.get("media"), row["quote_proto"]),
                when=datetime.fromtimestamp(float(when)) if when else None,
            )
        return ChatMessage(
            id=row["msg_id"],
            sender_name=row["sender_name"],
            sender_id=row["sender_id"],
            when=datetime.fromtimestamp(float(row["ts"])),
            text=row["text"],
            media=_media_from(data.get("media") if isinstance(data, dict) else None, row["media_proto"]),
            quote=quote,
            outgoing=bool(row["outgoing"]),
            sender_jid=row["sender_jid"],
        )

    def _reset_ts(self, chat_id: str) -> float:
        row = self._require().execute("SELECT reset_ts FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
        return float(row["reset_ts"]) if row else 0.0

    def set_chat_name(self, chat_id: str, name: str) -> None:
        if not chat_id:
            return
        value = " ".join(str(name or "").split())[:200]
        if not value:
            return
        with self._lock:
            self._require().execute(
                "INSERT INTO chats(chat_id, name) VALUES(?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET name=excluded.name",
                (chat_id, value),
            )
            self._require().commit()

    def chat_name(self, chat_id: str) -> str:
        if not chat_id:
            return ""
        with self._lock:
            row = self._require().execute("SELECT name FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
            return str(row["name"]) if row else ""

    def recent(self, chat_id: str, limit: int) -> list[ChatMessage]:
        with self._lock:
            reset = self._reset_ts(chat_id)
            rows = self._require().execute(
                "SELECT * FROM messages WHERE chat_id=? AND ts>=? ORDER BY ts DESC, id DESC LIMIT ?",
                (chat_id, reset, max(1, int(limit))),
            ).fetchall()
            return [self._decode(row) for row in reversed(rows)]

    def find(self, chat_id: str, msg_id: str) -> ChatMessage | None:
        if not msg_id:
            return None
        with self._lock:
            row = self._require().execute(
                "SELECT * FROM messages WHERE chat_id=? AND msg_id=? ORDER BY id DESC LIMIT 1",
                (chat_id, msg_id),
            ).fetchone()
            return self._decode(row) if row else None

    def search(
        self,
        chat_id: str,
        query: str = "",
        *,
        before: float | None = None,
        after: float | None = None,
        limit: int = 20,
    ) -> list[ChatMessage]:
        limit = max(1, min(int(limit or 20), 50))
        clauses = ["messages.chat_id=?"]
        args: list = [chat_id]
        if after is not None:
            clauses.append("messages.ts>=?")
            args.append(float(after))
        if before is not None:
            clauses.append("messages.ts<=?")
            args.append(float(before))
        with self._lock:
            if query.strip():
                match = _fts_query(query)
                if match and self._fts:
                    sql = (
                        "SELECT messages.* FROM messages JOIN messages_fts "
                        "ON messages_fts.rowid=messages.id WHERE "
                        + " AND ".join([*clauses, "messages_fts MATCH ?"])
                        + " ORDER BY messages.ts DESC, messages.id DESC LIMIT ?"
                    )
                    try:
                        rows = self._require().execute(sql, [*args, match, limit]).fetchall()
                        return [self._decode(row) for row in rows]
                    except sqlite3.OperationalError:
                        log.debug("query FTS gagal; fallback LIKE", exc_info=True)
                for term in _like_terms(query):
                    clauses.append("(messages.text LIKE ? OR messages.sender_name LIKE ?)")
                    args.extend([f"%{term}%", f"%{term}%"])
            sql = (
                "SELECT messages.* FROM messages WHERE "
                + " AND ".join(clauses)
                + " ORDER BY messages.ts DESC, messages.id DESC LIMIT ?"
            )
            rows = self._require().execute(sql, [*args, limit]).fetchall()
            return [self._decode(row) for row in rows]

    def reset(self, chat_id: str) -> None:
        with self._lock:
            self._require().execute(
                "INSERT INTO chats(chat_id, reset_ts, name) VALUES(?, ?, '') "
                "ON CONFLICT(chat_id) DO UPDATE SET reset_ts=excluded.reset_ts",
                (chat_id, time.time()),
            )
            self._require().commit()

    def message_count(self) -> int:
        with self._lock:
            return int(self._require().execute("SELECT COUNT(*) FROM messages").fetchone()[0])

    def needs_full_sync(self) -> bool:
        with self._lock:
            row = self._require().execute("SELECT value FROM meta WHERE key='full_sync_done'").fetchone()
            return row is None or row["value"] != "1"

    def mark_full_sync(self) -> None:
        with self._lock:
            self._require().execute("INSERT OR REPLACE INTO meta(key, value) VALUES('full_sync_done', '1')")
            self._require().commit()

    def _sync_item(
        self, web, fallback_chat: str, bot_name: str, fallback_ts: int = 0
    ) -> tuple[str, ChatMessage] | None:
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
        sender_id = digits(sender_jid) if sender_jid else ""
        sender_name = (web.pushName or "").strip() or (bot_name if from_me else sender_id)
        return (
            chat_id,
            ChatMessage(
                id=msg_id,
                sender_name=sender_name,
                sender_id=sender_id,
                when=event_time(int(web.messageTimestamp or fallback_ts or 0)),
                text=text,
                media=media,
                quote=quote,
                outgoing=from_me,
                sender_jid=sender_jid,
            ),
        )

    def ingest_sync(self, sync: HistorySyncProto, bot_name: str) -> tuple[int, set[str], bool]:
        """Simpan seluruh WebMessageInfo dari satu event HistorySync; kembalikan (jumlah, chat, selesai)."""
        chats: set[str] = set()
        total = 0
        batch: list[tuple[str, ChatMessage]] = []
        for conversation in sync.conversations:
            fallback_chat = conversation.ID or ""
            fallback_ts = int(conversation.conversationTimestamp or 0)
            conv_name = (conversation.name or conversation.displayName or "").strip()
            if conv_name and fallback_chat:
                self.set_chat_name(fallback_chat, conv_name)
            for item in conversation.messages:
                parsed = self._sync_item(item.message, fallback_chat, bot_name, fallback_ts)
                if parsed is None:
                    continue
                chat_id, message = parsed
                batch.append((chat_id, message))
                chats.add(chat_id)
                if len(batch) >= 500:
                    total += self.add_many(batch)
                    batch.clear()
        if batch:
            total += self.add_many(batch)
        done = bool(
            sync.syncType in (HistorySyncProto.FULL, HistorySyncProto.INITIAL_BOOTSTRAP)
            and sync.progress >= 100
        )
        if done:
            self.mark_full_sync()
        return total, chats, done

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.commit()
                self._conn.close()
                self._conn = None
