"""Response-spectrum completeness (ETABS parity): eccentricity, U3 / angled
excitation, several directions in one case and CQC3 / Absolute directional
combination.  Model side (fields, validation, JSON); the engine side lives in
:mod:`skyframe.engine.rs_ext`.  CONTRACT "Response spectrum: eccentricity,
U3 / angle, directional combination, shell forces".

New optional :class:`~skyframe.core.model.ResponseSpectrumCase` fields (all
default to the exact legacy behaviour and are written to JSON only when they
differ from their default, so legacy model files stay byte-identical):

* ``angle``        excitation angle in plan, degrees (CCW from +X).  ``X``
                   (= U1) acts along ``(cos a, sin a)``, ``Y`` (= U2) along
                   ``(-sin a, cos a)``; ``Z`` (= U3) is vertical and ignores it.
* ``loads``        several directions in ONE case (ETABS "Loads Applied"):
                   ``[{"dir": "X"|"Y"|"Z", "scale": 1.0, "function": ""}]``.
                   Each load uses its own ``function`` ("" = the case's
                   function / inline spectrum) and the scale
                   ``case.scale * load.scale``.  When non-empty it replaces
                   the single ``direction``.
* ``dir_combo``    directional combination of the loads: ``SRSS`` (default),
                   ``CQC3`` (Menun & Der Kiureghian, horizontal pair, U3 by
                   SRSS) or ``ABS`` (absolute, with ``dir_sf``).
* ``dir_sf``       ABS scale factor (1 = plain absolute sum, 0.3 = 100/30).
* ``ecc``          accidental eccentricity ratio (ETABS diaphragm
                   eccentricity, e.g. 0.05): +/- story torques from the RS
                   story (diaphragm) forces x ``ecc`` x the dimension
                   perpendicular to the excitation, solved statically and
                   added in absolute value to the RS envelope.
* ``ecc_overrides`` ``{story | "story/diaphragm": ratio}`` overrides of
                   ``ecc`` (a named-diaphragm key wins over its story key).
* ``ecc_ax``       ASCE 7-16 §12.8.4.3 torsional amplification
                   ``Ax = (dmax / 1.2 davg)^2`` in [1, 3] applied to the
                   torques (one extra static solve).
"""

from __future__ import annotations

import math
from typing import Dict, List

RS_LOAD_DIRS = ("X", "Y", "Z")
RS_DIR_COMBOS = ("SRSS", "CQC3", "ABS")
RS_ECC_MAX = 0.5
QUADRATIC_METHODS = ("CQC", "SRSS", "DSC")

DEFAULTS = {"angle": 0.0, "loads": [], "dir_combo": "SRSS", "dir_sf": 1.0,
            "ecc": 0.0, "ecc_overrides": {}, "ecc_ax": False}


def _finite(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def norm_load(d) -> dict:
    """A load entry as stored (``dir`` / ``scale`` / ``function``)."""
    if not isinstance(d, dict):
        raise ValueError("RS loads entries must be objects "
                         "{dir, scale, function}")
    out = {"dir": str(d.get("dir", "")).upper(),
           "scale": float(d.get("scale", 1.0)),
           "function": str(d.get("function", "") or "")}
    return out


def to_dict_extra(rs) -> dict:
    """The non-default extension keys of ``rs`` (merged into to_dict)."""
    d: dict = {}
    if float(getattr(rs, "angle", 0.0)) != 0.0:
        d["angle"] = float(rs.angle)
    loads = getattr(rs, "loads", None) or []
    if loads:
        d["loads"] = [dict(norm_load(x)) for x in loads]
    if getattr(rs, "dir_combo", "SRSS") != "SRSS":
        d["dir_combo"] = rs.dir_combo
    if float(getattr(rs, "dir_sf", 1.0)) != 1.0:
        d["dir_sf"] = float(rs.dir_sf)
    if float(getattr(rs, "ecc", 0.0)) != 0.0:
        d["ecc"] = float(rs.ecc)
    ov = getattr(rs, "ecc_overrides", None) or {}
    if ov:
        d["ecc_overrides"] = {str(k): float(v) for k, v in ov.items()}
    if getattr(rs, "ecc_ax", False):
        d["ecc_ax"] = True
    return d


def from_dict_kwargs(rd: dict) -> dict:
    """Constructor kwargs for the extension fields of one RS case dict."""
    return {
        "angle": float(rd.get("angle", 0.0)),
        "loads": [norm_load(x) for x in (rd.get("loads") or [])],
        "dir_combo": str(rd.get("dir_combo", "SRSS")),
        "dir_sf": float(rd.get("dir_sf", 1.0)),
        "ecc": float(rd.get("ecc", 0.0)),
        "ecc_overrides": {str(k): float(v) for k, v in
                          (rd.get("ecc_overrides") or {}).items()},
        "ecc_ax": bool(rd.get("ecc_ax", False)),
    }


def effective_loads(rs) -> List[dict]:
    """The case's load list: ``loads`` or the single legacy direction."""
    loads = getattr(rs, "loads", None) or []
    if loads:
        return [norm_load(x) for x in loads]
    return [{"dir": rs.direction, "scale": 1.0, "function": ""}]


def is_extended(rs) -> bool:
    """True when the case needs the extended engine path (anything other
    than one X or Y load at angle 0)."""
    return bool(getattr(rs, "loads", None)) or rs.direction == "Z" \
        or float(getattr(rs, "angle", 0.0)) != 0.0


def has_ecc(rs) -> bool:
    return (float(getattr(rs, "ecc", 0.0)) != 0.0
            or any(float(v) != 0.0 for v in
                   (getattr(rs, "ecc_overrides", None) or {}).values()))


def validate(model, rs) -> None:
    """Validate the extension fields of RS case ``rs`` (ValueError)."""
    nm = f"RS case {rs.name}"
    if rs.direction not in RS_LOAD_DIRS:
        raise ValueError(f"{nm}: direction must be X|Y|Z, got "
                         f"{rs.direction!r}")
    if not _finite(rs.angle):
        raise ValueError(f"{nm}: angle must be a finite number of degrees")
    if not isinstance(rs.loads, list):
        raise ValueError(f"{nm}: loads must be a list")
    seen = set()
    for k, ld in enumerate(rs.loads):
        if not isinstance(ld, dict):
            raise ValueError(f"{nm}: loads[{k}] must be an object")
        if ld.get("dir") not in RS_LOAD_DIRS:
            raise ValueError(f"{nm}: loads[{k}].dir must be X|Y|Z, got "
                             f"{ld.get('dir')!r}")
        if ld["dir"] in seen:
            raise ValueError(f"{nm}: direction {ld['dir']} is listed twice "
                             "in loads")
        seen.add(ld["dir"])
        if not _finite(ld.get("scale", 1.0)):
            raise ValueError(f"{nm}: loads[{k}].scale must be finite")
        fn = ld.get("function", "") or ""
        if fn and fn not in model.spectrum_functions:
            raise ValueError(f"{nm}: loads[{k}].function {fn!r} is not a "
                             "defined spectrum_function")
        if not fn and not rs.function and not rs.spectrum:
            raise ValueError(f"{nm}: loads[{k}] has no spectrum (name a "
                             "function or give the case a spectrum)")
    if rs.dir_combo not in RS_DIR_COMBOS:
        raise ValueError(f"{nm}: dir_combo must be one of "
                         f"{'|'.join(RS_DIR_COMBOS)}, got {rs.dir_combo!r}")
    if not (_finite(rs.dir_sf) and 0.0 <= rs.dir_sf <= 1.0):
        raise ValueError(f"{nm}: dir_sf must be in [0, 1], got "
                         f"{rs.dir_sf!r}")
    dirs = [ld["dir"] for ld in effective_loads(rs)]
    if rs.dir_combo == "CQC3":
        if not ("X" in dirs and "Y" in dirs):
            raise ValueError(f"{nm}: CQC3 needs both horizontal loads "
                             "(X = U1 and Y = U2) in loads")
        if (rs.combo_method not in QUADRATIC_METHODS
                or getattr(rs, "rigid_response", False)
                or getattr(rs, "include_missing_mass", False)):
            raise ValueError(f"{nm}: CQC3 needs a CQC, SRSS or DSC modal "
                             "combination without rigid response or "
                             "missing mass")
    for key, v in [("ecc", rs.ecc)] + [
            (f"ecc_overrides[{s!r}]", x)
            for s, x in (rs.ecc_overrides or {}).items()]:
        if not (_finite(v) and 0.0 <= v <= RS_ECC_MAX):
            raise ValueError(f"{nm}: {key} must be in [0, {RS_ECC_MAX}], "
                             f"got {v!r}")
    names = {s.name for s in model.stories}
    dias = set((getattr(model, "diaphragms", None) or {}))
    for s in (rs.ecc_overrides or {}):
        st, _, dia = str(s).partition("/")
        if st not in names or (dia and dia not in dias and dia != "(free)"):
            raise ValueError(f"{nm}: ecc_overrides names unknown story "
                             f"(or story/diaphragm) {s!r}")
    if not isinstance(rs.ecc_ax, bool):
        raise ValueError(f"{nm}: ecc_ax must be true or false")
    if has_ecc(rs) and not any(d in ("X", "Y") for d in dirs):
        raise ValueError(f"{nm}: eccentricity needs a horizontal (X/Y) "
                         "excitation")


def dir_vector(d: str, angle_deg: float):
    """Unit excitation vector (ex, ey, ez) of load direction ``d``."""
    if d == "Z":
        return (0.0, 0.0, 1.0)
    if angle_deg == 0.0:
        return (1.0, 0.0, 0.0) if d == "X" else (0.0, 1.0, 0.0)
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    return (c, s, 0.0) if d == "X" else (-s, c, 0.0)


def validate_combo(model, name: str, combo: Dict[str, str]) -> None:
    """Extra checks of an ``rs_combos`` entry using the CQC3 method: the two
    cases must be single horizontal excitations along perpendicular
    directions sharing one modal analysis and one quadratic modal
    combination (the cross-correlation needs a common rho matrix)."""
    if combo.get("method") != "CQC3":
        return
    a = model.rs_cases[combo["name_x"]]
    b = model.rs_cases[combo["name_y"]]
    for rs in (a, b):
        if rs.loads or rs.direction == "Z":
            raise ValueError(f"RS combo {name}: CQC3 needs two single "
                             f"horizontal cases ({rs.name!r} is not)")
        if (rs.combo_method not in QUADRATIC_METHODS or rs.rigid_response
                or rs.include_missing_mass):
            raise ValueError(f"RS combo {name}: CQC3 needs CQC, SRSS or DSC "
                             "modal combination without rigid response or "
                             "missing mass")
    va = dir_vector(a.direction, a.angle)
    vb = dir_vector(b.direction, b.angle)
    if abs(va[0] * vb[0] + va[1] * vb[1]) > 1e-9:
        raise ValueError(f"RS combo {name}: CQC3 needs perpendicular "
                         "excitation directions")
    if (a.num_modes != b.num_modes or a.combo_method != b.combo_method
            or a.damping != b.damping or a.dsc_td != b.dsc_td):
        raise ValueError(f"RS combo {name}: CQC3 needs both cases to use "
                         "the same modes, modal combination and damping")


def summary(rs) -> Dict[str, object]:
    """Short description used by the results ``rs`` block."""
    return {"loads": effective_loads(rs), "angle": float(rs.angle),
            "dir_combo": rs.dir_combo, "dir_sf": float(rs.dir_sf)}
