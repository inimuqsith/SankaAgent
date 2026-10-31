"""Konteks Indonesia (+62) yang diperbarui sendiri lewat `web_search`.

Ringkasan disimpan per topik dengan cap waktu; blok prompt menghilang setelah lebih tua
dari TTL, dan model diberi tahu supaya bilang tidak yakin daripada mengarang angka.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime

from .stores.currents import TTL_SECONDS, CurrentsStore

log = logging.getLogger(__name__)

TOPICS = (
    "pemerintah",
    "ekonomi",
    "rupiah",
    "prabowo",
    "sawit",
    "energi",
    "pangan",
    "hukum",
    "teknologi",
    "surabaya",
    "jawa timur",
)
ITEM_CHARS = 220
MAX_ITEMS = 24
YEAR = date.today().year


def query(topic: str) -> str:
    return f"{str(topic or '').strip()} Indonesia {YEAR} terbaru"


def _day(stamp: float) -> str:
    return datetime.fromtimestamp(float(stamp)).strftime("%Y-%m-%d")


def format_block(data: dict, now: float, limit: int = 6) -> str:
    """Blok prompt dari topik yang masih segar; kosong kalau semuanya kedaluwarsa."""
    topics = data.get("topics") if isinstance(data, dict) else None
    if not isinstance(topics, dict):
        return ""
    fresh: list[tuple[float, str, dict]] = []
    for name, item in topics.items():
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        try:
            fetched = float(item.get("fetched_at") or 0.0)
        except (TypeError, ValueError):
            fetched = 0.0
        if not text or float(now) - fetched > TTL_SECONDS:
            continue
        fresh.append((fetched, str(name), item))
    if not fresh:
        return ""
    fresh.sort(reverse=True)
    kept = fresh[: max(1, int(limit))]
    newest = max(fetched for fetched, _name, _item in kept)
    updated = max(newest, float(data.get("updated_at") or 0.0))
    lines = [f"## Current Indonesia context (fetched {_day(updated)})"]
    for fetched, name, item in kept:
        lines.append(f"- {name}: {str(item.get('text') or '').strip()} (as of {_day(fetched)})")
    return "\n".join(lines)


def _fresh(data: dict, topic: str, now: float) -> float:
    item = (data.get("topics") or {}).get(topic)
    if not isinstance(item, dict):
        return 0.0
    try:
        return float(item.get("fetched_at") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _prune(topics: dict) -> dict:
    ordered = sorted(
        (name for name, item in topics.items() if isinstance(item, dict)),
        key=lambda name: float(topics[name].get("fetched_at") or 0.0),
        reverse=True,
    )
    return {name: topics[name] for name in ordered[:MAX_ITEMS]}


async def refresh(
    store: CurrentsStore,
    search,
    summarize,
    *,
    topics: int = 2,
    results: int = 4,
    now: float | None = None,
) -> int:
    """Perbarui topik paling basi; error satu topik tidak menghapus item lain."""
    moment = time.time() if now is None else float(now)
    data = store.load()
    items: dict = dict(data.get("topics") or {})
    order = sorted(TOPICS, key=lambda name: (_fresh(data, name, moment), name))
    updated = 0
    for topic in order[: max(0, int(topics))]:
        prompt = query(topic)
        try:
            found = await search(prompt, results)
        except Exception:
            log.debug("currents: pencarian gagal topik=%s", topic, exc_info=True)
            continue
        text = str(found or "").strip()
        if not text or text.startswith("error:"):
            log.info("currents: hasil kosong topik=%s", topic)
            continue
        try:
            summary = await summarize(prompt, text)
        except Exception:
            log.debug("currents: ringkasan gagal topik=%s", topic, exc_info=True)
            continue
        body = " ".join(str(summary or "").split())
        if not body:
            continue
        items[topic] = {
            "text": body[:ITEM_CHARS],
            "fetched_at": moment,
            "source": "web_search",
        }
        updated += 1
    if updated:
        data["updated_at"] = moment
        data["topics"] = _prune(items)
        store.save(data)
    return updated
