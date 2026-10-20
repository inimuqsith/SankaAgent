"""Routing tests: command vs agent vs ignore."""

from __future__ import annotations

import pytest

from sanka.models import InboundAttachment, InboundMessage
from sanka.router import Route, route

BOT_ID = "628999999999"


def message(text: str, *, is_group: bool = True, **kwargs) -> InboundMessage:
    return InboundMessage(
        chat_id="chat",
        sender_id="628111111111",
        text=text,
        is_group=is_group,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("text", "is_group", "kwargs", "expected"),
    [
        ("/ping", True, {}, Route.COMMAND),
        ("/ping", False, {}, Route.COMMAND),
        ("/help@Sanka", True, {}, Route.COMMAND),
        ("halo", True, {}, Route.IGNORE),
        ("@sanka halo", True, {}, Route.AGENT),
        ("pagi @Sanka, bantu rekap", True, {}, Route.AGENT),
        ("@sankax halo", True, {}, Route.IGNORE),
        ("halo", True, {"reply_to_bot": True}, Route.AGENT),
        ("halo", False, {}, Route.AGENT),
        ("   ", False, {}, Route.IGNORE),
        ("\n\t", True, {}, Route.IGNORE),
    ],
)
def test_routing_table(text, is_group, kwargs, expected) -> None:
    assert route(message(text, is_group=is_group, **kwargs), bot_ids=(BOT_ID,)) is expected


def test_mention_id_matches_jid() -> None:
    incoming = message(
        "halo",
        mentions=("628999999999@s.whatsapp.net",),
    )
    assert route(incoming, bot_ids=(BOT_ID,)) is Route.AGENT


def test_mention_of_someone_else_is_ignored() -> None:
    incoming = message("halo", mentions=("628123123123@s.whatsapp.net",))
    assert route(incoming, bot_ids=(BOT_ID,)) is Route.IGNORE


def test_group_does_not_require_mention_when_disabled() -> None:
    assert route(message("halo"), require_mention=False) is Route.AGENT


ATTACHMENT = InboundAttachment(
    attachment_id="a" * 32,
    kind="document",
    mime="text/markdown",
    name="catatan.md",
    size=5,
)


def test_attachment_only_dm_is_agent() -> None:
    incoming = message("", is_group=False, attachments=(ATTACHMENT,))
    assert route(incoming, bot_ids=(BOT_ID,)) is Route.AGENT


def test_attachment_only_group_still_needs_mention() -> None:
    incoming = message("", is_group=True, attachments=(ATTACHMENT,))
    assert route(incoming, bot_ids=(BOT_ID,)) is Route.IGNORE


def test_attachment_with_caption_mention_is_agent() -> None:
    incoming = message(
        "@sanka baca ini",
        is_group=True,
        attachments=(ATTACHMENT,),
    )
    assert route(incoming, bot_ids=(BOT_ID,)) is Route.AGENT
