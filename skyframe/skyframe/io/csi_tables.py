"""CSI interactive-database TEXT format (SAP2000 ``.s2k``, SAFE ``.f2k``,
CSiBridge ``.b2k``) -- the table family that sits next to ETABS ``.e2k``.

A file is a header line, then tables::

    TABLE:  "JOINT COORDINATES"
       Joint=1   CoordSys=GLOBAL   CoordType=Cartesian   XorR=0   Y=0   Z=3
       Joint=2   ...

    END TABLE DATA

Each record is ``Field=Value`` pairs; values containing blanks are
double-quoted; a record may continue on the next line after a trailing
`` _``.  :func:`write_tables` / :func:`read_tables` are exact inverses
for the values they write (numbers via ``exchange_common.fmt``, i.e.
``repr`` precision).
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from skyframe.io.exchange_common import fmt

Row = Dict[str, str]

_MAX = 110                         # wrap records longer than this


def cell(v) -> str:
    """One value as written in a record."""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (int, float)):
        return fmt(v)
    s = str(v)
    if s == "" or re.search(r'[\s"=]', s):
        return '"' + s.replace('"', "'") + '"'
    return s


def write_tables(tables: Sequence[Tuple[str, List[dict]]],
                 header: str = "") -> str:
    out: List[str] = []
    if header:
        out += [header, ""]
    for name, rows in tables:
        if not rows:
            continue
        out.append(f'TABLE:  "{name}"')
        for r in rows:
            parts = [f"{k}={cell(v)}" for k, v in r.items() if v is not None]
            line = "  "
            for p in parts:
                piece = "   " + p
                if len(line) + len(piece) > _MAX and line.strip():
                    out.append(line + " _")
                    line = "  "
                line += piece
            out.append(line)
        out.append(" ")
    out.append("END TABLE DATA")
    return "\n".join(out) + "\n"


_TOK = re.compile(r'([A-Za-z0-9_]+)=("(?:[^"]*)"|\S*)')


def parse_record(line: str) -> Row:
    row: Row = OrderedDict()
    for m in _TOK.finditer(line):
        v = m.group(2)
        if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            v = v[1:-1]
        row[m.group(1)] = v
    return row


def read_tables(text: str) -> "OrderedDict[str, List[Row]]":
    """{table name: [row]} (table names upper-cased; rows keep order)."""
    tables: "OrderedDict[str, List[Row]]" = OrderedDict()
    cur: Optional[List[Row]] = None
    pending = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if pending:
            line = pending + " " + line.strip()
            pending = ""
        if line.endswith(" _"):
            pending = line[:-2]
            continue
        s = line.strip()
        if not s or s.startswith(("$", ";", "'")):
            continue
        m = re.match(r'^TABLE:\s*"([^"]+)"', s, re.I)
        if m:
            cur = tables.setdefault(m.group(1).strip().upper(), [])
            continue
        if s.upper().startswith("END TABLE DATA"):
            break
        if cur is not None and "=" in s:
            cur.append(parse_record(s))
    return tables


def num(row: Row, key: str, default: float = 0.0) -> float:
    v = row.get(key)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def yes(row: Row, key: str) -> bool:
    return str(row.get(key, "")).strip().lower() in ("yes", "true", "1", "y")


def units_field(units) -> str:
    """``CurrUnits`` text, e.g. ``"KN, m, C"``."""
    L = {"M": "m", "MM": "mm", "CM": "cm", "FT": "ft", "IN": "in"}[
        units.length]
    F = {"KN": "KN", "N": "N", "KIP": "Kip", "LB": "lb", "KGF": "Kgf",
         "TONF": "Tonf"}[units.force]
    T = "F" if units.length in ("FT", "IN") else "C"
    return f"{F}, {L}, {T}"


def units_from_field(text: str):
    """(force, length) keys of exchange_common.Units from ``CurrUnits``."""
    parts = [p.strip().upper() for p in str(text).split(",")]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def program_control(tables) -> Row:
    rows = tables.get("PROGRAM CONTROL") or [{}]
    return rows[0]
