"""MemoryBackend: semantic memory lewat Mem0, ledger autografis, dan kontrak bersama."""

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
    ) -> list[str]: ...

    async def capture(
        self,
        messages: list[dict],
        *,
        person_id: str,
        chat_id: str,
        audience: str,
        source: str = "turn",
    ) -> None: ...

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

    async def forget(self, memory_id: str, *, person_id: str = "", chat_id: str = "") -> bool: ...

    async def context(
        self,
        *,
        person_id: str,
        chat_id: str,
        query: str,
        mood=None,
        max_items: int | None = None,
        max_chars: int | None = None,
    ) -> str: ...

    async def close(self) -> None: ...


class NullMemory:
    """Fail-open: bot tetap membalas saat Mem0 tidak tersedia."""

    available = False

    def __init__(self, reason: str = "disabled") -> None:
        self.reason = reason

    async def add(self, text: str, **kwargs) -> list[str]:
        return []

    async def capture(self, messages: list[dict], **kwargs) -> None:
        return None

    async def search(self, query: str, **kwargs) -> list[MemoryHit]:
        return []

    async def list(self, **kwargs) -> list[MemoryHit]:
        return []

    async def get(self, memory_id: str) -> MemoryHit | None:
        return None

    async def forget(self, memory_id: str, **kwargs) -> bool:
        return False

    async def context(self, **kwargs) -> str:
        return ""

    async def close(self) -> None:
        return None


def memory_status(backend) -> str:
    if getattr(backend, "available", False):
        return str(getattr(backend, "label", "mem0"))
    return f"unavailable: {getattr(backend, 'reason', 'unknown')}"


async def build_memory(config, *, db=None) -> MemoryBackend:
    """Bangun backend Mem0; gagal apa pun → NullMemory dengan alasan yang bisa ditampilkan."""
    if config.memory.backend == "null":
        return NullMemory("disabled (memory.backend=null)")
    if config.database_url().startswith("sqlite"):
        return NullMemory("mem0 butuh Postgres+pgvector, bukan SQLite")
    try:
        from .mem0 import Mem0Backend

        backend = Mem0Backend(db, config)
        await backend.ready()
        return backend
    except Exception as exc:  # deps/model/pgvector hilang tidak boleh mematikan bot
        log.warning("memori Mem0 tidak tersedia: %s: %s", type(exc).__name__, exc)
        return NullMemory(f"{type(exc).__name__}: {exc}")
