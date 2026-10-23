"""Speed group: the SP1 benchmark at 10 and 40 stories (kip, ft).

Both come from SP1's generator (``cases_nonlin.rc_frame``): the same 4 x 3 bays of 25 ft, 13 ft stories,
8 in thin-shell slabs drawn as nm x nm separate area objects per bay in both programs (ETABS does not mesh
them further), beams split at every slab node, fixed column bases, the same load patterns, 12 modes and a
P-Delta case.  The runner times them like SP1 (``benchmark`` in run_etabs.py, ``speed_skyframe.py`` for
SkyFrame on the same PC).

    SP2  10 stories, slab mesh 2.5 ft (nm 10): 12,000 shells, 3,300 frames, ~76,000 equations
    SP3  40 stories, slab mesh 5 ft   (nm 5):  12,000 shells, 7,000 frames, ~81,000 equations

SP3 uses the coarser 5 ft mesh: at SP1's 2.5 ft it would be 48,000 shells, 13,200 frames and ~305,000
equations.  SkyFrame solves that (measured on 4 cores without PARDISO: 77 s linear, 144 s P-Delta, 2.9 GB),
but ETABS would be built through ~250,000 COM calls and analysed three times (two timed parts, then the
results run), and its outputs would be too big to push: ~65 MB of joint results in SP3.etabs.json, plus
analysis files over GitHub's 100 MB limit in the pushed SkyFrameVerification folder.

Both are marked "slow": the pytest suite skips running them unless SKYFRAME_SLOW=1.
"""

from __future__ import annotations

import math

from cases_dyn import _modal
from cases_nonlin import rc_frame
from defs import _COMMON, _b, _j


def SP2() -> dict:
    """A 10-story RC frame, 4 x 3 bays of 25 ft, shell slabs meshed at 2.5 ft in both programs."""
    return dict(rc_frame(
        "SP2", "Speed benchmark: 10-story RC frame",
        ("SP1 at 10 stories: 4 x 3 bays of 25 ft, 8 in slabs as thin shells meshed at 2.5 ft (12,000 shell "
         "elements, 3,300 frame elements, beams split at every slab node, about 76,000 equations). Lateral "
         "load 20 kips x level number. Timed twice: linear (self-weight, superimposed load, lateral load and "
         "12 modes) and nonlinear (P-Delta static under all three together). ETABS uses its advanced solver "
         "on all cores."),
        ns=10, nm=10, Fk=20.0), slow=True)


def SP3() -> dict:
    """A 40-story RC frame, 4 x 3 bays of 25 ft, shell slabs meshed at 5 ft in both programs."""
    return dict(rc_frame(
        "SP3", "Speed benchmark: 40-story RC frame",
        ("SP1 at 40 stories with 3 ft square columns: 4 x 3 bays of 25 ft, 8 in slabs as thin shells meshed "
         "at 5 ft (12,000 shell elements, 7,000 frame elements, beams split at every slab node, about 81,000 "
         "equations). Lateral load 1.5 kips x level number. Timed twice: linear (self-weight, superimposed "
         "load, lateral load and 12 modes) and nonlinear (P-Delta static under all three together). ETABS "
         "uses its advanced solver on all cores. The slab mesh is coarser than SP1's 2.5 ft (identical in "
         "both programs) so the model and its results stay a practical size."),
        ns=40, nm=5, Fk=1.5, col=3.0), slow=True)


CASES = {"SP2": SP2, "SP3": SP3}


# ── linear speed benchmarks: a large building and a 70-story shear-wall tower ─────────────────────────
def SP4() -> dict:
    """A large 10-story building: 10 x 6 bays of 25 ft (250 x 150 ft), slabs meshed at 5 ft, linear only."""
    c = rc_frame(
        "SP4", "Speed benchmark: large 10-story building",
        ("A large floor plate: 10 x 6 bays of 25 ft (250 x 150 ft), 10 stories of 13 ft, 8 in slabs as thin "
         "shells meshed at 5 ft (15,000 shell elements, beams split at every slab node, about 95,000 "
         "equations). Linear only: self-weight, superimposed load, lateral load and 12 modes, timed in both "
         "programs on the same PC, ETABS on its advanced solver with all cores."),
        ns=10, nm=5, nx=10, ny=6, Fk=40.0, col=2.5)
    c.pop("nlCases")
    c["benchmark"] = {"linear": ["SW", "SDL", "EQX", "MODES"], "nonlinear": []}
    c["checks"] = [k for k in c["checks"] if k["case"] != "PDALL"]
    c["slow"] = True
    c["runOnRequest"] = True                       # run_speed.bat, not run_changed.bat
    return c


def wall_tower(cid: str, title: str, purpose: str, ns: int, h: float = 12.0) -> dict:
    """A tower: 5 x 5 column grid at 30 ft (120 x 120 ft), a 30 x 30 ft concrete core of four 18 in shear
    walls (x and y 45 to 75 ft) meshed 2.5 ft wide by h/4 high, perimeter and interior beams that stop at
    the core, a rigid diaphragm at every floor.  Linear only."""
    from cases_walls import CONC_E, CONC_NU
    W, t, grid = 0.150, 1.5, [0.0, 30.0, 60.0, 90.0, 120.0]
    lo, hi, nw, nv = 45.0, 75.0, 12, 4                    # core walls: 12 x 4 elements per wall per story
    inside = lambda x, y: lo < x < hi and lo < y < hi
    cols = [(x, y) for x in grid for y in grid if not (lo <= x <= hi and lo <= y <= hi)]
    corners = [(lo, lo), (hi, lo), (hi, hi), (lo, hi)]
    frames, areas, loads, dias = [], [], [], []
    wall_nodes = sorted({(lo + (hi - lo) * k / nw, y) for k in range(nw + 1) for y in (lo, hi)}
                        | {(x, lo + (hi - lo) * k / nw) for k in range(nw + 1) for x in (lo, hi)})
    beam_len = 0.0
    for s in range(ns):
        z0, z1 = s * h, (s + 1) * h
        for x, y in cols:
            frames.append({"id": f"C{int(x)}_{int(y)}_{s + 1}", "i": [x, y, z0], "j": [x, y, z1],
                           "section": "COL", "angle": 0.0})
        for line in grid:                                # beams on every grid line, except across the core
            for a, b in zip(grid, grid[1:]):
                for p, q, tag in (((a, line), (b, line), "BX"), ((line, a), (line, b), "BY")):
                    mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
                    if inside(*mid) or not ((p in cols) and (q in cols)):
                        continue
                    frames.append({"id": f"{tag}{int(p[0])}_{int(p[1])}_{s + 1}", "i": [p[0], p[1], z1],
                                   "j": [q[0], q[1], z1], "section": "BM", "angle": 0.0})
                    if s == 0:
                        beam_len += math.dist(p, q)
        for k, ((xa, ya), (xb, yb)) in enumerate(zip(corners, corners[1:] + corners[:1])):
            areas.append({"id": f"W{k + 1}_{s + 1}", "section": "WALL", "mesh": [nw, nv], "pier": f"P{k + 1}",
                          "corners": [[xa, ya, z0], [xb, yb, z0], [xb, yb, z1], [xa, ya, z1]]})
        F = 10.0 * (s + 1) / len(cols)
        loads += [{"pattern": "EQX", "at": [x, y, z1], "f": [F, 0, 0, 0, 0, 0]} for x, y in cols]
        dias.append({"name": f"D{s + 1}", "points": [[x, y, z1] for x, y in cols] + [[x, y, z1] for x, y in wall_nodes]})
    supports = [{"at": [x, y, 0.0], "restraint": [1] * 6} for x, y in cols + wall_nodes]
    nb = len([f for f in frames if f["section"] == "BM"])
    colsec = {"name": "COL", "material": "CONC", "A": 9.0, "I33": 81 / 12, "I22": 81 / 12, "J": 2.25 * 1.5**4,
              "As2": 0.0, "As3": 0.0, "depth": 3.0, "width": 3.0}
    bm = {"name": "BM", "material": "CONC", "A": 3.0, "I33": 1.5 * 8 / 12, "I22": 2 * 1.5**3 / 12, "J": 0.95,
          "As2": 0.0, "As3": 0.0, "depth": 2.0, "width": 1.5}
    Wt = W * (len(cols) * ns * h * 9.0 + ns * beam_len * 3.0 + ns * 4 * (hi - lo) * h * t)
    V = sum(10.0 * (s + 1) for s in range(ns))
    sdl = [{"pattern": "SDL", "frame": f["id"], "w": 1.0} for f in frames if f["section"] == "BM"]
    return {
        "id": cid, "title": title, "purpose": purpose, "units": "kip-ft",
        "settings": dict(_COMMON, selfWeight="0.150 kcf on columns, beams and walls",
                         shells=f"Shell-Thin walls, {(hi - lo) / nw:g} x {h / nv:g} ft, identical",
                         diaphragm="rigid at every floor", mass="element self-mass",
                         solver="ETABS advanced solver, all cores; SkyFrame default (PARDISO when installed)"),
        "materials": [{"name": "CONC", "E": CONC_E, "nu": CONC_NU, "unitWeight": W}],
        "sections": [colsec, bm],
        "areaSections": [{"name": "WALL", "material": "CONC", "thickness": t, "kind": "wall", "shell": "thin"}],
        "stories": [{"name": f"Story{s + 1}", "height": h} for s in range(ns)],
        "frames": frames, "areas": areas, "supports": supports, "diaphragms": dias,
        "patterns": [{"name": "SW", "selfWeight": 1.0}, {"name": "SDL"}, {"name": "EQX"}],
        "distLoads": sdl, "pointLoads": loads,
        "massSource": {"elements": True, "masses": False, "patterns": {}},
        "modal": {"case": "MODES", "modes": 12},
        "benchmark": {"linear": ["SW", "SDL", "EQX", "MODES"], "nonlinear": []},
        "slow": True, "runOnRequest": True,          # run_speed.bat, not run_changed.bat
        "checks": [
            _b("SW-FZ", "Self-weight", "SW", "FZ", Wt, "0.15 x (columns + beams + walls)"),
            _b("SDL-FZ", "Superimposed load on the beams", "SDL", "FZ", 1.0 * nb * 30.0, "1 k/ft x beam length"),
            _b("EQX-FX", "Base shear", "EQX", "FX", -V, "-sum F"),
            _j("EQX-UX", "Roof sway", "EQX", [0.0, 0.0, ns * h], "UX", None, "programs compared"),
            _modal("T1", "Mode 1 period", 1, "T", None, "programs compared"),
            _modal("T2", "Mode 2 period", 2, "T", None, "programs compared"),
        ],
    }


def SP5() -> dict:
    """A 70-story tower with a concrete shear-wall core, linear only."""
    return wall_tower(
        "SP5", "Speed benchmark: 70-story shear-wall tower",
        ("A 70-story tower, 840 ft tall: a 30 x 30 ft core of four 18 in concrete shear walls meshed 2.5 x 3 ft "
         "(13,440 wall shells), 24 columns of 3 ft and beams on a 120 x 120 ft plan, a rigid diaphragm at every "
         "floor. Linear only: self-weight, superimposed load on the beams, lateral load (10 kips x level "
         "number) and 12 modes, timed in both programs on the same PC, ETABS on its advanced solver with all "
         "cores."),
        ns=70)


CASES.update({"SP4": SP4, "SP5": SP5})
