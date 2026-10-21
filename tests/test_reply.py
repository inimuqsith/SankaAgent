"""Kontrak balasan: quote wajib wrapper event Neonize, target wajib chat asal."""

from __future__ import annotations

import asyncio
from pathlib import Path

from neonize.aioze.client import NewAClient
from neonize.proto.Neonize_pb2 import Message
from neonize.utils import Jid2String
from neonize.utils.enum import ChatPresence, ChatPresenceMedia

from sanka.config import (
    Config,
    LLMConfig,
    RoleConfig,
    SandboxConfig,
    WhatsAppConfig,
)
from sanka.wa import App, Sessions, handle_message

BOT_JID = "628567770837@s.whatsapp.net"


def make_config() -> Config:
    return Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=Path("/tmp"),
        whatsapp=WhatsAppConfig(session_db=Path("/tmp/sanka-test.db")),
        roles={"member": RoleConfig("member")},
        default_role="member",
        sandbox=SandboxConfig(),
        mcp={},
    )


def transport() -> tuple[NewAClient, list[tuple], list[tuple]]:
    """NewAClient asli dengan builder asli, tapi send_message dan presence diganti perekam."""
    client = NewAClient.__new__(NewAClient)
    sent: list[tuple] = []
    presence: list[tuple] = []

    async def send_message(to, message, link_preview=False, add_msg_secret=False):
        sent.append((to, message))

    async def send_chat_presence(jid, state, media):
        presence.append((Jid2String(jid), state, media))

    client.send_message = send_message
    client.send_chat_presence = send_chat_presence
    return client, sent, presence


async def reply_for(ev: Message) -> tuple[list[tuple], list[tuple]]:
    client, sent, presence = transport()
    app = App(make_config(), None, None, Sessions())
    app.bot_digits = {"628567770837"}
    await handle_message(client, ev, app)
    return sent, presence


def dm_event(text: str) -> Message:
    ev = Message()
    ev.Info.ID = "3EB0DM"
    ev.Info.MessageSource.Chat.User = "628999999999"
    ev.Info.MessageSource.Chat.Server = "s.whatsapp.net"
    ev.Info.MessageSource.Sender.User = "628999999999"
    ev.Info.MessageSource.Sender.Server = "s.whatsapp.net"
    ev.Message.conversation = text
    return ev


def group_event(text: str, mention: bool = True) -> Message:
    ev = Message()
    ev.Info.ID = "3EB0GROUP"
    ev.Info.MessageSource.Chat.User = "1234567890"
    ev.Info.MessageSource.Chat.Server = "g.us"
    ev.Info.MessageSource.Sender.User = "628222222222"
    ev.Info.MessageSource.Sender.Server = "s.whatsapp.net"
    ev.Message.extendedTextMessage.text = text
    if mention:
        ev.Message.extendedTextMessage.contextInfo.mentionedJID.append(BOT_JID)
    return ev


def test_dm_reply_quotes_event_wrapper() -> None:
    ev = dm_event("/tidak-ada")
    sent, _ = asyncio.run(reply_for(ev))

    assert len(sent) == 1
    to, reply = sent[0]
    assert Jid2String(to) == "628999999999@s.whatsapp.net"
    context = reply.extendedTextMessage.contextInfo
    assert context.stanzaID == "3EB0DM"
    assert context.participant == "628999999999@s.whatsapp.net"
    assert context.quotedMessage.conversation == "/tidak-ada"
    assert reply.extendedTextMessage.text == "Command /tidak-ada tidak dikenal."


def test_group_reply_targets_group_not_sender() -> None:
    sent, _ = asyncio.run(reply_for(group_event("/tidak-ada")))

    assert len(sent) == 1
    to, reply = sent[0]
    assert Jid2String(to) == "1234567890@g.us"
    assert reply.extendedTextMessage.contextInfo.participant == "628222222222@s.whatsapp.net"
    assert reply.extendedTextMessage.contextInfo.quotedMessage.extendedTextMessage.text == "/tidak-ada"


def test_lid_sender_quotes_phone_alternative() -> None:
    ev = dm_event("/tidak-ada")
    ev.Info.MessageSource.Chat.User = "130799689031758"
    ev.Info.MessageSource.Chat.Server = "lid"
    ev.Info.MessageSource.Sender.User = "130799689031758"
    ev.Info.MessageSource.Sender.Server = "lid"
    ev.Info.MessageSource.SenderAlt.User = "628999999999"
    ev.Info.MessageSource.SenderAlt.Server = "s.whatsapp.net"

    sent, _ = asyncio.run(reply_for(ev))

    assert len(sent) == 1
    to, reply = sent[0]
    assert Jid2String(to) == "130799689031758@lid"
    assert reply.extendedTextMessage.contextInfo.participant == "628999999999@s.whatsapp.net"


def test_typing_presence_around_reply() -> None:
    _, presence = asyncio.run(reply_for(group_event("/tidak-ada")))

    assert [item[1] for item in presence] == [ChatPresence.CHAT_PRESENCE_COMPOSING, ChatPresence.CHAT_PRESENCE_PAUSED]
    assert {item[0] for item in presence} == {"1234567890@g.us"}
    assert {item[2] for item in presence} == {ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT}


def test_unaddressed_group_message_has_no_presence() -> None:
    _, presence = asyncio.run(reply_for(group_event("jawab ya", mention=False)))

    assert presence == []
