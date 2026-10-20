"""The Sheets allowlist must be the only way to reach a spreadsheet."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from sanka.config import (
    AllowlistedSheet,
    ConfigError,
    SankaConfig,
    load_config,
)
from sanka.tools.registry import ToolError
from sanka.tools.sheets import GoogleSheetsApi, make_sheet_read_tool
from tests.conftest import valid_config_dict, write_config
from tests.fakes import FakeTokenProvider

SPREADSHEET_ID = "sheetKehadiran0123456789ABCDEFGHIJKLMN"
ALIAS = "kehadiran"
CELL_RANGE = "Kehadiran!A1:E11"
FIXTURE = Path(__file__).parent / "fixtures" / "sheets" / "kehadiran.json"

# Values a model might try to smuggle in where only an alias is accepted.
FORBIDDEN_ALIASES = [
    SPREADSHEET_ID,
    f"{SPREADSHEET_ID[:20]}extra",
    f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit",
    "../kehadiran",
    "..%2fkehadiran",
    "kehadiran sheet",
    "%2e%2e",
    "Kehadiran",
    "kehadiran!A1:E11",
    "",
]


def load_payload() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def allowlisted_config(config: SankaConfig) -> SankaConfig:
    return replace(
        config,
        workspace=replace(
            config.workspace,
            read_allowlist=(
                AllowlistedSheet(alias=ALIAS, spreadsheet_id=SPREADSHEET_ID),
            ),
        ),
    )


def test_registered_alias_is_fetched(config: SankaConfig) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=load_payload())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    api = GoogleSheetsApi(
        allowlisted_config(config), FakeTokenProvider(), client=client
    )

    table = api.fetch(ALIAS, CELL_RANGE)

    assert table.alias == ALIAS
    assert len(seen) == 1
    assert SPREADSHEET_ID in str(seen[0].url)


@pytest.mark.parametrize("alias", FORBIDDEN_ALIASES)
def test_unknown_alias_is_not_found(
    config: SankaConfig, alias: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may leave for an unknown alias")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    api = GoogleSheetsApi(
        allowlisted_config(config), FakeTokenProvider(), client=client
    )

    with pytest.raises(ToolError) as excinfo:
        api.fetch(alias, CELL_RANGE)

    assert excinfo.value.code == "not_found"
    assert excinfo.value.message == "Sheet tidak ditemukan."
    assert SPREADSHEET_ID not in excinfo.value.message
    assert ALIAS not in excinfo.value.message


@pytest.mark.parametrize("alias", FORBIDDEN_ALIASES)
def test_schema_pattern_rejects_non_alias_values(
    config: SankaConfig, alias: str
) -> None:
    spec = make_sheet_read_tool(
        GoogleSheetsApi(allowlisted_config(config), FakeTokenProvider()),
        config,
    )
    pattern = spec.input_schema["properties"]["alias"]["pattern"]

    assert re.fullmatch(pattern, alias) is None


def test_schema_accepts_registered_alias_and_range(
    config: SankaConfig,
) -> None:
    spec = make_sheet_read_tool(
        GoogleSheetsApi(allowlisted_config(config), FakeTokenProvider()),
        config,
    )
    properties = spec.input_schema["properties"]

    assert re.fullmatch(properties["alias"]["pattern"], ALIAS) is not None
    assert (
        re.fullmatch(properties["range"]["pattern"], CELL_RANGE) is not None
    )
    for bad_range in ("../x", "Kehadiran", "Kehadiran!A1:B2:C3", "Kehadiran!A"):
        assert re.fullmatch(properties["range"]["pattern"], bad_range) is None


def test_config_rejects_recap_alias_outside_allowlist(
    tmp_path: Path,
) -> None:
    data = valid_config_dict()
    data["workspace"]["read_allowlist"] = [
        {"alias": ALIAS, "spreadsheet_id": SPREADSHEET_ID}
    ]
    data["recaps"] = {
        "kehadiran": {
            "title": "Rekap Kehadiran",
            "alias": "alias-lain",
            "range": "Kehadiran!A1:E100",
        }
    }
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    message = str(excinfo.value)
    assert "workspace.read_allowlist" in message
    assert SPREADSHEET_ID not in message


def test_config_accepts_recap_alias_inside_allowlist(
    tmp_path: Path,
) -> None:
    data = valid_config_dict()
    data["workspace"]["read_allowlist"] = [
        {"alias": ALIAS, "spreadsheet_id": SPREADSHEET_ID}
    ]
    data["recaps"] = {
        "kehadiran": {
            "title": "Rekap Kehadiran",
            "alias": ALIAS,
            "range": "Kehadiran!A1:E100",
            "filters": [{"column": "Status", "equals": "Hadir"}],
            "group_by": ["Divisi"],
            "metrics": [{"op": "count"}],
        }
    }
    path = write_config(tmp_path / "sanka.yaml", data)

    config = load_config(path)

    assert config.recaps["kehadiran"].alias == ALIAS
    assert [sheet.alias for sheet in config.workspace.read_allowlist] == [ALIAS]


def test_config_rejects_raw_spreadsheet_id_as_alias(tmp_path: Path) -> None:
    data = valid_config_dict()
    data["workspace"]["read_allowlist"] = [
        {"alias": SPREADSHEET_ID, "spreadsheet_id": SPREADSHEET_ID}
    ]
    path = write_config(tmp_path / "sanka.yaml", data)

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    message = str(excinfo.value)
    assert "alias" in message
    assert SPREADSHEET_ID not in message
