"""Wave C dynamics options — model side (CONTRACT "Eigen shift / cutoff /
tolerance, modal history and FNA case types, TH initial conditions, RS /
steady-state modal damping").

Data (every default reproduces the previous behaviour; nothing is emitted
by ``to_dict`` while unset, so legacy models serialise byte-identically):

* ``BuildingModel.eigen_options`` — ``{}`` or eigen parameters of the
  default ``MODAL`` case; the same keys are accepted inside every named
  eigen modal case (``modal_cases[name]``)::

      {"shift": f_s (Hz, >= 0),        # frequency shift (center)
       "cutoff": f_c (Hz, >= 0),       # cutoff frequency (radius), 0 = none
       "tolerance": tol}               # relative eigenvalue convergence

  With any key present the modes are found by shift-invert subspace
  iteration on ``(K - sigma M)^-1 M`` (``sigma = (2 pi f_s)^2``): the
  ``num_modes`` modes NEAREST the shift frequency with ``|f - f_s| <= f_c``
  (``f_c = 0``: no radius), converged until every retained Ritz value
  changes by less than ``tolerance`` between sweeps (default 1e-9).

* ``TimeHistoryCase.th_type`` — ``"direct"`` (default; direct integration,
  the legacy case), ``"modal"`` (Linear Modal History: modal superposition
  of the linear model, device links replaced by their linear part) or
  ``"fna"`` (Nonlinear Modal History, Fast Nonlinear Analysis).  The case
  stays in ``th_cases`` (results in ``th_cases``, combinations work as for
  any TH case) but ``model.case_kinds()`` reports ``"modal_history"`` /
  ``"fna"`` and ``run()`` runs it by its own method.

* ``TimeHistoryCase.initial_case`` may also name a STAGED case (direct
  integration and FNA) — the TH starts from the staged end displacements —
  and, for direct integration, any nonlinear static chain (hinged and/or
  displacement-controlled): the chain is replayed inside the TH domain.

* ``ResponseSpectrumCase.modal_damping`` / ``SteadyStateCase`` /
  ``PSDCase.modal_damping`` — ``None`` or::

      {"type": "constant" | "interpolated_period" | "interpolated_frequency"
               | "mass_stiffness" | "composite",
       "points": [[T or f, zeta], ...],         # interpolated types
       "a0": .., "a1": ..                       # mass_stiffness (1/s, s)
       | "periods": [T1, T2], "ratios": [z1, z2],
       "overrides": {"<mode>": zeta, ...},      # per-mode overrides
       "sa_correction": "none" | "ec8",         # RS only (default none)
       "function_damping": 0.05}                # RS ec8 reference ratio

  The per-mode ratios replace the constant ``damping`` in the CQC / DSC
  correlation (RS) and in the modal receptances (steady state / PSD).

This module does NOT import :mod:`skyframe.core.model` at import time.
"""

from __future__ import annotations

import copy
import math
from typing import Callable, Dict, List, Optional, Sequence

EIGEN_KEYS = ("shift", "cutoff", "tolerance")
EIGEN_DEFAULT_TOL = 1e-9
TH_TYPES = ("direct", "modal", "fna")
TH_TYPE_KINDS = {"modal": "modal_history", "fna": "fna"}
TH_TYPE_LABELS = {"direct": "Direct Integration",
                  "modal": "Linear Modal History",
                  "fna": "Nonlinear Modal History (FNA)"}
MD_TYPES = ("constant", "interpolated_period", "interpolated_frequency",
            "mass_stiffness", "composite")
MD_KEYS = ("type", "points", "a0", "a1", "periods", "ratios", "overrides",
           "sa_correction", "function_damping")
SA_CORRECTIONS = ("none", "ec8")


def _num(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


# --------------------------------------------------------------------------- #
# eigen parameters
# --------------------------------------------------------------------------- #
def eigen_params(d) -> dict:
    """The eigen keys present in ``d`` (a model ``eigen_options`` dict or a
    modal-case dict), unvalidated."""
    if not isinstance(d, dict):
        return {}
    return {k: d[k] for k in EIGEN_KEYS if k in d}


def validate_eigen(d, where: str) -> dict:
    """Validated eigen parameters (floats); raises ValueError."""
    if not isinstance(d, dict):
        raise ValueError(f"{where}: eigen options must be an object")
    bad = set(d) - set(EIGEN_KEYS)
    if bad:
        raise ValueError(f"{where}: unknown eigen option(s) {sorted(bad)} "
                         f"(allowed: {list(EIGEN_KEYS)})")
    out = {}
    for k in ("shift", "cutoff"):
        if k in d:
            if not (_num(d[k]) and d[k] >= 0.0):
                raise ValueError(f"{where}: {k} must be a frequency >= 0 "
                                 "(Hz)")
            out[k] = float(d[k])
    if "tolerance" in d:
        if not (_num(d["tolerance"]) and 0.0 < d["tolerance"] <= 1e-2):
            raise ValueError(f"{where}: tolerance must be in (0, 0.01]")
        out["tolerance"] = float(d["tolerance"])
    return out


def wants_eigen(d) -> bool:
    return bool(eigen_params(d))


# --------------------------------------------------------------------------- #
# modal damping (RS / steady state / PSD)
# --------------------------------------------------------------------------- #
def _pts(v, where: str, what: str) -> List[List[float]]:
    if not isinstance(v, list) or not v:
        raise ValueError(f"{where}: modal_damping.points must be a non-empty "
                         f"list of [{what}, zeta]")
    out = []
    prev = -math.inf
    for p in v:
        if not (isinstance(p, (list, tuple)) and len(p) == 2
                and _num(p[0]) and _num(p[1])):
            raise ValueError(f"{where}: modal_damping.points entries must be "
                             f"[{what}, zeta]")
        x, z = float(p[0]), float(p[1])
        if x <= prev or x < 0.0:
            raise ValueError(f"{where}: modal_damping.points {what} values "
                             "must be >= 0 and strictly increasing")
        if not 0.0 <= z < 1.0:
            raise ValueError(f"{where}: modal_damping ratios must be in "
                             "[0, 1)")
        prev = x
        out.append([x, z])
    return out


def validate_modal_damping(md, where: str, rs: bool = True) -> None:
    if md is None:
        return
    if not isinstance(md, dict):
        raise ValueError(f"{where}: modal_damping must be an object (or "
                         "null)")
    bad = set(md) - set(MD_KEYS)
    if bad:
        raise ValueError(f"{where}: modal_damping has unknown key(s) "
                         f"{sorted(bad)}")
    typ = md.get("type", "constant")
    if typ not in MD_TYPES:
        raise ValueError(f"{where}: modal_damping.type must be one of "
                         f"{list(MD_TYPES)}")
    if typ in ("interpolated_period", "interpolated_frequency"):
        _pts(md.get("points"), where,
             "T (s)" if typ == "interpolated_period" else "f (Hz)")
    elif "points" in md:
        raise ValueError(f"{where}: modal_damping.points is only valid for "
                         "the interpolated types")
    if typ == "mass_stiffness":
        if "periods" in md or "ratios" in md:
            T, z = md.get("periods"), md.get("ratios")
            if not (isinstance(T, list) and isinstance(z, list)
                    and len(T) == 2 and len(z) == 2
                    and all(_num(v) and v > 0.0 for v in T)
                    and all(_num(v) and 0.0 <= v < 1.0 for v in z)
                    and T[0] != T[1]):
                raise ValueError(f"{where}: modal_damping mass_stiffness "
                                 "needs periods [T1, T2] (> 0, distinct) and "
                                 "ratios [z1, z2] in [0, 1)")
            if "a0" in md or "a1" in md:
                raise ValueError(f"{where}: give a0/a1 OR periods/ratios")
        else:
            for k in ("a0", "a1"):
                if not (_num(md.get(k, 0.0)) and md.get(k, 0.0) >= 0.0):
                    raise ValueError(f"{where}: modal_damping.{k} must be "
                                     ">= 0")
            if md.get("a0", 0.0) == 0.0 and md.get("a1", 0.0) == 0.0:
                raise ValueError(f"{where}: modal_damping mass_stiffness "
                                 "needs a0 and/or a1 > 0 (or periods / "
                                 "ratios)")
    elif any(k in md for k in ("a0", "a1", "periods", "ratios")):
        raise ValueError(f"{where}: a0 / a1 / periods / ratios are only "
                         "valid for type mass_stiffness")
    ov = md.get("overrides", {})
    if not isinstance(ov, dict):
        raise ValueError(f"{where}: modal_damping.overrides must be "
                         "{mode: zeta}")
    for k, z in ov.items():
        try:
            mode = int(str(k))
        except ValueError:
            mode = 0
        if mode < 1 or str(mode) != str(k):
            raise ValueError(f"{where}: modal_damping.overrides keys must be "
                             "mode numbers >= 1")
        if not (_num(z) and 0.0 <= z < 1.0):
            raise ValueError(f"{where}: modal_damping override of mode {k} "
                             "must be in [0, 1)")
    sc = md.get("sa_correction", "none")
    if sc not in SA_CORRECTIONS:
        raise ValueError(f"{where}: modal_damping.sa_correction must be one "
                         f"of {list(SA_CORRECTIONS)}")
    if sc != "none" and not rs:
        raise ValueError(f"{where}: sa_correction applies to response "
                         "spectrum cases only")
    fd = md.get("function_damping", 0.05)
    if not (_num(fd) and 0.0 < fd < 1.0):
        raise ValueError(f"{where}: modal_damping.function_damping must be "
                         "in (0, 1)")


def mass_stiffness_coeffs(md: dict):
    """(a0, a1) of a mass_stiffness spec: zeta = a0/(2w) + a1 w/2."""
    if "periods" in md:
        w1, w2 = (2.0 * math.pi / float(t) for t in md["periods"])
        z1, z2 = (float(z) for z in md["ratios"])
        den = w2 * w2 - w1 * w1
        a0 = 2.0 * w1 * w2 * (z1 * w2 - z2 * w1) / den
        a1 = 2.0 * (z2 * w2 - z1 * w1) / den
        return a0, a1
    return float(md.get("a0", 0.0)), float(md.get("a1", 0.0))


def mode_zetas(md: dict, base: float, periods: Sequence[float],
               composite: Optional[Callable[[int], List[float]]] = None
               ) -> List[float]:
    """Per-mode damping ratios of a modal_damping spec for modes with the
    given periods (s); ``base`` = the case's constant ``damping``."""
    import numpy as np
    n = len(periods)
    typ = md.get("type", "constant")
    T = np.asarray([float(t) for t in periods], dtype=float)
    if typ == "constant":
        z = [float(base)] * n
    elif typ in ("interpolated_period", "interpolated_frequency"):
        xs = [p[0] for p in md["points"]]
        zs = [p[1] for p in md["points"]]
        q = T if typ == "interpolated_period" else 1.0 / T
        z = [float(v) for v in np.interp(q, xs, zs)]
    elif typ == "mass_stiffness":
        a0, a1 = mass_stiffness_coeffs(md)
        w = 2.0 * math.pi / T
        z = [float(v) for v in (a0 / (2.0 * w) + a1 * w / 2.0)]
    else:                                      # composite
        z = list(composite(n)) if composite is not None else []
        z = (z + [float(base)] * n)[:n]
    for k, v in (md.get("overrides") or {}).items():
        i = int(k) - 1
        if 0 <= i < n:
            z[i] = float(v)
    return z


def ec8_eta(zeta: float) -> float:
    """EN 1998-1 (3.6) damping correction eta = sqrt(10/(5 + xi)) >= 0.55
    (xi in percent)."""
    return max(math.sqrt(10.0 / (5.0 + 100.0 * float(zeta))), 0.55)


def sa_factors(md: dict, zetas: Sequence[float]) -> Optional[List[float]]:
    """Spectral scale factor per mode (None = no correction)."""
    if md.get("sa_correction", "none") == "none":
        return None
    ref = ec8_eta(float(md.get("function_damping", 0.05)))
    return [ec8_eta(z) / ref for z in zetas]


def cqc_rho_var(omegas: Sequence[float], zetas: Sequence[float]):
    """Der Kiureghian (1981) CQC correlation with modal damping z_i:

        rho_ij = 8 sqrt(zi zj) (zi + r zj) r^1.5 /
                 [(1 - r^2)^2 + 4 zi zj r (1 + r^2) + 4 (zi^2 + zj^2) r^2],
        r = w_j / w_i

    (equals the constant-damping formula when zi == zj)."""
    import numpy as np
    n = len(omegas)
    rho = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            zi, zj = float(zetas[i]), float(zetas[j])
            r = float(omegas[j]) / float(omegas[i])
            num = 8.0 * math.sqrt(zi * zj) * (zi + r * zj) * r ** 1.5
            den = ((1.0 - r * r) ** 2 + 4.0 * zi * zj * r * (1.0 + r * r)
                   + 4.0 * (zi * zi + zj * zj) * r * r)
            rho[i, j] = rho[j, i] = (num / den) if den > 0.0 else 1.0
    return rho


class ZetaVec(float):
    """A float (the case's constant damping, so every scalar use is
    unchanged) carrying the per-mode ratios in ``per_mode``."""

    def __new__(cls, base: float, per_mode: Sequence[float] = ()):
        obj = float.__new__(cls, float(base))
        obj.per_mode = [float(z) for z in per_mode]
        return obj

    def __reduce__(self):                    # copy / deepcopy / pickle
        return (ZetaVec, (float(self), list(self.per_mode)))


def per_mode_of(zeta, n: int) -> Optional[List[float]]:
    """The per-mode list of a :class:`ZetaVec` padded / truncated to ``n``
    (the last value repeats), or None for a plain float."""
    pm = getattr(zeta, "per_mode", None)
    if pm is None:
        return None
    pm = list(pm) or [float(zeta)]
    return (pm + [pm[-1]] * n)[:n]


# --------------------------------------------------------------------------- #
# TH case types
# --------------------------------------------------------------------------- #
def th_type(th) -> str:
    return getattr(th, "th_type", "direct") or "direct"


def case_kinds_hook(model, out: Dict[str, str]) -> None:
    """``case_kinds()``: TH cases of type modal / fna get their own kind."""
    for n, th in model.th_cases.items():
        k = TH_TYPE_KINDS.get(th_type(th))
        if k is not None and out.get(n) == "time_history":
            out[n] = k


# --------------------------------------------------------------------------- #
# (de)serialisation
# --------------------------------------------------------------------------- #
def th_to_dict_extra(th) -> dict:
    t = th_type(th)
    return {} if t == "direct" else {"th_type": t}


def md_to_dict_extra(case) -> dict:
    md = getattr(case, "modal_damping", None)
    return {} if md is None else {"modal_damping": copy.deepcopy(md)}


def model_to_dict(model) -> dict:
    eo = getattr(model, "eigen_options", None) or {}
    return {"eigen_options": dict(eo)} if eo else {}


def model_from_dict(model, d: dict) -> None:
    eo = d.get("eigen_options")
    if eo is not None:
        if not isinstance(eo, dict):
            raise ValueError("eigen_options must be an object")
        model.eigen_options = dict(eo)
    for cname, cd in (d.get("th_cases") or {}).items():
        if cname in model.th_cases and isinstance(cd, dict) \
                and "th_type" in cd:
            model.th_cases[cname].th_type = str(cd["th_type"])
    for key in ("rs_cases", "steady_state_cases", "psd_cases"):
        cases = getattr(model, key, None) or {}
        for cname, cd in (d.get(key) or {}).items():
            if cname in cases and isinstance(cd, dict) \
                    and cd.get("modal_damping") is not None:
                cases[cname].modal_damping = copy.deepcopy(
                    cd["modal_damping"])


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def validate(model) -> None:
    eo = getattr(model, "eigen_options", None) or {}
    validate_eigen(eo, "eigen_options")
    if eo and getattr(model, "modal_from_case", None):
        raise ValueError("eigen_options (shift / cutoff / tolerance) cannot "
                         "be combined with modal_from_case")
    for name, mc in (getattr(model, "modal_cases", None) or {}).items():
        ep = eigen_params(mc)
        if not ep:
            continue
        validate_eigen(ep, f"modal case {name!r}")
        if mc.get("type", "eigen") != "eigen":
            raise ValueError(f"modal case {name!r}: shift / cutoff / "
                             "tolerance apply to eigen cases only")
        if mc.get("stiffness_case"):
            raise ValueError(f"modal case {name!r}: shift / cutoff / "
                             "tolerance cannot be combined with a "
                             "stiffness_case")
    for name, th in model.th_cases.items():
        t = th_type(th)
        if t not in TH_TYPES:
            raise ValueError(f"TH case {name!r}: th_type must be one of "
                             f"{list(TH_TYPES)}, got {t!r}")
        if t != "direct" and getattr(th, "nonlinear", False):
            raise ValueError(f"TH case {name!r}: hinge nonlinearity needs "
                             "direct integration (th_type 'direct')")
        if t == "modal" and (th.gravity or getattr(th, "initial_case", "")):
            raise ValueError(f"TH case {name!r}: a linear modal history has "
                             "no initial conditions (use th_type 'fna' or "
                             "'direct')")
        if t != "direct" and (getattr(th, "integration", None)
                              or getattr(th, "di_damping", None)
                              or getattr(th, "solver", None)
                              or getattr(th, "energy", False)):
            raise ValueError(f"TH case {name!r}: direct-integration options "
                             "need th_type 'direct'")
    for name, po in model.pushover_cases.items():
        cd = getattr(po, "control_dof", None)
        if cd in ("UZ", "RX", "RY", "RZ") and po.control_mode == \
                "displacement_control" and po.target_disp is None:
            raise ValueError(f"Pushover case {name!r}: control_dof {cd} "
                             "needs target_disp (m or rad) — the drift "
                             "target applies to UX / UY only")
    for name, sc in model.staged_cases.items():
        td = getattr(sc, "time_dependent", None)
        if isinstance(td, dict) and "shell_creep" in td:
            if not isinstance(td["shell_creep"], bool):
                raise ValueError(f"Staged case {name!r}: time_dependent"
                                 "['shell_creep'] must be true/false")
            if td["shell_creep"] and td.get("method") != "step":
                raise ValueError(f"Staged case {name!r}: shell_creep needs "
                                 "time_dependent method 'step'")
    for key, rs in (("rs_cases", True), ("steady_state_cases", False),
                    ("psd_cases", False)):
        for name, c in (getattr(model, key, None) or {}).items():
            validate_modal_damping(getattr(c, "modal_damping", None),
                                   f"{key[:-6].replace('_', ' ')} case "
                                   f"{name!r}", rs=rs)


def initial_case_ok(model, name: str) -> bool:
    """``TimeHistoryCase.initial_case`` may name a nonlinear static or a
    staged case."""
    return (name in (getattr(model, "nonlinear_static_cases", None) or {})
            or name in model.staged_cases)


__all__ = ["EIGEN_KEYS", "TH_TYPES", "MD_TYPES", "eigen_params",
           "validate_eigen", "validate_modal_damping", "mode_zetas",
           "cqc_rho_var", "ZetaVec", "per_mode_of", "sa_factors", "ec8_eta",
           "th_type", "case_kinds_hook", "model_to_dict", "model_from_dict",
           "validate", "initial_case_ok"]
