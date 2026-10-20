"""Text extraction shared by ``doc_read`` and ``attachment_read``.

The functions here never resolve user paths; callers validate the file first.
Every extractor returns ``(text, units, truncated)`` and raises ``ToolError``
with a safe message on failure.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sanka.tools.registry import ToolError


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def read_pdf(path: Path, max_pages: int) -> tuple[str, int, bool]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ToolError("not_configured", "Pembaca PDF tidak tersedia.") from exc

    try:
        reader = PdfReader(str(path))
        total_pages = len(reader.pages)
        pages_to_read = min(max_pages, total_pages)
        parts: list[str] = []
        for page in reader.pages[:pages_to_read]:
            extracted = page.extract_text()
            parts.append(extracted if isinstance(extracted, str) else "")
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError("upstream_error", "Berkas PDF tidak dapat dibaca.") from exc

    text = "\n".join(parts).strip()
    return text, pages_to_read, total_pages > pages_to_read


def read_xlsx(path: Path, max_rows: int) -> tuple[str, int, bool]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ToolError("not_configured", "Pembaca Excel tidak tersedia.") from exc

    try:
        workbook = load_workbook(filename=str(path), read_only=True, data_only=True)
    except Exception as exc:
        raise ToolError("upstream_error", "Berkas Excel tidak dapat dibaca.") from exc

    try:
        sheet = workbook.active
        if sheet is None:
            return "", 0, False
        lines: list[str] = []
        truncated = False
        for index, row in enumerate(sheet.iter_rows(values_only=True)):
            if index >= max_rows:
                truncated = True
                break
            lines.append("\t".join(_cell_text(value) for value in row))
        return "\n".join(lines), len(lines), truncated
    finally:
        workbook.close()


def read_csv(path: Path, max_rows: int) -> tuple[str, int, bool]:
    lines: list[str] = []
    truncated = False
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            for index, row in enumerate(reader):
                if index >= max_rows:
                    truncated = True
                    break
                lines.append("\t".join(row))
    except OSError as exc:
        raise ToolError("upstream_error", "Berkas CSV tidak dapat dibaca.") from exc
    return "\n".join(lines), len(lines), truncated


def read_text(path: Path) -> tuple[str, int, bool]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ToolError("upstream_error", "Berkas teks tidak dapat dibaca.") from exc
    return text, 1, False


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars < 0:
        return text, False
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True
