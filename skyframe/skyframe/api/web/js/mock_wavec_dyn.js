/* SkyFrame ?mock=1 — Wave C dynamics results decoration (mirrors the
   backend shapes of CONTRACT "Eigen shift / cutoff / tolerance, modal
   history and FNA case types, TH initial conditions, RS / steady-state
   modal damping"):
     th_cases[n].th_type          (modal / fna typed cases)
     th_cases[n].initial_state    {case, kind}
     rs_cases[n].rs.modal_damping {type, zetas}
     steady_state / psd [n].modal_damping
     modal.eigen                  {shift, cutoff, tolerance, found, ...}
   Plausible values only: zetas from the spec at the mock periods. */

const interp = (xs, ys, x) => {
  if (x <= xs[0]) return ys[0];
  for (let i = 0; i + 1 < xs.length; i++) if (x <= xs[i + 1]) return ys[i] + (ys[i + 1] - ys[i]) * (x - xs[i]) / (xs[i + 1] - xs[i]);
  return ys[ys.length - 1];
};

/** Per-mode ratios of a modal_damping spec (core/wavec_dyn.mode_zetas). */
export function wcMockZetas(md, base, periods, mats) {
  const T = periods;
  let z;
  const t = md.type || "constant";
  if (t === "interpolated_period" || t === "interpolated_frequency") {
    const xs = md.points.map(p => p[0]), zs = md.points.map(p => p[1]);
    z = T.map(p => interp(xs, zs, t === "interpolated_period" ? p : 1 / p));
  } else if (t === "mass_stiffness") {
    let a0 = md.a0 || 0, a1 = md.a1 || 0;
    if (md.periods) {
      const [w1, w2] = md.periods.map(p => 2 * Math.PI / p), [z1, z2] = md.ratios;
      a0 = 2 * w1 * w2 * (z1 * w2 - z2 * w1) / (w2 * w2 - w1 * w1); a1 = 2 * (z2 * w2 - z1 * w1) / (w2 * w2 - w1 * w1);
    }
    z = T.map(p => { const w = 2 * Math.PI / p; return a0 / (2 * w) + a1 * w / 2; });
  } else if (t === "composite") {
    const vals = Object.values(mats || {});
    const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : base;
    z = T.map(() => avg);
  } else z = T.map(() => base);
  for (const [m, v] of Object.entries(md.overrides || {})) if (+m - 1 < z.length) z[+m - 1] = v;
  return z;
}

export function wcMockAugment(model, res) {
  if (!model || !res) return res;
  const periods = ((res.modal || {}).periods || []).slice();
  for (const [n, tc] of Object.entries(model.th_cases || {})) {
    const r = (res.th_cases || {})[n];
    if (!r) continue;
    if (tc.th_type && tc.th_type !== "direct") r.th_type = tc.th_type;
    if (tc.initial_case) r.initial_state = { case: tc.initial_case, kind: (model.staged_cases || {})[tc.initial_case] ? "staged" : "nonlinear_static", mock: true };
  }
  for (const [n, rc] of Object.entries(model.rs_cases || {})) {
    const r = (res.rs_cases || {})[n];
    if (!r || !rc.modal_damping) continue;
    r.rs = Object.assign(r.rs || {}, { modal_damping: { type: rc.modal_damping.type || "constant", zetas: wcMockZetas(rc.modal_damping, rc.damping ?? 0.05, periods, model.material_damping), mock: true } });
  }
  for (const [k, key] of [["steady_state_cases", "steady_state"], ["psd_cases", "psd"]])
    for (const [n, c] of Object.entries(model[k] || {})) {
      const r = (res[key] || {})[n];
      if (r && c.modal_damping) r.modal_damping = { type: c.modal_damping.type || "constant", zetas: wcMockZetas(c.modal_damping, c.damping ?? 0.05, periods, model.material_damping), mock: true };
    }
  const eo = model.eigen_options || {};
  if (res.modal && Object.keys(eo).length) {
    res.modal.eigen = { shift: eo.shift || 0, cutoff: eo.cutoff || 0, tolerance: eo.tolerance || 1e-9, requested: model.num_modes, found: periods.length, iterations: 0, converged: true, mock: true };
  }
  return res;
}
