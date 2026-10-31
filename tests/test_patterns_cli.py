"""Kontrak alat katalog: skema ditolak jelas, workbook review bolak-balik 1:1."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import patterns as cli
import xlsx


def row(ident: str, **overrides) -> dict:
    body = {
        "id": ident,
        "register": ["neutral"],
        "keys": ["kunci"],
        "target": "any",
        "min_toxicity": 0,
        "max_toxicity": 4,
        "min_adult": 0,
        "content": f"Konten asli {ident}.",
        "say": [],
        "avoid": [],
        "priority": 5,
        "always": False,
        "source": "curated",
    }
    body.update(overrides)
    return body


def book(*rows: dict) -> dict:
    return {"version": 2, "model": cli.MODEL, "patterns": list(rows)}


def test_xlsx_roundtrip_keeps_unicode_and_numbers(tmp_path) -> None:
    path = tmp_path / "book.xlsx"
    rows = [["no", "user", "sanka"], [1, "kowe lo elek", "raimu lo koyok 😌"]]

    xlsx.write_workbook(path, [("pola", rows, [6, 30, 40])])

    assert xlsx.read_workbook(path)["pola"] == [
        ["no", "user", "sanka"],
        ["1", "kowe lo elek", "raimu lo koyok 😌"],
    ]


def test_shipped_catalogue_passes_the_schema() -> None:
    body = cli.load()

    assert cli.validate(body) == []
    assert len(body["patterns"]) >= 30


def test_validate_names_each_deficit() -> None:
    deficits = cli.validate(
        book(
            row("ganda", register=["neutral"]),
            row("ganda"),
            row("tanpa_isi", content="pendek"),
            row("register_aneh", register=["fisip"]),
            row("tak_terjangkau", register=["creative"]),
            row("batas_terbalik", min_toxicity=3, max_toxicity=1),
            row("kunci_besar", keys=["Huruf"]),
            row("sumber_aneh", source="ngarang"),
        )
    )
    joined = "\n".join(deficits)

    assert "id ganda ganda" in joined
    assert "content harus 10-200 karakter" in joined
    assert "register 'fisip' di luar kosakata" in joined
    assert "tidak bisa dipilih selector" in joined
    assert "min/max toxicity" in joined
    assert "key 'Huruf' harus huruf kecil" in joined
    assert "source 'ngarang'" in joined


def test_validate_requires_capital_start_for_say() -> None:
    body = book(row("kecil", say=["halo semua"]))
    assert "huruf besar" in "\n".join(cli.validate(body))


def test_validate_flags_say_reused_across_rows() -> None:
    body = book(row("satu", say=["halo semua"]), row("dua", say=["halo semua"]))
    assert "say ganda" in "\n".join(cli.validate(body))


def test_always_pattern_may_omit_keys() -> None:
    assert cli.validate(book(row("invarian", keys=[], always=True))) == []
    assert "butuh minimal satu key" in "\n".join(cli.validate(book(row("kosong", keys=[]))))


def test_build_and_apply_workbook_roundtrip(tmp_path) -> None:
    body = book(
        row("satu", keys=["capek", "gak kuat"], say=["Boleehhh"], avoid=["bukan drama"]),
        row("dua", content="Konten kedua yang utuh."),
    )
    path = tmp_path / "review.xlsx"
    cli.build_xlsx(body, path)

    built = xlsx.read_workbook(path)["pola"]
    header = built[0]
    assert header[:6] == ["id", "register", "target", "level", "keys", "konten"]
    assert built[1][header.index("level")] == "tox 0-4 / adult 0+"
    assert built[1][header.index("contoh")] == "Boleehhh"

    built[1][header.index("konten")] = "Konten hasil edit pemilik."
    built[1][header.index("keys")] = "capek, numpuk"
    built[1][header.index("komentar")] = "dipakai untuk deadline"
    xlsx.write_workbook(path, [("pola", built, cli.WIDTHS)])

    applied, unknown = cli.apply_xlsx(body, path)

    assert (applied, unknown) == (2, [])
    assert body["patterns"][0]["content"] == "Konten hasil edit pemilik."
    assert body["patterns"][0]["keys"] == ["capek", "numpuk"]
    assert body["patterns"][0]["komentar"] == "dipakai untuk deadline"
    assert body["patterns"][1]["content"] == "Konten kedua yang utuh."
    assert body["patterns"][1]["say"] == []
    assert cli.validate(body) == []


def test_apply_workbook_reports_missing_columns(tmp_path) -> None:
    body = book(row("satu"))
    path = tmp_path / "review.xlsx"
    xlsx.write_workbook(path, [("pola", [["id", "konten"], ["satu", "isi"]], [8, 8])])

    applied, problems = cli.apply_xlsx(body, path)

    assert applied == 0
    assert "kolom hilang" in problems[0]


def test_apply_workbook_reports_unknown_ids(tmp_path) -> None:
    body = book(row("satu"))
    path = tmp_path / "review.xlsx"
    cli.build_xlsx(body, path)
    built = xlsx.read_workbook(path)["pola"]
    built.append(["tidak_ada", "", "", "", "", "konten", "", "", "", "", "", ""])
    xlsx.write_workbook(path, [("pola", built, cli.WIDTHS)])

    applied, unknown = cli.apply_xlsx(body, path)

    assert (applied, unknown) == (1, ["tidak_ada"])


def test_stats_cover_register_and_gates() -> None:
    body = book(
        row("satu", register=["comfort", "serious"]),
        row("dua", register=["banter"], target="male", min_toxicity=3, min_adult=1),
        row("tiga", register=["neutral"], always=True, keys=[]),
    )

    line = cli._stats(body)[1]

    assert "banter=1" in line
    assert "comfort=1" in line
    assert "serious=1" in line
