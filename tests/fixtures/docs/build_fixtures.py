"""One-shot generator for the ``doc_read`` fixtures.

Run manually after changing the fixture layout::

    python tests/fixtures/docs/build_fixtures.py

The test suite never calls this script. It rewrites ``sample.pdf``,
``sample.xlsx``, ``sample.csv``, and ``sample.txt`` next to itself.

The PDF is emitted from a byte template because pypdf can read PDFs but has no
high-level API for typesetting a text page. The single page contains the
marker ``SANKA DOC READ FIXTURE``; keep that string in sync with
``tests/test_doc_read.py``. The XLSX uses openpyxl when it is installed and
falls back to an equivalent minimal OOXML template otherwise.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PDF_MARKER = "SANKA DOC READ FIXTURE"

HEADER = ("Nama", "Divisi", "Nominal", "Catatan")
ROWS = (
    ("Ayu", "Media", "1500", "hadir"),
    ("Budi", "Acara", "00123", "teks angka"),
    ("Citra", "Media", "2500", ""),
)


def build_pdf() -> bytes:
    """Minimal one-page PDF carrying :data:`PDF_MARKER`."""

    stream = (
        b"BT /F1 14 Tf 72 720 Td (" + PDF_MARKER.encode("latin-1") + b") Tj ET\n"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream
        + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        b"trailer\n"
        + f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
        + f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)


def _column(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _sheet_parts() -> tuple[str, str]:
    """Return ``(worksheet_xml, shared_strings_xml)``."""

    strings: list[str] = []
    string_index: dict[str, int] = {}

    def shared(value: str) -> int:
        if value not in string_index:
            string_index[value] = len(strings)
            strings.append(value)
        return string_index[value]

    rows: list[str] = []
    for row_index, row in enumerate((HEADER, *ROWS), start=1):
        cells: list[str] = []
        for column_index, value in enumerate(row):
            ref = f"{_column(column_index)}{row_index}"
            if value == "":
                cells.append(f'<c r="{ref}"/>')
            elif value.isdigit() and not (
                len(value) > 1 and value.startswith("0")
            ):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="s"><v>{shared(value)}</v></c>')
        rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(rows)}</sheetData>'
        "</worksheet>"
    )
    items = "".join(f"<si><t>{_escape(value)}</t></si>" for value in strings)
    shared_strings = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(strings)}" uniqueCount="{len(strings)}">{items}</sst>'
    )
    return worksheet, shared_strings


def _template_xlsx() -> bytes:
    worksheet, shared_strings = _sheet_parts()
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/sharedStrings.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
            'Target="sharedStrings.xml"/>'
            "</Relationships>"
        ),
        "xl/worksheets/sheet1.xml": worksheet,
        "xl/sharedStrings.xml": shared_strings,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)
    return buffer.getvalue()


def build_xlsx() -> bytes:
    try:
        from openpyxl import Workbook
    except ImportError:
        return _template_xlsx()

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(HEADER)
    for row in ROWS:
        values: list[str | int] = []
        for value in row:
            # Keep zero-padded identifiers as text; plain digits become numbers
            # so the tool has to stringify them.
            if value.isdigit() and not value.startswith("0"):
                values.append(int(value))
            else:
                values.append(value)
        sheet.append(values)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_csv() -> bytes:
    return ("\n".join([",".join(HEADER), *((",".join(row)) for row in ROWS)]) + "\n").encode(
        "utf-8"
    )


def build_txt() -> bytes:
    return (
        f"{PDF_MARKER}\n"
        "Baris kedua untuk doc_read.\n"
        "Baris ketiga berisi teks biasa.\n"
    ).encode("utf-8")


def main() -> None:
    (HERE / "sample.pdf").write_bytes(build_pdf())
    (HERE / "sample.xlsx").write_bytes(build_xlsx())
    (HERE / "sample.csv").write_bytes(build_csv())
    (HERE / "sample.txt").write_bytes(build_txt())


if __name__ == "__main__":
    main()
