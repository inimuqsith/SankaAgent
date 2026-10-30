"""Event protobuf WhatsApp -> Inbound; satu-satunya tempat fakta transport dibaca."""

from __future__ import annotations

import logging
from datetime import datetime

from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ProtocolMessage

from ...engine.context import (
    AUTHOR_BOT,
    AUTHOR_MANUAL,
    AUTHOR_UNKNOWN,
    DEFAULT_VISIBILITY,
    ChatMessage,
    Quote,
)
from ...engine.inbound import Inbound
from ...engine.permissions import normalize
from . import directory
from .media import contexts, describe_media, event_time, message_text, views_once

log = logging.getLogger(__name__)


def is_group(chat_id: str) -> bool:
    return chat_id.endswith("@g.us")


def self_handles(me) -> set[str]:
    if me is None:
        return set()
    values = {
        normalize(getattr(getattr(me, "JID", None), "User", "") or ""),
        normalize(getattr(getattr(me, "LID", None), "User", "") or ""),
    }
    return {value for value in values if value and any(ch.isdigit() for ch in value)}


def self_jid(me) -> str:
    """JID akun sendiri (bukan LID) untuk baris `fromMe` saat ingest history sync."""
    jid = getattr(me, "JID", None)
    user = str(getattr(jid, "User", "") or "")
    server = str(getattr(jid, "Server", "") or "s.whatsapp.net")
    return f"{user}@{server}" if user else ""


def sender_name(ev, sender_digits: str, sender: str) -> str:
    return (ev.Info.Pushname or "").strip() or sender_digits or sender


def mentioned(ev, bots: set[str]) -> bool:
    """Fakta transport saja: mention asli di payload, bukan tebakan dari teks."""
    return any(
        normalize(jid) in bots for context in contexts(ev.Message) for jid in context.mentionedJID
    )


def replies_to_bot(ev, bots: set[str]) -> bool:
    return any(
        normalize(context.participant) in bots
        for context in contexts(ev.Message)
        if context.participant
    )


def quote(ev, windows, chat_id: str) -> Quote | None:
    for context in contexts(ev.Message):
        quoted = context.quotedMessage
        if not quoted.ListFields():
            continue
        known = windows.find(chat_id, context.stanzaID)
        if known is not None:
            return Quote(
                id=known.id,
                sender_name=known.sender_name,
                sender_id=known.sender_id,
                text=known.text,
                media=known.media,
                when=known.when,
            )
        quoted_text = message_text(quoted)
        quoted_media = describe_media(quoted, context.stanzaID or f"{ev.Info.ID}-quote")
        if not quoted_text.strip() and quoted_media is None:
            continue
        participant = normalize(context.participant) if context.participant else ""
        return Quote(
            id=context.stanzaID,
            sender_name=participant,
            sender_id=participant,
            text=quoted_text,
            media=quoted_media,
        )
    return None


def revoke_target(ev) -> tuple[str, str] | None:
    """(id pesan target, JID penghapus) untuk protocolMessage REVOKE; None untuk lainnya."""
    protocol = getattr(ev.Message, "protocolMessage", None)
    if protocol is None or not protocol.ListFields():
        return None
    if protocol.type != ProtocolMessage.REVOKE or not protocol.key.ID:
        return None
    sender = ev.Info.MessageSource.Sender
    return protocol.key.ID, f"{sender.User}@{sender.Server}"


def unreadable_message(ev, gateway) -> tuple[str, ChatMessage] | None:
    """Baris placeholder untuk stanza tanpa <enc> (mis. sekali lihat dari perangkat lain)."""
    source = ev.Info.MessageSource
    if not ev.Info.ID or source.Chat.Server == "broadcast":
        return None
    chat_id = f"{source.Chat.User}@{source.Chat.Server}"
    sender = f"{source.Sender.User}@{source.Sender.Server}"
    mapped = ""
    if sender.endswith("@lid"):
        mapped = directory.pn_jid(gateway.db, sender)
    sender_jid = mapped or sender
    digits = normalize(mapped or source.Sender.User or source.Sender.RawAgent)
    name = directory.contact_name(gateway.db, sender_jid) or (ev.Info.Pushname or "").strip() or digits
    message = ChatMessage(
        id=ev.Info.ID,
        sender_name=name,
        sender_id=digits,
        when=event_time(ev.Info.Timestamp),
        text="",
        author=AUTHOR_MANUAL if source.IsFromMe else AUTHOR_UNKNOWN,
        unreadable=True,
        sender_jid=sender_jid,
    )
    return chat_id, message


def event_to_inbound(ev, gateway) -> Inbound | None:
    source = ev.Info.MessageSource
    chat_id = f"{source.Chat.User}@{source.Chat.Server}"
    if chat_id.endswith("@broadcast"):
        return None
    sender = f"{source.Sender.User}@{source.Sender.Server}"
    mapped = ""
    if sender.endswith("@lid"):
        alt = getattr(source, "SenderAlt", None)
        mapped = f"{alt.User}@{alt.Server}" if getattr(alt, "User", "") else ""
        mapped = mapped or directory.pn_jid(gateway.db, sender)
    sender_jid = mapped or sender
    sender_digits = normalize(mapped or source.Sender.User or source.Sender.RawAgent)
    message_id = ev.Info.ID or f"tmp-{datetime.now().timestamp()}"
    text = message_text(ev.Message)
    media = describe_media(ev.Message, message_id)
    if not text.strip() and media is None:
        return None
    outgoing = bool(getattr(source, "IsFromMe", False))
    author = AUTHOR_UNKNOWN
    if outgoing:
        author = AUTHOR_BOT if gateway.is_bot_message(message_id) else AUTHOR_MANUAL
    name = sender_name(ev, sender_digits, sender)
    known = directory.contact_name(gateway.db, sender_jid)
    if author == AUTHOR_BOT:
        known = gateway.config.soul.name
        name = gateway.config.soul.name
    message = ChatMessage(
        id=message_id,
        sender_name=known or name,
        sender_id=sender_digits,
        when=event_time(ev.Info.Timestamp),
        text=text,
        media=media,
        quote=quote(ev, gateway.windows, chat_id),
        outgoing=outgoing,
        sender_jid=sender_jid,
        author=author,
        visibility=DEFAULT_VISIBILITY,
        view_once=views_once(ev.Message),
        unreadable=False,
    )
    bots = gateway.self_handles()
    person_id = directory.resolve_person(gateway.db, sender) or sender_digits
    return Inbound(
        chat_id=chat_id,
        message=message,
        is_dm=not is_group(chat_id),
        is_mention=mentioned(ev, bots),
        is_reply=replies_to_bot(ev, bots),
        is_self=bool(sender_digits and sender_digits in bots and not outgoing),
        person_id=person_id,
        raw=ev,
    )
