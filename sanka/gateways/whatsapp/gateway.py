"""Gateway WhatsApp: lifecycle Neonize, kirim/terima, direktori, dan media."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import re
import time
from pathlib import Path

from neonize.aioze.client import NewAClient
from neonize.aioze.events import ConnectedEv, GroupInfoEv, HistorySyncEv, MessageEv
from neonize.exc import SendMessageError
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ContextInfo, ExtendedTextMessage
from neonize.utils.enum import ChatPresence, ChatPresenceMedia
from neonize.utils.jid import build_jid

from ...engine.config import Config, GatewaySettings, secure_db
from ...engine.context import ChatMessage, human_size
from ...engine.inbound import GatewayError, Inbound
from ...engine.registry import Handler, discover
from ...engine.tools import FAMILIES
from ...engine.reply import FENCE, clean_prose, strip_blocks
from . import directory, history_sync, ingress
from .config import WhatsAppSettings
from .media import download_media, send_kind

log = logging.getLogger(__name__)

MAX_SEND_BYTES = 100 * 1024 * 1024
GROUP_INFO_TIMEOUT = 10.0
GROUP_NAME_RETRY = 300.0
REACTION_COOLDOWN = 300.0
MENTION_TOKEN = re.compile(r"@\+?\d[\d\s().-]{3,24}\d")


class WhatsAppGateway:
    name = "whatsapp"
    capabilities = frozenset({"text", "file", "reaction", "typing", "media", "directory"})

    def __init__(
        self,
        config: Config,
        db,
        history,
        windows,
        *,
        settings: GatewaySettings | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.history = history
        self.windows = windows
        raw = settings or config.gateways.get("whatsapp") or GatewaySettings("whatsapp")
        self.settings = WhatsAppSettings.from_mapping(
            raw.enabled, raw.options, config.paths.config.parent
        )
        self.client: NewAClient | None = None
        self.me = None
        self.reactions: dict[str, float] = {}
        self.name_retry: dict[str, float] = {}
        self.backfill = None
        self._stopping = False
        base = Path(__file__).parent
        self._tools = discover(__package__ + ".tools", base / "tools", "TOOL", "tool", FAMILIES)
        self._commands = discover(__package__ + ".commands", base / "commands", "COMMAND", "command")

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    @property
    def backlog_seconds(self) -> float:
        return self.settings.backlog_seconds

    def tools(self) -> list[Handler]:
        return list(self._tools)

    def commands(self) -> list[Handler]:
        return list(self._commands)

    # ------------------------------------------------------------ lifecycle

    async def run(self, agent) -> None:
        session = self.settings.session_db
        self.client = NewAClient(
            str(session),
            props=history_sync.history_props(history_sync.needs_full_sync(self.db)),
        )
        client = self.client

        @client.event(MessageEv)
        async def on_message(accepted: NewAClient, ev) -> None:
            self.me = accepted.me or self.me
            try:
                if ev.Info.MessageSource.Chat.Server == "g.us":
                    await self.ensure_group_name(ev.Info.MessageSource.Chat.User + "@g.us")
                inbound = ingress.event_to_inbound(ev, self)
                if inbound is None:
                    return
                await agent.handle(inbound, self)
            except Exception:
                log.exception("handler pesan WhatsApp gagal")

        @client.event(HistorySyncEv)
        async def on_history(accepted: NewAClient, ev) -> None:
            if ev.Data is None:
                return
            try:
                count, chat_ids, done = history_sync.ingest_sync(
                    self.db, self.history, ev.Data, self.config.soul.name
                )
                for chat_id in chat_ids:
                    self.windows.invalidate(chat_id)
                log.info(
                    "history sync: %d pesan, %d chat, sync=%s progress=%d%s",
                    count,
                    len(chat_ids),
                    ev.Data.syncType,
                    ev.Data.progress,
                    " (done)" if done else "",
                )
            except Exception:
                log.exception("ingest history sync gagal")

        @client.event(GroupInfoEv)
        async def on_group_info(accepted: NewAClient, ev) -> None:
            if not ev.JID.User:
                return
            name = getattr(ev.Name, "Name", "").strip()
            if name:
                directory.set_chat_name(self.db, f"{ev.JID.User}@{ev.JID.Server}", name)

        @client.event(ConnectedEv)
        async def on_connected(accepted: NewAClient, ev) -> None:
            self.me = accepted.me or self.me
            secure_db(session)
            try:
                await asyncio.to_thread(directory.import_contacts, self.db, session)
            except Exception:
                log.exception("gagal menyegarkan kontak")
            try:
                named = await directory.sync_group_names(accepted, self.db)
                if named:
                    log.info("nama grup tersinkron: %d", named)
            except Exception:
                log.exception("gagal menyinkronkan nama grup")
            log.info("Sanka tersambung ke WhatsApp")

        log.info("WhatsApp: menyiapkan sesi %s", session)
        await client.connect()
        secure_db(session)
        await client.idle()

    async def stop(self) -> None:
        self._stopping = True
        if self.client is not None:
            with contextlib.suppress(Exception):
                await self.client.stop()
            self.client = None

    def data_paths(self) -> list[Path]:
        return [self.settings.session_db]

    # --------------------------------------------------------------- kirim

    def _jid(self, chat_id: str):
        user, _, server = chat_id.partition("@")
        return build_jid(user, server)

    def _resolve_mentions(self, text: str, chat_id: str) -> tuple[str, list[str]]:
        if "@" not in text:
            return text, []
        known: dict[str, str] = {}
        for entry in self.windows.get(chat_id):
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
            return f"@{digits}"

        return MENTION_TOKEN.sub(replace, text), mentions

    async def send_text(
        self, chat_id: str, text: str, *, reply: Inbound | None = None
    ) -> str:
        if self.client is None:
            raise GatewayError("client WhatsApp tidak tersedia")
        body, mentions = self._resolve_mentions(str(text or ""), chat_id)
        payload = (
            ExtendedTextMessage(text=body, contextInfo=ContextInfo(mentionedJID=mentions))
            if mentions
            else body
        )
        try:
            if reply is not None and reply.raw is not None:
                sent = await self.client.reply_message(payload, reply.raw)
            else:
                sent = await self.client.send_message(self._jid(chat_id), payload)
        except SendMessageError as exc:
            log.exception("gagal mengirim teks ke %s", chat_id)
            raise GatewayError(str(exc)) from exc
        except Exception as exc:
            log.exception("gagal mengirim teks ke %s", chat_id)
            raise GatewayError(f"{type(exc).__name__}: {exc}") from exc
        return getattr(sent, "ID", "") or f"text-{time.time_ns()}"

    async def send_file(
        self,
        chat_id: str,
        path: Path,
        caption: str = "",
        *,
        reply: Inbound | None = None,
    ) -> str:
        if self.client is None:
            raise GatewayError("client WhatsApp tidak tersedia")
        target = Path(path).resolve()
        if not target.is_file():
            raise GatewayError(f"file tidak ditemukan: {path}")
        if not target.is_relative_to(self.config.workspace.resolve()):
            raise GatewayError(f"path di luar workspace: {path}")
        size = target.stat().st_size
        if size > MAX_SEND_BYTES:
            raise GatewayError(
                f"file terlalu besar ({human_size(size)}; maksimal {human_size(MAX_SEND_BYTES)})"
            )
        kind = send_kind(target)
        jid = self._jid(chat_id)
        try:
            if kind == "sticker":
                sent = await self.client.send_sticker(jid, str(target))
                if caption:
                    await self.client.send_message(jid, caption)
            elif kind == "photo":
                sent = await self.client.send_image(jid, str(target), caption=caption or None)
            else:
                sent = await self.client.send_document(
                    jid, str(target), caption=caption or None, filename=target.name
                )
        except Exception as exc:
            log.exception("gagal mengirim file %s ke %s", target, chat_id)
            raise GatewayError(f"{type(exc).__name__}: {exc}") from exc
        return getattr(sent, "ID", "") or f"file-{time.time_ns()}"

    def _sticker_paths(self) -> list[Path]:
        found: list[Path] = []
        for name in self.config.humanize.reaction_stickers:
            path = Path(str(name)).expanduser()
            path = path if path.is_absolute() else self.config.workspace / path
            try:
                if path.is_file() and send_kind(path) == "sticker":
                    found.append(path)
            except OSError:
                continue
        return found

    async def send_reaction(self, chat_id: str, message_id: str) -> str | None:
        if self.client is None or not self.config.humanize.reaction_stickers:
            return None
        moment = time.time()
        if moment - self.reactions.get(chat_id, 0.0) < REACTION_COOLDOWN:
            return None
        paths = self._sticker_paths()
        if not paths:
            log.info("reaksi dilewati: tidak ada stiker reaksi chat=%s", chat_id)
            return None
        path = random.choice(paths)
        try:
            await self.client.send_sticker(self._jid(chat_id), str(path))
        except Exception:
            log.warning("gagal mengirim stiker reaksi chat=%s", chat_id, exc_info=True)
            return None
        self.reactions[chat_id] = moment
        return f"(stiker reaksi: {path.name})"

    async def typing(self, chat_id: str, composing: bool) -> None:
        if self.client is None:
            return
        try:
            await self.client.send_chat_presence(
                self._jid(chat_id),
                ChatPresence.CHAT_PRESENCE_COMPOSING
                if composing
                else ChatPresence.CHAT_PRESENCE_PAUSED,
                ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT,
            )
        except Exception:
            log.debug("presence gagal", exc_info=True)

    async def prepare_media(self, chat_id: str, message: ChatMessage) -> None:
        if message.media is not None:
            await download_media(self.client, self.config, chat_id, message.id, message.media)
        if message.quote is not None and message.quote.media is not None:
            quote_id = message.quote.id or f"{message.id}-quote"
            await download_media(self.client, self.config, chat_id, quote_id, message.quote.media)

    # ------------------------------------------------------------- tampilan

    def render(self, text: str) -> str:
        pieces = FENCE.split(strip_blocks(str(text or "")))
        cleaned = "".join(
            piece if piece.startswith("```") else clean_prose(piece) for piece in pieces
        )
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    def prompt_hints(self) -> str:
        handles = ", ".join(sorted(self.self_handles())) or "-"
        return (
            "## WhatsApp interface\n"
            "- Use WhatsApp formatting: *bold*, _italic_, ~strike~, ```mono```. No markdown "
            "headings, no tables.\n"
            "- Start list items with '• '; never use '- ' or numbers with dots for bullets.\n"
            "- To mention someone, write @<number> exactly as it appears in the payload (e.g. "
            "@6285111242960), without '+', spaces, or punctuation; the runtime converts it into a "
            "real mention.\n"
            f"- Your handles: {handles}. A mention of any of them is meant for you, not for another "
            "person. When you refer to yourself, use those handles.\n"
            "- Do not @mention someone in a DM unless it is genuinely necessary."
        )

    def self_handles(self) -> set[str]:
        return ingress.self_handles(self.me)

    def chat_name(self, chat_id: str) -> str:
        return directory.chat_name(self.db, chat_id)

    async def ensure_group_name(self, chat_id: str) -> None:
        if directory.chat_name(self.db, chat_id):
            return
        moment = time.monotonic()
        if moment - self.name_retry.get(chat_id, 0.0) < GROUP_NAME_RETRY:
            return
        self.name_retry[chat_id] = moment
        getter = getattr(self.client, "get_group_info", None)
        if not callable(getter):
            return
        try:
            info = await asyncio.wait_for(getter(self._jid(chat_id)), GROUP_INFO_TIMEOUT)
        except Exception:
            log.debug("gagal mengambil nama grup %s", chat_id, exc_info=True)
            return
        name = directory.group_name(info)
        if name:
            directory.set_chat_name(self.db, chat_id, name)

    def list_targets(self, query: str, limit: int) -> list[tuple[str, str]]:
        return directory.list_targets(self.db, query, limit)

    def resolve_target(self, query: str):
        targets = directory.list_targets(self.db, query, 50)
        return directory.resolve_target(targets, query)
