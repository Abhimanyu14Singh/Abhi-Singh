"""Open-structure wind (ETABS Assign > Frame Loads > Open Structure Wind
Parameters + the "Open Structure" wind load pattern) — CONTRACT "Open
structure wind (frame drag loads)".

Per frame member ambient-wind exposure parameters are stored on
``FrameMember.open_wind`` (``None`` = not assigned, the default; emitted by
``to_dict`` only when set) in the canonical form::

    {"include": true,        # false = assigned but shielded out entirely
     "cf": null | Cf > 0,    # drag / force coefficient (null = generator cf)
     "width": "auto" | b > 0,# exposed width (m); "auto" = section depth h
                             #   (falls back to b when h is 0)
     "shielding": 0 < s <= 1}# shielding / solidity reduction factor

The generator (:func:`generate`) writes an ordinary ``kind="wind"``
:class:`LoadPattern` of member loads (ASCE 7 Ch. 29 "other structures",
Eq. 29.4-1 ``F = qz (Kd) G Cf Af``) per unit member length::

    w(z) = p(z) * width * shielding * proj
    p(z) = qz G Cf            (ASCE 7-16: qz = 0.613 Kz Kzt Kd Ke V^2)
    p(z) = qz Kd G Cf         (ASCE 7-22: qz = 0.613 Kz Kzt Ke V^2)
    proj = sqrt(1 - (t . d)^2)

with ``t`` the member unit axis and ``d`` the horizontal wind unit vector
(``proj`` = projected area normal to the wind per unit length: a member
parallel to the wind carries nothing, a vertical leg carries the full
width).  Kz comes from the existing machinery: ASCE 7-16
:func:`skyframe.core.builder.wind_kz` (2.01 (z/zg)^(2/alpha), 4.6 m floor)
and ASCE 7-22 :func:`skyframe.core.autolateral.asce22_kz` (2.41 ...,
15 ft floor, zg cap); Ke from :func:`autolateral.asce22_ke` (ground
elevation ``ze``) unless given.  z = member point elevation - ``z_ground``.

Loads act along the wind (``global_x`` / ``global_y`` components of d).
Horizontal members get one uniform load; inclined / vertical members are
split into ``segments`` equal trapezoids whose end ordinates are the exact
w(z) at the segment ends (piecewise-linear in qz).  An optional trussed-
tower default Cf from the solidity ratio (ASCE 7-16/7-22 Table 29.4-2:
square ``4.0 e^2 - 5.9 e + 4.0``, triangle ``3.4 e^2 - 4.7 e + 3.4``) can
replace the generator ``cf``.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from .autolateral import asce22_ke
from .model import BuildingModel, LoadPattern, MemberLoad

CODES = ("asce7_16", "asce7_22")
DIRECTIONS = {"X": 0.0, "Y": 90.0, "-X": 180.0, "-Y": 270.0}
MEMBER_SELECTIONS = ("assigned", "all")
TOWER_SHAPES = ("square", "triangle")
DEFAULT_PARAMS = {"include": True, "cf": None, "width": "auto",
                  "shielding": 1.0}


# ----------------------------------------------------------------- helpers
def _num(label: str, v, lo: Optional[float] = None, lo_open: bool = True,
         hi: Optional[float] = None) -> float:
    if (isinstance(v, bool) or not isinstance(v, (int, float))
            or not math.isfinite(v)):
        raise ValueError(f"{label} must be a finite number")
    v = float(v)
    if lo is not None and (v <= lo if lo_open else v < lo):
        raise ValueError(f"{label} must be {'>' if lo_open else '>='} {lo}")
    if hi is not None and v > hi:
        raise ValueError(f"{label} must be <= {hi}")
    return v


def normalize_params(d) -> Optional[dict]:
    """Canonical per-member parameter dict (``None`` passes through).
    Raises ``ValueError`` on bad values / unknown keys."""
    if d is None:
        return None
    if not isinstance(d, dict):
        raise ValueError("open_wind must be an object or null")
    bad = sorted(set(d) - set(DEFAULT_PARAMS))
    if bad:
        raise ValueError(f"open_wind: unknown key(s) {bad}")
    inc = d.get("include", True)
    if not isinstance(inc, bool):
        raise ValueError("open_wind.include must be true/false")
    cf = d.get("cf")
    cf = None if cf is None else _num("open_wind.cf", cf, 0.0)
    w = d.get("width", "auto")
    if w != "auto":
        w = _num("open_wind.width", w, 0.0)
    sh = _num("open_wind.shielding", d.get("shielding", 1.0), 0.0, hi=1.0)
    return {"include": inc, "cf": cf, "width": w, "shielding": sh}


def member_to_dict(m) -> dict:
    ow = getattr(m, "open_wind", None)
    return {} if ow is None else {"open_wind": dict(ow)}


def model_from_dict(model, d: dict) -> None:
    """Attach ``open_wind`` from the member dicts (absent = None)."""
    for m, md in zip(model.members, d.get("members") or []):
        if isinstance(md, dict) and md.get("open_wind") is not None:
            m.open_wind = md["open_wind"]


def validate_model(model) -> None:
    """Re-normalise every member's ``open_wind`` in place."""
    for m in model.members:
        try:
            m.open_wind = normalize_params(getattr(m, "open_wind", None))
        except ValueError as exc:
            raise ValueError(f"Member {m.uid}: {exc}") from None


def assign(model: BuildingModel, uids, params) -> List[str]:
    """Assign (``params`` dict) or clear (``None``) the parameters on the
    named members; returns the uids changed.  All-or-nothing."""
    if not isinstance(uids, (list, tuple)) or not uids:
        raise ValueError("'uids' must be a non-empty list of member uids")
    by = {m.uid: m for m in model.members}
    missing = [u for u in uids if u not in by]
    if missing:
        raise ValueError(f"unknown member uid(s): {missing}")
    norm = normalize_params(params)
    for u in uids:
        by[u].open_wind = None if norm is None else dict(norm)
    return list(uids)


def trussed_tower_cf(solidity: float, shape: str = "square") -> float:
    """ASCE 7-16/7-22 Table 29.4-2 trussed-tower force coefficient
    (flat-sided members, wind normal to a face): square ``4.0 e^2 - 5.9 e
    + 4.0``, triangle ``3.4 e^2 - 4.7 e + 3.4``; e = solidity ratio (0, 1]."""
    e = _num("solidity", solidity, 0.0, hi=1.0)
    if shape == "square":
        return 4.0 * e * e - 5.9 * e + 4.0
    if shape == "triangle":
        return 3.4 * e * e - 4.7 * e + 3.4
    raise ValueError(f"tower shape must be one of {TOWER_SHAPES}")


def exposed_width(model: BuildingModel, m, params: dict) -> float:
    w = params["width"]
    if w != "auto":
        return float(w)
    sec = model.sections.get(m.section)
    if sec is not None:
        if sec.h and sec.h > 0.0:
            return float(sec.h)
        if sec.b and sec.b > 0.0:
            return float(sec.b)
    raise ValueError(f"member {m.uid!r}: section {m.section!r} has no depth "
                     "- give an explicit open_wind width")


def _qz_fn(code: str, V: float, exposure: str, Kzt: float, Kd: float,
           Ke: float):
    """(qz(z) kPa, pressure factor such that p = qz * factor * G * Cf)."""
    if code == "asce7_16":
        from .builder import WIND_EXPOSURES, wind_kz
        if exposure not in WIND_EXPOSURES:
            raise ValueError(f"exposure must be one of "
                             f"{sorted(WIND_EXPOSURES)}")
        return (lambda z: 0.613 * wind_kz(z, exposure) * Kzt * Kd * Ke
                * V * V / 1000.0), 1.0
    if code == "asce7_22":
        from .autolateral import ASCE22_WIND_EXPOSURES, asce22_kz
        if exposure not in ASCE22_WIND_EXPOSURES:
            raise ValueError(f"exposure must be one of "
                             f"{sorted(ASCE22_WIND_EXPOSURES)}")
        return (lambda z: 0.613 * asce22_kz(z, exposure) * Kzt * Ke
                * V * V / 1000.0), Kd
    raise ValueError(f"code must be one of {CODES}")


def _selected(model: BuildingModel, members) -> List[tuple]:
    """[(member, params)] loaded by the generator."""
    out = []
    if members == "assigned" or members == "all":
        for m in model.members:
            p = normalize_params(getattr(m, "open_wind", None))
            if p is None:
                if members == "assigned":
                    continue
                p = dict(DEFAULT_PARAMS)
            if p["include"]:
                out.append((m, p))
        return out
    if isinstance(members, (list, tuple)) and members:
        by = {m.uid: m for m in model.members}
        missing = [u for u in members if u not in by]
        if missing:
            raise ValueError(f"unknown member uid(s): {missing}")
        for u in members:
            m = by[u]
            p = normalize_params(getattr(m, "open_wind", None)) \
                or dict(DEFAULT_PARAMS)
            if p["include"]:
                out.append((m, p))
        return out
    raise ValueError("'members' must be 'assigned', 'all' or a non-empty "
                     "list of member uids")


# --------------------------------------------------------------- generator
def compute(model: BuildingModel, V: float, code: str = "asce7_22",
            exposure: str = "C", direction: str = "X",
            angle: Optional[float] = None, Kzt: float = 1.0,
            Kd: float = 0.85, ze: float = 0.0, Ke: Optional[float] = None,
            G: float = 0.85, cf: float = 2.0, members="assigned",
            segments: int = 4, z_ground: float = 0.0,
            tower: Optional[dict] = None) -> dict:
    """Hand-calculation summary (no model change)::

        {"code", "V", "exposure", "angle", "d": [dx, dy], "G", "Kd", "Kzt",
         "Ke", "cf_default", "segments",
         "members": [{"uid", "cf", "width", "shielding", "proj", "z_i",
                      "z_j", "qz_i", "qz_j", "p_i", "p_j", "w_i", "w_j",
                      "F", "segments": [[a, b, w_a, w_b], ...]}],
         "FX", "FY", "F"}

    ``w_*`` (kN/m) are line-load magnitudes along the wind at the member
    ends; ``F`` the member's resultant (kN); FX/FY the pattern totals.
    """
    V = _num("V", V, 0.0)
    Kzt = _num("Kzt", Kzt, 0.0)
    Kd = _num("Kd", Kd, 0.0)
    G = _num("G", G, 0.0)
    cf = _num("cf", cf, 0.0)
    ze = _num("ze", ze)
    z_ground = _num("z_ground", z_ground)
    Ke = asce22_ke(ze) if Ke is None else _num("Ke", Ke, 0.0)
    if (isinstance(segments, bool) or not isinstance(segments, int)
            or not 1 <= segments <= 50):
        raise ValueError("segments must be an integer 1..50")
    if angle is None:
        if direction not in DIRECTIONS:
            raise ValueError(f"direction must be one of {list(DIRECTIONS)}")
        ang = DIRECTIONS[direction]
    else:
        ang = _num("angle", angle)
    if tower is not None:
        if not isinstance(tower, dict):
            raise ValueError("tower must be {shape, solidity}")
        cf = trussed_tower_cf(tower.get("solidity"),
                              tower.get("shape", "square"))
    qz, kfac = _qz_fn(code, V, exposure, Kzt, Kd, Ke)
    rad = math.radians(ang)
    dx, dy = math.cos(rad), math.sin(rad)
    if abs(dx) < 1e-12:
        dx = 0.0
    if abs(dy) < 1e-12:
        dy = 0.0
    rows = []
    FX = FY = 0.0
    for m, p in _selected(model, members):
        L = m.length
        if L <= 0.0:
            continue
        t = [(m.pj[k] - m.pi[k]) / L for k in range(3)]
        c = t[0] * dx + t[1] * dy
        proj = math.sqrt(max(0.0, 1.0 - c * c))
        if proj < 1e-9:
            continue                    # parallel to the wind
        mcf = p["cf"] if p["cf"] is not None else cf
        width = exposed_width(model, m, p)
        k = kfac * G * mcf * width * p["shielding"] * proj

        def zh(s: float) -> float:
            return max(0.0, m.pi[2] + s * (m.pj[2] - m.pi[2]) - z_ground)

        horizontal = abs(m.pj[2] - m.pi[2]) <= 1e-9
        nseg = 1 if horizontal else segments
        segs = []
        F = 0.0
        for i in range(nseg):
            a, b = i / nseg, (i + 1) / nseg
            wa = qz(zh(a)) * k
            wb = wa if horizontal else qz(zh(b)) * k
            segs.append([a, b, wa, wb])
            F += 0.5 * (wa + wb) * (b - a) * L
        qi, qj = qz(zh(0.0)), qz(zh(1.0))
        rows.append({"uid": m.uid, "cf": mcf, "width": width,
                     "shielding": p["shielding"], "proj": proj,
                     "z_i": zh(0.0), "z_j": zh(1.0), "qz_i": qi, "qz_j": qj,
                     "p_i": qi * kfac * G * mcf, "p_j": qj * kfac * G * mcf,
                     "w_i": qi * k, "w_j": qj * k, "F": F,
                     "segments": segs})
        FX += F * dx
        FY += F * dy
    return {"code": code, "V": V, "exposure": exposure, "angle": ang,
            "d": [dx, dy], "G": G, "Kd": Kd, "Kzt": Kzt, "Ke": Ke,
            "cf_default": cf, "segments": segments, "members": rows,
            "FX": FX, "FY": FY, "F": math.hypot(FX, FY)}


def write_pattern(model: BuildingModel, name: str, summary: dict,
                  add_case: bool = True) -> LoadPattern:
    """Store the summary as a ``kind="wind"`` member-load pattern
    (replacing ``name``); with ``add_case`` a same-named linear static
    case ``{name: 1.0}`` is created when no case of that name exists."""
    if not isinstance(name, str) or not name.strip() or len(name) > 60:
        raise ValueError("pattern name must be a non-empty string "
                         "(max 60 chars)")
    if not summary["members"]:
        raise ValueError("no frame member receives open-structure wind "
                         "(assign Open Structure Wind Parameters or use "
                         "members='all')")
    dx, dy = summary["d"]
    pat = LoadPattern(name, "wind")
    for row in summary["members"]:
        for a, b, wa, wb in row["segments"]:
            for comp, dirn in ((dx, "global_x"), (dy, "global_y")):
                if comp == 0.0:
                    continue
                if wa == wb:
                    pat.member_loads.append(MemberLoad(
                        row["uid"], kind="udl", w=wa * comp, a=a, b=b,
                        direction=dirn))
                else:
                    pat.member_loads.append(MemberLoad(
                        row["uid"], kind="trapezoid", w=wa * comp,
                        w2=wb * comp, a=a, b=b, direction=dirn))
    model.patterns[name] = pat
    if add_case and name not in model.cases:
        model.add_case(name, {name: 1.0})
    return pat


def generate(model: BuildingModel, name: str = "OWIND",
             add_case: bool = True, **params) -> LoadPattern:
    """Compute and store the pattern; returns it."""
    return write_pattern(model, name, compute(model, **params), add_case)


_PARAM_KEYS = ("V", "code", "exposure", "direction", "angle", "Kzt", "Kd",
               "ze", "Ke", "G", "cf", "members", "segments", "z_ground",
               "tower")


def params_from_body(body) -> tuple:
    """(name, add_case, params) from a request body."""
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")
    name = body.get("name", "OWIND")
    add_case = body.get("add_case", True)
    if not isinstance(add_case, bool):
        raise ValueError("'add_case' must be true/false")
    params = {k: v for k, v in body.items() if k not in ("name", "add_case")}
    bad = sorted(set(params) - set(_PARAM_KEYS))
    if bad:
        raise ValueError(f"unknown parameter(s): {bad}")
    if "V" not in params:
        raise ValueError("'V' (basic wind speed, m/s) is required")
    return name, add_case, params


def member_param_rows(model: BuildingModel) -> List[Dict]:
    """[{uid, kind, section, **params, width_eff}] for assigned members."""
    out = []
    for m in model.members:
        p = getattr(m, "open_wind", None)
        if p is None:
            continue
        try:
            weff = exposed_width(model, m, p)
        except ValueError:
            weff = None
        out.append({"uid": m.uid, "kind": m.kind, "section": m.section,
                    **p, "width_eff": weff})
    return out
