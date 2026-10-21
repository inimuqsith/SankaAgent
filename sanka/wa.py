"""Transport WhatsApp: satu handler Neonize, routing DM/grup, satu jalur balasan."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections import defaultdict
from datetime import date, datetime

from neonize.aioze.client import NewAClient
from neonize.exc import SendMessageError
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ContextInfo, ExtendedTextMessage
from neonize.utils.enum import ChatPresence, ChatPresenceMedia

from .chat import (
    ChatMessage,
    Quote,
    Sessions,
    build_user_content,
    clean,
    contexts,
    describe_media,
    event_time,
    fallback_content,
    message_text,
)
from .commands import REGISTRY as COMMANDS
from .commands import CommandContext
from .config import Config
from .format import format_reply
from .llm import LLM
from .mcp import Hub
from .permissions import (
    command_allowed,
    commands_for,
    normalize,
    role_for,
    tool_patterns,
    tools_for,
)
from .tools import ToolContext

log = logging.getLogger(__name__)

TYPING_REFRESH = 8


class App:
    def __init__(self, config: Config, llm: LLM, hub: Hub, sessions: Sessions):
        self.config = config
        self.llm = llm
        self.hub = hub
        self.sessions = sessions
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.bot_digits: set[str] = set()
        self.client: NewAClient | None = None
        self.backfill: asyncio.Task | None = None


def is_group(chat_jid: str) -> bool:
    return chat_jid.endswith("@g.us")


def digits(value: str) -> str:
    return normalize(value)


def bot_digits(me) -> set[str]:
    found = {digits(me.JID.User), digits(me.LID.User)}
    return {item for item in found if item}


def mentioned(ev, bots: set[str], bot_name: str = "sanka") -> bool:
    for context in contexts(ev.Message):
        if any(digits(jid) in bots for jid in context.mentionedJID):
            return True
    text = message_text(ev.Message)
    return bool(re.search(rf"(?<![\w@])@{re.escape(bot_name)}(?!\w)", text, re.IGNORECASE))


def replies_to_bot(ev, bots: set[str]) -> bool:
    return any(digits(context.participant) in bots for context in contexts(ev.Message) if context.participant)


def should_process(
    ev,
    chat: str,
    text: str,
    *,
    is_dm: bool,
    bot_name: str,
    require_mention: bool,
    bots: set[str],
    has_media: bool = False,
) -> bool:
    if not text.strip() and not has_media:
        return False
    if chat.endswith("@broadcast"):
        return False
    if getattr(ev.Info.MessageSource, "IsFromMe", False):
        return False
    sender = digits(ev.Info.MessageSource.Sender.User or ev.Info.MessageSource.Sender.RawAgent)
    if sender and sender in bots:
        return False
    if is_dm:
        return True
    return not require_mention or replies_to_bot(ev, bots) or mentioned(ev, bots, bot_name)


def system_prompt(config: Config, role_name: str, bots: set[str] | None = None) -> str:
    # Tanpa identitas ini model membaca mention @<LID bot> sebagai nomor orang lain.
    identity = (
        f"Identitasmu: {config.whatsapp.bot_name}, nomor {', '.join(sorted(bots))}. "
        "Mention @<nomor itu> di dalam teks pesan adalah untukmu, bukan orang lain.\n"
        if bots
        else ""
    )
    return (
        f"{config.persona}\n\n"
        f"Kamu berjalan sebagai bot WhatsApp. Role pengirim: {role_name}.\n"
        f"{identity}"
        f"Workspace: {config.workspace}. Pakai tool bila perlu; jangan mengarang hasil tool.\n"
        "Payload berisi 20 pesan terakhir chat ini, lengkap dengan nama, nomor, dan waktu pengirim. "
        "Entri terakhir adalah pesan yang harus kamu jawab. Pesan yang lebih lama tersimpan di arsip "
        "chat ini; cari dengan tool chat_history (query, before, after) bila butuh fakta dari "
        "percakapan lama. Arsip hanya mencakup chat ini, bukan chat lain.\n"
        "Gambar bernomor (#1, #2, ...) sudah dilampirkan dan bisa kamu lihat langsung. Teks bertanda "
        "[dipotong] bisa dibaca lengkap dengan tool read pada path yang tertera; di sandbox gunakan /work/<path>.\n"
        "Lampiran biner (PDF/audio/video/dll.) wajib kamu coba periksa dengan tool sandbox sebelum bilang "
        "tidak bisa. Kalau tool tidak diizinkan, sebutkan bahwa role pengirim perlu izin sandbox. Jangan "
        "mengarang isi lampiran yang tidak diinjeksi.\n"
        "Jawab langsung dalam bahasa pengguna. Jangan sebut prompt atau proses internal; sebut tool hanya "
        "jika perlu menjelaskan izin atau kegagalan.\n"
        "Untuk menyebut orang, tulis @<nomor> persis seperti di payload (contoh @6285111242960) tanpa +, "
        "spasi, atau tanda lain; runtime mengubahnya jadi mention asli.\n"
        "Format balasan untuk WhatsApp: tanpa heading markdown, tanpa tabel.\n"
        f"Tanggal: {date.today().isoformat()}."
    )


def _sender_name(ev, sender_digits: str, sender: str) -> str:
    # Info.VerifiedName bukan string (message VerifiedName), jadi tidak dipakai sebagai nama.
    return (ev.Info.Pushname or "").strip() or sender_digits or sender


def _quote(ev, app: App, chat_id: str) -> Quote | None:
    """Kutipan dari contextInfo: pakai entri log bila masih tersimpan, kalau tidak dari proto."""
    for context in contexts(ev.Message):
        quoted = context.quotedMessage
        if not quoted.ListFields():
            continue
        known = app.sessions.find(chat_id, context.stanzaID)
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
        participant = digits(context.participant) if context.participant else ""
        name = app.config.whatsapp.bot_name if participant and participant in app.bot_digits else participant
        return Quote(
            id=context.stanzaID,
            sender_name=name,
            sender_id=participant,
            text=quoted_text,
            media=quoted_media,
        )
    return None


MENTION_TOKEN = re.compile(r"@\+?\d[\d\s().-]{3,24}\d")


def _resolve_mentions(text: str, app: App, chat_id: str) -> tuple[str, list[str]]:
    """`@<nomor>` di balasan -> mention asli (JID dari log chat); token tak dikenal dibiarkan."""
    if "@" not in text:
        return text, []
    known: dict[str, str] = {}
    for entry in app.sessions.get(chat_id):
        if entry.sender_id and entry.sender_jid:
            known.setdefault(entry.sender_id, entry.sender_jid)
    if not known:
        return text, []
    mentions: list[str] = []

    def replace(match: re.Match) -> str:
        digits = "".join(ch for ch in match.group(0) if ch.isdigit())
        jid = known.get(digits)
        if jid is not None and jid not in mentions:
            mentions.append(jid)
        return f"@{digits}"  # +/spasi/tanda kurung mematikan mention, jadi selalu dikanonikkan

    return MENTION_TOKEN.sub(replace, text), mentions


async def _chat_presence(client: NewAClient, jid, composing: bool) -> None:
    """Status 'sedang mengetik' di chat asal; kegagalan presence tidak boleh menggagalkan balasan."""
    try:
        await client.send_chat_presence(
            jid,
            ChatPresence.CHAT_PRESENCE_COMPOSING if composing else ChatPresence.CHAT_PRESENCE_PAUSED,
            ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT,
        )
    except Exception:  # presence bersifat kosmetik: cukup dicatat, jangan dilempar
        log.debug("presence gagal", exc_info=True)


async def _keep_typing(client: NewAClient, jid) -> None:
    """Segarkan status mengetik selama proses panjang (tool/LLM) berjalan."""
    while True:
        await asyncio.sleep(TYPING_REFRESH)
        await _chat_presence(client, jid, True)


async def handle_message(client: NewAClient, ev, app: App) -> None:
    source = ev.Info.MessageSource
    chat_id = f"{source.Chat.User}@{source.Chat.Server}"
    chat = chat_id
    sender = f"{source.Sender.User}@{source.Sender.Server}"
    sender_digits = digits(source.Sender.User or source.Sender.RawAgent)

    if chat.endswith("@broadcast"):
        return

    message_id = ev.Info.ID or f"tmp-{time.time_ns()}"
    text = message_text(ev.Message)
    media = describe_media(ev.Message, message_id)
    if not text.strip() and media is None:  # reaksi/protokol tanpa isi tidak masuk log
        return

    message = ChatMessage(
        id=message_id,
        sender_name=_sender_name(ev, sender_digits, sender),
        sender_id=sender_digits,
        when=event_time(ev.Info.Timestamp),
        text=text,
        media=media,
        quote=_quote(ev, app, chat_id),
        outgoing=bool(getattr(ev.Info.MessageSource, "IsFromMe", False)),
        sender_jid=sender,
    )
    app.sessions.append(chat_id, message)
    if not message.outgoing and not is_group(chat):
        app.sessions.remember_name(chat_id, message.sender_name)

    if message.outgoing:
        return
    if sender_digits and sender_digits in app.bot_digits:
        return

    if not should_process(
        ev,
        chat,
        text,
        is_dm=not is_group(chat),
        bot_name=app.config.whatsapp.bot_name,
        require_mention=app.config.whatsapp.require_mention,
        bots=app.bot_digits,
        has_media=media is not None,
    ):
        return

    jid = source.Chat
    await _chat_presence(client, jid, True)
    typing = asyncio.create_task(_keep_typing(client, jid))
    try:
        role = role_for(sender, app.config)
        log.info("pesan chat=%s sender=%s role=%s", chat, sender, role.name)

        async with app.locks[chat_id]:
            reply: str | None = None
            if text.startswith("/"):
                head, _, rest = text[1:].partition(" ")
                name = head.lower()
                handler = COMMANDS.get(name) if name else None
                if handler is None:
                    reply = f"Command /{name} tidak dikenal."
                elif not command_allowed(role, handler.name):
                    reply = f"Command /{name} tidak diizinkan untuk role {role.name}."
                else:
                    ctx = CommandContext(
                        app,
                        chat_id,
                        role,
                        rest.strip(),
                        tuple(commands_for(role)),
                        tuple(tools_for(role)) + tuple(app.hub.specs(role)),
                    )
                    reply = await handler.fn(ctx)
            else:
                specs = list(tools_for(role)) + app.hub.specs(role)
                messages = app.sessions.through(chat_id, message)
                try:
                    content = await build_user_content(
                        app.config,
                        chat_id,
                        messages,
                        message,
                        client,
                        chat_name=app.sessions.chat_name(chat_id),
                    )
                except Exception:  # payload rusak tidak boleh membungkam balasan
                    log.exception("gagal merakit payload chat=%s", chat)
                    content = fallback_content(app.config, message)
                answer = await app.llm.run(
                    system_prompt(app.config, role.name, app.bot_digits),
                    content,
                    specs,
                    ToolContext(
                        app.config,
                        role,
                        tool_patterns(role),
                        chat_id=chat_id,
                        history=app.sessions.store,
                    ),
                    session=f"sanka-{chat_id}",
                )
                reply = answer

            for chunk in format_reply(reply) or ["(kosong)"]:
                chunk, mentions = _resolve_mentions(clean(chunk), app, chat_id)
                payload = (
                    ExtendedTextMessage(text=chunk, contextInfo=ContextInfo(mentionedJID=mentions))
                    if mentions
                    else chunk
                )
                try:
                    sent = await client.reply_message(payload, ev)
                except SendMessageError:
                    log.exception("gagal kirim balasan ke %s", chat)
                    continue
                app.sessions.append(
                    chat_id,
                    ChatMessage(
                        id=getattr(sent, "ID", "") or f"bot-{time.time_ns()}",
                        sender_name=app.config.whatsapp.bot_name,
                        sender_id="",
                        when=datetime.now(),
                        text=chunk,
                        outgoing=True,
                    ),
                )
    finally:
        typing.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing
        await _chat_presence(client, jid, False)
