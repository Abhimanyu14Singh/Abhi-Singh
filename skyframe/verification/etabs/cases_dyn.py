"""Diaphragms D1 and D3, supports S1-S2, dynamics M1-M4 and outputs O1 and O3 (kip, ft).

Definition keys added to those in cases.py and cases_loads.py:

    diaphragms   [{name, points[]}]           rigid diaphragm joining the listed joints (UX, UY, RZ)
    springs      [{at, k[6]}]                 grounded point spring, kip/ft and kip-ft/rad, global axes
    masses       [{at, m[6]}]                 joint mass, kip-s2/ft (UX UY UZ) and kip-ft-s2 (RX RY RZ)
    massSource   {"elements": bool, "masses": bool, "patterns": {name: factor}}
                 lateral mass only, lumped at the story levels (both programs' defaults)
    modal        {"case": name, "modes": n}   eigenvector modal case
    spectra      [{name, points [[T s, Sa g]], damping}]
    rsCases      [{name, dir "X"|"Y", function, scale (ft/s2), combine "SRSS"|"CQC", damping}]
    pdeltaCases  [{name, loads {pattern: factor}}]   nonlinear static with P-Delta, zero initial state
    combos       [{name, type "linear"|"envelope", cases {case: factor}}]
                 an envelope's minimum is reported as case "<name>:min"

Extra check quantities:
    "modal"  {mode, comp "T" | "UX" | "UY"}       period (s) or participating mass ratio of a mode
    "drift"  {story, comp "X" | "Y"}               story drift ratio
"""

from __future__ import annotations

import math

from defs import STEEL_E, STEEL_NU, _COMMON, _b, _beam_section, _col_section, _f, _j, _mat

G_FT = 9.80665 / 0.3048            # ft/s2, standard gravity (32.174)


def _modal(cid, label, mode, comp, hand, formula, case="MODES", how="signed"):
    return {"id": cid, "label": label, "case": case, "quantity": "modal", "mode": mode, "comp": comp,
            "hand": hand, "formula": formula, "how": how}


def _drift(cid, label, case, story, comp, hand, formula):
    return {"id": cid, "label": label, "case": case, "quantity": "drift", "story": story, "comp": comp,
            "hand": hand, "formula": formula, "how": "abs"}


_DYN = dict(_COMMON, mass="joint masses only (material mass 0); lateral mass, lumped at stories")


def _frame_bldg(n, h, bx, by, *, fix_rot=True, mass=0.0):
    """n stories of four corner columns (COL) on a bx x by plan, each floor a rigid diaphragm.
    fix_rot: floor joints restrained in RX, RY, so each column bends double curvature (12EI/h^3)."""
    corners = [(0.0, 0.0), (bx, 0.0), (bx, by), (0.0, by)]
    frames, supports, dias, masses = [], [], [], []
    for s in range(n):
        for k, (x, y) in enumerate(corners):
            frames.append({"id": f"C{k + 1}S{s + 1}", "i": [x, y, s * h], "j": [x, y, (s + 1) * h],
                           "section": "COL", "angle": 0.0})
    supports += [{"at": [x, y, 0.0], "restraint": [1] * 6} for x, y in corners]
    for s in range(1, n + 1):
        pts = [[x, y, s * h] for x, y in corners]
        dias.append({"name": f"D{s}", "points": pts})
        if fix_rot:
            supports += [{"at": p, "restraint": [0, 0, 0, 1, 1, 0]} for p in pts]
        if mass:
            masses += [{"at": p, "m": [mass, mass, 0, 0, 0, 0]} for p in pts]
    return frames, supports, dias, masses


def _shear_modes(n, k, m):
    """Uniform n-story shear building, story stiffness k, floor mass m: (omega^2, shape) per mode."""
    out = []
    for j in range(1, n + 1):
        a = (2 * j - 1) * math.pi / (2 * n + 1)
        lam = 4 * math.sin(a / 2) ** 2
        out.append((k / m * lam, [math.sin(i * a) for i in range(1, n + 1)]))
    return out


def D1() -> dict:
    """One story, four columns, rigid diaphragm, lateral load at one corner: translation plus twist."""
    E, h, bx, by, P = STEEL_E, 12.0, 30.0, 20.0, 10.0
    I33, I22, J = 0.02, 0.005, 0.0005
    G = E / (2 * (1 + STEEL_NU))
    kx, ky = 3 * E * I33 / h**3, 3 * E * I22 / h**3          # cantilever columns (tops free to rotate)
    xc, yc = bx / 2, by / 2
    kt = 4 * (kx * yc**2 + ky * xc**2) + 4 * G * J / h
    frames, supports, dias, _ = _frame_bldg(1, h, bx, by, fix_rot=False)
    at = [0.0, by, h]
    uc, th = P / (4 * kx), -P * (by - yc) / kt                # centre translation, rotation (Mz = -P e)
    ux = lambda x, y: uc - th * (y - yc)
    uy = lambda x, y: th * (x - xc)
    return {
        "id": "D1", "title": "Rigid diaphragm, eccentric load",
        "purpose": ("A rigid floor on four cantilever columns, loaded along X at a corner 10 ft off the centre "
                    "of rigidity: the floor translates and twists, and each column takes its share by stiffness."),
        "units": "kip-ft", "settings": dict(_COMMON, diaphragm="rigid, assigned to the four top joints",
                                            supports="fixed bases"),
        "materials": _mat(), "sections": [_col_section()],
        "stories": [{"name": "Story1", "height": h}],
        "frames": frames, "supports": supports, "diaphragms": dias,
        "patterns": [{"name": "PX"}],
        "pointLoads": [{"pattern": "PX", "at": at, "f": [P, 0, 0, 0, 0, 0]}],
        "checks": [
            _j("PX-UX1", "Loaded corner moves along X", "PX", at, "UX", ux(0, by), "P/4kx + theta e, kx = 3EI33/h^3"),
            _j("PX-UX2", "Opposite corner moves less", "PX", [bx, 0.0, h], "UX", ux(bx, 0), "P/4kx - theta e"),
            _j("PX-UY", "Twist moves a corner along Y", "PX", [bx, by, h], "UY", uy(bx, by),
               "theta (x - xc), theta = P e / K_theta"),
            _j("PX-RZ", "Floor rotation", "PX", at, "RZ", th,
               "-P e / [4 (kx yc^2 + ky xc^2) + 4 GJ/h]"),
            _f("PX-V", "Shear in the loaded corner column", "PX", "C4S1", "i", "V2", kx * ux(0, by), "kx ux"),
            _b("PX-FX", "Base shear", "PX", "FX", -P, "-P"),
            _b("PX-MZ", "Base torque about the origin", "PX", "MZ", P * by, "P y"),
        ],
    }


def D3() -> dict:
    """A one-story podium carrying two 3-story towers: one diaphragm per tower floor, one for the podium."""
    E, hp, ht, P = STEEL_E, 15.0, 12.0, 30.0
    I33 = 0.02
    kp1, kt1 = 12 * E * I33 / hp**3, 12 * E * I33 / ht**3      # floor joints fixed in rotation: 12EI/h^3
    xs_p, ys = [20.0 * i for i in range(6)], [0.0, 20.0, 40.0]
    towers = {"A": [0.0, 20.0, 40.0], "B": [60.0, 80.0, 100.0]}
    frames, supports, dias, loads = [], [], [], []
    for x in xs_p:
        for y in ys:
            frames.append({"id": f"P{int(x)}_{int(y)}", "i": [x, y, 0.0], "j": [x, y, hp], "section": "COL",
                           "angle": 0.0})
            supports.append({"at": [x, y, 0.0], "restraint": [1] * 6})
            supports.append({"at": [x, y, hp], "restraint": [0, 0, 0, 1, 1, 0]})
    dias.append({"name": "PODIUM", "points": [[x, y, hp] for x in xs_p for y in ys]})
    for t, xs in towers.items():
        for s in range(3):
            z0, z1 = hp + s * ht, hp + (s + 1) * ht
            pts = [[x, y, z1] for x in xs for y in ys]
            for p in pts:
                frames.append({"id": f"T{t}{s + 1}_{int(p[0])}_{int(p[1])}", "i": [p[0], p[1], z0], "j": p,
                               "section": "COL", "angle": 0.0})
                supports.append({"at": p, "restraint": [0, 0, 0, 1, 1, 0]})
            dias.append({"name": f"T{t}{s + 1}", "points": pts})
    roof = hp + 3 * ht
    loads = [{"pattern": "PA", "at": [x, y, roof], "f": [P / 9, 0, 0, 0, 0, 0]} for x in towers["A"] for y in ys]
    kp, kt = 18 * kp1, 9 * kt1
    up = P / kp
    return {
        "id": "D3", "title": "Two towers on a podium",
        "purpose": ("A 100 x 40 ft podium story carries two 40 ft square, 3-story towers. Every tower floor is "
                    "its own rigid diaphragm and the podium another. A load on tower A's roof runs down "
                    "through A's three stories into the podium; tower B rides on the podium with no shear."),
        "units": "kip-ft",
        "settings": dict(_COMMON, diaphragm="7 rigid diaphragms: the podium and each tower floor",
                         supports="fixed bases; floor joints fixed in rotation (shear building)"),
        "materials": _mat(), "sections": [_col_section()],
        "stories": [{"name": "Story1", "height": hp}] + [{"name": f"Story{s + 2}", "height": ht} for s in range(3)],
        "frames": frames, "supports": supports, "diaphragms": dias,
        "patterns": [{"name": "PA"}],
        "pointLoads": loads,
        "checks": [
            _j("PA-UP", "Podium moves", "PA", [100.0, 0.0, hp], "UX", up, "P / k_podium, 18 columns x 12EI/h^3"),
            _j("PA-UA", "Tower A roof", "PA", [0.0, 0.0, roof], "UX", up + 3 * P / kt,
               "P/k_podium + 3 P/k_story, 9 columns per story"),
            _j("PA-UA1", "Tower A first floor", "PA", [0.0, 0.0, hp + ht], "UX", up + P / kt, "P/k_podium + P/k_story"),
            _j("PA-UB", "Tower B roof rides on the podium", "PA", [100.0, 40.0, roof], "UX", up, "P / k_podium"),
            dict(_f("PA-VB", "No shear in tower B", "PA", "TB1_80_20", "i", "V2", 0.0, "0"), scale=P / 9),
            _f("PA-VA", "Shear in a tower A column", "PA", "TA1_20_20", "i", "V2", P / 9, "P / 9"),
            _b("PA-FX", "Base shear", "PA", "FX", -P, "-P"),
        ],
    }


def S1() -> dict:
    """A cantilever column standing on point springs in all six directions."""
    E, L, P = STEEL_E, 12.0, 10.0
    I33, I22, J = 0.02, 0.005, 0.0005
    G = E / (2 * (1 + STEEL_NU))
    k = [500.0, 400.0, 5000.0, 30000.0, 20000.0, 1000.0]     # kx ky kz (k/ft), krx kry krz (k-ft/rad)
    z0 = 12.0                                                # off the Base level (ETABS pins Base joints)
    base, top = [0.0, 0.0, z0], [0.0, 0.0, z0 + L]
    return {
        "id": "S1", "title": "Point springs at a column base",
        "purpose": ("Springs instead of fixity: the top moves by the column's own bending plus the base slip "
                    "and the base rotation carried up the height. Spring forces are the reactions."),
        "units": "kip-ft", "settings": dict(_COMMON, supports="six point springs at the base, no restraint"),
        "materials": _mat(), "sections": [_col_section()],
        "stories": [{"name": "Story1", "height": z0}, {"name": "Story2", "height": L}],
        "frames": [{"id": "C1", "i": base, "j": top, "section": "COL", "angle": 0.0}],
        "supports": [],
        "springs": [{"at": base, "k": k}],
        "patterns": [{"name": "PX"}, {"name": "PY"}, {"name": "PZ"}, {"name": "TZ"}],
        "pointLoads": [{"pattern": "PX", "at": top, "f": [P, 0, 0, 0, 0, 0]},
                       {"pattern": "PY", "at": top, "f": [0, P, 0, 0, 0, 0]},
                       {"pattern": "PZ", "at": top, "f": [0, 0, -5 * P, 0, 0, 0]},
                       {"pattern": "TZ", "at": top, "f": [0, 0, 0, 0, 0, P]}],
        "checks": [
            _j("PX-UX", "Top along X", "PX", top, "UX", P * L**3 / (3 * E * I33) + P / k[0] + P * L * L / k[4],
               "PL^3/3EI33 + P/kx + P L^2 / kry"),
            _j("PY-UY", "Top along Y", "PY", top, "UY", P * L**3 / (3 * E * I22) + P / k[1] + P * L * L / k[3],
               "PL^3/3EI22 + P/ky + P L^2 / krx"),
            _j("PZ-UZ", "Top settles", "PZ", top, "UZ", -5 * P * (L / (E * 0.1) + 1 / k[2]), "-N (L/EA + 1/kz)"),
            _j("TZ-RZ", "Top twists", "TZ", top, "RZ", P * (L / (G * J) + 1 / k[5]), "T (L/GJ + 1/krz)"),
            _b("PX-FX", "Spring force is the reaction", "PX", "FX", -P, "-P"),
            _b("PX-MY", "Reaction moment about the origin", "PX", "MY", -P * (z0 + L), "-P z_top"),
        ],
    }


def S2() -> dict:
    """Portal frame with pinned bases sitting on vertical springs."""
    E, h, L, H = STEEL_E, 12.0, 20.0, 10.0
    Ic, Ib, A, kz = 0.02, 0.03, 1.0, 2000.0
    R = H * h / L                                            # overturning couple, statically determinate
    sway = H * h * h * L / (12 * E * Ib) + H * h**3 / (6 * E * Ic) + 2 * R * (1 / kz + h / (E * A)) / L * h
    col = _col_section(A=A, I33=Ic)
    bm = dict(_beam_section(), A=A, I33=Ib)
    tl, tr = [0.0, 0.0, h], [L, 0.0, h]
    base = [1, 1, 0, 1, 0, 1]                                # pinned in the frame plane, free to settle
    return {
        "id": "S2", "title": "Pinned bases on vertical springs",
        "purpose": ("Pinned bases: zero base moment, top moments H h / 2. The vertical springs take the "
                    "overturning couple and let the whole frame rock, adding to the sway."),
        "units": "kip-ft", "settings": dict(_COMMON, supports="pins in the XZ plane on vertical springs 2000 k/ft"),
        "materials": _mat(), "sections": [col, bm],
        "stories": [{"name": "Story1", "height": h}],
        "frames": [{"id": "C1", "i": [0.0, 0.0, 0.0], "j": tl, "section": "COL", "angle": 0.0},
                   {"id": "C2", "i": [L, 0.0, 0.0], "j": tr, "section": "COL", "angle": 0.0},
                   {"id": "B1", "i": tl, "j": tr, "section": "BM", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 0.0], "restraint": base}, {"at": [L, 0.0, 0.0], "restraint": base}],
        "springs": [{"at": [0.0, 0.0, 0.0], "k": [0, 0, kz, 0, 0, 0]}, {"at": [L, 0.0, 0.0], "k": [0, 0, kz, 0, 0, 0]}],
        "patterns": [{"name": "H"}],
        "pointLoads": [{"pattern": "H", "at": tl, "f": [H / 2, 0, 0, 0, 0, 0]},
                       {"pattern": "H", "at": tr, "f": [H / 2, 0, 0, 0, 0, 0]}],
        "checks": [
            _j("H-UX", "Sway", "H", tl, "UX", sway,
               "H h^2 L/12EIb + H h^3/6EIc + rocking 2R(1/kz + h/EA) h/L"),
            _j("H-UZ", "Right base settles", "H", [L, 0.0, 0.0], "UZ", -R / kz, "-R / kz, R = H h / L"),
            dict(_f("H-Mbase", "No moment at the pinned base", "H", "C1", "i", "M3", 0.0, "0"), scale=H * h / 2),
            _f("H-Mtop", "Column top moment", "H", "C1", "j", "M3", H * h / 2, "H h / 2"),
            _f("H-P", "Right column compression", "H", "C2", "i", "P", -R, "-H h / L", how="signed"),
            _b("H-FZ", "Vertical reactions cancel", "H", "FZ", 0.0, "0") | {"scale": R},
        ],
    }


# ── dynamics ────────────────────────────────────────────────────────────────
_M1 = dict(n=3, h=12.0, bx=30.0, by=24.0, mc=0.5)


def _m1_modes():
    """All nine modes of the M1 building, sorted by period: [(T, dir, omega2, shape)]."""
    p = _M1
    E, G = STEEL_E, STEEL_E / (2 * (1 + STEEL_NU))
    I33, I22, J = 0.02, 0.005, 0.0005
    kxc, kyc = 12 * E * I33 / p["h"]**3, 12 * E * I22 / p["h"]**3
    m = 4 * p["mc"]
    xc, yc = p["bx"] / 2, p["by"] / 2
    kt = 4 * (kxc * yc**2 + kyc * xc**2) + 4 * G * J / p["h"]
    jm = 4 * p["mc"] * (xc**2 + yc**2)
    modes = []
    for d, k, mm in (("X", 4 * kxc, m), ("Y", 4 * kyc, m), ("RZ", kt, jm)):
        for w2, phi in _shear_modes(p["n"], k, mm):
            modes.append((2 * math.pi / math.sqrt(w2), d, w2, phi))
    return sorted(modes, key=lambda t: -t[0]), 4 * kxc, m


def _m1_model():
    p = _M1
    frames, supports, dias, masses = _frame_bldg(p["n"], p["h"], p["bx"], p["by"], mass=p["mc"])
    return {"materials": _mat(), "sections": [_col_section()],
            "stories": [{"name": f"Story{s + 1}", "height": p["h"]} for s in range(p["n"])],
            "frames": frames, "supports": supports, "diaphragms": dias, "masses": masses,
            "massSource": {"elements": False, "masses": True, "patterns": {}}}


def M1() -> dict:
    """Periods and mass participation of a three-story shear building."""
    modes, _, _ = _m1_modes()
    ratio = lambda phi: sum(phi) ** 2 / (len(phi) * sum(v * v for v in phi))
    checks = []
    for i, (T, d, _, phi) in enumerate(modes, start=1):
        checks.append(_modal(f"T{i}", f"Mode {i} period ({d})", i, "T", T,
                             f"2 pi / omega, omega^2 = (k/m) 4 sin^2((2j-1) pi / 14)"))
    for i, (T, d, _, phi) in enumerate(modes, start=1):
        if d in ("X", "Y") and phi is not None and i <= 4:
            checks.append(_modal(f"M{i}{d}", f"Mode {i} mass ratio U{d}", i, "U" + d, ratio(phi),
                                 "(sum phi)^2 / (n sum phi^2)"))
    return dict({
        "id": "M1", "title": "Shear building periods",
        "purpose": ("Three equal stories, rigid floors, columns bent in double curvature, mass at the four "
                    "corners: X, Y and torsion uncouple, and each has the closed-form periods of a uniform "
                    "shear building. Nine modes, in period order."),
        "units": "kip-ft", "settings": dict(_DYN, diaphragm="rigid, each floor",
                                            supports="fixed bases; floor joints fixed in rotation"),
        "patterns": [], "modal": {"case": "MODES", "modes": 9},
        "checks": checks,
    }, **_m1_model())


def M2() -> dict:
    """Mass from loads: a one-story frame whose only mass is a superimposed dead load."""
    E, h, bx, by, W = STEEL_E, 12.0, 30.0, 24.0, 64.0
    I33, I22 = 0.02, 0.005
    frames, supports, dias, _ = _frame_bldg(1, h, bx, by, fix_rot=False)
    kx, ky = 4 * 3 * E * I33 / h**3, 4 * 3 * E * I22 / h**3
    m = W / G_FT
    G, J = E / (2 * (1 + STEEL_NU)), 0.0005
    kt = kx * (by / 2)**2 + ky * (bx / 2)**2 + 4 * G * J / h      # torsion: columns' sway and own twist
    tops = [[x, y, h] for x, y in ((0.0, 0.0), (bx, 0.0), (bx, by), (0.0, by))]
    return {
        "id": "M2", "title": "Mass from a load pattern",
        "purpose": ("The mass source converts a 64 kip superimposed dead load to mass W/g, placed where the "
                    "load acts (the four corners); nothing else has mass. The sway periods follow from the "
                    "column stiffness, the torsion period from the corner masses' inertia."),
        "units": "kip-ft",
        "settings": dict(_COMMON, mass="from load pattern SDL x 1.0 only; lateral, lumped at stories",
                         diaphragm="rigid", supports="fixed bases"),
        "materials": _mat(), "sections": [_col_section()],
        "stories": [{"name": "Story1", "height": h}],
        "frames": frames, "supports": supports, "diaphragms": dias,
        "patterns": [{"name": "SDL"}],
        "pointLoads": [{"pattern": "SDL", "at": p, "f": [0, 0, -W / 4, 0, 0, 0]} for p in tops],
        "massSource": {"elements": False, "masses": False, "patterns": {"SDL": 1.0}},
        "modal": {"case": "MODES", "modes": 3},
        "checks": [
            _modal("T1", "Period, Y sway", 1, "T", 2 * math.pi * math.sqrt(m / ky), "2 pi sqrt(W/g / ky)"),
            _modal("M1Y", "Mode 1 takes all the Y mass", 1, "UY", 1.0, "1"),
            _modal("T2", "Period, torsion (mass at the four corners)", 2, "T",
                   2 * math.pi * math.sqrt(m * ((bx / 2)**2 + (by / 2)**2) / kt),
                   "2 pi sqrt(sum m r^2 / K_theta)"),
            _modal("T3", "Period, X sway", 3, "T", 2 * math.pi * math.sqrt(m / kx), "2 pi sqrt(W/g / kx)"),
            _b("SDL-FZ", "The load itself", "SDL", "FZ", W, "W"),
        ],
    }


def M3() -> dict:
    """Response spectrum (constant 0.4 g, SRSS) on the M1 building along X."""
    modes, k, m = _m1_modes()
    Sa = 0.4 * G_FT
    V2, U2, V2top = 0.0, 0.0, 0.0
    n = _M1["n"]
    for T, d, w2, phi in modes:
        if d != "X":
            continue
        gam = sum(phi) / sum(v * v for v in phi)                # equal floor masses
        V2 += (Sa * m * gam * sum(phi)) ** 2
        U2 += (gam * phi[-1] * Sa / w2) ** 2
        V2top += (Sa * m * gam * phi[-1]) ** 2                    # top-story shear = roof inertia force
    return dict({
        "id": "M3", "title": "Response spectrum, SRSS",
        "purpose": ("A flat 0.4 g spectrum along X on the M1 building, modes combined by SRSS: base shear and "
                    "roof displacement from the closed-form modes."),
        "units": "kip-ft",
        "settings": dict(_DYN, spectrum="constant 0.4 g, 5% damping", combination="SRSS, one direction",
                         eccentricity="none"),
        "patterns": [], "modal": {"case": "MODES", "modes": 9},
        "spectra": [{"name": "FLAT", "points": [[0.0, 0.4], [10.0, 0.4]], "damping": 0.05}],
        "rsCases": [{"name": "RSX", "dir": "X", "function": "FLAT", "scale": G_FT, "combine": "SRSS",
                     "damping": 0.05}],
        "checks": [
            _b("RSX-FX", "Base shear", "RSX", "FX", math.sqrt(V2), "SRSS of Sa M_eff,j"),
            _j("RSX-UX", "Roof displacement", "RSX", [0.0, 0.0, 3 * _M1["h"]], "UX", math.sqrt(U2),
               "SRSS of Gamma_j phi_roof,j Sa / omega_j^2"),
            _f("RSX-V3", "Top-story column shear", "RSX", "C1S3", "i", "V2", math.sqrt(V2top) / 4,
               "SRSS of the roof inertia force, a quarter per column"),
        ],
    }, **_m1_model())


def M4() -> dict:
    """P-Delta: a cantilever column under axial load and a lateral load, four frame objects."""
    E, L, P, H = STEEL_E, 12.0, 300.0, 10.0
    I33 = 0.02
    k = math.sqrt(P / (E * I33))
    d = H * (math.tan(k * L) - k * L) / (P * k)
    seg = [[0.0, 0.0, L * i / 4] for i in range(5)]
    return {
        "id": "M4", "title": "P-Delta",
        "purpose": ("A 300 kip axial load (a fifth of the buckling load) amplifies the sway of a cantilever by "
                    "a quarter. The exact answer comes from the beam-column equation; the column is four "
                    "frame objects in both programs so the curvature along it is captured."),
        "units": "kip-ft",
        "settings": dict(_COMMON, pDelta="nonlinear static case with P-Delta, zero initial state",
                         supports="fixed base"),
        "materials": _mat(), "sections": [_col_section()],
        "stories": [{"name": "Story1", "height": L}],
        "frames": [{"id": f"C{i + 1}", "i": seg[i], "j": seg[i + 1], "section": "COL", "angle": 0.0} for i in range(4)],
        "supports": [{"at": seg[0], "restraint": [1] * 6}],
        "patterns": [{"name": "P"}, {"name": "H"}],
        "pointLoads": [{"pattern": "P", "at": seg[-1], "f": [0, 0, -P, 0, 0, 0]},
                       {"pattern": "H", "at": seg[-1], "f": [H, 0, 0, 0, 0, 0]}],
        "pdeltaCases": [{"name": "PDH", "loads": {"P": 1.0, "H": 1.0}}],
        "checks": [
            _j("H-UX", "Sway without axial load", "H", seg[-1], "UX", H * L**3 / (3 * E * I33), "HL^3 / 3EI"),
            dict(_j("PDH-UX", "Sway with P-Delta", "PDH", seg[-1], "UX", d, "H (tan kL - kL) / (P k), k = sqrt(P/EI)"),
                 tol=[2e-3, 5e-3]),
            dict(_f("PDH-M", "Base moment with P-Delta", "PDH", "C1", "i", "M3", H * L + P * d, "H L + P delta"),
                 tol=[2e-3, 5e-3]),
        ],
    }


# ── outputs ─────────────────────────────────────────────────────────────────
def O1() -> dict:
    """Story drifts of the M1 building under floor loads 10, 20, 30 kip."""
    p = _M1
    E, h = STEEL_E, p["h"]
    k = 4 * 12 * E * 0.02 / h**3
    F = [10.0, 20.0, 30.0]
    V = [sum(F[s:]) for s in range(3)]
    corners = [(0.0, 0.0), (p["bx"], 0.0), (p["bx"], p["by"]), (0.0, p["by"])]
    model = _m1_model()
    model.pop("masses"), model.pop("massSource")
    u = 0.0
    checks = []
    for s in range(3):
        checks.append(_drift(f"EX-D{s + 1}", f"Story {s + 1} drift ratio", "EX", f"Story{s + 1}", "X",
                             V[s] / (k * h), f"V{s + 1} / (k h), V{s + 1} = {V[s]:g}"))
        u += V[s] / k
    checks.append(_j("EX-UX", "Roof displacement", "EX", [0.0, 0.0, 3 * h], "UX", u, "sum V / k"))
    checks.append(_b("EX-FX", "Base shear", "EX", "FX", -sum(F), "-sum F"))
    return dict({
        "id": "O1", "title": "Story drifts",
        "purpose": "Story drift ratios from the story shears of a shear building: drift = V / (k h).",
        "units": "kip-ft", "settings": dict(_COMMON, diaphragm="rigid, each floor",
                                            supports="fixed bases; floor joints fixed in rotation"),
        "patterns": [{"name": "EX"}],
        "pointLoads": [{"pattern": "EX", "at": [x, y, (s + 1) * h], "f": [F[s] / 4, 0, 0, 0, 0, 0]}
                       for s in range(3) for x, y in corners],
        "checks": checks,
    }, **model)


def O3() -> dict:
    """A linear combination and an envelope on a simply supported beam."""
    E, I, L, wd, wl = STEEL_E, 0.03, 30.0, 1.0, 0.5
    mid = [L / 2, 0.0, 12.0]
    dD, dL = 5 * wd * L**4 / (384 * E * I), 5 * wl * L**4 / (384 * E * I)
    return {
        "id": "O3", "title": "Combinations and envelopes",
        "purpose": ("1.2D + 1.6L adds the cases; an envelope of D and L keeps the largest and smallest of each "
                    "value, reported here as its maximum and minimum."),
        "units": "kip-ft", "settings": dict(_COMMON, supports="pin and roller"),
        "materials": _mat(), "sections": [_beam_section()],
        "stories": [{"name": "Story1", "height": 12.0}],
        "frames": [{"id": "B1", "i": [0.0, 0.0, 12.0], "j": mid, "section": "BM", "angle": 0.0},
                   {"id": "B2", "i": mid, "j": [L, 0.0, 12.0], "section": "BM", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 12.0], "restraint": [1, 1, 1, 1, 0, 0]},
                     {"at": [L, 0.0, 12.0], "restraint": [0, 1, 1, 1, 0, 0]}],
        "patterns": [{"name": "D"}, {"name": "L"}],
        "distLoads": [{"pattern": pt, "frame": f, "w": w} for pt, w in (("D", wd), ("L", wl)) for f in ("B1", "B2")],
        "combos": [{"name": "C1", "type": "linear", "cases": {"D": 1.2, "L": 1.6}},
                   {"name": "ENV", "type": "envelope", "cases": {"D": 1.0, "L": 1.0}}],
        "checks": [
            _j("C1-UZ", "1.2D + 1.6L: midspan deflection", "C1", mid, "UZ", -(1.2 * dD + 1.6 * dL),
               "-(1.2 dD + 1.6 dL)"),
            _f("C1-M", "1.2D + 1.6L: midspan moment", "C1", "B1", "j", "M3", (1.2 * wd + 1.6 * wl) * L**2 / 8,
               "(1.2 wD + 1.6 wL) L^2 / 8"),
            _b("C1-FZ", "1.2D + 1.6L: total reaction", "C1", "FZ", (1.2 * wd + 1.6 * wl) * L, "(1.2 wD + 1.6 wL) L"),
            _j("ENV-UZmax", "Envelope maximum of UZ (the smaller sag, L)", "ENV", mid, "UZ", -dL, "-dL"),
            _j("ENV-UZmin", "Envelope minimum of UZ (the larger sag, D)", "ENV:min", mid, "UZ", -dD, "-dD"),
            _b("ENV-FZmax", "Envelope maximum of the reaction", "ENV", "FZ", wd * L, "wD L"),
            _b("ENV-FZmin", "Envelope minimum of the reaction", "ENV:min", "FZ", wl * L, "wL L"),
        ],
    }


CASES = {"D1": D1, "D3": D3, "S1": S1, "S2": S2, "M1": M1, "M2": M2, "M3": M3, "M4": M4, "O1": O1, "O3": O3}
