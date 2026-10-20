"""Deterministic test doubles for the plan-5 tools.

None of these touch the network or read credentials. They mirror the small
protocols the tools depend on so each test can inject data directly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FakeSheetTable:
    """The subset of ``SheetTable`` the recap and sheet tools rely on.

    ``values`` contains every row returned by the fake endpoint, header row
    first. ``header``/``rows``/``row_count`` are derived properties so the
    object also matches the real table shape.
    """

    alias: str
    range: str
    values: tuple[tuple[str, ...], ...] = ()
    truncated: bool = False

    @property
    def header(self) -> tuple[str, ...]:
        return self.values[0] if self.values else ()

    @property
    def rows(self) -> tuple[tuple[str, ...], ...]:
        return self.values[1:]

    @property
    def row_count(self) -> int:
        return max(0, len(self.values) - 1)

    @classmethod
    def from_rows(
        cls,
        alias: str,
        cell_range: str,
        header: Sequence[Any],
        rows: Sequence[Sequence[Any]],
        *,
        truncated: bool = False,
    ) -> FakeSheetTable:
        values = (
            tuple("" if value is None else str(value) for value in header),
            *(
                tuple("" if value is None else str(value) for value in row)
                for row in rows
            ),
        )
        return cls(
            alias=alias,
            range=cell_range,
            values=values,
            truncated=truncated,
        )


class FakeSheetsApi:
    """In-memory ``SheetsApi`` keyed by alias.

    Unknown aliases raise the same ``not_found`` error as the real client.
    ``max_rows`` truncates data rows and marks the table as truncated, matching
    the sheet_read contract.
    """

    def __init__(
        self,
        tables: Mapping[str, FakeSheetTable | Mapping[str, Any]] | None = None,
    ) -> None:
        self.tables: dict[str, FakeSheetTable | Mapping[str, Any]] = dict(
            tables or {}
        )
        self.calls: list[tuple[str, str, int | None]] = []

    def fetch(
        self, alias: str, cell_range: str, *, max_rows: int | None = None
    ) -> FakeSheetTable:
        from sanka.tools.registry import ToolError

        if alias not in self.tables:
            raise ToolError("not_found", "Sheet tidak ditemukan.")
        raw = self.tables[alias]
        if isinstance(raw, Mapping):
            table = FakeSheetTable.from_rows(
                alias,
                cell_range,
                raw.get("header", ()),
                raw.get("rows", ()),
            )
        else:
            table = raw
        self.calls.append((alias, cell_range, max_rows))
        if max_rows is not None and table.row_count > max_rows:
            values = (table.header, *table.rows[:max_rows])
            return FakeSheetTable(
                alias=table.alias,
                range=cell_range,
                values=values,
                truncated=True,
            )
        return FakeSheetTable(
            alias=table.alias,
            range=cell_range,
            values=table.values,
            truncated=table.truncated,
        )


class FakeSearchProvider:
    """In-memory ``SearchProvider`` returning fixed results in order."""

    def __init__(
        self,
        results: Sequence[Any] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._results = list(results or [])
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, limit: int = 5) -> tuple[Any, ...]:
        from sanka.tools.web import SearchHit

        self.calls.append((query, limit))
        if self.error is not None:
            raise self.error
        hits: list[Any] = []
        for item in self._results[:limit]:
            if isinstance(item, SearchHit):
                hits.append(item)
            elif isinstance(item, Mapping):
                hits.append(
                    SearchHit(
                        title=str(item.get("title", "")),
                        url=str(item.get("url", "")),
                        snippet=str(item.get("snippet", "")),
                    )
                )
            else:
                hits.append(item)
        return tuple(hits)


class FakeTokenProvider:
    """Deterministic access token with a call counter.

    The token never appears in ``repr`` so tests cannot accidentally log it.
    """

    def __init__(self, token: str = "fake-access-token") -> None:
        self._token = token
        self.calls = 0

    def token(self) -> str:
        self.calls += 1
        return self._token

    def __repr__(self) -> str:
        return f"FakeTokenProvider(calls={self.calls})"
