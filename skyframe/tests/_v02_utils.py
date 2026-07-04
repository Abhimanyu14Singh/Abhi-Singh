"""Shared helpers for the v0.2 deep-validation test files.

Not a test module (no ``test_`` prefix).  Everything here sticks to the
public surface promised by CONTRACT.md: BuildingModel / PointSupport /
NodalLoad / NodalMass, OpenSeesEngine(model).run().to_dict(), and the v0.2
``member_stations`` block.  Guards for v0.2 features live in the test
modules; this module must import cleanly at ANY backend state.
"""

from __future__ import annotations

import copy
import dataclasses
import math

import numpy as np
import pytest

from skyframe.core import model as model_mod
from skyframe.core.model import (
    BuildingModel,
    FrameSection,
    Material,
    NodalLoad,
    NodalMass,
    PointSupport,
)

E_CONC = 25_000_000.0  # kPa, matches the existing benchmark suite

# ---- feature probes (evaluated lazily so a mid-rewrite backend just skips) --
def has_member_load() -> bool:
    """True when the v0.2 MemberLoad API (dataclass + pattern list) exists."""
    if not hasattr(model_mod, "MemberLoad"):
        return False
    fields = {f.name for f in dataclasses.fields(model_mod.LoadPattern)}
    return "member_loads" in fields


def has_releases() -> bool:
    return "releases" in {f.name for f in dataclasses.fields(model_mod.FrameMember)}


def has_shell_api() -> bool:
    needed = ("ShellSection", "ShellRegion", "AreaLoad")
    if not all(hasattr(model_mod, n) for n in needed):
        return False
    fields = {f.name for f in dataclasses.fields(BuildingModel)}
    return {"shell_sections", "shells"} <= fields


# --------------------------------------------------------------------------- #
# model plumbing
# --------------------------------------------------------------------------- #
def new_model(name: str, story_heights, E: float = E_CONC, nu: float = 0.2,
              mat: str = "CONC") -> BuildingModel:
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.add_material(Material(mat, E=E, nu=nu))
    mdl.set_stories(list(story_heights))
    return mdl


def finish(mdl: BuildingModel, mass_point) -> BuildingModel:
    """Token modal mass so engine.run()'s eigen stage has mass to chew on."""
    mdl.nodal_masses.append(NodalMass(tuple(mass_point), mx=1.0, my=1.0, mz=1.0))
    mdl.num_modes = 1
    return mdl


def run(mdl: BuildingModel) -> dict:
    from skyframe.engine.opensees_engine import OpenSeesEngine
    return OpenSeesEngine(mdl).run().to_dict()


def find_node(results: dict, pt, tol=1e-6) -> str:
    for tag, xyz in results["nodes"].items():
        if all(abs(a - b) < tol for a, b in zip(xyz, pt)):
            return tag
    raise AssertionError(f"no FE node at {pt}")


def add_gravity_udl(mdl: BuildingModel, pat_name: str, uid: str, w: float) -> None:
    """Full-span gravity UDL via MemberLoad when available, else legacy
    MemberUDL (contract keeps it as alias/legacy)."""
    pat = mdl.pattern(pat_name)
    if has_member_load():
        pat.member_loads.append(model_mod.MemberLoad(member_uid=uid, kind="udl", w=w))
    else:
        pat.member_udls.append(model_mod.MemberUDL(uid, w))


def add_member_load(mdl: BuildingModel, pat_name: str, **kw) -> None:
    mdl.pattern(pat_name).member_loads.append(model_mod.MemberLoad(**kw))


# --------------------------------------------------------------------------- #
# station-diagram helpers
# --------------------------------------------------------------------------- #
def stations(results: dict, case: str, uid: str) -> dict:
    """member_stations block for one member; skip if backend not there yet."""
    st = results["cases"][case].get("member_stations")
    if not st:
        pytest.skip("backend does not emit member_stations yet")
    assert uid in st, f"member_stations missing member {uid}; has {sorted(st)}"
    return st[uid]


def check_x_grid(st: dict, L: float) -> np.ndarray:
    """Contract: 11 equally spaced stations x = [0 ... L] per ORIGINAL member."""
    xs = np.asarray(st["x"], float)
    assert len(xs) == 11, f"expected 11 stations, got {len(xs)}"
    assert xs[0] == pytest.approx(0.0, abs=1e-9)
    assert xs[-1] == pytest.approx(L, rel=1e-9)
    assert np.all(np.diff(xs) > 0), f"station x not increasing: {xs}"
    return xs


def sign_fit(vals, ref) -> float:
    """Global +/-1 that best maps engine sign convention onto the analytic
    (sagging-positive) convention.  Fitted at the largest |ref| station so a
    genuinely wrong diagram can never sneak through: after applying the ONE
    sign, every station must match pointwise."""
    vals = np.asarray(vals, float)
    ref = np.asarray(ref, float)
    k = int(np.argmax(np.abs(ref)))
    if abs(ref[k]) < 1e-12 or vals[k] == 0.0:
        return 1.0
    return math.copysign(1.0, vals[k] * ref[k])


def assert_station_match(st: dict, key: str, ref_fn, L: float, rel: float,
                         label: str = "") -> float:
    """Compare engine station array (one global sign allowed, see sign_fit)
    against an analytic function, pointwise, atol = rel * max|analytic|."""
    xs = check_x_grid(st, L)
    vals = np.asarray(st[key], float)
    assert len(vals) == 11
    ref = np.asarray([ref_fn(float(x)) for x in xs])
    scale = float(np.max(np.abs(ref)))
    assert scale > 0.0
    s = sign_fit(vals, ref)
    np.testing.assert_allclose(
        s * vals, ref, rtol=0.0, atol=rel * scale,
        err_msg=f"{label}: {key} stations disagree with closed form "
                f"(sign fit {s:+.0f}, x={xs.tolist()})")
    return s


# --------------------------------------------------------------------------- #
# shell-mesh discovery
# --------------------------------------------------------------------------- #
def discover_mesh_nodes(mdl: BuildingModel, anchor_points) -> list:
    """Return every FE node coordinate produced for `mdl` WITHOUT knowing the
    mesher's subdivision rule: deep-copy the model, clamp a couple of corner
    points (so the probe is non-singular), run once, read results['nodes'].
    The real run re-meshes identically (dedup tol 1e-6 per CONTRACT.md), so
    supports/loads placed at these coordinates land on real nodes."""
    probe = copy.deepcopy(mdl)
    for p in anchor_points:
        probe.supports.append(PointSupport(tuple(p), (1, 1, 1, 1, 1, 1)))
    if not probe.cases:
        probe.pattern("_PROBE", "other").nodal_loads.append(
            NodalLoad(tuple(anchor_points[0]), fz=-1.0))
        probe.add_case("_PROBE", {"_PROBE": 1.0})
    finish(probe, anchor_points[0])
    d = run(probe)
    return [tuple(float(v) for v in xyz) for xyz in d["nodes"].values()]


def tributary_lengths(coords_1d) -> np.ndarray:
    """Consistent (= lumped, identical for linear edge shape functions)
    nodal weights for a uniform traction along a sorted 1-D node string."""
    c = np.asarray(coords_1d, float)
    seg = np.diff(c)
    trib = np.zeros(len(c))
    trib[:-1] += seg / 2.0
    trib[1:] += seg / 2.0
    return trib


__all__ = [
    "E_CONC", "model_mod", "BuildingModel", "FrameSection", "Material",
    "NodalLoad", "NodalMass", "PointSupport",
    "has_member_load", "has_releases", "has_shell_api",
    "new_model", "finish", "run", "find_node",
    "add_gravity_udl", "add_member_load",
    "stations", "check_x_grid", "sign_fit", "assert_station_match",
    "discover_mesh_nodes", "tributary_lengths",
]
