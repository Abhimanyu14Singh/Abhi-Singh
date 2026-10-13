"""Benchmark-pack core: result rows, CSI acceptance rating, model helpers.

CSI *Software Verification Examples for ETABS* (ISO ETA041825M6 Rev. 29,
Feb 2026), section "Acceptance criteria":

* **exact** -- no difference within the larger of the program output
  precision and the independent-result precision;
* **acceptable** -- |difference| <= 5 % for forces, moments and
  displacements (global response quantities: displacements, story shears,
  reactions, periods, eigenvalues, mode-shape ordinates), <= 10 % for
  internal forces and stresses (member / station forces);
* **unacceptable** -- beyond those limits.

Percent difference = ``100 * (value / independent - 1)``.  Where an example
has several area-element meshes only the finest needs to meet the limits.

Every published number is entered as a STRING exactly as printed so the
output precision (number of decimals) of the reference is known and the
"exact" rule can be applied literally: SkyFrame's value rounded to the
reference's printed precision must equal the reference (``|value - ref|
<= half a unit of the last printed digit``).
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 NodalLoad, NodalMass, PointSupport,
                                 ShellSection)
from skyframe.engine.opensees_engine import OpenSeesEngine

#: CSI acceptance limits (percent) by quantity class
LIMITS = {"global": 5.0, "internal": 10.0, "experimental": 25.0}

CSI_DOC = ("CSI, Software Verification Examples for ETABS, ISO ETA041825M6 "
           "Rev. 29 (Feb 2026), Analysis Examples 1-15")

FIXED = (1, 1, 1, 1, 1, 1)
PINNED = (1, 1, 1, 0, 0, 0)


# --------------------------------------------------------------------------- #
# published numbers
# --------------------------------------------------------------------------- #
def num(s) -> Tuple[Optional[float], Optional[float]]:
    """(value, half-unit precision) of a published number string.

    ``"1,687.50"`` -> (1687.5, 0.005); ``None``/``""`` -> (None, None);
    a float is taken at face value with a 1e-9 relative precision."""
    if s is None or s == "":
        return None, None
    if isinstance(s, (int, float)):
        v = float(s)
        return v, max(abs(v) * 1e-9, 1e-12)
    t = str(s).replace(",", "").strip()
    v = float(t)
    mant = t.lower().split("e")[0]
    dec = len(mant.split(".")[1]) if "." in mant else 0
    exp = int(t.lower().split("e")[1]) if "e" in t.lower() else 0
    return v, 0.5 * 10.0 ** (exp - dec)


def pct(value: Optional[float], ref: Optional[float]) -> Optional[float]:
    """CSI percent difference ``100 (value/ref - 1)`` (None if undefined)."""
    if value is None or ref is None or ref == 0.0:
        return None
    return 100.0 * (value / ref - 1.0)


@dataclass
class Row:
    """One compared quantity."""

    quantity: str
    location: str
    units: str
    independent: Optional[str]          # published string (or None)
    independent_src: str
    etabs: Optional[str]                # published ETABS string (or None)
    skyframe: Optional[float]
    cls: str = "global"                 # "global" | "internal"
    note: str = ""
    # filled by rate()
    pct_ind: Optional[float] = None
    pct_etabs: Optional[float] = None
    rating: str = ""                    # exact | acceptable | unacceptable
    group: str = ""                     # sub-table label (e.g. "Case I")

    def rate(self) -> "Row":
        ref, prec = num(self.independent)
        et, eprec = num(self.etabs)
        v = self.skyframe
        self.pct_ind = pct(v, ref)
        self.pct_etabs = pct(v, et)
        if v is None or ref is None:
            self.rating = "n/a"
            return self
        if abs(v - ref) <= prec * (1.0 + 1e-9):
            self.rating = "exact"
        elif ref != 0.0 and abs(self.pct_ind) <= LIMITS[self.cls]:
            self.rating = "acceptable"
        else:
            self.rating = "unacceptable"
        return self

    def to_dict(self) -> dict:
        d = asdict(self)
        d["independent_value"] = num(self.independent)[0]
        d["etabs_value"] = num(self.etabs)[0]
        return d


@dataclass
class CaseResult:
    """Everything the report needs about one benchmark case."""

    id: str
    title: str
    tier: str                    # "1" | "2" | "gap" | "blocked"
    source: str                  # citation of the independent reference
    tests: str = ""              # what the example exercises
    status: str = "run"          # run | partial | blocked | error
    inputs: List[Tuple[str, str]] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    differences: List[str] = field(default_factory=list)   # known diffs
    blocked: List[str] = field(default_factory=list)       # missing items
    rows: List[Row] = field(default_factory=list)
    svg: str = ""
    mesh: str = ""
    seconds: float = 0.0
    error: str = ""

    def add(self, *args, **kw) -> Row:
        r = Row(*args, **kw).rate()
        self.rows.append(r)
        return r

    def counts(self) -> Dict[str, int]:
        c = {"exact": 0, "acceptable": 0, "unacceptable": 0, "n/a": 0}
        for r in self.rows:
            c[r.rating] = c.get(r.rating, 0) + 1
        return c

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rows"] = [r.to_dict() for r in self.rows]
        d["counts"] = self.counts()
        return d


def timed(fn):
    """Decorator: run a case builder, stamp its wall time, trap errors."""
    def wrap() -> CaseResult:
        t0 = time.perf_counter()
        res = fn()
        res.seconds = time.perf_counter() - t0
        return res
    wrap.__name__ = fn.__name__
    wrap.__doc__ = fn.__doc__
    return wrap


# --------------------------------------------------------------------------- #
# model helpers (the engine is unit-agnostic: problems are entered in the
# published consistent units, kip-in-s or kip-ft-s)
# --------------------------------------------------------------------------- #
def new_model(name: str, E: float, nu: float = 0.3,
              heights: Sequence[float] = (1.0,),
              names: Optional[Sequence[str]] = None,
              diaphragm: bool = False) -> BuildingModel:
    """Weightless single-material model (no self mass, LLR never set)."""
    m = BuildingModel(name=name)
    m.rigid_diaphragms = diaphragm
    m.num_modes = 0
    m.add_material(Material("MAT", E=E, nu=nu, unit_weight=0.0))
    m.set_stories(list(heights), list(names) if names else None)
    return m


def section(m: BuildingModel, name: str, A: float, I33: float,
            I22: Optional[float] = None, J: Optional[float] = None,
            mat: str = "MAT") -> FrameSection:
    I22 = I33 if I22 is None else I22
    J = 2.0 * I33 if J is None else J
    return m.add_section(FrameSection(name, mat, A, I33, I22, J))


def support(m: BuildingModel, pt, restraints=FIXED) -> None:
    pt = tuple(float(v) for v in pt)
    for k, s in enumerate(m.supports):
        if all(abs(s.point[i] - pt[i]) < 1e-9 for i in range(3)):
            m.supports[k] = PointSupport(pt, tuple(
                max(a, b) for a, b in zip(s.restraints, restraints)))
            return
    m.supports.append(PointSupport(pt, tuple(restraints)))


def load(m: BuildingModel, pattern: str, pt, **comps) -> None:
    m.pattern(pattern).nodal_loads.append(
        NodalLoad(tuple(map(float, pt)), **comps))


def engine(m: BuildingModel) -> OpenSeesEngine:
    m.validate()
    return OpenSeesEngine(m)


def tag_at(eng: OpenSeesEngine, pt, tol: float = 1e-6) -> int:
    for t, c in eng._asm.node_coords.items():
        if all(abs(c[i] - pt[i]) < tol for i in range(3)):
            return t
    raise KeyError(f"no FE node at {pt}")


def disp(eng: OpenSeesEngine, res, pt, dof: int) -> float:
    return float(res.node_disp[tag_at(eng, pt)][dof])


def cm_stub(m: BuildingModel, sec: str, frm, cm, uid: str) -> None:
    """Massless, weightless stub member from an existing joint ``frm`` to a
    centre-of-mass point ``cm`` lying in the same floor plane.

    SkyFrame has no free-standing joint object; ETABS models these
    examples with an explicit joint at the CM carrying the story mass.
    The stub gives that joint an FE node: it lies in the rigid-diaphragm
    plane (the diaphragm constraint makes it rigid in plane) and its free
    far end carries no load, so it adds no stiffness to the structure.
    The OpenSees Transformation handler maps the slave-node mass onto the
    diaphragm master exactly (M_master = T^T m T), giving the coupled
    translational / rotational mass of a mass at the CM with zero mass
    moment of inertia about the CM (+ any ``mrz`` given explicitly)."""
    m.add_member("beam", sec, tuple(map(float, frm)), tuple(map(float, cm)),
                 story="", uid=uid)


def mass(m: BuildingModel, pt, mx=0.0, my=0.0, mz=0.0, mrz=0.0) -> None:
    m.nodal_masses.append(NodalMass(tuple(map(float, pt)), mx=mx, my=my,
                                    mz=mz, mrz=mrz))


def sig(x: float, n: int = 6) -> str:
    """A closed-form reference written to ``n`` significant digits (its
    'output precision' for the exact rule)."""
    return f"{x:.{n - 1}e}"


def fmt(v: Optional[float], sig: int = 6) -> str:
    if v is None:
        return "-"
    if v == 0.0:
        return "0"
    a = abs(v)
    if a >= 1e5 or a < 1e-4:
        return f"{v:.{sig - 1}e}"
    d = max(0, sig - 1 - int(math.floor(math.log10(a))))
    return f"{v:.{d}f}"
