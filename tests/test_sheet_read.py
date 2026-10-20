"""``GoogleSheetsApi`` and the ``sheet_read`` tool, driven by MockTransport."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from sanka.config import AllowlistedSheet, SankaConfig
from sanka.policy import Role
from sanka.tools.registry import ToolContext, ToolError
from sanka.tools.sheets import (
    GoogleSheetsApi,
    SheetTable,
    make_sheet_read_tool,
)
from tests.fakes import FakeTokenProvider

SPREADSHEET_ID = "sheetKehadiran0123456789ABCDEFGHIJKLMN"
ALIAS = "kehadiran"
CELL_RANGE = "Kehadiran!A1:E11"
FIXTURE = Path(__file__).parent / "fixtures" / "sheets" / "kehadiran.json"


def load_payload() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def sheet_config(
    config: SankaConfig,
    *,
    max_cells: int | None = None,
    timeout_seconds: int | None = None,
    alias: str = ALIAS,
) -> SankaConfig:
    workspace = replace(
        config.workspace,
        read_allowlist=(
            AllowlistedSheet(alias=alias, spreadsheet_id=SPREADSHEET_ID),
        ),
    )
    sheets = config.tools.sheets
    if max_cells is not None:
        sheets = replace(sheets, max_cells=max_cells)
    if timeout_seconds is not None:
        sheets = replace(sheets, timeout_seconds=timeout_seconds)
    return replace(
        config, workspace=workspace, tools=replace(config.tools, sheets=sheets)
    )


Handler = Callable[[httpx.Request], httpx.Response]


def make_api(
    config: SankaConfig, handler: Handler
) -> tuple[GoogleSheetsApi, list[httpx.Request], list[float]]:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(recording))
    api = GoogleSheetsApi(
        config,
        FakeTokenProvider("test-token"),
        client=client,
        sleep=sleeps.append,
    )
    return api, requests, sleeps


def json_handler(
    payload: dict[str, Any], status: int = 200
) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status, json=payload)

    return handler


def status_handler(status: int) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status, json={"error": {"status": "NOPE"}})

    return handler


def test_fetch_uses_one_values_get_with_expected_query(
    config: SankaConfig,
) -> None:
    api, requests, sleeps = make_api(
        sheet_config(config), json_handler(load_payload())
    )

    table = api.fetch(ALIAS, CELL_RANGE)

    assert isinstance(table, SheetTable)
    assert len(requests) == 1
    assert sleeps == []
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == (
        f"/v4/spreadsheets/{SPREADSHEET_ID}/values/Kehadiran!A1:E11"
    )
    assert (
        f"/v4/spreadsheets/{SPREADSHEET_ID}/values/Kehadiran%21A1%3AE11"
        in str(request.url)
    )
    assert request.url.params["majorDimension"] == "ROWS"
    assert request.url.params["valueRenderOption"] == "UNFORMATTED_VALUE"
    assert (
        request.url.params["dateTimeRenderOption"] == "FORMATTED_STRING"
    )
    assert request.headers["authorization"] == "Bearer test-token"


def test_fetch_parses_header_and_rows_from_fixture(
    config: SankaConfig,
) -> None:
    payload = load_payload()
    api, _, _ = make_api(sheet_config(config), json_handler(payload))

    table = api.fetch(ALIAS, CELL_RANGE)

    assert table.alias == ALIAS
    assert table.range == CELL_RANGE
    assert table.header == tuple(payload["values"][0])
    assert table.row_count == 10
    assert table.truncated is False
    assert table.rows[0] == ("Ayu", "Acara", "Hadir", "1.500.000", "2026-10-01")
    # Empty cells become empty strings, never None.
    assert table.rows[2] == ("Citra", "Logistik", "Tidak Hadir", "", "2026-10-02")
    assert table.rows[3][3] == "abc"


def test_fetch_maps_non_string_cells_to_text(config: SankaConfig) -> None:
    payload = {
        "values": [
            ["Nama", "Nominal"],
            ["Ayu", 1500000],
            ["Bima", None],
            ["Citra", True],
        ]
    }
    api, _, _ = make_api(sheet_config(config), json_handler(payload))

    table = api.fetch(ALIAS, CELL_RANGE)

    assert table.rows == (
        ("Ayu", "1500000"),
        ("Bima", ""),
        ("Citra", "True"),
    )


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_map_to_not_configured(
    config: SankaConfig, status: int
) -> None:
    api, _, _ = make_api(sheet_config(config), status_handler(status))

    with pytest.raises(ToolError) as excinfo:
        api.fetch(ALIAS, CELL_RANGE)

    assert excinfo.value.code == "not_configured"
    assert excinfo.value.message == "Akses sheet belum diatur"


def test_missing_sheet_maps_to_not_found(config: SankaConfig) -> None:
    api, _, _ = make_api(sheet_config(config), status_handler(404))

    with pytest.raises(ToolError) as excinfo:
        api.fetch(ALIAS, CELL_RANGE)

    assert excinfo.value.code == "not_found"


def test_rate_limit_retries_once_then_succeeds(
    config: SankaConfig,
) -> None:
    payload = load_payload()
    statuses = iter([429, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        status = next(statuses)
        return httpx.Response(status, json=payload)

    api, requests, sleeps = make_api(sheet_config(config), handler)

    table = api.fetch(ALIAS, CELL_RANGE)

    assert len(requests) == 2
    assert sleeps == [1.0]
    assert table.row_count == 10


def test_persistent_server_error_retries_once_then_fails(
    config: SankaConfig,
) -> None:
    api, requests, sleeps = make_api(sheet_config(config), status_handler(500))

    with pytest.raises(ToolError) as excinfo:
        api.fetch(ALIAS, CELL_RANGE)

    assert excinfo.value.code == "upstream_error"
    assert excinfo.value.message == "Layanan Google tidak dapat dihubungi."
    assert len(requests) == 2
    assert sleeps == [1.0]


def test_timeout_maps_to_timeout(config: SankaConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    api, _, _ = make_api(sheet_config(config), handler)

    with pytest.raises(ToolError) as excinfo:
        api.fetch(ALIAS, CELL_RANGE)

    assert excinfo.value.code == "timeout"


def test_request_error_maps_to_upstream_error(config: SankaConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    api, _, _ = make_api(sheet_config(config), handler)

    with pytest.raises(ToolError) as excinfo:
        api.fetch(ALIAS, CELL_RANGE)

    assert excinfo.value.code == "upstream_error"


def test_non_json_response_is_upstream_error(config: SankaConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"<html>not json</html>")

    api, _, _ = make_api(sheet_config(config), handler)

    with pytest.raises(ToolError) as excinfo:
        api.fetch(ALIAS, CELL_RANGE)

    assert excinfo.value.code == "upstream_error"


def test_max_rows_truncates_data_rows(config: SankaConfig) -> None:
    api, _, _ = make_api(
        sheet_config(config), json_handler(load_payload())
    )

    table = api.fetch(ALIAS, CELL_RANGE, max_rows=3)

    assert table.truncated is True
    assert table.row_count == 3
    assert table.header == ("Nama", "Divisi", "Status", "Nominal", "Tanggal")
    assert table.rows[-1][0] == "Citra"


def test_max_rows_none_keeps_every_row(config: SankaConfig) -> None:
    api, _, _ = make_api(
        sheet_config(config), json_handler(load_payload())
    )

    table = api.fetch(ALIAS, CELL_RANGE, max_rows=None)

    assert table.row_count == 10
    assert table.truncated is False


def test_too_many_cells_is_too_large(config: SankaConfig) -> None:
    api, requests, _ = make_api(
        sheet_config(config, max_cells=5), json_handler(load_payload())
    )

    with pytest.raises(ToolError) as excinfo:
        api.fetch(ALIAS, CELL_RANGE)

    assert excinfo.value.code == "too_large"
    assert len(requests) == 1


def test_empty_values_returns_empty_table(config: SankaConfig) -> None:
    api, _, _ = make_api(
        sheet_config(config), json_handler({"range": CELL_RANGE})
    )

    table = api.fetch(ALIAS, CELL_RANGE)

    assert table.values == ()
    assert table.header == ()
    assert table.row_count == 0


def test_sheet_read_tool_returns_data_and_tsv(
    config: SankaConfig,
) -> None:
    cfg = sheet_config(config)
    api, _, _ = make_api(cfg, json_handler(load_payload()))
    spec = make_sheet_read_tool(api, cfg)
    context = ToolContext(
        role=Role.COMMITTEE,
        chat_id="chat",
        sender_id="sender",
        config=cfg,
        now=lambda: datetime(2026, 10, 21, tzinfo=timezone.utc),
    )

    result = spec.handler(
        {"alias": ALIAS, "range": CELL_RANGE, "max_rows": 200}, context
    )

    assert spec.name == "sheet_read"
    assert spec.input_schema["required"] == ["alias", "range"]
    assert spec.input_schema["additionalProperties"] is False
    assert result.ok is True
    assert result.tool == "sheet_read"
    assert result.data["alias"] == ALIAS
    assert result.data["row_count"] == 10
    assert result.data["truncated"] is False
    assert result.data["header"][0] == "Nama"
    assert result.data["rows"][0][3] == "1.500.000"
    assert result.text.splitlines()[0] == "Nama\tDivisi\tStatus\tNominal\tTanggal"
    assert "\tHadir\t" in result.text
    assert "<data" not in result.text


def test_sheet_read_tool_uses_default_max_rows(
    config: SankaConfig,
) -> None:
    cfg = sheet_config(config)
    seen: list[int | None] = []

    class RecordingApi:
        def fetch(
            self, alias: str, cell_range: str, *, max_rows: int | None = None
        ) -> SheetTable:
            seen.append(max_rows)
            return SheetTable(alias=alias, range=cell_range, values=())

    spec = make_sheet_read_tool(RecordingApi(), cfg)
    context = ToolContext(
        role=Role.COMMITTEE,
        chat_id="chat",
        sender_id="sender",
        config=cfg,
        now=lambda: datetime(2026, 10, 21, tzinfo=timezone.utc),
    )

    spec.handler({"alias": ALIAS, "range": CELL_RANGE}, context)

    assert seen == [200]
