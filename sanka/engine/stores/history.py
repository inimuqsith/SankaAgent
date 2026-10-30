"""Arsip transcript per chat di Database; media disimpan sebagai metadata + bytes."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Text, and_, case, cast, func, or_, select

from ..context import (
    AUTHOR_RANK,
    AUTHOR_UNKNOWN,
    ChatMessage,
    Media,
    Quote,
    clamp_visibility,
)
from ..db import Database
from ..schema import Json, chats, messages

log = logging.getLogger(__name__)


def _media_json(media: Media) -> dict:
    return {
        "kind": media.kind,
        "mimetype": media.mimetype,
        "filename": media.filename,
        "size": int(media.size or 0),
    }


def _media_from(value, ref) -> Media | None:
    if not isinstance(value, dict):
        return None
    return Media(
        kind=str(value.get("kind") or ""),
        mimetype=str(value.get("mimetype") or ""),
        filename=str(value.get("filename") or ""),
        size=int(value.get("size") or 0),
        proto=None,
        ref=bytes(ref) if ref else None,
    )


def _data(message: ChatMessage) -> dict:
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
    if message.view_once:
        data["view_once"] = True
    if message.unreadable:
        data["unreadable"] = True
    return data


def _blob(media: Media | None) -> bytes | None:
    if media is None:
        return None
    if media.ref:
        return bytes(media.ref)
    proto = getattr(media, "proto", None)
    if proto is None:
        return None
    try:
        return proto.SerializeToString()
    except (AttributeError, TypeError):
        return None


def _like_terms(query: str) -> list[str]:
    return re.findall(r"\w+", query, re.UNICODE) or [query.strip()]


class HistoryStore:
    """Satu baris per (chat, message id); upsert mempertahankan isi yang lebih kaya."""

    def __init__(self, db: Database, workspace=None):
        self.db = db
        self.workspace = workspace

    # ------------------------------------------------------------ tulis

    def _row(self, chat_id: str, message: ChatMessage) -> dict:
        media = message.media
        quote = message.quote
        return {
            "chat_id": chat_id,
            "msg_id": message.id or f"tmp-{time.time_ns()}",
            "ts": message.when.timestamp(),
            "sender_id": message.sender_id or "",
            "sender_name": message.sender_name or "",
            "sender_address": message.sender_jid or "",
            "text": message.text or "",
            "outgoing": bool(message.outgoing),
            "author": message.author,
            "visibility": message.visibility,
            "deleted": bool(message.deleted),
            "deleted_by": message.deleted_by or "",
            "deleted_admin": bool(message.deleted_admin),
            "data": _data(message),
            "media_ref": _blob(media),
            "quote_media_ref": _blob(quote.media) if quote is not None else None,
        }

    @staticmethod
    def _merge(stmt, dialect: str) -> dict:
        table = messages
        excluded = stmt.excluded
        if dialect == "postgresql":
            data = func.coalesce(
                cast(func.nullif(cast(excluded.data, Text), "{}"), Json), table.c.data
            )
        else:
            data = func.coalesce(func.nullif(excluded.data, "{}"), table.c.data)
        new_rank = case(AUTHOR_RANK, value=excluded.author, else_=0)
        old_rank = case(AUTHOR_RANK, value=table.c.author, else_=0)
        keep_new = new_rank >= old_rank
        removed = excluded.deleted.is_(True)
        return {
            "sender_name": case(
                (keep_new, func.coalesce(func.nullif(excluded.sender_name, ""), table.c.sender_name)),
                else_=table.c.sender_name,
            ),
            "sender_id": case(
                (keep_new, func.coalesce(func.nullif(excluded.sender_id, ""), table.c.sender_id)),
                else_=table.c.sender_id,
            ),
            "sender_address": case(
                (
                    keep_new,
                    func.coalesce(func.nullif(excluded.sender_address, ""), table.c.sender_address),
                ),
                else_=table.c.sender_address,
            ),
            "author": case((keep_new, excluded.author), else_=table.c.author),
            "visibility": case(
                (excluded.visibility > table.c.visibility, excluded.visibility),
                else_=table.c.visibility,
            ),
            "text": func.coalesce(func.nullif(excluded.text, ""), table.c.text),
            "deleted": or_(table.c.deleted, excluded.deleted),
            "deleted_by": case((removed, excluded.deleted_by), else_=table.c.deleted_by),
            "deleted_admin": case((removed, excluded.deleted_admin), else_=table.c.deleted_admin),
            "data": data,
            "media_ref": func.coalesce(excluded.media_ref, table.c.media_ref),
            "quote_media_ref": func.coalesce(
                excluded.quote_media_ref, table.c.quote_media_ref
            ),
        }

    def add(self, chat_id: str, message: ChatMessage) -> None:
        self.add_many([(chat_id, message)])

    def add_many(self, items: list[tuple[str, ChatMessage]]) -> int:
        rows = [self._row(chat_id, message) for chat_id, message in items]
        if not rows:
            return 0
        with self.db.connect() as conn:
            self.db.upsert(
                conn,
                messages,
                rows,
                ["chat_id", "msg_id"],
                update=lambda stmt: self._merge(stmt, conn.dialect.name),
            )
        return len(rows)

    def reset(self, chat_id: str) -> None:
        now = time.time()
        with self.db.connect() as conn:
            self.db.upsert(
                conn,
                chats,
                [
                    {
                        "chat_id": chat_id,
                        "name": "",
                        "reset_ts": now,
                        "kind": "group" if chat_id.endswith("@g.us") else "dm",
                    }
                ],
                ["chat_id"],
                update={"reset_ts": now},
            )

    def set_visibility(self, chat_id: str, message_ids: Sequence[str], level: int) -> int:
        """Naikkan tanda privasi baris arsip; visibilitas hanya boleh naik, tidak turun."""
        value = clamp_visibility(level)
        changed = 0
        for message_id in dict.fromkeys(str(item) for item in message_ids if item):
            entry = self.find(chat_id, message_id)
            if entry is None or entry.visibility >= value:
                continue
            entry.visibility = value
            self.add(chat_id, entry)
            changed += 1
        return changed

    def mark_deleted(self, chat_id: str, message_id: str, by_jid: str, by_name: str = "") -> bool:
        entry = self.find(chat_id, message_id)
        if entry is None or not entry.mark_deleted(by_jid, by_name):
            return False
        self.add(chat_id, entry)
        return True

    def set_chat_name(self, chat_id: str, name: str) -> None:
        if not chat_id:
            return
        value = " ".join(str(name or "").split())[:200]
        if not value:
            return
        row = {
            "chat_id": chat_id,
            "name": value,
            "reset_ts": 0.0,
            "kind": "group" if chat_id.endswith("@g.us") else "dm",
        }
        with self.db.connect() as conn:
            self.db.upsert(
                conn, chats, [row], ["chat_id"], update={"name": value}
            )

    # ------------------------------------------------------------ baca

    def _decode(self, row: dict) -> ChatMessage:
        try:
            data = row.get("data") or {}
        except AttributeError:
            data = {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (TypeError, ValueError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        quote_data = data.get("quote")
        quote = None
        if isinstance(quote_data, dict):
            when = quote_data.get("when")
            quote = Quote(
                id=str(quote_data.get("id") or ""),
                sender_name=str(quote_data.get("sender_name") or ""),
                sender_id=str(quote_data.get("sender_id") or ""),
                text=str(quote_data.get("text") or ""),
                media=_media_from(quote_data.get("media"), row.get("quote_media_ref")),
                when=datetime.fromtimestamp(float(when)) if when else None,
            )
        author = str(row.get("author") or AUTHOR_UNKNOWN)
        if author not in AUTHOR_RANK:
            author = AUTHOR_UNKNOWN
        return ChatMessage(
            id=str(row["msg_id"]),
            sender_name=str(row["sender_name"]),
            sender_id=str(row["sender_id"]),
            when=datetime.fromtimestamp(float(row["ts"])),
            text=str(row["text"]),
            media=_media_from(data.get("media"), row.get("media_ref")),
            quote=quote,
            outgoing=bool(row["outgoing"]),
            sender_jid=str(row["sender_address"]),
            author=author,
            visibility=clamp_visibility(row.get("visibility")),
            view_once=bool(data.get("view_once")),
            unreadable=bool(data.get("unreadable")),
            deleted=bool(row.get("deleted")),
            deleted_by=str(row.get("deleted_by") or ""),
            deleted_admin=bool(row.get("deleted_admin")),
        )

    def _reset_ts(self, chat_id: str) -> float:
        found = self.db.fetch_one(
            select(chats.c.reset_ts).where(chats.c.chat_id == chat_id)
        )
        return float(found["reset_ts"]) if found else 0.0

    def chat_name(self, chat_id: str) -> str:
        if not chat_id:
            return ""
        found = self.db.fetch_one(
            select(chats.c.name).where(chats.c.chat_id == chat_id)
        )
        return str(found["name"]) if found else ""

    def recent(self, chat_id: str, limit: int) -> list[ChatMessage]:
        reset = self._reset_ts(chat_id)
        statement = (
            select(messages)
            .where(and_(messages.c.chat_id == chat_id, messages.c.ts >= reset))
            .order_by(messages.c.ts.desc(), messages.c.id.desc())
            .limit(max(1, int(limit)))
        )
        return [self._decode(row) for row in reversed(self.db.fetch_all(statement))]

    def find(self, chat_id: str, msg_id: str) -> ChatMessage | None:
        if not msg_id:
            return None
        statement = (
            select(messages)
            .where(and_(messages.c.chat_id == chat_id, messages.c.msg_id == msg_id))
            .order_by(messages.c.id.desc())
            .limit(1)
        )
        found = self.db.fetch_one(statement)
        return self._decode(found) if found else None

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
        clauses = [messages.c.chat_id == chat_id]
        if after is not None:
            clauses.append(messages.c.ts >= float(after))
        if before is not None:
            clauses.append(messages.c.ts <= float(before))
        if query.strip():
            for term in _like_terms(query):
                like = f"%{term}%"
                clauses.append(
                    messages.c.text.ilike(like) | messages.c.sender_name.ilike(like)
                )
        statement = (
            select(messages)
            .where(and_(*clauses))
            .order_by(messages.c.ts.desc(), messages.c.id.desc())
            .limit(limit)
        )
        return [self._decode(row) for row in self.db.fetch_all(statement)]

    def message_count(self) -> int:
        return int(self.db.scalar(select(func.count()).select_from(messages)) or 0)

    def recent_chats(self, limit: int = 50) -> list[str]:
        statement = (
            select(messages.c.chat_id, func.max(messages.c.ts).label("last"))
            .group_by(messages.c.chat_id)
            .order_by(func.max(messages.c.ts).desc())
            .limit(max(1, int(limit)))
        )
        return [str(row["chat_id"]) for row in self.db.fetch_all(statement) if row["chat_id"]]

    def _human_rows(self, limit: int, days: int) -> list[dict]:
        size = max(1, min(int(limit or 2000), 20_000))
        cutoff = time.time() - max(1, int(days)) * 86400
        statement = (
            select(
                messages.c.chat_id,
                messages.c.sender_id,
                messages.c.sender_name,
                messages.c.sender_address.label("sender_jid"),
                messages.c.text,
                messages.c.ts,
            )
            .where(
                and_(
                    messages.c.outgoing.is_(False),
                    messages.c.text != "",
                    messages.c.ts >= cutoff,
                )
            )
            .order_by(messages.c.ts.desc(), messages.c.id.desc())
            .limit(size)
        )
        return self.db.fetch_all(statement)

    def people_seed(self, limit: int = 5000, days: int = 90) -> list[dict]:
        return self._human_rows(limit, days)

    def style_sample(self, limit: int = 2000) -> list[dict]:
        return self._human_rows(limit, 365)

    def close(self) -> None:
        """Kompatibilitas shutdown; Database dimiliki runtime."""
