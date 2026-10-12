"""E2K records for pattern types, per-DOF frame end releases / partial
fixity, joint local axes and multi-direction response spectra (Wave B).

Used by :mod:`skyframe.io.e2k_write` / :mod:`skyframe.io.e2k_read` through
small commented hooks.  CONTRACT "Irregularity checks, grouped ASCE 7
combinations, E2K releases / joint axes, ..." section "E2K".

Load pattern types
------------------
Every SkyFrame pattern kind is written with its ETABS label
(``combos_codes.PATTERN_KIND_LABELS``: "Super Dead", "Reducible Live",
"Roof Live", "Snow", ...) and read back to the SAME kind (the reader used
to fold Super Dead into dead and Reducible / Roof Live into live).

Frame releases (``LINEASSIGN``)
-------------------------------
``RELEASE "PI V2I V3I TI M2I M3I PJ ... M3J"`` lists every released DOF
token (``<DOF><END>``), including partially fixed ones (ETABS: a partial
fixity is a release with a spring).  ``PARTIALFIXITY "M3I 1500 V2J 200"``
gives the spring of the partially fixed tokens, in the file's units
(force/length for P/V2/V3, force*length/rad for T/M2/M3).  A legacy pin
(``Mi`` / ``Mj``) stays ``M2I M3I`` / ``M2J M3J`` exactly as before.

Joint local axes (``POINTASSIGN``)
----------------------------------
``ANGLEA a  ANGLEB b  ANGLEC c`` (degrees; the SAP2000 "Joint Local Axes
Assignments" angles, ``R = Rz(a) Ry(b) Rx(c)``, see core/releases.py).

Multi-direction RS (``LOADCASE``)
---------------------------------
ETABS writes one ``ACC`` row per direction::

    LOADCASE "RSX"  ACCEL "U1"  FUNC "SPEC"  SF 9.81  ANG 30
    LOADCASE "RSX"  ACCEL "U2"  FUNC "SPEC"  SF 2.943  ANG 30
    LOADCASE "RSX"  ECCRATIO 0.05
    LOADCASE "RSX"  DIRCOMBO "SRSS"

``ANG`` is the excitation angle (deg), ``ECCRATIO`` the diaphragm
eccentricity ratio, ``DIRCOMBO`` the directional combination (SRSS / ABS /
CQC3, ``DIRSF`` the ABS scale).  Several directions give several ``ACCEL``
rows (each with its own ``FUNC`` / ``SF``; on import the case scale is 1
and each load carries ``SF / g``).  Fields with no E2K record
(``ecc_overrides``, ``ecc_ax``) stay in the ``$SF`` extension lines.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from skyframe.core import releases as _rel
from skyframe.core.combos_codes import PATTERN_KIND_LABELS

# --------------------------------------------------------------- patterns
#: ETABS TYPE text (upper) -> SkyFrame kind
TYPE_KIND: Dict[str, str] = {v.upper(): k
                             for k, v in PATTERN_KIND_LABELS.items()}
TYPE_KIND.update({"SUPERDEAD": "superdead", "REDUCE LIVE": "reducible_live",
                  "LIVE REDUCIBLE": "reducible_live", "ROOFLIVE": "roof_live",
                  "QUAKE": "quake", "EARTHQUAKE": "quake",
                  "PATTERNLIVE": "pattern_live",
                  "TEMP": "temperature"})


def pattern_kind(type_text: str) -> str:
    return TYPE_KIND.get(str(type_text).upper().strip(), "other")


# --------------------------------------------------------------- releases
_END_TAG = {"i": "I", "j": "J"}
_QTY = {"P": "stiffness", "V2": "stiffness", "V3": "stiffness",
        "T": "rot_stiffness", "M2": "rot_stiffness", "M3": "rot_stiffness"}


def release_parts(mem, n) -> List[str]:
    """``LINEASSIGN`` parts for ``mem`` (``n(v, qty)`` formats a SI value in
    the file units).  Legacy-only members give exactly the legacy text."""
    eff = _rel.effective(mem)
    toks: List[str] = []
    springs: List[str] = []
    if not _rel.has_extended(mem):          # legacy spelling, unchanged
        t = mem.release_tokens()
        if "Mi" in t:
            toks += ["M2I", "M3I"]
        if "Mj" in t:
            toks += ["M2J", "M3J"]
    else:
        for end in _rel.ENDS:
            for d in _rel.DOFS:
                if d not in eff[end]:
                    continue
                tok = f"{d}{_END_TAG[end]}"
                toks.append(tok)
                k = eff[end][d]
                if k > 0.0:
                    springs.append(f"{tok} {n(k, _QTY[d])}")
    parts = []
    if toks:
        parts.append(f'RELEASE "{" ".join(toks)}"')
    if springs:
        parts.append(f'PARTIALFIXITY "{" ".join(springs)}"')
    return parts


def parse_releases(kv, to_si) -> Tuple[str, Optional[dict]]:
    """(legacy ``releases`` tokens, ``end_releases``) from a LINEASSIGN's
    ``RELEASE`` / ``PARTIALFIXITY`` values, canonical (core.releases).
    ``to_si(text, qty)`` converts a file value to SI."""
    eff: Dict[str, Dict[str, float]] = {"i": {}, "j": {}}
    if "RELEASE" in kv:
        for t in kv["RELEASE"].text.upper().split():
            d, e = t[:-1], t[-1:]
            if d in _rel.DOFS and e in ("I", "J"):
                eff["i" if e == "I" else "j"][d] = 0.0
    if "PARTIALFIXITY" in kv:
        w = kv["PARTIALFIXITY"].text.upper().split()
        for t, v in zip(w[0::2], w[1::2]):
            d, e = t[:-1], t[-1:]
            if d in _rel.DOFS and e in ("I", "J"):
                k = to_si(v, _QTY[d])
                if k is not None and k >= 0.0:
                    eff["i" if e == "I" else "j"][d] = k
    eff = {end: {d: eff[end][d] for d in _rel.DOFS if d in eff[end]}
           for end in _rel.ENDS}
    return _rel.canonical(eff)


# --------------------------------------------------------- joint local axes
def joint_axes_parts(model) -> List[Tuple[list, str]]:
    """[(point, "ANGLEA a  ANGLEB b  ANGLEC c")] for every joint whose local
    axes are set (identity entries included: they are model data)."""
    from skyframe.io.exchange_common import fmt
    out = []
    for e in getattr(model, "joint_local_axes", None) or []:
        a, b, c = e["angles"]
        out.append((list(e["point"]),
                    f"ANGLEA {fmt(a)}  ANGLEB {fmt(b)}  ANGLEC {fmt(c)}"))
    return out


def parse_joint_axes(kv) -> Optional[List[float]]:
    from skyframe.io.e2k import _num
    if not any(k in kv for k in ("ANGLEA", "ANGLEB", "ANGLEC")):
        return None
    return [float(_num(kv.get(k)) or 0.0)
            for k in ("ANGLEA", "ANGLEB", "ANGLEC")]


# ------------------------------------------------------- multi-direction RS
_ACC = {"X": "U1", "Y": "U2", "Z": "U3"}
_ACC_IN = {"U1": "X", "UX": "X", "U2": "Y", "UY": "Y", "U3": "Z", "UZ": "Z"}
_DIRCOMBO = {"SRSS": "SRSS", "ABS": "ABS", "CQC3": "CQC3"}


def rs_acc_rows(rs, q, fmt, g_file: float, fn: str) -> List[str]:
    """The ``ACCEL`` row(s) of one RS case.  A legacy case (one direction,
    angle 0) gives exactly the legacy row; several ``loads`` give one row
    per load (``SF`` = case scale x load scale x g); a non-zero angle adds
    ``ANG`` to every row."""
    ang = float(getattr(rs, "angle", 0.0) or 0.0)
    tail = f"  ANG  {fmt(ang)}" if ang else ""
    loads = getattr(rs, "loads", None) or []
    if not loads:
        acc = _ACC.get(rs.direction, "U2")
        return [f"LOADCASE {q(rs.name)}  ACCEL  {q(acc)}  FUNC  {q(fn)}"
                f"  SF  {fmt(rs.scale * g_file)}{tail}"]
    rows = []
    for ld in loads:
        f = ld.get("function") or fn
        rows.append(f"LOADCASE {q(rs.name)}  ACCEL  "
                    f"{q(_ACC.get(str(ld.get('dir', 'X')).upper(), 'U1'))}"
                    f"  FUNC  {q(f)}  SF  "
                    f"{fmt(rs.scale * float(ld.get('scale', 1.0)) * g_file)}"
                    f"{tail}")
    return rows


def rs_extra_records(rs, q, fmt) -> List[str]:
    """``ECCRATIO`` (diaphragm eccentricity) and ``DIRCOMBO`` (+ ``DIRSF``
    for ABS) records of one RS case; none for a legacy case."""
    out = []
    ecc = float(getattr(rs, "ecc", 0.0) or 0.0)
    if ecc:
        out.append(f"LOADCASE {q(rs.name)}  ECCRATIO {fmt(ecc)}")
    dc = str(getattr(rs, "dir_combo", "SRSS") or "SRSS").upper()
    sf = float(getattr(rs, "dir_sf", 1.0))
    if dc != "SRSS" or sf != 1.0:
        tail = f"  DIRSF {fmt(sf)}" if sf != 1.0 else ""
        out.append(f"LOADCASE {q(rs.name)}  DIRCOMBO {q(dc)}{tail}")
    return out


def rs_read(rsd: dict, accel: list, angles: list, kv: dict, g_file: float,
            spec_fn: dict) -> None:
    """Apply the multi-direction / angle / eccentricity records to one RS
    case dict built by the reader from the first ACCEL row (in place)."""
    from skyframe.io.e2k import _num
    if len(accel) > 1:
        loads = []
        for acc, fn, sf in accel:
            loads.append({"dir": _ACC_IN.get(acc, "X"),
                          "scale": sf / g_file,
                          "function": fn if fn in spec_fn and
                          fn != rsd.get("function") else ""})
        rsd["loads"] = loads
        rsd["scale"] = 1.0
    ang = next((a for a in angles if a), 0.0)
    if ang:
        rsd["angle"] = float(ang)
    if "ECCRATIO" in kv and _num(kv["ECCRATIO"]):
        rsd["ecc"] = float(_num(kv["ECCRATIO"]))
    if "DIRCOMBO" in kv:
        dc = _DIRCOMBO.get(kv["DIRCOMBO"].text.upper())
        if dc and dc != "SRSS":
            rsd["dir_combo"] = dc
    if "DIRSF" in kv and _num(kv["DIRSF"]) is not None:
        rsd["dir_sf"] = float(_num(kv["DIRSF"]))


__all__ = ["TYPE_KIND", "pattern_kind", "release_parts", "parse_releases",
           "joint_axes_parts", "parse_joint_axes", "rs_acc_rows",
           "rs_extra_records", "rs_read"]
