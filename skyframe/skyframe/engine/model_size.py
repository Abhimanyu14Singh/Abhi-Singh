"""Model-size safety: warn (never fail) when an analysis will be slow.

CONTRACT "Analysis performance, solver policy and convergence strategy".

:func:`assess` sizes an assembled model (estimated equations, nodes, frame
and shell elements) and the work one ``run()`` will do (static solves,
modes x response-spectrum modal statics, time-history steps) and returns a
JSON-safe report; :func:`warnings_for` turns it into human warnings when a
threshold is exceeded.  ``OpenSeesEngine.run()`` attaches the report as the
results ``model_size`` block — ONLY when at least one warning fires (so
every model below the thresholds keeps byte-identical results) — and
appends the warnings to the results ``warning`` string.  Turned off with
``analysis_options.size_warnings = false``.

Graceful memory behaviour: :func:`band_fallback` is consulted when the
user FORCES the dense-band solver (``analysis_options.solver = "band"``)
on a model whose band storage estimate exceeds :data:`BAND_MEMORY_CAP`
(or the machine's physical memory when known) — instead of letting
OpenSees abort the process on an allocation failure (observed: a 20-story
meshed building segfaults inside BandGeneral), the sparse solver is used
and a warning says so.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from skyframe.engine import solver_policy as _slv

# thresholds (estimated equations / derived work units)
SLOW_EQUATIONS = 60_000          # one sparse factorization ~ seconds
VERY_SLOW_EQUATIONS = 250_000    # minutes; memory in the GB range
SLOW_SHELLS = 20_000             # shell elements (build + output volume)
SLOW_MODAL_STATICS = 400         # sum over RS cases of modes (each a solve)
SLOW_TH_WORK = 2.0e8             # TH steps x equations
SLOW_RESULT_VALUES = 2.0e7       # rough count of floats in the results
BAND_MEMORY_CAP = 2.0 * 1024 ** 3   # bytes of band storage allowed


def _phys_mem() -> Optional[int]:
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError, AttributeError):       # pragma: no cover
        return None


def band_bytes(asm) -> float:
    """Worst-case BandGeneral storage estimate (bytes): a rigid-diaphragm
    master couples a whole floor, so the half band is taken as the largest
    floor's equation count (or 6 x the largest connected story node set)
    — the measured behaviour on diaphragm buildings."""
    neq = _slv.estimate_equations(asm)
    floor = max((len(v) for v in asm.story_nodes.values()), default=0)
    hb = max(6 * floor, 12)
    hb = min(hb, neq)
    return float(neq) * (3.0 * hb + 1.0) * 8.0


def band_fallback(asm) -> Optional[str]:
    """``None`` when the band solver is affordable, else the warning text
    (the caller switches the system to UmfPack)."""
    need = band_bytes(asm)
    cap = BAND_MEMORY_CAP
    mem = _phys_mem()
    if mem:
        cap = min(cap, 0.5 * mem)
    if need <= cap:
        return None
    return (f"solver 'band' would need ~{need / 1024 ** 3:.1f} GB of band "
            f"storage for ~{_slv.estimate_equations(asm)} equations; the "
            "sparse UmfPack solver was used instead")


def assess(engine, asm) -> Dict[str, object]:
    """Size / work report of an engine's model (JSON-safe)."""
    model = engine.model
    neq = _slv.estimate_equations(asm)
    n_frames = len(asm.seg_ele)
    n_shells = len(asm.shell_quads)
    n_static = len(model.cases)
    modes = int(getattr(model, "num_modes", 0) or 0)
    rs_statics = sum((rs.num_modes or modes) for rs in model.rs_cases.values())
    th_steps = 0
    for th in model.th_cases.values():
        try:
            th_steps += len(engine._resolve_th_record(th)[0])
        except Exception:                             # pragma: no cover
            pass
    # results volume: per solved case node disp (6) + members 11 stations x
    # 7 + 12 end forces + shells 8
    per_case = (6 * len(asm.node_coords) + 89 * len(model.members)
                + 8 * n_shells)
    n_case_like = n_static + len(model.combos) + len(model.rs_cases)
    return {
        "equations_estimate": neq,
        "nodes": len(asm.node_coords),
        "frame_elements": n_frames,
        "shell_elements": n_shells,
        "system": _slv.system_of(asm),
        "static_cases": n_static,
        "rs_modal_statics": int(rs_statics),
        "th_steps": int(th_steps),
        "result_values_estimate": int(per_case * n_case_like),
    }


def warnings_for(rep: Dict[str, object]) -> List[str]:
    """Human warnings for a size report (empty below every threshold)."""
    out: List[str] = []
    neq = int(rep["equations_estimate"])
    if neq >= VERY_SLOW_EQUATIONS:
        out.append(f"very large model: ~{neq} equations — expect minutes "
                   "per factorization and several GB of memory; consider "
                   "a coarser shell mesh_size or analysing a sub-model")
    elif neq >= SLOW_EQUATIONS:
        out.append(f"large model: ~{neq} equations — the analysis will be "
                   "slow (seconds per factorization)")
    if int(rep["shell_elements"]) >= SLOW_SHELLS:
        out.append(f"{rep['shell_elements']} shell elements — consider a "
                   "coarser mesh_size (build and result volume grow with "
                   "the element count)")
    if int(rep["rs_modal_statics"]) >= SLOW_MODAL_STATICS:
        out.append(f"{rep['rs_modal_statics']} response-spectrum modal "
                   "static solves — reduce num_modes or RS cases")
    if float(rep["th_steps"]) * neq >= SLOW_TH_WORK:
        out.append(f"time history: {rep['th_steps']} steps x ~{neq} "
                   "equations — the transient analysis will be slow")
    if int(rep["result_values_estimate"]) >= SLOW_RESULT_VALUES:
        out.append(f"~{rep['result_values_estimate']:.3g} result values — "
                   "the results payload will be large (hundreds of MB)")
    return out


def enabled(model) -> bool:
    opts = getattr(model, "analysis_options", None) or {}
    return not (isinstance(opts, dict) and opts.get("size_warnings") is False)
