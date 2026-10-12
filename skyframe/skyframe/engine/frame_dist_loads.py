"""Distributed frame moments -- analysis side (CONTRACT "Distributed frame
moments, local 1 / 3 frame loads").

A distributed couple is recorded per member segment as the span-load
record::

    ("mtrap", (mx1, my1, mz1), (mx2, my2, mz2), xa, xb)

(member-LOCAL couple components per unit length at segment-local ``xa`` /
``xb``, linear between) and goes through the engine's exact fixed-end-force
path (``_apply_fef`` -> ``_condensed_fef`` -> ``_consistent_load``):

* consistent load = the integral of ``m(x)`` times the section-ROTATION
  field of the exact homogeneous solution (Hermite derivatives for Euler
  members, the interdependent Timoshenko rotation field ``mu * ...`` for
  shear-flexible members) and, for torsion, the linear twist shape.  The
  integrand is a cubic polynomial, so 3-point Gauss is exact; by Betti the
  result is the exact fixed-end force vector, and release condensation
  (``core.releases``) applies unchanged;
* stations (:func:`section_terms`): statics-exact -- the couple resultant
  ``int m`` from the segment start to the station adds to (T, M2, M3);
  shear and axial force are unaffected;
* deflections (:func:`defl_term`): the double integral of the moment
  jump, ``int m0(s) (xi - s)^2 / 2 ds`` (exact cubic Gauss); the
  Timoshenko shear-deflection term uses the force-only moment
  (:func:`bending_term` returns 0), because a distributed couple does not
  change the section shear force ``Q``.

These helpers are pure functions (no engine import at module load) so
``opensees_engine`` can call them from its span-load kernels.
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple

import numpy as np

_G3 = ((-math.sqrt(3.0 / 5.0), 5.0 / 9.0),
       (0.0, 8.0 / 9.0),
       (math.sqrt(3.0 / 5.0), 5.0 / 9.0))

TAG = "mtrap"


def _at(rec, x: float) -> Tuple[float, float, float]:
    """Local couple density (mx, my, mz) at segment-local ``x``."""
    m1, m2, xa, xb = rec[1], rec[2], rec[3], rec[4]
    span = xb - xa
    s = (x - xa) / span if span > 1e-12 else 0.0
    return tuple(m1[k] + (m2[k] - m1[k]) * s for k in range(3))


def _gauss(lo: float, hi: float):
    for g, wt in _G3:
        yield 0.5 * (lo + hi) + 0.5 * (hi - lo) * g, 0.5 * (hi - lo) * wt


def consistent(rec, L: float, phi_y: float = 0.0,
               phi_z: float = 0.0) -> np.ndarray:
    """Consistent (work-equivalent) nodal 12-vector of one ``mtrap`` record
    -- the distributed form of the engine's concentrated-``moment`` branch
    (same signs, same Timoshenko rotation functions)."""
    f = np.zeros(12)
    xa, xb = rec[3], rec[4]
    if xb - xa < 1e-12:
        return f

    def rot_fns(xi: float, phi: float) -> Tuple[float, float, float]:
        mu = 1.0 / (1.0 + phi)
        return (mu * 6.0 * (xi * xi - xi) / L,
                mu * ((1.0 - xi) * (1.0 - 3.0 * xi) + phi * (1.0 - xi)),
                mu * (xi * (3.0 * xi - 2.0) + phi * xi))

    for x, wt in _gauss(xa, xb):
        mx, my, mz = (wt * v for v in _at(rec, x))
        xi = x / L
        d1, d2, d4 = rot_fns(xi, phi_y)          # x-y plane (mz)
        e1, e2, e4 = rot_fns(xi, phi_z)          # x-z plane (my)
        f[3] += mx * (1.0 - xi)
        f[9] += mx * xi
        f[1] += mz * d1
        f[5] += mz * d2
        f[7] -= mz * d1
        f[11] += mz * d4
        f[2] -= my * e1
        f[4] += my * e2
        f[8] += my * e1
        f[10] += my * e4
    return f


def resultant(rec, x: float) -> Tuple[float, float, float]:
    """``int_xa^min(x, xb) m(s) ds`` (local components)."""
    xa, xb = rec[3], rec[4]
    q = min(x, xb)
    if q <= xa + 1e-12 or xb - xa < 1e-12:
        return (0.0, 0.0, 0.0)
    m_a, m_q = rec[1], _at(rec, q)
    h = q - xa
    return tuple(0.5 * (m_a[k] + m_q[k]) * h for k in range(3))


def section_terms(rec, x: float) -> Tuple[float, float, float]:
    """(dmx, dmy, dmz) added to the station moments of
    ``opensees_engine._section_forces`` (before its sign flip) -- exactly
    as a concentrated couple at every point already passed."""
    return resultant(rec, x)


def defl_term(rec, comp: int, xi: float) -> float:
    """Contribution to ``_defl_double_integral``: ``int m0(s) (xi - s)^2 /
    2 ds`` with ``m0 = -mz`` (x-y plane, comp 1) / ``+my`` (x-z plane,
    comp 2), s over [xa, min(xi, xb)]."""
    xa, xb = rec[3], rec[4]
    q = min(xi, xb)
    if q <= xa + 1e-12 or xb - xa < 1e-12:
        return 0.0
    val = 0.0
    for s, wt in _gauss(xa, q):
        m = _at(rec, s)
        m0 = -m[2] if comp == 1 else m[1]
        val += wt * m0 * (xi - s) ** 2 / 2.0
    return val


def bending_term(rec, comp: int, xi: float) -> float:
    """Contribution to ``_bending_moment`` (the Timoshenko shear-deflection
    moment): zero -- a distributed couple leaves the section shear force
    unchanged (``Q = -(Mb - Mb_couple)'``)."""
    return 0.0


def scale(rec, lam: float):
    """The record scaled by ``lam`` (nonlinear-static load stepping)."""
    return (rec[0], tuple(v * lam for v in rec[1]),
            tuple(v * lam for v in rec[2]), rec[3], rec[4])


# --------------------------------------------------------------------------- #
# load application (engine entry, one hook line in _apply_pattern)
# --------------------------------------------------------------------------- #
def apply(eng, asm, member, ml, scale_: float) -> None:
    """Apply one distributed-moment MemberLoad (``moment_dist`` set),
    case-scaled by ``scale_``, to every segment of ``member`` through the
    exact condensed fixed-end path."""
    from skyframe.core import frame_loads_ext as _fle
    from skyframe.core.loads_ext import member_moment_vectors
    from skyframe.engine import releases_eng as _rle
    from skyframe.engine.opensees_engine import _local_axes

    if getattr(member, "axial_limit", "both") != "both":
        return                    # Truss members take no span loads
    m1, m2 = _fle.end_values(ml)
    m1, m2 = m1 * scale_, m2 * scale_
    if m1 == 0.0 and m2 == 0.0:
        return
    xax, yax, zax, _, _ = _local_axes(member)
    unit, _glo = member_moment_vectors(ml.direction, 1.0, xax, yax, zax)
    L = member.length
    xa_p, xb_p = ml.a * L, ml.b * L
    if xb_p - xa_p < 1e-12:
        return
    segs = asm.mesh.segments[member.uid]
    last = len(segs) - 1
    timo = member.uid in asm.timo
    for seg in segs:
        lo = max(xa_p, seg.x0)
        hi = min(xb_p, seg.x0 + seg.length)
        if hi - lo < 1e-12:
            continue
        v_lo = m1 + (m2 - m1) * (lo - xa_p) / (xb_p - xa_p)
        v_hi = m1 + (m2 - m1) * (hi - xa_p) / (xb_p - xa_p)
        rec = (TAG, tuple(c * v_lo for c in unit),
               tuple(c * v_hi for c in unit), lo - seg.x0, hi - seg.x0)
        eng._apply_fef(asm, member, seg, [rec],
                       _rle.fef_released(member, seg.index, last, timo))


def records_total(records: Sequence) -> int:
    """Number of distributed-moment records (diagnostics / tests)."""
    return sum(1 for r in records if r and r[0] == TAG)
