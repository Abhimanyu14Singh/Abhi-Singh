/* SkyFrame mock (?mock=1) — extended load combinations (RS / TH / staged /
   nested members; Envelope / Absolute / SRSS / Range types), model-wide
   P-Delta summary and multi-component time histories. Mirrors the CONTRACT
   interval rules on the mock result blocks; called once by mockResults()
   after every case block exists. Pure (no DOM). */

import { memberKind, normComboType } from "./combo_refs.js";

const QTY = ["node_disp", "reactions", "base", "member_forces", "story", "member_stations"];
const isLegacy = (m, cb) => {
  const t = normComboType(cb.combo_type);
  return (t === "add" || t === "envelope") &&
    Object.keys(cb.cases || {}).every(n => memberKind(m, n) === "static");
};

/* flatten a result block into Map(path -> number); station x arrays skipped */
function flat(block, keys) {
  const out = new Map();
  const walk = (v, p) => {
    if (typeof v === "number") { out.set(p, v); return; }
    if (Array.isArray(v)) { v.forEach((x, i) => typeof x === "number" && out.set(`${p}|${i}`, x)); return; }
    if (v && typeof v === "object")
      for (const [k, x] of Object.entries(v)) {
        if (k === "x" && p.startsWith("member_stations|")) continue;
        walk(x, p ? `${p}|${k}` : k);
      }
  };
  for (const k of keys) if (block && block[k]) walk(block[k], k);
  return out;
}
/* rebuild a block from a Map using `tpl` (the first member) for shapes */
function unflat(map, tpl, keys) {
  const out = {};
  for (const k of keys) out[k] = JSON.parse(JSON.stringify(tpl[k] || {}));
  const zero = v => {
    if (typeof v === "number") return 0;
    if (Array.isArray(v)) return v.map(zero);
    if (v && typeof v === "object") { for (const kk of Object.keys(v)) if (kk !== "x") v[kk] = zero(v[kk]); return v; }
    return v;
  };
  for (const k of keys) out[k] = zero(out[k]);
  for (const [p, val] of map) {
    const parts = p.split("|");
    let o = out;
    for (let i = 0; i < parts.length - 1; i++) {
      const key = parts[i];
      if (o[key] === undefined) o[key] = /^\d+$/.test(parts[i + 1]) ? [] : {};
      o = o[key];
    }
    o[parts[parts.length - 1]] = val;
  }
  return out;
}

export function mockExtendResults(model, r) {
  const m = model;
  r.combos = r.combos || {};
  const memo = {};
  const storyH = {};
  {
    const ord = r.story_order || [];
    let prev = 0;
    for (const s of ord) { const z = (r.story_elev || {})[s] ?? prev; storyH[s] = Math.max(z - prev, 1e-9); prev = z; }
  }

  /* member -> {hi, lo, th} Maps for factor f */
  function interval(name, f, stack) {
    const k = memberKind(m, name);
    if (k === "static") {
      const c = (r.cases || {})[name]; if (!c) return null;
      const mp = flat(c, QTY); const s = new Map([...mp].map(([p, v]) => [p, f * v]));
      return { hi: s, lo: s, th: false, tpl: c };
    }
    if (k === "staged") {
      const c = (r.staged || {})[name]; if (!c) return null;
      const mp = flat(c, QTY); const s = new Map([...mp].map(([p, v]) => [p, f * v]));
      return { hi: s, lo: s, th: false, tpl: c };
    }
    if (k === "rs" || k === "rs_combo") {
      const c = (r.rs_cases || {})[name]; if (!c) return null;
      const mp = flat(c, QTY), a = Math.abs(f);
      return { hi: new Map([...mp].map(([p, v]) => [p, a * Math.abs(v)])),
               lo: new Map([...mp].map(([p, v]) => [p, -a * Math.abs(v)])), th: false, tpl: c };
    }
    if (k === "th") {
      const t = (r.th_cases || {})[name]; if (!t) return null;
      const hi = new Map(), lo = new Map();
      const put = (p, a, b) => { const x = f * a, y = f * b; hi.set(p, Math.max(x, y)); lo.set(p, Math.min(x, y)); };
      const mm = arr => arr && arr.length ? [Math.min(...arr), Math.max(...arr)] : [0, 0];
      const ord = r.story_order || Object.keys(t.story_ux || {});
      ord.forEach((s, i) => {
        const ux = (t.story_ux || {})[s] || [], uy = (t.story_uy || {})[s] || [];
        const bx = i ? (t.story_ux || {})[ord[i - 1]] || [] : [], by = i ? (t.story_uy || {})[ord[i - 1]] || [] : [];
        const [a1, b1] = mm(ux), [a2, b2] = mm(uy);
        put(`story|${s}|ux`, a1, b1); put(`story|${s}|uy`, a2, b2);
        const dx = ux.map((v, j) => (v - (bx[j] || 0)) / storyH[s]), dy = uy.map((v, j) => (v - (by[j] || 0)) / storyH[s]);
        const [c1, d1] = mm(dx), [c2, d2] = mm(dy);
        put(`story|${s}|drift_x`, c1, d1); put(`story|${s}|drift_y`, c2, d2);
        const pk = ((t.peaks || {}).story || {})[s] || {};
        const frac = (ord.length - i) / ord.length;
        const vx = pk.shear_x ?? (((t.peaks || {}).base || {}).FX || 0) * frac;
        const vy = pk.shear_y ?? (((t.peaks || {}).base || {}).FY || 0) * frac;
        put(`story|${s}|shear_x`, -vx, vx); put(`story|${s}|shear_y`, -vy, vy);
      });
      const [e1, f1] = mm(t.base_FX), [e2, f2] = mm(t.base_FY);
      put("base|FX", e1, f1); put("base|FY", e2, f2);
      const tpl = { story: Object.fromEntries(ord.map(s => [s, { ux: 0, uy: 0, drift_x: 0, drift_y: 0, shear_x: 0, shear_y: 0 }])), base: { FX: 0, FY: 0 } };
      return { hi, lo, th: true, tpl };
    }
    if (k === "combo") {
      const c = evalCombo(name, stack); if (!c) return null;
      if (!c.min) {
        const mp = flat(c, QTY); const s = new Map([...mp].map(([p, v]) => [p, f * v]));
        return { hi: s, lo: s, th: false, tpl: c };
      }
      const mx = flat(c, QTY), mn = flat(c.min, QTY);
      const hi = new Map(), lo = new Map();
      for (const [p, v] of mx) { const w = mn.get(p) ?? v; hi.set(p, Math.max(f * v, f * w)); lo.set(p, Math.min(f * v, f * w)); }
      return { hi, lo, th: !!c._th, tpl: c };
    }
    return null;
  }

  /* flatten nested add combos into leaves */
  function leaves(name, f, out, stack) {
    const cb = (m.combos || {})[name];
    for (const [n, g] of Object.entries(cb.cases || {})) {
      const k = memberKind(m, n);
      const nc = (m.combos || {})[n];
      if (k === "combo" && nc && normComboType(nc.combo_type) === "add" && !stack.has(n)) {
        stack.add(n); leaves(n, f * g, out, stack); stack.delete(n);
      } else out[n] = (out[n] || 0) + f * g;
    }
    return out;
  }

  function evalCombo(name, stack = new Set()) {
    if (memo[name] !== undefined) return memo[name];
    const cb = (m.combos || {})[name];
    if (!cb || stack.has(name)) return null;
    if (isLegacy(m, cb)) return (memo[name] = r.combos[name] || null);
    stack.add(name);
    const type = normComboType(cb.combo_type);
    const members = type === "add" ? leaves(name, 1, {}, new Set([name])) : { ...(cb.cases || {}) };
    const parts = [];
    for (const [n, f] of Object.entries(members)) {
      const iv = interval(n, f, stack);
      if (!iv) { stack.delete(name); return (memo[name] = null); }
      parts.push(iv);
    }
    stack.delete(name);
    if (!parts.length) return (memo[name] = null);
    const single = type === "add" && Object.keys(members).every(n => ["static", "staged"].includes(memberKind(m, n)));
    const tpl = parts.find(p => !p.th)?.tpl || parts[0].tpl;
    const anyTh = parts.some(p => p.th);
    const keys = anyTh ? ["story", "base"] : QTY;
    const paths = new Set();
    for (const p of parts) for (const k of p.hi.keys())
      if (!anyTh || k.startsWith("story|") || k === "base|FX" || k === "base|FY") paths.add(k);
    const mx = new Map(), mn = new Map();
    for (const path of paths) {
      const his = parts.map(p => p.hi.get(path) ?? 0), los = parts.map(p => p.lo.get(path) ?? 0);
      let a, b;
      if (type === "add") { a = his.reduce((s, v) => s + v, 0); b = los.reduce((s, v) => s + v, 0); }
      else if (type === "envelope") { a = Math.max(...his); b = Math.min(...los); }
      else if (type === "abs") { a = his.reduce((s, v, i) => s + Math.max(Math.abs(v), Math.abs(los[i])), 0); b = -a; }
      else if (type === "srss") { a = Math.sqrt(his.reduce((s, v, i) => s + Math.max(Math.abs(v), Math.abs(los[i])) ** 2, 0)); b = -a; }
      else { a = his.reduce((s, v) => s + Math.max(v, 0), 0); b = los.reduce((s, v) => s + Math.min(v, 0), 0); }
      mx.set(path, a); mn.set(path, b);
    }
    let out;
    if (single) {
      out = unflat(mx, tpl, QTY);
      out.member_deflections = {};
    } else {
      const ttpl = anyTh ? parts.find(p => p.th).tpl : tpl;
      out = unflat(mx, ttpl, keys);
      out.min = unflat(mn, ttpl, keys);
      if (anyTh) {
        for (const o of [out, out.min]) {
          o.node_disp = {}; o.reactions = {}; o.member_forces = {}; o.member_stations = {};
          o.base = { FX: o.base.FX || 0, FY: o.base.FY || 0 };
        }
        out.warning = "time-history member(s) record only story and base FX/FY results; " +
          "node_disp, reactions, member_forces and member_stations are omitted";
        Object.defineProperty(out, "_th", { value: true, enumerable: false });
      }
      // station x stays a coordinate
      for (const [u, st] of Object.entries(out.member_stations || {}))
        if (tpl.member_stations && tpl.member_stations[u]) {
          st.x = tpl.member_stations[u].x.slice();
          if (out.min.member_stations[u]) out.min.member_stations[u].x = st.x.slice();
        }
      out.member_deflections = {};
      out.min.member_deflections = {};
    }
    return (memo[name] = out);
  }


  /* ---- P-Delta options summary */
  const po = m.pdelta_options;
  if (po && po.method && po.method !== "none") {
    const own = Object.entries(m.cases || {})
      .filter(([, c]) => c.pdelta || (c.geometric && c.geometric !== "linear")).map(([n]) => n);
    const ord = r.story_order || [];
    if (po.method === "iterative_loads") {
      const n = Math.max(1, Math.min(po.max_iterations ?? 2, 3));
      const rel = Array.from({ length: n - 1 }, (_, i) => 0.018 / Math.pow(40, i + 1));
      r.pdelta = {
        method: "iterative_loads", n_springs: (m.members || []).length,
        iterations: n, converged: !rel.length || rel[rel.length - 1] <= (po.tolerance ?? 1e-3),
        relative_change: rel, load_factors: { ...(po.load_factors || {}) },
      };
    } else {
      const story_P = {};
      const W = Object.values(m.story_masses || {}).reduce((a, b) => a + b, 0) * 9.80665 || 1000 * ord.length;
      ord.forEach((s, i) => { story_P[s] = +(W * (ord.length - i) / Math.max(ord.length, 1)).toFixed(3); });
      r.pdelta = { method: "non_iterative_mass", n_springs: ord.length * 2, story_P };
    }
    if (own.length) r.pdelta.cases_own_geometric = own;
    // geometric stiffness softens lateral response a little (mock: +4 % drift)
    for (const c of Object.values(r.cases || {}))
      for (const st of Object.values(c.story || {})) for (const k of ["ux", "uy", "drift_x", "drift_y"]) if (isFinite(st[k])) st[k] *= 1.04;
  }

  /* ---- multi-component time histories: re-integrate an SDOF per direction */
  for (const [name, tc] of Object.entries(m.th_cases || {})) {
    const comps = tc.components;
    const rec = (r.th_cases || {})[name];
    if (!Array.isArray(comps) || !comps.length || !rec) continue;
    const caseScale = isFinite(tc.scale) ? tc.scale : 1;
    const recOf = c => {
      const fn = c.function ? (m.th_functions || {})[c.function] : (tc.function ? (m.th_functions || {})[tc.function] : null);
      return fn ? { v: fn.values || [], dt: fn.dt || 0.02 } : { v: tc.accel || [], dt: tc.dt || 0.02 };
    };
    const recs = comps.map(recOf);
    const dt = Math.min(...recs.map(x => x.dt));
    let n = 2;
    comps.forEach((c, i) => { n = Math.max(n, Math.ceil(((c.time_shift || 0) + (recs[i].v.length - 1) * recs[i].dt) / dt) + 1); });
    const at = (x, t) => { const k = t / x.dt, i = Math.floor(k); if (i < 0 || i >= x.v.length) return 0; if (i === x.v.length - 1) return x.v[i]; return x.v[i] + (k - i) * (x.v[i + 1] - x.v[i]); };
    const gx = new Array(n).fill(0), gy = new Array(n).fill(0), gz = new Array(n).fill(0);
    const info = [];
    comps.forEach((c, i) => {
      const s = caseScale * (c.scale ?? 1), sh = c.time_shift || 0;
      if (c.pattern !== undefined) {
        // pattern load f(t): mock as an equivalent X shaking of 0.5 m/s² per unit
        for (let k = 0; k < n; k++) gx[k] += 0.5 * s * at(recs[i], k * dt - sh);
        info.push({ pattern: c.pattern, function: c.function, effective_scale: s, time_shift: sh });
        return;
      }
      const th = (c.angle_deg || 0) * Math.PI / 180;
      const cos = c.direction === "UZ" ? [0, 0, 1] : c.direction === "UX" ? [Math.cos(th), Math.sin(th), 0] : [-Math.sin(th), Math.cos(th), 0];
      for (let k = 0; k < n; k++) {
        const a = s * at(recs[i], k * dt - sh);
        gx[k] += cos[0] * a; gy[k] += cos[1] * a; gz[k] += cos[2] * a;
      }
      info.push({ direction: c.direction, function: c.function || tc.function || "", effective_scale: s,
        angle_deg: c.angle_deg || 0, time_shift: sh, cosines: cos.map(v => +v.toFixed(12)) });
    });
    const P = (r.modal && r.modal.periods) || [];
    const sdof = (ag, T) => {
      const om = 2 * Math.PI / T, z = isFinite(tc.damping) ? tc.damping : 0.05;
      const u = new Array(n).fill(0), a0 = 1 / (dt * dt) + z * om / dt;
      for (let i = 1; i < n - 1; i++)
        u[i + 1] = (-ag[i] - (om * om - 2 / (dt * dt)) * u[i] - (1 / (dt * dt) - z * om / dt) * u[i - 1]) / a0;
      return u;
    };
    const ux = sdof(gx, P[0] || 0.6), uy = sdof(gy, P[1] || P[0] || 0.6);
    const ord = r.story_order || [];
    const H = Math.max(...ord.map(s => (r.story_elev || {})[s] || 0), 1);
    const peak = a => a.reduce((s, v) => Math.max(s, Math.abs(v)), 0);
    // base-shear stiffness from the legacy record (kN per m of roof), else a guess
    const top = ord[ord.length - 1];
    const legacyRoof = peak([...((rec.story_ux || {})[top] || []), ...((rec.story_uy || {})[top] || [])]);
    const legacyV = Math.max(peak(rec.base_FX || []), peak(rec.base_FY || []));
    const kV = legacyRoof > 1e-12 ? legacyV / legacyRoof : 2e4;
    const r6 = v => +v.toFixed(6);
    rec.t = Array.from({ length: n }, (_, k) => +((k + 1) * dt).toFixed(6));
    rec.story_ux = {}; rec.story_uy = {}; rec.peaks = { story: {}, base: {} };
    for (const s of ord) {
      const f = Math.sin(Math.PI * ((r.story_elev || {})[s] || 0) / (2 * H));
      rec.story_ux[s] = ux.map(v => r6(v * f));
      rec.story_uy[s] = uy.map(v => r6(v * f));
      rec.peaks.story[s] = { ux: peak(rec.story_ux[s]), uy: peak(rec.story_uy[s]) };
    }
    rec.base_FX = ux.map(v => +(kV * v).toFixed(3));
    rec.base_FY = uy.map(v => +(kV * v).toFixed(3));
    rec.peaks.base = { FX: peak(rec.base_FX), FY: peak(rec.base_FY) };
    const mz = Object.values(m.story_masses || {}).reduce((a, b) => a + b, 0);
    const vert = !!(m.mass_options && m.mass_options.include_vertical);
    rec.multi_component = { dt, n_steps: n, components: info,
      base_FZ: gz.map(a => +((vert ? mz : 0) * a).toFixed(3)) };
    if (gz.some(a => a !== 0) && !vert) {
      const w = `TH ${name}: UZ component requested but include_vertical is false (only explicit nodal mz carry it)`;
      r.warning = r.warning ? `${r.warning}; ${w}` : w;
    }
  }

  // evaluate in model order, keeping the results in model order
  const ordered = {};
  for (const name of Object.keys(m.combos || {})) {
    const cd = evalCombo(name);
    if (cd) ordered[name] = cd;
  }
  r.combos = ordered;
  return r;
}
