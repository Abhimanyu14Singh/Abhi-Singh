"""Buildings B1-B3 (kip, ft): the earlier features together at full size.

Hand values are what statics fixes exactly (weights, base shears, overturning moments); periods,
displacements, drifts and pier forces are compared between the programs.
"""

from __future__ import annotations

import math

from defs import STEEL_E, STEEL_NU, _COMMON, _b, _j

from cases_dyn import G_FT, _drift, _modal
from cases_walls import CONC_E, CONC_NU, _pier

CONC_W = 0.150


def _rect(name, mat, b, h, **extra):
    a, c = max(b, h) / 2, min(b, h) / 2
    J = a * c**3 * (16 / 3 - 3.36 * c / a * (1 - c**4 / (12 * a**4)))
    return dict({"name": name, "material": mat, "A": b * h, "I33": b * h**3 / 12, "I22": h * b**3 / 12,
                 "J": J, "As2": 0.0, "As3": 0.0, "depth": h, "width": b}, **extra)


def _aisc(name, A, Ix, Iy, J, d, bf, tw, tf):
    """AISC table values (in) to a general section in ft, with the I-shape shear areas ETABS uses:
    web d tw for shear along local 2 and (5/3) bf tf for local 3.  ETABS applies these to a section
    named like an AISC shape even when it is defined as a general section with zero shear areas (B2's
    first run), so they are stated here and used by both programs."""
    return {"name": name, "material": "STEEL", "A": A / 144, "I33": Ix / 20736, "I22": Iy / 20736,
            "J": J / 20736, "As2": d * tw / 144, "As3": 5.0 / 3.0 * bf * tf / 144, "depth": d / 12, "width": bf / 12}


def _grid(a, b, n):
    return [a + (b - a) * k / n for k in range(n + 1)]


def _core_tower(n, h, x0=0.0, tag="", mods=None):
    """A 90 x 90 ft plan, 3 x 3 bays of 30 ft, columns 2 x 2 ft and beams 1.5 x 2.5 ft, round a
    24 x 24 ft core of 1 ft walls (6 x 3 elements per wall per story).  Each floor is one rigid
    diaphragm.  mods: stiffness modifier set name for the sections ("" = none)."""
    g = [x0 + 30.0 * k for k in range(4)]
    yg = [30.0 * k for k in range(4)]
    c0, c1 = x0 + 33.0, x0 + 57.0
    frames, supports, areas, dias, cols_top = [], [], [], [], {}
    sfx = tag
    for s in range(n):
        z0, z1 = s * h, (s + 1) * h
        for x in g:
            for y in yg:
                frames.append({"id": f"C{sfx}_{int(x)}_{int(y)}_{s + 1}", "i": [x, y, z0], "j": [x, y, z1],
                               "section": "COL" + tag, "angle": 0.0})
        for y in yg:
            for a, b in zip(g, g[1:]):
                frames.append({"id": f"BX{sfx}_{int(a)}_{int(y)}_{s + 1}", "i": [a, y, z1], "j": [b, y, z1],
                               "section": "BM" + tag, "angle": 0.0})
        for x in g:
            for a, b in zip(yg, yg[1:]):
                frames.append({"id": f"BY{sfx}_{int(x)}_{int(a)}_{s + 1}", "i": [x, a, z1], "j": [x, b, z1],
                               "section": "BM" + tag, "angle": 0.0})
        walls = {"S": [[c0, 33, z0], [c1, 33, z0], [c1, 33, z1], [c0, 33, z1]],
                 "N": [[c0, 57, z0], [c1, 57, z0], [c1, 57, z1], [c0, 57, z1]],
                 "W": [[c0, 33, z0], [c0, 57, z0], [c0, 57, z1], [c0, 33, z1]],
                 "E": [[c1, 33, z0], [c1, 57, z0], [c1, 57, z1], [c1, 33, z1]]}
        for k, cs in walls.items():
            areas.append({"id": f"W{k}{sfx}{s + 1}", "section": "WALL" + tag, "mesh": [6, 3], "corners": cs,
                          "pier": f"C{k}{sfx}"})
        pts = [[x, y, z1] for x in g for y in yg]
        pts += [[x, y, z1] for x in _grid(c0, c1, 6) for y in (33.0, 57.0)]
        pts += [[x, y, z1] for x in (c0, c1) for y in _grid(33.0, 57.0, 6)[1:-1]]
        dias.append({"name": f"D{sfx}{s + 1}", "points": pts})
        cols_top[s] = [[x, y, z1] for x in g for y in yg]
    supports += [{"at": [x, y, 0.0], "restraint": [1] * 6} for x in g for y in yg]
    base = {(x, 33.0) for x in _grid(c0, c1, 6)} | {(x, 57.0) for x in _grid(c0, c1, 6)}
    base |= {(x, y) for x in (c0, c1) for y in _grid(33.0, 57.0, 6)}
    supports += [{"at": [x, y, 0.0], "restraint": [1] * 6} for x, y in sorted(base)]
    md = mods or {}
    sections = [_rect("COL" + tag, "CONC", 2.0, 2.0, **({"modifiers": md["col"]} if md else {})),
                _rect("BM" + tag, "CONC", 1.5, 2.5, **({"modifiers": md["beam"]} if md else {}))]
    asec = dict({"name": "WALL" + tag, "material": "CONC", "thickness": 1.0, "kind": "wall", "shell": "thin"},
                **({"modifiers": md["wall"]} if md else {}))
    weight = CONC_W * (16 * n * h * 4.0 + 24 * n * 30.0 * 3.75 + 4 * 24.0 * n * h * 1.0)
    return {"frames": frames, "supports": supports, "areas": areas, "diaphragms": dias, "sections": sections,
            "areaSection": asec, "cols_top": cols_top, "weight": weight}


def _elf(n, h, V):
    """Inverted-triangle story forces summing to V, and their overturning moment."""
    F = [V * (s + 1) / (n * (n + 1) / 2) for s in range(n)]
    return F, sum(F[s] * (s + 1) * h for s in range(n))


def B1() -> dict:
    """A 20-story concrete core-and-frame building."""
    n, h, V, Wfl = 20, 12.0, 400.0, 1200.0
    t = _core_tower(n, h)
    F, M = _elf(n, h, V)
    loads = [{"pattern": "EQX", "at": p, "f": [F[s] / 16, 0, 0, 0, 0, 0]} for s in range(n) for p in t["cols_top"][s]]
    masses = [{"at": p, "m": [Wfl / G_FT / 16] * 2 + [0, 0, 0, 0]} for s in range(n) for p in t["cols_top"][s]]
    return {
        "id": "B1", "title": "20-story core and frame",
        "purpose": ("A 90 ft square, 20-story concrete building: perimeter and interior moment frames round a "
                    "24 ft core of 1 ft walls (1,440 wall elements), rigid floors, self-weight, an inverted-"
                    "triangle lateral load and nine modes. Statics fix the weight, base shear and overturning; "
                    "periods, drifts and core pier forces are compared."),
        "units": "kip-ft",
        "settings": dict(_COMMON, selfWeight="0.150 kcf on frames and walls",
                         mass="1,200 kip per floor as joint masses at the 16 columns; no element mass",
                         shells="core walls Shell-Thin, 4 x 4 ft elements", diaphragm="rigid, each floor",
                         piers="each core wall its own pier"),
        "materials": [{"name": "CONC", "E": CONC_E, "nu": CONC_NU, "unitWeight": CONC_W}],
        "sections": t["sections"], "areaSections": [t["areaSection"]],
        "stories": [{"name": f"Story{s + 1}", "height": h} for s in range(n)],
        "frames": t["frames"], "areas": t["areas"], "supports": t["supports"], "diaphragms": t["diaphragms"],
        "masses": masses, "massSource": {"elements": False, "masses": True, "patterns": {}},
        "modal": {"case": "MODES", "modes": 9},
        "patterns": [{"name": "SW", "selfWeight": 1.0}, {"name": "EQX"}],
        "pointLoads": loads,
        "checks": [
            _b("SW-FZ", "Self-weight", "SW", "FZ", t["weight"], "0.15 x (columns + beams + walls)"),
            _b("EQX-FX", "Base shear", "EQX", "FX", -V, "-V"),
            _b("EQX-MY", "Overturning moment", "EQX", "MY", -M, "-sum F z"),
            _j("EQX-UX", "Roof displacement", "EQX", [0.0, 0.0, n * h], "UX", None, "programs compared"),
            _drift("EQX-D10", "Story 10 drift ratio", "EQX", "Story10", "X", None, "programs compared"),
            _pier("EQX-VS", "Core south wall shear, story 1", "EQX", "Story1", "CS", "V2", None, "programs compared"),
            _pier("EQX-MS", "Core south wall moment, story 1", "EQX", "Story1", "CS", "M3", None,
                  "programs compared"),
            _modal("T1", "Mode 1 period", 1, "T", None, "programs compared"),
            _modal("T2", "Mode 2 period", 2, "T", None, "programs compared"),
            _modal("T3", "Mode 3 period (torsion)", 3, "T", None, "programs compared"),
        ],
        "notes": "Square plan: modes 1 and 2 are an X/Y pair with equal periods, so their mass ratios are arbitrary.",
    }


def B2() -> dict:
    """A 10-story steel moment frame with AISC sections, response spectrum and ELF."""
    n, h, V, Wfl = 10, 13.0, 300.0, 900.0
    xs, ys = [25.0 * k for k in range(5)], [25.0 * k for k in range(4)]
    secs = [_aisc("W14X90", 26.5, 999.0, 362.0, 4.06, 14.0, 14.5, 0.44, 0.71),
            _aisc("W24X76", 22.4, 2100.0, 82.5, 2.68, 23.9, 8.99, 0.44, 0.68)]
    frames, dias, supports, tops = [], [], [], {}
    for s in range(n):
        z0, z1 = s * h, (s + 1) * h
        for x in xs:
            for y in ys:
                frames.append({"id": f"C_{int(x)}_{int(y)}_{s + 1}", "i": [x, y, z0], "j": [x, y, z1],
                               "section": "W14X90", "angle": 90.0 if x in (0.0, 100.0) else 0.0})
        for y in ys:
            for a, b in zip(xs, xs[1:]):
                frames.append({"id": f"BX_{int(a)}_{int(y)}_{s + 1}", "i": [a, y, z1], "j": [b, y, z1],
                               "section": "W24X76", "angle": 0.0})
        for x in xs:
            for a, b in zip(ys, ys[1:]):
                frames.append({"id": f"BY_{int(x)}_{int(a)}_{s + 1}", "i": [x, a, z1], "j": [x, b, z1],
                               "section": "W24X76", "angle": 0.0})
        tops[s] = [[x, y, z1] for x in xs for y in ys]
        dias.append({"name": f"D{s + 1}", "points": tops[s]})
    supports = [{"at": [x, y, 0.0], "restraint": [1] * 6} for x in xs for y in ys]
    F, M = _elf(n, h, V)
    nj = len(xs) * len(ys)
    loads = [{"pattern": "ELF", "at": p, "f": [F[s] / nj, 0, 0, 0, 0, 0]} for s in range(n) for p in tops[s]]
    masses = [{"at": p, "m": [Wfl / G_FT / nj] * 2 + [0, 0, 0, 0]} for s in range(n) for p in tops[s]]
    spec = [[0.0, 0.4], [0.2, 1.0], [0.6, 1.0], [1.0, 0.6], [2.0, 0.3], [4.0, 0.15], [8.0, 0.075]]
    W = sum(Wfl for _ in range(n))
    return {
        "id": "B2", "title": "10-story steel moment frame",
        "purpose": ("A 100 x 75 ft steel moment frame, W14x90 columns (the end columns turned 90 degrees) and "
                    "W24x76 beams, ten 13 ft stories, rigid floors. An equivalent-lateral-force pattern and a "
                    "design-shaped spectrum combined by CQC; mass from joint masses."),
        "units": "kip-ft",
        "settings": dict(_COMMON, sections="AISC W14x90 and W24x76 as general sections (table A, I, J; shear areas d tw and 5/3 bf tf)",
                         mass="900 kip per floor as joint masses", diaphragm="rigid, each floor",
                         spectrum="0.4 g to a 1.0 g plateau (0.2-0.6 s), then 0.6/T g; CQC, 5%"),
        "materials": [{"name": "STEEL", "E": STEEL_E, "nu": STEEL_NU, "unitWeight": 0.0}],
        "sections": secs,
        "stories": [{"name": f"Story{s + 1}", "height": h} for s in range(n)],
        "frames": frames, "supports": supports, "diaphragms": dias,
        "masses": masses, "massSource": {"elements": False, "masses": True, "patterns": {}},
        "modal": {"case": "MODES", "modes": 12},
        "spectra": [{"name": "DESIGN", "points": spec, "damping": 0.05}],
        "rsCases": [{"name": "RSX", "dir": "X", "function": "DESIGN", "scale": G_FT, "combine": "CQC",
                     "damping": 0.05}],
        "patterns": [{"name": "ELF"}],
        "pointLoads": loads,
        "checks": [
            _b("ELF-FX", "ELF base shear", "ELF", "FX", -V, "-V"),
            _b("ELF-MY", "ELF overturning", "ELF", "MY", -M, "-sum F z"),
            _j("ELF-UX", "ELF roof displacement", "ELF", [0.0, 0.0, n * h], "UX", None, "programs compared"),
            _drift("ELF-D3", "ELF story 3 drift ratio", "ELF", "Story3", "X", None, "programs compared"),
            _modal("T1", "Mode 1 period", 1, "T", None, "programs compared"),
            _modal("T2", "Mode 2 period", 2, "T", None, "programs compared"),
            _modal("T3", "Mode 3 period", 3, "T", None, "programs compared"),
            _b("RSX-FX", "Spectrum base shear", "RSX", "FX", None, "programs compared"),
            _j("RSX-UX", "Spectrum roof displacement", "RSX", [0.0, 0.0, n * h], "UX", None, "programs compared"),
        ],
        "notes": f"Total seismic weight {W:g} kip.",
    }


def B3() -> dict:
    """The B1 frame at 10 stories, twice: cracked (strength) and service stiffness sets side by side."""
    n, h, V = 10, 12.0, 200.0
    strength = {"col": {"A": 1.0, "I33": 0.7, "I22": 0.7, "J": 1.0},
                "beam": {"A": 1.0, "I33": 0.35, "I22": 0.35, "J": 1.0},
                "wall": {"f11": 0.35, "f22": 0.35, "f12": 0.35, "m11": 0.35, "m22": 0.35, "m12": 0.35}}
    service = {k: {d: (1.0 if d in ("A", "J") else min(1.0, 1.4 * f)) for d, f in v.items()}
               for k, v in strength.items()}
    a = _core_tower(n, h, 0.0, "S", strength)
    b = _core_tower(n, h, 150.0, "V", service)
    F, M = _elf(n, h, V)
    loads = []
    for pat, t in (("EQS", a), ("EQV", b)):
        loads += [{"pattern": pat, "at": p, "f": [F[s] / 16, 0, 0, 0, 0, 0]} for s in range(n) for p in t["cols_top"][s]]
    return {
        "id": "B3", "title": "Strength and service stiffness sets",
        "purpose": ("Two copies of a 10-story core-and-frame tower, 60 ft apart: one with cracked-section "
                    "modifiers for strength (columns 0.7, beams 0.35, walls 0.35) and one with the service set "
                    "(1.4 times those, ACI 318 6.6.3.2.2). The same lateral load on each: the service tower "
                    "drifts about 1/1.4 as much."),
        "units": "kip-ft",
        "settings": dict(_COMMON, modifiers="strength set on tower S, service set (x1.4) on tower V",
                         shells="core walls Shell-Thin with membrane and bending modifiers",
                         diaphragm="rigid, each floor of each tower"),
        "materials": [{"name": "CONC", "E": CONC_E, "nu": CONC_NU, "unitWeight": 0.0}],
        "sections": a["sections"] + b["sections"], "areaSections": [a["areaSection"], b["areaSection"]],
        "stories": [{"name": f"Story{s + 1}", "height": h} for s in range(n)],
        "frames": a["frames"] + b["frames"], "areas": a["areas"] + b["areas"],
        "supports": a["supports"] + b["supports"], "diaphragms": a["diaphragms"] + b["diaphragms"],
        "patterns": [{"name": "EQS"}, {"name": "EQV"}],
        "pointLoads": loads,
        "checks": [
            _b("EQS-FX", "Strength tower base shear", "EQS", "FX", -V, "-V"),
            _b("EQV-FX", "Service tower base shear", "EQV", "FX", -V, "-V"),
            _j("EQS-UX", "Strength tower roof displacement", "EQS", [0.0, 0.0, n * h], "UX", None,
               "programs compared"),
            _j("EQV-UX", "Service tower roof displacement", "EQV", [150.0, 0.0, n * h], "UX", None,
               "programs compared"),
            _pier("EQS-VS", "Strength tower core south wall shear, story 1", "EQS", "Story1", "CSS", "V2", None,
                  "programs compared"),
            _pier("EQV-VS", "Service tower core south wall shear, story 1", "EQV", "Story1", "CSV", "V2", None,
                  "programs compared"),
        ],
    }


CASES = {"B1": B1, "B2": B2, "B3": B3}
