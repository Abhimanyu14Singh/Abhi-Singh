"""ETABS Edit utilities — pure model-editing operations (no analysis change).

Every operation is a PURE function of the model's JSON form
(:meth:`BuildingModel.to_dict`): it deep-copies the dict, edits the copy
and returns ``(new_model_dict, summary)``.  :func:`apply_edit_model` wraps
it for a :class:`BuildingModel` (``to_dict`` -> edit -> ``from_dict``, so
the result is fully validated).  The SPA mirrors these algorithms in
``js/edit_geom.js`` for ``?mock=1``.

Selection::

    {"members": [uid, ...], "shells": [uid, ...], "links": [uid, ...],
     "points": [[x, y, z], ...]}

Operations (``op`` -> ``params``) — see CONTRACT "Edit and Select
utilities" for the full JSON:

* ``replicate``  — ``mode`` "linear" ``{dx, dy, dz, n}`` | "radial"
  ``{center:[x,y,z], axis:"x"|"y"|"z", angle (deg), n}`` | "mirror"
  ``{plane: "x"|"y"|"z", coord}`` or ``{point, normal}`` or a plan line
  ``{p1:[x,y], p2:[x,y]}`` (vertical mirror plane) | "story"
  ``{stories:[names]}``; options ``assignments`` (default True) and
  ``loads`` (default True).
* ``divide``     — ``mode`` "n" ``{n}`` | "intersections" ``{tol?}`` |
  "distance" ``{distance, from: "i"|"j"}`` on the selected frames.
* ``merge_joints`` — ``{tolerance}``; scope = joints of the selection (all
  joints when the selection is empty).
* ``align``      — ``mode`` "coordinate" ``{axis, value}`` | "line"
  ``{p1, p2}`` | "plane" ``{point, normal}`` (selected points) |
  "trim_extend" ``{p1, p2, tol?}`` (selected frames).
* ``move``       — ``{dx, dy, dz}``.
* ``extrude``    — ``mode`` "points_to_frames" ``{dx, dy, dz, n, section?,
  kind?}`` | "frames_to_shells" ``{dx, dy, dz, n, section?, kind?,
  behavior?, delete_source?}``.
* ``join``       — collinear selected frames sharing a free joint, same
  section and assignments.
* ``delete``     — selected objects + every dependent reference.

Reference consistency.  Joints are identified by coordinates (rounded to
1e-6 m, the engine's node-merge tolerance).  Point-keyed records (supports,
point springs, nodal masses, nodal loads, ground displacements, joint
temperatures, joint diaphragms, joint panel zones, group points) follow
moved / merged joints; uid-keyed records (member / area / thermal loads,
groups, pushover ``My``, tendon hosts) follow split / joined / copied /
deleted objects.
"""

from __future__ import annotations

import copy
import math
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float, float]

EDIT_OPS = ("replicate", "divide", "merge_joints", "align", "move",
            "extrude", "join", "delete")
KEY_DIGITS = 6                 # joint identity: coordinates rounded to 1e-6 m
GEOM_TOL = 1e-6
MAX_COPIES = 500
DEFAULT_MERGE_TOL = 0.005      # m

_GLOBAL_DIRS = {"global_x": 0, "global_y": 1, "global_z": 2}


# --------------------------------------------------------------------------- #
# small vector helpers
# --------------------------------------------------------------------------- #
def _f3(p) -> List[float]:
    if (not isinstance(p, (list, tuple)) or len(p) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       and math.isfinite(v) for v in p)):
        raise ValueError(f"expected a point [x, y, z], got {p!r}")
    return [float(v) for v in p]


def _key(p) -> Tuple[float, float, float]:
    return tuple(round(float(v), KEY_DIGITS) + 0.0 for v in p)


def _clean(p) -> List[float]:
    """Round away float noise (1e-9 m) so replicated grids stay exact."""
    return [round(float(v), 9) + 0.0 for v in p]


def _add(a, b):
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _mul(a, s):
    return [a[0] * s, a[1] * s, a[2] * s]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _norm(a):
    return math.sqrt(_dot(a, a))


def _lerp(a, b, t):
    return [a[k] + (b[k] - a[k]) * t for k in range(3)]


def _matvec(R, v):
    return [R[0][0] * v[0] + R[0][1] * v[1] + R[0][2] * v[2],
            R[1][0] * v[0] + R[1][1] * v[1] + R[1][2] * v[2],
            R[2][0] * v[0] + R[2][1] * v[1] + R[2][2] * v[2]]


def _num(params: dict, key: str, default=None, *, positive=False,
         integer=False, lo=None, hi=None):
    v = params.get(key, default)
    if v is None:
        raise ValueError(f"'{key}' is required")
    if isinstance(v, bool) or not isinstance(v, (int, float)) \
            or not math.isfinite(v):
        raise ValueError(f"'{key}' must be a finite number")
    if integer:
        if float(v) != int(v):
            raise ValueError(f"'{key}' must be an integer")
        v = int(v)
    else:
        v = float(v)
    if positive and v <= 0:
        raise ValueError(f"'{key}' must be > 0")
    if lo is not None and v < lo:
        raise ValueError(f"'{key}' must be >= {lo}")
    if hi is not None and v > hi:
        raise ValueError(f"'{key}' must be <= {hi}")
    return v


# --------------------------------------------------------------------------- #
# affine transforms  p -> R p + t  (det(R) = +-1)
# --------------------------------------------------------------------------- #
class Xform:
    def __init__(self, R=None, t=(0.0, 0.0, 0.0), rot_z_deg: float = 0.0,
                 mirror: bool = False):
        self.R = R or [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        self.t = list(t)
        self.rot_z_deg = rot_z_deg          # rotation about +Z (frame angles)
        self.mirror = mirror
        self.det = -1.0 if mirror else 1.0
        self.identity_R = (not mirror and all(
            abs(self.R[i][j] - (1.0 if i == j else 0.0)) < 1e-15
            for i in range(3) for j in range(3)))

    def p(self, p):
        return _clean(_add(_matvec(self.R, p), self.t))

    def v(self, v):                          # polar vector (force)
        return _matvec(self.R, v)

    def pv(self, v):                         # pseudo-vector (moment)
        return _mul(_matvec(self.R, v), self.det)


def translation(dx, dy, dz) -> Xform:
    return Xform(t=(dx, dy, dz))


def rotation(center, axis: str, angle_deg: float) -> Xform:
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    if axis == "z":
        R = [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]
    elif axis == "x":
        R = [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]]
    elif axis == "y":
        R = [[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]]
    else:
        raise ValueError("'axis' must be x|y|z")
    # exact quarter turns
    R = [[round(v) if abs(v - round(v)) < 1e-12 else v for v in row]
         for row in R]
    t = _sub(center, _matvec(R, center))
    return Xform(R, t, rot_z_deg=angle_deg if axis == "z" else 0.0)


def reflection(point, normal) -> Xform:
    n = list(normal)
    ln = _norm(n)
    if ln < 1e-12:
        raise ValueError("mirror plane normal must be non-zero")
    n = _mul(n, 1.0 / ln)
    R = [[(1.0 if i == j else 0.0) - 2.0 * n[i] * n[j] for j in range(3)]
         for i in range(3)]
    R = [[round(v) if abs(v - round(v)) < 1e-12 else v for v in row]
         for row in R]
    t = _mul(n, 2.0 * _dot(point, n))
    return Xform(R, t, mirror=True)


# --------------------------------------------------------------------------- #
# edit context
# --------------------------------------------------------------------------- #
class _Ctx:
    def __init__(self, d: dict):
        self.d = d
        d.setdefault("members", [])
        d.setdefault("shells", [])
        d.setdefault("links", [])
        d.setdefault("patterns", {})
        self.warnings: List[str] = []
        self.created = {"members": [], "shells": [], "links": [], "points": []}
        self.deleted = {"members": [], "shells": [], "links": [], "points": []}
        self.modified = {"members": [], "shells": [], "links": []}
        self._used = set()
        for k in ("members", "shells", "links"):
            for o in d[k]:
                self._used.add(o["uid"])
        self._next: Dict[str, int] = {}

    # ---------------------------------------------------------------- uids
    def new_uid(self, like: str, fallback: str = "E") -> str:
        m = re.match(r"^[A-Za-z]+", str(like))
        prefix = m.group(0) if m else fallback
        if prefix not in self._next:
            mx = 0
            rx = re.compile(r"^" + re.escape(prefix) + r"(\d+)$")
            for u in self._used:
                g = rx.match(u)
                if g:
                    mx = max(mx, int(g.group(1)))
            self._next[prefix] = mx + 1
        while True:
            uid = f"{prefix}{self._next[prefix]}"
            self._next[prefix] += 1
            if uid not in self._used:
                self._used.add(uid)
                return uid

    # ---------------------------------------------------------------- lookup
    def member(self, uid):
        return next((m for m in self.d["members"] if m["uid"] == uid), None)

    def shell(self, uid):
        return next((s for s in self.d["shells"] if s["uid"] == uid), None)

    def link(self, uid):
        return next((lk for lk in self.d["links"] if lk["uid"] == uid), None)

    def mark_modified(self, kind, uid):
        if uid not in self.modified[kind] and uid not in self.created[kind]:
            self.modified[kind].append(uid)

    # ---------------------------------------------------------------- stories
    def story_for(self, zs: Sequence[float]) -> Optional[str]:
        """Story owning an object whose top is max(zs): the story whose
        level equals it, else the story whose height range contains it."""
        stories = self.d.get("stories") or []
        if not stories:
            return None
        z = max(zs)
        for s in stories:
            if abs(float(s["elevation"]) - z) < 1e-6:
                return s["name"]
        for s in stories:
            zt = float(s["elevation"])
            zb = zt - float(s["height"])
            if zb - 1e-9 < z < zt:
                return s["name"]
        return None

    def restory(self, obj, pts, fallback=None):
        st = self.story_for([p[2] for p in pts])
        if st is not None:
            obj["story"] = st
        elif fallback is not None:
            obj["story"] = fallback

    # ---------------------------------------------------------------- joints
    def object_joints(self) -> Dict[tuple, int]:
        """joint key -> number of object ends/corners there."""
        cnt: Dict[tuple, int] = {}
        for m in self.d["members"]:
            for p in (m["pi"], m["pj"]):
                cnt[_key(p)] = cnt.get(_key(p), 0) + 1
        for s in self.d["shells"]:
            for c in s["corners"]:
                cnt[_key(c)] = cnt.get(_key(c), 0) + 1
        for lk in self.d["links"]:
            for p in (lk["pi"], lk["pj"]):
                cnt[_key(p)] = cnt.get(_key(p), 0) + 1
        return cnt

    def summary(self, op: str, extra: Optional[dict] = None) -> dict:
        out = {"op": op,
               "created": self.created, "deleted": self.deleted,
               "modified": self.modified, "warnings": self.warnings}
        out.update(extra or {})
        return out


# --------------------------------------------------------------------------- #
# point-keyed records
# --------------------------------------------------------------------------- #
def _point_lists(d: dict):
    """Yield (list, kind) of every model list whose entries carry ``point``."""
    for k in ("supports", "spring_supports", "nodal_masses",
              "joint_diaphragms", "joint_panel_zones"):
        lst = d.get(k)
        if isinstance(lst, list):
            yield lst, k
    for pat in (d.get("patterns") or {}).values():
        for k in ("nodal_loads", "ground_displacements",
                  "joint_temperatures"):
            lst = pat.get(k)
            if isinstance(lst, list):
                yield lst, k


def _remap_points(ctx: _Ctx, fn: Callable[[list], Optional[list]],
                  include_tendons: bool = False) -> list:
    """Apply a joint mapping (``fn(point) -> new point | None``) to every
    object end / corner and point-keyed record.  Returns the ordered list
    of ``(kind, uid)`` objects whose geometry changed."""
    d = ctx.d
    touched = []

    def mp(p):
        q = fn(p)
        return None if q is None else _clean(q)

    for m in d["members"]:
        ch = False
        for e in ("pi", "pj"):
            q = mp(m[e])
            if q is not None and _key(q) != _key(m[e]):
                m[e] = q
                ch = True
        if ch:
            touched.append(("members", m["uid"]))
    for s in d["shells"]:
        new = []
        ch = False
        deltas = set()
        for c in s["corners"]:
            q = mp(c)
            if q is not None and _key(q) != _key(c):
                deltas.add(_key(_sub(q, c)))
                new.append(q)
                ch = True
            else:
                deltas.add((0.0, 0.0, 0.0))
                new.append(list(c))
        if ch:
            s["corners"] = new
            touched.append(("shells", s["uid"]))
            if len(deltas) == 1:             # rigid translation: openings too
                dv = list(next(iter(deltas)))
                for op in s.get("openings") or []:
                    if op.get("polygon"):
                        op["polygon"] = [_clean(_add(p, dv))
                                         for p in op["polygon"]]
            elif any(op.get("polygon") for op in s.get("openings") or []):
                ctx.warnings.append(f"shell {s['uid']}: polygon openings "
                                    "kept in place (corners moved "
                                    "non-uniformly)")
    for lk in d["links"]:
        ch = False
        for e in ("pi", "pj"):
            q = mp(lk[e])
            if q is not None and _key(q) != _key(lk[e]):
                lk[e] = q
                ch = True
        if ch:
            touched.append(("links", lk["uid"]))
    for ls in d.get("line_springs") or []:
        for e in ("p1", "p2"):
            q = mp(ls[e])
            if q is not None:
                ls[e] = q
    for lst, _k in _point_lists(d):
        for rec in lst:
            if isinstance(rec, dict) and "point" in rec:
                q = mp(rec["point"])
                if q is not None:
                    rec["point"] = q
    for g in (d.get("groups") or {}).values():
        pts = g.get("points") or []
        g["points"] = [mp(p) or list(p) for p in pts]
    # analysis-case settings that name joints by coordinates
    for tc in (d.get("th_cases") or {}).values():
        req = tc.get("output_requests") if isinstance(tc, dict) else None
        if isinstance(req, dict) and isinstance(req.get("joints"), list):
            req["joints"] = [mp(p) or list(p) for p in req["joints"]]
    for key in ("steady_state_cases", "psd_cases"):
        for fc in (d.get(key) or {}).values():
            if isinstance(fc, dict) and isinstance(fc.get("output_points"), list):
                fc["output_points"] = [mp(p) or list(p)
                                       for p in fc["output_points"]]
    for key in ("pushover_cases", "nonlinear_static_cases"):
        for pc in (d.get(key) or {}).values():
            if isinstance(pc, dict) and pc.get("control_point"):
                pc["control_point"] = mp(pc["control_point"]) or list(
                    pc["control_point"])
    if include_tendons:
        for td in d.get("tendons") or []:
            td["points"] = [mp(p) or list(p) for p in td.get("points") or []]
    return touched


def _dedupe_point_records(d: dict) -> None:
    """After merging: one support / spring / joint diaphragm / panel zone
    per joint (first wins), unique group points."""
    for k in ("supports", "spring_supports", "joint_diaphragms",
              "joint_panel_zones"):
        lst = d.get(k)
        if not isinstance(lst, list):
            continue
        seen, out = set(), []
        for rec in lst:
            kk = _key(rec["point"])
            if kk in seen:
                continue
            seen.add(kk)
            out.append(rec)
        d[k] = out
    for g in (d.get("groups") or {}).values():
        seen, out = set(), []
        for p in g.get("points") or []:
            if _key(p) not in seen:
                seen.add(_key(p))
                out.append(p)
        g["points"] = out


# --------------------------------------------------------------------------- #
# uid-keyed references
# --------------------------------------------------------------------------- #
def _replace_uid_refs(d: dict, kind: str, old: str, new: Sequence[str]):
    """Replace object ``old`` by the list ``new`` in groups and tendon hosts
    (members also: pushover My).  ``new`` empty = remove."""
    for g in (d.get("groups") or {}).values():
        lst = g.get(kind) or []
        if old in lst:
            out = []
            for u in lst:
                if u == old:
                    out.extend(x for x in new if x not in out)
                elif u not in out:
                    out.append(u)
            g[kind] = out
    if kind in ("members", "shells"):
        for td in d.get("tendons") or []:
            hosts = td.get("host")
            if isinstance(hosts, str):
                hosts = [hosts]
            if hosts and old in hosts:
                out = []
                for u in hosts:
                    if u == old:
                        out.extend(x for x in new if x not in out)
                    elif u not in out:
                        out.append(u)
                td["host"] = out
    if kind == "members":
        for po in (d.get("pushover_cases") or {}).values():
            my = po.get("My")
            if isinstance(my, dict) and old in my:
                v = my.pop(old)
                for u in new:
                    my.setdefault(u, v)


def _copy_uid_refs(d: dict, kind: str, old: str, new: str):
    """Add copy ``new`` wherever ``old`` is a group member (+ pushover My)."""
    for g in (d.get("groups") or {}).values():
        lst = g.get(kind) or []
        if old in lst and new not in lst:
            lst.append(new)
            g[kind] = lst
    if kind == "members":
        for po in (d.get("pushover_cases") or {}).values():
            my = po.get("My")
            if isinstance(my, dict) and old in my:
                my.setdefault(new, my[old])


def _delete_objects(ctx: _Ctx, members=(), shells=(), links=(),
                    cleanup_points=True) -> None:
    d = ctx.d
    mset, sset, lset = set(members), set(shells), set(links)
    if not (mset or sset or lset):
        return
    gone_joints = set()
    for m in d["members"]:
        if m["uid"] in mset:
            gone_joints.update((_key(m["pi"]), _key(m["pj"])))
    for s in d["shells"]:
        if s["uid"] in sset:
            gone_joints.update(_key(c) for c in s["corners"])
    for lk in d["links"]:
        if lk["uid"] in lset:
            gone_joints.update((_key(lk["pi"]), _key(lk["pj"])))
    d["members"] = [m for m in d["members"] if m["uid"] not in mset]
    d["shells"] = [s for s in d["shells"] if s["uid"] not in sset]
    d["links"] = [lk for lk in d["links"] if lk["uid"] not in lset]
    for pat in d["patterns"].values():
        for k in ("member_loads", "member_udls", "thermal_loads"):
            if pat.get(k):
                pat[k] = [x for x in pat[k] if x["member_uid"] not in mset]
        for k in ("area_loads", "shell_thermal_loads"):
            if pat.get(k):
                pat[k] = [x for x in pat[k] if x["region_uid"] not in sset]
    for uid in mset:
        _replace_uid_refs(d, "members", uid, [])
    for uid in sset:
        _replace_uid_refs(d, "shells", uid, [])
    for uid in lset:
        _replace_uid_refs(d, "links", uid, [])
    if d.get("tendons"):
        keep = []
        for td in d["tendons"]:
            if td.get("host"):
                keep.append(td)
            else:
                ctx.warnings.append(f"tendon {td.get('uid')} deleted (no "
                                    "host left)")
        d["tendons"] = keep
    for kind, s in (("members", mset), ("shells", sset), ("links", lset)):
        for u in sorted(s):
            if u in ctx.created[kind]:
                ctx.created[kind].remove(u)
            else:
                ctx.deleted[kind].append(u)
            if u in ctx.modified[kind]:
                ctx.modified[kind].remove(u)
    if cleanup_points and gone_joints:
        live = set(ctx.object_joints())
        orphan = gone_joints - live
        if orphan:
            _delete_point_records(ctx, orphan)


def _delete_point_records(ctx: _Ctx, keys: set) -> int:
    n = 0
    d = ctx.d
    for lst, _k in list(_point_lists(d)):
        keep = [r for r in lst if _key(r["point"]) not in keys]
        n += len(lst) - len(keep)
        lst[:] = keep
    for g in (d.get("groups") or {}).values():
        g["points"] = [p for p in g.get("points") or []
                       if _key(p) not in keys]
    return n


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #
def normalize_selection(d: dict, sel: Optional[dict]) -> dict:
    sel = sel or {}
    if not isinstance(sel, dict):
        raise ValueError("'selection' must be an object")
    out = {}
    for kind in ("members", "shells", "links"):
        uids = sel.get(kind) or []
        if not isinstance(uids, list):
            raise ValueError(f"selection.{kind} must be a list of uids")
        have = {o["uid"] for o in d.get(kind) or []}
        seen = []
        for u in uids:
            if u not in have:
                raise ValueError(f"selection.{kind}: unknown uid {u!r}")
            if u not in seen:
                seen.append(u)
        out[kind] = seen
    pts = sel.get("points") or []
    if not isinstance(pts, list):
        raise ValueError("selection.points must be a list of [x, y, z]")
    out["points"] = []
    seenp = set()
    for p in pts:
        q = _f3(p)
        if _key(q) not in seenp:
            seenp.add(_key(q))
            out["points"].append(q)
    return out


def _sel_joints(ctx: _Ctx, sel: dict) -> List[list]:
    """Selected points + the joints of every selected object (ordered)."""
    out, seen = [], set()

    def add(p):
        if _key(p) not in seen:
            seen.add(_key(p))
            out.append(list(p))
    for p in sel["points"]:
        add(p)
    for u in sel["members"]:
        m = ctx.member(u)
        add(m["pi"]), add(m["pj"])
    for u in sel["shells"]:
        for c in ctx.shell(u)["corners"]:
            add(c)
    for u in sel["links"]:
        lk = ctx.link(u)
        add(lk["pi"]), add(lk["pj"])
    return out


def _empty(sel):
    return not (sel["members"] or sel["shells"] or sel["links"]
                or sel["points"])


# --------------------------------------------------------------------------- #
# transformed copies of loads / assignments
# --------------------------------------------------------------------------- #
def _xf_dir_load(ld: dict, xf: Xform, keys=("w", "w2"), dkey="direction",
                 moment=False) -> List[dict]:
    """A load with a ``direction`` transformed by xf: global directions are
    rotated / mirrored (split into per-axis entries); gravity and local
    directions are invariant."""
    dirn = ld.get(dkey, "gravity")
    if xf.identity_R or dirn not in _GLOBAL_DIRS:
        return [copy.deepcopy(ld)]
    e = [0.0, 0.0, 0.0]
    e[_GLOBAL_DIRS[dirn]] = 1.0
    v = xf.pv(e) if moment else xf.v(e)
    out = []
    for ax, name in enumerate(("global_x", "global_y", "global_z")):
        if abs(v[ax]) < 1e-12:
            continue
        nl = copy.deepcopy(ld)
        nl[dkey] = name
        for k in keys:
            if k in nl and isinstance(nl[k], (int, float)):
                nl[k] = nl[k] * v[ax]
        out.append(nl)
    return out


def _xf_nodal(rec: dict, xf: Xform, newp) -> dict:
    nr = copy.deepcopy(rec)
    nr["point"] = newp
    if not xf.identity_R:
        f = xf.v([rec.get("fx", 0.0), rec.get("fy", 0.0), rec.get("fz", 0.0)])
        nr["fx"], nr["fy"], nr["fz"] = f
        if any(k in rec for k in ("mx", "my", "mz")):
            mm = xf.pv([rec.get("mx", 0.0), rec.get("my", 0.0),
                        rec.get("mz", 0.0)])
            for k, v in zip(("mx", "my", "mz"), mm):
                if v or k in rec:
                    nr[k] = v
    return nr


def _xf_member(m: dict, xf: Xform, uid: str, assignments: bool) -> dict:
    if assignments:
        nm = copy.deepcopy(m)
    else:
        nm = {k: copy.deepcopy(m[k]) for k in
              ("kind", "section", "story") if k in m}
    nm["uid"] = uid
    nm["pi"], nm["pj"] = xf.p(m["pi"]), xf.p(m["pj"])
    nm.pop("length", None)
    if assignments:
        if xf.mirror:
            nm["angle"] = -float(m.get("angle", 0.0) or 0.0)
        elif xf.rot_z_deg:
            dx = abs(m["pj"][0] - m["pi"][0]) + abs(m["pj"][1] - m["pi"][1])
            if dx < 1e-9:                    # vertical: local x = +-Z
                sgn = 1.0 if m["pj"][2] > m["pi"][2] else -1.0
                a = float(m.get("angle", 0.0) or 0.0) + sgn * xf.rot_z_deg
                a = (a + 180.0) % 360.0 - 180.0
                nm["angle"] = round(a, 9) + 0.0
        jo = nm.get("joint_offsets")
        if isinstance(jo, dict) and jo.get("system", "global") == "global" \
                and not xf.identity_R:
            for e in ("i", "j"):
                if isinstance(jo.get(e), list) and len(jo[e]) == 3:
                    jo[e] = _clean(xf.v(jo[e]))
    return nm


def _xf_shell(s: dict, xf: Xform, uid: str, assignments: bool) -> dict:
    if assignments:
        ns = copy.deepcopy(s)
    else:
        ns = {k: copy.deepcopy(s[k]) for k in
              ("kind", "behavior", "section", "mesh_size", "story",
               "openings") if k in s}
    ns["uid"] = uid
    corners = [xf.p(c) for c in s["corners"]]
    ops = ns.get("openings") or []
    for op in ops:
        if op.get("polygon"):
            op["polygon"] = [xf.p(p) for p in op["polygon"]]
    if xf.mirror:
        # keep the corner ordering counter-clockwise: reverse (c0 first)
        corners = [corners[0]] + corners[1:][::-1]
        if len(corners) == 4:                # parametric u <-> v swap
            for op in ops:
                if not op.get("polygon"):
                    op["u0"], op["v0"] = op["v0"], op["u0"]
                    op["u1"], op["v1"] = op["v1"], op["u1"]
        for op in ops:
            if op.get("polygon"):
                op["polygon"] = op["polygon"][::-1]
    ns["corners"] = corners
    return ns


def _xf_link(lk: dict, xf: Xform, uid: str) -> dict:
    nl = copy.deepcopy(lk)
    nl["uid"] = uid
    nl["pi"], nl["pj"] = xf.p(lk["pi"]), xf.p(lk["pj"])
    return nl


def _geom_key_member(m):
    a, b = _key(m["pi"]), _key(m["pj"])
    return ("m",) + tuple(sorted((a, b)))


def _geom_key_shell(s):
    return ("s",) + tuple(sorted(_key(c) for c in s["corners"]))


def _geom_key_link(lk):
    a, b = _key(lk["pi"]), _key(lk["pj"])
    return ("l",) + tuple(sorted((a, b)))


def _copy_selection(ctx: _Ctx, sel: dict, xf: Xform, *, assignments=True,
                    loads=True, story: Optional[str] = None,
                    new_sel: Optional[dict] = None) -> int:
    """One transformed copy of the selection.  Duplicates of existing
    objects (same joints) are skipped.  Returns the number of objects
    created."""
    d = ctx.d
    existing = {_geom_key_member(m) for m in d["members"]}
    existing |= {_geom_key_shell(s) for s in d["shells"]}
    existing |= {_geom_key_link(lk) for lk in d["links"]}
    mmap, smap, lmap = {}, {}, {}
    made = 0
    skipped = 0
    for u in sel["members"]:
        m = ctx.member(u)
        nm = _xf_member(m, xf, "", assignments)
        if _geom_key_member(nm) in existing:
            skipped += 1
            continue
        nm["uid"] = ctx.new_uid(u, m.get("kind", "F")[:1].upper())
        if story is not None:
            nm["story"] = story
        else:
            ctx.restory(nm, (nm["pi"], nm["pj"]), "")
        existing.add(_geom_key_member(nm))
        d["members"].append(nm)
        ctx.created["members"].append(nm["uid"])
        mmap[u] = nm["uid"]
        made += 1
        if assignments:
            _copy_uid_refs(d, "members", u, nm["uid"])
    for u in sel["shells"]:
        s = ctx.shell(u)
        ns = _xf_shell(s, xf, "", assignments)
        if _geom_key_shell(ns) in existing:
            skipped += 1
            continue
        ns["uid"] = ctx.new_uid(u, "W" if s.get("kind") == "wall" else "S")
        if story is not None:
            ns["story"] = story
        else:
            ctx.restory(ns, ns["corners"], "")
        existing.add(_geom_key_shell(ns))
        d["shells"].append(ns)
        ctx.created["shells"].append(ns["uid"])
        smap[u] = ns["uid"]
        made += 1
        if assignments:
            _copy_uid_refs(d, "shells", u, ns["uid"])
    for u in sel["links"]:
        lk = ctx.link(u)
        nl = _xf_link(lk, xf, "")
        if _geom_key_link(nl) in existing:
            skipped += 1
            continue
        nl["uid"] = ctx.new_uid(u, "L")
        existing.add(_geom_key_link(nl))
        d["links"].append(nl)
        ctx.created["links"].append(nl["uid"])
        lmap[u] = nl["uid"]
        made += 1
        if assignments:
            _copy_uid_refs(d, "links", u, nl["uid"])
    if skipped:
        ctx.warnings.append(f"{skipped} object(s) skipped: an identical "
                            "object already exists at the target")
    # ---- loads on copied objects
    if loads:
        for pat in d["patterns"].values():
            for k in ("member_loads",):
                add = []
                for ld in pat.get(k) or []:
                    if ld["member_uid"] in mmap:
                        for nl in _xf_dir_load(ld, xf,
                                               moment=ld.get("kind") ==
                                               "moment"):
                            nl["member_uid"] = mmap[ld["member_uid"]]
                            add.append(nl)
                if add:
                    pat[k] = (pat.get(k) or []) + add
            for k in ("member_udls", "thermal_loads"):
                add = []
                for ld in pat.get(k) or []:
                    if ld["member_uid"] in mmap:
                        nl = copy.deepcopy(ld)
                        nl["member_uid"] = mmap[ld["member_uid"]]
                        add.append(nl)
                if add:
                    pat[k] = (pat.get(k) or []) + add
            add = []
            for ld in pat.get("area_loads") or []:
                if ld["region_uid"] in smap:
                    for nl in _xf_dir_load(ld, xf, keys=("q",)):
                        nl["region_uid"] = smap[ld["region_uid"]]
                        add.append(nl)
            if add:
                pat["area_loads"] = (pat.get("area_loads") or []) + add
            add = []
            for ld in pat.get("shell_thermal_loads") or []:
                if ld["region_uid"] in smap:
                    nl = copy.deepcopy(ld)
                    nl["region_uid"] = smap[ld["region_uid"]]
                    add.append(nl)
            if add:
                pat["shell_thermal_loads"] = \
                    (pat.get("shell_thermal_loads") or []) + add
        # tendons fully hosted by the copied objects
        hmap = dict(mmap)
        hmap.update(smap)
        new_t = []
        tuids = {t.get("uid") for t in d.get("tendons") or []}
        for td in d.get("tendons") or []:
            hosts = td.get("host") or []
            if isinstance(hosts, str):
                hosts = [hosts]
            if hosts and all(h in hmap for h in hosts):
                nt = copy.deepcopy(td)
                nt["host"] = [hmap[h] for h in hosts]
                nt["points"] = [xf.p(p) for p in td.get("points") or []]
                if td.get("drape_dir") is not None:
                    nt["drape_dir"] = _clean(xf.v(td["drape_dir"]))
                base = str(td.get("uid") or "T")
                k = 1
                while f"{base}_{k}" in tuids:
                    k += 1
                nt["uid"] = f"{base}_{k}"
                tuids.add(nt["uid"])
                new_t.append(nt)
        if new_t:
            d["tendons"] = (d.get("tendons") or []) + new_t
    # ---- selected points: point assignments + point loads
    if sel["points"]:
        keys = {_key(p): p for p in sel["points"]}
        newp = {k: xf.p(p) for k, p in keys.items()}
        for lst, kind in list(_point_lists(d)):
            is_load = kind in ("nodal_loads", "ground_displacements",
                               "joint_temperatures")
            if (is_load and not loads) or (not is_load and not assignments):
                continue
            have = {_key(r["point"]) for r in lst}
            add = []
            for r in lst:
                kk = _key(r["point"])
                if kk not in keys:
                    continue
                np_ = newp[kk]
                if not is_load and _key(np_) in have:
                    continue
                if kind == "nodal_loads":
                    nr = _xf_nodal(r, xf, np_)
                else:
                    nr = copy.deepcopy(r)
                    nr["point"] = np_
                    if kind == "spring_supports" and xf.rot_z_deg:
                        nr["angle_deg"] = float(r.get("angle_deg", 0.0)) \
                            + xf.rot_z_deg
                add.append(nr)
            lst.extend(add)
        if assignments:
            for g in (d.get("groups") or {}).values():
                gp = g.get("points") or []
                gk = {_key(p) for p in gp}
                for kk in list(gk):
                    if kk in keys and _key(newp[kk]) not in gk:
                        gp.append(newp[kk])
                g["points"] = gp
        for kk in keys:
            ctx.created["points"].append(newp[kk])
            if new_sel is not None:
                new_sel["points"].append(newp[kk])
    if new_sel is not None:
        new_sel["members"].extend(mmap.values())
        new_sel["shells"].extend(smap.values())
        new_sel["links"].extend(lmap.values())
    return made


# --------------------------------------------------------------------------- #
# operations
# --------------------------------------------------------------------------- #
def _new_sel():
    return {"members": [], "shells": [], "links": [], "points": []}


def op_replicate(ctx: _Ctx, sel: dict, params: dict) -> dict:
    if _empty(sel):
        raise ValueError("replicate: nothing selected")
    mode = params.get("mode", "linear")
    assignments = bool(params.get("assignments", True))
    loads = bool(params.get("loads", True))
    ns = _new_sel()
    made = 0
    if mode == "linear":
        dx = _num(params, "dx", 0.0)
        dy = _num(params, "dy", 0.0)
        dz = _num(params, "dz", 0.0)
        n = _num(params, "n", 1, integer=True, lo=1, hi=MAX_COPIES)
        if abs(dx) + abs(dy) + abs(dz) < GEOM_TOL:
            raise ValueError("replicate: the offset (dx, dy, dz) is zero")
        for k in range(1, n + 1):
            made += _copy_selection(ctx, sel, translation(dx * k, dy * k,
                                                          dz * k),
                                    assignments=assignments, loads=loads,
                                    new_sel=ns)
    elif mode == "radial":
        center = _f3(params.get("center", [0.0, 0.0, 0.0]))
        axis = params.get("axis", "z")
        ang = _num(params, "angle")
        n = _num(params, "n", 1, integer=True, lo=1, hi=MAX_COPIES)
        if abs(ang) < 1e-12:
            raise ValueError("replicate: 'angle' must be non-zero")
        for k in range(1, n + 1):
            made += _copy_selection(ctx, sel, rotation(center, axis, ang * k),
                                    assignments=assignments, loads=loads,
                                    new_sel=ns)
    elif mode == "mirror":
        made += _copy_selection(ctx, sel, _mirror_xform(params),
                                assignments=assignments, loads=loads,
                                new_sel=ns)
    elif mode == "story":
        targets = params.get("stories") or []
        if not isinstance(targets, list) or not targets:
            raise ValueError("replicate: 'stories' must be a non-empty list "
                             "of story names")
        elev = {s["name"]: float(s["elevation"])
                for s in ctx.d.get("stories") or []}
        for t in targets:
            if t not in elev:
                raise ValueError(f"replicate: unknown story {t!r}")
        # group the selection by source story
        groups: Dict[str, dict] = {}

        def src_story(obj, pts):
            st = obj.get("story") or ctx.story_for([p[2] for p in pts])
            if st not in elev:
                raise ValueError(f"replicate: object {obj['uid']} has no "
                                 "story")
            return st
        for u in sel["members"]:
            m = ctx.member(u)
            groups.setdefault(src_story(m, (m["pi"], m["pj"])),
                              _new_sel())["members"].append(u)
        for u in sel["shells"]:
            s = ctx.shell(u)
            groups.setdefault(src_story(s, s["corners"]),
                              _new_sel())["shells"].append(u)
        for u in sel["links"]:
            lk = ctx.link(u)
            st = ctx.story_for([lk["pi"][2], lk["pj"][2]])
            if st is None:
                raise ValueError(f"replicate: link {u} has no story")
            groups.setdefault(st, _new_sel())["links"].append(u)
        for p in sel["points"]:
            st = ctx.story_for([p[2]])
            if st is None:
                raise ValueError(f"replicate: point {p} is not on a story")
            groups.setdefault(st, _new_sel())["points"].append(p)
        for src, gsel in groups.items():
            for t in targets:
                if t == src:
                    continue
                made += _copy_selection(
                    ctx, gsel, translation(0.0, 0.0, elev[t] - elev[src]),
                    assignments=assignments, loads=loads, story=t,
                    new_sel=ns)
    else:
        raise ValueError("replicate: 'mode' must be linear|radial|mirror|"
                         "story")
    return {"new_selection": ns, "count": made}


def _mirror_xform(params: dict) -> Xform:
    plane = params.get("plane")
    if plane in ("x", "y", "z"):
        c = _num(params, "coord", 0.0)
        n = [0.0, 0.0, 0.0]
        n["xyz".index(plane)] = 1.0
        p = [0.0, 0.0, 0.0]
        p["xyz".index(plane)] = c
        return reflection(p, n)
    if params.get("p1") is not None and params.get("p2") is not None:
        p1, p2 = params["p1"], params["p2"]
        if len(p1) < 2 or len(p2) < 2:
            raise ValueError("mirror: p1/p2 must be plan points [x, y]")
        a = [float(p1[0]), float(p1[1]), 0.0]
        b = [float(p2[0]), float(p2[1]), 0.0]
        dv = _sub(b, a)
        if _norm(dv) < GEOM_TOL:
            raise ValueError("mirror: p1 and p2 coincide")
        return reflection(a, [dv[1], -dv[0], 0.0])
    if params.get("point") is not None and params.get("normal") is not None:
        return reflection(_f3(params["point"]), _f3(params["normal"]))
    raise ValueError("mirror: give plane x|y|z + coord, a plan line p1/p2, "
                     "or point + normal")


# ----------------------------------------------------------------- divide
def _remap_member_load(ld: dict, s0: float, s1: float) -> Optional[dict]:
    """The part of member load ``ld`` (fractions of the parent) acting on
    the piece [s0, s1], in piece fractions; None when it misses it."""
    r = s1 - s0
    kind = ld.get("kind", "udl")
    a = float(ld.get("a", 0.0))
    b = float(ld.get("b", 1.0))
    if kind in ("point", "moment"):
        last = s1 >= 1.0 - 1e-12
        if s0 - 1e-12 <= a < s1 - 1e-12 or (last and a >= s0 - 1e-12):
            nl = copy.deepcopy(ld)
            nl["a"] = min(1.0, max(0.0, round((a - s0) / r, 12)))
            return nl
        return None
    lo, hi = max(a, s0), min(b, s1)
    if hi - lo <= 1e-12:
        return None
    nl = copy.deepcopy(ld)
    nl["a"] = min(1.0, max(0.0, round((lo - s0) / r, 12)))
    nl["b"] = min(1.0, max(0.0, round((hi - s0) / r, 12)))
    if kind == "trapezoid":
        w, w2 = float(ld.get("w", 0.0)), float(ld.get("w2", 0.0))
        span = b - a
        nl["w"] = w + (w2 - w) * (lo - a) / span if span > 0 else w
        nl["w2"] = w + (w2 - w) * (hi - a) / span if span > 0 else w2
    return nl


def _split_member(ctx: _Ctx, uid: str, ts: List[float]) -> List[str]:
    d = ctx.d
    m = ctx.member(uid)
    ts = sorted(t for t in ts if 1e-9 < t < 1 - 1e-9)
    if not ts:
        return [uid]
    bounds = [0.0] + ts + [1.0]
    idx = d["members"].index(m)
    toks = [t.strip() for t in str(m.get("releases", "")).split(",")
            if t.strip()]
    pieces = []
    uids = []
    for k in range(len(bounds) - 1):
        s0, s1 = bounds[k], bounds[k + 1]
        pm = copy.deepcopy(m)
        pm.pop("length", None)
        pm["uid"] = uid if k == 0 else ctx.new_uid(uid, "F")
        pm["pi"] = _clean(_lerp(m["pi"], m["pj"], s0))
        pm["pj"] = _clean(_lerp(m["pi"], m["pj"], s1))
        first, last = k == 0, k == len(bounds) - 2
        pm["releases"] = ",".join(
            t for t in toks if (t == "Mi" and first) or (t == "Mj" and last))
        if not first:
            pm["rigid_i"] = 0.0
        if not last:
            pm["rigid_j"] = 0.0
        jo = pm.get("joint_offsets")
        if isinstance(jo, dict):
            if not first:
                jo["i"] = [0.0, 0.0, 0.0]
            if not last:
                jo["j"] = [0.0, 0.0, 0.0]
        if isinstance(m.get("hinges"), list):
            hl = []
            for h in m["hinges"]:
                rd = float(h.get("relative_distance", 0.0))
                if s0 - 1e-12 <= rd <= s1 + 1e-12 and not (
                        rd >= s1 - 1e-12 and not last):
                    nh = dict(h)
                    nh["relative_distance"] = round((rd - s0) / (s1 - s0), 12)
                    hl.append(nh)
            pm["hinges"] = hl if hl else "none"
        pieces.append(pm)
        uids.append(pm["uid"])
        if k:
            ctx.created["members"].append(pm["uid"])
    ctx.mark_modified("members", uid)
    d["members"][idx:idx + 1] = pieces
    for pat in d["patterns"].values():
        if pat.get("member_loads"):
            out = []
            for ld in pat["member_loads"]:
                if ld["member_uid"] != uid:
                    out.append(ld)
                    continue
                for k in range(len(bounds) - 1):
                    nl = _remap_member_load(ld, bounds[k], bounds[k + 1])
                    if nl is not None:
                        nl["member_uid"] = uids[k]
                        out.append(nl)
            pat["member_loads"] = out
        for key in ("member_udls", "thermal_loads"):
            if pat.get(key):
                out = []
                for ld in pat[key]:
                    out.append(ld)
                    if ld["member_uid"] == uid:
                        for u in uids[1:]:
                            nl = copy.deepcopy(ld)
                            nl["member_uid"] = u
                            out.append(nl)
                pat[key] = out
    _replace_uid_refs(d, "members", uid, uids)
    return uids


def _point_on_segment_t(p, a, b, tol) -> Optional[float]:
    ab = _sub(b, a)
    L2 = _dot(ab, ab)
    if L2 < 1e-18:
        return None
    t = _dot(_sub(p, a), ab) / L2
    if t <= 0.0 or t >= 1.0:
        return None
    if _norm(_sub(p, _lerp(a, b, t))) > tol:
        return None
    return t


def _segment_cross(a, b, c, e, tol) -> Optional[Tuple[float, float]]:
    """Closest-approach parameters (t on ab, s on ce) when the two segments
    intersect within tol; None otherwise / when parallel."""
    u, v, w0 = _sub(b, a), _sub(e, c), _sub(a, c)
    A, B, C = _dot(u, u), _dot(u, v), _dot(v, v)
    D, E = _dot(u, w0), _dot(v, w0)
    den = A * C - B * B
    if den < 1e-12 * A * C:
        return None
    t = (B * E - C * D) / den
    s = (A * E - B * D) / den
    if not (-1e-9 <= t <= 1 + 1e-9 and -1e-9 <= s <= 1 + 1e-9):
        return None
    if _norm(_sub(_lerp(a, b, t), _lerp(c, e, s))) > tol:
        return None
    return t, s


def op_divide(ctx: _Ctx, sel: dict, params: dict) -> dict:
    if not sel["members"]:
        raise ValueError("divide: select frame members")
    mode = params.get("mode", "n")
    ns = _new_sel()
    report = {}
    if mode == "n":
        n = _num(params, "n", 2, integer=True, lo=2, hi=MAX_COPIES)
        plan = {u: [k / n for k in range(1, n)] for u in sel["members"]}
    elif mode == "distance":
        dist = _num(params, "distance", positive=True)
        frm = params.get("from", "i")
        if frm not in ("i", "j"):
            raise ValueError("divide: 'from' must be i|j")
        plan = {}
        for u in sel["members"]:
            m = ctx.member(u)
            L = math.dist(m["pi"], m["pj"])
            if dist >= L - GEOM_TOL:
                ctx.warnings.append(f"member {u}: distance >= length "
                                    f"({L:.4g} m), not divided")
                continue
            plan[u] = [dist / L if frm == "i" else 1.0 - dist / L]
    elif mode == "intersections":
        tol = _num(params, "tol", 1e-3, positive=True)
        plan = {}
        joints = list(ctx.object_joints())
        for p in sel["points"]:
            joints.append(_key(p))
        for u in sel["members"]:
            m = ctx.member(u)
            a, b = m["pi"], m["pj"]
            ts = []
            for j in joints:
                t = _point_on_segment_t(list(j), a, b, tol)
                if t is not None:
                    ts.append(t)
            for o in ctx.d["members"]:
                if o["uid"] == u:
                    continue
                r = _segment_cross(a, b, o["pi"], o["pj"], tol)
                if r is not None and 1e-9 < r[0] < 1 - 1e-9:
                    ts.append(r[0])
            ts = sorted(ts)
            uniq = []
            L = math.dist(a, b)
            for t in ts:
                if not uniq or (t - uniq[-1]) * L > tol:
                    uniq.append(t)
            if uniq:
                plan[u] = uniq
    else:
        raise ValueError("divide: 'mode' must be n|intersections|distance")
    for u, ts in plan.items():
        uids = _split_member(ctx, u, ts)
        report[u] = uids
        ns["members"].extend(uids)
    return {"new_selection": ns, "divided": report}


# ----------------------------------------------------------------- merge
def op_merge_joints(ctx: _Ctx, sel: dict, params: dict) -> dict:
    tol = _num(params, "tolerance", DEFAULT_MERGE_TOL, positive=True,
               hi=10.0)
    d = ctx.d
    cnt = ctx.object_joints()
    allj = dict(cnt)
    for lst, _k in _point_lists(d):
        for r in lst:
            allj.setdefault(_key(r["point"]), 0)
    if _empty(sel):
        scope = list(allj)
    else:
        scope = [_key(p) for p in _sel_joints(ctx, sel)]
    supported = {_key(r["point"]) for r in (d.get("supports") or [])}
    supported |= {_key(r["point"]) for r in (d.get("spring_supports") or [])}
    # union-find over the scope via a bucket grid
    pts = sorted(set(scope))
    parent = {p: p for p in pts}

    def find(p):
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p
    cell = {}
    for p in pts:
        c = tuple(int(math.floor(v / tol)) for v in p)
        cell.setdefault(c, []).append(p)
    for p in pts:
        c = tuple(int(math.floor(v / tol)) for v in p)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for q in cell.get((c[0] + dx, c[1] + dy, c[2] + dz), ()):
                        if q <= p:
                            continue
                        if math.dist(p, q) <= tol + 1e-12:
                            ra, rb = find(p), find(q)
                            if ra != rb:
                                parent[max(ra, rb)] = min(ra, rb)
    clusters: Dict[tuple, List[tuple]] = {}
    for p in pts:
        clusters.setdefault(find(p), []).append(p)
    mapping: Dict[tuple, list] = {}
    merged = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        rep = sorted(members, key=lambda q: (q not in supported,
                                             -cnt.get(q, 0), q))[0]
        for q in members:
            if q != rep:
                mapping[q] = list(rep)
        merged.append({"to": list(rep),
                       "from": [list(q) for q in sorted(members)
                                if q != rep]})
    if mapping:
        touched = _remap_points(ctx, lambda p: mapping.get(_key(p)))
        for kind, uid in touched:
            ctx.mark_modified(kind, uid)
        _cleanup_degenerate(ctx)
        _dedupe_point_records(d)
    return {"merged": merged, "merged_count": sum(len(c["from"])
                                                  for c in merged),
            "new_selection": _new_sel()}


def _cleanup_degenerate(ctx: _Ctx) -> None:
    d = ctx.d
    zero_m = [m["uid"] for m in d["members"]
              if math.dist(m["pi"], m["pj"]) < GEOM_TOL]
    zero_l = []          # zero-length links are legal (zeroLength springs)
    bad_s = []
    for s in d["shells"]:
        out = []
        for c in s["corners"]:
            if not out or _key(out[-1]) != _key(c):
                out.append(c)
        if len(out) > 1 and _key(out[0]) == _key(out[-1]):
            out.pop()
        if len(out) != len(s["corners"]):
            if len(out) < 3:
                bad_s.append(s["uid"])
            else:
                s["corners"] = out
                ctx.warnings.append(f"shell {s['uid']}: duplicate corners "
                                    "removed")
    # duplicate members (same joints) -> keep the first
    seen = set()
    dup = []
    for m in d["members"]:
        if m["uid"] in zero_m:
            continue
        k = _geom_key_member(m)
        if k in seen:
            dup.append(m["uid"])
        seen.add(k)
    if zero_m:
        ctx.warnings.append(f"zero-length member(s) deleted: {zero_m}")
    if dup:
        ctx.warnings.append(f"duplicate member(s) deleted: {dup}")
    if bad_s:
        ctx.warnings.append(f"degenerate shell(s) deleted: {bad_s}")
    _delete_objects(ctx, members=zero_m + dup, shells=bad_s, links=zero_l)


# ----------------------------------------------------------------- align
def op_align(ctx: _Ctx, sel: dict, params: dict) -> dict:
    mode = params.get("mode", "coordinate")
    if mode == "trim_extend":
        return _trim_extend(ctx, sel, params)
    pts = _sel_joints(ctx, {"members": [], "shells": [], "links": [],
                            "points": sel["points"]}) if sel["points"] \
        else _sel_joints(ctx, sel)
    if not pts:
        raise ValueError("align: select points (or objects)")
    if mode == "coordinate":
        axis = params.get("axis", "z")
        if axis not in ("x", "y", "z"):
            raise ValueError("align: 'axis' must be x|y|z")
        val = _num(params, "value")
        ax = "xyz".index(axis)

        def target(p):
            q = list(p)
            q[ax] = val
            return q
    elif mode == "line":
        a, b = _f3(params.get("p1")), _f3(params.get("p2"))
        ab = _sub(b, a)
        if _norm(ab) < GEOM_TOL:
            raise ValueError("align: p1 and p2 coincide")

        def target(p):
            t = _dot(_sub(p, a), ab) / _dot(ab, ab)
            return _lerp(a, b, t)
    elif mode == "plane":
        o, n = _f3(params.get("point")), _f3(params.get("normal"))
        ln = _norm(n)
        if ln < 1e-12:
            raise ValueError("align: plane normal must be non-zero")
        n = _mul(n, 1.0 / ln)

        def target(p):
            return _sub(p, _mul(n, _dot(_sub(p, o), n)))
    else:
        raise ValueError("align: 'mode' must be coordinate|line|plane|"
                         "trim_extend")
    mapping = {_key(p): _clean(target(p)) for p in pts}
    touched = _remap_points(ctx, lambda p: mapping.get(_key(p)))
    for kind, uid in touched:
        ctx.mark_modified(kind, uid)
        _restory_obj(ctx, kind, uid)
    _cleanup_degenerate(ctx)
    _dedupe_point_records(ctx.d)
    return {"moved_points": len(mapping), "new_selection": sel}


def _restory_obj(ctx, kind, uid):
    if kind == "members":
        m = ctx.member(uid)
        if m:
            ctx.restory(m, (m["pi"], m["pj"]))
    elif kind == "shells":
        s = ctx.shell(uid)
        if s:
            ctx.restory(s, s["corners"])


def _trim_extend(ctx: _Ctx, sel: dict, params: dict) -> dict:
    if not sel["members"]:
        raise ValueError("align: trim/extend needs selected frames")
    a, b = _f3(params.get("p1")), _f3(params.get("p2"))
    tol = _num(params, "tol", 1e-3, positive=True)
    v = _sub(b, a)
    if _norm(v) < GEOM_TOL:
        raise ValueError("align: p1 and p2 coincide")
    changed = []
    for u in sel["members"]:
        m = ctx.member(u)
        pi, pj = m["pi"], m["pj"]
        uu = _sub(pj, pi)
        w0 = _sub(pi, a)
        A, B, C = _dot(uu, uu), _dot(uu, v), _dot(v, v)
        D, E = _dot(uu, w0), _dot(v, w0)
        den = A * C - B * B
        if den < 1e-12 * A * C:
            ctx.warnings.append(f"member {u}: parallel to the line")
            continue
        t = (B * E - C * D) / den
        s = (A * E - B * D) / den
        x = _lerp(pi, pj, t)
        if _norm(_sub(x, _lerp(a, b, s))) > tol:
            ctx.warnings.append(f"member {u}: does not meet the line")
            continue
        end = "pi" if t < 0.5 else "pj"
        x = _clean(x)
        if _key(x) == _key(m[end]):
            continue
        other = m["pj" if end == "pi" else "pi"]
        if math.dist(x, other) < GEOM_TOL:
            ctx.warnings.append(f"member {u}: would become zero length")
            continue
        m[end] = x
        ctx.mark_modified("members", u)
        ctx.restory(m, (m["pi"], m["pj"]))
        changed.append(u)
    return {"trimmed": changed, "new_selection": sel}


# ----------------------------------------------------------------- move
def op_move(ctx: _Ctx, sel: dict, params: dict) -> dict:
    if _empty(sel):
        raise ValueError("move: nothing selected")
    dv = [_num(params, "dx", 0.0), _num(params, "dy", 0.0),
          _num(params, "dz", 0.0)]
    if _norm(dv) < GEOM_TOL:
        raise ValueError("move: the offset (dx, dy, dz) is zero")
    pts = _sel_joints(ctx, sel)
    keys = {_key(p) for p in pts}
    # tendons hosted only by moved objects move rigidly with them
    hosts_moved = set(sel["members"]) | set(sel["shells"])
    for td in ctx.d.get("tendons") or []:
        h = td.get("host") or []
        if isinstance(h, str):
            h = [h]
        if h and all(x in hosts_moved for x in h):
            td["points"] = [_clean(_add(p, dv)) for p in td["points"]]
    touched = _remap_points(
        ctx, lambda p: _add(p, dv) if _key(p) in keys else None)
    for kind, uid in touched:
        ctx.mark_modified(kind, uid)
        _restory_obj(ctx, kind, uid)
    _cleanup_degenerate(ctx)
    _dedupe_point_records(ctx.d)
    ns = copy.deepcopy(sel)
    ns["points"] = [_clean(_add(p, dv)) for p in sel["points"]]
    return {"moved_points": len(keys), "new_selection": ns}


# ----------------------------------------------------------------- extrude
def _default_section(d: dict, kind: str) -> str:
    names = list((d.get("sections") or {}).keys())
    if not names:
        raise ValueError("extrude: the model has no frame sections")
    want = "COL" if kind == "column" else "BEAM"
    return next((n for n in names if want in n.upper()), names[0])


def _auto_kind(a, b) -> str:
    dv = _sub(b, a)
    h = math.hypot(dv[0], dv[1])
    if h < 1e-9:
        return "column"
    if abs(dv[2]) < 1e-9:
        return "beam"
    return "brace"


def op_extrude(ctx: _Ctx, sel: dict, params: dict) -> dict:
    d = ctx.d
    mode = params.get("mode", "points_to_frames")
    dv = [_num(params, "dx", 0.0), _num(params, "dy", 0.0),
          _num(params, "dz", 0.0)]
    n = _num(params, "n", 1, integer=True, lo=1, hi=MAX_COPIES)
    if _norm(dv) < GEOM_TOL:
        raise ValueError("extrude: the offset (dx, dy, dz) is zero")
    ns = _new_sel()
    existing = {_geom_key_member(m) for m in d["members"]}
    existing |= {_geom_key_shell(s) for s in d["shells"]}
    if mode == "points_to_frames":
        pts = sel["points"]
        if not pts:
            raise ValueError("extrude: select points")
        kind = params.get("kind") or _auto_kind([0, 0, 0], dv)
        if kind not in ("column", "beam", "brace"):
            raise ValueError("extrude: 'kind' must be column|beam|brace")
        sec = params.get("section") or _default_section(d, kind)
        if sec not in (d.get("sections") or {}):
            raise ValueError(f"extrude: unknown section {sec!r}")
        prefix = {"column": "C", "beam": "B", "brace": "D"}[kind]
        for p in pts:
            for k in range(n):
                a = _clean(_add(p, _mul(dv, k)))
                b = _clean(_add(p, _mul(dv, k + 1)))
                nm = {"uid": "", "kind": kind, "section": sec,
                      "pi": a, "pj": b}
                if _geom_key_member(nm) in existing:
                    ctx.warnings.append(f"frame {a}->{b} exists, skipped")
                    continue
                nm["uid"] = ctx.new_uid(prefix, prefix)
                nm["story"] = ""
                ctx.restory(nm, (a, b))
                existing.add(_geom_key_member(nm))
                d["members"].append(nm)
                ctx.created["members"].append(nm["uid"])
                ns["members"].append(nm["uid"])
    elif mode == "frames_to_shells":
        if not sel["members"]:
            raise ValueError("extrude: select frame members")
        ssecs = list((d.get("shell_sections") or {}).keys())
        sec = params.get("section") or (ssecs[0] if ssecs else None)
        if not sec or sec not in (d.get("shell_sections") or {}):
            raise ValueError("extrude: a shell section is required")
        behavior = params.get("behavior", "shell")
        if behavior not in ("shell", "membrane"):
            raise ValueError("extrude: 'behavior' must be shell|membrane")
        for u in sel["members"]:
            m = ctx.member(u)
            horiz = abs(dv[2]) < 1e-9 and abs(m["pi"][2] - m["pj"][2]) < 1e-9
            kind = params.get("kind") or ("slab" if horiz else "wall")
            if kind not in ("wall", "slab"):
                raise ValueError("extrude: 'kind' must be wall|slab")
            if kind == "wall" and behavior == "membrane":
                raise ValueError("extrude: membrane behavior is slab-only")
            for k in range(n):
                c0 = _clean(_add(m["pi"], _mul(dv, k)))
                c1 = _clean(_add(m["pj"], _mul(dv, k)))
                c2 = _clean(_add(m["pj"], _mul(dv, k + 1)))
                c3 = _clean(_add(m["pi"], _mul(dv, k + 1)))
                corners = [c0, c1, c2, c3]
                if kind == "slab":
                    # counter-clockwise seen from +Z
                    area2 = sum(corners[i][0] * corners[(i + 1) % 4][1]
                                - corners[(i + 1) % 4][0] * corners[i][1]
                                for i in range(4))
                    if area2 < 0:
                        corners = [c0, c3, c2, c1]
                ns_ = {"uid": "", "kind": kind, "behavior": behavior,
                       "section": sec, "corners": corners,
                       "mesh_size": float(params.get("mesh_size", 1.0)),
                       "openings": []}
                if _geom_key_shell(ns_) in existing:
                    ctx.warnings.append("shell exists, skipped")
                    continue
                p = "S" if kind == "slab" else "W"
                ns_["uid"] = ctx.new_uid(p, p)
                ns_["story"] = ""
                ctx.restory(ns_, corners)
                existing.add(_geom_key_shell(ns_))
                d["shells"].append(ns_)
                ctx.created["shells"].append(ns_["uid"])
                ns["shells"].append(ns_["uid"])
        if params.get("delete_source"):
            _delete_objects(ctx, members=list(sel["members"]))
    else:
        raise ValueError("extrude: 'mode' must be points_to_frames|"
                         "frames_to_shells")
    return {"new_selection": ns}


# ----------------------------------------------------------------- join
_JOIN_FREE = {"uid", "pi", "pj", "story", "length", "releases", "rigid_i",
              "rigid_j", "joint_offsets", "hinges"}


def _join_pair(ctx: _Ctx, A: dict, B: dict, J) -> Optional[str]:
    """Join B into A at joint J; returns None on success or the reason."""
    d = ctx.d
    for k in set(A) | set(B):
        if k in _JOIN_FREE:
            continue
        if A.get(k) != B.get(k):
            return f"different {k}"
    ha, hb = A.get("hinges", "none"), B.get("hinges", "none")
    if isinstance(ha, str) and isinstance(hb, str) and ha != hb:
        return "different hinges"
    if isinstance(ha, str) != isinstance(hb, str) and \
            "none" not in (ha, hb):
        return "different hinges"
    ra = {t.strip() for t in str(A.get("releases", "")).split(",")
          if t.strip()}
    rb = {t.strip() for t in str(B.get("releases", "")).split(",")
          if t.strip()}
    a_at_J = "pj" if _key(A["pj"]) == J else "pi"
    b_at_J = "pi" if _key(B["pi"]) == J else "pj"
    if ("Mj" if a_at_J == "pj" else "Mi") in ra or \
            ("Mi" if b_at_J == "pi" else "Mj") in rb:
        return "moment release at the shared joint"
    for pat in d["patterns"].values():
        ta = [x for x in pat.get("thermal_loads") or []
              if x["member_uid"] == A["uid"]]
        tb = [x for x in pat.get("thermal_loads") or []
              if x["member_uid"] == B["uid"]]
        strip = lambda lst: sorted(json_key({k: v for k, v in x.items()
                                             if k != "member_uid"})
                                   for x in lst)
        if strip(ta) != strip(tb):
            return "different temperature loads"
    La = math.dist(A["pi"], A["pj"])
    Lb = math.dist(B["pi"], B["pj"])
    L = La + Lb
    # new member: A's far end -> B's far end, keeping A's sense
    if a_at_J == "pj":
        new_pi, new_pj = list(A["pi"]), list(B["pj"] if b_at_J == "pi"
                                             else B["pi"])

        def fa(t):
            return t * La / L
        b_rev = b_at_J == "pj"

        def fb(s):
            return (La + (s if not b_rev else 1.0 - s) * Lb) / L
    else:
        new_pi, new_pj = list(B["pj"] if b_at_J == "pi" else B["pi"]), \
            list(A["pj"])

        def fa(t):
            return (Lb + t * La) / L
        b_rev = b_at_J == "pi"

        def fb(s):
            return ((s if not b_rev else 1.0 - s) * Lb) / L
    # end assignments from the end pieces
    i_piece, i_end = (A, "i") if a_at_J == "pj" else \
        (B, "j" if b_at_J == "pi" else "i")
    j_piece, j_end = (A, "j") if a_at_J == "pi" else \
        (B, "j" if b_at_J == "pi" else "i")
    rel_i = f"M{i_end}" in ({t.strip() for t in str(i_piece.get(
        "releases", "")).split(",")})
    rel_j = f"M{j_end}" in ({t.strip() for t in str(j_piece.get(
        "releases", "")).split(",")})
    nm = A
    nm["pi"], nm["pj"] = _clean(new_pi), _clean(new_pj)
    nm["releases"] = ",".join(t for t, on in (("Mi", rel_i), ("Mj", rel_j))
                              if on)
    nm["rigid_i"] = float(i_piece.get(f"rigid_{i_end}", 0.0) or 0.0)
    nm["rigid_j"] = float(j_piece.get(f"rigid_{j_end}", 0.0) or 0.0)
    joa, job = A.get("joint_offsets"), B.get("joint_offsets")
    if joa or job:
        def jo_of(piece, end):
            jo = piece.get("joint_offsets") or {}
            return list(jo.get(end) or [0.0, 0.0, 0.0])
        nm["joint_offsets"] = {
            "system": (joa or job).get("system", "global"),
            "i": jo_of(i_piece, i_end), "j": jo_of(j_piece, j_end)}
    if isinstance(ha, list) or isinstance(hb, list):
        hl = []
        for h in (ha if isinstance(ha, list) else []):
            nh = dict(h)
            nh["relative_distance"] = round(fa(float(
                h.get("relative_distance", 0.0))), 12)
            hl.append(nh)
        for h in (hb if isinstance(hb, list) else []):
            nh = dict(h)
            nh["relative_distance"] = round(fb(float(
                h.get("relative_distance", 0.0))), 12)
            hl.append(nh)
        nm["hinges"] = sorted(hl, key=lambda h: h["relative_distance"]) \
            or "none"
    nm.pop("length", None)
    # loads
    for pat in d["patterns"].values():
        out = []
        mine = []
        at = None
        for ld in pat.get("member_loads") or []:
            if ld["member_uid"] in (A["uid"], B["uid"]):
                f = fa if ld["member_uid"] == A["uid"] else fb
                rev = ld["member_uid"] == B["uid"] and b_rev
                if at is None:
                    at = len(out)
                mine.append(_map_load(ld, f, rev, A["uid"]))
                if rev and str(ld.get("direction", "")).startswith("local"):
                    ctx.warnings.append(f"member {B['uid']}: local-axis "
                                        "load copied onto reversed span")
            else:
                out.append(ld)
        udls = pat.get("member_udls") or []
        wa = sorted(x["w"] for x in udls if x["member_uid"] == A["uid"])
        wb = sorted(x["w"] for x in udls if x["member_uid"] == B["uid"])
        udl_out = []
        for ld in udls:
            if wa == wb:                 # same full-span UDL: keep A's
                if ld["member_uid"] != B["uid"]:
                    udl_out.append(ld)
            elif ld["member_uid"] in (A["uid"], B["uid"]):
                f = fa if ld["member_uid"] == A["uid"] else fb
                lo, hi = sorted((f(0.0), f(1.0)))
                mine.append({"member_uid": A["uid"], "kind": "udl",
                             "w": ld["w"], "w2": 0.0,
                             "a": round(lo, 12), "b": round(hi, 12),
                             "direction": "gravity"})
            else:
                udl_out.append(ld)
        if "member_udls" in pat:
            pat["member_udls"] = udl_out
        if mine:
            at = len(out) if at is None else at
            out[at:at] = _coalesce_udls(mine)
        if out or "member_loads" in pat:
            pat["member_loads"] = out
        if pat.get("thermal_loads"):
            pat["thermal_loads"] = [x for x in pat["thermal_loads"]
                                    if x["member_uid"] != B["uid"]]
    d["members"] = [m for m in d["members"] if m is not B]
    _replace_uid_refs(d, "members", B["uid"], [A["uid"]])
    if B["uid"] in ctx.created["members"]:
        ctx.created["members"].remove(B["uid"])
    else:
        ctx.deleted["members"].append(B["uid"])
    ctx.mark_modified("members", A["uid"])
    return None


def _coalesce_udls(loads: List[dict]) -> List[dict]:
    """Merge contiguous identical partial UDLs (the pieces of a divided UDL
    joined back) into one span; other loads keep their order."""
    out: List[dict] = []
    for ld in loads:
        hit = None
        if ld.get("kind", "udl") == "udl":
            for prev in out:
                if (prev.get("kind", "udl") == "udl"
                        and abs(float(prev.get("b", 1.0))
                                - float(ld.get("a", 0.0))) < 1e-9
                        and all(prev.get(k) == ld.get(k)
                                for k in set(prev) | set(ld)
                                if k not in ("a", "b"))):
                    hit = prev
                    break
        if hit is not None:
            hit["b"] = ld.get("b", 1.0)
            if abs(hit["b"] - 1.0) < 1e-9:
                hit["b"] = 1.0
            continue
        out.append(ld)
    return out


def json_key(x):
    import json
    return json.dumps(x, sort_keys=True)


def _map_load(ld, f, rev, uid):
    nl = copy.deepcopy(ld)
    nl["member_uid"] = uid
    kind = ld.get("kind", "udl")
    a = float(ld.get("a", 0.0))
    b = float(ld.get("b", 1.0))
    if kind in ("point", "moment"):
        nl["a"] = round(f(a), 12)
        return nl
    na, nb = f(a), f(b)
    if rev:
        na, nb = nb, na
        if kind == "trapezoid":
            nl["w"], nl["w2"] = ld.get("w2", 0.0), ld.get("w", 0.0)
    nl["a"], nl["b"] = round(na, 12), round(nb, 12)
    return nl


def op_join(ctx: _Ctx, sel: dict, params: dict) -> dict:
    d = ctx.d
    if len(sel["members"]) < 2:
        raise ValueError("join: select at least two frame members")
    point_recs = set()
    for lst, _k in _point_lists(d):
        for r in lst:
            point_recs.add(_key(r["point"]))
    selected = set(sel["members"])
    joined = []
    skipped = []
    progress = True
    while progress:
        progress = False
        cnt = ctx.object_joints()
        ends: Dict[tuple, List[dict]] = {}
        for m in d["members"]:
            if m["uid"] in selected:
                for e in ("pi", "pj"):
                    ends.setdefault(_key(m[e]), []).append(m)
        for J, ms in ends.items():
            if len(ms) != 2 or cnt.get(J, 0) != 2:
                continue
            A, B = ms
            ua = _sub(A["pj"], A["pi"])
            ub = _sub(B["pj"], B["pi"])
            if _norm(_cross(ua, ub)) > 1e-9 * _norm(ua) * _norm(ub) * 1e3:
                continue
            fa_ = A["pi"] if _key(A["pj"]) == J else A["pj"]
            fb_ = B["pi"] if _key(B["pj"]) == J else B["pj"]
            if _dot(_sub(fa_, list(J)), _sub(fb_, list(J))) >= 0:
                continue                      # overlapping, not end-to-end
            if J in point_recs:
                skipped.append({"joint": list(J), "reason":
                                "joint carries loads / assignments"})
                continue
            if A["section"] != B["section"]:
                skipped.append({"joint": list(J), "reason":
                                "different sections"})
                continue
            if d["members"].index(B) < d["members"].index(A):
                A, B = B, A
            why = _join_pair(ctx, A, B, J)
            if why:
                skipped.append({"joint": list(J), "reason": why})
                continue
            joined.append({"kept": A["uid"], "removed": B["uid"],
                           "joint": list(J)})
            selected.discard(B["uid"])
            progress = True
            break
    # dedupe skip reports
    uniq, seen = [], set()
    for s in skipped:
        k = (tuple(s["joint"]), s["reason"])
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    for m in d["members"]:
        if m["uid"] in [j["kept"] for j in joined]:
            ctx.restory(m, (m["pi"], m["pj"]))
    ns = _new_sel()
    ns["members"] = [u for u in sel["members"] if u in selected]
    return {"joined": joined, "skipped": uniq, "new_selection": ns}


# ----------------------------------------------------------------- delete
def op_delete(ctx: _Ctx, sel: dict, params: dict) -> dict:
    if _empty(sel):
        raise ValueError("delete: nothing selected")
    _delete_objects(ctx, members=sel["members"], shells=sel["shells"],
                    links=sel["links"])
    npts = 0
    if sel["points"]:
        keys = {_key(p) for p in sel["points"]}
        npts = _delete_point_records(ctx, keys)
        ctx.deleted["points"].extend(list(k) for k in sorted(keys))
    return {"point_records_deleted": npts, "new_selection": _new_sel()}


_OPS = {"replicate": op_replicate, "divide": op_divide,
        "merge_joints": op_merge_joints, "align": op_align,
        "move": op_move, "extrude": op_extrude, "join": op_join,
        "delete": op_delete}


# --------------------------------------------------------------------------- #
# public entry points
# --------------------------------------------------------------------------- #
def apply_edit(model_dict: dict, op: str, selection: Optional[dict] = None,
               params: Optional[dict] = None) -> Tuple[dict, dict]:
    """Apply edit ``op`` to a COPY of ``model_dict``; returns
    ``(new_model_dict, summary)``.  Raises ValueError on bad input."""
    if op not in _OPS:
        raise ValueError(f"unknown edit op {op!r} (one of {EDIT_OPS})")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("'params' must be an object")
    d = copy.deepcopy(model_dict)
    ctx = _Ctx(d)
    sel = normalize_selection(d, selection)
    extra = _OPS[op](ctx, sel, params)
    return d, ctx.summary(op, extra)


def apply_edit_model(model, op: str, selection: Optional[dict] = None,
                     params: Optional[dict] = None):
    """:class:`BuildingModel` wrapper: returns ``(new_model, summary)``;
    the input model is not modified.  The result is fully validated
    (``BuildingModel.from_dict``)."""
    from skyframe.core.model import BuildingModel
    d, summary = apply_edit(model.to_dict(), op, selection, params)
    return BuildingModel.from_dict(d), summary
