"""Shell force and stress results: contours, envelopes, stresses (A3).

Pure POST-PROCESSING of a solved results dict (``AnalysisResults.to_dict()``)
plus the :class:`~skyframe.core.model.BuildingModel`.  The solver is never
re-run and the engine results are never modified (legacy results JSON stays
byte-identical).  CONTRACT "Shell force contours and shell stresses".

Inputs per shell element (``results["shell_quads"][i]``)
--------------------------------------------------------
The 8 Gauss-averaged resultants ``[F11, F22, F12, M11, M22, M12, V13, V23]``
(= the engine's ``[Nxx, Nyy, Nxy, Mxx, Myy, Mxy, Vxz, Vyz]``) of a source:

* static case / single-valued combo / staged final state — signed state;
* response spectrum (and RS directional combo) — unsigned peaks ``r`` from
  ``results["rs_cases"][name]["shell_forces"]`` (same per-element shape as
  static cases), entering as the interval ``[-r, +r]`` (RS signs are lost,
  the ETABS rule used by every combo);
* max/min combos (envelope / abs / srss / range, and add combos with RS or
  max/min members) — evaluated here from the model's combo definition with
  the interval rules of CONTRACT "Load combinations: RS/TH/nested members".

Representation: a source is a list of STATES; a state is a box
``lo <= x <= hi`` of the 8 resultants per element.  A signed state is a
degenerate box.  ``add`` combos sum the member boxes into one box, ``abs`` /
``srss`` / ``range`` build one box by their CONTRACT rules, and ``envelope``
keeps one state per member, so every displayed quantity (including the
principal values) is enveloped member by member, as ETABS does.

Element axes (verified against OpenSees 'stresses' by probing)
--------------------------------------------------------------
* quads (ShellMITC4 / ShellDKGQ / ASDShellQ4 with ``-local``):
  ``e1 = (x2 + x3 - x1 - x4)/2`` normalised, ``e3 = e1 x (x3 + x4 - x1 -
  x2)/2`` normalised, ``e2 = e3 x e1``;
* triangles ShellDKGT: ``e1 = x2 - x1``, ``e3 = e1 x (x3 - x1)``,
  ``e2 = e3 x e1``; ASDShellT3 (``shell_thick`` / ``membrane`` /
  ``plate_thick`` triangles, built without ``-local``): ``e1' = -e2``,
  ``e2' = e1`` of the DKGT basis, same ``e3``.

Sign conventions: ``M = integral(sigma z dz)`` with ``z`` along ``e3`` (a
plate curving up, ``w = k x^2/2``, reports ``M11 = -D k``), so the stress at
``z`` is ``sigma(z) = N/t + 12 M z / t^3``; TOP is the ``+3`` face
(``S = N/t + 6M/t^2``), BOTTOM the ``-3`` face (``N/t - 6M/t^2``), MID is
``N/t``.  ``S13`` / ``S23`` are the average transverse shear stresses
``V/t``.

Output axes
-----------
``"local"`` — element axes above.  ``"global"`` — a global-aligned in-plane
frame per element: slabs (``|n_z| >= cos 45 deg``): 1 = global X projected
on the plane, 3 = the upward normal, 2 = 3 x 1 (a horizontal slab: X, Y,
Z); walls: 2 = global Z projected on the plane (up), 1 = the horizontal
in-plane direction pointing to +X (+Y for walls running along Y), 3 = 1 x 2.
Tensors transform as ``N' = B N B^T`` (``B_ab = a_a . e_b``); moments and
transverse shears also take ``s = sign(a3 . e3)`` (the top face follows
``a3``).

Derived quantities (per (11, 22, 12) triple)
-------------------------------------------
``Max/Min = (a+b)/2 +- sqrt(((a-b)/2)^2 + c^2)``, ``VM = sqrt(a^2 - ab +
b^2 + 3c^2)``, ``Angle = atan2(2c, a - b)/2`` (degrees, from axis 1 to the
Max direction), ``VMax = hypot(V13, V23)``.  On a non-degenerate box the
linear components are exact interval bounds; the nonlinear ones are taken
over the 8 box corners, which is EXACT for the governing bound (``FMax``,
``FVM`` upper; ``FMin`` lower: convex / concave functions attain their box
extremes at corners) and a corner estimate for the opposite bound.
``VMax`` is exact both ways.  Stresses of a box state are bounded
independently per resultant (conservative for RS: the true peak of
``N/t + 6M/t^2`` can be lower than ``|N|/t + 6|M|/t^2``).

Nodal averaging (ETABS Display > Shell Stresses/Forces, "Contour
averaging")
---------------------------------------------------------------------------
``"none"`` — element (centroid) values only.  ``"objects"`` — at every
element corner the value is the arithmetic mean over the elements of the
SAME shell object (region) sharing the node.  ``"all"`` — the mean over
every element sharing the node whose plane is parallel (|n_i . n_j| >=
``PARALLEL_COS``; elements meeting at a wall/slab junction are not mixed).
Neighbour tensors are first rotated into the receiving element's output
frame, so a uniform field is reproduced exactly at every node.  The
averaging is applied to the resultant box of each state, then the quantity
(component / principal / stress) is evaluated from the averaged resultants.

Layered shells (``ShellSection.layered``)
-----------------------------------------
Linear analyses solve layered sections as ONE elastic section (section
material ``E*mod``, ``nu``, summed thickness — CONTRACT v0.22), so no per-
layer results exist.  Per-layer stresses are therefore an APPROXIMATION
(always flagged in ``approximations``): the mid-surface strain ``A^-1 N``
and curvature ``D^-1 M`` of that homogeneous section give the strain at the
layer centre ``z`` (layers listed from the bottom (-3) face up, the OpenSees
LayeredShell order); concrete layers use their own plane-stress law, steel
layers ``sigma = E_s eps`` along the bar (layer ``angle`` from local 1).
For layers of the section material this equals ``N/t + 12 M z / t^3``
exactly.  Section stiffness modifiers (f11..v23, shell_type factors) are
not part of the strain recovery.

Public API::

    list_sources(model, results)                   -> [{name, kind, bounded}]
    source_states(model, results, name)            -> (states, info)
    contour(model, results, case, component, location="top", axes="local",
            averaging="none", bound="max")         -> contour dict
    element_rows(model, results, name, ...)        -> table helper
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .combos_ext import leaf_factors, member_kind
from . import th_shells as _ths          # Wave C TH shell sources

BASE = ("F11", "F22", "F12", "M11", "M22", "M12", "V13", "V23")
NB = 8
PARALLEL_COS = 0.9998          # ~1.15 deg: "parallel" planes for averaging
AXES = ("local", "global")
AVERAGING = ("none", "objects", "all")
BOUNDS = ("max", "min")
FACES = ("top", "mid", "bot")
_ASD_TRI = "ASDShellT3"

# component -> (family, sub, quantity)
COMPONENTS: Dict[str, Tuple[str, str, str]] = {}
for _p, _q in (("F", "line_force"), ("M", "line_moment"), ("S", "stress")):
    for _s in ("11", "22", "12", "Max", "Min", "VM", "Angle"):
        COMPONENTS[_p + _s] = (_p, _s, "angle" if _s == "Angle" else _q)
for _s in ("13", "23", "Max"):
    COMPONENTS["V" + _s] = ("V", _s, "line_force")
COMPONENTS["S13"] = ("T", "13", "stress")
COMPONENTS["S23"] = ("T", "23", "stress")
CONTOUR_COMPONENTS = [c for c in COMPONENTS if not c.endswith("Angle")]


class Unavailable(ValueError):
    """The source has no shell results (not run / TH member / no shells)."""


# --------------------------------------------------------------------------- #
# geometry: element frames, output frames, thickness / layers
# --------------------------------------------------------------------------- #
def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n < 1e-300, 1.0, n)


def element_frame(pts: Sequence[Sequence[float]], family: str = "") -> np.ndarray:
    """3x3 rows (e1, e2, e3) of one element (see the module doc)."""
    c = np.asarray(pts, dtype=float)
    if len(c) == 4:
        v1 = 0.5 * (c[1] + c[2] - c[0] - c[3])
        v2 = 0.5 * (c[2] + c[3] - c[0] - c[1])
    else:
        v1 = c[1] - c[0]
        v2 = c[2] - c[0]
    e1 = _unit(v1)
    e3 = _unit(np.cross(v1, v2))
    e2 = np.cross(e3, e1)
    if len(c) == 3 and family == _ASD_TRI:
        e1, e2 = -e2, e1
    return np.array([e1, e2, e3])


def global_frame(n: np.ndarray) -> np.ndarray:
    """Global-aligned in-plane frame (rows a1, a2, a3) for unit normal n."""
    n = np.asarray(n, dtype=float)
    if abs(n[2]) >= math.sqrt(0.5):                     # slab-like
        a3 = n if n[2] > 0 else -n
        a1 = _unit(np.array([1.0, 0, 0]) - a3[0] * a3)
        a2 = np.cross(a3, a1)
        return np.array([a1, a2, a3])
    a2 = _unit(np.array([0, 0, 1.0]) - n[2] * n)        # wall-like: up
    a1 = _unit(np.cross(a2, n))
    if abs(a1[0]) > 1e-6:
        if a1[0] < 0:
            a1 = -a1
    elif a1[1] < 0:
        a1 = -a1
    return np.array([a1, a2, np.cross(a1, a2)])


def tensor_map(B: np.ndarray, sg: np.ndarray) -> np.ndarray:
    """(P, 8, 8) linear maps of the 8 resultants for frame changes with
    in-plane matrices ``B`` (P, 2, 2) (``B_ab = a_a . e_b``) and normal
    signs ``sg`` (P,)."""
    B = np.asarray(B, dtype=float).reshape(-1, 2, 2)
    sg = np.asarray(sg, dtype=float).reshape(-1)
    P = len(B)
    b11, b12, b21, b22 = B[:, 0, 0], B[:, 0, 1], B[:, 1, 0], B[:, 1, 1]
    S = np.zeros((P, 3, 3))
    S[:, 0] = np.stack([b11 * b11, b12 * b12, 2 * b11 * b12], -1)
    S[:, 1] = np.stack([b21 * b21, b22 * b22, 2 * b21 * b22], -1)
    S[:, 2] = np.stack([b11 * b21, b12 * b22, b11 * b22 + b12 * b21], -1)
    T = np.zeros((P, NB, NB))
    T[:, 0:3, 0:3] = S
    T[:, 3:6, 3:6] = S * sg[:, None, None]
    T[:, 6:8, 6:8] = B * sg[:, None, None]
    return T


class Geometry:
    """Per-element frames, regions, thickness and layer data of a results
    dict (built once per request)."""

    def __init__(self, model, results: dict):
        quads = results.get("shell_quads") or []
        nodes = results.get("nodes") or {}
        self.n = len(quads)
        regions = {r.uid: r for r in getattr(model, "shells", [])}
        self.region = [str(q.get("region", "")) for q in quads]
        self.nodes = [[int(t) for t in q["nodes"]] for q in quads]
        frames, thick, secs, story = [], [], [], []
        for q, rid in zip(quads, self.region):
            pts = [nodes.get(str(t), nodes.get(t)) for t in q["nodes"]]
            reg = regions.get(rid)
            ssec = (model.shell_sections.get(reg.section)
                    if reg is not None else None)
            fam = ""
            if ssec is not None and len(pts) == 3:
                try:
                    from . import shell_types as _st
                    fam = _st.element_names(_st.shell_type_of(ssec))[1]
                except Exception:                      # pragma: no cover
                    fam = ""
            frames.append(element_frame(pts, fam))
            thick.append(float(ssec.total_thickness) if ssec is not None
                         else float("nan"))
            secs.append(ssec)
            story.append(getattr(reg, "story", "") if reg is not None else "")
        self.E = np.array(frames).reshape(-1, 3, 3)
        self.t = np.array(thick, dtype=float)
        self.sections = secs
        self.story = story
        self.model = model
        self._out: Dict[str, np.ndarray] = {}

    def out_frame(self, axes: str) -> np.ndarray:
        if axes not in self._out:
            if axes == "local":
                self._out[axes] = self.E.copy()
            else:
                self._out[axes] = (np.array([global_frame(e[2]) for e in self.E])
                                   .reshape(-1, 3, 3))
        return self._out[axes]

    def to_axes(self, axes: str) -> Optional[np.ndarray]:
        """(n, 8, 8) element-local -> output maps, None for local axes."""
        if axes == "local" or self.n == 0:
            return None
        A = self.out_frame(axes)
        B = np.einsum("pai,pbi->pab", A[:, :2], self.E[:, :2])
        sg = np.sign(np.einsum("pi,pi->p", A[:, 2], self.E[:, 2]))
        return tensor_map(B, sg)

    def neighbours(self, averaging: str, axes: str):
        """Corner averaging pairs: (corner index, j, weight, T or None)
        arrays plus ``offsets`` (corner index of each element's first
        corner)."""
        offsets = np.cumsum([0] + [len(nd) for nd in self.nodes])
        by_node: Dict[int, List[int]] = {}
        for i, nd in enumerate(self.nodes):
            for t in nd:
                by_node.setdefault(t, []).append(i)
        A = self.out_frame(axes)
        cor, js, ws = [], [], []
        for i, nd in enumerate(self.nodes):
            for k, t in enumerate(nd):
                J = [j for j in by_node[t]
                     if (averaging == "all" or self.region[j] == self.region[i])
                     and abs(float(A[i, 2] @ A[j, 2])) >= PARALLEL_COS]
                w = 1.0 / len(J)
                for j in J:
                    cor.append(offsets[i] + k)
                    js.append(j)
                    ws.append(w)
        cor = np.array(cor, dtype=int)
        js = np.array(js, dtype=int)
        ws = np.array(ws, dtype=float)
        ii = np.repeat(np.arange(self.n), np.diff(offsets))[cor] \
            if len(cor) else np.zeros(0, dtype=int)
        Ai, Aj = A[ii], A[js]
        B = np.einsum("pai,pbi->pab", Ai[:, :2], Aj[:, :2])
        sg = np.sign(np.einsum("pi,pi->p", Ai[:, 2], Aj[:, 2]))
        ident = (np.abs(B - np.eye(2)).reshape(len(B), -1).max(axis=1)
                 < 1e-12) & (sg > 0) if len(B) else np.zeros(0, bool)
        T = tensor_map(B[~ident], sg[~ident]) if (~ident).any() else None
        return offsets, cor, js, ws, ident, T

    # ------------------------------------------------ layered sections
    def layers(self, i: int) -> List[dict]:
        ssec = self.sections[i]
        lay = getattr(ssec, "layered", None) if ssec is not None else None
        if not lay:
            return []
        out, z0 = [], -0.5 * float(ssec.total_thickness)
        for k, la in enumerate(lay.get("layers", [])):
            t = float(la["t"])
            out.append({"index": k, "t": t, "z": z0 + 0.5 * t,
                        "material": la["material"], "kind": la["kind"],
                        "angle": float(la.get("angle", 0.0))})
            z0 += t
        return out


# --------------------------------------------------------------------------- #
# sources -> states
# --------------------------------------------------------------------------- #
def _arr(sf: Optional[dict], n: int) -> np.ndarray:
    out = np.zeros((n, NB))
    for k, v in (sf or {}).items():
        i = int(k)
        if 0 <= i < n and v is not None and len(v) >= NB:
            out[i] = np.asarray(v[:NB], dtype=float)
    return out


def _scale(state, f: float):
    lo, hi = state
    a, b = f * lo, f * hi
    return (np.minimum(a, b), np.maximum(a, b))


def _bbox(states):
    lo = states[0][0].copy()
    hi = states[0][1].copy()
    for a, b in states[1:]:
        lo = np.minimum(lo, a)
        hi = np.maximum(hi, b)
    return (lo, hi)


def _states(model, results: dict, name: str, n: int, depth: int = 0):
    """(states, bounded) of one source; raises :class:`Unavailable`."""
    if depth > 64:                                       # pragma: no cover
        raise Unavailable(f"{name}: combo nesting too deep")
    kind = member_kind(model, name)
    if kind is None and name in (results.get("rs_cases") or {}):
        kind = "response_spectrum"
    if kind == "static":
        cd = (results.get("cases") or {}).get(name)
        if not cd or "shell_forces" not in cd:
            raise Unavailable(f"case {name!r} has no shell forces")
        a = _arr(cd["shell_forces"], n)
        return [(a, a)], False
    if kind == "staged":
        cd = (results.get("staged") or {}).get(name)
        if not cd or "shell_forces" not in cd:
            raise Unavailable(f"staged case {name!r} has no shell forces")
        a = _arr(cd["shell_forces"], n)
        return [(a, a)], False
    if kind in ("response_spectrum", "rs_directional"):
        cd = (results.get("rs_cases") or {}).get(name)
        if not cd or "shell_forces" not in cd:
            raise Unavailable(f"response-spectrum case {name!r} has no shell "
                              "forces in these results")
        r = np.abs(_arr(cd["shell_forces"], n))
        return [(-r + 0.0, r)], True
    if kind == "time_history":                  # Wave C (core/th_shells)
        return _ths.th_states(results, name, n, _arr, Unavailable)
    if kind == "combo":
        cd = (results.get("combos") or {}).get(name)
        if cd and "shell_forces" in cd and "min" not in cd:
            a = _arr(cd["shell_forces"], n)              # engine superposition
            return [(a, a)], False
        cb = model.combos[name]
        ctype = cb.combo_type
        if ctype == "add":
            members = list((leaf_factors(model, name) or cb.cases).items())
        else:
            members = list(cb.cases.items())
        per = []
        bounded = False
        for c, f in members:
            st, bd = _states(model, results, c, n, depth + 1)
            bounded = bounded or bd
            per.append([_scale(s, float(f)) for s in st])
        if ctype == "envelope":
            return [s for st in per for s in st], True
        boxes = [_bbox(st) for st in per]
        if ctype == "add":
            lo = sum(b[0] for b in boxes)
            hi = sum(b[1] for b in boxes)
            return [(lo + 0.0, hi + 0.0)], bounded
        if ctype in ("abs", "srss"):
            mags = [np.maximum(np.abs(a), np.abs(b)) for a, b in boxes]
            v = (sum(mags) if ctype == "abs"
                 else np.sqrt(sum(m ** 2 for m in mags)))
            return [(-v + 0.0, v)], True
        if ctype == "range":
            hi = sum(np.maximum(b, 0.0) for _, b in boxes)
            lo = sum(np.minimum(a, 0.0) for a, _ in boxes)
            return [(lo + 0.0, hi + 0.0)], True
        raise Unavailable(f"combo {name!r}: unknown type {ctype!r}")
    st = _ths.step_states(model, results, name, n, _arr)   # Wave C TH step
    if st is not None:
        return st
    raise Unavailable(f"{name!r} is not a case with shell results")


def source_states(model, results: dict, name: str):
    """``(states, info)``: states = list of ``(lo, hi)`` (n, 8) arrays in
    ELEMENT local axes; info = ``{name, kind, bounded}``."""
    n = len(results.get("shell_quads") or [])
    if n == 0:
        raise Unavailable("the results have no meshed shell elements")
    states, bounded = _states(model, results, name, n)
    kind = (member_kind(model, name) or _ths.pseudo_kind(model, name)
            or "response_spectrum")
    return states, {"name": name, "kind": kind, "bounded": bool(bounded)}


def list_sources(model, results: dict) -> List[dict]:
    """Every case / combo with shell results: ``[{name, kind, bounded}]``
    (static cases, RS cases, combos, staged cases, in that order)."""
    out: List[dict] = []
    if not results or not results.get("shell_quads"):
        return out
    names: List[str] = []
    for blk in ("cases", "rs_cases", "combos", "staged"):
        for nm in (results.get(blk) or {}):
            if nm not in names:
                names.append(nm)
    names += [nm for nm in _ths.source_names(results)   # Wave C TH shells
              if nm not in names]
    for nm in names:
        try:
            _st, info = source_states(model, results, nm)
        except (Unavailable, KeyError):
            continue
        out.append(info)
    return out


# --------------------------------------------------------------------------- #
# quantities
# --------------------------------------------------------------------------- #
def _layer_maps(geo: Geometry, i: int, layer: int, T: Optional[np.ndarray]
                ) -> np.ndarray:
    """(3, 8) map from OUTPUT-frame resultants to the layer stress triple."""
    lays = geo.layers(i)
    if layer >= len(lays):
        return np.full((3, NB), np.nan)
    la = lays[layer]
    ssec = geo.sections[i]
    mats = geo.model.materials
    from skyframe.core import aniso as _wca          # Wave C: exact stack
    if _wca.layered_general(ssec):
        return _wca.layer_stress_map(ssec, mats, layer,
                                     None if T is None else T[i])
    m0 = mats[ssec.material]
    E0 = float(m0.E) * float(getattr(ssec, "mod", 1.0) or 1.0)
    nu0 = float(m0.nu)
    t = float(ssec.total_thickness)
    C = np.array([[1, -nu0, 0], [-nu0, 1, 0], [0, 0, 2 * (1 + nu0)]])
    d = np.array([math.cos(math.radians(la["angle"])),
                  math.sin(math.radians(la["angle"]))])
    sg = 1.0
    if T is not None:
        # the map holds B (V block = s*B) and s (M block = s * membrane block)
        sg = 1.0 if np.allclose(T[i, 3:6, 3:6], T[i, 0:3, 0:3]) else -1.0
        d = (sg * T[i, 6:8, 6:8]) @ d             # bar direction, output axes
    z = sg * la["z"]                              # +3 of the output frame
    # strain triple (eps11, eps22, gamma12) = C N/(E t) + z * 12 C M/(E t^3)
    Ems = np.zeros((3, NB))
    Ems[:, 0:3] = C / (E0 * t)
    Ems[:, 3:6] = z * 12.0 * C / (E0 * t ** 3)
    lm = mats[la["material"]]
    if la["kind"] == "steel":
        c, s = d
        along = np.array([c * c, s * s, c * s]) @ Ems    # eps along the bar
        sig = float(lm.E) * along
        return np.outer([c * c, s * s, c * s], sig)
    E, nu = float(lm.E), float(lm.nu)
    Q = E / (1 - nu * nu) * np.array([[1, nu, 0], [nu, 1, 0],
                                      [0, 0, 0.5 * (1 - nu)]])
    return Q @ Ems


def _family_map(geo: Geometry, family: str, location: str,
                T: Optional[np.ndarray]) -> np.ndarray:
    """(n, m, 8) linear maps from output-frame resultants to the family's
    linear components (m = 3 triples, 2 for V / T)."""
    n = geo.n
    if family in ("F", "M", "V"):
        idx = {"F": [0, 1, 2], "M": [3, 4, 5], "V": [6, 7]}[family]
        L = np.zeros((len(idx), NB))
        for r, c in enumerate(idx):
            L[r, c] = 1.0
        return np.broadcast_to(L, (n, len(idx), NB)).copy()
    t = geo.t
    if family == "T":
        L = np.zeros((n, 2, NB))
        L[:, 0, 6] = 1.0 / t
        L[:, 1, 7] = 1.0 / t
        return L
    # family S: faces or layers
    if location.startswith("layer"):
        k = int(location[5:].lstrip(":") or 0)
        return np.array([_layer_maps(geo, i, k, T) for i in range(n)]
                        ).reshape(n, 3, NB)
    c = {"top": 1.0, "mid": 0.0, "bot": -1.0}[location]
    L = np.zeros((n, 3, NB))
    for r in range(3):
        L[:, r, r] = 1.0 / t
        L[:, r, 3 + r] = c * 6.0 / t ** 2
    return L


def _derive(sub: str, family: str, lo: np.ndarray, hi: np.ndarray
            ) -> Tuple[np.ndarray, np.ndarray]:
    """(lo, hi) of component ``sub`` from linear-component boxes (.., m)."""
    if family in ("V", "T"):
        if sub in ("13", "23"):
            k = 0 if sub == "13" else 1
            return lo[..., k], hi[..., k]
        mag_hi = np.sqrt(np.maximum(lo ** 2, hi ** 2).sum(axis=-1))
        gap = np.maximum(0.0, np.maximum(lo, -hi))
        return np.sqrt((gap ** 2).sum(axis=-1)), mag_hi
    if sub in ("11", "22", "12"):
        k = {"11": 0, "22": 1, "12": 2}[sub]
        return lo[..., k], hi[..., k]
    if np.array_equal(lo, hi):
        v = _scalar(sub, lo[..., 0], lo[..., 1], lo[..., 2])
        return v, v
    vals = []
    for a in (lo[..., 0], hi[..., 0]):
        for b in (lo[..., 1], hi[..., 1]):
            for c in (lo[..., 2], hi[..., 2]):
                vals.append(_scalar(sub, a, b, c))
    V = np.stack(vals, axis=0)
    if sub == "Angle":
        return np.full(V.shape[1:], np.nan), np.full(V.shape[1:], np.nan)
    return V.min(axis=0), V.max(axis=0)


def _scalar(sub: str, a, b, c):
    if sub == "Max":
        return 0.5 * (a + b) + np.sqrt((0.5 * (a - b)) ** 2 + c * c)
    if sub == "Min":
        return 0.5 * (a + b) - np.sqrt((0.5 * (a - b)) ** 2 + c * c)
    if sub == "VM":
        return np.sqrt(np.maximum(a * a - a * b + b * b + 3 * c * c, 0.0))
    if sub == "Angle":
        return np.degrees(0.5 * np.arctan2(2 * c, a - b))
    raise KeyError(sub)                                  # pragma: no cover


def _apply(T: Optional[np.ndarray], C: np.ndarray, R: np.ndarray):
    if T is None:
        return C, R
    return (np.einsum("pij,pj->pi", T, C), np.einsum("pij,pj->pi",
                                                     np.abs(T), R))


def _quantity(L: np.ndarray, C: np.ndarray, R: np.ndarray, family: str,
              sub: str):
    c = np.einsum("pij,pj->pi", L, C)
    r = np.einsum("pij,pj->pi", np.abs(L), R)
    return _derive(sub, family, c - r, c + r)


def _check(component: str, location: str, axes: str, averaging: str,
           bound: str) -> None:
    if component not in COMPONENTS:
        raise ValueError(f"unknown component {component!r}; one of "
                         f"{list(COMPONENTS)}")
    if location not in FACES and not (location.startswith("layer")
                                      and location[5:].lstrip(":").isdigit()):
        raise ValueError("location must be top | mid | bot | layer:<i>")
    if axes not in AXES:
        raise ValueError(f"axes must be one of {AXES}")
    if averaging not in AVERAGING:
        raise ValueError(f"averaging must be one of {AVERAGING}")
    if bound not in BOUNDS:
        raise ValueError(f"bound must be one of {BOUNDS}")


def evaluate(model, results: dict, case: str, component: str, *,
             location: str = "top", axes: str = "local",
             averaging: str = "none", geometry: Optional[Geometry] = None):
    """Low-level: ``(elem_lo, elem_hi, corner_lo, corner_hi, offsets,
    info, geo)``; corner arrays are None without averaging."""
    _check(component, location, axes, averaging, "max")
    states, info = source_states(model, results, case)
    geo = geometry or Geometry(model, results)
    if len(states[0][0]) != geo.n:                      # pragma: no cover
        raise Unavailable("shell results do not match shell_quads")
    family, sub, _q = COMPONENTS[component]
    T = geo.to_axes(axes)
    L = _family_map(geo, family, location, T)
    e_lo = e_hi = c_lo = c_hi = None
    offsets = None
    nb = geo.neighbours(averaging, axes) if averaging != "none" else None
    for lo, hi in states:
        C, R = _apply(T, 0.5 * (lo + hi), 0.5 * (hi - lo))
        a, b = _quantity(L, C, R, family, sub)
        e_lo = a if e_lo is None else np.minimum(e_lo, a)
        e_hi = b if e_hi is None else np.maximum(e_hi, b)
        if nb is not None:
            offsets, cor, js, ws, ident, Tn = nb
            nc = offsets[-1]
            Cj, Rj = C[js], R[js]
            if Tn is not None:
                Cj = Cj.copy(); Rj = Rj.copy()
                Cj[~ident] = np.einsum("pij,pj->pi", Tn, C[js[~ident]])
                Rj[~ident] = np.einsum("pij,pj->pi", np.abs(Tn), R[js[~ident]])
            CC = np.zeros((nc, NB)); RR = np.zeros((nc, NB))
            np.add.at(CC, cor, ws[:, None] * Cj)
            np.add.at(RR, cor, ws[:, None] * Rj)
            owner = np.repeat(np.arange(geo.n), np.diff(offsets))
            ca, cb_ = _quantity(L[owner], CC, RR, family, sub)
            c_lo = ca if c_lo is None else np.minimum(c_lo, ca)
            c_hi = cb_ if c_hi is None else np.maximum(c_hi, cb_)
    return e_lo, e_hi, c_lo, c_hi, offsets, info, geo


def _clean(v) -> Optional[float]:
    f = float(v)
    if not math.isfinite(f):
        return None
    return f + 0.0


def contour(model, results: dict, case: str, component: str, *,
            location: str = "top", axes: str = "local",
            averaging: str = "none", bound: str = "max") -> dict:
    """Contour data of one component for one case/combo (CONTRACT shape)."""
    _check(component, location, axes, averaging, bound)
    e_lo, e_hi, c_lo, c_hi, offsets, info, geo = evaluate(
        model, results, case, component, location=location, axes=axes,
        averaging=averaging)
    pick_hi = (bound == "max") or not info["bounded"]
    ev = e_hi if pick_hi else e_lo
    values = [_clean(v) for v in ev]
    corners = None
    if c_lo is not None:
        cv = c_hi if pick_hi else c_lo
        corners = [[_clean(v) for v in cv[offsets[i]:offsets[i + 1]]]
                   for i in range(geo.n)]
    allv = [v for v in values if v is not None]
    if corners:
        allv += [v for cs in corners for v in cs if v is not None]
    family, sub, qty = COMPONENTS[component]
    approx: List[str] = []
    warnings: List[str] = []
    if family in ("S", "T") and np.isnan(geo.t).any():
        warnings.append("elements without a known section thickness are "
                        "reported as null")
    if location.startswith("layer"):
        approx.append("layer stresses: strain from the homogeneous elastic "
                      "section used by linear analyses, layer material law "
                      "applied (no per-layer FE results in linear runs)")
        if not any(geo.layers(i) for i in range(geo.n)):
            warnings.append("no layered shell sections in this model")
    if info["bounded"] and sub in ("Max", "Min", "VM"):
        approx.append("principal / von Mises of a max/min source: governing "
                      "bound exact over the resultant box, opposite bound "
                      "evaluated at the box corners")
    if info["bounded"] and family in ("S", "T"):
        approx.append("stresses of a max/min source bound N and M "
                      "independently (conservative for RS)")
    return {
        "case": case, "kind": info["kind"], "bounded": info["bounded"],
        "bound": bound if info["bounded"] else None,
        "component": component, "quantity": qty,
        "location": location if family == "S" else None,
        "axes": axes, "averaging": averaging,
        "values": values, "corners": corners,
        "range": [min(allv), max(allv)] if allv else [0.0, 0.0],
        "approximations": approx, "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# table helpers
# --------------------------------------------------------------------------- #
FORCE_TABLE_COMPS = ("F11", "F22", "F12", "FMax", "FMin", "FAngle", "FVM",
                     "M11", "M22", "M12", "MMax", "MMin", "MAngle",
                     "V13", "V23", "VMax")
STRESS_TABLE_COMPS = ("S11", "S22", "S12", "SMax", "SMin", "SAngle", "SVM")


def element_rows(model, results: dict, name: str, comps: Iterable[str],
                 location: str = "top", geometry: Optional[Geometry] = None
                 ) -> Tuple[dict, List[Dict[str, Optional[float]]],
                            List[Dict[str, Optional[float]]]]:
    """``(info, max_rows, min_rows)``: per element {component: value} of
    the element (centroid, unaveraged, local axes) values; ``min_rows`` is
    empty for signed sources."""
    geo = geometry or Geometry(model, results)
    hi_cols: Dict[str, np.ndarray] = {}
    lo_cols: Dict[str, np.ndarray] = {}
    info = None
    for comp in comps:
        lo, hi, _a, _b, _o, info, _g = evaluate(
            model, results, name, comp, location=location, geometry=geo)
        hi_cols[comp], lo_cols[comp] = hi, lo
    rows_hi = [{c: _clean(hi_cols[c][i]) for c in hi_cols}
               for i in range(geo.n)]
    rows_lo = ([{c: _clean(lo_cols[c][i]) for c in lo_cols}
                for i in range(geo.n)] if info and info["bounded"] else [])
    return info, rows_hi, rows_lo
