"""Native ETABS e2k records for the nonlinear / frequency / thermal model
data (Wave C).  Used by :mod:`skyframe.io.e2k_write` and
:mod:`skyframe.io.e2k_read` through small commented hooks; CONTRACT
section "E2K native nonlinear records (Wave C)" is the reference.

Before Wave C these features travelled only as ``$SF`` comment lines that
ETABS ignores.  They are now written as ETABS-style records and read back
by the native reader (``read_e2k(..., apply_extensions=False)``), so an
ETABS user sees the cases / hinges / links / temperature loads and a
SkyFrame re-import rebuilds them without the extension lines.  Whatever a
record cannot carry still falls back to ``$SF`` (the exporter diffs the
native re-import against the model, see :func:`e2k_write.export_e2k`).

Records (``"..."`` = quoted name; values in the file's UNITS)::

  LOADCASE "N"  TYPE "Nonlinear Static"  INITCOND "NONE"|"<case>"  ...
  LOADCASE "N"  LOADPAT "DEAD"  SF 1           (one per load, in order)
  LOADCASE "N"  MODE 1  SF 1  |  ACCEL "U1"  SF 1      (pushover loads)
  LOADCASE "N"  NLGEOMTYPE "None"|"PDelta"|"LargeDispl"
  LOADCASE "N"  LOADCONTROL "Full"|"Displacement"  DISPLTYPE "Monitored"
                MONITOREDDISPL "Joint"|"Story"|"Roof"  [STORY "S"]
                [DISPLMAG d]  [DOF "U1"]  [JOINT "p" "level"]
  LOADCASE "N"  RESULTSSAVED "Multiple"  MINSAVED n  MAXSAVED n
  LOADCASE "N"  LOADDIST "Roof Point"|"Pattern"|"Mode"|
                "Uniform Acceleration"|"Triangular"  DIR "X"
                TARGETDRIFT r  [K k]  [LOADMAG f]  [TOWER "T"]  (pushover)
  LOADCASE "N"  HINGEMODE "Column Base"|"All Ends"|"ASCE41"
                HARDENING h  [DEFAULTMY m]
  LOADCASE "N"  HINGEMY "uid"  MY m      LOADCASE "N"  HINGEPARAM "k" VALUE v
  LOADCASE "S"  TYPE "Nonlinear Staged Construction"  INITCOND "NONE"
  LOADCASE "S"  AUTOSTAGE "Per Story"  +  LOADPAT "DEAD"  SF 1
  LOADCASE "S"  STAGE "S1"  DURATION 7
  LOADCASE "S"  STAGE "S1"  OPERATION "Add Structure"|"Remove Structure"|
                "Load Objects"  OBJTYPE "Group"  OBJNAME "G"  [AGE 28]
                [LOADTYPE "Load Pattern"  LOADNAME "DEAD"  SF 1]
  LOADCASE "S"  FINALLOADPAT "LIVE"  SF 0.5          (include_live)
  FUNCTION "F"  FUNCTYPE "STEADYSTATE"|"PSD"  ;  FUNCTION "F"  FREQ f  VALUE v
  LOADCASE "F"  TYPE "Steady State"|"Power Spectral Density"
  LOADCASE "F"  LOADPAT "P"|ACCEL "U1"  SF s  FUNC "F"  PHASE deg
  LOADCASE "F"  FREQRANGE f0  FREQEND f1  NUMFREQ n  MODALFREQ "Yes"
  LOADCASE "F"  ADDFREQ f ;  SOLUTIONMETHOD "Modal"  NUMMODES n
  LOADCASE "F"  DAMPTYPE "Modal"|"Hysteretic"  CONSTDAMP z
  LOADCASE "F"  OUTPUTJOINT "p" "level"
  HINGE "H"  BEHAVIOR "Deformation Controlled"  DOF "M3"  TYPE
             "Moment-Rotation"  HYSTYPE "Kinematic"  BEYONDE "Drops To Zero"
  HINGE "H"  "B" d f   ...  "-B" -d -f ...       (backbone after point A)
  HINGE "H"  MOMENTSFP s  ROTATIONSFP r  MOMENTSFN s  ROTATIONSFN r
  HINGE "H"  IO x  LS x  CP x  -IO -x  -LS -x  -CP -x
  HINGE "H"  DOF "FiberPM2M3"  FROMSECTION "Yes"
  HINGE "H"  AUTOHINGETYPE "ASCE41-17"  TABLEITEM "Steel Beams"  DOF "M3"
  HINGE "H"  AUTOPARAM "rho"  VALUE 0.01 ;  HINGE "H"  GRAVLOADPAT "D" SF 1
  HINGEASSIGN "B1"  "Story1"  HINGEPROP "H"  RDISTANCE 0
  HINGEASSIGN "B1"  "Story1"  AUTOHINGETYPE "ASCE41-13"  TABLEITEM
             "Concrete Beams"  DOF "M3"  RDISTANCE 0      (ETABS auto)
  HINGEOVERWRITE "B1"  "Story1"  AUTOSUBDIVIDE "Yes"
             AUTOSUBDIVIDERELLENGTH 0.02
  LINKPROP "LP"  TYPE "DAMPER - EXPONENTIAL" | "GAP" | "HOOK" |
             "RUBBER ISOLATOR" | "FRICTION ISOLATOR" | ...  [HYSTYPE "..."]
  LINKPROP "LP"  DOF "U1"  NONLIN "Yes"  K k  C c  EXP a   (per-DOF params)
  LINKPROP "LP"  DOF "U2"  DISPL d  FORCE f      (multilinear points)
  POINTLOAD "p" "lvl"  TYPE "TEMPERATURE"  LC "P"  T dT
  LINELOAD "B1" "S"  TYPE "TEMPERATURE"  LC "P"  T dT
  LINELOAD "B1" "S"  TYPE "TEMPERATUREGRADIENT"  DIR "2"  LC "P"  TGRAD g
  AREALOAD "F1" "S"  TYPE "TEMPERATURE"|"TEMPERATUREGRADIENT"  ...

Confirmed against real ETABS files (see docs/REVIEW_FLAGS.md "E2K native
nonlinear records (Wave C)"): the Nonlinear Static LOADCASE keywords
(INITCOND / LOADPAT / MODE / NLGEOMTYPE / LOADCONTROL / DISPLTYPE /
MONITOREDDISPL / DISPLMAG / DOF / JOINT / RESULTSSAVED / MINSAVED /
MAXSAVED), HINGE user-hinge records (BEHAVIOR / DOF / TYPE / HYSTYPE,
labelled points, MOMENTSFP.., IO/LS/CP, FiberPM2M3 FROMSECTION),
HINGEASSIGN (HINGEPROP / AUTOHINGETYPE / TABLEITEM / DOF / RDISTANCE),
HINGEOVERWRITE AUTOSUBDIVIDERELLENGTH and the exponential damper
``DOF "U1" NONLIN "Yes" K C EXP``.  Everything else is written in the same
style and listed there as unconfirmed.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from skyframe.io.exchange_common import fmt, q

__all__ = ["KNOWN_RECORDS", "LINK_TYPE_NAMES", "link_type_of",
           "link_prop_rows", "link_params", "write_hinge_props",
           "write_hinge_assigns", "thermal_rows", "write_freq_functions",
           "write_cases", "read_hinges", "read_thermal",
           "read_freq_functions", "read_cases", "is_temperature"]

KNOWN_RECORDS = {"HINGE", "HINGEASSIGN", "HINGEOVERWRITE"}
_NONE_CASES = ("NONE", "PRESET", "")
_GRAV_SUFFIX = "~GRAV"


# --------------------------------------------------------------------------- #
# token helpers
# --------------------------------------------------------------------------- #
def _pairs(toks, start: int) -> List[Tuple[str, list]]:
    """``KEY value`` pairs of a record; ``JOINT`` / ``OUTPUTJOINT`` take two
    values (point, level).  Stops at a quoted token in key position."""
    out: List[Tuple[str, list]] = []
    i = start
    while i < len(toks):
        key = toks[i]
        if key.quoted or i + 1 >= len(toks):
            break
        k = key.text.upper()
        n = 2 if k in ("JOINT", "OUTPUTJOINT") else 1
        out.append((k, [t.text for t in toks[i + 1:i + 1 + n]]))
        i += 1 + n
    return out


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _yn(b) -> str:
    return '"Yes"' if b else '"No"'


def _is_yes(v) -> bool:
    return str(v).strip().upper() in ("YES", "TRUE")


# --------------------------------------------------------------------------- #
# nonlinear links
# --------------------------------------------------------------------------- #
#: SkyFrame link_type -> (ETABS TYPE, HYSTYPE discriminator or None)
LINK_TYPE_NAMES: Dict[str, Tuple[str, Optional[str]]] = {
    "damper": ("DAMPER - EXPONENTIAL", None),
    "damper_bilinear": ("DAMPER - BILINEAR", None),
    "gap": ("GAP", None),
    "hook": ("HOOK", None),
    "isolator": ("RUBBER ISOLATOR", "Bilinear"),
    "rubber_isolator_bouc_wen": ("RUBBER ISOLATOR", "Bouc-Wen"),
    "fp_isolator": ("FRICTION ISOLATOR", None),
    "tc_fp_isolator": ("T/C FRICTION ISOLATOR", None),
    "triple_fp": ("TRIPLE PENDULUM ISOLATOR", None),
    "multilinear": ("MULTILINEAR ELASTIC", None),
    "multilinear_kinematic": ("MULTILINEAR PLASTIC", "Kinematic"),
    "multilinear_takeda": ("MULTILINEAR PLASTIC", "Takeda"),
    "multilinear_pivot": ("MULTILINEAR PLASTIC", "Pivot"),
    "friction_spring": ("DAMPER - FRICTION SPRING", None),
    "plastic_wen": ("PLASTIC (WEN)", None),
    "brb": ("PLASTIC (WEN)", "BRB"),
}
_TYPE_IN: Dict[Tuple[str, Optional[str]], str] = {
    (t.upper(), h.upper() if h else None): k
    for k, (t, h) in LINK_TYPE_NAMES.items()}
_TYPE_IN.update({("DAMPER", None): "damper",        # older spellings
                 ("T/C FRICTION ISOLATOR", "TRIPLE"): "triple_fp"})

_SHEAR = ("U2", "U3")
# link_type -> [(dofs, KEYWORD, param, qty)]; qty "cd" = damping
# coefficient force*(s/length)^alpha, "cd1" = force*s/length
_LINK_KEYS: Dict[str, List[Tuple[Tuple[str, ...], str, str, str]]] = {
    "damper": [(("U1",), "K", "k", "stiffness"), (("U1",), "C", "cd", "cd"),
               (("U1",), "EXP", "alpha", "none")],
    "damper_bilinear": [(("U1",), "K", "k", "stiffness"),
                        (("U1",), "C", "cd", "cd1"),
                        (("U1",), "FR", "Fr", "force"),
                        (("U1",), "RATIO", "ratio", "none")],
    "gap": [(("U1",), "K", "k", "stiffness"),
            (("U1",), "OPEN", "gap", "length")],
    "hook": [(("U1",), "K", "k", "stiffness"),
             (("U1",), "OPEN", "slack", "length")],
    "isolator": [(("U1",), "KV", "kv", "stiffness"),
                 (_SHEAR, "K", "k1", "stiffness"),
                 (_SHEAR, "YIELD", "Fy", "force"),
                 (_SHEAR, "POSTK", "k2", "stiffness")],
    "rubber_isolator_bouc_wen": [
        (("U1",), "KV", "kv", "stiffness"),
        (_SHEAR, "K", "k_init", "stiffness"),
        (_SHEAR, "YIELD", "qd", "force"),
        (_SHEAR, "ALPHA1", "alpha1", "none"),
        (_SHEAR, "ALPHA2", "alpha2", "none"),
        (_SHEAR, "MU", "mu", "none"), (_SHEAR, "ETA", "eta", "none"),
        (_SHEAR, "BETA", "beta", "none"),
        (_SHEAR, "GAMMA", "gamma", "none")],
    "fp_isolator": [(("U1",), "KV", "kv", "stiffness"),
                    (("U1",), "P0", "P0", "force"),
                    (_SHEAR, "K", "k_init", "stiffness"),
                    (_SHEAR, "FSLOW", "mu", "none"),
                    (_SHEAR, "RADIUS", "R", "length")],
    "tc_fp_isolator": [(("U1",), "KV", "kv", "stiffness"),
                       (("U1",), "KT", "kt", "stiffness"),
                       (("U1",), "OPEN", "gap_t", "length"),
                       (_SHEAR, "K", "k_init", "stiffness"),
                       (_SHEAR, "FSLOW", "mu", "none"),
                       (_SHEAR, "RADIUS", "R", "length")],
    "triple_fp": [(("U1",), "KV", "kv", "stiffness"),
                  (("U1",), "KVT", "kvt", "stiffness"),
                  (("U1",), "W", "W", "force"),
                  (("U1",), "MINFV", "minFv", "force"),
                  (("U1",), "TOL", "tol", "none")] + [
        (_SHEAR, f"{k.upper()}", k, qty)
        for k, qty in (("R1", "length"), ("R2", "length"),
                       ("R3", "length"), ("mu1", "none"), ("mu2", "none"),
                       ("mu3", "none"), ("d1", "length"), ("d2", "length"),
                       ("d3", "length"), ("uy", "length"))],
    "multilinear": [(("U1",), "KV", "kv", "stiffness")],
    "multilinear_kinematic": [(("U1",), "KV", "kv", "stiffness")],
    "multilinear_takeda": [(("U1",), "KV", "kv", "stiffness")],
    "multilinear_pivot": [(("U1",), "KV", "kv", "stiffness"),
                          (_SHEAR, "PINCHX", "pinch_x", "none"),
                          (_SHEAR, "PINCHY", "pinch_y", "none"),
                          (_SHEAR, "UNLOADBETA", "unload_beta", "none")],
    "friction_spring": [(("U1",), "KV", "kv", "stiffness"),
                        (_SHEAR, "K", "k_init", "stiffness"),
                        (_SHEAR, "MU", "mu", "none")],
    "plastic_wen": [(("U1",), "K", "k", "stiffness"),
                    (("U1",), "YIELD", "Fy", "force"),
                    (("U1",), "RATIO", "ratio", "none"),
                    (("U1",), "EXP", "exp", "none"),
                    (("U1",), "KV", "kv", "stiffness")],
    "brb": [(("U1",), "K", "k", "stiffness"),
            (("U1",), "YIELD", "Fy", "force"),
            (("U1",), "DBM", "d_bm", "length"),
            (("U1",), "BETA", "beta", "none"),
            (("U1",), "OMEGA", "omega", "none"),
            (("U1",), "R0", "R0", "none")],
}
_POINT_TYPES = ("multilinear", "multilinear_kinematic", "multilinear_takeda",
                "multilinear_pivot")


def _cd_factor(u, qty: str, alpha: float) -> float:
    """SI value = file value * factor, damping coefficients included."""
    if qty == "cd":
        return u.F / u.L ** alpha
    if qty == "cd1":
        return u.F / u.L
    return u.factor(qty)


def link_type_of(kv: dict, table: dict) -> str:
    """SkyFrame link type of a ``LINKPROP ... TYPE`` record (``kv`` from
    e2k._kv); falls back to the legacy reader ``table``."""
    t = kv["TYPE"].text.upper().strip() if "TYPE" in kv else "LINEAR"
    h = kv["HYSTYPE"].text.upper().strip() if "HYSTYPE" in kv else None
    if (t, h) in _TYPE_IN:
        return _TYPE_IN[(t, h)]
    if (t, None) in _TYPE_IN:
        return _TYPE_IN[(t, None)]
    if t == "RUBBER ISOLATOR":
        return "isolator"
    if t == "MULTILINEAR PLASTIC":
        return "multilinear_kinematic"
    return table.get(t, "elastic")


def link_type_header(lk) -> str:
    """The ``TYPE "..." [HYSTYPE "..."]`` text of a non-elastic link."""
    t, h = LINK_TYPE_NAMES.get(lk.link_type, ("LINEAR", None))
    return f"TYPE {q(t)}" + (f"  HYSTYPE {q(h)}" if h else "")


def link_prop_rows(lk, pname: str, u) -> List[str]:
    """Per-DOF ``LINKPROP`` parameter rows of a device link (SI params ->
    file units).  Params with no record stay for the ``$SF`` fallback."""
    p = lk.params or {}
    alpha = float(p.get("alpha", 1.0))
    keys = _LINK_KEYS.get(lk.link_type, [])
    rows: List[str] = []
    for dof in ("U1", "U2", "U3"):
        parts = []
        for dofs, kw, key, qty in keys:
            if dof in dofs and key in p:
                v = float(p[key])
                parts.append(f"{kw} {fmt(v / _cd_factor(u, qty, alpha))}")
        if parts:
            rows.append(f"LINKPROP  {q(pname)}  DOF {q(dof)}  NONLIN \"Yes\""
                        f"  " + "  ".join(parts))
    if lk.link_type in _POINT_TYPES:
        dofs = ("U2", "U3")
        for dof in dofs:
            for d, f in p.get("points") or []:
                rows.append(f"LINKPROP  {q(pname)}  DOF {q(dof)}  DISPL "
                            f"{fmt(u.from_si(d, 'length'))}  FORCE "
                            f"{fmt(u.from_si(f, 'force'))}")
            for d, f in p.get("points_neg") or []:
                rows.append(f"LINKPROP  {q(pname)}  DOF {q(dof)}  DISPL "
                            f"{fmt(u.from_si(d, 'length'))}  FORCE "
                            f"{fmt(u.from_si(f, 'force'))}")
    return rows


def link_type_params(rows, pname: str, ltype: str, u) -> Tuple[str, dict]:
    """(link type, params) of a link property.  A parameterless
    ``T/C FRICTION ISOLATOR`` is the pre-Wave C SkyFrame spelling of a
    triple pendulum (its params then come from the ``$SF`` lines)."""
    if ltype == "elastic":
        return ltype, {}
    par = link_params(rows, pname, ltype, u)
    if ltype == "tc_fp_isolator" and not par:
        return "triple_fp", par
    return ltype, par


def link_params(rows, pname: str, ltype: str, u) -> dict:
    """``params`` of a device link from its ``LINKPROP`` rows (tokens)."""
    keys = _LINK_KEYS.get(ltype, [])
    found: Dict[str, Tuple[str, str]] = {}          # key -> (text, qty)
    pts: List[List[float]] = []
    neg: List[List[float]] = []
    first_pt_dof = None
    for t in rows:
        if len(t) < 2 or t[1].text != pname:
            continue
        kv = dict((k, v[0] if v else "") for k, v in _pairs(t, 2))
        dof = kv.get("DOF", "").upper()
        if "DISPL" in kv and "FORCE" in kv:
            if first_pt_dof is None:
                first_pt_dof = dof
            if dof != first_pt_dof:
                continue
            d, f = _f(kv["DISPL"]), _f(kv["FORCE"])
            if d is None or f is None:
                continue
            if d < 0 or (d == 0 and f < 0):
                neg.append([u.to_si(d, "length"), u.to_si(f, "force")])
            else:
                pts.append([u.to_si(d, "length"), u.to_si(f, "force")])
            continue
        for dofs, kw, key, qty in keys:
            if dof in dofs and kw in kv and key not in found:
                found[key] = (kv[kw], qty)
    out: dict = {}
    alpha = _f(found["alpha"][0]) if "alpha" in found else 1.0
    alpha = 1.0 if alpha is None else alpha
    # canonical key order of the type (required keys, then optional)
    from skyframe.core.model import _LINK_PARAM_KEYS
    req, opt = _LINK_PARAM_KEYS.get(ltype, ((), ()))
    rank = {k: i for i, k in enumerate(tuple(req) + tuple(opt))}
    for _dofs, _kw, key, qty in sorted(keys, key=lambda e: rank.get(e[2],
                                                                    99)):
        if key in found and key not in out:
            v = _f(found[key][0])
            if v is not None:
                fac = _cd_factor(u, qty, alpha)
                out[key] = v if fac == 1.0 else v * fac
    if pts:
        out["points"] = pts
    if neg:
        out["points_neg"] = neg
    return {k: out[k] for k in sorted(out, key=lambda k: rank.get(k, 99))}


# --------------------------------------------------------------------------- #
# hinges
# --------------------------------------------------------------------------- #
_HYST = {"kinematic": "Kinematic", "takeda": "Takeda", "pivot": "Pivot",
         "isotropic": "Isotropic", "concrete": "Concrete"}
_HYST_IN = {v.upper(): k for k, v in _HYST.items()}
_HYST_IN.update({"DEGRADING": "takeda", "BRB HARDENING": "kinematic"})
_LABELS = ("A", "B", "C", "D", "E", "F")
_HDOF = {"M3": "M3", "M2": "M2", "P": "P", "V2": "V2", "V3": "V3",
         "T": "T", "PMM": "PMM", "M2M3": "M2M3", "PMM_fiber": "FiberPM2M3"}
_HDOF_IN = {v.upper(): k for k, v in _HDOF.items()}
_HDOF_IN.update({"PM2M3": "PMM", "FIBERPMM": "PMM_fiber"})
_AUTO_DOF = {"PMM": "PM2M3"}
_AUTO_DOF_IN = {"PM2M3": "PMM", "PMM": "PMM", "M3": "M3", "M2": "M2",
                "P": "P", "V2": "V2", "V3": "V3"}
_TABLE_ITEMS = {"steel_beam": "Steel Beams", "steel_column": "Steel Columns",
                "steel_brace": "Steel Braces", "rc_beam": "Concrete Beams",
                "rc_column": "Concrete Columns",
                "rc_column_shear": "Concrete Columns - Shear"}
_AUTO_PARAM_QTY = {"fy_bar": "stress", "fyt": "stress", "db": "length"}


def _hinge_units(typ: str) -> Tuple[str, str, str]:
    """(force qty, deformation qty, elastic stiffness qty) of a hinge."""
    if typ in ("P", "V2", "V3"):
        return "force", "length", "stiffness"
    return "moment", "none", "rot_stiffness"


def _table_item_in(text: str) -> Optional[str]:
    up = text.upper().strip()
    best = None
    for comp, lab in _TABLE_ITEMS.items():
        if up == lab.upper():
            return comp
        if up.startswith(lab.upper()) and (best is None or len(lab) > len(
                _TABLE_ITEMS[best])):
            best = comp
    if best == "rc_column" and "SHEAR" in up:
        return "rc_column_shear"
    return best


def write_hinge_props(w) -> None:
    """``$ FRAME HINGE PROPERTIES``: one block per hinge property."""
    props = getattr(w.m, "hinge_properties", None) or {}
    if not props:
        return
    w.sec("FRAME HINGE PROPERTIES")
    u = w.u
    for name, p in props.items():
        nm = q(name)
        typ = p.get("type")
        hy = _HYST.get(p.get("hysteresis", "kinematic"), "Kinematic")
        if typ == "auto":
            a = p["auto"]
            w.rec(f"HINGE {nm}  AUTOHINGETYPE {q(a['code'])}  TABLEITEM "
                  f"{q(_TABLE_ITEMS.get(a['component'], a['component']))}"
                  f"  DOF {q(_AUTO_DOF.get(a['dof'], a['dof']))}  HYSTYPE "
                  f"{q(hy)}")
            for k, v in (a.get("params") or {}).items():
                if isinstance(v, str):
                    w.rec(f"HINGE {nm}  AUTOPARAM {q(k)}  TEXT {q(v)}")
                elif v is not None:
                    w.rec(f"HINGE {nm}  AUTOPARAM {q(k)}  VALUE "
                          f"{w.n(v, _AUTO_PARAM_QTY.get(k, 'none'))}")
            for pat, f in (a.get("gravity") or {}).items():
                w.rec(f"HINGE {nm}  GRAVLOADPAT {q(pat)}  SF {fmt(f)}")
            if a.get("P") is not None:
                w.rec(f"HINGE {nm}  AXIALP {w.n(a['P'], 'force')}")
            _hinge_tail(w, nm, p, "moment", "rot_stiffness")
            continue
        fq, dq, kq = _hinge_units(typ)
        if typ == "PMM_fiber":
            w.rec(f"HINGE {nm}  DOF \"FiberPM2M3\"  FROMSECTION \"Yes\"  "
                  f"HYSTYPE {q(hy)}")
        else:
            label = "Moment-Rotation" if fq == "moment" else \
                "Force-Displacement"
            beyond = "Drops To Zero" if p.get("drop_strength", "drops") \
                == "drops" else "Is Extrapolated"
            w.rec(f"HINGE {nm}  BEHAVIOR \"Deformation Controlled\"  DOF "
                  f"{q(_HDOF.get(typ, typ))}  TYPE {q(label)}  HYSTYPE "
                  f"{q(hy)}  BEYONDE {q(beyond)}")
        bb = p.get("backbone")
        sc = p.get("scale") or {}
        yd = sc.get("yield_deformation")
        dscale = None if yd is None else (yd if yd == "auto" else float(yd))

        def dtxt(d, dscale=dscale, dq=dq):
            return fmt(d) if dscale is not None else fmt(u.from_si(d, dq))
        if bb is not None:
            pos = bb["positive"] if isinstance(bb, dict) else bb
            neg = bb["negative"] if isinstance(bb, dict) else bb
            for lab, (d, f) in zip(_LABELS[1:], pos[1:]):
                w.rec(f"HINGE {nm}  {q(lab)}  {dtxt(d)}  {fmt(f)}")
            for lab, (d, f) in zip(_LABELS[1:], neg[1:]):
                w.rec(f"HINGE {nm}  {q('-' + lab)}  {dtxt(-d)}  {fmt(-f)}")
            fk, dk = ("MOMENTSF", "ROTATIONSF") if fq == "moment" else \
                ("FORCESF", "DISPSF")
            yv = w.n(float(sc.get("yield_value", 1.0)), fq)
            if dscale is None:
                dpart = ""
            elif dscale == "auto":
                dpart = '"Auto"'
            else:
                dpart = w.n(dscale, dq)
            sfd = (lambda s: f"  {dk}{s} {dpart}") if dpart else \
                (lambda s: "")
            w.rec(f"HINGE {nm}  {fk}P {yv}{sfd('P')}  {fk}N {yv}{sfd('N')}")
            acc = p.get("acceptance")
            if acc:
                ks = [k for k in ("IO", "LS", "CP") if k in acc]
                w.rec(f"HINGE {nm}  " + "  ".join(
                    f"{k} {dtxt(acc[k])}" for k in ks) + "  " + "  ".join(
                    f"-{k} {dtxt(-acc[k])}" for k in ks))
        _hinge_tail(w, nm, p, fq, kq)
    w.count("hinge_properties", len(props))


def _hinge_tail(w, nm, p, fq, kq) -> None:
    if p.get("k_elastic") is not None:
        w.rec(f"HINGE {nm}  KELASTIC {w.n(p['k_elastic'], kq)}")
    hp = p.get("hysteresis_params")
    if hp:
        w.rec(f"HINGE {nm}  " + "  ".join(
            f"{k.upper().replace('_', '')} {fmt(v)}" for k, v in hp.items()))


def write_hinge_assigns(w) -> None:
    """``$ FRAME HINGE ASSIGNMENTS`` / ``$ FRAME HINGE OVERWRITES``."""
    props = getattr(w.m, "hinge_properties", None) or {}
    rows: List[str] = []
    ovr: List[str] = []
    for mem in w.m.members:
        st = w.obj_story.get(mem.uid)
        if st is None:
            continue
        head = f"HINGEASSIGN {q(mem.uid)}  {q(st)}  "
        h = mem.hinges
        if isinstance(h, list):
            for e in h:
                p = props.get(e.get("property"), {})
                extra = ""
                if p.get("type") == "auto":
                    a = p["auto"]
                    extra = (f"  AUTOHINGETYPE {q(a['code'])}  TABLEITEM "
                             f"{q(_TABLE_ITEMS.get(a['component'], ''))}  "
                             f"DOF {q(_AUTO_DOF.get(a['dof'], a['dof']))}")
                rows.append(head + f"HINGEPROP {q(e['property'])}{extra}  "
                            f"RDISTANCE {fmt(e['relative_distance'])}")
        elif h == "auto_m3":
            for rd in (0, 1):
                rows.append(head + f"AUTOHINGETYPE \"ASCE41-17\"  DOF \"M3\""
                            f"  RDISTANCE {rd}")
        elif h == "fiber_pmm":
            for rd in (0, 1):
                rows.append(head + f"DOF \"FiberPM2M3\"  RDISTANCE {rd}")
        ov = getattr(mem, "hinge_overwrites", None)
        if ov is not None:
            parts = []
            if "auto_subdivide" in ov:
                parts.append(f"AUTOSUBDIVIDE {_yn(ov['auto_subdivide'])}")
            if ov.get("relative_length") is not None:
                parts.append("AUTOSUBDIVIDERELLENGTH "
                             f"{fmt(ov['relative_length'])}")
            ovr.append(f"HINGEOVERWRITE {q(mem.uid)}  {q(st)}  "
                       + "  ".join(parts))
    if rows:
        w.sec("FRAME HINGE ASSIGNMENTS")
        for t in rows:
            w.rec(t)
        w.count("hinge_assigns", len(rows))
    if ovr:
        w.sec("FRAME HINGE OVERWRITES")
        for t in ovr:
            w.rec(t)


def read_hinges(r, d: dict, member_story: dict, w: list) -> None:
    """Hinge properties, member hinge assignments and overwrites."""
    from skyframe.io.e2k import _kv
    u = r.u
    raw: Dict[str, dict] = {}
    for t in r.rows("HINGE"):
        if len(t) < 3:
            continue
        name = t[1].text
        h = raw.setdefault(name, {"kv": {}, "pts": [], "neg": [],
                                  "params": {}, "grav": {}, "acc": {}})
        if t[2].quoted and len(t) >= 5:             # labelled point
            lab = t[2].text.upper()
            dv, fv = _f(t[3].text), _f(t[4].text)
            if dv is None or fv is None:
                continue
            (h["neg"] if lab.startswith("-") else h["pts"]).append(
                (lab.lstrip("-"), abs(dv), abs(fv)))
            continue
        kv = _kv(t, 2)
        if "AUTOPARAM" in kv:
            k = kv["AUTOPARAM"].text
            if "TEXT" in kv:
                h["params"][k] = kv["TEXT"].text
            elif _f(kv.get("VALUE").text if "VALUE" in kv else None) \
                    is not None:
                h["params"][k] = u.to_si(float(kv["VALUE"].text),
                                         _AUTO_PARAM_QTY.get(k, "none"))
            continue
        if "GRAVLOADPAT" in kv:
            h["grav"][kv["GRAVLOADPAT"].text] = _f(kv["SF"].text) \
                if "SF" in kv else 1.0
            continue
        for tok in ("IO", "LS", "CP"):
            if tok in kv and _f(kv[tok].text) is not None:
                h["acc"][tok] = kv[tok].text
        for k, v in kv.items():
            h["kv"].setdefault(k, v.text)
    props: Dict[str, dict] = {}
    for name, h in raw.items():
        kv = h["kv"]
        hy = _HYST_IN.get(kv.get("HYSTYPE", "KINEMATIC").upper(),
                          "kinematic")
        if "AUTOHINGETYPE" in kv:
            comp = _table_item_in(kv.get("TABLEITEM", ""))
            if comp is None:
                w.append(f"hinge {name!r}: table item "
                         f"{kv.get('TABLEITEM')!r} not supported; skipped")
                continue
            a = {"code": kv["AUTOHINGETYPE"], "component": comp,
                 "dof": _AUTO_DOF_IN.get(kv.get("DOF", "M3").upper(), "M3")}
            if h["params"]:
                a["params"] = h["params"]
            if h["grav"]:
                a["gravity"] = h["grav"]
            if "AXIALP" in kv and _f(kv["AXIALP"]) is not None:
                a["P"] = u.to_si(float(kv["AXIALP"]), "force")
            p = {"type": "auto", "auto": a, "hysteresis": hy}
            _read_tail(p, kv, u, "rot_stiffness")
            props[name] = p
            continue
        dof = kv.get("DOF", "M3").upper()
        typ = _HDOF_IN.get(dof)
        if typ is None:
            w.append(f"hinge {name!r}: DOF {dof!r} not supported; skipped")
            continue
        fq, dq, kq = _hinge_units(typ)
        p = {"type": typ, "hysteresis": hy}
        fk, dk = ("MOMENTSF", "ROTATIONSF") if fq == "moment" else \
            ("FORCESF", "DISPSF")
        dsf = kv.get(dk + "P")
        if dsf is None:
            dscale = None
        elif dsf.upper() == "AUTO":
            dscale = "auto"
        else:
            dscale = u.to_si(float(dsf), dq)

        def dval(x, dscale=dscale, dq=dq):
            return abs(float(x)) if dscale is not None else \
                u.to_si(abs(float(x)), dq)
        order = {lab: i for i, lab in enumerate(_LABELS)}
        pos = [[0.0, 0.0]] + [[dval(dv), fv] for _l, dv, fv in sorted(
            h["pts"], key=lambda e: order.get(e[0], 9))]
        neg = [[0.0, 0.0]] + [[dval(dv), fv] for _l, dv, fv in sorted(
            h["neg"], key=lambda e: order.get(e[0], 9))]
        if len(pos) > 1:
            p["backbone"] = pos if (len(neg) == 1 or neg == pos) else \
                {"positive": pos, "negative": neg}
            if isinstance(p["backbone"], dict) and hy in ("kinematic",
                                                          "isotropic"):
                w.append(f"hinge {name!r}: asymmetric backbone with "
                         f"{hy} hysteresis; takeda used")
                p["hysteresis"] = "takeda"
            yv = kv.get(fk + "P")
            p["scale"] = {"yield_value": u.to_si(float(yv), fq)
                          if _f(yv) is not None else 1.0,
                          "yield_deformation": dscale}
        if h["acc"]:
            p["acceptance"] = {k: dval(v) for k, v in h["acc"].items()}
        if "BEYONDE" in kv:
            p["drop_strength"] = "holds" if "EXTRAP" in kv[
                "BEYONDE"].upper() else "drops"
        elif typ in ("PMM", "M2M3"):
            p["drop_strength"] = "holds"
        _read_tail(p, kv, u, kq)
        props[name] = p
    # member assignments
    by_uid: Dict[str, list] = {}
    flags: Dict[str, Set[str]] = {}
    for t in r.rows("HINGEASSIGN"):
        if len(t) < 3:
            continue
        uid = member_story.get((t[1].text, t[2].text))
        if uid is None:
            w.append(f"hinge assign for unknown frame {t[1].text!r}@"
                     f"{t[2].text!r} skipped")
            continue
        kv = _kv(t, 3)
        rd = _f(kv["RDISTANCE"].text) if "RDISTANCE" in kv else None
        if "HINGEPROP" in kv:
            pn = kv["HINGEPROP"].text
            if pn not in props:
                w.append(f"frame {uid!r}: hinge property {pn!r} unknown; "
                         "skipped")
                continue
            if rd is None:
                rd = 0.0 if "IEND" in (kv["LOCATIONTYPE"].text.upper()
                                       if "LOCATIONTYPE" in kv else "I") \
                    else 1.0
            by_uid.setdefault(uid, []).append(
                {"property": pn, "relative_distance": float(rd)})
            continue
        dof = kv["DOF"].text.upper() if "DOF" in kv else ""
        if "TABLEITEM" not in kv:
            if dof in ("FIBERPM2M3",):
                flags.setdefault(uid, set()).add("fiber_pmm")
                continue
            if "AUTOHINGETYPE" in kv and dof == "M3":
                flags.setdefault(uid, set()).add("auto_m3")
                continue
        if "AUTOHINGETYPE" in kv:
            comp = _table_item_in(kv["TABLEITEM"].text
                                  if "TABLEITEM" in kv else "")
            code = kv["AUTOHINGETYPE"].text
            from skyframe.design import hinge_tables as _ht
            if code not in _ht.CODES:
                new = "ASCE41-17" if code.upper().startswith("ASCE41") \
                    else ("FEMA356" if "FEMA" in code.upper() else code)
                msg_ = f"auto hinge table {code} mapped to {new}"
                if new != code and msg_ not in w:
                    w.append(msg_)
                code = new
            adof = _AUTO_DOF_IN.get(dof, "M3")
            if comp is None or code not in _ht.CODES or \
                    comp not in _ht.SUPPORT.get(code, {}) or \
                    adof not in _ht.SUPPORT[code][comp]:
                w.append(f"frame {uid!r}: auto hinge {code} "
                         f"{kv['TABLEITEM'].text if 'TABLEITEM' in kv else ''}"
                         f" {dof} not supported; skipped")
                continue
            pn = f"Auto {code} {_TABLE_ITEMS[comp]} {adof}"
            props.setdefault(pn, {"type": "auto", "auto": {
                "code": code, "component": comp, "dof": adof},
                "hysteresis": "kinematic"})
            by_uid.setdefault(uid, []).append(
                {"property": pn,
                 "relative_distance": float(rd if rd is not None else 0.0)})
            continue
        w.append(f"frame {uid!r}: hinge assignment without a property "
                 "skipped")
    ovr: Dict[str, dict] = {}
    for t in r.rows("HINGEOVERWRITE"):
        if len(t) < 3:
            continue
        uid = member_story.get((t[1].text, t[2].text))
        if uid is None:
            continue
        kv = _kv(t, 3)
        o: dict = {}
        if "AUTOSUBDIVIDE" in kv:
            o["auto_subdivide"] = _is_yes(kv["AUTOSUBDIVIDE"].text)
        rl = _f(kv["AUTOSUBDIVIDERELLENGTH"].text) \
            if "AUTOSUBDIVIDERELLENGTH" in kv else None
        if rl is not None and 0.0 < rl <= 0.5:
            o["relative_length"] = rl
        ovr[uid] = o
    from skyframe.core.user_hinges import normalize_property
    for name in list(props):
        try:
            normalize_property(name, props[name])
        except ValueError as exc:
            w.append(f"hinge {name!r} skipped: {exc}")
            del props[name]
    for uid in list(by_uid):
        by_uid[uid] = [e for e in by_uid[uid] if e["property"] in props]
        if not by_uid[uid]:
            del by_uid[uid]
    if props:
        d["hinge_properties"] = props
    for md in d.get("members", []):
        uid = md["uid"]
        if uid in by_uid:
            md["hinges"] = by_uid[uid]
        elif uid in flags:
            md["hinges"] = "fiber_pmm" if "fiber_pmm" in flags[uid] \
                else "auto_m3"
        if uid in ovr:
            md["hinge_overwrites"] = ovr[uid]


def _read_tail(p: dict, kv: dict, u, kq: str) -> None:
    if "KELASTIC" in kv and _f(kv["KELASTIC"]) is not None:
        p["k_elastic"] = u.to_si(float(kv["KELASTIC"]), kq)
    hp = {}
    for key in ("pinch_x", "pinch_y", "damage1", "damage2", "beta"):
        tag = key.upper().replace("_", "")
        if tag in kv and _f(kv[tag]) is not None:
            hp[key] = float(kv[tag])
    if hp:
        p["hysteresis_params"] = hp


# --------------------------------------------------------------------------- #
# thermal loads
# --------------------------------------------------------------------------- #
def is_temperature(kv: dict) -> bool:
    """True for a TEMPERATURE / TEMPERATUREGRADIENT load record."""
    return "TYPE" in kv and kv["TYPE"].text.upper().startswith("TEMPERAT")


def thermal_rows(w, p) -> Tuple[List[str], List[str], List[str]]:
    """(point, frame, area) temperature rows of one pattern."""
    lc = q(p.name)
    L = w.u.L                                    # grad file = grad SI * L
    pl: List[str] = []
    fl: List[str] = []
    al: List[str] = []
    for tl in p.thermal_loads:
        st = w.obj_story.get(tl.member_uid)
        if st is None:
            continue
        head = f"LINELOAD  {q(tl.member_uid)}  {q(st)}  "
        fl.append(head + f"TYPE \"TEMPERATURE\"  LC {lc}  T {fmt(tl.dT)}")
        for dirn, g in (("2", tl.grad2), ("3", tl.grad3)):
            if g:
                fl.append(head + f"TYPE \"TEMPERATUREGRADIENT\"  DIR "
                          f"{q(dirn)}  LC {lc}  TGRAD {fmt(g * L)}")
    for sl in getattr(p, "shell_thermal_loads", None) or []:
        st = w.obj_story.get(sl.region_uid)
        if st is None:
            continue
        head = f"AREALOAD  {q(sl.region_uid)}  {q(st)}  "
        al.append(head + f"TYPE \"TEMPERATURE\"  LC {lc}  T {fmt(sl.dT)}")
        if sl.grad3:
            al.append(head + f"TYPE \"TEMPERATUREGRADIENT\"  DIR \"3\"  LC "
                      f"{lc}  TGRAD {fmt(sl.grad3 * L)}")
    for jt in getattr(p, "joint_temperatures", None) or []:
        pt = w.p3.get(tuple(float(v) for v in jt.point))
        if pt is None:
            continue
        pl.append(f"POINTLOAD  {q(pt[0])}  {q(pt[1])}  LC {lc}  TYPE "
                  f"\"TEMPERATURE\"  T {fmt(jt.dT)}")
    return pl, fl, al


def read_thermal(r, pats: dict, member_story: dict, area_story: dict,
                 xyz, w: list) -> None:
    """TEMPERATURE / TEMPERATUREGRADIENT point / frame / area loads."""
    from skyframe.io.e2k import _kv
    L = r.u.L

    def grad(kv):
        return (_f(kv["TGRAD"].text) or 0.0) / L if "TGRAD" in kv else 0.0
    for t in r.rows("LINELOAD"):
        if len(t) < 3:
            continue
        kv = _kv(t, 3)
        if not is_temperature(kv):
            continue
        uid = member_story.get((t[1].text, t[2].text))
        p = pats.get(kv["LC"].text) if "LC" in kv else None
        if uid is None or p is None:
            w.append(f"temperature load on {t[1].text!r} skipped")
            continue
        lst = p.setdefault("thermal_loads", [])
        if kv["TYPE"].text.upper() == "TEMPERATURE":
            lst.append({"member_uid": uid,
                        "dT": _f(kv["T"].text) if "T" in kv else 0.0})
            continue
        key = "grad3" if ("DIR" in kv and kv["DIR"].text == "3") \
            else "grad2"
        tgt = next((e for e in reversed(lst) if e["member_uid"] == uid),
                   None)
        if tgt is None:
            tgt = {"member_uid": uid, "dT": 0.0}
            lst.append(tgt)
        tgt[key] = grad(kv)
    for t in r.rows("AREALOAD"):
        if len(t) < 3:
            continue
        kv = _kv(t, 3)
        if not is_temperature(kv):
            continue
        uid = area_story.get(t[1].text)
        p = pats.get(kv["LC"].text) if "LC" in kv else None
        if uid is None or p is None:
            w.append(f"temperature load on area {t[1].text!r} skipped")
            continue
        lst = p.setdefault("shell_thermal_loads", [])
        if kv["TYPE"].text.upper() == "TEMPERATURE":
            lst.append({"region_uid": uid,
                        "dT": _f(kv["T"].text) if "T" in kv else 0.0,
                        "grad3": 0.0})
            continue
        tgt = next((e for e in reversed(lst) if e["region_uid"] == uid),
                   None)
        if tgt is None:
            tgt = {"region_uid": uid, "dT": 0.0, "grad3": 0.0}
            lst.append(tgt)
        tgt["grad3"] = grad(kv)
    for t in r.rows("POINTLOAD"):
        if len(t) < 3:
            continue
        kv = _kv(t, 3)
        if not is_temperature(kv):
            continue
        pt = xyz(t[1].text, t[2].text)
        p = pats.get(kv["LC"].text) if "LC" in kv else None
        if pt is None or p is None:
            w.append(f"joint temperature at {t[1].text!r} skipped")
            continue
        p.setdefault("joint_temperatures", []).append(
            {"point": pt, "dT": _f(kv["T"].text) if "T" in kv else 0.0})


# --------------------------------------------------------------------------- #
# frequency functions
# --------------------------------------------------------------------------- #
_FN_KIND = {"steady_state": "STEADYSTATE", "psd": "PSD"}
_FN_KIND_IN = {"STEADYSTATE": "steady_state", "STEADY STATE":
               "steady_state", "PSD": "psd",
               "POWER SPECTRAL DENSITY": "psd"}


def write_freq_functions(w) -> None:
    fns = getattr(w.m, "frequency_functions", None) or {}
    if not fns or not w.inc.get("cases", True):
        return
    if "$ FUNCTIONS" not in w.lines:
        w.sec("FUNCTIONS")
    for name, fn in fns.items():
        w.rec(f"FUNCTION {q(name)}  FUNCTYPE {q(_FN_KIND[fn.kind])}")
        for f, v in fn.points:
            w.rec(f"FUNCTION {q(name)}  FREQ {fmt(f)}  VALUE {fmt(v)}")
    w.count("functions", len(fns))


def read_freq_functions(r, d: dict) -> None:
    from skyframe.io.e2k import _kv
    fns: Dict[str, dict] = {}
    for t in r.rows("FUNCTION"):
        if len(t) < 2:
            continue
        kv = _kv(t, 2)
        name = t[1].text
        if "FUNCTYPE" in kv:
            k = _FN_KIND_IN.get(kv["FUNCTYPE"].text.upper())
            if k:
                fns[name] = {"name": name, "kind": k, "points": []}
            continue
        if name in fns and "FREQ" in kv and "VALUE" in kv:
            fns[name]["points"].append([float(kv["FREQ"].text),
                                        float(kv["VALUE"].text)])
    if fns:
        d["frequency_functions"] = fns


def is_freq_function(t) -> bool:
    """True for the FUNCTYPE row of a frequency function (the legacy
    reader must not mistake it for a time history)."""
    from skyframe.io.e2k import _kv
    kv = _kv(t, 2)
    return "FUNCTYPE" in kv and kv["FUNCTYPE"].text.upper() in _FN_KIND_IN


# --------------------------------------------------------------------------- #
# cases: writer
# --------------------------------------------------------------------------- #
_ACC = {"X": "U1", "Y": "U2", "Z": "U3"}
_DOF_OUT = {"UX": "U1", "UY": "U2", "UZ": "U3", "RX": "R1", "RY": "R2",
            "RZ": "R3"}
_DOF_IN = {v: k for k, v in _DOF_OUT.items()}
_DOF_IN.update({k: k for k in _DOF_OUT})
_GEO_PO = {"linear": "None", "pdelta": "PDelta", "corotational": "LargeDispl"}
_GEO_NLS = {"none": "None", "p_delta": "PDelta",
            "large_displacement": "LargeDispl"}
_DIST = {"roof_point": "Roof Point", "pattern": "Pattern", "mode": "Mode",
         "uniform_accel": "Uniform Acceleration", "triangular": "Triangular"}
_DIST_IN = {v.upper(): k for k, v in _DIST.items()}
_HMODE = {"column_base": "Column Base", "all_ends": "All Ends",
          "asce41": "ASCE41"}
_HMODE_IN = {v.upper(): k for k, v in _HMODE.items()}
_OPS = {"add": "Add Structure", "remove": "Remove Structure",
        "load": "Load Objects"}
_OPS_IN = {v.upper(): k for k, v in _OPS.items()}
_OPS_IN.update({"ADD": "add", "REMOVE": "remove", "LOAD": "load"})


def _joint(w, pt) -> Optional[str]:
    """``JOINT "p" "level"`` of an existing model point (None if absent)."""
    if pt is None:
        return None
    p = w.p3.get(tuple(float(v) for v in pt))
    return None if p is None else f"JOINT  {q(p[0])}  {q(p[1])}"


def _hinge_rows(w, nm: str, c, always: bool) -> None:
    """Case-level hinge plan (pushover / nonlinear static)."""
    dflt = (c.hinges == "column_base" and not c.My and c.default_My is None
            and c.hardening == 0.02 and not c.hinge_params)
    if dflt and not always:
        return
    extra = "" if c.default_My is None else \
        f"  DEFAULTMY {w.n(c.default_My, 'moment')}"
    w.rec(f"LOADCASE {nm}  HINGEMODE {q(_HMODE.get(c.hinges, c.hinges))}  "
          f"HARDENING {fmt(c.hardening)}{extra}")
    for uid, my in c.My.items():
        w.rec(f"LOADCASE {nm}  HINGEMY {q(uid)}  MY {w.n(my, 'moment')}")
    for k, v in c.hinge_params.items():
        qty = "stress" if k == "fy_bar" else "none"
        w.rec(f"LOADCASE {nm}  HINGEPARAM {q(k)}  VALUE {w.n(v, qty)}")


def _control_row(w, nm: str, mode: str, dof, target, point, story,
                 roof: bool) -> None:
    lc = "Full" if mode == "load_control" else "Displacement"
    parts = [f"LOADCASE {nm}  LOADCONTROL  {q(lc)}  DISPLTYPE  \"Monitored\""]
    j = _joint(w, point)
    if j is not None:
        parts.append("MONITOREDDISPL  \"Joint\"")
    elif story is not None:
        parts.append(f"MONITOREDDISPL  \"Story\"  STORY  {q(story)}")
    elif roof:
        parts.append("MONITOREDDISPL  \"Roof\"")
    if target is not None:
        parts.append(f"DISPLMAG  {w.n(target, 'length')}")
    if dof:
        parts.append(f"DOF  {q(_DOF_OUT.get(dof, dof))}")
    if j is not None:
        parts.append(j)
    w.rec("  ".join(parts))


def _saved_row(w, nm: str, steps: int) -> None:
    w.rec(f"LOADCASE {nm}  RESULTSSAVED  \"Multiple\"  MINSAVED  "
          f"{int(steps)}  MAXSAVED  {int(steps)}")


def write_cases(w, used: set) -> int:
    """Pushover, nonlinear static, staged and frequency LOADCASE records
    (inside ``$ LOAD CASES``); returns the number of cases written."""
    from skyframe.core.model import MODAL_CASE
    m = w.m
    n = 0
    for c in (getattr(m, "nonlinear_static_cases", None) or {}).values():
        nm = q(c.name)
        init = c.start_from or "NONE"
        w.rec(f"LOADCASE {nm}  TYPE  \"Nonlinear Static\"  INITCOND  "
              f"{q(init)}  MODALCASE  {q(MODAL_CASE)}")
        for ld in c.loads:
            w.rec(f"LOADCASE {nm}  LOADPAT  {q(ld['pattern'])}  SF  "
                  f"{fmt(ld['scale'])}")
        w.rec(f"LOADCASE {nm}  NLGEOMTYPE  "
              f"{q(_GEO_NLS.get(c.geometric, 'None'))}")
        disp = c.load_application == "displacement_control"
        _control_row(w, nm, c.load_application, c.control_dof,
                     c.target_disp if disp else None, c.control_point,
                     c.control_story, False)
        _saved_row(w, nm, c.steps)
        if c.control_tower is not None:
            w.rec(f"LOADCASE {nm}  TOWER  {q(c.control_tower)}")
        _hinge_rows(w, nm, c, False)
        n += 1
    for c in (m.pushover_cases or {}).values():
        nm = q(c.name)
        init = c.start_from or "NONE"
        if c.gravity:
            g = c.name + _GRAV_SUFFIX
            while g in used:
                g += "_"
            used.add(g)
            gq = q(g)
            w.rec(f"LOADCASE {gq}  TYPE  \"Nonlinear Static\"  INITCOND  "
                  f"\"NONE\"  MODALCASE  {q(MODAL_CASE)}")
            for pat, f in c.gravity.items():
                w.rec(f"LOADCASE {gq}  LOADPAT  {q(pat)}  SF  {fmt(f)}")
            w.rec(f"LOADCASE {gq}  NLGEOMTYPE  "
                  f"{q(_GEO_PO.get(c.geometric, 'None'))}")
            w.rec(f"LOADCASE {gq}  LOADCONTROL  \"Full\"  DISPLTYPE  "
                  "\"Monitored\"  MONITOREDDISPL  \"Roof\"")
            w.rec(f"LOADCASE {gq}  RESULTSSAVED  \"Final\"")
            init = g if not c.start_from else init
        w.rec(f"LOADCASE {nm}  TYPE  \"Nonlinear Static\"  INITCOND  "
              f"{q(init)}  MODALCASE  {q(MODAL_CASE)}")
        acc = _ACC.get(c.direction, "U1")
        if c.load_distribution == "pattern" and c.pattern:
            w.rec(f"LOADCASE {nm}  LOADPAT  {q(c.pattern)}  SF  1")
        elif c.load_distribution == "mode":
            w.rec(f"LOADCASE {nm}  MODE  {int(c.mode_number or 0)}  SF  1")
        elif c.load_distribution == "uniform_accel":
            w.rec(f"LOADCASE {nm}  ACCEL  {q(acc)}  SF  1")
        w.rec(f"LOADCASE {nm}  NLGEOMTYPE  "
              f"{q(_GEO_PO.get(c.geometric, 'None'))}")
        disp = c.control_mode != "load_control"
        _control_row(w, nm, c.control_mode, c.control_dof,
                     c.target_disp if disp else None, c.control_point,
                     c.control_story, True)
        _saved_row(w, nm, c.steps)
        extra = ""
        if c.k is not None:
            extra += f"  K {fmt(c.k)}"
        if not disp or c.target_load != 1.0:
            extra += f"  LOADMAG {fmt(c.target_load)}"
        if c.control_tower is not None:
            extra += f"  TOWER {q(c.control_tower)}"
        dist = _DIST.get(c.load_distribution, c.load_distribution)
        w.rec(f"LOADCASE {nm}  LOADDIST {q(dist)}  DIR {q(c.direction)}  "
              f"TARGETDRIFT {fmt(c.target_drift)}{extra}")
        _hinge_rows(w, nm, c, True)
        n += 1
    for c in (m.staged_cases or {}).values():
        nm = q(c.name)
        w.rec(f"LOADCASE {nm}  TYPE  \"Nonlinear Staged Construction\"  "
              "INITCOND  \"NONE\"")
        if isinstance(c.stages, str):
            w.rec(f"LOADCASE {nm}  AUTOSTAGE  \"Per Story\"")
            w.rec(f"LOADCASE {nm}  LOADPAT  {q(c.pattern)}  SF  1")
        else:
            for st in c.stages:
                sn = q(st.get("name", ""))
                dur = f"  DURATION  {fmt(st['duration_days'])}" \
                    if "duration_days" in st else ""
                w.rec(f"LOADCASE {nm}  STAGE  {sn}{dur}")
                for op in st.get("operations") or []:
                    kind = op.get("op")
                    row = (f"LOADCASE {nm}  STAGE  {sn}  OPERATION  "
                           f"{q(_OPS.get(kind, kind))}  OBJTYPE  \"Group\"  "
                           f"OBJNAME  {q(op.get('group', ''))}")
                    if "age_days" in op:
                        row += f"  AGE  {fmt(op['age_days'])}"
                    if "pattern" in op:
                        row += (f"  LOADTYPE  \"Load Pattern\"  LOADNAME  "
                                f"{q(op['pattern'])}")
                    if "scale" in op:
                        row += f"  SF  {fmt(op['scale'])}"
                    w.rec(row)
        for pat, f in c.include_live.items():
            w.rec(f"LOADCASE {nm}  FINALLOADPAT  {q(pat)}  SF  {fmt(f)}")
        n += 1
    g_fac = w.u.factor("accel")
    for kind, cases in (("Steady State",
                         getattr(m, "steady_state_cases", None) or {}),
                        ("Power Spectral Density",
                         getattr(m, "psd_cases", None) or {})):
        for c in cases.values():
            nm = q(c.name)
            w.rec(f"LOADCASE {nm}  TYPE  {q(kind)}  INITCOND  \"PRESET\"  "
                  f"MODALCASE  {q(MODAL_CASE)}")
            for ld in c.loads:
                if ld.is_accel:
                    head = f"ACCEL  {q(_DOF_OUT.get(ld.direction, 'U1'))}"
                    sf = ld.scale / g_fac
                else:
                    head = f"LOADPAT  {q(ld.pattern)}"
                    sf = ld.scale
                w.rec(f"LOADCASE {nm}  {head}  SF  {fmt(sf)}  FUNC  "
                      f"{q(ld.function)}  PHASE  {fmt(ld.phase_deg)}")
            w.rec(f"LOADCASE {nm}  FREQRANGE  {fmt(c.freq_start_hz)}  "
                  f"FREQEND  {fmt(c.freq_end_hz)}  NUMFREQ  {int(c.n_freq)}"
                  f"  MODALFREQ  {_yn(c.modal_refine)}")
            for f in c.frequencies:
                w.rec(f"LOADCASE {nm}  ADDFREQ  {fmt(f)}")
            w.rec(f"LOADCASE {nm}  SOLUTIONMETHOD  "
                  f"{q(c.method.capitalize())}  NUMMODES  {int(c.num_modes)}")
            w.rec(f"LOADCASE {nm}  DAMPTYPE  "
                  f"{q(c.damping_type.capitalize())}  CONSTDAMP  "
                  f"{fmt(c.damping)}")
            for pt in c.output_points:
                j = w.p3.get(tuple(float(v) for v in pt))
                if j is not None:
                    w.rec(f"LOADCASE {nm}  OUTPUTJOINT  {q(j[0])}  "
                          f"{q(j[1])}")
            n += 1
    return n


# --------------------------------------------------------------------------- #
# cases: reader
# --------------------------------------------------------------------------- #
def _case_rows(r) -> Dict[str, List[List[Tuple[str, list]]]]:
    out: Dict[str, List[List[Tuple[str, list]]]] = {}
    for t in r.rows("LOADCASE"):
        if len(t) < 2:
            continue
        out.setdefault(t[1].text, []).append(_pairs(t, 2))
    return out


def _first(rows, key) -> Optional[list]:
    for row in rows:
        for k, v in row:
            if k == key:
                return v
    return None


def _row_with(rows, key) -> Optional[Dict[str, list]]:
    for row in rows:
        if row and row[0][0] == key:
            return dict(row)
    return None


def _monitor(rows, r, xyz, out: dict, w: list, name: str) -> None:
    lc = _row_with(rows, "LOADCONTROL")
    if lc is None:
        return
    mon = (lc.get("MONITOREDDISPL") or [""])[0].upper()
    if "JOINT" in lc and len(lc["JOINT"]) == 2:
        p = xyz(*lc["JOINT"])
        if p is None:
            w.append(f"case {name!r}: monitored joint {lc['JOINT']!r} "
                     "unknown; roof used")
        else:
            out["control_point"] = p
    elif mon == "STORY" and "STORY" in lc:
        out["control_story"] = lc["STORY"][0]
    if "DOF" in lc:
        out["control_dof"] = _DOF_IN.get(lc["DOF"][0].upper(), "UX")
    if "DISPLMAG" in lc and _f(lc["DISPLMAG"][0]) is not None:
        out["target_disp"] = r.u.to_si(float(lc["DISPLMAG"][0]), "length")
    out["_control"] = (lc.get("LOADCONTROL") or ["Full"])[0].upper()


def _steps(rows, default: int) -> int:
    sv = _row_with(rows, "RESULTSSAVED")
    if sv and "MINSAVED" in sv and _f(sv["MINSAVED"][0]):
        return int(float(sv["MINSAVED"][0]))
    return default


def _hinges_in(rows, r, out: dict) -> None:
    hm = _row_with(rows, "HINGEMODE")
    if hm:
        out["hinges"] = _HMODE_IN.get(hm["HINGEMODE"][0].upper(),
                                      "column_base")
        if "HARDENING" in hm:
            out["hardening"] = float(hm["HARDENING"][0])
        if "DEFAULTMY" in hm:
            out["default_My"] = r.u.to_si(float(hm["DEFAULTMY"][0]),
                                          "moment")
    my = {}
    hp = {}
    for row in rows:
        dd = dict(row)
        if row and row[0][0] == "HINGEMY" and "MY" in dd:
            my[dd["HINGEMY"][0]] = r.u.to_si(float(dd["MY"][0]), "moment")
        if row and row[0][0] == "HINGEPARAM" and "VALUE" in dd:
            k = dd["HINGEPARAM"][0]
            hp[k] = r.u.to_si(float(dd["VALUE"][0]),
                              "stress" if k == "fy_bar" else "none")
    if my:
        out["My"] = my
    if hp:
        out["hinge_params"] = hp


def _geo(rows, table: dict, default: str) -> str:
    g = _first(rows, "NLGEOMTYPE")
    if not g:
        return default
    up = g[0].upper()
    if "LARGE" in up:
        return table["LARGE"]
    if "PDELTA" in up.replace("-", "").replace(" ", ""):
        return table["PDELTA"]
    return default


_GEO_RANK = {"none": 0, "p_delta": 1, "large_displacement": 2}


def _unify_chain_geometry(nls: dict, w: list) -> None:
    """SkyFrame solves a start_from chain in one domain, so every case of
    a chain needs the same NLGEOMTYPE (ETABS allows mixing): the whole
    chain takes its most nonlinear option (reported)."""
    changed = True
    guard = 0
    while changed and guard < 100:
        changed = False
        guard += 1
        for name, c in nls.items():
            chain, cur, seen = [], name, set()
            while cur in nls and cur not in seen:
                seen.add(cur)
                chain.append(cur)
                cur = nls[cur].get("start_from")
            geos = {nls[n]["geometric"] for n in chain}
            if len(geos) > 1:
                top = max(geos, key=lambda g: _GEO_RANK.get(g, 0))
                for n in chain:
                    if nls[n]["geometric"] != top:
                        w.append(f"nonlinear static case {n!r}: geometric "
                                 f"nonlinearity set to {top!r} to match "
                                 f"its case chain")
                        nls[n]["geometric"] = top
                changed = True


def read_cases(r, d: dict, xyz, w: list) -> Set[str]:
    """Build pushover / nonlinear static / staged / frequency cases from
    the LOADCASE records; returns the names handled here (the legacy case
    loop skips them)."""
    rows_by = _case_rows(r)
    types = {n: ((_first(rows, "TYPE") or [""])[0].upper())
             for n, rows in rows_by.items()}
    taken: Set[str] = set()
    pushover, nls, staged, ss, psd = {}, {}, {}, {}, {}
    g_fac = r.u.factor("accel")
    # classify the nonlinear static cases
    po_names = []
    for name, rows in rows_by.items():
        if types[name] != "NONLINEAR STATIC":
            continue
        heads = {row[0][0] for row in rows if row}
        if heads & {"LOADDIST", "MODE", "ACCEL"}:
            po_names.append(name)
        elif "LOADCONTROL" in heads:
            nls[name] = None
    aux = set()
    for name in po_names:
        init = (_first(rows_by[name], "INITCOND") or ["NONE"])[0]
        if init.endswith(_GRAV_SUFFIX) or (
                _GRAV_SUFFIX in init and init in nls):
            aux.add(init)
    for name in list(nls):
        if name in aux:
            del nls[name]
    # nonlinear static
    for name in list(nls):
        rows = rows_by[name]
        c: dict = {"name": name, "loads": []}
        for row in rows:
            dd = dict(row)
            if row and row[0][0] == "LOADPAT":
                c["loads"].append({"pattern": dd["LOADPAT"][0],
                                   "scale": _f((dd.get("SF") or ["1"])[0])
                                   or 0.0})
        mon: dict = {}
        _monitor(rows, r, xyz, mon, w, name)
        ctl = mon.pop("_control", "FULL")
        c["load_application"] = "displacement_control" \
            if ctl.startswith("DISPL") else "load_control"
        if c["load_application"] == "load_control":
            mon.pop("target_disp", None)
        c.update(mon)
        c["steps"] = _steps(rows, 10)
        c["geometric"] = _geo(rows, {"LARGE": "large_displacement",
                                     "PDELTA": "p_delta"}, "none")
        init = (_first(rows, "INITCOND") or ["NONE"])[0]
        if init.upper() not in _NONE_CASES:
            c["start_from"] = init
        tw = _first(rows, "TOWER")
        if tw:
            c["control_tower"] = tw[0]
        _hinges_in(rows, r, c)
        nls[name] = c
        taken.add(name)
    for name in list(nls):
        sf = nls[name].get("start_from")
        if sf is not None and sf not in nls:
            w.append(f"nonlinear static case {name!r}: initial case {sf!r} "
                     "is not a nonlinear static case; started from zero")
            nls[name].pop("start_from")
    _unify_chain_geometry(nls, w)
    # pushover
    for name in po_names:
        rows = rows_by[name]
        c = {"name": name}
        ld = _row_with(rows, "LOADDIST") or {}
        mode = _first(rows, "MODE")
        acc = _first(rows, "ACCEL")
        pat = _first(rows, "LOADPAT")
        if "LOADDIST" in ld:
            dist = _DIST_IN.get(ld["LOADDIST"][0].upper(), "roof_point")
        elif mode is not None:
            dist = "mode"
        elif acc is not None:
            dist = "uniform_accel"
        else:
            dist = "pattern" if pat else "roof_point"
        mon: dict = {}
        _monitor(rows, r, xyz, mon, w, name)
        ctl = mon.pop("_control", "DISPLACEMENT")
        if "DIR" in ld:
            c["direction"] = ld["DIR"][0].upper()
        elif acc is not None:
            c["direction"] = {"U2": "Y", "UY": "Y"}.get(acc[0].upper(), "X")
        else:
            c["direction"] = {"UY": "Y"}.get(mon.get("control_dof", "UX"),
                              "X")
        init = (_first(rows, "INITCOND") or ["NONE"])[0]
        grav = {}
        if init.upper() not in _NONE_CASES:
            src = rows_by.get(init)
            if init in aux or (init in nls and src is not None):
                for row in src or []:
                    dd = dict(row)
                    if row and row[0][0] == "LOADPAT":
                        grav[dd["LOADPAT"][0]] = grav.get(
                            dd["LOADPAT"][0], 0.0) + \
                            (_f((dd.get("SF") or ["1"])[0]) or 0.0)
            elif init in rows_by and types.get(init) in ("LINEAR STATIC",
                                                         "NONLINEAR STATIC"):
                c["start_from"] = init
            else:
                w.append(f"pushover {name!r}: initial case {init!r} not "
                         "supported; started from zero")
        c["gravity"] = grav
        c["target_drift"] = float(ld["TARGETDRIFT"][0]) \
            if "TARGETDRIFT" in ld else 0.02
        c["steps"] = _steps(rows, 100)
        c["geometric"] = _geo(rows, {"LARGE": "corotational",
                                     "PDELTA": "pdelta"}, "linear")
        c["load_distribution"] = dist
        if dist == "pattern" and pat:
            c["pattern"] = pat[0]
        if dist == "mode" and mode and int(float(mode[0])) > 0:
            c["mode_number"] = int(float(mode[0]))
        if "K" in ld:
            c["k"] = float(ld["K"][0])
        if ctl.startswith("FULL"):
            c["control_mode"] = "load_control"
            mon.pop("target_disp", None)
        if "LOADMAG" in ld:
            c["target_load"] = float(ld["LOADMAG"][0])
        if "TOWER" in ld:
            c["control_tower"] = ld["TOWER"][0]
        c.update(mon)
        _hinges_in(rows, r, c)
        pushover[name] = c
        taken.add(name)
    taken |= aux
    # staged construction
    for name, rows in rows_by.items():
        if not types[name].startswith("NONLINEAR STAGED") and \
                types[name] != "STAGED CONSTRUCTION":
            continue
        c = {"name": name}
        stages: Dict[str, dict] = {}
        live = {}
        for row in rows:
            dd = dict(row)
            head = row[0][0] if row else ""
            if head == "LOADPAT":
                c["pattern"] = dd["LOADPAT"][0]
            elif head == "FINALLOADPAT":
                live[dd["FINALLOADPAT"][0]] = float((dd.get("SF") or
                                                     ["1"])[0])
            elif head == "STAGE":
                sn = dd["STAGE"][0]
                st = stages.setdefault(sn, {"name": sn, "operations": []})
                if "OPERATION" not in dd:
                    if "DURATION" in dd:
                        st["duration_days"] = float(dd["DURATION"][0])
                        st["operations"] = st.pop("operations")
                    continue
                op = {"op": _OPS_IN.get(dd["OPERATION"][0].upper(), "add"),
                      "group": (dd.get("OBJNAME") or [""])[0]}
                if "AGE" in dd:
                    op["age_days"] = float(dd["AGE"][0])
                if "LOADNAME" in dd:
                    op["pattern"] = dd["LOADNAME"][0]
                if "SF" in dd:
                    op["scale"] = float(dd["SF"][0])
                st["operations"].append(op)
        c["stages"] = list(stages.values()) if stages else "per_story"
        c["include_live"] = live
        staged[name] = c
        taken.add(name)
    # frequency domain
    for name, rows in rows_by.items():
        typ = types[name]
        if typ not in ("STEADY STATE", "POWER SPECTRAL DENSITY", "PSD"):
            continue
        c = {"name": name, "loads": []}
        for row in rows:
            dd = dict(row)
            head = row[0][0] if row else ""
            if head in ("LOADPAT", "ACCEL"):
                sf = float((dd.get("SF") or ["1"])[0])
                ld = {"scale": sf, "function": (dd.get("FUNC") or [""])[0],
                      "phase_deg": float((dd.get("PHASE") or ["0"])[0])}
                if head == "ACCEL":
                    ld.update(pattern="accel", direction=_DOF_IN.get(
                        dd["ACCEL"][0].upper(), "UX"), scale=sf * g_fac)
                else:
                    ld.update(pattern=dd["LOADPAT"][0], direction="")
                c["loads"].append(ld)
            elif head == "FREQRANGE":
                c["freq_start_hz"] = float(dd["FREQRANGE"][0])
                if "FREQEND" in dd:
                    c["freq_end_hz"] = float(dd["FREQEND"][0])
                if "NUMFREQ" in dd:
                    c["n_freq"] = int(float(dd["NUMFREQ"][0]))
                if "MODALFREQ" in dd:
                    c["modal_refine"] = _is_yes(dd["MODALFREQ"][0])
            elif head == "ADDFREQ":
                c.setdefault("frequencies", []).append(
                    float(dd["ADDFREQ"][0]))
            elif head == "SOLUTIONMETHOD":
                c["method"] = dd["SOLUTIONMETHOD"][0].lower()
                if "NUMMODES" in dd:
                    c["num_modes"] = int(float(dd["NUMMODES"][0]))
            elif head == "DAMPTYPE":
                c["damping_type"] = dd["DAMPTYPE"][0].lower()
                if "CONSTDAMP" in dd:
                    c["damping"] = float(dd["CONSTDAMP"][0])
            elif head == "OUTPUTJOINT" and len(dd["OUTPUTJOINT"]) == 2:
                p = xyz(*dd["OUTPUTJOINT"])
                if p is not None:
                    c.setdefault("output_points", []).append(p)
        (ss if typ == "STEADY STATE" else psd)[name] = c
        taken.add(name)
    if nls:
        d["nonlinear_static_cases"] = nls
    if pushover:
        d["pushover_cases"] = pushover
    if staged:
        d["staged_cases"] = staged
    if ss:
        d["steady_state_cases"] = ss
    if psd:
        d["psd_cases"] = psd
    return taken


# --------------------------------------------------------------------------- #
# salvage: an ETABS file whose nonlinear data SkyFrame cannot accept
# --------------------------------------------------------------------------- #
_FAMILIES = ("pushover_cases", "nonlinear_static_cases", "staged_cases",
             "steady_state_cases", "psd_cases", "frequency_functions",
             "hinge_properties")


def _drop_hinges(d: dict) -> None:
    d.pop("hinge_properties", None)
    for md in d.get("members", []):
        if isinstance(md.get("hinges"), list):
            md["hinges"] = "none"
        md.pop("hinge_overwrites", None)


def salvage(d: dict, w: list):
    """Build the model after removing the Wave C items it rejects (the
    item named in the validation error, else a whole family); returns
    the BuildingModel or None when the failure lies elsewhere."""
    from skyframe.core.model import BuildingModel
    for _ in range(200):
        try:
            return BuildingModel.from_dict(d)
        except Exception as exc:                     # noqa: BLE001
            msg = str(exc)
        hit = None
        for fam in _FAMILIES:
            for name in sorted(d.get(fam) or {}, key=len, reverse=True):
                if f"{name!r}" in msg or f" {name}:" in msg or \
                        f" {name} " in msg:
                    hit = (fam, name)
                    break
            if hit:
                break
        if hit:
            fam, name = hit
            w.append(f"{fam.replace('_', ' ')[:-1]} {name!r} skipped: "
                     f"{msg}")
            if fam == "hinge_properties":
                del d[fam][name]
                for md in d.get("members", []):
                    if isinstance(md.get("hinges"), list):
                        md["hinges"] = [e for e in md["hinges"]
                                        if e.get("property") != name] \
                            or "none"
            else:
                del d[fam][name]
            continue
        fam = next((f for f in _FAMILIES if d.get(f)), None)
        if fam is None:
            return None
        w.append(f"all {fam.replace('_', ' ')} skipped: {msg}")
        if fam == "hinge_properties":
            _drop_hinges(d)
        else:
            d.pop(fam)
    return None
