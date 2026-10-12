"""Build the Wave C steel section tables (``skyframe/data/steel/{cisc,jis,
ks,gost}.json``) from open transcriptions (see docs/SECTION_SOURCES.md,
"Wave C regional tables").

Usage (from the repository's ``skyframe`` folder; every source file is the
raw file at the pinned commit listed in SOURCES)::

    python3 tools/build_wavec_steel_db.py --src <folder with the files>

Rules (the SECTION_SOURCES.md data rules): a value is tabulated (copied
from the source, converted to SI with exact factors) or computed exactly
from published dimensions (listed in the record's ``computed``); nothing is
invented.  Gates: the exact fillet outline must reproduce every tabulated
area within 2 % and I within 3 %, else the row is reported and dropped.

Exact doubly-symmetric rolled I outline (parallel flanges, root radius r,
no toe radius): two flanges b x tf, web tw x (h - 2 tf) and four root
spandrels (r x r square minus a quarter disc), integrated in closed form.
J: El Darwish & Johnston (1965) as used by SCI P363 / EN tables; Cw =
I22 (h - tf)^2 / 4; As2 = h tw, As3 = 5/3 b tf (SkyFrame convention).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "skyframe" / "data" / "steel"

SOURCES = {
    "cisc_W.csv": ("rpakishore/steelsections-cisc",
                   "929bf04787ff41b8136490d65da1475bab061b17",
                   "src/cisc_sections/data/cisc_9.2/W.csv", "Apache-2.0"),
    "limitstates_cisc_12_w.csv": (
        "cslotboom/limitstates", "08f466e8a337441d76f14c114e18efc59c6aa79e",
        "src/limitstates/objects/section/db/csa/steel/cisc_12_w.csv",
        "GPL-3.0"),
    "gost_calcx.js": ("vkorotenko/calcxactive",
                      "9599e47a145b42f70d11976ea9b3c61a3e161564",
                      "src/typos/data26020.js", "MIT"),
    "gost_kuvbur_B.txt": (
        "kuvbur/gdl_macro", "026532c9f7992ed49f7ce1487b76660a9403899e",
        "hsf/metall/sortament/дв_Двутавр_Б_по_ГОСТ_26020-83.txt", "MIT"),
    "jfe_H.csv": ("z-ichinohe/jfe_binran_csv",
                  "7f4e0d1d480cfe21d1002ee7bf06cac1463c45e9",
                  "5. 断面性能表/5-1. H形鋼.csv", "none stated"),
    "mq_ks.txt": ("hnanmal/mQ", "9e7a8bc5a177268decd32834baf0dce7d17278a2",
                  "etc/testPjt/Steel Family_2024_0614/Column/KSD/"
                  "Steel Column_H_KSD3502.txt", "none stated"),
    "openaec_ks.json": ("OpenAEC-Foundation/open-pdf-studio-library",
                        "46f844f67cfa4226c84ebfaeaad66086fa4d7112",
                        "collections/ks-steel-shapes/parametric.json", "MIT"),
}

MM = 1e-3
UNITS = "SI (m, m^2, m^4, m^6, kg/m)"
CONVENTIONS = ("local 3 = major axis, 2 = minor; d along local 2.  As2 = "
               "d*tw, As3 = 5/3*bf*tf.  x0 = y0 = 0 (doubly symmetric).")


# ------------------------------------------------------------ exact I outline
def exact_I(h, b, tw, tf, r):
    """A, I33, I22, Z33, Z22 of the rolled I outline (any length unit)."""
    hw = h - 2.0 * tf
    A = 2.0 * b * tf + hw * tw
    I33 = b * h ** 3 / 12.0 - (b - tw) * hw ** 3 / 12.0
    I22 = 2.0 * tf * b ** 3 / 12.0 + hw * tw ** 3 / 12.0
    Z33 = b * tf * (h - tf) + tw * hw * hw / 4.0
    Z22 = tf * b * b / 2.0 + hw * tw * tw / 4.0
    if r > 0.0:
        # one spandrel: square [0, r]^2 at the web-flange corner minus the
        # quarter disc of radius r centred at (r, r) (local coords: u away
        # from the web face, v away from the flange face)
        a_sq, a_qd = r * r, math.pi * r * r / 4.0
        a = a_sq - a_qd
        c = 4.0 * r / (3.0 * math.pi)
        u_sq, u_qd = r / 2.0, r - c            # centroid offsets
        i_sq = r ** 4 / 12.0                   # own centroidal axis
        i_qd = math.pi * r ** 4 / 16.0 - a_qd * c * c
        # about the major axis: v measured from the flange face toward the
        # centre, the spandrel centroid at distance yv = (h/2 - tf) - v
        y0 = h / 2.0 - tf
        x0 = tw / 2.0

        def second(d0, sgn):
            # sum over square / disc: I_own + A (d0 + sgn*off)^2
            return ((i_sq + a_sq * (d0 + sgn * u_sq) ** 2)
                    - (i_qd + a_qd * (d0 + sgn * u_qd) ** 2))

        def first(d0, sgn):
            return a_sq * (d0 + sgn * u_sq) - a_qd * (d0 + sgn * u_qd)
        A += 4.0 * a
        I33 += 4.0 * second(y0, -1.0)
        I22 += 4.0 * second(x0, +1.0)
        Z33 += 4.0 * first(y0, -1.0)
        Z22 += 4.0 * first(x0, +1.0)
    return {"A": A, "I33": I33, "I22": I22, "Z33": Z33, "Z22": Z22,
            "S33": I33 / (h / 2.0), "S22": I22 / (b / 2.0)}


def darwish_J(h, b, tw, tf, r):
    """El Darwish & Johnston (1965) torsion constant of a rolled I."""
    alpha = (-0.042 + 0.2204 * tw / tf + 0.1355 * r / tf
             - 0.0865 * r * tw / tf ** 2 - 0.0725 * tw ** 2 / tf ** 2)
    D = ((tf + r) ** 2 + tw * (r + tw / 4.0)) / (2.0 * r + tf)
    return (2.0 / 3.0 * b * tf ** 3 + (h - 2.0 * tf) * tw ** 3 / 3.0
            + 2.0 * alpha * D ** 4 - 0.42 * tf ** 4)


def _sig(v, n=6):
    return float(f"{v:.{n}g}")


def record(name, family, h, b, tw, tf, r, tab, mass, src, edi=None,
           r_computed=False, extra=None):
    """``tab``: tabulated SI props (missing keys are computed exactly)."""
    ex = exact_I(h, b, tw, tf, r)
    props, computed = {}, []
    for k in ("A", "I33", "I22", "S33", "S22", "Z33", "Z22"):
        if tab.get(k) is not None:
            props[k] = _sig(tab[k])
        else:
            props[k] = _sig(ex[k])
            computed.append(k)
    if tab.get("J") is not None:
        props["J"] = _sig(tab["J"])
    else:
        props["J"] = _sig(darwish_J(h, b, tw, tf, r))
        computed.append("J")
    if tab.get("Cw") is not None:
        props["Cw"] = _sig(tab["Cw"])
    else:
        props["Cw"] = _sig(props["I22"] * (h - tf) ** 2 / 4.0)
        computed.append("Cw")
    props["As2"] = _sig(h * tw)
    props["As3"] = _sig(5.0 / 3.0 * b * tf)
    computed += ["As2", "As3"]
    for k, ik in (("r33", "I33"), ("r22", "I22")):
        if tab.get(k) is not None:
            props[k] = _sig(tab[k])
        else:
            props[k] = _sig(math.sqrt(props[ik] / props["A"]))
            computed.append(k)
    props["x0"] = 0.0
    props["y0"] = 0.0
    computed += ["x0", "y0"]
    props["mass"] = _sig(mass)
    rec = {"name": name, "family": family, "type": "I",
           "dims": {"d": _sig(h), "bf": _sig(b), "tf": _sig(tf),
                    "tw": _sig(tw), "r": _sig(r)},
           "props": props, "computed": computed, "src": src}
    if r_computed:
        rec["computed_dims"] = ["r"]
    if edi:
        rec["edi"] = edi
    if extra:
        rec.update(extra)
    return rec, ex


class Gate:
    def __init__(self, sid):
        self.sid, self.worst, self.dropped = sid, {}, []

    def check(self, rec, ex, keys=("A", "I33", "I22")):
        """A within 2 % and I within 3 % of the exact outline (3 % / 4.5 %
        when the source gives no root radius: the outline then omits the
        fillets)."""
        nofil = rec["dims"]["r"] == 0.0
        bad = []
        for k in keys:
            if k in rec["computed"]:
                continue
            d = abs(rec["props"][k] / ex[k] - 1.0)
            w = self.worst.get(k, (0.0, ""))
            if d > w[0]:
                self.worst[k] = (d, rec["name"])
            if d > (0.02 if k == "A" else 0.03) * (1.5 if nofil else 1.0):
                bad.append(f"{k} {d:.1%}")
        if bad:
            self.dropped.append((rec["name"], bad))
        return not bad

    def report(self):
        print(f"[{self.sid}] worst exact-geometry deviation: "
              + ", ".join(f"{k} {v[0]:.2%} ({v[1]})"
                          for k, v in sorted(self.worst.items())))
        for n, b in self.dropped:
            print(f"[{self.sid}] DROPPED {n}: {b}")


def _num(s):
    return float(str(s).strip())


# --------------------------------------------------------------------- CISC
def build_cisc(src: Path):
    rows = list(csv.DictReader(open(src / "cisc_W.csv", encoding="utf-8")))
    ls = {}
    for x in csv.DictReader(open(src / "limitstates_cisc_12_w.csv",
                                 encoding="utf-8")):
        ls.setdefault(x["EDI_Std_Nomenclature"], x)
    aisc = {s["name"].upper(): s for s in json.load(
        open(OUT / "aisc.json", encoding="utf-8"))["shapes"]}
    gate, shapes, cross, nor = Gate("cisc"), [], {}, []
    for x in rows:
        h, b, tf, tw = (_num(x[k]) * MM for k in ("D", "B", "T", "W"))
        # root radius: the same ASTM A6 shape in AISC v16 (dims.r = kdes -
        # tf), accepted only when d, bf, tw, tf agree within 1 mm
        a = aisc.get(x["Ds_i"].upper())
        r = 0.0
        if a and all(abs(a["dims"][k] - v) <= tol * MM for k, v, tol in (
                ("d", h, 1.5), ("bf", b, 1.5), ("tw", tw, 0.5),
                ("tf", tf, 0.5))):
            r = a["dims"].get("r", 0.0)
        else:
            nor.append(x["Dsg"])
        tab = {"A": _num(x["A"]) * 1e-6, "I33": _num(x["Ix"]) * 1e-12,
               "I22": _num(x["Iy"]) * 1e-12, "S33": _num(x["Sx"]) * 1e-9,
               "S22": _num(x["Sy"]) * 1e-9, "Z33": _num(x["Zx"]) * 1e-9,
               "Z22": _num(x["Zy"]) * 1e-9, "r33": _num(x["Rx"]) * MM,
               "r22": _num(x["Ry"]) * MM, "J": _num(x["J"]) * 1e-12,
               "Cw": _num(x["Cw"]) * 1e-18}
        rec, ex = record(x["Dsg"], "W", h, b, tw, tf, r, tab,
                         _num(x["Mass"]), "CISC-SST9.2", edi=x["Ds_i"],
                         r_computed=r > 0.0,
                         extra={"cisc": {k: x[k] for k in (
                             "Dsg", "D", "B", "T", "W", "K", "Mass", "A",
                             "Ix", "Iy", "Zx", "Zy", "J", "Cw")}})
        if gate.check(rec, ex):
            shapes.append(rec)
        o = ls.get(x["Dsg"])
        if o:
            for k, ok in (("A", "A"), ("I33", "Ix"), ("I22", "Iy"),
                          ("Z33", "Zx"), ("J", "J")):
                sc = {"A": 1e-6, "I33": 1e-12, "I22": 1e-12, "Z33": 1e-9,
                      "J": 1e-12}[k]
                d = abs(float(o[ok]) * sc / rec["props"][k] - 1.0)
                if d > cross.get(k, (0.0, ""))[0]:
                    cross[k] = (d, x["Dsg"])
    gate.report()
    print(f"[cisc] no AISC root radius for {len(nor)} shapes: {nor}")
    print("[cisc] cross-check vs limitstates SST 12: " + ", ".join(
        f"{k} {v[0]:.2%} ({v[1]})" for k, v in sorted(cross.items())))
    return {"standard": "Canadian (CISC / CSA G40.20)",
            "edition": "CISC Structural Section Tables (SST) 9.2 - W shapes",
            "source": ("Published tables (see 'sources' and "
                       "skyframe/docs/SECTION_SOURCES.md). Values not printed "
                       "by the source are computed exactly from the "
                       "published dimensions and listed per shape in "
                       "'computed'."),
            "units": UNITS,
            "sources": {"CISC-SST9.2": _src_line("cisc_W.csv",
                        "CISC Structural Section Tables v9.2, W shapes")},
            "conventions": CONVENTIONS + (" dims.r (root radius, listed in "
                                          "computed_dims) is taken from the "
                                          "identical ASTM A6 shape of "
                                          "aisc.json (AISC v16 kdes - tf)."),
            "shapes": shapes}


# --------------------------------------------------------------------- GOST
_SER = {"Б": "Б", "Ш": "Ш", "К": "К", "Дб": "Д", "Дш": "Д"}
_LAT = str.maketrans({"Б": "B", "Ш": "Sh", "К": "K", "Д": "D", "б": "b",
                      "ш": "sh"})


def build_gost(src: Path):
    js = open(src / "gost_calcx.js", encoding="utf-8").read()
    dims = {}
    for m in re.finditer(r'"([0-9]+[А-Яа-я]+[0-9]+)":\s*\{(.*?)\}', js, re.S):
        body = dict(re.findall(r'(\w+):\s*"([^"]*)"', m.group(2)))
        dims[m.group(1)] = body
    kv = {}
    for line in open(src / "gost_kuvbur_B.txt", encoding="utf-8-sig"):
        f = line.rstrip("\n").split("\t")
        if len(f) > 17 and f[1].strip() and f[0].strip().isdigit():
            kv[f[1].strip()] = f
    gate, shapes, notes = Gate("gost"), [], []
    fam_label = {"Б": "GOST Б (normal)", "Ш": "GOST Ш (wide flange)",
                 "К": "GOST К (column)", "Д": "GOST Д (additional)"}
    for name, d in dims.items():
        ser = re.match(r"[0-9]+([А-Яа-я]+)", name).group(1)
        fam = fam_label[_SER[ser]]
        h, b, tw, tf, r = (float(d[k].replace(",", ".")) * MM
                           for k in ("h", "b", "s", "t", "R"))
        A_tab = float(d["S"].replace(",", ".")) * 1e-4
        mass = float(d["m"].replace(",", "."))
        tab = {"A": A_tab}
        src_id = "GOST-CALCX"
        if name in kv:                          # Б series: tabulated props
            f = kv[name]
            kd = [float(v) for v in f[2:18]]
            # P A h b s t r1 r1 d Iy Wy Sy iy Iz Wz iz
            P, A, hh, bb, ss, tt, r1 = kd[0:7]
            if (abs(hh - h / MM) > 1e-6 or abs(bb - b / MM) > 1e-6
                    or abs(ss - tw / MM) > 1e-6 or abs(tt - tf / MM) > 1e-6):
                # keep the depth that reproduces the table's own W = I/(h/2)
                Iy_, Wy_ = kd[9], kd[10]
                if (abs(2 * Iy_ / hh / Wy_ - 1) < abs(2 * Iy_ / (h / MM)
                                                       / Wy_ - 1)):
                    notes.append(f"{name}: h {h / MM:g} -> {hh:g} mm "
                                 "(kuvbur; matches its Wy = Iy/(h/2))")
                    h, b, tw, tf = hh * MM, bb * MM, ss * MM, tt * MM
                else:
                    notes.append(f"{name}: dims differ between sources")
            if abs(r1 - r / MM) > 1e-6:
                # resolve with the exact area: keep the radius whose outline
                # reproduces the tabulated area
                ea = exact_I(h, b, tw, tf, r)["A"]
                eb = exact_I(h, b, tw, tf, r1 * MM)["A"]
                if abs(eb / (A * 1e-4) - 1) < abs(ea / (A * 1e-4) - 1):
                    notes.append(f"{name}: r {r / MM:g} -> {r1:g} (kuvbur; "
                                 "reproduces the tabulated A)")
                    r = r1 * MM
            if abs(P - mass) > 1e-6:
                notes.append(f"{name}: mass {mass:g} -> {P:g} (kuvbur)")
                mass = P
            if abs(A * 1e-4 / A_tab - 1) > 1e-6:
                notes.append(f"{name}: A {A_tab * 1e4:g} -> {A:g} cm2 "
                             "(kuvbur)")
            Iy, Wy, Sy, iy, Iz, Wz, iz = kd[9:16]
            tab = {"A": A * 1e-4, "I33": Iy * 1e-8, "S33": Wy * 1e-6,
                   "Z33": 2.0 * Sy * 1e-6, "r33": iy * 1e-3,
                   "I22": Iz * 1e-8, "S22": Wz * 1e-6, "r22": iz * 1e-3}
            src_id = "GOST-KUVBUR"
        rec, ex = record(name, fam, h, b, tw, tf, r, tab, mass, src_id,
                         edi=name.translate(_LAT))
        if "Z33" in tab:
            rec["computed"].append("Z33")   # 2 x the tabulated S_y
        if gate.check(rec, ex):
            # mass gate: A * 7850
            dm = abs(rec["props"]["A"] * 7850.0 / rec["props"]["mass"] - 1)
            if dm > 0.03:
                notes.append(f"{name}: mass {mass:g} contradicts A "
                             f"({dm:.1%}); A*7850 used")
                rec["props"]["mass"] = _sig(rec["props"]["A"] * 7850.0)
                rec["computed"].append("mass")
            shapes.append(rec)
    gate.report()
    for n in notes:
        print("[gost]", n)
    return {"standard": "Russian (GOST 26020-83)",
            "edition": "GOST 26020-83 hot-rolled parallel-flange I-beams "
                       "(series Б, Ш, К, Д)",
            "source": ("Published tables (see 'sources' and "
                       "skyframe/docs/SECTION_SOURCES.md). Series Ш, К and "
                       "Д print dimensions, area and mass only: their "
                       "section properties are computed exactly from the "
                       "published dimensions (listed in 'computed')."),
            "units": UNITS,
            "sources": {
                "GOST-CALCX": _src_line("gost_calcx.js",
                                        "GOST 26020-83 dimensions, A, mass"),
                "GOST-KUVBUR": _src_line(
                    "gost_kuvbur_B.txt", "GOST 26020-83 series Б full "
                    "property table (Iy, Wy, Sy, iy, Iz, Wz, iz)")},
            "conventions": CONVENTIONS + (" GOST y-y (major) = local 3; "
                                          "Z33 = 2 Sy (tabulated half-"
                                          "section static moment)."),
            "notes": notes, "shapes": shapes}


# ---------------------------------------------------------------------- JIS
_JFE_FAM = {"広幅": "H wide (HW)", "中幅": "H medium (HM)",
            "細幅": "H narrow (HN)"}


def build_jis(src: Path):
    rows = list(csv.reader(open(src / "jfe_H.csv", encoding="utf-8-sig")))
    head, rows = rows[0], rows[1:]
    gate, shapes = Gate("jis"), []
    for x in rows:
        if x[1] not in _JFE_FAM:
            continue                     # SH = JFE fixed-outer-size series
        H, B, t1, t2, r = (float(v) * MM for v in x[4:9])
        A, mass, Ix, Iy, ix, iy, Zx, Zy = (float(v) for v in x[9:17])
        Zpx, Zpy = float(x[19]), float(x[20])
        tab = {"A": A * 1e-4, "I33": Ix * 1e-8, "I22": Iy * 1e-8,
               "r33": ix * 1e-2, "r22": iy * 1e-2, "S33": Zx * 1e-6,
               "S22": Zy * 1e-6, "Z33": Zpx * 1e-6, "Z22": Zpy * 1e-6}
        rec, ex = record(x[3], _JFE_FAM[x[1]], H, B, t1, t2, r, tab, mass,
                         "JFE", edi=x[3].replace("H-", "H"),
                         extra={"jis_series": x[2],
                                "jis_note": ("*" if x[0] == "*" else "")})
        if gate.check(rec, ex):
            shapes.append(rec)
    gate.report()
    return {"standard": "Japanese (JIS G 3192)",
            "edition": "JIS G 3192 hot-rolled H-sections (wide, medium, "
                       "narrow series) as tabulated in the JFE Steel shapes "
                       "handbook",
            "source": ("Published tables (see 'sources' and "
                       "skyframe/docs/SECTION_SOURCES.md). J, Cw and shear "
                       "areas are computed exactly from the published "
                       "dimensions (listed in 'computed')."),
            "units": UNITS,
            "sources": {"JFE": _src_line("jfe_H.csv",
                                         "JFE H-shape section property table "
                                         "(JIS G 3192 sizes)")},
            "conventions": CONVENTIONS + (" jis_note '*' = sizes marked in "
                                          "the handbook (outside the "
                                          "standard JIS series)."),
            "shapes": shapes}


# ----------------------------------------------------------------------- KS
def build_ks(src: Path):
    rows = list(csv.reader(open(src / "mq_ks.txt", encoding="utf-8")))
    head, rows = rows[0], rows[1:]
    col = {h.split("##")[0]: i for i, h in enumerate(head)}
    oa = {}
    for fam in json.load(open(src / "openaec_ks.json",
                              encoding="utf-8"))["families"]:
        for s in fam["sizes"]:
            oa[s[0].replace(" ", "-")] = s[1:]
    gate, shapes, diffs, nchk = Gate("ks"), [], [], 0
    for x in rows:
        if not x or not x[0].startswith("H_SC_"):
            continue
        name = x[0][len("H_SC_"):]
        g = lambda k: float(str(x[col[k]]).strip())  # noqa: E731
        H, B = g("Height") * MM, g("Width") * MM
        tw, tf, r = (g("Web Thickness") * MM, g("Flange Thickness") * MM,
                     g("Web Fillet") * MM)
        m = re.match(r"H-([\d.]+)x([\d.]+)x([\d.]+)x([\d.]+)$", name)
        if not m or [float(v) * MM for v in m.groups()] != [H, B, tw, tf]:
            raise SystemExit(f"KS row {name}: designation != dims")
        tab = {"A": g("Section Area") * 1e-6,
               "I33": g("Moment of Inertia strong axis") * 1e-12,
               "I22": g("Moment of Inertia weak axis") * 1e-12,
               "S33": g("Elastic Modulus strong axis") * 1e-9,
               "S22": g("Elastic Modulus weak axis") * 1e-9,
               "Z33": g("Plastic Modulus strong axis") * 1e-9,
               "Z22": g("Plastic Modulus weak axis") * 1e-9}
        mass = g("Nominal Weight")
        if name in oa:
            nchk += 1
            if [v * MM for v in oa[name]] != [H, B, tw, tf, r]:
                diffs.append((name, oa[name]))
        rec, ex = record(name, "H (KS D 3502)", H, B, tw, tf, r, tab, mass,
                         "KS-MQ", edi=name.replace("H-", "H"))
        if gate.check(rec, ex):
            shapes.append(rec)
    gate.report()
    print(f"[ks] dims cross-checked against OpenAEC for {nchk} sizes, "
          f"{len(diffs)} differ: {diffs}")
    return {"standard": "Korean (KS D 3502)",
            "edition": "KS D 3502 hot-rolled H-sections",
            "source": ("Published tables (see 'sources' and "
                       "skyframe/docs/SECTION_SOURCES.md). J, Cw, radii of "
                       "gyration and shear areas are computed exactly from "
                       "the published dimensions (listed in 'computed')."),
            "units": UNITS,
            "sources": {"KS-MQ": _src_line(
                "mq_ks.txt", "KS D 3502 H-section dimensions and properties "
                "(Revit type catalogue); dimensions cross-checked against "
                "OpenAEC ks-steel-shapes " + SOURCES["openaec_ks.json"][1][:7]
                + " (MIT)")},
            "conventions": CONVENTIONS,
            "shapes": shapes}


def _src_line(key, what):
    repo, sha, path, lic = SOURCES[key]
    return f"{what} as transcribed in {repo} @{sha[:7]} ({lic}), {path}"


BUILD = {"cisc": build_cisc, "gost": build_gost, "jis": build_jis,
         "ks": build_ks}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--only", nargs="*", default=list(BUILD))
    a = ap.parse_args(argv)
    for key in SOURCES:
        p = a.src / key
        if p.is_file():
            print(f"{key}: sha256 {hashlib.sha256(p.read_bytes()).hexdigest()}")
    for sid in a.only:
        data = BUILD[sid](a.src)
        path = OUT / f"{sid}.json"
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        print(f"{path.name}: {len(data['shapes'])} shapes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
