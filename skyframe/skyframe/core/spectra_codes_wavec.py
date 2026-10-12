"""Wave C response-spectrum functions and spectrum file import (CONTRACT
"Wave C: regional seismic / wind generators, code spectra, spectrum file
import, USGS lookup and regional material / steel libraries").

Adds code spectra to :data:`skyframe.core.spectra_codes.CODES` (one-line
hook at the end of that module), so the existing catalogue / preview /
create endpoints and the Define > Functions > Response Spectrum "From
code" dialog pick them up:

* ``tsc2018``   TBDY 2018 §2.3.4 elastic Sae(T) (Tables 2.1/2.2 FS/F1) and
                the §4.3 reduced design SaR(T) = Sae(T)/Ra(T);
* ``kds41``     KDS 41 17 00:2019 §4.2.1.3 design spectrum (SDS = S 2.5 Fa
                2/3, SD1 = S Fv 2/3; T0 = 0.2 Ts, Ts = SD1/SDS, TL);
* ``nscp2015``  NSCP 2015 §208.4.8 design response spectrum (Ca, Cv:
                Ts = Cv/(2.5 Ca), T0 = 0.2 Ts);
* ``ntc_cdmx``  Mexico City NTC-Sismo 2017 §3.1 site spectrum a(T) and the
                design a(T)/(Q'(T) R(T));
* ``ntc2018``   Italian NTC 2018 §3.2.3.2 elastic / §3.2.3.5 design;
* ``ec8_na``    EN 1998-1 elastic / design spectrum with a national-annex
                preset (:data:`skyframe.core.seismic_codes_wavec.EC8_NA`).

File import (:func:`parse_spectrum_text`): period-value pairs from CSV /
whitespace / semicolon text, header lines skipped, period in s or a
frequency in Hz, values in g or an acceleration unit (converted to g), an
optional damping line.
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Optional

from . import seismic_codes_ext as _sx
from . import seismic_codes_wavec as _sxc
from . import spectra_codes as _sc
from .spectra_codes import _ch, _p

G0 = 9.80665
_G = {"quantity": "g"}
_S = {"quantity": "period"}
_R = {"quantity": "ratio"}


def _tmax(default=6.0):
    return _p("T_max", "Last period", default, gt=0.0, max=50.0,
              advanced=True, **_S)


# ------------------------------------------------------------- TSC 2018
def _tsc(p: dict):
    site = p["site"]
    FS = (_sx._interp(p["SS"], _sx.TSC_SS, _sx.TSC_FS[site])
          if p.get("FS") is None else p["FS"])
    F1 = (_sx._interp(p["S1"], _sx.TSC_S1, _sx.TSC_F1[site])
          if p.get("F1") is None else p["F1"])
    SDS, SD1, TL = p["SS"] * FS, p["S1"] * F1, p["TL"]
    if not SDS > 0.0:
        raise ValueError("tsc2018: SDS must be > 0")
    TA, TB = 0.2 * SD1 / SDS, SD1 / SDS
    design = p["kind"] == "design"
    R, D, I = p["R"], p["D"], p["I"]

    def Ra(T):
        return R / I if T > TB else D + (R / I - D) * T / TB

    def f(T):
        v = _sx.tsc_sae(T, SDS, SD1, TL)
        return v / Ra(T) if design else v
    return f, (0.0, TA, TB, TL), {"SDS": SDS, "SD1": SD1, "FS": FS,
                                  "F1": F1, "TA": TA, "TB": TB, "TL": TL}


# ---------------------------------------------------------------- KDS 41
def _kds(p: dict):
    SDS = (p["S"] * 2.5 * p["Fa"] * 2.0 / 3.0 if p.get("SDS") is None
           else p["SDS"])
    SD1 = p["S"] * p["Fv"] * 2.0 / 3.0 if p.get("SD1") is None else p["SD1"]
    TL = p["TL"]
    Ts = SD1 / SDS
    T0 = 0.2 * Ts
    sc = p["IE"] / p["R"]

    def f(T):
        if T < T0:
            v = SDS * (0.4 + 0.6 * T / T0)
        elif T <= Ts:
            v = SDS
        elif T <= TL:
            v = SD1 / T
        else:
            v = SD1 * TL / (T * T)
        return sc * v
    return f, (0.0, T0, Ts, TL), {"SDS": SDS, "SD1": SD1, "T0": T0,
                                  "Ts": Ts, "TL": TL, "scale": sc}


# ------------------------------------------------------------- NSCP 2015
def _nscp(p: dict):
    zi = 1 if float(p["Z"]) == 0.4 else 0
    soil = p["soil"]
    Ca = (_sx.NSCP_CA[soil][zi] * (p["Na"] if zi else 1.0)
          if p.get("Ca") is None else p["Ca"])
    Cv = (_sx.NSCP_CV[soil][zi] * (p["Nv"] if zi else 1.0)
          if p.get("Cv") is None else p["Cv"])
    Ts = Cv / (2.5 * Ca)
    T0 = 0.2 * Ts
    sc = p["I"] / p["R"]

    def f(T):
        if T < T0:
            v = Ca * (1.0 + 1.5 * T / T0)
        elif T <= Ts:
            v = 2.5 * Ca
        else:
            v = Cv / T
        return sc * v
    return f, (0.0, T0, Ts), {"Ca": Ca, "Cv": Cv, "T0": T0, "Ts": Ts,
                              "scale": sc}


# ------------------------------------------------------- NTC-Sismo CDMX
def ntc_cdmx_design(T: float, p: dict) -> dict:
    """a(T), Q'(T), R(T) of NTC-Sismo 2017 §3.1 / §3.4 / §3.5 (the same
    expressions as :func:`seismic_codes_ext.compute_ntc_cdmx`)."""
    a0, c, Ta, Tb, k, beta = (p["a0"], p["c"], p["Ta"], p["Tb"], p["k"],
                              p["beta"])
    sp = _sx.ntc_spectrum(T, a0, c, Ta, Tb, k, beta)
    a, pp = sp["a"], sp["p"]
    Q, R0, k1 = p["Q"], p["R0"], p["k1"]
    if T <= Ta:
        Qp = 1.0 + (Q - 1.0) * math.sqrt(beta / k) * T / Ta
        R = k1 * R0 + 1.0 - math.sqrt(T / Ta)
    else:
        Qp = (1.0 + (Q - 1.0) * math.sqrt(beta / k) if T <= Tb else
              1.0 + (Q - 1.0) * math.sqrt(beta * pp / k))
        R = k1 * R0
    return {"a": a, "Qp": Qp, "R": R}


def _ntc_cdmx(p: dict):
    if p["Tb"] < p["Ta"]:
        raise ValueError("ntc_cdmx: Tb must be >= Ta")
    design = p["kind"] == "design"

    def f(T):
        d = ntc_cdmx_design(T, p)
        return d["a"] / (d["Qp"] * d["R"]) if design else d["a"]
    return f, (0.0, p["Ta"], p["Tb"]), {"plateau": p["beta"] * p["c"],
                                        "Ta": p["Ta"], "Tb": p["Tb"]}


# --------------------------------------------------------- Italian NTC 2018
def _ntc2018(p: dict):
    ag, F0, Tcs = p["ag"], p["F0"], p["Tc_star"]
    q = _sxc.ntc18_parameters(ag, F0, Tcs, p["soil"], p["topography"])
    if p["kind"] == "design":
        eta = 1.0 / p["q"]
        floor = 0.2 * ag
    else:
        eta = max(math.sqrt(10.0 / (5.0 + 100.0 * p["damping"])), 0.55)
        floor = 0.0

    def f(T):
        return max(_sxc.ntc18_spectrum(T, ag, F0, q["S"], q["TB"], q["TC"],
                                       q["TD"], eta), floor)
    return f, (0.0, q["TB"], q["TC"], q["TD"]), {
        **q, "eta": eta, "plateau": ag * q["S"] * eta * F0}


# ------------------------------------------------------------ EC8 + NA
def _ec8_na(p: dict):
    st = None if p["spectrum_type"] == "na" else int(p["spectrum_type"])
    gamma_ag = p["gamma_I"] * p["agR"]
    na = _sxc.ec8_na_parameters(p["annex"], p["ground_type"], st,
                                ag=gamma_ag)
    q = dict(p)
    q.update({"spectrum_type": 1, "ground_type": "A", "S": na["S"],
              "TB": na["TB"], "TC": na["TC"], "TD": na["TD"]})
    if p.get("beta") is None:
        q["beta"] = na["beta"]
    fn, corners, summ = _sc._ec8(q)
    summ.update({"annex": p["annex"], "spectrum_type": na["spectrum_type"],
                 "ground_type": p["ground_type"]})
    return fn, corners, summ


_EC8_GROUNDS = []
for _na in _sxc.EC8_NA.values():
    for _tab in _na["spectra"].values():
        for _g in _tab:
            if _g not in _EC8_GROUNDS:
                _EC8_GROUNDS.append(_g)

WAVEC_CODES: Dict[str, dict] = {
    "tsc2018": {
        "label": "TSC 2018 (TBDY)", "clause": "§2.3.4 Sae(T) / §4.3 SaR(T)",
        "fn": _tsc,
        "params": [
            _p("SS", "SS (0.2 s map)", 1.0, gt=0.0, **_G),
            _p("S1", "S1 (1 s map)", 0.3, min=0.0, **_G),
            _ch("site", "Local soil class", "ZC",
                [(s, s) for s in ("ZA", "ZB", "ZC", "ZD", "ZE")]),
            _p("FS", "FS override", None, nullable=True, gt=0.0,
               advanced=True, **_R),
            _p("F1", "F1 override", None, nullable=True, gt=0.0,
               advanced=True, **_R),
            _p("TL", "Long-period TL", 6.0, gt=0.0, **_S),
            _ch("kind", "Spectrum", "elastic",
                [("elastic", "Elastic Sae(T)"),
                 ("design", "Reduced SaR(T) = Sae/Ra")]),
            _p("R", "Response modification R", 8.0, gt=0.0, **_R),
            _p("D", "Overstrength D", 3.0, gt=0.0, **_R),
            _p("I", "Importance factor I", 1.0, gt=0.0, **_R),
            _tmax()]},
    "kds41": {
        "label": "KDS 41 17 00:2019 (Korea)",
        "clause": "§4.2.1.3 design response spectrum",
        "fn": _kds,
        "params": [
            _p("S", "Effective PGA S", 0.22, gt=0.0, **_G),
            _p("Fa", "Site coefficient Fa", 1.0, gt=0.0, **_R),
            _p("Fv", "Site coefficient Fv", 1.0, gt=0.0, **_R),
            _p("SDS", "SDS override", None, nullable=True, gt=0.0,
               advanced=True, **_G),
            _p("SD1", "SD1 override", None, nullable=True, gt=0.0,
               advanced=True, **_G),
            _p("TL", "Long-period TL", 5.0, gt=0.0, **_S),
            _p("R", "Response modification R (1 = elastic)", 1.0, gt=0.0,
               advanced=True, **_R),
            _p("IE", "Importance factor IE", 1.0, gt=0.0, advanced=True,
               **_R),
            _tmax()]},
    "nscp2015": {
        "label": "NSCP 2015 (Philippines)",
        "clause": "§208.4.8 design response spectrum (Ca, Cv)",
        "fn": _nscp,
        "params": [
            _ch("Z", "Seismic zone", 0.4, [(0.2, "Zone 2 (Z = 0.2)"),
                                           (0.4, "Zone 4 (Z = 0.4)")]),
            _ch("soil", "Soil profile", "SD",
                [(s, s) for s in ("SA", "SB", "SC", "SD", "SE")]),
            _p("Na", "Near-source Na", 1.0, gt=0.0, **_R),
            _p("Nv", "Near-source Nv", 1.0, gt=0.0, **_R),
            _p("Ca", "Ca override", None, nullable=True, gt=0.0,
               advanced=True, **_G),
            _p("Cv", "Cv override", None, nullable=True, gt=0.0,
               advanced=True, **_G),
            _p("R", "Response modification R (1 = elastic)", 1.0, gt=0.0,
               advanced=True, **_R),
            _p("I", "Importance factor I", 1.0, gt=0.0, advanced=True,
               **_R),
            _tmax()]},
    "ntc_cdmx": {
        "label": "Mexico CDMX NTC-Sismo 2017",
        "clause": "§3.1 site spectrum a(T), design a/(Q'R)",
        "fn": _ntc_cdmx,
        "params": [
            _p("a0", "a0", 0.2, gt=0.0, **_G),
            _p("c", "c (plateau)", 0.6, gt=0.0, **_G),
            _p("Ta", "Ta", 0.4, gt=0.0, **_S),
            _p("Tb", "Tb", 1.0, gt=0.0, **_S),
            _p("k", "k", 1.0, gt=0.0, **_R),
            _p("beta", "Damping factor beta", 1.0, gt=0.0, **_R),
            _ch("kind", "Spectrum", "elastic",
                [("elastic", "Elastic a(T)"),
                 ("design", "Design a/(Q'R)")]),
            _p("Q", "Behaviour factor Q", 2.0, min=1.0, **_R),
            _p("R0", "Overstrength R0", 2.0, gt=0.0, **_R),
            _p("k1", "Redundancy k1", 1.0, gt=0.0, **_R),
            _tmax()]},
    "ntc2018": {
        "label": "Italian NTC 2018",
        "clause": "§3.2.3.2 elastic / §3.2.3.5 design spectrum",
        "fn": _ntc2018,
        "params": [
            _p("ag", "ag (site)", 0.25, gt=0.0, **_G),
            _p("F0", "F0", 2.4, gt=0.0, **_R),
            _p("Tc_star", "Tc*", 0.3, gt=0.0, **_S),
            _ch("soil", "Soil category", "B",
                [(s, "Category " + s) for s in "ABCDE"]),
            _ch("topography", "Topography", "T1",
                [(t, t) for t in ("T1", "T2", "T3", "T4")]),
            _ch("kind", "Spectrum", "elastic",
                [("elastic", "Elastic Se(T)"),
                 ("design", "Design Sd(T) (eta = 1/q)")]),
            _p("q", "Behaviour factor q", 1.5, gt=0.0, **_R),
            _p("damping", "Damping ratio (elastic)", 0.05, gt=0.0, lt=1.0,
               **_R),
            _tmax(4.0)]},
    "ec8_na": {
        "label": "Eurocode 8 + national annex",
        "clause": "EN 1998-1 §3.2.2 with NA parameter set",
        "fn": _ec8_na,
        "params": [
            _ch("annex", "National annex", "UK",
                [(k, v["label"]) for k, v in _sxc.EC8_NA.items()]),
            _ch("spectrum_type", "Spectrum type", "na",
                [("na", "NA default"), (1, "Type 1"), (2, "Type 2")]),
            _ch("ground_type", "Ground type", "B",
                [(g, g) for g in _EC8_GROUNDS]),
            _p("agR", "Reference PGA agR", 0.1, gt=0.0, **_G),
            _p("gamma_I", "Importance factor gamma_I", 1.0, gt=0.0, **_R),
            _ch("kind", "Spectrum", "elastic",
                [("elastic", "Elastic Se(T)"), ("design", "Design Sd(T)")]),
            _p("q", "Behaviour factor q", 1.5, gt=0.0, **_R),
            _p("beta", "Lower bound beta (blank = NA)", None,
               nullable=True, min=0.0, advanced=True, **_R),
            _p("damping", "Damping ratio (elastic)", 0.05, gt=0.0, lt=1.0,
               **_R),
            _tmax(4.0)]},
}


def register() -> None:
    """Add the Wave C codes to ``spectra_codes.CODES`` (idempotent)."""
    for k, v in WAVEC_CODES.items():
        _sc.CODES.setdefault(k, v)


# =========================================================== file import
#: acceleration unit -> factor to g
VALUE_UNITS = {"g": 1.0, "m/s2": 1.0 / G0, "cm/s2": 0.01 / G0,
               "mm/s2": 0.001 / G0, "ft/s2": 0.3048 / G0,
               "in/s2": 0.0254 / G0}
PERIOD_UNITS = ("s", "Hz")
_NUM = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eEdD][+-]?\d+)?$")
_DAMP = re.compile(r"damp\w*\s*[=:]?\s*([0-9]*\.?[0-9]+)\s*(%?)", re.I)


def _tok(line: str) -> List[str]:
    return [t for t in re.split(r"[,;\s]+", line.strip()) if t]


def _isnum(t: str) -> bool:
    return bool(_NUM.match(t))


def _f(t: str) -> float:
    return float(t.replace("d", "e").replace("D", "e"))


def parse_spectrum_text(text: str, period_units: str = "s",
                        value_units: str = "g", skip_lines: int = 0,
                        scale: float = 1.0) -> dict:
    """Parse ``[T, Sa]`` pairs from a text file.

    * Lines: comments after ``#``, ``//``, ``!`` or ``;;`` are dropped;
      fields split on commas, semicolons, tabs or spaces; the first two
      numeric fields are (period-or-frequency, value).  ``skip_lines``
      header lines are dropped first; any other leading non-numeric line
      is a header (a later one is an error).
    * A header line matching ``damping[:=] 5%`` or ``damping 0.05`` sets
      the reported damping (a bare value > 1 is read as a percentage).
    * ``period_units``: ``"s"`` (period) or ``"Hz"`` (frequency, T = 1/f;
      f = 0 rejected); ``value_units``: g, m/s2, cm/s2, mm/s2, ft/s2,
      in/s2 (converted to g with g0 = 9.80665 m/s^2), then x ``scale``.
    * Points are sorted by period; duplicate periods and negative values
      are rejected.

    Returns ``{"points": [[T, Sa_g]], "n", "damping", "header_lines",
    "period_units", "value_units", "scale"}``.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("spectrum file is empty")
    if period_units not in PERIOD_UNITS:
        raise ValueError(f"period_units must be one of {PERIOD_UNITS}")
    if value_units not in VALUE_UNITS:
        raise ValueError(f"value_units must be one of {tuple(VALUE_UNITS)}")
    if (isinstance(skip_lines, bool) or not isinstance(skip_lines, int)
            or skip_lines < 0):
        raise ValueError("skip_lines must be an integer >= 0")
    if (isinstance(scale, bool) or not isinstance(scale, (int, float))
            or not math.isfinite(scale) or scale <= 0.0):
        raise ValueError("scale must be a finite value > 0")
    lines = text.splitlines()[skip_lines:]
    damping: Optional[float] = None
    pts: List[List[float]] = []
    header = skip_lines
    for no, raw in enumerate(lines, start=skip_lines + 1):
        line = re.split(r"#|//|!|;;", raw, maxsplit=1)[0]
        toks = _tok(line)
        if not toks:
            m = _DAMP.search(raw)
            if m and not pts:
                damping = _damp_value(m)
            continue
        nums = [t for t in toks if _isnum(t)]
        if len(nums) >= 2 and _isnum(toks[0]):
            x, y = _f(toks[0]), _f(toks[1]) if _isnum(toks[1]) else _f(
                nums[1])
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError(f"line {no}: values must be finite")
            if period_units == "Hz":
                if x <= 0.0:
                    raise ValueError(f"line {no}: frequency must be > 0")
                x = 1.0 / x
            if x < 0.0 or y < 0.0:
                raise ValueError(f"line {no}: period and value must be "
                                 ">= 0")
            pts.append([x, y * VALUE_UNITS[value_units] * scale])
            continue
        if pts:
            raise ValueError(f"line {no}: expected a period-value pair, "
                             f"got {raw.strip()!r}")
        header += 1
        m = _DAMP.search(raw)
        if m:
            damping = _damp_value(m)
    if not pts:
        raise ValueError("no period-value pairs found")
    pts.sort(key=lambda p: p[0])
    for a, b in zip(pts, pts[1:]):
        if b[0] == a[0]:
            raise ValueError(f"duplicate period {a[0]:g} s")
    return {"points": pts, "n": len(pts), "damping": damping,
            "header_lines": header, "period_units": period_units,
            "value_units": value_units, "scale": float(scale)}


def _damp_value(m) -> float:
    v = float(m.group(1))
    if m.group(2) == "%" or v >= 1.0:
        v /= 100.0
    if not 0.0 < v < 1.0:
        raise ValueError("damping in the file must be in (0, 100) %")
    return v


def import_spectrum_function(model, name: str, text: str,
                             period_units: str = "s",
                             value_units: str = "g", skip_lines: int = 0,
                             scale: float = 1.0,
                             damping: Optional[float] = None):
    """Add (or replace) ``model.spectrum_functions[name]`` from file text.
    ``damping``: given > the file's damping line > 0.05.  The function is
    a plain user function (``source`` None).  Returns (function, parse)."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("'name' is required")
    res = parse_spectrum_text(text, period_units, value_units, skip_lines,
                              scale)
    d = damping if damping is not None else (res["damping"] or 0.05)
    if (isinstance(d, bool) or not isinstance(d, (int, float))
            or not 0.0 < d < 1.0):
        raise ValueError("damping must be in (0, 1)")
    fn = model.add_spectrum_function(name.strip(), res["points"],
                                     damping=float(d))
    res["damping_used"] = float(d)
    return fn, res


register()
