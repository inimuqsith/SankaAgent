"""XLSX minimal (tulis/baca) dengan pustaka standar saja.

Repo ini tidak memakai openpyxl/pandas, jadi workbook dibuat langsung dari XML di dalam ZIP.
Cukup untuk kebutuhan audit: beberapa sheet, header tebal, kolom lebar, sel teks inline, dan
pembacaan kembali komentar yang diketik manusia (inline string maupun sharedStrings).
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, unescape

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
{sheets}</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

STYLES = """<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="1"><fill><patternFill patternType="none"/></fill></fills>
<borders count="1"><border/></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>
</styleSheet>"""

SHEET_NAME = re.compile(r"[\\/*?:\[\]]")


def _column(index: int) -> str:
    name = ""
    while index > 0:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _cell(ref: str, value, bold: bool) -> str:
    style = ' s="1"' if bold else ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    text = escape(str(value if value is not None else ""))
    return f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{text}</t></is></c>'


def write_workbook(path: Path, sheets: list[tuple[str, list[list], list[int]]]) -> Path:
    """[(nama_sheet, [[sel,...],...], [lebar_kolom,...]), ...] -> file .xlsx."""
    path.parent.mkdir(parents=True, exist_ok=True)
    overrides = []
    workbook_sheets = []
    rels = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, (name, rows, widths) in enumerate(sheets, start=1):
            safe = SHEET_NAME.sub("-", name)[:31] or f"sheet{index}"
            overrides.append(
                f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            )
            workbook_sheets.append(f'<sheet name="{escape(safe, {chr(34): "&quot;"})}" sheetId="{index}" r:id="rId{index}"/>')
            rels.append(
                f'<Relationship Id="rId{index}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{index}.xml"/>'
            )
            cols = "".join(
                f'<col min="{position}" max="{position}" width="{width}" customWidth="1"/>'
                for position, width in enumerate(widths, start=1)
            )
            body = []
            for row_index, row in enumerate(rows, start=1):
                cells = "".join(
                    _cell(f"{_column(position)}{row_index}", value, row_index == 1)
                    for position, value in enumerate(row, start=1)
                )
                body.append(f'<row r="{row_index}">{cells}</row>')
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"<cols>{cols}</cols><sheetData>{''.join(body)}</sheetData></worksheet>",
            )
        archive.writestr(
            "[Content_Types].xml", CONTENT_TYPES.format(sheets="".join(overrides))
        )
        archive.writestr("_rels/.rels", ROOT_RELS)
        archive.writestr("xl/styles.xml", STYLES)
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{''.join(workbook_sheets)}</sheets></workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{''.join(rels)}"
            '<Relationship Id="rIdStyles" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/></Relationships>',
        )
    return path


CELL = re.compile(r"<c\b([^>]*)>(.*?)</c>|<c\b([^>]*)/>", re.DOTALL)
ATTR = re.compile(r'(\w+)="([^"]*)"')
INLINE = re.compile(r"<t[^>]*>(.*?)</t>", re.DOTALL)
VALUE = re.compile(r"<v>(.*?)</v>", re.DOTALL)
ROW = re.compile(r"<row\b[^>]*>(.*?)</row>", re.DOTALL)
SHEET = re.compile(r'<sheet name="([^"]+)"[^>]*r:id="rId(\d+)"')


def _text(attributes: dict, body: str, shared: list[str]) -> str:
    return unescape(
        _raw_text(attributes, body, shared), {"&quot;": '"', "&apos;": "'", "&#xA;": "\n"}
    )


def _raw_text(attributes: dict, body: str, shared: list[str]) -> str:
    kind = attributes.get("t", "")
    if kind == "s":
        found = VALUE.search(body)
        if not found:
            return ""
        try:
            return shared[int(found.group(1))]
        except (ValueError, IndexError):
            return ""
    if kind == "inlineStr":
        return "".join(INLINE.findall(body))
    found = VALUE.search(body)
    return found.group(1) if found else ""


def read_workbook(path: Path) -> dict[str, list[list[str]]]:
    """{nama_sheet: [[sel,...],...]}; mendukung inline string dan sharedStrings."""
    shared: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "xl/sharedStrings.xml" in names:
            raw = archive.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            shared = [INLINE.sub(lambda m: m.group(1), item) for item in re.findall(r"<si>(.*?)</si>", raw, re.DOTALL)]
        workbook = archive.read("xl/workbook.xml").decode("utf-8", "replace")
        rels_xml = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8", "replace")
        targets = {
            match.group(1): match.group(2)
            for match in re.finditer(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels_xml)
        }
        sheets: dict[str, list[list[str]]] = {}
        for name, rel in SHEET.findall(workbook):
            target = targets.get(f"rId{rel}", "")
            member = "xl/" + target.lstrip("/").replace("xl/", "")
            if member not in names:
                continue
            body = archive.read(member).decode("utf-8", "replace")
            rows: list[list[str]] = []
            for row in ROW.findall(body):
                values: dict[int, str] = {}
                for match in CELL.finditer(row):
                    attributes = dict(ATTR.findall(match.group(1) or match.group(3) or ""))
                    cell_body = match.group(2) or ""
                    ref = attributes.get("r", "")
                    letters = re.match(r"([A-Z]+)", ref)
                    if not letters:
                        continue
                    column = 0
                    for char in letters.group(1):
                        column = column * 26 + (ord(char) - 64)
                    values[column] = _text(attributes, cell_body, shared)
                width = max(values) if values else 0
                rows.append([values.get(index, "") for index in range(1, width + 1)])
            sheets[name] = rows
    return sheets
