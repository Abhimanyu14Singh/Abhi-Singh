"""Time-history function input: file import, generators and spectral
matching (ETABS Define > Functions > Time History parity).

Pure numpy; the result of every entry point is a plain
``{"values": [...], "dt": float, "source": {...}, ...}`` dict whose
``values`` / ``dt`` drop straight into a
:class:`skyframe.core.model.TimeHistoryFunction` (SI: m/s^2, s; sample k
applies at ``t = k*dt``).  ``source`` is the provenance record stored on
the function (``TimeHistoryFunction.source``; emitted by ``to_dict`` only
when set, so functions without it serialise byte-identically).

File import (:func:`import_function`)
-------------------------------------
* ``"at2"`` — PEER NGA ``.AT2`` (both the classic
  ``NPTS=  3909, DT= .0100 SEC`` header line and the NGA-West2
  ``3909    0.0100    NPTS, DT`` form; units from the header's
  ``UNITS OF G`` line).  The first 4 lines are the header; the first
  ``NPTS`` numbers after it are the record.
* ``"time_value"`` — free-format ``t a`` pairs after ``header_lines``
  skipped lines.  A uniform spacing (within 1e-6 relative) is kept
  exactly; otherwise the record is RESAMPLED by linear interpolation at
  ``dt`` (given, else the smallest positive spacing) — the engine needs
  equal steps (documented deviation from ETABS, which integrates the
  non-uniform pairs).  A first time ``t0 > 0`` gets a ``(0, 0)`` point
  prepended (warning); a negative time is an error.
* ``"equal"`` — values at equal intervals ``dt`` (required) after
  ``header_lines``; any number of values per line.

Units / scale: ``values = raw * UNIT_FACTORS[units] * scale``
(``units`` in ``g`` (9.80665), ``m/s2``, ``cm/s2``, ``mm/s2``,
``in/s2``, ``ft/s2``; AT2 defaults to the header units, else ``g``).

Generators (:func:`generate_function`)
--------------------------------------
``sine`` / ``cosine`` (period ``T``, ``steps_per_cycle``, ``cycles``,
``amplitude``), ``ramp`` (``ramp_time``, ``amplitude``, ``max_time``,
``dt``), ``sawtooth`` (``T``, ``ramp_time`` <= T/4, ``steps_per_cycle``,
``cycles``, ``amplitude``: per period the piecewise-linear wave through
(0,0) (tr,A) (T/2-tr,A) (T/2+tr,-A) (T-tr,-A) (T,0)), ``triangular``
((0,0) (T/4,A) (3T/4,-A) (T,0)) and ``user_periodic`` (one cycle of
``values`` at ``dt`` repeated ``cycles`` times; the closing sample of a
cycle is the next cycle's first, so it is not duplicated).

Spectral matching (:func:`match_spectrum`)
------------------------------------------
Frequency-domain approximation of spectral matching (the iterative
Fourier-amplitude scaling family; the time-domain wavelet adjustment of
Al Atik & Abrahamson 2010 is NOT implemented):

1. the 5%-damped pseudo-acceleration spectrum ``PSA(T)`` of the current
   record is computed EXACTLY for piecewise-linear excitation
   (:func:`response_spectrum`: state-transition matrix of the SDOF over
   one step, the Nigam-Jennings idea) on ``n_periods`` log-spaced
   periods covering ``[t_min, t_max]``;
2. the ratio ``r(T) = target(T) / PSA(T)`` is mapped to frequency
   ``f = 1/T``, interpolated linearly in ``log f`` onto the FFT bins and
   applied to the Fourier amplitudes (phases kept); outside the period
   range the ratio fades to 1 over a quarter decade;
3. the inverse FFT (zero padded to twice the length, then truncated)
   gets a baseline correction (the least-squares quartic of the
   double-integrated displacement is removed through its second
   derivative), and the loop repeats until ``max |r - 1| <= tol`` on the
   grid or ``max_iter`` is reached.

The matched spectrum is checked against the target on the same grid and
``max_misfit`` (``max |PSA/target - 1|``) is reported; the CONTRACT pins
it within +-10% over the period range for a synthetic record.
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

G_ACC = 9.80665
UNIT_FACTORS: Dict[str, float] = {
    "g": G_ACC, "m/s2": 1.0, "cm/s2": 0.01, "mm/s2": 0.001,
    "in/s2": 0.0254, "ft/s2": 0.3048,
}
IMPORT_FORMATS = ("at2", "time_value", "equal")
GENERATOR_KINDS = ("sine", "cosine", "ramp", "sawtooth", "triangular",
                   "user_periodic")
MAX_SAMPLES = 200000

_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?")


def _numbers(text: str) -> List[float]:
    return [float(t.replace("D", "E").replace("d", "e"))
            for t in _NUM.findall(text)]


def _pos(v, what: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number (got {v!r})") from None
    if not (math.isfinite(x) and x > 0.0):
        raise ValueError(f"{what} must be > 0 (got {v!r})")
    return x


def _count(v, what: str, lo: int = 1) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) \
            or int(v) != v or int(v) < lo:
        raise ValueError(f"{what} must be an integer >= {lo} (got {v!r})")
    return int(v)


def _unit_factor(units: str) -> float:
    if units not in UNIT_FACTORS:
        raise ValueError(f"units must be one of {list(UNIT_FACTORS)} "
                         f"(got {units!r})")
    return UNIT_FACTORS[units]


def _check_len(n: int) -> None:
    if n < 2:
        raise ValueError("a time-history function needs at least 2 values")
    if n > MAX_SAMPLES:
        raise ValueError(f"too many samples ({n} > {MAX_SAMPLES})")


# --------------------------------------------------------------------------- #
# file import
# --------------------------------------------------------------------------- #
def parse_at2(text: str) -> dict:
    """Parse a PEER NGA ``.AT2`` record.

    Returns ``{"values": raw record values (header units), "dt", "npts",
    "units": "g" | ..., "title": str}``.  Raises ``ValueError`` on a
    missing NPTS/DT header or a short record."""
    if not isinstance(text, str):
        raise ValueError("AT2 text must be a string")
    lines = text.splitlines()
    if len(lines) < 4:
        raise ValueError("AT2: expected a 4-line header")
    hdr = lines[3]
    m = re.search(r"NPTS\s*=\s*(\d+)\s*,?\s*DT\s*=\s*([-+0-9.EeDd]+)", hdr,
                  re.IGNORECASE)
    if m:
        npts = int(m.group(1))
        dt = float(m.group(2).replace("D", "E").replace("d", "e"))
    else:
        nums = _numbers(hdr)
        if len(nums) < 2 or "NPTS" not in hdr.upper():
            raise ValueError("AT2: line 4 must give NPTS and DT "
                             f"(got {hdr.strip()!r})")
        npts, dt = int(nums[0]), float(nums[1])
    if npts < 2 or not (dt > 0.0 and math.isfinite(dt)):
        raise ValueError(f"AT2: bad NPTS/DT ({npts}, {dt})")
    units = "g"
    u3 = lines[2].upper()
    for key, name in (("CM/S", "cm/s2"), ("CM/SEC", "cm/s2"),
                      ("M/S", "m/s2"), ("IN/S", "in/s2"),
                      ("UNITS OF G", "g")):
        if key in u3:
            units = name
            break
    vals = _numbers("\n".join(lines[4:]))
    if len(vals) < npts:
        raise ValueError(f"AT2: header says NPTS={npts} but only "
                         f"{len(vals)} values follow")
    return {"values": vals[:npts], "dt": dt, "npts": npts, "units": units,
            "title": " / ".join(s.strip() for s in lines[:2] if s.strip())}


def import_function(text: str, fmt: str, *, dt: Optional[float] = None,
                    header_lines: int = 0, units: Optional[str] = None,
                    scale: float = 1.0) -> dict:
    """Import a record from file text (see the module docstring).

    Returns ``{"values" (m/s^2), "dt", "source", "warnings"}``."""
    if fmt not in IMPORT_FORMATS:
        raise ValueError(f"format must be one of {IMPORT_FORMATS} "
                         f"(got {fmt!r})")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("the file text is empty")
    try:
        sc = float(scale)
    except (TypeError, ValueError):
        raise ValueError(f"scale must be a number (got {scale!r})") from None
    if not math.isfinite(sc) or sc == 0.0:
        raise ValueError("scale must be a finite non-zero number")
    header_lines = _count(header_lines, "header_lines", 0)
    warnings: List[str] = []
    src: dict = {"kind": "file", "format": fmt}
    if fmt == "at2":
        rec = parse_at2(text)
        raw, step = rec["values"], rec["dt"]
        u = units or rec["units"]
        src.update({"npts": rec["npts"], "title": rec["title"]})
    else:
        body = "\n".join(text.splitlines()[header_lines:])
        nums = _numbers(body)
        u = units or "g"
        if fmt == "equal":
            step = _pos(dt, "dt")
            raw = nums
        else:
            if len(nums) % 2:
                raise ValueError("time-value pairs: an odd count of numbers "
                                 f"({len(nums)}) — every line needs t and a")
            ts = np.array(nums[0::2], dtype=float)
            av = np.array(nums[1::2], dtype=float)
            if len(ts) < 2:
                raise ValueError("time-value pairs: need at least 2 pairs")
            if ts[0] < 0.0:
                raise ValueError("time-value pairs: times must be >= 0")
            dts = np.diff(ts)
            if np.any(dts <= 0.0):
                raise ValueError("time-value pairs: times must strictly "
                                 "increase")
            if ts[0] > 0.0:
                ts = np.concatenate([[0.0], ts])
                av = np.concatenate([[0.0], av])
                dts = np.diff(ts)
                warnings.append(f"record starts at t = {nums[0]:g} s: a "
                                "(0, 0) point was prepended")
            uniform = float(np.max(dts) - np.min(dts)) \
                <= 1e-6 * float(np.max(dts))
            if dt is None and uniform:
                step = float((ts[-1] - ts[0]) / (len(ts) - 1))
                raw = av.tolist()
            else:
                step = (_pos(dt, "dt") if dt is not None
                        else float(np.min(dts)))
                n = int(math.floor(ts[-1] / step + 1e-9)) + 1
                _check_len(n)
                grid = np.arange(n) * step
                raw = np.interp(grid, ts, av).tolist()
                if not uniform or dt is not None:
                    warnings.append(f"resampled to dt = {step:g} s "
                                    "(linear interpolation)")
            src["resampled"] = bool(not uniform or dt is not None)
    _check_len(len(raw))
    fac = _unit_factor(u) * sc
    vals = [float(v) * fac for v in raw]
    if not all(math.isfinite(v) for v in vals):
        raise ValueError("the record holds non-finite values")
    src.update({"units": u, "scale": sc})
    if fmt != "at2":
        src["header_lines"] = header_lines
    return {"values": vals, "dt": float(step), "source": src,
            "warnings": warnings}


# --------------------------------------------------------------------------- #
# generators
# --------------------------------------------------------------------------- #
def _periodic(T: float, n: int, cycles: int, wave) -> Tuple[List[float],
                                                              float]:
    dt = T / n
    vals = [float(wave((k % n) / n)) for k in range(n * cycles)]
    vals.append(float(wave(1.0)))           # closing sample of the last cycle
    return vals, dt


def _pl(points: Sequence[Tuple[float, float]]):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return lambda s: float(np.interp(s, xs, ys))


def generate_function(kind: str, **p) -> dict:
    """Generate a record (see the module docstring for each ``kind``).

    Returns ``{"values", "dt", "source"}``; ``amplitude`` is in m/s^2."""
    if kind not in GENERATOR_KINDS:
        raise ValueError(f"generator must be one of {GENERATOR_KINDS} "
                         f"(got {kind!r})")
    A = float(p.get("amplitude", 1.0))
    if not math.isfinite(A):
        raise ValueError("amplitude must be finite")
    src: dict = {"kind": kind}
    if kind in ("sine", "cosine", "sawtooth", "triangular"):
        T = _pos(p.get("period", 1.0), "period")
        n = _count(p.get("steps_per_cycle",
                         16 if kind in ("sine", "cosine") else 40),
                   "steps_per_cycle", 4)
        cyc = _count(p.get("cycles", 1), "cycles")
        _check_len(n * cyc + 1)
        if kind == "sine":
            wave = lambda s: A * math.sin(2.0 * math.pi * s)   # noqa: E731
        elif kind == "cosine":
            wave = lambda s: A * math.cos(2.0 * math.pi * s)   # noqa: E731
        elif kind == "triangular":
            wave = _pl([(0, 0), (0.25, A), (0.75, -A), (1, 0)])
        else:
            tr = _pos(p.get("ramp_time", T / 8.0), "ramp_time")
            if tr > T / 4.0 + 1e-12:
                raise ValueError("sawtooth: ramp_time must be <= period/4")
            r = tr / T
            wave = _pl([(0, 0), (r, A), (0.5 - r, A), (0.5 + r, -A),
                        (1 - r, -A), (1, 0)])
            src["ramp_time"] = tr
        vals, dt = _periodic(T, n, cyc, wave)
        if kind == "sine":                       # exact zero at cycle ends
            vals = [0.0 if (k % n) == 0 else v for k, v in enumerate(vals)]
        src.update({"period": T, "steps_per_cycle": n, "cycles": cyc,
                    "amplitude": A})
    elif kind == "ramp":
        tr = _pos(p.get("ramp_time", 1.0), "ramp_time")
        tm = _pos(p.get("max_time", 2.0 * tr), "max_time")
        dt = _pos(p.get("dt", tr / 20.0), "dt")
        if tm < tr:
            raise ValueError("ramp: max_time must be >= ramp_time")
        n = int(math.floor(tm / dt + 1e-9)) + 1
        _check_len(n)
        vals = [A * min(k * dt / tr, 1.0) for k in range(n)]
        src.update({"ramp_time": tr, "max_time": tm, "amplitude": A})
    else:                                        # user_periodic
        cyc_vals = [float(v) for v in (p.get("values") or [])]
        if len(cyc_vals) < 2 or not all(math.isfinite(v) for v in cyc_vals):
            raise ValueError("user_periodic: give at least 2 finite values "
                             "for one cycle")
        dt = _pos(p.get("dt"), "dt")
        cyc = _count(p.get("cycles", 1), "cycles")
        _check_len(len(cyc_vals) * cyc)
        vals = []
        for c in range(cyc):
            vals.extend(cyc_vals if c == 0 else cyc_vals[1:]
                        if cyc_vals[0] == cyc_vals[-1] else cyc_vals)
        vals = [A * v for v in vals]
        src.update({"cycles": cyc, "amplitude": A,
                    "cycle_values": cyc_vals})
    src["dt"] = float(dt)
    return {"values": [float(v) for v in vals], "dt": float(dt),
            "source": src}


# --------------------------------------------------------------------------- #
# response spectrum (exact for piecewise-linear excitation)
# --------------------------------------------------------------------------- #
def _expm(A: np.ndarray) -> np.ndarray:
    """Matrix exponential by scaling and squaring + Taylor (small dense)."""
    nrm = float(np.max(np.sum(np.abs(A), axis=1)))
    s = max(0, int(math.ceil(math.log2(nrm / 0.25))) if nrm > 0.25 else 0)
    X = A / (2.0 ** s)
    E = np.eye(A.shape[0])
    term = np.eye(A.shape[0])
    for k in range(1, 20):
        term = term @ X / k
        E = E + term
    for _ in range(s):
        E = E @ E
    return E


def _spectrum_block(a: np.ndarray, dt: float, w: np.ndarray,
                    damping: float) -> np.ndarray:
    """PSA of the periods ``2 pi / w`` for one record / step.

    The one-step state transition of the SDOF under linearly varying
    excitation is ``x_{k+1} = P x_k + Q0 a_k + Q1 s_k`` (``P = e^{A dt}``,
    ``Q0`` / ``Q1`` from the exponential of the augmented 4x4 matrix), so
    ``u_k = sum_j (P^{k-1-j} Q0)_u a_j + (P^{k-1-j} Q1)_u s_j`` — two
    discrete convolutions with the closed-form kernels
    ``(e^{A t} q)_u = e^{-z w t} [q_u cos(wd t) + (q_v + z w q_u)/wd
    sin(wd t)]``, evaluated for all periods at once by FFT."""
    n = len(a)
    if n < 2:
        return np.zeros(len(w))
    Q0 = np.zeros((len(w), 2))
    Q1 = np.zeros((len(w), 2))
    for i, wi in enumerate(w):
        M = np.array([[0.0, 1.0, 0.0, 0.0],
                      [-wi * wi, -2.0 * damping * wi, -1.0, 0.0],
                      [0.0, 0.0, 0.0, 1.0],
                      [0.0, 0.0, 0.0, 0.0]])
        E = _expm(M * dt)
        Q0[i] = E[:2, 2]
        Q1[i] = E[:2, 3]
    z = damping
    wd = w * math.sqrt(1.0 - z * z)
    tm = np.arange(n - 1) * dt
    ex = np.exp(-z * np.outer(w, tm))
    c = np.cos(np.outer(wd, tm))
    sn = np.sin(np.outer(wd, tm))

    def kern(Q):
        qu = Q[:, 0:1]
        qv = Q[:, 1:2]
        return ex * (qu * c + ((qv + z * w[:, None] * qu)
                               / wd[:, None]) * sn)
    h0 = kern(Q0)
    h1 = kern(Q1)
    aj = a[:-1]
    sj = np.diff(a) / dt
    nf = 1 << int(math.ceil(math.log2(2 * (n - 1))))
    U = (np.fft.rfft(h0, nf, axis=1) * np.fft.rfft(aj, nf)[None, :]
         + np.fft.rfft(h1, nf, axis=1) * np.fft.rfft(sj, nf)[None, :])
    u = np.fft.irfft(U, nf, axis=1)[:, :n - 1]
    return w * w * np.max(np.abs(u), axis=1)


def response_spectrum(acc: Sequence[float], dt: float,
                      periods: Sequence[float],
                      damping: float = 0.05,
                      min_steps_per_period: int = 20) -> np.ndarray:
    """Pseudo-acceleration spectrum ``PSA(T) = w^2 max|u|`` (same units as
    ``acc``) of the linear SDOF ``u'' + 2 z w u' + w^2 u = -a(t)``, ``a``
    linear between samples (exact one-step state transition).  The peak
    is sampled at the record steps, sub-divided (the record linearly
    interpolated — the SAME excitation) so every period gets at least
    ``min_steps_per_period`` samples per cycle (peak sampling error
    <= 1 - cos(pi/20) = 1.2%)."""
    a = np.asarray(acc, dtype=float)
    Ts = np.asarray(periods, dtype=float)
    if np.any(Ts <= 0.0):
        raise ValueError("periods must be > 0")
    w = 2.0 * math.pi / Ts
    out = np.zeros(len(Ts))
    m_of = np.maximum(1, np.ceil(min_steps_per_period * dt / Ts)
                      ).astype(int)
    n = len(a)
    for m in sorted(set(m_of.tolist())):
        sel = m_of == m
        if m == 1:
            out[sel] = _spectrum_block(a, dt, w[sel], damping)
        else:
            tf_ = np.arange((n - 1) * m + 1) * (dt / m)
            af = np.interp(tf_, np.arange(n) * dt, a)
            out[sel] = _spectrum_block(af, dt / m, w[sel], damping)
    return out


# --------------------------------------------------------------------------- #
# spectral matching
# --------------------------------------------------------------------------- #
def _baseline(a: np.ndarray, dt: float) -> np.ndarray:
    """Remove the least-squares quartic of the displacement (through its
    second derivative) so velocity/displacement drift is suppressed."""
    n = len(a)
    t = np.arange(n) * dt
    v = np.concatenate([[0.0], np.cumsum(0.5 * (a[1:] + a[:-1]) * dt)])
    d = np.concatenate([[0.0], np.cumsum(0.5 * (v[1:] + v[:-1]) * dt)])
    tn = t / max(t[-1], dt)
    V = np.vander(tn, 5, increasing=True)[:, 2:]          # t^2, t^3, t^4
    c, *_ = np.linalg.lstsq(V, d, rcond=None)
    T = max(t[-1], dt)
    corr = (2.0 * c[0] + 6.0 * c[1] * tn + 12.0 * c[2] * tn * tn) / (T * T)
    return a - corr


def _interp_target(target: Sequence[Sequence[float]], T: np.ndarray
                   ) -> np.ndarray:
    pts = sorted((float(p[0]), float(p[1])) for p in target)
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    return np.interp(T, xs, ys)


def match_spectrum(acc: Sequence[float], dt: float,
                   target: Sequence[Sequence[float]], *,
                   damping: float = 0.05, t_min: float = 0.05,
                   t_max: float = 3.0, n_periods: int = 100,
                   max_iter: int = 40, tol: float = 0.04,
                   target_units: str = "g") -> dict:
    """Spectrally match ``acc`` (m/s^2) to ``target`` (``[[T, Sa], ...]``
    in ``target_units``; RS functions use g) — see the module docstring.

    Returns ``{"values", "dt", "periods", "target", "matched",
    "original", "max_misfit", "iterations", "source"}`` (spectra in
    m/s^2 on the log-spaced check grid)."""
    a0 = np.asarray([float(v) for v in acc], dtype=float)
    _check_len(len(a0))
    dt = _pos(dt, "dt")
    t_min = _pos(t_min, "t_min")
    t_max = _pos(t_max, "t_max")
    if t_max <= t_min:
        raise ValueError("t_max must exceed t_min")
    if not 0.0 < float(damping) < 1.0:
        raise ValueError("damping must be in (0, 1)")
    if t_min < 2.0 * dt:
        raise ValueError(f"t_min ({t_min:g} s) must be >= 2 dt "
                         f"({2 * dt:g} s) — the record cannot carry "
                         "shorter periods")
    if not target or len(target) < 2:
        raise ValueError("the target spectrum needs at least 2 points")
    n_periods = _count(n_periods, "n_periods", 5)
    max_iter = _count(max_iter, "max_iter", 1)
    if not np.any(a0):
        raise ValueError("the seed record is all zeros")
    fac = _unit_factor(target_units)
    Tg = np.exp(np.linspace(math.log(t_min), math.log(t_max), n_periods))
    tgt = _interp_target(target, Tg) * fac
    if np.any(tgt <= 0.0):
        raise ValueError("the target spectrum must be > 0 over the range")

    n = len(a0)
    nfft = 1 << int(math.ceil(math.log2(2 * n)))
    f = np.fft.rfftfreq(nfft, dt)
    fg = 1.0 / Tg[::-1]                    # ascending frequency grid
    lo, hi = math.log(fg[0]), math.log(fg[-1])
    fade = math.log(10.0) / 4.0           # quarter decade

    a = a0.copy()
    S0 = response_spectrum(a, dt, Tg, damping)
    S = S0
    misfit = float(np.max(np.abs(S / tgt - 1.0)))
    it = 0
    best = (misfit, a.copy(), S)
    while it < max_iter and misfit > tol:
        it += 1
        r = (tgt / S)[::-1]               # on ascending fg
        with np.errstate(divide="ignore"):
            lf = np.log(np.where(f > 0.0, f, fg[0]))
        rb = np.interp(lf, np.log(fg), r)
        # fade to 1 outside the range
        wlo = np.clip((lf - (lo - fade)) / fade, 0.0, 1.0)
        whi = np.clip(((hi + fade) - lf) / fade, 0.0, 1.0)
        wgt = np.minimum(wlo, whi)
        rb = 1.0 + wgt * (rb - 1.0)
        rb[0] = 1.0
        A = np.fft.rfft(a, nfft) * rb
        a = np.fft.irfft(A, nfft)[:n]
        a = _baseline(a, dt)
        S = response_spectrum(a, dt, Tg, damping)
        misfit = float(np.max(np.abs(S / tgt - 1.0)))
        if misfit < best[0]:
            best = (misfit, a.copy(), S)
    misfit, a, S = best
    src = {"kind": "matched", "method": "frequency_domain",
           "damping": float(damping), "t_min": t_min, "t_max": t_max,
           "iterations": it, "max_misfit": misfit,
           "target": [[float(p[0]), float(p[1])] for p in target],
           "target_units": target_units}
    return {"values": [float(v) for v in a], "dt": float(dt),
            "periods": Tg.tolist(), "target": tgt.tolist(),
            "matched": S.tolist(), "original": S0.tolist(),
            "max_misfit": misfit, "iterations": it, "source": src}


# --------------------------------------------------------------------------- #
# TimeHistoryFunction.source validation
# --------------------------------------------------------------------------- #
SOURCE_KINDS = ("file", "matched", "inline") + GENERATOR_KINDS


def validate_source(name: str, src) -> None:
    """``TimeHistoryFunction.source``: None or a JSON object whose
    ``kind`` is one of :data:`SOURCE_KINDS` (metadata only — the engine
    always reads ``values`` / ``dt``)."""
    if src is None:
        return
    if not isinstance(src, dict):
        raise ValueError(f"TH function {name!r}: source must be an object "
                         "or null")
    if src.get("kind") not in SOURCE_KINDS:
        raise ValueError(f"TH function {name!r}: source.kind must be one of "
                         f"{list(SOURCE_KINDS)} (got {src.get('kind')!r})")
