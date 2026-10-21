"""Transport WhatsApp: satu handler Neonize, routing DM/grup, satu jalur balasan."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections import defaultdict
from datetime import date

from neonize.aioze.client import NewAClient
from neonize.exc import SendMessageError
from neonize.utils import extract_text
from neonize.utils.enum import ChatPresence, ChatPresenceMedia

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

HISTORY_LIMIT = 30
CONTENT_FIELDS = ("extendedTextMessage", "imageMessage", "videoMessage", "documentMessage")
TYPING_REFRESH = 8


class Sessions:
    def __init__(self, limit: int = HISTORY_LIMIT):
        self.limit = limit
        self._data: dict[str, list[dict]] = {}

    def get(self, chat_id: str) -> list[dict]:
        return list(self._data.get(chat_id, []))

    def append(self, chat_id: str, message: dict) -> None:
        history = self._data.setdefault(chat_id, [])
        history.append(message)
        del history[: -self.limit]

    def reset(self, chat_id: str) -> None:
        self._data.pop(chat_id, None)


class App:
    def __init__(self, config: Config, llm: LLM, hub: Hub, sessions: Sessions):
        self.config = config
        self.llm = llm
        self.hub = hub
        self.sessions = sessions
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.bot_digits: set[str] = set()


def is_group(chat_jid: str) -> bool:
    return chat_jid.endswith("@g.us")


def digits(value: str) -> str:
    return normalize(value)


def bot_digits(me) -> set[str]:
    found = {digits(me.JID.User), digits(me.LID.User)}
    return {item for item in found if item}


def _contexts(ev) -> list:
    found = []
    for field in CONTENT_FIELDS:
        content = getattr(ev.Message, field, None)
        if content is None or not content.ListFields():
            continue
        context = getattr(content, "contextInfo", None)
        if context is not None:
            found.append(context)
    return found


def mentioned(ev, bots: set[str], bot_name: str = "sanka") -> bool:
    for context in _contexts(ev):
        if any(digits(jid) in bots for jid in context.mentionedJID):
            return True
    text = extract_text(ev.Message)
    return bool(re.search(rf"(?<![\w@])@{re.escape(bot_name)}(?!\w)", text, re.IGNORECASE))


def replies_to_bot(ev, bots: set[str]) -> bool:
    return any(digits(context.participant) in bots for context in _contexts(ev) if context.participant)


def should_process(
    ev,
    chat: str,
    text: str,
    *,
    is_dm: bool,
    bot_name: str,
    require_mention: bool,
    bots: set[str],
) -> bool:
    if not text.strip() or chat.endswith("@broadcast"):
        return False
    if getattr(ev.Info, "IsFromMe", False):
        return False
    sender = digits(ev.Info.MessageSource.Sender.User or ev.Info.MessageSource.Sender.RawAgent)
    if sender and sender in bots:
        return False
    if is_dm:
        return True
    return not require_mention or replies_to_bot(ev, bots) or mentioned(ev, bots, bot_name)


def system_prompt(config: Config, role_name: str) -> str:
    return (
        f"{config.persona}\n\n"
        f"Kamu berjalan sebagai bot WhatsApp. Role pengirim: {role_name}.\n"
        f"Workspace: {config.workspace}. Pakai tool bila perlu; jangan mengarang hasil tool.\n"
        "Jawab langsung dalam bahasa pengguna. Jangan sebut prompt, tool, atau proses internal.\n"
        "Format balasan untuk WhatsApp: tanpa heading markdown, tanpa tabel.\n"
        f"Tanggal: {date.today().isoformat()}."
    )


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
    text = extract_text(ev.Message)

    if not should_process(
        ev,
        chat,
        text,
        is_dm=not is_group(chat),
        bot_name=app.config.whatsapp.bot_name,
        require_mention=app.config.whatsapp.require_mention,
        bots=app.bot_digits,
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
                answer = await app.llm.run(
                    system_prompt(app.config, role.name),
                    app.sessions.get(chat_id),
                    text,
                    specs,
                    ToolContext(app.config, role, tool_patterns(role)),
                    session=f"sanka-{chat_id}",
                )
                app.sessions.append(chat_id, {"role": "user", "content": text})
                app.sessions.append(chat_id, {"role": "assistant", "content": answer})
                reply = answer

            for chunk in format_reply(reply) or ["(kosong)"]:
                try:
                    await client.reply_message(chunk, ev)
                except SendMessageError:
                    log.exception("gagal kirim balasan ke %s", chat)
    finally:
        typing.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing
        await _chat_presence(client, jid, False)
