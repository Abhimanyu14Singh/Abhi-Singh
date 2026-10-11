"""Built-up sections (v1.21, skyframe.core.builtup) — closed-form checks.

Every expected value below is derived BY HAND (parallel-axis theorem,
Bredt-Batho, Mohr's circle) independently of the implementation.
"""

import copy
import json
import math

import pytest

from skyframe.core import builtup as B
from skyframe.core import builtup_db
from skyframe.core.model import BuildingModel, Material

E_S = 200e6          # kPa
IN = 0.0254


def _mats():
    return {"S": Material("S", E=E_S, nu=0.3),
            "AL": Material("AL", E=E_S / 3.0, nu=0.33)}


def _props(comps, axes="geometric", torsion="auto"):
    bs = B.BuiltupSection.from_dict({"name": "T", "material": "S",
                                     "components": comps, "axes": axes,
                                     "torsion": torsion})
    return B.section_properties(bs, _mats())


def _plate(b, t, x, y, angle=0.0, **kw):
    return dict({"kind": "plate", "b": b, "t": t, "x": x, "y": y,
                 "angle": angle}, **kw)


# --------------------------------------------------------------------------- #
# 1. plate girder by hand (parallel-axis theorem)
# --------------------------------------------------------------------------- #
def _girder_hand(d, tw, bt, tt, bb, tb):
    hw = d - tt - tb
    parts = [(bt * tt, d / 2 - tt / 2, bt * tt ** 3 / 12, tt * bt ** 3 / 12),
             (hw * tw, -d / 2 + tb + hw / 2, tw * hw ** 3 / 12,
              hw * tw ** 3 / 12),
             (bb * tb, -d / 2 + tb / 2, bb * tb ** 3 / 12, tb * bb ** 3 / 12)]
    A = sum(p[0] for p in parts)
    yc = sum(p[0] * p[1] for p in parts) / A
    Ix = sum(p[2] + p[0] * (p[1] - yc) ** 2 for p in parts)
    Iy = sum(p[3] for p in parts)
    return A, yc, Ix, Iy


def test_plate_girder_unequal_flanges_by_hand():
    pr = dict(d=1.2, tw=0.012, bf_top=0.40, tf_top=0.025, bf_bot=0.50,
              tf_bot=0.030)
    bs = B.template_section("plate_girder", pr, name="PG", material="S")
    p = B.section_properties(bs, _mats())
    A, yc, Ix, Iy = _girder_hand(1.2, 0.012, 0.40, 0.025, 0.50, 0.030)
    assert p["A"] == pytest.approx(A, rel=1e-12)
    assert p["cy"] == pytest.approx(yc, rel=1e-12)
    assert p["cx"] == pytest.approx(0.0, abs=1e-15)
    assert p["I33"] == pytest.approx(Ix, rel=1e-12)
    assert p["I22"] == pytest.approx(Iy, rel=1e-12)
    assert p["Ixy"] == 0.0 and p["theta"] == 0.0
    # S at both faces: I / c
    assert p["S33_top"] == pytest.approx(Ix / (0.6 - yc), rel=1e-12)
    assert p["S33_bot"] == pytest.approx(Ix / (0.6 + yc), rel=1e-12)
    assert p["S33"] == pytest.approx(min(Ix / (0.6 - yc), Ix / (0.6 + yc)))
    # open J = sum b t^3 / 3
    J = (0.40 * 0.025 ** 3 + 0.50 * 0.030 ** 3 + 1.145 * 0.012 ** 3) / 3
    assert p["J"] == pytest.approx(J, rel=1e-12)
    assert p["J_method"] == "open"
    # plastic NA: equal areas; bottom flange (0.015) < A/2 = 0.01937
    # -> PNA in the web at y_p above the bottom flange top
    Ah = A / 2
    yp = -0.6 + 0.030 + (Ah - 0.015) / 0.012          # web below = Ah-0.015
    Z = (0.015 * (yp - (-0.585))                        # bottom flange
         + 0.012 * (yp - (-0.57)) ** 2 / 2              # web below PNA
         + 0.012 * (0.575 - yp) ** 2 / 2                # web above PNA
         + 0.010 * (0.5875 - yp))                       # top flange
    assert p["Z33"] == pytest.approx(Z, rel=1e-9)
    assert p["r33"] == pytest.approx(math.sqrt(Ix / A))
    # mono-symmetric shear centre: y = sum(If y)/sum(If) (web ~ 0)
    It, Ib, Iw = 0.025 * 0.4 ** 3 / 12, 0.03 * 0.5 ** 3 / 12, \
        1.145 * 0.012 ** 3 / 12
    ysc = (It * 0.5875 + Ib * -0.585 + Iw * (-0.57 + 1.145 / 2)) \
        / (It + Ib + Iw)
    assert p["shear_centre"]["y"] == pytest.approx(ysc, rel=1e-12)
    assert p["y0"] == pytest.approx(ysc - yc)
    # shear areas: web -> As2, flanges -> As3
    assert p["As2"] == pytest.approx(1.145 * 0.012)
    assert p["As3"] == pytest.approx(0.4 * 0.025 + 0.5 * 0.03)
    assert p["b"] == pytest.approx(0.5) and p["h"] == pytest.approx(1.2)


def test_rectangle_plate_exact_and_plastic():
    p = _props([_plate(0.3, 0.02, 0.1, -0.2)])
    assert p["A"] == pytest.approx(0.006)
    assert (p["cx"], p["cy"]) == pytest.approx((0.1, -0.2))
    assert p["I33"] == pytest.approx(0.3 * 0.02 ** 3 / 12)
    assert p["I22"] == pytest.approx(0.02 * 0.3 ** 3 / 12)
    assert p["Z33"] == pytest.approx(0.3 * 0.02 ** 2 / 4, rel=1e-9)
    assert p["Z22"] == pytest.approx(0.02 * 0.3 ** 2 / 4, rel=1e-9)
    assert p["J"] == pytest.approx(0.3 * 0.02 ** 3 / 3)
    # the same plate rotated 90 degrees swaps the axes exactly
    q = _props([_plate(0.3, 0.02, 0.1, -0.2, 90.0)])
    assert q["I33"] == pytest.approx(p["I22"], rel=1e-12)
    assert q["I22"] == pytest.approx(p["I33"], rel=1e-12)
    assert q["As2"] == pytest.approx(0.006) and q["As3"] == 0.0


# --------------------------------------------------------------------------- #
# 2. box girder: Bredt-Batho J = 4 A^2 t / perimeter
# --------------------------------------------------------------------------- #
def test_box_girder_bredt_batho_uniform_thickness():
    b, d, t = 0.5, 0.8, 0.02
    bs = B.template_section("box_girder", dict(b=b, d=d, tf=t, tw=t,
                                               s=b - t), material="S")
    p = B.section_properties(bs, _mats())
    Am = (b - t) * (d - t)
    perim = 2 * ((b - t) + (d - t))
    assert p["J_method"] == "closed_cell"
    assert p["J"] == pytest.approx(4 * Am ** 2 * t / perim, rel=1e-12)
    assert p["cells"][0]["Am"] == pytest.approx(Am)
    # I by hand: outer minus inner rectangle
    assert p["I33"] == pytest.approx(
        (b * d ** 3 - (b - 2 * t) * (d - 2 * t) ** 3) / 12, rel=1e-12)
    assert p["shear_centre"]["method"] == "symmetry"


def test_box_girder_bredt_unequal_walls_and_outstands():
    b, d, tf, tw, s = 0.6, 1.0, 0.025, 0.016, 0.5   # flanges overhang
    bs = B.template_section("box_girder", dict(b=b, d=d, tf=tf, tw=tw, s=s),
                            material="S")
    p = B.section_properties(bs, _mats())
    W, H = s, d - tf                       # wall mid-line rectangle
    J = 4 * (W * H) ** 2 / (2 * W / tf + 2 * H / tw)
    assert p["J"] == pytest.approx(J, rel=1e-12)


def test_rect_tube_component_matches_bredt():
    p = _props([{"kind": "param", "type": "rect_tube",
                 "dims": {"d": 0.3, "b": 0.2, "t": 0.01}, "x": 0, "y": 0,
                 "rotation": 0, "mirror": False}])
    Am, pm = 0.19 * 0.29, 2 * (0.19 + 0.29)
    assert p["J"] == pytest.approx(4 * Am ** 2 * 0.01 / pm)
    assert p["A"] == pytest.approx(0.3 * 0.2 - 0.28 * 0.18)


def test_two_channels_toe_to_toe_form_a_cell():
    dims = {"d": 0.3, "bf": 0.1, "tf": 0.01, "tw": 0.01}
    comps = [{"kind": "param", "type": "channel", "dims": dims,
              "x": 0.05, "y": 0, "rotation": 0, "mirror": True},
             {"kind": "param", "type": "channel", "dims": dims,
              "x": -0.05, "y": 0, "rotation": 0, "mirror": False}]
    p = _props(comps)
    # closed 0.2 x 0.3 box, t = 0.01 everywhere
    Am, pm = 0.19 * 0.29, 2 * (0.19 + 0.29)
    assert p["J_method"] == "closed_cell"
    assert p["J"] == pytest.approx(4 * Am ** 2 * 0.01 / pm, rel=1e-12)


# --------------------------------------------------------------------------- #
# 3. W with cover plate(s): W props + plate props shifted to the centroid
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("where", ["top", "both"])
def test_w_cover_plate_equals_parallel_axis_sum(where):
    rec = builtup_db.shape("aisc", "W14X90")
    W = rec["props"]
    dW = rec["dims"]["d"]
    pb, pt = 0.40, 0.020
    bs = B.template_section("w_cover", dict(name="W14X90", plate_b=pb,
                                            plate_t=pt, where=where),
                            material="S")
    p = B.section_properties(bs, _mats())
    plates = [(dW / 2 + pt / 2)] + ([-(dW / 2 + pt / 2)]
                                    if where == "both" else [])
    Ap = pb * pt
    A = W["A"] + Ap * len(plates)
    yc = sum(Ap * y for y in plates) / A
    I33 = W["I33"] + W["A"] * yc ** 2 + sum(
        pb * pt ** 3 / 12 + Ap * (y - yc) ** 2 for y in plates)
    I22 = W["I22"] + len(plates) * pt * pb ** 3 / 12
    assert p["A"] == pytest.approx(A, rel=1e-12)
    assert p["cy"] == pytest.approx(yc, rel=1e-12, abs=1e-15)
    assert p["I33"] == pytest.approx(I33, rel=1e-12)
    assert p["I22"] == pytest.approx(I22, rel=1e-12)
    # tabulated J of the W + the plates' b t^3 / 3
    assert p["J"] == pytest.approx(W["J"] + len(plates) * pb * pt ** 3 / 3)
    # tabulated values snapshot into the component
    assert bs.components[0]["tab"]["A"] == W["A"]


# --------------------------------------------------------------------------- #
# 4. double angle by hand (AISC 2L tabulated values once the DB lands)
# --------------------------------------------------------------------------- #
def _angle_hand(d, b, t):
    """Single fillet-free angle, heel at (0,0), long leg d up, b right."""
    r = [(t * d, t / 2, d / 2, t * d ** 3 / 12, d * t ** 3 / 12),
         ((b - t) * t, t + (b - t) / 2, t / 2, (b - t) * t ** 3 / 12,
          t * (b - t) ** 3 / 12)]
    A = sum(q[0] for q in r)
    xc = sum(q[0] * q[1] for q in r) / A
    yc = sum(q[0] * q[2] for q in r) / A
    Ix = sum(q[3] + q[0] * (q[2] - yc) ** 2 for q in r)
    Iy = sum(q[4] + q[0] * (q[1] - xc) ** 2 for q in r)
    Ixy = sum(q[0] * (q[1] - xc) * (q[2] - yc) for q in r)
    return A, xc, yc, Ix, Iy, Ixy


@pytest.mark.parametrize("orient", ["LLBB", "SLBB"])
def test_double_angle_by_hand(orient):
    d, b, t, s = 6 * IN, 4 * IN, 0.5 * IN, 0.375 * IN
    bs = B.template_section("double_angle", dict(d=d, b=b, t=t, s=s,
                                                 orientation=orient),
                            material="S")
    p = B.section_properties(bs, _mats())
    v, h = (d, b) if orient == "LLBB" else (b, d)
    A1, xc, yc, Ix, Iy, _ = _angle_hand(v, h, t)
    assert p["A"] == pytest.approx(2 * A1, rel=1e-12)
    assert p["I33"] == pytest.approx(2 * Ix, rel=1e-12)
    # minor axis: each angle centroid at s/2 + xc from the symmetry axis
    assert p["I22"] == pytest.approx(2 * (Iy + A1 * (s / 2 + xc) ** 2),
                                     rel=1e-12)
    assert p["cy"] == pytest.approx(-v / 2 + yc, rel=1e-12)
    assert p["Ixy"] == 0.0
    assert p["J"] == pytest.approx(2 * (v + h - t) * t ** 3 / 3, rel=1e-12)
    # radius of gyration about the symmetry axis
    assert p["r22"] == pytest.approx(math.sqrt(p["I22"] / p["A"]))


# --------------------------------------------------------------------------- #
# 5. unsymmetric: Ixy and the principal angle
# --------------------------------------------------------------------------- #
def test_single_angle_ixy_and_principal_angle():
    d, b, t = 6 * IN, 4 * IN, 0.5 * IN
    p = _props([{"kind": "param", "type": "angle",
                 "dims": {"d": d, "b": b, "t": t}, "x": 0.1, "y": 0.2,
                 "rotation": 0, "mirror": False}])
    A, xc, yc, Ix, Iy, Ixy = _angle_hand(d, b, t)
    assert p["Ixy"] == pytest.approx(Ixy, rel=1e-12)
    assert Ixy < 0                         # legs up and right of the heel
    th = 0.5 * math.atan2(-2 * Ixy, Ix - Iy)
    assert math.tan(2 * p["theta"]) == pytest.approx(-2 * Ixy / (Ix - Iy))
    assert p["theta"] == pytest.approx(th, rel=1e-12)
    # Mohr: I about the rotated axis at theta == I_major
    Iu = (Ix + Iy) / 2 + (Ix - Iy) / 2 * math.cos(2 * th) \
        - Ixy * math.sin(2 * th)
    assert p["I_major"] == pytest.approx(Iu, rel=1e-12)
    assert p["I_major"] + p["I_minor"] == pytest.approx(Ix + Iy)
    assert p["I_major"] * p["I_minor"] == pytest.approx(Ix * Iy - Ixy ** 2)
    # mirrored angle: Ixy flips sign, theta flips sign
    q = _props([{"kind": "param", "type": "angle",
                 "dims": {"d": d, "b": b, "t": t}, "x": 0, "y": 0,
                 "rotation": 0, "mirror": True}])
    assert q["Ixy"] == pytest.approx(-Ixy, rel=1e-12)
    assert q["theta"] == pytest.approx(-th, rel=1e-12)
    # principal axes option feeds I_major / I_minor to the frame section
    r = _props([{"kind": "param", "type": "angle",
                 "dims": {"d": d, "b": b, "t": t}, "x": 0, "y": 0,
                 "rotation": 0, "mirror": False}], axes="principal")
    assert r["I33"] == pytest.approx(p["I_major"])
    assert r["I22"] == pytest.approx(p["I_minor"])
    assert p["shear_centre"] is None       # unsymmetric: not computed


def test_rotation_of_whole_section_rotates_tensor():
    d, b, t = 0.15, 0.10, 0.012
    base = {"kind": "param", "type": "angle", "dims": {"d": d, "b": b,
                                                       "t": t},
            "x": 0.0, "y": 0.0, "mirror": False}
    p0 = _props([dict(base, rotation=0.0)])
    a = 30.0
    p1 = _props([dict(base, rotation=a)])
    c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
    Ixx = c * c * p0["Ixx"] + s * s * p0["Iyy"] + 2 * s * c * p0["Ixy"]
    assert p1["Ixx"] == pytest.approx(Ixx, rel=1e-12)
    assert p1["theta"] == pytest.approx(p0["theta"] + math.radians(a),
                                        rel=1e-9)
    assert p1["I_major"] == pytest.approx(p0["I_major"], rel=1e-12)


def test_starred_angles_point_symmetric():
    bs = B.template_section("starred_angles", dict(b=0.1, t=0.01, s=0.01),
                            material="S")
    p = B.section_properties(bs, _mats())
    assert (p["cx"], p["cy"]) == pytest.approx((0.0, 0.0), abs=1e-15)
    assert p["Ixy"] > 0                     # legs in quadrants I and III
    assert p["shear_centre"]["method"] == "symmetry"
    assert math.tan(2 * p["theta"]) == pytest.approx(
        -2 * p["Ixy"] / (p["Ixx"] - p["Iyy"]))


# --------------------------------------------------------------------------- #
# 6. mixed materials (modular ratio)
# --------------------------------------------------------------------------- #
def test_mixed_material_modular_ratio():
    comps = [_plate(0.2, 0.02, 0, 0.11),
             _plate(0.2, 0.02, 0, -0.11, material="AL")]
    p = _props(comps)
    n = 1 / 3
    A = 0.004 + n * 0.004
    yc = (0.004 * 0.11 - n * 0.004 * 0.11) / A
    I = (0.2 * 0.02 ** 3 / 12 + 0.004 * (0.11 - yc) ** 2) \
        + n * (0.2 * 0.02 ** 3 / 12 + 0.004 * (-0.11 - yc) ** 2)
    assert p["A"] == pytest.approx(A) and p["cy"] == pytest.approx(yc)
    assert p["I33"] == pytest.approx(I, rel=1e-12)
    assert p["n"] == pytest.approx([1.0, n])


# --------------------------------------------------------------------------- #
# 7. sectionproperties cross-check (only when installed)
# --------------------------------------------------------------------------- #
def test_cross_check_with_sectionproperties():
    pytest.importorskip("sectionproperties")
    import shapely
    from shapely import Polygon
    from shapely.ops import unary_union
    from sectionproperties.analysis.section import Section
    from sectionproperties.pre.geometry import Geometry
    # unsymmetric: two different angles + a plate
    comps = [{"kind": "param", "type": "angle",
              "dims": {"d": 0.15, "b": 0.10, "t": 0.012}, "x": 0.0,
              "y": 0.0, "rotation": 0, "mirror": False},
             _plate(0.3, 0.015, 0.08, -0.0825)]
    p = _props(comps)
    bs = B.BuiltupSection.from_dict({"name": "T", "material": "S",
                                     "components": comps})
    polys = [Polygon(q["pts"]) for c in bs.components
             for q in B.component_geometry(c)["pieces"]]
    g = Geometry(shapely.union_all(polys, grid_size=1e-9))
    g.create_mesh(mesh_sizes=[1e-5])
    sec = Section(g)
    sec.calculate_geometric_properties()
    sec.calculate_plastic_properties()
    ixx, iyy, ixy = sec.get_ic()
    assert p["A"] == pytest.approx(sec.get_area(), rel=1e-9)
    assert p["Ixx"] == pytest.approx(ixx, rel=1e-9)
    assert p["Iyy"] == pytest.approx(iyy, rel=1e-9)
    assert p["Ixy"] == pytest.approx(ixy, rel=1e-9)
    assert p["Z33"] == pytest.approx(sec.get_s()[0], rel=1e-6)
    # FEM torsion of a box ~ Bredt (thin-wall), plate girder ~ sum bt^3/3
    box = B.template_section("box_girder", dict(b=0.5, d=0.8, tf=0.02,
                                                tw=0.02, s=0.48),
                             material="S")
    jb = B.section_properties(box, _mats())["J"]
    box.torsion = "fem"
    pf = B.section_properties(box, _mats())
    assert pf["J_method"] == "fem"
    assert pf["J"] == pytest.approx(jb, rel=0.03)
    pg = B.template_section("plate_girder", {}, material="S")
    jo = B.section_properties(pg, _mats())["J"]
    pg.torsion = "fem"
    pgf = B.section_properties(pg, _mats())
    assert pgf["J"] == pytest.approx(jo, rel=0.05)
    assert pgf["shear_centre"]["y"] == pytest.approx(
        B.section_properties(B.template_section("plate_girder", {},
                                                material="S"),
                             _mats())["shear_centre"]["y"], rel=0.01)


def test_fem_mode_falls_back_without_package(monkeypatch):
    monkeypatch.setattr(B, "fem_available", lambda: False)
    bs = B.template_section("plate_girder", {}, material="S")
    bs.torsion = "fem"
    p = B.section_properties(bs, _mats())
    assert p["J_method"] == "open"
    assert any("sectionproperties" in w for w in p["warnings"])


# --------------------------------------------------------------------------- #
# 8. model integration: storage, mirror, round trip, engine
# --------------------------------------------------------------------------- #
def _model():
    mdl = BuildingModel(name="BU")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("S", E=E_S, nu=0.3))
    mdl.set_stories([3.0])
    return mdl


def test_model_mirror_and_round_trip_byte_identical():
    mdl = _model()
    assert "builtup_sections" not in mdl.to_dict()      # default: absent
    bs = B.template_section("double_angle", {}, name="2L", material="S")
    props = mdl.add_builtup_section(bs)
    sec = mdl.sections["2L"]
    assert sec.shape == "builtup"
    assert (sec.A, sec.I33, sec.I22, sec.J) == (props["A"], props["I33"],
                                                props["I22"], props["J"])
    assert sec.As2 is None and sec.As3 is None           # Euler-Bernoulli
    d1 = mdl.to_dict()
    s1 = json.dumps(d1, sort_keys=True)
    d2 = BuildingModel.from_dict(json.loads(s1)).to_dict()
    assert json.dumps(d2, sort_keys=True) == s1
    # modifiers on the mirrored section survive a re-save
    mdl.sections["2L"].mod_I33 = 0.5
    mdl.add_builtup_section(B.BuiltupSection.from_dict(
        d1["builtup_sections"]["2L"]))
    assert mdl.sections["2L"].mod_I33 == 0.5
    mdl.add_member("beam", "2L", (0, 0, 3), (4, 0, 3), story="Story1",
                   uid="B1")
    with pytest.raises(ValueError):
        mdl.remove_builtup_section("2L")       # still used
    mdl.members.clear()
    mdl.remove_builtup_section("2L")
    assert "2L" not in mdl.sections and not mdl.builtup_sections


def test_validation_errors():
    mats = _mats()
    bad = [
        {"name": "", "material": "S", "components": [_plate(1, 1, 0, 0)]},
        {"name": "X", "material": "NOPE", "components": [_plate(1, 1, 0, 0)]},
        {"name": "X", "material": "S", "components": []},
        {"name": "X", "material": "S", "components": [_plate(0, 1, 0, 0)]},
        {"name": "X", "material": "S",
         "components": [_plate(1, 1, 0, 0, material="NOPE")]},
        {"name": "X", "material": "S", "components": [
            {"kind": "param", "type": "I", "dims": {"d": 0.1, "bf": 0.1,
                                                    "tf": 0.06, "tw": 0.01},
             "x": 0, "y": 0, "rotation": 0, "mirror": False}]},
        {"name": "X", "material": "S", "components": [
            {"kind": "param", "type": "blob", "dims": {}, "x": 0, "y": 0,
             "rotation": 0, "mirror": False}]},
        {"name": "X", "material": "S", "components": [
            {"kind": "db", "standard": "aisc", "name": "W99X1", "x": 0,
             "y": 0, "rotation": 0, "mirror": False}]},
        {"name": "X", "material": "S", "axes": "weird",
         "components": [_plate(1, 1, 0, 0)]},
    ]
    for d in bad:
        with pytest.raises(ValueError):
            B.section_properties(B.BuiltupSection.from_dict(d), mats)
    with pytest.raises(ValueError):
        B.BuiltupSection.from_dict({"name": "X", "material": "S",
                                    "components": [{"kind": "zz"}]})


def test_db_component_snapshot_is_stable():
    bs = B.BuiltupSection.from_dict({"name": "W", "material": "S",
                                     "components": [
                                         {"kind": "db", "standard": "AISC",
                                          "name": "W14x90"}]})
    B.resolve_components(bs)
    c = bs.components[0]
    assert c["type"] == "I" and c["dims"]["d"] == pytest.approx(14 * IN)
    p = B.section_properties(bs, _mats())
    assert p["A"] == pytest.approx(26.5 * IN ** 2)       # tabulated
    assert p["J"] == pytest.approx(4.06 * IN ** 4)
    c2 = dict(c, props_from="geometry")
    bs2 = B.BuiltupSection.from_dict({"name": "W", "material": "S",
                                      "components": [c2]})
    g = B.section_properties(bs2, _mats())
    A_geo = 2 * 14.5 * 0.71 + (14 - 2 * 0.71) * 0.44
    assert g["A"] == pytest.approx(A_geo * IN ** 2, rel=1e-12)


def test_laced_column_states_assumption():
    bs = B.template_section("laced_column", {}, name="LC", material="S")
    assert "shear flexibility" in bs.notes.lower()
    d = bs.to_dict()
    assert d["notes"] == bs.notes and d["template"]["id"] == "laced_column"


def test_all_templates_compute():
    for t in B.template_list():
        bs = B.template_section(t["id"], {}, material="S")
        p = B.section_properties(bs, _mats())
        assert p["A"] > 0 and p["I33"] > 0 and p["I22"] > 0 and p["J"] > 0
        rt = B.BuiltupSection.from_dict(json.loads(json.dumps(bs.to_dict())))
        assert rt.to_dict() == bs.to_dict()


engine_mod = None
try:                                        # engine optional for collection
    from skyframe.engine import opensees_engine as engine_mod   # noqa
except Exception:                           # pragma: no cover
    engine_mod = None


@pytest.mark.skipif(engine_mod is None, reason="OpenSeesPy unavailable")
def test_cantilever_with_builtup_section_matches_hand():
    from skyframe.core.model import NodalLoad, NodalMass, PointSupport
    mdl = _model()
    bs = B.template_section("plate_girder", dict(d=0.6, tw=0.010,
                                                 bf_top=0.25, tf_top=0.016,
                                                 bf_bot=0.30, tf_bot=0.020),
                            name="PG", material="S")
    props = mdl.add_builtup_section(bs)
    A, yc, Ix, Iy = _girder_hand(0.6, 0.010, 0.25, 0.016, 0.30, 0.020)
    assert props["I33"] == pytest.approx(Ix, rel=1e-12)
    L, P = 4.0, 10.0
    mdl.add_member("beam", "PG", (0, 0, 3.0), (L, 0, 3.0), story="Story1",
                   uid="B1")
    mdl.supports.append(PointSupport((0, 0, 3.0), (1, 1, 1, 1, 1, 1)))
    pat = mdl.pattern("P", "other")
    pat.nodal_loads.append(NodalLoad((L, 0, 3.0), fz=-P))
    pat2 = mdl.pattern("Q", "other")
    pat2.nodal_loads.append(NodalLoad((L, 0, 3.0), fy=P))
    mdl.add_case("P", {"P": 1.0})
    mdl.add_case("Q", {"Q": 1.0})
    mdl.nodal_masses.append(NodalMass((L, 0, 3.0), mx=1.0, my=1.0, mz=1.0))
    mdl.num_modes = 1
    res = engine_mod.OpenSeesEngine(mdl).run().to_dict()
    tip = next(t for t, xyz in res["nodes"].items()
               if all(abs(a - b) < 1e-6 for a, b in zip(xyz, (L, 0, 3.0))))
    uz = res["cases"]["P"]["node_disp"][tip][2]
    uy = res["cases"]["Q"]["node_disp"][tip][1]
    assert uz == pytest.approx(-P * L ** 3 / (3 * E_S * Ix), rel=1e-6)
    assert uy == pytest.approx(P * L ** 3 / (3 * E_S * Iy), rel=1e-6)


# --------------------------------------------------------------------------- #
# 9. HTTP API
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    from skyframe.api.server import create_app
    app = create_app()
    app.testing = True
    return app.test_client()


def test_api_round_trip(client):
    m = client.get("/api/model").get_json()
    mat = next(iter(m["materials"]))
    tl = client.get("/api/sections/builtup/templates").get_json()
    assert {t["id"] for t in tl["templates"]} == set(B.TEMPLATES)
    assert tl["db_source"] in ("stub", "steel_db")
    r = client.post("/api/sections/builtup/template",
                    json={"id": "plate_girder", "name": "PG1",
                          "material": mat}).get_json()
    sec = r["section"]
    pr = client.post("/api/sections/builtup",
                     json={"action": "properties", "section": sec}).get_json()
    assert pr["properties"]["A"] == pytest.approx(r["properties"]["A"])
    before = json.dumps(client.get("/api/model").get_json(), sort_keys=True)
    after = json.dumps(client.get("/api/model").get_json(), sort_keys=True)
    assert before == after                  # properties never mutates
    up = client.post("/api/sections/builtup",
                     json={"action": "upsert", "section": sec}).get_json()
    assert up["model"]["sections"]["PG1"]["shape"] == "builtup"
    assert up["model"]["builtup_sections"]["PG1"]["components"] \
        == sec["components"]
    # POST /api/model round-trips byte-identically
    echoed = client.post("/api/model", json=up["model"]).get_json()
    assert json.dumps(echoed, sort_keys=True) == json.dumps(
        up["model"], sort_keys=True)
    sh = client.get("/api/sections/builtup/db/shapes?standard=aisc&family=W"
                    "&q=W14").get_json()
    assert sh["shapes"] and all("W14" in s["name"].upper()
                                for s in sh["shapes"])
    bad = client.post("/api/sections/builtup",
                      json={"action": "upsert",
                            "section": dict(sec, material="NOPE")})
    assert bad.status_code == 400
    de = client.post("/api/sections/builtup",
                     json={"action": "delete", "name": "PG1"}).get_json()
    assert "PG1" not in de["model"]["sections"]
    assert "builtup_sections" not in de["model"]
