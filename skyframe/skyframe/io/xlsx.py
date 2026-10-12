"""Model tables <-> Excel ``.xlsx`` without dependencies (zipfile + a
minimal OOXML writer / reader).

Reuses the interactive-database tables of :mod:`skyframe.core.modeltables`
(the same catalogue, rows, CSV cell text and atomic ``apply_tables``):

* ``export_xlsx(model, tables=None)`` -> ``(bytes, report)``: one
  worksheet per table (sheet name = table key), header row ``_id`` +
  ``<column key> [<SI unit>]``, numbers as numeric cells (exact doubles),
  booleans as boolean cells, text / references / points (``"x,y,z; ..."``)
  as inline strings; plus ``_index`` (table, title, group, editable, rows)
  and ``_meta`` (format, units, model_sha256) sheets.
* ``import_xlsx(model, data)`` -> ``(model|None, errors, summary)``: reads
  any .xlsx (inline or shared strings, sparse cells), turns each known
  editable sheet back into rows (headers may be keys or labels, with or
  without ``[unit]``) and applies them atomically — ``_id`` columns are
  honoured only when ``_meta.model_sha256`` matches the base model, exactly
  like the zip-of-CSV import.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

__all__ = ["export_xlsx", "import_xlsx", "write_workbook", "read_workbook"]

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PNS = "http://schemas.openxmlformats.org/package/2006/relationships"
_BAD_SHEET = re.compile(r"[\[\]:*?/\\]")


def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _cell(ref: str, v) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return f'<c r="{ref}" t="b"><v>{1 if v else 0}</v></c>'
    if isinstance(v, (int, float)):
        return f'<c r="{ref}"><v>{repr(float(v)) if isinstance(v, float) else v}</v></c>'
    txt = escape(str(v))
    sp = ' xml:space="preserve"' if txt != txt.strip() else ""
    return f'<c r="{ref}" t="inlineStr"><is><t{sp}>{txt}</t></is></c>'


def write_workbook(sheets: List[Tuple[str, List[list]]]) -> bytes:
    """Minimal valid .xlsx from ``[(sheet name, rows)]`` (rows of cells:
    str / int / float / bool / None)."""
    names = []
    for name, _rows in sheets:
        nm = _BAD_SHEET.sub("_", name)[:31] or "Sheet"
        base, k = nm, 1
        while nm in names:
            k += 1
            nm = f"{base[:28]}~{k}"
        names.append(nm)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            'content-types"><Default Extension="rels" ContentType="'
            'application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"'
            '/>' + "".join(
                f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.'
                'spreadsheetml.worksheet+xml"/>' for i in range(len(names)))
            + '<Override PartName="/xl/styles.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '</Types>'))
        z.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_PNS}"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>'))
        z.writestr("xl/workbook.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{_NS}" xmlns:r="{_RNS}"><sheets>' + "".join(
                f'<sheet name="{escape(n, {chr(34): "&quot;"})}" '
                f'sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                for i, n in enumerate(names)) + '</sheets></workbook>'))
        z.writestr("xl/_rels/workbook.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_PNS}">' + "".join(
                f'<Relationship Id="rId{i + 1}" Type="http://schemas.'
                'openxmlformats.org/officeDocument/2006/relationships/'
                f'worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                for i in range(len(names)))
            + f'<Relationship Id="rId{len(names) + 1}" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/></Relationships>'))
        z.writestr("xl/styles.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<styleSheet xmlns="{_NS}"><fonts count="1"><font><sz val="11"/>'
            '<name val="Calibri"/></font></fonts><fills count="2"><fill>'
            '<patternFill patternType="none"/></fill><fill><patternFill '
            'patternType="gray125"/></fill></fills><borders count="1">'
            '<border><left/><right/><top/><bottom/><diagonal/></border>'
            '</borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" '
            'fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="1">'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '</cellXfs></styleSheet>'))
        for i, (_name, rows) in enumerate(sheets):
            body = []
            for r, row in enumerate(rows):
                cells = "".join(_cell(f"{_col(c)}{r + 1}", v)
                                for c, v in enumerate(row))
                body.append(f'<row r="{r + 1}">{cells}</row>')
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<worksheet xmlns="{_NS}"><sheetData>' + "".join(body)
                + '</sheetData></worksheet>'))
    return buf.getvalue()


def read_workbook(data: bytes) -> Dict[str, List[List[str]]]:
    """{sheet name: rows of cell TEXT} of any .xlsx (numbers -> their
    stored text, booleans -> TRUE/FALSE, strings inline or shared)."""
    z = zipfile.ZipFile(io.BytesIO(data))
    ns = {"m": _NS}
    shared: List[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", ns):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS}}}t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in rels}
    out: Dict[str, List[List[str]]] = {}
    for sh in wb.find("m:sheets", ns):
        rid = sh.get(f"{{{_RNS}}}id")
        path = target.get(rid, "")
        path = path.lstrip("/")
        if not path.startswith("xl/"):
            path = "xl/" + path
        root = ET.fromstring(z.read(path))
        rows: List[List[str]] = []
        for row in root.iter(f"{{{_NS}}}row"):
            rnum = int(row.get("r", len(rows) + 1))
            while len(rows) < rnum - 1:
                rows.append([])
            vals: List[str] = []
            for c in row.findall("m:c", ns):
                idx = _col_index(c.get("r")) if c.get("r") else len(vals)
                while len(vals) < idx:
                    vals.append("")
                t = c.get("t", "n")
                if t == "inlineStr":
                    v = "".join(x.text or "" for x in c.iter(f"{{{_NS}}}t"))
                else:
                    ve = c.find("m:v", ns)
                    v = ve.text if ve is not None and ve.text else ""
                    if t == "s" and v:
                        v = shared[int(v)]
                    elif t == "b":
                        v = "TRUE" if v == "1" else "FALSE"
                vals.append(v)
            rows.append(vals)
        out[sh.get("name")] = rows
    return out


# --------------------------------------------------------------------------- #
# model tables
# --------------------------------------------------------------------------- #
def export_xlsx(model, tables: Optional[List[str]] = None
                ) -> Tuple[bytes, dict]:
    from skyframe.core import modeltables as MT
    d = model if isinstance(model, dict) else model.to_dict()
    d = json.loads(json.dumps(d))
    sheets = []
    index = [["table", "title", "group", "editable", "rows"]]
    written: Dict[str, int] = {}
    for t in MT.catalogue():
        if tables is not None and t.key not in tables:
            continue
        cols = t.columns(d)
        rows = t.rows(d)
        head = ["_id"] + [f"{c.key} [{MT.QUANTITIES[c.quantity]}]"
                          if MT.QUANTITIES.get(c.quantity) else c.key
                          for c in cols]
        body = [head]
        for r in rows:
            line = [json.dumps(r.get("_id"), separators=(",", ":"))]
            for c in cols:
                v = r.get(c.key)
                if isinstance(v, bool) or v is None:
                    line.append(v)
                elif isinstance(v, (int, float)) and c.type in ("number",
                                                                "int"):
                    line.append(v)
                else:
                    line.append(MT._csv_cell(c.type, v))
            body.append(line)
        sheets.append((t.key, body))
        index.append([t.key, t.title, t.group, t.editable, len(rows)])
        written[t.key] = len(rows)
    meta = [["key", "value"], ["format", "skyframe-modeltables-xlsx"],
            ["version", 1], ["units", "SI (kN, m, kPa, tonne, degC)"],
            ["model_sha256", MT.model_signature(d)]]
    data = write_workbook([("_index", index)] + sheets + [("_meta", meta)])
    return data, {"format": "xlsx", "written": written,
                  "sheets": len(sheets) + 2, "skipped": [
                      "fields that are not table columns (layered shells, "
                      "link params, TH records, function libraries) stay "
                      "untouched on import"]}


def import_xlsx(model, data: bytes):
    from skyframe.core import modeltables as MT
    d = model if isinstance(model, dict) else model.to_dict()
    d = json.loads(json.dumps(d))
    try:
        book = read_workbook(data)
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        return None, [MT._err(None, None, None,
                              f"not a valid .xlsx workbook: {exc}")], {}
    same = False
    for row in book.get("_meta", []):
        if len(row) >= 2 and row[0] == "model_sha256":
            same = row[1] == MT.model_signature(d)
    tables = {}
    for t in MT.catalogue():
        rows = book.get(t.key)
        if not rows or not t.editable:
            continue
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        for r in rows:
            w.writerow(r)
        parsed = MT.csv_to_rows(d, t.key, buf.getvalue())
        if not same:
            for r in parsed:
                r.pop("_id", None)
        tables[t.key] = parsed
    if not tables:
        return None, [MT._err(None, None, None, "the workbook contains no "
                              "editable model table sheet")], {}
    return MT.apply_tables(d, tables)
