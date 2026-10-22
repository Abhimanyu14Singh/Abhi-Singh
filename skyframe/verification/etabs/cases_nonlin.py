"""Nonlinear and stability group N1-N3, and the speed benchmark SP1 (kip, ft).

Keys added to the earlier groups:

    frames[].tcLimit   "tension": the member carries no compression (tension-only brace; active in
                       nonlinear cases)
    bucklingCases      [{name, loads {pattern: factor}, modes}]   linear buckling
    nlCases            [{name, loads {pattern: factor}, geometry "none" | "pdelta" | "large"}]
                       nonlinear static from zero; "large" = P-Delta plus large displacements
    benchmark          {"linear": [case names], "nonlinear": [case names]}: the run is timed in two
                       parts, each program on the same machine (ETABS: advanced solver, all cores)
    checks quantity "buckling"  {mode, comp "factor"}
"""

from __future__ import annotations

import math

from defs import STEEL_E, STEEL_NU, _COMMON, _b, _col_section, _f, _j, _mat

from cases_dyn import _modal


def _buck(cid, label, case, mode, hand, formula, **kw):
    return dict({"id": cid, "label": label, "case": case, "quantity": "buckling", "mode": mode,
                 "comp": "factor", "hand": hand, "formula": formula, "how": "signed"}, **kw)


def _column(L, n, z0=0.0, section="COL"):
    pts = [[0.0, 0.0, z0 + L * i / n] for i in range(n + 1)]
    return pts, [{"id": f"C{i + 1}", "i": pts[i], "j": pts[i + 1], "section": section, "angle": 0.0}
                 for i in range(n)]


def N1() -> dict:
    """Linear buckling of a cantilever column, four frame objects."""
    E, L = STEEL_E, 12.0
    I33, I22 = 0.02, 0.005
    pts, frames = _column(L, 4)
    return {
        "id": "N1", "title": "Linear buckling",
        "purpose": ("Buckling factors of a cantilever under a 1 kip axial load: Euler's pi^2 EI / 4L^2 about "
                    "the weak axis first, then the strong axis. Four elements in both programs."),
        "units": "kip-ft", "settings": dict(_COMMON, buckling="linear buckling case, two modes",
                                            supports="fixed base"),
        "materials": _mat(), "sections": [_col_section()],
        "stories": [{"name": "Story1", "height": L}],
        "frames": frames, "supports": [{"at": pts[0], "restraint": [1] * 6}],
        "patterns": [{"name": "P"}],
        "pointLoads": [{"pattern": "P", "at": pts[-1], "f": [0, 0, -1.0, 0, 0, 0]}],
        "bucklingCases": [{"name": "BUCK", "loads": {"P": 1.0}, "modes": 2}],
        "checks": [
            _buck("B1", "First buckling factor (weak axis)", "BUCK", 1, math.pi**2 * E * I22 / (4 * L * L),
                  "pi^2 E I22 / 4L^2"),
            _buck("B2", "Second buckling factor (strong axis)", "BUCK", 2, math.pi**2 * E * I33 / (4 * L * L),
                  "pi^2 E I33 / 4L^2"),
        ],
    }


def N2() -> dict:
    """Tension-only X-bracing in a pin-jointed bay: the compression brace goes slack."""
    E, A, h, L, H = STEEL_E, 0.1, 12.0, 20.0, 10.0
    D = math.hypot(L, h)
    c, t = L / D, h / L
    rel = {"i": {"M2": 0.0, "M3": 0.0}, "j": {"M2": 0.0, "M3": 0.0}}
    bl, br, tl, tr = [0.0, 0.0, 0.0], [L, 0.0, 0.0], [0.0, 0.0, h], [L, 0.0, h]
    frames = [{"id": "CL", "i": bl, "j": tl, "section": "COL", "angle": 0.0, "releases": rel},
              {"id": "CR", "i": br, "j": tr, "section": "COL", "angle": 0.0, "releases": rel},
              {"id": "BM", "i": tl, "j": tr, "section": "COL", "angle": 0.0, "releases": rel},
              {"id": "BRT", "i": bl, "j": tr, "section": "COL", "angle": 0.0, "releases": rel, "tcLimit": "tension"},
              {"id": "BRC", "i": br, "j": tl, "section": "COL", "angle": 0.0, "releases": rel, "tcLimit": "tension"}]
    ux = (H * L + (H / c)**2 * D / H + (H * t)**2 * h / H) / (E * A)
    return {
        "id": "N2", "title": "Tension-only bracing",
        "purpose": ("A pin-jointed bay with two tension-only braces under a lateral load, solved as a "
                    "nonlinear static case: the brace that would be compressed goes slack, the other carries "
                    "H / cos(theta), and the bay sways as a statically determinate truss."),
        "units": "kip-ft",
        "settings": dict(_COMMON, nonlinear="tension-only braces (compression limit 0), nonlinear static case",
                         supports="pinned bases; joints held out of plane"),
        "materials": _mat(), "sections": [_col_section()],
        "stories": [{"name": "Story1", "height": h}],
        "frames": frames,
        "supports": [{"at": bl, "restraint": [1] * 6}, {"at": br, "restraint": [1] * 6},
                     {"at": tl, "restraint": [0, 1, 0, 1, 1, 1]}, {"at": tr, "restraint": [0, 1, 0, 1, 1, 1]}],
        "patterns": [{"name": "H"}],
        "pointLoads": [{"pattern": "H", "at": tl, "f": [H, 0, 0, 0, 0, 0]}],
        "nlCases": [{"name": "NLH", "loads": {"H": 1.0}, "geometry": "none"}],
        "checks": [
            _f("NLH-T", "Tension brace force", "NLH", "BRT", "i", "P", H / c, "H / cos(theta)", how="signed"),
            dict(_f("NLH-C", "Compression brace is slack", "NLH", "BRC", "i", "P", 0.0, "0"), scale=H / c),
            _f("NLH-CR", "Right column compression", "NLH", "CR", "i", "P", -H * t, "-H h / L", how="signed"),
            _j("NLH-UX", "Sway", "NLH", tl, "UX", ux, "sum N^2 L / (E A H), truss"),
            _b("NLH-FX", "Base shear", "NLH", "FX", -H, "-H"),
        ],
    }


def _elastica(alpha: float, n: int = 4000):
    """Cantilever under a fixed-direction tip load across it, alpha = P L^2 / EI: tip position (x/L,
    z/L) from theta'' = -alpha cos(theta), theta(0) = 0, theta'(1) = 0, by shooting with RK4."""
    def run(k):
        h, th, w, x, z = 1.0 / n, 0.0, k, 0.0, 0.0
        f = lambda a, b: (b, -alpha * math.cos(a))
        for _ in range(n):
            k1 = f(th, w)
            k2 = f(th + h / 2 * k1[0], w + h / 2 * k1[1])
            k3 = f(th + h / 2 * k2[0], w + h / 2 * k2[1])
            k4 = f(th + h * k3[0], w + h * k3[1])
            t0 = th
            th += h / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
            w += h / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
            tm = (t0 + th) / 2
            x += h / 6 * (math.sin(t0) + 4 * math.sin(tm) + math.sin(th))
            z += h / 6 * (math.cos(t0) + 4 * math.cos(tm) + math.cos(th))
        return w, x, z
    lo, hi = 0.0, alpha
    for _ in range(80):
        mid = (lo + hi) / 2
        if run(mid)[0] > 0:
            hi = mid
        else:
            lo = mid
    _, x, z = run((lo + hi) / 2)
    return x, z


def N3() -> dict:
    """Large displacements: a cantilever bent by a lateral tip load PL^2/EI = 2 (the elastica)."""
    E, L, I33 = STEEL_E, 12.0, 0.02
    alpha = 2.0
    P = alpha * E * I33 / L**2
    x, z = _elastica(alpha)
    pts, frames = _column(L, 16, section="STIFF")
    sec = _col_section(name="STIFF", A=10.0)
    return {
        "id": "N3", "title": "Large displacements",
        "purpose": ("A lateral tip load of 2 EI / L^2 swings the tip half a length sideways and drops it 16%: "
                    "far beyond small-displacement theory. The exact elastica is solved numerically; both "
                    "programs use 16 elements and P-Delta plus large displacements."),
        "units": "kip-ft",
        "settings": dict(_COMMON, nonlinear="P-Delta plus large displacements, nonlinear static",
                         supports="fixed base", axial="area 10 ft2, practically inextensible"),
        "materials": _mat(), "sections": [sec],
        "stories": [{"name": "Story1", "height": L}],
        "frames": frames, "supports": [{"at": pts[0], "restraint": [1] * 6}],
        "patterns": [{"name": "H"}],
        "pointLoads": [{"pattern": "H", "at": pts[-1], "f": [P, 0, 0, 0, 0, 0]}],
        "nlCases": [{"name": "LDH", "loads": {"H": 1.0}, "geometry": "large"}],
        "checks": [
            _j("H-UX", "Small-displacement sway (linear case)", "H", pts[-1], "UX", P * L**3 / (3 * E * I33),
               "PL^3 / 3EI = 0.667 L"),
            dict(_j("LDH-UX", "Large-displacement sway", "LDH", pts[-1], "UX", x * L, f"elastica: {x:.4f} L"),
                 tol=[2e-3, 5e-3]),
            dict(_j("LDH-UZ", "Tip drops", "LDH", pts[-1], "UZ", (z - 1) * L, f"elastica: -{1 - z:.4f} L"),
                 tol=[2e-3, 5e-3]),
            dict(_f("LDH-M", "Base moment: the lever arm is the deflected tip height", "LDH", "C1", "i", "M3",
                    P * z * L, "P z_tip"), tol=[2e-3, 5e-3]),
        ],
    }


# ── speed benchmark ─────────────────────────────────────────────────────────
def SP1() -> dict:
    """A 4-story RC frame, 4 x 3 bays of 25 ft, shell slabs meshed at 2.5 ft in both programs."""
    from cases_walls import CONC_E, CONC_NU
    W, h, bay, nm = 0.150, 13.0, 25.0, 10                 # nm elements per bay side (2.5 ft)
    nx, ny, ns = 4, 3, 4
    xs = [bay * i for i in range(nx + 1)]
    ys = [bay * j for j in range(ny + 1)]
    sub = lambda a, b: [a + (b - a) * k / nm for k in range(nm + 1)]
    frames, areas, supports, loads = [], [], [], []
    for s in range(ns):
        z0, z1 = s * h, (s + 1) * h
        for x in xs:
            for y in ys:
                frames.append({"id": f"C{int(x)}_{int(y)}_{s + 1}", "i": [x, y, z0], "j": [x, y, z1],
                               "section": "COL", "angle": 0.0})
        for y in ys:                                     # beams split at every slab node
            for a, b in zip(xs, xs[1:]):
                p = sub(a, b)
                frames += [{"id": f"BX{int(a)}_{int(y)}_{s + 1}_{k + 1}", "i": [p[k], y, z1], "j": [p[k + 1], y, z1],
                            "section": "BM", "angle": 0.0} for k in range(nm)]
        for x in xs:
            for a, b in zip(ys, ys[1:]):
                p = sub(a, b)
                frames += [{"id": f"BY{int(x)}_{int(a)}_{s + 1}_{k + 1}", "i": [x, p[k], z1], "j": [x, p[k + 1], z1],
                            "section": "BM", "angle": 0.0} for k in range(nm)]
        for a, b in zip(xs, xs[1:]):
            for c, d in zip(ys, ys[1:]):
                areas.append({"id": f"S{int(a)}_{int(c)}_{s + 1}", "section": "SLAB", "mesh": [nm, nm],
                              "corners": [[a, c, z1], [b, c, z1], [b, d, z1], [a, d, z1]]})
        F = 40.0 * (s + 1)
        loads += [{"pattern": "EQX", "at": [x, y, z1], "f": [F / 20, 0, 0, 0, 0, 0]} for x in xs for y in ys]
    supports = [{"at": [x, y, 0.0], "restraint": [1] * 6} for x in xs for y in ys]
    col = {"name": "COL", "material": "CONC", "A": 4.0, "I33": 4 / 3, "I22": 4 / 3, "J": 2.25,
           "As2": 0.0, "As3": 0.0, "depth": 2.0, "width": 2.0}
    bm = {"name": "BM", "material": "CONC", "A": 3.0, "I33": 1.5 * 8 / 12, "I22": 2 * 1.5**3 / 12, "J": 0.95,
          "As2": 0.0, "As3": 0.0, "depth": 2.0, "width": 1.5}
    Wt = W * (len(xs) * len(ys) * ns * h * 4.0
              + ns * (len(ys) * nx + len(xs) * ny) * bay * 3.0
              + ns * nx * ny * bay * bay * (8 / 12))
    V = sum(40.0 * (s + 1) for s in range(ns))
    return {
        "id": "SP1", "title": "Speed benchmark: 4-story RC frame",
        "purpose": ("Run time of a real-size model in both programs on the same machine: a 4-story concrete "
                    "frame, 4 x 3 bays of 25 ft, 8 in slabs as thin shells meshed at 2.5 ft (4,800 shell "
                    "elements, beams split at every slab node, about 30,000 equations). Timed twice: linear "
                    "(self-weight, superimposed load, lateral load and 12 modes) and nonlinear (P-Delta static "
                    "under all three together). ETABS uses its advanced solver on all cores."),
        "units": "kip-ft",
        "settings": dict(_COMMON, selfWeight="0.150 kcf on frames and slabs", shells="Shell-Thin, 2.5 ft, identical",
                         diaphragm="none: slabs in-plane stiff by their own shells", mass="element self-mass",
                         solver="ETABS advanced solver, all cores; SkyFrame default (PARDISO when installed)"),
        "materials": [{"name": "CONC", "E": CONC_E, "nu": CONC_NU, "unitWeight": W}],
        "sections": [col, bm],
        "areaSections": [{"name": "SLAB", "material": "CONC", "thickness": 8 / 12, "kind": "slab", "shell": "thin"}],
        "stories": [{"name": f"Story{s + 1}", "height": h} for s in range(ns)],
        "frames": frames, "areas": areas, "supports": supports,
        "patterns": [{"name": "SW", "selfWeight": 1.0}, {"name": "SDL"}, {"name": "EQX"}],
        "areaLoads": [{"pattern": "SDL", "area": a["id"], "q": 0.025} for a in areas],
        "pointLoads": loads,
        "massSource": {"elements": True, "masses": False, "patterns": {}},
        "modal": {"case": "MODES", "modes": 12},
        "nlCases": [{"name": "PDALL", "loads": {"SW": 1.0, "SDL": 1.0, "EQX": 1.0}, "geometry": "pdelta"}],
        "benchmark": {"linear": ["SW", "SDL", "EQX", "MODES"], "nonlinear": ["PDALL"]},
        "checks": [
            _b("SW-FZ", "Self-weight", "SW", "FZ", Wt, "0.15 x (columns + beams + slabs)"),
            _b("SDL-FZ", "Superimposed load", "SDL", "FZ", 0.025 * 100 * 75 * ns, "0.025 ksf x floor area"),
            _b("EQX-FX", "Base shear", "EQX", "FX", -V, "-sum F"),
            _j("EQX-UX", "Roof sway, linear", "EQX", [0.0, 0.0, ns * h], "UX", None, "programs compared"),
            _j("PDALL-UX", "Roof sway, P-Delta", "PDALL", [0.0, 0.0, ns * h], "UX", None, "programs compared"),
            _modal("T1", "Mode 1 period", 1, "T", None, "programs compared"),
            _modal("T2", "Mode 2 period", 2, "T", None, "programs compared"),
        ],
    }


CASES = {"N1": N1, "N2": N2, "N3": N3, "SP1": SP1}


# ── nonlinear group II: time history, gap, pushover, staged construction ──────
G_FT2 = 9.80665 / 0.3048                 # ft/s2


def N4() -> dict:
    """Linear time history: a one-mass cantilever under a step ground acceleration, 2% damping."""
    E, L, I33 = STEEL_E, 12.0, 0.02
    k = 3 * E * I33 / L**3
    T = 0.5
    w = 2 * math.pi / T
    mass = k / w**2
    zeta, a_g = 0.02, 0.2
    a = a_g * G_FT2
    peak = a / w**2 * (1 + math.exp(-zeta * math.pi / math.sqrt(1 - zeta * zeta)))
    top = [0.0, 0.0, L]
    dt, n = 0.001, 1500
    return {
        "id": "N4", "title": "Linear time history",
        "purpose": ("A single mass on a cantilever (T = 0.5 s, 2% damping) under a sudden, constant 0.2 g ground "
                    "acceleration: the first peak is a/omega^2 (1 + e^(-pi zeta / sqrt(1 - zeta^2))), almost "
                    "twice the static sway."),
        "units": "kip-ft",
        "settings": dict(_COMMON, mass="one joint mass, X only", timeHistory="step of -0.2 g from t = 0, 1.5 s at "
                         "0.001 s, 2% damping (ETABS modal; SkyFrame direct Newmark, Rayleigh)"),
        "materials": _mat(), "sections": [_col_section()],
        "stories": [{"name": "Story1", "height": L}],
        "frames": [{"id": "C1", "i": [0.0, 0.0, 0.0], "j": top, "section": "COL", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 0.0], "restraint": [1] * 6}],
        "masses": [{"at": top, "m": [mass, 0, 0, 0, 0, 0]}],
        "massSource": {"elements": False, "masses": True, "patterns": {}},
        "modal": {"case": "MODES", "modes": 3},
        "patterns": [],
        "thFunctions": [{"name": "STEP", "points": [[0.0, -a_g], [dt * n, -a_g]]}],
        "thCases": [{"name": "THX", "dir": "X", "function": "STEP", "scale": G_FT2, "damping": zeta,
                     "dt": dt, "steps": n}],
        "checks": [
            _modal("T1", "Period", 1, "T", T, "2 pi sqrt(m / k)"),
            dict(_j("THX-UX", "Peak sway", "THX", top, "UX", peak,
                    "a/w^2 (1 + exp(-pi z / sqrt(1 - z^2)))", how="abs"), tol=[2e-3, 5e-3]),
        ],
    }


def N5() -> dict:
    """A gap (compression-only) support under a cantilever tip: closed under gravity, open under uplift."""
    E, I, L, w, P, kg = STEEL_E, 0.03, 20.0, 1.0, 10.0, 1.0e5
    EI = E * I
    R = (w * L**4 / (8 * EI)) / (L**3 / (3 * EI) + 1 / kg)
    tip, low = [L, 0.0, 12.0], [L, 0.0, 11.0]
    from defs import _beam_section
    return {
        "id": "N5", "title": "Gap support (uplift)",
        "purpose": ("A cantilever whose tip rests on a compression-only gap link (k = 1e5 k/ft). Under gravity the "
                    "gap closes and props the tip; under an uplift load it opens and carries nothing. Nonlinear "
                    "static cases in both programs."),
        "units": "kip-ft",
        "settings": dict(_COMMON, nonlinear="gap link (compression only, zero opening), nonlinear static",
                         supports="fixed at x = 0; gap link to ground at the tip"),
        "materials": _mat(), "sections": [_beam_section()],
        "stories": [{"name": "Story1", "height": 12.0}],
        "frames": [{"id": "B1", "i": [0.0, 0.0, 12.0], "j": tip, "section": "BM", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 12.0], "restraint": [1] * 6}, {"at": tip, "restraint": [0, 1, 0, 1, 0, 1]},
                     {"at": low, "restraint": [1] * 6}],
        "links": [{"id": "G1", "i": low, "j": tip, "type": "gap", "k": kg, "open": 0.0}],
        "patterns": [{"name": "D"}, {"name": "U"}],
        "distLoads": [{"pattern": "D", "frame": "B1", "w": w}],
        "pointLoads": [{"pattern": "U", "at": tip, "f": [0, 0, P, 0, 0, 0]}],
        "nlCases": [{"name": "NLD", "loads": {"D": 1.0}, "geometry": "none"},
                    {"name": "NLU", "loads": {"U": 1.0}, "geometry": "none"}],
        "checks": [
            _f("NLD-R", "Gravity: gap force (tip shear)", "NLD", "B1", "j", "V2", R,
               "(wL^4/8EI) / (L^3/3EI + 1/k)"),
            _j("NLD-UZ", "Gravity: tip settles into the gap", "NLD", tip, "UZ", -R / kg, "-R / k"),
            _j("NLU-UZ", "Uplift: gap open, free cantilever", "NLU", tip, "UZ", P * L**3 / (3 * EI), "PL^3 / 3EI"),
            _f("NLU-R", "Uplift: the tip carries only the load (gap force 0)", "NLU", "B1", "j", "V2", P, "P"),
            _b("NLU-FZ", "Uplift: base reaction", "NLU", "FZ", -P, "-P"),
        ],
    }


def N6() -> dict:
    """Pushover of a fixed-base portal with elastic-perfectly-plastic column hinges: sway mechanism."""
    h, L, Mp = 12.0, 20.0, 200.0
    from defs import _beam_section
    col = _col_section()
    bm = dict(_beam_section(), A=1.0)
    tl, tr = [0.0, 0.0, h], [L, 0.0, h]
    return {
        "id": "N6", "title": "Pushover, plastic hinges",
        "purpose": ("A fixed-base portal pushed sideways to 5% drift. Rigid-plastic moment hinges (Mp = 200 k-ft, "
                    "no hardening) at both ends of each column: the sway mechanism forms and the base shear "
                    "levels off at 4 Mp / h."),
        "units": "kip-ft",
        "settings": dict(_COMMON, nonlinear="M3 hinges at both column ends, elastic-perfectly-plastic; "
                         "displacement-controlled push at the roof", supports="fixed bases"),
        "materials": _mat(), "sections": [col, bm],
        "stories": [{"name": "Story1", "height": h}],
        "frames": [{"id": "C1", "i": [0.0, 0.0, 0.0], "j": tl, "section": "COL", "angle": 0.0},
                   {"id": "C2", "i": [L, 0.0, 0.0], "j": tr, "section": "COL", "angle": 0.0},
                   {"id": "B1", "i": tl, "j": tr, "section": "BM", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 0.0], "restraint": [1] * 6}, {"at": [L, 0.0, 0.0], "restraint": [1] * 6},
                     {"at": tl, "restraint": [0, 1, 0, 1, 0, 1]}, {"at": tr, "restraint": [0, 1, 0, 1, 0, 1]}],
        "patterns": [{"name": "PUSH"}],
        "pointLoads": [{"pattern": "PUSH", "at": tl, "f": [1.0, 0, 0, 0, 0, 0]}],
        "pushover": [{"name": "PO", "pattern": "PUSH", "control": tl, "dir": "X", "target": 0.05 * h,
                      "steps": 100, "hinges": {"C1": Mp, "C2": Mp}}],
        "checks": [
            _b("PO-FX", "Base shear at the mechanism", "PO", "FX", -4 * Mp / h, "-4 Mp / h"),
        ],
    }


def N7() -> dict:
    """Staged construction: a three-story column built and loaded story by story."""
    E, A, h, P = STEEL_E, 0.1, 12.0, 100.0
    u = P * h / (E * A)
    pts = [[0.0, 0.0, h * k] for k in range(4)]
    return {
        "id": "N7", "title": "Staged construction",
        "purpose": ("A column stack built one story at a time, each story's 100 kip load applied when it is built: "
                    "a floor only shortens under loads placed after it exists, so the levels move 3, 4 and 3 "
                    "units of Ph/EA instead of 3, 5 and 6 for the load all at once."),
        "units": "kip-ft",
        "settings": dict(_COMMON, staged="three stages, one story each: add the story, load it", supports="fixed base"),
        "materials": _mat(), "sections": [_col_section()],
        "stories": [{"name": f"Story{k + 1}", "height": h} for k in range(3)],
        "frames": [{"id": f"C{k + 1}", "i": pts[k], "j": pts[k + 1], "section": "COL", "angle": 0.0} for k in range(3)],
        "supports": [{"at": pts[0], "restraint": [1] * 6}],
        "patterns": [{"name": "G"}],
        "pointLoads": [{"pattern": "G", "at": pts[k], "f": [0, 0, -P, 0, 0, 0]} for k in (1, 2, 3)],
        "stagedCases": [{"name": "STG", "pattern": "G"}],
        "checks": [
            _j("STG-U1", "Staged: level 1", "STG", pts[1], "UZ", -3 * u, "-3 Ph/EA"),
            _j("STG-U2", "Staged: level 2", "STG", pts[2], "UZ", -4 * u, "-(2 + 2) Ph/EA"),
            _j("STG-U3", "Staged: level 3", "STG", pts[3], "UZ", -3 * u, "-(1 + 1 + 1) Ph/EA"),
            _j("G-U3", "All at once: level 3", "G", pts[3], "UZ", -6 * u, "-(3 + 2 + 1) Ph/EA"),
            _f("STG-P1", "Staged: bottom column axial", "STG", "C1", "i", "P", -3 * P, "-3P", how="signed"),
        ],
    }


def N8() -> dict:
    """Pushover of a pin-jointed portal braced by one yielding (Plastic Wen) diagonal link."""
    h, L, Fy, k = 12.0, 20.0, 50.0, 2000.0
    from defs import _beam_section
    col = _col_section()
    bm = dict(_beam_section(), A=1.0)
    tl, tr = [0.0, 0.0, h], [L, 0.0, h]
    cos = L / math.hypot(L, h)
    pin = {"j": {"M2": 0.0, "M3": 0.0}}
    return {
        "id": "N8", "title": "Pushover, yielding brace",
        "purpose": ("A portal whose columns are pinned top and bottom, so a single diagonal brace carries all the "
                    "sway. The brace is a Plastic (Wen) link (k = 2000 k/ft, yield 50 kips, no hardening): "
                    "pushed to 5% drift it yields and the base shear levels off at Fy cos(theta). The ETABS "
                    "API cannot assign frame hinges, so this is the pushover both programs can build."),
        "units": "kip-ft",
        "settings": dict(_COMMON, nonlinear="one Plastic (Wen) axial link, exponent 2, post-yield ratio 0; "
                         "displacement-controlled push at the roof", supports="pinned bases"),
        "materials": _mat(), "sections": [col, bm],
        "stories": [{"name": "Story1", "height": h}],
        "frames": [{"id": "C1", "i": [0.0, 0.0, 0.0], "j": tl, "section": "COL", "angle": 0.0, "releases": pin},
                   {"id": "C2", "i": [L, 0.0, 0.0], "j": tr, "section": "COL", "angle": 0.0, "releases": pin},
                   {"id": "B1", "i": tl, "j": tr, "section": "BM", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 0.0], "restraint": [1, 1, 1, 0, 0, 0]},
                     {"at": [L, 0.0, 0.0], "restraint": [1, 1, 1, 0, 0, 0]},
                     {"at": tl, "restraint": [0, 1, 0, 1, 0, 1]}, {"at": tr, "restraint": [0, 1, 0, 1, 0, 1]}],
        "links": [{"id": "BR", "i": [0.0, 0.0, 0.0], "j": tr, "type": "wen", "k": k, "yield": Fy, "ratio": 0.0,
                   "exp": 2.0}],
        "patterns": [{"name": "PUSH"}],
        "pointLoads": [{"pattern": "PUSH", "at": tl, "f": [1.0, 0, 0, 0, 0, 0]}],
        "pushover": [{"name": "PO", "pattern": "PUSH", "control": tl, "dir": "X", "target": 0.05 * h,
                      "steps": 100}],
        "checks": [
            dict(_b("PO-FX", "Base shear once the brace yields", "PO", "FX", -Fy * cos, "-Fy cos(theta)"),
                 tol=[1e-3, 5e-3]),
        ],
    }


CASES.update({"N4": N4, "N5": N5, "N6": N6, "N7": N7, "N8": N8})
