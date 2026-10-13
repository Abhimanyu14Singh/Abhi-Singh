/* SkyFrame mock — results.extra.solver for the PARDISO solver (Wave E2).
   Mirrors engine/pardiso_solver.attach: emitted only when the model asks
   for "pardiso" (the mock has no size threshold), so default models keep
   their exact mock results. */
export function soMockAugment(model, r) {
  const ao = (model && model.analysis_options) || {};
  if (ao.solver !== "pardiso") return;
  const nodes = Object.keys(r.nodes || {}).length;
  const thr = ao.pardiso_threads;
  r.extra = Object.assign({}, r.extra || {}, {
    solver: {
      requested: "pardiso", used: true, engine: "pardiso", reason: "",
      builds: 1, equations: Math.max(6 * nodes - 6, 0), solves: 1 + Object.keys(r.cases || {}).length,
      rhs: Object.keys(r.cases || {}).length + 12, domain_solves: Object.keys(r.cases || {}).length,
      mtype: 2, threads: typeof thr === "number" ? thr : 4, residual_check: 1e-15,
      timing: { extract: 0.01, replay: 0.01, assemble: 0.02, factor: 0.05, solve: 0.02 },
    },
  });
}
