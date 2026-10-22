"""Dynamics extension group H1-H4 (kip, ft): nonlinear direct integration, FNA with a viscous damper,
Ritz vectors and response-spectrum load / directional-combination options.

The stock keys do not cover these features, so each case sets ``"ext": "dyn"`` and the builders live in
``sf_ext_dyn.py`` (SkyFrame) and ``etabs_ext_dyn.py`` (ETABS).  Keys the extension reads:

    dynTH      [{name, method "direct" | "fna", loads [{pattern, function, scale}], dt, steps,
                 damping {"type": "none"} | {"type": "modal", "zeta": z}, modalCase?}]
               a time history driven by load patterns times a time function (thFunctions, the stock
               key: ETABS builds the functions itself; SkyFrame samples them at the case's dt).
               "direct": nonlinear direct integration, Newmark average acceleration (ETABS: Hilber-
               Hughes-Taylor alpha 0), no viscous damping; "fna": nonlinear modal history (FNA) on
               modalCase with constant modal damping zeta
    dampers    [{id, i, j, c, k}]   linear viscous damper link along i -> j: dashpot c (kip-s/ft,
               exponent 1) in series with a stiff spring k (kip/ft); no linear effective stiffness or
               damping, so it acts only in nonlinear cases (FNA)
    ritzCases  [{name, modes, starts ["UX", ...]}]   Ritz-vector modal case, starting vectors from
               ground acceleration in the listed directions
    rsMulti    [{name, function, loads [{dir "X"|"Y", scale (ft/s2 per g of the function)}],
                 combine "CQC", damping, dirCombo "SRSS" | "ABS", dirSF}]   response spectrum with
               several loads in one case and a directional combination

Named scalars both programs export (res["values"]):
    <case>:peakLinkP:<id>    peak |axial force| of a link over a time history
    <case>:peakBaseFX        peak |base shear X| over a time history
    <case>:peakVX            peak |velocity X| of the mass (SkyFrame: central differences of its story
                             displacement history; ETABS: joint velocity envelope at the top joint)
    <modal>:T<n>, <modal>:UX<n>   period and UX participating-mass ratio of mode n of a modal case
"""

from __future__ import annotations

import math

from defs import STEEL_E, _COMMON, _b, _col_section, _j, _mat, _v

from cases_dyn import _modal

G_FT = 9.80665 / 0.3048            # ft/s2


def _cantilever(L=12.0, angle=0.0):
    top = [0.0, 0.0, L]
    return top, [{"id": "C1", "i": [0.0, 0.0, 0.0], "j": top, "section": "COL", "angle": angle}]


def H1() -> dict:
    """Nonlinear direct integration: a one-mass cantilever under a step force strikes a gap.

    Mass m on a cantilever (k1 = 3EI/L^3, T = 0.5 s), no damping, step force F0 at t = 0.  A compression
    gap of stiffness k2 = 3 k1 sits a distance d = F0/k1 (the static sway) away from the mass.  Without
    the gap the mass would swing to 2 F0/k1.  The motion is piecewise linear and conservative, so the
    peak follows from the work-energy balance at zero velocity:
        F0 u = k1 u^2 / 2 + k2 (u - d)^2 / 2
        u_max = [(F0 + k2 d) + sqrt((F0 + k2 d)^2 - (k1 + k2) k2 d^2)] / (k1 + k2)  (= 1.5 F0/k1 here)
    At the peak the gap carries k2 (u_max - d) and the column k1 u_max; base shear is their sum.
    (Phase 1: u = u_st (1 - cos wt) to d at t = T/4; phase 2: stiffness 4 k1, oscillation about u_st
    with amplitude u_st w / 2w = u_st / 2: the same 1.5 u_st, reached at t = 3T/8.)"""
    E, L, I33 = STEEL_E, 12.0, 0.02
    k1 = 3 * E * I33 / L**3
    T = 0.5
    m = k1 / (2 * math.pi / T)**2
    F0, k2 = 5.0, 3 * k1
    d = F0 / k1
    b = F0 + k2 * d
    u = (b + math.sqrt(b * b - (k1 + k2) * k2 * d * d)) / (k1 + k2)
    top, frames = _cantilever(L)
    ground = [2.0, 0.0, L]                                   # gap from the mass to a fixed point at +X
    dt, n = 0.001, 300
    return {
        "id": "H1", "title": "Nonlinear time history, gap impact",
        "purpose": ("A one-mass cantilever (T = 0.5 s, no damping) under a suddenly applied 5 kip force swings "
                    "towards a compression-only gap (k = 3 k_column) that closes at the static sway. Solved by "
                    "nonlinear direct integration in both programs; the peak follows from work = strain "
                    "energy: 1.5 F0/k instead of 2 F0/k without the gap."),
        "units": "kip-ft", "ext": "dyn",
        "settings": dict(_COMMON, mass="one joint mass, X only",
                         nonlinear="gap link (compression only, opening = F0/k1), nonlinear direct integration",
                         timeHistory="load pattern F x step function from t = 0, 0.3 s at 0.001 s, no damping, "
                                     "Newmark average acceleration (ETABS: HHT alpha 0)",
                         supports="fixed base; gap to a fixed point 2 ft along +X"),
        "materials": _mat(), "sections": [_col_section()],
        "stories": [{"name": "Story1", "height": L}],
        "frames": frames,
        "supports": [{"at": [0.0, 0.0, 0.0], "restraint": [1] * 6}, {"at": ground, "restraint": [1] * 6}],
        "masses": [{"at": top, "m": [m, 0, 0, 0, 0, 0]}],
        "massSource": {"elements": False, "masses": True, "patterns": {}},
        "links": [{"id": "G1", "i": top, "j": ground, "type": "gap", "k": k2, "open": d}],
        "patterns": [{"name": "F"}],
        "pointLoads": [{"pattern": "F", "at": top, "f": [F0, 0, 0, 0, 0, 0]}],
        "thFunctions": [{"name": "STEP", "points": [[0.0, 1.0], [dt * n, 1.0]]}],
        "dynTH": [{"name": "NLTH", "method": "direct", "loads": [{"pattern": "F", "function": "STEP", "scale": 1.0}],
                   "dt": dt, "steps": n, "damping": {"type": "none"}}],
        "extCases": ["NLTH"],
        "checks": [
            dict(_j("NLTH-UX", "Peak sway (gap closed)", "NLTH", top, "UX", u,
                    "F0 u = k1 u^2/2 + k2 (u - d)^2/2  ->  1.5 F0/k1", how="abs"), tol=[2e-3, 5e-3]),
            dict(_v("NLTH-G", "Peak gap force", "NLTH:peakLinkP:G1", k2 * (u - d), "k2 (u_max - d) = 1.5 F0",
                    how="abs"), tol=[3e-3, 5e-3]),
            dict(_v("NLTH-V", "Peak base shear (column + gap)", "NLTH:peakBaseFX", k1 * u + k2 * (u - d),
                    "k1 u_max + k2 (u_max - d) = 3 F0", how="abs"), tol=[3e-3, 5e-3]),
        ],
    }


def _step_peak(zeta):
    """Step response of a damped SDOF, per unit static deflection: peak displacement and peak velocity / w."""
    s = math.sqrt(1 - zeta * zeta)
    u = 1 + math.exp(-math.pi * zeta / s)
    ts = math.atan2(s, zeta) / s                              # w t* (velocity peak)
    v = math.exp(-zeta * ts)
    return u, v


def H2() -> dict:
    """FNA: a one-mass cantilever with a linear viscous damper to ground under a step force.

    Damper c = 2 zeta_d m w with zeta_d = 0.08 (series spring 1e5 k/ft: relaxation time c/k ~ 2e-5 s,
    a pure dashpot to 1e-7), modal damping zeta_0 = 0.02, so the SDOF has zeta = 0.10.  Step force F0:
        u(t) = u_st [1 - e^(-zeta w t) (cos wd t + zeta / sqrt(1 - zeta^2) sin wd t)]
        u_max = u_st (1 + exp(-pi zeta / sqrt(1 - zeta^2)))
        v(t) = u_st w / sqrt(1 - zeta^2) e^(-zeta w t) sin wd t, peak at tan(wd t*) = sqrt(1 - zeta^2) / zeta:
        v_max = u_st w exp(-zeta w t*),  damper force c v_max."""
    E, L, I33 = STEEL_E, 12.0, 0.02
    k = 3 * E * I33 / L**3
    T = 0.5
    w = 2 * math.pi / T
    m = k / w**2
    F0, z0, zd, kd = 5.0, 0.02, 0.08, 1.0e5
    c = 2 * zd * m * w
    zeta = z0 + zd
    ust = F0 / k
    pu, pv = _step_peak(zeta)
    top, frames = _cantilever(L)
    ground = [2.0, 0.0, L]
    dt, n = 0.001, 400
    return {
        "id": "H2", "title": "FNA with a viscous damper",
        "purpose": ("A one-mass cantilever (T = 0.5 s, 2% modal damping) with a linear viscous damper to ground "
                    "adding 8% of critical, under a suddenly applied 5 kip force, solved by fast nonlinear "
                    "analysis (FNA) on its one mode. The damper is a nonlinear link, so its force enters the "
                    "modal equations as a pseudo-force: the response must be the 10%-damped closed form."),
        "units": "kip-ft", "ext": "dyn",
        "settings": dict(_COMMON, mass="one joint mass, X only", modal="eigen, 1 mode",
                         nonlinear="viscous damper link (c, exponent 1, series spring 1e5 k/ft, no linear "
                                   "effective stiffness or damping), FNA",
                         timeHistory="load pattern F x step function from t = 0, 0.4 s at 0.001 s, constant "
                                     "modal damping 0.02",
                         supports="fixed base; damper to a fixed point 2 ft along +X"),
        "materials": _mat(), "sections": [_col_section()],
        "stories": [{"name": "Story1", "height": L}],
        "frames": frames,
        "supports": [{"at": [0.0, 0.0, 0.0], "restraint": [1] * 6}, {"at": ground, "restraint": [1] * 6}],
        "masses": [{"at": top, "m": [m, 0, 0, 0, 0, 0]}],
        "massSource": {"elements": False, "masses": True, "patterns": {}},
        "modal": {"case": "MODES", "modes": 1},
        "dampers": [{"id": "D1", "i": top, "j": ground, "c": c, "k": kd}],
        "patterns": [{"name": "F"}],
        "pointLoads": [{"pattern": "F", "at": top, "f": [F0, 0, 0, 0, 0, 0]}],
        "thFunctions": [{"name": "STEP", "points": [[0.0, 1.0], [dt * n, 1.0]]}],
        "dynTH": [{"name": "FNA", "method": "fna", "modalCase": "MODES",
                   "loads": [{"pattern": "F", "function": "STEP", "scale": 1.0}],
                   "dt": dt, "steps": n, "damping": {"type": "modal", "zeta": z0}}],
        "extCases": ["FNA"],
        "checks": [
            _modal("T1", "Period", 1, "T", T, "2 pi sqrt(m / k)"),
            dict(_j("FNA-UX", "Peak sway, 10% damping", "FNA", top, "UX", ust * pu,
                    "F0/k (1 + exp(-pi z / sqrt(1 - z^2))), z = 0.02 + c / 2mw", how="abs"), tol=[2e-3, 5e-3]),
            # SkyFrame's FNA reports no device forces: the damper is judged by the velocity it allows
            # (damper force = c x this); ETABS also logs its link force
            dict(_v("FNA-V", "Peak velocity (damper force / c)", "FNA:peakVX", ust * w * pv,
                    "(F0/k) w exp(-z w t*), tan(wd t*) = sqrt(1 - z^2) / z", how="abs"),
                 tol=[2e-3, 5e-3]),
        ],
    }


def H3() -> dict:
    """Ritz vectors from the X ground acceleration on a two-story shear building.

    Equal story masses m and story stiffness k = 12 E I33 / h^3 (floor joints fixed in rotation).
    Eigen: w^2 = (k/m) (3 -+ sqrt 5)/2, mode 1 shape (1, phi), phi = (1 + sqrt 5)/2, UX mass ratio
    (1 + phi)^2 / (2 (1 + phi^2)).
    Ritz, one vector: x1 = K^-1 M r = (2, 3) m/k (story shears 2m, m), so the Rayleigh quotient gives
    w^2 = x1' K x1 / x1' M x1 = (5 m^2/k) / (13 m^3/k^2) = 5k / 13m (period 0.35% below eigen: an upper
    bound on w^2) and UX ratio (x1' M r)^2 / (x1' M x1 * r' M r) = 25 m^2 / (13 m * 2 m) = 25/26.
    Ritz, two vectors: x2 = K^-1 M x1 completes the space, so both periods and ratios equal eigen."""
    E, h, I33, m = STEEL_E, 12.0, 0.02, 0.5
    k = 12 * E * I33 / h**3
    phi = (1 + math.sqrt(5)) / 2
    w1, w2 = math.sqrt((3 - math.sqrt(5)) / 2 * k / m), math.sqrt((3 + math.sqrt(5)) / 2 * k / m)
    T1, T2 = 2 * math.pi / w1, 2 * math.pi / w2
    r1 = (1 + phi)**2 / (2 * (1 + phi * phi))
    TR = 2 * math.pi / math.sqrt(5 * k / (13 * m))
    pts = [[0.0, 0.0, h * s] for s in range(3)]
    return {
        "id": "H3", "title": "Ritz vectors",
        "purpose": ("A two-story shear building analysed with Ritz vectors started from the X ground "
                    "acceleration. One vector is the static deflection under the floor inertia forces: its "
                    "period is the Rayleigh quotient (just below the first eigen period) and it carries 25/26 "
                    "of the mass. Two vectors span the whole space and reproduce the eigen solution exactly."),
        "units": "kip-ft", "ext": "dyn",
        "settings": dict(_COMMON, mass="joint masses at both floors, X only",
                         modal="eigen (2 modes) and Ritz from acceleration UX (1 and 2 vectors)",
                         supports="fixed base; floor joints held in Y, Z and rotation (shear building)"),
        "materials": _mat(), "sections": [_col_section()],
        "stories": [{"name": "Story1", "height": h}, {"name": "Story2", "height": h}],
        "frames": [{"id": f"C{s + 1}", "i": pts[s], "j": pts[s + 1], "section": "COL", "angle": 0.0}
                   for s in range(2)],
        "supports": [{"at": pts[0], "restraint": [1] * 6}] + [{"at": p, "restraint": [0, 1, 1, 1, 1, 1]}
                                                             for p in pts[1:]],
        "masses": [{"at": p, "m": [m, 0, 0, 0, 0, 0]} for p in pts[1:]],
        "massSource": {"elements": False, "masses": True, "patterns": {}},
        "modal": {"case": "MODES", "modes": 2},
        "ritzCases": [{"name": "RITZ1", "modes": 1, "starts": ["UX"]},
                      {"name": "RITZ2", "modes": 2, "starts": ["UX"]}],
        "patterns": [],
        "checks": [
            _modal("T1", "Eigen mode 1 period", 1, "T", T1, "2 pi / w1, w1^2 = (3 - sqrt 5)/2 k/m"),
            _v("R1-T1", "One Ritz vector: period (Rayleigh quotient)", "RITZ1:T1", TR, "2 pi sqrt(13 m / 5 k)"),
            _v("R1-UX", "One Ritz vector: UX mass ratio", "RITZ1:UX1", 25 / 26, "(x1' M r)^2 / (x1' M x1 r' M r) = 25/26"),
            _v("R2-T1", "Two Ritz vectors: period 1 = eigen", "RITZ2:T1", T1, "2 pi / w1"),
            _v("R2-T2", "Two Ritz vectors: period 2 = eigen", "RITZ2:T2", T2, "2 pi / w2, w2^2 = (3 + sqrt 5)/2 k/m"),
            _v("R2-UX1", "Two Ritz vectors: mode 1 UX mass ratio = eigen", "RITZ2:UX1", r1,
               "(1 + phi)^2 / 2(1 + phi^2), phi = (1 + sqrt 5)/2"),
        ],
    }


def _rho(r, zeta):
    return 8 * zeta**2 * (1 + r) * r**1.5 / ((1 - r * r)**2 + 4 * zeta**2 * r * (1 + r)**2)


def H4() -> dict:
    """Response spectrum with X and Y loads in one case: CQC of coupled modes, SRSS and ABS across directions.

    A cantilever turned 30 degrees about its axis carries one mass m in X and Y: its modes are the two
    principal sways, a along local 2 e_a = (cos 30, sin 30), k_a = 3EI33/L^3, and b along local 3
    e_b = (-sin 30, cos 30), k_b = 3EI22/L^3 (w_a = 2 w_b).  A flat spectrum Sa (0.4 g) is applied along
    X with scale s_X = 1.0 g and along Y with s_Y = 0.6 g.  For a load along d, mode i gives the
    displacement component (e_i.d)(e_i.c) S_d / w_i^2 along c, and the base force m S_d (e_i.d)(e_i.c);
    the two modes combine by CQC (5%, rho = 8 z^2 (1+r) r^1.5 / ((1-r^2)^2 + 4 z^2 r (1+r)^2), r = 2):
        R_d = sqrt(r_a^2 + r_b^2 + 2 rho r_a r_b)
    and the directions by SRSS, sqrt(R_X^2 + R_Y^2), or ABS, R_X + R_Y."""
    E, L, I33, I22, m, th = STEEL_E, 12.0, 0.02, 0.005, 0.5, math.radians(30.0)
    ka, kb = 3 * E * I33 / L**3, 3 * E * I22 / L**3
    wa2, wb2 = ka / m, kb / m
    zeta, Sa = 0.05, 0.4
    rho = _rho(math.sqrt(wa2 / wb2), zeta)
    ea, eb = (math.cos(th), math.sin(th)), (-math.sin(th), math.cos(th))
    loads = {"X": ((1.0, 0.0), 1.0), "Y": ((0.0, 1.0), 0.6)}

    def per_dir(comp, base):
        """CQC response along unit vector comp to each load: {dir: R_d}."""
        out = {}
        for dname, (dv, s) in loads.items():
            S = Sa * s * G_FT
            r = []
            for e, w2 in ((ea, wa2), (eb, wb2)):
                v = (e[0] * dv[0] + e[1] * dv[1]) * (e[0] * comp[0] + e[1] * comp[1]) * S
                r.append(v * m if base else v / w2)
            out[dname] = math.sqrt(r[0]**2 + r[1]**2 + 2 * rho * r[0] * r[1])
        return out

    srss = lambda o: math.sqrt(sum(v * v for v in o.values()))
    absum = lambda o: sum(o.values())
    ux, uy = per_dir((1, 0), False), per_dir((0, 1), False)
    fx, fy = per_dir((1, 0), True), per_dir((0, 1), True)
    top, frames = _cantilever(L, angle=30.0)
    rs = lambda name, dc: {"name": name, "function": "FLAT", "combine": "CQC", "damping": zeta,
                           "dirCombo": dc, "dirSF": 1.0,
                           "loads": [{"dir": "X", "scale": 1.0 * G_FT}, {"dir": "Y", "scale": 0.6 * G_FT}]}
    f = "CQC of the two principal modes per load, then"
    return {
        "id": "H4", "title": "Spectrum: two directions, SRSS and ABS",
        "purpose": ("One spectrum case with loads along X (scale 1.0 g) and Y (0.6 g) on a cantilever turned 30 "
                    "degrees, so each load excites both principal modes: modal responses combine by CQC "
                    "(coupled through rho for w_a = 2 w_b) and the two directions by SRSS (ETABS default) or "
                    "absolute sum (set through the load-case table)."),
        "units": "kip-ft", "ext": "dyn",
        "settings": dict(_COMMON, mass="one joint mass, X and Y", modal="eigen, 2 modes",
                         spectrum="constant 0.4 g, 5% damping; loads U1 x 1.0 g and U2 x 0.6 g",
                         combination="CQC at 5%; directions SRSS (RS2) and absolute, scale 1 (RS2ABS)",
                         eccentricity="none", supports="fixed base"),
        "materials": _mat(), "sections": [_col_section()],
        "stories": [{"name": "Story1", "height": L}],
        "frames": frames,
        "supports": [{"at": [0.0, 0.0, 0.0], "restraint": [1] * 6}],
        "masses": [{"at": top, "m": [m, m, 0, 0, 0, 0]}],
        "massSource": {"elements": False, "masses": True, "patterns": {}},
        "modal": {"case": "MODES", "modes": 2},
        "spectra": [{"name": "FLAT", "points": [[0.0, Sa], [10.0, Sa]], "damping": zeta}],
        "rsMulti": [rs("RS2", "SRSS"), rs("RS2ABS", "ABS")],
        "extCases": ["RS2", "RS2ABS"],
        "patterns": [],
        "checks": [
            _modal("T1", "Period, weak-axis sway (local 3)", 1, "T", 2 * math.pi / math.sqrt(wb2), "2 pi sqrt(m / kb)"),
            _modal("T2", "Period, strong-axis sway (local 2)", 2, "T", 2 * math.pi / math.sqrt(wa2), "2 pi sqrt(m / ka)"),
            _j("RS2-UX", "UX, directions by SRSS", "RS2", top, "UX", srss(ux), f + " SRSS of X and Y", how="abs"),
            _j("RS2-UY", "UY, directions by SRSS", "RS2", top, "UY", srss(uy), f + " SRSS of X and Y", how="abs"),
            _b("RS2-FX", "Base shear X, SRSS", "RS2", "FX", srss(fx), f + " SRSS of X and Y"),
            _b("RS2-FY", "Base shear Y, SRSS", "RS2", "FY", srss(fy), f + " SRSS of X and Y"),
            _j("ABS-UX", "UX, directions by absolute sum", "RS2ABS", top, "UX", absum(ux), f + " |X| + |Y|", how="abs"),
            _j("ABS-UY", "UY, directions by absolute sum", "RS2ABS", top, "UY", absum(uy), f + " |X| + |Y|", how="abs"),
            _b("ABS-FX", "Base shear X, absolute sum", "RS2ABS", "FX", absum(fx), f + " |X| + |Y|"),
        ],
    }


CASES = {"H1": H1, "H2": H2, "H3": H3, "H4": H4}
