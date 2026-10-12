/* SkyFrame — ?mock=1 stand-ins for POST /api/member_buckling,
   POST /api/member_buckling/forces and the v1.24 buckling-case extras
   (CONTRACT "Buckling with shells and links, sparse eigen and BucklingFEM").
   Same JSON shapes as the backend; the numbers are the closed forms the
   backend is pinned against (fork LTB Mcr, Euler, plate k = 4 / 0.425) on a
   real mid-line shell mesh, with sinusoidal mode shapes.  Pure functions. */

const SECTION_TYPES = ["I", "I_mono", "channel", "tee", "rect_tube"];
const FORCE_KEYS = ["P", "M3i", "M3j", "M2i", "M2j", "w2"];

function dimsOf(sec) {
  if (sec && SECTION_TYPES.includes(sec.shape) && sec.dims) return [sec.shape, sec.dims];
  // library-like fallback from the drawing depth / width
  const d = (sec && sec.h) || 0.3, bf = (sec && sec.b) || 0.15;
  return ["I", { d, bf, tf: Math.max(0.006, d / 30), tw: Math.max(0.005, d / 45) }];
}

export function mockMemberForces(model, body) {
  const m = (model.members || []).find(x => x.uid === body.member);
  if (!m) throw new Error(`unknown frame member ${JSON.stringify(body.member)}`);
  if (!(model.cases || {})[body.case]) throw new Error(`unknown static case ${JSON.stringify(body.case)}`);
  const L = Math.hypot(...[0, 1, 2].map(k => m.pj[k] - m.pi[k]));
  const beam = m.kind !== "column";
  return { forces: beam ? { P: 0, M3i: -0.06 * 20 * L * L, M3j: -0.06 * 20 * L * L, M2i: 0, M2j: 0, w2: 20 }
                        : { P: -400, M3i: 15, M3j: -12, M2i: 0, M2j: 0, w2: 0 } };
}

export function mockMemberBuckling(model, body) {
  const m = (model.members || []).find(x => x.uid === body.member);
  if (!m) throw new Error(`unknown frame member ${JSON.stringify(body.member)}`);
  const sec = (model.sections || {})[m.section] || {};
  const mat = (model.materials || {})[sec.material] || { E: 2e8, nu: 0.3, name: "?" };
  const [type, g] = dimsOf(sec);
  const L = +body.length || Math.hypot(...[0, 1, 2].map(k => m.pj[k] - m.pi[k]));
  const F = Object.fromEntries(FORCE_KEYS.map(k => [k, +((body.forces || {})[k] || 0)]));
  if (!FORCE_KEYS.some(k => F[k])) throw new Error("no load: give at least one non-zero force");
  const E = +mat.E, nu = +(mat.nu ?? 0.3), G = E / (2 * (1 + nu));
  const d = g.d, bf = g.bf || g.b || g.bf_top, tf = g.tf || g.t || g.tf_top, tw = g.tw || g.t;
  const h0 = d - tf, A = 2 * bf * tf + h0 * tw;
  const I22 = 2 * tf * bf ** 3 / 12, I33 = 2 * bf * tf * (h0 / 2) ** 2 + tw * h0 ** 3 / 12;
  const J = (2 * bf * tf ** 3 + h0 * tw ** 3) / 3, Cw = (tf * bf ** 3 / 12) * h0 * h0 / 2;
  const Mcr = Math.PI / L * Math.sqrt(E * I22 * G * J) * Math.sqrt(1 + Math.PI ** 2 * E * Cw / (G * J * L * L));
  const D = E / (12 * (1 - nu * nu));
  const sigWeb = 4 * Math.PI ** 2 * D * (tw / h0) ** 2;
  const sigFl = 0.425 * Math.PI ** 2 * D * (tf / (bf / 2)) ** 2;
  const Pe = Math.PI ** 2 * E * I22 / L ** 2;
  const nModes = Math.max(1, Math.min(20, Math.round(+body.num_modes || 4)));
  const Mmax = Math.max(Math.abs(F.M3i), Math.abs(F.M3j), Math.abs(F.w2) * L * L / 8);
  const cands = [];
  if (Mmax > 0) for (let n = 1; n <= nModes; n++) cands.push({ lam: Mcr * n * n / Mmax * (1 + 0.02 * (n - 1)), cls: "lateral-torsional", hw: n, gf: 0.99 });
  if (F.P < 0) {
    for (let n = 1; n <= nModes; n++) cands.push({ lam: Pe * n * n / -F.P, cls: "flexural-minor", hw: n, gf: 1 });
    const sloc = Math.min(sigWeb * 1.45, sigFl) / (-F.P / A);
    const hw = Math.max(1, Math.round(L / h0));
    for (let n = 0; n < nModes; n++) cands.push({ lam: sloc * (1 + 0.01 * n), cls: "local", hw: hw + n, gf: 0.2 });
  }
  if (!cands.length) cands.push({ lam: 1e3, cls: "local", hw: 3, gf: 0.2 });
  cands.sort((a, b) => a.lam - b.lam);
  const pick = cands.slice(0, nModes);
  // mesh
  const nWeb = (body.mesh && body.mesh.n_web) || 8, nFl = (body.mesh && body.mesh.n_flange) || 4;
  const nLong = (body.mesh && body.mesh.n_long) || Math.min(60, Math.max(12, Math.ceil(L / (h0 / 4))));
  const sec2 = [];
  for (let i = 0; i <= nWeb; i++) sec2.push([0, -h0 / 2 + h0 * i / nWeb, "w"]);
  for (const y of [h0 / 2, -h0 / 2]) for (let i = 1; i <= nFl; i++) { sec2.push([-bf / 2 * i / nFl, y, "f"]); sec2.push([bf / 2 * i / nFl, y, "f"]); }
  const ns = sec2.length, nodes = {}, quads = [];
  const tag = (ix, i) => ix * ns + i + 1;
  for (let ix = 0; ix <= nLong; ix++) sec2.forEach(([y3, y2], i) => { nodes[String(tag(ix, i))] = [L * ix / nLong, y3, y2]; });
  const idx = (y3, y2) => sec2.findIndex(p => Math.abs(p[0] - y3) < 1e-12 && Math.abs(p[1] - y2) < 1e-12);
  const segs = [];
  for (let i = 0; i < nWeb; i++) segs.push([i, i + 1]);
  for (const y of [h0 / 2, -h0 / 2]) for (const s of [-1, 1]) {
    let prev = idx(0, y);
    for (let i = 1; i <= nFl; i++) { const cur = idx(s * bf / 2 * i / nFl, y); segs.push([prev, cur]); prev = cur; }
  }
  for (let ix = 0; ix < nLong; ix++) for (const [a, b] of segs) quads.push([tag(ix, a), tag(ix + 1, a), tag(ix + 1, b), tag(ix, b)]);
  const shapes = {};
  pick.forEach((md, k) => {
    const sh = {};
    for (let ix = 0; ix <= nLong; ix++) {
      const s = Math.sin(md.hw * Math.PI * ix / nLong);
      sec2.forEach(([y3, y2, role], i) => {
        let u;
        if (md.cls === "local") u = role === "w" ? [0, s * Math.cos(Math.PI * y2 / h0), 0] : [0, 0, 0];
        else if (md.cls === "lateral-torsional") u = [0, s * (1 - y2 / h0), s * 0.5 * y3 / bf];
        else u = [0, s, 0];
        sh[String(tag(ix, i))] = u.map(v => +v.toFixed(6));
      });
    }
    shapes[String(k + 1)] = sh;
  });
  return {
    member: m.uid, section: { name: sec.name || m.section, type, dims: g },
    material: { name: mat.name, E, nu }, length: L, boundary: body.boundary || "fork",
    load_position: body.load_position || "shear_center", forces: F,
    mesh: { n_long: nLong, n_web: nWeb, n_flange: nFl, nodes: Object.keys(nodes).length, shells: quads.length, equations: 6 * Object.keys(nodes).length },
    section_props: { A, xc: 0, yc: 0, I33, I22, J, Cw },
    factors: pick.map(p => p.lam),
    modes: pick.map((p, k) => ({ mode: k + 1, factor: p.lam, class: p.cls, group: p.cls === "local" ? "local" : "global", global_fraction: p.gf, half_waves: p.hw })),
    nodes, quads, shapes,
    references: { P_euler_minor: Pe, P_euler_major: Math.PI ** 2 * E * I33 / L ** 2, Mcr_ltb: Mcr,
      plates: [{ role: "web", b: h0, t: tw, k: 4, sigma_cr: sigWeb }, { role: "flange_top", b: bf / 2, t: tf, k: 0.425, sigma_cr: sigFl }] },
    solver: { solves: 48, converged: true, max_residual: 1e-10 },
    warnings: [],
  };
}

/** v1.24 extras of a "fem" buckling case result (mock.js hook). */
export function mockBucklingCaseExtra(bc) {
  if (!bc || bc.formulation !== "fem") return null;
  return {
    formulation: "fem", include_shells: bc.include_shells !== false, include_links: bc.include_links !== false,
    solver: { system: "Mumps", equations_estimate: 0, solves: 46, converged: true, max_residual: 1e-10 },
    geometric_stiffness: { frame_elements: 0, shell_elements: 0 },
  };
}
