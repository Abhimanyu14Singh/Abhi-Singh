"""ETABS material-property parity (v1.12) — analysis-only expansion.

Hand-pinned checks for the expanded :class:`Material` dataclass and its
engine wiring:

  * dataclass defaults, derived G/mass_per_volume/Fye/Fue, positional
    signature preservation;
  * to_dict/from_dict round-trip (new fields + Optional null), back-compat
    load of a minimal old material and a whole old model;
  * validation of the new fields (type/symmetry/E/nu/strengths/Ry/damping/
    lam/mass_density) and the model-level mass_source_mode;
  * the ETABS default-material library + helpers + API endpoint;
  * ANALYSIS wiring: per-material thermal alpha changes a thermal axial
    force (None => model default, bit-identical); mass_density drives the
    opt-in element_self_mass source and scales a modal period (None equals
    weight/g exactly); mat.fc / mat.fy / mat.Ry / mat.lam flow into the
    cracked-slab Mcr and the ASCE-41 hinge backbone, with the documented
    fallback preserved when the field is None.
"""

import math

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core.model import (
    AreaLoad,
    BuildingModel,
    FrameSection,
    MASS_SOURCE_MODES,
    MATERIAL_SYMMETRY,
    MATERIAL_TYPES,
    Material,
    PointSupport,
    ShellSection,
    ThermalLoad,
    default_material_library,
    library_material,
)
from skyframe.design.hinges import auto_backbone

G_ACCEL = 9.80665


# --------------------------------------------------------------------------- #
# 1. dataclass shape, defaults, derived properties
# --------------------------------------------------------------------------- #
def test_material_defaults_are_backcompat():
    """A material built the old way carries analysis-neutral defaults."""
    m = Material("C", 25_000_000.0)
    assert m.nu == 0.2 and m.unit_weight == 24.0
    assert m.material_type == "concrete" and m.symmetry == "isotropic"
    assert m.mass_density is None and m.alpha is None
    assert m.fc is None and m.fy is None and m.fu is None
    assert m.Ry == 1.1 and m.damping == 0.0
    assert m.lightweight is False and m.lam == 1.0
    assert m.color == "" and m.notes == ""


def test_material_positional_signature_preserved():
    """Material(name, E, nu, unit_weight) still binds positionally."""
    m = Material("S", 199_947_980.0, 0.3, 76.9729)
    assert (m.name, m.E, m.nu, m.unit_weight) == \
        ("S", 199_947_980.0, 0.3, 76.9729)


def test_material_derived_properties():
    """G, mass_per_volume, Fye, Fue derive from the stored fields."""
    m = Material("S", 200_000_000.0, 0.25, 78.5, fy=345_000.0, fu=450_000.0,
                 Ry=1.1)
    assert m.G == pytest.approx(200_000_000.0 / (2.0 * 1.25))
    assert m.mass_per_volume == pytest.approx(78.5 / G_ACCEL)   # weight/g
    assert m.Fye == pytest.approx(1.1 * 345_000.0)
    assert m.Fue == pytest.approx(1.1 * 450_000.0)


def test_material_derived_none_when_optionals_unset():
    """mass_density overrides weight/g; Fye/Fue are None without fy/fu."""
    m = Material("X", 30_000_000.0, mass_density=2.5)
    assert m.mass_per_volume == 2.5                    # explicit density wins
    assert m.Fye is None and m.Fue is None


def test_material_to_dict_echoes_derived_and_nulls():
    """to_dict echoes derived values; unset optionals serialize as null."""
    d = Material("C", 25_000_000.0).to_dict()
    assert d["G"] == pytest.approx(25_000_000.0 / 2.4)
    assert d["mass_per_volume"] == pytest.approx(24.0 / G_ACCEL)
    assert d["Fye"] is None and d["Fue"] is None
    assert d["fc"] is None and d["fy"] is None and d["mass_density"] is None
    assert d["material_type"] == "concrete"


# --------------------------------------------------------------------------- #
# 2. round-trip and back-compat
# --------------------------------------------------------------------------- #
def test_material_round_trip_through_model():
    """Every new field survives BuildingModel.from_dict(to_dict())."""
    src = Material("MX", 26_000_000.0, 0.18, 23.0,
                   material_type="masonry", symmetry="isotropic",
                   mass_density=2.35, alpha=8.5e-6, fc=15_000.0,
                   fy=250_000.0, fu=400_000.0, Ry=1.3, damping=0.03,
                   lightweight=True, lam=0.75, color="#abcdef", notes="hi")
    m = BuildingModel(name="rt")
    m.add_material(src)
    back = BuildingModel.from_dict(m.to_dict()).materials["MX"]
    for f in ("name", "E", "nu", "unit_weight", "material_type", "symmetry",
              "mass_density", "alpha", "fc", "fy", "fu", "Ry", "damping",
              "lightweight", "lam", "color", "notes"):
        assert getattr(back, f) == getattr(src, f), f


def test_backcompat_minimal_old_material_loads():
    """A pre-v1.12 material dict (name/E/nu/unit_weight only) rebuilds with
    type=concrete and every optional None -> the engine fallbacks fire."""
    old = {"materials": {"CONC": {"name": "CONC", "E": 25_000_000.0,
                                  "nu": 0.2, "unit_weight": 24.0}}}
    mat = BuildingModel.from_dict(old).materials["CONC"]
    assert mat.material_type == "concrete" and mat.symmetry == "isotropic"
    assert mat.fc is None and mat.fy is None and mat.fu is None
    assert mat.mass_density is None and mat.alpha is None
    assert mat.Ry == 1.1 and mat.lam == 1.0 and mat.damping == 0.0


def test_backcompat_null_optionals_stay_null():
    """Explicit JSON nulls load back as None (not coerced to a number)."""
    js = {"materials": {"C": {"name": "C", "E": 25_000_000.0,
                              "mass_density": None, "alpha": None,
                              "fc": None, "fy": None, "fu": None}}}
    mat = BuildingModel.from_dict(js).materials["C"]
    assert (mat.mass_density, mat.alpha, mat.fc, mat.fy, mat.fu) == \
        (None, None, None, None, None)


def test_model_round_trip_includes_mass_source_mode():
    """mass_source_mode round-trips; absent => 'weight' (pre-v1.12)."""
    m = BuildingModel(name="m")
    m.add_library_material("A992Fy50")
    m.mass_source_mode = "element_self_mass"
    back = BuildingModel.from_dict(m.to_dict())
    assert back.mass_source_mode == "element_self_mass"
    d = m.to_dict()
    del d["mass_source_mode"]
    assert BuildingModel.from_dict(d).mass_source_mode == "weight"


# --------------------------------------------------------------------------- #
# 3. default material library
# --------------------------------------------------------------------------- #
def test_default_material_library_pinned_values():
    """The four ETABS built-ins carry their contract values."""
    lib = default_material_library()
    assert set(lib) == {"A992Fy50", "4000Psi", "A615Gr60", "A416Gr270"}
    a992 = lib["A992Fy50"]
    assert a992.material_type == "steel" and a992.symmetry == "isotropic"
    assert a992.E == 199_947_980.0 and a992.fy == 344_740.0
    assert a992.fu == 448_160.0 and a992.Ry == 1.1
    assert a992.Fye == pytest.approx(379_214.0)
    conc = lib["4000Psi"]
    assert conc.material_type == "concrete" and conc.fc == 27_580.0
    assert conc.fy is None and conc.alpha == pytest.approx(9.9e-6)
    rebar = lib["A615Gr60"]
    assert rebar.material_type == "rebar" and rebar.symmetry == "uniaxial"
    assert rebar.Ry == 1.25 and rebar.Fye == pytest.approx(517_112.5)
    tendon = lib["A416Gr270"]
    assert tendon.material_type == "tendon" and tendon.E == 196_501_000.0
    assert tendon.fu == 1_861_580.0


def test_library_material_helper_and_model_add():
    """library_material returns a copy; add_library_material inserts it."""
    with pytest.raises(KeyError):
        library_material("NoSuchMaterial")
    m = BuildingModel(name="lib")
    got = m.add_library_material("4000Psi")
    assert got.name == "4000Psi"
    assert m.materials["4000Psi"].fc == 27_580.0
    m.validate()                                       # library passes rules


# --------------------------------------------------------------------------- #
# 4. validation
# --------------------------------------------------------------------------- #
def _model_with(mat: Material) -> BuildingModel:
    m = BuildingModel(name="v")
    m.add_material(mat)
    return m


@pytest.mark.parametrize("kw, msg", [
    ({"material_type": "wood"}, "material_type"),
    ({"symmetry": "orthotropic"}, "symmetry"),
    ({"E": 0.0}, "E must be"),
    ({"nu": 0.6}, "nu"),
    ({"fc": -1.0}, "fc must be"),
    ({"fy": 0.0}, "fy must be"),
    ({"fu": float("inf")}, "fu must be"),
    ({"Ry": 0.0}, "Ry"),
    ({"Ry": 2.5}, "Ry"),
    ({"damping": 1.0}, "damping"),
    ({"damping": -0.1}, "damping"),
    ({"lam": 0.0}, "lam"),
    ({"mass_density": -2.0}, "mass_density"),
    ({"alpha": float("nan")}, "alpha"),
])
def test_material_validation_rejects_bad_fields(kw, msg):
    base = dict(name="BAD", E=25_000_000.0)
    base.update(kw)                                    # kw may override E/nu
    mat = Material(**base)
    with pytest.raises(ValueError, match=msg):
        _model_with(mat).validate()


def test_symmetry_uniaxial_ignores_poisson():
    """A uniaxial (rebar/tendon) material skips the isotropic nu range check."""
    mat = Material("R", 199_947_980.0, nu=0.9, symmetry="uniaxial",
                   material_type="rebar", fy=413_690.0)
    _model_with(mat).validate()                        # no raise despite nu=0.9


def test_mass_source_mode_validation():
    """mass_source_mode is enumerated; a bad value is rejected."""
    assert set(MASS_SOURCE_MODES) == {"weight", "element_self_mass"}
    m = BuildingModel(name="ms")
    m.mass_source_mode = "bogus"
    with pytest.raises(ValueError, match="mass_source_mode"):
        m.validate()


def test_enumerations_exposed():
    assert "concrete" in MATERIAL_TYPES and "steel" in MATERIAL_TYPES
    assert "orthotropic" not in MATERIAL_TYPES
    assert MATERIAL_SYMMETRY == ("isotropic", "uniaxial")


# --------------------------------------------------------------------------- #
# 5. ANALYSIS wiring — per-material thermal alpha
# --------------------------------------------------------------------------- #
def _thermal_axial_force(alpha):
    """Fixed-fixed concrete bar heated dT; return the axial force N[0]."""
    E, b, h, L, dT = 25_000_000.0, 0.3, 0.5, 6.0, 30.0
    m = BuildingModel(name="th")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E, 0.2, 24.0, alpha=alpha))
    m.set_stories([3.0])
    m.add_section(FrameSection.rectangular("BAR", "C", b, h))
    m.add_member("beam", "BAR", (0, 0, 0), (L, 0, 0), story="Story1", uid="B1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.supports.append(PointSupport((L, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.pattern("T", "other").thermal_loads.append(ThermalLoad("B1", dT))
    m.add_case("T", {"T": 1.0})
    m.num_modes = 0
    d = OpenSeesEngine(m).run().to_dict()
    return d["cases"]["T"]["member_stations"]["B1"]["N"][0]


def test_per_material_alpha_none_is_model_default():
    """alpha=None uses the model-wide thermal_alpha (bit-identical): the
    axial force equals -E*A*alpha_model*dT."""
    E, A, dT = 25_000_000.0, 0.3 * 0.5, 30.0
    n_none = _thermal_axial_force(None)
    n_expl = _thermal_axial_force(1.2e-5)              # == model default
    assert n_none == pytest.approx(-E * A * 1.2e-5 * dT, rel=1e-9)
    assert n_expl == pytest.approx(n_none, rel=1e-12)  # explicit default match


def test_per_material_alpha_changes_thermal_force():
    """A different per-material alpha scales the thermal force linearly and
    ONLY through that material (independent of the model default)."""
    n_default = _thermal_axial_force(1.2e-5)
    n_low = _thermal_axial_force(9.9e-6)
    assert n_low == pytest.approx(n_default * (9.9e-6 / 1.2e-5), rel=1e-9)
    assert abs(n_low) < abs(n_default)


# --------------------------------------------------------------------------- #
# 6. ANALYSIS wiring — mass_density -> element_self_mass
# --------------------------------------------------------------------------- #
def _portal(mass_density=None, mode="element_self_mass"):
    m = BuildingModel(name="p")
    m.rigid_diaphragms = False
    m.add_material(Material("S", 199_947_980.0, 0.3, 76.9729,
                            material_type="steel", mass_density=mass_density))
    m.set_stories([3.0])
    m.add_section(FrameSection.rectangular("col", "S", 0.3, 0.4))
    m.add_member("column", "col", (0, 0, 0), (0, 0, 3), story="Story1",
                 uid="C1")
    m.add_member("column", "col", (4, 0, 0), (4, 0, 3), story="Story1",
                 uid="C2")
    m.add_member("beam", "col", (0, 0, 3), (4, 0, 3), story="Story1", uid="B1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.supports.append(PointSupport((4, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.mass_source_mode = mode
    return m


def test_element_self_mass_total_equals_density_times_volume():
    """element_self_mass lumps mat.mass_per_volume * element volume as
    translational nodal mass; the total (dof-1) mass equals rho*V exactly."""
    m = _portal(mass_density=None)                     # rho = weight/g
    eng = OpenSeesEngine(m)
    eng.run_modal(1)
    total = sum(v for (t, dof), v in eng._asm.mass_map.items() if dof == 1)
    rho = 76.9729 / G_ACCEL
    vol = 0.3 * 0.4 * 3.0 * 2 + 0.3 * 0.4 * 4.0        # 2 columns + 1 beam
    assert total == pytest.approx(rho * vol, rel=1e-12)


def test_mass_density_none_equals_weight_over_g():
    """With element_self_mass, mass_density=None gives the SAME period as an
    explicit mass_density equal to unit_weight/g (mass_per_volume identity)."""
    t_none = OpenSeesEngine(_portal(mass_density=None)).run_modal(1).periods[0]
    t_wg = OpenSeesEngine(
        _portal(mass_density=76.9729 / G_ACCEL)).run_modal(1).periods[0]
    assert t_wg == pytest.approx(t_none, rel=1e-9)


def test_mass_density_scales_modal_period():
    """Doubling mass_density doubles the self-mass -> the fundamental period
    scales by sqrt(2); the weight (unit_weight) is untouched, so mass is
    decoupled from weight."""
    t1 = OpenSeesEngine(
        _portal(mass_density=76.9729 / G_ACCEL)).run_modal(1).periods[0]
    t2 = OpenSeesEngine(
        _portal(mass_density=2.0 * 76.9729 / G_ACCEL)).run_modal(1).periods[0]
    assert t2 / t1 == pytest.approx(math.sqrt(2.0), rel=1e-6)


# --------------------------------------------------------------------------- #
# 7. ANALYSIS wiring — fc / fy / Ry / lambda into hinge & cracked
# --------------------------------------------------------------------------- #
def _steel_hinge(Ry=1.1, fy=None, expected_factor=None):
    m = BuildingModel(name="h")
    m.add_material(Material("S", 199_947_980.0, 0.3, 76.9729,
                            material_type="steel", fy=fy, Ry=Ry))
    m.add_section(FrameSection.from_library("W12x26", "S"))
    mem = m.add_member("beam", "W12x26", (0, 0, 0), (5, 0, 0), uid="B1")
    mem.hinges = "auto_m3"
    kw = {} if expected_factor is None else {"expected_factor": expected_factor}
    return auto_backbone(m, mem, **kw)


def test_Ry_defaults_from_material_and_is_overridable():
    """auto_backbone defaults expected_factor to mat.Ry (1.1 bit-identical);
    a hinge_params expected_factor still overrides it."""
    my_11 = _steel_hinge(Ry=1.1)["My"]
    my_15 = _steel_hinge(Ry=1.5)["My"]
    assert my_15 / my_11 == pytest.approx(1.5 / 1.1, rel=1e-9)
    # explicit kwarg overrides the material Ry
    my_over = _steel_hinge(Ry=1.5, expected_factor=1.1)["My"]
    assert my_over == pytest.approx(my_11, rel=1e-12)


def test_steel_hinge_fy_fallback_and_use():
    """mat.fy None -> the documented 345 MPa A992 fallback; a set fy flows in
    (regression for the getattr(mat,'fy',...) or <default> consumer sites)."""
    my_fallback = _steel_hinge(fy=None)["My"]          # 345 MPa default
    my_345 = _steel_hinge(fy=345_000.0)["My"]
    my_hi = _steel_hinge(fy=450_000.0)["My"]
    assert my_345 == pytest.approx(my_fallback, rel=1e-9)   # None == default
    assert my_hi > my_fallback                              # set value used


def _concrete_hinge(fc=None):
    m = BuildingModel(name="ch")
    m.add_material(Material("C", 25_000_000.0, 0.2, 24.0,
                            material_type="concrete", fc=fc))
    m.add_section(FrameSection.rectangular("R", "C", 0.3, 0.5))
    mem = m.add_member("beam", "R", (0, 0, 0), (5, 0, 0), uid="B1")
    mem.hinges = "auto_m3"
    return auto_backbone(m, mem)


def test_concrete_hinge_fc_fallback_and_use():
    """mat.fc None -> fc_from_E fallback; a set fc changes the hinge Mn away
    from that fallback."""
    my_fallback = _concrete_hinge(fc=None)["My"]
    my_set = _concrete_hinge(fc=40_000.0)["My"]
    assert my_set != pytest.approx(my_fallback)
    assert my_set > my_fallback                        # higher fc -> higher Mn


def _slab_strip(fc=None, lam=1.0, lightweight=False):
    m = BuildingModel(name="strip")
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("CONC", 25.0e6, 0.2, 24.0, material_type="concrete",
                            fc=fc, lam=lam, lightweight=lightweight))
    m.add_shell_section(ShellSection("SLAB", "CONC", 0.2))
    m.set_stories([3.0])
    m.add_shell("slab", "shell", "SLAB",
                [(0, 0, 3.0), (6, 0, 3.0), (6, 1, 3.0), (0, 1, 3.0)],
                mesh_size=0.5, story="Story1", uid="S1")
    for y in (0.0, 0.5, 1.0):
        m.supports.append(PointSupport((0, y, 3.0), (1, 1, 1, 0, 0, 0)))
        m.supports.append(PointSupport((6, y, 3.0), (1, 1, 1, 0, 0, 0)))
    m.pattern("Q").area_loads.append(AreaLoad("S1", 1.0))
    m.add_case("Q", {"Q": 1.0})
    m.validate()
    return m


def _first_mcr(model):
    res = OpenSeesEngine(model).run_cracked("Q")
    return next(iter(res.cracking.values()))["Mcr"]


def test_fc_flows_into_cracked_mcr():
    """A concrete material's fc drives the cracked-slab modulus of rupture:
    Mcr = fr*t^2/6 with fr = 0.62*sqrt(fc'[MPa]); a set fc moves Mcr off the
    fc_from_E fallback and matches the hand value."""
    mcr_fallback = _first_mcr(_slab_strip(fc=None))
    fc_set = 40_000.0                                  # kPa (40 MPa)
    mcr_set = _first_mcr(_slab_strip(fc=fc_set))
    fr = 0.62 * math.sqrt(fc_set / 1000.0) * 1000.0    # kPa
    mcr_hand = fr * 0.2 ** 2 / 6.0
    assert mcr_set == pytest.approx(mcr_hand, rel=1e-9)
    assert mcr_set != pytest.approx(mcr_fallback)


def test_lightweight_lambda_scales_cracked_mcr():
    """Lightweight lambda multiplies the modulus of rupture: lam=0.75 gives
    exactly 0.75 x the normal-weight Mcr (lam=1.0 is bit-identical)."""
    mcr_nw = _first_mcr(_slab_strip(fc=30_000.0, lam=1.0))
    mcr_lw = _first_mcr(_slab_strip(fc=30_000.0, lam=0.75, lightweight=True))
    assert mcr_lw == pytest.approx(0.75 * mcr_nw, rel=1e-12)
