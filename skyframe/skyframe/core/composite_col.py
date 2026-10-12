"""Composite column frame sections: steel-encased concrete (SRC) and
concrete-filled steel tubes (CFT) with transformed effective stiffness
(Wave B; CONTRACT "Orthotropic materials, slab / deck property types,
cold-formed and composite sections, named line / area springs").

A composite section is an ordinary :class:`FrameSection` whose material is
the STEEL (E_s), with ``shape`` one of :data:`TYPES` and ``dims``::

    SRC_I     b, h (concrete, along 3 / 2), d, bf, tf, tw (steel I, centred)
              [, bar_d, n_bars_3, n_bars_2, cover]   rebar (cover = face
              to bar centre; n_bars_3 bars along each face parallel to 3,
              n_bars_2 along each face parallel to 2, corners counted in
              both)
    CFT_rect  d, b, t  (rectangular tube, filled)
    CFT_round D, t     (round tube, filled)

plus the string keys ``"concrete"`` (concrete material name, required),
``"rebar"`` (rebar material, default = the steel) and ``"code"``
(``"AISC360"`` default | ``"EC4"``).

Stored analysis properties (all transformed to the steel modulus E_s):

* ``A = (E_s A_s + E_c A_c + E_r A_r) / E_s``
* ``I33 = EI_eff,33 / E_s`` (``I22`` likewise) with

  - AISC 360-16 I2.1b (encased): ``EI_eff = E_s I_s + 0.5 E_s I_sr +
    C1 E_c I_c``, ``C1 = 0.25 + 3 (A_s + A_sr) / A_g <= 0.7``;
  - AISC 360-16 I2.2b (filled): ``EI_eff = E_s I_s + E_s I_sr + C3 E_c
    I_c``, ``C3 = 0.45 + 3 (A_s + A_sr) / A_g <= 0.9``;
  - EN 1994-1-1 6.7.3.3(3): ``(EI)_eff = E_a I_a + E_s I_s + K_e E_cm
    I_c``, ``K_e = 0.6``.

* ``J = J_s + (G_c / G_s) J_c`` (Bredt tube / thin-walled I + the solid
  concrete core), shear deformation off;
* ``mod_weight = mod_mass = (gamma_s A_s + gamma_c A_c + gamma_r A_r) /
  (gamma_s A)`` so the member self-weight / self-mass are those of the
  real steel + concrete (the transformed A would otherwise weigh as
  steel).

A_g = gross section area, I_c the concrete (net of steel and bars), all
about the section centroid (doubly symmetric sections).
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

TYPES = ("SRC_I", "CFT_rect", "CFT_round")
CODES = ("AISC360", "EC4")
NUM_KEYS = {
    "SRC_I": (("b", "h", "d", "bf", "tf", "tw"),
              ("bar_d", "n_bars_3", "n_bars_2", "cover")),
    "CFT_rect": (("d", "b", "t"), ()),
    "CFT_round": (("D", "t"), ()),
}
STR_KEYS = ("concrete", "rebar", "code")


def _finite(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def is_composite(sec) -> bool:
    return getattr(sec, "shape", None) in TYPES and isinstance(
        getattr(sec, "dims", None), dict)


def validate_dims(typ: str, dims: dict, materials=None) -> None:
    if typ not in TYPES:
        raise ValueError(f"unknown composite type {typ!r}")
    req, opt = NUM_KEYS[typ]
    for k in req:
        if not (_finite(dims.get(k)) and dims[k] > 0):
            raise ValueError(f"{typ}: {k} must be > 0")
    for k in opt:
        v = dims.get(k)
        if v is not None and not (_finite(v) and v >= 0):
            raise ValueError(f"{typ}: {k} must be >= 0")
    unknown = set(dims) - set(req) - set(opt) - set(STR_KEYS) - {"torsion"}
    if unknown:
        raise ValueError(f"{typ}: unknown dims {sorted(unknown)}")
    if dims.get("code", "AISC360") not in CODES:
        raise ValueError(f"{typ}: code must be one of {CODES}")
    if not isinstance(dims.get("concrete"), str) or not dims["concrete"]:
        raise ValueError(f"{typ}: dims.concrete (material name) is required")
    if materials is not None:
        for k in ("concrete", "rebar"):
            n = dims.get(k)
            if n is not None and n not in materials:
                raise ValueError(f"{typ}: {k} material {n!r} is not "
                                 "defined")
    g = dims.get
    if typ == "SRC_I":
        if g("d") >= g("h") or g("bf") >= g("b"):
            raise ValueError("SRC_I: the steel shape must fit inside the "
                             "concrete")
        if 2 * g("tf") >= g("d") or g("tw") >= g("bf"):
            raise ValueError("SRC_I: inconsistent steel I dimensions")
        nb = (g("n_bars_3") or 0, g("n_bars_2") or 0)
        if any(nb) and not (g("bar_d") and g("cover")):
            raise ValueError("SRC_I: rebar needs bar_d and cover")
        if any(nb) and (min(nb) < 2):
            raise ValueError("SRC_I: n_bars_3 and n_bars_2 must be >= 2 "
                             "(corner bars)")
        if any(nb) and 2 * g("cover") >= min(g("b"), g("h")):
            raise ValueError("SRC_I: cover too large")
    elif typ == "CFT_rect":
        if 2 * g("t") >= min(g("d"), g("b")):
            raise ValueError("CFT_rect: wall thicker than half the tube")
    else:
        if 2 * g("t") >= g("D"):
            raise ValueError("CFT_round: wall thicker than the radius")


def _bars(dims) -> List[Tuple[float, float]]:
    """Bar centres (x along 3, y along 2) about the centroid."""
    n3, n2 = int(dims.get("n_bars_3") or 0), int(dims.get("n_bars_2") or 0)
    if not n3 or not n2:
        return []
    c = dims["cover"]
    x0, x1 = -dims["b"] / 2 + c, dims["b"] / 2 - c
    y0, y1 = -dims["h"] / 2 + c, dims["h"] / 2 - c
    pts = set()
    for i in range(n3):
        x = x0 + (x1 - x0) * i / (n3 - 1)
        pts.add((round(x, 12), round(y0, 12)))
        pts.add((round(x, 12), round(y1, 12)))
    for j in range(n2):
        y = y0 + (y1 - y0) * j / (n2 - 1)
        pts.add((round(x0, 12), round(y, 12)))
        pts.add((round(x1, 12), round(y, 12)))
    return sorted(pts)


def parts(typ: str, dims: dict) -> Dict[str, float]:
    """Gross geometry: steel, rebar, concrete (net) areas, second moments
    (about local 3 = I33, local 2 = I22) and torsion constants."""
    g = dims.get
    if typ == "SRC_I":
        b, h, d, bf, tf, tw = (g("b"), g("h"), g("d"), g("bf"), g("tf"),
                               g("tw"))
        As = 2 * bf * tf + (d - 2 * tf) * tw
        Is33 = (bf * d ** 3 - (bf - tw) * (d - 2 * tf) ** 3) / 12.0
        Is22 = (2 * tf * bf ** 3 + (d - 2 * tf) * tw ** 3) / 12.0
        Js = (2 * bf * tf ** 3 + (d - tf) * tw ** 3) / 3.0
        bars = _bars(dims)
        ab = math.pi * (g("bar_d") or 0.0) ** 2 / 4.0
        Asr = ab * len(bars)
        Isr33 = sum(ab * y * y + ab * (g("bar_d") or 0) ** 2 / 16
                    for _, y in bars)
        Isr22 = sum(ab * x * x + ab * (g("bar_d") or 0) ** 2 / 16
                    for x, _ in bars)
        Ag = b * h
        Ac = Ag - As - Asr
        Ic33 = b * h ** 3 / 12.0 - Is33 - Isr33
        Ic22 = h * b ** 3 / 12.0 - Is22 - Isr22
        from .shape_props import _roark_J
        Jc = _roark_J(b, h)
        return {"Ag": Ag, "As": As, "Asr": Asr, "Ac": Ac, "Is33": Is33,
                "Is22": Is22, "Isr33": Isr33, "Isr22": Isr22, "Ic33": Ic33,
                "Ic22": Ic22, "Js": Js, "Jc": Jc, "b": b, "h": h,
                "n_bars": len(bars)}
    if typ == "CFT_rect":
        d, b, t = g("d"), g("b"), g("t")
        bi, di = b - 2 * t, d - 2 * t
        As = b * d - bi * di
        Is33 = (b * d ** 3 - bi * di ** 3) / 12.0
        Is22 = (d * b ** 3 - di * bi ** 3) / 12.0
        Js = 4.0 * ((b - t) * (d - t)) ** 2 * t / (2.0 * ((b - t) + (d - t)))
        from .shape_props import _roark_J
        return {"Ag": b * d, "As": As, "Asr": 0.0, "Ac": bi * di,
                "Is33": Is33, "Is22": Is22, "Isr33": 0.0, "Isr22": 0.0,
                "Ic33": bi * di ** 3 / 12.0, "Ic22": di * bi ** 3 / 12.0,
                "Js": Js, "Jc": _roark_J(bi, di), "b": b, "h": d,
                "n_bars": 0}
    D, t = g("D"), g("t")
    Di = D - 2 * t
    Is = math.pi * (D ** 4 - Di ** 4) / 64.0
    Ic = math.pi * Di ** 4 / 64.0
    return {"Ag": math.pi * D * D / 4.0,
            "As": math.pi * (D * D - Di * Di) / 4.0, "Asr": 0.0,
            "Ac": math.pi * Di * Di / 4.0, "Is33": Is, "Is22": Is,
            "Isr33": 0.0, "Isr22": 0.0, "Ic33": Ic, "Ic22": Ic,
            "Js": 2.0 * Is, "Jc": 2.0 * Ic, "b": D, "h": D, "n_bars": 0}


def coefficient(typ: str, code: str, p: Dict[str, float]) -> float:
    """Concrete stiffness coefficient (C1 / C3 / K_e)."""
    if code == "EC4":
        return 0.6
    ratio = (p["As"] + p["Asr"]) / p["Ag"]
    if typ == "SRC_I":
        return min(0.25 + 3.0 * ratio, 0.7)
    return min(0.45 + 3.0 * ratio, 0.9)


def properties(typ: str, dims: dict, steel, concrete, rebar=None) -> dict:
    """Transformed FrameSection properties + the code breakdown."""
    rebar = rebar or steel
    code = dims.get("code", "AISC360")
    p = parts(typ, dims)
    Es, Ec, Er = float(steel.E), float(concrete.E), float(rebar.E)
    C = coefficient(typ, code, p)
    rb = 0.5 if (typ == "SRC_I" and code == "AISC360") else 1.0
    EI33 = Es * p["Is33"] + rb * Er * p["Isr33"] + C * Ec * p["Ic33"]
    EI22 = Es * p["Is22"] + rb * Er * p["Isr22"] + C * Ec * p["Ic22"]
    EA = Es * p["As"] + Ec * p["Ac"] + Er * p["Asr"]
    Gs, Gc = steel.G, concrete.G
    A = EA / Es
    w_real = (steel.unit_weight * p["As"] + concrete.unit_weight * p["Ac"]
              + rebar.unit_weight * p["Asr"])
    m_real = (steel.mass_per_volume * p["As"]
              + concrete.mass_per_volume * p["Ac"]
              + rebar.mass_per_volume * p["Asr"])
    return {"A": A, "I33": EI33 / Es, "I22": EI22 / Es,
            "J": p["Js"] + Gc / Gs * p["Jc"], "b": p["b"], "h": p["h"],
            "EA": EA, "EI33": EI33, "EI22": EI22, "C": C, "code": code,
            "mod_weight": (w_real / (steel.unit_weight * A)
                           if steel.unit_weight > 0 else 1.0),
            "mod_mass": (m_real / (steel.mass_per_volume * A)
                         if steel.mass_per_volume > 0 else 1.0),
            "parts": p}


def composite_section(model, name: str, typ: str, dims: dict,
                      steel: str):
    """A FrameSection (material = ``steel``) for a composite column."""
    from .model import FrameSection
    validate_dims(typ, dims, model.materials)
    mats = model.materials
    pr = properties(typ, dims, mats[steel], mats[dims["concrete"]],
                    mats.get(dims.get("rebar") or steel))
    sec = FrameSection(name=name, material=steel, A=pr["A"], I33=pr["I33"],
                       I22=pr["I22"], J=pr["J"], b=pr["b"], h=pr["h"])
    sec.mod_weight = pr["mod_weight"]
    sec.mod_mass = pr["mod_mass"]
    sec.shape = typ
    sec.dims = {k: (v if isinstance(v, str) else float(v))
                for k, v in dims.items()}
    return sec


def validate_section(sec, materials) -> None:
    if not is_composite(sec):
        return
    try:
        validate_dims(sec.shape, sec.dims, materials)
    except ValueError as exc:
        raise ValueError(f"Section {sec.name}: {exc}") from None
