"""Deterministic recap computation over an allowlisted spreadsheet.

The model never computes or guesses a number: every figure in a recap comes
from this module. Numbers are aggregated with pandas in code, rendered into
Indonesian text in code, and only then handed to the formatter.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sanka.config import RecapDefinition, RecapFilter, RecapMetric, SankaConfig
from sanka.policy import Role, can_use_tool
from sanka.tools.registry import ToolError, ToolSpec, ToolResult
from sanka.tools.sheets import SheetsApi

__all__ = [
    "RecapDefinition",
    "RecapFilter",
    "RecapMetric",
    "RecapResult",
    "RecapService",
    "compute_recap",
    "format_number",
    "make_recap_tool",
    "render_recap",
]

RECAP_NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"
_NUMERIC_OPS = frozenset({"sum", "mean", "min", "max"})
_KNOWN_OPS = _NUMERIC_OPS | {"count"}
_UNREADABLE_LINE = (
    "Data: {matched} dari {total} baris, {skipped} sel nominal tidak terbaca"
)
_CODE_ATTRIBUTION = (
    'Dihitung oleh kode dari sheet "{alias}". Bukan perkiraan model.'
)


@dataclass(frozen=True, slots=True)
class RecapResult:
    """A computed recap plus the metadata needed to render it."""

    name: str
    title: str
    generated_at: str
    total_rows: int
    matched_rows: int
    skipped_numeric_cells: int
    groups: tuple[Mapping[str, Any], ...] = ()
    truncated_groups: int = 0
    filters: tuple[RecapFilter, ...] = ()
    group_by: tuple[str, ...] = ()
    alias: str = ""

    def to_data(self) -> dict[str, Any]:
        """Return only the serialized contract keys (no alias, no filters)."""

        return {
            "name": self.name,
            "title": self.title,
            "generated_at": self.generated_at,
            "total_rows": self.total_rows,
            "matched_rows": self.matched_rows,
            "skipped_numeric_cells": self.skipped_numeric_cells,
            "groups": [
                {
                    "keys": list(group["keys"]),
                    "count": group["count"],
                    "sums": dict(group["sums"]),
                }
                for group in self.groups
            ],
            "truncated_groups": self.truncated_groups,
        }


def compute_recap(
    definition: RecapDefinition,
    table: Any,
    now: Callable[[], datetime],
    *,
    max_groups: int = 20,
) -> RecapResult:
    """Aggregate ``table`` according to ``definition``.

    ``table`` is duck-typed: ``values`` (header first, possibly with leading
    rows before ``header_row``) is preferred, otherwise ``header``/``rows``.
    No network access happens here; the caller fetches the sheet.
    """

    # Lazy import: pandas is heavy and only recap needs it.
    import pandas as pd

    header, raw_rows = _extract_table(table, definition.header_row)
    columns = _column_lookup(header)
    rows = [_normalise_row(row, len(header)) for row in raw_rows]
    frame = pd.DataFrame(rows, columns=[str(column) for column in header])

    total_rows = len(frame)
    matched = _apply_filters(frame, definition.filters, columns)
    matched_frame = frame.loc[matched]

    numeric_metrics: list[tuple[RecapMetric, str]] = []
    for metric in definition.metrics:
        op = metric.op.strip().lower()
        if op not in _KNOWN_OPS:
            raise ToolError(
                "invalid_arguments",
                f"Operasi rekap {metric.op!r} tidak dikenal.",
            )
        if op == "count":
            continue
        if not metric.column:
            raise ToolError(
                "invalid_arguments",
                f"Metrik {op!r} membutuhkan kolom.",
            )
        actual = _resolve_column(columns, metric.column)
        numeric_metrics.append((metric, actual))

    numeric_cache: dict[str, Any] = {}
    skipped_columns: list[str] = []
    for _metric, actual in numeric_metrics:
        if actual in numeric_cache:
            continue
        raw = (
            matched_frame[actual]
            .astype("string")
            .str.replace(",", ".", regex=False)
            .str.strip()
        )
        numeric = pd.to_numeric(raw, errors="coerce")
        numeric_cache[actual] = numeric
        non_empty = raw.notna() & (raw != "")
        if bool((non_empty & numeric.isna()).any()):
            skipped_columns.append(actual)
    skipped_cells = sum(
        int(((numeric_cache[column].isna()) & _non_empty(matched_frame[column])).sum())
        for column in skipped_columns
    )

    group_columns = [
        _resolve_column(columns, column) for column in definition.group_by
    ]
    buckets = _bucket_rows(matched_frame, group_columns)

    groups: list[dict[str, Any]] = []
    for key, indices in buckets.items():
        sums: dict[str, Any] = {}
        for metric, actual in numeric_metrics:
            op = metric.op.strip().lower()
            series = numeric_cache[actual].loc[indices]
            column_label = metric.column or actual
            label = column_label if op == "sum" else f"{op}:{column_label}"
            sums[label] = _aggregate(series, op)
        groups.append({"keys": list(key), "count": len(indices), "sums": sums})

    ordered = sorted(groups, key=lambda group: (-group["count"], group["keys"]))
    limit = max(1, int(max_groups))
    shown = ordered[:limit]
    truncated_groups = max(0, len(ordered) - len(shown))

    return RecapResult(
        name=definition.name,
        title=definition.title,
        alias=definition.alias,
        generated_at=_iso_utc(now()),
        total_rows=total_rows,
        matched_rows=len(matched_frame),
        skipped_numeric_cells=int(skipped_cells),
        groups=tuple(shown),
        truncated_groups=truncated_groups,
        filters=tuple(definition.filters),
        group_by=tuple(definition.group_by),
    )


def format_number(value: float | int | None, decimals: int = 0) -> str:
    """Format with Indonesian separators: ``.`` for thousands, ``,`` decimal."""

    places = max(0, int(decimals))
    if value is None:
        return "0"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    if not math.isfinite(number):
        return "0"

    factor = 10**places
    scaled = int(round(abs(number) * factor))
    whole, fraction = divmod(scaled, factor)
    text = f"{whole:,}".replace(",", ".")
    if places:
        text = f"{text},{fraction:0{places}d}"
    if number < 0 and scaled != 0:
        return f"-{text}"
    return text


def render_recap(result: RecapResult) -> str:
    """Build the final, user-facing recap text in code (never the model)."""

    lines = [f"{result.title} \u2014 {_display_timestamp(result.generated_at)} UTC"]
    if result.filters:
        filter_text = ", ".join(
            f"{item.column} = {item.equals}" for item in result.filters
        )
        lines.append(f"Filter: {filter_text}")
    lines.append(
        _UNREADABLE_LINE.format(
            matched=format_number(result.matched_rows),
            total=format_number(result.total_rows),
            skipped=format_number(result.skipped_numeric_cells),
        )
    )
    if result.group_by:
        lines.append("Kelompok: " + ", ".join(result.group_by))

    if not result.groups:
        lines.append("Tidak ada baris yang cocok.")
    for index, group in enumerate(result.groups, start=1):
        keys = [str(key) for key in group["keys"]]
        label = " / ".join(keys) if keys else "Semua data"
        parts = [f"{format_number(group['count'])} baris"]
        for name, value in group["sums"].items():
            parts.append(f"{name}: {_format_metric(value)}")
        lines.append(f"{index}. {label} \u2014 " + "; ".join(parts))

    if result.truncated_groups:
        lines.append(
            "Grup lain tidak ditampilkan: "
            f"{format_number(result.truncated_groups)}."
        )
    lines.append(f"Total: {format_number(result.matched_rows)} baris cocok.")
    lines.append(_CODE_ATTRIBUTION.format(alias=result.alias))
    return "\n".join(lines)


class RecapService:
    """Resolve configured recap names, fetch the sheet, compute in code."""

    def __init__(
        self,
        config: SankaConfig,
        sheets: SheetsApi,
        now: Callable[[], datetime],
    ) -> None:
        self._config = config
        self._sheets = sheets
        self._now = now

    def names(self) -> tuple[str, ...]:
        return tuple(self._config.recaps.keys())

    def run(self, name: str, role: Role) -> RecapResult:
        """Gate on policy, resolve the name, fetch, and compute."""

        if not can_use_tool(
            role,
            "recap",
            shell_enabled=self._config.tools.shell_enabled,
        ):
            raise ToolError("forbidden", "Rekap tidak tersedia untuk peran ini.")

        definition = self._config.recaps.get(name)
        if definition is None:
            # Never echo configured aliases or spreadsheet ids.
            raise ToolError("not_found", "Rekap tidak ditemukan.")

        table = self._sheets.fetch(definition.alias, definition.range)
        return compute_recap(
            definition,
            table,
            self._now,
            max_groups=self._config.tools.recap.max_groups,
        )


def _recap_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "title": {"type": "string"},
            "generated_at": {"type": "string"},
            "total_rows": {"type": "integer", "minimum": 0},
            "matched_rows": {"type": "integer", "minimum": 0},
            "skipped_numeric_cells": {"type": "integer", "minimum": 0},
            "groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "keys": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "count": {"type": "integer", "minimum": 0},
                        "sums": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                    },
                    "required": ["keys", "count", "sums"],
                    "additionalProperties": False,
                },
            },
            "truncated_groups": {"type": "integer", "minimum": 0},
        },
        "required": [
            "name",
            "title",
            "generated_at",
            "total_rows",
            "matched_rows",
            "skipped_numeric_cells",
            "groups",
            "truncated_groups",
        ],
        "additionalProperties": False,
    }


def make_recap_tool(service: RecapService) -> ToolSpec:
    """Build the ``recap`` tool spec around a configured service."""

    def handler(arguments: Mapping[str, Any], context: Any) -> ToolResult:
        name = arguments["name"]
        result = service.run(str(name), context.role)
        return ToolResult(
            tool="recap",
            ok=True,
            data=result.to_data(),
            text=render_recap(result),
        )

    return ToolSpec(
        name="recap",
        description=(
            "Hitung rekap dari sheet terdaftar. Angka dihitung oleh kode, "
            "bukan diperkirakan."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "pattern": RECAP_NAME_PATTERN,
                    "description": "Nama rekap yang terdaftar di config.",
                }
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        output_schema=_recap_output_schema(),
        handler=handler,
    )


# ---------------------------------------------------------------------------
# Internals


def _extract_table(
    table: Any, header_row: int
) -> tuple[tuple[Any, ...], Sequence[Sequence[Any]]]:
    values = getattr(table, "values", None)
    if values:
        rows = tuple(tuple(str(cell) for cell in row) for row in values)
        index = max(1, int(header_row)) - 1
        if index >= len(rows):
            raise ToolError(
                "invalid_arguments",
                "header_row melewati jumlah baris sheet.",
            )
        return rows[index], rows[index + 1 :]

    header = tuple(str(cell) for cell in (getattr(table, "header", None) or ()))
    if not header:
        raise ToolError("invalid_arguments", "Sheet tidak memiliki header.")
    rows = tuple(
        tuple(str(cell) for cell in row)
        for row in (getattr(table, "rows", None) or ())
    )
    return header, rows


def _column_lookup(header: Sequence[Any]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for column in header:
        name = str(column)
        lookup.setdefault(name.strip().casefold(), name)
    return lookup


def _resolve_column(lookup: Mapping[str, str], name: str) -> str:
    actual = lookup.get(str(name).strip().casefold())
    if actual is None:
        raise ToolError(
            "invalid_arguments",
            f"Kolom {name!r} tidak ada pada sheet.",
        )
    return actual


def _normalise_row(row: Sequence[Any], width: int) -> tuple[str, ...]:
    cells = tuple(str(cell) for cell in row)
    if len(cells) >= width:
        return cells[:width]
    return cells + ("",) * (width - len(cells))


def _apply_filters(
    frame: Any,
    filters: Sequence[RecapFilter],
    columns: Mapping[str, str],
) -> Any:
    mask = None
    for item in filters:
        column = _resolve_column(columns, item.column)
        series = (
            frame[column]
            .astype("string")
            .str.strip()
            .str.casefold()
            .fillna("")
        )
        condition = series == item.equals.strip().casefold()
        mask = condition if mask is None else (mask & condition)
    if mask is None:
        return frame.index
    return mask


def _non_empty(series: Any) -> Any:
    raw = series.astype("string").str.strip()
    return raw.notna() & (raw != "")


def _bucket_rows(
    frame: Any, group_columns: Sequence[str]
) -> dict[tuple[str, ...], list[int]]:
    if not group_columns:
        return {(): list(frame.index)}

    buckets: dict[tuple[str, ...], list[int]] = {}
    if frame.empty:
        return buckets
    key_rows = frame[group_columns].itertuples(index=False, name=None)
    for index, row in zip(frame.index, key_rows):
        key = tuple("" if value is None else str(value) for value in row)
        buckets.setdefault(key, []).append(int(index))
    return buckets


def _aggregate(series: Any, op: str) -> int | float:
    if op == "sum":
        value = series.sum(min_count=0)
    elif op == "mean":
        value = series.mean()
    elif op == "min":
        value = series.min()
    elif op == "max":
        value = series.max()
    else:  # pragma: no cover - guarded by compute_recap
        raise ToolError("invalid_arguments", "Operasi rekap tidak dikenal.")
    return _clean_number(value)


def _clean_number(value: Any) -> int | float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(number):
        return 0
    rounded = round(number, 6)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def _iso_utc(moment: datetime) -> str:
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _display_timestamp(generated_at: str) -> str:
    text = generated_at.strip()
    if len(text) >= 16 and text[4] == "-" and text[7] == "-" and text[10] == "T":
        return f"{text[:10]} {text[11:16]}"
    return text


def _format_metric(value: Any) -> str:
    decimals = 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    if math.isfinite(number) and not number.is_integer():
        decimals = 2
    return format_number(number, decimals)

