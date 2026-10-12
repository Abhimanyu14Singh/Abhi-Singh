"""Tendons as STIFFNESS ELEMENTS (bonded, ETABS "model tendons as elements")
and the extra loss options — the model side.

A tendon dict (``BuildingModel.tendons``, :mod:`skyframe.core.tendons`)
gains two OPTIONAL keys, emitted by ``normalize_tendon`` only when set, so
every legacy tendon (and file) stays byte-identical:

* ``"model_as": "elements"`` (default ``"loads"`` = the equivalent-load
  model, unchanged).  The tendon is discretised into its sub-pieces
  (``n_sub`` per segment, split at host changes — exactly the pieces of the
  load model); every piece becomes a straight BONDED TRUSS element (E = the
  tendon material E, A = ``area``, strand bending stiffness ``A^2/(4 pi)``)
  between the chord end points, rigidly linked to the host: frame hosts are
  split by the mesher at the projection of every piece boundary on the host
  axis and the element connects those host nodes through rigid joint
  offsets (the vector from the host node to the tendon point); shell hosts
  connect to the NEAREST host-region mesh node (pieces whose two ends map
  to the same node are merged).  The prestress is an INITIAL STRESS: piece
  ``e`` carries ``P_e`` = the piece force after friction / wobble / anchor
  set / long-term losses (the load model's ``P_mid``), realised as the
  self-equilibrated pair ``+P_e t_e`` at its start point and ``-P_e t_e``
  at its end point (``t_e`` the unit chord) applied to the host nodes with
  their transfer couples whenever the tendon's load pattern is applied.
  The element force in any static case is then ``P = scale * P_e + (EA/L)
  * delta_L`` — the ELASTIC SHORTENING loss (and the change under any other
  load) comes out of the analysis.  Bonded at transfer = all tendons
  released at once (pretensioned behaviour).
* ``losses.es_factor`` (loads model only, >= 0, default absent): hand
  elastic-shortening loss ``delta_fp = K_es (Ep/Ec) f_cgp`` with ``f_cgp = P
  (1/A + e2^2/I33 + e3^2/I22)`` on the host frame section at each piece
  (e2/e3 = tendon offset from the host axis along local 2/3), subtracted
  from the piece force (``K_es = 0.5`` for sequentially stressed
  post-tensioned tendons, 1.0 pretensioned — ACI 423 / PCI).

Hand loss formulas used by the tests and the dialog's loss preview are in
:func:`hand_losses`.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

MODEL_AS = ("loads", "elements")


def normalize_extra(raw: dict, out: dict, tag: str) -> None:
    """Carry the optional element / ES keys of a raw tendon dict into its
    normalised form ``out`` (only when set)."""
    ma = raw.get("model_as", "loads")
    if ma not in MODEL_AS:
        raise ValueError(f"{tag}: model_as must be one of {MODEL_AS}")
    if ma == "elements":
        out["model_as"] = "elements"
    lo = raw.get("losses") or {}
    if isinstance(lo, dict) and lo.get("es_factor") is not None:
        v = lo["es_factor"]
        if (isinstance(v, bool) or not isinstance(v, (int, float))
                or not math.isfinite(v) or v < 0.0):
            raise ValueError(f"{tag}: losses.es_factor must be >= 0")
        out["losses"]["es_factor"] = float(v)


EXTRA_LOSS_KEYS = ("es_factor",)


def is_element(td: dict) -> bool:
    return td.get("model_as") == "elements"


def element_tendons(model) -> List[dict]:
    from skyframe.core import tendons as _td
    return [_td.normalize_tendon(t) for t in
            (getattr(model, "tendons", None) or [])
            if isinstance(t, dict) and t.get("model_as") == "elements"]


def chord_points(td: dict, pieces: List[dict]) -> List[Tuple[tuple, tuple]]:
    """(start, end) chord points of every sub-piece."""
    from skyframe.core import tendons as _td
    segs = _td.segments(td)
    return [(segs[pc["seg"]].p(pc["u1"]), segs[pc["seg"]].p(pc["u2"]))
            for pc in pieces]


def interior_points(model) -> Dict[str, List[Tuple[float, tuple]]]:
    """uid -> [(d, point)] host-axis cut points (0 < d < 1) under every
    piece boundary of an element tendon on frame hosts (the mesher splits
    the host there so the bonded element has host nodes)."""
    tds = getattr(model, "tendons", None) or []
    if not any(isinstance(t, dict) and t.get("model_as") == "elements"
               for t in tds):
        return {}
    from skyframe.core import tendons as _td
    out: Dict[str, List[Tuple[float, tuple]]] = {}
    mem = {m.uid: m for m in model.members}
    for td in element_tendons(model):
        if _td.host_kind(model, td) != "frame":
            continue
        pieces = _td.sub_pieces(model, td)
        for pc, (a, b) in zip(pieces, chord_points(td, pieces)):
            m = mem[pc["host"]]
            ax = _td._member_axis(m)
            for p in (a, b):
                x, _ = _td._project(ax, p)
                d = x / ax[2]
                if 1e-9 < d < 1.0 - 1e-9:
                    foot = tuple(ax[0][i] + ax[1][i] * x for i in range(3))
                    out.setdefault(m.uid, []).append((d, foot))
    for uid, lst in out.items():
        lst.sort(key=lambda e: e[0])
        clean: List[Tuple[float, tuple]] = []
        for e in lst:
            if not clean or e[0] - clean[-1][0] > 1e-9:
                clean.append(e)
        out[uid] = clean
    return out


def validate(model, td: dict) -> None:
    """Element / ES specific checks (called by validate_tendons)."""
    from skyframe.core import tendons as _td
    kind = _td.host_kind(model, td)
    if td["losses"].get("es_factor") is not None and kind != "frame":
        raise ValueError(f"Tendon {td['uid']!r}: losses.es_factor needs "
                         "frame hosts")
    if is_element(td):
        mat = _td.tendon_material(model, td)
        if not mat.E > 0.0:
            raise ValueError(f"Tendon {td['uid']!r}: material E must be > 0 "
                             "for model_as 'elements'")


# --------------------------------------------------------------------------- #
# hand elastic-shortening reduction (loads model, es_factor)
# --------------------------------------------------------------------------- #
def _local_offsets(model, member, p) -> Tuple[float, float]:
    from skyframe.engine.opensees_engine import _local_axes
    from skyframe.core import tendons as _td
    ax = _td._member_axis(member)
    x, _ = _td._project(ax, p)
    foot = tuple(ax[0][i] + ax[1][i] * x for i in range(3))
    r = tuple(p[i] - foot[i] for i in range(3))
    _x, yax, zax, _, _ = _local_axes(member)
    return (sum(r[i] * yax[i] for i in range(3)),
            sum(r[i] * zax[i] for i in range(3)))


def es_loss_force(model, td: dict, host_uid: str, p, P: float) -> float:
    """``K_es (Ep/Ec) f_cgp Ap`` at tendon point ``p`` on host member."""
    from skyframe.core import tendons as _td
    kes = td["losses"].get("es_factor")
    if not kes:
        return 0.0
    m = next(mm for mm in model.members if mm.uid == host_uid)
    sec = model.sections[m.section]
    Ec = model.materials[sec.material].E
    Ep = _td.tendon_material(model, td).E
    e2, e3 = _local_offsets(model, m, p)
    A = sec.A * sec.mod_A
    I33 = sec.I33 * sec.mod_I33
    I22 = sec.I22 * sec.mod_I22
    fcgp = P * (1.0 / A + e2 * e2 / I33 + e3 * e3 / I22)
    return kes * Ep / Ec * fcgp * td["area"]


def apply_es(model, td: dict, pieces: List[dict], P_mid: List[float],
             P_bnd: List[float]) -> None:
    """In-place ES reduction of the piece / boundary forces (es_factor)."""
    if not td["losses"].get("es_factor"):
        return
    from skyframe.core import tendons as _td
    segs = _td.segments(td)
    for i, pc in enumerate(pieces):
        sg = segs[pc["seg"]]
        pm = sg.p(0.5 * (pc["u1"] + pc["u2"]))
        P_mid[i] = max(P_mid[i] - es_loss_force(model, td, pc["host"], pm,
                                                P_mid[i]), 0.0)
    for j in range(len(P_bnd)):
        pc = pieces[min(j, len(pieces) - 1)]
        sg = segs[pc["seg"]]
        pb = sg.p(pc["u1"] if j < len(pieces) else pc["u2"])
        P_bnd[j] = max(P_bnd[j] - es_loss_force(model, td, pc["host"], pb,
                                                P_bnd[j]), 0.0)


# --------------------------------------------------------------------------- #
# hand formulas (tests + dialog preview)
# --------------------------------------------------------------------------- #
def hand_losses(P0: float, mu: float, k: float, alpha: float, x: float,
                Ep: float, Ap: float, set_mm: float = 0.0,
                Ec: Optional[float] = None, fcgp: Optional[float] = None,
                kes: float = 1.0) -> dict:
    """Closed-form loss values for one jacking end:

    * friction ``P(x) = P0 exp(-(mu alpha + k x))``;
    * anchor-set length on a linear friction slope ``p = (P0 - P(x))/x``:
      ``l_set = sqrt(delta Ep Ap / p)`` and the force loss at the anchor
      ``2 p l_set``;
    * elastic shortening ``delta_fp = K_es (Ep/Ec) f_cgp`` (stress).
    """
    Px = P0 * math.exp(-(mu * alpha + k * x))
    out = {"P_friction": Px, "friction_loss": P0 - Px}
    if set_mm > 0.0 and x > 0.0 and P0 > Px:
        p = (P0 - Px) / x
        lset = math.sqrt(set_mm / 1000.0 * Ep * Ap / p)
        out["set_length"] = lset
        out["set_loss_at_anchor"] = 2.0 * p * lset
    if Ec and fcgp is not None:
        out["es_stress_loss"] = kes * Ep / Ec * fcgp
    return out
