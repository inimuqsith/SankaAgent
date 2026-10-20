"""Normalize raw bridge payloads into typed inbound events."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from sanka.models import InboundMessage

MAX_TEXT_CHARS = 32_768
MAX_MENTIONS = 32
MAX_ID_CHARS = 200

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
    payload: object, *, now: Callable[[], float] = time.time
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

    reply_to_bot = False
    if "reply_to_bot" in payload:
        supplied_reply = payload["reply_to_bot"]
        if not isinstance(supplied_reply, bool):
            raise MalformedPayload("reply_to_bot_invalid")
        reply_to_bot = supplied_reply

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
        ),
        timestamp=timestamp,
    )


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
