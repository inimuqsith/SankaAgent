"""Kontrak kolektor riset: area, promosi berbasis bukti, penyaringan, dan workbook audit."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research
import xlsx

FIXTURE = Path(__file__).resolve().parent / "data" / "research_fixture.json"


def item(
    text: str,
    *,
    sources: tuple[str, ...] = ("https://a.example/1",),
    repeats: int = 3,
    confidence: int = 2,
    synthetic: bool = False,
) -> dict:
    return {
        "kind": "line",
        "text": text,
        "meaning": "arti singkat",
        "register": "banter",
        "media": "none",
        "gate": {"target": "any", "min_toxicity": 0, "min_adult": 0},
        "evidence": {"repeats": repeats, "sources": list(sources), "platform": "forum"},
        "synthetic": synthetic,
        "confidence": confidence,
        "why": "uji",
    }


def test_topics_are_unique_areas() -> None:
    assert len(research.TOPICS) == 24
    assert len(set(research.TOPICS)) == 24
    for tid, topic in research.TOPICS.items():
        # Hanya `label`: worker Muse sendiri yang menurunkan sudut dan query, tanpa contoh tertanam.
        assert set(topic) == {"label"}, tid
        assert 1 <= len(str(topic["label"]).split()) <= 6, tid


def test_promote_requires_two_domains_and_repeats() -> None:
    two_domains = [
        item("yo wes mangan durung", sources=("https://a.example/1",)),
        item("yo wes mangan durung cok", sources=("https://b.example/2",)),
    ]
    promoted, rejected = research.promote([two_domains])
    assert rejected == []
    assert len(promoted) == 1
    assert promoted[0]["domains"] == ["a.example", "b.example"]

    one_domain = [
        item("yo wes mangan durung", sources=("https://a.example/1",)),
        item("yo wes mangan durung cok", sources=("https://a.example/2",)),
    ]
    promoted, rejected = research.promote([one_domain])
    assert promoted == []
    assert rejected[0]["reason"] == "bukti_kurang"


def test_group_size_promotes_repeated_construct() -> None:
    items = [
        item("cok yo wes mangan durung", repeats=0, confidence=1),
        item("cok yo wes mangan durung ta", repeats=0, confidence=1),
        item("cok yo wes mangan durung rek", repeats=0, confidence=1),
    ]
    groups = research.group(items)
    assert len(groups) == 1
    assert len(groups[0]) == 3
    promoted, rejected = research.promote(groups)
    assert rejected == []
    assert len(promoted) == 1
    assert promoted[0]["size"] == 3


def test_synthetic_items_never_promoted() -> None:
    items = [
        item(
            f"sintetis {index} yo wes mangan durung",
            sources=("https://a.example/1", "https://b.example/2"),
            repeats=9,
            confidence=3,
            synthetic=True,
        )
        for index in range(3)
    ]
    promoted, rejected = research.promote(research.group(items))
    assert promoted == []
    assert [row["reason"] for row in rejected] == ["sintetis"] * len(rejected)


def test_item_ok_rejects_paths_urls_phones_and_long_text() -> None:
    assert research.item_ok(item("yo wes mangan durung cok")) is None
    assert research.item_ok(item("lihat scripts/patterns.py dulu")) is not None
    assert research.item_ok(item("cek http://x.example dulu")) is not None
    assert research.item_ok(item("hubungi 081234567890 sekarang")) is not None
    assert research.item_ok(item("a" * 300)) is not None
    assert research.item_ok(item("```json")) is not None


def test_from_file_pipeline_writes_workbook(tmp_path: Path) -> None:
    assert research.main(["--from-file", str(FIXTURE), "--out", str(tmp_path)]) == 0
    books = list(tmp_path.glob("audit-research-*.xlsx"))
    assert len(books) == 1
    sheets = xlsx.read_workbook(books[0])
    assert sheets["baris"][0][0] == "topic"
    promoted = json.loads((tmp_path / "filtered.json").read_text(encoding="utf-8"))["promoted"]
    assert len(sheets["baris"]) == len(promoted) + 1
    assert "query_log" in sheets
    assert all(row["synthetic"] is False for row in promoted)
