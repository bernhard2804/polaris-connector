"""Textextraktion aus Office-Open-XML-Dateien (.docx, .xlsx/.xlsm, .pptx)
nur mit der Python-Standardbibliothek.

Diese Formate sind ZIP-Archive mit XML-Inhalt - fuer reine Textextraktion
genuegen zipfile + ElementTree, eine zusaetzliche Abhaengigkeit (python-docx,
openpyxl, ...) ist bewusst nicht noetig. Formatierung, Bilder, Kommentare
und Kopf-/Fusszeilen werden ignoriert; es geht nur um den inhaltlichen Text
fuer die Kuratierung. Alte Binaerformate (.doc/.xls/.ppt) werden nicht
unterstuetzt.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"

# Eingebaute Excel-Zahlenformate, die ein Datum/eine Uhrzeit darstellen.
_BUILTIN_DATE_FORMATS = set(range(14, 23)) | {45, 46, 47}
# Maximal ausgegebene Zeilen pro Tabellenblatt - grosse Datenexporte sollen
# den Prompt nicht fluten (die Gesamtlaenge wird zusaetzlich gekappt).
MAX_ROWS_PER_SHEET = 500


def _read_xml(zf: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        return ET.fromstring(zf.read(name))
    except KeyError:
        return None


# --- docx -------------------------------------------------------------------


def _docx_paragraph_text(p: ET.Element) -> str:
    parts: list[str] = []
    for el in p.iter():
        if el.tag == _W + "t" and el.text:
            parts.append(el.text)
        elif el.tag == _W + "tab":
            parts.append("\t")
        elif el.tag in (_W + "br", _W + "cr"):
            parts.append("\n")
    return "".join(parts)


def extract_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        root = _read_xml(zf, "word/document.xml")
    if root is None:
        raise ValueError("word/document.xml fehlt")
    body = root.find(_W + "body")
    if body is None:
        return ""

    lines: list[str] = []
    for child in body:
        if child.tag == _W + "p":
            text = _docx_paragraph_text(child).strip()
            if text:
                lines.append(text)
        elif child.tag == _W + "tbl":
            for row in child.iter(_W + "tr"):
                cells = [
                    " ".join(
                        t for t in (_docx_paragraph_text(p).strip() for p in tc.iter(_W + "p")) if t
                    )
                    for tc in row.findall(_W + "tc")
                ]
                if any(cells):
                    lines.append(" | ".join(cells))
    return "\n".join(lines)


# --- xlsx -------------------------------------------------------------------


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    root = _read_xml(zf, "xl/sharedStrings.xml")
    if root is None:
        return []
    return ["".join(t.text or "" for t in si.iter(_S + "t")) for si in root.findall(_S + "si")]


def _date_style_indices(zf: zipfile.ZipFile) -> set[int]:
    """Liefert die Indizes der Zellformate (cellXfs), die ein Datum
    darstellen - sonst stuenden Datumswerte als Excel-Seriennummer im Text."""
    root = _read_xml(zf, "xl/styles.xml")
    if root is None:
        return set()
    custom_date_ids: set[int] = set()
    num_fmts = root.find(_S + "numFmts")
    if num_fmts is not None:
        for fmt in num_fmts.findall(_S + "numFmt"):
            code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", fmt.get("formatCode", "")).lower()
            if re.search(r"[dmy]", code) and "0.0" not in code:
                custom_date_ids.add(int(fmt.get("numFmtId", "0")))
    result: set[int] = set()
    cell_xfs = root.find(_S + "cellXfs")
    if cell_xfs is not None:
        for idx, xf in enumerate(cell_xfs.findall(_S + "xf")):
            fmt_id = int(xf.get("numFmtId", "0"))
            if fmt_id in _BUILTIN_DATE_FORMATS or fmt_id in custom_date_ids:
                result.add(idx)
    return result


def _excel_serial_to_text(value: str) -> str:
    try:
        serial = float(value)
    except ValueError:
        return value
    dt = datetime(1899, 12, 30) + timedelta(days=serial)
    return dt.strftime("%Y-%m-%d %H:%M") if serial % 1 else dt.strftime("%Y-%m-%d")


def _sheet_paths(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = _read_xml(zf, "xl/workbook.xml")
    rels = _read_xml(zf, "xl/_rels/workbook.xml.rels")
    if workbook is None or rels is None:
        return []
    targets = {r.get("Id"): r.get("Target", "") for r in rels.findall(_PR + "Relationship")}
    result: list[tuple[str, str]] = []
    sheets = workbook.find(_S + "sheets")
    for sheet in sheets.findall(_S + "sheet") if sheets is not None else []:
        target = targets.get(sheet.get(_R + "id"), "")
        if not target:
            continue
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        result.append((sheet.get("name", "?"), path))
    return result


def _cell_text(c: ET.Element, shared: list[str], date_styles: set[int]) -> str:
    t = c.get("t")
    if t == "inlineStr":
        return "".join(x.text or "" for x in c.iter(_S + "t"))
    v = c.find(_S + "v")
    if v is None or v.text is None:
        return ""
    if t == "s":
        try:
            return shared[int(v.text)]
        except (ValueError, IndexError):
            return ""
    if t == "b":
        return "WAHR" if v.text == "1" else "FALSCH"
    if t in (None, "n") and int(c.get("s", "0")) in date_styles:
        return _excel_serial_to_text(v.text)
    if t in (None, "n"):
        return _clean_number(v.text)
    return v.text


def _clean_number(value: str) -> str:
    """Entfernt Gleitkomma-Rauschen (28.000000000000004 -> 28)."""
    try:
        return f"{float(value):.12g}"
    except ValueError:
        return value


def extract_xlsx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared = _shared_strings(zf)
        date_styles = _date_style_indices(zf)
        blocks: list[str] = []
        for name, path in _sheet_paths(zf):
            root = _read_xml(zf, path)
            if root is None:
                continue
            rows: list[str] = []
            truncated = False
            for row in root.iter(_S + "row"):
                # Leere Zellen weglassen: Layout-Tabellen (verbundene Zellen)
                # erzeugen sonst lange " |  |  |"-Ketten, die nur Budget kosten.
                cells = [
                    text
                    for text in (_cell_text(c, shared, date_styles).strip() for c in row.findall(_S + "c"))
                    if text
                ]
                if not cells:
                    continue
                if len(rows) >= MAX_ROWS_PER_SHEET:
                    truncated = True
                    break
                rows.append(" | ".join(cells))
            if rows:
                block = f"## Tabellenblatt: {name}\n" + "\n".join(rows)
                if truncated:
                    block += f"\n[... weitere Zeilen abgeschnitten, nur die ersten {MAX_ROWS_PER_SHEET}]"
                blocks.append(block)
    return "\n\n".join(blocks)


# --- pptx -------------------------------------------------------------------


def extract_pptx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        slide_names = sorted(
            (n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)),
        )
        blocks: list[str] = []
        for idx, name in enumerate(slide_names, start=1):
            root = _read_xml(zf, name)
            if root is None:
                continue
            paragraphs = [
                "".join(t.text or "" for t in p.iter(_A + "t")).strip() for p in root.iter(_A + "p")
            ]
            text = "\n".join(p for p in paragraphs if p)
            if text:
                blocks.append(f"## Folie {idx}\n{text}")
    return "\n\n".join(blocks)
