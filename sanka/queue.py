"""Per-chat FIFO queues with one daemon worker thread per active chat.

Different chats may run in parallel; messages from the same chat are executed
strictly in order so two turns cannot race on the same session history.
The queue is deliberately free of runtime/client imports so it can be tested
with a plain callable runner.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar

from sanka.models import InboundMessage
from sanka.policy import Role

T = TypeVar("T")


class QueueBusyError(RuntimeError):
    """Raised when a new chat would exceed the active-chat cap."""


class AgentCancelled(Exception):
    """Raised by a runner that noticed ``cancel_event`` and stopped early."""


class StopOutcome(str, Enum):
    """Result of a ``/stop`` request."""

    STOPPED = "stopped"
    IDLE = "idle"
    FORBIDDEN = "forbidden"


@dataclass(slots=True)
class AgentJob(Generic[T]):
    """One queued agent turn."""

    message: InboundMessage
    role: Role
    future: Future[T]
    cancel_event: threading.Event
    sender_id: str
    chat_id: str


@dataclass(slots=True)
class _ChatState:
    jobs: deque[AgentJob[Any]] = field(default_factory=deque)
    thread: threading.Thread | None = None
    current: AgentJob[Any] | None = None


class ChatQueue(Generic[T]):
    """FIFO queue per chat with a small cap on simultaneously active chats.

    A worker thread is started on demand, drains the chat queue, then removes
    its own state under the shared lock. A later ``submit`` for that chat
    starts a fresh worker, so idle chats do not keep threads alive.
    """

    def __init__(
        self,
        runner: Callable[[AgentJob[T]], T],
        *,
        max_active_chats: int = 16,
    ) -> None:
        if max_active_chats < 1:
            raise ValueError("max_active_chats must be at least 1")
        self._runner = runner
        self._max_active_chats = max_active_chats
        self._lock = threading.RLock()
        self._queues: dict[str, _ChatState] = {}
        self._closed = False

    def submit(self, message: InboundMessage, role: Role) -> Future[T]:
        """Queue one turn and return its future.

        Raises :class:`QueueBusyError` when a message for a new chat arrives
        while ``max_active_chats`` chats already have live workers.
        """
        chat_id = message.chat_id
        with self._lock:
            if self._closed:
                raise RuntimeError("ChatQueue is shut down")

            state = self._queues.get(chat_id)
            if state is None:
                if len(self._queues) >= self._max_active_chats:
                    raise QueueBusyError(
                        f"too many active chats ({self._max_active_chats})"
                    )
                state = _ChatState()
                self._queues[chat_id] = state

            job: AgentJob[T] = AgentJob(
                message=message,
                role=role,
                future=Future(),
                cancel_event=threading.Event(),
                sender_id=message.sender_id,
                chat_id=chat_id,
            )
            state.jobs.append(job)

            if state.thread is None:
                thread = threading.Thread(
                    target=self._worker,
                    args=(chat_id,),
                    name=f"sanka-chat-{chat_id}",
                    daemon=True,
                )
                state.thread = thread
                thread.start()

            return job.future

    def stop(
        self,
        chat_id: str,
        requester_id: str,
        is_owner: bool,
    ) -> StopOutcome:
        """Cancel the chat's current and queued work.

        Only the sender of the running job or an owner may stop it. The HTTP
        request in flight is not killed; its result is discarded cooperatively
        after the runner returns.
        """
        with self._lock:
            state = self._queues.get(chat_id)
            if state is None:
                return StopOutcome.IDLE
            current = state.current
            if current is None and not state.jobs:
                return StopOutcome.IDLE
            job = current or state.jobs[0]
            if job.sender_id != requester_id and not is_owner:
                return StopOutcome.FORBIDDEN

            if current is not None:
                current.cancel_event.set()
            for queued in state.jobs:
                queued.future.cancel()
            state.jobs.clear()
            return StopOutcome.STOPPED

    def shutdown(self, wait: bool = True) -> None:
        """Stop accepting work and cancel current and pending jobs."""
        with self._lock:
            self._closed = True
            states = list(self._queues.values())
            for state in states:
                if state.current is not None:
                    state.current.cancel_event.set()
                self._cancel_pending_locked(state)

        if not wait:
            return
        for state in states:
            thread = state.thread
            if thread is not None and thread is not threading.current_thread():
                thread.join()

    def _worker(self, chat_id: str) -> None:
        while True:
            with self._lock:
                state = self._queues.get(chat_id)
                if state is None:
                    return
                if self._closed or not state.jobs:
                    self._cancel_pending_locked(state)
                    self._queues.pop(chat_id, None)
                    return
                job = state.jobs.popleft()
                state.current = job

            if not self._run_job(chat_id, job):
                return

    def _run_job(self, chat_id: str, job: AgentJob[T]) -> bool:
        """Execute one job, complete its future, and report whether to keep running."""
        cancelled = job.cancel_event.is_set() or job.future.cancelled()
        result: T | None = None
        error: Exception | None = None

        if not cancelled:
            try:
                result = self._runner(job)
            except AgentCancelled:
                cancelled = True
            except Exception as exc:  # noqa: BLE001 - surfaced to the waiting caller
                error = exc
            else:
                cancelled = job.cancel_event.is_set() or job.future.cancelled()

        # Completing the future while clearing ``current`` (and removing the
        # state when the queue is drained) under the lock means a waiter that
        # wakes from ``result()`` sees an idle chat; a later submit starts a
        # fresh worker instead of racing the old one's cleanup.
        with self._lock:
            state = self._queues.get(chat_id)
            if state is not None and state.current is job:
                state.current = None

            keep_running = (
                state is not None and not self._closed and bool(state.jobs)
            )
            if state is not None and not keep_running:
                self._cancel_pending_locked(state)
                self._queues.pop(chat_id, None)

            if cancelled:
                job.future.cancel()
            elif error is not None:
                job.future.set_exception(error)
            else:
                job.future.set_result(result)

        return keep_running

    @staticmethod
    def _cancel_pending_locked(state: _ChatState) -> None:
        for queued in state.jobs:
            queued.future.cancel()
        state.jobs.clear()
