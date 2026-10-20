"""Recap arithmetic, rendering, and service tests (no network)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sanka.config import SankaConfig, load_config
from sanka.policy import Role
from sanka.tools.recap import (
    RecapDefinition,
    RecapFilter,
    RecapMetric,
    RecapService,
    compute_recap,
    format_number,
    make_recap_tool,
    render_recap,
)
from sanka.tools.registry import ToolContext, ToolError
from tests.conftest import valid_config_dict, write_config
from tests.fakes import FakeSheetTable, FakeSheetsApi

NOW = datetime(2026, 10, 21, 3, 14, tzinfo=timezone.utc)
HEADER = ["Nama", "Divisi", "Status", "Nominal"]
ROWS = [
    ["Ani", "Acara", "Hadir", "1500"],
    ["Budi", "Acara", "Hadir", "750,5"],
    ["Citra", "Logistik", "Hadir", "1000"],
    ["Dedi", "Logistik", "Tidak Hadir", ""],
    ["Eka", "Acara", "Hadir", "abc"],
]


def _now() -> datetime:
    return NOW


def _table(
    header: Sequence[str] = HEADER,
    rows: Sequence[Sequence[str]] = ROWS,
) -> FakeSheetTable:
    return FakeSheetTable.from_rows(
        "kehadiran", "Kehadiran!A1:D6", header, rows
    )


def _definition(**overrides: object) -> RecapDefinition:
    base: dict[str, object] = {
        "name": "kehadiran",
        "title": "Rekap Kehadiran",
        "alias": "kehadiran",
        "range": "Kehadiran!A1:D6",
        "header_row": 1,
        "filters": (RecapFilter(column="Status", equals="Hadir"),),
        "group_by": ("Divisi",),
        "metrics": (RecapMetric(op="count"), RecapMetric(op="sum", column="Nominal")),
    }
    base.update(overrides)
    return RecapDefinition(**base)  # type: ignore[arg-type]


def _config(tmp_path) -> SankaConfig:
    data = valid_config_dict()
    data["workspace"]["read_allowlist"] = [
        {"alias": "kehadiran", "spreadsheet_id": "abc123_XYZ-1234567890"}
    ]
    data["recaps"] = {
        "kehadiran": {
            "title": "Rekap Kehadiran",
            "alias": "kehadiran",
            "range": "Kehadiran!A1:D6",
            "header_row": 1,
            "filters": [{"column": "Status", "equals": "Hadir"}],
            "group_by": ["Divisi"],
            "metrics": [
                {"op": "count"},
                {"op": "sum", "column": "Nominal"},
            ],
        }
    }
    path = write_config(tmp_path / "sanka.yaml", data)
    return load_config(path)


def test_case_insensitive_filter_and_group_totals() -> None:
    definition = _definition(
        filters=(RecapFilter(column=" status ", equals=" hadir "),),
        group_by=("divisi",),
        metrics=(
            RecapMetric(op="count"),
            RecapMetric(op="sum", column="nominal"),
        ),
    )

    result = compute_recap(definition, _table(), _now)

    assert result.total_rows == 5
    assert result.matched_rows == 4
    assert result.skipped_numeric_cells == 1
    assert result.generated_at == "2026-10-21T03:14:00Z"
    assert [group["keys"] for group in result.groups] == [["Acara"], ["Logistik"]]

    acara, logistik = result.groups
    assert acara["count"] == 3
    assert acara["sums"] == {"nominal": 2250.5}
    assert logistik["count"] == 1
    assert logistik["sums"] == {"nominal": 1000}


def test_empty_group_by_yields_one_group_with_sum_and_mean() -> None:
    definition = _definition(
        group_by=(),
        metrics=(
            RecapMetric(op="count"),
            RecapMetric(op="sum", column="Nominal"),
            RecapMetric(op="mean", column="Nominal"),
        ),
    )

    result = compute_recap(definition, _table(), _now)

    assert result.matched_rows == 4
    assert result.skipped_numeric_cells == 1
    assert len(result.groups) == 1
    group = result.groups[0]
    assert list(group["keys"]) == []
    assert group["count"] == 4
    assert group["sums"] == {"Nominal": 3250.5, "mean:Nominal": 1083.5}


def test_no_match_reports_zero_rows_without_error() -> None:
    definition = _definition(filters=(RecapFilter(column="Status", equals="Pulang"),))

    result = compute_recap(definition, _table(), _now)

    assert result.total_rows == 5
    assert result.matched_rows == 0
    assert result.groups == ()
    assert result.truncated_groups == 0
    assert "Tidak ada baris yang cocok." in render_recap(result)


def test_no_match_without_grouping_keeps_one_zero_group() -> None:
    definition = _definition(
        filters=(RecapFilter(column="Status", equals="Pulang"),),
        group_by=(),
        metrics=(RecapMetric(op="count"), RecapMetric(op="sum", column="Nominal")),
    )

    result = compute_recap(definition, _table(), _now)

    assert result.matched_rows == 0
    assert len(result.groups) == 1
    assert result.groups[0]["count"] == 0
    assert result.groups[0]["sums"] == {"Nominal": 0}


def test_group_order_is_count_desc_then_keys_asc() -> None:
    table = _table(
        header=["Divisi", "Status", "Nominal"],
        rows=[
            ["Zeta", "Hadir", "10"],
            ["Alpha", "Hadir", "20"],
            ["Beta", "Hadir", "30"],
            ["Beta", "Hadir", "40"],
        ],
    )
    definition = _definition(
        filters=(),
        group_by=("Divisi",),
        metrics=(RecapMetric(op="count"),),
    )

    result = compute_recap(definition, table, _now)

    assert [group["keys"] for group in result.groups] == [
        ["Beta"],
        ["Alpha"],
        ["Zeta"],
    ]
    assert [group["count"] for group in result.groups] == [2, 1, 1]

    capped = compute_recap(definition, table, _now, max_groups=2)
    assert [group["keys"] for group in capped.groups] == [["Beta"], ["Alpha"]]
    assert capped.truncated_groups == 1


def test_nonnumeric_cell_is_skipped_but_sum_still_works() -> None:
    table = _table(
        header=["Status", "Nominal"],
        rows=[["Hadir", "100"], ["Hadir", "abc"], ["Hadir", "250,5"]],
    )
    definition = _definition(
        filters=(),
        group_by=(),
        metrics=(RecapMetric(op="sum", column="Nominal"),),
    )

    result = compute_recap(definition, table, _now)

    assert result.skipped_numeric_cells == 1
    assert result.groups[0]["sums"] == {"Nominal": 350.5}


def test_header_row_selects_header_inside_values() -> None:
    table = FakeSheetTable(
        alias="kehadiran",
        range="Kehadiran!A1:C4",
        values=(
            ("Laporan", "", ""),
            ("Nama", "Status", "Nominal"),
            ("Ani", "Hadir", "10"),
            ("Budi", "Hadir", "20"),
        ),
    )
    definition = _definition(
        header_row=2,
        filters=(RecapFilter(column="status", equals="hadir"),),
        group_by=(),
        metrics=(RecapMetric(op="count"), RecapMetric(op="sum", column="nominal")),
    )

    result = compute_recap(definition, table, _now)

    assert result.total_rows == 2
    assert result.matched_rows == 2
    assert result.groups[0]["count"] == 2
    assert result.groups[0]["sums"] == {"nominal": 30}


def test_unknown_column_is_an_invalid_arguments_error() -> None:
    definition = _definition(filters=(RecapFilter(column="Kota", equals="X"),))

    with pytest.raises(ToolError) as excinfo:
        compute_recap(definition, _table(), _now)

    assert excinfo.value.code == "invalid_arguments"


def test_to_data_matches_contract_and_is_json_ready() -> None:
    result = compute_recap(_definition(), _table(), _now)

    data = result.to_data()

    assert set(data) == {
        "name",
        "title",
        "generated_at",
        "total_rows",
        "matched_rows",
        "skipped_numeric_cells",
        "groups",
        "truncated_groups",
    }
    assert json.loads(json.dumps(data)) == data
    assert set(data["groups"][0]) == {"keys", "count", "sums"}


def test_fixture_sheet_counts_and_skips_malformed_numbers() -> None:
    fixture_path = (
        Path(__file__).resolve().parent / "fixtures" / "sheets" / "kehadiran.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    values = tuple(tuple(str(cell) for cell in row) for row in payload["values"])
    table = FakeSheetTable(
        alias="kehadiran", range=payload["range"], values=values
    )
    definition = RecapDefinition(
        name="kehadiran",
        title="Rekap Kehadiran",
        alias="kehadiran",
        range=payload["range"],
        filters=(RecapFilter(column="Status", equals="Hadir"),),
        group_by=("Divisi",),
        metrics=(RecapMetric(op="count"), RecapMetric(op="sum", column="Nominal")),
    )

    result = compute_recap(definition, table, _now)

    assert result.total_rows == 10
    assert result.matched_rows == 8
    assert result.skipped_numeric_cells == 2  # "1.500.000" and "abc"
    assert [group["keys"] for group in result.groups] == [
        ["Acara"],
        ["Logistik"],
        ["Media"],
    ]
    assert [group["count"] for group in result.groups] == [4, 2, 2]
    assert [group["sums"] for group in result.groups] == [
        {"Nominal": 3_750_000},
        {"Nominal": 750_000},
        {"Nominal": 900_000},
    ]


def test_format_number_uses_indonesian_separators() -> None:
    assert format_number(1_234_567) == "1.234.567"
    assert format_number(-1_500.5, 1) == "-1.500,5"
    assert format_number(750.5, 2) == "750,50"
    assert format_number(None) == "0"
    assert format_number(0) == "0"
    assert format_number(float("nan")) == "0"


def test_render_recap_is_generated_by_code() -> None:
    result = compute_recap(_definition(), _table(), _now)

    text = render_recap(result)

    assert text.splitlines()[0] == "Rekap Kehadiran \u2014 2026-10-21 03:14 UTC"
    assert "Filter: Status = Hadir" in text
    assert "Data: 4 dari 5 baris, 1 sel nominal tidak terbaca" in text
    assert "1. Acara \u2014 3 baris; Nominal: 2.250,50" in text
    assert "2. Logistik \u2014 1 baris; Nominal: 1.000" in text
    assert "Total: 4 baris cocok." in text
    assert (
        'Dihitung oleh kode dari sheet "kehadiran". Bukan perkiraan model.'
        in text
    )


def test_service_resolves_names_and_unknown_names(tmp_path) -> None:
    config = _config(tmp_path)
    table = _table()
    api = FakeSheetsApi({"kehadiran": table})
    service = RecapService(config, api, _now)

    assert service.names() == ("kehadiran",)

    with pytest.raises(ToolError) as missing:
        service.run("tidak-ada", Role.COMMITTEE)
    assert missing.value.code == "not_found"
    assert api.calls == []

    result = service.run("kehadiran", Role.COMMITTEE)

    assert result.matched_rows == 4
    assert result.groups[0]["sums"] == {"Nominal": 2250.5}
    assert api.calls == [("kehadiran", "Kehadiran!A1:D6", None)]


def test_make_recap_tool_returns_raw_rendered_text(tmp_path) -> None:
    config = _config(tmp_path)
    service = RecapService(config, FakeSheetsApi({"kehadiran": _table()}), _now)
    spec = make_recap_tool(service)
    context = ToolContext(
        role=Role.COMMITTEE,
        chat_id="chat",
        sender_id="628222222222",
        config=config,
        now=_now,
    )

    result = spec.handler({"name": "kehadiran"}, context)

    assert result.ok is True
    assert result.tool == "recap"
    assert result.data["matched_rows"] == 4
    assert result.text.startswith("Rekap Kehadiran")
    assert "<data" not in result.text
    assert "2250" not in result.text  # rendered with Indonesian separators
    assert "2.250,50" in result.text
    assert spec.input_schema["required"] == ["name"]
    assert spec.input_schema["additionalProperties"] is False
