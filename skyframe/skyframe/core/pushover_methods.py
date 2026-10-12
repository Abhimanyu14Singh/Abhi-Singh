"""Pushover performance methods — ADRS conversion, ATC-40 capacity
spectrum (procedures A and B), FEMA 440 equivalent linearization with the
MADRS, and the EN 1998-1 Annex B (N2) target displacement.

Everything here is a PURE function of the capacity curve, the modal
properties of the push direction and the 5 %-damped demand spectrum, so
each step is pinned by a hand calculation in
``tests/test_pushover_methods.py``.  The engine glue (modal properties,
monitored joint, attaching the block to the pushover results) lives in
:mod:`skyframe.engine.pushover_performance`; see CONTRACT "Pushover
performance methods (ATC-40, FEMA 440 EL, EN 1998 N2) and the ADRS plot".

Units: SI (kN, m, t, s); spectral accelerations in g, ``g = 9.81``.

ADRS conversion (ATC-40 Eq. 8-1 / 8-2)
--------------------------------------
    Sa = V / (alpha1 W)          Sd = d_roof / (PF1 phi_roof)

``alpha1`` = effective modal mass ratio of the push-direction mode,
``PF1 phi_roof`` = participation factor times the mode-shape value at the
monitored joint (normalisation-invariant product, ``pf_phi``).

Demand spectra
--------------
``{"code": "asce7", "SDS", "SD1", "TL"?}`` (the ASCE 41 two-parameter
spectrum of :func:`skyframe.design.performance.design_sa`, TL branch
``SD1 TL / T^2`` only when ``TL`` is given) or ``{"code": "ec8", "ag",
"ground_type"? (A..E), "spectrum_type"? (1|2), "S"?, "TB"?, "TC"?,
"TD"?}`` (EN 1998-1 §3.2.2.2 elastic spectrum, eta = 1; S/TB/TC/TD
default to Table 3.2 (type 1) / 3.3 (type 2) for the ground type).  The
corner period ``TC`` (ASCE: ``Ts = SD1/SDS``) splits the acceleration and
velocity regions.

Bilinear representation (ATC-40 §8.2.2.1, FEMA 440 §6.2)
--------------------------------------------------------
Initial slope ``K`` = the secant to the first capacity point; the
post-yield branch runs from ``(dy, ay = K dy)`` to the trial point
``(dpi, api)`` on the curve; ``dy`` from EQUAL AREA up to ``dpi``.  The
``dy^2`` terms cancel, so ``dy = (2 A - dpi api) / (K dpi - api)``
(exact for a bilinear curve).  ``alpha = ((api - ay)/(dpi - dy)) / K``,
``mu = dpi / dy``, ``T0 = 2 pi / sqrt(K g)``.

ATC-40 (Procedure A iterative, Procedure B locus)
-------------------------------------------------
    beta0   = 63.7 (ay dpi - dy api) / (api dpi)          [%]  (Eq. 8-6)
    beta_eff = kappa beta0 + 5                                  (Eq. 8-8)
    SRA = (3.21 - 0.68 ln beta_eff) / 2.12 >= SRA_min           (Eq. 8-9)
    SRV = (2.31 - 0.41 ln beta_eff) / 1.65 >= SRV_min           (Eq. 8-10)

kappa (Table 8-1): type A 1.0 for beta0 <= 16.25 else
``1.13 - 0.51 x``; type B 0.67 for beta0 <= 25 else ``0.845 - 0.446 x``;
type C 0.33; ``x = (ay dpi - dy api)/(api dpi)``.  Minimum SRA/SRV
(Table 8-2): A 0.33/0.50, B 0.44/0.56, C 0.56/0.67.  The reduced spectrum
is ``SRA Sa(T)`` up to TC and ``min(SRA Sa(TC), SRV Sa(T))`` beyond (the
ATC-40 2.5CA / CV/T construction; the long-period TL/TD tail is scaled by
SRV).

* Procedure A: start at the 5 % demand / capacity intersection, rebuild
  the bilinear at each trial ``dpi``, intersect the reduced demand with
  the capacity spectrum (``di``) and repeat until ``|di - dpi| <= tol
  dpi``; a non-converging (oscillating) sequence falls back to bisection
  on ``di(dpi) - dpi`` (``solver: "bisection"``).
* Procedure B: ONE bilinear (built at the end of the capacity curve);
  the locus of ``(dpi, Sa_red(dpi))`` — the reduced spectrum for
  ``beta_eff(dpi)`` read at ``Sd = dpi`` — is intersected with that
  bilinear.

FEMA 440 equivalent linearization (§6.2.1, Eqs. 6-5 to 6-10 — the
coefficients "for any capacity curve"), MADRS (§6.3)
-----------------------------------------------------------------
with ``m = mu - 1`` and beta0 = 5 %::

    mu < 4:          beta_eff = 4.9 m^2 - 1.1 m^3 + beta0
                     Teff = (0.20 m^2 - 0.038 m^3 + 1) T0
    4 <= mu <= 6.5:  beta_eff = 14.0 + 0.32 m + beta0
                     Teff = (0.28 + 0.13 m + 1) T0
    mu > 6.5:        beta_eff = 19 [(0.64 m - 1)/(0.64 m)^2] (Teff/T0)^2
                                + beta0
                     Teff = {0.89 [sqrt(m / (1 + 0.05 (mu - 2))) - 1] + 1} T0

    B = 4 / (5.6 - ln beta_eff)               (Eq. 6-11, beta_eff in %)
    M = (Teff/Tsec)^2 = (Teff/T0)^2 (1 + alpha (mu - 1)) / mu   (Eq. 6-12)

The reduced demand is ``Sa(T)/B``; the MADRS multiplies its ACCELERATION
ordinates by M (displacements unchanged).  The performance point is the
MADRS / capacity intersection, found by the same iteration as ATC-40
procedure A (with the bisection fallback).

EN 1998-1 Annex B (N2)
----------------------
With ``Gamma = pf_phi`` (Phi normalised to 1 at the monitored joint) and
``m* = alpha1 M / Gamma`` (= sum m_i Phi_i): ``F* = V / Gamma``,
``d* = d / Gamma`` (B.2, B.3).  Elastic-perfectly-plastic idealisation:
``F*y`` = peak F*, ``d*m`` = d* at the END of the peak plateau (the
plastic mechanism; the last point when the curve still hardens), ``E*m`` = area under the F*-d*
curve up to ``d*m``, ``d*y = 2 (d*m - E*m / F*y)`` (B.6), ``T* = 2 pi
sqrt(m* d*y / F*y)`` (B.7), ``d*et = Se(T*) (T*/2 pi)^2`` (B.8).
T* >= TC: ``d*t = d*et`` (equal displacement, B.9).  T* < TC: if
``F*y/m* >= Se(T*)`` the response is elastic (``d*t = d*et``, B.10);
otherwise ``qu = Se(T*) m* / F*y`` and ``d*t = d*et/qu (1 + (qu - 1)
TC/T*) >= d*et`` (B.11).  Roof target ``dt = Gamma d*t`` (B.12).  The
optional B.6 re-idealisation iteration is not applied.
"""

from __future__ import annotations

import copy
import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

G = 9.81
METHODS = ("asce41", "atc40", "fema440", "n2")
METHOD_LABELS = {"asce41": "ASCE 41-17 coefficient method",
                 "atc40": "ATC-40 capacity spectrum",
                 "fema440": "FEMA 440 equivalent linearization",
                 "n2": "EN 1998-1 Annex B (N2)"}
BEHAVIOR_TYPES = ("A", "B", "C")
ATC40_PROCEDURES = ("A", "B")
SPECTRUM_CODES = ("asce7", "ec8")
SITE_CLASSES = ("A", "B", "C", "D", "E", "F")
EC8_GROUND = ("A", "B", "C", "D", "E")
#: ATC-40 Table 8-2 minimum (SRA, SRV) by structural behaviour type
SR_MIN = {"A": (0.33, 0.50), "B": (0.44, 0.56), "C": (0.56, 0.67)}
#: EN 1998-1 Table 3.2 (type 1) / 3.3 (type 2): ground -> (S, TB, TC, TD)
EC8_TABLE = {
    1: {"A": (1.0, 0.15, 0.4, 2.0), "B": (1.2, 0.15, 0.5, 2.0),
        "C": (1.15, 0.20, 0.6, 2.0), "D": (1.35, 0.20, 0.8, 2.0),
        "E": (1.4, 0.15, 0.5, 2.0)},
    2: {"A": (1.0, 0.05, 0.25, 1.2), "B": (1.35, 0.05, 0.25, 1.2),
        "C": (1.5, 0.10, 0.25, 1.2), "D": (1.8, 0.10, 0.30, 1.2),
        "E": (1.6, 0.05, 0.25, 1.2)},
}
#: canonical defaults of PushoverCase.performance (a missing key = this)
PERF_DEFAULTS = {
    "methods": list(METHODS),
    "spectrum": {"code": "asce7", "SDS": 1.0, "SD1": 0.6},
    "site_class": "D",
    "behavior": "A",
    "atc40_procedure": "A",
}
PERF_KEYS = tuple(PERF_DEFAULTS)
_ASCE_KEYS = ("code", "SDS", "SD1", "TL")
_EC8_KEYS = ("code", "ag", "ground_type", "spectrum_type", "S", "TB", "TC",
             "TD")
_TOL = 1e-6
_MAX_IT = 60


def _num(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


# ======================================================================
# options
# ======================================================================
def normalize_options(perf: Optional[dict]) -> dict:
    """Validated options with every default filled (raises ValueError)."""
    if perf is None:
        perf = {}
    if not isinstance(perf, dict):
        raise ValueError("performance must be an object")
    bad = set(perf) - set(PERF_KEYS)
    if bad:
        raise ValueError(f"performance: unknown key(s) {sorted(bad)} "
                         f"(allowed: {list(PERF_KEYS)})")
    out = copy.deepcopy(PERF_DEFAULTS)
    out.update(copy.deepcopy(perf))
    meths = out["methods"]
    if (not isinstance(meths, list) or not meths
            or any(m not in METHODS for m in meths)
            or len(set(meths)) != len(meths)):
        raise ValueError(f"performance.methods must be a non-empty list of "
                         f"distinct names from {list(METHODS)}")
    out["spectrum"] = normalize_spectrum(out["spectrum"])
    if out["site_class"] not in SITE_CLASSES:
        raise ValueError(f"performance.site_class must be one of "
                         f"{list(SITE_CLASSES)}")
    if out["behavior"] not in BEHAVIOR_TYPES:
        raise ValueError("performance.behavior (ATC-40 structural behaviour "
                         "type) must be 'A', 'B' or 'C'")
    if out["atc40_procedure"] not in ATC40_PROCEDURES:
        raise ValueError("performance.atc40_procedure must be 'A' or 'B'")
    return out


def validate_case(po) -> None:
    """``BuildingModel._validate_pushover_case`` hook: the stored options
    are kept verbatim (byte-identical round trip) but must normalise."""
    perf = getattr(po, "performance", None)
    if perf is None:
        return
    try:
        normalize_options(perf)
    except ValueError as exc:
        raise ValueError(f"Pushover case {po.name}: {exc}") from None


def normalize_spectrum(sp) -> dict:
    """Validated demand-spectrum dict (EC8 S/TB/TC/TD filled in)."""
    if not isinstance(sp, dict):
        raise ValueError("performance.spectrum must be an object")
    code = sp.get("code", "asce7")
    if code not in SPECTRUM_CODES:
        raise ValueError(f"performance.spectrum.code must be one of "
                         f"{list(SPECTRUM_CODES)}")
    keys = _ASCE_KEYS if code == "asce7" else _EC8_KEYS
    bad = set(sp) - set(keys)
    if bad:
        raise ValueError(f"performance.spectrum ({code}): unknown key(s) "
                         f"{sorted(bad)}")
    out = dict(sp)
    out["code"] = code
    if code == "asce7":
        for k in ("SDS", "SD1"):
            if not (_num(out.get(k)) and out[k] > 0.0):
                raise ValueError(f"performance.spectrum.{k} must be a "
                                 "number > 0 (g)")
        if out.get("TL") is not None and not (_num(out["TL"])
                                              and out["TL"] > 0.0):
            raise ValueError("performance.spectrum.TL must be > 0 (s)")
        return out
    if not (_num(out.get("ag")) and out["ag"] > 0.0):
        raise ValueError("performance.spectrum.ag must be a number > 0 (g)")
    st = out.get("spectrum_type", 1)
    if st not in (1, 2):
        raise ValueError("performance.spectrum.spectrum_type must be 1 or 2")
    gt = out.get("ground_type", "C")
    if gt not in EC8_GROUND:
        raise ValueError(f"performance.spectrum.ground_type must be one of "
                         f"{list(EC8_GROUND)}")
    tab = EC8_TABLE[st][gt]
    for i, k in enumerate(("S", "TB", "TC", "TD")):
        v = out.get(k)
        if v is None:
            continue
        if not (_num(v) and v > 0.0):
            raise ValueError(f"performance.spectrum.{k} must be > 0")
    full = {"S": tab[0], "TB": tab[1], "TC": tab[2], "TD": tab[3]}
    for k in full:
        if out.get(k) is not None:
            full[k] = float(out[k])
    if not full["TB"] < full["TC"] < full["TD"]:
        raise ValueError("performance.spectrum: need TB < TC < TD")
    return out


class Spectrum:
    """5 %-damped elastic demand spectrum, Sa(T) in g."""

    def __init__(self, sp: dict):
        sp = normalize_spectrum(sp)
        self.spec = sp
        self.code = sp["code"]
        if self.code == "asce7":
            self.SDS, self.SD1 = float(sp["SDS"]), float(sp["SD1"])
            self.TL = None if sp.get("TL") is None else float(sp["TL"])
            self.TC = self.SD1 / self.SDS
            self.T0 = 0.2 * self.TC
            self.TD = self.TL
        else:
            st = sp.get("spectrum_type", 1)
            gt = sp.get("ground_type", "C")
            S, TB, TC, TD = EC8_TABLE[st][gt]
            self.ag = float(sp["ag"])
            self.S = float(sp["S"]) if sp.get("S") is not None else S
            self.TB = float(sp["TB"]) if sp.get("TB") is not None else TB
            self.TC = float(sp["TC"]) if sp.get("TC") is not None else TC
            self.TD = float(sp["TD"]) if sp.get("TD") is not None else TD
            self.T0 = self.TB

    def sa(self, T: float) -> float:
        T = max(float(T), 0.0)
        if self.code == "asce7":
            if T < self.T0:
                return self.SDS * (0.4 + 0.6 * T / self.T0)
            if T <= self.TC:
                return self.SDS
            if self.TL is not None and T > self.TL:
                return self.SD1 * self.TL / (T * T)
            return self.SD1 / T
        a = self.ag * self.S
        if T <= self.TB:
            return a * (1.0 + T / self.TB * (2.5 - 1.0))
        if T <= self.TC:
            return a * 2.5
        if T <= self.TD:
            return a * 2.5 * self.TC / T
        return a * 2.5 * self.TC * self.TD / (T * T)

    def describe(self) -> dict:
        if self.code == "asce7":
            return {"code": "asce7", "SDS": self.SDS, "SD1": self.SD1,
                    "TL": self.TL, "Ts": self.TC, "T0": self.T0}
        return {"code": "ec8", "ag": self.ag, "S": self.S, "TB": self.TB,
                "TC": self.TC, "TD": self.TD,
                "ground_type": self.spec.get("ground_type", "C"),
                "spectrum_type": self.spec.get("spectrum_type", 1)}


def sd_of(sa_g: float, T: float) -> float:
    """Spectral displacement (m) of Sa (g) at period T."""
    return sa_g * G * T * T / (4.0 * math.pi ** 2)


T_MAX = 10.0


def _periods(n: int = 160) -> List[float]:
    """Plot grid 0 .. T_MAX s, dense at short periods."""
    out = [0.0]
    for i in range(1, n + 1):
        out.append(T_MAX * (i / n) ** 2)
    return out


class Demand:
    """A demand curve in ADRS, parameterised by the period T:
    ``Sd(T) = sa_d(T) g T^2/(4 pi^2)``, ``Sa(T) = mult * sa_d(T)``
    (``mult`` = the FEMA 440 MADRS factor M; 1 otherwise)."""

    def __init__(self, sa_fn: Callable[[float], float], mult: float = 1.0):
        self.sa_fn = sa_fn
        self.mult = float(mult)

    def point(self, T: float) -> Tuple[float, float]:
        s = self.sa_fn(T)
        return sd_of(s, T), self.mult * s

    def sa_at_sd(self, sd: float) -> Optional[float]:
        """Demand Sa (g) at spectral displacement sd; None when sd lies
        beyond the curve (past T_MAX / on the constant-displacement
        tail)."""
        if sd <= 0.0:
            return self.point(0.0)[1]
        lo, hi = 0.0, T_MAX
        if self.point(hi)[0] < sd:
            return None
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if self.point(mid)[0] < sd:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-12:
                break
        return self.point(0.5 * (lo + hi))[1]

    def curve(self) -> Dict[str, List[float]]:
        sd, sa = [], []
        for T in _periods():
            d, a = self.point(T)
            sd.append(d)
            sa.append(a)
        return {"Sd": sd, "Sa": sa}


# ======================================================================
# capacity curve in ADRS + bilinear
# ======================================================================
def adrs(disp: Sequence[float], shear: Sequence[float], *, W: float,
         alpha: float, pf_phi: float) -> Tuple[List[float], List[float]]:
    """Capacity curve -> capacity spectrum: Sa = V/(alpha W) (g),
    Sd = d/(PF1 phi_roof) (m)."""
    if not (W > 0.0 and alpha > 0.0 and pf_phi != 0.0):
        raise ValueError("adrs: W, alpha1 must be > 0 and PF1*phi_roof "
                         "non-zero")
    return ([float(d) / pf_phi for d in disp],
            [float(v) / (alpha * W) for v in shear])


class Capacity:
    """Piecewise-linear capacity spectrum from the origin (positive part,
    cut at the peak Sa)."""

    def __init__(self, sd: Sequence[float], sa: Sequence[float]):
        d = [0.0] + [float(x) for x in sd]
        a = [0.0] + [float(x) for x in sa]
        if len(d) < 3:
            raise ValueError("capacity curve: need at least 2 points")
        if d[1] <= 0.0 or a[1] <= 0.0:
            raise ValueError("capacity curve: the first point must be "
                             "positive (push in the + direction)")
        ip = max(range(len(a)), key=lambda i: a[i])
        # keep the curve to the LAST point (post-peak softening is
        # allowed); only drop non-monotonic displacement reversals
        keep_d, keep_a = [0.0], [0.0]
        for x, y in zip(d[1:], a[1:]):
            if x > keep_d[-1]:
                keep_d.append(x)
                keep_a.append(y)
        self.d, self.a = keep_d, keep_a
        self.K = self.a[1] / self.d[1]
        self.d_end = self.d[-1]
        self.i_peak = ip

    def sa_at(self, sd: float) -> Optional[float]:
        if sd < 0.0 or sd > self.d_end * (1.0 + 1e-12):
            return None
        for (d1, a1), (d2, a2) in zip(zip(self.d, self.a),
                                      zip(self.d[1:], self.a[1:])):
            if sd <= d2:
                return a1 + (a2 - a1) * (sd - d1) / (d2 - d1)
        return self.a[-1]

    def area(self, upto: float) -> float:
        A = 0.0
        for (d1, a1), (d2, a2) in zip(zip(self.d, self.a),
                                      zip(self.d[1:], self.a[1:])):
            if upto <= d1:
                break
            if upto < d2:
                a_up = a1 + (a2 - a1) * (upto - d1) / (d2 - d1)
                A += 0.5 * (a1 + a_up) * (upto - d1)
                break
            A += 0.5 * (a1 + a2) * (d2 - d1)
        return A

    def bilinear(self, dpi: float) -> dict:
        """ATC-40 / FEMA 440 equal-area bilinear up to the trial dpi."""
        dpi = min(max(dpi, self.d_end * 1e-12), self.d_end)
        api = self.sa_at(dpi)
        K = self.K
        A = self.area(dpi)
        den = K * dpi - api
        if den <= 1e-12 * K * dpi:
            dy = dpi                    # no softening: elastic
        else:
            dy = (2.0 * A - dpi * api) / den
            dy = min(max(dy, 0.0), dpi)
        if dy <= 0.0:
            raise ValueError("bilinear: no positive yield displacement "
                             "(degenerate capacity curve)")
        ay = K * dy
        alpha = ((api - ay) / (dpi - dy) / K) if dpi - dy > 1e-12 * dpi \
            else 0.0
        return {"K": K, "dy": dy, "ay": ay, "dpi": dpi, "api": api,
                "alpha": alpha, "mu": dpi / dy,
                "T0": 2.0 * math.pi / math.sqrt(K * G), "area": A}

    def intersect(self, dem: Demand) -> Optional[Tuple[float, float]]:
        """First crossing of the demand curve with the capacity spectrum
        (capacity rising through the demand); None when the demand stays
        above the capacity over the whole curve."""
        def f(sd):
            sd_dem = dem.sa_at_sd(sd)
            return self.sa_at(sd) - (sd_dem if sd_dem is not None
                                     else -1.0)
        pts = []
        for d1, d2 in zip(self.d[1:], self.d[2:]):
            pts.append(d1)
            for k in range(1, 8):
                pts.append(d1 + (d2 - d1) * k / 8.0)
        pts.append(self.d_end)
        pts = sorted(set([self.d[1] * k / 8.0 for k in range(1, 8)] + pts))
        prev_x, prev_f = 0.0, -1.0
        for x in pts:
            fx = f(x)
            if fx >= 0.0 and prev_f < 0.0:
                lo, hi = prev_x, x
                for _ in range(200):
                    mid = 0.5 * (lo + hi)
                    if f(mid) >= 0.0:
                        hi = mid
                    else:
                        lo = mid
                    if hi - lo <= 1e-13 * max(1.0, hi):
                        break
                sd = 0.5 * (lo + hi)
                return sd, self.sa_at(sd)
            prev_x, prev_f = x, fx
        return None

    def to_dict(self) -> dict:
        return {"Sd": list(self.d[1:]), "Sa": list(self.a[1:])}


# ======================================================================
# ATC-40
# ======================================================================
def atc40_kappa(behavior: str, beta0: float, x: float) -> float:
    """ATC-40 Table 8-1 damping modification factor kappa."""
    if behavior == "A":
        return 1.0 if beta0 <= 16.25 else 1.13 - 0.51 * x
    if behavior == "B":
        return 0.67 if beta0 <= 25.0 else 0.845 - 0.446 * x
    if behavior == "C":
        return 0.33
    raise ValueError(f"unknown ATC-40 behaviour type {behavior!r}")


def atc40_damping(bl: dict, behavior: str) -> dict:
    """beta0, kappa, beta_eff (%), SRA, SRV at a bilinear trial point."""
    ay, dy, api, dpi = bl["ay"], bl["dy"], bl["api"], bl["dpi"]
    x = max((ay * dpi - dy * api) / (api * dpi), 0.0)
    beta0 = 63.7 * x
    kappa = atc40_kappa(behavior, beta0, x)
    beta_eff = kappa * beta0 + 5.0
    sra_min, srv_min = SR_MIN[behavior]
    sra = max((3.21 - 0.68 * math.log(beta_eff)) / 2.12, sra_min)
    srv = max((2.31 - 0.41 * math.log(beta_eff)) / 1.65, srv_min)
    return {"beta0": beta0, "kappa": kappa, "beta_eff": beta_eff,
            "SRA": min(sra, 1.0), "SRV": min(srv, 1.0)}


def atc40_reduced(spec: Spectrum, sra: float, srv: float
                  ) -> Callable[[float], float]:
    plateau = spec.sa(spec.TC)

    def fn(T):
        if T <= spec.TC:
            return sra * spec.sa(T)
        return min(sra * plateau, srv * spec.sa(T))
    return fn


# ======================================================================
# FEMA 440 EL
# ======================================================================
def fema440_el(mu: float, alpha: float, T0: float,
               beta0: float = 5.0) -> dict:
    """FEMA 440 §6.2.1 effective period / damping ("any capacity curve"
    coefficients), B (Eq. 6-11) and the MADRS factor M (Eq. 6-12)."""
    mu = max(float(mu), 1.0)
    m = mu - 1.0
    if mu < 4.0:
        beta = 4.9 * m ** 2 - 1.1 * m ** 3 + beta0
        r = 0.20 * m ** 2 - 0.038 * m ** 3 + 1.0
    elif mu <= 6.5:
        beta = 14.0 + 0.32 * m + beta0
        r = 0.28 + 0.13 * m + 1.0
    else:
        r = 0.89 * (math.sqrt(m / (1.0 + 0.05 * (mu - 2.0))) - 1.0) + 1.0
        q = 0.64 * m
        beta = 19.0 * ((q - 1.0) / (q * q)) * r * r + beta0
    Teff = r * T0
    B = 4.0 / (5.6 - math.log(beta))
    sec2 = (1.0 + alpha * (mu - 1.0)) / mu      # (T0/Tsec)^2
    M = r * r * sec2
    Tsec = T0 / math.sqrt(sec2) if sec2 > 0.0 else float("inf")
    return {"mu": mu, "alpha": alpha, "T0": T0, "Teff": Teff, "Tsec": Tsec,
            "beta_eff": beta, "B": B, "M": M}


# ======================================================================
# iterations
# ======================================================================
def _solve(cap: Capacity, trial: Callable[[float], Tuple[Optional[float],
                                                        dict]],
           d0: float) -> dict:
    """Fixed-point iteration dpi <- di(dpi) (ATC-40 procedure A / FEMA 440
    MADRS), bisection fallback.  ``trial(dpi)`` -> (di or None, info)."""
    its: List[dict] = []
    dpi = min(max(d0, 1e-9 * cap.d_end), cap.d_end)
    for _ in range(_MAX_IT):
        di, info = trial(dpi)
        its.append(dict(info, dpi=dpi, di=di))
        if di is None:
            break
        if abs(di - dpi) <= _TOL * max(dpi, 1e-12):
            return {"converged": True, "solver": "iteration", "d": di,
                    "info": info, "iterations": its}
        dpi = min(max(di, 1e-9 * cap.d_end), cap.d_end)
    # bisection on h(d) = di(d) - d over the curve
    def h(d):
        di, info = trial(d)
        return (float("inf") if di is None else di - d), di, info
    xs = sorted(set([cap.d[1] * k / 8.0 for k in range(1, 8)]
                    + cap.d[1:] + [cap.d_end]))
    prev = None
    for x in xs:
        hx = h(x)
        if prev is not None and prev[1][0] > 0.0 >= hx[0]:
            lo, hi = prev[0], x
            for _ in range(200):
                mid = 0.5 * (lo + hi)
                hm = h(mid)
                if hm[0] > 0.0:
                    lo = mid
                else:
                    hi = mid
                if hi - lo <= 1e-12 * max(hi, 1e-12):
                    break
            d = 0.5 * (lo + hi)
            di, info = trial(d)
            its.append(dict(info, dpi=d, di=di))
            return {"converged": True, "solver": "bisection",
                    "d": d if di is None else di, "info": info,
                    "iterations": its}
        prev = (x, hx)
    return {"converged": False, "solver": "none", "d": None,
            "info": its[-1] if its else {}, "iterations": its}


def _start(cap: Capacity, spec: Spectrum) -> float:
    hit = cap.intersect(Demand(spec.sa))
    return hit[0] if hit else cap.d_end


def atc40(cap: Capacity, spec: Spectrum, behavior: str = "A",
          procedure: str = "A") -> dict:
    """ATC-40 capacity-spectrum performance point (module docstring)."""
    if procedure == "B":
        return _atc40_b(cap, spec, behavior)

    def trial(dpi):
        bl = cap.bilinear(dpi)
        dm = atc40_damping(bl, behavior)
        hit = cap.intersect(Demand(atc40_reduced(spec, dm["SRA"],
                                                 dm["SRV"])))
        info = dict(dm, bilinear=bl)
        return (hit[0] if hit else None), info
    res = _solve(cap, trial, _start(cap, spec))
    out = _atc_out(cap, spec, res, behavior, "A")
    return out


def _atc40_b(cap: Capacity, spec: Spectrum, behavior: str) -> dict:
    bl0 = cap.bilinear(cap.d_end)
    K, dy, ay = bl0["K"], bl0["dy"], bl0["ay"]
    slope = bl0["alpha"] * K

    def bil(d):
        return K * d if d <= dy else ay + slope * (d - dy)

    def locus(d):
        api = bil(d)
        bl = dict(bl0, dpi=d, api=api, mu=d / dy)
        dm = atc40_damping(bl, behavior) if d > dy else {
            "beta0": 0.0, "kappa": atc40_kappa(behavior, 0.0, 0.0),
            "beta_eff": 5.0, "SRA": min(max((3.21 - 0.68 * math.log(5.0))
                                            / 2.12, 0), 1.0),
            "SRV": min((2.31 - 0.41 * math.log(5.0)) / 1.65, 1.0)}
        a_red = Demand(atc40_reduced(spec, dm["SRA"], dm["SRV"])
                       ).sa_at_sd(d)
        return (a_red if a_red is not None else -1.0) - api, dm, bl
    xs = [cap.d_end * k / 400.0 for k in range(1, 401)]
    prev = None
    loc_sd, loc_sa = [], []
    found = None
    for x in xs:
        fx, dm, bl = locus(x)
        loc_sd.append(x)
        loc_sa.append(fx + bl["api"])
        if found is None and prev is not None and prev[1] > 0.0 >= fx:
            lo, hi = prev[0], x
            for _ in range(200):
                mid = 0.5 * (lo + hi)
                if locus(mid)[0] > 0.0:
                    lo = mid
                else:
                    hi = mid
                if hi - lo <= 1e-12 * hi:
                    break
            found = 0.5 * (lo + hi)
        prev = (x, fx)
    if found is None:
        res = {"converged": False, "solver": "locus", "d": None,
               "info": dict(locus(cap.d_end)[1], bilinear=bl0),
               "iterations": []}
    else:
        _, dm, bl = locus(found)
        res = {"converged": True, "solver": "locus", "d": found,
               "info": dict(dm, bilinear=bl), "iterations": []}
    out = _atc_out(cap, spec, res, behavior, "B")
    out["locus"] = {"Sd": loc_sd, "Sa": loc_sa}
    out["bilinear_fixed"] = bl0
    if found is not None:
        out["performance_point"]["Sa"] = bil(found)
    return out


def _atc_out(cap, spec, res, behavior, procedure) -> dict:
    info = res["info"] or {}
    out = {"procedure": procedure, "behavior": behavior,
           "converged": res["converged"], "solver": res["solver"],
           "iterations": [{k: v for k, v in it.items() if k != "bilinear"}
                          for it in res["iterations"]]}
    for k in ("beta0", "kappa", "beta_eff", "SRA", "SRV"):
        out[k] = info.get(k)
    out["bilinear"] = info.get("bilinear")
    if info.get("SRA") is not None:
        out["reduced_demand"] = Demand(atc40_reduced(
            spec, info["SRA"], info["SRV"])).curve()
    d = res["d"]
    out["performance_point"] = (None if d is None else
                                {"Sd": d, "Sa": cap.sa_at(min(d,
                                                              cap.d_end))})
    if d is not None and info.get("bilinear"):
        bl = info["bilinear"]
        a = cap.sa_at(min(d, cap.d_end))
        out["Teff"] = 2.0 * math.pi * math.sqrt(d / (a * G)) if a else None
    return out


def fema440(cap: Capacity, spec: Spectrum) -> dict:
    """FEMA 440 equivalent linearization performance point with MADRS
    (module docstring)."""
    def trial(dpi):
        bl = cap.bilinear(dpi)
        el = fema440_el(bl["mu"], bl["alpha"], bl["T0"])
        red = (lambda T, B=el["B"]: spec.sa(T) / B)
        hit = cap.intersect(Demand(red, mult=el["M"]))
        return (hit[0] if hit else None), dict(el, bilinear=bl)
    res = _solve(cap, trial, _start(cap, spec))
    info = res["info"] or {}
    out = {"converged": res["converged"], "solver": res["solver"],
           "iterations": [{k: v for k, v in it.items() if k != "bilinear"}
                          for it in res["iterations"]],
           "bilinear": info.get("bilinear")}
    for k in ("mu", "alpha", "T0", "Teff", "Tsec", "beta_eff", "B", "M"):
        out[k] = info.get(k)
    if info.get("B") is not None:
        B, M = info["B"], info["M"]
        out["reduced_demand"] = Demand(lambda T: spec.sa(T) / B).curve()
        out["madrs"] = Demand(lambda T: spec.sa(T) / B, mult=M).curve()
    d = res["d"]
    out["performance_point"] = (None if d is None
                                else {"Sd": d, "Sa": cap.sa_at(d)})
    if d is not None and info.get("Teff"):
        # FEMA 440 procedure B check: the Teff radial line meets the
        # reduced spectrum at d_max = Sa(Teff)/B * Teff^2 g / 4 pi^2
        Te = info["Teff"]
        out["d_max_radial"] = sd_of(spec.sa(Te) / info["B"], Te)
    return out


# ======================================================================
# EN 1998-1 Annex B (N2)
# ======================================================================
def n2(disp: Sequence[float], shear: Sequence[float], *, gamma: float,
       m_star: float, spec: Spectrum) -> dict:
    """EN 1998-1 Annex B target displacement (module docstring).

    ``disp``/``shear`` = the MDOF curve (control displacement m, base
    shear kN); ``gamma`` = Gamma (= PF1 phi_roof); ``m_star`` (t)."""
    if not (gamma > 0.0 and m_star > 0.0):
        raise ValueError("n2: Gamma and m* must be > 0")
    ds = [0.0] + [float(d) / gamma for d in disp]
    fs = [0.0] + [float(v) / gamma for v in shear]
    fmax = max(fs)
    # d*m: the END of the peak plateau (the plastic mechanism)
    ip = max(i for i in range(len(fs)) if fs[i] >= fmax * (1.0 - 1e-9))
    if ip < 1 or fs[ip] <= 0.0:
        raise ValueError("n2: the capacity curve has no positive force")
    Fy = fs[ip]
    dm = ds[ip]
    Em = sum(0.5 * (f1 + f2) * (d2 - d1) for d1, f1, d2, f2 in
             zip(ds[:ip], fs[:ip], ds[1:ip + 1], fs[1:ip + 1]))
    dy = 2.0 * (dm - Em / Fy)
    if dy <= 0.0:
        raise ValueError("n2: non-positive idealised yield displacement")
    T = 2.0 * math.pi * math.sqrt(m_star * dy / Fy)
    Se = spec.sa(T)                                     # g
    d_et = Se * G * (T / (2.0 * math.pi)) ** 2
    TC = spec.TC
    qu = Se * G * m_star / Fy
    if T >= TC:
        rule = "equal_displacement"
        d_t = d_et
    elif Fy / m_star >= Se * G:
        rule = "elastic"
        d_t = d_et
    else:
        rule = "short_period"
        d_t = max(d_et / qu * (1.0 + (qu - 1.0) * TC / T), d_et)
    return {"gamma": gamma, "m_star": m_star, "F_star_y": Fy,
            "d_star_y": dy, "d_star_m": dm, "E_star_m": Em, "T_star": T,
            "TC": TC, "Se": Se, "d_star_et": d_et, "qu": qu,
            "d_star_t": d_t, "rule": rule, "dt": gamma * d_t,
            "epp": {"d_star": [0.0, dy, max(dm, d_t)],
                    "F_star": [0.0, Fy, Fy]}}


# ======================================================================
# ASCE 41 coefficient method on any spectrum
# ======================================================================
def asce41(disp, shear, *, Ti: float, W: float, spec: Spectrum,
           site_class: str = "D", num_stories: int = 1) -> dict:
    """ASCE 41-17 §7.4.3 coefficient method — the exact formulas of
    :func:`skyframe.design.performance.target_displacement` with Sa(Te)
    read from ``spec`` (identical results for an ``asce7`` spectrum
    without TL)."""
    from skyframe.design import performance as _pf
    if spec.code == "asce7" and spec.TL is None:
        return _pf.target_displacement(disp, shear, Ti=Ti, W=W,
                                       SDS=spec.SDS, SD1=spec.SD1,
                                       site_class=site_class,
                                       num_stories=num_stories)
    if Ti <= 0.0 or W <= 0.0:
        raise ValueError("target_displacement: Ti and W must be > 0")
    sc = str(site_class).upper()
    bl = _pf.bilinearize(disp, shear)
    Te = Ti * math.sqrt(bl["Ki"] / bl["Ke"])
    Sa = spec.sa(Te)
    mu = Sa / (bl["Vy"] / W)
    a = _pf._SITE_A[sc]
    if Te >= 1.0:
        C1 = 1.0
    else:
        Tc = max(Te, 0.2)
        C1 = 1.0 + (mu - 1.0) / (a * Tc * Tc)
    C2 = 1.0 if Te > 0.7 else 1.0 + ((mu - 1.0) / Te) ** 2 / 800.0
    C0 = _pf.c0_factor(num_stories)
    delta_t = C0 * C1 * C2 * Sa * (Te / (2.0 * math.pi)) ** 2 * G
    out = dict(bl)
    out.update({"Te": Te, "Ti": Ti, "Sa": Sa, "mu": mu, "Cm": 1.0,
                "C0": C0, "C1": C1, "C2": C2, "W": W, "site_class": sc,
                "delta_t": delta_t})
    return out


# ======================================================================
# all methods
# ======================================================================
def _interp(xs, ys, x):
    if not xs:
        return None
    if x <= xs[0]:
        return ys[0] * x / xs[0] if xs[0] else ys[0]
    for x1, y1, x2, y2 in zip(xs, ys, xs[1:], ys[1:]):
        if x <= x2:
            return y1 + (y2 - y1) * (x - x1) / (x2 - x1)
    return None


def evaluate(disp: Sequence[float], shear: Sequence[float], *,
             modal: dict, options: Optional[dict] = None,
             num_stories: int = 1) -> dict:
    """Every selected method on one capacity curve.

    ``modal``: {T, gamma, phi_ctrl, pf_phi, alpha, mass, W, mode}.
    Returns the CONTRACT block {options, modal, spectrum, capacity,
    demand, methods, summary, warnings}."""
    opts = normalize_options(options)
    spec = Spectrum(opts["spectrum"])
    W = float(modal["W"])
    alpha = float(modal["alpha"])
    pf = float(modal["pf_phi"])
    if pf < 0.0:                # mode-shape sign: flip to the push sense
        pf = -pf
    if not (pf > 0.0 and alpha > 0.0 and W > 0.0):
        raise ValueError("pushover performance: the push-direction mode "
                         "has no participation (alpha1, PF1*phi_roof)")
    disp = [float(x) for x in disp]
    shear = [float(x) for x in shear]
    if len(disp) < 2:
        raise ValueError("pushover performance: fewer than 2 converged "
                         "steps")
    Sd, Sa = adrs(disp, shear, W=W, alpha=alpha, pf_phi=pf)
    cap = Capacity(Sd, Sa)
    warnings: List[str] = []
    methods: Dict[str, dict] = {}
    summary: List[dict] = []

    def row(key, sd, extra):
        r = {"method": key, "label": METHOD_LABELS[key]}
        if sd is None:
            r.update({"roof_disp": None, "base_shear": None, "Sd": None,
                      "Sa": None, "beyond_curve": True})
        else:
            dr = sd * pf
            v = _interp(disp, shear, dr)
            r.update({"roof_disp": dr, "base_shear": v, "Sd": sd,
                      "Sa": (None if v is None else v / (alpha * W)),
                      "beyond_curve": v is None})
        r.update(extra)
        summary.append(r)

    for key in opts["methods"]:
        try:
            if key == "asce41":
                res = asce41(disp, shear, Ti=float(modal["T"]), W=W,
                             spec=spec, site_class=opts["site_class"],
                             num_stories=num_stories)
                methods[key] = res
                row(key, res["delta_t"] / pf,
                    {"Teff": res["Te"], "beta_eff": None,
                     "converged": True})
            elif key == "atc40":
                res = atc40(cap, spec, opts["behavior"],
                            opts["atc40_procedure"])
                methods[key] = res
                pp = res["performance_point"]
                row(key, None if pp is None else pp["Sd"],
                    {"Teff": res.get("Teff"), "beta_eff": res["beta_eff"],
                     "converged": res["converged"]})
            elif key == "fema440":
                res = fema440(cap, spec)
                methods[key] = res
                pp = res["performance_point"]
                row(key, None if pp is None else pp["Sd"],
                    {"Teff": res["Teff"], "beta_eff": res["beta_eff"],
                     "converged": res["converged"]})
            else:
                m_star = alpha * float(modal["mass"]) / pf
                res = n2(disp, shear, gamma=pf, m_star=m_star, spec=spec)
                methods[key] = res
                row(key, res["d_star_t"],
                    {"Teff": res["T_star"], "beta_eff": None,
                     "converged": True})
            if summary and summary[-1]["method"] == key and \
                    summary[-1].get("roof_disp") is None:
                warnings.append(f"{METHOD_LABELS[key]}: the demand exceeds "
                                "the capacity curve (no performance point "
                                "within the pushover)")
            elif summary and summary[-1]["method"] == key and \
                    summary[-1].get("beyond_curve"):
                warnings.append(f"{METHOD_LABELS[key]}: the target lies "
                                "beyond the end of the capacity curve")
        except (ValueError, ZeroDivisionError, OverflowError) as exc:
            methods[key] = {"error": str(exc)}
            warnings.append(f"{METHOD_LABELS[key]}: {exc}")
    return {"options": opts,
            "modal": dict(modal, pf_phi=pf),
            "spectrum": spec.describe(),
            "capacity": cap.to_dict(),
            "demand": Demand(spec.sa).curve(),
            "methods": methods,
            "summary": summary,
            "warnings": warnings}
