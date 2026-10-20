"""Shared doubles for the gateway test suite (no network, no real sleeps)."""

from __future__ import annotations

import threading
from collections.abc import Sequence

from sanka.agent.runtime import AgentReply
from sanka.formatter import ResponseKind
from sanka.models import InboundMessage
from sanka.policy import Role


class FakeClock:
    """Monotonic clock whose sleep advances time instead of blocking."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = float(start)
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += float(seconds)

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("sleep duration must not be negative")
        self.advance(seconds)


class FakeBridge:
    """In-memory :class:`gateway.outbound.BridgeClient`.

    ``failures`` is consumed one entry per ``send`` call before a send can
    succeed, which lets tests script retry sequences.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.typing_events: list[tuple[str, str]] = []
        self.reads: list[tuple[str, str]] = []
        self.failures: list[Exception] = []
        self._lock = threading.Lock()

    def send(self, chat_id: str, text: str, idempotency_key: str) -> str | None:
        with self._lock:
            if self.failures:
                raise self.failures.pop(0)
            self.sent.append((chat_id, text, idempotency_key))
            return f"wamid-{len(self.sent)}"

    def typing(self, chat_id: str, state: str) -> None:
        with self._lock:
            self.typing_events.append((chat_id, state))

    def read(self, chat_id: str, message_id: str) -> None:
        with self._lock:
            self.reads.append((chat_id, message_id))


class FakeRuntime:
    """In-memory :class:`gateway.dispatcher.AgentHandler`."""

    def __init__(self, replies: Sequence[str] = ("Balasan agent.",)) -> None:
        self.replies = tuple(replies)
        self.calls: list[tuple[InboundMessage, Role]] = []
        self.error: Exception | None = None

    def handle(self, message: InboundMessage, role: Role) -> AgentReply:
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        self.calls.append((message, role))
        return AgentReply(messages=self.replies, kind=ResponseKind.TEXT)
