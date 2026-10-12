"""Analysis-results tables -> Excel (.xlsx) and SQLite (.sqlite) files
(ETABS File > Export > Microsoft Excel / Microsoft Access).

No dependencies: the workbook goes through the minimal OOXML writer of
:mod:`skyframe.io.xlsx` (:func:`~skyframe.io.xlsx.write_workbook`) and the
database through the standard-library :mod:`sqlite3` — SQLite is the open,
cross-platform stand-in for ETABS' Microsoft Access export (an ``.mdb`` /
``.accdb`` writer needs the proprietary Jet/ACE engine; every Access, Excel,
Python, R or BI tool reads SQLite through its ODBC driver or natively).

Both exports reuse :func:`skyframe.core.tables.compute_table` (the Show
Tables catalogue, pure post-processing of stored results) and the display
unit sets of :mod:`skyframe.core.units` (``units`` = a set name, default
``"kN-m"``; the values are converted, the headers carry the unit).

* ``export_results_xlsx(model, results, context=None, keys=None,
  cases=None, units="kN-m")`` -> ``(bytes, report)`` — sheet ``_index``
  (key, table, group, rows, sheet), then ONE SHEET PER TABLE (sheet name =
  ETABS table name, <= 31 chars) with the header row ``Label [unit]`` and
  numeric cells as numbers;
* ``export_results_sqlite(...)`` -> ``(bytes, report)`` — one SQL table per
  results table (table name = ETABS table name, field names = column labels
  without spaces, ETABS-Access style: ``OutputCase``, ``CaseType`` ...;
  REAL / INTEGER / TEXT affinities), plus ``"Table Index"`` (TableKey,
  TableName, TableGroup, NumRows) and ``"Field Units"`` (TableName, Field,
  Quantity, Unit).
"""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from typing import Dict, Iterable, List, Optional, Tuple

from skyframe.core import tables as _tables
from skyframe.core import units as _units
from skyframe.io.xlsx import write_workbook

__all__ = ["compute_tables", "export_results_xlsx", "export_results_sqlite",
           "unit_for"]

# Show-Tables quantity -> core.units quantity (others are unit-free)
_QMAP = {"force": "force", "length": "length", "moment": "moment",
         "stiffness": "translational_stiffness", "mass": "mass"}
_FIXED_UNIT = {"angle": "rad", "time": "s", "frequency": "Hz",
               "percent": "%", "circular_frequency": "rad/s",
               "eigenvalue": "rad^2/s^2"}


# Wave C quantities (core/tables_wave_c.py): volume, rotational mass,
# translational velocity / acceleration (length unit per s / s^2)
_QMAP.update({"volume": "volume", "mass_moi": "rotational_mass"})
_FIXED_UNIT.update({"angular_velocity": "rad/s", "angular_accel": "rad/s^2"})
_PER_TIME = {"velocity": "/s", "accel": "/s^2"}


def unit_for(quantity: str, units: str) -> Tuple[float, str]:
    """(SI -> display factor, unit label) of a table column quantity."""
    if quantity in _PER_TIME:                       # Wave C
        f, lab = unit_for("length", units)
        return f, lab + _PER_TIME[quantity]
    q = _QMAP.get(quantity)
    if q is None:
        return 1.0, _FIXED_UNIT.get(quantity, "")
    f, l, _si = _units.QUANTITIES[q]
    lab = _units._label(units, f, l)
    if q == "mass":
        lab = "tonne" if units in ("kN-m",) else f"{lab}*s^2"
    if q == "rotational_mass":                      # Wave C
        lab = "tonne*m^2" if units in ("kN-m",) else f"{lab}*s^2"
    return _units.factor(q, units), lab


def compute_tables(model, results: dict, context: Optional[dict] = None,
                   keys: Optional[Iterable[str]] = None,
                   cases: Optional[Iterable[str]] = None,
                   skip_empty: bool = False) -> List[dict]:
    """The selected Show-Tables tables (catalogue order; ``keys=None`` =
    every table).  KeyError on an unknown key."""
    want = None if keys is None else list(keys)
    if want is not None:
        for k in want:
            if k not in _tables.TABLES:
                raise KeyError(k)
    out = []
    for t in _tables.list_tables():
        if want is not None and t["key"] not in want:
            continue
        tab = _tables.compute_table(t["key"], results, model, context,
                                    cases=cases)
        if skip_empty and not tab["rows"]:
            continue
        out.append(tab)
    return out


def _check_units(units: str) -> str:
    if units not in _units.UNIT_SETS:
        raise ValueError(f"unknown unit set {units!r} "
                         f"({', '.join(_units.UNIT_SETS)})")
    return units


def _converted(tab: dict, units: str):
    cols = tab["columns"]
    conv = [unit_for(c["quantity"], units) for c in cols]
    head = [f"{c['label']} [{u}]" if u else c["label"]
            for c, (_f, u) in zip(cols, conv)]
    body = []
    for r in tab["rows"]:
        line = []
        for c, (f, _u) in zip(cols, conv):
            v = r.get(c["key"])
            if isinstance(v, bool) or v is None:
                line.append(v)
            elif isinstance(v, (int, float)) and c["quantity"] not in (
                    "text",):
                line.append(v * f if f != 1.0 else v)
            else:
                line.append(str(v))
        body.append(line)
    return head, conv, body


def export_results_xlsx(model, results: dict, context: Optional[dict] = None,
                        keys: Optional[Iterable[str]] = None,
                        cases: Optional[Iterable[str]] = None,
                        units: str = "kN-m", skip_empty: bool = False
                        ) -> Tuple[bytes, dict]:
    units = _check_units(units)
    tabs = compute_tables(model, results, context, keys, cases, skip_empty)
    index = [["key", "table", "group", "rows", "sheet"]]
    sheets = []
    used: List[str] = []
    for tab in tabs:
        head, _conv, body = _converted(tab, units)
        name = re.sub(r"[\[\]:*?/\\]", "_", tab["title"])[:31]
        base, k = name, 1
        while name in used:              # same rule as write_workbook
            k += 1
            name = f"{base[:28]}~{k}"
        used.append(name)
        sheets.append((name, [head] + body))
        index.append([tab["key"], tab["title"], tab["group"],
                      len(tab["rows"]), name])
    meta = [["key", "value"], ["format", "skyframe-results-xlsx"],
            ["version", 1], ["units", units],
            ["model", getattr(model, "name", "") or ""]]
    data = write_workbook([("_index", index)] + sheets + [("_meta", meta)])
    return data, {"format": "xlsx", "units": units,
                  "tables": {t["key"]: len(t["rows"]) for t in tabs},
                  "sheets": len(sheets) + 2,
                  "warnings": {t["key"]: t["warnings"] for t in tabs
                               if t.get("warnings")}}


def _field(label: str) -> str:
    f = re.sub(r"[^0-9A-Za-z_]+", "", label.replace(">", "Gt")
               .replace("<=", "Le").replace("-", "_"))
    return f or "Field"


def _sql_name(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def export_results_sqlite(model, results: dict,
                          context: Optional[dict] = None,
                          keys: Optional[Iterable[str]] = None,
                          cases: Optional[Iterable[str]] = None,
                          units: str = "kN-m", skip_empty: bool = False
                          ) -> Tuple[bytes, dict]:
    units = _check_units(units)
    tabs = compute_tables(model, results, context, keys, cases, skip_empty)
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        con = sqlite3.connect(path)
        cur = con.cursor()
        cur.execute('CREATE TABLE "Table Index" (TableKey TEXT PRIMARY KEY, '
                    'TableName TEXT, TableGroup TEXT, NumRows INTEGER)')
        cur.execute('CREATE TABLE "Field Units" (TableName TEXT, Field TEXT, '
                    'Quantity TEXT, Unit TEXT)')
        cur.execute('CREATE TABLE "Program Control" (Item TEXT, Value TEXT)')
        cur.executemany('INSERT INTO "Program Control" VALUES (?, ?)', [
            ("ProgramName", "SkyFrame"), ("Format", "skyframe-results-sqlite"),
            ("Version", "1"), ("Units", units),
            ("Model", getattr(model, "name", "") or "")])
        names: Dict[str, int] = {}
        for tab in tabs:
            tname = tab["title"]
            if tname in names:
                names[tname] += 1
                tname = f"{tname} ({names[tname]})"
            else:
                names[tname] = 1
            _head, conv, body = _converted(tab, units)
            fields: List[str] = []
            for c in tab["columns"]:
                f = _field(c["label"])
                base, k = f, 1
                while f in fields:
                    k += 1
                    f = f"{base}_{k}"
                fields.append(f)
            aff = ["TEXT" if c["quantity"] == "text" else
                   "INTEGER" if c["quantity"] == "id" else "REAL"
                   for c in tab["columns"]]
            cur.execute(f"CREATE TABLE {_sql_name(tname)} (" + ", ".join(
                f"{_sql_name(f)} {a}" for f, a in zip(fields, aff)) + ")")
            if body:
                cur.executemany(
                    f"INSERT INTO {_sql_name(tname)} VALUES ("
                    + ", ".join("?" * len(fields)) + ")", body)
            cur.execute('INSERT INTO "Table Index" VALUES (?, ?, ?, ?)',
                        (tab["key"], tname, tab["group"], len(body)))
            cur.executemany('INSERT INTO "Field Units" VALUES (?, ?, ?, ?)', [
                (tname, f, c["quantity"], u) for f, c, (_x, u) in
                zip(fields, tab["columns"], conv)])
        con.commit()
        con.close()
        with open(path, "rb") as fh:
            data = fh.read()
    finally:
        try:
            os.remove(path)
        except OSError:                                # pragma: no cover
            pass
    return data, {"format": "sqlite", "units": units,
                  "tables": {t["key"]: len(t["rows"]) for t in tabs},
                  "warnings": {t["key"]: t["warnings"] for t in tabs
                               if t.get("warnings")}}
