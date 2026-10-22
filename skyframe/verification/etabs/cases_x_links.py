"""Links and shells group K1-K4 (kip, ft): a hook link, plate moments, a section cut, a
nonlinear layered wall.  Every case names "ext": "links", so sf_ext_links.py (SkyFrame) and
etabs_ext_links.py (ETABS) build what the stock keys do not cover.  Keys added here:

    hookLinks       [{id, i, j, k, slack}]   tension-only hook along i -> j: engages once the
                    pair has OPENED by ``slack`` (ft), force k (opening - slack) (kip/ft)
    shellPoints     {key: {area, at, comp}}  a shell resultant (comp M11 | M22, local axes, kip-ft/ft)
                    at a mesh node, averaged over the elements that share it; exported as
                    res["values"][key]
    sectionCuts     [{name, area, z, at, case, keys {F: key}}]  a horizontal cut through a wall
                    at elevation z (a mesh node line): the resultant of the elements above the line
                    on the line's nodes, moments about ``at``; exported as res["values"][key]
    layeredWalls    {section: {fy, steel_t, layers}}  a nonlinear layered wall section: a vertical
                    steel layer (elastic-perfectly-plastic, yield fy, ksf) carries the vertical
                    stress, a second layer carries the horizontal and shear stress elastically
    checks quantity "value"  (defs._v): a named scalar both programs export
"""

from __future__ import annotations

import math

from defs import STEEL_E, STEEL_NU, _COMMON, _b, _beam_section, _f, _j, _mat, _v

from cases_walls import CONC_E, _SHELL, _edge_loads, _grid


# ── K1: hook (tension-only) link ────────────────────────────────────────────
def K1() -> dict:
    """A hook (tension-only, with slack) tie-down under a cantilever tip: the mirror of N5's gap."""
    E, I, L, w, P, Ps, k, s = STEEL_E, 0.03, 20.0, 1.0, 10.0, 2.0, 1.0e5, 0.05
    EI = E * I
    f = L**3 / (3 * EI)                      # tip flexibility of the cantilever
    d0 = P * f                                # free uplift of the tip under P
    R = (d0 - s) / (f + 1 / k)                # hook force once the slack is taken up
    tip, low = [L, 0.0, 12.0], [L, 0.0, 11.0]
    return {
        "id": "K1", "title": "Hook link (tie-down)",
        "purpose": ("A cantilever whose tip is held down by a tension-only hook link (k = 1e5 k/ft) with "
                    "0.05 ft of slack, the mirror of N5's gap. Under gravity the hook goes slack and the "
                    "cantilever is free; under a small uplift the tip rises less than the slack and the hook "
                    "stays slack; under a larger uplift the slack is taken up and the hook carries "
                    "(PL^3/3EI - slack) / (L^3/3EI + 1/k). Nonlinear static cases in both programs."),
        "units": "kip-ft", "ext": "links",
        "settings": dict(_COMMON, nonlinear="hook link (tension only, opening 0.05 ft), nonlinear static",
                         supports="fixed at x = 0; hook link to ground below the tip"),
        "materials": _mat(), "sections": [_beam_section()],
        "stories": [{"name": "Story1", "height": 12.0}],
        "frames": [{"id": "B1", "i": [0.0, 0.0, 12.0], "j": tip, "section": "BM", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 12.0], "restraint": [1] * 6}, {"at": tip, "restraint": [0, 1, 0, 1, 0, 1]},
                     {"at": low, "restraint": [1] * 6}],
        "hookLinks": [{"id": "H1", "i": low, "j": tip, "k": k, "slack": s}],
        "patterns": [{"name": "D"}, {"name": "S"}, {"name": "U"}],
        "distLoads": [{"pattern": "D", "frame": "B1", "w": w}],
        "pointLoads": [{"pattern": "S", "at": tip, "f": [0, 0, Ps, 0, 0, 0]},
                       {"pattern": "U", "at": tip, "f": [0, 0, P, 0, 0, 0]}],
        "nlCases": [{"name": "NLD", "loads": {"D": 1.0}, "geometry": "none"},
                    {"name": "NLS", "loads": {"S": 1.0}, "geometry": "none"},
                    {"name": "NLU", "loads": {"U": 1.0}, "geometry": "none"}],
        "checks": [
            _j("NLD-UZ", "Gravity: hook slack, free cantilever", "NLD", tip, "UZ", -w * L**4 / (8 * EI), "-wL^4 / 8EI"),
            dict(_f("NLD-V", "Gravity: no hook force (tip shear 0)", "NLD", "B1", "j", "V2", 0.0, "0"), scale=w * L),
            _j("NLS-UZ", "Small uplift: rises less than the slack, hook slack", "NLS", tip, "UZ", Ps * f,
               "Ps L^3 / 3EI (< slack)"),
            dict(_f("NLS-V", "Small uplift: tip shear is the load alone", "NLS", "B1", "j", "V2", Ps, "Ps"), scale=Ps),
            _j("NLU-UZ", "Uplift: slack taken up, hook stretched", "NLU", tip, "UZ", s + R / k, "slack + R / k"),
            _f("NLU-V", "Uplift: tip shear P - R (hook force R)", "NLU", "B1", "j", "V2", P - R,
               "P - (PL^3/3EI - slack) / (L^3/3EI + 1/k)"),
            _f("NLU-M", "Uplift: fixed-end moment (P - R) L", "NLU", "B1", "i", "M3", (P - R) * L, "(P - R) L"),
            _b("NLU-FZ", "Uplift: total reaction", "NLU", "FZ", -P, "-P"),
        ],
    }


# ── K2: plate bending moments ───────────────────────────────────────────────
PLATE_NU = 0.3                           # Timoshenko's tables are for nu = 0.3


def _navier(a: float, b: float, nu: float, terms: int = 801):
    """Centre deflection and moments of a simply supported a x b plate under uniform q (Navier's
    double series), as coefficients: w = cw q a^4 / D, Mx = cx q a^2, My = cy q a^2."""
    sw = sx = sy = 0.0
    for m in range(1, terms + 1, 2):
        for n in range(1, terms + 1, 2):
            sg = (-1) ** ((m - 1) // 2 + (n - 1) // 2)
            am, bn = (m / a) ** 2, (n / b) ** 2
            W = 16.0 / (math.pi**6 * m * n * (am + bn) ** 2)          # x q / D
            sw += sg * W
            sx += sg * W * math.pi**2 * (am + nu * bn)                  # x q (D cancels)
            sy += sg * W * math.pi**2 * (nu * am + bn)
    return sw / a**4, sx / a**2, sy / a**2


def K2() -> dict:
    """A simply supported 2:1 plate under uniform load: centre M11 and M22 (Timoshenko, Navier)."""
    a, b, t, q, z = 16.0, 32.0, 0.5, 0.2, 12.0      # a along X (short span), b along Y
    E, nu = CONC_E, PLATE_NU
    D = E * t**3 / (12 * (1 - nu**2))
    cw, cx, cy = _navier(a, b, nu)                  # 0.01013, 0.1017, 0.0464 in Timoshenko's Table 8
    n1, n2 = 16, 32                                 # 1 ft elements
    g1, g2 = _grid(0.0, a, n1), _grid(0.0, b, n2)
    edge = {(x, y) for x in g1 for y in (0.0, b)} | {(x, y) for y in g2 for x in (0.0, a)}
    centre = [a / 2, b / 2, z]
    tol = [0.01, 0.01]
    note = ("1 ft mesh: SkyFrame averages the four element-centre values round the node (0.5 ft away), "
            "ETABS extrapolates to the node; both within 1% of Navier")
    return {
        "id": "K2", "title": "Plate bending moments",
        "purpose": ("A 16 x 32 ft simply supported plate, 6 in thick (nu = 0.3), under 0.2 ksf, meshed at 1 ft: "
                    "the centre moments across the short span (M11, along X) and the long span (M22, along Y) "
                    "and the centre deflection against Navier's series, which gives Timoshenko's "
                    "coefficients 0.1017 q a^2, 0.0464 q a^2 and 0.01013 q a^4 / D for b / a = 2. A 2:1 "
                    "plate so that M11 and M22 differ and a swapped local axis cannot hide."),
        "units": "kip-ft", "ext": "links",
        "settings": dict(_SHELL, supports="every edge node pinned (simply supported)",
                         shells="Shell-Thin, 16 x 32 elements of 1 ft, identical in both programs",
                         moments="local axes: 1 = +X, 2 = +Y; magnitudes compared (sign conventions differ)"),
        "materials": [{"name": "PLATE", "E": E, "nu": nu, "unitWeight": 0.0}],
        "sections": [],
        "areaSections": [{"name": "PLT", "material": "PLATE", "thickness": t, "kind": "slab", "shell": "thin"}],
        "stories": [{"name": "Story1", "height": z}],
        "frames": [],
        "areas": [{"id": "PL", "section": "PLT", "mesh": [n1, n2],
                   "corners": [[0, 0, z], [a, 0, z], [a, b, z], [0, b, z]]}],
        "supports": [{"at": [x, y, z], "restraint": [1, 1, 1, 0, 0, 0]} for x, y in sorted(edge)],
        "patterns": [{"name": "Q"}],
        "areaLoads": [{"pattern": "Q", "area": "PL", "q": q}],
        "shellPoints": {"K2-M11": {"area": "PL", "at": centre, "comp": "M11", "case": "Q"},
                        "K2-M22": {"area": "PL", "at": centre, "comp": "M22", "case": "Q"}},
        "checks": [
            dict(_j("Q-UZ", "Centre deflection", "Q", centre, "UZ", -cw * q * a**4 / D,
                    f"-{cw:.5f} q a^4 / D (Navier; Timoshenko 0.01013)"), tol=[0.005, 0.005]),
            dict(_v("Q-M11", "Centre moment across the short span, M11", "K2-M11", cx * q * a * a,
                    f"{cx:.5f} q a^2 (Navier; Timoshenko 0.1017)", how="abs"), tol=tol, note=note),
            dict(_v("Q-M22", "Centre moment across the long span, M22", "K2-M22", cy * q * a * a,
                    f"{cy:.5f} q a^2 (Navier; Timoshenko 0.0464)", how="abs"), tol=tol, note=note),
            _b("Q-FZ", "Total reaction", "Q", "FZ", q * a * b, "q a b"),
        ],
    }


# ── K3: section cut through a wall ──────────────────────────────────────────
def K3() -> dict:
    """A section cut across a cantilever wall at 9 ft: shear, axial force and moment from statics."""
    bw, H, zc = 12.0, 24.0, 9.0
    H1, H2, N = 40.0, 20.0, 120.0             # lateral at the top, lateral at 15 ft, gravity at the top
    z2 = 15.0
    xs = _grid(0.0, bw, 4)
    loads = (_edge_loads("H", [[x, 0.0, H] for x in xs], H1, 0) + _edge_loads("H", [[x, 0.0, z2] for x in xs], H2, 0)
             + _edge_loads("H", [[x, 0.0, H] for x in xs], -N, 2))
    V, P, M = H1 + H2, N, H1 * (H - zc) + H2 * (z2 - zc)
    return {
        "id": "K3", "title": "Section cut through a wall",
        "purpose": ("A 12 ft wide, 24 ft tall cantilever wall cut horizontally at 9 ft, between floors (stories "
                    "of 9 and 15 ft so the cut is also SkyFrame's pier line). Above the cut: 40 kips lateral at "
                    "the top, 20 kips at 15 ft and 120 kips of gravity at the top. The cut must carry "
                    "V = 60 kips, P = 120 kips and M = 40 x 15 + 20 x 6 = 720 kip-ft about the wall centre: "
                    "statics, exact at any mesh. ETABS reads it from a section cut (a group of the elements "
                    "above the line and the line's joints); SkyFrame from the same free body of the shell "
                    "nodal forces (its pier kernel)."),
        "units": "kip-ft", "ext": "links",
        "settings": dict(_SHELL, supports="every base node fixed",
                         sectionCut="horizontal, z = 9 ft, through the whole wall, moments about (6, 0, 9)",
                         signs="magnitudes compared (the programs report the cut from opposite sides)"),
        "materials": [{"name": "CONC", "E": CONC_E, "nu": 0.2, "unitWeight": 0.0}],
        "sections": [],
        "areaSections": [{"name": "WALL", "material": "CONC", "thickness": 1.0, "kind": "wall", "shell": "thin"}],
        "stories": [{"name": "Story1", "height": zc}, {"name": "Story2", "height": H - zc}],
        "frames": [],
        "areas": [{"id": "WL", "section": "WALL", "mesh": [4, 3], "pier": "P1",
                   "corners": [[0, 0, 0], [bw, 0, 0], [bw, 0, zc], [0, 0, zc]]},
                  {"id": "WU", "section": "WALL", "mesh": [4, 5], "pier": "P1",
                   "corners": [[0, 0, zc], [bw, 0, zc], [bw, 0, H], [0, 0, H]]}],
        "supports": [{"at": [x, 0.0, 0.0], "restraint": [1] * 6} for x in xs],
        "patterns": [{"name": "H"}],
        "pointLoads": loads,
        "sectionCuts": [{"name": "CUT9", "area": "WU", "z": zc, "at": [bw / 2, 0.0, zc], "case": "H",
                         "keys": {"V": "K3-V", "P": "K3-P", "M": "K3-M"}}],
        "checks": [
            _v("H-V", "Cut shear", "K3-V", V, "H1 + H2", how="abs"),
            _v("H-P", "Cut axial force (compression)", "K3-P", P, "N", how="abs"),
            _v("H-M", "Cut moment about the wall centre", "K3-M", M, "H1 (24 - 9) + H2 (15 - 9)", how="abs"),
            _b("H-FX", "Base shear", "H", "FX", -V, "-(H1 + H2)"),
            _b("H-MY", "Base moment about the origin", "H", "MY", -(H1 * H + H2 * z2) - N * bw / 2,
               "-(H1 24 + H2 15) - N b / 2"),
        ],
    }


# ── K4: nonlinear layered wall ──────────────────────────────────────────────
def K4() -> dict:
    """Pushover of a steel-plate cantilever wall (layered shell): the base reaches its plastic moment."""
    bw, H, ts, fy = 4.0, 16.0, 0.05, 50.0 * 144.0     # 4 ft wide, 16 ft tall, 0.6 in vertical steel, 50 ksi
    E = STEEL_E
    Mp = fy * ts * bw**2 / 4
    Hp = Mp / H
    dy = 2 * fy * H**2 / (3 * E * bw)                  # first-yield sway (bending only)
    xs = _grid(0.0, bw, 8)
    # finer rows at the base, where the wall yields: 1.5 in rows to 0.5 ft, 6 in to 2 ft, 1 ft above
    areas = [{"id": "W0", "section": "WNL", "mesh": [8, 4], "corners": [[0, 0, 0], [bw, 0, 0], [bw, 0, 0.5], [0, 0, 0.5]]},
             {"id": "W1", "section": "WNL", "mesh": [8, 3], "corners": [[0, 0, 0.5], [bw, 0, 0.5], [bw, 0, 2], [0, 0, 2]]},
             {"id": "W2", "section": "WNL", "mesh": [8, 14], "corners": [[0, 0, 2], [bw, 0, 2], [bw, 0, H], [0, 0, H]]}]
    top = [bw, 0.0, H]
    return {
        "id": "K4", "title": "Nonlinear layered wall",
        "purpose": ("A 4 ft wide, 16 ft tall cantilever wall built as a nonlinear layered shell: a 0.6 in steel "
                    "layer, elastic-perfectly-plastic at 50 ksi, carries the vertical stress; a second layer "
                    "carries the horizontal and shear stress elastically. Pushed at the top to 2.5% drift (about "
                    "5 times the first-yield sway) the base section turns fully plastic and the base shear "
                    "levels off at Mp / H = fy t b^2 / 4H = 90 kips."),
        "units": "kip-ft", "ext": "links",
        "settings": dict(_SHELL, shells=("nonlinear layered shell, 8 elements across, rows of 1.5 in at the base "
                                         "growing to 1 ft; identical meshes"),
                         nonlinear=("layer 1: steel, vertical (local 2) stress only, EPP fy = 50 ksi; layer 2: "
                                    "the same steel, horizontal (local 1) and shear stress, linear"),
                         pushover="displacement-controlled push at the top corner, no geometric nonlinearity",
                         supports="every base node fixed"),
        "materials": [{"name": "WSTEEL", "E": E, "nu": STEEL_NU, "unitWeight": 0.0}],
        "sections": [],
        "areaSections": [{"name": "WNL", "material": "WSTEEL", "thickness": ts, "kind": "wall", "shell": "thin"}],
        "layeredWalls": {"WNL": {"fy": fy, "steel_t": ts}},
        "stories": [{"name": "Story1", "height": H}],
        "frames": [], "areas": areas,
        "supports": [{"at": [x, 0.0, 0.0], "restraint": [1] * 6} for x in xs],
        "patterns": [{"name": "PUSH"}],
        "pointLoads": _edge_loads("PUSH", [[x, 0.0, H] for x in xs], 1.0, 0),
        "pushover": [{"name": "PO", "pattern": "PUSH", "control": top, "dir": "X", "target": 0.025 * H,
                      "steps": 50}],
        "checks": [
            dict(_b("PO-FX", "Base shear at the plastic moment", "PO", "FX", -Hp, "-fy t b^2 / 4H"),
                 tol=[0.01, 0.02],
                 note=(f"first yield at {dy:.3f} ft of sway; the base row of elements yields at its integration "
                       "points (a little above the base), so the finite-element plateau sits up to about 1% "
                       "above Mp / H")),
        ],
    }


CASES = {"K1": K1, "K2": K2, "K3": K3, "K4": K4}
