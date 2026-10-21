"""Normalize raw bridge payloads into typed inbound events."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from sanka.attachments import AttachmentStore
from sanka.models import InboundAttachment, InboundMessage

MAX_TEXT_CHARS = 32_768
MAX_MENTIONS = 32
MAX_ID_CHARS = 200
MAX_ATTACHMENTS = 4
MAX_ATTACHMENT_ERRORS = 8

_ATTACHMENT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_ATTACHMENT_ERROR_CODES = frozenset(
    {"download_failed", "upload_failed", "too_large", "unsupported"}
)

_JID_PATTERN = re.compile(r"^[0-9A-Za-z._:\-]+@[0-9A-Za-z.\-]+$")


@dataclass(frozen=True, slots=True)
class InboundEvent:
    """A validated bridge payload plus its Python receive-side metadata."""

    message_id: str
    message: InboundMessage
    timestamp: float


class MalformedPayload(ValueError):
    """Raised when a payload cannot be normalized.

    ``reason`` is a stable code from the gateway HTTP contract. The exception
    message only ever contains that code, never a payload value.
    """

    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def normalize_inbound(
    payload: object,
    *,
    now: Callable[[], float] = time.time,
    attachments: AttachmentStore | None = None,
) -> InboundEvent:
    """Validate ``payload`` and build an :class:`InboundEvent`.

    Unknown fields are ignored. Raises :class:`MalformedPayload` with a stable
    reason code on any invalid field.
    """
    if not isinstance(payload, dict):
        raise MalformedPayload("not_an_object")

    message_id = _message_id(payload)
    chat_id = _jid_field(payload, "chat_id", "chat_id_invalid")
    sender_id = _jid_field(payload, "sender_id", "sender_id_invalid")

    text = payload.get("text", "")
    if not isinstance(text, str):
        raise MalformedPayload("text_invalid")
    if len(text) > MAX_TEXT_CHARS:
        raise MalformedPayload("text_too_long")

    is_group = chat_id.endswith("@g.us")
    if "is_group" in payload:
        supplied = payload["is_group"]
        if not isinstance(supplied, bool):
            raise MalformedPayload("is_group_invalid")
        if supplied != is_group:
            raise MalformedPayload("is_group_mismatch")

    mentions = _mentions(payload)
    sender_name = _sender_name(payload)
    mention_names = _mention_names(payload)
    quoted_text = _quoted_text(payload)
    quoted_sender_name = _quoted_sender_name(payload)

    reply_to_bot = False
    if "reply_to_bot" in payload:
        supplied_reply = payload["reply_to_bot"]
        if not isinstance(supplied_reply, bool):
            raise MalformedPayload("reply_to_bot_invalid")
        reply_to_bot = supplied_reply

    attachment_items = _attachments(
        payload,
        store=attachments,
        chat_id=chat_id,
        sender_id=sender_id,
        message_id=message_id,
    )
    attachment_errors = _attachment_errors(payload)
    timestamp = _timestamp(payload, now)

    return InboundEvent(
        message_id=message_id,
        message=InboundMessage(
            chat_id=chat_id,
            sender_id=sender_id,
            text=text,
            is_group=is_group,
            mentions=mentions,
            reply_to_bot=reply_to_bot,
            sender_name=sender_name,
            mention_names=mention_names,
            quoted_text=quoted_text,
            quoted_sender_name=quoted_sender_name,
            attachments=attachment_items,
            attachment_errors=attachment_errors,
        ),
        timestamp=timestamp,
    )


def _attachments(
    payload: dict[object, object],
    *,
    store: AttachmentStore | None,
    chat_id: str,
    sender_id: str,
    message_id: str,
) -> tuple[InboundAttachment, ...]:
    if "attachments" not in payload:
        return ()
    raw = payload["attachments"]
    limit = store.max_per_message if store is not None else MAX_ATTACHMENTS
    if not isinstance(raw, list) or len(raw) > limit:
        raise MalformedPayload("attachments_invalid")

    result: list[InboundAttachment] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise MalformedPayload("attachments_invalid")
        attachment_id = entry.get("attachment_id")
        if (
            not isinstance(attachment_id, str)
            or _ATTACHMENT_ID_PATTERN.fullmatch(attachment_id) is None
            or attachment_id in seen
        ):
            raise MalformedPayload("attachments_invalid")
        seen.add(attachment_id)
        kind = entry.get("kind")
        mime = entry.get("mime")
        size = entry.get("size")
        if kind not in ("image", "document"):
            raise MalformedPayload("attachments_invalid")
        if not isinstance(mime, str) or not 0 < len(mime) <= 100:
            raise MalformedPayload("attachments_invalid")
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise MalformedPayload("attachments_invalid")

        record = (
            store.get(attachment_id, chat_id=chat_id)
            if store is not None
            else None
        )
        if (
            record is None
            or record.sender_id != sender_id
            or record.message_id != message_id
            or record.kind != kind
            or record.mime != mime
            or record.size != size
        ):
            raise MalformedPayload("attachment_unknown")
        result.append(
            InboundAttachment(
                attachment_id=record.attachment_id,
                kind=record.kind,
                mime=record.mime,
                name=record.name,
                size=record.size,
            )
        )
    return tuple(result)


def _attachment_errors(payload: dict[object, object]) -> tuple[str, ...]:
    if "attachment_errors" not in payload:
        return ()
    raw = payload["attachment_errors"]
    if not isinstance(raw, list) or len(raw) > MAX_ATTACHMENT_ERRORS:
        raise MalformedPayload("attachment_errors_invalid")
    result: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or entry not in _ATTACHMENT_ERROR_CODES:
            raise MalformedPayload("attachment_errors_invalid")
        if entry not in result:
            result.append(entry)
    return tuple(result)


def _message_id(payload: dict[object, object]) -> str:
    value = payload.get("message_id")
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_ID_CHARS
    ):
        raise MalformedPayload("message_id_invalid")
    return value


def _jid_field(payload: dict[object, object], field: str, reason: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not _is_jid(value):
        raise MalformedPayload(reason)
    return value


def _is_jid(value: str) -> bool:
    if not value or len(value) > MAX_ID_CHARS:
        return False
    return _JID_PATTERN.fullmatch(value) is not None


def _mentions(payload: dict[object, object]) -> tuple[str, ...]:
    if "mentions" not in payload:
        return ()
    raw = payload["mentions"]
    if not isinstance(raw, list) or len(raw) > MAX_MENTIONS:
        raise MalformedPayload("mentions_invalid")

    seen: set[str] = set()
    result: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not _is_jid(entry):
            raise MalformedPayload("mentions_invalid")
        if entry not in seen:
            seen.add(entry)
            result.append(entry)
    return tuple(result)


def _sender_name(payload: dict[object, object]) -> str:
    value = payload.get("sender_name")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MalformedPayload("sender_name_invalid")
    return " ".join(value.split())[:64]


def _mention_names(payload: dict[object, object]) -> tuple[str, ...]:
    value = payload.get("mention_names")
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > MAX_MENTIONS:
        raise MalformedPayload("mention_names_invalid")
    names: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise MalformedPayload("mention_names_invalid")
        names.append(" ".join(entry.split())[:64])
    return tuple(names)


def _quoted_text(payload: dict[object, object]) -> str:
    value = payload.get("quoted_text")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MalformedPayload("quoted_text_invalid")
    return " ".join(value.split())[:500]


def _quoted_sender_name(payload: dict[object, object]) -> str:
    value = payload.get("quoted_sender_name")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MalformedPayload("quoted_sender_name_invalid")
    return " ".join(value.split())[:64]


def _timestamp(payload: dict[object, object], now: Callable[[], float]) -> float:
    if "timestamp" not in payload:
        return float(now())

    value = payload["timestamp"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MalformedPayload("timestamp_invalid")

    stamp = float(value)
    if not math.isfinite(stamp) or stamp <= 0:
        raise MalformedPayload("timestamp_invalid")
    return stamp
