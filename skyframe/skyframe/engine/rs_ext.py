"""Response-spectrum completeness, engine side (ETABS parity).

CONTRACT "Response spectrum: eccentricity, U3 / angle, directional
combination, shell forces".  The model fields live in
:mod:`skyframe.core.rs_ext`; the shared engine file only carries small hooks
(``run_response_spectrum`` dispatch + eccentricity post-step,
``run_rs_directional`` CQC3 / ABS dispatch, the per-mode shell resultants
and the UZ participation entry).

Per mode ``i`` (period ``T_i``, mass-normalised shape ``phi_i``) ONE linear
static solve gives the response ``q_i`` to the load ``M phi_i`` (on large
models the condensed modal path's unit response is scaled instead).  An
excitation along the unit vector ``e`` with spectrum ``S`` then produces the
signed modal response

    r_i(e) = Gamma_i(e) * S(T_i) * scale * g * q_i,
    Gamma_i(e) = phi_i^T M iota_e / phi_i^T M phi_i,

``iota_e`` = ``(ex, ey, ez)`` on the UX/UY/UZ massed DOFs.  ``X`` (U1) acts
along ``(cos a, sin a)``, ``Y`` (U2) along ``(-sin a, cos a)`` (``a`` = the
case ``angle``), ``Z`` (U3) vertically (needs UZ mass: Mass Source
``include_vertical`` or nodal ``mz``).  Each load's modal responses are
combined with the case's modal method (every v1.13 method, rigid response
and missing mass included) into ``R_d >= 0``; the loads are then combined:

* ``SRSS``  ``R = sqrt(sum_d R_d^2)``;
* ``ABS``   ``R = max_k (R_k + sf * sum_{j != k} R_j)`` (``sf = dir_sf``;
            1 = absolute sum, 0.3 = the 100/30 rule);
* ``CQC3``  Menun & Der Kiureghian (1998) for the horizontal pair (U1 with
            spectrum S1, U2 with S2) maximised over the excitation angle
            ``theta``, U3 added by SRSS::

                R(theta)^2 = c^2 P + s^2 Q + 2 s c C (+ R_z^2)
                P = A1 + B2,  Q = B1 + A2,  C = C1 - C2
                A_k = sum rho_ij S_k,i S_k,j a_i1 a_j1
                B_k = sum rho_ij S_k,i S_k,j a_i2 a_j2
                C_k = sum rho_ij S_k,i S_k,j a_i1 a_j2
                R_max^2 = (P + Q)/2 + sqrt(((P - Q)/2)^2 + C^2) (+ R_z^2)

            with ``a_i1 = Gamma_i(U1) q_i``, ``a_i2 = Gamma_i(U2) q_i``.
            For ``S2 = gamma S1`` this is the published formula; ``S1 = S2``
            gives ``R^2 = R_U1^2 + R_U2^2`` (SRSS) for every model.

Accidental eccentricity (``ecc`` / ``ecc_overrides``)
----------------------------------------------------
Per story (and per named diaphragm) ``g`` the RS diaphragm force along each
horizontal load direction ``e_d`` is the modal combination (case method) of
the signed per-mode forces ``F_i = Gamma_i(e_d) S_d(T_i) g sum_{t in g}
m_t phi_i(t) . e_d``.  The torque is ``T_g,d = ecc_g * F_g,d * B_perp`` with
``B_perp = Lx |e_y| + Ly |e_x|`` (story / tower / diaphragm plan extents —
``Ly`` for X, ``Lx`` for Y, exactly the static ``accidental_torsion``
dimensions); several horizontal loads combine their torques by SRSS (SRSS,
CQC3) or the ABS rule.  The torque set is applied as ONE static case
(diaphragm master ``rz``; else a pure couple ``f_t = T w_t / I_p (-(y -
y_c), x - x_c)`` over the story nodes, ``w`` = lateral mass) and, since ``+T``
and ``-T`` are enveloped, every quantity becomes ``R + |R_T|`` (ETABS adds
the eccentricity results to the RS results in absolute value).  With
``ecc_ax`` the torques are amplified once by ASCE 7-16 §12.8.4.3
``Ax = (d_max / 1.2 d_avg)^2`` in [1, 3], ``d`` the combined displacement
along ``e_d`` at the two extreme story nodes perpendicular to ``e_d``.
"""

from __future__ import annotations

import math
import warnings
from typing import Dict, List, Optional, Sequence

import numpy as np

from skyframe.core import rs_ext as _crx

BASE_KEYS = ("FX", "FY", "FZ", "MX", "MY", "MZ")
ST_KEYS = ("N", "V2", "V3", "T", "M2", "M3")
AX_MAX = 3.0


# --------------------------------------------------------------------------- #
# CaseResults <-> flat vector
# --------------------------------------------------------------------------- #
class Flat:
    """Fixed quantity order of a CaseResults (template = the first one)."""

    def __init__(self, cr):
        spec = []
        for fld in ("node_disp", "reactions", "member_forces",
                    "shell_forces"):
            for k, v in getattr(cr, fld).items():
                spec.append((fld, k, None, len(v)))
        for k in BASE_KEYS:
            spec.append(("base", k, None, 1))
        for s, row in cr.story.items():
            for k in row:
                spec.append(("story", s, k, 1))
        for uid, st in cr.member_stations.items():
            for k in ST_KEYS:
                if k in st:
                    spec.append(("st", uid, k, len(st[k])))
        self.spec = spec
        self.xs = {uid: list(st.get("x", []))
                   for uid, st in cr.member_stations.items()}

    def vec(self, cr) -> np.ndarray:
        out: List[float] = []
        for fld, k, sub, n in self.spec:
            if fld == "base":
                out.append(cr.base[k])
            elif fld == "story":
                out.append(cr.story[k][sub])
            elif fld == "st":
                out.extend(cr.member_stations[k][sub])
            else:
                out.extend(getattr(cr, fld)[k])
        return np.asarray(out, dtype=float)

    def build(self, name: str, arr):
        from skyframe.engine.opensees_engine import CaseResults
        vals = [float(v) for v in arr]
        d: Dict[str, dict] = {"node_disp": {}, "reactions": {},
                              "member_forces": {}, "shell_forces": {}}
        base: Dict[str, float] = {}
        story: Dict[str, Dict[str, float]] = {}
        stations: Dict[str, Dict[str, List[float]]] = {}
        pos = 0
        for fld, k, sub, n in self.spec:
            chunk = vals[pos:pos + n]
            pos += n
            if fld == "base":
                base[k] = chunk[0]
            elif fld == "story":
                story.setdefault(k, {})[sub] = chunk[0]
            elif fld == "st":
                e = stations.get(k)
                if e is None:
                    e = stations[k] = {"x": list(self.xs.get(k, []))}
                e[sub] = chunk
            else:
                d[fld][k] = chunk
        return CaseResults(name=name, node_disp=d["node_disp"],
                           reactions=d["reactions"], base=base,
                           member_forces=d["member_forces"], story=story,
                           member_stations=stations,
                           shell_forces=d["shell_forces"])


# --------------------------------------------------------------------------- #
# modal combination on matrices (columns = quantities)
# --------------------------------------------------------------------------- #
def make_combiner(rs, omegas: Sequence[float], n_extra: int = 0):
    from skyframe.engine.modalcombo import ModalCombiner
    return ModalCombiner(rs.combo_method, omegas, zeta=rs.damping,
                         f1=rs.gmc_f1, f2=rs.gmc_f2, td=rs.dsc_td,
                         rigid_response=rs.rigid_response,
                         n_rigid_extra=n_extra)


def combine_cols(comb, V: np.ndarray,
                 extra: Optional[np.ndarray] = None) -> np.ndarray:
    """:class:`ModalCombiner` applied to every column of ``V`` (modes x
    quantities); ``extra`` = rigid extra-mode rows (missing mass)."""
    P = comb.periodic[:, None] * V
    if comb.base == "ABS":
        rp = np.abs(P).sum(axis=0)
    elif comb.base == "NRC10":
        A = np.abs(P)
        rp = np.sqrt(np.maximum((P * P).sum(axis=0)
                                + (A * (comb.mat @ A)).sum(axis=0), 0.0))
    else:
        rp = np.sqrt(np.maximum((P * (comb.mat @ P)).sum(axis=0), 0.0))
    if not comb.rigid and comb.n_extra == 0:
        return rp
    rr = comb.alpha @ V
    if extra is not None and len(extra):
        rr = rr + np.asarray(extra).sum(axis=0)
    return np.sqrt(rr * rr + rp * rp)


def dir_combine(parts: List[np.ndarray], method: str, sf: float
                ) -> np.ndarray:
    """SRSS / ABS combination of positive per-direction responses."""
    if len(parts) == 1:
        return parts[0]
    if method == "ABS":
        tot = sum(parts)
        best = None
        for p in parts:
            v = p + sf * (tot - p)
            best = v if best is None else np.maximum(best, v)
        return best
    return np.sqrt(sum(p * p for p in parts))


def cqc3(rho: np.ndarray, Q: np.ndarray, s1, s2, g1, g2,
         rz2: Optional[np.ndarray] = None) -> np.ndarray:
    """Menun & Der Kiureghian CQC3 envelope (see module docstring).
    ``Q`` modes x quantities unit responses, ``s1``/``s2`` spectral
    accelerations of U1/U2 per mode, ``g1``/``g2`` participation factors
    along U1/U2 per mode."""
    s1, s2 = np.asarray(s1, float), np.asarray(s2, float)
    g1, g2 = np.asarray(g1, float), np.asarray(g2, float)
    U = (s1 * g1)[:, None] * Q
    W = (s1 * g2)[:, None] * Q
    X = (s2 * g1)[:, None] * Q
    Y = (s2 * g2)[:, None] * Q

    def qf(a, b):
        return (a * (rho @ b)).sum(axis=0)

    P = qf(U, U) + qf(Y, Y)
    Qq = qf(W, W) + qf(X, X)
    C = qf(U, W) - qf(X, Y)
    r2 = 0.5 * (P + Qq) + np.sqrt(0.25 * (P - Qq) ** 2 + C * C)
    if rz2 is not None:
        r2 = r2 + rz2
    return np.sqrt(np.maximum(r2, 0.0))


# --------------------------------------------------------------------------- #
# loads of a case
# --------------------------------------------------------------------------- #
def case_loads(eng, rs) -> List[dict]:
    """Resolved loads ``{dir, e, points, scale, ecc, overrides}``."""
    from skyframe.engine.opensees_engine import G_ACCEL
    model = eng.model
    out = []
    for ld in _crx.effective_loads(rs):
        fn = ld.get("function") or ""
        if fn:
            f = model.spectrum_functions.get(fn)
            if f is None:
                raise ValueError(f"RS case {rs.name!r}: spectrum function "
                                 f"{fn!r} is not defined")
            pts = f.points
        else:
            pts = eng._resolve_rs_spectrum(rs)
        out.append({"dir": ld["dir"],
                    "e": _crx.dir_vector(ld["dir"], float(rs.angle)),
                    "points": pts,
                    "scale": float(rs.scale) * float(ld["scale"]) * G_ACCEL,
                    "ecc": float(rs.ecc),
                    "overrides": dict(rs.ecc_overrides or {})})
    return out


def gammas(modal, mass_map, e) -> np.ndarray:
    """Gamma_i(e) for every mode."""
    out = []
    for i in range(1, len(modal.periods) + 1):
        phi = modal.shapes[i]
        den = num = 0.0
        for (t, d), m in mass_map.items():
            v = phi[t][d - 1]
            den += m * v * v
            if d <= 3 and e[d - 1] != 0.0:
                num += m * v * e[d - 1]
        out.append(num / den if den > 0.0 else 0.0)
    return np.asarray(out, dtype=float)


def spectral(modal, ld) -> np.ndarray:
    from skyframe.engine.opensees_engine import _interp_spectrum
    return np.asarray([_interp_spectrum(ld["points"], T) * ld["scale"]
                       for T in modal.periods], dtype=float)


def _check_vertical(rs_name, loads, mass_map) -> None:
    if any(ld["dir"] == "Z" for ld in loads) and not any(
            d == 3 for (_t, d) in mass_map):
        raise ValueError(
            f"RS case {rs_name!r}: U3 (vertical) excitation needs vertical "
            "mass — set mass_options.include_vertical = true (Define > Mass "
            "Source) or add nodal mz")


def mode_units(eng, modal, mass_map) -> list:
    """Per-mode CaseResults of the load ``M phi_i`` (one solve per mode,
    or a scaled condensed unit response)."""
    from skyframe.engine.opensees_engine import _mcd_units
    asm = eng._asm
    unit = _mcd_units(eng, modal)
    if unit is not None and eng.__dict__.get("_tw"):
        unit = None
    out = []
    for i in range(1, len(modal.periods) + 1):
        phi = modal.shapes[i]
        loads = {(t, dof): m * phi[t][dof - 1]
                 for (t, dof), m in mass_map.items()}
        if unit is not None:
            out.append(eng._modal_static_scaled(
                asm, unit[i], 1.0 / unit[i]["lam"], loads))
        else:
            out.append(eng._modal_static(loads))
    return out


def missing_mass_vec(eng, flat, modal, mass_map, ld, g) -> np.ndarray:
    """Static response to the residual-mass load of one load direction."""
    from skyframe.engine.opensees_engine import _interp_spectrum
    sa0 = _interp_spectrum(ld["points"], 0.0) * ld["scale"]
    e = ld["e"]
    loads = {}
    for (t, d), m in mass_map.items():
        cap = sum(g[i - 1] * modal.shapes[i][t][d - 1]
                  for i in range(1, len(modal.periods) + 1))
        ed = e[d - 1] if d <= 3 else 0.0
        loads[(t, d)] = sa0 * m * (ed - cap)
    return flat.vec(eng._modal_static(loads))


# --------------------------------------------------------------------------- #
# extended RS case
# --------------------------------------------------------------------------- #
def run_case(eng, rs, name: str, modal):
    """Run an RS case with U3 / angle / several loads (+ eccentricity)."""
    mass_map = dict(eng._asm.mass_map)
    loads = case_loads(eng, rs)
    _check_vertical(rs.name, loads, mass_map)
    res = evaluate(eng, rs, name, modal, loads, rs.dir_combo,
                   float(rs.dir_sf))
    res.rs_info = {"loads": [{"dir": ld["dir"],
                              "vector": [float(x) for x in ld["e"]],
                              "scale": ld["scale"]} for ld in loads],
                   "dir_combo": rs.dir_combo}
    return apply_ecc(eng, rs, name, modal, res, loads=loads)


def evaluate(eng, rs, name: str, modal, loads: List[dict], dir_combo: str,
             dir_sf: float):
    mass_map = dict(eng._asm.mass_map)
    omegas = [2.0 * math.pi / T for T in modal.periods]
    units = mode_units(eng, modal, mass_map)
    flat = Flat(units[0])
    Q = np.vstack([flat.vec(u) for u in units])
    g = [gammas(modal, mass_map, ld["e"]) for ld in loads]
    sa = [spectral(modal, ld) for ld in loads]
    mm = bool(rs.include_missing_mass)
    if dir_combo == "CQC3":
        comb = make_combiner(rs, omegas)
        h = [k for k, ld in enumerate(loads) if ld["dir"] in ("X", "Y")]
        k1 = next(k for k in h if loads[k]["dir"] == "X")
        k2 = next(k for k in h if loads[k]["dir"] == "Y")
        rz2 = None
        for k, ld in enumerate(loads):
            if ld["dir"] == "Z":
                rz = combine_cols(comb, (g[k] * sa[k])[:, None] * Q)
                rz2 = rz * rz
        arr = cqc3(comb.mat, Q, sa[k1], sa[k2], g[k1], g[k2], rz2)
        return flat.build(name, arr)
    comb = make_combiner(rs, omegas, n_extra=1 if mm else 0)
    parts = []
    for k, ld in enumerate(loads):
        extra = (missing_mass_vec(eng, flat, modal, mass_map, ld,
                                  g[k])[None, :] if mm else None)
        parts.append(combine_cols(comb, (g[k] * sa[k])[:, None] * Q, extra))
    return flat.build(name, dir_combine(parts, dir_combo, dir_sf))


# --------------------------------------------------------------------------- #
# accidental eccentricity
# --------------------------------------------------------------------------- #
def torsion_groups(eng, asm) -> List[dict]:
    """Stories / named diaphragms carrying an RS story torque."""
    from skyframe.engine import towers as _tw
    model = eng.model
    lx0, ly0 = model.plan_extents()
    dia = getattr(asm, "dia", None) or {}
    named = dia.get("named", ()) or ()
    coords = asm.node_coords
    out: List[dict] = []
    for s in model.stories:
        if s.name in named:
            groups = [(d, g) for (sn, d), g in dia["groups"].items()
                      if sn == s.name]
            grouped = {t for _d, g in groups for t in g["nodes"]}
            for d, g in groups:
                x0, x1, y0, y1 = g["bbox"]
                nodes = list(g["nodes"])
                if g["master"] is not None:
                    nodes.append(g["master"])
                out.append({"label": f"{s.name}/{d}", "story": s.name,
                            "nodes": nodes, "master": g["master"],
                            "dims": (x1 - x0, y1 - y0)})
            free = [t for t in asm.story_nodes.get(s.name, ())
                    if t not in grouped]
            if free:
                xs = [coords[t][0] for t in free]
                ys = [coords[t][1] for t in free]
                out.append({"label": f"{s.name}/(free)", "story": s.name,
                            "nodes": free, "master": None,
                            "dims": (max(xs) - min(xs), max(ys) - min(ys))})
            continue
        nodes = list(asm.story_nodes.get(s.name, ()))
        master = asm.masters.get(s.name)
        if master is not None and master not in nodes:
            nodes.append(master)
        if not nodes:
            continue
        out.append({"label": s.name, "story": s.name, "nodes": nodes,
                    "master": master,
                    "dims": tuple(_tw.story_extents(eng, s.name, lx0, ly0))})
    return out


def _group_unit_forces(modal, mass_map, groups, e) -> np.ndarray:
    """modes x groups: sum_t m_t phi_i(t) . e over each group's nodes."""
    n = len(modal.periods)
    F = np.zeros((n, len(groups)))
    for j, g in enumerate(groups):
        for i in range(1, n + 1):
            phi = modal.shapes[i]
            v = 0.0
            for t in g["nodes"]:
                for d in (1, 2):
                    m = mass_map.get((t, d))
                    if m and e[d - 1] != 0.0:
                        v += m * phi[t][d - 1] * e[d - 1]
            F[i - 1, j] = v
    return F


def _torque_loads(asm, g: dict, T: float) -> Dict[tuple, float]:
    if T == 0.0:
        return {}
    if g["master"] is not None:
        return {(g["master"], 6): T}
    nodes = [t for t in g["nodes"]]
    w = [asm.mass_map.get((t, 1), 0.0) for t in nodes]
    if not any(v > 0.0 for v in w):
        w = [1.0] * len(nodes)
    ws = sum(w)
    xs = [asm.node_coords[t][0] for t in nodes]
    ys = [asm.node_coords[t][1] for t in nodes]
    xc = sum(a * b for a, b in zip(w, xs)) / ws
    yc = sum(a * b for a, b in zip(w, ys)) / ws
    ip = sum(a * ((x - xc) ** 2 + (y - yc) ** 2)
             for a, x, y in zip(w, xs, ys))
    if ip <= 1e-12:
        warnings.warn(f"RS eccentricity: {g['label']!r} has no plan spread "
                      "to carry a torque; skipped", UserWarning)
        return {}
    out: Dict[tuple, float] = {}
    for a, t, x, y in zip(w, nodes, xs, ys):
        k = T * a / ip
        if k == 0.0:
            continue
        out[(t, 1)] = out.get((t, 1), 0.0) - k * (y - yc)
        out[(t, 2)] = out.get((t, 2), 0.0) + k * (x - xc)
    return out


def _ax_factors(asm, groups, res, e) -> List[float]:
    """ASCE 7 Ax per group from the combined displacements along ``e``."""
    out = []
    for g in groups:
        nodes = [t for t in g["nodes"] if t != g["master"]
                 and t in res.node_disp]
        if len(nodes) < 2:
            out.append(1.0)
            continue
        perp = [-e[1] * asm.node_coords[t][0] + e[0] * asm.node_coords[t][1]
                for t in nodes]
        lo = nodes[int(np.argmin(perp))]
        hi = nodes[int(np.argmax(perp))]

        def dlt(t):
            u = res.node_disp[t]
            return abs(u[0] * e[0]) + abs(u[1] * e[1])

        d1, d2 = dlt(lo), dlt(hi)
        avg = 0.5 * (d1 + d2)
        if avg <= 0.0:
            out.append(1.0)
            continue
        ax = (max(d1, d2) / (1.2 * avg)) ** 2
        out.append(min(max(ax, 1.0), AX_MAX))
    return out


def apply_ecc(eng, rs, name: str, modal, res, loads=None):
    """RS result + |static response to the eccentricity torques|."""
    if not _crx.has_ecc(rs):
        return res
    if loads is None:
        loads = case_loads(eng, rs)
    return ecc_envelope(eng, rs, name, modal, res, loads, rs.dir_combo,
                        float(rs.dir_sf), bool(rs.ecc_ax))


def ecc_envelope(eng, rs, name, modal, res, loads, dir_combo, dir_sf,
                 use_ax):
    asm = eng._asm
    mass_map = dict(asm.mass_map)
    omegas = [2.0 * math.pi / T for T in modal.periods]
    groups = torsion_groups(eng, asm)
    hz = [ld for ld in loads if ld["dir"] in ("X", "Y")
          and (ld["ecc"] or any(ld["overrides"].values()))]
    if not groups or not hz:
        return res
    mm = bool(rs.include_missing_mass)
    comb = make_combiner(rs, omegas, n_extra=1 if mm else 0)
    torques = []          # per load: (forces, torques) arrays over groups
    for ld in hz:
        e = ld["e"]
        gam = gammas(modal, mass_map, e)
        sa = spectral(modal, ld)
        F = (gam * sa)[:, None] * _group_unit_forces(modal, mass_map,
                                                     groups, e)
        extra = None
        if mm:
            from skyframe.engine.opensees_engine import _interp_spectrum
            sa0 = _interp_spectrum(ld["points"], 0.0) * ld["scale"]
            row = []
            for g in groups:
                v = 0.0
                for t in g["nodes"]:
                    for d in (1, 2):
                        m = mass_map.get((t, d))
                        if not m or e[d - 1] == 0.0:
                            continue
                        cap = sum(gam[i - 1] * modal.shapes[i][t][d - 1]
                                  for i in range(1, len(modal.periods) + 1))
                        v += sa0 * m * (e[d - 1] - cap) * e[d - 1]
                row.append(v)
            extra = np.asarray([row])
        Fc = combine_cols(comb, F, extra)
        ov = ld["overrides"]          # "story/diaphragm" > "story" > ecc
        ratio = np.asarray([ov.get(g["label"], ov.get(g["story"], ld["ecc"]))
                            for g in groups], dtype=float)
        bperp = np.asarray([g["dims"][0] * abs(e[1]) + g["dims"][1]
                            * abs(e[0]) for g in groups], dtype=float)
        torques.append((Fc, ratio * Fc * bperp))
    method = "ABS" if dir_combo == "ABS" else "SRSS"

    def solve(tq):
        T = dir_combine([t for t in tq], method, dir_sf)
        tl: Dict[tuple, float] = {}
        for g, Tg in zip(groups, T):
            for k, v in _torque_loads(asm, g, float(Tg)).items():
                tl[k] = tl.get(k, 0.0) + v
        return T, eng._modal_static(tl)

    T, rt = solve([t for _f, t in torques])
    flat = Flat(res)
    base = flat.vec(res)
    out = flat.build(name, base + np.abs(flat.vec(rt)))
    ax_info = None
    if use_ax:
        ax = [_ax_factors(asm, groups, out, ld["e"]) for ld in hz]
        if any(a != 1.0 for row in ax for a in row):
            T, rt = solve([t * np.asarray(a) for (_f, t), a
                           in zip(torques, ax)])
            out = flat.build(name, base + np.abs(flat.vec(rt)))
        ax_info = {g["label"]: max(row[j] for row in ax)
                   for j, g in enumerate(groups)}
    info = dict(getattr(res, "rs_info", None) or {})
    ecc_info = {
        "ratio": float(rs.ecc),
        "forces": {g["label"]: {ld["dir"]: float(f[0][j])
                                for ld, f in zip(hz, torques)}
                   for j, g in enumerate(groups)},
        "torques": {g["label"]: float(T[j]) for j, g in enumerate(groups)},
    }
    if ax_info is not None:
        ecc_info["ax"] = ax_info
    info["ecc"] = ecc_info
    out.rs_info = info
    out.warning = res.warning
    return out


# --------------------------------------------------------------------------- #
# rs_combos CQC3 / ABS
# --------------------------------------------------------------------------- #
def directional(eng, name_x: str, name_y: str, method: str, name: str):
    """``rs_combos`` with ``method`` ABS (|Rx| + |Ry|) or CQC3."""
    rx = eng.run_response_spectrum(name_x)
    ry = eng.run_response_spectrum(name_y)
    if method == "ABS":
        flat = Flat(rx)
        return flat.build(name, np.abs(flat.vec(rx)) + np.abs(flat.vec(ry)))
    model = eng.model
    a, b = model.rs_cases[name_x], model.rs_cases[name_y]
    modal = eng.run_modal(a.num_modes if a.num_modes > 0 else None)
    la = case_loads(eng, a)[0]
    lb = case_loads(eng, b)[0]
    # CQC3 is symmetric in the pair: the X-like one is "U1"
    la = dict(la, dir="X")
    lb = dict(lb, dir="Y")
    res = evaluate(eng, a, name, modal, [la, lb], "CQC3", 1.0)
    if _crx.has_ecc(a) or _crx.has_ecc(b):
        res = ecc_envelope(eng, a, name, modal, res, [la, lb], "CQC3", 1.0,
                           bool(a.ecc_ax or b.ecc_ax))
    return res


# --------------------------------------------------------------------------- #
# UZ participation (results["modal"]["participation"])
# --------------------------------------------------------------------------- #
def wants_uz(model) -> bool:
    """UZ participation is reported when vertical mass was requested
    (Mass Source include_vertical) or an RS case excites U3."""
    try:
        if model.mass_option("include_vertical"):
            return True
    except Exception:                              # noqa: BLE001
        pass
    return any(ld["dir"] == "Z" for rs in model.rs_cases.values()
               for ld in _crx.effective_loads(rs))


def add_uz(model, asm, shapes, out: List[dict]) -> None:
    """Add ``uz`` (mass ratio) and ``gamma_z`` to each participation
    entry (no-op unless :func:`wants_uz`)."""
    if not out or not wants_uz(model):
        return
    tot = sum(m for (t, d), m in asm.mass_map.items() if d == 3)
    for entry in out:
        phi = shapes[entry["mode"]]
        den = sum(m * phi[t][d - 1] ** 2
                  for (t, d), m in asm.mass_map.items())
        num = sum(m * phi[t][2] for (t, d), m in asm.mass_map.items()
                  if d == 3)
        if den <= 0.0 or tot <= 0.0:
            entry["uz"] = 0.0
            entry["gamma_z"] = 0.0
            continue
        entry["uz"] = (num * num / den) / tot
        entry["gamma_z"] = num / den
