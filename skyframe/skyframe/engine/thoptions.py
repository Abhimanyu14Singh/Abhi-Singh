"""Direct-integration TH options + energy output (OpenSees runtime side).

Used by ``OpenSeesEngine.run_time_history`` ONLY when a case sets one of
the options of :mod:`skyframe.core.di_options` (``integration``,
``di_damping``, ``solver``, ``energy``); otherwise the engine keeps its
legacy code path untouched (bit-identical results).

Energy method (relative-motion frame, ground fixed; kN*m = kJ)
--------------------------------------------------------------

The equation of motion is ``M a + f_D + f_S = P`` with the external
nodal load ``P = P_grav - M iota ag(t)`` (held gravity-stage loads plus
the ground-inertia loads).  At every converged (sub)step the tracker
reads, for EVERY node of the domain and all 6 dofs:

* ``u, v, a`` (``nodeDisp/nodeVel/nodeAccel``), lumped mass ``m``
  (the engine's ``mass_map``);
* the element static resisting forces assembled at the node,
  ``f_S = R0 + unbal - alphaM m v`` — ``R0`` = ``ops.reactions()``
  (OpenSees ``Node::resetReactionForce(0)`` returns
  ``-unbal + sum(f_S) + alphaM m v``; the last term is a documented
  OpenSees quirk verified by the SDOF tests) and ``unbal`` =
  ``nodeUnbalance`` read right AFTER the reactions call (``reactions``
  re-applies the loads at the current time — HHT/Wilson leave them at the
  alpha/theta point — so reading it afterwards cancels exactly);
* the viscous Rayleigh damping forces actually applied (central
  difference: evaluated at the scheme's central velocity, see
  ``EnergyTracker._damping_at``),
  ``f_D = R1 + unbal - m a - f_S`` with ``R1`` =
  ``ops.reactions('-dynamic')`` (element ``getResistingForceIncInertia``
  + node ``alphaM m v``) — elements with Rayleigh switched off (e.g.
  zeroLength hinge springs) contribute nothing, exactly as in the solve;
* for ``damping_model == "modal"`` the modal damping forces (applied by
  OpenSees inside the integrator, invisible to reactions) are the
  equilibrium residual ``P - m a - f_S - f_D``.

Increments use the trapezoidal rule on the nodal displacement increment
``du`` (exactly energy-consistent with Newmark average acceleration):

* input       ``E_I += sum(0.5 (P_k + P_k+1) . du)``
* kinetic     ``E_K = 0.5 sum(m v^2)``
* damping     ``E_D += sum(0.5 (f_D,k + f_D,k+1) . du)``
* total work of the resisting forces ``W_S += sum(0.5 (f_S,k + f_S,k+1)
  . du)``, split into
  - hysteretic ``E_H`` = sum over the NONLINEAR elements (zeroLength
    hinge springs and device links) of their basic work
    ``0.5 (F_k + F_k+1)(d_k+1 - d_k)`` minus their recoverable elastic
    energy ``F^2/(2 k0)`` (``k0`` = initial material tangent; 0 -> fully
    dissipative), and
  - recoverable strain ``E_S = W_S - E_H`` (for an elastic structure
    ``0.5 du^T K du`` + work of the held gravity forces).
* error ``E_I - (E_K + E_S + E_D + E_H)`` and its normalised form
  divided by the run maximum of ``max(|E_I|, |E_K|+|E_S|+|E_D|+|E_H|)``.

The balance is exact (round-off) for Newmark average acceleration on a
linear model; for HHT / Wilson / linear-acceleration Newmark the error is
the scheme's numerical dissipation + O(dt^2) quadrature.  Central
difference uses its own exact discrete balance (forces at t_n on the
central increment ``(u_n+1 - u_n-1)/2``, leapfrog kinetic energy
``0.5 m ((u_n+1 - u_n)/dt)^2``; samples at t_n, since OpenSees reports
``v_n, a_n`` with ``u_n+1``).

Rigid-diaphragm / equalDOF constraint forces do no net work (linear
constraints), support dofs have ``du = 0``; both are included in the
nodal sums without effect.  Fiber (forceBeamColumn) hinges are not split
— their work counts as strain energy (documented limitation).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import openseespy.opensees as ops

from skyframe.core import di_options as dio

ENERGY_KEYS = ("t", "input", "kinetic", "strain", "damping", "hysteretic",
               "error", "error_normalized")


def path_value(accel, dt: float, t: float) -> float:
    """OpenSees ``Path -dt`` series value at time t (linear, 0 beyond)."""
    n = len(accel)
    if t < -1e-12 or n == 0:
        return 0.0
    x = t / dt
    k = int(math.floor(x + 1e-9))
    if k >= n - 1:
        return float(accel[n - 1]) if abs(x - (n - 1)) <= 1e-9 else 0.0
    r = x - k
    if r < 1e-9:
        return float(accel[k])
    return float(accel[k]) + r * (float(accel[k + 1]) - float(accel[k]))


class EnergyTracker:
    """Cumulative energy bookkeeping (see module docstring)."""

    def __init__(self, asm, dof: int, accel, dt: float, scale: float,
                 alpha_m: float, modal: bool,
                 nl_elements: Dict[int, str],
                 method: str = "newmark") -> None:
        self.method = method
        self.dof = dof
        self.accel = accel
        self.dt = dt
        self.scale = scale
        self.alpha_m = float(alpha_m)
        self.modal = modal
        self.tags = sorted(int(t) for t in ops.getNodeTags())
        n = len(self.tags)
        self.idx = {t: i for i, t in enumerate(self.tags)}
        self.ndf = [min(6, len(ops.nodeVel(t))) for t in self.tags]
        self.M = np.zeros((n, 6))
        for (t, d), m in asm.mass_map.items():
            if t in self.idx:
                self.M[self.idx[t], d - 1] += m
        self.iota = np.zeros(6)
        self.iota[dof - 1] = 1.0
        self.nl = dict(nl_elements)
        self.k0: Dict[int, np.ndarray] = {}
        self.series: Dict[str, List[float]] = {k: [] for k in ENERGY_KEYS}
        self.E = {"input": 0.0, "damping": 0.0, "work": 0.0}
        self.ele_work: Dict[int, float] = {e: 0.0 for e in self.nl}
        self.Pg = np.zeros((n, 6))
        self.prev = None
        # multi-component / load-pattern cases: callable
        # ext(t, tracker) -> (external load array, f_S correction)
        # (skyframe.engine.thmulti); None = legacy single record
        self.ext = None

    # ------------------------------------------------------------ helpers
    def _nodes(self, name: str) -> np.ndarray:
        fn = getattr(ops, name)
        out = np.zeros((len(self.tags), 6))
        for i, t in enumerate(self.tags):
            v = fn(t)
            out[i, :len(v)] = v[:6]
        return out

    def _ele_state(self) -> Dict[int, Tuple[np.ndarray, np.ndarray]]:
        st = {}
        for e in self.nl:
            F = ops.eleResponse(e, "basicForce")
            D = ops.eleResponse(e, "basicDeformation")
            st[e] = (np.asarray(F, float), np.asarray(D, float))
        return st

    def _initial_tangents(self, st) -> None:
        for e, (F, _D) in st.items():
            k = np.zeros(len(F))
            for i in range(len(F)):
                try:
                    r = ops.eleResponse(e, "material", str(i + 1), "tangent")
                    k[i] = float(r[0]) if r else 0.0
                except Exception:          # pragma: no cover - defensive
                    k[i] = 0.0
            self.k0[e] = k

    def _raw(self) -> dict:
        t = ops.getTime()
        u = self._nodes("nodeDisp")
        v = self._nodes("nodeVel")
        a = self._nodes("nodeAccel")
        # ops.reactions() re-applies the loads at the CURRENT domain time
        # (HHT/Wilson leave them at the alpha/theta point), so the nodal
        # unbalance is read AFTER each reactions call to cancel exactly
        ops.reactions()
        R0 = self._nodes("nodeReaction") + self._nodes("nodeUnbalance")
        ops.reactions("-dynamic")
        R1 = self._nodes("nodeReaction") + self._nodes("nodeUnbalance")
        fs = R0 - self.alpha_m * self.M * v
        fd = R1 - self.M * a - fs
        if self.ext is None:
            ag = self.scale * path_value(self.accel, self.dt, t)
            P = self.Pg - self.M * self.iota * ag
        else:
            Pe, corr = self.ext(t, self)
            P = self.Pg + Pe
            fs = fs + corr      # element-load part -> external work
        return {"t": t, "u": u, "v": v, "a": a, "P": P, "fs": fs, "fd": fd,
                "ele": self._ele_state()}

    def _damping_at(self, vel: np.ndarray, v_now: np.ndarray) -> np.ndarray:
        """Applied Rayleigh damping forces at the nodal velocities ``vel``.

        Central difference: OpenSees integrates with the central velocity
        ``(u_n+1 - u_n-1)/(2 dt)`` but REPORTS a different nodal velocity,
        so the nodal velocities are set to ``vel`` for the reactions call
        and restored afterwards (the step is already committed, so the
        restore is exact; the scheme keeps its own displacement history).
        """
        def put(vv):
            for i, t in enumerate(self.tags):
                for j in range(self.ndf[i]):
                    # '-commit': setNodeVel starts from the COMMITTED
                    # velocity, so each dof must be committed to stick
                    ops.setNodeVel(t, j + 1, float(vv[i, j]), "-commit")
        put(vel)
        a = self._nodes("nodeAccel")
        ops.reactions()
        R0 = self._nodes("nodeReaction") + self._nodes("nodeUnbalance")
        ops.reactions("-dynamic")
        R1 = self._nodes("nodeReaction") + self._nodes("nodeUnbalance")
        put(v_now)
        fs = R0 - self.alpha_m * self.M * vel
        return R1 - self.M * a - fs

    def _finish(self, st: dict) -> dict:
        if self.modal:     # modal damping = equilibrium residual
            st["fd"] = st["P"] - self.M * st["a"] - st["fs"]
        return st

    # ------------------------------------------------------------ API
    def begin(self) -> None:
        """Capture the start state (after gravity, before the transient)."""
        self.Pg = self._nodes("nodeUnbalance")
        raw = self._raw()
        self._initial_tangents(raw["ele"])
        st = self._finish(dict(raw))
        st["ek"] = 0.5 * float(np.sum(self.M * st["v"] ** 2))
        self.prev = st             # last effective state (sampled)
        self._raw_prev = raw       # central difference: u_n, f_S,n, ...
        self._u_nm1 = raw["u"]     # u_n-1 (OpenSees CD: u_-1 = u_0)
        self._D_nm1 = {e: D for e, (_F, D) in raw["ele"].items()}

    def record(self) -> None:
        """Accumulate one converged (sub)step."""
        raw = self._raw()
        if self.method == "central_difference":
            self._record_cd(raw)
            return
        st, pv = self._finish(raw), self.prev
        du = st["u"] - pv["u"]
        self.E["input"] += float(np.sum(0.5 * (st["P"] + pv["P"]) * du))
        self.E["damping"] += float(np.sum(0.5 * (st["fd"] + pv["fd"]) * du))
        self.E["work"] += float(np.sum(0.5 * (st["fs"] + pv["fs"]) * du))
        for e, (F, D) in st["ele"].items():
            F0, D0 = pv["ele"][e]
            self.ele_work[e] += float(np.dot(0.5 * (F + F0), D - D0))
        st["ek"] = 0.5 * float(np.sum(self.M * st["v"] ** 2))
        self.prev = st

    def _record_cd(self, raw: dict) -> None:
        """Central difference (exact discrete balance of the scheme).

        OpenSees reports ``u_n+1`` with ``v_n, a_n``; equilibrium holds at
        t_n.  Forces at t_n do work on the central increment
        ``(u_n+1 - u_n-1)/2`` and the kinetic energy is the leapfrog
        ``0.5 m ((u_n+1 - u_n)/dt)^2`` — the sums telescope exactly for a
        linear system, so the error measures only nonlinearity/roundoff.
        The sample is reported at t_n.
        """
        pr = self._raw_prev
        h = self.dt
        vc = (raw["u"] - self._u_nm1) / (2.0 * h)
        ac = (raw["u"] - 2.0 * pr["u"] + self._u_nm1) / (h * h)
        st = {"t": pr["t"], "u": pr["u"], "fs": pr["fs"], "ele": pr["ele"],
              "P": pr["P"], "v": vc, "a": ac,
              "fd": self._damping_at(vc, raw["v"])}
        st = self._finish(st)
        du = 0.5 * (raw["u"] - self._u_nm1)
        self.E["input"] += float(np.sum(st["P"] * du))
        self.E["damping"] += float(np.sum(st["fd"] * du))
        self.E["work"] += float(np.sum(st["fs"] * du))
        for e, (F, _D) in st["ele"].items():
            dD = 0.5 * (raw["ele"][e][1] - self._D_nm1[e])
            self.ele_work[e] += float(np.dot(F, dD))
        vh = (raw["u"] - pr["u"]) / self.dt
        st["ek"] = 0.5 * float(np.sum(self.M * vh ** 2))
        self._u_nm1 = pr["u"]
        self._D_nm1 = {e: D for e, (_F, D) in pr["ele"].items()}
        self._raw_prev = raw
        self.prev = st

    def sample(self) -> None:
        """Append the current cumulative energies to the series."""
        st = self.prev
        ek = st["ek"]
        eh = 0.0
        for e, (F, _D) in st["ele"].items():
            k0 = self.k0.get(e)
            stored = 0.0
            if k0 is not None:
                stored = float(sum(0.5 * f * f / k for f, k in zip(F, k0)
                                   if k > 0.0))
            eh += self.ele_work[e] - stored
        es = self.E["work"] - eh
        ei, ed = self.E["input"], self.E["damping"]
        s = self.series
        s["t"].append(float(st["t"]))
        s["input"].append(ei)
        s["kinetic"].append(ek)
        s["strain"].append(es)
        s["damping"].append(ed)
        s["hysteretic"].append(eh)
        s["error"].append(ei - (ek + es + ed + eh))

    def to_dict(self) -> dict:
        s = self.series
        d = {k: [float(x) for x in s[k]] for k in ENERGY_KEYS
             if k != "error_normalized"}
        # normalised by the largest energy magnitude of the whole run
        scale = 0.0
        for i in range(len(s["input"])):
            scale = max(scale, abs(s["input"][i]),
                        abs(s["kinetic"][i]) + abs(s["strain"][i])
                        + abs(s["damping"][i]) + abs(s["hysteretic"][i]))
        d["error_normalized"] = [float(e / scale) if scale > 0.0 else 0.0
                                 for e in s["error"]]
        d["max_abs_error_normalized"] = max(
            (abs(x) for x in d["error_normalized"]), default=0.0)
        d["normalization"] = float(scale)
        d["units"] = "kN*m"
        d["method"] = (
            "nodal work, relative frame: P = P_grav - M iota ag; "
            + ("central-difference increments (u_n+1 - u_n-1)/2, "
               "leapfrog kinetic, samples at t_n"
               if self.method == "central_difference" else
               "trapezoidal increments 0.5(f_k + f_k+1).du, kinetic "
               "0.5 v'Mv")
            + "; damping = applied Rayleigh forces"
            + (" + modal damping as equilibrium residual"
               if self.modal else "")
            + "; hysteretic = nonlinear-element basic work - F^2/(2 k0); "
            "strain = resisting-force work - hysteretic")
        return d


class DirectIntegrationRun:
    """Runtime driver of the TH options for one ``run_time_history`` call."""

    def __init__(self, engine, th, asm, accel, dt: float,
                 use_newton: bool, modal) -> None:
        self.engine = engine
        self.th = th
        self.asm = asm
        self.accel = accel
        self.dt = float(dt)
        self.use_newton = use_newton
        self.modal = modal
        self.integ = dio.resolve_integration(getattr(th, "integration", None))
        self.damp = dio.resolve_di_damping(
            getattr(th, "di_damping", None),
            default_basis="committed" if use_newton else "current")
        self.solver = dio.resolve_solver(getattr(th, "solver", None))
        self.want_energy = bool(getattr(th, "energy", False))
        self.alpha_m = 0.0
        self.tracker: Optional[EnergyTracker] = None
        self.substeps: List[dict] = []
        self.dt_limit: Optional[float] = None
        self.T_min: Optional[float] = None
        self._level = 0

    # ------------------------------------------------------------ setup
    def check_stability(self) -> None:
        """Conditionally stable schemes: dt < dt_crit from T_min (eigen of
        ALL massed dofs of the built domain)."""
        if not dio.needs_stability_check(self.integ):
            return
        asm = self.asm
        n_massed = asm.free_massed_dofs()
        if n_massed < 1:
            raise ValueError("explicit integration needs mass on the "
                             "free dofs")
        self.engine._setup_analysis(asm)
        lams = self.engine._solve_eigen(n_massed, n_massed, asm)
        if self.integ["method"] == "central_difference":
            # explicit scheme: M/dt^2 (+ C/2dt) is the system matrix, so a
            # massless free equation is singular or violently unstable
            n_eq = int(ops.systemSize())
            massed_eq = set()
            for (t, d), m in asm.mass_map.items():
                if m <= 0.0:
                    continue
                eqs = ops.nodeDOFs(t)
                if d - 1 < len(eqs) and eqs[d - 1] >= 0:
                    massed_eq.add(int(eqs[d - 1]))
            if len(massed_eq) < n_eq:
                raise ValueError(
                    f"central_difference needs mass on every free "
                    f"equation: {n_eq - len(massed_eq)} of {n_eq} "
                    "equations are massless (e.g. member-end rotations); "
                    "use newmark/hht/wilson for this model")
        lam_max = max(lam for lam in lams if math.isfinite(lam))
        self.T_min = 2.0 * math.pi / math.sqrt(lam_max)
        self.dt_limit = dio.check_stability(self.integ, self.dt, self.T_min)

    def apply_damping(self, legacy_a0: float) -> bool:
        """Apply ``di_damping`` (True) or leave the legacy block (False)."""
        if self.damp is None:
            if getattr(self.th, "damping_model", "rayleigh") != "modal":
                self.alpha_m = legacy_a0
            return False
        a0, a1 = self.damp["a0"], self.damp["a1"]
        ops.rayleigh(*dio.rayleigh_ops_args(a0, a1, self.damp["stiffness"]))
        self.alpha_m = a0
        return True

    def apply_solution(self) -> None:
        """test / algorithm / integrator (replaces the legacy lines)."""
        sv = self.solver
        explicit = self.integ["method"] == "central_difference"
        if self.use_newton and not explicit:
            ops.test(sv["test"], sv["tolerance"], sv["max_iterations"])
            ops.algorithm("Newton")
        else:
            ops.algorithm("Linear")
        ops.integrator(*dio.integrator_args(self.integ))

    def begin(self, dof: int, scale: float, ext=None) -> None:
        if not self.want_energy:
            return
        nl: Dict[int, str] = {e: "hinge" for e in self.asm.hinge_ele.values()}
        for uh in getattr(self.asm, "user_hinges", []):   # B10
            nl[uh["ele"]] = "hinge"
        link_types = {lk.uid: getattr(lk, "link_type", "elastic")
                      for lk in self.engine.model.links}
        for uid, e in self.asm.link_ele.items():
            if link_types.get(uid, "elastic") != "elastic":
                nl[e] = "link"
        modal = (self.damp is None and getattr(self.th, "damping_model",
                                                "rayleigh") == "modal")
        self.tracker = EnergyTracker(
            self.asm, dof, self.accel, self.dt, scale, self.alpha_m, modal,
            nl, method=self.integ["method"])
        self.tracker.ext = ext
        self.tracker.begin()

    # ------------------------------------------------------------ stepping
    def _analyze(self, h: float) -> int:
        ok = ops.analyze(1, h)
        if ok != 0 and self.use_newton \
                and self.integ["method"] != "central_difference":
            ops.algorithm("NewtonLineSearch")
            ok = ops.analyze(1, h)
            ops.algorithm("Newton")
        if ok == 0 and self.tracker is not None:
            self.tracker.record()
        return ok

    def _solve(self, h: float, level: int) -> int:
        ok = self._analyze(h)
        if ok == 0:
            return 0
        if (level >= self.solver["max_halvings"]
                or self.integ["method"] == "central_difference"):
            return ok
        self._level = max(self._level, level + 1)
        for _ in range(2):
            ok = self._solve(h / 2.0, level + 1)
            if ok != 0:
                return ok
        return 0

    def step(self, k: int) -> int:
        """Advance one record step (with the substep fallback)."""
        self._level = 0
        ok = self._solve(self.dt, 0)
        if ok == 0 and self._level > 0:
            self.substeps.append({"step": k + 1,
                                  "t": (k + 1) * self.dt,
                                  "halvings": self._level})
        if ok != 0 and self.integ["method"] == "central_difference":
            raise ValueError(
                "central_difference: the explicit step failed (singular "
                "M/dt^2 + C/(2 dt)) — every free dof needs mass (or "
                "stiffness-proportional damping); use an implicit method")
        if ok == 0 and self.tracker is not None:
            self.tracker.sample()
        return ok

    # ------------------------------------------------------------ output
    def results(self) -> dict:
        info = {"integration": dict(self.integ),
                "solver": dict(self.solver),
                "substepped_steps": list(self.substeps)}
        if self.damp is not None:
            dd = dict(self.damp)
            if self.modal is not None and self.modal.periods:
                dd["ratio_at_modes"] = [
                    dio.rayleigh_ratio(dd["a0"], dd["a1"], T)
                    for T in self.modal.periods]
            info["damping"] = dd
        if self.dt_limit is not None:
            info["dt_limit"] = self.dt_limit
            info["T_min"] = self.T_min
        out = {"direct_integration": info}
        if self.tracker is not None:
            out["energy"] = self.tracker.to_dict()
        return out


def make_run(engine, th, asm, accel, dt, use_newton,
             modal) -> Optional[DirectIntegrationRun]:
    """A driver when the case uses any option, else ``None`` (legacy)."""
    if not dio.has_options(th):
        return None
    return DirectIntegrationRun(engine, th, asm, accel, dt, use_newton, modal)
