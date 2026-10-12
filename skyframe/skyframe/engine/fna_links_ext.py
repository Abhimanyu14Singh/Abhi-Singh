"""FNA records of the Wave C device links (one hook line in
:func:`skyframe.engine.fna._devices`).

Link ``type`` -> device (:mod:`skyframe.core.fna_devices_ext`), rows and
the linear part kept in the modal basis:

* ``multilinear_takeda`` / ``multilinear_pivot`` (and a
  ``multilinear_degrading`` link with ``points``, C5's option, when that
  type exists) — :class:`HystereticDevice` on the two horizontal shear
  rows (isolator layout, ``u_j - u_i`` in global X / Y, node order lower ->
  upper); basis: elastic link ``[k1, k1, kv]`` with ``k1`` the first
  backbone slope (the pseudo-force is the hysteretic correction);
* ``brb`` — :class:`MenegottoPintoAsym` on the axial row (elongation along
  ``pi -> pj``), ``b_t`` / ``b_c`` from
  :func:`skyframe.core.links_ext.brb_hardening`; no basis stiffness (the
  full brace force is the pseudo-force, like the legacy axial devices —
  use a Ritz modal case with ``links`` starts for a braced frame);
* ``damper_bilinear`` — :class:`BilinearDamper` on the axial row, no basis
  stiffness;
* ``tc_fp_isolator`` — :class:`TCFrictionPendulum` on rows x / y / z;
  basis ``[k_init + N/R, k_init + N/R, kv]`` with ``N`` the gravity axial
  load of the bearing (FNA needs gravity initial conditions —
  ``initial_case`` / ``gravity`` — for it, as for ``fp_isolator``).
"""

from __future__ import annotations

import math

import numpy as np

from skyframe.core import fna_devices_ext as _fx
from skyframe.core import links_ext as _lx

HYST_TYPES = ("multilinear_takeda", "multilinear_pivot",
              "multilinear_degrading")
TYPES = HYST_TYPES + ("brb", "damper_bilinear", "tc_fp_isolator")


def add(th, lk, lt: str, prm: dict, disp_at, lin, recs) -> bool:
    """Append the device record of link ``lk`` (and its basis link to
    ``lin``); False when ``lt`` is not a Wave C type."""
    from skyframe.core.model import (DAMPER_DEFAULT_K, FP_DEFAULT_KINIT,
                                     ISOLATOR_DEFAULT_KV, LinkMember)
    from skyframe.engine import fna as _fna_eng
    if lt not in TYPES or (lt == "multilinear_degrading"
                           and "points" not in prm):
        return False
    pi, pj = tuple(lk.pi), tuple(lk.pj)
    if lt in ("brb", "damper_bilinear"):
        dx = np.array(pj, dtype=float) - np.array(pi, dtype=float)
        e = dx / float(np.linalg.norm(dx))
        if lt == "brb":
            bt, bc = _lx.brb_hardening(prm)
            dev = _fx.MenegottoPintoAsym(
                prm["k"], prm["Fy"], bt, bc,
                float(prm.get("R0", _lx.DEFAULTS["brb"]["R0"])),
                _lx.STEEL4_R1, _lx.STEEL4_R2)
        else:
            dev = _fx.BilinearDamper(
                float(prm.get("k", DAMPER_DEFAULT_K)), prm["cd"], prm["Fr"],
                float(prm.get("ratio", 0.0)))
        recs.append((dev, [("axial", e)], [0.0], (pi, pj), "axial", lk.uid))
        return True
    lo, up = _fna_eng._bearing_axis(lk)
    kv = float(prm.get("kv", ISOLATOR_DEFAULT_KV))
    if lt in HYST_TYPES:
        dev = _fx.HystereticDevice(prm, rows=2, ltype=lt)
        kl = float(dev.k_init)
        lin.links.append(LinkMember(lk.uid, lo, up,
                                    [kl, kl, kv, 0.0, 0.0, 0.0]))
        recs.append((dev, [("dof", 0), ("dof", 1)], [kl, kl], (lo, up),
                     "shear", lk.uid))
        return True
    # tc_fp_isolator
    k0 = float(prm.get("k_init", FP_DEFAULT_KINIT))
    R = float(prm["R"])
    if disp_at is None:
        raise ValueError(
            f"FNA case {th.name!r}: tc_fp_isolator {lk.uid!r} needs its "
            "axial load — give the case gravity initial conditions "
            "(initial_case / gravity)")
    ul, uu = disp_at(lo), disp_at(up)
    d0z = (uu[2] if uu else 0.0) - (ul[2] if ul else 0.0)
    Nref = max(-kv * d0z, 0.0)
    kl = k0 + Nref / R
    dev = _fx.TCFrictionPendulum(R, float(prm["mu"]), k0, 0.0, kv,
                                 float(prm.get("kt", 0.0)),
                                 float(prm.get("gap_t", 0.0)))
    lin.links.append(LinkMember(lk.uid, lo, up, [kl, kl, kv, 0.0, 0.0, 0.0]))
    recs.append((dev, [("dof", 0), ("dof", 1), ("dof", 2)], [kl, kl, kv],
                 (lo, up), "shear", lk.uid))
    return True


__all__ = ["TYPES", "add"]
