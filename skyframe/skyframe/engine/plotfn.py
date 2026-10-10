"""Plot-function recorder for direct-integration time histories.

Used by ``OpenSeesEngine.run_time_history`` ONLY when the case carries
``output_requests`` (:mod:`skyframe.core.plotfn`); without it the engine
never constructs a recorder and its results are byte-identical.

Every series starts at t = 0 (the state after the held gravity stage,
just before the transient) and then holds one sample per record step, so
each has ``len(THResults.t) + 1`` values aligned with
``plot_functions["t"] = [0, dt, 2 dt, ...]``.

Recorded quantities (SI: m, rad, s, kN, kN*m):

* ``ground`` — the applied ground motion per global direction:
  acceleration (the Path samples, x scale), velocity and displacement
  integrated EXACTLY for the piecewise-linear acceleration
  (:func:`skyframe.core.plotfn.integrate_linear_accel`).
* ``joints`` — per requested point: RELATIVE displacement (past the
  gravity state), velocity and acceleration for all 6 dofs (OpenSees
  ``nodeDisp/nodeVel/nodeAccel`` are relative to the moving base under
  ``UniformExcitation``), plus ABSOLUTE translations ``disp_abs`` /
  ``vel_abs`` / ``acc_abs`` = relative + ground.
* ``links`` — per requested link: element basic deformation / force per
  component (``basicDeformation`` / ``basicForce``; component labels in
  ``components``), TOTAL values (gravity included), and the cumulative
  basic work ``work`` = sum 0.5 (F_k + F_k+1) . (d_k+1 - d_k) — the area
  enclosed by the force-deformation loops (equals the energy tracker's
  per-element work; at a zero-force instant it is the dissipated
  hysteretic energy).
* ``frames`` — per requested member: the 12 local end forces (engine
  ``member_forces`` convention: end i from the first segment, end j from
  the last; any recorded fixed-end corrections of the gravity stage are
  added), TOTAL values.
* ``hinges`` — every zeroLength hinge spring (``uid:end``): rotations
  R2/R3 (local y/z) and moments M2/M3 (``deformation`` /
  ``basicForce`` components 2 and 3 of the hinge spring).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
import openseespy.opensees as ops

from skyframe.core import plotfn as _core

DOF6 = ("UX", "UY", "UZ", "RX", "RY", "RZ")
FRAME_KEYS = ("P_i", "V2_i", "V3_i", "T_i", "M2_i", "M3_i",
              "P_j", "V2_j", "V3_j", "T_j", "M2_j", "M3_j")


def _link_labels(link, n: int) -> List[str]:
    """Human labels of a link's basic components (element dir order)."""
    lt = getattr(link, "link_type", "elastic")
    zero = link.length < 1e-6
    if lt == "elastic":
        names = [f"U{d + 1}" if d < 3 else f"R{d - 2}" for d in range(6)
                 if link.stiffness[d] > 0.0]
        if len(names) == n:
            return names
    elif lt in ("damper", "gap", "hook") and n == 1:
        return ["P"]
    elif n == 3 and zero:
        return ["UX", "UY", "UZ"]        # global shear X, shear Y, axial
    elif n == 3:
        return ["P", "V2", "V3"]         # local axial, shear 2, shear 3
    elif n == 6:
        return ["P", "V2", "V3", "T", "M2", "M3"]
    return [f"c{i + 1}" for i in range(n)]


JOINT_SNAP_TOL = 1.0e-3     # m: display-unit round-off of a typed point


def _node_near(engine, asm, p) -> int:
    """The FE node at ``p`` (1e-6 match), else the nearest structural node
    within JOINT_SNAP_TOL (a point typed in kip-in / ft carries ~1e-6 m
    round-off); otherwise ``ValueError``."""
    try:
        return engine._find_node(asm, tuple(p))
    except ValueError:
        best, dmin = None, JOINT_SNAP_TOL
        for t, c in asm.struct_coords.items():
            d = math.dist(c, p)
            if d <= dmin:
                best, dmin = t, d
        if best is None:
            raise ValueError(f"output_requests: no FE node at point "
                             f"{tuple(p)} (nor within "
                             f"{JOINT_SNAP_TOL * 1000:g} mm)") from None
        return best


class Recorder:
    """Per-step recorder for one TH run (see module docstring)."""

    def __init__(self, engine, th, asm, accel, dt, dof, mc) -> None:
        self.engine = engine
        self.req = _core.normalize(th.output_requests) or {}
        self.asm = asm
        self.dt = float(dt)
        n = len(accel)
        self.n = n
        # ground acceleration per global direction at t = 0 .. n*dt
        g = {d: np.zeros(n + 1) for d in (1, 2, 3)}
        if mc is None:
            src = np.asarray(accel, dtype=float) * float(th.scale)
            g[dof][:n] = src
        else:
            for d in (1, 2, 3):
                src = np.asarray(mc.ag[d], dtype=float)
                g[d][:min(n, len(src))] = src[:n]
        # Newmark samples the Path at the step ends; beyond the record the
        # series is 0 (OpenSees Path semantics)
        self.ag = g
        self.vg: Dict[int, np.ndarray] = {}
        self.ug: Dict[int, np.ndarray] = {}
        for d in (1, 2, 3):
            self.vg[d], self.ug[d] = _core.integrate_linear_accel(g[d],
                                                                  self.dt)
        # joints
        self.joints = []
        for p in self.req.get("joints", []):
            tag = _node_near(engine, asm, p)
            self.joints.append({"point": list(p), "node": int(tag),
                                "u0": list(ops.nodeDisp(tag))[:6],
                                "d": {k: [] for k in DOF6},
                                "v": {k: [] for k in DOF6},
                                "a": {k: [] for k in DOF6}})
        links = {lk.uid: lk for lk in engine.model.links}
        self.links = {}
        for uid in self.req.get("links", []):
            etag = asm.link_ele.get(uid)
            if etag is None:
                raise ValueError(f"output_requests: link {uid!r} has no "
                                 "element in the analysis model")
            self.links[uid] = {"ele": etag, "link": links[uid], "F": [],
                               "D": []}
        self.frames = {}
        for uid in self.req.get("frames", []):
            if uid in asm.truss_uids:
                self.frames[uid] = {"truss": asm.seg_ele[(uid, 0)],
                                    "uid": uid, "rows": []}
                continue
            segs = asm.mesh.segments[uid]
            self.frames[uid] = {
                "first": (asm.seg_ele[(uid, segs[0].index)], segs[0].index),
                "last": (asm.seg_ele[(uid, segs[-1].index)],
                         segs[-1].index),
                "uid": uid, "rows": []}
        self.hinges = {}
        if self.req.get("hinges"):
            for (uid, end), etag in sorted(asm.hinge_ele.items()):
                self.hinges[f"{uid}:{end}"] = {"ele": etag, "rot": [],
                                               "mom": []}
        self._sample()                      # t = 0 state

    # ------------------------------------------------------------ sampling
    def _frame_row(self, fr) -> List[float]:
        if "truss" in fr:
            N = float(ops.eleResponse(fr["truss"], "basicForce")[0])
            return [-N, 0.0, 0.0, 0.0, 0.0, 0.0, N, 0.0, 0.0, 0.0, 0.0, 0.0]
        out = []
        for key, sl in (("first", slice(0, 6)), ("last", slice(6, 12))):
            etag, sidx = fr[key]
            f = list(ops.eleResponse(etag, "localForce"))
            fef = self._fef_of(fr, sidx)
            if fef is not None:
                f = [v + c for v, c in zip(f, fef)]
            out.extend(float(v) for v in f[sl])
        return out

    def _fef_of(self, fr, sidx):
        return self.engine._seg_fef.get((fr["uid"], sidx))

    def _sample(self) -> None:
        self.n_samples = getattr(self, "n_samples", 0) + 1
        for j in self.joints:
            t = j["node"]
            u = list(ops.nodeDisp(t))
            v = list(ops.nodeVel(t))
            a = list(ops.nodeAccel(t))
            for i, k in enumerate(DOF6):
                j["d"][k].append(float(u[i] - j["u0"][i]) if i < len(u)
                                 else 0.0)
                j["v"][k].append(float(v[i]) if i < len(v) else 0.0)
                j["a"][k].append(float(a[i]) if i < len(a) else 0.0)
        for lk in self.links.values():
            lk["F"].append([float(x) for x in
                            ops.eleResponse(lk["ele"], "basicForce")])
            lk["D"].append([float(x) for x in
                            ops.eleResponse(lk["ele"], "basicDeformation")])
        for fr in self.frames.values():
            fr["rows"].append(self._frame_row(fr))
        for h in self.hinges.values():
            d = list(ops.eleResponse(h["ele"], "deformation"))
            f = list(ops.eleResponse(h["ele"], "basicForce"))
            h["rot"].append([float(d[1]) if len(d) > 1 else 0.0,
                             float(d[2]) if len(d) > 2 else 0.0])
            h["mom"].append([float(f[1]) if len(f) > 1 else 0.0,
                             float(f[2]) if len(f) > 2 else 0.0])

    def record(self, k: int) -> None:     # noqa: ARG002 - step index
        """Sample after the converged step k (t = (k+1) dt)."""
        self._sample()

    # ------------------------------------------------------------ output
    def results(self) -> dict:
        n_s = self.n_samples
        t = [float(i * self.dt) for i in range(n_s)]
        keyd = {1: "UX", 2: "UY", 3: "UZ"}
        ground = {"acc": {keyd[d]: [float(x) for x in self.ag[d][:n_s]]
                          for d in (1, 2, 3)},
                  "vel": {keyd[d]: [float(x) for x in self.vg[d][:n_s]]
                          for d in (1, 2, 3)},
                  "disp": {keyd[d]: [float(x) for x in self.ug[d][:n_s]]
                           for d in (1, 2, 3)}}
        joints = []
        for j in self.joints:
            jd = {"point": j["point"], "node": j["node"],
                  "disp": j["d"], "vel": j["v"], "acc": j["a"]}
            for name, rel, gk in (("disp_abs", j["d"], "disp"),
                                  ("vel_abs", j["v"], "vel"),
                                  ("acc_abs", j["a"], "acc")):
                jd[name] = {k: [float(r + g) for r, g in
                                zip(rel[k], ground[gk][k])]
                            for k in ("UX", "UY", "UZ")}
            joints.append(jd)
        links = {}
        for uid, lk in self.links.items():
            F = np.asarray(lk["F"], dtype=float)
            D = np.asarray(lk["D"], dtype=float)
            nc = F.shape[1] if F.ndim == 2 else 0
            labels = _link_labels(lk["link"], nc)
            work = [0.0]
            for i in range(1, len(F)):
                work.append(work[-1] + float(
                    np.dot(0.5 * (F[i] + F[i - 1]), D[i] - D[i - 1])))
            links[uid] = {
                "type": getattr(lk["link"], "link_type", "elastic"),
                "components": labels,
                "deformation": {lab: [float(x) for x in D[:, c]]
                                for c, lab in enumerate(labels)},
                "force": {lab: [float(x) for x in F[:, c]]
                          for c, lab in enumerate(labels)},
                "work": work}
        frames = {}
        for uid, fr in self.frames.items():
            rows = np.asarray(fr["rows"], dtype=float)
            frames[uid] = {k: [float(x) for x in rows[:, c]]
                           for c, k in enumerate(FRAME_KEYS)}
        hinges = {}
        for key, h in self.hinges.items():
            r = np.asarray(h["rot"], dtype=float)
            mo = np.asarray(h["mom"], dtype=float)
            hinges[key] = {"rotation": {"R2": [float(x) for x in r[:, 0]],
                                        "R3": [float(x) for x in r[:, 1]]},
                           "moment": {"M2": [float(x) for x in mo[:, 0]],
                                      "M3": [float(x) for x in mo[:, 1]]}}
        out = {"t": t, "ground": ground, "joints": joints, "links": links,
               "frames": frames}
        if self.req.get("hinges"):
            out["hinges"] = hinges
        out["units"] = {"disp": "m", "rot": "rad", "vel": "m/s",
                        "acc": "m/s^2", "force": "kN",
                        "moment": "kN*m", "work": "kN*m"}
        return out


def make(engine, th, asm, accel, dt, dof, mc) -> Optional[Recorder]:
    """A recorder when the case requests outputs, else ``None``."""
    if getattr(th, "output_requests", None) is None:
        return None
    return Recorder(engine, th, asm, accel, dt, dof, mc)
