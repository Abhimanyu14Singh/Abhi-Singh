"""Static seismic eccentricity overrides per story / diaphragm (ETABS Load
Pattern > Seismic > Modify Lateral Load > "Overwrite Eccentricities") --
CONTRACT "Static seismic eccentricity overrides".

``LoadPattern.ecc_overrides`` (``None`` = none, the legacy path)::

    {"<story>": ratio | {"length": e},
     "<story>/<diaphragm>": ratio | {"length": e}, ...}

* a number is an eccentricity RATIO that replaces ``|ecc|`` (or ``|ecc_y|``
  for the fy torque) at that story / diaphragm: ``Mz = F * ratio *
  B_perp`` with ``B_perp`` the same plan dimension the pattern uses
  (story / tower extents for the master-node and semi-rigid paths, the
  diaphragm's own extents for a named diaphragm);
* ``{"length": e}`` is the ETABS overwrite: an absolute eccentricity
  ``e`` (m) used for both directions, ``Mz = F * e``;
* the SIGN comes from the pattern (``ecc`` for fx, ``ecc_y`` for fy;
  ``+`` when zero), so the "+ ecc" / "- ecc" variants keep their sense;
* a ``"story/diaphragm"`` key wins over the ``"story"`` key; ``"(free)"``
  addresses the free (non-diaphragm) nodes of a named-diaphragm story;
* overrides act only when ``accidental_torsion`` is on (like ``ecc``).

Ratios are limited to [0, 0.5] and lengths to >= 0.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

ECC_MAX = 0.5


def _finite(v) -> bool:
    return (not isinstance(v, bool) and isinstance(v, (int, float))
            and math.isfinite(v))


def normalize_value(v):
    if _finite(v):
        return float(v)
    if isinstance(v, dict) and set(v) == {"length"} and _finite(v["length"]):
        return {"length": float(v["length"])}
    raise ValueError("an eccentricity override must be a ratio or "
                     '{"length": e}')


def validate(model, pat) -> None:
    ov = getattr(pat, "ecc_overrides", None)
    if ov is None:
        return
    if not isinstance(ov, dict):
        raise ValueError(f"Pattern {pat.name}: ecc_overrides must be an "
                         "object {story | story/diaphragm: ratio | "
                         '{"length": e}}')
    names = {s.name for s in model.stories}
    dias = set(getattr(model, "diaphragms", None) or {})
    for k, v in ov.items():
        st, _, dia = str(k).partition("/")
        if st not in names or (dia and dia not in dias and dia != "(free)"):
            raise ValueError(f"Pattern {pat.name}: ecc_overrides names "
                             f"unknown story (or story/diaphragm) {k!r}")
        try:
            nv = normalize_value(v)
        except ValueError as exc:
            raise ValueError(f"Pattern {pat.name}: ecc_overrides[{k!r}]: "
                             f"{exc}") from None
        if isinstance(nv, float) and not 0.0 <= nv <= ECC_MAX:
            raise ValueError(f"Pattern {pat.name}: ecc_overrides[{k!r}] "
                             f"ratio must be in [0, {ECC_MAX}]")
        if isinstance(nv, dict) and nv["length"] < 0.0:
            raise ValueError(f"Pattern {pat.name}: ecc_overrides[{k!r}] "
                             "length must be >= 0")


def has_overrides(pat) -> bool:
    return bool(getattr(pat, "ecc_overrides", None))


def lookup(pat, story: str, dia: Optional[str] = None):
    """The override value for a story (and diaphragm), or None."""
    ov = getattr(pat, "ecc_overrides", None) or {}
    if dia:
        key = f"{story}/{dia}"
        if key in ov:
            return normalize_value(ov[key])
    if story in ov:
        return normalize_value(ov[story])
    return None


def _sgn(e: float) -> float:
    return -1.0 if e < 0.0 else 1.0


def torques(pat, story: str, dia: Optional[str], fx: float, fy: float,
            lx: float, ly: float, ecc: float, ecc_y: float
            ) -> Tuple[float, float]:
    """(Mz from fx, Mz from fy) at one story / diaphragm.  Without an
    override this is exactly the legacy ``(fx*ecc*ly, fy*ecc_y*lx)``."""
    ov = lookup(pat, story, dia)
    if ov is None:
        return fx * ecc * ly, fy * ecc_y * lx
    if isinstance(ov, dict):
        e = abs(ov["length"])
        return fx * _sgn(ecc) * e, fy * _sgn(ecc_y) * e
    r = abs(ov)
    return fx * _sgn(ecc) * r * ly, fy * _sgn(ecc_y) * r * lx


def story_torque_table(model, pat) -> list:
    """Hand-check rows: per story force of ``pat`` the applied torques of
    the master-node (non-named) path: ``[{story, fx, fy, ecc_x, ecc_y,
    mz_x, mz_y, override}]`` using the model plan extents."""
    lx, ly = model.plan_extents()
    ecc = getattr(pat, "ecc", 0.05)
    ecc_y = ecc if getattr(pat, "ecc_y", None) is None else pat.ecc_y
    rows = []
    for sf in pat.story_forces:
        mx_, my_ = (torques(pat, sf.story, None, sf.fx, sf.fy, lx, ly, ecc,
                            ecc_y)
                    if getattr(pat, "accidental_torsion", False)
                    else (0.0, 0.0))
        rows.append({"story": sf.story, "fx": sf.fx, "fy": sf.fy,
                     "mz_x": mx_, "mz_y": my_,
                     "override": lookup(pat, sf.story)})
    return rows


def engine_fn(pat, ecc: float, ecc_y: float):
    """Callback for ``engine.diaphragms.apply_story_force`` (None when the
    pattern has no overrides -> the legacy expressions run untouched)."""
    if not has_overrides(pat):
        return None

    def fn(story, dia, gx, gy, lx, ly):
        return torques(pat, story, dia, gx, gy, lx, ly, ecc, ecc_y)
    return fn
