"""Wave C regional libraries: GB / CSA / JIS / KS / GOST material grades and
the CISC W, JIS G 3192 H, KS D 3502 H and GOST 26020 I-beam section
tables (docs/SECTION_SOURCES.md "Wave C regional tables").

Spot checks compare >= 10 designations per steel table with the source
values quoted below in the units the source prints them (the raw files at
the commits listed in SECTION_SOURCES.md).  The exact-outline functions of
tools/build_wavec_steel_db.py are checked against the EN 10365 values of
eurocode.json and used to pin every computed property.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

from skyframe.core import material_db as mdb
from skyframe.core import steel_db as sdb
from skyframe.core.model import Material

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_wavec_steel_db as bw  # noqa: E402

A = pytest.approx
MPA = 1e6
NEW_MAT = ("gb", "csa", "jis", "ks", "gost")
NEW_STEEL = ("cisc", "jis", "ks", "gost")


# ============================================================ materials
def rec(std, name):
    return mdb.material(std, name)


def test_material_libraries_listed_and_flagged():
    st = {s["id"]: s for s in mdb.standards()}
    for sid in NEW_MAT:
        assert {"steel", "concrete", "rebar"} <= set(st[sid]["kinds"])
        d = mdb._load(sid)
        assert d["from_memory"] is True
        for r in d["materials"]:
            assert "REVIEW_FLAGS" in r["source"]


def test_gb_values():
    """GB 50017 E = 206 GPa; GB/T 1591-2018 Q355 fy 355 / fu 470; GB 50010
    C30: fck 20.1 MPa, Ec 3.00e4 (Table 4.1.5), formula 1e5/(2.2 +
    34.7/30) = 29 791 MPa kept as E_formula; HRB400 400/540, Es 2.0e5."""
    q = rec("gb", "Q355")
    assert (q["E"], q["fy"], q["fu"]) == (206e9, 355e6, 470e6)
    c = rec("gb", "C30")
    assert c["fc"] == A(20.1e6) and c["E"] == A(3.0e10)
    assert c["E_formula"] == A(1e5 / (2.2 + 34.7 / 30) * MPA)
    assert c["E_formula"] / MPA == A(29791.46, abs=0.01)
    assert c["fcu"] == 30e6
    h = rec("gb", "HRB400")
    assert (h["fy"], h["fu"], h["E"]) == (400e6, 540e6, 200e9)


def test_csa_jis_ks_gost_formulas():
    """CSA A23.3 Ec = 4500 sqrt(f'c) (30 MPa: 24 647.5); AIJ E = 3.35e4
    (23/24)^2 (24/60)^(1/3) = 22 669 MPa (Fc24); KDS Ec = 8500 cbrt(fck +
    4) (fck 24: 25 811 MPa) with df = 5 at fck 50; SP 63 B25 Rb,n 18.5,
    Eb 30.0e3."""
    assert rec("csa", "30MPa-CSA")["E"] == A(4500 * math.sqrt(30) * MPA)
    assert rec("csa", "30MPa-CSA")["E"] / MPA == A(24647.52, abs=0.01)
    assert rec("csa", "350W")["fy"] == 350e6
    e = rec("jis", "Fc24")["E"] / MPA
    assert e == A(3.35e4 * (23 / 24) ** 2 * 0.4 ** (1 / 3))
    assert e == A(22668.95, abs=0.01)
    assert rec("jis", "Fc48")["unit_weight"] == 24500.0
    assert rec("jis", "SN490B")["fy"] == 325e6
    k = rec("ks", "fck24-KDS")
    assert k["E"] / MPA == A(8500 * 28 ** (1 / 3)) == A(25811.0, abs=1)
    assert rec("ks", "fck50-KDS")["fcm"] == 55e6
    assert rec("ks", "SM355")["E"] == 210e9
    b = rec("gost", "B25")
    assert b["fc"] == 18.5e6 and b["E"] == 30.0e9
    assert rec("gost", "C345")["E"] == 206e9


@pytest.mark.parametrize("std,name", [("gb", "Q235"), ("csa", "400W"),
                                      ("jis", "Fc30"), ("ks", "SD500"),
                                      ("gost", "A500")])
def test_import_regional_material(std, name):
    r = rec(std, name)
    m = mdb.material_from_db(std, name)
    assert isinstance(m, Material) and m.E == r["E"] / 1000
    assert m.material_type == mdb.KIND_TO_TYPE[r["kind"]]


# ============================================================ steel tables
def shape(std, name):
    return sdb.shape(std, name)


def test_exact_outline_matches_en10365():
    """The exact rolled-I outline reproduces the EN 10365 / ArcelorMittal
    A, I and Wpl of eurocode.json within 0.05 %; El Darwish J within 3.1 %
    of the published It."""
    for nm in ("IPE300", "IPE600", "HEA200", "HEB300", "HEM400"):
        r = shape("eurocode", nm)
        g, p = r["dims"], r["props"]
        e = bw.exact_I(g["d"], g["bf"], g["tw"], g["tf"], g["r"])
        for k in ("A", "I33", "I22", "Z33", "Z22"):
            assert e[k] == A(p[k], rel=5e-4), (nm, k)
        assert bw.darwish_J(g["d"], g["bf"], g["tw"], g["tf"],
                            g["r"]) == A(p["J"], rel=0.031)


def test_exact_outline_hand_value():
    """H-200x200x8x12, r 13 mm: A = 2 x 200 x 12 + 176 x 8 + (4 - pi) 13^2
    = 4800 + 1408 + 145.07 = 6353.07 mm^2 (JFE prints 63.53 cm^2)."""
    e = bw.exact_I(200.0, 200.0, 8.0, 12.0, 13.0)
    assert e["A"] == A(6208.0 + (4 - math.pi) * 169.0) == A(6353.07, abs=0.01)
    assert e["A"] / 100 == A(63.53, abs=0.005)


SPOT = {
    # CISC SST 9.2 (rpakishore/steelsections-cisc, mm-based)
    "cisc": [
        ("W1100x499", {"A": 63400, "I33": 1.29e10, "I22": 5.00e8,
                       "Z33": 2.66e7, "J": 3.10e7, "Cw": 1.44e14,
                       "mass": 499}),
        ("W760x147", {"A": 18700, "I33": 1.66e9, "I22": 5.29e7,
                      "Z33": 5.10e6, "J": 1.56e6, "mass": 147}),
        ("W610x125", {"A": 15900, "I33": 9.85e8, "I22": 3.93e7,
                      "Z33": 3.67e6, "J": 1.54e6}),
        ("W530x92", {"A": 11800, "I33": 5.52e8, "I22": 2.38e7,
                     "Z33": 2.36e6, "Cw": 1.59e12}),
        ("W460x74", {"A": 9450, "I33": 3.33e8, "I22": 1.66e7,
                     "Z33": 1.65e6, "J": 5.17e5}),
        ("W410x60", {"A": 7580, "I33": 2.16e8, "I22": 1.20e7,
                     "Z33": 1.19e6, "J": 3.28e5}),
        ("W360x134", {"A": 17100, "I33": 4.15e8, "I22": 1.51e8,
                      "Z33": 2.56e6, "J": 1.68e6, "Cw": 4.31e12}),
        ("W310x39", {"A": 4940, "I33": 8.51e7, "I22": 7.27e6,
                     "Z33": 6.10e5, "J": 1.26e5, "Cw": 1.64e11}),
        ("W250x67", {"A": 8550, "I33": 1.04e8, "I22": 2.22e7,
                     "Z33": 9.01e5, "J": 6.25e5}),
        ("W200x46", {"A": 5860, "I33": 4.54e7, "I22": 1.53e7,
                     "Z33": 4.95e5, "J": 2.20e5}),
        ("W150x22", {"A": 2860, "I33": 1.21e7, "I22": 3.87e6,
                     "Z33": 1.77e5, "J": 4.34e4, "mass": 22}),
    ],
    # JFE handbook, JIS G 3192 (cm based)
    "jis": [
        ("H-100x100x6x8", {"A": 21.59, "I33": 378, "I22": 134, "S33": 75.6,
                           "Z33": 86.4, "Z22": 41, "mass": 16.9}),
        ("H-150x75x5x7", {"A": 17.85, "I33": 666, "I22": 49.5,
                          "Z33": 102, "mass": 14}),
        ("H-194x150x6x9", {"A": 38.11, "I33": 2630, "I22": 507,
                           "S33": 271, "Z22": 103}),
        ("H-200x200x8x12", {"A": 63.53, "I33": 4720, "I22": 1600,
                            "Z33": 525, "mass": 49.9}),
        ("H-300x150x6.5x9", {"A": 46.78, "I33": 7210, "I22": 508,
                             "Z33": 542}),
        ("H-300x300x10x15", {"A": 118.5, "I33": 20200, "I22": 6750,
                             "Z33": 1480, "Z22": 683}),
        ("H-350x175x7x11", {"A": 62.91, "I33": 13500, "I22": 984,
                            "S33": 771}),
        ("H-400x200x8x13", {"A": 83.37, "I33": 23500, "I22": 1740,
                            "Z33": 1310}),
        ("H-400x400x13x21", {"A": 218.7, "I33": 66600, "I22": 22400,
                             "Z33": 3670, "mass": 172}),
        ("H-488x300x11x18", {"A": 159.2, "I33": 68900, "I22": 8110}),
        ("H-600x200x11x17", {"A": 131.7, "I33": 75600, "I22": 2270,
                             "Z33": 2900}),
        ("H-700x300x13x24", {"A": 231.5, "I33": 197000, "I22": 10800}),
        ("H-900x300x16x28", {"A": 305.8, "I33": 404000, "I22": 12600,
                             "S33": 8990, "Z33": 10300, "mass": 240}),
    ],
    # KS D 3502 catalogue (mm based)
    "ks": [
        ("H-100x100x6x8", {"A": 2190, "I33": 3.83e6, "I22": 1.34e6,
                           "Z33": 87600, "S33": 76500, "mass": 17.2}),
        ("H-150x150x7x10", {"A": 4014, "I33": 1.64e7, "I22": 5.63e6,
                            "Z33": 246000}),
        ("H-200x100x5.5x8", {"A": 2716, "I33": 1.84e7, "I22": 1.34e6,
                             "Z33": 209000}),
        ("H-250x250x9x14", {"A": 9218, "I33": 1.08e8, "I22": 3.65e7,
                            "S33": 867000}),
        ("H-300x300x10x15", {"A": 11980, "I33": 2.04e8, "I22": 6.75e7,
                             "Z33": 1.50e6}),
        ("H-350x175x7x11", {"A": 6314, "I33": 1.36e8, "I22": 9.84e6}),
        ("H-400x400x13x21", {"A": 21870, "I33": 6.66e8, "I22": 2.24e8,
                             "Z33": 3.67e6, "mass": 172}),
        ("H-446x199x8x12", {"A": 8430, "I33": 2.87e8, "I22": 1.58e7}),
        ("H-500x200x10x16", {"A": 11420, "I33": 4.78e8, "I22": 2.14e7,
                             "Z33": 2.18e6}),
        ("H-588x300x12x20", {"A": 19250, "I33": 1.18e9, "I22": 9.02e7,
                             "S33": 4.02e6, "mass": 151}),
        ("H-700x300x13x24", {"A": 23550, "I33": 2.01e9, "I22": 1.08e8}),
        ("H-900x300x16x28", {"A": 30580, "I33": 4.04e9, "I22": 1.26e8,
                             "Z33": 1.05e7}),
    ],
    # GOST 26020-83 (cm based; Z33 = 2 x the printed Sy)
    "gost": [
        ("10Б1", {"A": 10.32, "I33": 171, "S33": 34.2, "Z33": 2 * 19.7,
                  "I22": 15.9, "mass": 8.1}),
        ("20Б1", {"A": 28.49, "I33": 1943, "S33": 194.3, "I22": 142.3}),
        ("30Б1", {"A": 41.92, "I33": 6328, "S33": 427, "I22": 390}),
        ("40Б2", {"A": 69.72, "I33": 18530, "Z33": 2 * 529.7,
                  "I22": 865}),
        ("50Б1", {"A": 92.98, "I33": 37160, "S33": 1511, "I22": 1606}),
        ("60Б2", {"A": 147.3, "I33": 87640.008, "I22": 3561}),
        ("70Б1", {"A": 164.7, "I33": 125930.016, "I22": 4556}),
        ("80Б2", {"A": 226.6, "I33": 232200, "I22": 7527}),
        ("90Б1", {"A": 247.1, "I33": 304400, "S33": 6817, "I22": 8365}),
        ("100Б4", {"A": 400.6, "I33": 655400, "I22": 17830,
                   "mass": 314.5}),
        ("20Ш1", {"A": 38.95, "mass": 30.6}),
        ("30Ш1", {"A": 68.31, "mass": 53.6}),
        ("40Ш2", {"A": 141.60, "mass": 111.1}),
        ("70Ш5", {"A": 389.7, "mass": 305.9}),
        ("20К1", {"A": 52.82, "mass": 41.5}),
        ("40К5", {"A": 371, "mass": 291.2}),
        ("24Дб1", {"A": 35.45, "mass": 27.8}),
        ("50Дш1", {"A": 198.0, "mass": 155.0}),
    ],
}
SCALE = {"cisc": {"A": 1e-6, "I33": 1e-12, "I22": 1e-12, "S33": 1e-9,
                  "Z33": 1e-9, "Z22": 1e-9, "J": 1e-12, "Cw": 1e-18,
                  "mass": 1.0},
         "ks": {"A": 1e-6, "I33": 1e-12, "I22": 1e-12, "S33": 1e-9,
                "Z33": 1e-9, "mass": 1.0},
         "jis": {"A": 1e-4, "I33": 1e-8, "I22": 1e-8, "S33": 1e-6,
                 "Z33": 1e-6, "Z22": 1e-6, "mass": 1.0},
         "gost": {"A": 1e-4, "I33": 1e-8, "I22": 1e-8, "S33": 1e-6,
                  "Z33": 1e-6, "mass": 1.0}}


@pytest.mark.parametrize("std", NEW_STEEL)
def test_spot_checks_against_source(std):
    assert len(SPOT[std]) >= 10
    for name, vals in SPOT[std]:
        r = shape(std, name)
        for k, v in vals.items():
            assert r["props"][k] == A(v * SCALE[std][k], rel=1e-5), \
                (std, name, k)


def test_listing_counts_and_schema():
    st = {s["id"]: s for s in sdb.standards()}
    assert st["cisc"]["count"] == 281 and st["jis"]["count"] == 85
    assert st["ks"]["count"] == 79 and st["gost"]["count"] == 91
    assert sdb.families("gost") == ["GOST Б (normal)", "GOST Ш (wide flange)",
                                    "GOST К (column)", "GOST Д (additional)"]
    assert sdb.families("jis") == ["H wide (HW)", "H medium (HM)",
                                   "H narrow (HN)"]
    need = ("A", "I33", "I22", "J", "Cw", "S33", "S22", "Z33", "Z22", "As2",
            "As3", "r33", "r22", "x0", "y0", "mass")
    for sid in NEW_STEEL:
        d = json.loads((sdb.DATA_DIR / f"{sid}.json").read_text("utf-8"))
        assert d["units"] == "SI (m, m^2, m^4, m^6, kg/m)"
        names = [s["name"] for s in d["shapes"]]
        assert len(names) == len(set(names))
        for s in d["shapes"]:
            assert s["type"] == "I" and all(k in s["props"] for k in need)
            assert s["src"] in d["sources"]
            p, g = s["props"], s["dims"]
            assert p["I33"] > p["I22"] > 0 and p["S33"] <= p["Z33"]
            assert p["r33"] == A(math.sqrt(p["I33"] / p["A"]), rel=0.02)
            # every computed property is the exact value of the outline
            e = bw.exact_I(g["d"], g["bf"], g["tw"], g["tf"], g["r"])
            for k in ("A", "I33", "I22", "S33", "S22", "Z22"):
                if k in s["computed"]:
                    assert p[k] == A(e[k], rel=1e-5), (sid, s["name"], k)
            if "J" in s["computed"]:
                assert p["J"] == A(bw.darwish_J(g["d"], g["bf"], g["tw"],
                                                g["tf"], g["r"]), rel=1e-5)
            if "Cw" in s["computed"]:
                assert p["Cw"] == A(p["I22"] * (g["d"] - g["tf"]) ** 2 / 4,
                                    rel=1e-5)
            # tabulated A within 2 % of the exact outline (3 % without r)
            tol = 0.02 if g["r"] > 0 else 0.03
            assert p["A"] == A(e["A"], rel=tol), (sid, s["name"])


def test_gost_computed_series_and_aliases():
    """30Ш1 (h 291, b 200, s 8, t 11, R 18): every property is computed
    from the outline; the printed A = 68.31 cm^2 is reproduced (0.1 %)."""
    r = shape("gost", "30Ш1")
    e = bw.exact_I(0.291, 0.200, 0.008, 0.011, 0.018)
    assert r["props"]["A"] == A(68.31e-4)
    assert e["A"] == A(68.31e-4, rel=1e-3)
    assert r["props"]["I33"] == A(e["I33"], rel=1e-5)
    assert {"I33", "I22", "S33", "Z33", "J", "Cw"} <= set(r["computed"])
    assert sdb.shape("gost", "30Sh1")["name"] == "30Ш1"
    # Б-series depth resolved by the table's own Wy = Iy/(h/2)
    assert shape("gost", "90Б1")["dims"]["d"] == A(0.893)
    assert sdb.shape("cisc", "W12x26")["name"] == "W310x39"


def test_section_import_from_new_tables():
    for std, name in (("cisc", "W310x39"), ("jis", "H-400x400x13x21"),
                      ("ks", "H-588x300x12x20"), ("gost", "30Ш1")):
        sec = sdb.section_from_db(std, name, "S")
        assert sec.db_ref == {"standard": std, "name": name}
        assert sec.A == A(shape(std, name)["props"]["A"])
