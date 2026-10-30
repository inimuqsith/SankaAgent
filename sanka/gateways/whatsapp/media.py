"""Media WhatsApp: deteksi isi file, unduh lampiran, dan baca proto pesan."""

from __future__ import annotations

import contextlib
import logging
import mimetypes
import time
from pathlib import Path

from neonize.utils import extract_text
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message

from ...engine.context import Media, Quote, clean, human_size
from ...engine.config import Config

log = logging.getLogger(__name__)

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
PHOTO_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")
STICKER_MAX_SIDE = 512


def _webp_shape(head: bytes) -> tuple[int, int, bool] | None:
    if len(head) < 30 or head[:4] != b"RIFF" or head[8:12] != b"WEBP":
        return None
    chunk = head[12:16]
    if chunk == b"VP8X":
        flags = head[20]
        width = int.from_bytes(head[24:27], "little") + 1
        height = int.from_bytes(head[27:30], "little") + 1
        return width, height, bool(flags & 0x12)
    if chunk == b"VP8 ":
        width = int.from_bytes(head[26:28], "little") & 0x3FFF
        height = int.from_bytes(head[28:30], "little") & 0x3FFF
        return width, height, False
    if chunk == b"VP8L":
        bits = int.from_bytes(head[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, bool(bits >> 28)
    return None


def send_kind(path: Path) -> str:
    """Klasifikasi isi file untuk jalur kirim: `sticker`, `photo`, atau `file`."""
    try:
        with path.open("rb") as handle:
            head = handle.read(30)
    except OSError:
        return "file"
    if head.startswith(PHOTO_MAGIC):
        return "photo"
    shape = _webp_shape(head)
    if shape is None:
        return "file"
    width, height, marked = shape
    return "sticker" if marked and max(width, height) <= STICKER_MAX_SIDE else "photo"


def sanitize(value: str, limit: int = 60) -> str:
    cleaned = "".join(
        ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_" for ch in str(value)
    )
    return cleaned.strip("._-")[:limit] or "x"


VIEW_ONCE_WRAPPERS = ("viewOnceMessage", "viewOnceMessageV2", "viewOnceMessageV2Extension")


def views_once(message) -> bool:
    for name in VIEW_ONCE_WRAPPERS:
        wrapper = getattr(message, name, None)
        if wrapper is not None and wrapper.ListFields():
            return True
    return False


def unwrap_message(message):
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
    body = unwrap_message(message)
    for field_name in ("extendedTextMessage", *MEDIA_FIELDS):
        content = getattr(body, field_name, None)
        if content is None or not content.ListFields():
            continue
        context = getattr(content, "contextInfo", None)
        if context is not None:
            yield context


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


def quote_from_proto(message, message_id: str) -> Quote | None:
    from ...engine.permissions import normalize

    for context in contexts(message):
        quoted = context.quotedMessage
        if not quoted.ListFields():
            continue
        text = message_text(quoted)
        media = describe_media(quoted, context.stanzaID or f"{message_id}-quote")
        if not text.strip() and media is None:
            continue
        participant = context.participant or ""
        sender_id = normalize(participant) if participant else ""
        return Quote(
            id=context.stanzaID,
            sender_name=sender_id or participant,
            sender_id=sender_id,
            text=text,
            media=media,
        )
    return None


def event_time(timestamp: int):
    from datetime import datetime

    try:
        value = int(timestamp or 0)
    except (TypeError, ValueError):
        value = 0
    if value > 10**12:
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
    if media.proto is None and media.ref:
        try:
            media.proto = Message.FromString(bytes(media.ref))
        except Exception:
            media.error = "proto tidak terbaca"
            return
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
    except Exception as exc:
        media.error = f"{type(exc).__name__}: {exc}"[:200]
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)


__all__ = [
    "IMAGE_MIMES",
    "MAX_IMAGES",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_TOTAL",
    "INLINE_TEXT_BYTES",
    "attachment_path",
    "contexts",
    "describe_media",
    "download_media",
    "event_time",
    "human_size",
    "message_text",
    "quote_from_proto",
    "sanitize",
    "send_kind",
    "text_path",
    "unwrap_message",
    "views_once",
]
