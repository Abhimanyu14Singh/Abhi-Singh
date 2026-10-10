"""Shared helpers for the SkyFrame independent-verification suite.

Every verification test builds a small model, runs the REAL engine
(``OpenSeesEngine``) and compares one or more quantities against a
PUBLISHED or closed-form reference with :func:`check`.  ``check`` both
asserts (with an honest relative tolerance chosen from the element
formulation, never widened to hide an error) and records the comparison
so ``make_verification_md.py`` can regenerate ``docs/VERIFICATION.md``.

Units: whatever is CONSISTENT for the problem.  SkyFrame's engine is unit
agnostic (it only multiplies numbers), so a problem published in kip-ft
or in non-dimensional units is entered verbatim; problems in SI use the
SkyFrame default kN, m, tonne, s, kPa.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 NodalLoad, NodalMass, PointSupport,
                                 ShellRegion, ShellSection)
from skyframe.engine.opensees_engine import OpenSeesEngine

#: every comparison made in this pytest session (read by conftest.py)
RECORDS: List[dict] = []

FIXED = (1, 1, 1, 1, 1, 1)
PINNED = (1, 1, 1, 0, 0, 0)


def rel_err(value: float, reference: float) -> float:
    """Signed relative error ``(value - reference) / |reference|``."""
    return (value - reference) / abs(reference)


def check(problem: str, source: str, quantity: str, reference: float,
          value: float, tol: float, *, family: str, note: str = "",
          abs_floor: float = 0.0) -> float:
    """Record and assert ``|value - reference| <= tol * |reference|``.

    ``abs_floor`` (optional) is an absolute floor for quantities whose
    reference is (near) zero.  Returns the signed relative error.
    """
    if reference == 0.0:
        err = value
        ok = abs(value) <= abs_floor
    else:
        err = rel_err(value, reference)
        ok = abs(err) <= tol or abs(value - reference) <= abs_floor
    RECORDS.append(dict(family=family, problem=problem, source=source,
                        quantity=quantity, reference=float(reference),
                        value=float(value), err=float(err), tol=float(tol),
                        abs_floor=float(abs_floor),
                        status="PASS" if ok else "FAIL", note=note))
    assert ok, (f"{problem} / {quantity}: SkyFrame {value:.6g} vs reference "
                f"{reference:.6g} (err {100 * err:+.3f}% > tol "
                f"{100 * tol:.3g}%)")
    return err


# --------------------------------------------------------------------------- #
# model building
# --------------------------------------------------------------------------- #
def new_model(name: str, E: float, nu: float = 0.3, heights=(1.0,),
              unit_weight: float = 0.0) -> BuildingModel:
    """Bare model: one material ``MAT``, no diaphragms, no modal stage
    (``num_modes = 0``), stories only as reporting labels."""
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("MAT", E=E, nu=nu, unit_weight=unit_weight))
    mdl.set_stories(list(heights))
    return mdl


def section(mdl: BuildingModel, name: str, A: float, I33: float,
            I22: Optional[float] = None, J: Optional[float] = None
            ) -> FrameSection:
    """General section with explicit properties (I22/J default to I33 /
    2*I33 -- irrelevant for planar problems with out-of-plane restraint)."""
    I22 = I33 if I22 is None else I22
    J = 2.0 * I33 if J is None else J
    return mdl.add_section(FrameSection(name, "MAT", A, I33, I22, J))


def chain(mdl: BuildingModel, sec: str, p0, p1, n: int, prefix: str,
          kind: str = "beam", story: str = "Story1") -> List[str]:
    """Split the straight line p0->p1 into ``n`` equal members."""
    uids = []
    for k in range(n):
        a = [p0[i] + (p1[i] - p0[i]) * k / n for i in range(3)]
        b = [p0[i] + (p1[i] - p0[i]) * (k + 1) / n for i in range(3)]
        uid = f"{prefix}{k + 1}"
        mdl.add_member(kind, sec, tuple(a), tuple(b), story=story, uid=uid)
        uids.append(uid)
    return uids


def support(mdl: BuildingModel, pt, restraints=FIXED) -> None:
    """Add (or MERGE into an existing) point support at ``pt``."""
    pt = tuple(float(v) for v in pt)
    for k, s in enumerate(mdl.supports):
        if all(abs(s.point[i] - pt[i]) < 1e-9 for i in range(3)):
            mdl.supports[k] = PointSupport(pt, tuple(
                max(a, b) for a, b in zip(s.restraints, restraints)))
            return
    mdl.supports.append(PointSupport(pt, tuple(restraints)))


def load(mdl: BuildingModel, pattern: str, pt, **comps) -> None:
    mdl.pattern(pattern).nodal_loads.append(
        NodalLoad(tuple(map(float, pt)), **comps))


def planar_xz(mdl: BuildingModel) -> None:
    """Restrict the model to the global X-Z plane (ETABS/SAP "Set Active
    Degrees of Freedom" = UX, UZ, RY)."""
    mdl.active_dof = ["UX", "UZ", "RY"]


# --------------------------------------------------------------------------- #
# running / reading
# --------------------------------------------------------------------------- #
def engine(mdl: BuildingModel) -> OpenSeesEngine:
    mdl.validate()
    return OpenSeesEngine(mdl)


def tag_at(eng: OpenSeesEngine, pt, tol: float = 1e-6) -> int:
    """FE node tag at coordinates ``pt`` (after a build)."""
    for t, c in eng._asm.node_coords.items():
        if all(abs(c[i] - pt[i]) < tol for i in range(3)):
            return t
    raise AssertionError(f"no FE node at {pt}")


def disp(eng: OpenSeesEngine, res, pt, dof: int) -> float:
    return res.node_disp[tag_at(eng, pt)][dof]


def run_case(mdl: BuildingModel, case: str):
    eng = engine(mdl)
    res = eng.run_static(case)
    return eng, res


def pct(x: float) -> str:
    return f"{100.0 * x:+.3f}%"


# --------------------------------------------------------------------------- #
# shells
# --------------------------------------------------------------------------- #
def shell_section(mdl: BuildingModel, name: str, t: float) -> ShellSection:
    return mdl.add_shell_section(ShellSection(name, "MAT", t))


def grid_shell(mdl: BuildingModel, sec: str, xyz, nu: int, nv: int,
               prefix: str = "E", kind: str = "wall"
               ) -> Dict[Tuple[int, int], tuple]:
    """Mesh a (curved) surface as ``nu x nv`` single-element planar quads.

    ``xyz(i, j)`` returns the 3D grid point (i in 0..nu, j in 0..nv).  Each
    cell becomes its own 4-corner ShellRegion with mesh_size larger than the
    cell, so the SkyFrame mesher emits exactly ONE ShellMITC4 per cell and
    shared corners merge (dedup 1e-6).  Every cell must be planar (true for
    the chord quads of cylinders and the lat/long trapezoids of spheres).
    Returns {(i, j): point}.
    """
    pts = {(i, j): tuple(float(c) for c in xyz(i, j))
           for i in range(nu + 1) for j in range(nv + 1)}
    for i in range(nu):
        for j in range(nv):
            c = [pts[(i, j)], pts[(i + 1, j)], pts[(i + 1, j + 1)],
                 pts[(i, j + 1)]]
            size = 10.0 * max(math.dist(c[0], c[2]), math.dist(c[1], c[3]))
            mdl.shells.append(ShellRegion(f"{prefix}{i}_{j}", kind, "shell",
                                          sec, c, mesh_size=size))
    return pts
