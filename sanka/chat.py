"""Konteks WhatsApp: log 20 pesan per chat, unduh lampiran, rakit payload untuk LLM."""

from __future__ import annotations

import base64
import contextlib
import logging
import mimetypes
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from neonize.utils import extract_text

from .config import Config

if TYPE_CHECKING:
    from .history import HistoryStore

log = logging.getLogger(__name__)

HISTORY_LIMIT = 20
PREVIEW_LINES = 12
PREVIEW_CHARS = 1500
INLINE_TEXT_LINES = 80
INLINE_TEXT_CHARS = 6000
INLINE_TEXT_BYTES = 512 * 1024
MAX_IMAGES = 6
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_TOTAL = 10 * 1024 * 1024
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MIME_ALIASES = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg", "image/x-png": "image/png"}
MEDIA_FIELDS = ("imageMessage", "videoMessage", "audioMessage", "documentMessage", "stickerMessage")
WRAPPERS = (
    "viewOnceMessage",
    "viewOnceMessageV2",
    "viewOnceMessageV2Extension",
    "ephemeralMessage",
    "documentWithCaptionMessage",
)
FALLBACK_MIME = {
    "image": "image/jpeg",
    "video": "video/mp4",
    "audio": "audio/ogg",
    "document": "application/octet-stream",
    "sticker": "image/webp",
}
EXT_FOR_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "video/mp4": ".mp4",
    "text/plain": ".txt",
    "application/json": ".json",
    "application/zip": ".zip",
}
IMAGE_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
TEXT_MIMES = {"application/json", "application/xml", "application/yaml", "application/x-yaml"}
TEXT_EXT = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".html", ".log", ".ini", ".toml",
    ".py", ".js", ".ts", ".sh", ".sql", ".c", ".h", ".cpp", ".java", ".go", ".rs", ".rb", ".php", ".css",
}
OPAQUE_MIMES = ("", "application/octet-stream")
NAME_LIMIT = 80
FOOTER = (
    "Petunjuk: jawab pesan terakhir. Teks bertanda [dipotong] bisa dibaca lengkap dengan tool read "
    "pada path yang tertera (di sandbox: /work/<path>). Lampiran biner wajib diperiksa dengan tool "
    "sandbox; jangan mengarang isinya."
)


@dataclass
class Media:
    kind: str  # image/video/audio/document/sticker: field asal di Message
    mimetype: str
    filename: str
    size: int
    proto: object = field(repr=False, compare=False)
    path: Path | None = None
    error: str = ""


@dataclass
class Quote:
    id: str
    sender_name: str
    sender_id: str
    text: str
    media: Media | None = None
    when: datetime | None = None


@dataclass
class ChatMessage:
    id: str
    sender_name: str
    sender_id: str
    when: datetime
    text: str
    media: Media | None = None
    quote: Quote | None = None
    outgoing: bool = False
    sender_jid: str = ""  # JID asli (…@lid atau …@s.whatsapp.net) untuk mention balasan


class Sessions:
    """Jendela pesan per chat; ditulis ke SQLite dan dimuat ulang setelah restart."""

    def __init__(self, limit: int = HISTORY_LIMIT, store: HistoryStore | None = None):
        self.limit = max(1, limit)
        self.store = store
        self._data: dict[str, list[ChatMessage]] = {}
        self._loaded: set[str] = set()

    def _ensure(self, chat_id: str) -> None:
        if self.store is None or chat_id in self._loaded:
            return
        try:
            self._data[chat_id] = self.store.recent(chat_id, self.limit)
        except Exception:  # arsip rusak tidak boleh mematikan percakapan
            log.exception("gagal memuat riwayat chat=%s", chat_id)
            self._data.setdefault(chat_id, [])
        self._loaded.add(chat_id)

    @staticmethod
    def _merge(old: ChatMessage, new: ChatMessage) -> ChatMessage:
        """Pesan yang sama bisa datang dari event live dan history sync; pilih yang lebih kaya."""
        return new if (new.media and not old.media) or (new.quote and not old.quote) or (new.text and not old.text) else old

    def append(self, chat_id: str, message: ChatMessage) -> None:
        self._ensure(chat_id)
        history = self._data.setdefault(chat_id, [])
        for index, old in enumerate(reversed(history)):
            if message.id and old.id == message.id:
                history[len(history) - 1 - index] = self._merge(old, message)
                break
        else:
            history.append(message)
        del history[: -self.limit]
        if self.store is not None:
            try:
                self.store.add(chat_id, message)
            except Exception:
                log.exception("gagal menyimpan pesan chat=%s id=%s", chat_id, message.id)

    def get(self, chat_id: str) -> list[ChatMessage]:
        self._ensure(chat_id)
        return list(self._data.get(chat_id, []))

    def find(self, chat_id: str, message_id: str) -> ChatMessage | None:
        if not message_id:
            return None
        for item in reversed(self.get(chat_id)):
            if item.id == message_id:
                return item
        if self.store is None:
            return None
        try:
            return self.store.find(chat_id, message_id)
        except Exception:
            log.exception("gagal mencari pesan lama chat=%s id=%s", chat_id, message_id)
            return None

    def chat_name(self, chat_id: str) -> str:
        if self.store is None:
            return ""
        try:
            return self.store.chat_name(chat_id)
        except Exception:
            log.exception("gagal membaca nama chat=%s", chat_id)
            return ""

    def remember_name(self, chat_id: str, name: str) -> None:
        if self.store is None or not name.strip():
            return
        if self.chat_name(chat_id):
            return
        try:
            self.store.set_chat_name(chat_id, name)
        except Exception:
            log.exception("gagal menyimpan nama chat=%s", chat_id)

    def through(self, chat_id: str, message: ChatMessage) -> list[ChatMessage]:
        """Entri sampai `message` (identitas objek); pesan yang sedang dijawab selalu terakhir."""
        history = self.get(chat_id)
        for index, item in enumerate(history):
            if item is message:
                return history[: index + 1]
        tail = history[-(self.limit - 1) :] if self.limit > 1 else []
        return [*tail, message]

    def ingest(self, chat_id: str, messages: list[ChatMessage]) -> None:
        """Masukkan batch history sync ke arsip; cache chat dimuat ulang saat dibutuhkan."""
        if not messages or self.store is None:
            return
        try:
            self.store.add_many([(chat_id, message) for message in messages])
        except Exception:
            log.exception("gagal menyimpan history sync chat=%s", chat_id)
            return
        loaded = chat_id in self._loaded
        self.invalidate(chat_id)
        if loaded:
            self._ensure(chat_id)

    def invalidate(self, chat_id: str) -> None:
        self._data.pop(chat_id, None)
        self._loaded.discard(chat_id)

    def reset(self, chat_id: str) -> None:
        if self.store is not None:
            try:
                self.store.reset(chat_id)
            except Exception:
                log.exception("gagal menandai reset chat=%s", chat_id)
        self._data.pop(chat_id, None)
        self._loaded.discard(chat_id)


INVISIBLE = dict.fromkeys(map(ord, "\u2068\u2069\u200e\u200f\u202a\u202b\u202c\u202d\u202e"), None)


def clean(text: str) -> str:
    """Buang karakter isolasi bidi yang membungkus mention WhatsApp, normalisasi NBSP."""
    return (text or "").translate(INVISIBLE).replace("\u00a0", " ")


def digits(value: str) -> str:
    """Digit JID tanpa device/server; JID tanpa digit dipakai apa adanya."""
    head = str(value or "").split("@")[0].split(":")[0]
    found = "".join(ch for ch in head if ch.isdigit())
    return found or str(value or "").lower()


def sanitize(value: str, limit: int = 60) -> str:
    cleaned = "".join(
        ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_" for ch in str(value)
    )
    return cleaned.strip("._-")[:limit] or "x"


def _flatten(text: str, limit: int = PREVIEW_CHARS) -> str:
    flat = " ".join(clean(str(text)).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def unwrap_message(message):
    """Turun lewat wrapper (viewOnce/ephemeral/documentWithCaption) sampai isi asli."""
    for _ in range(5):
        inner = None
        for name in WRAPPERS:
            wrapper = getattr(message, name, None)
            if wrapper is not None and wrapper.ListFields():
                inner = wrapper.message
                break
        if inner is None:
            break
        message = inner
    return message


def contexts(message):
    """ContextInfo dari isi pesan (extended text atau media), termasuk wrapper."""
    body = unwrap_message(message)
    for field_name in ("extendedTextMessage", *MEDIA_FIELDS):
        content = getattr(body, field_name, None)
        if content is None or not content.ListFields():
            continue
        context = getattr(content, "contextInfo", None)
        if context is not None:
            yield context


def quote_from_proto(message, message_id: str) -> Quote | None:
    """Kutipan apa adanya dari proto (tanpa lookup log); dipakai history sync."""
    for context in contexts(message):
        quoted = context.quotedMessage
        if not quoted.ListFields():
            continue
        text = message_text(quoted)
        media = describe_media(quoted, context.stanzaID or f"{message_id}-quote")
        if not text.strip() and media is None:
            continue
        participant = context.participant or ""
        sender_id = digits(participant) if participant else ""
        return Quote(
            id=context.stanzaID,
            sender_name=sender_id or participant,
            sender_id=sender_id,
            text=text,
            media=media,
        )
    return None


def message_text(message) -> str:
    return clean(extract_text(unwrap_message(message)) or "")


def _base_name(message_id: str, content) -> str:
    raw = getattr(content, "fileName", "") or getattr(content, "title", "") or ""
    name = sanitize(Path(str(raw)).name, 80) if raw else ""
    return name or sanitize(message_id, 60) or "file"


def describe_media(message, message_id: str) -> Media | None:
    body = unwrap_message(message)
    for field_name in MEDIA_FIELDS:
        content = getattr(body, field_name, None)
        if content is None or not content.ListFields():
            continue
        kind = field_name[: -len("Message")].lower()
        declared = (getattr(content, "mimetype", "") or "").split(";")[0].strip().lower()
        name = _base_name(message_id, content)
        mime = declared or mimetypes.guess_type(name)[0] or FALLBACK_MIME.get(kind, "")
        mime = MIME_ALIASES.get(mime, mime)
        if not Path(name).suffix:
            name = f"{name}{EXT_FOR_MIME.get(mime, '')}"
        return Media(
            kind=kind,
            mimetype=mime or "application/octet-stream",
            filename=name,
            size=int(getattr(content, "fileLength", 0) or 0),
            proto=message,
        )
    return None


def event_time(timestamp: int) -> datetime:
    try:
        value = int(timestamp or 0)
    except (TypeError, ValueError):
        value = 0
    if value > 10**12:  # milidetik
        value //= 1000
    if value <= 0:
        return datetime.now().replace(microsecond=0)
    try:
        return datetime.fromtimestamp(value)
    except (OverflowError, OSError, ValueError):
        return datetime.now().replace(microsecond=0)


def _relative(config: Config, path: Path) -> str:
    try:
        return path.relative_to(config.workspace).as_posix()
    except ValueError:
        return path.as_posix()


def attachment_path(config: Config, chat_id: str, message_id: str, filename: str) -> Path:
    folder = config.workspace / "attachments" / sanitize(chat_id, 40)
    return folder / f"{sanitize(message_id, 60)}-{sanitize(filename, 80)}"


def text_path(config: Config, chat_id: str, message_id: str) -> Path:
    folder = config.workspace / "whatsapp" / sanitize(chat_id, 40)
    return folder / f"{sanitize(message_id or f'tmp-{time.time_ns()}', 60)}.txt"


async def download_media(client, config: Config, chat_id: str, message_id: str, media: Media) -> None:
    """Unduh lampiran sekali; kegagalan dicatat di `media.error` dan tidak pernah dilempar."""
    if media.error:
        return
    if media.path is not None:
        try:
            if media.path.is_file() and media.path.stat().st_size:
                return
        except OSError:
            pass
        media.path = None
    target = attachment_path(config, chat_id, message_id, media.filename)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size:
            media.path = target
            media.size = media.size or target.stat().st_size
            return
        if client is None:
            media.error = "client tidak tersedia"
            return
        if media.proto is None:
            media.error = "proto tidak tersedia"
            return
        await client.download_any(media.proto, str(target))
        if not target.is_file() or not target.stat().st_size:
            with contextlib.suppress(OSError):
                target.unlink(missing_ok=True)
            media.error = "unduhan kosong"
            return
        media.path = target
        media.size = media.size or target.stat().st_size
    except Exception as exc:  # satu lampiran gagal tidak boleh membatalkan balasan
        media.error = f"{type(exc).__name__}: {exc}"[:200]
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)


def _is_image(media: Media) -> bool:
    mime = media.mimetype.lower()
    if mime in MIME_ALIASES:
        media.mimetype = mime = MIME_ALIASES[mime]
    if mime in IMAGE_MIMES:
        return True
    if mime in OPAQUE_MIMES:
        guessed = IMAGE_EXT.get(Path(media.filename).suffix.lower())
        if guessed:
            media.mimetype = guessed
            return True
    return False


def _is_text(media: Media) -> bool:
    mime = media.mimetype.lower()
    if mime.startswith("text/") or mime in TEXT_MIMES:
        return True
    if mime in OPAQUE_MIMES:
        return Path(media.filename).suffix.lower() in TEXT_EXT
    return False


def _clip(text: str, max_lines: int = PREVIEW_LINES, max_chars: int = PREVIEW_CHARS) -> tuple[str, int]:
    """Potong ke `max_lines`/`max_chars`; sisa baris yang tidak tampil dikembalikan sebagai angka."""
    lines = text.splitlines() or [text]
    if len(lines) <= max_lines and len(text) <= max_chars:
        return text, 0
    head = lines[:max_lines]
    joined = "\n".join(head)
    if len(joined) <= max_chars:
        return joined, len(lines) - len(head)
    cut = joined[:max_chars]
    newline = cut.rfind("\n")
    if newline > 0:
        shown = cut[:newline]
        return shown, len(lines) - len(shown.splitlines())
    return cut, len(lines)  # satu baris raksasa: tidak ada baris utuh yang tampil


def _save_full(config: Config, chat_id: str, message_id: str, text: str) -> str:
    path = text_path(config, chat_id, message_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        log.warning("gagal menyimpan teks penuh %s", path, exc_info=True)
        return ""
    return _relative(config, path)


def _clip_marker(remaining: int, path: str) -> str:
    if path:
        return f'  ... [dipotong: masih {remaining} baris; baca lengkap: read path="{path}"]'
    return f"  ... [dipotong: masih {remaining} baris; gagal menyimpan teks penuh]"


def _media_key(config: Config, media: Media) -> str:
    return _relative(config, media.path) if media.path is not None else ""


def _text_lines(config: Config, chat_id: str, message: ChatMessage) -> list[str]:
    body = message.text.rstrip()
    preview, remaining = _clip(body)
    lines = [f"  {line}" for line in preview.splitlines()]
    if remaining:
        lines.append(_clip_marker(remaining, _save_full(config, chat_id, message.id, body)))
    return lines


def _inline_lines(media: Media, relative: str) -> list[str]:
    try:
        size = media.path.stat().st_size
    except OSError as exc:
        return [f"  [gagal dibaca: {type(exc).__name__}]"]
    if size > INLINE_TEXT_BYTES:
        return [f"  [file besar; baca dengan tool read path=\"{relative}\"]"]
    try:
        content = media.path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [f"  [gagal dibaca: {type(exc).__name__}]"]
    preview, remaining = _clip(content, INLINE_TEXT_LINES, INLINE_TEXT_CHARS)
    lines = [f"  {line}" for line in preview.splitlines()]
    if remaining:
        lines.append(_clip_marker(remaining, relative))
    return lines


def _media_lines(
    config: Config,
    media: Media,
    number: int,
    current: bool,
    prefix: str = "lampiran",
) -> list[str]:
    label = "gambar" if _is_image(media) else media.kind
    head = f"  {prefix}: {label} {media.filename} ({media.mimetype}, {human_size(media.size)})"
    if media.error:
        return [f"{head} [gagal diunduh: {media.error}]"]
    if media.path is None:
        return [f"{head} [tidak tersedia]"]
    relative = _relative(config, media.path)
    if number:
        return [f"{head} -> {relative} [gambar #{number}]"]
    if _is_image(media):
        return [f"{head} -> {relative} [tidak diinjeksi; periksa dengan sandbox]"]
    if _is_text(media):
        if not current:
            return [f"{head} -> {relative} [baca dengan tool read]"]
        return [f"{head} -> {relative}", *_inline_lines(media, relative)]
    hint = f"[biner; periksa dengan tool sandbox, mis. file /work/{relative}]"
    return [f"{head} -> {relative} {hint}"]


def fallback_content(config: Config, message: ChatMessage) -> str:
    """Payload minimal saat perakitan penuh gagal: teks pesan terbaru + baris lampirannya."""
    lines = [f"Pesan terbaru: {message.text}"]
    if message.media is not None:
        lines.extend(_media_lines(config, message.media, 0, True))
    return "\n".join(lines)


def _quote_lines(config: Config, chat_id: str, quote: Quote) -> list[str]:
    stamp = f"[{quote.when:%Y-%m-%d %H:%M}] " if quote.when else ""
    name = _flatten(quote.sender_name, NAME_LIMIT) or "-"
    ident = quote.sender_id if quote.sender_id and quote.sender_id != quote.sender_name else ""
    who = f"{name} ({ident})" if ident else name
    body = quote.text.rstrip()
    if body.strip():
        preview, remaining = _clip(body)
        lines = [f'  ↳ membalas {stamp}{who}: "{_flatten(preview)}"']
        if remaining:
            quote_id = quote.id or f"quote-{time.time_ns()}"
            lines.append(_clip_marker(remaining, _save_full(config, chat_id, quote_id, body)))
        return lines
    if quote.media is not None:
        return [f'  ↳ membalas {stamp}{who}: "(lampiran)"']
    return [f'  ↳ membalas {stamp}{who}: "(isi tidak tersedia)"']


def _render_order(messages: list[ChatMessage]) -> list[Media]:
    """Media sesuai urutan render: media kutipan sebelum media pesannya sendiri."""
    order: list[Media] = []
    for entry in messages:
        if entry.quote is not None and entry.quote.media is not None:
            order.append(entry.quote.media)
        if entry.media is not None:
            order.append(entry.media)
    return order


def _choose_images(config: Config, current: ChatMessage, order: list[Media]) -> set[str]:
    priority: list[Media] = []
    if current.media is not None:
        priority.append(current.media)
    if current.quote is not None and current.quote.media is not None:
        priority.append(current.quote.media)
    priority.extend(reversed(order))

    chosen: set[str] = set()
    total = 0
    for media in priority:
        if len(chosen) >= MAX_IMAGES:
            break
        if media.error or media.path is None or not _is_image(media):
            continue
        key = _media_key(config, media)
        if not key or key in chosen:
            continue
        try:
            size = media.path.stat().st_size
        except OSError:
            continue
        if not size or size > MAX_IMAGE_BYTES or total + size > MAX_IMAGE_TOTAL:
            continue
        chosen.add(key)
        total += size
    return chosen


def _plan_images(config: Config, current: ChatMessage, order: list[Media]) -> tuple[dict[str, int], list[Media]]:
    """Nomori gambar terpilih mengikuti urutan render; kembalikan peta key->nomor dan urutannya."""
    chosen = _choose_images(config, current, order)
    numbers: dict[str, int] = {}
    planned: list[Media] = []
    for media in order:
        key = _media_key(config, media)
        if key in chosen and key not in numbers:
            numbers[key] = len(planned) + 1
            planned.append(media)
    return numbers, planned


def _message_lines(
    config: Config,
    chat_id: str,
    entry: ChatMessage,
    numbers: dict[str, int],
    current: ChatMessage,
) -> list[str]:
    stamp = f"[{entry.when:%Y-%m-%d %H:%M}]"
    if entry.outgoing:
        name = _flatten(entry.sender_name or config.whatsapp.bot_name, NAME_LIMIT) or "-"
        head = f"{stamp} {name} (kamu):"
    else:
        name = _flatten(entry.sender_name, NAME_LIMIT) or "-"
        ident = entry.sender_id if entry.sender_id and entry.sender_id != entry.sender_name else ""
        head = f"{stamp} {name} ({ident}):" if ident else f"{stamp} {name}:"
    lines = [head]
    if entry.text.strip():
        lines.extend(_text_lines(config, chat_id, entry))
    elif entry.media is not None:
        lines.append("  (tanpa teks)")
    if entry.quote is not None:
        lines.extend(_quote_lines(config, chat_id, entry.quote))
        if entry.quote.media is not None:
            media = entry.quote.media
            number = numbers.get(_media_key(config, media), 0)
            lines.extend(_media_lines(config, media, number, entry is current, prefix="lampiran kutipan"))
    if entry.media is not None:
        number = numbers.get(_media_key(config, entry.media), 0)
        lines.extend(_media_lines(config, entry.media, number, entry is current))
    return lines


async def _hydrate(config: Config, chat_id: str, entry: ChatMessage, client) -> None:
    if entry.media is not None:
        await download_media(client, config, chat_id, entry.id, entry.media)
    if entry.quote is not None and entry.quote.media is not None:
        quote_id = entry.quote.id or f"{entry.id}-quote"
        await download_media(client, config, chat_id, quote_id, entry.quote.media)


async def build_user_content(
    config: Config,
    chat_id: str,
    messages: list[ChatMessage],
    current: ChatMessage,
    client=None,
    chat_name: str = "",
) -> str | list[dict]:
    """Rakit payload: header konteks, tiap pesan (nama, nomor, waktu, teks, kutipan, lampiran)."""
    for entry in messages:
        await _hydrate(config, chat_id, entry, client)

    order = _render_order(messages)
    numbers, planned = _plan_images(config, current, order)

    label = f' "{_flatten(chat_name, NAME_LIMIT)}"' if chat_name.strip() else ""
    lines = [f"Konteks WhatsApp{label} ({len(messages)} pesan terakhir, lama → baru; waktu lokal):"]
    for entry in messages:
        lines.append("")
        lines.extend(_message_lines(config, chat_id, entry, numbers, current))

    parts: list[dict] = []
    for media in planned:
        number = numbers[_media_key(config, media)]
        try:
            raw = media.path.read_bytes()
        except OSError as exc:
            lines.append(f"Gambar #{number} gagal dilampirkan ({type(exc).__name__})")
            continue
        encoded = base64.b64encode(raw).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media.mimetype};base64,{encoded}", "detail": "high"},
            }
        )

    if planned:
        label = f"#{1}..#{len(planned)}" if len(planned) > 1 else "#1"
        lines.append(f"Gambar {label} dilampirkan berurutan setelah teks ini.")
    lines.append("")
    lines.append(FOOTER)

    text = "\n".join(lines)
    if not parts:
        return text
    return [{"type": "text", "text": text}, *parts]
