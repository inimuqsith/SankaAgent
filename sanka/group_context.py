"""Short-lived ambient group context, persisted outside the repository.

Only group text messages are stored. Entries are keyed by an HMAC of the chat
JID, written as JSONL with 0600 permissions, and pruned by age and size. The
model sees a bounded, escaped block; it is data, never instructions.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from sanka.models import InboundMessage

if TYPE_CHECKING:
    from sanka.config import GroupContextConfig

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 1000
_MAX_NAME_CHARS = 40
_MAX_FILE_BYTES = 512_000


class GroupContextProvider(Protocol):
    """Narrow surface the agent runtime needs for prompt context."""

    def context(self, chat_id: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class GroupMessage:
    timestamp: float
    message_id: str
    sender_id: str
    sender_name: str
    text: str


class GroupContextStore:
    """Bounded per-group JSONL store for ambient conversation context."""

    def __init__(
        self,
        root: Path,
        *,
        retention_seconds: float,
        max_messages: int,
        inject_messages: int,
        inject_max_chars: int,
        capture_all: bool,
        capture_group_ids: Collection[str],
        clock: Callable[[], float] = time.time,
    ) -> None:
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")
        if max_messages < 1 or inject_messages < 1:
            raise ValueError("message limits must be positive")
        if inject_max_chars < 1:
            raise ValueError("inject_max_chars must be positive")
        if inject_messages > max_messages:
            raise ValueError("inject_messages must not exceed max_messages")

        self._root = _prepare_root(root)
        self._salt = _load_salt(self._root)
        self._retention_seconds = retention_seconds
        self._max_messages = max_messages
        self._inject_messages = inject_messages
        self._inject_max_chars = inject_max_chars
        self._capture_all = capture_all
        self._capture_group_ids = frozenset(capture_group_ids)
        self._clock = clock
        self._lock = threading.RLock()
        self._cache: dict[str, deque[GroupMessage]] = {}

    def captures(self, chat_id: str) -> bool:
        if not chat_id.endswith("@g.us"):
            return False
        if self._capture_all:
            return True
        return chat_id in self._capture_group_ids

    def record(
        self, message: InboundMessage, *, timestamp: float, message_id: str = ""
    ) -> None:
        """Append one group message; ignore DMs, commands, and empty text."""

        if not self.captures(message.chat_id):
            return
        text = _clean_text(message.text)
        if not text or text.startswith("/"):
            return

        entry = GroupMessage(
            timestamp=float(timestamp),
            message_id=message_id,
            sender_id=self._sender_ref(message.sender_id),
            sender_name=_clean_name(message.sender_name, message.sender_id),
            text=text,
        )
        with self._lock:
            entries = self._entries_locked(message.chat_id)
            entries.append(entry)
            dropped = self._prune_locked(entries)
            self._append_locked(message.chat_id, entry)
            if dropped:
                self._rewrite_file_locked(self._path(message.chat_id), entries)

    def context(self, chat_id: str) -> str | None:
        """Return the newest bounded context block, or ``None`` when empty."""

        if not self.captures(chat_id):
            return None
        with self._lock:
            entries = self._entries_locked(chat_id)
            dropped = self._prune_locked(entries)
            if dropped:
                self._rewrite_file_locked(self._path(chat_id), entries)
            if not entries:
                return None
            selected: list[str] = []
            used = 0
            for entry in reversed(entries):
                line = _render_line(entry)
                if selected and used + len(line) + 1 > self._inject_max_chars:
                    break
                selected.append(line)
                used += len(line) + 1
                if len(selected) >= self._inject_messages:
                    break
            selected.reverse()
            return "\n".join(selected) if selected else None

    def _entries_locked(self, chat_id: str) -> deque[GroupMessage]:
        cached = self._cache.get(chat_id)
        if cached is not None:
            return cached

        entries: deque[GroupMessage] = deque()
        path = self._path(chat_id)
        seen = 0
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    seen += 1
                    entry = _parse_line(line)
                    if entry is not None:
                        entries.append(entry)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("group_context_read_failed reason=%s", type(exc).__name__)

        dropped = self._prune_locked(entries)
        if seen > len(entries) or dropped:
            self._rewrite_file_locked(path, entries)
        self._cache[chat_id] = entries
        return entries

    def _prune_locked(self, entries: deque[GroupMessage]) -> int:
        before = len(entries)
        cutoff = self._clock() - self._retention_seconds
        while entries and entries[0].timestamp < cutoff:
            entries.popleft()
        while len(entries) > self._max_messages:
            entries.popleft()
        return before - len(entries)

    def _append_locked(self, chat_id: str, entry: GroupMessage) -> None:
        path = self._path(chat_id)
        line = json.dumps(
            {
                "ts": entry.timestamp,
                "id": entry.message_id,
                "sender": entry.sender_id,
                "name": entry.sender_name,
                "text": entry.text,
            },
            ensure_ascii=False,
        )
        try:
            fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.write(fd, (line + "\n").encode("utf-8"))
            finally:
                os.close(fd)
            os.chmod(path, 0o600)
            self._maybe_rewrite_locked(path, self._cache[chat_id])
        except OSError as exc:
            logger.warning("group_context_write_failed reason=%s", type(exc).__name__)

    def _maybe_rewrite_locked(
        self, path: Path, entries: deque[GroupMessage]
    ) -> None:
        try:
            if path.stat().st_size <= _MAX_FILE_BYTES:
                return
        except OSError:
            return
        self._rewrite_file_locked(path, entries)

    def _rewrite_file_locked(
        self, path: Path, entries: deque[GroupMessage]
    ) -> None:
        temp = path.with_suffix(".tmp")
        try:
            fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                for entry in entries:
                    handle.write(
                        json.dumps(_entry_payload(entry), ensure_ascii=False) + "\n"
                    )
            os.replace(temp, path)
            os.chmod(path, 0o600)
        except OSError as exc:
            logger.warning(
                "group_context_rewrite_failed reason=%s", type(exc).__name__
            )
            try:
                temp.unlink()
            except OSError:
                pass

    def _path(self, chat_id: str) -> Path:
        digest = hmac.new(
            self._salt, chat_id.encode("utf-8"), hashlib.sha256
        ).hexdigest()[:32]
        return self._root / f"{digest}.jsonl"

    def _sender_ref(self, sender_id: str) -> str:
        return hmac.new(
            self._salt, sender_id.encode("utf-8"), hashlib.sha256
        ).hexdigest()[:16]


def build_group_context(config: GroupContextConfig) -> GroupContextStore | None:
    """Create the store from validated config, or ``None`` when disabled."""

    if not config.enabled:
        return None
    return GroupContextStore(
        Path(config.dir),
        retention_seconds=config.retention_hours * 3600,
        max_messages=config.max_messages,
        inject_messages=config.inject_messages,
        inject_max_chars=config.inject_max_chars,
        capture_all=config.capture_all_groups,
        capture_group_ids=config.capture_group_ids,
    )


def _prepare_root(root: Path) -> Path:
    resolved = Path(root).expanduser().resolve()
    repo = Path(__file__).resolve().parents[1]
    if resolved == repo or repo in resolved.parents:
        raise ValueError("group context directory must be outside the repository")
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(resolved, 0o700)
    return resolved


def _load_salt(root: Path) -> bytes:
    path = root / "salt"
    try:
        existing = path.read_text(encoding="ascii").strip()
        if existing:
            os.chmod(path, 0o600)
            return bytes.fromhex(existing)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        raise ValueError("group context salt is unreadable") from exc

    salt = secrets.token_bytes(32)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(salt.hex())
    return salt


def _clean_text(text: str) -> str:
    return " ".join(text.split())[:_MAX_TEXT_CHARS]


def _clean_name(name: str, sender_id: str) -> str:
    cleaned = " ".join(name.split())[:_MAX_NAME_CHARS]
    if cleaned:
        return cleaned
    digits = "".join(char for char in sender_id.split("@")[0] if char.isdigit())
    suffix = digits[-4:] if digits else "?"
    return f"Peserta {suffix}"


def _parse_line(line: str) -> GroupMessage | None:
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    timestamp = data.get("ts")
    text = data.get("text")
    sender_id = data.get("sender")
    name = data.get("name")
    message_id = data.get("id")
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not isinstance(text, str)
        or not isinstance(sender_id, str)
        or not isinstance(name, str)
        or not isinstance(message_id, str)
    ):
        return None
    return GroupMessage(
        timestamp=float(timestamp),
        message_id=message_id,
        sender_id=sender_id,
        sender_name=name,
        text=text,
    )


def _entry_payload(entry: GroupMessage) -> dict[str, object]:
    return {
        "ts": entry.timestamp,
        "id": entry.message_id,
        "sender": entry.sender_id,
        "name": entry.sender_name,
        "text": entry.text,
    }


def _render_line(entry: GroupMessage) -> str:
    stamp = datetime.fromtimestamp(entry.timestamp).strftime("%H:%M")
    name = html.escape(entry.sender_name, quote=False)
    text = html.escape(entry.text, quote=False)
    return f"[{stamp}] {name}: {text}"
