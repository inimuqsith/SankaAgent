"""Outbound side of the gateway: bridge client and delivery queue.

Every message that leaves Sanka for WhatsApp passes through
:class:`OutboundQueue`, which formats text, enforces one global rate window,
retries only retryable bridge failures, and drops overflow items. Logs carry
metadata only: never message text, full JIDs, or tokens.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

import httpx

from sanka.formatter import Formatter, ResponseKind

logger = logging.getLogger(__name__)

_RATE_WINDOW_SECONDS = 60.0
_MAX_RETRY_AFTER_SECONDS = 30.0
_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
_MIN_WAIT_SECONDS = 0.001


class OutboundKind(str, Enum):
    SEND = "send"
    TYPING = "typing"
    READ = "read"


@dataclass(frozen=True, slots=True)
class OutboundItem:
    """One unit of work for the outbound worker."""

    kind: OutboundKind
    chat_id: str
    text: str = ""
    idempotency_key: str = ""
    message_id: str = ""
    state: str = "composing"


class BridgeClient(Protocol):
    """Minimal bridge contract used by the queue."""

    def send(self, chat_id: str, text: str, idempotency_key: str) -> str | None: ...

    def typing(self, chat_id: str, state: str) -> None: ...

    def read(self, chat_id: str, message_id: str) -> None: ...


class BridgeError(Exception):
    """A bridge call failed.

    ``retryable`` marks connection errors, timeouts, ``429``, and ``5xx``.
    ``retry_after`` is the bridge's requested delay in seconds, if any.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.retry_after = retry_after


class HttpBridgeClient:
    """HTTP client for the local bridge (ADR-0018).

    ``409`` on ``/v1/send`` means the chunk was already delivered, so it is
    reported as success (``None``). Any other non-2xx response raises
    :class:`BridgeError` with the retry classification already applied.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url must not be empty")
        if not token:
            raise ValueError("token must not be empty")

        self._headers = {"X-Sanka-Token": token}
        self._owns_client = client is None
        self._http = client or httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        if self._owns_client:
            self._http.close()

    def send(self, chat_id: str, text: str, idempotency_key: str) -> str | None:
        """Deliver one chunk; return the bridge message id when provided."""
        response = self._post(
            "/v1/send",
            {"chat_id": chat_id, "text": text, "idempotency_key": idempotency_key},
        )
        status = response.status_code
        if status == 409:
            return None
        if 200 <= status < 300:
            return _message_id(response)
        raise self._error_for(status, response)

    def typing(self, chat_id: str, state: str) -> None:
        """Set the typing indicator for ``chat_id``."""
        response = self._post("/v1/typing", {"chat_id": chat_id, "state": state})
        if not 200 <= response.status_code < 300:
            raise self._error_for(response.status_code, response)

    def read(self, chat_id: str, message_id: str) -> None:
        """Mark one inbound message as read."""
        response = self._post(
            "/v1/read", {"chat_id": chat_id, "message_id": message_id}
        )
        if not 200 <= response.status_code < 300:
            raise self._error_for(response.status_code, response)

    def _post(self, path: str, body: dict[str, str]) -> httpx.Response:
        try:
            return self._http.post(path, json=body, headers=self._headers)
        except httpx.HTTPError as exc:
            # Transport details may include URLs; keep the surface generic.
            raise BridgeError("bridge request failed", retryable=True) from exc

    @staticmethod
    def _error_for(status: int, response: httpx.Response) -> BridgeError:
        retryable = status == 429 or status >= 500
        return BridgeError(
            f"bridge returned HTTP {status}",
            retryable=retryable,
            status=status,
            retry_after=_retry_after(response) if retryable else None,
        )


class OutboundQueue:
    """Single-worker, per-chat FIFO queue with a global send rate window."""

    def __init__(
        self,
        bridge: BridgeClient,
        formatter: Formatter,
        *,
        per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        max_per_chat: int = 32,
        max_attempts: int = 3,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._bridge = bridge
        self._formatter = formatter
        self._per_minute = max(1, int(per_minute))
        self._clock = clock
        self._max_per_chat = max(1, int(max_per_chat))
        self._max_attempts = max(1, int(max_attempts))
        self._sleep = sleep if sleep is not None else self._interruptible_sleep

        self._cond = threading.Condition()
        self._queues: dict[str, deque[OutboundItem]] = {}
        self._order: deque[str] = deque()
        self._active: set[str] = set()
        self._window: deque[float] = deque()
        self._stopping = False
        self._busy = False
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start the worker once; safe to call repeatedly."""
        if self._thread is not None and self._thread.is_alive():
            return
        thread = threading.Thread(
            target=self._run, name="sanka-outbound", daemon=True
        )
        self._thread = thread
        thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the worker, join it, and drop anything still queued."""
        with self._cond:
            self._stopping = True
            self._cond.notify_all()

        thread = self._thread
        if thread is not None:
            thread.join(timeout if timeout > 0 else 0)

        with self._cond:
            dropped = sum(len(queue) for queue in self._queues.values())
            self._queues.clear()
            self._order.clear()
            self._active.clear()
            self._window.clear()
            self._cond.notify_all()

        if dropped:
            logger.warning("outbound_stop_dropped count=%d", dropped)
        if thread is not None and not thread.is_alive():
            self._thread = None

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Wait until no item is queued or in flight; return ``True`` on idle."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while self._queues or self._busy:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(timeout=remaining)
            return True

    @property
    def pending(self) -> int:
        """Number of queued items (excluding the one being delivered)."""
        with self._cond:
            return sum(len(queue) for queue in self._queues.values())

    # -- enqueue -----------------------------------------------------------

    def enqueue_reply(
        self,
        chat_id: str,
        messages: Sequence[str],
        *,
        base_key: str,
        kind: ResponseKind = ResponseKind.TEXT,
    ) -> None:
        """Format a reply and queue one ``SEND`` per non-empty chunk."""
        chunks: list[str] = []
        for message in messages:
            chunks.extend(self._formatter.format(message, kind))
        chunks = [chunk for chunk in chunks if chunk.strip()]
        if not chunks:
            return

        with self._cond:
            if self._stopping:
                return
            for index, text in enumerate(chunks):
                self._append_locked(
                    OutboundItem(
                        OutboundKind.SEND,
                        chat_id,
                        text=text,
                        idempotency_key=f"{base_key}:{index}",
                    )
                )

    def enqueue_typing(self, chat_id: str, state: str = "composing") -> None:
        """Queue a typing indicator update."""
        with self._cond:
            if self._stopping:
                return
            self._append_locked(
                OutboundItem(OutboundKind.TYPING, chat_id, state=state)
            )

    def enqueue_read(self, chat_id: str, message_id: str) -> None:
        """Queue a read receipt for one inbound message."""
        with self._cond:
            if self._stopping:
                return
            self._append_locked(
                OutboundItem(OutboundKind.READ, chat_id, message_id=message_id)
            )

    # -- worker ------------------------------------------------------------

    def _run(self) -> None:
        while True:
            with self._cond:
                item = self._next_locked()
                while item is None:
                    if self._stopping:
                        return
                    self._cond.wait()
                    item = self._next_locked()
                if self._stopping:
                    return
                self._busy = True
            try:
                self._deliver(item)
            finally:
                with self._cond:
                    self._busy = False
                    self._cond.notify_all()

    def _next_locked(self) -> OutboundItem | None:
        """Pop the next item, rotating chats for round-robin fairness."""
        while self._order:
            chat_id = self._order.popleft()
            queue = self._queues.get(chat_id)
            if not queue:
                self._active.discard(chat_id)
                self._queues.pop(chat_id, None)
                continue
            item = queue.popleft()
            if queue:
                self._order.append(chat_id)
            else:
                self._active.discard(chat_id)
                del self._queues[chat_id]
            return item
        return None

    def _append_locked(self, item: OutboundItem) -> None:
        queue = self._queues.get(item.chat_id)
        if queue is None:
            queue = deque()
            self._queues[item.chat_id] = queue
        if item.chat_id not in self._active:
            self._active.add(item.chat_id)
            self._order.append(item.chat_id)

        if len(queue) >= self._max_per_chat:
            self._log_overflow(item)
            return

        queue.append(item)
        self._cond.notify()

    # -- delivery ----------------------------------------------------------

    def _deliver(self, item: OutboundItem) -> None:
        if item.kind is OutboundKind.SEND:
            if not self._reserve_send_slot():
                return
            self._deliver_send(item)
            return

        if not self._slot_available():
            logger.warning(
                "outbound_dropped kind=%s chat=%s reason=rate_limit",
                item.kind.value,
                _chat_digest(item.chat_id),
            )
            return
        self._deliver_light(item)

    def _deliver_light(self, item: OutboundItem) -> None:
        try:
            if item.kind is OutboundKind.TYPING:
                self._bridge.typing(item.chat_id, item.state)
            else:
                self._bridge.read(item.chat_id, item.message_id)
        except BridgeError as exc:
            logger.warning(
                "outbound_call_failed kind=%s chat=%s status=%s retryable=%s",
                item.kind.value,
                _chat_digest(item.chat_id),
                exc.status,
                exc.retryable,
            )
        except Exception as exc:  # noqa: BLE001 - worker must not die
            logger.warning(
                "outbound_call_failed kind=%s chat=%s error=%s",
                item.kind.value,
                _chat_digest(item.chat_id),
                type(exc).__name__,
            )

    def _deliver_send(self, item: OutboundItem) -> None:
        attempt = 0
        while True:
            attempt += 1
            try:
                self._bridge.send(item.chat_id, item.text, item.idempotency_key)
                return
            except BridgeError as exc:
                if not exc.retryable or attempt >= self._max_attempts:
                    logger.error(
                        "outbound_send_dropped chat=%s status=%s attempts=%d "
                        "retryable=%s",
                        _chat_digest(item.chat_id),
                        exc.status,
                        attempt,
                        exc.retryable,
                    )
                    return
                if self._stopping:
                    logger.warning(
                        "outbound_send_dropped chat=%s reason=stopped attempts=%d",
                        _chat_digest(item.chat_id),
                        attempt,
                    )
                    return
                delay = exc.retry_after
                if delay is None:
                    index = min(attempt - 1, len(_BACKOFF_SECONDS) - 1)
                    delay = _BACKOFF_SECONDS[index]
                self._sleep(min(max(delay, 0.0), _MAX_RETRY_AFTER_SECONDS))
            except Exception as exc:  # noqa: BLE001 - worker must not die
                logger.error(
                    "outbound_send_dropped chat=%s error=%s attempts=%d",
                    _chat_digest(item.chat_id),
                    type(exc).__name__,
                    attempt,
                )
                return

    # -- rate window -------------------------------------------------------

    def _reserve_send_slot(self) -> bool:
        """Block until a send slot is free, then consume it."""
        while True:
            with self._cond:
                if self._stopping:
                    return False
                now = self._clock()
                self._prune_locked(now)
                if len(self._window) < self._per_minute:
                    self._window.append(now)
                    return True
                wait = self._window[0] + _RATE_WINDOW_SECONDS - now
            self._sleep(wait if wait > _MIN_WAIT_SECONDS else _MIN_WAIT_SECONDS)

    def _slot_available(self) -> bool:
        """Check for a free send slot without consuming one."""
        with self._cond:
            if self._stopping:
                return False
            self._prune_locked(self._clock())
            return len(self._window) < self._per_minute

    def _prune_locked(self, now: float) -> None:
        cutoff = now - _RATE_WINDOW_SECONDS
        while self._window and self._window[0] <= cutoff:
            self._window.popleft()

    def _interruptible_sleep(self, seconds: float) -> None:
        with self._cond:
            if not self._stopping:
                self._cond.wait(timeout=seconds if seconds > 0 else 0)

    # -- logging -----------------------------------------------------------

    def _log_overflow(self, item: OutboundItem) -> None:
        log = logger.error if item.kind is OutboundKind.SEND else logger.warning
        log(
            "outbound_overflow kind=%s chat=%s limit=%d",
            item.kind.value,
            _chat_digest(item.chat_id),
            self._max_per_chat,
        )


def _chat_digest(chat_id: str) -> str:
    """Short, stable digest so logs never carry a full JID."""
    return hashlib.sha256(chat_id.encode("utf-8")).hexdigest()[:12]


def _message_id(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    message_id = payload.get("message_id")
    return message_id if isinstance(message_id, str) else None


def _retry_after(response: httpx.Response) -> float | None:
    """Return the bridge's requested delay in seconds, body first."""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        retry_after_ms = payload.get("retry_after_ms")
        if (
            isinstance(retry_after_ms, (int, float))
            and not isinstance(retry_after_ms, bool)
            and retry_after_ms >= 0
        ):
            return float(retry_after_ms) / 1000.0

    header = response.headers.get("retry-after")
    if header is not None:
        try:
            value = float(header.strip())
        except ValueError:
            return None
        if value >= 0:
            return value
    return None
