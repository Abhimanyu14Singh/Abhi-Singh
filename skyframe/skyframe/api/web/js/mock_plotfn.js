/* SkyFrame — mock backend for Plot Functions (?mock=1 / backend offline).

   Mirrors CONTRACT "Plot functions, floor response spectra and story
   response plots":
     pfMockValidate(model)              → error string | null for
                                          th_cases[*].output_requests
     pfMockExtend(model, results)       → adds th_cases[name].plot_functions
                                          for every TH case that requests
                                          outputs (plausible series built on
                                          the mock SDOF story response)
     pfMockSpectrum(model, results, b)  → POST /api/plotfn/spectrum mirror
                                          (exact Nigam–Jennings recurrence,
                                          same JSON as the backend)
   Pure functions: never mutate the model. Everything SI. */

const KEYS = ["joints", "links", "frames", "hinges"];
const DOF6 = ["UX", "UY", "UZ", "RX", "RY", "RZ"];
const FRAME_KEYS = ["P_i", "V2_i", "V3_i", "T_i", "M2_i", "M3_i",
  "P_j", "V2_j", "V3_j", "T_j", "M2_j", "M3_j"];
const isNum = v => typeof v === "number" && isFinite(v);

/* ------------------------------------------------ validation */
export function pfMockValidate(model) {
  if (!model || typeof model !== "object") return null;
  const links = new Set((model.links || []).map(l => l.uid));
  const mems = new Set((model.members || []).map(m => m.uid));
  for (const [name, th] of Object.entries(model.th_cases || {})) {
    const req = th && th.output_requests;
    if (req === undefined || req === null) continue;
    const pre = `TH case ${name}: `;
    if (typeof req !== "object" || Array.isArray(req)) return pre + "output_requests must be an object";
    const bad = Object.keys(req).filter(k => !KEYS.includes(k));
    if (bad.length) return pre + `output_requests: unknown key(s) ${bad.join(", ")}`;
    if (req.joints !== undefined) {
      if (!Array.isArray(req.joints)) return pre + "output_requests.joints must be a list of [x, y, z] points";
      for (const p of req.joints)
        if (!Array.isArray(p) || p.length !== 3 || !p.every(isNum))
          return pre + "output_requests.joints entries must be [x, y, z]";
    }
    for (const [key, set, what] of [["links", links, "link"], ["frames", mems, "member"]]) {
      if (req[key] === undefined) continue;
      if (!Array.isArray(req[key]) || !req[key].every(u => typeof u === "string" && u))
        return pre + `output_requests.${key} must be a list of uid strings`;
      const miss = req[key].find(u => !set.has(u));
      if (miss) return pre + `output_requests.${key} references unknown ${what} '${miss}'`;
    }
    if (req.hinges !== undefined && typeof req.hinges !== "boolean")
      return pre + "output_requests.hinges must be true/false";
  }
  return null;
}

/* ------------------------------------------------ kinematics */
export function integrateLinearAccel(a, dt) {
  const n = a.length, v = new Array(n).fill(0), u = new Array(n).fill(0);
  for (let i = 0; i < n - 1; i++) {
    v[i + 1] = v[i] + 0.5 * dt * (a[i] + a[i + 1]);
    u[i + 1] = u[i] + dt * v[i] + dt * dt * (2 * a[i] + a[i + 1]) / 6;
  }
  return { v, u };
}

/* ------------------------------------------------ Nigam–Jennings */
function njCoeffs(w, z, h) {
  const k = w * w, sq = Math.sqrt(1 - z * z), wd = w * sq;
  const e = Math.exp(-z * w * h), s = Math.sin(wd * h), c = Math.cos(wd * h), zs = z / sq;
  return {
    A: e * (zs * s + c),
    B: e * (s / wd),
    C: (2 * z / (w * h) + e * (((1 - 2 * z * z) / (wd * h) - zs) * s - (1 + 2 * z / (w * h)) * c)) / k,
    D: (1 - 2 * z / (w * h) + e * ((2 * z * z - 1) / (wd * h) * s + 2 * z / (w * h) * c)) / k,
    Ap: -e * (w / sq * s),
    Bp: e * (c - zs * s),
    Cp: (-1 / h + e * ((w / sq + z / (h * sq)) * s + c / h)) / k,
    Dp: (1 - e * (zs * s + c)) / (k * h),
  };
}

export const DEFAULT_PERIODS = [0].concat(Array.from({ length: 99 }, (_, i) =>
  Math.pow(10, Math.log10(0.02) + i * (Math.log10(5) - Math.log10(0.02)) / 98)));

export function responseSpectrum(acc, dt, periods, damping) {
  if (!(isNum(dt) && dt > 0)) throw new Error("dt must be > 0");
  if (!Array.isArray(acc) || acc.length < 2) throw new Error("acceleration series needs >= 2 samples");
  const Ts = periods || DEFAULT_PERIODS;
  if (!Ts.length || !Ts.every(T => isNum(T) && T >= 0)) throw new Error("periods must be finite values >= 0");
  if (!damping.length || !damping.every(z => isNum(z) && z >= 0 && z < 1))
    throw new Error("damping ratios must be in [0, 1)");
  const pga = acc.reduce((m, a) => Math.max(m, Math.abs(a)), 0);
  const spectra = damping.map(z => {
    const Sd = [], Sv = [], Sa = [], PSv = [], PSa = [];
    for (const T of Ts) {
      if (T <= 0) { Sd.push(0); Sv.push(0); Sa.push(pga); PSv.push(0); PSa.push(pga); continue; }
      const w = 2 * Math.PI / T, c = njCoeffs(w, z, dt);
      let u = 0, v = 0, mu = 0, mv = 0, ma = 0;
      for (let i = 0; i < acc.length - 1; i++) {
        const p0 = -acc[i], p1 = -acc[i + 1];
        const un = c.A * u + c.B * v + c.C * p0 + c.D * p1;
        const vn = c.Ap * u + c.Bp * v + c.Cp * p0 + c.Dp * p1;
        u = un; v = vn;
        mu = Math.max(mu, Math.abs(u)); mv = Math.max(mv, Math.abs(v));
        ma = Math.max(ma, Math.abs(2 * z * w * v + w * w * u));
      }
      Sd.push(mu); Sv.push(mv); Sa.push(ma); PSv.push(w * mu); PSa.push(w * w * mu);
    }
    return { damping: z, Sd, Sv, Sa, PSv, PSa };
  });
  return { periods: Ts.slice(), spectra, pga, method: "Nigam-Jennings exact piecewise-linear recurrence" };
}

/* ------------------------------------------------ plot_functions mock */
function storyProfile(results) {
  const order = results.story_order || [];
  const elev = results.story_elev || {};
  return order.map(s => ({ name: s, z: +elev[s] || 0 }));
}
/** relative series at height z: linear interpolation of the story series. */
function seriesAtZ(stories, ser, z, n) {
  const pts = [{ z: 0, v: null }].concat(stories.map(s => ({ z: s.z, v: ser[s.name] })));
  const zero = new Array(n).fill(0);
  const get = p => p.v || zero;
  if (!stories.length) return zero;
  if (z <= 0) return zero;
  for (let i = 1; i < pts.length; i++) {
    if (z <= pts[i].z + 1e-9) {
      const a = pts[i - 1], b = pts[i];
      const t = (z - a.z) / ((b.z - a.z) || 1);
      const va = get(a), vb = get(b);
      return vb.map((x, k) => (va[k] || 0) + t * (x - (va[k] || 0)));
    }
  }
  return get(pts[pts.length - 1]).slice();
}
const deriv = (u, dt) => u.map((x, i) => {
  if (u.length < 2) return 0;
  if (i === 0) return (u[1] - u[0]) / dt;
  if (i === u.length - 1) return (u[i] - u[i - 1]) / dt;
  return (u[i + 1] - u[i - 1]) / (2 * dt);
});

function bilinear(d, k1, k2, Fy) {
  // kinematic-hardening bilinear (return mapping), returns forces
  const H = k2 >= k1 ? 0 : k1 * k2 / (k1 - k2);
  let up = 0, alpha = 0;
  return d.map(x => {
    const ftr = k1 * (x - up), xi = ftr - alpha;
    if (Math.abs(xi) <= Fy) return ftr;
    const dg = (Math.abs(xi) - Fy) / (k1 + H), sg = Math.sign(xi);
    up += dg * sg; alpha += H * dg * sg;
    return k1 * (x - up);
  });
}

export function pfMockExtend(model, r) {
  if (!model || !r || !r.th_cases) return r;
  const stories = storyProfile(r);
  for (const [name, res] of Object.entries(r.th_cases)) {
    const th = (model.th_cases || {})[name];
    const req = th && th.output_requests;
    if (!req || typeof req !== "object") continue;
    const dt = res.t && res.t.length > 1 ? res.t[1] - res.t[0] : (th.dt || 0.02);
    const n = (res.t || []).length;
    const t = Array.from({ length: n + 1 }, (_, i) => +(i * dt).toFixed(6));
    const scale = isNum(th.scale) ? th.scale : 1;
    const rec = (Array.isArray(th.accel) ? th.accel : []).map(a => a * scale);
    const agDir = Array.from({ length: n + 1 }, (_, i) => rec[i] || 0);
    const zero = new Array(n + 1).fill(0);
    const dirKey = th.direction === "Y" ? "UY" : "UX";
    const g = { acc: {}, vel: {}, disp: {} };
    for (const k of ["UX", "UY", "UZ"]) {
      const a = k === dirKey ? agDir : zero.slice();
      const { v, u } = integrateLinearAccel(a, dt);
      g.acc[k] = a; g.vel[k] = v; g.disp[k] = u;
    }
    const pad = ser => { const o = {}; for (const [s, v] of Object.entries(ser || {})) o[s] = [0].concat(v); return o; };
    const sux = pad(res.story_ux), suy = pad(res.story_uy);
    const joints = (req.joints || []).map((p, ji) => {
      const ux = seriesAtZ(stories, sux, p[2], n + 1), uy = seriesAtZ(stories, suy, p[2], n + 1);
      const disp = { UX: ux, UY: uy, UZ: zero.slice(), RX: zero.slice(), RY: zero.slice(), RZ: zero.slice() };
      const vel = {}, acc = {};
      for (const k of DOF6) { vel[k] = deriv(disp[k], dt); acc[k] = deriv(vel[k], dt); }
      const abs = (rel, grp) => ({ UX: rel.UX.map((x, i) => x + g[grp].UX[i]),
        UY: rel.UY.map((x, i) => x + g[grp].UY[i]), UZ: rel.UZ.map((x, i) => x + g[grp].UZ[i]) });
      return { point: p.map(Number), node: 9000 + ji, disp, vel, acc,
        disp_abs: abs(disp, "disp"), vel_abs: abs(vel, "vel"), acc_abs: abs(acc, "acc") };
    });
    const links = {};
    for (const uid of req.links || []) {
      const lk = (model.links || []).find(l => l.uid === uid);
      if (!lk) continue;
      const prm = lk.params || {};
      const zi = Math.min(lk.pi[2], lk.pj[2]), zj = Math.max(lk.pi[2], lk.pj[2]);
      const pick = th.direction === "Y" ? suy : sux;
      let d = seriesAtZ(stories, pick, zj, n + 1).map((x, i) => x - seriesAtZ(stories, pick, zi, n + 1)[i]);
      if (Math.max(...d.map(Math.abs)) < 1e-9)          // horizontal link: drift-scaled
        d = seriesAtZ(stories, pick, zj, n + 1).map(x => 0.15 * x);
      let F;
      if (lk.link_type === "isolator") F = bilinear(d, +prm.k1 || 1e4, +prm.k2 || 1e3, +prm.Fy || 50);
      else if (lk.link_type === "damper") {
        const v = deriv(d, dt), a = +prm.alpha || 1;
        F = v.map(x => (+prm.cd || 100) * Math.sign(x) * Math.pow(Math.abs(x), a));
      } else F = d.map(x => x * ((lk.stiffness && lk.stiffness[0]) || prm.k || 1e4));
      const comps = lk.link_type === "elastic" ? ["U1"] : ["P", "V2", "V3"];
      const sh = comps.length > 1 ? (th.direction === "Y" ? "V3" : "V2") : "U1";
      const deformation = {}, force = {};
      for (const c of comps) { deformation[c] = c === sh ? d : zero.slice(); force[c] = c === sh ? F : zero.slice(); }
      const work = [0];
      for (let i = 1; i <= n; i++) work.push(work[i - 1] + 0.5 * (F[i] + F[i - 1]) * (d[i] - d[i - 1]));
      links[uid] = { type: lk.link_type || "elastic", components: comps, deformation, force, work };
    }
    const frames = {};
    const base = [0].concat(th.direction === "Y" ? (res.base_FY || []) : (res.base_FX || []));
    const cols = (model.members || []).filter(m => m.kind === "column").length || 1;
    for (const uid of req.frames || []) {
      const mm = (model.members || []).find(m => m.uid === uid);
      if (!mm) continue;
      const L = Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1], mm.pj[2] - mm.pi[2]) || 1;
      const V = base.map(x => (x || 0) / cols);
      const row = { P_i: zero.slice(), V2_i: V, V3_i: zero.slice(), T_i: zero.slice(), M2_i: zero.slice(),
        M3_i: V.map(x => 0.5 * x * L), P_j: zero.slice(), V2_j: V.map(x => -x), V3_j: zero.slice(),
        T_j: zero.slice(), M2_j: zero.slice(), M3_j: V.map(x => 0.5 * x * L) };
      frames[uid] = {};
      for (const k of FRAME_KEYS) frames[uid][k] = row[k];
    }
    const pf = { t, ground: g, joints, links, frames,
      units: { disp: "m", rot: "rad", vel: "m/s", acc: "m/s^2", force: "kN", moment: "kN*m", work: "kN*m" } };
    if (req.hinges) pf.hinges = {};
    res.plot_functions = pf;
  }
  return r;
}

/* ------------------------------------------------ POST /api/plotfn/spectrum */
export function pfMockSpectrum(model, results, body) {
  const b = body || {};
  let damping = b.damping === undefined ? [0.05] : (Array.isArray(b.damping) ? b.damping : [b.damping]);
  if (!damping.length || !damping.every(isNum)) throw new Error("'damping' must be a ratio or a non-empty list of ratios");
  if (b.accel) {
    const out = responseSpectrum(b.accel, b.dt, b.periods, damping);
    return Object.assign(out, { source: "series", dt: b.dt });
  }
  const th = (model.th_cases || {})[b.case];
  if (!th) throw new Error("'case' must name a time-history case");
  const dir = b.direction || th.direction || "X";
  if (!["X", "Y", "Z"].includes(dir)) throw new Error("'direction' must be X|Y|Z");
  const key = { X: "UX", Y: "UY", Z: "UZ" }[dir];
  let res = results && results.th_cases && results.th_cases[b.case];
  let pf = res && res.plot_functions, source = "recorded";
  const point = b.point === undefined ? "ground" : b.point;
  const find = p => p && (point === "ground" ? p.ground.acc[key]
    : ((p.joints || []).find(j => j.point.every((c, i) => Math.abs(c - point[i]) < 1e-6)) || {}).acc_abs);
  let acc = find(pf);
  if (acc && point !== "ground") acc = acc[key];
  if (!acc) {
    if (!res) throw new Error("run the analysis first");
    const m2 = JSON.parse(JSON.stringify(model));
    const t2 = m2.th_cases[b.case];
    const req = Object.assign({}, t2.output_requests || {});
    if (point !== "ground") req.joints = (req.joints || []).concat([point.map(Number)]);
    t2.output_requests = req;
    const r2 = JSON.parse(JSON.stringify(results));
    delete r2.th_cases[b.case].plot_functions;
    pfMockExtend(m2, r2);
    pf = r2.th_cases[b.case].plot_functions;
    acc = find(pf);
    if (acc && point !== "ground") acc = acc[key];
    source = "rerun";
  }
  const dt = pf.t[1] - pf.t[0];
  const out = responseSpectrum(acc, dt, b.periods, damping);
  return Object.assign(out, { case: b.case, point, direction: dir, dt, source });
}
