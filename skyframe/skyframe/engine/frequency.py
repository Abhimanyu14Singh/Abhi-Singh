"""Frequency-domain analysis: Steady-State and Power Spectral Density.

Method (both case types share one modal "basis"):

1. Eigen solve of the elastic model (``OpenSeesEngine.run_modal``; rigid
   diaphragms, springs, links, shells — whatever the standard build holds).
   The retained shapes are mass-normalised (``phi^T M phi = 1``, modified
   Gram-Schmidt against the diagonal mass map exactly like FNA, which
   repairs non-orthogonal vectors inside degenerate clusters).
2. Per mode ``i`` the response FIELD ``Phi_i`` (joint displacements, support
   reactions incl. spring reactions, base totals, story displacements and
   drifts) is obtained from ONE linear static solve under
   ``f = w_i^2 M phi_i`` (displacements = ``phi_i``; reactions = the elastic
   support forces that equilibrate the mode shape).
3. Modal load factors: for a pattern load ``p_ij = phi_i^T F_j``, computed
   exactly as ``w_i^2 phi_i^T M u_j`` from the pattern's static
   displacement ``u_j = K^-1 F_j`` (K symmetric, ``K phi = w^2 M phi``; holds
   in the constrained/reduced coordinates because full-node values are the
   transformed reduced ones).  For a ground acceleration in direction d:
   ``p_ij = -phi_i^T M r_d`` (``r_d`` = unit rigid-body translation), the
   response is RELATIVE to the ground.
4. Response at circular frequency ``w`` (complex amplitude):
   ``Z(w) = sum_j a_j(f) [ sum_i p_ij H_i(w) Phi_i  +  h_res R_j ]`` with
   ``H_i = 1/(w_i^2 - w^2 + 2 i zeta w_i w)`` (modal viscous) or
   ``1/(w_i^2 (1 + 2 i zeta) - w^2)`` (hysteretic, complex stiffness
   ``K(1 + 2 i zeta)``), ``h_res = 1`` (viscous) or ``1/(1 + 2 i zeta)``.
   ``R_j`` (method "direct" only) is the static residual field
   ``static_j - sum_i (p_ij / w_i^2) Phi_i``; with ALL eigenmodes it lies in
   the massless (condensed) subspace, so modal + residual is EXACTLY the
   complex direct solution of the condensed system.  Method "modal" uses
   ``num_modes`` modes and no residual (classic modal superposition).
5. Steady state: ``Z`` reported as amplitude ``|Z|`` and phase
   ``atan2(Im Z, Re Z)`` in degrees (a response LAGGING the load has a
   negative phase).  PSD: ``a_j = scale_j sqrt(S_j(f)) e^{i phase_j}``
   (fully correlated loads), response PSD ``|Z|^2`` and RMS
   ``sqrt(trapz(|Z|^2, f))``.

Reactions are the ELASTIC support forces of the displacement field (the
same convention as the time-history base series — no damping share).
All results are linear; nonlinear members/links act with their initial
(elastic) stiffness.  Units: kN, m, tonne, s; frequencies in Hz.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import openseespy.opensees as ops

from skyframe.core.frequency_cases import (FREQ_MAX_POINTS, PSDCase,
                                           SteadyStateCase, interp_function)

FREQ_DIRECT_MODE_CAP = 400          # "direct" = all modes; dense eigen cap
_REFINE_K = (0.1, 0.2, 0.3, 0.45, 0.6, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0,
             4.0, 5.0, 6.5, 8.0, 10.0, 13.0)
_BASE_KEYS = ("FX", "FY", "FZ", "MX", "MY", "MZ")
_STORY_KEYS = ("ux", "uy", "drift_x", "drift_y")
_ACCEL_DOF = {"UX": 1, "UY": 2, "UZ": 3}
_CHUNK = 256                        # frequencies evaluated per block


# --------------------------------------------------------------------------- #
# pure numerics
# --------------------------------------------------------------------------- #
def modal_frf(omegas: np.ndarray, w: np.ndarray, zeta: float,
              damping_type: str = "modal") -> np.ndarray:
    """Modal receptances ``H[k, i]`` at circular frequencies ``w[k]``."""
    om = np.asarray(omegas, dtype=float)[None, :]
    ww = np.asarray(w, dtype=float)[:, None]
    if damping_type == "hysteretic":
        return 1.0 / (om * om * (1.0 + 2.0j * zeta) - ww * ww)
    return 1.0 / (om * om - ww * ww + 2.0j * zeta * om * ww)


def frequency_grid(case, modal_freqs_hz: Sequence[float]) -> np.ndarray:
    """The case's analysis frequencies (Hz), sorted and de-duplicated."""
    f0, f1 = float(case.freq_start_hz), float(case.freq_end_hz)
    pts: List[float] = []
    if case.n_freq == 1:
        pts.append(f0)
    elif case.n_freq > 1:
        pts.extend(np.linspace(f0, f1, int(case.n_freq)).tolist())
    pts.extend(float(f) for f in case.frequencies)
    if case.modal_refine:
        z = float(case.damping)
        for fi in modal_freqs_hz:
            if not f0 <= fi <= f1:
                continue
            for k in (0.0,) + _REFINE_K + tuple(-k for k in _REFINE_K):
                f = fi * (1.0 + k * z)
                if f0 <= f <= f1:
                    pts.append(f)
    arr = np.unique(np.asarray(pts, dtype=float))
    if arr.size > 1:                      # drop numerically equal neighbours
        keep = np.concatenate(([True], np.diff(arr) >
                               1e-12 * np.maximum(1.0, arr[1:])))
        arr = arr[keep]
    if arr.size > FREQ_MAX_POINTS:
        raise ValueError(f"frequency grid of {arr.size} points exceeds the "
                         f"{FREQ_MAX_POINTS}-point cap")
    return arr


def _trapz_weights(f: np.ndarray) -> np.ndarray:
    """Trapezoid-rule weights so that ``sum(w * y) == trapz(y, f)``."""
    n = f.size
    wts = np.zeros(n)
    if n < 2:
        return wts
    d = np.diff(f)
    wts[:-1] += 0.5 * d
    wts[1:] += 0.5 * d
    return wts


# --------------------------------------------------------------------------- #
# response-field layout
# --------------------------------------------------------------------------- #
class _Layout:
    """Flattened response vector: joints x 6 | supports x 6 | base 6 |
    stories x (ux, uy, drift_x, drift_y)."""

    def __init__(self, eng, asm, support_tags: List[int]):
        self.eng = eng
        self.asm = asm
        self.node_tags: List[int] = sorted(asm.node_coords)
        self.node_index = {t: k for k, t in enumerate(self.node_tags)}
        self.support_tags = list(support_tags)
        self.stories = [s for s in eng.model.stories]
        self.n_nodes = len(self.node_tags)
        self.o_react = 6 * self.n_nodes
        self.o_base = self.o_react + 6 * len(self.support_tags)
        self.o_story = self.o_base + 6
        self.size = self.o_story + 4 * len(self.stories)

    def vector(self, node_disp: Dict[int, List[float]],
               reactions: Dict[int, List[float]]) -> np.ndarray:
        eng, asm = self.eng, self.asm
        v = np.zeros(self.size)
        for t, k in self.node_index.items():
            v[6 * k:6 * k + 6] = node_disp[t]
        for k, t in enumerate(self.support_tags):
            v[self.o_react + 6 * k:self.o_react + 6 * k + 6] = \
                reactions.get(t, [0.0] * 6)
        base = eng._base_totals(asm, reactions)
        v[self.o_base:self.o_base + 6] = [base[k] for k in _BASE_KEYS]
        prev_ux = prev_uy = 0.0
        for k, s in enumerate(self.stories):     # same rule as RS statics
            if s.name in asm.masters:
                d = node_disp[asm.masters[s.name]]
                ux, uy = d[0], d[1]
            else:
                nodes = asm.story_nodes.get(s.name, [])
                ux = (sum(node_disp[t][0] for t in nodes) / len(nodes)
                      if nodes else 0.0)
                uy = (sum(node_disp[t][1] for t in nodes) / len(nodes)
                      if nodes else 0.0)
            h = s.height if s.height > 0 else 1.0
            o = self.o_story + 4 * k
            v[o:o + 4] = [ux, uy, (ux - prev_ux) / h, (uy - prev_uy) / h]
            prev_ux, prev_uy = ux, uy
        return v


def _static_solve(eng, asm, apply) -> Tuple[Dict[int, List[float]],
                                            Dict[int, List[float]]]:
    """One linear static solve on the live elastic domain; reverts it."""
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    apply()
    eng._setup_analysis(asm)
    try:
        if ops.analyze(1) != 0:
            raise RuntimeError("frequency-domain static solve failed")
        node_disp = {t: list(ops.nodeDisp(t)) for t in asm.node_coords}
        ops.reactions()
        reactions = {t: list(ops.nodeReaction(t)) for t in asm.support_tags}
        eng._add_spring_reactions(asm, node_disp, reactions)
    finally:
        ops.remove("loadPattern", 1)
        ops.remove("timeSeries", 1)
        ops.reset()
    return node_disp, reactions


def _nodal_apply(loads: Dict[Tuple[int, int], float]):
    def apply() -> None:
        for (t, dof), v in loads.items():
            if v == 0.0:
                continue
            vec = [0.0] * 6
            vec[dof - 1] = v
            ops.load(t, *vec)
    return apply


# --------------------------------------------------------------------------- #
# modal basis
# --------------------------------------------------------------------------- #
class _Basis:
    """Everything frequency-independent for one case."""

    layout: _Layout
    omegas: np.ndarray            # (nm,)
    Phi: np.ndarray               # (nm, Q) per-mode response fields
    P: np.ndarray                 # (nm, nl) modal load factors
    R: Optional[np.ndarray]       # (nl, Q) static residual fields (direct)
    modal_freqs_hz: List[float]
    warnings: List[str]


def _build_basis(eng, case) -> _Basis:
    model = eng.model
    if case.method == "direct":
        asm0 = eng._build()
        n_massed = asm0.free_massed_dofs()
        if n_massed > FREQ_DIRECT_MODE_CAP:
            raise ValueError(
                f"case {case.name!r}: method 'direct' needs all {n_massed} "
                f"eigenmodes (cap {FREQ_DIRECT_MODE_CAP}); use method "
                "'modal'")
        modal = eng.run_modal(max(n_massed, 1))
    else:
        modal = eng.run_modal(case.num_modes if case.num_modes > 0 else None)
    if not modal.periods:
        raise RuntimeError(f"case {case.name!r}: the model has no dynamic "
                           "modes (no mass on unrestrained DOFs)")
    asm = eng._asm                    # live domain of the eigen build
    nm = len(modal.periods)
    omegas = np.array([2.0 * math.pi / T for T in modal.periods])

    tags = sorted(asm.node_coords)
    idx = {t: k for k, t in enumerate(tags)}
    nflat = 6 * len(tags)
    mvec = np.zeros(nflat)
    for (t, d), m in asm.mass_map.items():
        mvec[6 * idx[t] + d - 1] += m
    S = np.zeros((nm, nflat))
    for i in range(nm):
        sh = modal.shapes[i + 1]
        for t, k in idx.items():
            S[i, 6 * k:6 * k + 6] = sh[t]
    for i in range(nm):                     # M-orthonormalise (MGS)
        for j in range(i):
            c = float((mvec * S[i]) @ S[j])
            if c != 0.0:
                S[i] -= c * S[j]
        den = float((mvec * S[i]) @ S[i])
        S[i] *= (1.0 / math.sqrt(den)) if den > 0.0 else 0.0

    # first solve fixes the reaction key set (supports + spring nodes)
    def mode_loads(i: int) -> Dict[Tuple[int, int], float]:
        w2 = omegas[i] ** 2
        return {(t, d): w2 * m * S[i, 6 * idx[t] + d - 1]
                for (t, d), m in asm.mass_map.items()}

    nd0, re0 = _static_solve(eng, asm, _nodal_apply(mode_loads(0)))
    layout = _Layout(eng, asm, sorted(re0))
    Phi = np.zeros((nm, layout.size))
    Phi[0] = layout.vector(nd0, re0)
    for i in range(1, nm):
        nd, re = _static_solve(eng, asm, _nodal_apply(mode_loads(i)))
        Phi[i] = layout.vector(nd, re)

    # modal load factors (+ static fields for the residual)
    direct = case.method == "direct"
    nl = len(case.loads)
    P = np.zeros((nm, nl))
    R = np.zeros((nl, layout.size)) if direct else None
    static_cache: Dict[str, np.ndarray] = {}
    for j, ld in enumerate(case.loads):
        key = f"accel:{ld.direction}" if ld.is_accel else f"pat:{ld.pattern}"
        if ld.is_accel:
            dof = _ACCEL_DOF[ld.direction]
            mdir = np.zeros(nflat)
            mdir[dof - 1::6] = mvec[dof - 1::6]
            P[:, j] = -(S @ mdir)
            if direct and key not in static_cache:
                loads = {(t, d): -m for (t, d), m in asm.mass_map.items()
                         if d == dof}
                nd, re = _static_solve(eng, asm, _nodal_apply(loads))
                static_cache[key] = layout.vector(nd, re)
        else:
            if key not in static_cache:
                def apply(name=ld.pattern) -> None:
                    eng._seg_span_loads = {}
                    eng._seg_fef = {}
                    eng._apply_pattern(asm, name, 1.0)
                nd, re = _static_solve(eng, asm, apply)
                static_cache[key] = layout.vector(nd, re)
            u = static_cache[key][:nflat]
            P[:, j] = omegas ** 2 * (S @ (mvec * u))
        if direct:
            R[j] = static_cache[key] - (P[:, j] / omegas ** 2) @ Phi

    b = _Basis()
    b.layout = layout
    b.omegas = omegas
    b.Phi = Phi
    b.P = P
    b.R = R
    b.modal_freqs_hz = [float(o / (2.0 * math.pi)) for o in omegas]
    b.warnings = []
    return b


def _load_amplitudes(model, case, f: np.ndarray, psd: bool) -> np.ndarray:
    """``A[k, j]`` complex input amplitude of load j at frequency f[k]."""
    A = np.zeros((f.size, len(case.loads)), dtype=complex)
    for j, ld in enumerate(case.loads):
        fn = (model.frequency_functions.get(ld.function)
              if ld.function else None)
        g = interp_function(fn, f)
        if psd:
            g = np.sqrt(np.maximum(g, 0.0))
        A[:, j] = (ld.scale * g) * np.exp(1j * math.radians(ld.phase_deg))
    return A


def _response_block(b: _Basis, case, A: np.ndarray, f: np.ndarray,
                    cols: Optional[np.ndarray] = None) -> np.ndarray:
    """Complex response ``Z[k, q]`` for one frequency block."""
    w = 2.0 * math.pi * f
    H = modal_frf(b.omegas, w, case.damping, case.damping_type)
    Phi = b.Phi if cols is None else b.Phi[:, cols]
    Z = (H * (A @ b.P.T)) @ Phi
    if b.R is not None:
        R = b.R if cols is None else b.R[:, cols]
        hres = (1.0 / (1.0 + 2.0j * case.damping)
                if case.damping_type == "hysteretic" else 1.0)
        Z = Z + (A * hres) @ R
    return Z


# --------------------------------------------------------------------------- #
# output helpers
# --------------------------------------------------------------------------- #
def _output_nodes(eng, layout: _Layout, case, default_all: bool) -> List[int]:
    if case.output_points:
        return [eng._find_node(layout.asm, tuple(p))
                for p in case.output_points]
    return list(layout.node_tags) if default_all else []


def _node_cols(layout: _Layout, tags: List[int]) -> np.ndarray:
    return np.array([6 * layout.node_index[t] + d for t in tags
                     for d in range(6)], dtype=int)


def _amp_phase(z: np.ndarray) -> Tuple[List[float], List[float]]:
    amp = np.abs(z)
    ph = np.where(amp > 0.0, np.degrees(np.angle(z)), 0.0)
    return [float(v) for v in amp], [float(v) for v in ph]


def _header(case, b: _Basis, f: np.ndarray, kind: str) -> dict:
    return {"case": case.name, "type": kind, "method": case.method,
            "damping": {"type": case.damping_type,
                        "ratio": float(case.damping)},
            "modes_used": int(len(b.omegas)),
            "modal_frequencies_hz": list(b.modal_freqs_hz),
            "frequencies_hz": [float(v) for v in f]}


def _cache(eng) -> Dict[Tuple[str, str], dict]:
    return eng.__dict__.setdefault("_freq_cache", {})


# --------------------------------------------------------------------------- #
# public entry points
# --------------------------------------------------------------------------- #
def run_steady_state(eng, name: str) -> dict:
    """Run one steady-state case on an ``OpenSeesEngine`` (cached)."""
    cache = _cache(eng)
    if ("ss", name) in cache:
        return cache[("ss", name)]
    model = eng.model
    case = getattr(model, "steady_state_cases", {}).get(name)
    if case is None:
        raise ValueError(f"Unknown steady-state case {name!r}")
    b = _build_basis(eng, case)
    lay = b.layout
    f = frequency_grid(case, b.modal_freqs_hz)
    A = _load_amplitudes(model, case, f, psd=False)
    out_tags = _output_nodes(eng, lay, case, default_all=True)
    sel = np.concatenate([_node_cols(lay, out_tags),
                          np.arange(lay.o_base, lay.size)]).astype(int)

    nq = lay.size
    peak = np.zeros(nq)
    peak_f = np.zeros(nq)
    sel_blocks: List[np.ndarray] = []
    for k0 in range(0, f.size, _CHUNK):
        fb = f[k0:k0 + _CHUNK]
        Z = _response_block(b, case, A[k0:k0 + _CHUNK], fb)
        amp = np.abs(Z)
        kmax = np.argmax(amp, axis=0)
        amax = amp[kmax, np.arange(nq)]
        better = amax > peak
        peak = np.where(better, amax, peak)
        peak_f = np.where(better, fb[kmax], peak_f)
        sel_blocks.append(Z[:, sel])
    Zs = np.vstack(sel_blocks) if sel_blocks else np.zeros((0, sel.size))

    res = _header(case, b, f, "steady_state")
    nsel = 6 * len(out_tags)
    node_disp: Dict[str, dict] = {}
    for n, t in enumerate(out_tags):
        blk = Zs[:, 6 * n:6 * n + 6]
        amp = np.abs(blk)
        ph = np.where(amp > 0.0, np.degrees(np.angle(blk)), 0.0)
        node_disp[str(t)] = {"amp": amp.tolist(), "phase_deg": ph.tolist()}
    res["node_disp"] = node_disp
    res["base"] = {}
    for k, key in enumerate(_BASE_KEYS):
        a, p = _amp_phase(Zs[:, nsel + k])
        res["base"][key] = {"amp": a, "phase_deg": p}
    res["story"] = {}
    for si, s in enumerate(lay.stories):
        ent = {}
        for k, key in enumerate(_STORY_KEYS):
            a, p = _amp_phase(Zs[:, nsel + 6 + 4 * si + k])
            ent[key] = {"amp": a, "phase_deg": p}
        res["story"][s.name] = ent
    res["peaks"] = {
        "node_disp": {str(t): peak[6 * k:6 * k + 6].tolist()
                      for k, t in enumerate(lay.node_tags)},
        "node_disp_freq_hz": {str(t): peak_f[6 * k:6 * k + 6].tolist()
                              for k, t in enumerate(lay.node_tags)},
        "reactions": {str(t): peak[lay.o_react + 6 * k:
                                   lay.o_react + 6 * k + 6].tolist()
                      for k, t in enumerate(lay.support_tags)},
        "base": {key: float(peak[lay.o_base + k])
                 for k, key in enumerate(_BASE_KEYS)},
        "base_freq_hz": {key: float(peak_f[lay.o_base + k])
                         for k, key in enumerate(_BASE_KEYS)},
        "story": {s.name: {key: float(peak[lay.o_story + 4 * si + k])
                           for k, key in enumerate(_STORY_KEYS)}
                  for si, s in enumerate(lay.stories)},
    }
    res["warnings"] = list(b.warnings)
    cache[("ss", name)] = res
    return res


def run_psd(eng, name: str) -> dict:
    """Run one power-spectral-density case on an ``OpenSeesEngine``."""
    cache = _cache(eng)
    if ("psd", name) in cache:
        return cache[("psd", name)]
    model = eng.model
    case = getattr(model, "psd_cases", {}).get(name)
    if case is None:
        raise ValueError(f"Unknown PSD case {name!r}")
    b = _build_basis(eng, case)
    lay = b.layout
    f = frequency_grid(case, b.modal_freqs_hz)
    A = _load_amplitudes(model, case, f, psd=True)
    wts = _trapz_weights(f)
    out_tags = _output_nodes(eng, lay, case, default_all=False)
    sel = np.concatenate([_node_cols(lay, out_tags),
                          np.arange(lay.o_base, lay.size)]).astype(int)

    var = np.zeros(lay.size)
    sel_blocks: List[np.ndarray] = []
    for k0 in range(0, f.size, _CHUNK):
        Z = _response_block(b, case, A[k0:k0 + _CHUNK], f[k0:k0 + _CHUNK])
        S = (Z * Z.conj()).real                 # response PSD |Z|^2
        var += wts[k0:k0 + _CHUNK] @ S
        sel_blocks.append(S[:, sel])
    Ss = np.vstack(sel_blocks) if sel_blocks else np.zeros((0, sel.size))
    rms = np.sqrt(np.maximum(var, 0.0))

    res = _header(case, b, f, "psd")
    res["correlation"] = "full"
    res["rms"] = {
        "node_disp": {str(t): rms[6 * k:6 * k + 6].tolist()
                      for k, t in enumerate(lay.node_tags)},
        "reactions": {str(t): rms[lay.o_react + 6 * k:
                                  lay.o_react + 6 * k + 6].tolist()
                      for k, t in enumerate(lay.support_tags)},
        "base": {key: float(rms[lay.o_base + k])
                 for k, key in enumerate(_BASE_KEYS)},
        "story": {s.name: {key: float(rms[lay.o_story + 4 * si + k])
                           for k, key in enumerate(_STORY_KEYS)}
                  for si, s in enumerate(lay.stories)},
    }
    nsel = 6 * len(out_tags)
    res["psd"] = {
        "node_disp": {str(t): Ss[:, 6 * n:6 * n + 6].tolist()
                      for n, t in enumerate(out_tags)},
        "base": {key: Ss[:, nsel + k].tolist()
                 for k, key in enumerate(_BASE_KEYS)},
        "story": {s.name: {key: Ss[:, nsel + 6 + 4 * si + k].tolist()
                           for k, key in enumerate(_STORY_KEYS)}
                  for si, s in enumerate(lay.stories)},
    }
    res["warnings"] = list(b.warnings)
    cache[("psd", name)] = res
    return res


__all__ = ["run_steady_state", "run_psd", "modal_frf", "frequency_grid",
           "SteadyStateCase", "PSDCase", "FREQ_DIRECT_MODE_CAP"]
