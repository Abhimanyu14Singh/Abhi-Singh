/* SkyFrame Wave H — side-by-side compare: difference fields (pure; node-testable).

   Exact JavaScript mirror of skyframe/core/compare_view.py (pinned to the
   same numbers by tests/test_h_windows.py): B − A of joint displacements,
   reactions and frame stations, joints matched by tag at the same place or
   by coordinates, frame stations by uid with B interpolated onto A's
   stations, flags by the Compare Results rule |d| > max(atol, rtol·S) with
   S the quantity-family scale of run A (core/results_compare.py _scales).
   Used for step / envelope states (the browser holds them) and in ?mock=1;
   the live app asks POST /api/results/compare/fields for case states. */

export const HC_QUANTITIES = ["node_disp", "reactions", "member_stations"];
export const HC_STATION_COMPS = ["N", "V2", "V3", "T", "M2", "M3"];
const NAME_KEYED = new Set(["cases", "combos", "rs_cases", "th_cases", "pushover", "staged", "buckling", "takedown", "section_cuts", "piers",
  "deflection_checks", "story_stiffness", "irregularity", "story_props", "nonlinear_static", "steady_state", "psd", "hyperstatic", "convergence"]);
const fin = v => typeof v === "number" && isFinite(v);

/* ---------------------------------------------------------------- results_compare._family / _group / _scales */
export function hcFamily(path) {
  return path.map((p, i) => {
    if (typeof p === "number") return "#";
    if ((i === 1 && NAME_KEYED.has(path[0])) || /\d/.test(p) || /[ \-+]/.test(p)) return "*";
    return p;
  }).join(".");
}
export function hcGroup(fam) {
  const parts = fam.split(".");
  for (let i = parts.length - 1; i >= 0; i--) {
    if (parts[i] === "*") return fam;
    if (parts[i] !== "#") { parts[i] = "*"; break; }
  }
  return parts.join(".");
}
function walk(obj, path, fn) {
  if (Array.isArray(obj)) obj.forEach((v, i) => walk(v, path.concat(i), fn));
  else if (obj && typeof obj === "object") for (const [k, v] of Object.entries(obj)) walk(v, path.concat(String(k)), fn);
  else fn(path, obj);
}
export function hcScales(ref) {
  if (ref && Object.keys(ref).some(k => NAME_KEYED.has(k))) {          // engine rule incl. the name level
    const sc = new Map();
    walk(ref, [], (path, v) => {
      if (typeof v !== "number" || !isFinite(v)) return;
      const f = hcFamily(path), a = Math.abs(v), g = "~" + hcGroup(f);
      sc.set(f, Math.max(sc.get(f) || 0, a));
      sc.set(g, Math.max(sc.get(g) || 0, a));
    });
    return sc;
  }
  // states: the same families with the key collapse memoised per key string (fast on station blocks)
  const sc = new Map(), coll = new Map(), grp = new Map();
  const rec = (obj, fam) => {
    if (Array.isArray(obj)) { const f2 = fam + ".#"; for (const v of obj) rec(v, f2); return; }
    if (obj && typeof obj === "object") {
      for (const [k, v] of Object.entries(obj)) {
        let c = coll.get(k);
        if (c === undefined) { c = /\d/.test(k) || /[ \-+]/.test(k) ? "*" : k; coll.set(k, c); }
        rec(v, fam ? fam + "." + c : c);
      }
      return;
    }
    if (typeof obj !== "number" || !isFinite(obj)) return;
    const a = Math.abs(obj);
    if (!(sc.get(fam) >= a)) sc.set(fam, a);
    let g = grp.get(fam);
    if (g === undefined) { g = "~" + hcGroup(fam); grp.set(fam, g); }
    if (!(sc.get(g) >= a)) sc.set(g, a);
  };
  for (const [q, v] of Object.entries(ref || {})) rec(v, q);
  return sc;
}
export function hcScale(sc, path) { const f = hcFamily(path); return Math.max(sc.get(f) || 0, sc.get("~" + hcGroup(f)) || 0); }

/* ---------------------------------------------------------------- matching */
const keyOf = (p, tol) => p.slice(0, 3).map(c => Math.round(+c / tol)).join(",");
/** B joint tag → A joint tag. */
export function hcNodeMap(a, b, tol = 1e-6) {
  const na = (a && a.nodes) || {}, nb = (b && b.nodes) || {};
  if (a === b || !Object.keys(na).length || !Object.keys(nb).length) {
    const out = {};
    for (const t of Object.keys(Object.keys(nb).length ? nb : na)) out[t] = t;
    return out;
  }
  const byXyz = new Map();
  for (const [t, p] of Object.entries(na)) { const k = keyOf(p, tol); if (!byXyz.has(k)) byXyz.set(k, String(t)); }
  const out = {};
  for (const [t, p] of Object.entries(nb)) {
    const pa = na[t];
    if (pa && pa.slice(0, 3).every((x, i) => Math.abs(+x - +p[i]) <= tol)) { out[t] = t; continue; }
    const hit = byXyz.get(keyOf(p, tol));
    if (hit != null) out[t] = hit;
  }
  return out;
}
export function hcInterp(xs, vals, x) {
  const n = xs.length;
  if (!n) return 0;
  if (x <= xs[0]) return +vals[0];
  if (x >= xs[n - 1]) return +vals[n - 1];
  for (let i = 1; i < n; i++) {
    if (x <= xs[i]) {
      const x0 = xs[i - 1], x1 = xs[i];
      if (x1 - x0 <= 1e-15) return +vals[i];
      const f = (x - x0) / (x1 - x0);
      return +vals[i - 1] + f * (+vals[i] - +vals[i - 1]);
    }
  }
  return +vals[n - 1];
}
function vecDiff(va, vb) {
  if (!Array.isArray(va) || !Array.isArray(vb)) return null;
  const n = Math.min(va.length, vb.length), out = [];
  for (let i = 0; i < n; i++) out.push(fin(va[i]) && fin(vb[i]) ? +vb[i] - +va[i] : 0);
  return out;
}
function mapDiff(ma = {}, mb = {}, nmap) {
  ma = ma || {}; mb = mb || {};
  const inv = {};
  for (const [tb, ta] of Object.entries(nmap)) if (!(ta in inv)) inv[ta] = tb;
  const out = {}, onlyA = [];
  for (const [ta, va] of Object.entries(ma)) {
    const tb = inv[ta];
    if (tb == null || !(tb in mb)) { onlyA.push(String(ta)); continue; }
    const d = vecDiff(va, mb[tb]);
    if (d) out[ta] = d;
  }
  const mapped = new Set(Object.values(inv));
  const onlyB = Object.keys(mb).filter(t => !mapped.has(t)).sort();
  return [out, onlyA.sort(), onlyB];
}
function stationsDiff(sa = {}, sb = {}) {
  sa = sa || {}; sb = sb || {};
  const out = {};
  for (const [uid, a] of Object.entries(sa)) {
    const b = sb[uid];
    if (!a || !b || typeof a !== "object" || typeof b !== "object") continue;
    const xa = a.x || [], xb = b.x || [];
    const same = xa.length === xb.length && xa.every((p, i) => Math.abs(+p - +xb[i]) <= 1e-9);
    const d = { x: xa.map(Number) };
    for (const c of HC_STATION_COMPS) {
      const ca = a[c], cb = b[c];
      if (!Array.isArray(ca) || !Array.isArray(cb)) continue;
      if (same) d[c] = ca.map((v, i) => +cb[i] - +v);
      else if (xb.length && cb.length === xb.length) d[c] = ca.map((v, i) => hcInterp(xb, cb, +xa[i]) - +v);
    }
    out[uid] = d;
  }
  return [out, Object.keys(sa).filter(u => !(u in sb)).sort(), Object.keys(sb).filter(u => !(u in sa)).sort()];
}

/** B − A of two states (keys of A's joints / members); compare_view.field_difference. */
export function hcFieldDifference(stA, stB, nmap = null, { rtol = 1e-6, atol = 0 } = {}) {
  stA = stA || {}; stB = stB || {};
  if (!nmap) {
    nmap = {};
    for (const t of new Set([...Object.keys(stA.node_disp || {}), ...Object.keys(stB.node_disp || {})])) nmap[t] = t;
  }
  const out = { only_a: {}, only_b: {}, stats: {} };
  for (const q of ["node_disp", "reactions"]) {
    if (stA[q] == null && stB[q] == null) continue;
    const [d, oa, ob] = mapDiff(stA[q], stB[q], nmap);
    out[q] = d; out.only_a[q] = oa; out.only_b[q] = ob;
  }
  if (stA.member_stations != null || stB.member_stations != null) {
    const [d, oa, ob] = stationsDiff(stA.member_stations, stB.member_stations);
    out.member_stations = d; out.only_a.member_stations = oa; out.only_b.member_stations = ob;
  }
  const ref = {};
  for (const q of HC_QUANTITIES) if (stA[q] != null) ref[q] = stA[q];
  const sc = hcScales(ref);
  const memo = new Map();                       // the family depends only on the collapsed keys
  const coll = p => (typeof p === "number" ? 0 : (/\d/.test(p) || /[ \-+]/.test(p) ? "*" : p));
  const scale = path => { const k = path.map((p, i) => (i ? coll(p) : p)).join("\u0001"); let v = memo.get(k); if (v === undefined) { v = hcScale(sc, path); memo.set(k, v); } return v; };
  let nFlag = 0;
  for (const q of ["node_disp", "reactions"]) {
    if (!(q in out)) continue;
    let best = 0, at = null, trans = 0, tat = null, S = 0;
    for (const [t, d] of Object.entries(out[q])) {
      d.forEach((v, i) => {
        const s = scale([q, t, i]);
        S = Math.max(S, s);
        if (Math.abs(v) > Math.max(atol, rtol * s)) nFlag++;
        if (Math.abs(v) > best) { best = Math.abs(v); at = [t, i]; }
      });
      const n3 = Math.sqrt(d.slice(0, 3).reduce((a, v) => a + v * v, 0));
      if (n3 > trans) { trans = n3; tat = t; }
    }
    const st = { max_abs: best, at, scale: S, rel: S > 0 ? best / S : (best === 0 ? 0 : null) };
    if (q === "node_disp") { st.max_trans = trans; st.at_trans = tat; }
    out.stats[q] = st;
  }
  if (out.member_stations) {
    const per = {};
    for (const [uid, d] of Object.entries(out.member_stations)) {
      for (const [c, vals] of Object.entries(d)) {
        if (c === "x") continue;
        const s = scale(["member_stations", uid, c, 0]);
        const e = per[c] || (per[c] = { max_abs: 0, at: null, scale: 0 });
        e.scale = Math.max(e.scale, s);
        vals.forEach((v, k) => {
          if (Math.abs(v) > Math.max(atol, rtol * s)) nFlag++;
          if (Math.abs(v) > e.max_abs) { e.max_abs = Math.abs(v); e.at = [uid, d.x[k]]; }
        });
      }
    }
    for (const e of Object.values(per)) e.rel = e.scale > 0 ? e.max_abs / e.scale : (e.max_abs === 0 ? 0 : null);
    out.stats.member_stations = per;
  }
  out.n_flagged = nFlag; out.rtol = rtol; out.atol = atol; out.equal = nFlag === 0;
  return out;
}

/** A results state of `a` re-keyed into A's joint tags (for showing run B on run A's geometry). */
export function hcRekey(st, nmap) {
  if (!st) return st;
  const out = { ...st };
  for (const q of ["node_disp", "reactions"]) {
    if (!st[q]) continue;
    const m = {};
    for (const [tb, v] of Object.entries(st[q])) { const ta = nmap[tb]; if (ta != null) m[ta] = v; }
    out[q] = m;
  }
  return out;
}

/** A Wave G display state for the difference window (A's state with B − A fields). */
export function hcDiffState(stA, diff, labelA, labelB) {
  if (!stA || !diff) return null;
  return { ...stA, node_disp: diff.node_disp || null, reactions: diff.reactions || null, member_stations: diff.member_stations || null,
    member_stations_min: null, node_disp_max: null, node_disp_min: null, reactions_min: null, shell_forces: null, shell_forces_min: null,
    piers: null, spandrels: null, member_forces: null, env: "step", signed: true, animate: false,
    label: `Δ ${labelB} − ${labelA}`, stepLabel: "", note: "", hDiff: true };
}
