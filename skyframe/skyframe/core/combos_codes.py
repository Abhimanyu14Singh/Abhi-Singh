"""Default design load combinations by code (ETABS Define > Load
Combinations > Add Default Design Combos) — CONTRACT "Code spectra, design
combinations, pattern types and live-load reduction".

The generators sit next to :func:`skyframe.core.codes.asce7_combinations`
and classify the model's load cases by their pattern TYPE
(:data:`PATTERN_KINDS`).  A case belongs to an action group when every
pattern it applies maps to that group:

======================  ======  ============================================
pattern kind            group   role
======================  ======  ============================================
dead, superdead         G       permanent (all cases summed)
live, reducible_live    L       imposed, floors (all cases summed)
roof_live               Lr      imposed, roofs (all cases summed)
snow                    S       snow (all cases summed)
wind                    W       alternative cases, +/- sign variants
quake (+ RS cases)      E       alternative cases, +/- (static) / + (RS)
temperature             T       alternative cases, + only (EN 1990 only)
======================  ======  ============================================

Gravity groups are applied as a whole (every case of the group with the
same factor); the alternative groups W/E/T enter one case at a time.  An
accompanying signed action always takes a sign (+ or -, both generated:
for a linear response one of the two is at least as onerous as omitting
it); an accompanying unsigned action (T) also has the "absent" variant.

Codes (``standard`` key -> clause):

* ``en1990``  EN 1990:2002 + A1 Table A1.2(B): Eq. 6.10 or 6.10a/6.10b,
  seismic 6.12b, optional SLS 6.14b/6.15b/6.16b; Table A1.1 psi factors
  (recommended values) with national-annex overrides for every gamma and
  psi; A1.2.1(1) roof imposed loads are not combined with snow or wind.
* ``is456``   IS 456:2000 Table 18 = IS 875 (Part 5) §8.1 = IS 1893
  (Part 1):2016 §6.3.4.1: 1.5(G+IL), 1.2(G+IL+-X), 1.5(G+-X), 0.9G+-1.5X;
  SLS G+IL, G+-X, G+0.8IL+-0.8X.
* ``is800``   IS 800:2007 Table 4 (no crane / erection / accidental rows):
  1.5G+1.5IL1+1.05ILa, 1.2G+1.2IL1+1.05ILa+-0.6X, 1.2G+1.2IL1+0.53ILa+-1.2X,
  1.5G+-1.5X, 0.9G+-1.5X; SLS rows.
* ``nbcc2020`` NBCC 2020 Table 4.1.3.2.-A load cases 1-5 (principal +
  companion, 1.25D / 0.9D variants).
* ``asnzs1170`` AS/NZS 1170.0:2002 §4.2.2 (a), (b), (d)-(g) with Table 4.1
  psi_c / psi_E (SLS §4.3 short / long-term optional).

Public API::

    STANDARDS / catalogue()
    classify(model, include_rs=True) -> {group: [...], "warnings": [...]}
    code_combinations(model, standard, **params)
        -> [{name, factors: {case: f}, desc}]
    apply_code_combinations(model, standard, replace=True, **params)
"""

from __future__ import annotations

import itertools
import re
import warnings
from typing import Any, Dict, List, Optional, Tuple

#: every pattern TYPE (ETABS Define > Load Patterns > Type).  The first
#: seven are the legacy kinds.
PATTERN_KINDS = ("dead", "live", "quake", "wind", "other", "notional",
                 "prestress", "superdead", "reducible_live", "roof_live",
                 "snow", "temperature", "pattern_live", "construction",
                 "rain", "ice", "move", "shrinkage", "creep", "settlement",
                 "earth_pressure", "hyperstatic")

#: ETABS display names (e2k / UI)
PATTERN_KIND_LABELS = {
    "dead": "Dead", "live": "Live", "quake": "Seismic", "wind": "Wind",
    "other": "Other", "notional": "Notional", "prestress": "Prestress",
    "superdead": "Super Dead", "reducible_live": "Reducible Live",
    "roof_live": "Roof Live", "snow": "Snow", "temperature": "Temperature",
    "pattern_live": "Pattern Live", "construction": "Construction",
    "rain": "Rain", "ice": "Ice", "move": "Move", "shrinkage": "Shrinkage",
    "creep": "Creep", "settlement": "Settlement",
    "earth_pressure": "Earth Pressure", "hyperstatic": "Hyperstatic"}

#: pattern kind -> combination action group
KIND_GROUP = {"dead": "G", "superdead": "G", "live": "L",
              "reducible_live": "L", "roof_live": "Lr", "snow": "S",
              "wind": "W", "quake": "E", "temperature": "T"}

#: live kinds subject to live-load reduction (core.llr_codes)
REDUCIBLE_KINDS = ("reducible_live",)

GRAVITY_GROUPS = ("G", "L", "Lr", "S")
ALT_GROUPS = ("W", "E", "T")

# EN 1990 Table A1.1 recommended psi (psi0, psi1, psi2)
EN_PSI_LIVE = {"A": (0.7, 0.5, 0.3), "B": (0.7, 0.5, 0.3),
               "C": (0.7, 0.7, 0.6), "D": (0.7, 0.7, 0.6),
               "E": (1.0, 0.9, 0.8), "F": (0.7, 0.7, 0.6),
               "G": (0.7, 0.5, 0.3), "H": (0.0, 0.0, 0.0)}
EN_PSI_SNOW = {"low": (0.5, 0.2, 0.0), "high": (0.7, 0.5, 0.2)}
EN_PSI_WIND = (0.6, 0.2, 0.0)
EN_PSI_TEMP = (0.6, 0.5, 0.0)

# AS/NZS 1170.0 Table 4.1 (psi_s, psi_l, psi_c, psi_E)
AS_PSI = {"residential_office": (0.7, 0.4, 0.4, 0.3),
          "parking": (0.7, 0.4, 0.4, 0.3),
          "retail": (0.7, 0.4, 0.4, 0.3),
          "storage": (1.0, 0.6, 0.6, 0.6),
          "other": (1.0, 0.6, 0.6, 0.6),
          "roof_floor_type": (0.7, 0.4, 0.4, 0.3),
          "roof_other": (0.7, 0.0, 0.0, 0.0)}


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #
def classify(model, include_rs: bool = True) -> Dict[str, Any]:
    """Cases by action group: ``{"G": [..], "L": [..], "Lr", "S",
    "W": [(case, signed)], "E": [(case, signed)], "T": [(case, False)],
    "warnings": [...]}`` in model order."""
    out: Dict[str, Any] = {g: [] for g in GRAVITY_GROUPS + ALT_GROUPS}
    warns: List[str] = []
    for cname, case in model.cases.items():
        groups = set()
        for pname in case.patterns:
            pat = model.patterns.get(pname)
            groups.add(KIND_GROUP.get(pat.kind) if pat is not None
                       else None)
        if not groups or None in groups:
            continue
        if len(groups) > 1:
            warns.append(f"case {cname!r} mixes pattern types "
                         f"{sorted(groups)}; not used by the generator")
            continue
        g = groups.pop()
        if g in GRAVITY_GROUPS:
            out[g].append(cname)
        else:
            out[g].append((cname, g != "T"))
    if include_rs:
        for rname in model.rs_cases:
            out["E"].append((rname, False))
    out["warnings"] = warns
    return out


# --------------------------------------------------------------------------- #
# expansion engine
# --------------------------------------------------------------------------- #
def _choices(cls, g: str, leading: bool):
    """Signed alternatives of an alternative group: [(case, sign)]."""
    ch = []
    for c, signed in cls[g]:
        ch.append((c, 1.0))
        if signed:
            ch.append((c, -1.0))
    if not leading and ch and not any(s for _, s in cls[g]):
        ch.append((None, 0.0))          # unsigned accompanying: may be absent
    return ch


def _expand(cls, terms: Dict[str, float], lead: Optional[str]
            ) -> List[Tuple[Dict[str, float], str]]:
    """Expand a template ``{group: factor}`` into concrete combos.

    Missing accompanying groups are dropped; a missing LEADING group drops
    the template.  Returns ``[(factors, desc)]``."""
    if lead is not None and not cls[lead]:
        return []
    terms = {g: f for g, f in terms.items() if f != 0.0 and cls[g]}
    alt = [g for g in terms if g in ALT_GROUPS]
    opts = [_choices(cls, g, g == lead) for g in alt]
    res = []
    for pick in itertools.product(*opts) if opts else [()]:
        fac: Dict[str, float] = {}
        parts: List[str] = []
        for g, f in terms.items():
            if g in ALT_GROUPS:
                continue
            for c in cls[g]:
                fac[c] = fac.get(c, 0.0) + f
            parts.append(f"{f:g}{g}")
        for g, (c, sgn) in zip(alt, pick):
            if c is None:
                continue
            f = terms[g] * sgn
            fac[c] = fac.get(c, 0.0) + f
            parts.append(f"{'-' if f < 0 else '+'}{abs(f):g}{c}")
        if not fac:
            continue
        desc = " + ".join(p for p in parts if p[0] not in "+-")
        signed = " ".join(p for p in parts if p[0] in "+-")
        res.append(({c: round(f, 10) for c, f in fac.items()},
                    (desc + (" " + signed if signed else "")).strip()))
    return res


def _collect(cls, templates, prefix: str) -> List[dict]:
    out: List[dict] = []
    seen = set()
    for terms, lead in templates:
        for fac, desc in _expand(cls, terms, lead):
            key = tuple(sorted(fac.items()))
            if key in seen:
                continue
            seen.add(key)
            out.append({"factors": fac, "desc": desc})
    for i, c in enumerate(out, 1):
        c["name"] = f"{prefix}{i:02d}"
    return out


# --------------------------------------------------------------------------- #
# EN 1990
# --------------------------------------------------------------------------- #
def _en_psi(p) -> Dict[str, Tuple[float, float, float]]:
    psi = {"L": EN_PSI_LIVE[p["live_category"]], "Lr": EN_PSI_LIVE["H"],
           "S": EN_PSI_SNOW[p["snow_site"]], "W": EN_PSI_WIND,
           "T": EN_PSI_TEMP}
    for g, v in (p.get("psi") or {}).items():
        if g not in psi:
            raise ValueError(f"en1990: psi override group must be one of "
                             f"{tuple(psi)}, got {g!r}")
        if (not isinstance(v, (list, tuple)) or len(v) != 3
                or not all(_num_ok(x) and 0.0 <= x <= 1.0 for x in v)):
            raise ValueError(f"en1990: psi[{g}] must be [psi0, psi1, psi2] "
                             "in [0, 1]")
        psi[g] = tuple(float(x) for x in v)
    return psi


def _en1990(cls, p) -> List[dict]:
    psi = _en_psi(p)
    gs, gi, gq, xi = (p["gamma_G_sup"], p["gamma_G_inf"], p["gamma_Q"],
                      p["xi"])
    var = ("L", "Lr", "S", "W", "T")

    def accomp(lead, k):            # psi_k of every non-leading variable
        d = {}
        for g in var:
            if g == lead:
                continue
            if "Lr" in (lead, g) and {lead, g} & {"S", "W"}:
                continue            # A1.2.1(1): roof imposed not with S/W
            d[g] = psi[g][k]
        return d

    out: List[dict] = []
    uls = []
    if p["equation"] == "6.10":
        uls.append(({"G": gs}, None))
        for q in var:
            t = {"G": gs, q: gq}
            t.update({g: gq * f for g, f in accomp(q, 0).items()})
            uls.append((t, q))
            if q == "W":
                uls.append(({"G": gi, "W": gq}, "W"))
    else:                                     # 6.10a + 6.10b
        uls.append(({"G": gs}, None))
        t = {"G": gs}
        t.update({g: gq * psi[g][0] for g in var
                  if not (g == "Lr" and (cls["S"] or cls["W"]))})
        uls.append((t, None))
        for q in var:
            t = {"G": xi * gs, q: gq}
            t.update({g: gq * f for g, f in accomp(q, 0).items()})
            uls.append((t, q))
            if q == "W":
                uls.append(({"G": gi, "W": gq}, "W"))
    out += _collect(cls, uls, p["prefix"] + "ULS")
    if p["seismic"] and cls["E"]:
        t = {"G": 1.0, "E": 1.0}
        t.update({g: psi[g][2] for g in var})
        out += _collect(cls, [(t, "E")], p["prefix"] + "SEIS")
    if p["sls"]:
        ch = [({"G": 1.0}, None)]
        fr = [({"G": 1.0}, None)]
        for q in var:
            t = {"G": 1.0, q: 1.0}
            t.update(accomp(q, 0))
            ch.append((t, q))
            t = {"G": 1.0, q: psi[q][1]}
            t.update(accomp(q, 2))
            fr.append((t, q))
        qp = {"G": 1.0}
        qp.update({g: psi[g][2] for g in var})
        out += _collect(cls, ch, p["prefix"] + "SLSC")
        out += _collect(cls, fr, p["prefix"] + "SLSF")
        out += _collect(cls, [(qp, None)], p["prefix"] + "SLSQ")
    return out


# --------------------------------------------------------------------------- #
# IS 456 / IS 875-5 / IS 1893 and IS 800
# --------------------------------------------------------------------------- #
def _is_live(cls) -> Tuple[Optional[str], List[str]]:
    lives = [g for g in ("L", "Lr", "S") if cls[g]]
    return (lives[0] if lives else None), lives[1:]


def _is456(cls, p) -> List[dict]:
    lead, acc = _is_live(cls)
    IL = ([lead] if lead else []) + acc
    tpl = []

    def t(gf, ilf, x=None, xf=0.0, lead_=None):
        d = {"G": gf}
        d.update({g: ilf for g in IL})
        if x:
            d[x] = xf
        return d, lead_
    tpl.append(t(1.5, 1.5))
    for X in ("W", "E"):
        tpl.append(t(1.2, 1.2, X, 1.2, X))
        tpl.append(t(1.5, 0.0, X, 1.5, X))
        tpl.append(t(0.9, 0.0, X, 1.5, X))
    out = _collect(cls, tpl, p["prefix"])
    if p["sls"]:
        s = [t(1.0, 1.0)]
        for X in ("W", "E"):
            s.append(t(1.0, 0.0, X, 1.0, X))
            s.append(t(1.0, 0.8, X, 0.8, X))
        out += _collect(cls, s, p["prefix"] + "SLS")
    return out


def _is800(cls, p) -> List[dict]:
    lead, acc = _is_live(cls)

    def t(gf, lf, af, x=None, xf=0.0):
        d = {"G": gf}
        if lead:
            d[lead] = lf
        d.update({g: af for g in acc})
        if x:
            d[x] = xf
        return d, x
    tpl = [t(1.5, 1.5, 1.05)]
    for X in ("W", "E"):
        tpl += [t(1.2, 1.2, 1.05, X, 0.6), t(1.2, 1.2, 0.53, X, 1.2),
                t(1.5, 0.0, 0.0, X, 1.5), t(0.9, 0.0, 0.0, X, 1.5)]
    out = _collect(cls, tpl, p["prefix"])
    if p["sls"]:
        s = [t(1.0, 1.0, 1.0)]
        for X in ("W", "E"):
            s += [t(1.0, 0.8, 0.8, X, 0.8), t(1.0, 0.0, 0.0, X, 1.0)]
        out += _collect(cls, s, p["prefix"] + "SLS")
    return out


# --------------------------------------------------------------------------- #
# NBCC 2020
# --------------------------------------------------------------------------- #
def _nbcc(cls, p) -> List[dict]:
    lc = 1.0 if p["storage"] else 0.5        # Table note: storage areas
    L = ("L", "Lr")

    def lt(f):
        return {g: f for g in L}
    tpl = [({"G": 1.4}, None)]

    def companions(base, comps, lead):
        present = [c for c in comps if any(cls[g] for g in c)]
        if not present:
            tpl.append((base, lead))
        for c in present:
            d = dict(base)
            d.update(c)
            tpl.append((d, lead))
    for dfac in (1.25, 0.9):
        b = {"G": dfac, **lt(1.5)}
        if any(cls[g] for g in L):
            companions(b, [{"S": 1.0}, {"W": 0.4}], None)
    for dfac in (1.25, 0.9):
        companions({"G": dfac, "S": 1.5}, [lt(1.0), {"W": 0.4}], "S")
    for dfac in (1.25, 0.9):
        companions({"G": dfac, "W": 1.4}, [lt(lc), {"S": 0.5}], "W")
    tpl.append(({"G": 1.0, "E": 1.0, **lt(lc), "S": 0.25}, "E"))
    return _collect(cls, tpl, p["prefix"])


# --------------------------------------------------------------------------- #
# AS/NZS 1170.0
# --------------------------------------------------------------------------- #
def _asnzs(cls, p) -> List[dict]:
    ps, pl, pc, pe = AS_PSI[p["occupancy"]]
    rs, rl, rc, re_ = AS_PSI["roof_other"]
    if p.get("psi_c") is not None:
        pc = p["psi_c"]
    if p.get("psi_E") is not None:
        pe = p["psi_E"]
    tpl = [({"G": 1.35}, None),
           ({"G": 1.2, "L": 1.5, "Lr": 1.5}, "L" if cls["L"] else "Lr"),
           ({"G": 1.2, "W": 1.0, "L": pc, "Lr": rc}, "W"),
           ({"G": 0.9, "W": 1.0}, "W"),
           ({"G": 1.0, "E": 1.0, "L": pe, "Lr": re_}, "E"),
           ({"G": 1.2, "S": 1.0, "L": pc, "Lr": rc}, "S")]
    out = _collect(cls, tpl, p["prefix"])
    if p["sls"]:
        s = [({"G": 1.0, "L": ps, "Lr": rs}, None),
             ({"G": 1.0, "L": pl, "Lr": rl}, None)]
        out += _collect(cls, s, p["prefix"] + "SLS")
    return out


# --------------------------------------------------------------------------- #
# catalogue + public API
# --------------------------------------------------------------------------- #
def _num_ok(x) -> bool:
    return (not isinstance(x, bool) and isinstance(x, (int, float))
            and x == x and abs(x) != float("inf"))


def _P(key, label, default, type_="number", **kw):
    d = {"key": key, "label": label, "type": type_, "default": default}
    d.update(kw)
    return d


_SLS = _P("sls", "Include serviceability combinations", False, "bool")
STANDARDS: Dict[str, dict] = {
    "en1990": {
        "label": "EN 1990 (Eurocode)", "fn": _en1990,
        "clause": "Table A1.2(B) Eq. 6.10 / 6.10a,b; 6.12b; A1.1 psi",
        "params": [
            _P("prefix", "Name prefix", "EN-", "text"),
            _P("equation", "ULS expression", "6.10", "choice",
               choices=[["6.10", "Eq. 6.10"], ["6.10ab", "Eq. 6.10a + 6.10b"]]),
            _P("live_category", "Imposed load category", "B", "choice",
               choices=[[k, f"Category {k}"] for k in EN_PSI_LIVE]),
            _P("snow_site", "Snow", "low", "choice",
               choices=[["low", "Sites H <= 1000 m"],
                        ["high", "Sites H > 1000 m / Nordic"]]),
            _P("gamma_G_sup", "gamma_G,sup", 1.35, gt=0.0),
            _P("gamma_G_inf", "gamma_G,inf", 1.0, min=0.0),
            _P("gamma_Q", "gamma_Q", 1.5, gt=0.0),
            _P("xi", "xi (6.10b)", 0.85, gt=0.0, max=1.0),
            _P("psi", "psi overrides {L|Lr|S|W|T: [psi0, psi1, psi2]}", None,
               "psi", nullable=True),
            _P("seismic", "Include seismic (6.12b)", True, "bool"),
            _SLS]},
    "is456": {
        "label": "IS 456 / IS 875-5 / IS 1893", "fn": _is456,
        "clause": "IS 456 Table 18; IS 875 (Part 5) §8.1; IS 1893 §6.3.4.1",
        "params": [_P("prefix", "Name prefix", "IS456-", "text"), _SLS]},
    "is800": {
        "label": "IS 800:2007", "fn": _is800,
        "clause": "Table 4 partial safety factors (limit state of strength)",
        "params": [_P("prefix", "Name prefix", "IS800-", "text"), _SLS]},
    "nbcc2020": {
        "label": "NBCC 2020", "fn": _nbcc,
        "clause": "Table 4.1.3.2.-A load combinations 1-5",
        "params": [_P("prefix", "Name prefix", "NBCC-", "text"),
                   _P("storage", "Storage / equipment areas (companion "
                      "1.0L)", False, "bool")]},
    "asnzs1170": {
        "label": "AS/NZS 1170.0", "fn": _asnzs,
        "clause": "§4.2.2 strength (a), (b), (d)-(g); §4.3 SLS",
        "params": [
            _P("prefix", "Name prefix", "AS-", "text"),
            _P("occupancy", "Floor occupancy (Table 4.1)",
               "residential_office", "choice",
               choices=[["residential_office", "Residential / office"],
                        ["parking", "Parking"], ["retail", "Retail"],
                        ["storage", "Storage"], ["other", "Other"]]),
            _P("psi_c", "psi_c override", None, nullable=True, min=0.0,
               max=1.0),
            _P("psi_E", "psi_E override", None, nullable=True, min=0.0,
               max=1.0),
            _SLS]},
}


def catalogue() -> List[dict]:
    return [{"standard": k, "label": v["label"], "clause": v["clause"],
             "params": [dict(p) for p in v["params"]]}
            for k, v in STANDARDS.items()]


def normalize_params(standard: str, params: Optional[dict]) -> dict:
    if standard not in STANDARDS:
        raise ValueError(f"standard must be one of {tuple(STANDARDS)}, "
                         f"got {standard!r}")
    params = params or {}
    specs = {s["key"]: s for s in STANDARDS[standard]["params"]}
    bad = [k for k in params if k not in specs]
    if bad:
        raise ValueError(f"{standard}: unknown parameter(s) {bad}")
    out: Dict[str, Any] = {}
    for k, s in specs.items():
        v = params.get(k, s["default"])
        t = s["type"]
        if t == "bool":
            if not isinstance(v, bool):
                raise ValueError(f"{standard}: {k} must be true/false")
        elif t == "text":
            if not isinstance(v, str) or not v.strip() or re.search(r'["\\]',
                                                                   v):
                raise ValueError(f"{standard}: {k} must be a non-empty "
                                 "name without quotes")
            v = v.strip()
        elif t == "choice":
            if v not in [c for c, _ in s["choices"]]:
                raise ValueError(f"{standard}: {k} must be one of "
                                 f"{[c for c, _ in s['choices']]}")
        elif t == "psi":
            if v is not None and not isinstance(v, dict):
                raise ValueError(f"{standard}: psi must be an object")
        else:
            if v is None and s.get("nullable"):
                pass
            elif not _num_ok(v):
                raise ValueError(f"{standard}: {k} must be a finite number")
            else:
                v = float(v)
                if "gt" in s and not v > s["gt"]:
                    raise ValueError(f"{standard}: {k} must be > {s['gt']}")
                if "min" in s and v < s["min"]:
                    raise ValueError(f"{standard}: {k} must be >= "
                                     f"{s['min']}")
                if "max" in s and v > s["max"]:
                    raise ValueError(f"{standard}: {k} must be <= "
                                     f"{s['max']}")
        out[k] = v
    return out


def code_combinations(model, standard: str, include_rs: bool = True,
                      **params) -> List[dict]:
    """Generate (do not store) ``[{name, factors, desc}]``.  Emits a
    UserWarning when no dead case exists (nothing generated), for mixed
    cases, and for temperature cases a code table does not cover."""
    p = normalize_params(standard, params)
    cls = classify(model, include_rs=include_rs)
    for w in cls["warnings"]:
        warnings.warn(f"{standard} combinations: {w}", UserWarning)
    if not cls["G"]:
        warnings.warn(f"{standard} combinations: no dead / superdead case "
                      "found; no combinations generated", UserWarning)
        return []
    if cls["T"] and standard != "en1990":
        warnings.warn(f"{standard} combinations: temperature cases are not "
                      "in the code table and are not combined",
                      UserWarning)
        cls["T"] = []
    return STANDARDS[standard]["fn"](cls, p)


def apply_code_combinations(model, standard: str, replace: bool = True,
                            include_rs: bool = True,
                            **params) -> List[dict]:
    """Generate and ADD the combinations (``model.add_combo``).  With
    ``replace`` (default) earlier combos of the same generator prefix
    (``<prefix><digits>``) not referenced by another combo are removed
    first, so a re-run does not leave stale combinations."""
    out = code_combinations(model, standard, include_rs=include_rs,
                            **params)
    p = normalize_params(standard, params)
    if replace:
        pre = re.escape(p["prefix"])
        pat = re.compile(rf"{pre}(ULS|SEIS|SLSC|SLSF|SLSQ|SLS)?\d{{2,}}")
        for name in [n for n in model.combos if pat.fullmatch(n)]:
            if not any(name in cb.cases for k, cb in model.combos.items()
                       if k != name):
                del model.combos[name]
    for c in out:
        model.add_combo(c["name"], c["factors"])
    return out
