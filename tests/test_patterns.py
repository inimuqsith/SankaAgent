"""Kontrak katalog pola: gate level/target/register, urutan deterministik, fallback."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sanka.engine import patterns
from sanka.engine.patterns import load_patterns, room_register, select_patterns

ROOT = Path(__file__).resolve().parents[1]
DIM = patterns.DIM
UNIT = [1.0] + [0.0] * (DIM - 1)
ZERO = [0.0] * DIM


def _row(ident: str, **overrides) -> dict:
    row = {
        "id": ident,
        "register": ["neutral"],
        "keys": [],
        "target": "any",
        "min_toxicity": 0,
        "max_toxicity": 4,
        "min_adult": 0,
        "content": f"Pola {ident} untuk turn ini.",
        "say": [],
        "avoid": [],
        "priority": 5,
        "always": False,
        "source": "curated",
    }
    row.update(overrides)
    return row


BOOK = {
    "version": 2,
    "model": patterns.MODEL,
    "patterns": [
        _row(
            "comfort_validate",
            register=["comfort", "serious"],
            keys=["capek", "gak kuat"],
            priority=8,
            content="Validasi dulu lalu tawarkan satu hal.",
            say=["boleehhh"],
        ),
        _row(
            "roast_return",
            register=["banter"],
            keys=["goblok"],
            target="male",
            min_toxicity=3,
            priority=10,
            content="Balas sekali dalam register ruangan.",
        ),
        _row(
            "meme_gate",
            register=["meme", "banter"],
            keys=["jomok"],
            min_adult=1,
            priority=5,
            content="Mirror hanya kalau ruangan memakainya.",
        ),
        _row(
            "care_female",
            register=["comfort"],
            keys=["sedih"],
            target="female",
            priority=7,
            content="Temani dulu tanpa memaksa.",
        ),
        _row(
            "invariant",
            register=["soal", "comfort", "banter", "meme", "serious", "neutral"],
            keys=[],
            priority=3,
            content="Satu bahasa per balasan dan tetap pendek.",
            always=True,
        ),
    ],
}

VECTORS = {
    "model": patterns.MODEL,
    "dim": DIM,
    "vectors": {
        "comfort_validate": UNIT,
        "roast_return": ZERO,
        "meme_gate": ZERO,
        "care_female": ZERO,
        "invariant": ZERO,
    },
}


class Stub:
    """Encoder tiruan: vektor kueri konstan, tidak pernah memuat model asli."""

    def __init__(self, vector: list[float]) -> None:
        self.vector = vector
        self.calls = 0

    def encode(self, texts: list[str], **_kwargs):
        self.calls += 1
        return [list(self.vector) for _ in texts]


@pytest.fixture(autouse=True)
def clean_cache():
    patterns.load_patterns.cache_clear()
    patterns._load_vectors.cache_clear()
    yield
    patterns.load_patterns.cache_clear()
    patterns._load_vectors.cache_clear()


def use(tmp_path, monkeypatch, book: dict | str = BOOK, vectors: dict | None = None) -> None:
    data = tmp_path / "patterns.json"
    data.write_text(book if isinstance(book, str) else json.dumps(book), encoding="utf-8")
    monkeypatch.setattr(patterns, "DATA", data)
    built = tmp_path / "vectors.json"
    if vectors is not None:
        built.write_text(json.dumps(vectors), encoding="utf-8")
    monkeypatch.setattr(patterns, "VECTORS", built)


def test_missing_or_broken_catalogue_returns_no_patterns(tmp_path, monkeypatch) -> None:
    use(tmp_path, monkeypatch, "{bukan json")
    assert load_patterns() == []


def test_malformed_rows_are_dropped_not_crashing(tmp_path, monkeypatch) -> None:
    book = {
        "version": 2,
        "model": patterns.MODEL,
        "patterns": [
            _row("obsolete", keys=["ok"]),
            {"id": "", "content": "tanpa id", "register": ["neutral"]},
            {"id": "tanpa_konten", "register": ["neutral"], "keys": ["x"]},
            _row("gate_rusak", keys=["ok"], min_toxicity="x", priority="tinggi"),
            "bukan objek",
        ],
    }
    use(tmp_path, monkeypatch, book)
    kept = load_patterns()
    assert [row["id"] for row in kept] == ["obsolete", "gate_rusak"]
    assert (kept[1]["min_toxicity"], kept[1]["priority"]) == (0, 1)


def test_room_register_prefers_serious_then_banter_then_meme() -> None:
    assert room_register(["capek banget hari ini", "wkwk"]) == "serious"
    assert room_register(["wkwk parah", "goblok"]) == "banter"
    assert room_register(["jomok lagi", "gemoy"]) == "meme"
    assert room_register(["besok rapat jam sembilan"]) == "neutral"
    assert room_register([]) == "neutral"


def test_keyword_path_ranks_by_hits_and_respects_gates(tmp_path, monkeypatch) -> None:
    use(tmp_path, monkeypatch)

    got = select_patterns(
        3,
        register="comfort",
        target="female",
        limits=(1, 0),
        messages=["aku capek banget, gak kuat"],
        encoder=None,
    )
    assert got[0].startswith("Validasi dulu"), got
    assert '(contoh: "boleehhh")' in got[0]
    assert all("Balas sekali" not in line for line in got)
    assert (
        select_patterns(
            0, register="banter", target="male", limits=(4, 3), messages=["ayoo"], encoder=None
        )
        == []
    )


def test_target_and_level_gates(tmp_path, monkeypatch) -> None:
    use(tmp_path, monkeypatch)

    male = select_patterns(
        6, register="banter", target="male", limits=(3, 1), messages=["ayo"], encoder=None
    )
    assert any(line.startswith("Balas sekali") for line in male)
    for target in ("female", "unknown", ""):
        quiet = select_patterns(
            6, register="banter", target=target, limits=(3, 1), messages=["ayo"], encoder=None
        )
        assert all("Balas sekali" not in line for line in quiet), (target, quiet)
    low = select_patterns(
        6, register="banter", target="male", limits=(2, 1), messages=["ayo"], encoder=None
    )
    assert all("Balas sekali" not in line for line in low)
    adult_off = select_patterns(
        6, register="meme", target="male", limits=(3, 0), messages=["ayo"], encoder=None
    )
    assert all("Mirror hanya" not in line for line in adult_off)


def test_always_pattern_is_eligible_without_keyword_hits(tmp_path, monkeypatch) -> None:
    use(tmp_path, monkeypatch)

    got = select_patterns(
        2, register="neutral", target="unknown", limits=(0, 0), messages=["apa kabar"], encoder=None
    )
    assert got == ["Satu bahasa per balasan dan tetap pendek."]


def test_cosine_path_ranks_by_similarity_and_drops_far_patterns(tmp_path, monkeypatch) -> None:
    use(tmp_path, monkeypatch, vectors=VECTORS)
    stub = Stub(UNIT)

    got = select_patterns(
        3,
        register="comfort",
        target="female",
        limits=(0, 0),
        messages=["aku capek banget"],
        encoder=stub,
    )

    assert stub.calls == 1
    assert got[0].startswith("Validasi dulu"), got
    assert all("Temani dulu" not in line for line in got)


def test_vector_model_mismatch_falls_back_to_keywords(tmp_path, monkeypatch) -> None:
    stale = {**VECTORS, "model": "model/lain"}
    use(tmp_path, monkeypatch, vectors=stale)
    stub = Stub(UNIT)

    got = select_patterns(
        2,
        register="comfort",
        target="female",
        limits=(0, 0),
        messages=["aku sedih"],
        encoder=stub,
    )

    assert stub.calls == 0
    assert got[0].startswith("Temani dulu"), got


def test_shipped_catalogue_loads_whole_with_vectors() -> None:
    body = json.loads((ROOT / "sanka" / "data" / "patterns.json").read_text(encoding="utf-8"))

    assert len(body["patterns"]) >= 30
    invariants = {row["id"] for row in body["patterns"] if row["always"]}
    assert invariants == {
        "language_lock",
        "length",
        "question_limit",
        "grounding",
        "floor",
        "curse_trigger",
    }
    assert len(load_patterns()) == len(body["patterns"])
    assert patterns._vectors([row["id"] for row in body["patterns"]]), "vektor katalog hilang"
