"""ASCE 7-16 / 7-22 §12.10 diaphragm design forces Fpx -- CONTRACT
"Diaphragm design forces (ASCE 7 12.10)".

``method = "12.10.1"`` (default) -- §12.10.1.1, Eqs. 12.10-1/2/3::

    Fpx = (sum_{i=x}^{n} F_i / sum_{i=x}^{n} w_i) * w_px
    0.2 SDS Ie w_px  <=  Fpx  <=  0.4 SDS Ie w_px

with ``F_i`` the story forces of the source seismic pattern (fx or fy by
``direction``; the ELF forces of §12.8.3), ``w_i`` the story seismic
weights (``model.compute_story_masses() * g``, as the ELF generator, or
``weights`` {story: kN}) and ``w_px`` the weight tributary to the
diaphragm at level x (``wpx`` {story: kN}, default ``w_x``).  Sums run from
level x to the top level n.

``method = "12.10.3"`` -- the alternative design provisions (§12.10.3.2,
Eqs. 12.10-4 .. 12.10-13)::

    Fpx = Cpx / Rs * w_px  >=  0.2 SDS Ie w_px
    Cp0 = 0.4 SDS Ie                         (base)
    Cpi = max(Cp0, 0.9 Gm1 Omega0 Cs)        (at 0.8 hn)
    Cpn = sqrt((Gm1 Omega0 Cs)^2 + (Gm2 Cs2)^2) >= Cpi   (at hn)
    Gm1 = 1 + zs/2 (1 - 1/N),  Gm2 = 0.9 zs (1 - 1/N)^2
    Cs2 = min((0.15 N + 0.25) Ie SDS, Ie SDS, Ie SD1 / (0.03 (N - 1)))
          (Cs2 = 0 for N = 1)

``Cpx`` is linear in height from Cp0 (base) to Cpi (0.8 hn) and from Cpi
to Cpn (hn); N = number of stories above the base; Cs the §12.8 response
coefficient; zs the mode-shape factor (0.3 BRBF, 0.7 moment frames, 0.85
dual systems, 1.0 other); Rs the diaphragm design force reduction factor
(Table 12.10.3-1).

``BuildingModel.fpx_settings`` (optional, emitted only when set) stores
the inputs the "Diaphragm Design Forces" table uses::

    {"SDS", "Ie", "patterns": [names], "method"?, "wpx"?: {story: kN},
     "weights"?: {story: kN}, "Rs"?, "Cs"?, "Omega0"?, "zs"?, "SD1"?}

:func:`generate_patterns` writes one story-force pattern per level
(``"<source>_Fpx_<story>"``, kind "quake", the force at that level only,
with the source pattern's direction sign and accidental-torsion settings)
-- each diaphragm is designed for its own Fpx, applied alone.
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional

METHODS = ("12.10.1", "12.10.3")
_KEYS = ("SDS", "Ie", "patterns", "method", "wpx", "weights", "Rs", "Cs",
         "Omega0", "zs", "SD1")


def _num(v) -> bool:
    return (not isinstance(v, bool) and isinstance(v, (int, float))
            and math.isfinite(v))


def _pos(label, v) -> float:
    if not _num(v) or v <= 0.0:
        raise ValueError(f"Fpx: {label} must be a finite value > 0")
    return float(v)


def normalize(s: Optional[dict]) -> dict:
    if s is None:
        s = {}
    if not isinstance(s, dict):
        raise ValueError("fpx_settings must be an object")
    bad = [k for k in s if k not in _KEYS]
    if bad:
        raise ValueError(f"fpx_settings: unknown key(s) {bad}")
    out = dict(s)
    m = out.get("method", "12.10.1")
    if m not in METHODS:
        raise ValueError(f"fpx_settings.method must be one of {METHODS}")
    for k in ("SDS", "Ie"):
        if k in out:
            _pos(k, out[k])
    for k in ("Rs", "Cs", "Omega0", "zs", "SD1"):
        if k in out and out[k] is not None:
            _pos(k, out[k])
    for k in ("wpx", "weights"):
        v = out.get(k)
        if v is not None and (not isinstance(v, dict) or not all(
                _num(x) and x >= 0 for x in v.values())):
            raise ValueError(f"fpx_settings.{k} must map story -> kN >= 0")
    p = out.get("patterns")
    if p is not None and (not isinstance(p, list)
                          or not all(isinstance(x, str) for x in p)):
        raise ValueError("fpx_settings.patterns must be a list of names")
    return out


def validate(model) -> None:
    s = getattr(model, "fpx_settings", None)
    if not s:
        return
    s = normalize(s)
    for p in s.get("patterns") or []:
        if p not in model.patterns:
            raise ValueError(f"fpx_settings: unknown load pattern {p!r}")
    names = {st.name for st in model.stories}
    for k in ("wpx", "weights"):
        for st in (s.get(k) or {}):
            if st not in names:
                raise ValueError(f"fpx_settings.{k}: unknown story {st!r}")


def _story_weights(model, weights: Optional[Dict[str, float]]):
    from .model import G_ACCEL
    if weights:
        return {s.name: float(weights.get(s.name, 0.0))
                for s in model.stories}
    masses = model.compute_story_masses()
    return {s.name: masses.get(s.name, 0.0) * G_ACCEL
            for s in model.stories}


def _direction(pat, direction: Optional[str]) -> str:
    if direction in ("X", "Y"):
        return direction
    sx = sum(abs(sf.fx) for sf in pat.story_forces)
    sy = sum(abs(sf.fy) for sf in pat.story_forces)
    return "Y" if sy > sx else "X"


def _alt_cpx(h: float, hn: float, cp0: float, cpi: float,
             cpn: float) -> float:
    if hn <= 0.0:
        return cp0
    z = h / hn
    if z <= 0.8:
        return cp0 + (cpi - cp0) * z / 0.8
    return cpi + (cpn - cpi) * (z - 0.8) / 0.2


def compute(model, pattern: str, SDS: float, Ie: float = 1.0,
            direction: Optional[str] = None,
            weights: Optional[Dict[str, float]] = None,
            wpx: Optional[Dict[str, float]] = None,
            method: str = "12.10.1", Rs: Optional[float] = None,
            Cs: Optional[float] = None, Omega0: Optional[float] = None,
            zs: Optional[float] = None, SD1: Optional[float] = None
            ) -> dict:
    """Per-story Fpx of one seismic pattern (hand-calculation summary)::

        {"pattern", "direction", "method", "SDS", "Ie", "stories": [
          {"story", "h", "w", "F", "sum_F", "sum_w", "wpx", "Fpx_eq",
           "Fpx_min", "Fpx_max", "Fpx", "governs", ("Cpx")}, ...]}

    rows top-down (ETABS order); ``governs`` is "12.10-1", "min" or
    "max" (12.10.3: "12.10-4" / "min")."""
    if pattern not in model.patterns:
        raise ValueError(f"Unknown load pattern {pattern!r}")
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    SDS, Ie = _pos("SDS", SDS), _pos("Ie", Ie)
    pat = model.patterns[pattern]
    d = _direction(pat, direction)
    w = _story_weights(model, weights)
    F: Dict[str, float] = {}
    for sf in pat.story_forces:
        F[sf.story] = F.get(sf.story, 0.0) + abs(sf.fx if d == "X"
                                                  else sf.fy)
    stories = sorted(model.stories, key=lambda s: s.elevation)
    hn = max((s.elevation for s in stories), default=0.0)
    alt = None
    if method == "12.10.3":
        for lab, v in (("Rs", Rs), ("Cs", Cs), ("Omega0", Omega0),
                       ("zs", zs), ("SD1", SD1)):
            _pos(lab, v)
        N = len(stories)
        g1 = 1.0 + zs / 2.0 * (1.0 - 1.0 / N)
        g2 = 0.9 * zs * (1.0 - 1.0 / N) ** 2
        if N == 1:
            cs2 = 0.0
        else:
            cs2 = min((0.15 * N + 0.25) * Ie * SDS, Ie * SDS,
                      Ie * SD1 / (0.03 * (N - 1)))
        cp0 = 0.4 * SDS * Ie
        cpi = max(cp0, 0.9 * g1 * Omega0 * Cs)
        cpn = max(math.sqrt((g1 * Omega0 * Cs) ** 2 + (g2 * cs2) ** 2), cpi)
        alt = {"N": N, "Gm1": g1, "Gm2": g2, "Cs2": cs2, "Cp0": cp0,
               "Cpi": cpi, "Cpn": cpn, "Rs": float(Rs), "Cs": float(Cs),
               "Omega0": float(Omega0), "zs": float(zs), "SD1": float(SD1)}
    rows: List[dict] = []
    for k, s in enumerate(stories):
        above = stories[k:]
        sF = sum(F.get(t.name, 0.0) for t in above)
        sW = sum(w.get(t.name, 0.0) for t in above)
        wp = float((wpx or {}).get(s.name, w.get(s.name, 0.0)))
        lo = 0.2 * SDS * Ie * wp
        hi = 0.4 * SDS * Ie * wp
        row = {"story": s.name, "h": float(s.elevation),
               "w": float(w.get(s.name, 0.0)), "F": float(F.get(s.name, 0.0)),
               "sum_F": float(sF), "sum_w": float(sW), "wpx": wp,
               "Fpx_min": lo, "Fpx_max": hi}
        if alt is None:
            eq = sF / sW * wp if sW > 0.0 else 0.0
            val = min(max(eq, lo), hi)
            gov = "12.10-1" if lo <= eq <= hi else ("min" if eq < lo
                                                     else "max")
            row.update({"Fpx_eq": eq, "Fpx": val, "governs": gov})
        else:
            cpx = _alt_cpx(s.elevation, hn, alt["Cp0"], alt["Cpi"],
                           alt["Cpn"])
            eq = cpx / alt["Rs"] * wp
            val = max(eq, lo)
            row.update({"Cpx": cpx, "Fpx_eq": eq, "Fpx": val,
                        "Fpx_max": None,
                        "governs": "12.10-4" if eq >= lo else "min"})
        rows.append(row)
    rows.reverse()
    out = {"pattern": pattern, "direction": d, "method": method,
           "SDS": SDS, "Ie": Ie, "stories": rows}
    if alt is not None:
        out["alternative"] = alt
    return out


def compute_settings(model, settings: Optional[dict] = None) -> List[dict]:
    """:func:`compute` for every pattern of ``fpx_settings`` (or the given
    settings).  Patterns default to every seismic ("quake") pattern with
    story forces."""
    s = normalize(settings if settings is not None
                  else getattr(model, "fpx_settings", None))
    if "SDS" not in s:
        return []
    names = s.get("patterns") or [
        n for n, p in model.patterns.items()
        if p.kind in ("quake", "seismic") and p.story_forces
        and "_Fpx_" not in n]
    kw = {k: s.get(k) for k in ("Rs", "Cs", "Omega0", "zs", "SD1")}
    return [compute(model, n, s["SDS"], s.get("Ie", 1.0),
                    weights=s.get("weights"), wpx=s.get("wpx"),
                    method=s.get("method", "12.10.1"), **kw)
            for n in names if n in model.patterns]


def generate_patterns(model, pattern: str, SDS: float, Ie: float = 1.0,
                      prefix: Optional[str] = None, copy_ecc: bool = True,
                      **kw) -> List[str]:
    """Write one story-force pattern per level carrying that level's Fpx
    (replacing same-named patterns).  Returns the pattern names."""
    from .model import LoadPattern, StoryForce
    rep = compute(model, pattern, SDS, Ie, **kw)
    src = model.patterns[pattern]
    d = rep["direction"]
    tot = sum((sf.fx if d == "X" else sf.fy) for sf in src.story_forces)
    sgn = -1.0 if tot < 0.0 else 1.0
    base = prefix if prefix is not None else f"{pattern}_Fpx_"
    names = []
    for row in rep["stories"]:
        name = f"{base}{row['story']}"
        pat = LoadPattern(name, "quake")
        f = sgn * row["Fpx"]
        pat.story_forces.append(StoryForce(
            row["story"], fx=f if d == "X" else 0.0,
            fy=f if d == "Y" else 0.0))
        if copy_ecc:
            pat.accidental_torsion = src.accidental_torsion
            pat.ecc = src.ecc
            pat.ecc_y = src.ecc_y
            if getattr(src, "ecc_overrides", None):
                pat.ecc_overrides = copy.deepcopy(src.ecc_overrides)
        model.patterns[name] = pat
        names.append(name)
    return names


# --------------------------------------------------------------------------- #
# results table "Diaphragm Design Forces (ASCE 7 12.10)" (core.tables)
# --------------------------------------------------------------------------- #
_G = "Analysis Results > Structure Output > Other Output Items"


def _cols():
    def c(k, label, q):
        return {"key": k, "label": label, "quantity": q}
    return [c("pattern", "Load Pattern", "text"), c("story", "Story", "text"),
            c("direction", "Dir", "text"), c("method", "Method", "text"),
            c("h", "Elevation", "length"), c("w", "w_i", "force"),
            c("F", "F_i", "force"), c("sum_F", "Sum F", "force"),
            c("sum_w", "Sum w", "force"), c("wpx", "w_px", "force"),
            c("Fpx_eq", "Fpx (eq.)", "force"),
            c("Fpx_min", "Fpx min", "force"),
            c("Fpx_max", "Fpx max", "force"), c("Fpx", "Fpx", "force"),
            c("governs", "Governs", "text")]


def _t_fpx(c) -> List[dict]:
    model = c.model
    try:
        reps = compute_settings(model)
    except ValueError as exc:
        c.warnings.append(str(exc))
        return []
    if not reps:
        c.warnings.append("Set the ASCE 7 12.10 inputs (SDS, Ie) in Define "
                          "> Load Patterns > Diaphragm Design Forces (Fpx)")
    rows = []
    for r in reps:
        for s in r["stories"]:
            rows.append({"pattern": r["pattern"], "direction": r["direction"],
                         "method": r["method"],
                         **{k: s.get(k) for k in (
                             "story", "h", "w", "F", "sum_F", "sum_w",
                             "wpx", "Fpx_eq", "Fpx_min", "Fpx_max", "Fpx",
                             "governs")}})
    return rows


def register() -> None:
    from . import tables as T
    T.register_table("diaphragm_design_forces",
                     "Diaphragm Design Forces (ASCE 7 12.10)", _G, _cols(),
                     _t_fpx)
