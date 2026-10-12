"""BucklingFEM: shell finite-element buckling of ONE frame member.

CONTRACT "Buckling with shells and links, sparse eigen and BucklingFEM".

ETABS' BucklingFEM idea: the member is rebuilt as a folded-plate shell
model of its cross-section along its length, loaded by the member's end
forces, and a linear buckling analysis finds LOCAL (plate), DISTORTIONAL
and GLOBAL (flexural, lateral-torsional) modes in one eigen solve.

Model (member-local coordinates: X along the member 0..L, Y = section
local 3, Z = section local 2; SI kN, m):

* plates on the section MID-LINES from the section dimensions —
  ``I`` (web + two flanges, each flange split at the web junction),
  ``I_mono``, ``channel`` (web at the back, flanges toward +3), ``tee``
  (flange on top), ``rect_tube`` (four walls) — dimensions from the
  section's parametric ``dims``, its steel-database record (``db_ref`` or
  a library W-shape name), or an explicit override;
* ShellMITC4 quads (``ElasticMembranePlateSection``, the section
  material's E and nu); ``mesh = {n_long, n_web, n_flange}``;
* loads — normal stresses at the two end sections from the internal
  forces ``P`` (tension +, constant), ``M3i``/``M3j`` and ``M2i``/``M2j``
  (linear along the member): ``sigma = P/A - M3 (y2 - yc)/I33 + M2 (y3 -
  xc)/I22`` (the engine's member_stations convention: positive M3
  compresses the +2 face — sagging of a beam with local 2 up; positive M2
  puts the +3 face in tension), integrated EXACTLY over the mid-line
  segments (consistent nodal forces of the linear stress) with the
  mid-line section properties, so the applied resultants equal the input;
  an optional uniform span load ``w2`` (kN/m along -2) acts on the load
  line (``load_position`` "shear_center" | "top" | "bottom");
* boundary ``"fork"`` (default; simply supported, twist prevented,
  warping free): every web (tube: every) node of both end sections is
  held in Y and Z, one node at end i in X; ``"fixed"``: end sections
  restrained in Y, Z and all rotations, end i also in X (warping
  prevented), end j nodes tied in X to one node (uniform shortening) —
  end moments are reacted by the fixity and ignored (warned);
* linear buckling: reference solve -> shell membrane geometric stiffness
  (:mod:`skyframe.engine.geom_stiffness`) -> shift-invert Lanczos on the
  factored stiffness (:mod:`skyframe.engine.lanczos`).

Mode classification (heuristic, documented): per station the in-plane
section displacements are fitted by a rigid section motion (vY, vZ, twist
about the centroid); ``global_fraction`` = fitted share of the squared
displacement.  >= 0.85 -> "global" ("flexural-minor" / "flexural-major" /
"lateral-torsional" / "torsional" by the share of twist x radius of
gyration and of each translation); else "local" when the plate junction
lines barely move (junction displacement < 25 % of the largest in-plane
displacement) and "distortional" otherwise.  ``half_waves`` counts the
sign changes of the dominant displacement line along X.

Closed-form references (mid-line properties) are returned alongside:
Euler loads, the elastic lateral-torsional moment of a fork-supported
doubly-symmetric I under uniform moment, ``Mcr = (pi/L) sqrt(E I22 G J)
sqrt(1 + pi^2 E Cw / (G J L^2))``, and the plate local-buckling stresses
of the web (k = 4) and flange outstands (k = 0.425).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

SECTION_TYPES = ("I", "I_mono", "channel", "tee", "rect_tube")
BOUNDARIES = ("fork", "fixed")
LOAD_POSITIONS = ("shear_center", "top", "bottom")
FORCE_KEYS = ("P", "M3i", "M3j", "M2i", "M2j", "w2")
MAX_NODES = 40000
_TOL = 1e-9


# ------------------------------------------------------------- geometry
def section_geometry(model, sec, override: Optional[dict] = None
                     ) -> Tuple[str, Dict[str, float]]:
    """``(type, dims)`` of a frame section's plate model."""
    if override:
        typ = str(override.get("type") or "")
        dims = override.get("dims") or {}
        return _checked(typ, dims, getattr(sec, "name", "?"))
    shape = getattr(sec, "shape", None)
    dims = getattr(sec, "dims", None)
    if shape in SECTION_TYPES and isinstance(dims, dict):
        return _checked(shape, dims, sec.name)
    ref = getattr(sec, "db_ref", None)
    names = []
    if isinstance(ref, dict) and ref.get("name"):
        names.append((ref.get("standard") or "aisc", ref["name"]))
    names.append(("aisc", sec.name))
    from skyframe.core import steel_db as _sdb
    for std, nm in names:
        try:
            rec = _sdb.shape(std, nm)
        except (KeyError, ValueError):
            continue
        if rec.get("type") in SECTION_TYPES:
            return _checked(rec["type"], rec["dims"], sec.name)
    raise ValueError(f"section {sec.name!r} has no plate dimensions "
                     f"(types {', '.join(SECTION_TYPES)}): pass 'section' "
                     "{type, dims} explicitly")


def _checked(typ: str, dims: dict, name: str):
    req = {"I": ("d", "bf", "tf", "tw"),
           "I_mono": ("d", "bf_top", "tf_top", "bf_bot", "tf_bot", "tw"),
           "channel": ("d", "bf", "tf", "tw"),
           "tee": ("d", "bf", "tf", "tw"),
           "rect_tube": ("d", "b", "t")}
    if typ not in req:
        raise ValueError(f"section {name!r}: type must be one of "
                         f"{', '.join(SECTION_TYPES)} (got {typ!r})")
    g: Dict[str, float] = {}
    for k in req[typ]:
        v = dims.get(k) if isinstance(dims, dict) else None
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(float(v)) or float(v) <= 0.0:
            raise ValueError(f"section {name!r}: dims.{k} must be a "
                             "positive number")
        g[k] = float(v)
    return typ, g


def plates(typ: str, g: Dict[str, float]) -> List[dict]:
    """Mid-line plates ``{p0, p1, t, role}`` in section coords (x = local
    3, y = local 2).  Flanges are split at the web junction."""
    P: List[dict] = []

    def add(p0, p1, t, role):
        P.append({"p0": (float(p0[0]), float(p0[1])),
                  "p1": (float(p1[0]), float(p1[1])), "t": float(t),
                  "role": role})
    if typ in ("I", "I_mono"):
        if typ == "I":
            bt, tt, bb, tb = g["bf"], g["tf"], g["bf"], g["tf"]
        else:
            bt, tt, bb, tb = (g["bf_top"], g["tf_top"], g["bf_bot"],
                              g["tf_bot"])
        yb, yt = tb / 2.0, g["d"] - tt / 2.0
        add((0, yb), (0, yt), g["tw"], "web")
        add((-bt / 2, yt), (0, yt), tt, "flange_top")
        add((0, yt), (bt / 2, yt), tt, "flange_top")
        add((-bb / 2, yb), (0, yb), tb, "flange_bot")
        add((0, yb), (bb / 2, yb), tb, "flange_bot")
    elif typ == "channel":
        xw = g["tw"] / 2.0
        yb, yt = g["tf"] / 2.0, g["d"] - g["tf"] / 2.0
        add((xw, yb), (xw, yt), g["tw"], "web")
        add((xw, yt), (g["bf"], yt), g["tf"], "flange_top")
        add((xw, yb), (g["bf"], yb), g["tf"], "flange_bot")
    elif typ == "tee":
        yt = g["d"] - g["tf"] / 2.0
        add((0, 0.0), (0, yt), g["tw"], "web")
        add((-g["bf"] / 2, yt), (0, yt), g["tf"], "flange_top")
        add((0, yt), (g["bf"] / 2, yt), g["tf"], "flange_top")
    elif typ == "rect_tube":
        t = g["t"]
        bx, by = (g["b"] - t) / 2.0, (g["d"] - t) / 2.0
        add((-bx, by), (bx, by), t, "flange_top")
        add((bx, -by), (-bx, -by), t, "flange_bot")
        add((bx, by), (bx, -by), t, "web")
        add((-bx, -by), (-bx, by), t, "web")
    for p in P:
        if math.dist(p["p0"], p["p1"]) <= _TOL:
            raise ValueError("degenerate plate (zero mid-line length)")
    return P


def midline_props(pl: List[dict]) -> dict:
    """Thin-walled mid-line section properties (exact line integrals)."""
    A = Sx = Sy = 0.0
    for p in pl:
        (x0, y0), (x1, y1) = p["p0"], p["p1"]
        L = math.dist(p["p0"], p["p1"])
        a = p["t"] * L
        A += a
        Sx += a * (x0 + x1) / 2.0
        Sy += a * (y0 + y1) / 2.0
    xc, yc = Sx / A, Sy / A
    I33 = I22 = J = 0.0
    for p in pl:
        (x0, y0), (x1, y1) = p["p0"], p["p1"]
        L = math.dist(p["p0"], p["p1"])
        t = p["t"]
        u0, u1 = y0 - yc, y1 - yc
        v0, v1 = x0 - xc, x1 - xc
        I33 += t * L * (u0 * u0 + u0 * u1 + u1 * u1) / 3.0
        I22 += t * L * (v0 * v0 + v0 * v1 + v1 * v1) / 3.0
        J += L * t ** 3 / 3.0
    return {"A": A, "xc": xc, "yc": yc, "I33": I33, "I22": I22, "J": J}


def warping_constant(typ: str, g: Dict[str, float], pl: List[dict]
                     ) -> Optional[float]:
    """Cw of the mid-line model (I / I_mono / channel), else None."""
    if typ == "I":
        h0 = g["d"] - g["tf"]
        If = g["tf"] * g["bf"] ** 3 / 12.0
        return If * h0 * h0 / 2.0
    if typ == "I_mono":
        h0 = g["d"] - g["tf_top"] / 2.0 - g["tf_bot"] / 2.0
        It = g["tf_top"] * g["bf_top"] ** 3 / 12.0
        Ib = g["tf_bot"] * g["bf_bot"] ** 3 / 12.0
        return h0 * h0 * It * Ib / (It + Ib)
    if typ == "channel":
        h = g["d"] - g["tf"]
        b = g["bf"] - g["tw"] / 2.0
        tf, tw = g["tf"], g["tw"]
        return tf * b ** 3 * h ** 2 / 12.0 * (3 * b * tf + 2 * h * tw) / (
            6 * b * tf + h * tw)
    return None


# ------------------------------------------------------------- the solve
def _defaults_mesh(typ, g, pl, L, mesh):
    mesh = dict(mesh or {})
    n_web = int(mesh.get("n_web", 8))
    n_fl = int(mesh.get("n_flange", 4))
    if n_web < 1 or n_fl < 1:
        raise ValueError("mesh.n_web and mesh.n_flange must be >= 1")
    sizes = []
    for p in pl:
        n = n_web if (p["role"] == "web" or typ == "rect_tube") else n_fl
        sizes.append(math.dist(p["p0"], p["p1"]) / n)
    webs = [math.dist(p["p0"], p["p1"]) for p in pl if p["role"] == "web"]
    hw = max(webs) if webs else max(math.dist(p["p0"], p["p1"]) for p in pl)
    if mesh.get("n_long") is not None:
        n_long = int(mesh["n_long"])
    else:
        dx = max(min(hw / 4.0, 3.0 * min(sizes)), min(sizes))
        n_long = int(min(160, max(12, math.ceil(L / dx))))
    if n_long < 2:
        raise ValueError("mesh.n_long must be >= 2")
    return n_long, n_web, n_fl


def member_buckling(model, uid: str, forces: Optional[dict] = None,
                    boundary: str = "fork", mesh: Optional[dict] = None,
                    num_modes: int = 4, section: Optional[dict] = None,
                    load_position: str = "shear_center",
                    length: Optional[float] = None) -> dict:
    """BucklingFEM of frame member ``uid`` (see module doc)."""
    import openseespy.opensees as ops
    from skyframe.engine import geom_stiffness as _gk
    from skyframe.engine import lanczos as _lz
    from skyframe.engine import opensees_engine as _oe

    members = {m.uid: m for m in model.members}
    if uid not in members:
        raise ValueError(f"unknown frame member {uid!r}")
    m = members[uid]
    sec = model.sections.get(m.section)
    if sec is None:
        raise ValueError(f"member {uid!r}: unknown section {m.section!r}")
    mat = model.materials.get(sec.material)
    if mat is None:
        raise ValueError(f"section {sec.name!r}: unknown material")
    if boundary not in BOUNDARIES:
        raise ValueError(f"boundary must be one of {'|'.join(BOUNDARIES)}")
    if load_position not in LOAD_POSITIONS:
        raise ValueError("load_position must be one of "
                         f"{'|'.join(LOAD_POSITIONS)}")
    num_modes = int(num_modes)
    if not 1 <= num_modes <= 20:
        raise ValueError("num_modes must be 1..20")
    F = {k: 0.0 for k in FORCE_KEYS}
    for k, v in (forces or {}).items():
        if k not in F:
            raise ValueError(f"unknown force key {k!r} "
                             f"(allowed {', '.join(FORCE_KEYS)})")
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(float(v)):
            raise ValueError(f"forces.{k} must be a finite number")
        F[k] = float(v)
    typ, g = section_geometry(model, sec, section)
    pl = plates(typ, g)
    props = midline_props(pl)
    L = float(length) if length is not None else float(m.length)
    if not L > 0.0:
        raise ValueError("member length must be > 0")
    n_long, n_web, n_fl = _defaults_mesh(typ, g, pl, L, mesh)
    E, nu = float(mat.E), float(mat.nu)
    G = E / (2.0 * (1.0 + nu))
    warn: List[str] = []

    # ---- section nodes (y3, y2) with plate segment lists
    skey = {}
    spts: List[Tuple[float, float]] = []

    def snode(p):
        k = (round(p[0], 9), round(p[1], 9))
        if k not in skey:
            skey[k] = len(spts)
            spts.append((float(p[0]), float(p[1])))
        return skey[k]
    segs: List[Tuple[int, int, float, str]] = []
    plate_nodes: List[List[int]] = []
    for p in pl:
        n = n_web if (p["role"] == "web" or typ == "rect_tube") else n_fl
        ids = []
        for k in range(n + 1):
            s = k / n
            ids.append(snode((p["p0"][0] + s * (p["p1"][0] - p["p0"][0]),
                              p["p0"][1] + s * (p["p1"][1] - p["p0"][1]))))
        plate_nodes.append(ids)
        for a, b in zip(ids[:-1], ids[1:]):
            segs.append((a, b, p["t"], p["role"]))
    ns = len(spts)
    if ns * (n_long + 1) > MAX_NODES:
        raise ValueError(f"mesh too large ({ns * (n_long + 1)} nodes > "
                         f"{MAX_NODES}); reduce mesh.n_long / n_web / "
                         "n_flange")
    ends = [0] * ns
    for ids in plate_nodes:
        ends[ids[0]] += 1
        ends[ids[-1]] += 1
    junction = [i for i in range(ns) if ends[i] >= 2]   # plate fold lines
    web_nodes = sorted({i for (a, b, _t, r) in segs if r == "web"
                        for i in (a, b)})
    if typ == "rect_tube":
        web_nodes = list(range(ns))
    xs = [L * k / n_long for k in range(n_long + 1)]

    def tag(ix, i):
        return ix * ns + i + 1

    # ---- OpenSees domain (standalone; the engine's reused domain is
    # released first so no engine mistakes this domain for its own)
    _oe._REUSE["owner"] = None
    _oe._REUSE["loaded"] = False
    _oe._REUSE["factored"] = None
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    coords: Dict[int, Tuple[float, float, float]] = {}
    for ix, x in enumerate(xs):
        for i, (y3, y2) in enumerate(spts):
            t = tag(ix, i)
            ops.node(t, x, y3, y2)
            coords[t] = (x, y3, y2)
    sec_tag: Dict[float, int] = {}
    for (_a, _b, t, _r) in segs:
        if t not in sec_tag:
            sec_tag[t] = len(sec_tag) + 1
            ops.section("ElasticMembranePlateSection", sec_tag[t], E, nu, t,
                        0.0)
    quads: List[List[int]] = []
    et = 0
    for ix in range(n_long):
        for (a, b, t, _r) in segs:
            et += 1
            nd = [tag(ix, a), tag(ix + 1, a), tag(ix + 1, b), tag(ix, b)]
            ops.element("ShellMITC4", et, *nd, sec_tag[t])
            quads.append(nd)
    xc, yc = props["xc"], props["yc"]
    centre = min(web_nodes, key=lambda i: (spts[i][0] - xc) ** 2
                 + (spts[i][1] - yc) ** 2)
    use_tf = False
    if boundary == "fork":
        for ix in (0, n_long):
            for i in web_nodes:
                fx = 1 if (ix == 0 and i == centre) else 0
                ops.fix(tag(ix, i), fx, 1, 1, 0, 0, 0)
    else:
        for i in range(ns):
            ops.fix(tag(0, i), 1, 1, 1, 1, 1, 1)
            ops.fix(tag(n_long, i), 0, 1, 1, 1, 1, 1)
        for i in range(ns):
            if i != centre:
                ops.equalDOF(tag(n_long, centre), tag(n_long, i), 1)
        use_tf = True
        if any(F[k] for k in ("M3i", "M3j", "M2i", "M2j")):
            warn.append("end moments are reacted by the fixed ends and "
                        "ignored (boundary 'fixed')")
    # ---- reference loads (global flat vector over the node tags)
    tags = sorted(coords)
    space = _gk.Space(tags)
    f_ref = np.zeros(space.n)
    A, I33, I22 = props["A"], props["I33"], props["I22"]

    def sigma(i, M3, M2):
        y3, y2 = spts[i]
        s = F["P"] / A
        if boundary == "fork":
            s += -M3 * (y2 - yc) / I33 + M2 * (y3 - xc) / I22
        return s
    for ix, sgn, M3, M2 in ((0, -1.0, F["M3i"], F["M2i"]),
                            (n_long, 1.0, F["M3j"], F["M2j"])):
        for (a, b, t, _r) in segs:
            Ls = math.dist(spts[a], spts[b])
            sa, sb = sigma(a, M3, M2), sigma(b, M3, M2)
            fa = t * Ls * (2 * sa + sb) / 6.0
            fb = t * Ls * (sa + 2 * sb) / 6.0
            f_ref[6 * space.idx[tag(ix, a)]] += sgn * fa
            f_ref[6 * space.idx[tag(ix, b)]] += sgn * fb
    if F["w2"]:
        if load_position == "shear_center":
            line = centre
        else:
            jn = junction or web_nodes
            pick = max if load_position == "top" else min
            line = pick(jn, key=lambda i: (spts[i][1], -abs(spts[i][0] - xc)))
        dx = L / n_long
        for ix in range(n_long + 1):
            w = F["w2"] * dx * (0.5 if ix in (0, n_long) else 1.0)
            f_ref[6 * space.idx[tag(ix, line)] + 2] -= w
    from skyframe.engine.sparse_solve import StandaloneSolver
    solver = StandaloneSolver(space, use_tf)
    solver(f_ref)                                    # reference solve
    Kg = _gk.SymOp(space.n)
    for et_i, nd in enumerate(quads, start=1):
        ops.eleResponse(et_i, "forces")
        vals = ops.eleResponse(et_i, "stresses")
        tens = _gk.membrane_tensors("ShellMITC4", vals)
        P = np.array([coords[t] for t in nd])
        Kg.add(space.dofs(nd), _gk.shell_kg_global("ShellMITC4", P, tens))
    free = np.ones(space.n, dtype=bool)
    if not np.any(f_ref):
        raise ValueError("no load: give at least one non-zero force")

    def gmul(x):
        return -Kg.matvec(x)
    lr = _lz.eigs(solver, gmul, free, num_modes, tol=1e-9)
    if not lr.converged:
        warn.append("Lanczos did not reach the residual tolerance")
    factors = [1.0 / t for t in lr.thetas]
    if not factors:
        warn.append("no positive buckling factor: the forces put no part "
                    "of the member in compression")
    # ---- modes: translations per node + classification
    modes = []
    shapes: Dict[str, Dict[str, List[float]]] = {}
    for k, x in enumerate(lr.vectors):
        X = x.reshape(-1, 6)
        imax = int(np.argmax(np.abs(X[:, :3])))
        if X[:, :3].reshape(-1)[imax] < 0.0:
            X = -X
        mx = float(np.max(np.abs(X[:, :3]))) or 1.0
        X = X / mx
        info = _classify(X, space, tag, n_long, ns, spts, xc, yc, props,
                         junction)
        info.update({"mode": k + 1, "factor": float(factors[k])})
        modes.append(info)
        shapes[str(k + 1)] = {str(t): [round(float(v), 6)
                                       for v in X[space.idx[t], :3]]
                              for t in tags}
    ops.wipe()
    refs = _references(typ, g, pl, props, E, G, nu, L)
    return {
        "member": uid, "section": {"name": sec.name, "type": typ,
                                   "dims": g},
        "material": {"name": mat.name, "E": E, "nu": nu},
        "length": L, "boundary": boundary, "load_position": load_position,
        "forces": F,
        "mesh": {"n_long": n_long, "n_web": n_web, "n_flange": n_fl,
                 "nodes": len(tags), "shells": len(quads),
                 "equations": int(space.n)},
        "section_props": {**props, "Cw": warning_none(
            warping_constant(typ, g, pl))},
        "factors": [float(f) for f in factors],
        "modes": modes,
        "nodes": {str(t): [float(c) for c in coords[t]] for t in tags},
        "quads": quads,
        "shapes": shapes,
        "references": refs,
        "solver": {"system": solver.system, "solves": int(solver.count),
                   "converged": bool(lr.converged),
                   "max_residual": float(max(lr.residuals)
                                         if lr.residuals else 0.0)},
        "warnings": warn,
    }


def warning_none(v):
    return None if v is None else float(v)


def _classify(X, space, tag, n_long, ns, spts, xc, yc, props, junction
              ) -> dict:
    """Heuristic mode class (see module doc)."""
    r = math.sqrt((props["I33"] + props["I22"]) / props["A"])
    tot = fit = 0.0
    sv = sw = st = 0.0
    dom = []
    jmax = allmax = 0.0
    for ix in range(n_long + 1):
        rows, rhs = [], []
        for i in range(ns):
            y3, y2 = spts[i]
            u = X[space.idx[tag(ix, i)]]
            rows.append([1.0, 0.0, -(y2 - yc)])
            rows.append([0.0, 1.0, (y3 - xc)])
            rhs.extend([u[1], u[2]])
            amp = math.hypot(u[1], u[2])
            allmax = max(allmax, amp)
            if i in junction:
                jmax = max(jmax, amp)
        Am = np.array(rows)
        b = np.array(rhs)
        c, *_ = np.linalg.lstsq(Am, b, rcond=None)
        res = b - Am @ c
        tot += float(b @ b)
        fit += float(b @ b - res @ res)
        sv += c[0] ** 2
        sw += c[1] ** 2
        st += (c[2] * r) ** 2
        k = int(np.argmax(np.abs(b)))
        dom.append(float(b[k]))
    gf = fit / tot if tot > 0 else 0.0
    # dominant line: the node/direction with the largest amplitude
    best = None
    for i in range(ns):
        for d in (1, 2):
            line = [X[space.idx[tag(ix, i)], d] for ix in range(n_long + 1)]
            a = max(abs(v) for v in line)
            if best is None or a > best[0]:
                best = (a, line)
    line = best[1] if best else []
    thr = 0.05 * (best[0] if best else 0.0)
    signs = [1 if v > thr else (-1 if v < -thr else 0) for v in line]
    signs = [s for s in signs if s]
    hw = 1 + sum(1 for a, b in zip(signs[:-1], signs[1:]) if a != b) \
        if signs else 0
    s_all = sv + sw + st
    if gf >= 0.85 and s_all > 0:
        ft, fv, fw = st / s_all, sv / s_all, sw / s_all
        if ft > 0.25 and fv > 0.15:
            cls = "lateral-torsional"
        elif ft >= 0.6:
            cls = "torsional"
        elif fv >= fw:
            cls = "flexural-minor"
        else:
            cls = "flexural-major"
        group = "global"
    else:
        group = "local" if (allmax > 0 and jmax < 0.25 * allmax) \
            else "distortional"
        cls = group
    return {"class": cls, "group": group, "global_fraction": round(gf, 4),
            "half_waves": int(hw)}


def _references(typ, g, pl, props, E, G, nu, L) -> dict:
    out = {"P_euler_minor": math.pi ** 2 * E * props["I22"] / L ** 2,
           "P_euler_major": math.pi ** 2 * E * props["I33"] / L ** 2}
    Cw = warping_constant(typ, g, pl)
    if typ == "I" and Cw is not None:
        GJ = G * props["J"]
        out["Mcr_ltb"] = (math.pi / L) * math.sqrt(E * props["I22"] * GJ) \
            * math.sqrt(1.0 + math.pi ** 2 * E * Cw / (GJ * L * L))
    D = E / (12.0 * (1.0 - nu * nu))
    plates_out = []
    for p in pl:
        b = math.dist(p["p0"], p["p1"])
        if p["role"] == "web":
            k = 4.0 if typ != "tee" else 0.425
        else:
            k = 0.425 if typ != "rect_tube" else 4.0
        plates_out.append({"role": p["role"], "b": b, "t": p["t"], "k": k,
                           "sigma_cr": k * math.pi ** 2 * D
                           * (p["t"] / b) ** 2})
    out["plates"] = plates_out
    return out


def forces_from_case(results_case: dict, uid: str, L: float) -> dict:
    """BucklingFEM forces from a static case's ``member_stations``:
    mean N, end M3 / M2 and the uniform ``w2`` that reproduces the
    mid-span deviation of M3 from its chord (``w = 8 dM / L^2``)."""
    st = (results_case.get("member_stations") or {}).get(uid)
    if not st:
        raise ValueError(f"no member stations for {uid!r} in that case")
    N = st.get("N") or [0.0]
    M3 = st.get("M3") or [0.0]
    M2 = st.get("M2") or [0.0]
    xs = st.get("x") or [0.0]
    out = {"P": float(sum(N) / len(N)), "M3i": float(M3[0]),
           "M3j": float(M3[-1]), "M2i": float(M2[0]),
           "M2j": float(M2[-1]), "w2": 0.0}
    if len(xs) >= 3 and L > 0:
        mid = len(xs) // 2
        x = float(xs[mid])
        chord = M3[0] + (M3[-1] - M3[0]) * x / L
        dev = float(M3[mid]) - chord
        # sagging M3 > 0 under a downward (+w2) load: M = w x (L - x) / 2
        denom = x * (L - x) / 2.0
        if denom > 0:
            out["w2"] = dev / denom
    return out
