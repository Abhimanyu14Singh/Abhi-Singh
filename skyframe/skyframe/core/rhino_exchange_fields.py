"""Model fields of the Rhino / Grasshopper exchange (``skyframe.exchange/1``).

Kept separate from :mod:`skyframe.core.rhino_exchange` (the conversion / merge
logic) so :mod:`skyframe.core.model` can import it without pulling in the
repair / edit machinery.

* ``source_ref`` on :class:`FrameMember`, :class:`ShellRegion`,
  :class:`PointSupport` and :class:`LinkMember`:
  ``{"session": str, "id": str, "part"?: int}`` -- the exchange object the
  SkyFrame object came from (``part`` = index of the piece when one source
  object makes several SkyFrame objects: polyline segments, sampled NURBS
  segments, mesh faces).  ``None`` (default) = drawn natively; emitted by
  ``to_dict`` only when set.
* ``BuildingModel.exchange_mappings``: ``{name: {"rules": [rule, ...]}}``
  -- saved layer-mapping profiles (emitted only when non-empty).
* ``BuildingModel.exchange_sessions``: ``{session: {"app", "doc", "units",
  "tolerance", "last_update", "joints": {key: [x, y, z]}}}`` -- one entry
  per exchange session applied to the model (emitted only when
  non-empty); ``joints`` holds the session's pure joints (points without
  a support), which have no other home in the model.

A model that never used the exchange carries none of these keys, so its
JSON is byte-identical to the pre-exchange format.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Dict, Optional

RULE_KEYS = ("layer", "role", "section", "material", "shell_section",
             "story_rule", "angle", "thickness", "mesh_size", "restraint")
ROLES = ("column", "beam", "brace", "frame", "wall", "slab", "shell",
         "support", "joint", "link", "ignore")


# --------------------------------------------------------------------------- #
# source_ref
# --------------------------------------------------------------------------- #
def normalize_ref(ref) -> Optional[dict]:
    """Canonical ``source_ref`` (ValueError on a malformed one)."""
    if ref is None:
        return None
    if not isinstance(ref, dict):
        raise ValueError("source_ref must be an object or null")
    bad = set(ref) - {"session", "id", "part"}
    if bad:
        raise ValueError(f"source_ref: unknown key(s) {sorted(bad)}")
    ses, oid = ref.get("session"), ref.get("id")
    if not isinstance(ses, str) or not ses:
        raise ValueError("source_ref.session must be a non-empty string")
    if not isinstance(oid, str) or not oid:
        raise ValueError("source_ref.id must be a non-empty string")
    out = {"session": ses, "id": oid}
    part = ref.get("part")
    if part is not None:
        if isinstance(part, bool) or not isinstance(part, (int, float)) \
                or float(part) != int(part) or int(part) < 0:
            raise ValueError("source_ref.part must be an integer >= 0")
        out["part"] = int(part)
    return out


def ref_to_dict(obj) -> dict:
    """``{"source_ref": ...}`` when the object has one, else ``{}``."""
    ref = getattr(obj, "source_ref", None)
    return {} if ref is None else {"source_ref": copy.deepcopy(ref)}


# --------------------------------------------------------------------------- #
# mapping profiles
# --------------------------------------------------------------------------- #
def _restraint6(v, what: str):
    """``"111000"`` / [6 bools|ints] -> [6 ints] (ValueError)."""
    if isinstance(v, str):
        s = v.strip()
        if len(s) != 6 or any(c not in "01" for c in s):
            raise ValueError(f"{what} must be a 6-character 0/1 string")
        return [int(c) for c in s]
    if isinstance(v, (list, tuple)) and len(v) == 6:
        return [int(bool(x)) for x in v]
    raise ValueError(f"{what} must be a 0/1 string or a list of 6 booleans")


def normalize_rule(rule, where: str = "rule") -> dict:
    if not isinstance(rule, dict):
        raise ValueError(f"{where} must be an object")
    bad = set(rule) - set(RULE_KEYS)
    if bad:
        raise ValueError(f"{where}: unknown key(s) {sorted(bad)}")
    layer = rule.get("layer")
    if not isinstance(layer, str) or not layer.strip():
        raise ValueError(f"{where}.layer must be a non-empty glob string")
    out: Dict[str, Any] = {"layer": layer}
    role = rule.get("role")
    if role not in (None, ""):
        if role not in ROLES:
            raise ValueError(f"{where}.role must be one of {ROLES}")
        out["role"] = role
    for k in ("section", "material", "shell_section", "story_rule"):
        v = rule.get(k)
        if v not in (None, ""):
            if not isinstance(v, str):
                raise ValueError(f"{where}.{k} must be a string")
            out[k] = v
    for k in ("angle", "thickness", "mesh_size"):
        v = rule.get(k)
        if v is None or v == "":
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(v):
            raise ValueError(f"{where}.{k} must be a finite number")
        if k != "angle" and v < 0:
            raise ValueError(f"{where}.{k} must be >= 0")
        out[k] = float(v)
    if rule.get("restraint") not in (None, ""):
        out["restraint"] = "".join(
            str(x) for x in _restraint6(rule["restraint"],
                                        f"{where}.restraint"))
    return out


def normalize_profile(prof, where: str = "mapping") -> dict:
    if not isinstance(prof, dict):
        raise ValueError(f"{where} must be an object {{rules: [...]}}")
    bad = set(prof) - {"rules", "story_tolerance"}
    if bad:
        raise ValueError(f"{where}: unknown key(s) {sorted(bad)}")
    rules = prof.get("rules") or []
    if not isinstance(rules, list):
        raise ValueError(f"{where}.rules must be a list")
    out: Dict[str, Any] = {"rules": [normalize_rule(r, f"{where}.rules[{i}]")
                                     for i, r in enumerate(rules)]}
    st = prof.get("story_tolerance")
    if st is not None:
        if isinstance(st, bool) or not isinstance(st, (int, float)) \
                or not math.isfinite(st) or st <= 0:
            raise ValueError(f"{where}.story_tolerance must be > 0 (m)")
        out["story_tolerance"] = float(st)
    return out


# --------------------------------------------------------------------------- #
# model hooks (one line each in model.py)
# --------------------------------------------------------------------------- #
def model_to_dict(model) -> dict:
    out = {}
    if getattr(model, "exchange_mappings", None):
        out["exchange_mappings"] = copy.deepcopy(model.exchange_mappings)
    if getattr(model, "exchange_sessions", None):
        out["exchange_sessions"] = copy.deepcopy(model.exchange_sessions)
    return out


def model_from_dict(mdl, d: dict) -> None:
    """Read ``source_ref`` of every object + the model-level exchange keys
    (absent = unset / empty)."""
    for objs, key in ((mdl.members, "members"), (mdl.shells, "shells"),
                      (mdl.supports, "supports"), (mdl.links, "links")):
        for o, od in zip(objs, d.get(key) or []):
            if isinstance(od, dict) and od.get("source_ref") is not None:
                o.source_ref = normalize_ref(od["source_ref"])
    em = d.get("exchange_mappings")
    if em:
        if not isinstance(em, dict):
            raise ValueError("exchange_mappings must be an object")
        mdl.exchange_mappings = {str(k): normalize_profile(
            v, f"exchange_mappings[{k!r}]") for k, v in em.items()}
    es = d.get("exchange_sessions")
    if es:
        if not isinstance(es, dict):
            raise ValueError("exchange_sessions must be an object")
        mdl.exchange_sessions = copy.deepcopy(es)


def validate(model) -> None:
    for objs in (model.members, model.shells, model.supports, model.links):
        for o in objs:
            if getattr(o, "source_ref", None) is not None:
                o.source_ref = normalize_ref(o.source_ref)
    for k, v in (getattr(model, "exchange_mappings", None) or {}).items():
        model.exchange_mappings[k] = normalize_profile(
            v, f"exchange_mappings[{k!r}]")
    es = getattr(model, "exchange_sessions", None) or {}
    if not isinstance(es, dict) or not all(isinstance(v, dict)
                                           for v in es.values()):
        raise ValueError("exchange_sessions must map session -> object")
