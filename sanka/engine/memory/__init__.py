"""MemoryBackend: semantic memory through Mem0, the action ledger, and the shared contract."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryHit:
    id: str
    text: str
    score: float = 0.0
    scope: str = "person"
    person_id: str = ""
    chat_id: str = ""
    created_at: float | None = None
    metadata: dict = field(default_factory=dict)
    level: int = 2
    owner_id: str = ""
    subject_id: str = ""
    confirmed: bool = True
    vetoed: bool = False


@dataclass(frozen=True)
class MemoryContext:
    """Rendered memory block plus the exact hits the model was allowed to see."""

    text: str = ""
    hits: tuple[MemoryHit, ...] = ()


class MemoryBackend(Protocol):
    available: bool
    reason: str

    async def add(
        self,
        text: str,
        *,
        scope: str = "person",
        kind: str = "fact",
        person_id: str = "",
        chat_id: str = "",
        audience: str = "",
        source: str = "",
        metadata: dict | None = None,
        supersedes: str | None = None,
        infer: bool = False,
        level: int = 2,
        owner_id: str = "",
        subject_id: str = "",
        confirmed: bool = True,
    ) -> list[str]: ...

    async def capture(
        self,
        messages: list[dict],
        *,
        person_id: str,
        chat_id: str,
        audience: str,
        source: str = "turn",
        level: int = 2,
        owner_id: str = "",
        subject_id: str = "",
        confirmed: bool = True,
    ) -> list[str]: ...

    async def search(
        self,
        query: str,
        *,
        person_id: str = "",
        chat_id: str = "",
        limit: int = 12,
        scopes: tuple[str, ...] = ("global", "person", "chat"),
    ) -> list[MemoryHit]: ...

    async def list(
        self,
        *,
        scope: str,
        person_id: str = "",
        chat_id: str = "",
        limit: int = 20,
    ) -> list[MemoryHit]: ...

    async def get(self, memory_id: str) -> MemoryHit | None: ...

    async def forget(
        self,
        memory_id: str,
        *,
        requester_id: str = "",
        chat_id: str = "",
        allow_global: bool = False,
    ) -> bool: ...

    async def set_privacy(
        self,
        memory_id: str,
        *,
        level: int | None = None,
        confirmed: bool | None = None,
        subject_id: str | None = None,
        vetoed: bool | None = None,
        actor: str = "",
    ) -> bool: ...

    async def veto(self, memory_id: str, *, subject_id: str, active: bool = True) -> bool: ...

    async def context(
        self,
        *,
        person_id: str,
        chat_id: str,
        query: str,
        mood=None,
        max_items: int | None = None,
        max_chars: int | None = None,
    ) -> MemoryContext: ...

    async def close(self) -> None: ...


class NullMemory:
    """Fail-open: the bot still replies when Mem0 is unavailable."""

    available = False

    def __init__(self, reason: str = "disabled") -> None:
        self.reason = reason

    async def add(self, text: str, **kwargs) -> list[str]:
        return []

    async def capture(self, messages: list[dict], **kwargs) -> list[str]:
        return []

    async def search(self, query: str, **kwargs) -> list[MemoryHit]:
        return []

    async def list(self, **kwargs) -> list[MemoryHit]:
        return []

    async def get(self, memory_id: str) -> MemoryHit | None:
        return None

    async def forget(self, memory_id: str, **kwargs) -> bool:
        return False

    async def set_privacy(self, memory_id: str, **kwargs) -> bool:
        return False

    async def veto(self, memory_id: str, **kwargs) -> bool:
        return False

    async def context(self, **kwargs) -> MemoryContext:
        return MemoryContext()

    async def close(self) -> None:
        return None


def memory_status(backend) -> str:
    if getattr(backend, "available", False):
        return str(getattr(backend, "label", "mem0"))
    return f"unavailable: {getattr(backend, 'reason', 'unknown')}"


async def build_memory(config, *, db=None) -> MemoryBackend:
    """Build the Mem0 backend; any failure falls back to NullMemory with a visible reason."""
    if config.memory.backend == "null":
        return NullMemory("disabled (memory.backend=null)")
    if config.database_url().startswith("sqlite"):
        return NullMemory("mem0 needs Postgres+pgvector, not SQLite")
    try:
        from .mem0 import Mem0Backend

        backend = Mem0Backend(db, config)
        await backend.ready()
        return backend
    except Exception as exc:  # missing deps/model/pgvector must not kill the bot
        log.warning("Mem0 memory is unavailable: %s: %s", type(exc).__name__, exc)
        return NullMemory(f"{type(exc).__name__}: {exc}")
