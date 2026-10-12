"""Wave C (C9): Section Designer moment-curvature.  CONTRACT "Anisotropic
shells, material angle, Section Designer moment-curvature, layered shells
with any material, results CSV folder".

Rectangular RC section by hand (strain compatibility):
b = 0.30, h = 0.50, 3 bars of 491 mm^2 at d = 0.45 (y = 0, z = -0.20),
f'c = 30 MPa (Hognestad: parabola to eps_c0 = 0.002, linear to 0.85 f'c
at eps_cu = 0.0035, no tension), steel elastic-perfectly-plastic
fy = 420 MPa, Es = 200 GPa, P = 0.

* first yield: eps_s = eps_y = fy/Es = 0.0021, top strain eps_t =
  eps_y c / (d - c); C = b c/eps_t int_0^eps_t sigma = As fy (one equation
  in c, solved to 1e-14 by bisection in the test); arm = d - c (1 -
  ebar/eps_t), ebar = int sigma e / int sigma; M_y = As fy arm,
  phi_y = eps_y / (d - c).
* ultimate: eps_t = eps_cu = 0.0035, steel yielded: c = As fy eps_cu /
  (b int_0^eps_cu sigma) with int sigma = fc (2/3 eps_c0) + (eps_cu -
  eps_c0)(fc + 0.85 fc)/2 (closed form), M_u = As fy (d - c (1 -
  ebar/eps_cu)), phi_u = eps_cu / c.
"""

from __future__ import annotations

import math

import pytest

from skyframe.core.model import Material
from skyframe.core import moment_curvature as mc
from skyframe.core.sections_designer import DesignerSection

B, H, D, AS1 = 0.30, 0.50, 0.45, 491e-6
AS = 3 * AS1
FC, FY, ES = 30_000.0, 420_000.0, 200e6
E0, ECU = 0.002, 0.0035


def _mats():
    c = Material("C30", E=27e6, nu=0.2, material_type="concrete")
    c.fc = FC
    s = Material("R", E=ES, nu=0.3, material_type="rebar")
    s.fy = FY
    return {"C30": c, "R": s}


def _rect(bars=True, top=False):
    ds = DesignerSection(name="RC", material="C30", base="concrete")
    ds.polygons.append({"vertices": [[-B / 2, -H / 2], [B / 2, -H / 2],
                                     [B / 2, H / 2], [-B / 2, H / 2]],
                        "material": "C30", "hole": False})
    if bars:
        for y in (-0.1, 0.0, 0.1):
            ds.rebar.append({"y": y, "z": H / 2 - D, "area": AS1,
                             "material": "R"})
    if top:
        for y in (-0.1, 0.1):
            ds.rebar.append({"y": y, "z": H / 2 - 0.05, "area": AS1,
                             "material": "R"})
    return ds


def _sig(e):
    """Hognestad (compression positive here)."""
    if e <= E0:
        r = e / E0
        return FC * (2 * r - r * r)
    return FC + (0.85 * FC - FC) * (e - E0) / (ECU - E0)


def _int_sig(et):
    """(int_0^et sigma de, int_0^et sigma e de) - closed forms."""
    if et <= E0:
        i0 = FC * (et * et / E0 - et ** 3 / (3 * E0 * E0))
        i1 = FC * (2 * et ** 3 / (3 * E0) - et ** 4 / (4 * E0 * E0))
        return i0, i1
    i0 = FC * (2 * E0 / 3)
    i1 = FC * (2 * E0 ** 2 / 3 - E0 ** 2 / 4)
    k = -0.15 * FC / (ECU - E0)                       # slope
    a = FC - k * E0                                   # sigma = a + k e
    i0 += a * (et - E0) + k * (et ** 2 - E0 ** 2) / 2
    i1 += a * (et ** 2 - E0 ** 2) / 2 + k * (et ** 3 - E0 ** 3) / 3
    return i0, i1


def _hand_yield():
    ey = FY / ES
    lo, hi = 1e-4, D - 1e-4
    for _ in range(200):
        c = 0.5 * (lo + hi)
        et = ey * c / (D - c)
        i0, _ = _int_sig(et)
        C = B * c / et * i0
        if C > AS * FY:
            hi = c
        else:
            lo = c
    c = 0.5 * (lo + hi)
    et = ey * c / (D - c)
    i0, i1 = _int_sig(et)
    arm = D - c * (1 - (i1 / i0) / et)
    return c, AS * FY * arm, ey / (D - c)


def _hand_ult():
    i0, i1 = _int_sig(ECU)
    c = AS * FY * ECU / (B * i0)
    arm = D - c * (1 - (i1 / i0) / ECU)
    return c, AS * FY * arm, ECU / c


def test_rc_rectangle_yield_and_ultimate_by_hand():
    out = mc.moment_curvature(_rect(), _mats(), P=0.0, angle=0.0,
                              concrete="simple", steel="epp",
                              params={"eps_c0": E0, "eps_cu": ECU})
    c_y, M_y, phi_y = _hand_yield()
    c_u, M_u, phi_u = _hand_ult()
    assert out["yield"]["M"] == pytest.approx(M_y, rel=2e-3)
    assert out["yield"]["phi"] == pytest.approx(phi_y, rel=2e-3)
    assert out["yield"]["na_depth"] == pytest.approx(c_y, rel=3e-3)
    assert out["ultimate"]["M"] == pytest.approx(M_u, rel=2e-3)
    assert out["ultimate"]["phi"] == pytest.approx(phi_u, rel=2e-3)
    assert out["ultimate"]["na_depth"] == pytest.approx(c_u, rel=3e-3)
    assert out["ultimate"]["eps_c"] == pytest.approx(-ECU, rel=1e-6)
    assert out["limit"] == "concrete crushing"
    # monotone curvature, M(0) = 0, the curve contains the yield point
    phis = [r["phi"] for r in out["points"]]
    assert phis == sorted(phis) and out["points"][0]["M"] == \
        pytest.approx(0.0, abs=1e-9)
    assert any(r is out["yield"] or r == out["yield"]
               for r in out["points"])
    # Caltrans idealisation: through first yield, area conserved
    idl = out["idealized"]
    assert idl["k_e"] == pytest.approx(out["yield"]["M"] /
                                       out["yield"]["phi"])
    area_bil = idl["Mp"] * idl["phi_u"] - idl["Mp"] ** 2 / (2 * idl["k_e"])
    assert area_bil == pytest.approx(idl["area"], rel=1e-9)
    assert out["yield"]["M"] < idl["Mp"] < 1.1 * out["ultimate"]["M"]


def test_rc_rectangle_hand_values_numbers():
    """Pin the hand numbers themselves (independent of the module)."""
    c_y, M_y, phi_y = _hand_yield()
    c_u, M_u, phi_u = _hand_ult()
    # As fy = 618.66 kN; ultimate block int = 30000 (0.0013333 + 0.0015 *
    # 0.925) = 81.625 -> c = 618.66 * 0.0035 / (0.3 * 81.625) = 0.08843 m
    assert AS * FY == pytest.approx(618.66, rel=1e-12)
    assert c_u == pytest.approx(618.66 * 0.0035 / (0.3 * 81.625), rel=1e-9)
    assert 0.08 < c_u < 0.1 and 0.12 < c_y < 0.16
    assert 230.0 < M_y < 260.0 and 245.0 < M_u < 260.0
    assert phi_u / phi_y > 4.0


def test_axial_load_and_angle_and_models():
    m = _mats()
    base = mc.moment_curvature(_rect(top=True), m, P=0.0)
    comp = mc.moment_curvature(_rect(top=True), m, P=800.0)
    # moderate compression raises the yield moment and cuts ductility
    assert comp["yield"]["M"] > base["yield"]["M"]
    assert comp["ultimate"]["phi"] < base["ultimate"]["phi"]
    # axial equilibrium holds at every step
    sec = mc._Section(_rect(top=True), m, 0.0, "simple", "simple",
                      mc._normalise(None), mc.N_STRIPS)
    for r in comp["points"][::7]:
        N, M, _ = sec.forces(r["eps0"], r["phi"])
        assert N == pytest.approx(-800.0, abs=1e-6)
        assert M == pytest.approx(r["M"], rel=1e-9, abs=1e-9)
    # 90 degrees: bending about the 2-axis; no bars in tension -> the
    # unreinforced-width response has no yield point before crushing
    side = mc.moment_curvature(_rect(), m, angle=90.0)
    assert side["angle"] == 90.0 and side["depth"] == pytest.approx(B)
    # 180 degrees: the bars sit on the compression side
    flip = mc.moment_curvature(_rect(), m, angle=180.0)
    assert flip["ultimate"]["M"] < 0.5 * base["ultimate"]["M"]
    # confined Mander is more ductile than the unconfined Hognestad law
    conf = mc.moment_curvature(_rect(top=True), m, concrete="mander_confined",
                               params={"fcc_ratio": 1.3,
                                       "eps_cu_conf": 0.015})
    assert conf["ultimate"]["phi"] > 2 * base["ultimate"]["phi"]
    park = mc.moment_curvature(_rect(top=True), m, steel="park")
    assert park["limit"] in ("concrete crushing", "steel rupture")
    rows = mc.table_rows(base)
    assert rows[-1]["event"] == "concrete crushing"
    assert any(r["event"] == "first yield" for r in rows)


def test_validation():
    m = _mats()
    with pytest.raises(ValueError, match="concrete model"):
        mc.moment_curvature(_rect(), m, concrete="bogus")
    with pytest.raises(ValueError, match="unknown moment-curvature"):
        mc.moment_curvature(_rect(), m, params={"x": 1})
    with pytest.raises(ValueError, match="eps_cu"):
        mc.moment_curvature(_rect(), m, params={"eps_cu": 0.001})
    with pytest.raises(ValueError, match="equilibrium"):
        mc.moment_curvature(_rect(), m, P=1e6)


def test_steel_section_plastic_moment():
    """Steel W-like rectangle 0.2 x 0.4, fy = 345 MPa EPP: the moment
    tends to Mp = fy b h^2 / 4 and first yield is My = fy b h^2 / 6."""
    st = Material("S", E=200e6, nu=0.3, material_type="steel")
    st.fy = 345_000.0
    ds = DesignerSection(name="ST", material="S", base="steel")
    ds.polygons.append({"vertices": [[-0.1, -0.2], [0.1, -0.2], [0.1, 0.2],
                                     [-0.1, 0.2]], "material": "S",
                        "hole": False})
    out = mc.moment_curvature(ds, {"S": st}, steel="epp",
                              params={"eps_su": 0.05})
    My = 345_000.0 * 0.2 * 0.4 ** 2 / 6
    Mp = 345_000.0 * 0.2 * 0.4 ** 2 / 4
    assert out["yield"]["M"] == pytest.approx(My, rel=1e-3)
    assert out["ultimate"]["M"] == pytest.approx(Mp, rel=2e-3)
    assert out["limit"] == "steel rupture"
    assert out["ultimate"]["phi"] == pytest.approx(0.05 / 0.2, rel=1e-6)
