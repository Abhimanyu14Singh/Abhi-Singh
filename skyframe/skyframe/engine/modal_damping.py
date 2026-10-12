"""Modal damping options of response-spectrum, steady-state and PSD cases —
engine side (``modal_damping`` spec: :mod:`skyframe.core.wavec_dyn`).

CONTRACT "Eigen shift / cutoff / tolerance, modal history and FNA case
types, TH initial conditions, RS / steady-state modal damping".

The case runs exactly as before on a SHALLOW model copy in which its
``damping`` is a :class:`~skyframe.core.wavec_dyn.ZetaVec` — the same
float (every scalar use is unchanged) carrying the per-mode ratios:

* RS: the CQC correlation (``_cqc_matrix`` / ``modalcombo.cqc_rho``) uses
  Der Kiureghian's variable-damping coefficient and DSC (``dsc_eps``) the
  per-mode ``zeta_i``; with ``sa_correction: "ec8"`` the spectrum is
  scaled per mode by ``eta(zeta_i) / eta(function_damping)``, ``eta =
  sqrt(10 / (5 + 100 zeta)) >= 0.55`` (EN 1998-1 3.2.2.2(3)), by giving
  the case a spectrum whose points sit exactly at the modal periods;
* steady state / PSD: the modal receptances use ``zeta_i``
  (``H_i = 1 / (w_i^2 - w^2 + 2 i zeta_i w_i w)``; hysteretic:
  ``w_i^2 (1 + 2 i zeta_i)``); the static residual keeps the constant
  ``damping``.

``"composite"`` ratios come from
:func:`skyframe.engine.composite_damping.composite` (strain-energy
weighted material ratios of the same modes; materials without a ratio
take the case's ``damping``).  The user's model is never mutated.

Report: RS ``CaseResults.rs_info["modal_damping"]`` (``rs_cases[name]["rs"]
["modal_damping"]``), steady state / PSD ``result["modal_damping"]`` =
``{"type", "zetas": [...], "sa_factors"?: [...], "composite"?: {...}}``.
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional

from skyframe.core import wavec_dyn as _wcd


def wants(eng, case) -> bool:
    return (getattr(case, "modal_damping", None) is not None
            and not eng.__dict__.get("_mdx_active"))


def _zetas(eng, case, periods) -> dict:
    md = case.modal_damping
    info: Dict[str, object] = {"type": md.get("type", "constant")}
    comp = None
    if info["type"] == "composite":
        from skyframe.engine import composite_damping as _ecd
        comp = _ecd.composite(eng.source_model, float(case.damping),
                              num_modes=len(periods))

        def comp_fn(n: int) -> List[float]:
            return [m["zeta"] for m in comp["modes"]][:n]
        info["composite"] = comp
    else:
        comp_fn = None
    z = _wcd.mode_zetas(md, float(case.damping), periods, comp_fn)
    info["zetas"] = z
    return info


def _swap(eng, attr: str, name: str, case2) -> None:
    m2 = copy.copy(eng.model)
    d = dict(getattr(eng.model, attr))
    d[name] = case2
    setattr(m2, attr, d)
    eng.model = m2


def run_rs(eng, name: str):
    """Response-spectrum case ``name`` with its modal damping options."""
    rs = eng.model.rs_cases[name]
    n_req = rs.num_modes if rs.num_modes > 0 else None
    modal = eng.run_modal(n_req)
    if not modal.periods:
        raise RuntimeError(f"RS case {name!r}: the model has no dynamic "
                           "modes (no mass on unrestrained DOFs)")
    info = _zetas(eng, rs, modal.periods)
    rs2 = copy.copy(rs)
    rs2.damping = _wcd.ZetaVec(rs.damping, info["zetas"])
    fac = _wcd.sa_factors(rs.modal_damping, info["zetas"])
    if fac is not None:
        if getattr(rs, "loads", None):
            raise ValueError(f"RS case {name!r}: sa_correction is not "
                             "available with several RS loads")
        from skyframe.engine.opensees_engine import _interp_spectrum
        spec = eng._resolve_rs_spectrum(rs)
        pts = {0.0: _interp_spectrum(spec, 0.0)}
        for T, f in zip(modal.periods, fac):
            pts.setdefault(float(T), _interp_spectrum(spec, T) * f)
        rs2.spectrum = [[t, pts[t]] for t in sorted(pts)]
        rs2.function = ""
        info["sa_factors"] = fac
    _swap(eng, "rs_cases", name, rs2)
    eng._rs_cache.pop(name, None)
    eng.__dict__["_mdx_active"] = True
    try:
        res = eng.run_response_spectrum(name)
    finally:
        eng.__dict__["_mdx_active"] = False
    res.rs_info = dict(res.rs_info or {}, modal_damping=info)
    eng._rs_cache[name] = res
    return res


def run_freq(eng, name: str, kind: str) -> dict:
    """Steady-state (``kind`` "ss") / PSD ("psd") case with modal damping."""
    from skyframe.engine import frequency as _fq
    attr = "steady_state_cases" if kind == "ss" else "psd_cases"
    case = getattr(eng.model, attr)[name]
    if case.method == "direct":
        asm0 = eng._build()
        modal = eng.run_modal(max(asm0.free_massed_dofs(), 1))
    else:
        modal = eng.run_modal(case.num_modes if case.num_modes > 0
                              else None)
    info = _zetas(eng, case, modal.periods)
    c2 = copy.copy(case)
    c2.damping = _wcd.ZetaVec(case.damping, info["zetas"])
    _swap(eng, attr, name, c2)
    eng.__dict__["_mdx_active"] = True
    try:
        res = (_fq.run_steady_state(eng, name) if kind == "ss"
               else _fq.run_psd(eng, name))
    finally:
        eng.__dict__["_mdx_active"] = False
    res = dict(res)
    res["modal_damping"] = info
    _fq._cache(eng)[(kind, name)] = res
    return res


__all__ = ["wants", "run_rs", "run_freq"]
