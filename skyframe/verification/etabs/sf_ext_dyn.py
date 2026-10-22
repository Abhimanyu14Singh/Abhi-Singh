"""SkyFrame side of the dynamics extension group (cases_x_dyn.py, H1-H4).

Keys: dynTH (load-pattern time histories, direct integration or FNA), dampers (viscous damper links),
ritzCases (Ritz modal cases) and rsMulti (spectrum cases with several loads and a directional
combination).  Definition units kip-ft; SkyFrame works in kN-m (conversions from skyframe_side).
"""

from __future__ import annotations

from skyframe_side import FT, G_FT, KIP

KIPS_FT = KIP / FT                  # kip/ft -> kN/m, and kip-s/ft -> kN-s/m


def _sample(points, dt, n):
    """A (time, value) function, piecewise linear and held at its ends, sampled at k dt, k = 0..n."""
    def at(t):
        if t <= points[0][0]:
            return points[0][1]
        for (t0, v0), (t1, v1) in zip(points, points[1:]):
            if t <= t1:
                return v0 + (v1 - v0) * (t - t0) / (t1 - t0) if t1 > t0 else v1
        return points[-1][1]
    return [at(k * dt) for k in range(n + 1)]


def build(case: dict, m, keep) -> None:
    for dp in case.get("dampers", []):
        m.add_link(tuple(c * FT for c in dp["i"]), tuple(c * FT for c in dp["j"]), uid=dp["id"],
                   link_type="damper", params={"cd": dp["c"] * KIPS_FT, "alpha": 1.0, "k": dp["k"] * KIPS_FT})

    fns = {f["name"]: f["points"] for f in case.get("thFunctions", [])}
    links = [lk["id"] for lk in case.get("links", [])] + [dp["id"] for dp in case.get("dampers", [])]
    for th in case.get("dynTH", []):
        if not keep(th["name"]):
            continue
        comps = []
        for ld in th["loads"]:
            fname = f"{th['name']}_{ld['function']}"            # sampled on this case's grid
            m.add_th_function(fname, _sample(fns[ld["function"]], th["dt"], th["steps"]), th["dt"])
            comps.append({"pattern": ld["pattern"], "function": fname, "scale": float(ld["scale"])})
        dmp = th["damping"]
        kw = {}
        if dmp["type"] == "none":
            # no viscous damping: Rayleigh coefficients both zero (the case ratio is then unused); the
            # engine still wants the initial model's modes (it checks for mass), so ask for one
            kw = dict(damping=0.05, di_damping={"type": "rayleigh_coefficients", "mass_coeff": 0.0,
                                                "stiffness_coeff": 0.0})
            m.num_modes = max(int(m.num_modes or 0), 1)
        elif dmp["type"] == "modal":
            kw = dict(damping=float(dmp["zeta"]), damping_model="modal", modal_zeta=[float(dmp["zeta"])])
        else:
            raise ValueError(f"dynTH damping {dmp!r} not mapped")
        c = m.add_th_case(th["name"], "X", dt=th["dt"], components=comps,
                          output_requests={"links": links} if links else None, **kw)
        if th["method"] == "fna":
            c.th_type = "fna"              # modal case: the model's (stock "modal") eigen case
        elif th["method"] != "direct":
            raise ValueError(f"dynTH method {th['method']!r} not mapped")

    for rc in case.get("ritzCases", []):
        if keep(rc["name"]):
            m.modal_cases[rc["name"]] = {"type": "ritz", "num_modes": int(rc["modes"]),
                                         "ritz_starts": [{"type": "accel", "dir": d} for d in rc["starts"]]}

    spectra = {f["name"]: f for f in case.get("spectra", [])}
    for rs in case.get("rsMulti", []):
        if not keep(rs["name"]):
            continue
        fn = spectra[rs["function"]]
        # SkyFrame takes Sa in g; each load's scale (ft/s2 per g) becomes a factor on g
        m.add_rs_case(rs["name"], rs["loads"][0]["dir"], fn["points"], combo_method=rs["combine"],
                      damping=rs["damping"], scale=1.0,
                      loads=[{"dir": ld["dir"], "scale": ld["scale"] / G_FT} for ld in rs["loads"]],
                      dir_combo=rs["dirCombo"], dir_sf=rs.get("dirSF", 1.0))


def results(case: dict, d: dict, res: dict) -> None:
    vals = res.setdefault("values", {})
    for th in case.get("dynTH", []):
        r = (d.get("th_cases") or {}).get(th["name"])
        if r is None:
            continue
        t = r.get("t") or []
        vmax = 0.0
        for u in (r.get("story_ux") or {}).values():         # velocity by central differences
            for k in range(1, min(len(u), len(t)) - 1):
                vmax = max(vmax, abs(u[k + 1] - u[k - 1]) / (t[k + 1] - t[k - 1]))
        vals[f"{th['name']}:peakVX"] = vmax / FT
        pb = r.get("peaks", {}).get("base", {})
        if "FX" in pb:
            vals[f"{th['name']}:peakBaseFX"] = abs(pb["FX"]) / KIP
        for uid, lk in ((r.get("plot_functions") or {}).get("links") or {}).items():
            # the first basic force component: axial along the link (kN)
            series = lk["force"][lk["components"][0]]
            vals[f"{th['name']}:peakLinkP:{uid}"] = max(abs(f) for f in series) / KIP
    for rc in case.get("ritzCases", []):
        md = (d.get("modal_cases") or {}).get(rc["name"])
        if md is None:
            continue
        for k, T in enumerate(md.get("periods", []), start=1):
            vals[f"{rc['name']}:T{k}"] = T
        for k, p in enumerate(md.get("participation", []), start=1):
            vals[f"{rc['name']}:UX{k}"] = p.get("ux", 0.0)
