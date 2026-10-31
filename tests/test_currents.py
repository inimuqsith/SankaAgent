"""Kontrak konteks Indonesia: blok segar kedaluwarsa, refresh tidak menghapus item lama."""

from __future__ import annotations

import asyncio
import time

from support import database

from sanka.engine.currents import ITEM_CHARS, TOPICS, YEAR, format_block, query, refresh
from sanka.engine.stores.currents import TTL_SECONDS, CurrentsStore


def store(tmp_path) -> CurrentsStore:
    return CurrentsStore(database(tmp_path))


def test_query_names_the_topic_and_year() -> None:
    assert query("rupiah") == f"rupiah Indonesia {YEAR} terbaru"
    assert len(TOPICS) >= 8


def test_format_block_prints_fresh_items_and_expires() -> None:
    now = time.time()
    items = {
        "updated_at": now,
        "topics": {
            "rupiah": {"text": "rupiah melemah ke 16.8rb/USD", "fetched_at": now, "source": "web_search"}
        },
    }

    text = format_block(items, now=now, limit=6)

    assert text.startswith("## Current Indonesia context (fetched ")
    assert "rupiah: rupiah melemah" in text
    assert "(as of " in text
    assert format_block(items, now=now + 8 * 24 * 3600) == ""
    assert format_block(items, now=now, limit=6) != ""
    assert TTL_SECONDS == 7 * 24 * 3600
    assert format_block({}, now=now) == ""


def test_refresh_writes_one_item_per_topic_and_takes_the_oldest_first(tmp_path) -> None:
    calls: list[str] = []

    async def search(prompt: str, results: int) -> str:
        calls.append(prompt)
        return f"hasil untuk {prompt}"

    async def summarize(prompt: str, text: str) -> str:
        return f"ringkas {prompt}"

    instrument = store(tmp_path)
    assert asyncio.run(refresh(instrument, search, summarize, topics=2, results=4, now=1000.0)) == 2
    data = instrument.load()
    assert len(data["topics"]) == 2
    assert data["updated_at"] == 1000.0
    assert all(len(item["text"]) <= ITEM_CHARS for item in data["topics"].values())

    calls.clear()
    assert asyncio.run(refresh(instrument, search, summarize, topics=2, results=4, now=2000.0)) == 2
    refreshed = instrument.load()["topics"]
    assert {name for name, item in refreshed.items() if item["fetched_at"] == 2000.0} == {
        call.split(" Indonesia")[0] for call in calls
    }
    older = [name for name, item in refreshed.items() if item["fetched_at"] == 1000.0]
    assert len(older) == 2 and len(refreshed) == 4


def test_refresh_keeps_existing_items_when_search_fails(tmp_path) -> None:
    async def search(prompt: str, results: int) -> str:
        return "error: searxng tidak tersedia"

    async def summarize(prompt: str, text: str) -> str:
        return "tidak dipakai"

    instrument = store(tmp_path)
    instrument.save(
        {
            "updated_at": 500.0,
            "topics": {"ekonomi": {"text": "inflasi stabil", "fetched_at": 500.0, "source": "web_search"}},
        }
    )

    assert asyncio.run(refresh(instrument, search, summarize, topics=3, now=900.0)) == 0
    data = instrument.load()
    assert data["updated_at"] == 500.0
    assert data["topics"]["ekonomi"]["text"] == "inflasi stabil"


def test_store_roundtrip_and_block(tmp_path) -> None:
    instrument = store(tmp_path)
    assert instrument.load() == {"updated_at": 0.0, "topics": {}}
    assert instrument.block(time.time(), 6) == ""

    now = time.time()
    instrument.save(
        {"updated_at": now, "topics": {"pangan": {"text": "harga beras naik", "fetched_at": now}}}
    )

    assert instrument.load()["topics"]["pangan"]["text"] == "harga beras naik"
    assert "pangan: harga beras naik" in instrument.block(now, 6)
