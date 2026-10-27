"""Kontrak kompetisi blok prompt: wajib selalu masuk, opsional bersaing saat sempit."""

from __future__ import annotations

from sanka.salience import COMPETING, MANDATORY, ORDER, select


def names(kept: list[tuple[str, str]]) -> list[str]:
    return [name for name, _ in kept]


def test_all_blocks_fit_are_kept_in_order() -> None:
    blocks = {"memory": "M", "skills": "S", "base": "B", "topic": "T", "emotion": "E"}

    kept, dropped = select(blocks, max_chars=9000)

    assert dropped == []
    assert names(kept) == [name for name in ORDER if name in blocks]
    assert names(kept) == ["base", "topic", "emotion", "memory", "skills"]
    assert kept[1] == ("topic", "T")


def test_disabled_salience_never_drops_blocks() -> None:
    blocks = {"base": "b" * 100, "memory": "m" * 5000, "topic": "t" * 5000}

    kept, dropped = select(blocks, max_chars=200, enabled=False)

    assert dropped == []
    assert names(kept) == ["base", "topic", "memory"]


def test_tight_budget_keeps_mandatory_blocks_only() -> None:
    blocks = {
        "base": "b" * 100,
        "emotion": "e" * 100,
        "memory": "m" * 400,
        "topic": "t" * 400,
    }

    kept, dropped = select(blocks, max_chars=300)

    assert set(names(kept)) == {"base", "emotion"}
    assert set(dropped) == {"memory", "topic"}
    assert set(dropped) <= set(COMPETING)
    assert set(MANDATORY) & set(names(kept)) == {"base", "emotion"}


def test_keyword_overlap_survives_before_unrelated_block() -> None:
    blocks = {"base": "b" * 10, "topic": "hujan " * 5, "memory": "kopi " * 5}

    kept, dropped = select(blocks, "kopi enak", max_chars=50)

    assert names(kept) == ["base", "memory"]
    assert dropped == ["topic"]


def test_bonus_keeps_skills_over_memory() -> None:
    blocks = {"base": "b" * 10, "memory": "m" * 40, "skills": "s" * 40}

    kept, dropped = select(blocks, max_chars=55, bonuses={"skills": 0.2})

    assert names(kept) == ["base", "skills"]
    assert dropped == ["memory"]
