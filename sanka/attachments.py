"""Chat-bound attachment storage for inbound WhatsApp media (plan-12).

Files live outside the repository under a directory the owner chooses. The wire
never carries a path: bridge uploads bytes, gateway assigns a random ID, and
later lookups resolve that ID internally. Stored names have no extension so a
downloaded file can never be executed by accident.

Security invariants:
- only ids matching ``[0-9a-f]{32}`` resolve to paths;
- a record is readable only with the same ``chat_id`` that uploaded it;
- images and PDF/XLSX are checked by magic bytes, not just MIME;
- every file is ``0600`` in a ``0700`` directory;
- expired files and quota overflow are deleted by ``cleanup()``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from sanka.config import AttachmentsConfig, SankaConfig

_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_JID_PATTERN = re.compile(r"^[0-9A-Za-z._:\-]+@[0-9A-Za-z.\-]+$")
_IMAGE_MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
_DOCUMENT_MIMES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
        "text/markdown",
        "text/plain",
    }
)
_EXTENSION_MIMES = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}
_IMAGE_EXTENSION_MIMES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_MIME_ALIASES = {
    "image/jpg": "image/jpeg",
    "text/x-markdown": "text/markdown",
    "text/md": "text/markdown",
    "text/x-csv": "text/csv",
    "application/csv": "text/csv",
}
_MAX_NAME_CHARS = 128
_MAX_SIDECAR_BYTES = 16_384
_TMP_PREFIX = ".tmp-"
_MAX_TMP_AGE_SECONDS = 3600


class AttachmentError(Exception):
    """A safe upload/lookup failure with a stable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AttachmentRecord:
    """Metadata stored next to one attachment byte file."""

    attachment_id: str
    chat_id: str
    sender_id: str
    message_id: str
    kind: str
    mime: str
    name: str
    size: int
    sha256: str
    created_at: float


def sanitize_filename(raw: str, *, kind: str) -> str:
    """Return a display-only filename without path separators or controls."""

    value = unicodedata.normalize("NFKC", raw or "")
    cleaned = "".join(
        character
        for character in value
        if character.isprintable() and character not in ("/", "\\")
    )
    cleaned = " ".join(cleaned.split())[:_MAX_NAME_CHARS]
    if cleaned:
        return cleaned
    return "gambar" if kind == "image" else "dokumen"


def canonical_mime(kind: str, mime: str, name: str) -> str:
    """Validate MIME for ``kind`` and infer it from the filename if needed."""

    value = (mime or "").split(";", 1)[0].strip().lower()
    value = _MIME_ALIASES.get(value, value)
    if value == "application/vnd.ms-excel" and name.lower().endswith(".csv"):
        value = "text/csv"
    allowed = _IMAGE_MIMES if kind == "image" else _DOCUMENT_MIMES
    if value in allowed:
        return value
    if value in ("", "application/octet-stream"):
        suffix = Path(name).suffix.lower()
        if kind == "image":
            inferred_image = _IMAGE_EXTENSION_MIMES.get(suffix)
            if inferred_image is not None:
                return inferred_image
        else:
            inferred = _EXTENSION_MIMES.get(suffix)
            if inferred is not None:
                return inferred
    raise AttachmentError("unsupported_media")


def _magic_ok(mime: str, header: bytes) -> bool:
    if mime == "image/jpeg":
        return header.startswith(b"\xff\xd8\xff")
    if mime == "image/png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    if mime == "application/pdf":
        return header.startswith(b"%PDF-")
    if mime.endswith("spreadsheetml.sheet"):
        return header.startswith(b"PK\x03\x04")
    return True


class AttachmentStore:
    """Filesystem-backed, chat-bound attachment store."""

    def __init__(
        self,
        directory: str,
        *,
        max_bytes: int,
        retention_seconds: int,
        max_total_bytes: int,
        max_per_message: int = 4,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not directory:
            raise ValueError("attachment directory must not be empty")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if retention_seconds < 1:
            raise ValueError("retention_seconds must be positive")
        if max_total_bytes < max_bytes:
            raise ValueError("max_total_bytes must be at least max_bytes")
        if not 1 <= max_per_message <= 10:
            raise ValueError("max_per_message must be between 1 and 10")

        self._root = Path(directory)
        self._max_bytes = max_bytes
        self._retention_seconds = retention_seconds
        self._max_total_bytes = max_total_bytes
        self._max_per_message = max_per_message
        self._clock = clock
        self._id_factory = id_factory or (lambda: secrets.token_hex(16))
        self._lock = threading.RLock()
        self._prepare_directory()
        with self._lock:
            self.cleanup()

    @classmethod
    def from_config(
        cls,
        config: SankaConfig,
        *,
        clock: Callable[[], float] = time.time,
    ) -> AttachmentStore | None:
        """Build a store, or return ``None`` when the feature is disabled."""

        settings: AttachmentsConfig = config.attachments
        if not settings.dir:
            return None
        return cls(
            settings.dir,
            max_bytes=settings.max_bytes,
            retention_seconds=settings.retention_minutes * 60,
            max_total_bytes=settings.max_total_bytes,
            max_per_message=settings.max_per_message,
            clock=clock,
        )

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def max_per_message(self) -> int:
        return self._max_per_message

    @property
    def directory(self) -> Path:
        return self._root

    def save(
        self,
        chunks: Iterable[bytes],
        *,
        chat_id: str,
        sender_id: str,
        message_id: str,
        kind: str,
        mime: str,
        name: str,
        declared_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> AttachmentRecord:
        """Write one attachment and return its record.

        ``chunks`` is consumed exactly once. The write is atomic: a failure at
        any point removes the temporary file and leaves no partial record.
        """

        if kind not in ("image", "document"):
            raise AttachmentError("invalid_metadata")
        for value in (chat_id, sender_id):
            if not self._is_jid(value):
                raise AttachmentError("invalid_metadata")
        if not isinstance(message_id, str) or not 0 < len(message_id) <= 200:
            raise AttachmentError("invalid_metadata")
        if declared_size is not None:
            if declared_size < 1:
                raise AttachmentError("invalid_metadata")
            if declared_size > self._max_bytes:
                raise AttachmentError("too_large")

        safe_name = sanitize_filename(name, kind=kind)
        canonical = canonical_mime(kind, mime, safe_name)
        expected_hash = None
        if expected_sha256 is not None:
            if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
                raise AttachmentError("invalid_metadata")
            expected_hash = expected_sha256.lower()

        self._prepare_directory()
        attachment_id = self._new_id()
        temp_path = self._root / f"{_TMP_PREFIX}{attachment_id}"
        digest = hashlib.sha256()
        size = 0
        descriptor = os.open(
            temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                for chunk in chunks:
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise AttachmentError("invalid_metadata")
                    data = bytes(chunk)
                    size += len(data)
                    if size > self._max_bytes:
                        raise AttachmentError("too_large")
                    digest.update(data)
                    handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())

            if size == 0 or (
                declared_size is not None and size != declared_size
            ):
                raise AttachmentError("invalid_metadata")

            with temp_path.open("rb") as handle:
                header = handle.read(16)
            if not _magic_ok(canonical, header):
                raise AttachmentError("unsupported_media")

            actual_hash = digest.hexdigest()
            if expected_hash is not None and actual_hash != expected_hash:
                raise AttachmentError("invalid_metadata")

            record = AttachmentRecord(
                attachment_id=attachment_id,
                chat_id=chat_id,
                sender_id=sender_id,
                message_id=message_id,
                kind=kind,
                mime=canonical,
                name=safe_name,
                size=size,
                sha256=actual_hash,
                created_at=self._clock(),
            )
            final_path = self._bin_path(attachment_id)
            os.replace(temp_path, final_path)
            os.chmod(final_path, 0o600)
            self._write_sidecar(record)
        except Exception:
            for leftover in (
                temp_path,
                self._root / f"{_TMP_PREFIX}{attachment_id}.json",
                self._bin_path(attachment_id),
            ):
                self._unlink(leftover)
            raise

        with self._lock:
            self.cleanup()
        return record

    def get(
        self, attachment_id: str, *, chat_id: str | None = None
    ) -> AttachmentRecord | None:
        """Return a live record, optionally checking chat ownership."""

        if not self._valid_id(attachment_id):
            return None
        sidecar = self._json_path(attachment_id)
        try:
            raw = sidecar.read_text(encoding="utf-8")
        except OSError:
            return None
        if len(raw) > _MAX_SIDECAR_BYTES:
            return None
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            return None
        record = self._record_from_json(attachment_id, data)
        if record is None:
            return None
        if chat_id is not None and record.chat_id != chat_id:
            return None
        if self._clock() - record.created_at >= self._retention_seconds:
            self._delete(attachment_id)
            return None
        try:
            if self._bin_path(attachment_id).stat().st_size != record.size:
                return None
        except OSError:
            return None
        return record

    def resolve(self, record: AttachmentRecord) -> Path:
        """Return the internal path for a record previously obtained from get()."""

        if not self._valid_id(record.attachment_id):
            raise AttachmentError("invalid_metadata")
        return self._bin_path(record.attachment_id)

    def read_bytes(self, record: AttachmentRecord) -> bytes:
        path = self.resolve(record)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise AttachmentError("not_found") from exc

    def cleanup(self) -> None:
        """Delete expired, orphaned, and over-quota attachments."""

        now = self._clock()
        records: list[AttachmentRecord] = []
        with self._lock:
            try:
                entries = list(self._root.iterdir())
            except OSError:
                return

            for entry in entries:
                if not entry.is_file():
                    continue
                if entry.name.startswith(_TMP_PREFIX):
                    self._remove_if_old(entry, now)
                    continue
                if entry.suffix == ".json":
                    record = self._read_sidecar_file(entry)
                    if record is None:
                        self._unlink(entry)
                        continue
                    if now - record.created_at >= self._retention_seconds:
                        self._delete(record.attachment_id)
                        continue
                    if not self._bin_path(record.attachment_id).exists():
                        self._unlink(entry)
                        continue
                    records.append(record)
                elif entry.suffix == ".bin":
                    sidecar = entry.with_suffix(".json")
                    if not sidecar.exists():
                        self._unlink(entry)

            total = sum(record.size for record in records)
            if total > self._max_total_bytes:
                for record in sorted(records, key=lambda item: item.created_at):
                    if total <= self._max_total_bytes:
                        break
                    self._delete(record.attachment_id)
                    total -= record.size

    def _prepare_directory(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self._root, 0o700)
        except OSError:
            pass

    def _new_id(self) -> str:
        for _ in range(10):
            candidate = self._id_factory()
            if self._valid_id(candidate) and not self._bin_path(candidate).exists():
                return candidate
        raise AttachmentError("invalid_metadata")

    def _write_sidecar(self, record: AttachmentRecord) -> None:
        payload = {
            "chat_id": record.chat_id,
            "sender_id": record.sender_id,
            "message_id": record.message_id,
            "kind": record.kind,
            "mime": record.mime,
            "name": record.name,
            "size": record.size,
            "sha256": record.sha256,
            "created_at": record.created_at,
        }
        temp = self._root / f"{_TMP_PREFIX}{record.attachment_id}.json"
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, self._json_path(record.attachment_id))

    def _read_sidecar_file(self, path: Path) -> AttachmentRecord | None:
        try:
            if path.stat().st_size > _MAX_SIDECAR_BYTES:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            return None
        return self._record_from_json(path.stem, data)

    def _record_from_json(
        self, attachment_id: str, data: object
    ) -> AttachmentRecord | None:
        if not isinstance(data, dict):
            return None
        try:
            record = AttachmentRecord(
                attachment_id=attachment_id,
                chat_id=str(data["chat_id"]),
                sender_id=str(data["sender_id"]),
                message_id=str(data["message_id"]),
                kind=str(data["kind"]),
                mime=str(data["mime"]),
                name=str(data["name"]),
                size=int(data["size"]),
                sha256=str(data["sha256"]),
                created_at=float(data["created_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if (
            record.kind not in ("image", "document")
            or not self._is_jid(record.chat_id)
            or not self._is_jid(record.sender_id)
            or record.size < 1
            or not _ID_PATTERN.fullmatch(record.attachment_id)
        ):
            return None
        try:
            canonical_mime(record.kind, record.mime, record.name)
        except AttachmentError:
            return None
        return record

    def _delete(self, attachment_id: str) -> None:
        self._unlink(self._json_path(attachment_id))
        self._unlink(self._bin_path(attachment_id))

    def _remove_if_old(self, path: Path, now: float) -> None:
        try:
            if now - path.stat().st_mtime >= _MAX_TMP_AGE_SECONDS:
                self._unlink(path)
        except OSError:
            pass

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _bin_path(self, attachment_id: str) -> Path:
        return self._root / f"{attachment_id}.bin"

    def _json_path(self, attachment_id: str) -> Path:
        return self._root / f"{attachment_id}.json"

    @staticmethod
    def _valid_id(value: str) -> bool:
        return isinstance(value, str) and _ID_PATTERN.fullmatch(value) is not None

    @staticmethod
    def _is_jid(value: str) -> bool:
        return isinstance(value, str) and _JID_PATTERN.fullmatch(value) is not None


def decode_filename_header(value: str | None) -> str:
    """Decode the bridge's base64url filename header; empty is allowed."""

    if not value:
        return ""
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(value + padding, altchars=b"-_", validate=True)
        return raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise AttachmentError("invalid_metadata") from exc
