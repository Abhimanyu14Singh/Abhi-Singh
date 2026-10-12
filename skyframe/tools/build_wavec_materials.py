"""Build the Wave C regional material libraries
(``skyframe/skyframe/data/materials/{gb,csa,jis,ks,gost}.json``).

Run from the repository's ``skyframe`` folder::

    python3 tools/build_wavec_materials.py

Every value here was entered FROM MEMORY of the cited standards (no copy of
the standards was reachable from the build environment) and is listed in
docs/REVIEW_FLAGS.md ("Wave C").  Formula-derived moduli are computed
exactly from the stated formula; the record keeps the formula text.
Record format = the v1.21 library spec (skyframe.core.material_db).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

G0 = 9.80665
MPA = 1e6
OUT = Path(__file__).resolve().parents[1] / "skyframe" / "data" / "materials"
FLAG = " [entered from memory - see docs/REVIEW_FLAGS.md Wave C]"


def _rec(name, kind, group, E, nu, rho, alpha, fy=None, fu=None, fc=None,
         E_basis="code value", source="", formula="", unit_weight=None,
         **extra):
    uw = rho * G0 if unit_weight is None else unit_weight
    rho = uw / G0
    r = {"name": name, "kind": kind, "group": group, "E": E, "nu": nu,
         "G": E / (2.0 * (1.0 + nu)), "unit_weight": uw,
         "mass_density": rho, "alpha": alpha, "fy": fy, "fu": fu,
         "fc": fc, "E_basis": E_basis, "source": source + FLAG,
         "formula": formula}
    r.update(extra)
    return r


def steel(name, group, fy, fu, E, source, formula, kind="steel"):
    return _rec(name, kind, group, E, 0.3, 7850.0, 1.2e-5, fy * MPA,
                fu * MPA, None, "code value", source, formula)


def rebar(name, group, fy, fu, E, source, formula):
    return _rec(name, "rebar", group, E, 0.3, 7850.0, 1.2e-5, fy * MPA,
                fu * MPA, None, "code value", source, formula)


def concrete(name, group, fc, E, uw, source, formula, E_basis, **extra):
    return _rec(name, "concrete", group, E, 0.2, 0.0, 1e-5,
                None, None, fc * MPA, E_basis, source, formula,
                unit_weight=uw, **extra)


# ------------------------------------------------------------------- GB
def gb():
    mats = []
    E = 206000 * MPA
    for nm, fy, fu, grp in (("Q235", 235, 370, "GB/T 700-2006"),
                            ("Q355", 355, 470, "GB/T 1591-2018"),
                            ("Q390", 390, 490, "GB/T 1591-2018"),
                            ("Q420", 420, 520, "GB/T 1591-2018"),
                            ("Q460", 460, 550, "GB/T 1591-2018")):
        mats.append(steel(
            nm, grp, fy, fu, E,
            f"{grp} nominal yield ReH (t <= 16 mm) and minimum tensile Rm; "
            "E = 206,000 MPa, G = 79,000 MPa (GB 50017-2017 Table 4.4.8); "
            "alpha = 12e-6 /K; 7850 kg/m^3",
            "E = 206,000 MPa (GB 50017-2017 Table 4.4.8); G = E/2(1+nu)"))
    # GB 50010-2010 (2015) Table 4.1.3-1 fck, Table 4.1.5 Ec (x 1e4 MPa)
    for g, fck, ec in ((20, 13.4, 2.55), (25, 16.7, 2.80), (30, 20.1, 3.00),
                       (35, 23.4, 3.15), (40, 26.8, 3.25), (45, 29.6, 3.35),
                       (50, 32.4, 3.45), (55, 35.5, 3.55), (60, 38.5, 3.60)):
        ef = 1e5 / (2.2 + 34.7 / g)
        mats.append(concrete(
            f"C{g}", "GB 50010-2010", fck, ec * 1e4 * MPA, 25000.0,
            f"GB 50010-2010 (2015 ed.) Table 4.1.3-1 fck = {fck} MPa (axial "
            f"characteristic; fcu,k = {g} MPa cube) and Table 4.1.5 Ec; "
            "nu = 0.2, alpha = 1e-5 /K (§4.1.8); 25 kN/m^3 reinforced "
            "concrete (GB 50009 Appendix A)",
            f"Ec tabulated {ec:.2f}e4 MPa (Table 4.1.5); the commentary "
            f"formula 1e5/(2.2 + 34.7/fcu,k) = {ef:.0f} MPa is kept as "
            "E_formula", "table", fcu=g * MPA, E_table=ec * 1e4 * MPA,
            E_formula=ef * MPA))
    for nm, fy, fu, es in (("HPB300", 300, 420, 2.10e5),
                           ("HRB400", 400, 540, 2.00e5),
                           ("HRB500", 500, 630, 2.00e5)):
        mats.append(rebar(
            nm, "GB 50010-2010", fy, fu, es * MPA,
            "GB 50010-2010 Table 4.2.2-1 (fyk, fstk) and Table 4.2.5 (Es)",
            f"Es = {es:.2e} MPa; fy = fyk, fu = fstk"))
    return {"standard": "Chinese (GB)",
            "edition": "GB 50017-2017, GB/T 700-2006, GB/T 1591-2018, "
                       "GB 50010-2010 (2015)",
            "source": "Characteristic values of the cited tables" + FLAG,
            "notes": "Concrete fc = fck (axial characteristic strength of "
                     "GB 50010, not the cube grade); fcu keeps the grade.",
            "materials": mats}


# ------------------------------------------------------------------ CSA
def csa():
    mats = []
    E = 200000 * MPA
    for nm, fy, fu in (("260W", 260, 410), ("300W", 300, 450),
                       ("350W", 350, 450)):
        mats.append(steel(
            nm, "CSA G40.20/G40.21", fy, fu, E,
            "CSA G40.21 grade minimum Fy / Fu; E = 200,000 MPa, G = 77,000 "
            "MPa (CSA S16-19 Clause 2.2 symbols); alpha = 12e-6 /K",
            "E = 200,000 MPa (CSA S16); G = E/2(1+nu) = 76,923 MPa "
            "(S16 G = 77,000)"))
    for fc in (25, 30, 35, 40, 45, 50, 60):
        Ec = 4500.0 * fc ** 0.5
        mats.append(concrete(
            f"{fc}MPa-CSA", "CSA A23.3-19", fc, Ec * MPA, 2400.0 * G0,
            f"CSA A23.3-19 Clause 8.6.2.3: Ec = 4500 sqrt(f'c) for normal "
            "density concrete (gamma_c ~ 2300 kg/m^3); 2400 kg/m^3 "
            "reinforced; nu = 0.2; alpha = 10e-6 /K",
            f"Ec = 4500 sqrt({fc}) = {Ec:.1f} MPa", "formula",
            E_formula=Ec * MPA))
    for nm, fy, fu in (("400W", 400, 540), ("400R", 400, 540),
                       ("500W", 500, 625)):
        mats.append(rebar(
            nm, "CSA G30.18", fy, fu, E,
            "CSA G30.18 grade yield and minimum tensile strength; Es = "
            "200,000 MPa (CSA A23.3-19 Clause 8.5.4.1)",
            "Es = 200,000 MPa"))
    return {"standard": "Canadian (CSA)",
            "edition": "CSA G40.20/G40.21, CSA S16-19, CSA A23.3-19, "
                       "CSA G30.18",
            "source": "Characteristic values of the cited clauses" + FLAG,
            "materials": mats}


# ------------------------------------------------------------------ JIS
def jis():
    mats = []
    E = 205000 * MPA
    for nm, grp, F, fu in (("SS400", "JIS G 3101", 235, 400),
                           ("SN400B", "JIS G 3136", 235, 400),
                           ("SN490B", "JIS G 3136", 325, 490),
                           ("SM490A", "JIS G 3106", 325, 490),
                           ("SM520B", "JIS G 3106", 355, 520),
                           ("STKR400", "JIS G 3466", 245, 400),
                           ("BCR295", "BCR (MLIT certified)", 295, 400)):
        mats.append(steel(
            nm, grp, F, fu, E,
            f"{grp} {nm}: design reference strength F (t <= 40 mm, MLIT "
            "Notification 2464) and minimum tensile strength; E = 205,000 "
            "MPa, G = 79,000 MPa (AIJ steel standard)",
            "E = 205,000 N/mm^2 (AIJ); G = E/2(1+nu)"))
    for fc in (21, 24, 27, 30, 33, 36, 42, 48, 60):
        gam = 23.0 if fc <= 36 else (23.5 if fc <= 48 else 24.0)
        Ec = 3.35e4 * (gam / 24.0) ** 2 * (fc / 60.0) ** (1.0 / 3.0)
        mats.append(concrete(
            f"Fc{fc}", "AIJ RC standard", fc, Ec * MPA,
            (gam + 1.0) * 1000.0,
            "AIJ Standard for Structural Calculation of RC Structures: E = "
            "3.35e4 (gamma/24)^2 (Fc/60)^(1/3) N/mm^2 with plain-concrete "
            f"gamma = {gam} kN/m^3; reinforced unit weight gamma + 1 = "
            f"{gam + 1.0} kN/m^3; nu = 0.2; alpha = 1e-5 /K",
            f"E = 3.35e4 x ({gam}/24)^2 x ({fc}/60)^(1/3) = {Ec:.1f} MPa",
            "formula", E_formula=Ec * MPA))
    for nm, fy, fu in (("SD295", 295, 440), ("SD345", 345, 490),
                       ("SD390", 390, 560), ("SD490", 490, 620)):
        mats.append(rebar(
            nm, "JIS G 3112", fy, fu, E,
            "JIS G 3112 minimum yield point and tensile strength; Es = "
            "205,000 N/mm^2 (AIJ)", "Es = 205,000 N/mm^2"))
    return {"standard": "Japanese (JIS / AIJ)",
            "edition": "JIS G 3101 / G 3106 / G 3136 / G 3466 / G 3112, "
                       "AIJ standards",
            "source": "Characteristic values of the cited standards" + FLAG,
            "materials": mats}


# ------------------------------------------------------------------- KS
def ks():
    mats = []
    E = 210000 * MPA
    for nm, grp, fy, fu in (("SS275", "KS D 3503", 275, 410),
                            ("SM275", "KS D 3515", 275, 410),
                            ("SM355", "KS D 3515", 355, 490),
                            ("SM420", "KS D 3515", 420, 520),
                            ("SM460", "KS D 3515", 460, 570),
                            ("SN275", "KS D 3861", 275, 410),
                            ("SN355", "KS D 3861", 355, 490)):
        mats.append(steel(
            nm, grp, fy, fu, E,
            f"{grp} (2018 grade names) yield (t <= 16 mm) and minimum "
            "tensile strength; E = 210,000 MPa, G = 81,000 MPa (KDS 14 31 "
            "10)", "E = 210,000 MPa (KDS 14 31 10); G = E/2(1+nu)"))
    for fck in (21, 24, 27, 30, 35, 40, 50, 60):
        df = 4.0 if fck <= 40 else (6.0 if fck >= 60
                                    else 4.0 + (fck - 40) / 10.0)
        fcm = fck + df
        Ec = 8500.0 * fcm ** (1.0 / 3.0)
        mats.append(concrete(
            f"fck{fck}-KDS", "KDS 14 20 10", fck, Ec * MPA, 24000.0,
            "KDS 14 20 10: Ec = 0.077 mc^1.5 cbrt(fcm) = 8500 cbrt(fcm) for "
            "mc = 2300 kg/m^3, fcm = fck + df (df = 4 MPa for fck <= 40, 6 "
            "for fck >= 60, linear between); 24 kN/m^3 reinforced; nu = "
            "0.2; alpha = 1e-5 /K",
            f"Ec = 8500 x cbrt({fcm:g}) = {Ec:.1f} MPa", "formula",
            fcm=fcm * MPA, E_formula=Ec * MPA))
    for nm, fy, fu in (("SD400", 400, 560), ("SD500", 500, 620),
                       ("SD600", 600, 710)):
        mats.append(rebar(
            nm, "KS D 3504", fy, fu, 200000 * MPA,
            "KS D 3504 yield and minimum tensile strength; Es = 200,000 MPa "
            "(KDS 14 20 10)", "Es = 200,000 MPa"))
    return {"standard": "Korean (KS / KDS)",
            "edition": "KS D 3503 / D 3515 / D 3861 / D 3504, KDS 14 31 10, "
                       "KDS 14 20 10",
            "source": "Characteristic values of the cited standards" + FLAG,
            "materials": mats}


# ----------------------------------------------------------------- GOST
def gost():
    mats = []
    E = 206000 * MPA
    for nm, fy, fu, t in (("C245", 245, 370, "2-20"),
                          ("C255", 255, 380, "4-10"),
                          ("C345", 345, 490, "2-10"),
                          ("C390", 390, 540, "4-50")):
        mats.append(steel(
            nm, "GOST 27772-2015", fy, fu, E,
            f"SP 16.13330.2017 Table B.5 (GOST 27772): Ryn / Run for t = {t} "
            "mm; E = 2.06e5 MPa, G = 0.78e5 MPa (Table G.10)",
            "E = 206,000 MPa (SP 16.13330 Table G.10); G = E/2(1+nu)"))
    for b, rbn, eb in ((15, 11.0, 24.0), (20, 15.0, 27.5), (25, 18.5, 30.0),
                       (30, 22.0, 32.5), (35, 25.5, 34.5), (40, 29.0, 36.0),
                       (45, 32.0, 37.0), (50, 36.0, 38.0), (55, 39.5, 39.0),
                       (60, 43.0, 39.5)):
        mats.append(concrete(
            f"B{b}", "SP 63.13330.2018", rbn, eb * 1e3 * MPA, 25000.0,
            f"SP 63.13330.2018 Table 6.7 Rb,n = {rbn} MPa (prism "
            f"characteristic; class B{b} = cube guaranteed strength) and "
            "Table 6.11 Eb; nu = 0.2; alpha = 1e-5 /K; 25 kN/m^3",
            f"Eb tabulated {eb}e3 MPa (Table 6.11)", "table",
            E_table=eb * 1e3 * MPA))
    for nm, fy, fu in (("A240", 240, 373), ("A400", 400, 590),
                       ("A500", 500, 600)):
        mats.append(rebar(
            nm, "SP 63.13330 / GOST 34028", fy, fu, 200000 * MPA,
            "SP 63.13330.2018 Table 6.13 Rs,n; minimum tensile strength per "
            "GOST 5781 / GOST 34028; Es = 2.0e5 MPa (§6.2.12)",
            "Es = 200,000 MPa"))
    return {"standard": "Russian (GOST / SP)",
            "edition": "GOST 27772-2015, SP 16.13330.2017, SP 63.13330.2018",
            "source": "Characteristic values of the cited tables" + FLAG,
            "notes": "Concrete fc = Rb,n (prism characteristic strength).",
            "materials": mats}


BUILDERS = {"gb": gb, "csa": csa, "jis": jis, "ks": ks, "gost": gost}


def main(argv=None) -> int:
    for sid, fn in BUILDERS.items():
        data = fn()
        data["units"] = ("SI (E, G, fy, fu, fc: Pa; unit_weight: N/m^3; "
                         "mass_density: kg/m^3; alpha: 1/K)")
        data["from_memory"] = True
        path = OUT / f"{sid}.json"
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False)
                        + "\n", encoding="utf-8")
        print(f"{path.name}: {len(data['materials'])} materials")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
