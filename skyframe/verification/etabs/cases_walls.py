"""Walls and slabs W1-W6, the semi-rigid floor D2 and pier forces O2 (kip, ft).

Shell models are meshed identically in both programs: a meshed area is drawn in ETABS as n1 x n2
separate area objects, and SkyFrame meshes the same area n1 x n2.  No element is larger than 4 ft,
so ETABS's automatic wall and floor meshing has nothing left to divide (the run log reports the
ETABS element count).  Keys added to the earlier groups:

    areas[].pier            pier label (walls)
    areaSections[].shell    "thin" (Kirchhoff plate bending) for every shell here
    edgeConstraints         true: mesh nodes on another area's edge are tied to that edge
    checks quantity "pier"  {story, name, location "bottom", comp P | V2 | M3}

Hand values for shells come from beam or plate theory; the finite-element answer converges to the
elasticity solution, which differs from beam theory by a little (stated per check as its tolerance).
"""

from __future__ import annotations

import math

from defs import _COMMON, _b, _j

CONC_E = 4000.0 * 144.0            # ksf
CONC_NU = 0.2
CONC_G = CONC_E / (2 * (1 + CONC_NU))
_SHELL = dict(_COMMON, shells="Shell-Thin, explicit meshes no larger than 4 ft, identical in both programs",
              selfWeight="none (unit weight 0)")


def _conc():
    return [{"name": "CONC", "E": CONC_E, "nu": CONC_NU, "unitWeight": 0.0}]


def _pier(cid, label, case, story, name, comp, hand, formula, how="abs", **kw):
    return dict({"id": cid, "label": label, "case": case, "quantity": "pier", "story": story, "name": name,
                 "location": "bottom", "comp": comp, "hand": hand, "formula": formula, "how": how}, **kw)


def _wall_cells(prefix, xs, zs, y=0.0, skip=lambda x0, x1, z0, z1: False, pier=lambda x0, x1, z0, z1: ""):
    """One area per grid cell of a wall in the XZ plane at y; skip() leaves openings."""
    out = []
    for i, (x0, x1) in enumerate(zip(xs, xs[1:])):
        for k, (z0, z1) in enumerate(zip(zs, zs[1:])):
            if skip(x0, x1, z0, z1):
                continue
            a = {"id": f"{prefix}{i + 1}_{k + 1}", "section": "WALL",
                 "corners": [[x0, y, z0], [x1, y, z0], [x1, y, z1], [x0, y, z1]]}
            p = pier(x0, x1, z0, z1)
            if p:
                a["pier"] = p
            out.append(a)
    return out


def _edge_loads(pattern, pts, total, comp):
    """A total force spread over equally spaced nodes as a uniform line load (half shares at the ends)."""
    n = len(pts) - 1
    out = []
    for k, p in enumerate(pts):
        share = total / n * (0.5 if k in (0, n) else 1.0)
        f = [0.0] * 6
        f[comp] = share
        out.append({"pattern": pattern, "at": p, "f": f})
    return out


def _grid(a, b, n):
    return [a + (b - a) * k / n for k in range(n + 1)]


def W1() -> dict:
    """A cantilever wall, 8 ft wide and 24 ft tall, at three mesh sizes side by side."""
    b, H, t, P = 8.0, 24.0, 1.0, 100.0
    I, A = t * b**3 / 12, t * b
    hand = P * H**3 / (3 * CONC_E * I) + P * H / (5 / 6 * CONC_G * A)
    meshes = [(2, 6), (4, 12), (8, 24)]                 # 4 ft, 2 ft and 1 ft elements
    areas, supports, loads, checks = [], [], [], []
    for k, (n1, n2) in enumerate(meshes):
        x0, pat = 30.0 * k, f"P{n1}"
        areas.append({"id": f"W{n1}", "section": "WALL", "mesh": [n1, n2],
                      "corners": [[x0, 0, 0], [x0 + b, 0, 0], [x0 + b, 0, H], [x0, 0, H]]})
        xs = _grid(x0, x0 + b, n1)
        supports += [{"at": [x, 0.0, 0.0], "restraint": [1] * 6} for x in xs]
        loads += _edge_loads(pat, [[x, 0.0, H] for x in xs], P, 0)
        size = b / n1
        tol = [0.06, 0.04] if n1 == 2 else [0.03, 0.02]
        checks.append(dict(_j(f"{pat}-UX", f"Top displacement, {size:g} ft mesh", pat, [x0 + b, 0.0, H], "UX", hand,
                              "PH^3/3EI + PH/(5/6 G A), Timoshenko beam"), tol=tol))
        checks.append(_b(f"{pat}-MY", f"Base moment, {size:g} ft mesh", pat, "MY", -P * H, "-P H"))
    return {
        "id": "W1", "title": "Cantilever wall, three meshes",
        "purpose": ("A wall loaded in its plane at the top, meshed at 4, 2 and 1 ft. The displacement converges "
                    "towards the bending-plus-shear answer; the base reactions are exact at any mesh."),
        "units": "kip-ft", "settings": dict(_SHELL, supports="every base node fixed",
                                            load="100 kip at the top, spread evenly along the top edge"),
        "materials": _conc(),
        "sections": [],
        "areaSections": [{"name": "WALL", "material": "CONC", "thickness": t, "kind": "wall", "shell": "thin"}],
        "stories": [{"name": "Story1", "height": 12.0}, {"name": "Story2", "height": 12.0}],
        "frames": [], "areas": areas, "supports": supports,
        "patterns": [{"name": f"P{n1}"} for n1, _ in meshes],
        "pointLoads": loads,
        "checks": checks,
    }


def W2() -> dict:
    """A one-story wall with a centred door: two piers share the shear equally."""
    xs, zs = _grid(0.0, 20.0, 10), _grid(0.0, 12.0, 6)
    door = lambda x0, x1, z0, z1: x0 >= 8.0 - 1e-9 and x1 <= 12.0 + 1e-9 and z1 <= 8.0 + 1e-9
    pier = lambda x0, x1, z0, z1: "P1" if x1 <= 8.0 + 1e-9 else ("P2" if x0 >= 12.0 - 1e-9 else "")
    areas = _wall_cells("A", xs, zs, skip=door, pier=pier)
    P = 50.0
    return {
        "id": "W2", "title": "Wall with a door",
        "purpose": ("A 20 ft wall with a centred 4 x 8 ft door, loaded along its top. The wall is symmetric and "
                    "the load antisymmetric, so the two piers carry equal shears; their axial forces are equal "
                    "and opposite."),
        "units": "kip-ft", "settings": dict(_SHELL, supports="every base node fixed", piers="P1 left, P2 right"),
        "materials": _conc(), "sections": [],
        "areaSections": [{"name": "WALL", "material": "CONC", "thickness": 1.0, "kind": "wall", "shell": "thin"}],
        "stories": [{"name": "Story1", "height": 12.0}],
        "frames": [], "areas": areas,
        "supports": [{"at": [x, 0.0, 0.0], "restraint": [1] * 6} for x in xs if not 8.0 < x < 12.0],
        "patterns": [{"name": "H"}],
        "pointLoads": _edge_loads("H", [[x, 0.0, 12.0] for x in xs], P, 0),
        "checks": [
            _pier("H-V1", "Left pier shear", "H", "Story1", "P1", "V2", P / 2, "V / 2 (symmetry)"),
            _pier("H-V2", "Right pier shear", "H", "Story1", "P2", "V2", P / 2, "V / 2 (symmetry)"),
            _pier("H-P1", "Left pier axial (equal and opposite to the right)", "H", "Story1", "P1", "P", None,
                  "programs compared"),
            _pier("H-M1", "Left pier moment at its base", "H", "Story1", "P1", "M3", None, "programs compared"),
            dict(_j("H-UX", "Top corner displacement", "H", [20.0, 0.0, 12.0], "UX", None, "programs compared"),
                 knownDifference=("at this 2 ft mesh both programs are stiffer than the converged answer "
                                  "(about 4.33e-4 ft, refining SkyFrame to 0.5 ft): SkyFrame 6% short, ETABS 9%, "
                                  "the door corners need a finer mesh; pier forces agree")),
            _b("H-FX", "Base shear", "H", "FX", -P, "-V"),
            _b("H-MY", "Base moment", "H", "MY", -P * 12.0, "-V h"),
        ],
    }


def W3() -> dict:
    """Coupled walls: two 6 ft piers joined by 3 ft deep coupling beams over three stories."""
    h, ns = 12.0, 3
    xs = _grid(0.0, 6.0, 4) + _grid(6.0, 9.0, 2)[1:] + _grid(9.0, 15.0, 4)[1:]
    zs = _grid(0.0, h * ns, 8 * ns)
    opening = lambda x0, x1, z0, z1: x0 >= 6.0 - 1e-9 and x1 <= 9.0 + 1e-9 and z0 % h < 9.0 - 1e-9
    pier = lambda x0, x1, z0, z1: "PL" if x1 <= 6.0 + 1e-9 else ("PR" if x0 >= 9.0 - 1e-9 else "")
    areas = _wall_cells("C", xs, zs, skip=opening, pier=pier)
    F = [10.0, 20.0, 30.0]
    loads = []
    for s in range(ns):
        loads += _edge_loads("EQ", [[x, 0.0, h * (s + 1)] for x in xs], F[s], 0)
    V = [sum(F[s:]) for s in range(ns)]
    checks = []
    for s in range(ns):
        st = f"Story{s + 1}"
        checks.append(_pier(f"EQ-VL{s + 1}", f"{st} left pier shear", "EQ", st, "PL", "V2", V[s] / 2,
                            f"V{s + 1} / 2 (symmetry), V{s + 1} = {V[s]:g}"))
        checks.append(_pier(f"EQ-PL{s + 1}", f"{st} left pier axial: the coupling beam shears above", "EQ", st,
                            "PL", "P", None, "programs compared"))
        checks.append(_pier(f"EQ-ML{s + 1}", f"{st} left pier moment at the story bottom", "EQ", st, "PL", "M3",
                            None, "programs compared"))
    checks += [_j("EQ-UX", "Roof displacement", "EQ", [15.0, 0.0, h * ns], "UX", None, "programs compared"),
               _b("EQ-FX", "Base shear", "EQ", "FX", -sum(F), "-sum F"),
               _b("EQ-MY", "Base moment", "EQ", "MY", -sum(F[s] * h * (s + 1) for s in range(ns)), "-sum F z")]
    return {
        "id": "W3", "title": "Coupled walls",
        "purpose": ("Two piers linked by coupling beams at each floor. Symmetry splits each story shear equally; "
                    "a pier's axial force equals the sum of the coupling-beam shears above it, which depends on "
                    "the beams' stiffness and is compared between the programs."),
        "units": "kip-ft", "settings": dict(_SHELL, supports="every base node fixed",
                                            piers="PL left, PR right; openings 3 ft wide, 9 ft tall"),
        "materials": _conc(), "sections": [],
        "areaSections": [{"name": "WALL", "material": "CONC", "thickness": 1.0, "kind": "wall", "shell": "thin"}],
        "stories": [{"name": f"Story{s + 1}", "height": h} for s in range(ns)],
        "frames": [], "areas": areas,
        "supports": [{"at": [x, 0.0, 0.0], "restraint": [1] * 6} for x in xs if not 6.0 < x < 9.0],
        "patterns": [{"name": "EQ"}],
        "pointLoads": loads,
        "checks": checks,
    }


def W4() -> dict:
    """A C-shaped core twisted by a torque at the top, through a rigid roof diaphragm."""
    H, T = 24.0, 500.0
    ys, xs, zs = _grid(0.0, 12.0, 4), _grid(0.0, 6.0, 2), _grid(0.0, H, 8)
    areas = [{"id": "WEB", "section": "WALL", "mesh": [4, 8],
              "corners": [[0, 0, 0], [0, 12, 0], [0, 12, H], [0, 0, H]]},
             {"id": "FL1", "section": "WALL", "mesh": [2, 8], "corners": [[0, 0, 0], [6, 0, 0], [6, 0, H], [0, 0, H]]},
             {"id": "FL2", "section": "WALL", "mesh": [2, 8],
              "corners": [[0, 12, 0], [6, 12, 0], [6, 12, H], [0, 12, H]]}]
    base = {(0.0, y) for y in ys} | {(x, 0.0) for x in xs} | {(x, 12.0) for x in xs}
    top = [[x, y, H] for x, y in sorted(base)]
    return {
        "id": "W4", "title": "C-core under torsion",
        "purpose": ("An open C-section core is weak in torsion and warps. Base torque balances the load exactly; "
                    "the twist depends on warping restraint at the fixed base and is compared between programs."),
        "units": "kip-ft", "settings": dict(_SHELL, supports="every base node fixed", diaphragm="rigid at the roof"),
        "materials": _conc(), "sections": [],
        "areaSections": [{"name": "WALL", "material": "CONC", "thickness": 1.0, "kind": "wall", "shell": "thin"}],
        "stories": [{"name": "Story1", "height": 12.0}, {"name": "Story2", "height": 12.0}],
        "frames": [], "areas": areas,
        "supports": [{"at": [x, y, 0.0], "restraint": [1] * 6} for x, y in sorted(base)],
        "diaphragms": [{"name": "ROOF", "points": top}],
        "patterns": [{"name": "T"}],
        "pointLoads": [{"pattern": "T", "at": [0.0, 0.0, H], "f": [0, 0, 0, 0, 0, T]}],
        "checks": [
            _j("T-RZ", "Roof twist", "T", [0.0, 0.0, H], "RZ", None, "programs compared"),
            _j("T-UX", "Flange tip moves along X", "T", [6.0, 12.0, H], "UX", None, "programs compared"),
            _b("T-MZ", "Base torque", "T", "MZ", -T, "-T"),
            dict(_b("T-FX", "No net base shear", "T", "FX", 0.0, "0"), scale=T / 12.0),
        ],
    }


def W5() -> dict:
    """Square plates under uniform load: simply supported, and clamped (Timoshenko's coefficients)."""
    a, t, q, n = 20.0, 0.5, 0.2, 10
    z = 12.0                                   # off the Base level (ETABS pins Base joints)
    D = CONC_E * t**3 / (12 * (1 - CONC_NU**2))
    areas, supports = [], []
    for k, (name, rest) in enumerate((("SS", [1, 1, 1, 0, 0, 0]), ("CL", [1] * 6))):
        x0 = 30.0 * k
        areas.append({"id": name, "section": "PLATE", "mesh": [n, n],
                      "corners": [[x0, 0, z], [x0 + a, 0, z], [x0 + a, a, z], [x0, a, z]]})
        g = _grid(0.0, a, n)
        edge = {(x, y) for x in g for y in (0.0, a)} | {(x, y) for y in g for x in (0.0, a)}
        supports += [{"at": [x0 + x, y, z], "restraint": rest} for x, y in sorted(edge)]
    return {
        "id": "W5", "title": "Plates under uniform load",
        "purpose": ("Out-of-plane bending of a 20 ft square, 6 in plate (span / thickness 40, thin), meshed 10 x "
                    "10: centre deflection 0.00406 q a^4 / D simply supported and 0.00126 q a^4 / D clamped."),
        "units": "kip-ft", "settings": dict(_SHELL, supports="every edge node: pinned (SS) or fixed (CL)"),
        "materials": _conc(), "sections": [],
        "areaSections": [{"name": "PLATE", "material": "CONC", "thickness": t, "kind": "slab", "shell": "thin"}],
        "stories": [{"name": "Story1", "height": 12.0}],
        "frames": [], "areas": areas, "supports": supports,
        "patterns": [{"name": "QSS"}, {"name": "QCL"}],
        "areaLoads": [{"pattern": "QSS", "area": "SS", "q": q}, {"pattern": "QCL", "area": "CL", "q": q}],
        "checks": [
            dict(_j("QSS-UZ", "Simply supported: centre deflection", "QSS", [a / 2, a / 2, z], "UZ",
                    -0.00406235 * q * a**4 / D, "-0.00406 q a^4 / D (Navier)"), tol=[0.02, 0.02]),
            _b("QSS-FZ", "Simply supported: total reaction", "QSS", "FZ", q * a * a, "q a^2"),
            dict(_j("QCL-UZ", "Clamped: centre deflection (10 x 10 mesh is about 3% flexible, converging)", "QCL",
                    [30.0 + a / 2, a / 2, z], "UZ", -0.00126532 * q * a**4 / D, "-0.00127 q a^4 / D"),
                 tol=[0.03, 0.03]),
            _b("QCL-FZ", "Clamped: total reaction", "QCL", "FZ", q * a * a, "q a^2"),
        ],
    }


def W6() -> dict:
    """A slab on a wall whose meshes do not line up along the joint: edge constraints tie them."""
    L, q = 12.0, 0.1
    wall = {"id": "WALL", "section": "WALL", "mesh": [3, 3], "corners": [[0, 0, 0], [L, 0, 0], [L, 0, L], [0, 0, L]]}
    slab = {"id": "SLAB", "section": "SLAB", "mesh": [4, 4], "corners": [[0, 0, L], [L, 0, L], [L, L, L], [0, L, L]]}
    far = [[x, L, L] for x in _grid(0.0, L, 4)]
    return {
        "id": "W6", "title": "Mismatched wall and slab meshes",
        "purpose": ("A slab spans 12 ft from a wall to a line of supports. The wall's top edge has 3 elements and "
                    "the slab's edge 4, so only the corners coincide; edge constraints carry the load across."),
        "units": "kip-ft", "settings": dict(_SHELL, edgeConstraints="on in both programs",
                                            supports="wall base fixed; far slab edge pinned"),
        "materials": _conc(), "sections": [],
        "areaSections": [{"name": "WALL", "material": "CONC", "thickness": 1.0, "kind": "wall", "shell": "thin"},
                         {"name": "SLAB", "material": "CONC", "thickness": 0.5, "kind": "slab", "shell": "thin"}],
        "stories": [{"name": "Story1", "height": L}],
        "frames": [], "areas": [wall, slab], "edgeConstraints": True,
        "supports": [{"at": [x, 0.0, 0.0], "restraint": [1] * 6} for x in _grid(0.0, L, 3)]
                    + [{"at": p, "restraint": [1, 1, 1, 0, 0, 0]} for p in far],
        "patterns": [{"name": "Q"}],
        "areaLoads": [{"pattern": "Q", "area": "SLAB", "q": q}],
        "checks": [
            _b("Q-FZ", "Total reaction", "Q", "FZ", q * L * L, "q L^2"),
            dict(_j("Q-UZ", "Slab deflection at mid-span, mid-width", "Q", [L / 2, L / 2, L], "UZ", None,
                    "programs compared"), tol=[1e-3, 0.05],
                 note="ETABS re-cuts the slab at the wall's nodes (27 elements for 25 objects), SkyFrame ties "
                      "the meshes with edge constraints: the meshes differ, so 5% is allowed"),
            dict(_j("Q-UZw", "Wall top settles between the meshes", "Q", [L / 2, 0.0, L], "UZ", None,
                    "programs compared"), tol=[1e-3, 0.05]),
        ],
    }


def D2() -> dict:
    """A long floor plate loaded in its own plane: a semi-rigid diaphragm acting as a deep beam."""
    L, d, t, w = 60.0, 20.0, 0.5, 1.0
    I, A = t * d**3 / 12, t * d
    hand = 5 * w * L**4 / (384 * CONC_E * I) + w * L * L / (8 * 5 / 6 * CONC_G * A)
    ys = _grid(0.0, d, 5)
    z = 12.0                                   # off the Base level (ETABS pins Base joints)
    sup = [{"at": [0.0, y, z], "restraint": [1 if y == 0.0 else 0, 1, 1, 0, 0, 0]} for y in ys]
    sup += [{"at": [L, y, z], "restraint": [0, 1, 1, 0, 0, 0]} for y in ys]
    return {
        "id": "D2", "title": "Semi-rigid floor",
        "purpose": ("A 60 x 20 ft floor with no diaphragm constraint, held at its short edges and loaded along "
                    "its long edge: the floor bends and shears in its plane like a deep beam."),
        "units": "kip-ft", "settings": dict(_SHELL, diaphragm="none: the shell membrane stiffness only (semi-rigid)",
                                            supports="short edges held across the span"),
        "materials": _conc(), "sections": [],
        "areaSections": [{"name": "FLOOR", "material": "CONC", "thickness": t, "kind": "slab", "shell": "thin"}],
        "stories": [{"name": "Story1", "height": 12.0}],
        "frames": [],
        "areas": [{"id": "FL", "section": "FLOOR", "mesh": [20, 5],
                   "corners": [[0, 0, z], [L, 0, z], [L, d, z], [0, d, z]]}],
        "supports": sup,
        "patterns": [{"name": "WY"}],
        "pointLoads": _edge_loads("WY", [[x, 0.0, z] for x in _grid(0.0, L, 20)], w * L, 1),
        "checks": [
            dict(_j("WY-UY", "Midspan in-plane deflection, far edge", "WY", [L / 2, d, z], "UY", hand,
                    "5wL^4/384EI + wL^2/(8 5/6 G A), deep-beam theory (approximate)"), tol=[0.05, 0.02]),
            _b("WY-FY", "Total reaction", "WY", "FY", -w * L, "-w L"),
        ],
    }


def O2() -> dict:
    """Pier forces of a two-story wall from statics."""
    b, h = 12.0, 12.0
    F2, F1 = 30.0, 20.0
    areas = [{"id": f"W{s + 1}", "section": "WALL", "mesh": [3, 3], "pier": "P1",
              "corners": [[0, 0, s * h], [b, 0, s * h], [b, 0, (s + 1) * h], [0, 0, (s + 1) * h]]} for s in range(2)]
    xs = _grid(0.0, b, 3)
    loads = _edge_loads("EQ", [[x, 0.0, 2 * h] for x in xs], F2, 0) + _edge_loads("EQ", [[x, 0.0, h] for x in xs], F1, 0)
    return {
        "id": "O2", "title": "Pier forces",
        "purpose": ("Pier shear and moment at the bottom of each story follow from the loads above it: an exact "
                    "statics check of how each program cuts a wall into story piers."),
        "units": "kip-ft", "settings": dict(_SHELL, supports="every base node fixed", piers="P1, both stories"),
        "materials": _conc(), "sections": [],
        "areaSections": [{"name": "WALL", "material": "CONC", "thickness": 1.0, "kind": "wall", "shell": "thin"}],
        "stories": [{"name": "Story1", "height": h}, {"name": "Story2", "height": h}],
        "frames": [], "areas": areas,
        "supports": [{"at": [x, 0.0, 0.0], "restraint": [1] * 6} for x in xs],
        "patterns": [{"name": "EQ"}],
        "pointLoads": loads,
        "checks": [
            _pier("EQ-V2", "Story 2 pier shear", "EQ", "Story2", "P1", "V2", F2, "F2"),
            _pier("EQ-M2", "Story 2 pier moment at its bottom", "EQ", "Story2", "P1", "M3", F2 * h, "F2 h"),
            _pier("EQ-V1", "Story 1 pier shear", "EQ", "Story1", "P1", "V2", F1 + F2, "F1 + F2"),
            _pier("EQ-M1", "Story 1 pier moment at the base", "EQ", "Story1", "P1", "M3", F2 * 2 * h + F1 * h,
                  "F2 2h + F1 h"),
            _pier("EQ-P1", "No axial force", "EQ", "Story1", "P1", "P", 0.0, "0", scale=F1 + F2),
        ],
    }


CASES = {"W1": W1, "W2": W2, "W3": W3, "W4": W4, "W5": W5, "W6": W6, "D2": D2, "O2": O2}
