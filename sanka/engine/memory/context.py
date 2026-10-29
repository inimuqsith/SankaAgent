"""Cross-chat memory query: global + sender person + active chat, then render the prompt block."""

from __future__ import annotations

import asyncio
import logging
import time

from . import MemoryContext

log = logging.getLogger(__name__)

SCOPES = ("global", "person", "chat")
HEADER = "Long-term memory (cross-chat):"
CHAT_BOOST = 0.15
RECENCY_BOOST = 0.1
TIMEOUT = 2.5


def _dedupe(hits: list) -> list:
    found: dict[str, object] = {}
    texts: set[str] = set()
    for hit in hits:
        key = str(getattr(hit, "id", "") or "")
        text = " ".join(str(getattr(hit, "text", "") or "").lower().split())
        if key and key in found:
            continue
        if text and text in texts:
            continue
        if key:
            found[key] = hit
        if text:
            texts.add(text)
    return list(found.values())


def _boost(hit, chat_id: str, now: float | None = None) -> float:
    score = float(getattr(hit, "score", 0.0) or 0.0)
    if chat_id and getattr(hit, "chat_id", "") == chat_id:
        score += CHAT_BOOST
    created = getattr(hit, "created_at", None)
    if created:
        age_days = max(0.0, (now or time.time()) - float(created)) / 86400.0
        score += RECENCY_BOOST / (1.0 + age_days)
    return score


async def gather(
    backend,
    *,
    query: str,
    person_id: str,
    chat_id: str,
    limit: int,
    scopes: tuple[str, ...] = SCOPES,
) -> list:
    """Fetch three scopes in parallel; another person's memory is never requested."""
    calls = []
    for scope in scopes:
        if scope == "person" and not person_id:
            continue
        if scope == "chat" and not chat_id:
            continue
        calls.append(backend._search_scope(scope, query, person_id, chat_id, limit))
    if not calls:
        return []
    results = await asyncio.gather(*calls, return_exceptions=True)
    hits: list = []
    for result in results:
        if isinstance(result, BaseException):
            log.debug("memory scope failed: %s", result)
            continue
        hits.extend(result)
    hits = _dedupe(hits)
    hits.sort(key=lambda hit: _boost(hit, chat_id), reverse=True)
    return hits


def _date(value) -> str:
    if not value:
        return ""
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(value)))
    except (TypeError, ValueError, OSError):
        return ""


def render(hits: list, *, max_items: int, max_chars: int) -> str:
    if not hits:
        return ""
    lines = [HEADER]
    used = len(HEADER)
    for hit in hits[: max(1, int(max_items))]:
        scope = str(getattr(hit, "scope", "") or "person")
        stamp = _date(getattr(hit, "created_at", None))
        label = f"{scope}/{stamp}" if stamp else scope
        level = int(getattr(hit, "level", 2) or 0)
        mark = "" if getattr(hit, "confirmed", True) else "?"
        text = " ".join(str(getattr(hit, "text", "") or "").split())
        if not text:
            continue
        line = f"- [{label} L{level}{mark}] {text}"
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) if len(lines) > 1 else ""


async def build(
    backend,
    *,
    person_id: str,
    chat_id: str,
    query: str,
    max_items: int = 12,
    max_chars: int = 3000,
    scopes: tuple[str, ...] = SCOPES,
) -> MemoryContext:
    hits = [
        hit
        for hit in await gather(
            backend,
            query=query,
            person_id=person_id,
            chat_id=chat_id,
            limit=max(1, int(max_items)),
            scopes=scopes,
        )
        if not getattr(hit, "vetoed", False)
    ]
    text = render(hits, max_items=max_items, max_chars=max_chars)
    return MemoryContext(text=text, hits=tuple(hit for hit in hits if getattr(hit, "text", "")))
