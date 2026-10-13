"""Gap cases (not in CSI's ETABS analysis set): plate bending and the
shell patch tests.

GAP-PLATE -- simply supported square plate under uniform load
(Timoshenko & Woinowsky-Krieger, *Theory of Plates and Shells*, Table 8;
exact Navier series w = 0.00406235 q a^4 / D, M_x = 0.0478864 q a^2 at the
centre, nu = 0.3).  a = 1, t = 0.01 (a/t = 100), E = 200 GPa, q = 1; hard
simple support (w = 0 and the tangential rotation = 0 on every edge);
SkyFrame default ``shell`` (ShellMITC4) on a 32 x 32 mesh.  The centre
moment is the mean of the 4 quads around the centre (their centroids sit
h/2 off the centre -- a -0.2 % sampling effect).

GAP-PATCH -- MacNeal & Harder (1985) patch test (Finite Elem. Anal. Des.
1, "A proposed standard set of problems to test finite element
accuracy"): rectangle 0.24 x 0.12 with interior nodes (0.04, 0.02), (0.18,
0.03), (0.16, 0.08), (0.08, 0.08) -> 5 distorted quads; E = 1e6, nu =
0.25, t = 0.001.  Boundary displacements prescribed from the exact fields
(SkyFrame ground displacements on the 4 corner supports):

* membrane: u = 1e-3 (x + y/2), v = 1e-3 (y + x/2) -> N_x = N_y = 1.3333,
  N_xy = 0.4 (sigma 1333 / 400);
* bending: w = 1e-3 (1 + x + y + x^2 + xy + y^2) / 2 with theta_x =
  dw/dy, theta_y = -dw/dx -> M_x = M_y = 1.111e-7, M_xy = 3.333e-8.

An element passes when the interior nodes reproduce the linear /
quadratic field and every element carries the constant stress resultant.
Stress resultants are compared through their in-plane invariants (N_x +
N_y and the Mohr radius sqrt(((N_x - N_y)/2)^2 + N_xy^2)), which do not
depend on each distorted element's local axes; magnitudes are compared
(the moment sign convention, M = -D w'' in Timoshenko's form, differs
from the positive values tabulated by MacNeal & Harder).
"""

from __future__ import annotations

import math

from skyframe.core.loads_ext import GroundDisplacement
from skyframe.core.model import AreaLoad, ShellRegion, ShellSection

from ._core import (CaseResult, FIXED, disp, engine, new_model, sig,
                    support, timed)
from ._svg import Sketch

TITLE = "Plate bending and shell patch tests"
TIER = "gap"

# --------------------------------------------------------------------------- #
# square plate
# --------------------------------------------------------------------------- #
W_REF, M_REF = 0.00406235, 0.0478864


def square_plate(n: int = 32):
    a, t, E, nu, q = 1.0, 0.01, 2.0e8, 0.3, 1.0
    m = new_model("plate", E, nu=nu, heights=[1.0])
    m.add_shell_section(ShellSection("SH", "MAT", t))
    m.shells.append(ShellRegion("S1", "slab", "shell", "SH",
                                [(0, 0, 0), (a, 0, 0), (a, a, 0), (0, a, 0)],
                                mesh_size=a / n))
    for k in range(n + 1):
        s = a * k / n
        for p, r in (((s, 0, 0), (0, 0, 1, 0, 1, 0)),
                     ((s, a, 0), (0, 0, 1, 0, 1, 0)),
                     ((0, s, 0), (0, 0, 1, 1, 0, 0)),
                     ((a, s, 0), (0, 0, 1, 1, 0, 0))):
            support(m, p, r)
    support(m, (0, 0, 0), (1, 1, 1, 0, 0, 1))
    support(m, (a, 0, 0), (0, 1, 1, 0, 0, 0))
    m.pattern("Q").area_loads.append(AreaLoad("S1", q))
    m.add_case("Q", {"Q": 1.0})
    eng = engine(m)
    r = eng.run_static("Q")
    D = E * t ** 3 / (12 * (1 - nu ** 2))
    w = -disp(eng, r, (a / 2, a / 2, 0), 2) * D / (q * a ** 4)
    cen = [[sum(eng._asm.node_coords[tg][k] for tg in qd["nodes"]) / 4
            for k in range(2)] for qd in eng._asm.shell_quads]
    near = sorted(range(len(cen)), key=lambda i: math.dist(
        cen[i], (a / 2, a / 2)))[:4]
    M = abs(sum(r.shell_forces[i][3] for i in near) / 4) / (q * a * a)
    return w, M


# --------------------------------------------------------------------------- #
# patch tests
# --------------------------------------------------------------------------- #
OUT = [(0.0, 0.0), (0.24, 0.0), (0.24, 0.12), (0.0, 0.12)]
INN = [(0.04, 0.02), (0.18, 0.03), (0.16, 0.08), (0.08, 0.08)]
ELEMS = [(OUT[0], OUT[1], INN[1], INN[0]), (OUT[1], OUT[2], INN[2], INN[1]),
         (OUT[2], OUT[3], INN[3], INN[2]), (OUT[3], OUT[0], INN[0], INN[3]),
         (INN[0], INN[1], INN[2], INN[3])]
PE, PNU, PT = 1.0e6, 0.25, 0.001


def membrane_field(x, y):
    return (1e-3 * (x + y / 2), 1e-3 * (y + x / 2))


def bending_field(x, y):
    w = 1e-3 * (1 + x + y + x * x + x * y + y * y) / 2
    dwdx = 1e-3 * (1 + 2 * x + y) / 2
    dwdy = 1e-3 * (1 + x + 2 * y) / 2
    return w, dwdy, -dwdx


def patch(kind: str, shell_type: str = "shell"):
    """(interior-node field error max, node-3 value, N/M trace, radius
    per element)."""
    m = new_model("patch", PE, nu=PNU, heights=[1.0])
    sec = m.add_shell_section(ShellSection("SH", "MAT", PT))
    sec.shell_type = shell_type
    for i, e in enumerate(ELEMS):
        m.shells.append(ShellRegion(f"E{i + 1}", "slab", "shell", "SH",
                                    [(x, y, 0.0) for x, y in e],
                                    mesh_size=10.0))
    pat = m.pattern("U")
    for x, y in OUT:
        support(m, (x, y, 0.0), FIXED)
        if kind == "membrane":
            u, v = membrane_field(x, y)
            pat.ground_displacements.append(
                GroundDisplacement((x, y, 0.0), ux=u, uy=v))
        else:
            w, rx, ry = bending_field(x, y)
            pat.ground_displacements.append(
                GroundDisplacement((x, y, 0.0), uz=w, rx=rx, ry=ry))
    m.add_case("U", {"U": 1.0})
    eng = engine(m)
    r = eng.run_static("U")
    err = 0.0
    for x, y in INN:
        if kind == "membrane":
            ex = membrane_field(x, y)
            got = (disp(eng, r, (x, y, 0), 0), disp(eng, r, (x, y, 0), 1))
        else:
            ex = bending_field(x, y)
            got = tuple(disp(eng, r, (x, y, 0), d) for d in (2, 3, 4))
        err = max(err, max(abs(g - e) / max(abs(e), 1e-30)
                           for g, e in zip(got, ex)))
    x3, y3 = INN[2]
    node3 = (disp(eng, r, (x3, y3, 0), 0) if kind == "membrane"
             else disp(eng, r, (x3, y3, 0), 2))
    off = 0 if kind == "membrane" else 3
    inv = []
    for f in r.shell_forces.values():
        nx, ny, nxy = f[off], f[off + 1], f[off + 2]
        # magnitudes: plate-moment sign conventions differ (Timoshenko
        # M = -D w'' vs the patch-test tables' positive values)
        inv.append((abs(nx + ny), math.hypot((nx - ny) / 2, nxy)))
    return err, node3, inv


def run_id(case_id: str) -> CaseResult:
    return _plate() if case_id == "GAP-PLATE" else _patch()


@timed
def _plate() -> CaseResult:
    res = CaseResult(
        "GAP-PLATE", "Simply supported square plate (Timoshenko)", TIER,
        source="Timoshenko & Woinowsky-Krieger, Theory of Plates and "
               "Shells, Table 8 (Navier series)",
        tests="Plate bending of the default shell element (ShellMITC4)")
    res.inputs = [("Plate", "a = 1, t = 0.01 (a/t = 100), nu = 0.3"),
                  ("Load", "uniform q = 1"),
                  ("Supports", "hard simple support on all edges")]
    res.assumptions = ["32 x 32 ShellMITC4 mesh (SkyFrame default shell).",
                       "Centre moment = mean of the 4 centre quads "
                       "(centroids h/2 off the centre)."]
    res.mesh = "32 x 32 ShellMITC4"
    w, M = square_plate(32)
    res.add("Centre deflection w D / q a^4", "plate centre", "-",
            sig(W_REF), "Timoshenko Table 8", None, w)
    res.add("Centre moment M_x / q a^2", "4 centre quads", "-", sig(M_REF),
            "Timoshenko Table 8", None, M, cls="internal")
    sk = Sketch(200, 200)
    sk.poly([(0, 0), (1, 0), (1, 1), (0, 1)], "wall")
    for i in range(1, 8):
        sk.line((i / 8, 0), (i / 8, 1), "mesh").line((0, i / 8), (1, i / 8),
                                                     "mesh")
    sk.dot((0.5, 0.5), "cm")
    res.svg = sk.render("Square plate")
    return res


@timed
def _patch() -> CaseResult:
    res = CaseResult(
        "GAP-PATCH", "Shell patch tests (MacNeal & Harder)", TIER,
        source="MacNeal & Harder (1985), Finite Elem. Anal. Des. 1, "
               "patch test (membrane and bending)",
        tests="Constant-strain membrane and constant-curvature bending on a "
              "distorted 5-element patch (default shell, ShellMITC4)")
    res.inputs = [("Patch", "0.24 x 0.12, 5 distorted quads"),
                  ("Material", "E = 1e6, nu = 0.25, t = 0.001")]
    res.assumptions = [
        "Boundary displacements prescribed at the 4 corners from the exact "
        "fields; stress resultants compared through in-plane invariants.",
        "Rows give element 5 (the interior quad); the note gives the "
        "spread over all 5 elements and the interior-node field error."]
    sx = PE / (1 - PNU ** 2) * (1e-3 + PNU * 1e-3)
    txy = PE / (2 * (1 + PNU)) * 1e-3
    D = PE * PT ** 3 / (12 * (1 - PNU ** 2))
    Mx = D * (1 + PNU) * 1e-3
    Mxy = D * (1 - PNU) / 2 * 1e-3
    for kind, tr, rad, n3 in (
            ("membrane", 2 * sx * PT, txy * PT,
             membrane_field(*INN[2])[0]),
            ("bending", 2 * Mx, Mxy, bending_field(*INN[2])[0])):
        err, node3, inv = patch(kind)
        spread = max(max(abs(a / tr - 1), abs(b / rad - 1)) for a, b in inv)
        note = (f"interior-node field error {err:.1e}; max invariant error "
                f"over 5 elements {spread:.1e}")
        q = "u" if kind == "membrane" else "w"
        res.add(f"{q} at interior node (0.16, 0.08)", f"{kind} patch", "-",
                sig(n3), "exact field", None, node3, group=kind, note=note)
        lab = "N" if kind == "membrane" else "M"
        res.add(f"{lab}_x + {lab}_y", f"{kind} patch, element 5", "-",
                sig(tr), "exact field", None, inv[4][0], cls="internal",
                group=kind)
        res.add(f"Mohr radius of {lab} (= |{lab}_xy|)",
                f"{kind} patch, element 5", "-", sig(rad), "exact field",
                None, inv[4][1], cls="internal", group=kind)
    sk = Sketch(260, 160)
    for e in ELEMS:
        sk.poly(list(e), "wall")
    for p in INN:
        sk.dot(p, "col")
    res.svg = sk.render("MacNeal-Harder patch")
    return res
