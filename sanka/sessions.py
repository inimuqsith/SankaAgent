"""In-memory conversation sessions, one per chat.

Sessions are short-lived context for the agent loop (plan-2). They are not
long-term memory: restarting the process drops every session. Access is
guarded by a re-entrant lock because the queue worker, ``/new``, and ``/stop``
can touch the same session from different threads.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

Clock = Callable[[], float]
IdFactory = Callable[[], str]

DEFAULT_MAX_HISTORY_CHARS = 60_000


@dataclass(slots=True)
class AgentSession:
    """Short-term conversation state for a single chat."""

    chat_id: str
    opencode_session_id: str
    history: list[dict[str, str]] = field(default_factory=list)
    updated_at: float = 0.0


class SessionStore:
    """Thread-safe, TTL-based store of :class:`AgentSession` objects.

    ``clock`` is injectable so tests can advance time without sleeping.
    ``id_factory`` produces the OpenCode session id; ``/new`` calls it again to
    make sure upstream prompt caches cannot leak the old context.
    """

    def __init__(
        self,
        ttl_seconds: float,
        *,
        clock: Clock = time.monotonic,
        max_history_chars: int = DEFAULT_MAX_HISTORY_CHARS,
        id_factory: IdFactory = lambda: uuid4().hex,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._max_history_chars = max_history_chars
        self._id_factory = id_factory
        self._lock = threading.RLock()
        self._sessions: dict[str, AgentSession] = {}

    def get(self, chat_id: str) -> AgentSession | None:
        """Return the live session for ``chat_id``, evicting expired ones."""
        with self._lock:
            self._evict_expired_locked()
            return self._sessions.get(chat_id)

    def get_or_create(self, chat_id: str) -> AgentSession:
        """Return the existing session or create an empty one."""
        with self._lock:
            self._evict_expired_locked()
            session = self._sessions.get(chat_id)
            if session is None:
                session = self._new_session_locked(chat_id)
            return session

    def reset(self, chat_id: str) -> AgentSession:
        """Replace the session with a fresh id and empty history."""
        with self._lock:
            self._evict_expired_locked()
            session = self._new_session_locked(chat_id)
            return session

    def history(self, chat_id: str) -> list[dict[str, str]]:
        """Return a copy of the chat history (empty when no live session)."""
        with self._lock:
            self._evict_expired_locked()
            session = self._sessions.get(chat_id)
            if session is None:
                return []
            return [dict(message) for message in session.history]

    def append(
        self,
        chat_id: str,
        expected_session_id: str,
        role: str,
        content: str,
    ) -> bool:
        """Append a completed message if the session id still matches.

        The id guard makes late results from a cancelled or reset turn
        harmless: they are dropped instead of polluting the new context.
        """
        with self._lock:
            session = self._sessions.get(chat_id)
            if session is None:
                return False
            if self._is_expired_locked(session):
                del self._sessions[chat_id]
                return False
            if session.opencode_session_id != expected_session_id:
                return False
            session.history.append({"role": role, "content": content})
            _trim_history(session.history, self._max_history_chars)
            session.updated_at = self._clock()
            return True

    def evict_expired(self) -> int:
        """Drop expired sessions and return how many were removed."""
        with self._lock:
            return self._evict_expired_locked()

    @property
    def session_count(self) -> int:
        """Number of live sessions currently held in memory."""
        with self._lock:
            self._evict_expired_locked()
            return len(self._sessions)

    def _new_session_locked(self, chat_id: str) -> AgentSession:
        session = AgentSession(
            chat_id=chat_id,
            opencode_session_id=self._id_factory(),
            history=[],
            updated_at=self._clock(),
        )
        self._sessions[chat_id] = session
        return session

    def _evict_expired_locked(self) -> int:
        expired = [
            chat_id
            for chat_id, session in self._sessions.items()
            if self._is_expired_locked(session)
        ]
        for chat_id in expired:
            del self._sessions[chat_id]
        return len(expired)

    def _is_expired_locked(self, session: AgentSession) -> bool:
        return self._clock() - session.updated_at >= self._ttl_seconds


def _trim_history(history: list[dict[str, str]], max_chars: int) -> None:
    """Drop oldest messages over the cap, then leading assistant messages.

    At least the newest message is always kept, even when that single message
    is larger than the cap. Removing a leading assistant turn keeps the
    upstream chat format valid (a conversation must start with a user turn).
    """
    total_chars = sum(len(message.get("content", "")) for message in history)
    while len(history) > 1 and total_chars > max_chars:
        dropped = history.pop(0)
        total_chars -= len(dropped.get("content", ""))

    while len(history) > 1 and history[0].get("role") == "assistant":
        history.pop(0)
