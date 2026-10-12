"""Composite (material-proportional) modal damping — engine side.

See :mod:`skyframe.core.composite_damping` for the definition.  Per mode
``i`` the element strain energies ``E_e = 1/2 u_e . f_e`` are read from
ONE linear static solve under ``f = w_i^2 M phi_i`` (displacements =
the mass-normalised mode ``phi_i``, element resisting forces =
``K_e phi_i``; the same device as the frequency-domain mode fields), on
a FRESH engine of the analysis model so the caller's domain is never
touched.  ``sum_e E_e = w_i^2 / 2`` is reported as ``energy_check``.

A TH case with ``damping_model == "composite"`` is run (direct
integration and FNA) as a ``"modal"`` case whose ``modal_zeta`` are the
composite ratios: :func:`run` swaps the case on a shallow copy of the
engine's analysis model (the user's model is never mutated) and attaches
``THResults.extra["composite_damping"]``.  Ratios are computed for the
modes of the full elastic analysis model (``num_modes``); FNA applies
them by mode index to its linearised basis (identical when the model has
no device links).
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional

import numpy as np
import openseespy.opensees as ops

from skyframe.core import composite_damping as _cd


def _groups(eng, asm) -> Dict[int, str]:
    """element tag -> material group (frames / shells); others absent."""
    model = eng.model
    out: Dict[int, str] = {}
    members = {m.uid: m for m in model.members}
    for (uid, _idx), etag in asm.seg_ele.items():
        m = members.get(uid)
        sec = model.sections.get(getattr(m, "section", None)) if m else None
        mat = getattr(sec, "material", None)
        if mat in model.materials:
            out[int(etag)] = mat
    regions = {r.uid: r for r in model.shells}
    for q, etag in zip(asm.shell_quads, asm.quad_ele):
        reg = regions.get(q.get("region"))
        ssec = (model.shell_sections.get(reg.section)
                if reg is not None else None)
        mat = getattr(ssec, "material", None)
        if mat in model.materials:
            out[int(etag)] = mat
    return out


def mode_energies(model, num_modes: Optional[int] = None) -> dict:
    """``{"periods", "energies": [{group: E}], "energy_check": [..]}``."""
    from skyframe.engine.opensees_engine import OpenSeesEngine
    eng = OpenSeesEngine(model)
    modal = eng.run_modal(num_modes)
    if not modal.periods:
        return {"periods": [], "energies": [], "energy_check": []}
    asm = eng._build()                     # fresh domain, same tags
    nm = len(modal.periods)
    omegas = [2.0 * math.pi / T for T in modal.periods]
    tags = sorted(asm.node_coords)
    groups = _groups(eng, asm)
    out_e: List[Dict[str, float]] = []
    checks: List[float] = []
    mass = asm.mass_map
    for i in range(nm):
        sh = modal.shapes[i + 1]
        den = sum(mv * sh[t][d - 1] ** 2 for (t, d), mv in mass.items())
        s = 1.0 / math.sqrt(den) if den > 0.0 else 0.0
        w2 = omegas[i] ** 2
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        loads: Dict[int, List[float]] = {}
        for (t, d), mv in mass.items():
            loads.setdefault(t, [0.0] * 6)[d - 1] += w2 * mv * s * sh[t][d - 1]
        for t, vec in loads.items():
            if any(vec):
                ops.load(t, *vec)
        eng._setup_analysis(asm)
        try:
            if ops.analyze(1) != 0:
                raise RuntimeError("composite damping: modal static solve "
                                   "failed")
            disp = {t: np.asarray(ops.nodeDisp(t), dtype=float)
                    for t in tags}
            E: Dict[str, float] = {}
            for e in ops.getEleTags():
                nodes = ops.eleNodes(e)
                f = np.asarray(ops.eleForce(e), dtype=float)
                if not nodes or f.size == 0:
                    continue
                nd = f.size // len(nodes)
                u = np.concatenate([disp.get(n, np.zeros(6))[:nd]
                                    for n in nodes])
                g = groups.get(int(e), _cd.OTHER)
                E[g] = E.get(g, 0.0) + 0.5 * float(u @ f)
        finally:
            ops.remove("loadPattern", 1)
            ops.remove("timeSeries", 1)
            ops.reset()
        out_e.append(E)
        tot = sum(E.values())
        checks.append(tot / (0.5 * w2) if w2 > 0.0 else 0.0)
    return {"periods": [float(T) for T in modal.periods], "energies": out_e,
            "energy_check": checks}


def composite(model, default: float, num_modes: Optional[int] = None
              ) -> dict:
    """Per-mode composite ratios + energy fractions (JSON-ready)."""
    me = mode_energies(model, num_modes)
    ratios = dict(getattr(model, "material_damping", None) or {})
    modes = []
    for T, E, chk in zip(me["periods"], me["energies"], me["energy_check"]):
        tot = sum(max(v, 0.0) for v in E.values())
        modes.append({
            "period": T,
            "zeta": _cd.composite_zeta(E, ratios, default),
            "fractions": {g: (max(v, 0.0) / tot if tot > 0.0 else 0.0)
                          for g, v in sorted(E.items())},
            "energy_check": float(chk)})
    return {"default": float(default), "material_damping": ratios,
            "modes": modes}


# ------------------------------------------------------------ TH routing
def _done(eng) -> Dict[str, dict]:
    return eng.__dict__.setdefault("_cpd_done", {})


def wants(eng, name: str) -> bool:
    if eng.__dict__.get("_cpd_active"):
        return False
    if name in _done(eng):
        return True
    th = eng.model.th_cases.get(name)
    return (th is not None
            and getattr(th, "damping_model", "rayleigh") == _cd.COMPOSITE)


def run(eng, name: str, kind: str):
    """Run TH case ``name`` (``kind`` "th" | "fna") with composite ratios."""
    done = _done(eng)
    if name not in done:
        th = eng.model.th_cases[name]
        info = composite(eng.source_model, float(th.damping))
        zetas = [m["zeta"] for m in info["modes"]] or [float(th.damping)]
        th2 = copy.copy(th)
        th2.damping_model = "modal"
        th2.modal_zeta = zetas
        m2 = copy.copy(eng.model)              # shallow: only th_cases differ
        m2.th_cases = dict(eng.model.th_cases)
        m2.th_cases[name] = th2
        eng.model = m2
        done[name] = info
    eng.__dict__["_cpd_active"] = True
    try:
        res = (eng.run_time_history(name) if kind == "th"
               else eng.run_fna(name))
    finally:
        eng.__dict__["_cpd_active"] = False
    res.extra["composite_damping"] = done[name]
    return res


__all__ = ["mode_energies", "composite", "wants", "run"]
