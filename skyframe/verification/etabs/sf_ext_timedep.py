"""SkyFrame side of the time-dependent group (cases_x_timedep.py): ACI 209R-92 material properties
and a one-stage, step-by-step staged case (C1, C2), and the N6 pushover with its hinges (C3).

SkyFrame works in kN and m; the definition is kip-ft (conversions from skyframe_side)."""

from __future__ import annotations

from skyframe_side import FT, KIPFT

_LAW = {"ACI 209R-92": "aci209"}


def td_props(td: dict) -> dict:
    """The definition's timeDependent block as SkyFrame material_time_dependent props."""
    return {"law": _LAW[td["law"]], "rh": float(td["rh"]),
            "h0": 2.0 * td["vs"] * FT,                    # notional size 2 Ac/u = 2 V/S, m
            "cure_days": float(td["shrinkStart"]), "cement": td["cement"], "curing": td["curing"],
            "creep": bool(td["creep"]), "shrinkage": bool(td["shrinkage"]), "aging": bool(td["aging"])}


def build(case, m, keep) -> None:
    td = case.get("timeDependent")
    if td:
        m.material_time_dependent = {td["material"]: td_props(td)}
    st = case.get("tdStaged")
    if st and keep(st["name"]):
        pts = {tuple(round(c * FT, 9) for c in p) for f in case["frames"] for p in (f["i"], f["j"])}
        m.add_group("ALL", members=[f["id"] for f in case["frames"]], points=[list(p) for p in sorted(pts)])
        ops = [{"op": "add", "group": "ALL", "age_days": float(st["age"])}]
        if st.get("pattern"):
            ops.append({"op": "load", "group": "ALL", "pattern": st["pattern"]})
        m.add_staged_case(st["name"], stages=[{"name": "S1", "duration_days": float(st["duration"]),
                                               "operations": ops}],
                          time_dependent={"method": "step", "steps_per_stage": 8})
    for po in case.get("extPushover", []):
        if not keep(po["name"]):
            continue
        h = sum(s["height"] for s in case["stories"])
        m.add_pushover_case(po["name"], po["dir"], target_drift=po["target"] / h, steps=po["steps"],
                            hardening=0.0, control_point=[c * FT for c in po["control"]],
                            hinges="all_ends", My={f: v * KIPFT for f, v in po["hinges"].items()})


def _uz(res: dict, cname: str, at) -> float:
    import math
    c = res["cases"].get(cname) or {}
    best = min(c.get("joints") or [{"xyz": [1e9] * 3, "u": [0.0] * 6}], key=lambda j: math.dist(j["xyz"], at))
    return best["u"][2] if math.dist(best["xyz"], at) < 1e-3 else None


def values(case: dict, res: dict) -> None:
    """The named scalars both programs export (shared by etabs_ext_timedep)."""
    keys = {c.get("key") for c in case["checks"] if c["quantity"] == "value"}
    st = case.get("tdStaged") or {}
    if not st:
        return
    top = max((p for f in case["frames"] for p in (f["i"], f["j"])), key=lambda p: p[2])
    u_td = _uz(res, st["name"], top)
    if "phi" in keys and st.get("pattern"):
        u_el = _uz(res, st["pattern"], top)
        if u_td is not None and u_el:
            res["values"]["phi"] = u_td / u_el - 1.0
    if "eps_sh" in keys and u_td is not None:
        res["values"]["eps_sh"] = -u_td / top[2]


def results(case, d, res) -> None:
    values(case, res)
