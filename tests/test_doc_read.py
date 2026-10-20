"""Tests for the ``doc_read`` tool.

Fixtures are committed binaries under ``tests/fixtures/docs``; the builder in
that directory is never executed here.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from sanka.config import DocsConfig, SankaConfig
from sanka.tools.docs import make_doc_read_tool
from sanka.tools.registry import ToolError

FIXTURES = Path(__file__).parent / "fixtures" / "docs"
PDF_MARKER = "SANKA DOC READ FIXTURE"
FIXTURE_NAMES = ("sample.pdf", "sample.xlsx", "sample.csv", "sample.txt")


def _root(tmp_path: Path, names: tuple[str, ...] = FIXTURE_NAMES) -> Path:
    root = tmp_path / "docs"
    root.mkdir()
    for name in names:
        shutil.copyfile(FIXTURES / name, root / name)
    return root


def _handler(
    config: SankaConfig,
    root: Path,
    *,
    max_bytes: int = 5_000_000,
    max_chars: int = 12_000,
) -> Any:
    docs = DocsConfig(dir=str(root), max_bytes=max_bytes, max_chars=max_chars)
    sanka_config = replace(config, tools=replace(config.tools, docs=docs))
    return make_doc_read_tool(sanka_config).handler


def _read(handler: Any, path: str, **arguments: Any) -> dict[str, Any]:
    result = handler({"path": path, **arguments}, None)
    assert result.ok is True
    return dict(result.data)


def test_pdf_marker_is_extracted(config: SankaConfig, tmp_path: Path) -> None:
    root = _root(tmp_path, ("sample.pdf",))
    data = _read(_handler(config, root), "sample.pdf")

    assert data["kind"] == "pdf"
    assert data["units"] == 1
    assert data["truncated"] is False
    assert PDF_MARKER in data["text"]
    assert data["characters"] == len(data["text"])


def test_xlsx_keeps_header_rows_and_text_numbers(
    config: SankaConfig, tmp_path: Path
) -> None:
    root = _root(tmp_path, ("sample.xlsx",))
    data = _read(_handler(config, root), "sample.xlsx")

    lines = data["text"].splitlines()
    assert data["kind"] == "xlsx"
    assert data["units"] == 4
    assert lines[0] == "Nama\tDivisi\tNominal\tCatatan"
    assert lines[1].startswith("Ayu\tMedia\t1500")
    assert "00123" in lines[2]
    assert data["truncated"] is False


def test_xlsx_max_rows_truncates(config: SankaConfig, tmp_path: Path) -> None:
    root = _root(tmp_path, ("sample.xlsx",))
    data = _read(_handler(config, root), "sample.xlsx", max_rows=2)

    assert data["units"] == 2
    assert data["truncated"] is True


def test_csv_is_read_as_tab_separated_rows(
    config: SankaConfig, tmp_path: Path
) -> None:
    root = _root(tmp_path, ("sample.csv",))
    data = _read(_handler(config, root), "sample.csv")

    assert data["kind"] == "csv"
    assert data["units"] == 4
    assert data["text"].splitlines()[0] == "Nama\tDivisi\tNominal\tCatatan"
    assert "00123" in data["text"]
    assert data["truncated"] is False


def test_text_file_is_returned_verbatim(config: SankaConfig, tmp_path: Path) -> None:
    root = _root(tmp_path, ("sample.txt",))
    data = _read(_handler(config, root), "sample.txt")

    assert data["kind"] == "text"
    assert data["units"] == 1
    assert PDF_MARKER in data["text"]


def test_text_is_capped_by_max_chars(config: SankaConfig, tmp_path: Path) -> None:
    root = _root(tmp_path, ("sample.txt",))
    data = _read(_handler(config, root, max_chars=10), "sample.txt")

    assert data["truncated"] is True
    assert len(data["text"]) == 10
    assert data["characters"] > 10


def test_missing_file_lists_at_most_five_basenames(
    config: SankaConfig, tmp_path: Path
) -> None:
    root = _root(tmp_path, ())
    for index in range(7):
        (root / f"notes-{index}.txt").write_text(f"body {index}", encoding="utf-8")

    with pytest.raises(ToolError) as raised:
        _handler(config, root)({"path": "notes-9.txt"}, None)

    assert raised.value.code == "not_found"
    message = raised.value.message
    assert "Kandidat: " in message
    candidates = message.split("Kandidat: ", 1)[1].split(", ")
    assert len(candidates) <= 5
    assert all("/" not in candidate for candidate in candidates)
    assert all(candidate.endswith(".txt") for candidate in candidates)


def test_parent_traversal_is_rejected(config: SankaConfig, tmp_path: Path) -> None:
    root = _root(tmp_path, ("sample.txt",))
    with pytest.raises(ToolError) as raised:
        _handler(config, root)({"path": "../sample.txt"}, None)
    assert raised.value.code == "invalid_arguments"


def test_absolute_path_is_rejected(config: SankaConfig, tmp_path: Path) -> None:
    root = _root(tmp_path, ("sample.txt",))
    with pytest.raises(ToolError) as raised:
        _handler(config, root)({"path": str(root / "sample.txt")}, None)
    assert raised.value.code == "invalid_arguments"


def test_symlink_escape_is_rejected(config: SankaConfig, tmp_path: Path) -> None:
    root = _root(tmp_path, ())
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)

    with pytest.raises(ToolError) as raised:
        _handler(config, root)({"path": "link.txt"}, None)
    assert raised.value.code == "invalid_arguments"


def test_foreign_extension_is_rejected(config: SankaConfig, tmp_path: Path) -> None:
    root = _root(tmp_path, ())
    (root / "script.exe").write_bytes(b"MZ")

    with pytest.raises(ToolError) as raised:
        _handler(config, root)({"path": "script.exe"}, None)
    assert raised.value.code == "invalid_arguments"


def test_oversized_file_is_too_large(config: SankaConfig, tmp_path: Path) -> None:
    root = _root(tmp_path, ("sample.txt",))
    with pytest.raises(ToolError) as raised:
        _handler(config, root, max_bytes=10)({"path": "sample.txt"}, None)
    assert raised.value.code == "too_large"


def test_empty_docs_dir_is_not_configured(
    config: SankaConfig, tmp_path: Path
) -> None:
    docs = DocsConfig(dir="")
    sanka_config = replace(config, tools=replace(config.tools, docs=docs))
    handler = make_doc_read_tool(sanka_config).handler

    with pytest.raises(ToolError) as raised:
        handler({"path": "sample.txt"}, None)
    assert raised.value.code == "not_configured"
