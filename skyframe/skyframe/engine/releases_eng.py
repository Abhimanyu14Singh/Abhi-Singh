"""Frame end releases / partial fixity and joint local axes (engine side).

CONTRACT "Frame end releases, partial fixity and joint local axes"; the
model side is :mod:`skyframe.core.releases`.

Two mechanisms, chosen per member END:

* **native** -- the end carries only FULL M2 and/or M3 releases (no
  spring) and the member is an Euler ``elasticBeamColumn``: the element's
  own ``-releasey`` (M2) / ``-releasez`` (M3) codes condense the end
  exactly (static condensation inside the element).  The legacy Mi/Mj pin
  is exactly this path, with the identical argument list, so legacy models
  build the identical domain (byte-identical results).  Exact fixed-end
  forces condense the same DOFs (:func:`fef_released`).
* **spring** -- anything else (P, V2, V3, T, a partial-fixity spring, or
  any release on an ``ElasticTimoshenkoBeam``): the element end connects to
  a co-located DUPLICATE node, joined to the joint by a ``zeroLength``
  element oriented on the member local axes (``-orient x, local 2``).  Per
  local DOF: a released DOF gets its partial-fixity spring ``k`` (no
  material when ``k == 0``: fully free); a continuous DOF gets a stiff tie
  ``RELEASE_TIE_FACTOR`` (1e6) x the member's OWN stiffness in that DOF
  (EA/L, 12EI/L^3, GJ/L, 4EI/L).  Measured trade-off (quick building,
  spring path vs the exact element release): 1e6 softens a released end
  by ~1e-6 of its own stiffness while round-off stays ~1e-8 (base-shear
  equilibrium 5e-9); 1e8 cuts the softening but round-off grows to ~3e-6
  (stiff ties between free nodes), so 1e6 is the robust choice.  The
  element itself is unreleased, so every load
  path (beamUniform / beamPoint / exact FEF / thermal) uses the
  unreleased element FEF and redirects its end nodal loads to the
  duplicate (``asm.pz_load_tag``), and the global solve performs the
  condensation -- exact for every release combination, on Euler,
  Timoshenko, auto-meshed, curved (chord), nonprismatic and P-Delta /
  Corotational builds alike.

Unstable release combinations (core.releases.unstable) are warned
(``UserWarning``, ETABS-style) and kept regular with a tiny residual
stiffness ``RELEASE_RESIDUAL_RATIO x`` the end stiffness on the fully
released DOFs taking part in the mechanism.

Joint local axes: an explicit restraint group (translations UX..UZ or
rotations RX..RZ) whose three flags are equal is axis-independent and stays
an exact ``ops.fix``; a MIXED group at a joint with local axes becomes a
grounded stiff spring on the restrained LOCAL DOFs (penalty
``SKEW_PENALTY_FACTOR x`` the model's largest member end stiffness) that
rides the named-spring machinery (``-orient`` zeroLength, exact global
reactions ``-R^T k u_local``).  Point springs at such a joint are
re-oriented into the joint axes.
"""

from __future__ import annotations

import warnings
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import openseespy.opensees as ops

from skyframe.core import releases as _rel

RELEASE_TIE_FACTOR = 1.0e6
RELEASE_RESIDUAL_RATIO = 1.0e-6
SKEW_PENALTY_FACTOR = 1.0e8

_DIR = {"P": 1, "V2": 2, "V3": 3, "T": 4, "M2": 5, "M3": 6}

#: verification switch (tests): build EVERY released end on the spring
#: path, so it can be compared with the exact element release
FORCE_SPRING_PATH = False


# ------------------------------------------------------------ planning
def plan(m, timo: bool) -> Optional[dict]:
    """{"i": (mode, eff_end), "j": (...)} or None (no releases)."""
    if not _rel.has_any(m):
        return None
    eff = _rel.effective(m)
    out = {}
    for end in _rel.ENDS:
        e = eff[end]
        if not e:
            out[end] = ("none", e)
        elif _rel.is_native_end(e) and not timo and not FORCE_SPRING_PATH:
            out[end] = ("native", e)
        else:
            out[end] = ("spring", e)
    return out


def native_codes(p: Optional[dict], seg_index: int, last: int
                 ) -> Tuple[int, int]:
    """(code_z for M3, code_y for M2) elasticBeamColumn release codes."""
    if not p:
        return 0, 0
    cz = cy = 0
    for end, bit, here in (("i", 1, seg_index == 0),
                           ("j", 2, seg_index == last)):
        mode, e = p[end]
        if here and mode == "native":
            if "M3" in e:
                cz += bit
            if "M2" in e:
                cy += bit
    return cz, cy


def release_args(cz: int, cy: int) -> list:
    """Element args; the legacy pin (cz == cy) keeps the legacy order."""
    out: list = []
    if cz:
        out += ["-releasez", cz]
    if cy:
        out += ["-releasey", cy]
    return out


def fef_released(m, seg_index: int, last: int, timo: bool) -> List[int]:
    """Local 12-vector indices condensed out of the fixed-end forces of
    segment ``seg_index`` (native ends only; spring ends use the
    unreleased element FEF).  Legacy pins give [4, 5] / [10, 11]."""
    toks = m.release_tokens()
    if not getattr(m, "end_releases", None):
        rel: List[int] = []                         # legacy (identical)
        if "Mi" in toks and seg_index == 0:
            rel += [4, 5]
        if "Mj" in toks and seg_index == last:
            rel += [10, 11]
        return rel
    p = plan(m, timo)
    rel = []
    for end, off, here in (("i", 0, seg_index == 0),
                           ("j", 6, seg_index == last)):
        mode, e = p[end]
        if here and mode == "native":
            for d in ("M2", "M3"):
                if d in e:
                    rel.append(_rel.DOF_INDEX[d] + off)
    return rel


def rot_presence(p: Optional[dict], end: str, here: bool, axes,
                 torsion_only, eye3) -> Tuple[np.ndarray, bool]:
    """(3x3 rotational-stiffness pattern contributed to the JOINT,
    whether a rotation is fully released there) for the auto-restraint of
    rotations left unstiffened by releases."""
    if not p or not here:
        return eye3, False
    mode, e = p[end]
    if mode == "none":
        return eye3, False
    xax, yax, zax = axes
    rot_axis = {"T": xax, "M2": yax, "M3": zax}
    freed = [d for d in ("T", "M2", "M3") if e.get(d, None) == 0.0]
    if not freed:
        return eye3, False
    if set(freed) == {"M2", "M3"}:
        return torsion_only, True                  # the legacy pin
    S = np.zeros((3, 3))
    for d in ("T", "M2", "M3"):
        if d not in freed:
            a = np.asarray(rot_axis[d], dtype=float)
            S += np.outer(a, a)
    return S, True


def warn_unstable(m) -> None:
    msgs = _rel.unstable(m)
    if msgs:
        warnings.warn(f"Frame {m.uid}: unstable end releases ("
                      + "; ".join(msgs) + ") - the member is a mechanism; "
                      "a tiny residual stiffness keeps the solve regular "
                      "(ETABS warns likewise)", UserWarning)


# ------------------------------------------------------------ spring ends
def _end_stiffness(E: float, G: float, props, L: float) -> Tuple[float,
                                                                   float]:
    A, I22, I33, J = props
    Imax = max(I22, I33)
    kt = E * A / L + 12.0 * E * Imax / L ** 3
    kr = 4.0 * E * Imax / L + G * J / L
    return kt, kr


def _dof_stiffness(E: float, G: float, props, L: float) -> Dict[str, float]:
    """The member's own end stiffness per local DOF (tie / residual
    reference; see the module docstring for the tie factor)."""
    A, I22, I33, J = props
    return {"P": E * A / L, "V2": 12.0 * E * I33 / L ** 3,
            "V3": 12.0 * E * I22 / L ** 3, "T": G * J / L,
            "M2": 4.0 * E * I22 / L, "M3": 4.0 * E * I33 / L}


def spring_ends(eng, asm, m, p: Optional[dict], seg, last: int,
                ni_tag: int, nj_tag: int, tag: int, etag: int, mtag: int,
                props, mat, axes) -> Tuple[int, int, int, int, int]:
    """Insert the duplicate node + zeroLength release spring at every
    spring-mode end of this segment.  Returns updated
    (ni_tag, nj_tag, tag, etag, mtag)."""
    if not p:
        return ni_tag, nj_tag, tag, etag, mtag
    xax, yax, _ = axes
    kref = _dof_stiffness(mat.E, mat.G, props, seg.length)
    unst = _rel.unstable_dofs(m)
    for end, here in (("i", seg.index == 0), ("j", seg.index == last)):
        mode, e = p[end]
        if not here or mode != "spring":
            continue
        joint = ni_tag if end == "i" else nj_tag
        tag += 1
        dup = tag
        ops.node(dup, *[float(v) for v in ops.nodeCoord(joint)])
        mats: List[int] = []
        dirs: List[int] = []
        for d in _rel.DOFS:
            ref = kref[d]
            if d in e:
                k = float(e[d])
                if k == 0.0 and d in unst[end]:
                    k = RELEASE_RESIDUAL_RATIO * ref
            else:
                k = RELEASE_TIE_FACTOR * ref
            if k <= 0.0:
                continue
            mtag += 1
            ops.uniaxialMaterial("Elastic", mtag, k)
            mats.append(mtag)
            dirs.append(_DIR[d])
        etag += 1
        ops.element("zeroLength", etag, joint, dup, "-mat", *mats,
                    "-dir", *dirs, "-orient", *[float(v) for v in xax],
                    *[float(v) for v in yax])
        node_idx = seg.ni if end == "i" else seg.nj
        asm.pz_load_tag[(m.uid, node_idx)] = dup
        asm.release_dups[(m.uid, node_idx)] = (dup, node_idx + 1)
        asm.release_ele[(m.uid, end)] = etag
        asm.release_penalty = True
        if end == "i":
            ni_tag = dup
        else:
            nj_tag = dup
    return ni_tag, nj_tag, tag, etag, mtag


def dup_disp(node_disp: Dict[int, List[float]], asm, uid: str,
             node_idx: int) -> Optional[List[float]]:
    """Displacement of the member-side duplicate of a spring end (the
    joint displacement in ``node_disp`` plus the live release slip)."""
    ent = getattr(asm, "release_dups", {}).get((uid, node_idx))
    if ent is None:
        return None
    dup, orig = ent
    base = node_disp.get(orig)
    if base is None:
        return None
    du = ops.nodeDisp(dup)
    do = ops.nodeDisp(orig)
    return [b + (a - c) for b, a, c in zip(base, du, do)]


# ------------------------------------------------------------ joint axes
def _model_ref_stiffness(model) -> Tuple[float, float]:
    kt = kr = 0.0
    for m in model.members:
        sec = model.sections.get(m.section)
        if sec is None:
            continue
        mat = model.materials.get(sec.material)
        if mat is None:
            continue
        L = max(m.length, 1e-6)
        props = (sec.A * sec.mod_A, sec.I22 * sec.mod_I22,
                 sec.I33 * sec.mod_I33, sec.J * sec.mod_J)
        a, b = _end_stiffness(mat.E, mat.G, props, L)
        kt, kr = max(kt, a), max(kr, b)
    for r in getattr(model, "shells", []) or []:
        ss = getattr(model, "shell_sections", {}).get(r.section)
        if ss is None:
            continue
        mat = model.materials.get(ss.material)
        if mat is None:
            continue
        t = float(ss.thickness)
        kt = max(kt, mat.E * t)
        kr = max(kr, mat.E * t ** 3 / 12.0)
    return (kt or 1.0e6), (kr or 1.0e6)


def support_restraints(eng, asm, sup, ntag: int, named_specs: list,
                       spring_cover: Dict[int, set]) -> List[int]:
    """Global ``ops.fix`` flags of an explicit support.  At a joint with
    local axes a MIXED restraint group becomes a stiff grounded spring on
    the restrained local DOFs (appended to ``named_specs``)."""
    restr = [int(bool(r)) for r in sup.restraints]
    model = eng.model
    if not getattr(model, "joint_local_axes", None):
        return restr
    R = _rel.joint_axes_at(model, sup.point)
    if R is None:
        return restr
    from skyframe.core.springprops import DofLaw, SpringSpec
    cache = getattr(eng, "_skew_ref", None)
    if cache is None or cache[0] is not model:
        cache = (model, _model_ref_stiffness(model))
        eng._skew_ref = cache
    kt, kr = cache[1]
    laws: List[Optional[object]] = [None] * 6
    out = list(restr)
    for grp, kref in ((range(0, 3), kt), (range(3, 6), kr)):
        flags = [restr[d] for d in grp]
        if len(set(flags)) == 1:
            continue                         # uniform: exact global fix
        for d in grp:
            out[d] = 0
            if restr[d]:
                laws[d] = DofLaw("linear", k=SKEW_PENALTY_FACTOR * kref)
    if all(lw is None for lw in laws):
        return out
    spec = SpringSpec([list(r) for r in R], laws)
    asm.release_penalty = True
    named_specs.append((ntag, spec))
    spring_cover.setdefault(ntag, set()).update(spec.global_dofs())
    return out


def spring_routed(model, sp) -> bool:
    """A point spring at a joint with local axes takes the named path."""
    return (bool(getattr(model, "joint_local_axes", None))
            and _rel.joint_axes_at(model, sp.point) is not None)


def orient_spec(model, point, spec):
    """Re-express a resolved spring's axes in the joint local axes."""
    R = _rel.joint_axes_at(model, point) if getattr(
        model, "joint_local_axes", None) else None
    if R is None:
        return spec
    S = spec.axes
    spec.axes = [[sum(S[i][k] * R[k][j] for k in range(3)) for j in range(3)]
                 for i in range(3)]
    return spec


def local_reactions(model, results_reactions: Dict[int, List[float]],
                    coords: Dict[int, Sequence[float]]) -> Dict[str, list]:
    """{"x,y,z": [F1..M3]} reactions in joint local axes (joints with
    local axes only)."""
    out = {}
    for t, r in results_reactions.items():
        p = coords.get(t)
        if p is None:
            continue
        if _rel.joint_axes_at(model, p) is None:
            continue
        out[",".join(f"{float(v):g}" for v in p)] = \
            _rel.global_to_joint_local(model, p, r)
    return out


def member_node_disp(node_disp, asm, uid: str, segs):
    """``node_disp`` as seen by member ``uid``'s deflection recovery: the
    end joints of spring-released ends replaced by the member-side
    duplicate displacement (the same dict when the member has none)."""
    dups = getattr(asm, "release_dups", None)
    if not dups:
        return node_disp
    out = None
    for idx in (segs[0].ni, segs[-1].nj):
        d = dup_disp(node_disp, asm, uid, idx)
        if d is not None:
            if out is None:
                out = dict(node_disp)
            out[idx + 1] = d
    return node_disp if out is None else out
