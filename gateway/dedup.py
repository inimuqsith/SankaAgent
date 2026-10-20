"""Thread-safe, in-memory TTL registry for deduplication and notice throttling."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

_SWEEP_INTERVAL = 256
_FULL_RATIO = 0.9


class TtlRegistry:
    """Remember keys for ``ttl_seconds`` with lazy expiration.

    Expiration is checked on lookup for the requested key, and stale entries
    are swept every 256 registrations or once the registry is nearly full.
    When a sweep still leaves the registry at capacity, the oldest inserted key
    is evicted (``dict`` insertion order). A duplicate lookup never refreshes
    the original TTL.
    """

    def __init__(
        self,
        ttl_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = 10_000,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")

        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._max_entries = max_entries
        self._entries: dict[str, float] = {}
        self._since_sweep = 0
        self._lock = threading.Lock()

    def register(self, key: str) -> bool:
        """Register ``key``; return ``True`` when it is new (or expired)."""
        now = self._clock()
        with self._lock:
            registered_at = self._entries.get(key)
            if registered_at is not None and now - registered_at < self._ttl:
                return False
            if registered_at is not None:
                del self._entries[key]

            self._entries[key] = now
            self._since_sweep += 1
            if (
                self._since_sweep >= _SWEEP_INTERVAL
                or len(self._entries) > self._max_entries * _FULL_RATIO
            ):
                self._purge(now)
                self._since_sweep = 0

            while len(self._entries) > self._max_entries:
                oldest = next(iter(self._entries))
                del self._entries[oldest]
            return True

    def discard(self, key: str) -> None:
        """Forget ``key`` so the next registration is treated as new."""
        with self._lock:
            self._entries.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def _purge(self, now: float) -> None:
        expired = [
            key
            for key, registered_at in self._entries.items()
            if now - registered_at >= self._ttl
        ]
        for key in expired:
            del self._entries[key]
