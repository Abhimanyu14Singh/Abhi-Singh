"""Build ``skyframe/data/steel/aisc.json`` from the AISC Shapes Database v16.0.

Not shipped (``tools/`` is outside the package).  Reproduce with::

    python3 tools/build_aisc_db.py \
        --csv <structural-shapes>/data/aisc-v16.csv \
        --supplement steelsnakes-0.0.1a11-py3-none-any.whl \
        --out skyframe/data/steel/aisc.json

Inputs (see docs/SECTION_SOURCES.md):

* ``--csv``  primary: ``data/aisc-v16.csv`` of github.com/cmccomb/
  structural-shapes (commit afacff69), an extract of all 2,299 rows of
  "aisc-shapes-database-v160-2.xlsx" (sheet "Database v16.0", US columns,
  workbook SHA-256 82d0ceb9...).  33 columns.
* ``--supplement``  the US data JSON of the PyPI wheel steelsnakes
  0.0.1a11 (an independent extract of the same v16.0 workbook) - used ONLY
  for the columns the CSV omits: kdes (fillet), x / y (centroid), eo.
  Every column the two extracts share is compared first (36,301 values,
  must agree exactly) so a supplement value is accepted only from a source
  proven identical on everything we can check.

Conversion (exact factors): 1 in = 0.0254 m; lb/ft -> kg/m x 0.45359237 /
0.3048.  Published US values are kept verbatim in each record's ``aisc``
block so every SI number is auditable.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skyframe.core.shape_props import shear_areas, shape_properties  # noqa

IN = 0.0254
LBFT = 0.45359237 / 0.3048
POW = {"A": 2, "Ix": 4, "Iy": 4, "J": 4, "Cw": 6, "Sx": 3, "Sy": 3,
       "Zx": 3, "Zy": 3, "rx": 1, "ry": 1, "Iw": 4, "Iz": 4, "C": 3,
       "eo": 1, "rts": 1, "ho": 1}
SUPP_KEYS = ("kdes", "x", "y", "eo")
FAMILY_TYPE = {"W": "I", "M": "I", "S": "I", "HP": "I", "C": "channel",
               "MC": "channel", "L": "angle", "2L": "double_angle",
               "WT": "tee", "MT": "tee", "ST": "tee", "PIPE": "round_tube"}
FAMILY_ORDER = ["W", "M", "S", "HP", "C", "MC", "L", "2L", "WT", "MT", "ST",
                "HSS Rect", "HSS Square", "HSS Round", "Pipe"]


def si(key: str, v: float) -> float:
    return v * IN ** POW[key]


def frac(txt: str) -> float:
    """'1-1/2' -> 1.5, '3/4' -> 0.75, '1' -> 1."""
    whole, _, f = txt.partition("-")
    if "/" in whole:
        n, d = whole.split("/")
        return int(n) / int(d)
    val = float(whole)
    if f:
        n, d = f.split("/")
        val += int(n) / int(d)
    return val


def load_supplement(whl: str) -> dict:
    z = zipfile.ZipFile(whl)
    out = {}
    for n in z.namelist():
        if n.startswith("steelsnakes/US/data/") and n.endswith(".json"):
            out.update(json.loads(z.read(n)))
    return out


def crosscheck(rows, supp) -> int:
    alias = {"tan(α)": "tan_alpha"}
    n = 0
    for r in rows:
        s = supp[r["AISC_Manual_Label"]]
        for k, v in r.items():
            if k in ("Type", "EDI_Std_Nomenclature", "AISC_Manual_Label") \
                    or v == "" or alias.get(k, k) not in s:
                continue
            if abs(float(v) - float(s[alias.get(k, k)])) > 1e-9 * max(
                    1.0, abs(float(v))):
                raise SystemExit(f"supplement disagrees: {r['AISC_Manual_Label']}"
                                 f" {k} {v} vs {s[alias.get(k, k)]}")
            n += 1
    return n


def g(r, k):
    v = r.get(k, "")
    return float(v) if v not in ("", None) else None


def record(r: dict, s: dict, single_J: dict) -> dict:
    fam0, name = r["Type"], r["AISC_Manual_Label"]
    computed = []
    if fam0 == "HSS":
        if r["OD"]:
            fam, typ = "HSS Round", "round_tube"
        else:
            fam = "HSS Square" if r["Ht"] == r["B"] else "HSS Rect"
            typ = "rect_tube"
    else:
        fam, typ = ("Pipe" if fam0 == "PIPE" else fam0), FAMILY_TYPE[fam0]
    v = lambda k: g(r, k)                                     # noqa: E731
    kdes = s.get("kdes")
    dims: dict = {}
    if typ in ("I", "channel", "tee"):
        dims = {"d": v("d") * IN, "bf": v("bf") * IN, "tf": v("tf") * IN,
                "tw": v("tw") * IN}
        if kdes is not None:
            dims["r"] = (kdes - v("tf")) * IN
            computed.append("dims.r")
    elif typ == "angle":
        # AISC: b = long leg (vertical, about which Ix is the larger),
        # d = short leg.  SkyFrame: d along local 2 (vertical), b along 3.
        dims = {"d": v("b") * IN, "b": v("d") * IN, "t": v("t") * IN}
        if kdes is not None:
            dims["r"] = (kdes - v("t")) * IN
            computed.append("dims.r")
    elif typ == "double_angle":
        m = re.match(r"^2L([\d./-]+)X([\d./-]+)X([\d./-]+?)(?:X([\d./-]+))?"
                     r"(LLBB|SLBB)?$", name)
        if not m:
            raise SystemExit(f"cannot parse {name}")
        gap = frac(m.group(4)) if m.group(4) else 0.0
        dims = {"d": v("d") * IN, "b": v("b") * IN, "t": v("t") * IN,
                "s": gap * IN, "orientation": m.group(5) or "EQUAL"}
        computed.append("dims.s")
    elif typ == "rect_tube":
        dims = {"d": v("Ht") * IN, "b": v("B") * IN, "t": v("tdes") * IN,
                "t_nom": v("tnom") * IN, "r_out": 2.0 * v("tdes") * IN}
        computed.append("dims.r_out")
    elif typ == "round_tube":
        dims = {"D": v("OD") * IN, "t": v("tdes") * IN,
                "t_nom": v("tnom") * IN}
    props = {"A": si("A", v("A")), "I33": si("Ix", v("Ix")),
             "I22": si("Iy", v("Iy"))}
    if v("J") is not None:
        props["J"] = si("J", v("J"))
    else:                       # 2L: two separate single angles, J adds up
        legs = re.match(r"^2L([\d./-]+X[\d./-]+X[\d./-]+?)(?:X[\d./-]+)?"
                        r"(?:LLBB|SLBB)?$", name).group(1)
        a, b2, t = legs.split("X")
        key = f"L{a}X{b2}X{t}" if float(frac(a)) >= float(frac(b2)) \
            else f"L{b2}X{a}X{t}"
        props["J"] = 2.0 * single_J[key] * IN ** 4
        computed.append("J")
    if v("Cw") is not None:
        props["Cw"] = si("Cw", v("Cw"))
    props.update({"S33": si("Sx", v("Sx")), "S22": si("Sy", v("Sy")),
                  "Z33": si("Zx", v("Zx")), "Z22": si("Zy", v("Zy"))})
    gdims = {k: x for k, x in dims.items() if not isinstance(x, str)}
    props["As2"], props["As3"] = shear_areas(typ, gdims, props["A"])
    computed += ["As2", "As3"]
    props["r33"], props["r22"] = si("rx", v("rx")), si("ry", v("ry"))
    # centroid (standard placement of skyframe.core.shape_props) and the
    # shear-centre offsets from the tabulated x / y / eo
    x0 = y0 = 0.0
    if typ == "channel" and s.get("x") is not None:
        props["cx"] = s["x"] * IN
        x0 = -(s["x"] + s["eo"]) * IN
        computed += ["x0"]
    elif typ == "angle" and s.get("x") is not None:
        props["cx"], props["cy"] = s["x"] * IN, s["y"] * IN
        x0 = (v("t") / 2 - s["x"]) * IN
        y0 = (v("t") / 2 - s["y"]) * IN
        computed += ["x0", "y0"]
    elif typ in ("tee", "double_angle") and s.get("y") is not None:
        tfl = v("tf") if typ == "tee" else v("t")
        props["cy"] = (v("d") - s["y"]) * IN
        y0 = (s["y"] - tfl / 2) * IN
        computed += ["cy", "y0"]
    props["x0"], props["y0"] = x0, y0
    props["mass"] = v("W") * LBFT
    if typ == "angle":
        props["I_major"], props["I_minor"] = si("Iw", v("Iw")), \
            si("Iz", v("Iz"))
        props["alpha"] = math.atan(v("tan(α)"))
    for k in ("C", "eo", "rts", "ho"):
        if v(k) is not None:
            props[k] = si(k, v(k))
    aisc = {k: float(x) for k, x in r.items()
            if x not in ("", None) and k not in ("Type", "EDI_Std_Nomenclature",
                                                 "AISC_Manual_Label")}
    for k in SUPP_KEYS:
        if s.get(k) is not None and k not in aisc:
            aisc[k] = float(s[k])
    rec = {"name": name, "family": fam, "type": typ, "dims": dims,
           "props": props}
    if r["EDI_Std_Nomenclature"] != name:
        rec["edi"] = r["EDI_Std_Nomenclature"]
    rec["computed"] = computed
    rec["aisc"] = aisc
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--supplement", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.csv, encoding="utf-8")))
    supp = load_supplement(a.supplement)
    n = crosscheck(rows, supp)
    single_J = {r["AISC_Manual_Label"]: float(r["J"]) for r in rows
                if r["Type"] == "L"}
    recs = [record(r, supp[r["AISC_Manual_Label"]], single_J) for r in rows]
    recs.sort(key=lambda x: FAMILY_ORDER.index(x["family"]))   # stable
    head = {
        "standard": "AISC",
        "edition": "AISC Shapes Database v16.0",
        "source": ("AISC Shapes Database v16.0 (August 2023, consistent with "
                   "the Steel Construction Manual 16th ed.), US-customary "
                   "columns, via github.com/cmccomb/structural-shapes "
                   "data/aisc-v16.csv @afacff69 (MIT; workbook "
                   "aisc-shapes-database-v160-2.xlsx SHA-256 82d0ceb96a0d938a"
                   "e1a6bd9637cb10a1e269225b5d668dce5b0bdc8d86013496); "
                   "kdes / x / y / eo from PyPI steelsnakes 0.0.1a11 US data "
                   f"(same workbook; {n} shared values cross-checked, 0 "
                   "differences). Tabulated values converted to SI with "
                   "1 in = 0.0254 m; 'computed' lists derived keys "
                   "(docs/SECTION_SOURCES.md)."),
        "units": "SI (m, m^2, m^4, m^6, kg/m)",
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("{" + ", ".join(f"{json.dumps(k)}: {json.dumps(v)}"
                                 for k, v in head.items()))
        fh.write(', "shapes": [\n')
        fh.write(",\n".join(json.dumps(r, ensure_ascii=False,
                                       separators=(",", ":"))
                            for r in recs))
        fh.write("\n]}\n")
    print(f"{len(recs)} shapes -> {out} ({out.stat().st_size} bytes); "
          f"{n} cross-checked values")


if __name__ == "__main__":
    main()
