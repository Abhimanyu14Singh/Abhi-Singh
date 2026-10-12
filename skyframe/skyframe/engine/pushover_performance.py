"""Pushover performance methods — engine glue.

:func:`modal_props` reads the push-direction modal properties the ADRS
conversion needs (dominant mode in the push direction: period ``T``,
participation factor ``gamma``, mode-shape value at the monitored joint
``phi_ctrl``, ``pf_phi = gamma * phi_ctrl``, effective modal mass ratio
``alpha``, total push-direction mass ``mass`` (t) and ``W = mass g``),
using the elastic modal solve and the SAME monitored joint as the push
(:func:`skyframe.engine.pushover_distribution.resolve_control`, located on
the elastic assembly).  :func:`evaluate` runs every selected method
(:mod:`skyframe.core.pushover_methods`) and adds the nearest converged
step + hinge-state counts to each summary row.  :func:`attach` is the
one-line hook of ``OpenSeesEngine.run``: it sets
``PushoverResults.performance`` only when the case has ``performance``
options (default ``None`` -> results byte-identical).
"""

from __future__ import annotations

from typing import Optional

from skyframe.core import pushover_methods as _pm

G = _pm.G


def modal_props(engine, case) -> dict:
    """Push-direction modal properties of ``case`` (module docstring)."""
    from skyframe.engine import pushover_distribution as _pod
    from skyframe.engine import towers_ext as _twx
    model = engine.model
    modal = engine.run_modal()
    if not modal.participation:
        raise ValueError("pushover performance: the model has no modes "
                         "(no mass on free DOFs)")
    key = "ux" if case.direction == "X" else "uy"
    gkey = "gamma_x" if case.direction == "X" else "gamma_y"
    p = max(modal.participation, key=lambda e: e.get(key, 0.0))
    mode = int(p["mode"])
    asm = getattr(engine, "_asm", None)
    if asm is None:
        asm = engine._build()
    dof = 1 if case.direction == "X" else 2
    top = model.stories[-1]
    if top.name in asm.masters:
        ctrl = asm.masters[top.name]
    else:
        zmax = max(c[2] for c in asm.struct_coords.values())
        ctrl = min(t for t, c in asm.struct_coords.items()
                   if abs(c[2] - zmax) < 1e-6)
    H = top.elevation
    top, ctrl, H = _twx.pushover_roof(engine, asm, case, top, ctrl, H)
    mon, mdof, _ = _pod.resolve_control(engine, asm, case, ctrl, dof, H)
    shape = modal.shapes.get(mode) or modal.shapes.get(str(mode)) or {}
    vec = shape.get(mon)
    if vec is None:
        raise ValueError("pushover performance: no mode-shape value at the "
                         "monitored joint")
    phi = float(vec[mdof - 1])
    gamma = float(p.get(gkey, 0.0))
    mass = sum(m for (t, d), m in asm.mass_map.items() if d == dof)
    return {"mode": mode, "T": float(p["T"]), "gamma": gamma,
            "phi_ctrl": phi, "pf_phi": gamma * phi,
            "alpha": float(p.get(key, 0.0)), "mass": mass, "W": mass * G,
            "control_node": int(mon)}


def _hinge_summary(po, step: int) -> Optional[dict]:
    if not po.hinge_detail:
        return None
    out = {"elastic": 0, "IO": 0, "LS": 0, "CP": 0, "collapse": 0}
    for hd in po.hinge_detail:
        st = hd.get("state") or []
        if step < len(st) and st[step] in out:
            out[st[step]] += 1
    return out


def evaluate(engine, case, po, options: Optional[dict] = None) -> dict:
    """The performance block for pushover results ``po`` of ``case``."""
    opts = options if options is not None else getattr(case, "performance",
                                                       None)
    modal = modal_props(engine, case)
    out = _pm.evaluate(po.roof_disp, po.base_shear, modal=modal,
                       options=opts,
                       num_stories=len(engine.model.stories))
    for r in out["summary"]:
        d = r.get("roof_disp")
        if d is None or not po.roof_disp:
            r["step"] = None
            continue
        step = min(range(len(po.roof_disp)),
                   key=lambda i: abs(po.roof_disp[i] - d))
        r["step"] = step
        hs = _hinge_summary(po, step)
        if hs is not None:
            r["hinge_summary"] = hs
    return out


def attach(engine, case, res) -> None:
    """``OpenSeesEngine.run`` hook: performance block when configured."""
    if getattr(case, "performance", None) is None or res is None:
        return
    try:
        res.performance = evaluate(engine, case, res)
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        res.performance = {"error": str(exc)}
