"""v1.16 temperature gradients, shell / joint temperatures, projected frame
loads — data, serialization and validation (CONTRACT "Temperature
gradients, projected loads and auto-lateral generators").

ETABS parity:

* Assign > Frame Loads > Temperature: ``ThermalLoad.grad2`` / ``grad3`` —
  temperature GRADIENT (deg C per metre) along the member local 2 / local 3
  axis (``T(y) = grad2 * y``).  The uniform part stays ``ThermalLoad.dT``.
* Assign > Shell Loads > Temperature: :class:`ShellThermalLoad` — uniform
  ``dT`` (membrane expansion) and ``grad3`` through the thickness (deg C/m
  along the region local 3 = corner-ordering normal; hotter on the +3 face
  for ``grad3 > 0``).
* Joint-pattern temperature: :class:`JointTemperature` — a temperature at a
  joint; a frame member takes the AVERAGE of its two end-joint values
  (missing joint = 0) as a uniform ``dT`` (exact for the axial statics of a
  prismatic member under a linear temperature: the elongation is
  ``alpha * T_avg * L`` and the restrained force ``E A alpha T_avg``).
* Projected frame loads: ``MemberLoad.projected`` — the load value is per
  unit length PROJECTED on the plane normal to the load direction (gravity
  on a rafter: per horizontal length), so the engine applies
  ``w * sqrt(1 - (x . d)^2)`` per true member length.

Every default (grad 0, projected False, no shell / joint temperatures)
serializes exactly as before (no new keys) and loads nothing.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional, Tuple

PROJECTED_DIRECTIONS = ("gravity", "global_x", "global_y", "global_z")
_DIR_VEC = {"gravity": (0.0, 0.0, -1.0), "global_x": (1.0, 0.0, 0.0),
            "global_y": (0.0, 1.0, 0.0), "global_z": (0.0, 0.0, 1.0)}


@dataclass
class ShellThermalLoad:
    """Shell temperature load (deg C; gradient deg C / m through t)."""
    region_uid: str
    dT: float = 0.0
    grad3: float = 0.0

    def to_dict(self) -> dict:
        return {"region_uid": self.region_uid, "dT": float(self.dT),
                "grad3": float(self.grad3)}

    @classmethod
    def from_dict(cls, d: dict) -> "ShellThermalLoad":
        return cls(str(d["region_uid"]), float(d.get("dT", 0.0)),
                   float(d.get("grad3", 0.0)))


@dataclass
class JointTemperature:
    """Joint-pattern temperature (deg C) at a point."""
    point: Tuple[float, float, float]
    dT: float = 0.0

    def to_dict(self) -> dict:
        return {"point": [float(v) for v in self.point],
                "dT": float(self.dT)}

    @classmethod
    def from_dict(cls, d: dict) -> "JointTemperature":
        return cls(tuple(float(v) for v in d["point"]),
                   float(d.get("dT", 0.0)))


# ------------------------------------------------------------ serialization
def thermal_load_to_dict(tl) -> dict:
    """ThermalLoad dict; zero gradients are omitted (legacy shape)."""
    d = asdict(tl)
    for k in ("grad2", "grad3"):
        if not d.get(k):
            d.pop(k, None)
    return d


def member_load_to_dict(ml) -> dict:
    """MemberLoad dict; ``projected`` only when True (legacy shape)."""
    d = asdict(ml)
    if not d.get("projected"):
        d.pop("projected", None)
    return d


def pattern_ext_to_dict(pat) -> dict:
    """Extra LoadPattern keys (empty dict for a legacy pattern)."""
    out = {}
    st = getattr(pat, "shell_thermal_loads", None)
    if st:
        out["shell_thermal_loads"] = [s.to_dict() for s in st]
    jt = getattr(pat, "joint_temperatures", None)
    if jt:
        out["joint_temperatures"] = [j.to_dict() for j in jt]
    return out


def pattern_ext_from_dict(pat, pd: dict) -> None:
    """Read the v1.16 pattern keys (absent = defaults)."""
    for s in pd.get("shell_thermal_loads") or []:
        pat.shell_thermal_loads.append(ShellThermalLoad.from_dict(s))
    for j in pd.get("joint_temperatures") or []:
        pat.joint_temperatures.append(JointTemperature.from_dict(j))


# ---------------------------------------------------------------- geometry
def projected_factor(member, ml) -> float:
    """Length factor of a projected member load: ``sqrt(1 - (x.d)^2)`` with
    ``x`` the member axis and ``d`` the load direction (1.0 when the load is
    not projected — the multiply by 1.0 is bit-exact)."""
    if not getattr(ml, "projected", False):
        return 1.0
    d = _DIR_VEC[ml.direction]
    dx = [b - a for a, b in zip(member.pi, member.pj)]
    L = math.sqrt(sum(v * v for v in dx))
    if L <= 0.0:
        return 0.0
    c = sum(v * w for v, w in zip(dx, d)) / L
    return math.sqrt(max(0.0, 1.0 - c * c))


# --------------------------------------------------------------- validation
def _finite(v) -> bool:
    return (not isinstance(v, bool) and isinstance(v, (int, float))
            and math.isfinite(v))


def validate_pattern_ext(model, pat) -> None:
    """Validate the v1.16 load data of one pattern (raises ValueError)."""
    for tl in pat.thermal_loads:
        for k in ("grad2", "grad3"):
            if not _finite(getattr(tl, k, 0.0)):
                raise ValueError(f"Pattern {pat.name}: thermal load {k} must "
                                 "be a finite number (deg C / m)")
    for ml in pat.member_loads:
        if not getattr(ml, "projected", False):
            continue
        if not isinstance(ml.projected, bool):
            raise ValueError(f"Pattern {pat.name}: member load projected "
                             "must be a boolean")
        if ml.kind not in ("udl", "trapezoid"):
            raise ValueError(f"Pattern {pat.name}: projected member loads "
                             "must be distributed (udl | trapezoid)")
        if ml.direction not in PROJECTED_DIRECTIONS:
            raise ValueError(f"Pattern {pat.name}: projected member loads "
                             f"need a global direction "
                             f"{PROJECTED_DIRECTIONS}")
    regions = {r.uid: r for r in model.shells}
    for st in getattr(pat, "shell_thermal_loads", ()):
        r = regions.get(st.region_uid)
        if r is None:
            raise ValueError(f"Pattern {pat.name}: shell thermal load "
                             f"references unknown shell region "
                             f"{st.region_uid!r}")
        if r.behavior != "shell":
            raise ValueError(f"Pattern {pat.name}: shell thermal load on "
                             f"membrane region {st.region_uid!r} (needs "
                             "behavior 'shell')")
        if not (_finite(st.dT) and _finite(st.grad3)):
            raise ValueError(f"Pattern {pat.name}: shell thermal dT / grad3 "
                             "must be finite numbers")
    for jt in getattr(pat, "joint_temperatures", ()):
        p = jt.point
        if (len(p) != 3 or not all(_finite(v) for v in p)
                or not _finite(jt.dT)):
            raise ValueError(f"Pattern {pat.name}: joint temperature needs a "
                             "finite 3D point and a finite dT")


def joint_temperature_lookup(pat, tol: float = 1e-6):
    """``f(point) -> dT`` over the pattern's joint temperatures (0 when the
    joint has none; duplicates add)."""
    jts = list(getattr(pat, "joint_temperatures", ()))

    def f(pt) -> float:
        tot = 0.0
        for j in jts:
            if all(abs(a - b) < tol for a, b in zip(j.point, pt)):
                tot += j.dT
        return tot
    return f


def member_joint_temperature(pat, member) -> Optional[float]:
    """Average end-joint temperature of a member (None when neither end
    carries a joint temperature)."""
    jts = getattr(pat, "joint_temperatures", ())
    if not jts:
        return None
    f = joint_temperature_lookup(pat)
    ti, tj = f(member.pi), f(member.pj)
    if ti == 0.0 and tj == 0.0:
        return None
    return 0.5 * (ti + tj)
