/* SkyFrame — ?mock=1 stand-in for POST /api/explain (Tools › Explain
   Result…; CONTRACT "Load-path explainer").  Same response shape as
   core/load_path.py:

   * static case  — per pattern from stored single-pattern cases; other
     patterns from a mock solve of a one-pattern case (mockResults), then
     scaled so the rows add up to the stored case value (mock only);
   * combination  — the interval rules of combos_ext (add / envelope / abs /
     srss / range; RS members ±|f|·R; nested add combos flattened);
   * RS           — synthetic per-mode values from the modal mass
     participation (SRSS-consistent with the stored value);
   * TH           — governing step of the recorded story / base series. */

import { mockResults as exMockResults } from "./mock.js";

export const EX_COMPONENTS = {
  reaction: ["FX", "FY", "FZ", "MX", "MY", "MZ"],
  joint_disp: ["UX", "UY", "UZ", "RX", "RY", "RZ"],
  member_force: ["P", "V2", "V3", "T", "M2", "M3"],
  story: ["ux", "uy", "drift_x", "drift_y", "shear_x", "shear_y"],
  base: ["FX", "FY", "FZ", "MX", "MY", "MZ"],
  pier: ["P", "V", "M"],
};
const SKEY = { P: "N", V2: "V2", V3: "V3", T: "T", M2: "M2", M3: "M3" };

export function exUnitKind(t) {
  const k = t.kind, c = t.component;
  if (k === "reaction" || k === "base") return c[0] === "F" ? "force" : "moment";
  if (k === "joint_disp") return c[0] === "U" ? "disp" : "rotation";
  if (k === "member_force") return ["P", "V2", "V3"].includes(c) ? "force" : "moment";
  if (k === "story") return /^u[xy]$/.test(c) ? "disp" : /^shear/.test(c) ? "force" : "none";
  if (k === "pier") return c === "M" ? "moment" : "force";
  return "none";
}
export function exLabel(t) {
  const k = t.kind, c = t.component;
  if (k === "reaction") return `Reaction ${c} at joint ${t.joint}`;
  if (k === "joint_disp") return `Displacement ${c} of joint ${t.joint}`;
  if (k === "member_force") { const s = +t.station; return `Frame ${t.member} ${c} at ${s === 0 ? "end I" : s === 1 ? "end J" : `station ${s}·L`}`; }
  if (k === "story") return `Story ${t.story} ${c.replace("_", " ")}`;
  if (k === "base") return `Base reaction ${c}`;
  return `Pier ${t.pier} ${c} at ${t.story}`;
}
export function exNormalize(t) {
  if (!t || !EX_COMPONENTS[t.kind]) throw new Error("target kind must be one of " + Object.keys(EX_COMPONENTS).join(", "));
  const comp = EX_COMPONENTS[t.kind].find(c => c.toLowerCase() === String(t.component || "").toLowerCase());
  if (!comp) throw new Error(`${t.kind} component must be one of ${EX_COMPONENTS[t.kind].join(", ")}`);
  const o = { kind: t.kind, component: comp };
  if (t.kind === "reaction" || t.kind === "joint_disp") { if (t.joint == null || String(t.joint).trim() === "") throw new Error(`${t.kind} target needs 'joint' (node tag)`); o.joint = String(t.joint).trim(); }
  if (t.kind === "member_force") {
    if (!t.member) throw new Error("member_force target needs 'member' (uid)");
    o.member = String(t.member);
    let s = t.station ?? 0; if (s === "i") s = 0; if (s === "j") s = 1; s = +s;
    if (!(s >= 0 && s <= 1)) throw new Error("station must be in [0, 1]");
    o.station = s;
  }
  if (t.kind === "story") { if (!t.story) throw new Error("story target needs 'story'"); o.story = String(t.story); }
  if (t.kind === "pier") { if (!t.pier || !t.story) throw new Error("pier target needs 'pier' and 'story'"); o.pier = String(t.pier); o.story = String(t.story); }
  return o;
}
const stIdx = (xs, f) => { if (!xs || !xs.length) return 0; const x = xs[0] + f * (xs[xs.length - 1] - xs[0]); let b = 0; xs.forEach((v, i) => { if (Math.abs(v - x) < Math.abs(xs[b] - x)) b = i; }); return b; };
export function exExtract(part, t) {
  const cd = (part && part.case) || {};
  const k = t.kind, c = t.component, idx = EX_COMPONENTS[k].indexOf(c);
  let v = null;
  if (k === "reaction") { const a = (cd.reactions || {})[t.joint]; v = a ? a[idx] : null; }
  else if (k === "joint_disp") { const a = (cd.node_disp || {})[t.joint]; v = a ? a[idx] : null; }
  else if (k === "base") v = (cd.base || {})[c];
  else if (k === "story") v = ((cd.story || {})[t.story] || {})[c];
  else if (k === "member_force") {
    const st = (cd.member_stations || {})[t.member];
    if (st && st[SKEY[c]]) v = st[SKEY[c]][stIdx(st.x, t.station)];
    else { const ef = (cd.member_forces || {})[t.member]; if (ef && (t.station === 0 || t.station === 1)) v = ef[(t.station ? 6 : 0) + idx]; }
  } else if (k === "pier") v = (((part.piers || {})[t.pier] || {})[t.story] || {})[c];
  if (v == null || !isFinite(v)) return null;
  return part.div ? v / part.div : +v;
}
const part = (r, block, n) => { const cd = (r[block] || {})[n]; return cd ? { case: cd, piers: (r.piers || {})[n] || {} } : null; };
const check = (stored, s) => {
  if (stored == null) return { stored: null, sum: s, rel_err: null, ok: true };
  let e = Math.abs(s - stored) / Math.max(Math.abs(stored), 1e-30);
  if (Math.abs(stored) < 1e-9 && Math.abs(s) < 1e-9) e = 0;
  return { stored, sum: s, rel_err: e, ok: e < 1e-6 };
};
const rank = (rows, key = "contribution") => { rows.sort((a, b) => Math.abs(b[key] || 0) - Math.abs(a[key] || 0)); rows.forEach((r, i) => { r.rank = i + 1; }); return rows; };
const shares = (rows, total) => {
  const den = rows.reduce((s, r) => s + Math.abs(r.contribution || 0), 0);
  for (const r of rows) { r.share = total ? (r.contribution || 0) / total : null; r.abs_share = den ? Math.abs(r.contribution || 0) / den : 0; }
};
const isNonlinear = (model, name) => {
  const lc = (model.cases || {})[name];
  if (lc && ((lc.geometric && lc.geometric !== "linear") || lc.pdelta)) return `case ${JSON.stringify(name)} is solved with geometric nonlinearity; its pattern responses do not superpose`;
  if ((model.members || []).some(m => (m.axial_limit || "both") !== "both")) return "the model has tension/compression-only members, so static cases are solved nonlinearly";
  return null;
};

function patternParts(model, R, pats) {
  const out = {}, missing = [];
  for (const p of pats) {
    const hit = Object.entries(model.cases || {}).find(([n, lc]) => { const ks = Object.keys(lc.patterns || {}).filter(k => lc.patterns[k]); return ks.length === 1 && ks[0] === p && (R.cases || {})[n] && !isNonlinear(model, n); });
    if (hit) { const s = +hit[1].patterns[p]; const pt = part(R, "cases", hit[0]); out[p] = [s === 1 ? pt : { ...pt, div: s }, `stored:${hit[0]}`]; }
    else missing.push(p);
  }
  if (missing.length) {
    const m = JSON.parse(JSON.stringify(model));
    m.cases = {}; m.combos = {}; m.rs_cases = {}; m.th_cases = {}; m.pushover_cases = {}; m.staged_cases = {}; m.buckling_cases = {}; m.cases_not_run = [];
    missing.forEach((p, i) => { m.cases[`__lp__${i}`] = { name: `__lp__${i}`, patterns: { [p]: 1 } }; });
    let r2 = {};
    try { r2 = exMockResults(m); } catch { r2 = {}; }
    missing.forEach((p, i) => { out[p] = [part(r2, "cases", `__lp__${i}`) || { case: {} }, "re-solved"]; });
  }
  return out;
}

function explainStatic(model, R, t, name) {
  const lc = model.cases[name];
  const st = part(R, "cases", name);
  const total = st ? exExtract(st, t) : null;
  const out = { case_kind: "static", method: "superposition", value: total };
  const why = isNonlinear(model, name);
  if (why) return { ...out, supported: false, method: "nonlinear", message: `No pattern breakdown: ${why}. Define a linear case per pattern to compare, or run the patterns as separate nonlinear cases.` };
  const parts = patternParts(model, R, Object.keys(lc.patterns || {}));
  const rows = Object.entries(lc.patterns || {}).map(([p, f]) => {
    const [pt, src] = parts[p]; const v = exExtract(pt, t);
    return { kind: "pattern", name: p, factor: +f, unit_value: v, contribution: v == null ? null : f * v, source: src, link: { type: "pattern", pattern: p, case: src.startsWith("stored:") ? src.slice(7) : null } };
  });
  let s = rows.reduce((a, r) => a + (r.contribution || 0), 0);
  const notes = [];
  if (total != null && rows.some(r => r.source === "re-solved") && Math.abs(s - total) > 1e-9 * Math.max(1, Math.abs(total))) {
    const solvedSum = rows.filter(r => r.source === "re-solved").reduce((a, r) => a + (r.contribution || 0), 0);
    const target = total - rows.filter(r => r.source !== "re-solved").reduce((a, r) => a + (r.contribution || 0), 0);
    const sc = solvedSum ? target / solvedSum : 0;
    for (const r of rows) if (r.source === "re-solved" && r.contribution != null) { r.contribution *= sc; r.unit_value = r.contribution / (r.factor || 1); }
    s = rows.reduce((a, r) => a + (r.contribution || 0), 0);
    notes.push("Mock: re-solved pattern values are scaled to the stored case total.");
  }
  shares(rows, total ?? s);
  const nre = rows.filter(r => r.source === "re-solved").length;
  notes.unshift(`${rows.length} pattern(s); ${nre} re-solved by superposition (linear case), ${rows.length - nre} from stored single-pattern cases.`);
  return { ...out, value: total ?? s, computed_total: s, rows: rank(rows), check: check(total, s), notes };
}

function memberKind(model, n) {
  if ((model.cases || {})[n]) return "static";
  if ((model.rs_cases || {})[n]) return "response_spectrum";
  if ((model.rs_combos || {})[n]) return "rs_directional";
  if ((model.th_cases || {})[n]) return "time_history";
  if ((model.staged_cases || {})[n]) return "staged";
  if ((model.combos || {})[n]) return "combo";
  return null;
}
function flatten(model, name, f, out, depth = 0) {
  for (const [m, g] of Object.entries(model.combos[name].cases || {})) {
    if (memberKind(model, m) === "combo" && (model.combos[m].combo_type || "add") === "add" && depth < 32) flatten(model, m, f * g, out, depth + 1);
    else out.push([m, f * g]);
  }
}
export function exThSeries(th, t, model) {
  if (t.kind === "base" && (t.component === "FX" || t.component === "FY")) return th["base_" + t.component] || [];
  if (t.kind === "story" && /^u[xy]$/.test(t.component)) { const s = (th["story_" + t.component] || {})[t.story]; return s || null; }
  if (t.kind === "story" && /^drift_/.test(t.component) && model) {
    const comp = t.component === "drift_x" ? "ux" : "uy", names = (model.stories || []).map(s => s.name), i = names.indexOf(t.story);
    const cur = (th["story_" + comp] || {})[t.story];
    if (i < 0 || !cur) return null;
    const prev = i > 0 ? (th["story_" + comp] || {})[names[i - 1]] || cur.map(() => 0) : cur.map(() => 0);
    const h = model.stories[i].height || 1;
    return cur.map((v, k) => (v - prev[k]) / h);
  }
  return null;
}
function explainCombo(model, R, t, name, expand) {
  const cb = model.combos[name], ctype = cb.combo_type || "add";
  const st = part(R, "combos", name), smax = st ? exExtract(st, t) : null;
  const smin = st && st.case.min ? exExtract({ case: st.case.min, piers: {} }, t) : null;
  const leaves = [];
  if (ctype === "add") flatten(model, name, 1, leaves); else for (const [m, f] of Object.entries(cb.cases || {})) leaves.push([m, +f]);
  const rows = [], notes = [];
  let single = true;
  for (const [m, f] of leaves) {
    const kind = memberKind(model, m);
    const row = { kind: "case", case_kind: kind, name: m, factor: f, link: { type: "case", case: m } };
    let lo = null, hi = null;
    if (kind === "static") {
      const v = exExtract(part(R, "cases", m) || {}, t);
      if (v != null) { lo = hi = f * v; row.unit_value = v; }
      if (expand && ctype === "add" && v != null && !isNonlinear(model, m)) {
        const pp = patternParts(model, R, Object.keys(model.cases[m].patterns || {}));
        const ch = Object.entries(model.cases[m].patterns || {}).map(([p, s]) => { const [pt, src] = pp[p]; const u = exExtract(pt, t); return { kind: "pattern", name: p, factor: f * s, unit_value: u, contribution: u == null ? null : f * s * u, source: src, link: { type: "pattern", pattern: p } }; });
        const sumc = ch.reduce((a, r) => a + (r.contribution || 0), 0);
        if (ch.some(r => r.source === "re-solved") && sumc && Math.abs(sumc - f * v) > 1e-9 * Math.max(1, Math.abs(f * v))) for (const r of ch) if (r.contribution != null) { r.contribution *= f * v / sumc; r.unit_value = r.contribution / (r.factor || 1); }
        row.children = ch.sort((a, b) => Math.abs(b.contribution || 0) - Math.abs(a.contribution || 0));
      }
    } else if (kind === "response_spectrum" || kind === "rs_directional") {
      single = false;
      const v = exExtract(part(R, "rs_cases", m) || {}, t);
      if (v != null) { lo = -Math.abs(f) * Math.abs(v); hi = Math.abs(f) * Math.abs(v); row.unit_value = v; row.signless = true; }
    } else if (kind === "time_history") {
      single = false;
      const th = (R.th_cases || {})[m], ser = th ? exThSeries(th, t, model) : null;
      if (ser) { const a = f * Math.min(...ser, 0), b = f * Math.max(...ser, 0); lo = Math.min(a, b); hi = Math.max(a, b); }
    } else if (kind === "combo") {
      const p = part(R, "combos", m), vmax = p ? exExtract(p, t) : null, vmin = p && p.case.min ? exExtract({ case: p.case.min }, t) : null;
      if (vmax != null) { if (vmin == null) { lo = hi = f * vmax; row.unit_value = vmax; } else { single = false; lo = Math.min(f * vmax, f * vmin); hi = Math.max(f * vmax, f * vmin); } }
    }
    if (lo == null) { row.available = false; notes.push(`${m}: this quantity is not available in its results (not run, or not recorded for this case type).`); }
    row.lo = lo; row.hi = hi;
    rows.push(row);
  }
  const av = rows.filter(r => r.lo != null);
  let cmax = 0, cmin = 0;
  if (ctype === "add") { for (const r of av) { r.contribution = r.hi; r.contribution_min = r.lo; } cmax = av.reduce((a, r) => a + r.hi, 0); cmin = av.reduce((a, r) => a + r.lo, 0); }
  else if (ctype === "envelope") {
    single = false;
    const gmax = av.reduce((b, r) => (!b || r.hi > b.hi ? r : b), null), gmin = av.reduce((b, r) => (!b || r.lo < b.lo ? r : b), null);
    for (const r of av) { r.contribution = r.hi; r.contribution_min = r.lo; r.governs_max = r === gmax; r.governs_min = r === gmin; }
    cmax = gmax ? gmax.hi : 0; cmin = gmin ? gmin.lo : 0;
    if (gmax) notes.unshift(`Max governed by ${gmax.name} (factor ${gmax.factor}).`);
    if (gmin) notes.splice(gmax ? 1 : 0, 0, `Min governed by ${gmin.name} (factor ${gmin.factor}).`);
  } else if (ctype === "abs") { single = false; for (const r of av) r.contribution = Math.max(Math.abs(r.hi), Math.abs(r.lo)); cmax = av.reduce((a, r) => a + r.contribution, 0); cmin = -cmax; }
  else if (ctype === "srss") { single = false; for (const r of av) r.contribution = Math.max(Math.abs(r.hi), Math.abs(r.lo)); cmax = Math.sqrt(av.reduce((a, r) => a + r.contribution ** 2, 0)); cmin = -cmax; for (const r of av) r.srss_share = cmax ? r.contribution ** 2 / cmax ** 2 : 0; }
  else { single = false; for (const r of av) { r.contribution = Math.max(r.hi, 0); r.contribution_min = Math.min(r.lo, 0); } cmax = av.reduce((a, r) => a + r.contribution, 0); cmin = av.reduce((a, r) => a + r.contribution_min, 0); }
  single = single && smin == null;
  const out = { case_kind: "combo", combo_type: ctype, method: `combo-${ctype}`, single_valued: single,
    value: smax ?? cmax, value_min: single ? null : (smin ?? cmin), computed_total: cmax, computed_min: single ? null : cmin, check: check(smax, cmax) };
  if (!single) out.check_min = check(smin, cmin);
  shares(rows, out.value);
  if (rows.some(r => r.signless)) notes.push("Response-spectrum members carry no sign: they add +|f|·R to the max and −|f|·R to the min (ETABS rule).");
  return { ...out, rows: rank(rows), notes };
}

function explainRs(model, R, t, name) {
  const rs = model.rs_cases[name], st = part(R, "rs_cases", name), total = st ? exExtract(st, t) : null;
  const out = { case_kind: "response_spectrum", method: "modal", value: total, combination: rs.combo_method || "CQC" };
  if (t.kind === "pier") return { ...out, supported: false, message: `No per-mode breakdown: modal pier forces are combined from internal cut vectors that are not kept per mode. The combined value is ${total}.` };
  const modal = R.modal || {}, per = modal.periods || [];
  if (!per.length) return { ...out, supported: false, message: "The model has no dynamic modes." };
  const dir = (rs.direction || "X") === "Y" ? "uy" : "ux";
  const w = (modal.participation || []).map(p => Math.max(+(p[dir] || 0), 0));
  const W = w.reduce((a, b) => a + b, 0) || 1;
  const R0 = Math.abs(total || 0);
  const vals = w.map((x, i) => (i % 3 === 2 ? -1 : 1) * R0 * Math.sqrt(x / W));
  const ss = vals.reduce((a, v) => a + v * v, 0);
  const rows = per.map((T, i) => ({ kind: "mode", name: `Mode ${i + 1}`, mode: i + 1, period: T, unit_value: vals[i], modal_value: vals[i], contribution: vals[i] * vals[i],
    share: ss ? vals[i] * vals[i] / ss : 0, abs_share: ss ? vals[i] * vals[i] / ss : 0, srss_share: ss ? vals[i] * vals[i] / ss : 0, link: { type: "mode", mode: i + 1 } }));
  return { ...out, rows: rank(rows), computed_total: Math.sqrt(ss), cross_terms: 0, sum_squares: ss, check: check(total, Math.sqrt(ss)),
    notes: [`${rows.length} modes (mock: modal values from the mass participation, SRSS-consistent).`, "Modal values are signed; the combined result is positive (response-spectrum results carry no sign)."] };
}

function explainTh(model, R, t, name) {
  const th = (R.th_cases || {})[name];
  const out = { case_kind: "time_history", method: "governing-step" };
  if (!th) return { ...out, supported: false, message: `No results for time-history case ${JSON.stringify(name)}.` };
  const ser = exThSeries(th, t, model);
  if (!ser || !ser.length) return { ...out, supported: false, message: "The time history of this quantity is not recorded (TH output keeps story ux / uy and base FX / FY series)." };
  const tt = th.t || ser.map((_, k) => k + 1);
  const order = ser.map((_, k) => k).sort((a, b) => Math.abs(ser[b]) - Math.abs(ser[a])).slice(0, 10);
  const rows = order.map((k, i) => ({ kind: "step", name: `Step ${k + 1}`, step: k + 1, time: tt[k], unit_value: ser[k], contribution: ser[k], rank: i + 1, governs: i === 0, link: { type: "step", case: name, step: k + 1 } }));
  const g = rows[0];
  return { ...out, rows, value: g.unit_value, max: Math.max(...ser), min: Math.min(...ser), governing_step: g.step, governing_time: g.time,
    notes: [`Governing step ${g.step} at t = ${(+g.time).toPrecision(4)} s of ${ser.length} (largest |value|).`] };
}

export function mockExplain(model, R, body) {
  const t0 = performance.now();
  if (!R) throw new Error("no current results for this model: run the analysis first (F5)");
  const t = exNormalize(body.target), name = String(body.case || "");
  if (!name) throw new Error("'case' must name a load case or combination");
  let r;
  if ((model.combos || {})[name]) r = explainCombo(model, R, t, name, body.expand !== false);
  else if ((model.cases || {})[name]) r = explainStatic(model, R, t, name);
  else if ((model.rs_cases || {})[name]) r = explainRs(model, R, t, name);
  else if ((model.th_cases || {})[name]) r = explainTh(model, R, t, name);
  else if ((model.pushover_cases || {})[name] || (model.staged_cases || {})[name] || (model.rs_combos || {})[name])
    r = { case_kind: "nonlinear", supported: false, method: "none", message: "These results are not a superposition of load patterns, so no contribution breakdown is available." };
  else throw new Error(`unknown case or combination ${JSON.stringify(name)}`);
  const out = { target: { ...t, label: exLabel(t), unit: exUnitKind(t) }, case: name, supported: true, ...r };
  if (out.supported !== false) out.supported = true;
  out.rows = out.rows || []; out.notes = out.notes || [];
  out.elapsed_ms = +(performance.now() - t0).toFixed(3);
  return out;
}
