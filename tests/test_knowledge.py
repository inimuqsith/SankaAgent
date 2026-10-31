"""Kontrak lapisan pengetahuan: fakta bersumber ganda, dipilih semantik, terpisah dari pola."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fakta

from sanka.engine import knowledge
from sanka.engine.prompts import character_block


def test_shipped_facts_pass_schema_and_vectors() -> None:
    body = fakta.load()
    assert fakta.validate(body) == []
    assert fakta.vectors_deficits(body, fakta.VECTORS) == []
    assert len(fakta.rows(body)) >= 40


def test_fact_text_must_start_with_capital() -> None:
    body = fakta.load()
    body["facts"][0]["text"] = "huruf kecil di awal"
    assert "huruf besar" in "\n".join(fakta.validate(body))


def test_select_facts_finds_uin_mahad() -> None:
    got = knowledge.select_facts(3, messages=["uin malang maba wajib mahad berapa lama?"])
    assert got
    assert any("mondok" in line.lower() for line in got)


def test_select_facts_zero_count_returns_empty() -> None:
    assert knowledge.select_facts(0, messages=["uin malang maba mahad"]) == []


def test_select_facts_ignores_unrelated_message() -> None:
    assert knowledge.select_facts(3, messages=["wkwk"]) == []


def test_missing_file_is_empty_not_crash(tmp_path: Path) -> None:
    assert knowledge.load_facts(str(tmp_path / "hilang.json")) == []


def test_character_block_keeps_facts_separate_from_patterns() -> None:
    block = character_block((0, 0), "any", 0.9, patterns=["Pola satu."], facts=["Fakta satu."])
    assert "## Room patterns" in block and "Pola satu." in block
    assert "## Known facts" in block and "Fakta satu." in block
    assert block.index("## Room patterns") < block.index("## Known facts")
