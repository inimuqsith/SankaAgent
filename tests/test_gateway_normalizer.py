"""Normalizer contract tests: valid mapping and every stable reason code."""

from __future__ import annotations

import math

import pytest

from gateway.normalizer import (
    MAX_ID_CHARS,
    MAX_MENTIONS,
    MAX_TEXT_CHARS,
    MalformedPayload,
    normalize_inbound,
)

DM_ID = "628111111111@s.whatsapp.net"
BOT_ID = "628999999999@s.whatsapp.net"
GROUP_ID = "120363000000000000@g.us"


def payload(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "message_id": "msg-1",
        "chat_id": DM_ID,
        "sender_id": DM_ID,
        "text": "hello",
    }
    data.update(overrides)
    return data


def without(*keys: str) -> dict[str, object]:
    data = payload()
    for key in keys:
        data.pop(key, None)
    return data


def assert_reason(data: object, reason: str) -> None:
    with pytest.raises(MalformedPayload) as excinfo:
        normalize_inbound(data)  # type: ignore[arg-type]
    assert excinfo.value.reason == reason
    assert str(excinfo.value) == reason


@pytest.mark.parametrize("data", [None, [], "text", 3, 1.5, True])
def test_payload_must_be_an_object(data: object) -> None:
    assert_reason(data, "not_an_object")


@pytest.mark.parametrize(
    "data",
    [
        without("message_id"),
        payload(message_id=""),
        payload(message_id="   "),
        payload(message_id=123),
        payload(message_id=None),
        payload(message_id="m" * (MAX_ID_CHARS + 1)),
    ],
)
def test_message_id_invalid(data: dict[str, object]) -> None:
    assert_reason(data, "message_id_invalid")


@pytest.mark.parametrize(
    "data",
    [
        without("chat_id"),
        payload(chat_id=""),
        payload(chat_id="not-a-jid"),
        payload(chat_id="a@b@c"),
        payload(chat_id="a" * (MAX_ID_CHARS - len("@s.whatsapp.net") + 1) + "@s.whatsapp.net"),
    ],
)
def test_chat_id_invalid(data: dict[str, object]) -> None:
    assert_reason(data, "chat_id_invalid")


@pytest.mark.parametrize(
    "data",
    [
        without("sender_id"),
        payload(sender_id=""),
        payload(sender_id="12345"),
        payload(sender_id=42),
        payload(sender_id="a b@c"),
    ],
)
def test_sender_id_invalid(data: dict[str, object]) -> None:
    assert_reason(data, "sender_id_invalid")


@pytest.mark.parametrize("text", [123, None, ["hello"], {"text": "x"}])
def test_text_invalid(text: object) -> None:
    assert_reason(payload(text=text), "text_invalid")


def test_text_too_long() -> None:
    assert_reason(payload(text="a" * (MAX_TEXT_CHARS + 1)), "text_too_long")


@pytest.mark.parametrize("is_group", ["true", 1, 0, None, "false"])
def test_is_group_invalid(is_group: object) -> None:
    assert_reason(payload(is_group=is_group), "is_group_invalid")


@pytest.mark.parametrize(
    ("chat_id", "is_group"),
    [(GROUP_ID, False), (DM_ID, True)],
)
def test_is_group_mismatch(chat_id: str, is_group: bool) -> None:
    assert_reason(payload(chat_id=chat_id, is_group=is_group), "is_group_mismatch")


@pytest.mark.parametrize(
    "mentions",
    [
        "someone@s.whatsapp.net",
        None,
        [123],
        [""],
        ["not-a-jid"],
        [f"62800{i:05d}@s.whatsapp.net" for i in range(MAX_MENTIONS + 1)],
    ],
)
def test_mentions_invalid(mentions: object) -> None:
    assert_reason(payload(mentions=mentions), "mentions_invalid")


@pytest.mark.parametrize("reply_to_bot", ["yes", 1, 0, None])
def test_reply_to_bot_invalid(reply_to_bot: object) -> None:
    assert_reason(payload(reply_to_bot=reply_to_bot), "reply_to_bot_invalid")


@pytest.mark.parametrize(
    "timestamp",
    [0, -1, -0.5, True, False, "1700000000", None, math.nan, math.inf, -math.inf],
)
def test_timestamp_invalid(timestamp: object) -> None:
    assert_reason(payload(timestamp=timestamp), "timestamp_invalid")


def test_minimal_payload_maps_to_event() -> None:
    event = normalize_inbound(without("text"))

    assert event.message_id == "msg-1"
    assert event.message.chat_id == DM_ID
    assert event.message.sender_id == DM_ID
    assert event.message.text == ""
    assert event.message.is_group is False
    assert event.message.mentions == ()
    assert event.message.reply_to_bot is False
    assert isinstance(event.timestamp, float)


def test_valid_payload_is_mapped_without_stripping_text() -> None:
    event = normalize_inbound(
        payload(
            text="  hello world  ",
            is_group=False,
            mentions=[BOT_ID, "628123123123@s.whatsapp.net", BOT_ID],
            reply_to_bot=True,
            timestamp=1_700_000_000,
            unknown_field="ignored",
        )
    )

    assert event.message.text == "  hello world  "
    assert event.message.is_group is False
    assert event.message.mentions == (BOT_ID, "628123123123@s.whatsapp.net")
    assert event.message.reply_to_bot is True
    assert event.timestamp == 1_700_000_000.0


def test_group_flag_is_derived_from_chat_id() -> None:
    event = normalize_inbound(payload(chat_id=GROUP_ID, is_group=True))
    assert event.message.is_group is True

    dm_event = normalize_inbound(payload(chat_id=DM_ID, is_group=False))
    assert dm_event.message.is_group is False


def test_timestamp_defaults_to_receive_time() -> None:
    event = normalize_inbound(payload(), now=lambda: 1234.5)
    assert event.timestamp == 1234.5


def test_mentions_are_deduplicated_in_order() -> None:
    event = normalize_inbound(
        payload(mentions=[BOT_ID, BOT_ID, "628123123123@s.whatsapp.net", BOT_ID])
    )
    assert event.message.mentions == (BOT_ID, "628123123123@s.whatsapp.net")


def test_max_mentions_and_max_lengths_are_inclusive() -> None:
    mentions = [f"62800{i:05d}@s.whatsapp.net" for i in range(MAX_MENTIONS)]
    long_id = "a" * (MAX_ID_CHARS - len("@s.whatsapp.net")) + "@s.whatsapp.net"
    assert len(long_id) == MAX_ID_CHARS

    event = normalize_inbound(
        payload(
            message_id="m" * MAX_ID_CHARS,
            chat_id=long_id,
            sender_id=long_id,
            text="a" * MAX_TEXT_CHARS,
            mentions=mentions,
        )
    )

    assert event.message.mentions == tuple(mentions)
    assert event.message.chat_id == long_id


def test_reason_never_echoes_payload_values() -> None:
    secret = "super-secret-value"
    with pytest.raises(MalformedPayload) as excinfo:
        normalize_inbound(payload(chat_id=secret))
    assert secret not in str(excinfo.value)
