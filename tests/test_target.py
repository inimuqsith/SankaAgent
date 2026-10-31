"""Kontrak target gender: gaya mengetik dulu, avatar hanya cadangan, dan override manual."""

from __future__ import annotations

from support import build_config, database

from sanka.engine.stores.affect import AffectStore
from sanka.engine.target import combine, parse_gender, score_style


def test_style_marker_requires_hits_and_margin() -> None:
    assert score_style(["gue juga", "anjir", "goblok sih"]) == ("male", 1.0)
    assert score_style(["akuu gemes", "kak akuu", "gemes deh", "🥺🥺🥺"])[0] == "female"
    assert score_style(["aku sedih", "kamu gimana"]) == ("unknown", 0.0)
    assert score_style([]) == ("unknown", 0.0)
    assert score_style(["gue", "anjir"])[0] == "unknown"  # dua hit belum cukup


def test_style_ignores_false_friends_and_keeps_the_window() -> None:
    assert score_style(["capek banget", "banget dah", "banget"])[0] == "unknown"
    old = ["gue", "anjir", "goblok"]
    assert score_style([*old, *["aku sedih"] * 8])[0] == "unknown"


def test_combine_prefers_style_and_gates_the_avatar() -> None:
    assert combine(("female", 0.9), ("male", 0.8)) == ("female", 0.9, "style")
    assert combine(("male", 0.7), None) == ("male", 0.7, "style")
    assert combine(("unknown", 0.0), ("male", 0.8)) == ("male", 0.8, "avatar")
    assert combine(("unknown", 0.0), ("male", 0.8), gate=False) == ("unknown", 0.0, "none")
    assert combine(("unknown", 0.0), None) == ("unknown", 0.0, "none")


def test_parse_gender_accepts_indonesian_aliases() -> None:
    assert parse_gender("Pria") == "male"
    assert parse_gender("cewek") == "female"
    assert parse_gender("Unknown") == "unknown"
    assert parse_gender("banana") is None
    assert parse_gender("") is None


def test_gender_store_writes_only_known_people(tmp_path) -> None:
    db = database(tmp_path)
    store = AffectStore(db, build_config(tmp_path).affect)
    store.turn_end("chat", "u1", "Raka", "gue anjir goblok", "member", "", None, "m1")
    assert store.gender("u1") == ("", 0.0, "")

    store.set_gender("u1", "male", 0.75, "style")
    assert store.gender("u1") == ("male", 0.75, "style")

    store.set_gender("u1", "female", 1.0, "manual")
    assert store.gender("u1") == ("female", 1.0, "manual")

    store.set_gender("u1", "banana", 1.0, "manual")
    assert store.gender("u1") == ("female", 1.0, "manual")

    store.set_gender("nobody", "male", 1.0, "manual")
    assert store.gender("nobody") == ("", 0.0, "")
