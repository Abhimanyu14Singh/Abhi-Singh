"""Analysis-results tables -> a plain CSV FOLDER (Wave C; CONTRACT
"Anisotropic shells, material angle, Section Designer moment-curvature,
layered shells with any material, results CSV folder").

The open-format companion of :mod:`skyframe.io.results_export` (Excel /
SQLite): every Show-Tables table becomes ONE RFC 4180 CSV file (UTF-8, CRLF
line ends, header ``Label [unit]``, values in the chosen display unit
set), so any spreadsheet, database, script or BI tool can read every table
without SkyFrame, Excel, Access or an ODBC driver.

Folder layout (``<model>_results/``)::

    _index.csv        TableKey, TableName, TableGroup, NumRows, File
    _units.csv        File, Field, Quantity, Unit
    _meta.csv         Item, Value  (program, format, version, units, model)
    README.txt        what the folder is and how to read it
    <Table Name>.csv  one per table (file name = ETABS table name with
                      characters unsafe in file names replaced by "_")

* :func:`export_results_csv_folder` writes the folder to disk;
* :func:`export_results_csv_zip` returns the same folder as a ZIP
  (``POST /api/tables/export/csv``; browsers download one file).
"""

from __future__ import annotations

import csv
import io
import os
import re
import zipfile
from typing import Dict, Iterable, List, Optional, Tuple

from skyframe.io.results_export import (_check_units, _converted,
                                        compute_tables)

__all__ = ["export_results_csv_folder", "export_results_csv_zip",
           "csv_files"]

FORMAT = "skyframe-results-csv"
VERSION = 1

README = (
    "SkyFrame analysis results - CSV folder\r\n"
    "======================================\r\n\r\n"
    "One CSV file per results table (RFC 4180, UTF-8, comma separated,\r\n"
    "first row = column headers 'Label [unit]').  _index.csv lists the\r\n"
    "tables (key, ETABS table name, group, rows, file); _units.csv gives\r\n"
    "the unit of every column; _meta.csv the program / unit set / model.\r\n"
    "Numbers are written with full precision in the unit set named in\r\n"
    "_meta.csv.  Empty cells are null values.\r\n")


def _fname(title: str, used: Dict[str, int]) -> str:
    base = re.sub(r'[\\/:*?"<>|\[\]]+', "_", title).strip() or "table"
    base = base[:120]
    name = f"{base}.csv"
    if name.lower() in used:
        used[name.lower()] += 1
        name = f"{base} ({used[name.lower()]}).csv"
    used[name.lower()] = used.get(name.lower(), 1)
    return name


def _csv_text(rows: List[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    for r in rows:
        w.writerow(["" if v is None else
                    ("TRUE" if v is True else "FALSE" if v is False else
                     repr(v) if isinstance(v, float) else v) for v in r])
    return buf.getvalue()


def csv_files(model, results: dict, context: Optional[dict] = None,
              keys: Optional[Iterable[str]] = None,
              cases: Optional[Iterable[str]] = None, units: str = "kN-m",
              skip_empty: bool = False) -> Tuple[Dict[str, str], dict]:
    """``({file name: CSV text}, report)`` of the selected tables."""
    units = _check_units(units)
    tabs = compute_tables(model, results, context, keys, cases, skip_empty)
    files: Dict[str, str] = {}
    used: Dict[str, int] = {}
    index = [["TableKey", "TableName", "TableGroup", "NumRows", "File"]]
    unit_rows = [["File", "Field", "Quantity", "Unit"]]
    for tab in tabs:
        head, conv, body = _converted(tab, units)
        fn = _fname(tab["title"], used)
        files[fn] = _csv_text([head] + body)
        index.append([tab["key"], tab["title"], tab["group"], len(body), fn])
        unit_rows += [[fn, c["label"], c["quantity"], u]
                      for c, (_f, u) in zip(tab["columns"], conv)]
    files["_index.csv"] = _csv_text(index)
    files["_units.csv"] = _csv_text(unit_rows)
    files["_meta.csv"] = _csv_text([
        ["Item", "Value"], ["ProgramName", "SkyFrame"], ["Format", FORMAT],
        ["Version", VERSION], ["Units", units],
        ["Model", getattr(model, "name", "") or ""]])
    files["README.txt"] = README
    report = {"format": "csv", "units": units,
              "tables": {t["key"]: len(t["rows"]) for t in tabs},
              "files": len(files),
              "warnings": {t["key"]: t["warnings"] for t in tabs
                           if t.get("warnings")}}
    return files, report


def folder_name(model) -> str:
    name = re.sub(r"[^\w.-]+", "_", getattr(model, "name", "") or "model")
    return f"{name}_results"


def export_results_csv_folder(model, results: dict, folder: str,
                              context: Optional[dict] = None,
                              keys: Optional[Iterable[str]] = None,
                              cases: Optional[Iterable[str]] = None,
                              units: str = "kN-m", skip_empty: bool = False
                              ) -> dict:
    """Write the CSV folder to ``folder`` (created; existing files of the
    same names are replaced).  Returns the report (+ ``folder``)."""
    files, rep = csv_files(model, results, context, keys, cases, units,
                           skip_empty)
    os.makedirs(folder, exist_ok=True)
    for fn, text in files.items():
        with open(os.path.join(folder, fn), "w", encoding="utf-8",
                  newline="") as fh:
            fh.write(text)
    rep["folder"] = folder
    return rep


def export_results_csv_zip(model, results: dict,
                           context: Optional[dict] = None,
                           keys: Optional[Iterable[str]] = None,
                           cases: Optional[Iterable[str]] = None,
                           units: str = "kN-m", skip_empty: bool = False
                           ) -> Tuple[bytes, dict]:
    """The CSV folder as ZIP bytes (entries under ``<model>_results/``;
    fixed timestamps, so the same results give the same bytes)."""
    files, rep = csv_files(model, results, context, keys, cases, units,
                           skip_empty)
    root = folder_name(model)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in sorted(files, key=lambda n: (not n.startswith(("_",
                                                                 "README")),
                                               n)):
            info = zipfile.ZipInfo(f"{root}/{fn}",
                                   date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, files[fn].encode("utf-8"))
    rep["folder"] = root
    return buf.getvalue(), rep
