"""v1.16 engine side of temperature gradients, shell / joint temperatures
(CONTRACT "Temperature gradients, projected loads and auto-lateral
generators").

Frame gradients (``ThermalLoad.grad2`` / ``grad3``, deg C / m along local
2 / 3) use the existing exact fixed-end-force machinery of
``OpenSeesEngine._apply_thermal``:

* free thermal curvature: a fibre at local ``y`` strains ``alpha*grad2*y``,
  so the member bends with ``v'' = -alpha*grad2`` (x-y plane, local 2
  deflection ``v``) and ``w'' = -alpha*grad3`` (x-z plane) — it curves away
  from the hot face;
* the fully-restrained fixed-end vector holds the constant moment that
  cancels that curvature: ``Mz0 = E*I33*alpha*grad2``,
  ``My0 = E*I22*alpha*grad3``; local ``f0[5] = -Mz0, f0[11] = +Mz0,
  f0[4] = +My0, f0[10] = -My0`` (end-release condensation exactly as
  ``_condensed_fef``);
* ``f0`` is applied REVERSED as nodal loads and accumulated into the
  segment end-force correction (``_seg_fef``), so a fully fixed member
  reports end moments ``E I alpha grad`` with zero displacement and a free
  member reports zero force while curving by ``alpha*grad``;
* a ``("kappa", (0, EI33*kv, EI22*kw), 0.0)`` span record carries the free
  curvature into the v0.16 closed-form deflection stations (statics and
  consistent-load paths skip it).

Shell temperatures (:class:`skyframe.core.thermal_ext.ShellThermalLoad`)
use the boundary-traction form of the thermal equivalent load: for each
mesh element (CCW about its own normal ``n_q``), each edge A->B adds

* membrane (uniform ``dT``): ``N_th * Le/2 * n_out`` at A and B with
  ``N_th = E_m t alpha dT / (1 - nu)`` and ``n_out = t_hat x n_q``;
* bending (``grad3``): the couple ``m_th/2 * (B - A)`` at A and B with
  ``m_th = s * E_b t^3 alpha grad3 / (12 (1 - nu))`` (``s = +1`` when
  ``n_q`` agrees with the region local 3, else -1).

Interior edges cancel; the free plate then strains ``alpha dT`` / curves
``alpha grad3`` uniformly (constant-stress patch test) and a restrained
plate carries ``-N_th`` / ``-m_th``.  The linear static shell-force output
subtracts the same isotropic thermal resultants (:func:`correct_shell_
outputs`) so restrained / free plates report the physical resultants.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import openseespy.opensees as ops

from skyframe.core import thermal_ext as _cthx


def _alpha(model, mat) -> float:
    a = getattr(mat, "alpha", None)
    return a if a is not None else getattr(model, "thermal_alpha", 1.2e-5)


# ------------------------------------------------------------------ frames
def apply_frame_gradient(eng, asm, member, grad2: float,
                         grad3: float) -> None:
    """Gradient fixed-end forces of one member (already case-scaled)."""
    if grad2 == 0.0 and grad3 == 0.0:
        return
    if getattr(member, "axial_limit", "both") != "both":
        return                    # axial-only Truss members take no bending
    from skyframe.engine.opensees_engine import _local_axes, _local_stiffness
    model = eng.model
    sec = model.sections[member.section]
    mat = model.materials[sec.material]
    A_eff, I22_eff, I33_eff, J_eff = eng._eff_props(sec)
    alpha = _alpha(model, mat)
    Mz0 = mat.E * I33_eff * alpha * grad2
    My0 = mat.E * I22_eff * alpha * grad3
    xax, yax, zax, _, _ = _local_axes(member)

    def to_global(lx: float, ly: float, lz: float):
        return (lx * xax[0] + ly * yax[0] + lz * zax[0],
                lx * xax[1] + ly * yax[1] + lz * zax[1],
                lx * xax[2] + ly * yax[2] + lz * zax[2])

    segs = asm.mesh.segments[member.uid]
    last = len(segs) - 1
    toks = member.release_tokens()
    for seg in segs:
        f0 = np.zeros(12)
        f0[5], f0[11] = -Mz0, Mz0
        f0[4], f0[10] = My0, -My0
        rel: List[int] = []
        if "Mi" in toks and seg.index == 0:
            rel += [4, 5]
        if "Mj" in toks and seg.index == last:
            rel += [10, 11]
        if rel:
            K = _local_stiffness(mat.E, mat.G, A_eff, I22_eff, I33_eff,
                                 J_eff, seg.length)
            f0 = f0 - K[:, rel] @ np.linalg.solve(K[np.ix_(rel, rel)],
                                                 f0[rel])
        for node, ofs in ((seg.ni, 0), (seg.nj, 6)):
            fg = to_global(-f0[ofs], -f0[ofs + 1], -f0[ofs + 2])
            mg = to_global(-f0[ofs + 3], -f0[ofs + 4], -f0[ofs + 5])
            em = eng._ecc_node_moment(member, seg, node, fg)
            ops.load(asm.pz_load_tag.get((member.uid, node), node + 1),
                     *fg, mg[0] + em[0], mg[1] + em[1], mg[2] + em[2])
        key = (member.uid, seg.index)
        eng._seg_fef[key] = eng._seg_fef.get(key, np.zeros(12)) + f0
        # free curvature (EI * kappa) for the closed-form deflections
        eng._record(member.uid, seg.index,
                    ("kappa", (0.0, -Mz0, -My0), 0.0))


# ------------------------------------------------------------------ shells
def _shell_props(eng, region, qi: int):
    """(E_membrane, E_bending, nu, t, alpha) of one mesh element."""
    from skyframe.core.modifiers import (shell_mods_default,
                                         shell_uniform_factors)
    model = eng.model
    ssec = model.shell_sections[region.section]
    mat = model.materials[ssec.material]
    E = mat.E * ssec.mod
    scale = float(getattr(eng, "_quad_stiff_scale", {}).get(qi, 1.0))
    if scale != 1.0:
        E = E * scale
    if shell_mods_default(ssec):
        f = b = 1.0
    else:
        uni = shell_uniform_factors(ssec)
        if uni is not None:
            f, b = uni
        else:             # orthotropic modifiers: mean factors (documented)
            f = 0.5 * (getattr(ssec, "f11", 1.0) + getattr(ssec, "f22", 1.0))
            b = 0.5 * (getattr(ssec, "m11", 1.0) + getattr(ssec, "m22", 1.0))
    return (E * f, E * b, mat.nu, ssec.total_thickness, _alpha(model, mat))


def _quad_resultants(eng, asm, loads: Dict[str, List[float]]):
    """Yield (qi, node tags, coords, n_q, N_th, m_th) for loaded elements;
    ``loads`` maps region uid -> [dT, grad3] (case-scaled sums)."""
    from skyframe.core.loads_ext import shell_local_axes
    regions = {r.uid: r for r in eng.model.shells}
    e3_cache = {}
    for qi, sq in enumerate(asm.shell_quads):
        rid = sq["region"]
        if rid not in loads:
            continue
        dT, grad = loads[rid]
        region = regions[rid]
        if rid not in e3_cache:
            e3_cache[rid] = np.asarray(shell_local_axes(region)[2])
        tags = list(sq["nodes"])
        xyz = [np.asarray(asm.node_coords[t], dtype=float) for t in tags]
        if len(xyz) == 4:
            nq = np.cross(xyz[2] - xyz[0], xyz[3] - xyz[1])
        else:
            nq = np.cross(xyz[1] - xyz[0], xyz[2] - xyz[0])
        nq = nq / np.linalg.norm(nq)
        s = 1.0 if float(nq @ e3_cache[rid]) >= 0.0 else -1.0
        Em, Eb, nu, t, alpha = _shell_props(eng, region, qi)
        N_th = Em * t * alpha * dT / (1.0 - nu)
        m_th = s * Eb * t ** 3 * alpha * grad / (12.0 * (1.0 - nu))
        yield qi, tags, xyz, nq, N_th, m_th


def _shell_loads(pats) -> Dict[str, List[float]]:
    out: Dict[str, List[float]] = {}
    for pat, scale in pats:
        for st in getattr(pat, "shell_thermal_loads", ()):
            acc = out.setdefault(st.region_uid, [0.0, 0.0])
            acc[0] += st.dT * scale
            acc[1] += st.grad3 * scale
    return {k: v for k, v in out.items() if v[0] != 0.0 or v[1] != 0.0}


def apply_shell_thermal(eng, asm, pat, scale: float) -> None:
    loads = _shell_loads([(pat, scale)])
    if not loads:
        return
    nodal: Dict[int, np.ndarray] = {}
    for qi, tags, xyz, nq, N_th, m_th in _quad_resultants(eng, asm, loads):
        n = len(tags)
        for k in range(n):
            a, b = k, (k + 1) % n
            edge = xyz[b] - xyz[a]
            Le = float(np.linalg.norm(edge))
            if Le <= 0.0:
                continue
            n_out = np.cross(edge / Le, nq)
            f = 0.5 * N_th * Le * n_out
            m = 0.5 * m_th * edge
            for tg in (tags[a], tags[b]):
                v = nodal.setdefault(tg, np.zeros(6))
                v[:3] += f
                v[3:] += m
    for tg in sorted(nodal):
        ops.load(tg, *[float(x) for x in nodal[tg]])


def correct_shell_outputs(eng, asm, patterns: Dict[str, float],
                          shell_forces: Dict[int, List[float]]
                          ) -> Dict[int, List[float]]:
    """Subtract the isotropic thermal resultants from the element output
    (Nxx, Nyy -= N_th; Mxx, Myy -= m_th) — linear static cases."""
    model = eng.model
    pats = [(model.patterns[p], s) for p, s in patterns.items()
            if p in model.patterns]
    loads = _shell_loads(pats)
    if not loads:
        return shell_forces
    for qi, tags, xyz, nq, N_th, m_th in _quad_resultants(eng, asm, loads):
        v = shell_forces.get(qi)
        if v is None:
            continue
        v = list(v)
        v[0] -= N_th
        v[1] -= N_th
        v[3] -= m_th
        v[4] -= m_th
        shell_forces[qi] = v
    return shell_forces


# ------------------------------------------------------------- per pattern
def apply_pattern_ext(eng, asm, pat, scale: float) -> None:
    """All v1.16 temperature loads of one pattern (no-op for legacy
    patterns: no gradient, no shell / joint temperatures)."""
    for tl in pat.thermal_loads:
        g2 = getattr(tl, "grad2", 0.0)
        g3 = getattr(tl, "grad3", 0.0)
        if g2 or g3:
            member = eng._members_by_uid[tl.member_uid]
            apply_frame_gradient(eng, asm, member, g2 * scale, g3 * scale)
    if getattr(pat, "joint_temperatures", None):
        for member in eng.model.members:
            t_avg = _cthx.member_joint_temperature(pat, member)
            if t_avg:
                eng._apply_thermal(asm, member, t_avg * scale)
    if getattr(pat, "shell_thermal_loads", None):
        apply_shell_thermal(eng, asm, pat, scale)
