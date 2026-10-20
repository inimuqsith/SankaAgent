"""``/rekap`` command tests: deterministic text, role gate, no network."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from sanka.commands.recap import register_recap_command
from sanka.commands.registry import (
    CommandContext,
    CommandRegistry,
)
from sanka.config import SankaConfig, load_config
from sanka.formatter import ResponseKind
from sanka.policy import Role
from sanka.tools.recap import RecapService, compute_recap, render_recap
from sanka.tools.registry import ToolError
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


def _table() -> FakeSheetTable:
    return FakeSheetTable.from_rows(
        "kehadiran", "Kehadiran!A1:D6", HEADER, ROWS
    )


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


def _context(config: SankaConfig, role: Role) -> CommandContext:
    return CommandContext(role=role, config=config, sender_id="628000000000")


class RecordingService:
    """Fake service that records every ``run`` call."""

    def __init__(
        self,
        names: tuple[str, ...] = ("kehadiran", "panitia"),
        result: Any = None,
        error: ToolError | None = None,
    ) -> None:
        self._names = names
        self._result = result
        self._error = error
        self.calls: list[tuple[str, Role]] = []

    def names(self) -> tuple[str, ...]:
        return self._names

    def run(self, name: str, role: Role) -> Any:
        self.calls.append((name, role))
        if self._error is not None:
            raise self._error
        return self._result


def test_committee_receives_code_computed_numbers(tmp_path) -> None:
    config = _config(tmp_path)
    api = FakeSheetsApi({"kehadiran": _table()})
    service = RecapService(config, api, _now)
    registry = CommandRegistry()
    register_recap_command(registry, service)

    outcome = registry.dispatch_result(
        "/rekap kehadiran", _context(config, Role.COMMITTEE)
    )

    expected = compute_recap(
        config.recaps["kehadiran"],
        _table(),
        _now,
        max_groups=config.tools.recap.max_groups,
    )
    assert outcome.kind is ResponseKind.RECAP
    assert outcome.text == render_recap(expected)
    assert "Data: 4 dari 5 baris, 1 sel nominal tidak terbaca" in outcome.text
    assert "Nominal: 2.250,50" in outcome.text
    assert 'Dihitung oleh kode dari sheet "kehadiran".' in outcome.text
    assert api.calls == [("kehadiran", "Kehadiran!A1:D6", None)]


def test_legacy_dispatch_still_returns_str(tmp_path) -> None:
    config = _config(tmp_path)
    registry = CommandRegistry()
    register_recap_command(
        registry, RecapService(config, FakeSheetsApi({"kehadiran": _table()}), _now)
    )

    reply = registry.dispatch("/rekap kehadiran", _context(config, Role.COMMITTEE))

    assert isinstance(reply, str)
    assert "Rekap Kehadiran" in reply


def test_missing_and_unknown_names_list_available_names(tmp_path) -> None:
    config = _config(tmp_path)
    service = RecordingService()
    registry = CommandRegistry()
    register_recap_command(registry, service)

    without_args = registry.dispatch_result(
        "/rekap", _context(config, Role.COMMITTEE)
    )
    unknown = registry.dispatch_result(
        "/rekap rahasia", _context(config, Role.COMMITTEE)
    )

    assert unknown.text == without_args.text
    assert without_args.text.splitlines() == [
        "Rekap tersedia:",
        "- kehadiran",
        "- panitia",
        "Ketik /rekap <nama> untuk menghitung.",
    ]
    assert service.calls == []


def test_tool_error_becomes_short_safe_reply(tmp_path) -> None:
    config = _config(tmp_path)
    service = RecordingService(
        error=ToolError("timeout", "https://sheets.googleapis.com/secret")
    )
    registry = CommandRegistry()
    register_recap_command(registry, service)

    outcome = registry.dispatch_result(
        "/rekap kehadiran", _context(config, Role.COMMITTEE)
    )

    assert outcome.text == "Sheet sedang tidak merespons. Coba lagi nanti."
    assert outcome.kind is ResponseKind.RECAP
    assert "googleapis" not in outcome.text
    assert service.calls == [("kehadiran", Role.COMMITTEE)]


def test_unknown_recap_from_service_is_safe(tmp_path) -> None:
    config = _config(tmp_path)
    service = RecordingService(
        names=("kehadiran",),
        error=ToolError("not_found", "alias kehadiran tidak ada"),
    )
    registry = CommandRegistry()
    register_recap_command(registry, service)

    outcome = registry.dispatch_result(
        "/rekap kehadiran", _context(config, Role.COMMITTEE)
    )

    assert outcome.text == "Rekap tidak tersedia."
    assert "alias" not in outcome.text


@pytest.mark.parametrize("role", [Role.OWNER, Role.COMMITTEE])
def test_command_registered_for_committee_roles(tmp_path, role: Role) -> None:
    config = _config(tmp_path)
    registry = CommandRegistry()
    register_recap_command(registry, RecordingService(names=()))

    command = registry.get("rekap")

    assert command is not None
    assert command.kind is ResponseKind.RECAP
    assert role in command.roles
