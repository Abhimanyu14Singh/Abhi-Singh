/* SkyFrame — Wave C mock (?mock=1): plausible stand-ins for the backend's
   engine/cut_results.py, core/th_output.py, core/th_shells.py and
   core/results_diff.py (CONTRACT "Cut, pier and spandrel forces for every
   case type, TH shell results and Compare Results").

     wcMockAugment(model, r)   time-history cut / pier / spandrel histories
                               (th_cases[*].cut_forces) + Max/Min entries,
                               max/min combo entries (interval rules), RS
                               spandrels, TH shell_forces / animation frames
                               when th_output asks for them. The TH series
                               scale a reference static case by the base
                               shear history (linear stand-in).
     wcMockValidate(model)     th_output validation (same messages).
     wcMockDiff(a, b, o)       results comparison (same row shape).
     wcMockTh*                 TH shell sources for js/mock_shellres.js. */

const CUT_KEYS = ["FX", "FY", "FZ", "MX", "MY", "MZ"];
const SKIP = new Set(["n_members", "n_shells", "n_frames", "length", "warnings", "min"]);

/* ---------------------------------------------------------------- entries */
function flat(e, pre = "", out = {}) {           // numeric leaves of an entry
  for (const [k, v] of Object.entries(e || {})) {
    if (SKIP.has(k)) continue;
    if (v && typeof v === "object" && !Array.isArray(v)) flat(v, pre + k + ".", out);
    else if (typeof v === "number") out[pre + k] = v;
  }
  return out;
}
function unflat(base, hi, lo) {
  const e = JSON.parse(JSON.stringify(base));
  delete e.min;
  const set = (obj, path, v) => { const ks = path.split("."); let o = obj; for (const k of ks.slice(0, -1)) o = o[k] = o[k] || {}; o[ks[ks.length - 1]] = +(+v).toFixed(4) + 0; };
  for (const [p, v] of Object.entries(hi)) set(e, p, v);
  if (lo) { e.min = {}; for (const [p, v] of Object.entries(lo)) set(e.min, p, v); }
  return e;
}
/** Visit every entry of a block: section_cuts[c][cut], piers[c][lab][story], spandrels[c][lab][story]. */
function entries(block, kind, cname) {
  const out = [];
  const by = (block || {})[cname] || {};
  if (kind === "section_cuts") for (const [cut, e] of Object.entries(by)) out.push([[cut], e]);
  else for (const [lab, bs] of Object.entries(by)) for (const [st, e] of Object.entries(bs || {})) out.push([[lab, st], e]);
  return out;
}
function putEntry(block, cname, path, e) {
  let o = block[cname] = block[cname] || {};
  for (const k of path.slice(0, -1)) o = o[k] = o[k] || {};
  o[path[path.length - 1]] = e;
}
const BLOCKS = ["section_cuts", "piers", "spandrels"];

function refStatic(model, r, dir) {
  const want = dir === "Y" ? "FY" : "FX";
  let best = null, bv = 0;
  for (const [n, cd] of Object.entries(r.cases || {})) {
    const v = Math.abs(((cd || {}).base || {})[want] || 0);
    if (v > bv) { bv = v; best = n; }
  }
  return best ? { name: best, base: ((r.cases[best].base || {})[want]) || 1 } : null;
}

/* ---------------------------------------------------------------- augment */
export function wcMockAugment(model, r) {
  if (!r) return r;
  const ths = r.th_cases || {};
  // time histories: histories + Max/Min entries from a reference static case
  for (const [name, th] of Object.entries(ths)) {
    const tc = (model.th_cases || {})[name] || {};
    const dir = tc.direction || "X";
    const ref = refStatic(model, r, dir);
    const series = (dir === "Y" ? th.base_FY : th.base_FX) || [];
    if (!ref || !series.length) continue;
    const s = series.map(v => v / ref.base);       // static base reaction -> history
    const cf = { t: (th.t || []).slice() };
    for (const blk of BLOCKS) {
      if (!r[blk] || !r[blk][ref.name]) continue;
      for (const [path, e] of entries(r[blk], blk, ref.name)) {
        const f = flat(e);
        const hist = {}, hi = {}, lo = {};
        for (const [p, v] of Object.entries(f)) {
          const h = s.map(x => +(x * v).toFixed(4) + 0);
          hist[p] = h; hi[p] = Math.max(...h); lo[p] = Math.min(...h);
        }
        const tgt = cf[blk] = cf[blk] || {};
        let o = tgt;
        for (const k of path.slice(0, -1)) o = o[k] = o[k] || {};
        const leaf = o[path[path.length - 1]] = {};
        for (const [p, h] of Object.entries(hist)) {
          const ks = p.split("."); let q = leaf;
          for (const k of ks.slice(0, -1)) q = q[k] = q[k] || {};
          q[ks[ks.length - 1]] = h;
        }
        if (!(r[blk][name] && getPath(r[blk][name], path))) putEntry(r[blk], name, path, unflat(e, hi, lo));
      }
    }
    if (Object.keys(cf).length > 1) th.cut_forces = cf;
    const req = tc.th_output || null;
    if (req && req.shells && (r.shell_quads || []).length && (r.cases[ref.name] || {}).shell_forces) {
      const sf0 = r.cases[ref.name].shell_forces;
      const n = s.length, every = +req.every || 0;
      const steps = [...new Set([...(req.steps || []).filter(k => k >= 1 && k <= n), ...(every ? Array.from({ length: Math.floor(n / every) }, (_, i) => (i + 1) * every) : [])])].sort((a, b) => a - b);
      const smax = Math.max(...s), smin = Math.min(...s);
      const sc = (v, k) => v.map(x => +(x * k).toFixed(5) + 0);
      const mx = {}, mn = {}, at = {};
      for (const [q, v] of Object.entries(sf0)) {
        mx[q] = v.map(x => +Math.max(x * smax, x * smin).toFixed(5) + 0);
        mn[q] = v.map(x => +Math.min(x * smax, x * smin).toFixed(5) + 0);
      }
      for (const k of steps) { at[String(k)] = {}; for (const [q, v] of Object.entries(sf0)) at[String(k)][q] = sc(v, s[k - 1]); }
      th.shell_forces = { steps, t: steps.map(k => th.t[k - 1]), at, max: mx, min: mn };
    }
    if (req && +req.frames > 0 && (r.cases[ref.name] || {}).node_disp) {
      const n = s.length, nf = Math.min(+req.frames, n);
      const steps = [...new Set(Array.from({ length: nf }, (_, k) => Math.max(1, Math.round(n * (k + 1) / nf))))].sort((a, b) => a - b);
      const nd = {};
      for (const k of steps) { nd[String(k)] = {}; for (const [t, d] of Object.entries(r.cases[ref.name].node_disp)) nd[String(k)][t] = d.map(x => +(x * s[k - 1]).toExponential(6)); }
      th.frames = { steps, t: steps.map(k => th.t[k - 1]), node_disp: nd };
    }
  }
  // RS spandrels (the mock's RS piers / cuts already exist)
  if (r.spandrels) for (const name of Object.keys(r.rs_cases || {})) {
    if (r.spandrels[name]) continue;
    const rc = (model.rs_cases || {})[name] || {};
    const ref = refStatic(model, r, rc.direction === "Y" ? "Y" : "X");
    const rsBase = Math.abs((((r.rs_cases[name] || {}).base) || {})[rc.direction === "Y" ? "FY" : "FX"] || 0);
    if (!ref || !r.spandrels[ref.name]) continue;
    const k = rsBase / Math.abs(ref.base || 1);
    for (const [path, e] of entries(r.spandrels, "spandrels", ref.name)) {
      const f = flat(e), hi = {};
      for (const [p, v] of Object.entries(f)) hi[p] = Math.abs(v) * k;
      putEntry(r.spandrels, name, path, unflat(e, hi, null));
    }
  }
  // max/min combos (interval rules) for entries the mock did not produce
  for (const [name, cd] of Object.entries(r.combos || {})) {
    if (!cd || !cd.min) continue;
    const cb = (model.combos || {})[name];
    if (!cb) continue;
    for (const blk of BLOCKS) {
      if (!r[blk]) continue;
      const anyMember = Object.keys(cb.cases || {}).find(c => r[blk][c]);
      if (!anyMember) continue;
      for (const [path, e] of entries(r[blk], blk, anyMember)) {
        if (r[blk][name] && getPath(r[blk][name], path) && getPath(r[blk][name], path).min) continue;
        const terms = [];
        let ok = true;
        for (const [c, f0] of Object.entries(cb.cases || {})) {
          const f = +f0, me = r[blk][c] && getPath(r[blk][c], path);
          if (!me) { ok = false; break; }
          const v = flat(me), vm = me.min ? flat(me.min) : null;
          const isRs = !!(r.rs_cases || {})[c];
          const hi = {}, lo = {};
          for (const [p, x] of Object.entries(v)) {
            if (isRs) { hi[p] = Math.abs(f * x); lo[p] = -Math.abs(f * x); }
            else if (vm && p in vm) { hi[p] = Math.max(f * x, f * vm[p]); lo[p] = Math.min(f * x, f * vm[p]); }
            else { hi[p] = lo[p] = f * x; }
          }
          terms.push([hi, lo]);
        }
        if (!ok || !terms.length) continue;
        const ct = cb.combo_type || "add";
        const hi = {}, lo = {};
        for (const p of Object.keys(terms[0][0])) {
          const H = terms.map(t => t[0][p]), L = terms.map(t => t[1][p]);
          if (ct === "envelope") { hi[p] = Math.max(...H); lo[p] = Math.min(...L); }
          else if (ct === "abs" || ct === "srss") {
            const m = H.map((h, i) => Math.max(Math.abs(h), Math.abs(L[i])));
            const v = ct === "abs" ? m.reduce((a, b) => a + b, 0) : Math.sqrt(m.reduce((a, b) => a + b * b, 0));
            hi[p] = v; lo[p] = -v;
          } else if (ct === "range") { hi[p] = H.reduce((a, b) => a + Math.max(b, 0), 0); lo[p] = L.reduce((a, b) => a + Math.min(b, 0), 0); }
          else { hi[p] = H.reduce((a, b) => a + b, 0); lo[p] = L.reduce((a, b) => a + b, 0); }
        }
        putEntry(r[blk], name, path, unflat(e, hi, lo));
      }
    }
  }
  return r;
}
function getPath(o, path) { for (const k of path) { if (!o) return null; o = o[k]; } return o || null; }

/* ---------------------------------------------------------------- validate */
export function wcMockValidate(model) {
  for (const [n, th] of Object.entries((model && model.th_cases) || {})) {
    const q = th && th.th_output;
    if (q == null) continue;
    if (typeof q !== "object" || Array.isArray(q)) return `TH case ${n}: th_output must be an object`;
    const bad = Object.keys(q).filter(k => !["shells", "steps", "every", "frames"].includes(k));
    if (bad.length) return `TH case ${n}: th_output: unknown key(s) ${JSON.stringify(bad.sort())}`;
    if (q.steps != null && (!Array.isArray(q.steps) || q.steps.some(s => !Number.isInteger(s) || s < 1)))
      return `TH case ${n}: th_output.steps entries must be integers >= 1`;
    if (q.every != null && (!Number.isInteger(q.every) || q.every < 0)) return `TH case ${n}: th_output.every must be an integer >= 0`;
    if (q.frames != null && (!Number.isInteger(q.frames) || q.frames < 0 || q.frames > 1000)) return `TH case ${n}: th_output.frames must be an integer in [0, 1000]`;
  }
  return "";
}

/* ---------------------------------------------------------------- TH shell sources */
const STEP_RE = /^(.*) @ step (\d+)$/;
export function wcMockThStates(r, name, n, arrOf, Unavailable) {
  const sf = ((r.th_cases || {})[name] || {}).shell_forces;
  if (!sf || !sf.max) throw new Unavailable(`time-history case '${name}' recorded no shell forces (set the case's shell output request)`);
  return [[[arrOf(sf.min, n), arrOf(sf.max, n)]], true];
}
export function wcMockThStepStates(model, r, name, n, arrOf) {
  const m = STEP_RE.exec(name || "");
  if (!m || !(model.th_cases || {})[m[1]]) return null;
  const snap = ((((r.th_cases || {})[m[1]] || {}).shell_forces || {}).at || {})[String(+m[2])];
  if (!snap) return null;
  const a = arrOf(snap, n);
  return [[[a, a]], false];
}
export function wcMockThSourceNames(r) {
  const out = [];
  for (const [n, th] of Object.entries((r && r.th_cases) || {})) {
    if (!th || !th.shell_forces) continue;
    out.push(n, ...(th.shell_forces.steps || []).map(s => `${n} @ step ${s}`));
  }
  return out;
}
export const wcMockThStepKind = (model, name) => {
  const m = STEP_RE.exec(name || "");
  return m && (model.th_cases || {})[m[1]] ? "time_history_step" : null;
};

/* ---------------------------------------------------------------- diff */
const NAME_KEYED = new Set(["cases", "combos", "rs_cases", "th_cases", "pushover", "staged", "buckling", "takedown",
  "section_cuts", "piers", "deflection_checks", "story_stiffness", "irregularity", "story_props", "nonlinear_static",
  "steady_state", "psd", "hyperstatic", "convergence", "spandrels"]);
const CASE_BLOCKS = new Set(["cases", "combos", "rs_cases", "th_cases", "staged", "nonlinear_static", "pushover"]);
const SKIP_TOP = new Set(["model_name", "nodes", "members", "supports", "shell_quads", "story_order", "story_elev", "case_status", "combo_status"]);
const isNum = v => typeof v === "number";
function family(path) {
  return path.map((p, i) => typeof p === "number" ? "#" : ((i === 1 && NAME_KEYED.has(path[0])) || /[\d +-]/.test(p)) ? "*" : p).join(".");
}
function group(fam) {
  const parts = fam.split(".");
  for (let i = parts.length - 1; i >= 0; i--) {
    if (parts[i] === "*") return fam;
    if (parts[i] !== "#") { parts[i] = "*"; break; }
  }
  return parts.join(".");
}
function classify(path) {
  const block = String(path[0] || "");
  const rest = path.slice(1);
  let cs = "";
  if (NAME_KEYED.has(block) && rest.length) cs = String(rest.shift());
  let q = block;
  if (CASE_BLOCKS.has(block) && rest.length) {
    q = String(rest.shift());
    if (q === "min" && rest.length) q = "min." + rest.shift();
  }
  return { block, case: cs, quantity: q, item: rest.join(".") };
}
export function wcMockCatalogue(...rs) {
  const cases = [], qty = [];
  for (const r of rs) for (const [blk, v] of Object.entries(r || {})) {
    if (SKIP_TOP.has(blk)) continue;
    if (NAME_KEYED.has(blk) && v && typeof v === "object") {
      for (const [n, sub] of Object.entries(v)) {
        if (!cases.includes(n)) cases.push(n);
        if (CASE_BLOCKS.has(blk) && sub && typeof sub === "object") { for (const q of Object.keys(sub)) if (q !== "min" && !qty.includes(q)) qty.push(q); }
        else if (!qty.includes(blk)) qty.push(blk);
      }
    } else if (!qty.includes(blk)) qty.push(blk);
  }
  return { cases, quantities: qty };
}
const unwrap = o => {
  for (let i = 0; i < 3 && o && typeof o === "object"; i++) {
    if (o.cases || o.modal || o.rs_cases || o.th_cases) return o;
    o = o.results || (o.data && o.data.results);
  }
  throw new Error("not an analysis-results object (no 'cases' / 'modal' block)");
};
export function wcMockDiff(a, b, o = {}) {
  a = unwrap(a); b = unwrap(b);
  const rtol = o.rtol == null ? 1e-6 : +o.rtol, atol = +(o.atol || 0), maxRows = o.max_rows || 500;
  if (!(rtol >= 0)) throw new Error("rtol must be a finite value >= 0");
  const sc = {};
  const walk = (x, path, fn) => {
    if (Array.isArray(x)) x.forEach((v, i) => walk(v, path.concat(i), fn));
    else if (x && typeof x === "object") for (const [k, v] of Object.entries(x)) walk(v, path.concat(k), fn);
    else fn(path, x);
  };
  walk(a, [], (p, v) => { if (isNum(v) && isFinite(v)) { const f = family(p), g = "~" + group(f); sc[f] = Math.max(sc[f] || 0, Math.abs(v)); sc[g] = Math.max(sc[g] || 0, Math.abs(v)); } });
  const scale = p => { const f = family(p); return Math.max(sc[f] || 0, sc["~" + group(f)] || 0); };
  const rows = [], st = { compared: 0, flagged: 0, worst: 0 };
  const prune = path => {
    if (!path.length) return false;
    const top = String(path[0]);
    if (SKIP_TOP.has(top)) return true;
    if (o.case && !NAME_KEYED.has(top)) return true;
    if (o.case && path.length >= 2 && String(path[1]) !== o.case) return true;
    if (o.quantity && !CASE_BLOCKS.has(top) && top !== o.quantity) return true;
    if (o.quantity && CASE_BLOCKS.has(top) && path.length >= 3) {
      const q = classify(path).quantity;
      if (q !== o.quantity && q !== "min." + o.quantity && String(path[2]) !== "min") return true;
    }
    return false;
  };
  const cnt = x => { let n = 0; walk(x, [], (_, v) => { if (isNum(v)) n++; }); return n; };
  const row = (path, va, vb, status, d = 0, rel = 0) => rows.push({ path: path.join("."), ...classify(path), a: va, b: vb, diff: d, rel, status });
  const rec = (x, y, path) => {
    if (prune(path)) return;
    const ox = x && typeof x === "object" && !Array.isArray(x), oy = y && typeof y === "object" && !Array.isArray(y);
    if (ox && oy) {
      for (const k of Object.keys(x)) {
        if (!(k in y)) { if (!prune(path.concat(k))) { st.flagged++; row(path.concat(k), cnt(x[k]), null, "only_a"); } continue; }
        rec(x[k], y[k], path.concat(k));
      }
      for (const k of Object.keys(y)) if (!(k in x) && !prune(path.concat(k))) { st.flagged++; row(path.concat(k), null, cnt(y[k]), "only_b"); }
      return;
    }
    if (Array.isArray(x) && Array.isArray(y)) {
      if (x.length !== y.length) { st.flagged++; row(path, x.length, y.length, "length"); }
      for (let i = 0; i < Math.min(x.length, y.length); i++) rec(x[i], y[i], path.concat(i));
      return;
    }
    if (isNum(x) && isNum(y)) {
      st.compared++;
      const s = Math.max(Math.abs(x), Math.abs(y), scale(path)), d = Math.abs(x - y), rel = s > 0 ? d / s : 0;
      st.worst = Math.max(st.worst, rel);
      if (d > Math.max(atol, rtol * s)) { st.flagged++; row(path, x, y, "changed", d, rel); }
      return;
    }
    if (ox || oy || Array.isArray(x) || Array.isArray(y) || isNum(x) !== isNum(y)) { st.flagged++; row(path, typeof x, typeof y, "type"); return; }
    if (x !== y) { st.flagged++; row(path, String(x), String(y), "changed"); }
  };
  rec(a, b, []);
  const ord = { changed: 0, type: 1, length: 2, only_a: 3, only_b: 4 };
  rows.sort((p, q) => (ord[p.status] - ord[q.status]) || (q.rel - p.rel) || (p.path < q.path ? -1 : 1));
  const cat = wcMockCatalogue(a, b);
  return { equal: st.flagged === 0, n_compared: st.compared, n_flagged: st.flagged, worst_rel: st.worst, rtol, atol,
    truncated: rows.length > maxRows, rows: rows.slice(0, maxRows), cases: cat.cases, quantities: cat.quantities };
}
