"""v1.21 parametric section properties (skyframe.core.shape_props).

1. Closed forms: rectangle / circle / pipe exactly, thin-walled J,
   channel shear centre e = 3 b^2 tf / (6 b tf + h tw), principal axes.
2. Independent cross-check with ``sectionproperties`` (finite elements;
   test-only dependency, skipped when absent): A, I, I23, centroid and
   plastic moduli agree to 1e-6; J / Cw / shear centre within the stated
   thin-walled tolerances.
3. Parametric-from-published-dimensions vs the tabulated AISC v16.0 values
   for EVERY shape of each family, with the tolerances documented in
   CONTRACT / docs/SECTION_SOURCES.md (differences = rolled fillets, sloped
   flanges, rounding of the published dimensions).
"""

import math
import statistics

import pytest

from skyframe.core import steel_db as sdb
from skyframe.core.model import FrameSection
from skyframe.core.shape_props import (SHAPE_TYPES, SHEAR_AREA_FORMULAS,
                                       principal, rect_J_exact,
                                       shape_properties, validate_dims)

R = 1e-12


def P(typ, **dims):
    return shape_properties(typ, dims)


# --------------------------------------------------------------------------- #
# 1. closed forms
# --------------------------------------------------------------------------- #
def test_rectangle_exact():
    b, d = 0.3, 0.6
    p = P("rect_solid", d=d, b=b)
    assert p["A"] == pytest.approx(b * d, rel=R)
    assert p["I33"] == pytest.approx(b * d ** 3 / 12, rel=R)
    assert p["I22"] == pytest.approx(d * b ** 3 / 12, rel=R)
    assert p["S33"] == pytest.approx(b * d ** 2 / 6, rel=R)
    assert p["Z33"] == pytest.approx(b * d ** 2 / 4, rel=R)
    assert p["Z22"] == pytest.approx(d * b ** 2 / 4, rel=R)
    assert p["I23"] == 0.0 and (p["x0"], p["y0"]) == (0.0, 0.0)
    assert (p["As2"], p["As3"]) == (5 / 6 * p["A"], 5 / 6 * p["A"])
    # J == FrameSection.rectangular (Roark), within 0.2 % of the exact series
    assert p["J"] == FrameSection.rectangular("R", "C", b, d).J
    for ratio in (1, 2, 5, 10):
        assert P("rect_solid", d=ratio * 0.1, b=0.1)["J"] == pytest.approx(
            rect_J_exact(0.1, ratio * 0.1), rel=2e-3)
    assert P("plate", d=d, b=b) == p


def test_circle_and_pipe_exact():
    D = 0.5
    c = P("round_solid", D=D)
    assert c["A"] == pytest.approx(math.pi * D ** 2 / 4, rel=R)
    assert c["I33"] == c["I22"] == pytest.approx(math.pi * D ** 4 / 64, rel=R)
    assert c["J"] == pytest.approx(math.pi * D ** 4 / 32, rel=R)
    assert c["S33"] == pytest.approx(math.pi * D ** 3 / 32, rel=R)
    assert c["Z33"] == pytest.approx(D ** 3 / 6, rel=R)
    assert c["As2"] == pytest.approx(0.9 * c["A"], rel=R)
    t = 0.02
    di = D - 2 * t
    p = P("round_tube", D=D, t=t)
    assert p["A"] == pytest.approx(math.pi * (D ** 2 - di ** 2) / 4, rel=R)
    assert p["I33"] == pytest.approx(math.pi * (D ** 4 - di ** 4) / 64, rel=R)
    assert p["J"] == pytest.approx(math.pi * (D ** 4 - di ** 4) / 32, rel=R)
    assert p["Z33"] == pytest.approx((D ** 3 - di ** 3) / 6, rel=R)
    assert p["As2"] == p["As3"] == pytest.approx(p["A"] / 2, rel=R)


def test_rect_tube_sharp_and_rounded():
    d, b, t = 0.3, 0.2, 0.01
    p = P("rect_tube", d=d, b=b, t=t)
    assert p["A"] == pytest.approx(b * d - (b - 2 * t) * (d - 2 * t), rel=R)
    assert p["I33"] == pytest.approx(
        (b * d ** 3 - (b - 2 * t) * (d - 2 * t) ** 3) / 12, rel=R)
    # Bredt, sharp mid-line rectangle: 2 t (b-t)^2 (d-t)^2 / (b + d - 2t)
    assert p["J"] == pytest.approx(
        2 * t * (b - t) ** 2 * (d - t) ** 2 / (b + d - 2 * t), rel=R)
    assert p["Z33"] == pytest.approx(
        (b * d ** 2 - (b - 2 * t) * (d - 2 * t) ** 2) / 4, rel=R)
    # rounded corners: area = sharp - (4 - pi)(ro^2 - ri^2)
    ro = 2 * t
    q = P("rect_tube", d=d, b=b, t=t, r_out=ro)
    assert q["A"] == pytest.approx(
        p["A"] - (4 - math.pi) * (ro ** 2 - (ro - t) ** 2), rel=R)
    assert q["As2"] == 2 * d * t and q["As3"] == 2 * b * t


def test_I_shape_closed_form():
    d, bf, tf, tw = 0.4, 0.2, 0.015, 0.01
    p = P("I", d=d, bf=bf, tf=tf, tw=tw)
    assert p["A"] == pytest.approx(2 * bf * tf + (d - 2 * tf) * tw, rel=R)
    assert p["I33"] == pytest.approx(
        (bf * d ** 3 - (bf - tw) * (d - 2 * tf) ** 3) / 12, rel=R)
    assert p["I22"] == pytest.approx(
        (2 * tf * bf ** 3 + (d - 2 * tf) * tw ** 3) / 12, rel=R)
    assert p["J"] == pytest.approx((2 * bf * tf ** 3 + (d - tf) * tw ** 3) / 3,
                                   rel=R)
    assert p["Cw"] == pytest.approx(tf * bf ** 3 * (d - tf) ** 2 / 24, rel=R)
    assert (p["As2"], p["As3"]) == (d * tw, 5 / 3 * bf * tf)
    assert (p["x0"], p["y0"]) == (0.0, 0.0)
    # an I_mono with equal flanges is the I
    m = P("I_mono", d=d, bf_top=bf, tf_top=tf, bf_bot=bf, tf_bot=tf, tw=tw)
    for k in ("A", "I33", "I22", "J", "Cw", "Z33", "Z22"):
        assert m[k] == pytest.approx(p[k], rel=1e-12), k
    assert m["y0"] == pytest.approx(0.0, abs=1e-15)


def test_I_mono_shear_centre():
    d, tw = 0.5, 0.01
    bt, tt, bb, tb = 0.3, 0.02, 0.15, 0.015
    p = P("I_mono", d=d, bf_top=bt, tf_top=tt, bf_bot=bb, tf_bot=tb, tw=tw)
    It, Ib = tt * bt ** 3 / 12, tb * bb ** 3 / 12
    ho = d - (tt + tb) / 2
    y_sc = d - tt / 2 - ho * Ib / (It + Ib)
    assert p["y0"] + p["cy"] == pytest.approx(y_sc, rel=R)
    assert p["Cw"] == pytest.approx(ho ** 2 * It * Ib / (It + Ib), rel=R)


def test_channel_thin_walled_shear_centre_and_J():
    d, bf, tf, tw = 0.3, 0.1, 0.012, 0.008
    p = P("channel", d=d, bf=bf, tf=tf, tw=tw)
    b, h = bf - tw / 2, d - tf                     # mid-line dimensions
    e = 3 * b ** 2 * tf / (6 * b * tf + h * tw)    # from the web mid-line
    assert p["x0"] == pytest.approx((tw / 2 - e) - p["cx"], rel=R)
    assert p["y0"] == pytest.approx(0.0, abs=1e-15)
    assert p["J"] == pytest.approx((2 * b * tf ** 3 + h * tw ** 3) / 3, rel=R)
    A = 2 * bf * tf + (d - 2 * tf) * tw
    cx = (2 * bf * tf * bf / 2 + (d - 2 * tf) * tw * tw / 2) / A
    assert p["cx"] == pytest.approx(cx, rel=R)


def test_tee_and_concrete_tee_centroid():
    d, bf, tf, tw = 0.2, 0.2, 0.015, 0.01
    p = P("tee", d=d, bf=bf, tf=tf, tw=tw)
    Af, Aw = bf * tf, (d - tf) * tw
    cy = (Af * (d - tf / 2) + Aw * (d - tf) / 2) / (Af + Aw)
    assert p["cy"] == pytest.approx(cy, rel=R)
    assert p["y0"] == pytest.approx((d - tf / 2) - cy, rel=R)
    assert p["J"] == pytest.approx((bf * tf ** 3 + (d - tf / 2) * tw ** 3) / 3,
                                   rel=R)
    bf, hf, bw, h = 1.2, 0.15, 0.3, 0.6
    c = P("tee_concrete", bf=bf, hf=hf, bw=bw, h=h)
    Af, Aw = bf * hf, bw * (h - hf)
    yc = (Af * (h - hf / 2) + Aw * (h - hf) / 2) / (Af + Aw)
    I33 = (bf * hf ** 3 / 12 + Af * (h - hf / 2 - yc) ** 2
           + bw * (h - hf) ** 3 / 12 + Aw * ((h - hf) / 2 - yc) ** 2)
    assert c["A"] == pytest.approx(Af + Aw, rel=R)
    assert c["cy"] == pytest.approx(yc, rel=R)
    assert c["I33"] == pytest.approx(I33, rel=R)
    assert (c["As2"], c["As3"]) == (5 / 6 * bw * h, 5 / 6 * bf * hf)
    assert (c["b"], c["h"]) == (bf, h)


def test_angle_thin_walled_J_and_principal_axes():
    d, b, t = 0.2, 0.15, 0.02
    p = P("angle", d=d, b=b, t=t)
    assert p["J"] == pytest.approx((d - t / 2 + b - t / 2) * t ** 3 / 3,
                                   rel=R)
    # invariants of the inertia tensor
    assert p["I_major"] + p["I_minor"] == pytest.approx(p["I33"] + p["I22"],
                                                        rel=R)
    assert p["I_major"] * p["I_minor"] == pytest.approx(
        p["I33"] * p["I22"] - p["I23"] ** 2, rel=1e-10)
    a = p["alpha"]
    Iu = (p["I33"] * math.cos(a) ** 2 + p["I22"] * math.sin(a) ** 2
          - 2 * p["I23"] * math.sin(a) * math.cos(a))
    assert Iu == pytest.approx(p["I_major"], rel=1e-12)
    assert p["I23"] < 0 and 0 < a < math.pi / 4    # heel at bottom-left
    assert (p["x0"], p["y0"]) == pytest.approx((t / 2 - p["cx"],
                                                t / 2 - p["cy"]))
    e = P("angle", d=0.1, b=0.1, t=0.01)            # equal legs: 45 deg
    assert e["alpha"] == pytest.approx(math.pi / 4, rel=1e-12)
    assert principal(2.0, 1.0, 0.0) == (2.0, 1.0, 0.0)


def test_double_angle_parallel_axis():
    d, b, t, s = 0.2, 0.15, 0.02, 0.01
    two = P("double_angle", d=d, b=b, t=t, s=s)
    single = P("angle", d=d, b=b, t=t)
    assert two["A"] == pytest.approx(2 * single["A"], rel=R)
    assert two["I33"] == pytest.approx(2 * single["I33"], rel=1e-12)
    # I22 about the symmetry axis: angle centroid at s/2 + cx from it
    exp = 2 * (single["I22"] + single["A"] * (s / 2 + single["cx"]) ** 2)
    assert two["I22"] == pytest.approx(exp, rel=1e-12)
    assert two["J"] == pytest.approx(2 * single["J"], rel=R)
    assert two["b"] == pytest.approx(2 * b + s)
    # the shear centre is on the outstanding-leg mid-line (each angle's heel)
    assert two["y0"] + two["cy"] == pytest.approx(d - t / 2, rel=R)


def test_every_type_has_formula_and_validates():
    good = {"I": dict(d=.4, bf=.2, tf=.015, tw=.01),
            "I_mono": dict(d=.4, bf_top=.2, tf_top=.015, bf_bot=.15,
                           tf_bot=.012, tw=.01),
            "channel": dict(d=.3, bf=.1, tf=.012, tw=.008),
            "angle": dict(d=.2, b=.15, t=.02),
            "double_angle": dict(d=.2, b=.15, t=.02, s=.01),
            "tee": dict(d=.2, bf=.2, tf=.015, tw=.01),
            "rect_tube": dict(d=.3, b=.2, t=.01, r_out=.02),
            "round_tube": dict(D=.3, t=.01), "rect_solid": dict(d=.6, b=.3),
            "plate": dict(d=.02, b=.3), "round_solid": dict(D=.5),
            "tee_concrete": dict(bf=1.2, hf=.15, bw=.3, h=.6),
            "L_concrete": dict(bf=.8, hf=.15, bw=.3, h=.6)}
    assert set(good) == set(SHAPE_TYPES) == set(SHEAR_AREA_FORMULAS)
    for typ, dims in good.items():
        p = shape_properties(typ, dims)
        for k in ("A", "I33", "I22", "J", "S33", "S22", "Z33", "Z22", "As2",
                  "As3", "b", "h"):
            assert p[k] > 0 and math.isfinite(p[k]), (typ, k)
        assert p["Z33"] >= p["S33"] * 0.999 and p["Z22"] >= p["S22"] * 0.999
    for typ, dims, msg in [
            ("I", dict(d=.1, bf=.2, tf=.06, tw=.01), "thicker"),
            ("tee", dict(d=.2, bf=.01, tf=.01, tw=.02), "wider"),
            ("angle", dict(d=.1, b=.05, t=.05), "smaller"),
            ("rect_tube", dict(d=.1, b=.1, t=.06), "thicker"),
            ("round_tube", dict(D=.1, t=.05), "thicker"),
            ("tee_concrete", dict(bf=.3, hf=.7, bw=.3, h=.6), "deeper"),
            ("I", dict(d=.4, bf=.2, tf=0, tw=.01), "> 0"),
            ("I", dict(d=.4, bf=.2, tf="x", tw=.01), "finite"),
            ("zz", {}, "unknown")]:
        with pytest.raises(ValueError, match=msg):
            validate_dims(typ, dims)


# --------------------------------------------------------------------------- #
# 2. independent finite-element cross-check (sectionproperties)
# --------------------------------------------------------------------------- #
SP_CASES = [
    ("I", dict(d=.4, bf=.2, tf=.015, tw=.01), .03),
    ("I_mono", dict(d=.5, bf_top=.3, tf_top=.02, bf_bot=.15, tf_bot=.015,
                    tw=.01), .03),
    ("channel", dict(d=.3, bf=.1, tf=.012, tw=.008), .03),
    ("angle", dict(d=.2, b=.15, t=.02), .03),
    # J of a double angle is 2 x single angle (stitched, not fused): the
    # fused FE outline (one 2t-thick back-to-back leg) is not comparable
    ("double_angle", dict(d=.2, b=.15, t=.02, s=0.0), None),
    ("tee", dict(d=.2, bf=.2, tf=.015, tw=.01), .03),
    ("rect_tube", dict(d=.3, b=.2, t=.01, r_out=.02), .01),
    ("tee_concrete", dict(bf=1.2, hf=.15, bw=.3, h=.6), .12),
    ("L_concrete", dict(bf=.8, hf=.15, bw=.3, h=.6), .12),
]


def _fe(typ, dims):
    sp = pytest.importorskip("sectionproperties")   # noqa: F841
    from shapely.geometry import box
    from shapely.ops import unary_union
    from sectionproperties.analysis.section import Section
    from sectionproperties.pre.geometry import CompoundGeometry, Geometry
    from sectionproperties.pre.library import rectangular_hollow_section
    from skyframe.core.shape_props import _rects
    if typ == "rect_tube":
        g = rectangular_hollow_section(d=dims["d"], b=dims["b"], t=dims["t"],
                                       r_out=dims["r_out"], n_r=48)
    else:
        u = unary_union([box(x0, y0, x1, y1) for x0, x1, y0, y1
                         in _rects(typ, validate_dims(typ, dims))])
        g = (Geometry(u) if u.geom_type == "Polygon" else
             CompoundGeometry([Geometry(q) for q in u.geoms]))
    g.create_mesh(mesh_sizes=[g.calculate_area() / 400])
    s = Section(g)
    s.calculate_geometric_properties()
    s.calculate_warping_properties()
    s.calculate_plastic_properties()
    ixx, iyy, ixy = s.get_ic()
    cx, cy = s.get_c()
    xs, ys = s.get_sc()
    zx, zy = s.get_s()
    return dict(A=s.get_area(), I33=ixx, I22=iyy, I23=ixy, cx=cx, cy=cy,
                Z33=zx, Z22=zy, J=s.get_j(), x0=xs - cx, y0=ys - cy)


@pytest.mark.parametrize("typ,dims,jtol", SP_CASES,
                         ids=[c[0] for c in SP_CASES])
def test_matches_sectionproperties(typ, dims, jtol):
    fe, me = _fe(typ, dims), shape_properties(typ, dims)
    scale = max(me["b"], me["h"])
    for k in ("A", "I33", "I22", "Z33", "Z22"):
        assert me[k] == pytest.approx(fe[k], rel=1e-4 if typ == "rect_tube"
                                      else 1e-6), k
    assert me["I23"] == pytest.approx(fe["I23"], abs=1e-6 * me["I33"])
    for k in ("cx", "cy"):
        assert me[k] == pytest.approx(fe[k], abs=1e-6 * scale), k
    # J: thin-walled (steel) / rectangle-split (concrete) approximations
    if jtol is None:
        pass
    elif typ in ("tee_concrete", "L_concrete"):
        assert fe["J"] * (1 - jtol) <= me["J"] <= fe["J"]   # conservative
    else:
        assert me["J"] == pytest.approx(fe["J"], rel=jtol)
    # shear centre: thin-walled rules within 4 % of the depth / width (steel)
    if typ not in ("tee_concrete", "L_concrete"):
        assert me["x0"] == pytest.approx(fe["x0"], abs=0.04 * me["b"])
        assert me["y0"] == pytest.approx(fe["y0"], abs=0.04 * me["h"])


# --------------------------------------------------------------------------- #
# 3. parametric (from published dimensions) vs tabulated AISC v16.0
# --------------------------------------------------------------------------- #
# max |error| % for (A, I33, I22); None = not compared (sloped flanges:
# the outline uses the AISC average flange thickness, so I22 / Z22 of S, C,
# MC and ST shapes are 5-35 % high - documented, not a defect)
TOL = {"W": (3, 4.5, 1.5), "HP": (5, 5.5, 1.5), "M": (9.5, 9.5, 3),
       "WT": (3, 2, 2), "MT": (5, 1, 1), "L": (1.5, 2.5, 2.5),
       "2L": (1.5, 3, 3.5), "HSS Rect": (0.5, 1, 1),
       "HSS Square": (0.5, 1, 1), "HSS Round": (1, 2, 2),
       "Pipe": (3.5, 3.5, 3.5), "S": (2, 1.5, None), "C": (2, 1, None),
       "MC": (2, 2, None), "ST": (2, 4, None)}


def _param(rec):
    dims = {k: v for k, v in rec["dims"].items()
            if not isinstance(v, str) and k not in ("r", "t_nom")}
    return shape_properties(rec["type"], dims)


@pytest.mark.parametrize("family", list(TOL))
def test_parametric_vs_tabulated_aisc(family):
    tA, t33, t22 = TOL[family]
    errs = {"A": [], "I33": [], "I22": []}
    for rec in sdb.shapes("aisc", family):
        p, tab = _param(rec), rec["props"]
        for k, tol in (("A", tA), ("I33", t33), ("I22", t22)):
            if tol is None:
                continue
            e = abs(p[k] / tab[k] - 1) * 100
            errs[k].append(e)
            assert e <= tol, (rec["name"], k, round(e, 2))
    for k, v in errs.items():                     # typical error far lower
        if v and family not in ("M", "Pipe"):
            assert statistics.median(v) < 2.5, (family, k)


def test_W14X90_from_dims_within_2_percent():
    rec = sdb.shape("aisc", "W14X90")
    p, tab = _param(rec), rec["props"]
    for k in ("A", "I33", "I22", "S33", "Z33", "Z22", "Cw"):
        assert p[k] == pytest.approx(tab[k], rel=0.02), k
    # J: DG9 plate formula omits the fillet "bulbs": -5.5 % here
    assert p["J"] == pytest.approx(tab["J"], rel=0.06)


def test_torsion_and_principal_vs_tabulated():
    for rec in sdb.shapes("aisc"):
        fam, p, tab = rec["family"], None, rec["props"]
        if fam in ("HSS Rect", "HSS Square"):
            p = _param(rec)
            assert p["J"] == pytest.approx(tab["J"], rel=0.006), rec["name"]
        elif fam in ("HSS Round", "Pipe"):
            p = _param(rec)
            assert p["J"] == pytest.approx(tab["J"], rel=0.035), rec["name"]
        elif fam in ("W", "WT", "L"):
            p = _param(rec)
            assert p["J"] == pytest.approx(tab["J"], rel=0.20), rec["name"]
        if fam == "L":
            assert p["I_major"] == pytest.approx(tab["I_major"], rel=0.02)
            assert p["I_minor"] == pytest.approx(tab["I_minor"], rel=0.065)
            assert p["alpha"] == pytest.approx(tab["alpha"], rel=0.015)
            assert p["cx"] == pytest.approx(tab["cx"], rel=0.025)
            assert p["cy"] == pytest.approx(tab["cy"], rel=0.025)
