"""Supa's ``solveFem`` on the SkyFrame engine (OpenSees).

Input is Supa's ``FemModel`` and output its ``FemResult``, field for field
(``SP Files/supa-fem-kit/src/types.ts``).  Units pass through untouched:
Supa works in kip and foot, and so does this solve.

The model is built in OpenSees as Supa describes it:

* each element an ``elasticBeamColumn`` (Euler-Bernoulli), local y the
  hint ``yh`` made square to x, local z = x × y (``geomTransf Linear`` with
  that z as its vecxz); ``ri`` / ``rj`` release bending about both axes at
  that end through the element's own ``-releasey`` / ``-releasez``
  (torsion is never released);
* each diaphragm a ``rigidDiaphragm`` about z, its master a node at the
  given centre, held in uz, rx, ry;
* fixed nodes fixed in all six;
* loads along elements as ``beamUniform`` in local axes, node loads, and
  diaphragm loads (fx, fy, mz) at the master;
* one ``Linear -factorOnce`` analysis: the stiffness is factored once and
  each of the six cases is a back-substitution.

What Supa's own solver does and this one does not: a small spring on every
free DOF.  Instead :mod:`.stability` runs first; rotations no element
resists (joints where every member is pinned) get a negligible spring --
they carry no load, the model has no applied moments -- and real
mechanisms are held, reported in ``notes`` and listed in ``looseNodes``.

Results follow Supa's rules exactly: ``pieces[src][case]`` from end forces
plus the load along the element, sampled at five points; a wall's strips
summed side by side, a member's pieces the largest; N tension positive.
"""

from __future__ import annotations

import math
import time
from typing import Callable, Dict, List, Optional

import numpy as np

from . import ENGINE_API_VERSION
from .stability import check, element_frames

CASES = ("D", "L", "Wx", "Wy", "Ex", "Ey")
LOOSE_FT = 1.0          # Supa's rule: moving more than 1 ft under D or L = unheld


class ModelError(ValueError):
    """The FemModel is malformed (bad index, wrong array length...)."""


def validate(m: dict) -> None:
    for k in ("xyz", "fixed", "els", "dia", "nodeLoads", "diaLoads"):
        if k not in m:
            raise ModelError(f"FemModel has no '{k}'")
    if len(m["xyz"]) % 3:
        raise ModelError("xyz length is not a multiple of 3")
    nn = len(m["xyz"]) // 3
    if len(m["fixed"]) != nn:
        raise ModelError(f"fixed has {len(m['fixed'])} entries for {nn} nodes")
    for k, e in enumerate(m["els"]):
        for f in ("i", "j", "E", "G", "A", "Iy", "Iz", "J"):
            if f not in e:
                raise ModelError(f"element {k} has no '{f}'")
        if not (0 <= e["i"] < nn and 0 <= e["j"] < nn) or e["i"] == e["j"]:
            raise ModelError(f"element {k} has bad nodes {e['i']}, {e['j']}")
        if min(e["E"], e["A"]) <= 0:
            raise ModelError(f"element {k} has E or A not positive")
    seen = set()
    for d in m["dia"]:
        for n in d["nodes"]:
            if not 0 <= n < nn:
                raise ModelError(f"diaphragm {d.get('id')} has bad node {n}")
            if n in seen:
                raise ModelError(f"node {n} is on two diaphragms")
            if m["fixed"][n]:
                raise ModelError(f"fixed node {n} is on diaphragm {d.get('id')}")
            seen.add(n)
    for c in CASES:
        if len(m["nodeLoads"].get(c, [])) not in (0, 3 * nn):
            raise ModelError(f"nodeLoads.{c} has the wrong length")
        if len(m["diaLoads"].get(c, [])) not in (0, 3 * len(m["dia"])):
            raise ModelError(f"diaLoads.{c} has the wrong length")


def solve_fem(m: dict, progress: Optional[Callable[[str], None]] = None) -> dict:
    """Solve a Supa ``FemModel``; return a ``FemResult`` dict."""
    import openseespy.opensees as ops

    say = progress or (lambda s: None)
    t0 = time.perf_counter()
    validate(m)
    nn = len(m["xyz"]) // 3
    xyz = np.asarray(m["xyz"], float).reshape(-1, 3)
    els = m["els"]
    ne = len(els)
    fixed = [bool(f) for f in m["fixed"]]
    notes: List[str] = list(m.get("notes", []))

    say("Checking the model")
    st = check(m["xyz"], fixed, els, m["dia"])
    floating = {n for g in st.floating_groups for n in g}
    notes += st.notes()

    say("Assembling")
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    tag = lambda i: i + 1                                   # OpenSees tags from 1
    for i in range(nn):
        ops.node(tag(i), *xyz[i])
    for i in range(nn):
        if fixed[i] or i in floating:
            ops.fix(tag(i), 1, 1, 1, 1, 1, 1)
    # mechanisms: held in the free direction(s), reported
    held_dof: Dict[int, List[int]] = {}
    for n, dirs in st.free_translations.items():
        held_dof[n] = [{"ux": 1, "uy": 2, "uz": 3}[d] for d in dirs]
    # negligible rotational springs where no element resists rotation
    I = np.array([e["i"] for e in els]) if ne else np.zeros(0, int)
    J = np.array([e["j"] for e in els]) if ne else np.zeros(0, int)
    yh = np.array([e.get("yh", [0, 0, 1]) for e in els], float) if ne else np.zeros((0, 3))
    ex, ey, ez, Ls = element_frames(xyz, I, J, yh) if ne else (None,) * 4
    next_node = nn + 1
    next_ele = ne + 1
    if st.free_rotations or held_dof:
        k_rot = 1e-9 * float(np.median([e["G"] * e["J"] / L for e, L in zip(els, Ls)]) or 1.0)
        k_tr = 1e-9 * float(np.median([e["E"] * e["A"] / L for e, L in zip(els, Ls)]) or 1.0)
        ops.uniaxialMaterial("Elastic", 900001, k_rot)
        ops.uniaxialMaterial("Elastic", 900002, k_tr)
    ground_of: Dict[int, int] = {}

    def ground(n: int) -> int:
        nonlocal next_node
        if n not in ground_of:
            g = next_node; next_node += 1
            ops.node(g, *xyz[n]); ops.fix(g, 1, 1, 1, 1, 1, 1)
            ground_of[n] = g
        return ground_of[n]

    for n in st.free_rotations:
        if n in floating:
            continue
        ops.element("zeroLength", next_ele, ground(n), tag(n), "-mat", 900001, 900001, 900001,
                    "-dir", 4, 5, 6)
        next_ele += 1
    held_springs: List[int] = []
    for n, dofs in held_dof.items():
        if n in floating:
            continue
        # a mechanism: held stiffly in that direction so the rest solves; its
        # load goes nowhere real, so the reaction there is reported, not summed
        ops.uniaxialMaterial("Elastic", 910000 + len(held_springs), 1e3 * k_tr / 1e-9)
        ops.element("zeroLength", next_ele, ground(n), tag(n), "-mat",
                    *([910000 + len(held_springs)] * len(dofs)), "-dir", *dofs)
        held_springs.append(n); next_ele += 1

    # diaphragms
    masters: List[int] = []
    for d in m["dia"]:
        mt = next_node; next_node += 1
        ops.node(mt, float(d["x"]), float(d["y"]), float(d["z"]))
        ops.fix(mt, 0, 0, 1, 1, 1, 0)
        slaves = [tag(n) for n in d["nodes"] if n not in floating]
        if slaves:
            ops.rigidDiaphragm(3, mt, *slaves)
        masters.append(mt)

    # elements
    R_all = np.stack([ex, ey, ez], axis=1) if ne else np.zeros((0, 3, 3))
    for k, e in enumerate(els):
        tt = k + 1
        ops.geomTransf("Linear", tt, *ez[k].tolist())
        args = [k + 1, tag(e["i"]), tag(e["j"]), e["A"], e["E"], e["G"], e["J"], e["Iy"], e["Iz"], tt]
        rel = (1 if e.get("ri") else 0) + (2 if e.get("rj") else 0)
        if rel:
            args += ["-releasez", rel, "-releasey", rel]
        ops.element("elasticBeamColumn", *args)

    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-12, 1)
    ops.algorithm("Linear", "-factorOnce")
    ops.integrator("LoadControl", 1.0)
    ops.analysis("Static")
    ops.timeSeries("Constant", 1)

    res = {
        "pieces": {}, "dia": [{"id": d["id"], "z": d["z"], "u": {}} for d in m["dia"]],
        "balance": {}, "nodeU": {}, "maxU": {}, "maxAt": {}, "loose": 0, "looseNodes": [],
        "dofs": 0, "notes": notes,
    }
    loose = set(st.mechanism_nodes)
    held_load: Dict[str, List[float]] = {}
    fixed_idx = [i for i in range(nn) if fixed[i]]
    is_src = [bool(e.get("src")) for e in els]
    wall = [e.get("kind") == "wall" for e in els]

    for ci, c in enumerate(CASES):
        say(f"Factoring" if ci == 0 else f"Solving {c}")
        ops.pattern("Plain", ci + 1, 1)
        applied = [0.0, 0.0, 0.0]
        nl = m["nodeLoads"].get(c) or []
        for i in range(nn):
            f = nl[3 * i:3 * i + 3] if nl else (0, 0, 0)
            if any(f):
                applied[0] += f[0]; applied[1] += f[1]; applied[2] += f[2]
                if not fixed[i]:
                    ops.load(tag(i), f[0], f[1], f[2], 0.0, 0.0, 0.0)
        qloc = np.zeros((ne, 3))
        for k, e in enumerate(els):
            q = (e.get("q") or {}).get(c)
            if q:
                L = Ls[k]
                for d in range(3):
                    applied[d] += q[d] * L
                ql = R_all[k] @ np.asarray(q, float)
                qloc[k] = ql
                ops.eleLoad("-ele", k + 1, "-type", "-beamUniform", ql[1], ql[2], ql[0])
        dl = m["diaLoads"].get(c) or []
        for k, mt in enumerate(masters):
            f = dl[3 * k:3 * k + 3] if dl else (0, 0, 0)
            if any(f):
                applied[0] += f[0]; applied[1] += f[1]
                ops.load(mt, f[0], f[1], 0.0, 0.0, 0.0, f[2])
        ok = ops.analyze(1)
        if ok != 0:
            ops.wipe()
            raise RuntimeError(
                "The model could not be solved: its stiffness is singular. "
                + " ".join(st.notes()) + " Check for members that connect to nothing or "
                "chains of pin-ended members.")
        if ci == 0:
            try:
                res["dofs"] = int(ops.systemSize())
            except Exception:  # noqa: BLE001 - older openseespy
                res["dofs"] = 0
        ops.reactions()
        # displacements
        U = np.array([ops.nodeDisp(tag(i))[:3] for i in range(nn)])
        if floating:
            U[sorted(floating)] = 0.0
        mag = np.linalg.norm(U, axis=1)
        if c in ("D", "L"):
            loose.update(np.nonzero(mag > LOOSE_FT)[0].tolist())
        res["nodeU"][c] = U.reshape(-1).tolist()
        res["maxU"][c] = float(mag.max()) if nn else 0.0
        res["maxAt"][c] = int(mag.argmax()) if nn else 0
        for k, mt in enumerate(masters):
            u = ops.nodeDisp(mt)
            res["dia"][k]["u"][c] = [u[0], u[1]]
        # reactions at fixed nodes only (held mechanisms are not ground)
        Rx = [0.0, 0.0, 0.0]
        for i in fixed_idx:
            r = ops.nodeReaction(tag(i))
            Rx[0] += r[0]; Rx[1] += r[1]; Rx[2] += r[2]
        for i in fixed_idx:                     # loads put straight on fixed nodes go to the ground
            f = nl[3 * i:3 * i + 3] if nl else (0, 0, 0)
            Rx[0] -= f[0]; Rx[1] -= f[1]; Rx[2] -= f[2]
        res["balance"][c] = {"applied": applied, "reactions": Rx}
        if held_springs:
            h = [0.0, 0.0, 0.0]
            for n in held_springs:
                r = ops.nodeReaction(ground_of[n])
                h[0] += r[0]; h[1] += r[1]; h[2] += r[2]
            held_load[c] = h
        # pieces
        pieces = res["pieces"]
        for k, e in enumerate(els):
            if not is_src[k]:
                continue
            f = np.asarray(ops.eleResponse(k + 1, "localForce"), float)
            ql = qloc[k]; L = Ls[k]
            Mx = Vx = 0.0
            for s in range(5):
                x = L * s / 4
                My = f[4] + x * f[2] + x * x / 2 * ql[2]
                Mz = f[5] - x * f[1] - x * x / 2 * ql[1]
                Mx = max(Mx, math.hypot(My, Mz))
                Vx = max(Vx, math.hypot(f[1] + ql[1] * x, f[2] + ql[2] * x))
            N = -float(f[0])
            rec = pieces.setdefault(e["src"], {})
            was = rec.get(c)
            if was is None:
                rec[c] = {"N": N, "M": Mx, "V": Vx}
            elif wall[k]:
                rec[c] = {"N": was["N"] + N, "M": max(was["M"], Mx), "V": was["V"] + Vx}
            else:
                rec[c] = {"N": N if abs(N) > abs(was["N"]) else was["N"],
                          "M": max(was["M"], Mx), "V": max(was["V"], Vx)}
        ops.remove("loadPattern", ci + 1)

    ops.wipe()
    loose -= set(fixed_idx)
    res["loose"] = len(loose)
    res["looseNodes"] = sorted(loose)
    soft = loose - set(st.mechanism_nodes)
    if soft:
        deg: Dict[int, int] = {}
        for e in els:
            deg[e["i"]] = deg.get(e["i"], 0) + 1; deg[e["j"]] = deg.get(e["j"], 0) + 1
        tips = sum(1 for n in soft if deg.get(n, 0) == 1)
        worst = max(soft, key=lambda n: max(math.hypot(*res["nodeU"][c][3 * n:3 * n + 3]) for c in ("D", "L")))
        wu = max(math.hypot(*res["nodeU"][c][3 * worst:3 * worst + 3]) for c in ("D", "L"))
        res["notes"].append(
            f"{len(soft)} node(s) move more than {LOOSE_FT:g} ft under dead or live load although the "
            f"model is stable: framing that nothing holds up stiffly enough ({tips} of them are free ends "
            f"of members). Largest {wu:.1f} ft at node {worst}.")
    if held_load:
        worst = max(held_load.items(), key=lambda kv: math.hypot(*kv[1]))
        res["notes"].append(
            f"Held mechanisms carry load that has no path to the ground: up to "
            f"{math.hypot(*worst[1]):.1f} kip under {worst[0]}. The balance check shows it as missing reaction.")
    res["engine"] = {"name": "SkyFrame (OpenSees)", "api": ENGINE_API_VERSION,
                     "seconds": round(time.perf_counter() - t0, 2),
                     "stability": {"floatingNodes": len(floating),
                                   "mechanismNodes": len(st.free_translations),
                                   "pinnedJoints": len(st.free_rotations),
                                   "objectsAtMechanisms": sorted({s for n in st.free_translations
                                                                  for s in st.srcs_at.get(n, [])})[:200]}}
    return res
