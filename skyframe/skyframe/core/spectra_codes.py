"""Code response-spectrum functions (ETABS Define > Functions > Response
Spectrum > "From code") — CONTRACT "Code spectra, design combinations,
pattern types and live-load reduction".

Every code spectrum is a closed-form ``Sa(T)`` (in g) built from the code's
own parameters, sampled into ``[[T, Sa], ...]`` points (every corner period
exactly + a dense grid) so it can be stored as a
:class:`~skyframe.core.model.SpectrumFunction`.  The parameters are kept on
the function as ``source = {"code", "params"}`` so the dialog can reopen
them; the solver only ever reads ``points``.

Codes (key -> clause):

* ``asce7_16``          ASCE 7-16 §11.4.6 (Fa/Fv tables, existing helper);
* ``asce7_22_mprs``     ASCE 7-22 §11.4.5.1 multi-period spectrum from the
                        USGS tabulated site-class ordinates (22 periods,
                        MCE_R or design basis; Sa = 2/3 MCE_R; linear
                        interpolation; Supplement-3 long-period extension);
* ``asce7_22_two_period`` ASCE 7-22 §11.4.5.2 two-period fallback (SDS, SD1);
* ``ec8``               EN 1998-1 §3.2.2.2 elastic (Eq. 3.2-3.5) and
                        §3.2.2.5 design (Eq. 3.13-3.16, q, beta) spectra,
                        Type 1 / Type 2, ground A-E, national-annex
                        overrides of S, TB, TC, TD;
* ``is1893_2016``       IS 1893 (Part 1):2016 §6.4.2 response-spectrum
                        method Sa/g (Fig. 2b), Ah = (Z/2)(I/R)(Sa/g) x
                        damping factor (Table 3);
* ``nbcc_2020``         NBCC 2020 4.1.8.4(7): S(T) from the six site
                        ordinates, plateau max(Sa(0.2), Sa(0.5)), linear
                        interpolation, S(10) beyond 10 s; design scale
                        Mv Ie/(Rd Ro);
* ``nzs1170_5``         NZS 1170.5:2004 §3.1 C(T) = Ch(T) Z R N (Table 3.1,
                        modal/numerical Ch), design Cd(T) = C(T) Sp/k_mu
                        (§5.2.1.1) with its floor;
* ``as1170_4``          AS 1170.4-2007 §6.4 Ch(T) (Table 6.4), kp Z Ch(T)
                        and the design kp Z Ch(T) Sp/mu;
* ``gb50011``           GB 50011-2010 §5.1.4-5.1.5 seismic influence
                        coefficient alpha(T) (alpha_max Table 5.1.4-1, Tg
                        Table 5.1.4-2, damping-adjusted gamma/eta1/eta2).

Public API::

    catalogue()                       -> [{code, label, clause, params:[...]}]
    normalize_params(code, params)    -> full validated params dict
    spectrum_value(code, params, T)   -> Sa(T) [g]
    spectrum(code, params)            -> {code, label, params, points, summary}
    make_spectrum_function(model, name, code, params, damping=None)
    validate_source(source)           (model hook: SpectrumFunction.source)
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# ASCE 7-22 multi-period periods (USGS 22-period table, §11.4.5.1)
# --------------------------------------------------------------------------- #
MPRS_PERIODS = (0.0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2, 0.25,
                0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.5,
                10.0)

# EN 1998-1 Tables 3.2 / 3.3: ground type -> (S, TB, TC, TD)
EC8_GROUND = {
    1: {"A": (1.0, 0.15, 0.4, 2.0), "B": (1.2, 0.15, 0.5, 2.0),
        "C": (1.15, 0.20, 0.6, 2.0), "D": (1.35, 0.20, 0.8, 2.0),
        "E": (1.4, 0.15, 0.5, 2.0)},
    2: {"A": (1.0, 0.05, 0.25, 1.2), "B": (1.35, 0.05, 0.25, 1.2),
        "C": (1.5, 0.10, 0.25, 1.2), "D": (1.8, 0.10, 0.30, 1.2),
        "E": (1.6, 0.05, 0.25, 1.2)},
}

# IS 1893:2016 Table 3 zone factors and Fig. 2b corner periods
IS1893_ZONES = {"II": 0.10, "III": 0.16, "IV": 0.24, "V": 0.36}
IS1893_SOIL_RS = {"I": (0.40, 1.00, 0.25), "II": (0.55, 1.36, 0.34),
                  "III": (0.67, 1.67, 0.42)}
# IS 1893:2016 Table 3 damping multiplying factor (damping % -> factor)
IS1893_DAMPING = ((0.0, 3.20), (2.0, 1.40), (5.0, 1.00), (7.0, 0.90),
                  (10.0, 0.80), (15.0, 0.70), (20.0, 0.60), (25.0, 0.55),
                  (30.0, 0.50))

# NBCC 2020 4.1.8.4(7) ordinates (period -> parameter key)
NBCC_ORDINATES = ((0.2, "Sa02"), (0.5, "Sa05"), (1.0, "Sa10"),
                  (2.0, "Sa20"), (5.0, "Sa50"), (10.0, "Sa100"))

# NZS 1170.5 Table 3.1 / Eq. 3.1(1): site class -> Ch(T) piecewise data
# (Ch(0) modal/numerical value, slope to T = 0.1, plateau, plateau end,
#  descending branch (coef, Tref) for coef*(Tref/T)^0.75 up to 1.5 s,
#  1.5-3 s numerator a/T, > 3 s numerator b/T^2).
NZS_SITES = ("A", "B", "C", "D", "E")

# AS 1170.4-2007 Table 6.4: site -> (Ch0, slope, cap, k_mid, k_long)
AS_SITES = {"Ae": (0.8, 15.5, 2.35, 0.704, 1.056),
            "Be": (1.0, 19.4, 2.94, 0.88, 1.32),
            "Ce": (1.3, 23.8, 3.68, 1.25, 1.874),
            "De": (1.1, 25.8, 3.68, 1.98, 2.97),
            "Ee": (1.1, 25.8, 3.68, 3.08, 4.62)}

# GB 50011-2010 Table 5.1.4-1 alpha_max by intensity and level
GB_ALPHA_MAX = {
    "6": {"frequent": 0.04, "fortification": 0.12, "rare": 0.28},
    "7": {"frequent": 0.08, "fortification": 0.23, "rare": 0.50},
    "7.5": {"frequent": 0.12, "fortification": 0.34, "rare": 0.72},
    "8": {"frequent": 0.16, "fortification": 0.45, "rare": 0.90},
    "8.5": {"frequent": 0.24, "fortification": 0.68, "rare": 1.20},
    "9": {"frequent": 0.32, "fortification": 0.90, "rare": 1.40},
}
# Table 5.1.4-2 characteristic period Tg: group -> site -> Tg
GB_TG = {
    "1": {"I0": 0.20, "I1": 0.25, "II": 0.35, "III": 0.45, "IV": 0.65},
    "2": {"I0": 0.25, "I1": 0.30, "II": 0.40, "III": 0.55, "IV": 0.75},
    "3": {"I0": 0.30, "I1": 0.35, "II": 0.45, "III": 0.65, "IV": 0.90},
}


# --------------------------------------------------------------------------- #
# parameter schema helpers
# --------------------------------------------------------------------------- #
def _p(key, label, default, type_="number", **kw) -> dict:
    d = {"key": key, "label": label, "type": type_, "default": default}
    d.update(kw)
    return d


def _ch(key, label, default, choices, **kw) -> dict:
    return _p(key, label, default, "choice",
              choices=[[c, l] for c, l in choices], **kw)


def _finite(v) -> bool:
    return (not isinstance(v, bool) and isinstance(v, (int, float))
            and math.isfinite(v))


def _check_number(code: str, spec: dict, v):
    k = spec["key"]
    if v is None:
        if spec.get("nullable"):
            return None
        raise ValueError(f"{code}: {k} is required")
    if not _finite(v):
        raise ValueError(f"{code}: {k} must be a finite number, got {v!r}")
    v = float(v)
    if "gt" in spec and not v > spec["gt"]:
        raise ValueError(f"{code}: {k} must be > {spec['gt']}")
    if "min" in spec and v < spec["min"]:
        raise ValueError(f"{code}: {k} must be >= {spec['min']}")
    if "lt" in spec and not v < spec["lt"]:
        raise ValueError(f"{code}: {k} must be < {spec['lt']}")
    if "max" in spec and v > spec["max"]:
        raise ValueError(f"{code}: {k} must be <= {spec['max']}")
    return v


# --------------------------------------------------------------------------- #
# sampling
# --------------------------------------------------------------------------- #
def _sample(fn: Callable[[float], float], corners: Sequence[float],
            t_max: float) -> List[List[float]]:
    """Points at every corner period (exact) plus a grid: 0.02 s to 1 s,
    0.05 s to 4 s, 0.25 s beyond, up to ``t_max``."""
    ts = set()

    def grid(a, b, h):
        n = int(round((b - a) / h))
        for i in range(n + 1):
            t = round(a + i * h, 10)
            if t <= t_max + 1e-12:
                ts.add(t)
    grid(0.0, min(1.0, t_max), 0.02)
    if t_max > 1.0:
        grid(1.0, min(4.0, t_max), 0.05)
    if t_max > 4.0:
        grid(4.0, t_max, 0.25)
    for c in corners:
        if 0.0 <= c <= t_max + 1e-12:
            ts.add(round(float(c), 10))
    ts.add(round(float(t_max), 10))
    return [[float(t), float(fn(t))] for t in sorted(ts)]


# --------------------------------------------------------------------------- #
# ASCE 7-16 (existing closed form in core.codes)
# --------------------------------------------------------------------------- #
def _asce7_16(p: dict):
    from .codes import spectrum_parameters, spectrum_value as _sv
    sds, sd1, sms, sm1, t0, ts = spectrum_parameters(p["Ss"], p["S1"],
                                                     p["site_class"])
    TL, sc = p["TL"], p["Ie"] / p["R"]

    def f(T):
        return sc * _sv(T, sds, sd1, t0, ts, TL)
    return f, (0.0, t0, ts, TL), {"SDS": sds, "SD1": sd1, "SMS": sms,
                                  "SM1": sm1, "T0": t0, "Ts": ts, "TL": TL,
                                  "scale": sc}


# --------------------------------------------------------------------------- #
# ASCE 7-22
# --------------------------------------------------------------------------- #
def _mprs_points(code: str, v) -> List[List[float]]:
    """Ordinates: either 22 Sa values aligned to :data:`MPRS_PERIODS` or a
    list of >= 2 [T, Sa] pairs (strictly increasing T)."""
    if not isinstance(v, (list, tuple)) or len(v) < 2:
        raise ValueError(f"{code}: ordinates must be 22 Sa values or >= 2 "
                         "[T, Sa] pairs")
    if all(_finite(x) for x in v):
        if len(v) != len(MPRS_PERIODS):
            raise ValueError(f"{code}: a plain Sa list must have "
                             f"{len(MPRS_PERIODS)} values (one per USGS "
                             "period)")
        pts = [[t, float(s)] for t, s in zip(MPRS_PERIODS, v)]
    else:
        pts = []
        for q in v:
            if (not isinstance(q, (list, tuple)) or len(q) != 2
                    or not all(_finite(x) for x in q)):
                raise ValueError(f"{code}: ordinates must be [T, Sa] pairs")
            pts.append([float(q[0]), float(q[1])])
    for t, s in pts:
        if t < 0.0 or s < 0.0:
            raise ValueError(f"{code}: ordinates must be >= 0")
    for a, b in zip(pts, pts[1:]):
        if not b[0] > a[0]:
            raise ValueError(f"{code}: ordinate periods must increase")
    return pts


def _asce7_22_mprs(p: dict):
    from .autolateral import _mprs_sa, mprs_sd1, mprs_sds
    raw = _mprs_points("asce7_22_mprs", p["ordinates"])
    f23 = 2.0 / 3.0 if p["basis"] == "mcer" else 1.0
    pts = [[t, f23 * s] for t, s in raw]
    TL, sc = p["TL"], p["Ie"] / p["R"]

    def f(T):
        return sc * _mprs_sa(pts, T, TL)
    summ = {"basis": p["basis"], "SDS": mprs_sds(pts, TL), "TL": TL,
            "scale": sc, "T_last": pts[-1][0],
            "Sa_max": max(s for _, s in pts)}
    if p.get("vs30") is not None:
        summ["SD1"] = mprs_sd1(pts, p["vs30"], TL)
    return f, [t for t, _ in pts] + [TL], summ


def _asce7_22_two(p: dict):
    sds, sd1, TL = p["SDS"], p["SD1"], p["TL"]
    t0, ts = 0.2 * sd1 / sds, sd1 / sds
    sc = p["Ie"] / p["R"]

    def f(T):
        if T <= t0:
            v = sds * (0.4 + 0.6 * T / t0) if t0 > 0 else sds
        elif T <= ts:
            v = sds
        elif T <= TL:
            v = sd1 / T
        else:
            v = sd1 * TL / (T * T)
        return sc * v
    return f, (0.0, t0, ts, TL), {"SDS": sds, "SD1": sd1, "T0": t0,
                                  "Ts": ts, "TL": TL, "scale": sc}


# --------------------------------------------------------------------------- #
# EN 1998-1
# --------------------------------------------------------------------------- #
def _ec8(p: dict):
    t = int(p["spectrum_type"])
    S0, TB0, TC0, TD0 = EC8_GROUND[t][p["ground_type"]]
    S = S0 if p.get("S") is None else p["S"]
    TB = TB0 if p.get("TB") is None else p["TB"]
    TC = TC0 if p.get("TC") is None else p["TC"]
    TD = TD0 if p.get("TD") is None else p["TD"]
    if not TB < TC < TD:
        raise ValueError("ec8: need TB < TC < TD")
    ag = p["gamma_I"] * p["agR"]
    if p["kind"] == "elastic":
        eta = max(math.sqrt(10.0 / (5.0 + 100.0 * p["damping"])), 0.55)

        def f(T):
            if T <= TB:
                return ag * S * (1.0 + T / TB * (eta * 2.5 - 1.0))
            if T <= TC:
                return ag * S * eta * 2.5
            if T <= TD:
                return ag * S * eta * 2.5 * TC / T
            return ag * S * eta * 2.5 * TC * TD / (T * T)
        plateau = ag * S * eta * 2.5
        extra = {"eta": eta}
    else:
        q, beta = p["q"], p["beta"]

        def f(T):
            if T <= TB:
                return ag * S * (2.0 / 3.0 + T / TB * (2.5 / q - 2.0 / 3.0))
            if T <= TC:
                return ag * S * 2.5 / q
            if T <= TD:
                return max(ag * S * 2.5 / q * TC / T, beta * ag)
            return max(ag * S * 2.5 / q * TC * TD / (T * T), beta * ag)
        plateau = ag * S * 2.5 / q
        extra = {"q": q, "beta": beta}
    corners = [0.0, TB, TC, TD]
    if p["kind"] == "design":            # where the beta*ag floor starts
        tf = 2.5 * S * TC / (p["q"] * p["beta"])
        if tf > TD:
            tf = math.sqrt(2.5 * S * TC * TD / (p["q"] * p["beta"]))
        corners.append(tf)
    return f, corners, {"ag": ag, "S": S, "TB": TB, "TC": TC, "TD": TD,
                        "plateau": plateau, **extra}


# --------------------------------------------------------------------------- #
# IS 1893 (Part 1):2016
# --------------------------------------------------------------------------- #
def is1893_damping_factor(damping: float) -> float:
    """Table 3 multiplying factor, linear between the tabulated dampings."""
    x = 100.0 * float(damping)
    tab = IS1893_DAMPING
    if x <= tab[0][0]:
        return tab[0][1]
    if x >= tab[-1][0]:
        return tab[-1][1]
    for (x0, y0), (x1, y1) in zip(tab, tab[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return tab[-1][1]  # pragma: no cover


def is1893_rs_sa_g(T: float, soil: str) -> float:
    """IS 1893:2016 §6.4.2 / Fig. 2b (response-spectrum method) Sa/g."""
    tc, num, tail = IS1893_SOIL_RS[soil]
    if T < 0.10:
        return 1.0 + 15.0 * T
    if T <= tc:
        return 2.5
    if T <= 4.0:
        return num / T
    return tail


def _is1893(p: dict):
    Z = IS1893_ZONES[p["zone"]] if p.get("Z") is None else p["Z"]
    df = is1893_damping_factor(p["damping"])
    soil = p["soil"]
    sc = 1.0 if p["output"] == "sa_g" else Z / 2.0 * p["I"] / p["R"]

    def f(T):
        return sc * df * is1893_rs_sa_g(T, soil)
    tc = IS1893_SOIL_RS[soil][0]
    return f, (0.0, 0.1, tc, 4.0), {"Z": Z, "damping_factor": df,
                                    "Tc": tc, "scale": sc,
                                    "plateau": sc * df * 2.5}


# --------------------------------------------------------------------------- #
# NBCC 2020
# --------------------------------------------------------------------------- #
def nbcc2020_value(T: float, ords: Sequence[Tuple[float, float]]) -> float:
    """4.1.8.4(7): S(T) = max(Sa(0.2), Sa(0.5)) for T <= 0.2 s, the site
    ordinate at each tabulated period, LINEAR interpolation between, and
    the last ordinate (S(10)) beyond the last period."""
    s02 = max(ords[0][1], ords[1][1])
    pts = [(ords[0][0], s02)] + list(ords[1:])
    if T <= pts[0][0]:
        return s02
    if T >= pts[-1][0]:
        return pts[-1][1]
    for (t0, a), (t1, b) in zip(pts, pts[1:]):
        if t0 <= T <= t1:
            return a + (b - a) * (T - t0) / (t1 - t0)
    return pts[-1][1]  # pragma: no cover


def _nbcc(p: dict):
    ords = [(t, p[k]) for t, k in NBCC_ORDINATES if p.get(k) is not None]
    if len(ords) < 4 or [t for t, _ in ords] != [t for t, _ in
                                                 NBCC_ORDINATES[:len(ords)]]:
        raise ValueError("nbcc_2020: give Sa02, Sa05, Sa10, Sa20 (and "
                         "optionally Sa50, then Sa100)")
    sc = p["Mv"] * p["Ie"] / p["RdRo"]

    def f(T):
        return sc * nbcc2020_value(T, ords)
    return f, [t for t, _ in ords], {"S_plateau": max(ords[0][1],
                                                      ords[1][1]),
                                     "scale": sc}


# --------------------------------------------------------------------------- #
# NZS 1170.5:2004
# --------------------------------------------------------------------------- #
def nzs_ch(T: float, site: str) -> float:
    """Table 3.1 spectral shape factor Ch(T), modal / numerical form
    (Ch(0) values 1.0 / 1.33 / 1.12 / 1.12, linear to T = 0.1 s)."""
    if site in ("A", "B"):
        c0, pl, tpl, a, tr, n15, n3 = 1.0, 2.35, 0.3, 1.60, 0.5, 1.05, 3.15
    elif site == "C":
        c0, pl, tpl, a, tr, n15, n3 = 1.33, 2.93, 0.3, 2.0, 0.5, 1.32, 3.96
    elif site == "D":
        c0, pl, tpl, a, tr, n15, n3 = 1.12, 3.0, 0.56, 2.4, 0.75, 2.14, 6.42
    else:  # E
        c0, pl, tpl, a, tr, n15, n3 = 1.12, 3.0, 1.0, None, None, 3.32, 9.96
    if T < 0.1:
        return c0 + (pl - c0) * T / 0.1
    if T <= tpl:
        return pl
    if T <= 1.5:
        return 3.0 / T ** 0.75 if a is None else a * (tr / T) ** 0.75
    if T <= 3.0:
        return n15 / T
    return n3 / (T * T)


def nzs_sp(mu: float) -> float:
    """§4.4.2 structural performance factor Sp from the ductility mu."""
    if mu <= 1.0:
        return 1.0
    if mu < 2.0:
        return 1.3 - 0.3 * mu
    return 0.7


def nzs_kmu(mu: float, T1: float, site: str) -> float:
    """§5.2.1.1 inelastic spectrum scaling factor k_mu."""
    if site == "E":
        if T1 >= 1.0 or mu < 1.5:
            return mu
        return (mu - 1.5) * T1 + 1.5
    if T1 >= 0.7:
        return mu
    return (mu - 1.0) * T1 / 0.7 + 1.0


def _nzs(p: dict):
    site = p["site_class"]
    ZRN = p["Z"] * p["R"] * p["N"]
    if p["Z"] * p["R"] > 0.7 + 1e-12:
        raise ValueError("nzs1170_5: Z*R must not exceed 0.7 (§3.1.6)")
    summ: Dict[str, Any] = {"ZRN": ZRN}
    if p["kind"] == "design":
        mu = p["mu"]
        sp = nzs_sp(mu) if p.get("Sp") is None else p["Sp"]
        kmu = nzs_kmu(mu, 10.0 if p.get("T1") is None else p["T1"], site)
        sc = sp / kmu
        floor = max((p["Z"] / 20.0 + 0.02) * p["R"], 0.03 * p["R"])
        summ.update(Sp=sp, k_mu=kmu, floor=floor)
    else:
        sc, floor = 1.0, 0.0

    def f(T):
        return max(ZRN * nzs_ch(T, site) * sc, floor)
    tpl = {"A": 0.3, "B": 0.3, "C": 0.3, "D": 0.56, "E": 1.0}[site]
    summ["plateau"] = max(ZRN * nzs_ch(0.1, site) * sc, floor)
    return f, (0.0, 0.1, tpl, 1.5, 3.0), summ


# --------------------------------------------------------------------------- #
# AS 1170.4-2007
# --------------------------------------------------------------------------- #
def as_ch(T: float, site: str) -> float:
    """Table 6.4 spectral shape factor Ch(T) (modal form, Ch(0) included)."""
    c0, slope, cap, kmid, klong = AS_SITES[site]
    if T <= 0.1:
        return c0 + slope * T
    if T <= 1.5:
        return min(kmid / T, cap)
    return klong / (T * T)


def _as(p: dict):
    site = p["site_class"]
    kpz = p["kp"] * p["Z"]
    kpz_used = max(kpz, 0.08)
    sc = 1.0 if p["kind"] == "elastic" else p["Sp"] / p["mu"]

    def f(T):
        return kpz_used * as_ch(T, site) * sc
    c0, slope, cap, kmid, _ = AS_SITES[site]
    return f, (0.0, 0.1, kmid / cap, 1.5), {
        "kpZ": kpz, "kpZ_used": kpz_used, "scale": sc,
        "plateau": kpz_used * cap * sc}


# --------------------------------------------------------------------------- #
# GB 50011-2010
# --------------------------------------------------------------------------- #
def gb_coefficients(damping: float) -> Tuple[float, float, float]:
    """(gamma, eta1, eta2) — Eqs. 5.1.5-1 .. 5.1.5-3."""
    z = float(damping)
    gamma = 0.9 + (0.05 - z) / (0.3 + 6.0 * z)
    eta1 = max(0.02 + (0.05 - z) / (4.0 + 32.0 * z), 0.0)
    eta2 = max(1.0 + (0.05 - z) / (0.08 + 1.6 * z), 0.55)
    return gamma, eta1, eta2


def _gb(p: dict):
    amax = (GB_ALPHA_MAX[p["intensity"]][p["level"]]
            if p.get("alpha_max") is None else p["alpha_max"])
    Tg = GB_TG[p["group"]][p["site"]]
    if p["level"] == "rare" and p["intensity"] in ("8", "8.5", "9"):
        Tg += 0.05                       # §5.1.4-2: +0.05 s for rare, 8 & 9
    if p.get("Tg") is not None:
        Tg = p["Tg"]
    gamma, eta1, eta2 = gb_coefficients(p["damping"])

    def f(T):
        if T < 0.1:
            return amax * (0.45 + (eta2 - 0.45) * T / 0.1)
        if T <= Tg:
            return eta2 * amax
        if T <= 5.0 * Tg:
            return (Tg / T) ** gamma * eta2 * amax
        return max(eta2 * 0.2 ** gamma - eta1 * (T - 5.0 * Tg), 0.0) * amax
    return f, (0.0, 0.1, Tg, 5.0 * Tg, 6.0), {
        "alpha_max": amax, "Tg": Tg, "gamma": gamma, "eta1": eta1,
        "eta2": eta2, "plateau": eta2 * amax}


# --------------------------------------------------------------------------- #
# catalogue
# --------------------------------------------------------------------------- #
_G = {"quantity": "g"}
_S = {"quantity": "period"}
_R = {"quantity": "ratio"}
_TMAX = _p("T_max", "Last period", 10.0, gt=0.0, max=50.0, advanced=True,
           **_S)

CODES: Dict[str, dict] = {
    "asce7_16": {
        "label": "ASCE 7-16", "clause": "§11.4.6 design spectrum",
        "fn": _asce7_16,
        "params": [
            _p("Ss", "Ss (0.2 s MCE_R)", 1.5, min=0.0, **_G),
            _p("S1", "S1 (1 s MCE_R)", 0.6, min=0.0, **_G),
            _ch("site_class", "Site class", "D",
                [(s, "Site class " + s) for s in "ABCDE"]),
            _p("TL", "Long-period transition TL", 8.0, gt=0.0, **_S),
            _p("R", "Response modification R (1 = elastic)", 1.0, gt=0.0,
               advanced=True, **_R),
            _p("Ie", "Importance factor Ie", 1.0, gt=0.0, advanced=True,
               **_R),
            _TMAX]},
    "asce7_22_mprs": {
        "label": "ASCE 7-22 multi-period (MPRS)",
        "clause": "§11.4.5.1 multi-period design spectrum (USGS data)",
        "fn": _asce7_22_mprs,
        "params": [
            _p("ordinates", "USGS site-class ordinates (22 periods)", None,
               "table", periods=list(MPRS_PERIODS),
               hint="Sa (g) at the 22 USGS periods for the site class"),
            _ch("basis", "Ordinates are", "mcer",
                [("mcer", "MCE_R (design = 2/3 x)"),
                 ("design", "Design (Sa)")]),
            _p("TL", "Long-period transition TL", 8.0, gt=0.0, **_S),
            _p("vs30", "vs30 for SD1 (optional)", None, nullable=True,
               gt=0.0, advanced=True, quantity="velocity"),
            _p("R", "Response modification R (1 = elastic)", 1.0, gt=0.0,
               advanced=True, **_R),
            _p("Ie", "Importance factor Ie", 1.0, gt=0.0, advanced=True,
               **_R),
            _TMAX]},
    "asce7_22_two_period": {
        "label": "ASCE 7-22 two-period",
        "clause": "§11.4.5.2 two-period design spectrum (fallback)",
        "fn": _asce7_22_two,
        "params": [
            _p("SDS", "SDS", 1.0, gt=0.0, **_G),
            _p("SD1", "SD1", 0.6, min=0.0, **_G),
            _p("TL", "Long-period transition TL", 8.0, gt=0.0, **_S),
            _p("R", "Response modification R (1 = elastic)", 1.0, gt=0.0,
               advanced=True, **_R),
            _p("Ie", "Importance factor Ie", 1.0, gt=0.0, advanced=True,
               **_R),
            _TMAX]},
    "ec8": {
        "label": "Eurocode 8 (EN 1998-1)",
        "clause": "§3.2.2.2 elastic / §3.2.2.5 design spectrum",
        "fn": _ec8,
        "params": [
            _ch("spectrum_type", "Spectrum type", 1,
                [(1, "Type 1 (Ms > 5.5)"), (2, "Type 2 (Ms <= 5.5)")]),
            _ch("ground_type", "Ground type", "B",
                [(s, "Ground type " + s) for s in "ABCDE"]),
            _p("agR", "Reference PGA agR", 0.25, gt=0.0, **_G),
            _p("gamma_I", "Importance factor gamma_I", 1.0, gt=0.0, **_R),
            _ch("kind", "Spectrum", "elastic",
                [("elastic", "Elastic Se(T)"), ("design", "Design Sd(T)")]),
            _p("q", "Behaviour factor q", 1.5, gt=0.0, **_R),
            _p("beta", "Lower bound factor beta", 0.2, min=0.0,
               advanced=True, **_R),
            _p("damping", "Damping ratio (elastic)", 0.05, gt=0.0, lt=1.0,
               **_R),
            _p("S", "Soil factor S (NA override)", None, nullable=True,
               gt=0.0, advanced=True, **_R),
            _p("TB", "TB (NA override)", None, nullable=True, gt=0.0,
               advanced=True, **_S),
            _p("TC", "TC (NA override)", None, nullable=True, gt=0.0,
               advanced=True, **_S),
            _p("TD", "TD (NA override)", None, nullable=True, gt=0.0,
               advanced=True, **_S),
            _p("T_max", "Last period", 4.0, gt=0.0, max=50.0,
               advanced=True, **_S)]},
    "is1893_2016": {
        "label": "IS 1893 (Part 1):2016",
        "clause": "§6.4.2 response spectrum method (Fig. 2b)",
        "fn": _is1893,
        "params": [
            _ch("zone", "Seismic zone", "IV",
                [(z, f"Zone {z} (Z = {v:g})")
                 for z, v in IS1893_ZONES.items()]),
            _p("Z", "Zone factor Z override", None, nullable=True, gt=0.0,
               advanced=True, **_R),
            _ch("soil", "Soil type", "II",
                [("I", "I rock / hard"), ("II", "II medium"),
                 ("III", "III soft")]),
            _p("I", "Importance factor I", 1.0, gt=0.0, **_R),
            _p("R", "Response reduction R", 1.0, gt=0.0, **_R),
            _p("damping", "Damping ratio", 0.05, min=0.0, lt=1.0, **_R),
            _ch("output", "Function value", "ah",
                [("ah", "Ah = (Z/2)(I/R)(Sa/g)"), ("sa_g", "Sa/g")]),
            _p("T_max", "Last period", 6.0, gt=0.0, max=50.0,
               advanced=True, **_S)]},
    "nbcc_2020": {
        "label": "NBCC 2020",
        "clause": "4.1.8.4(7) design spectral acceleration S(T)",
        "fn": _nbcc,
        "params": [
            _p("Sa02", "Sa(0.2)", 0.848, min=0.0, **_G),
            _p("Sa05", "Sa(0.5)", 0.751, min=0.0, **_G),
            _p("Sa10", "Sa(1.0)", 0.425, min=0.0, **_G),
            _p("Sa20", "Sa(2.0)", 0.257, min=0.0, **_G),
            _p("Sa50", "Sa(5.0)", 0.080, min=0.0, nullable=True, **_G),
            _p("Sa100", "Sa(10.0)", 0.029, min=0.0, nullable=True, **_G),
            _p("Mv", "Higher-mode factor Mv", 1.0, gt=0.0, advanced=True,
               **_R),
            _p("Ie", "Importance factor IE", 1.0, gt=0.0, advanced=True,
               **_R),
            _p("RdRo", "Rd x Ro (1 = elastic)", 1.0, gt=0.0,
               advanced=True, **_R),
            _TMAX]},
    "nzs1170_5": {
        "label": "NZS 1170.5:2004",
        "clause": "§3.1 elastic C(T) / §5.2.1.1 design Cd(T)",
        "fn": _nzs,
        "params": [
            _ch("site_class", "Site subsoil class", "C",
                [(s, "Class " + s) for s in NZS_SITES]),
            _p("Z", "Hazard factor Z", 0.4, gt=0.0, **_R),
            _p("R", "Return period factor R", 1.0, gt=0.0, **_R),
            _p("N", "Near-fault factor N(T,D)", 1.0, min=1.0, **_R),
            _ch("kind", "Spectrum", "elastic",
                [("elastic", "Elastic C(T)"), ("design", "Design Cd(T)")]),
            _p("mu", "Ductility mu", 1.0, min=1.0, **_R),
            _p("Sp", "Sp override", None, nullable=True, gt=0.0,
               advanced=True, **_R),
            _p("T1", "T1 for k_mu (blank = long period)", None,
               nullable=True, gt=0.0, advanced=True, **_S),
            _TMAX]},
    "as1170_4": {
        "label": "AS 1170.4-2007",
        "clause": "§6.4 Ch(T), §7.2 design kp Z Ch(T) Sp/mu",
        "fn": _as,
        "params": [
            _ch("site_class", "Sub-soil class", "Ce",
                [(s, "Class " + s) for s in AS_SITES]),
            _p("kp", "Probability factor kp", 1.0, gt=0.0, **_R),
            _p("Z", "Hazard factor Z", 0.08, gt=0.0, **_R),
            _ch("kind", "Spectrum", "elastic",
                [("elastic", "Elastic kp Z Ch(T)"),
                 ("design", "Design kp Z Ch(T) Sp/mu")]),
            _p("Sp", "Structural performance factor Sp", 0.77, gt=0.0,
               **_R),
            _p("mu", "Ductility mu", 2.0, min=1.0, **_R),
            _TMAX]},
    "gb50011": {
        "label": "GB 50011-2010",
        "clause": "§5.1.4-5.1.5 seismic influence coefficient",
        "fn": _gb,
        "params": [
            _ch("intensity", "Fortification intensity", "8",
                [("6", "6 (0.05g)"), ("7", "7 (0.10g)"),
                 ("7.5", "7 (0.15g)"), ("8", "8 (0.20g)"),
                 ("8.5", "8 (0.30g)"), ("9", "9 (0.40g)")]),
            _ch("level", "Earthquake level", "frequent",
                [("frequent", "Frequent (多遇)"),
                 ("fortification", "Fortification (设防)"),
                 ("rare", "Rare (罕遇)")]),
            _ch("group", "Design group", "1",
                [("1", "Group 1"), ("2", "Group 2"), ("3", "Group 3")]),
            _ch("site", "Site class", "II",
                [(s, "Site " + s) for s in ("I0", "I1", "II", "III",
                                             "IV")]),
            _p("damping", "Damping ratio", 0.05, gt=0.0, lt=1.0, **_R),
            _p("alpha_max", "alpha_max override", None, nullable=True,
               gt=0.0, advanced=True, **_G),
            _p("Tg", "Tg override", None, nullable=True, gt=0.0,
               advanced=True, **_S),
            _p("T_max", "Last period", 6.0, gt=0.0, max=50.0,
               advanced=True, **_S)]},
}


def catalogue() -> List[dict]:
    """``[{code, label, clause, params: [schema]}]`` (JSON-safe)."""
    return [{"code": c, "label": d["label"], "clause": d["clause"],
             "params": [dict(p) for p in d["params"]]}
            for c, d in CODES.items()]


def normalize_params(code: str, params: Optional[dict]) -> dict:
    """Validated params with every default filled; ValueError on unknown
    code, unknown key or a bad value."""
    if code not in CODES:
        raise ValueError(f"unknown spectrum code {code!r}; one of "
                         f"{tuple(CODES)}")
    params = params or {}
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    specs = {s["key"]: s for s in CODES[code]["params"]}
    bad = [k for k in params if k not in specs]
    if bad:
        raise ValueError(f"{code}: unknown parameter(s) {bad}")
    out: Dict[str, Any] = {}
    for k, s in specs.items():
        v = params.get(k, s["default"])
        if s["type"] == "choice":
            allowed = [c for c, _ in s["choices"]]
            if v not in allowed:
                # tolerate "1"/1 for integer choices
                m = [c for c in allowed if str(c) == str(v)]
                if not m:
                    raise ValueError(f"{code}: {k} must be one of {allowed}")
                v = m[0]
            out[k] = v
        elif s["type"] == "table":
            if v is None:
                raise ValueError(f"{code}: {k} is required")
            _mprs_points(code, v)
            out[k] = [list(map(float, q)) if isinstance(q, (list, tuple))
                      else float(q) for q in v]
        else:
            out[k] = _check_number(code, s, v)
    return out


def _build(code: str, params: Optional[dict]):
    p = normalize_params(code, params)
    fn, corners, summary = CODES[code]["fn"](p)
    return p, fn, corners, summary


def spectrum_value(code: str, params: Optional[dict], T: float) -> float:
    """Sa(T) [g] of a code spectrum (closed form, no sampling)."""
    if not (_finite(T) and T >= 0.0):
        raise ValueError("period T must be a finite value >= 0")
    _, fn, _, _ = _build(code, params)
    return float(fn(float(T)))


def spectrum(code: str, params: Optional[dict] = None) -> dict:
    """``{code, label, clause, params, points: [[T, Sa]], summary}``."""
    p, fn, corners, summary = _build(code, params)
    pts = _sample(fn, corners, p["T_max"])
    return {"code": code, "label": CODES[code]["label"],
            "clause": CODES[code]["clause"], "params": p, "points": pts,
            "summary": {k: (float(v) if _finite(v) else v)
                        for k, v in summary.items()}}


def make_spectrum_function(model, name: str, code: str,
                           params: Optional[dict] = None,
                           damping: Optional[float] = None):
    """Add (or replace) ``model.spectrum_functions[name]`` from a code
    spectrum; ``source = {"code", "params"}`` records the generator.
    ``damping`` defaults to the code's own damping parameter (else 0.05)."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("'name' is required")
    sp = spectrum(code, params)
    if damping is None:
        damping = sp["params"].get("damping", 0.05) or 0.05
    fn = model.add_spectrum_function(name.strip(), sp["points"],
                                     damping=float(damping))
    fn.source = {"code": code, "params": sp["params"]}
    return fn


def validate_source(fn) -> None:
    """Model hook: a SpectrumFunction ``source`` must name a known code with
    valid parameters (None = user-defined points)."""
    src = getattr(fn, "source", None)
    if src is None:
        return
    if not isinstance(src, dict) or "code" not in src:
        raise ValueError(f"Spectrum function {fn.name}: source must be "
                         "{code, params}")
    try:
        normalize_params(src["code"], src.get("params") or {})
    except ValueError as exc:
        raise ValueError(f"Spectrum function {fn.name}: {exc}") from None


# Wave C hook: TSC 2018 / KDS 41 / NSCP 2015 / NTC CDMX / NTC 2018 / EC8+NA
# spectra (skyframe.core.spectra_codes_wavec registers into CODES)
from . import spectra_codes_wavec as _scw  # noqa: E402,F401
