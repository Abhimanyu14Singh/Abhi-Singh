"""Multi-component / load-pattern time histories (OpenSees runtime side).

Used by ``OpenSeesEngine.run_time_history`` (direct integration, linear
and nonlinear) and ``OpenSeesEngine.run_fna`` (modal superposition) ONLY
when ``TimeHistoryCase.components`` is set; a legacy case never reaches
this module (bit-identical results).  Component semantics and the common
time grid: :mod:`skyframe.core.th_components`.

Direct integration
------------------
* each series carries one trailing 0.0 sample, so the value at the
  last grid time is the last sample even when the accumulated domain
  time lands a few ulps past it (and 0 one step later);
* every ground component becomes one ``UniformExcitation`` pattern per
  nonzero global direction cosine (``Path -dt dt -values <resampled
  record> -factor case_scale*scale*cos``; a rotated horizontal component
  -> an X and a Y excitation sharing the same record);
* every load-pattern component becomes a ``Plain`` pattern driven by a
  ``Path`` series of its time function (``-factor case_scale*scale``)
  holding the pattern's loads (``_apply_pattern``: nodal loads, member /
  area / thermal loads, story forces).  ``ground_displacements`` are not
  supported in a time-varying pattern (ValueError).

Unit pattern load vectors (story shears, FNA modal loads, energy)
-----------------------------------------------------------------
Each pattern is applied once at factor 1 in the freshly built domain
(u = 0) and ``ops.reactions()`` is read: ``P = -nodeReaction`` is the
full consistent nodal load (nodal loads + element-load equivalent nodal
forces) and ``p0 = -(nodeReaction + nodeUnbalance)`` its element-load
part.  The temporary pattern is removed and the loads re-zeroed before
the analysis starts.

Results
-------
Same ``THResults`` shape.  Story shears are ``sum_above (f(t) P_x -
m a_total)`` (inertia-equilibrium rule extended by the applied pattern
loads at the nodes at/above the story elevation; element-load
equivalents are attributed to the member end nodes).  Base reactions:
direct integration reads them (pattern loads included); FNA uses
``R = sum_i L_i (qdd_i + 2 zeta_i w_i qd_i) + M ag - iota'P(t)``.
``extra["multi_component"]`` reports the resolved grid/components and
the base FZ series.

Energy (v1.13 tracker)
----------------------
``P(t) = P_grav - M sum_d iota_d ag_d(t) + sum_p f_p(t) P_p`` (input
energy includes the work of the pattern loads), and the element-load
part ``f_p(t) p0_p`` (which OpenSees carries inside the element resisting
forces) is moved from the resisting forces to the external loads, so
strain energy stays ``0.5 u'Ku`` for an elastic model.
"""

from __future__ import annotations

import warnings
from typing import Dict, List, Optional, Tuple

import numpy as np
import openseespy.opensees as ops

from skyframe.core import th_components as thc

_TS0 = 9100          # time-series / pattern tags (clear of 1, 10, ...)
_TMP = 9099          # temporary unit-load pattern
_TOL = 1e-6


def plan_for(engine, th) -> Optional["MultiRun"]:
    """A runtime driver when the case has ``components``, else ``None``."""
    if getattr(th, "components", None) is None:
        return None
    return MultiRun(engine, th)


class MultiRun:
    def __init__(self, engine, th) -> None:
        self.th = th
        model = engine.model
        self.plan = thc.resolve(model, th)
        for p, _v, _f in self.plan.patterns:
            if getattr(model.patterns[p], "ground_displacements", None):
                raise ValueError(
                    f"TH case {th.name!r}: pattern {p!r} has ground "
                    "displacements, which are not supported in a "
                    "load-pattern time history component")
        self.dt = self.plan.dt
        self.n = self.plan.n
        n = self.n
        self.ag = {d: np.array(self.plan.ground_series(d)) for d in (1, 2, 3)}
        self.pf = [np.array(v) for v in self.plan.pattern_factors()]

        def end(arr):
            out = np.zeros(n)
            out[:n - 1] = arr[1:]
            return out
        self.ag_end = {d: end(a) for d, a in self.ag.items()}
        self.pf_end = [end(a) for a in self.pf]
        self.units: Dict[str, Tuple[Dict[int, np.ndarray],
                                    Dict[int, np.ndarray]]] = {}
        self.fz0 = 0.0
        self.fz: List[float] = []

    # ------------------------------------------------------------ setup
    def zero_accel(self) -> List[float]:
        """Placeholder record of the run length (drives the step loop)."""
        return [0.0] * self.n

    def unit_loads(self, engine, asm) -> None:
        """Unit consistent load vectors of every component pattern (u = 0)."""
        self.units = {}
        names = []
        for p, _v, _f in self.plan.patterns:
            if p not in names:
                names.append(p)
        if not names:
            return
        for p in names:
            ops.timeSeries("Constant", _TMP)
            ops.pattern("Plain", _TMP, _TMP)
            engine._apply_pattern(asm, p, 1.0)
            ops.reactions()
            P: Dict[int, np.ndarray] = {}
            p0: Dict[int, np.ndarray] = {}
            for t in ops.getNodeTags():
                r = np.zeros(6)
                u = np.zeros(6)
                rv = ops.nodeReaction(t)
                uv = ops.nodeUnbalance(t)
                r[:len(rv)] = rv[:6]
                u[:len(uv)] = uv[:6]
                P[int(t)] = -r
                p0[int(t)] = -(r + u)
            self.units[p] = (P, p0)
            ops.remove("loadPattern", _TMP)
            ops.remove("timeSeries", _TMP)
        ops.reactions()          # re-zero node unbalance + element loads

    def check_vertical(self, model, mass_map) -> None:
        if 3 not in self.plan.dofs():
            return
        mz = sum(m for (_t, d), m in mass_map.items() if d == 3)
        if mz <= 0.0:
            warnings.warn(
                f"TH case {self.th.name!r}: a UZ ground component is "
                "requested but the model has no vertical (UZ) mass — the "
                "vertical excitation has no effect; set "
                "mass_options.include_vertical = true or add nodal mz",
                UserWarning)
        elif not model.mass_option("include_vertical"):
            warnings.warn(
                f"TH case {self.th.name!r}: UZ ground component with "
                "mass_options.include_vertical = false — only explicit "
                "nodal mz masses carry the vertical excitation",
                UserWarning)

    def apply(self, engine, asm) -> None:
        """Create the excitation / pattern-load patterns (DI)."""
        tag = _TS0
        dt = float(self.dt)
        for dof, vals, fac in self.plan.ground:
            ops.timeSeries("Path", tag, "-dt", dt,
                           "-values", *[float(a) for a in vals], 0.0,
                           "-factor", float(fac))
            ops.pattern("UniformExcitation", tag, dof, "-accel", tag)
            tag += 1
        for p, vals, fac in self.plan.patterns:
            ops.timeSeries("Path", tag, "-dt", dt,
                           "-values", *[float(a) for a in vals], 0.0,
                           "-factor", float(fac))
            ops.pattern("Plain", tag, tag)
            engine._apply_pattern(asm, p, 1.0)
            tag += 1

    # ------------------------------------------------------------ outputs
    def ext_above(self, dof: int, elevation: float, node_coords
                  ) -> np.ndarray:
        """sum_p f_p(t_k+1) * (pattern load in ``dof`` at/above elev)."""
        out = np.zeros(self.n)
        for (p, _v, _f), fe in zip(self.plan.patterns, self.pf_end):
            P = self.units[p][0]
            tot = sum(float(vec[dof - 1]) for t, vec in P.items()
                      if t in node_coords
                      and node_coords[t][2] >= elevation - _TOL)
            if tot != 0.0:
                out = out + fe * tot
        return out

    def ext_total(self, dof: int) -> np.ndarray:
        out = np.zeros(self.n)
        for (p, _v, _f), fe in zip(self.plan.patterns, self.pf_end):
            tot = sum(float(vec[dof - 1])
                      for vec in self.units[p][0].values())
            if tot != 0.0:
                out = out + fe * tot
        return out

    def begin_base(self, base_tags) -> None:
        self.fz0 = sum(ops.nodeReaction(t)[2] for t in base_tags)
        self.fz = []

    def record_base(self, base_tags) -> None:
        """Call after ``ops.reactions()`` each step."""
        self.fz.append(sum(ops.nodeReaction(t)[2] for t in base_tags)
                       - self.fz0)

    def extra(self) -> dict:
        return {"multi_component": {
            "dt": float(self.dt), "n_steps": int(self.n),
            "components": [dict(c) for c in self.plan.resolved],
            "base_FZ": [float(v) for v in self.fz]}}

    # ------------------------------------------------------------ energy
    def energy_ext(self):
        """``ext(t, tracker) -> (P_ext, fs_correction)`` for EnergyTracker."""
        cache: dict = {}

        def arrays(tracker):
            if "pu" not in cache:
                nn = len(tracker.tags)
                pu, p0 = [], []
                for p, _v, _f in self.plan.patterns:
                    P, P0 = self.units[p]
                    a = np.zeros((nn, 6))
                    b = np.zeros((nn, 6))
                    for i, t in enumerate(tracker.tags):
                        if t in P:
                            a[i] = P[t]
                            b[i] = P0[t]
                    pu.append(a)
                    p0.append(b)
                cache["pu"], cache["p0"] = pu, p0
            return cache["pu"], cache["p0"]

        def ext(t: float, tracker):
            pu, p0 = arrays(tracker)
            Pe = np.zeros_like(tracker.M)
            for dof, vals, fac in self.plan.ground:
                ag = fac * thc.path_value(vals, self.dt, t)
                if ag != 0.0:
                    Pe[:, dof - 1] -= tracker.M[:, dof - 1] * ag
            corr = np.zeros_like(tracker.M)
            for (_p, vals, fac), a, b in zip(self.plan.patterns, pu, p0):
                f = fac * thc.path_value(vals, self.dt, t)
                if f != 0.0:
                    Pe += f * a
                    corr += f * b
            return Pe, corr
        return ext

    # ------------------------------------------------------------ FNA
    def fna_modal_loads(self, eng2, asm2, shp, nm: int, mass_items
                        ) -> Tuple[np.ndarray, Dict[int, np.ndarray],
                                   Dict[int, float]]:
        """``(p_ext, L_d, M_d)``: modal load history (row k at t_k+1),
        per-direction participation ``L_d`` and total mass ``M_d``."""
        eng2._seg_span_loads = {}
        eng2._seg_fef = {}
        self.unit_loads(eng2, asm2)
        L = {d: np.array([sum(mv * float(shp[i][t][d - 1])
                              for (t, dd), mv in mass_items if dd == d)
                          for i in range(nm)]) for d in (1, 2, 3)}
        Mt = {d: sum(mv for (_t, dd), mv in mass_items if dd == d)
              for d in (1, 2, 3)}
        p_ext = np.zeros((self.n, nm))
        for d in (1, 2, 3):
            if np.any(self.ag_end[d]):
                p_ext -= np.outer(self.ag_end[d], L[d])
        for (p, _v, _f), fe in zip(self.plan.patterns, self.pf_end):
            P = self.units[p][0]
            phiP = np.array([sum(float(np.dot(shp[i][t][:6], P[t]))
                                 for t in shp[i] if t in P)
                             for i in range(nm)])
            p_ext += np.outer(fe, phiP)
        return p_ext, L, Mt

    def fna_base(self, d: int, qdd, qd, cz, L, Mt) -> np.ndarray:
        """Base reaction series in global ``d`` (see module docstring)."""
        return (qdd @ L[d] + (qd * cz) @ L[d] + Mt[d] * self.ag_end[d]
                - self.ext_total(d))
