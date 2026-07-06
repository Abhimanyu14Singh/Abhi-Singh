"""PRELIMINARY ACI 318 two-way (punching) shear checks at slab columns (v0.18).

Screens every COLUMN that supports a MESHED SHELL slab (a horizontal
``ShellRegion(kind="slab", behavior="shell")``) against the ACI 318
two-way shear provisions on the critical section at d/2 from the column
faces:

* Support detection — a column END node (top or bottom) lying in a shell
  slab's plane (same z within 1e-6) and inside its plan polygon (boundary
  inclusive).  Column ends only: a multi-story column drawn as ONE member
  through a slab plane is not detected (SkyFrame/ETABS practice splits
  columns at stories).
* Demand ``Vu`` — the column stack's AXIAL FORCE STEP at the slab level:
  ``Vu = C_below - C_above`` where ``C_below`` is the compression at the
  TOP of the column under the slab and ``C_above`` the compression at the
  BOTTOM of the column above (0 when absent), both read from the member
  STATIONS (station ``N`` is tension-positive per the engine convention,
  so ``C = -N``).  Sign: positive = net load delivered DOWNWARD by that
  floor into the column stack (a gravity floor); a transfer/mat situation
  gives a negative step (the column loads the slab) — the check uses
  ``|Vu|`` so both directions of transfer are screened, and the signed
  value is reported.
* Critical section (ACI §22.6.4.1) — rectangular column ``c1 x c2`` (the
  frame section's drawing ``h``/``b``), perimeter at d/2 from the faces:
  ``b0 = 2*(c1 + d) + 2*(c2 + d)``; slab effective depth ``d = t_slab -
  cover`` with ``cover`` defaulting to 0.03 m (25 mm clear cover + half a
  16 mm bar, rounded — documented simplification).
* Stress capacity — ``vc`` = min of the three ACI Table 22.6.5.2 formulas
  in their SI transcription (psi originals in parentheses)::

      vc1 = 0.33 * lam * sqrt(fc')                      (4  sqrt(psi))
      vc2 = 0.17 * (1 + 2/beta) * lam * sqrt(fc')       (2 + 4/beta)
      vc3 = 0.083 * (2 + alpha_s*d/b0) * lam * sqrt(fc') (2 + a_s d/b0)

  with fc' in MPa inside the square root, ``beta = max(c1,c2)/
  min(c1,c2)``, ``lam = 1`` (normal weight) and ``alpha_s = 40`` — v0.18
  checks INTERIOR columns only (edge/corner classification, and their
  alpha_s = 30/20, are OUT OF SCOPE; every supported column is rated as
  interior, which is unconservative for true edge/corner columns — noted
  on each result).  ``phi = 0.75`` (ACI 21.2.1).
* Unbalanced-moment transfer (gamma_v shear from M_sc, ACI 8.4.4.2) is
  OUT OF SCOPE in v0.18 — the check covers direct shear only.  The
  ACI 318-19 size-effect factor lambda_s is likewise not applied (the
  Table 22.6.5.2 values here are the 318-14 forms).

fc' comes from ``fc_prime`` (kPa) when supplied, else it is derived from
the SLAB shell section's concrete modulus via ACI 19.2.2.1 inverted:
``fc' = (E[MPa]/4700)^2`` (:func:`skyframe.design.wall.fc_from_E`).

Units: SkyFrame SI — kN, m, kPa; every MPa <-> kPa conversion explicit.
Results are PRELIMINARY screening values (``"preliminary": True``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from skyframe.design.concrete import _case_block
from skyframe.design.wall import fc_from_E

__all__ = [
    "PunchingCheck", "check_punching", "punching_capacity",
    "default_gravity_case", "ALPHA_S_INTERIOR", "PHI_SHEAR_PUNCHING",
    "DEFAULT_COVER",
]

ALPHA_S_INTERIOR = 40.0     # ACI 22.6.5.3: interior columns
PHI_SHEAR_PUNCHING = 0.75   # ACI 21.2.1
DEFAULT_COVER = 0.03        # m: d = t_slab - 0.03 (documented simplification)
_TOL = 1e-6


def default_gravity_case(model) -> str:
    """First load case that applies a DEAD-classified pattern.

    The v0.18 punching default: scanning ``model.cases`` in insertion
    order, return the first case any of whose patterns has
    ``LoadPattern.kind == "dead"``.  Raises ``ValueError`` when none
    exists (pass an explicit ``case``).
    """
    for name, case in model.cases.items():
        for pname in case.patterns:
            pat = model.patterns.get(pname)
            if pat is not None and pat.kind == "dead":
                return name
    raise ValueError("no DEAD-classified load case found — pass an "
                     "explicit 'case'")


def punching_capacity(c1: float, c2: float, d: float, fc: float, *,
                      alpha_s: float = ALPHA_S_INTERIOR,
                      lam: float = 1.0) -> dict:
    """b0, beta, and the min-of-three ACI Table 22.6.5.2 stress (SI).

    ``vc`` is returned in kPa: each SI formula yields MPa
    (fc' kPa -> MPa inside the sqrt), converted explicitly (*1000).
    """
    if c1 <= 0.0 or c2 <= 0.0 or d <= 0.0:
        raise ValueError("punching_capacity: c1, c2, d must be > 0")
    b0 = 2.0 * (c1 + d) + 2.0 * (c2 + d)                   # m
    beta = max(c1, c2) / min(c1, c2)
    fc_MPa = fc / 1000.0                                   # kPa -> MPa
    root = math.sqrt(fc_MPa)                               # sqrt(MPa)
    vc1 = 0.33 * lam * root                                # MPa
    vc2 = 0.17 * (1.0 + 2.0 / beta) * lam * root           # MPa
    vc3 = 0.083 * (2.0 + alpha_s * d / b0) * lam * root    # MPa
    vc = min(vc1, vc2, vc3) * 1000.0                       # MPa -> kPa
    return {"b0": b0, "beta": beta, "vc": vc,
            "vc1": vc1 * 1000.0, "vc2": vc2 * 1000.0, "vc3": vc3 * 1000.0}


def _point_in_polygon(x: float, y: float,
                      poly: List[Tuple[float, float]]) -> bool:
    """Plan point-in-polygon, BOUNDARY INCLUSIVE (within 1e-6).

    Standard ray casting; a point on an edge or vertex counts as inside
    (v0.18 rates every supported column as interior anyway).
    """
    n = len(poly)
    for i in range(n):                       # boundary test first
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        if L2 < _TOL * _TOL:
            if math.hypot(x - x1, y - y1) < _TOL:
                return True
            continue
        s = ((x - x1) * dx + (y - y1) * dy) / L2
        if -_TOL <= s <= 1.0 + _TOL:
            px, py = x1 + s * dx, y1 + s * dy
            if math.hypot(x - px, y - py) < _TOL:
                return True
    inside = False
    for i in range(n):                       # ray cast (+x direction)
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xi > x:
                inside = not inside
    return inside


@dataclass
class PunchingCheck:
    """Outcome of one two-way shear check (SI: kN, m, kPa)."""

    uid: str                             # the column BELOW the slab (or the
    #                                      one above when none is below)
    story: str
    slab: str                            # supporting slab region uid
    case: str
    Vu: float = 0.0                      # kN, signed axial step (see module
    #                                      docstring); the check uses |Vu|
    vu: float = 0.0                      # kPa, |Vu| / (b0*d)
    vc: float = 0.0                      # kPa, min-of-three Table 22.6.5.2
    phi_vc: float = 0.0                  # kPa
    b0: float = 0.0                      # m
    d: float = 0.0                       # m
    c1: float = 0.0                      # m (section h)
    c2: float = 0.0                      # m (section b)
    beta: float = 0.0
    fc: float = 0.0                      # kPa
    ratio: Optional[float] = None        # vu / (phi*vc)
    status: str = "N/A"                  # "OK" | "NG" | "N/A"
    notes: List[str] = field(default_factory=list)
    preliminary: bool = True             # ALWAYS True — screening check only

    def to_dict(self) -> dict:
        return {
            "uid": self.uid, "story": self.story, "slab": self.slab,
            "case": self.case, "Vu": self.Vu, "vu": self.vu,
            "vc": self.vc, "phi_vc": self.phi_vc, "b0": self.b0,
            "d": self.d, "c1": self.c1, "c2": self.c2, "beta": self.beta,
            "fc": self.fc, "ratio": self.ratio, "status": self.status,
            "notes": list(self.notes), "preliminary": True,
        }


def _column_ends(m) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
    """(bottom point, top point) of a column by z (ties broken by input
    order — a horizontal 'column' is nonsensical here anyway)."""
    return (m.pi, m.pj) if m.pj[2] >= m.pi[2] else (m.pj, m.pi)


def _compression_at(case: dict, uid: str, station_index: int) -> float:
    """Compression (kN, +compression) at one end station of a member.

    Station ``N`` is TENSION-positive (engine convention), so the
    compression is ``-N``; ``station_index`` 0 = end i, -1 = end j.
    """
    st = (case.get("member_stations") or {}).get(uid)
    if not st or not st.get("N"):
        return 0.0
    return -float(st["N"][station_index])


def check_punching(model, results, case: Optional[str] = None, *,
                   fc_prime: Optional[float] = None,
                   cover: float = DEFAULT_COVER,
                   phi: float = PHI_SHEAR_PUNCHING,
                   lam: float = 1.0) -> List[PunchingCheck]:
    """ACI two-way shear checks at every slab-supported column.

    Parameters
    ----------
    model    : the BuildingModel the results were computed from.
    results  : engine results object (with ``.to_dict()``) or the
               CONTRACT.md results dict (member stations must be present).
    case     : name of the static case or additive combo supplying the
               gravity axial forces; default = the first DEAD-classified
               case (:func:`default_gravity_case`).  Unknown names raise.
    fc_prime : explicit slab fc' (kPa); default derived from the slab
               section's E via ACI 19.2.2.1 (``fc_from_E``).
    cover    : slab depth deduction, ``d = t_slab - cover`` (m).

    Returns one :class:`PunchingCheck` per (slab, supported column stack)
    joint — an EMPTY list (not an error) when the model has no meshed
    shell slabs.  A joint whose column section lacks rectangular b/h
    drawing dimensions, or whose slab is too thin for the cover, reports
    status "N/A" with a note.
    """
    if not (isinstance(cover, (int, float)) and not isinstance(cover, bool)
            and math.isfinite(cover) and cover > 0.0):
        raise ValueError(f"'cover' must be a finite value > 0 (m), "
                         f"got {cover!r}")
    if fc_prime is not None and not (
            isinstance(fc_prime, (int, float))
            and not isinstance(fc_prime, bool)
            and math.isfinite(fc_prime) and fc_prime > 0.0):
        raise ValueError(f"'fc_prime' must be a finite value > 0 (kPa), "
                         f"got {fc_prime!r}")

    slabs = []
    for r in model.shells:
        if r.kind != "slab" or r.behavior != "shell":
            continue
        zs = [float(p[2]) for p in r.corners]
        if max(zs) - min(zs) > _TOL:
            continue                     # non-horizontal slab: not checked
        ssec = model.shell_sections.get(r.section)
        if ssec is None:
            continue
        mat = model.materials.get(ssec.material)
        fc = (float(fc_prime) if fc_prime is not None
              else fc_from_E(float(mat.E)) if mat is not None else 0.0)
        slabs.append({"region": r, "z": zs[0],
                      "poly": [(float(p[0]), float(p[1]))
                               for p in r.corners],
                      "t": float(ssec.thickness), "fc": fc})
    if not slabs:
        return []

    if case is None:
        case = default_gravity_case(model)
    blk = _case_block(results, case)     # raises KeyError on unknown name

    # joints: (slab uid, rounded x, y) -> {"below": member, "above": member}
    joints: Dict[Tuple[str, float, float], dict] = {}
    for m in model.members:
        if m.kind != "column":
            continue
        bot, top = _column_ends(m)
        for sl in slabs:
            for end, role in ((top, "below"), (bot, "above")):
                if abs(float(end[2]) - sl["z"]) > _TOL:
                    continue
                if not _point_in_polygon(float(end[0]), float(end[1]),
                                         sl["poly"]):
                    continue
                key = (sl["region"].uid, round(float(end[0]), 6),
                       round(float(end[1]), 6))
                joints.setdefault(key, {"slab": sl})[role] = m

    story_by_elev = [(s.elevation, s.name) for s in model.stories]
    story_order = {s.name: i for i, s in enumerate(model.stories)}

    def story_of(z: float) -> str:
        if not story_by_elev:
            return ""
        return min(story_by_elev, key=lambda ez: abs(ez[0] - z))[1]

    checks: List[PunchingCheck] = []
    for key in joints:
        j = joints[key]
        sl = j["slab"]
        below, above = j.get("below"), j.get("above")
        col = below or above
        chk = PunchingCheck(uid=col.uid, story=story_of(sl["z"]),
                            slab=sl["region"].uid, case=case,
                            fc=sl["fc"])
        checks.append(chk)
        # Vu = C_below(top) - C_above(bottom): the axial step at this level
        C_below = 0.0
        if below is not None:
            idx = -1 if below.pj[2] >= below.pi[2] else 0
            C_below = _compression_at(blk, below.uid, idx)
        C_above = 0.0
        if above is not None:
            idx = 0 if above.pj[2] >= above.pi[2] else -1
            C_above = _compression_at(blk, above.uid, idx)
        chk.Vu = C_below - C_above
        if chk.Vu < 0.0:
            chk.notes.append("negative axial step (column loads the slab — "
                             "transfer/mat); |Vu| checked")

        sec = model.sections.get(col.section)
        if sec is None or float(sec.b) <= 0.0 or float(sec.h) <= 0.0:
            chk.notes.append("column section has no rectangular b/h "
                             "drawing dimensions — not checked")
            continue
        chk.c1, chk.c2 = float(sec.h), float(sec.b)
        chk.d = sl["t"] - cover
        if chk.d <= 0.0:
            chk.notes.append(f"effective depth d = {chk.d:.3f} m <= 0 "
                             "(slab thinner than the cover deduction)")
            continue
        cap = punching_capacity(chk.c1, chk.c2, chk.d, sl["fc"], lam=lam)
        chk.b0, chk.beta, chk.vc = cap["b0"], cap["beta"], cap["vc"]
        chk.phi_vc = phi * cap["vc"]
        chk.vu = abs(chk.Vu) / (chk.b0 * chk.d)          # kPa
        chk.ratio = chk.vu / chk.phi_vc
        chk.status = "OK" if chk.ratio <= 1.0 else "NG"
        chk.notes.append("interior-column check (alpha_s = 40); "
                         "edge/corner classification and unbalanced-moment "
                         "transfer (gamma_v) out of scope in v0.18")
    checks.sort(key=lambda c: (story_order.get(c.story, 1_000_000), c.uid))
    return checks
