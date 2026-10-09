"""Nonprismatic (tapered / haunched) frame sections (ETABS Define > Frame
Sections > Nonprismatic).

A :class:`~skyframe.core.model.FrameSection` with ``kind ==
"nonprismatic"`` carries an ordered list of SEGMENTS along the member
(end i -> end j)::

    {"start_section": "<prismatic section>",
     "end_section":   "<prismatic section>",
     "length": float > 0,
     "length_type": "relative" | "absolute",      # default "relative"
     "EI33": "linear" | "parabolic" | "cubic",     # default "linear"
     "EI22": "linear" | "parabolic" | "cubic"}     # default "linear"

and ``subdivisions`` (int >= 1, default 16): the number of equal analysis
sub-elements per VARYING segment.

Segment lengths (ETABS rule): absolute lengths are metres; the member
length left after them is shared by the relative segments in proportion
to their ``length`` values.  Members whose absolute lengths exceed the
member length, or that have only absolute segments not summing to the
member length, are rejected.

Property variation inside a segment (fraction ``s`` in [0, 1])::

    A, J        linear                              (ETABS)
    I33 / I22   I^(1/n) linear, n = 1 / 2 / 3 for linear / parabolic / cubic

The engine divides every member using the section into analysis
sub-elements (``subdivisions`` equal pieces per varying segment, plus
every segment boundary) through the frame auto-mesh path
(:mod:`skyframe.core.mesh`), so results are stitched per DRAWN member,
releases and rigid end offsets act at the drawn ends only, and other
cuts (shell nodes, auto mesh, foundation) still merge.  Each analysis
segment gets the interpolated (A, I22, I33, J) at its midpoint, times the
nonprismatic section's own stiffness modifiers.  Self-weight is applied
as exact trapezoidal loads over each segment (A linear), element / story
self mass integrate the varying area.

A nonprismatic section whose segments all have IDENTICAL property sets
(same A/I33/I22/J at every start and end) is "uniform": it is never
divided and behaves exactly like the prismatic section (byte-identical).

The section's own ``material`` / ``A`` / ``I33`` / ``I22`` / ``J`` /
``b`` / ``h`` are representative values (those of the i-end section)
filled by :func:`resolve_sections`; they are what consumers that need ONE
value per section see (drawing, panel-zone sizing, hinge springs,
design).  All referenced sections must share one material.  Referenced
sections' own modifiers are not used; frame shear deformation (As2/As3)
is not supported on nonprismatic sections.
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional, Tuple

NP_KIND = "nonprismatic"
SECTION_KINDS = ("prismatic", NP_KIND)
EI_VARIATIONS = {"linear": 1, "parabolic": 2, "cubic": 3}
LENGTH_TYPES = ("relative", "absolute")
DEFAULT_SUBDIVISIONS = 16
MAX_SUBDIVISIONS = 200
SEG_KEYS = ("start_section", "end_section", "length", "length_type",
            "EI33", "EI22")
_LTOL = 1e-6          # m, length bookkeeping tolerance
_CUT_TOL = 1e-6       # m (= mesh._TOL)


# --------------------------------------------------------------------------- #
# predicates
# --------------------------------------------------------------------------- #
def is_np(sec) -> bool:
    return sec is not None and getattr(sec, "kind", "prismatic") == NP_KIND


def _raw(sec) -> Tuple[float, float, float, float]:
    return (float(sec.A), float(sec.I22), float(sec.I33), float(sec.J))


def is_varying(model, sec) -> bool:
    """True for a nonprismatic section whose properties actually vary
    (False for prismatic sections and uniform nonprismatic ones)."""
    if not is_np(sec):
        return False
    props = set()
    for sg in sec.segments or ():
        for k in ("start_section", "end_section"):
            ref = model.sections.get(sg[k])
            if ref is None:
                return False
            props.add(_raw(ref))
    return len(props) > 1


def varying_members(model) -> List:
    out = []
    cache: Dict[str, bool] = {}
    for m in model.members:
        if m.section not in cache:
            cache[m.section] = is_varying(model, model.sections.get(m.section))
        if cache[m.section]:
            out.append(m)
    return out


def any_varying(model) -> bool:
    return any(is_varying(model, s) for s in model.sections.values()
               if is_np(s))


# --------------------------------------------------------------------------- #
# normalisation / validation
# --------------------------------------------------------------------------- #
def normalize_segment(sg, where: str = "") -> dict:
    if not isinstance(sg, dict):
        raise ValueError(f"{where}segment must be an object")
    bad = set(sg) - set(SEG_KEYS)
    if bad:
        raise ValueError(f"{where}segment: unknown key(s) {sorted(bad)}")
    out = {}
    for k in ("start_section", "end_section"):
        v = sg.get(k)
        if not isinstance(v, str) or not v:
            raise ValueError(f"{where}segment.{k} must be a section name")
        out[k] = v
    ln = sg.get("length", 1.0)
    if isinstance(ln, bool) or not isinstance(ln, (int, float)):
        raise ValueError(f"{where}segment.length must be a number")
    ln = float(ln)
    if not (math.isfinite(ln) and ln > 0.0):
        raise ValueError(f"{where}segment.length must be a finite value > 0")
    out["length"] = ln
    lt = sg.get("length_type", "relative")
    if lt not in LENGTH_TYPES:
        raise ValueError(f"{where}segment.length_type must be one of "
                         f"{LENGTH_TYPES}, got {lt!r}")
    out["length_type"] = lt
    for k in ("EI33", "EI22"):
        v = sg.get(k, "linear")
        if v not in EI_VARIATIONS:
            raise ValueError(f"{where}segment.{k} must be one of "
                             f"{tuple(EI_VARIATIONS)}, got {v!r}")
        out[k] = v
    return out


def normalize_subdivisions(n, where: str = "") -> int:
    if isinstance(n, bool) or not isinstance(n, (int, float)) \
            or float(n) != int(n):
        raise ValueError(f"{where}subdivisions must be an integer")
    n = int(n)
    if not 1 <= n <= MAX_SUBDIVISIONS:
        raise ValueError(f"{where}subdivisions must be in "
                         f"[1, {MAX_SUBDIVISIONS}]")
    return n


def resolve_sections(model) -> None:
    """Normalise every nonprismatic section in place and fill its
    representative material / A / I33 / I22 / J / b / h from the i-end
    section (raises ``ValueError`` on bad input)."""
    for name, sec in model.sections.items():
        kind = getattr(sec, "kind", "prismatic")
        if kind not in SECTION_KINDS:
            raise ValueError(f"Section {name}: kind must be one of "
                             f"{SECTION_KINDS}, got {kind!r}")
        if kind != NP_KIND:
            continue
        where = f"Section {name}: "
        segs = sec.segments
        if not isinstance(segs, list) or not segs:
            raise ValueError(f"{where}a nonprismatic section needs a "
                             "non-empty segments list")
        sec.segments = [normalize_segment(sg, where) for sg in segs]
        sec.subdivisions = normalize_subdivisions(sec.subdivisions, where)
        mats = set()
        for sg in sec.segments:
            for k in ("start_section", "end_section"):
                ref = model.sections.get(sg[k])
                if ref is None:
                    raise ValueError(f"{where}unknown section {sg[k]!r}")
                if is_np(ref):
                    raise ValueError(f"{where}segment section {sg[k]!r} "
                                     "must be prismatic")
                for v in _raw(ref):
                    if not (math.isfinite(v) and v > 0.0):
                        raise ValueError(
                            f"{where}section {sg[k]!r} needs A, I33, I22, "
                            "J > 0")
                mats.add(ref.material)
        if len(mats) != 1:
            raise ValueError(f"{where}all segment sections must share one "
                             f"material (got {sorted(mats)})")
        if (getattr(sec, "shear_deformation", False)
                or sec.As2 is not None or sec.As3 is not None):
            raise ValueError(f"{where}frame shear deformation (As2/As3) is "
                             "not supported on nonprismatic sections")
        ref = model.sections[sec.segments[0]["start_section"]]
        sec.material = ref.material
        sec.A, sec.I33, sec.I22, sec.J = ref.A, ref.I33, ref.I22, ref.J
        sec.b, sec.h = ref.b, ref.h


def validate_model(model) -> None:
    """Sections (see :func:`resolve_sections`) + every member using a
    varying nonprismatic section (segment lengths fit; not axial-only)."""
    resolve_sections(model)
    for m in varying_members(model):
        if getattr(m, "axial_limit", "both") != "both":
            raise ValueError(f"Member {m.uid}: axial-only members cannot use "
                             f"the nonprismatic section {m.section!r}")
        layout(model, m)                      # raises on bad lengths


# --------------------------------------------------------------------------- #
# geometry of one member
# --------------------------------------------------------------------------- #
def layout(model, m) -> List[dict]:
    """Absolute segment layout of member ``m``: ``[{"x0", "x1", "p0",
    "p1", "n33", "n22"}]`` (``p`` = raw (A, I22, I33, J))."""
    sec = model.sections[m.section]
    L = float(m.length)
    segs = sec.segments
    abs_sum = sum(s["length"] for s in segs if s["length_type"] == "absolute")
    rel_sum = sum(s["length"] for s in segs if s["length_type"] == "relative")
    where = f"Member {m.uid} (nonprismatic section {sec.name}): "
    if abs_sum > L + _LTOL:
        raise ValueError(f"{where}absolute segment lengths ({abs_sum:.6g} m) "
                         f"exceed the member length ({L:.6g} m)")
    rem = L - abs_sum
    if rel_sum <= 0.0:
        if abs(rem) > _LTOL:
            raise ValueError(f"{where}absolute segment lengths "
                             f"({abs_sum:.6g} m) must sum to the member "
                             f"length ({L:.6g} m) when no segment is "
                             "relative")
    elif rem <= _LTOL:
        raise ValueError(f"{where}no length is left for the relative "
                         "segments")
    out: List[dict] = []
    x = 0.0
    for k, s in enumerate(segs):
        ln = (s["length"] if s["length_type"] == "absolute"
              else rem * s["length"] / rel_sum)
        x1 = L if k == len(segs) - 1 else x + ln
        out.append({"x0": x, "x1": x1,
                    "p0": _raw(model.sections[s["start_section"]]),
                    "p1": _raw(model.sections[s["end_section"]]),
                    "n33": EI_VARIATIONS[s["EI33"]],
                    "n22": EI_VARIATIONS[s["EI22"]]})
        x = x1
    return out


def _interp(p0, p1, n22: int, n33: int, s: float
            ) -> Tuple[float, float, float, float]:
    def ipow(a: float, b: float, n: int) -> float:
        if n == 1:
            return a + s * (b - a)
        ra, rb = a ** (1.0 / n), b ** (1.0 / n)
        return (ra + s * (rb - ra)) ** n
    return (p0[0] + s * (p1[0] - p0[0]),
            ipow(p0[1], p1[1], n22),
            ipow(p0[2], p1[2], n33),
            p0[3] + s * (p1[3] - p0[3]))


def props_at(model, m, x: float, lay: Optional[List[dict]] = None
             ) -> Tuple[float, float, float, float]:
    """Raw interpolated (A, I22, I33, J) at distance ``x`` from end i."""
    lay = lay if lay is not None else layout(model, m)
    seg = lay[-1]
    for sg in lay:
        if x <= sg["x1"] + 1e-12:
            seg = sg
            break
    ln = seg["x1"] - seg["x0"]
    s = 0.0 if ln <= 0.0 else min(max((x - seg["x0"]) / ln, 0.0), 1.0)
    return _interp(seg["p0"], seg["p1"], seg["n22"], seg["n33"], s)


def sub_boundaries(model, m) -> List[float]:
    """Sorted interior cut stations (m): every segment boundary plus the
    ``subdivisions`` equal divisions of every varying segment."""
    sec = model.sections[m.section]
    n = int(sec.subdivisions)
    L = float(m.length)
    xs: List[float] = []
    for sg in layout(model, m):
        x0, x1 = sg["x0"], sg["x1"]
        xs.append(x0)
        if sg["p0"] != sg["p1"]:
            for i in range(1, n):
                xs.append(x0 + i * (x1 - x0) / n)
    out: List[float] = []
    for x in sorted(xs):
        if x <= _CUT_TOL or x >= L - _CUT_TOL:
            continue
        if out and x - out[-1] <= _CUT_TOL:
            continue
        out.append(x)
    return out


def merge_cut_points(model, auto_pts: dict) -> dict:
    """Add the nonprismatic cuts to the frame auto-mesh cut dict
    (``{uid: [(t, point)]}``).  Returns ``auto_pts`` itself (untouched)
    when no member uses a varying nonprismatic section."""
    mems = varying_members(model)
    if not mems:
        return auto_pts
    out = dict(auto_pts)
    for m in mems:
        pi = tuple(map(float, m.pi))
        pj = tuple(map(float, m.pj))
        L = float(m.length)
        lst = list(out.get(m.uid, []))
        for x in sub_boundaries(model, m):
            lst.append((x, tuple(pi[k] + (pj[k] - pi[k]) * x / L
                                 for k in range(3))))
        lst.sort(key=lambda c: c[0])
        clean = []
        for t, p in lst:
            if clean and t - clean[-1][0] <= _CUT_TOL:
                continue
            clean.append((t, p))
        out[m.uid] = clean
    return out


def segment_raw_props(model, m, x0: float, length: float
                      ) -> Tuple[float, float, float, float]:
    """Raw props of the analysis segment [x0, x0 + length] (midpoint)."""
    return props_at(model, m, x0 + 0.5 * length)


def segment_eff_props(model, m, x0: float, length: float
                      ) -> Tuple[float, float, float, float]:
    """(A, I22, I33, J) of an analysis segment with the nonprismatic
    section's own stiffness modifiers applied."""
    sec = model.sections[m.section]
    A, I22, I33, J = segment_raw_props(model, m, x0, length)
    return (A * sec.mod_A, I22 * sec.mod_I22, I33 * sec.mod_I33,
            J * sec.mod_J)


# --------------------------------------------------------------------------- #
# area integrals (weight / mass)
# --------------------------------------------------------------------------- #
def area_pieces(model, m) -> List[Tuple[float, float, float, float]]:
    """Exact piecewise-linear area profile: ``[(a, b, A_a, A_b)]`` with
    ``a``/``b`` member-length fractions (raw areas, no modifiers)."""
    L = float(m.length)
    return [(sg["x0"] / L, sg["x1"] / L, sg["p0"][0], sg["p1"][0])
            for sg in layout(model, m)]


def area_integral(model, m) -> float:
    """Integral of A(x) over the member (m^3)."""
    L = float(m.length)
    return sum(0.5 * (a0 + a1) * (b - a) * L
               for a, b, a0, a1 in area_pieces(model, m))


def member_area(model, m, sec) -> float:
    """``sec.A`` (the SAME object) unless ``sec`` is a varying
    nonprismatic section, then the mean area ``integral(A) / L`` so
    ``member_area * L`` is the exact volume per unit length."""
    if not is_np(sec) or not is_varying(model, sec):
        return sec.A
    return area_integral(model, m) / float(m.length)


def axial_flexibility(model, m) -> float:
    """``integral dx / A(x)`` over the analysis sub-elements (1/m)."""
    L = float(m.length)
    xs = [0.0] + sub_boundaries(model, m) + [L]
    sec = model.sections[m.section]
    return sum((x1 - x0) / (segment_raw_props(model, m, x0, x1 - x0)[0]
                            * sec.mod_A)
               for x0, x1 in zip(xs[:-1], xs[1:]))


# --------------------------------------------------------------------------- #
# expanded model (self-contained numpy solvers: buckling / Ritz)
# --------------------------------------------------------------------------- #
def expanded_model(model):
    """``model`` itself when nothing varies; else a deep copy in which every
    member using a varying nonprismatic section is replaced by prismatic
    sub-members ``"<uid>#np<k>"`` (sections ``"<section>#np:<uid>:<k>"``
    with the interpolated midpoint properties and the section modifiers).
    Member loads keep referencing the drawn member; the copy carries
    ``_np_parents`` ({uid: drawn member}) for load lumping and
    ``_np_children`` ({uid: [sub uids]})."""
    mems = varying_members(model)
    if not mems:
        return model
    from .model import FrameSection
    new = copy.deepcopy(model)
    parents = {m.uid: m for m in new.members}
    targets = {m.uid for m in mems}
    members = []
    children: Dict[str, List[str]] = {}
    for m in new.members:
        if m.uid not in targets:
            members.append(m)
            continue
        sec = new.sections[m.section]
        L = float(m.length)
        xs = [0.0] + sub_boundaries(new, m) + [L]
        pi, pj = tuple(map(float, m.pi)), tuple(map(float, m.pj))

        def at(x):
            return tuple(pi[k] + (pj[k] - pi[k]) * x / L for k in range(3))
        toks = m.release_tokens()
        for k, (x0, x1) in enumerate(zip(xs[:-1], xs[1:])):
            A, I22, I33, J = segment_raw_props(new, m, x0, x1 - x0)
            sname = f"{sec.name}#np:{m.uid}:{k}"
            sub_sec = FrameSection(sname, sec.material, A, I33, I22, J,
                                   b=sec.b, h=sec.h, mod_A=sec.mod_A,
                                   mod_I33=sec.mod_I33, mod_I22=sec.mod_I22,
                                   mod_J=sec.mod_J, mod_mass=sec.mod_mass,
                                   mod_weight=sec.mod_weight)
            new.sections[sname] = sub_sec
            sub = copy.copy(m)
            sub.uid = f"{m.uid}#np{k}"
            sub.section = sname
            sub.pi = m.pi if k == 0 else at(x0)
            sub.pj = m.pj if k == len(xs) - 2 else at(x1)
            rel = []
            if "Mi" in toks and k == 0:
                rel.append("Mi")
            if "Mj" in toks and k == len(xs) - 2:
                rel.append("Mj")
            sub.releases = ",".join(rel)
            members.append(sub)
            children.setdefault(m.uid, []).append(sub.uid)
    new.members = members
    new._np_parents = parents
    new._np_children = children
    return new


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
def section_to_dict(sec, d: dict) -> dict:
    """FrameSection.to_dict filter: prismatic sections drop the three
    nonprismatic fields (old files byte-identical)."""
    if getattr(sec, "kind", "prismatic") != NP_KIND:
        for k in ("kind", "segments", "subdivisions"):
            d.pop(k, None)
    return d


def section_from_dict(name: str, sd: dict):
    """FrameSection from a ``kind == "nonprismatic"`` JSON object; the
    representative fields may be omitted (filled by resolve_sections)."""
    from .model import FrameSection, _load_frame_shear_mods
    sec = FrameSection(
        name=sd.get("name", name), material=str(sd.get("material", "")),
        A=float(sd.get("A", 0.0)), I33=float(sd.get("I33", 0.0)),
        I22=float(sd.get("I22", 0.0)), J=float(sd.get("J", 0.0)),
        b=float(sd.get("b", 0.0)), h=float(sd.get("h", 0.0)),
        mod_A=float(sd.get("mod_A", 1.0)),
        mod_I33=float(sd.get("mod_I33", 1.0)),
        mod_I22=float(sd.get("mod_I22", 1.0)),
        mod_J=float(sd.get("mod_J", 1.0)))
    _load_frame_shear_mods(sec, sd)
    sec.kind = NP_KIND
    segs = sd.get("segments")
    sec.segments = ([dict(s) if isinstance(s, dict) else s for s in segs]
                    if isinstance(segs, list) else segs)
    sec.subdivisions = sd.get("subdivisions", DEFAULT_SUBDIVISIONS)
    return sec


def nonprismatic_section(name: str, segments: List[dict],
                         subdivisions: int = DEFAULT_SUBDIVISIONS, **mods):
    """Unresolved nonprismatic FrameSection (use
    :func:`add_nonprismatic_section` to add it to a model)."""
    from .model import FrameSection
    sec = FrameSection(name, "", 0.0, 0.0, 0.0, 0.0, **mods)
    sec.kind = NP_KIND
    sec.segments = [dict(s) for s in segments]
    sec.subdivisions = subdivisions
    return sec


def add_nonprismatic_section(model, name: str, segments: List[dict],
                             subdivisions: int = DEFAULT_SUBDIVISIONS,
                             **mods):
    """Create, resolve (representative props from the i-end section) and
    add a nonprismatic section to ``model``; returns it."""
    sec = nonprismatic_section(name, segments, subdivisions, **mods)
    old = model.sections.get(name)
    model.sections[name] = sec
    try:
        resolve_sections(model)
    except ValueError:
        if old is None:
            del model.sections[name]
        else:
            model.sections[name] = old
        raise
    del model.sections[name]
    return model.add_section(sec)
