"""Floor-cracking iterative stiffness analysis — v0.25.

ETABS-style cracked-slab service analysis: a LINEAR static case is
iterated, and after each solve every SLAB shell quad's extreme-fiber
bending stress

    sigma = 6 * max(|Mxx|, |Myy|) / t**2        (kPa; M in kN*m/m, t in m)

is compared against the concrete modulus of rupture

    fr = fr_factor * sqrt(fc' [MPa])  MPa       (ACI 318 19.2.3, fr_factor
                                                 default 0.62)

with fc' taken from the material's optional ``fc`` attribute or the
established ACI 19.2.2.1 E-inversion (:func:`skyframe.design.wall.
fc_from_E`, fc' = (E/4700)^2 in MPa).  Quads whose sigma exceeds fr are
CRACKED: their shell-section stiffness is scaled by the flat factor
``cracked_ratio`` (default 0.35 — the Branson SIMPLIFIED flat Icr/Ig
ratio; the full Branson interpolation ``Ie = (Mcr/Ma)^3 Ig +
(1 - (Mcr/Ma)^3) Icr`` is deliberately NOT applied, documented) and the
case is re-solved on the softened model.  Iteration continues until the
cracked set stabilizes.

Delivered granularity (documented honestly): PER-QUAD stiffness factors —
the engine build creates one extra ``ElasticMembranePlateSection`` per
distinct (section, factor) pair and assigns it quad by quad
(``OpenSeesEngine._quad_stiff_scale``).  ElasticMembranePlateSection has a
SINGLE modulus, so the factor scales bending AND membrane stiffness
together — the same limitation as the v0.4 ``ShellSection.mod`` modifier
(an exact bending-only split is impossible with this section type).  For
gravity-loaded floor plates the response is bending-dominated, so the
approximation is the standard one.

Iteration rules (all documented):

* MONOTONE — once a quad cracks it STAYS cracked (physical: cracking is
  irreversible; numerical: guarantees termination in at most n_quads
  iterations up to ``max_iter``);
* CONVERGED when either (a) an iteration adds no new cracked quads (the
  primary criterion — the cracked set is stable) or (b) the max absolute
  vertical deflection changed by less than ``tol`` relative between
  successive iterations (the moment field has effectively stabilized;
  marginal single-quad flips no longer matter);
* the reported :class:`CrackedResults` carries the FINAL iteration's
  :class:`~skyframe.engine.opensees_engine.CaseResults` and a per-quad
  cracking map ``{quad index: {"region", "cracked", "Ma", "Mcr"}}``
  (Ma/Mcr in kN*m/m; quad indices match ``shell_forces`` /
  ``AnalysisResults.shell_quads``).

An UNCRACKED run (every sigma <= fr) is BIT-IDENTICAL to the plain
elastic case: the first iteration's engine build carries no stiffness
scales, so it IS the standard build (pinned in the tests).

Units: kN, m, kPa throughout (fr converted explicitly).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .model import BuildingModel

__all__ = ["CrackedResults", "cracked_analysis"]


@dataclass
class CrackedResults:
    """Final cracked-iteration state of one static case (v0.25).

    ``case`` is the last iteration's CaseResults (solved on the final
    cracked stiffness); ``cracking`` maps quad index -> ``{"region",
    "cracked", "Ma", "Mcr"}``; ``iterations`` counts SOLVES performed.
    """

    name: str
    case: object                     # CaseResults (engine dataclass)
    cracking: Dict[int, dict]
    iterations: int
    converged: bool
    cracked_ratio: float
    fr_factor: float
    warnings: List[str] = field(default_factory=list)

    @property
    def cracked_quads(self) -> List[int]:
        return sorted(q for q, e in self.cracking.items() if e["cracked"])

    def to_dict(self) -> dict:
        d = self.case.to_dict()
        d["cracking"] = {str(q): {"region": e["region"],
                                  "cracked": bool(e["cracked"]),
                                  "Ma": float(e["Ma"]),
                                  "Mcr": float(e["Mcr"])}
                         for q, e in self.cracking.items()}
        d["iterations"] = int(self.iterations)
        d["converged"] = bool(self.converged)
        d["cracked_ratio"] = float(self.cracked_ratio)
        d["fr_factor"] = float(self.fr_factor)
        d["cracked_warnings"] = list(self.warnings)
        return d


def _check_params(cracked_ratio: float, fr_factor: float, max_iter: int,
                  tol: float) -> None:
    if not (isinstance(cracked_ratio, (int, float))
            and not isinstance(cracked_ratio, bool)
            and math.isfinite(cracked_ratio)
            and 0.0 < cracked_ratio <= 1.0):
        raise ValueError("cracked_ratio must be a finite value in (0, 1], "
                         f"got {cracked_ratio!r}")
    if not (isinstance(fr_factor, (int, float))
            and not isinstance(fr_factor, bool)
            and math.isfinite(fr_factor) and fr_factor > 0.0):
        raise ValueError(f"fr_factor must be > 0, got {fr_factor!r}")
    if not (isinstance(max_iter, int) and max_iter >= 1):
        raise ValueError(f"max_iter must be an int >= 1, got {max_iter!r}")
    if not (isinstance(tol, (int, float)) and math.isfinite(tol)
            and tol > 0.0):
        raise ValueError(f"tol must be > 0, got {tol!r}")


def cracked_analysis(model: BuildingModel, case_name: str,
                     cracked_ratio: float = 0.35,
                     fr_factor: float = 0.62,
                     max_iter: int = 10,
                     tol: float = 0.02) -> CrackedResults:
    """Iterated cracked-slab solve of one static case (module docstring).

    Fresh :class:`~skyframe.engine.opensees_engine.OpenSeesEngine`
    instances are created per iteration (the per-quad stiffness scales
    enter at build time), so a caller engine's caches are never touched.
    """
    # local imports: the engine imports nothing from this module, but keep
    # the dependency one-directional at import time anyway
    from skyframe.engine.opensees_engine import OpenSeesEngine
    from skyframe.design.wall import fc_from_E

    _check_params(cracked_ratio, fr_factor, max_iter, tol)
    if case_name not in model.cases:
        raise ValueError(f"Unknown load case {case_name!r}")

    warnings_out: List[str] = []
    cracked: set = set()
    quad_meta: Optional[Dict[int, dict]] = None   # qi -> {"region", "Mcr"}
    result = None
    iterations = 0
    converged = False
    prev_metric: Optional[float] = None

    while iterations < max_iter:
        eng = OpenSeesEngine(model)
        eng._quad_stiff_scale = {qi: float(cracked_ratio) for qi in cracked}
        res = eng.run_static(case_name)
        iterations += 1
        result = res

        if quad_meta is None:
            quad_meta = {}
            regions = {r.uid: r for r in model.shells}
            for qi, q in enumerate(eng._asm.shell_quads):
                region = regions[q["region"]]
                if region.kind != "slab":
                    continue                       # walls never crack here
                ssec = model.shell_sections[region.section]
                mat = model.materials[ssec.material]
                fc_kpa = getattr(mat, "fc", 0.0) or fc_from_E(mat.E)
                fr_kpa = fr_factor * math.sqrt(fc_kpa / 1000.0) * 1000.0
                t = ssec.total_thickness
                quad_meta[qi] = {"region": region.uid,
                                 "Mcr": fr_kpa * t * t / 6.0}
            if not quad_meta:
                warnings_out.append("no slab shell quads in the model; "
                                    "the elastic result is returned "
                                    "unchanged")
                converged = True
                break

        # extreme-fiber check: sigma > fr  <=>  Ma > Mcr = fr*t^2/6
        new_cracked = set(cracked)
        for qi, meta in quad_meta.items():
            sf = res.shell_forces.get(qi)
            if sf is None:
                continue
            ma = max(abs(sf[3]), abs(sf[4]))       # |Mxx|, |Myy| (kN*m/m)
            meta["Ma"] = ma
            if ma > meta["Mcr"]:
                new_cracked.add(qi)

        metric = max((abs(v[2]) for v in res.node_disp.values()),
                     default=0.0)
        if new_cracked == cracked:
            converged = True                        # set is stable
            break
        if (prev_metric is not None and metric > 0.0
                and abs(metric - prev_metric) / metric < tol):
            converged = True                        # deflection stabilized
            warnings_out.append(
                f"stopped on the deflection tolerance ({tol:g} relative) "
                f"with {len(new_cracked - cracked)} marginal quad(s) still "
                "flipping; the reported map is the last SOLVED state")
            break
        cracked = new_cracked
        prev_metric = metric
    else:
        warnings_out.append(f"cracked iteration did not stabilize within "
                            f"max_iter = {max_iter} solves; the last state "
                            "is returned")

    cracking = {qi: {"region": meta["region"],
                     "cracked": qi in cracked,
                     "Ma": float(meta.get("Ma", 0.0)),
                     "Mcr": float(meta["Mcr"])}
                for qi, meta in (quad_meta or {}).items()}
    return CrackedResults(case_name, result, cracking, iterations,
                          converged, float(cracked_ratio), float(fr_factor),
                          warnings_out)
