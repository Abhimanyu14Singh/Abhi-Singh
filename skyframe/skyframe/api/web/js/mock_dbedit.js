/* SkyFrame — ?mock=1 router for Interactive Database Editing
   (CONTRACT "Interactive database editing (model tables)").

   A client-side port of skyframe/core/modeltables.py: the same table keys,
   columns (key / label / type / quantity / enum / default / editable), the
   same row identity rules (_id → edit, key match → edit, else add, missing →
   delete), per-cell type / enum / reference / required / duplicate checks,
   rename cascades, object-delete cascades, rectangle b/h recompute and story
   elevation recompute. Atomic: nothing is applied on any error. The model
   store stays SI. Also CSV (SI, `_id` + column keys) and a stored-zip
   workbook (one CSV per table). Exported: mockDbEdit(model, path, body). */

const clone = o => JSON.parse(JSON.stringify(o));
const QUANTITIES = {
  none: "", length: "m", dim: "m", area: "m^2", inertia: "m^4", force: "kN", moment: "kN*m",
  line_force: "kN/m", pressure: "kPa", stress: "kPa", modulus: "kPa", unit_weight: "kN/m^3",
  mass: "tonne", mass_density: "tonne/m^3", mass_per_length: "tonne/m", mass_per_area: "tonne/m^2",
  stiffness: "kN/m", rot_stiffness: "kN*m/rad", line_spring: "kN/m/m", thermal_coeff: "1/degC",
  period: "s", angle_deg: "deg",
};
const MASS_DEF = { self_mass: true, patterns: true, include_lateral: true, include_vertical: false, lump_at_stories: true };

class RowErr extends Error {}

/* ------------------------------------------------------------ columns */
function col(key, label, type = "number", quantity = "none", o = {}) {
  return { key, label, type, quantity, editable: o.editable !== false, required: !!o.required,
    optional: !!o.optional, def: o.def, enum: o.enum, ref: o.ref, refc: o.ref || (o.enum && o.enum.refc),
    path: o.path || [key], get: o.get, put: o.put };
}
const names = key => { const f = d => Object.keys(d[key] || {}); f.refc = key; return f; };
const optNames = key => { const f = d => ["", ...Object.keys(d[key] || {})]; f.refc = key; return f; };
const storiesOf = d => (d.stories || []).map(s => s.name);
const optStories = d => ["", ...storiesOf(d)]; optStories.refc = "stories";
const storiesEnum = d => storiesOf(d); storiesEnum.refc = "stories";
const withCurrent = (st, cont, field) => d => {
  const out = [...st];
  const src = d[cont] || {};
  for (const o of Array.isArray(src) ? src : Object.values(src)) if (o && typeof o[field] === "string" && !out.includes(o[field])) out.push(o[field]);
  return out;
};
const CASE_CONTS = ["cases", "rs_cases", "th_cases", "pushover_cases", "staged_cases", "buckling_cases",
  "steady_state_cases", "psd_cases", "nonlinear_static_cases", "hyperstatic_cases"];
function allCases(d) {
  const out = [];
  for (const k of CASE_CONTS) for (const n of Object.keys(d[k] || {})) if (!out.includes(n)) out.push(n);
  if (!out.includes("MODAL")) out.push("MODAL");
  return out;
}
const comboPool = d => [...allCases(d), ...Object.keys(d.combos || {}).filter(c => !allCases(d).includes(c))];
comboPool.refc = "*cases";
const REF_POOL = {
  members: d => new Set((d.members || []).map(m => m.uid)),
  shells: d => new Set((d.shells || []).map(r => r.uid)),
  links: d => new Set((d.links || []).map(l => l.uid)),
};
const REF_LABEL = { members: "frame", shells: "shell", links: "link" };
const opts = (c, d) => c.enum == null ? null : (typeof c.enum === "function" ? c.enum(d) : [...c.enum]);

function getv(c, obj, d) {
  if (c.get) return c.get(obj, d);
  let cur = obj;
  for (const p of c.path) { if (cur == null || typeof cur !== "object" || !(p in cur)) return c.def ?? null; cur = cur[p]; }
  return cur == null && !c.optional ? (c.def ?? null) : cur;
}
function putv(c, obj, v, d) {
  if (c.put) { c.put(obj, v, d); return; }
  let cur = obj;
  for (const p of c.path.slice(0, -1)) cur = cur[p];
  cur[c.path[c.path.length - 1]] = v;
}
const isNum = v => typeof v === "number" && isFinite(v);
const TRUE = new Set(["true", "yes", "y", "1", "t", "on", "x"]), FALSE = new Set(["false", "no", "n", "0", "f", "off", ""]);
export function parsePoints(raw) {
  if (typeof raw === "string") raw = raw.split(";").map(s => s.trim()).filter(Boolean).map(s => s.split(/[,\s]+/).filter(Boolean));
  if (!Array.isArray(raw)) throw new RowErr("expected a list of points 'x,y,z; x,y,z; ...'");
  return raw.map(p => {
    if (!Array.isArray(p) || p.length !== 3) throw new RowErr("every point needs exactly 3 coordinates x,y,z");
    const q = p.map(Number);
    if (!q.every(isFinite) || p.some(v => typeof v === "boolean")) throw new RowErr("point coordinates must be finite numbers");
    return q;
  });
}
function parseCell(c, raw, d) {
  const blank = raw == null || (typeof raw === "string" && !raw.trim());
  if (c.type === "number" || c.type === "int") {
    if (blank) { if (c.optional) return null; throw new RowErr("a number is required"); }
    if (typeof raw === "boolean") throw new RowErr("must be a number");
    let v = raw;
    if (typeof v === "string") { const t = v.trim().replace("−", "-"); v = /^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(t) ? +t : NaN; if (!isFinite(v)) throw new RowErr(`'${raw}' is not a number`); }
    if (!isNum(v)) throw new RowErr("must be a finite number");
    if (c.type === "int" && v !== Math.trunc(v)) throw new RowErr("must be a whole number");
    const o = opts(c, d);
    if (o && !o.includes(v)) throw new RowErr(`must be one of [${o.join(", ")}]`);
    return v;
  }
  if (c.type === "bool") {
    if (typeof raw === "boolean") return raw;
    if (raw === 0 || raw === 1) return !!raw;
    const s = String(raw ?? "").trim().toLowerCase();
    if (TRUE.has(s)) return true;
    if (FALSE.has(s)) return false;
    throw new RowErr("must be true / false");
  }
  if (c.type === "points") { if (blank) throw new RowErr("points are required"); return parsePoints(raw); }
  if (raw != null && typeof raw === "object") throw new RowErr("must be text");
  const v = raw == null ? "" : String(raw).trim();
  if (c.type === "enum") { const o = opts(c, d); if (!o.includes(v)) throw new RowErr(`'${v}' is not one of [${o.map(x => `'${x}'`).join(", ")}]`); }
  if (c.type === "ref" && v && !REF_POOL[c.ref](d).has(v)) throw new RowErr(`unknown ${REF_LABEL[c.ref]} '${v}'`);
  return v;
}
const same = (a, b) => {
  if (isNum(a) && isNum(b)) return a === b;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => same(x, b[i]));
  return JSON.stringify(a) === JSON.stringify(b);
};
const E = (table, row, c, message) => ({ table, row, col: c, message });

function rowValues(t, i, row, base, d, errs, isNew, skip = []) {
  for (const c of t.cols) {
    if (!c.editable || skip.includes(c.key)) continue;
    if (!(c.key in row)) { if (isNew && c.required) errs.push(E(t.key, i, c.key, `${c.label} is required`)); continue; }
    const cur = getv(c, base, d);
    if (!isNew && same(row[c.key], cur)) continue;
    let v;
    try { v = parseCell(c, row[c.key], d); } catch (e) { errs.push(E(t.key, i, c.key, `${c.label}: ${e.message}`)); continue; }
    if (isNew && c.required && (v === "" || v == null)) { errs.push(E(t.key, i, c.key, `${c.label} is required`)); continue; }
    if (!isNew && same(cur, v)) continue;
    putv(c, base, v, d);
  }
}

/* ------------------------------------------------------------ cascades */
const renameKeys = (o, mp) => o && typeof o === "object" ? Object.fromEntries(Object.entries(o).map(([k, v]) => [mp[k] ?? k, v])) : o;
const CASC = {
  material: (d, mp) => {
    for (const s of Object.values(d.sections || {})) s.material = mp[s.material] ?? s.material;
    for (const s of Object.values(d.shell_sections || {})) {
      s.material = mp[s.material] ?? s.material;
      for (const la of (s.layered && s.layered.layers) || []) la.material = mp[la.material] ?? la.material;
    }
  },
  section: (d, mp) => { for (const m of d.members || []) m.section = mp[m.section] ?? m.section; },
  shellSection: (d, mp) => { for (const r of d.shells || []) r.section = mp[r.section] ?? r.section; },
  pattern: (d, mp) => {
    for (const c of Object.values(d.cases || {})) { c.patterns = renameKeys(c.patterns, mp); if (c.pdelta_gravity) c.pdelta_gravity = renameKeys(c.pdelta_gravity, mp); }
    for (const k of ["mass_source", "mass_from_patterns"]) if (d[k]) d[k] = renameKeys(d[k], mp);
    for (const k of ["th_cases", "pushover_cases", "buckling_cases"]) for (const c of Object.values(d[k] || {})) if (c.gravity) c.gravity = renameKeys(c.gravity, mp);
    for (const c of Object.values(d.staged_cases || {})) { c.pattern = mp[c.pattern] ?? c.pattern; if (c.include_live) c.include_live = renameKeys(c.include_live, mp); }
  },
  case: (d, mp) => {
    for (const c of Object.values(d.combos || {})) c.cases = renameKeys(c.cases, mp);
    d.cases_not_run = (d.cases_not_run || []).map(c => mp[c] ?? c);
    for (const c of Object.values(d.rs_combos || {})) for (const k of ["name_x", "name_y"]) c[k] = mp[c[k]] ?? c[k];
    for (const c of Object.values(d.buckling_cases || {})) if (c.base_case) c.base_case = mp[c.base_case] ?? c.base_case;
  },
  story: (d, mp) => {
    for (const k of ["members", "shells"]) for (const o of d[k] || []) o.story = mp[o.story] ?? o.story;
    for (const k of ["story_diaphragm", "explicit_story_masses"]) if (d[k]) d[k] = renameKeys(d[k], mp);
    for (const p of Object.values(d.patterns || {})) for (const s of p.story_forces || []) s.story = mp[s.story] ?? s.story;
  },
  group: (d, mp) => { for (const c of d.section_cuts || []) if (c.group in mp) c.group = mp[c.group]; },
  diaphragm: (d, mp) => {
    for (const r of d.shells || []) if (r.diaphragm in mp) r.diaphragm = mp[r.diaphragm];
    for (const e of d.joint_diaphragms || []) if (e.diaphragm in mp) e.diaphragm = mp[e.diaphragm];
  },
};
const UID_LISTS = { members: [["member_udls", "member_uid"], ["member_loads", "member_uid"], ["thermal_loads", "member_uid"]],
  shells: [["area_loads", "region_uid"], ["shell_thermal_loads", "region_uid"]], links: [] };
const cascadeUid = kind => (d, mp) => {
  for (const p of Object.values(d.patterns || {})) for (const [k, f] of UID_LISTS[kind]) for (const e of p[k] || []) if (e && e[f] in mp) e[f] = mp[e[f]];
  for (const g of Object.values(d.groups || {})) if (Array.isArray(g[kind])) g[kind] = g[kind].map(u => mp[u] ?? u);
};
const deleteUid = kind => (d, gone) => {
  for (const p of Object.values(d.patterns || {})) for (const [k, f] of UID_LISTS[kind]) if (Array.isArray(p[k])) p[k] = p[k].filter(e => !(e && gone.has(e[f])));
  for (const g of Object.values(d.groups || {})) if (Array.isArray(g[kind])) g[kind] = g[kind].filter(u => !gone.has(u));
};
const usedBy = (d, gone, list) => { for (const [arr, f, what] of list(d)) for (const o of arr) if (gone.has(o[f])) return what(o); return null; };
const REFS = {
  material: (d, g) => usedBy(d, g, d => [[Object.values(d.sections || {}), "material", o => `material '${o.material}' is used by frame section '${o.name}'`],
    [Object.values(d.shell_sections || {}), "material", o => `material '${o.material}' is used by shell section '${o.name}'`]]),
  section: (d, g) => usedBy(d, g, d => [[d.members || [], "section", o => `frame section '${o.section}' is used by frame '${o.uid}'`]]),
  shellSection: (d, g) => usedBy(d, g, d => [[d.shells || [], "section", o => `shell section '${o.section}' is used by shell '${o.uid}'`]]),
  pattern: (d, g) => {
    for (const c of Object.values(d.cases || {})) for (const p of Object.keys(c.patterns || {})) if (g.has(p)) return `load pattern '${p}' is used by load case '${c.name}'`;
    for (const p of Object.keys(d.mass_source || {})) if (g.has(p)) return `load pattern '${p}' is used by the mass source`;
    return null;
  },
  case: (d, g) => { for (const c of Object.values(d.combos || {})) for (const m of Object.keys(c.cases || {})) if (g.has(m)) return `'${m}' is used by load combination '${c.name}'`; return null; },
};

/* ------------------------------------------------------------ table kinds */
function objectRows(t, d) {
  const items = t.kind === "dict" ? Object.entries(d[t.container] || {}) : (d[t.container] || []).map((o, i) => [i, o]);
  return items.map(([rid, o]) => {
    const r = { _id: rid };
    for (const c of t.cols) r[c.key] = t.kind === "dict" && c.key === t.keycol ? rid : getv(c, o, d);
    return r;
  });
}
function objectApply(t, d, rows, errs) {
  const items = t.kind === "dict" ? Object.entries(d[t.container] || {}) : (d[t.container] || []).map((o, i) => [i, o]);
  const orig = new Map(items);
  const keyOf = new Map(items.map(([rid, o]) => [rid, t.kind === "dict" ? rid : (t.keycol ? o[t.keycol] : undefined)]));
  const byKey = new Map([...keyOf].map(([rid, k]) => [k, rid]));
  const seen = new Set(), pairs = [];
  const n0 = errs.length;
  rows.forEach((row, i) => {
    let rid = row._id;
    if (rid != null && !orig.has(rid)) rid = null;
    if (rid == null && t.keycol) { const kv = typeof row[t.keycol] === "string" ? row[t.keycol].trim() : row[t.keycol]; if (byKey.has(kv) && !seen.has(byKey.get(kv))) rid = byKey.get(kv); }
    if (rid != null && seen.has(rid)) { errs.push(E(t.key, i, null, "row duplicates another row's object")); return; }
    const isNew = rid == null;
    const base = isNew ? t.newObj(d) : clone(orig.get(rid));
    rowValues(t, i, row, base, d, errs, isNew, t.kind === "dict" ? [t.keycol] : []);
    if (isNew && t.validateNew) { const bad = t.validateNew(base); if (bad) errs.push(E(t.key, i, bad[0], bad[1])); }
    if (!isNew) seen.add(rid);
    pairs.push([i, rid, base]);
  });
  if (errs.length > n0) return {};
  const keys = [], renames = {};
  if (t.keycol) {
    const used = new Set(), taken = new Set(pairs.map(p => p[2][t.keycol]));
    for (const [i, rid, obj] of pairs) {
      let k = t.kind === "dict" ? (t.keycol in rows[i] ? rows[i][t.keycol] : (rid ?? "")) : obj[t.keycol];
      k = k == null ? "" : String(k).trim();
      if (!k && t.autoPrefix) { let n = 1; while (taken.has(`${t.autoPrefix}${n}`) || used.has(`${t.autoPrefix}${n}`)) n++; k = `${t.autoPrefix}${n}`; }
      if (t.kind === "list" && obj[t.keycol] !== k) obj[t.keycol] = k;
      const lab = t.cols.find(c => c.key === t.keycol).label;
      if (!k) { errs.push(E(t.key, i, t.keycol, `${lab} is required`)); continue; }
      if (used.has(k)) { errs.push(E(t.key, i, t.keycol, `duplicate ${lab} '${k}'`)); continue; }
      used.add(k); keys.push(k);
      if (rid != null && keyOf.get(rid) !== k) renames[keyOf.get(rid)] = k;
    }
    if (errs.length > n0) return {};
  }
  const gone = new Set([...orig.keys()].filter(rid => !seen.has(rid)).map(rid => keyOf.get(rid) ?? rid));
  if (gone.size && t.deleteRefs) { const msg = t.deleteRefs(d, gone); if (msg) { errs.push(E(t.key, null, null, msg)); return {}; } }
  if (!(t.container in d) && !pairs.length) { /* absent + still empty: keep the key absent */ }
  else if (t.kind === "dict") {
    const nw = {};
    pairs.forEach(([, , obj], j) => { if (t.nameInObj !== false) obj.name = keys[j]; nw[keys[j]] = obj; });
    d[t.container] = nw;
  } else d[t.container] = pairs.map(p => p[2]);
  if (t.post) t.post(d, pairs, orig);
  if (Object.keys(renames).length && t.onRename) t.onRename(d, renames);
  if (gone.size && t.onDelete) t.onDelete(d, gone);
  const out = { added: pairs.filter(p => p[1] == null).length,
    modified: pairs.filter(p => p[1] != null && JSON.stringify(p[2]) !== JSON.stringify(orig.get(p[1]))).length, deleted: gone.size };
  if (Object.keys(renames).length) out.renamed = renames;
  return out;
}
function* patItems(t, d) {
  for (const [pn, p] of Object.entries(d.patterns || {}))
    for (const lst of t.lists) for (const [k, e] of (p[lst] || []).entries()) if (t.belongs(lst, e)) yield [[pn, lst, k], e];
}
function patRows(t, d) {
  const out = [];
  for (const [rid, e] of patItems(t, d)) {
    const o = t.toRowObj(rid[1], e), r = { _id: rid, pattern: rid[0] };
    for (const c of t.cols) if (c.key !== "pattern") r[c.key] = getv(c, o, d);
    out.push(r);
  }
  return out;
}
function patApply(t, d, rows, errs) {
  const n0 = errs.length, orig = new Map(), pats = d.patterns || {};
  for (const [rid, e] of patItems(t, d)) orig.set(rid.join("\u0001"), [rid, e]);
  const seen = new Set(), placed = [];
  rows.forEach((row, i) => {
    let rid = Array.isArray(row._id) ? row._id : null;
    let key = rid ? rid.join("\u0001") : null;
    if (key && !orig.has(key)) { rid = null; key = null; }
    if (key && seen.has(key)) { errs.push(E(t.key, i, null, "row duplicates another row's load")); return; }
    const pn = typeof row.pattern === "string" ? row.pattern.trim() : (rid ? rid[0] : null);
    if (!pn || !(pn in pats)) { errs.push(E(t.key, i, "pattern", `unknown load pattern '${pn}'`)); return; }
    const base = rid ? t.toRowObj(rid[1], clone(orig.get(key)[1])) : t.newObj(d);
    rowValues(t, i, row, base, d, errs, !rid, ["pattern"]);
    if (key) seen.add(key);
    placed.push([rid, pn, base]);
  });
  if (errs.length > n0) return {};
  const slot = new Map(), extra = [];
  for (const [rid, pn, obj] of placed) {
    const [lst, out] = t.target(rid ? rid[1] : null, obj);
    if (rid && rid[0] === pn && rid[1] === lst) slot.set(rid.join("\u0001"), out); else extra.push([pn, lst, out]);
  }
  for (const [pn, p] of Object.entries(pats)) for (const lst of t.lists) {
    const old = p[lst] || [], nw = [];
    old.forEach((e, k) => { const key = [pn, lst, k].join("\u0001"); if (!t.belongs(lst, e)) nw.push(e); else if (slot.has(key)) nw.push(slot.get(key)); });
    for (const [pn2, ls, o] of extra) if (pn2 === pn && ls === lst) nw.push(o);
    if (nw.length || lst in p) p[lst] = nw;
  }
  return { added: placed.filter(p => !p[0]).length, modified: [...slot].filter(([k, o]) => JSON.stringify(o) !== JSON.stringify(orig.get(k)[1])).length,
    deleted: [...orig.keys()].filter(k => !seen.has(k)).length };
}
const mergeOrdered = (old, nw) => { const out = {}; for (const k of Object.keys(old)) if (k in nw) out[k] = nw[k]; for (const [k, v] of Object.entries(nw)) if (!(k in out)) out[k] = v; return out; };
function childRows(t, d) {
  if (!t.container) return Object.entries(d[t.field] || {}).map(([k, v]) => ({ _id: [k], [t.childCol]: k, [t.valueCol]: v }));
  const out = [];
  for (const [pn, p] of Object.entries(d[t.container] || {})) for (const [k, v] of Object.entries(p[t.field] || {}))
    out.push({ _id: [pn, k], [t.parentCol]: pn, [t.childCol]: k, [t.valueCol]: v });
  return out;
}
function childApply(t, d, rows, errs) {
  const n0 = errs.length, parents = t.container ? (d[t.container] || {}) : null;
  const vc = t.cols.find(c => c.key === t.valueCol), cc = t.cols.find(c => c.key === t.childCol);
  const built = new Map(), seen = new Set();
  rows.forEach((row, i) => {
    let pn = null;
    if (parents) {
      pn = typeof row[t.parentCol] === "string" ? row[t.parentCol].trim() : row[t.parentCol];
      if (!(pn in parents)) { errs.push(E(t.key, i, t.parentCol, `unknown ${t.cols.find(c => c.key === t.parentCol).label} '${pn}'`)); return; }
    }
    let ch, v;
    try { ch = parseCell(cc, row[t.childCol], d); if (!ch) throw new RowErr("is required"); } catch (e) { errs.push(E(t.key, i, t.childCol, `${cc.label}: ${e.message}`)); return; }
    try { v = parseCell(vc, row[t.valueCol], d); } catch (e) { errs.push(E(t.key, i, t.valueCol, `${vc.label}: ${e.message}`)); return; }
    const sk = `${pn}\u0001${ch}`;
    if (seen.has(sk)) { errs.push(E(t.key, i, t.childCol, `duplicate ${cc.label} '${ch}'`)); return; }
    seen.add(sk);
    if (!built.has(pn)) built.set(pn, {});
    built.get(pn)[ch] = v;
  });
  if (errs.length > n0) return {};
  if (!parents) { const nw = mergeOrdered(d[t.field] || {}, built.get(null) || {}); if (d[t.field] || Object.keys(nw).length) d[t.field] = nw; return { rows: rows.length }; }
  for (const [pn, p] of Object.entries(parents)) p[t.field] = mergeOrdered(p[t.field] || {}, built.get(pn) || {});
  return { rows: rows.length };
}

/* ------------------------------------------------------------ special tables */
const pkey = p => p.map(v => (Math.round(+v * 1e6) / 1e6).toFixed(6)).join(",");
function jointRows(d) {
  const J = new Map();
  const add = (p, what) => {
    const k = pkey(p);
    let j = J.get(k);
    if (!j) { j = { x: +p[0], y: +p[1], z: +p[2], frames: 0, shells: 0, links: 0, support: "" }; J.set(k, j); }
    if (what) j[what]++;
    return j;
  };
  for (const m of d.members || []) { add(m.pi, "frames"); add(m.pj, "frames"); }
  for (const r of d.shells || []) for (const c of r.corners || []) add(c, "shells");
  for (const l of d.links || []) { add(l.pi, "links"); add(l.pj, "links"); }
  for (const s of d.supports || []) { const r = s.restraints || []; add(s.point).support = r.every(Boolean) ? "fixed" : r.join("") === "111000" ? "pinned" : r.map(v => v ? 1 : 0).join(""); }
  for (const s of d.spring_supports || []) { const j = add(s.point); j.support = (j.support + " spring").trim(); }
  const elev = new Map((d.stories || []).map(s => [(+s.elevation).toFixed(6), s.name]));
  const st0 = (d.stories || [])[0];
  const z0 = st0 ? +st0.elevation - +st0.height : null;
  return [...J.values()].sort((a, b) => a.z - b.z || a.y - b.y || a.x - b.x).map((j, n) => ({
    _id: n, id: `J${n + 1}`, story: elev.get(j.z.toFixed(6)) || (z0 != null && Math.abs(j.z - z0) < 1e-6 ? "Base" : ""), ...j }));
}
function caseSummaryRows(d) {
  const kinds = [["cases", "Linear Static"], ["rs_cases", "Response Spectrum"], ["th_cases", "Time History"], ["pushover_cases", "Pushover"],
    ["staged_cases", "Staged Construction"], ["buckling_cases", "Buckling"], ["steady_state_cases", "Steady State"], ["psd_cases", "PSD"],
    ["nonlinear_static_cases", "Nonlinear Static"], ["hyperstatic_cases", "Hyperstatic"]];
  const nr = new Set(d.cases_not_run || []), seen = new Set(), out = [];
  for (const [k, lab] of kinds) for (const n of Object.keys(d[k] || {})) if (!seen.has(n)) { seen.add(n); out.push({ _id: n, name: n, type: lab, run: !nr.has(n) }); }
  if (!seen.has("MODAL")) out.push({ _id: "MODAL", name: "MODAL", type: "Modal", run: !nr.has("MODAL") });
  return out;
}
const gridList = d => (d.grid_systems && d.grid_systems.length) ? d.grid_systems : (d.grid ? [d.grid] : []);
const XL = i => { let s = "", n = i; do { s = String.fromCharCode(65 + (n % 26)) + s; n = Math.floor(n / 26) - 1; } while (n >= 0); return s; };
function gridRows(d) {
  const out = [];
  for (const g of gridList(d)) {
    const orth = (g.kind || "orthogonal") === "orthogonal";
    const axes = orth ? [["x", "x_lines", "x_labels"], ["y", "y_lines", "y_labels"]] : [["radius", "radii", null], ["theta", "theta_deg", null]];
    for (const [ax, key, lab] of axes) (g[key] || []).forEach((v, k) => out.push({ _id: [g.name, ax, k], system: g.name, axis: ax,
      label: (lab && g[lab] && g[lab][k]) || (orth ? (ax === "x" ? XL(k) : String(k + 1)) : `${ax === "radius" ? "R" : "T"}${k + 1}`), ordinate: v }));
  }
  return out;
}
function gridApply(t, d, rows, errs) {
  const n0 = errs.length, grids = gridList(d), by = new Map(grids.map(g => [g.name, g])), acc = new Map(), fresh = [];
  rows.forEach((row, i) => {
    const sn = typeof row.system === "string" ? row.system.trim() : "";
    if (!sn) { errs.push(E(t.key, i, "system", "Grid System is required")); return; }
    let ax, v;
    try { ax = parseCell(t.cols[1], row.axis, d); } catch (e) { errs.push(E(t.key, i, "axis", e.message)); return; }
    try { v = parseCell(t.cols[3], row.ordinate, d); } catch (e) { errs.push(E(t.key, i, "ordinate", e.message)); return; }
    const kind = by.has(sn) ? (by.get(sn).kind || "orthogonal") : "orthogonal";
    if ((kind === "orthogonal") !== (ax === "x" || ax === "y")) { errs.push(E(t.key, i, "axis", `axis '${ax}' does not fit the ${kind} grid '${sn}'`)); return; }
    if (!by.has(sn) && !fresh.includes(sn)) fresh.push(sn);
    if (!acc.has(sn)) acc.set(sn, {});
    (acc.get(sn)[ax] = acc.get(sn)[ax] || []).push(v);
  });
  if (errs.length > n0) return {};
  const K = { x: "x_lines", y: "y_lines", radius: "radii", theta: "theta_deg" };
  for (const g of grids) {
    const got = acc.get(g.name) || {};
    for (const ax of (g.kind || "orthogonal") === "orthogonal" ? ["x", "y"] : ["radius", "theta"]) {
      const nw = got[ax] || [];
      if (!same(g[K[ax]] || [], nw)) { g[K[ax]] = nw; if (ax === "x" && g.x_labels) g.x_labels = nw.map((_, k) => XL(k)); if (ax === "y" && g.y_labels) g.y_labels = nw.map((_, k) => String(k + 1)); }
    }
  }
  for (const n of fresh) grids.push({ name: n, kind: "orthogonal", origin: [0, 0], rotation: 0, x_lines: acc.get(n).x || [], y_lines: acc.get(n).y || [], radii: [], theta_deg: [] });
  d.grid_systems = grids; d.grid = grids[0] || null;
  return { rows: rows.length, systems_added: fresh.length };
}

/* ------------------------------------------------------------ helpers for columns */
const pt = (prefix, key, label, req = true) => [0, 1, 2].map(i => col(`${prefix}${"xyz"[i]}`, `${label}${"XYZ"[i]}`, "number", "length",
  { required: req, path: [key, i], put: (o, v) => { const p = [...(o[key] || [0, 0, 0])]; while (p.length < 3) p.push(0); p[i] = v; o[key] = p; } }));
const k6 = () => ["kx", "ky", "kz", "krx", "kry", "krz"].map((lb, i) => col(lb, lb.toUpperCase(), "number", i < 3 ? "stiffness" : "rot_stiffness",
  { def: 0, path: ["stiffness", i], put: (o, v) => { const k = [...(o.stiffness || [0, 0, 0, 0, 0, 0])]; k[i] = v; o.stiffness = k; } }));
const relCol = (tok, label) => col(`release_${tok.slice(-1)}`, `Release ${label}`, "bool", "none", { def: false,
  get: o => String(o.releases || "").split(",").map(s => s.trim()).includes(tok),
  put: (o, v) => {
    let t = String(o.releases || "").split(",").map(s => s.trim()).filter(Boolean);
    if (v && !t.includes(tok)) t.push(tok);
    if (!v) t = t.filter(x => x !== tok);
    t.sort((a, b) => ({ Mi: 0, Mj: 1 }[a] ?? 9) - ({ Mi: 0, Mj: 1 }[b] ?? 9));
    o.releases = t.join(",");
  } });
const polyArea = cs => {
  if (!cs || cs.length < 3) return 0;
  let nx = 0, ny = 0, nz = 0;
  for (let i = 0; i < cs.length; i++) { const a = cs[i], b = cs[(i + 1) % cs.length]; nx += a[1] * b[2] - a[2] * b[1]; ny += a[2] * b[0] - a[0] * b[2]; nz += a[0] * b[1] - a[1] * b[0]; }
  return Math.hypot(nx, ny, nz) / 2;
};
function rectProps(b, h) {
  const a = Math.max(b, h) / 2, c = Math.min(b, h) / 2;
  return { A: b * h, I33: b * h ** 3 / 12, I22: h * b ** 3 / 12, J: a * c ** 3 * (16 / 3 - 3.36 * (c / a) * (1 - c ** 4 / (12 * a ** 4))) };
}
function sectionPost(d, pairs, orig) {
  for (const [, rid, s] of pairs) {
    for (const k of ["A", "I33", "I22", "J"]) if (k in s && s[k] == null) delete s[k];
    if (rid == null) { if (!("A" in s) && s.b > 0 && s.h > 0) Object.assign(s, rectProps(+s.b, +s.h)); continue; }
    if ((s.kind || "prismatic") !== "prismatic" || (d.designer_sections || {})[rid]) continue;
    const o = orig.get(rid);
    const dims = s.b !== o.b || s.h !== o.h, propsSame = ["A", "I33", "I22", "J"].every(k => s[k] === o[k]);
    const wasRect = o.b > 0 && o.h > 0 && Math.abs((o.A || 0) - o.b * o.h) <= 1e-9 * Math.abs(o.b * o.h);
    if (dims && propsSame && wasRect && s.b > 0 && s.h > 0) Object.assign(s, rectProps(+s.b, +s.h));
  }
}
function storyPost(d, pairs, orig) {
  const st = d.stories || [], old = [...orig.keys()].sort((a, b) => a - b).map(k => orig.get(k));
  const unchanged = old.length === st.length && old.every((o, i) => o.height === st[i].height) && pairs.every(([, rid], k) => rid === k);
  if (unchanged) { st.forEach((s, i) => { s.elevation = old[i].elevation; }); return; }
  let z = old.length ? (+old[0].elevation || 0) - +old[0].height : 0;
  if (Math.abs(z) < 1e-12) z = 0;
  for (const s of st) { z += +s.height; s.elevation = z; }
}
const udlAsLoad = (lst, e) => lst === "member_udls" ? { member_uid: e.member_uid, kind: "udl", w: e.w, w2: 0, a: 0, b: 1, direction: "gravity" } : clone(e);
const distTarget = (lst, o) => {
  if ((lst === "member_udls" || lst == null) && o.kind === "udl" && o.direction === "gravity" && +(o.a ?? 0) === 0 && +(o.b ?? 1) === 1 && !o.projected && +(o.w2 ?? 0) === 0)
    return ["member_udls", { member_uid: o.member_uid, w: o.w }];
  const out = { ...o }; if (!out.projected) delete out.projected;
  return ["member_loads", out];
};
const mlTarget = (lst, o) => { const out = { ...o }; if (!out.projected) delete out.projected; return ["member_loads", out]; };

/* ------------------------------------------------------------ catalogue */
const G = { DEF: "Model Definition > Properties", OBJ: "Model Definition > Objects", ASN: "Model Definition > Assignments",
  LOAD: "Model Definition > Loads", CASE: "Model Definition > Load Cases & Combinations" };
const MATERIAL_TYPES = ["steel", "concrete", "rebar", "tendon", "masonry", "aluminum", "coldformed", "other"];
const LINK_TYPES = ["elastic", "damper", "gap", "hook", "isolator", "fp_isolator", "triple_fp", "multilinear",
  "multilinear_takeda", "multilinear_pivot", "multilinear_kinematic", "friction_spring", "rubber_isolator_bouc_wen"];
const ML_DIRS = ["gravity", "local_y", "global_x", "global_y", "global_z"];
const AREA_DIRS = ["gravity", "global_x", "global_y", "global_z", "local_1", "local_2", "local_3"];
const GEOM = ["linear", "pdelta", "corotational"];
const PATTERN_KINDS = ["dead", "live", "quake", "wind", "other", "notional", "prestress"];

function obj(key, title, group, cols, o) { return { key, title, group, cols, tkind: "object", kind: "dict", editable: true, canAdd: true, canDelete: true, newObj: () => ({}), ...o }; }
function plist(key, title, cols, o) { return { key, title, group: G.LOAD, cols, tkind: "pattern", editable: true, canAdd: true, canDelete: true,
  belongs: () => true, toRowObj: (l, e) => clone(e), keycol: null, ...o }; }
function child(key, title, group, cols, o) { return { key, title, group, cols, tkind: "child", editable: true, canAdd: true, canDelete: true, keycol: null, ...o }; }

function buildCatalogue() {
  const n = "number";
  const patCol = () => col("pattern", "Load Pattern", "enum", "none", { enum: names("patterns"), required: true });
  const caseTbl = (key, title, container, cols, newObj) => obj(key, title, G.CASE, [col("name", "Case", "text", "none", { required: true }), ...cols],
    { container, keycol: "name", onRename: CASC.case, deleteRefs: REFS.case, newObj });
  return [
    obj("materials", "Material Properties", G.DEF, [
      col("name", "Name", "text", "none", { required: true }),
      col("material_type", "Type", "enum", "none", { enum: withCurrent(MATERIAL_TYPES, "materials", "material_type"), def: "concrete" }),
      col("symmetry", "Symmetry", "enum", "none", { enum: ["isotropic", "uniaxial"], def: "isotropic" }),
      col("E", "E", n, "modulus", { required: true }), col("nu", "ν", n, "none", { def: 0.2 }),
      col("G", "G", n, "modulus", { editable: false, get: o => o.G ?? (isNum(o.E) && isNum(o.nu ?? 0.2) ? o.E / (2 * (1 + (o.nu ?? 0.2))) : null) }),
      col("unit_weight", "Unit Weight", n, "unit_weight", { def: 24 }),
      col("mass_density", "Mass Density", n, "mass_density", { optional: true }), col("alpha", "α (thermal)", n, "thermal_coeff", { optional: true }),
      col("fc", "f'c", n, "stress", { optional: true }), col("fy", "Fy", n, "stress", { optional: true }), col("fu", "Fu", n, "stress", { optional: true }),
      col("Ry", "Ry", n, "none", { def: 1.1 }), col("damping", "Damping", n, "none", { def: 0 }), col("notes", "Notes", "text", "none", { def: "" }),
    ], { container: "materials", keycol: "name", onRename: CASC.material, deleteRefs: REFS.material, newObj: () => ({ nu: 0.2, unit_weight: 24 }) }),
    obj("frame_sections", "Frame Section Properties", G.DEF, [
      col("name", "Name", "text", "none", { required: true }),
      col("material", "Material", "enum", "none", { enum: names("materials"), required: true }),
      col("kind", "Kind", "text", "none", { def: "prismatic", editable: false }),
      col("b", "b (width)", n, "dim", { def: 0 }), col("h", "h (depth)", n, "dim", { def: 0 }),
      col("A", "A", n, "area", { optional: true }), col("I33", "I33", n, "inertia", { optional: true }),
      col("I22", "I22", n, "inertia", { optional: true }), col("J", "J", n, "inertia", { optional: true }),
      col("As2", "As2", n, "area", { optional: true }), col("As3", "As3", n, "area", { optional: true }),
      ...[["mod_A", "A mod"], ["mod_I33", "I33 mod"], ["mod_I22", "I22 mod"], ["mod_J", "J mod"], ["mod_mass", "Mass mod"], ["mod_weight", "Weight mod"]]
        .map(([k, l]) => col(k, l, n, "none", { def: 1 })),
    ], { container: "sections", keycol: "name", onRename: CASC.section, deleteRefs: REFS.section, post: sectionPost,
      newObj: () => ({ name: "", material: "", b: 0, h: 0 }),
      validateNew: s => {
        const p = ["A", "I33", "I22", "J"].map(k => s[k]);
        if (p.every(v => v != null)) return null;
        if (p.every(v => v == null) && s.b > 0 && s.h > 0) return null;
        return ["A", "give all of A / I33 / I22 / J, or b and h (> 0) for a rectangle"];
      } }),
    obj("shell_sections", "Shell Section Properties", G.DEF, [
      col("name", "Name", "text", "none", { required: true }),
      col("material", "Material", "enum", "none", { enum: names("materials"), required: true }),
      col("thickness", "Thickness", n, "dim", { required: true }), col("mod", "Stiffness mod", n, "none", { def: 1 }),
      ...["f11", "f22", "f12", "m11", "m22", "m12", "v13", "v23"].map(k => col(k, `${k} mod`, n, "none", { def: 1 })),
      col("mass", "Mass mod", n, "none", { def: 1 }), col("weight", "Weight mod", n, "none", { def: 1 }),
    ], { container: "shell_sections", keycol: "name", onRename: CASC.shellSection, deleteRefs: REFS.shellSection, newObj: () => ({ mod: 1, layered: null }) }),
    obj("stories", "Story Definitions", G.DEF, [
      col("name", "Story", "text", "none", { required: true }), col("height", "Height", n, "length", { required: true }),
      col("elevation", "Elevation", n, "length", { editable: false }),
    ], { container: "stories", kind: "list", keycol: "name", onRename: CASC.story, post: storyPost,
      onDelete: (d, gone) => { if (d.story_diaphragm) for (const g of gone) delete d.story_diaphragm[g]; },
      newObj: () => ({ name: "", height: 3, elevation: 0 }) }),
    { key: "grid_lines", title: "Grid Lines", group: G.DEF, tkind: "grid", editable: true, canAdd: true, canDelete: true, keycol: null, cols: [
      col("system", "Grid System", "text", "none", { required: true }), col("axis", "Axis", "enum", "none", { enum: ["x", "y", "radius", "theta"], required: true }),
      col("label", "Label", "text", "none", { editable: false }), col("ordinate", "Ordinate", n, "length", { required: true })] },
    { key: "joints", title: "Joint Coordinates (derived)", group: G.OBJ, tkind: "joints", editable: false, canAdd: false, canDelete: false, keycol: null, cols: [
      col("id", "Joint", "text", "none", { editable: false }), ...["x", "y", "z"].map(k => col(k, k.toUpperCase(), n, "length", { editable: false })),
      col("story", "Story", "text", "none", { editable: false }), col("frames", "Frame ends", "int", "none", { editable: false }),
      col("shells", "Shell corners", "int", "none", { editable: false }), col("links", "Link ends", "int", "none", { editable: false }),
      col("support", "Support", "text", "none", { editable: false })] },
    obj("frame_objects", "Frame Objects", G.OBJ, [
      col("uid", "Frame", "text"), col("kind", "Type", "enum", "none", { enum: ["column", "beam", "brace"], required: true }),
      col("section", "Section", "enum", "none", { enum: names("sections"), required: true }),
      col("story", "Story", "enum", "none", { enum: optStories, def: "" }),
      ...pt("i", "pi", "I-End "), ...pt("j", "pj", "J-End "),
      col("angle", "Angle", n, "angle_deg", { def: 0 }), relCol("Mi", "I (M2,M3)"), relCol("Mj", "J (M2,M3)"),
      col("cardinal_point", "Cardinal Pt", "int", "none", { enum: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], def: 10 }),
      col("end_offsets", "End Offsets", "enum", "none", { enum: ["manual", "auto"], def: "manual" }),
      col("rigid_i", "Offset I", n, "length", { def: 0 }), col("rigid_j", "Offset J", n, "length", { def: 0 }),
      col("rigid_factor", "Rigid Factor", n, "none", { def: 1 }),
      col("axial_limit", "Axial Limit", "enum", "none", { enum: ["both", "tension", "compression"], def: "both" }),
      col("additional_mass", "Add. Mass", n, "mass_per_length", { def: 0 }),
      col("length", "Length", n, "length", { editable: false, get: o => (o.pi && o.pj) ? Math.hypot(o.pj[0] - o.pi[0], o.pj[1] - o.pi[1], o.pj[2] - o.pi[2]) : null }),
    ], { container: "members", kind: "list", keycol: "uid", autoPrefix: "F", onRename: cascadeUid("members"), onDelete: deleteUid("members"),
      newObj: () => ({ uid: "", story: "", releases: "", angle: 0 }),
      post: d => { for (const m of d.members || []) if (m.pi && m.pj) m.length = Math.hypot(m.pj[0] - m.pi[0], m.pj[1] - m.pi[1], m.pj[2] - m.pi[2]); } }),
    obj("shell_objects", "Shell Objects", G.OBJ, [
      col("uid", "Shell", "text"), col("kind", "Type", "enum", "none", { enum: ["wall", "slab"], required: true }),
      col("behavior", "Behavior", "enum", "none", { enum: ["shell", "membrane"], required: true }),
      col("section", "Section", "enum", "none", { enum: optNames("shell_sections"), def: "" }),
      col("story", "Story", "enum", "none", { enum: optStories, def: "" }),
      col("corners", "Corners", "points", "length", { required: true }), col("mesh_size", "Mesh Size", n, "length", { def: 1 }),
      col("pier", "Pier", "text", "none", { def: "" }), col("wind_cp", "Wind Cp", n, "none", { optional: true }),
      col("diaphragm", "Diaphragm", "enum", "none", { enum: optNames("diaphragms"), def: "" }),
      col("additional_mass", "Add. Mass", n, "mass_per_area", { def: 0 }),
      col("area", "Area", n, "area", { editable: false, get: o => polyArea(o.corners) }),
    ], { container: "shells", kind: "list", keycol: "uid", autoPrefix: "A", onRename: cascadeUid("shells"), onDelete: deleteUid("shells"),
      newObj: () => ({ uid: "", mesh_size: 1, story: "", openings: [], pier: "", area_spring: null, wind_cp: null }) }),
    obj("links", "Link Objects", G.OBJ, [
      col("uid", "Link", "text"), col("link_type", "Type", "enum", "none", { enum: withCurrent(LINK_TYPES, "links", "link_type"), def: "elastic" }),
      ...pt("i", "pi", "I-End "), ...pt("j", "pj", "J-End "), ...k6(),
    ], { container: "links", kind: "list", keycol: "uid", autoPrefix: "L", onRename: cascadeUid("links"), onDelete: deleteUid("links"),
      newObj: () => ({ uid: "", stiffness: [0, 0, 0, 0, 0, 0], link_type: "elastic", params: {} }) }),
    obj("supports", "Joint Restraints (Supports)", G.ASN, [...pt("", "point", ""),
      ...["UX", "UY", "UZ", "RX", "RY", "RZ"].map((lb, i) => col(lb.toLowerCase(), lb, "bool", "none", { def: false,
        get: o => !!(o.restraints || [])[i], put: (o, v) => { const r = [...(o.restraints || [0, 0, 0, 0, 0, 0])]; r[i] = v ? 1 : 0; o.restraints = r; } }))],
    { container: "supports", kind: "list", keycol: null, newObj: () => ({ point: [0, 0, 0], restraints: [0, 0, 0, 0, 0, 0] }) }),
    obj("point_springs", "Point Springs", G.ASN, [...pt("", "point", ""), ...k6(),
      col("property", "Property", "enum", "none", { enum: optNames("spring_properties"), def: "", get: o => o.property || "",
        put: (o, v) => { if (v) o.property = v; else delete o.property; } }),
      col("angle_deg", "Angle", n, "angle_deg", { def: 0, get: o => o.angle_deg ?? 0, put: (o, v) => { if (v) o.angle_deg = v; else delete o.angle_deg; } })],
    { container: "spring_supports", kind: "list", keycol: null, newObj: () => ({ point: [0, 0, 0], stiffness: [0, 0, 0, 0, 0, 0] }) }),
    obj("line_springs", "Line Springs", G.ASN, [...pt("1", "p1", "P1 "), ...pt("2", "p2", "P2 "),
      col("kx", "kx", n, "line_spring", { def: 0 }), col("ky", "ky", n, "line_spring", { def: 0 }), col("kz", "kz", n, "line_spring", { def: 0 }),
      col("compression_only", "Compression Only", "bool", "none", { def: false })],
    { container: "line_springs", kind: "list", keycol: null, newObj: () => ({ p1: [0, 0, 0], p2: [0, 0, 0], kx: 0, ky: 0, kz: 0, compression_only: false }) }),
    obj("diaphragm_definitions", "Diaphragm Definitions", G.ASN, [col("name", "Name", "text", "none", { required: true }),
      col("type", "Type", "enum", "none", { enum: ["rigid", "semi_rigid"], def: "rigid" })],
    { container: "diaphragms", keycol: "name", nameInObj: false, onRename: CASC.diaphragm, newObj: () => ({ type: "rigid" }) }),
    { key: "story_diaphragms", title: "Story Diaphragm Option", group: G.ASN, tkind: "storydia", editable: true, canAdd: false, canDelete: false, keycol: "story",
      cols: [col("story", "Story", "text", "none", { editable: false }), col("diaphragm", "Option", "enum", "none", { enum: ["default", "rigid", "none"] })] },
    obj("joint_diaphragms", "Joint Diaphragm Assignments", G.ASN, [...pt("", "point", ""),
      col("diaphragm", "Diaphragm", "enum", "none", { enum: names("diaphragms"), required: true })],
    { container: "joint_diaphragms", kind: "list", keycol: null, newObj: () => ({ point: [0, 0, 0], diaphragm: "" }) }),
    obj("groups", "Group Definitions", G.ASN, [col("name", "Group", "text", "none", { required: true }), col("color", "Color", "text", "none", { def: "" }),
      ...[["n_members", "Frames", "members"], ["n_shells", "Shells", "shells"], ["n_links", "Links", "links"], ["n_points", "Points", "points"]]
        .map(([k, l, f]) => col(k, l, "int", "none", { editable: false, get: o => (o[f] || []).length }))],
    { container: "groups", keycol: "name", nameInObj: false, onRename: CASC.group, newObj: () => ({ members: [], shells: [], links: [], points: [], color: "" }) }),
    { key: "group_assignments", title: "Group Assignments", group: G.ASN, tkind: "groupasn", editable: true, canAdd: true, canDelete: true, keycol: null,
      cols: [col("group", "Group", "enum", "none", { enum: names("groups"), required: true }),
        col("type", "Object Type", "enum", "none", { enum: ["frame", "shell", "link"], required: true }), col("object", "Object", "text", "none", { required: true })] },
    obj("load_patterns", "Load Pattern Definitions", G.LOAD, [col("name", "Name", "text", "none", { required: true }),
      col("kind", "Type", "enum", "none", { enum: withCurrent(PATTERN_KINDS, "patterns", "kind"), def: "other" }),
      col("self_weight_factor", "Self Wt Mult", n, "none", { def: 0 }), col("accidental_torsion", "Acc. Torsion", "bool", "none", { def: false }),
      col("ecc", "Ecc. Ratio", n, "none", { def: 0.05 })],
    { container: "patterns", keycol: "name", onRename: CASC.pattern, deleteRefs: REFS.pattern,
      newObj: () => ({ kind: "other", member_udls: [], nodal_loads: [], story_forces: [], member_loads: [], area_loads: [], thermal_loads: [],
        accidental_torsion: false, ecc: 0.05, self_weight_factor: 0 }) }),
    plist("joint_loads", "Joint Loads", [patCol(), ...pt("", "point", ""), ...["fx", "fy", "fz"].map(k => col(k, k.toUpperCase(), n, "force", { def: 0 })),
      ...["mx", "my", "mz"].map(k => col(k, k.toUpperCase(), n, "moment", { def: 0 }))], { lists: ["nodal_loads"],
      target: (l, o) => { const out = { point: o.point, fx: o.fx ?? 0, fy: o.fy ?? 0, fz: o.fz ?? 0 }; for (const k of ["mx", "my", "mz"]) if (o[k]) out[k] = o[k]; return ["nodal_loads", out]; },
      newObj: () => ({ point: [0, 0, 0], fx: 0, fy: 0, fz: 0 }) }),
    plist("story_forces", "Story Lateral Forces", [patCol(), col("story", "Story", "enum", "none", { enum: storiesEnum, required: true }),
      col("fx", "FX", n, "force", { def: 0 }), col("fy", "FY", n, "force", { def: 0 })], { lists: ["story_forces"],
      target: (l, o) => ["story_forces", { story: o.story, fx: o.fx ?? 0, fy: o.fy ?? 0 }], newObj: () => ({ story: "", fx: 0, fy: 0 }) }),
    plist("frame_distributed_loads", "Frame Loads - Distributed", [patCol(), col("member_uid", "Frame", "ref", "none", { ref: "members", required: true }),
      col("kind", "Shape", "enum", "none", { enum: ["udl", "trapezoid"], def: "udl" }), col("direction", "Direction", "enum", "none", { enum: ML_DIRS, def: "gravity" }),
      col("w", "w (start)", n, "line_force", { required: true }), col("w2", "w (end)", n, "line_force", { def: 0 }),
      col("a", "Rel. Dist. a", n, "none", { def: 0 }), col("b", "Rel. Dist. b", n, "none", { def: 1 }), col("projected", "Projected", "bool", "none", { def: false })],
    { lists: ["member_udls", "member_loads"], belongs: (l, e) => l === "member_udls" || ["udl", "trapezoid"].includes(e.kind || "udl"),
      toRowObj: udlAsLoad, target: distTarget, newObj: () => ({ member_uid: "", kind: "udl", w: 0, w2: 0, a: 0, b: 1, direction: "gravity" }) }),
    plist("frame_point_loads", "Frame Loads - Point", [patCol(), col("member_uid", "Frame", "ref", "none", { ref: "members", required: true }),
      col("direction", "Direction", "enum", "none", { enum: ML_DIRS, def: "gravity" }), col("w", "P", n, "force", { required: true }),
      col("a", "Rel. Dist.", n, "none", { def: 0.5 })], { lists: ["member_loads"], belongs: (l, e) => e.kind === "point", target: mlTarget,
      newObj: () => ({ member_uid: "", kind: "point", w: 0, w2: 0, a: 0.5, b: 1, direction: "gravity" }) }),
    plist("area_loads", "Shell Loads - Uniform", [patCol(), col("region_uid", "Shell", "ref", "none", { ref: "shells", required: true }),
      col("q", "Load", n, "pressure", { required: true }), col("direction", "Direction", "enum", "none", { enum: AREA_DIRS, def: "gravity" }),
      col("projected", "Projected", "bool", "none", { def: false })], { lists: ["area_loads"],
      target: (l, o) => { const out = { region_uid: o.region_uid, q: o.q }; if ((o.direction || "gravity") !== "gravity") out.direction = o.direction;
        if (o.projected) out.projected = true; if (o.joint_pattern != null) out.joint_pattern = o.joint_pattern; return ["area_loads", out]; },
      newObj: () => ({ region_uid: "", q: 0 }) }),
    { key: "load_cases", title: "Load Case Summary", group: G.CASE, tkind: "casesum", editable: false, canAdd: false, canDelete: false, keycol: null,
      cols: [col("name", "Case", "text", "none", { editable: false }), col("type", "Type", "text", "none", { editable: false }), col("run", "Run", "bool", "none", { editable: false })] },
    caseTbl("static_cases", "Load Cases - Linear Static", "cases", [col("geometric", "Geometric Nonlinearity", "enum", "none", { enum: GEOM, def: "linear" }),
      col("pdelta", "P-Delta (legacy)", "bool", "none", { def: false })], () => ({ patterns: {}, pdelta: false, pdelta_gravity: null, geometric: "linear" })),
    child("static_case_loads", "Load Cases - Static Load Assignments", G.CASE, [col("case", "Case", "enum", "none", { enum: names("cases"), required: true }),
      col("pattern", "Load Pattern", "enum", "none", { enum: names("patterns"), required: true }), col("factor", "Scale Factor", n, "none", { required: true })],
    { container: "cases", field: "patterns", parentCol: "case", childCol: "pattern", valueCol: "factor" }),
    caseTbl("rs_cases", "Load Cases - Response Spectrum", "rs_cases", [col("direction", "Direction", "enum", "none", { enum: ["X", "Y"], required: true }),
      col("function", "Function", "enum", "none", { enum: optNames("spectrum_functions"), def: "" }), col("num_modes", "Modes (0=all)", "int", "none", { def: 0 }),
      col("combo_method", "Modal Combo", "enum", "none", { enum: ["CQC", "SRSS", "ABS", "GMC", "NRC10", "DSC"], def: "CQC" }),
      col("damping", "Damping", n, "none", { def: 0.05 }), col("scale", "Scale Factor", n, "none", { def: 1 })],
    () => ({ spectrum: [], num_modes: 0, combo_method: "CQC", damping: 0.05, scale: 1, function: "" })),
    caseTbl("th_cases", "Load Cases - Time History", "th_cases", [col("direction", "Direction", "enum", "none", { enum: ["X", "Y"], required: true }),
      col("function", "Function", "enum", "none", { enum: optNames("th_functions"), def: "" }), col("dt", "Time Step", n, "period", { def: 0 }),
      col("damping", "Damping", n, "none", { def: 0.05 }), col("scale", "Scale Factor", n, "none", { def: 1 }),
      col("nonlinear", "Nonlinear", "bool", "none", { def: false }), col("damping_model", "Damping Model", "enum", "none", { enum: ["rayleigh", "modal"], def: "rayleigh" })],
    () => ({ accel: [], dt: 0, damping: 0.05, scale: 1, function: "" })),
    caseTbl("pushover_cases", "Load Cases - Pushover", "pushover_cases", [col("direction", "Direction", "enum", "none", { enum: ["X", "Y"], required: true }),
      col("target_drift", "Target Drift", n, "none", { def: 0.02 }), col("steps", "Steps", "int", "none", { def: 100 }),
      col("hinges", "Hinges", "enum", "none", { enum: ["column_base", "all_ends", "asce41"], def: "column_base" }),
      col("hardening", "Hardening", n, "none", { def: 0.02 }), col("geometric", "Geometric Nonlinearity", "enum", "none", { enum: GEOM, def: "linear" })],
    () => ({ gravity: {} })),
    caseTbl("staged_cases", "Load Cases - Staged Construction", "staged_cases", [col("pattern", "Load Pattern", "enum", "none", { enum: names("patterns"), required: true })],
      () => ({ stages: "per_story", include_live: {} })),
    caseTbl("buckling_cases", "Load Cases - Buckling", "buckling_cases", [col("num_modes", "Modes", "int", "none", { def: 6 }),
      col("base_case", "Base Case", "enum", "none", { enum: optNames("cases"), def: "", get: o => o.base_case || "", put: (o, v) => { o.base_case = v || null; } })],
    () => ({ gravity: {} })),
    obj("load_combinations", "Load Combination Definitions", G.CASE, [col("name", "Combination", "text", "none", { required: true }),
      col("combo_type", "Type", "enum", "none", { enum: ["add", "envelope", "abs", "srss", "range"], def: "add" })],
    { container: "combos", keycol: "name", onRename: CASC.case, deleteRefs: REFS.case, newObj: () => ({ cases: {}, combo_type: "add" }) }),
    child("combo_members", "Load Combination Members", G.CASE, [col("combo", "Combination", "enum", "none", { enum: names("combos"), required: true }),
      col("case", "Case / Combo", "enum", "none", { enum: comboPool, required: true }), col("factor", "Scale Factor", n, "none", { required: true })],
    { container: "combos", field: "cases", parentCol: "combo", childCol: "case", valueCol: "factor" }),
    child("mass_source", "Mass Source - Load Patterns", G.DEF, [col("pattern", "Load Pattern", "enum", "none", { enum: names("patterns"), required: true }),
      col("factor", "Multiplier", n, "none", { required: true })], { container: null, field: "mass_source", childCol: "pattern", valueCol: "factor" }),
    { key: "mass_source_options", title: "Mass Source - Options", group: G.DEF, tkind: "massopt", editable: true, canAdd: false, canDelete: false, keycol: null,
      cols: [col("mode", "Self Mass Mode", "enum", "none", { enum: ["weight", "element_self_mass"] }),
        ...[["self_mass", "Element Self Mass"], ["patterns", "Additional Mass from Patterns"], ["include_lateral", "Lateral Mass"],
          ["include_vertical", "Vertical Mass"], ["lump_at_stories", "Lump at Stories"]].map(([k, l]) => col(k, l, "bool"))] },
  ];
}
let CAT = null;
const catalogue = () => (CAT = CAT || buildCatalogue());
const table = key => catalogue().find(t => t.key === key);

function rowsOf(t, d) {
  switch (t.tkind) {
    case "object": return objectRows(t, d);
    case "pattern": return patRows(t, d);
    case "child": return childRows(t, d);
    case "grid": return gridRows(d);
    case "joints": return jointRows(d);
    case "casesum": return caseSummaryRows(d);
    case "storydia": { const sd = d.story_diaphragm || {}; return (d.stories || []).map(s => ({ _id: s.name, story: s.name, diaphragm: sd[s.name] || "default" })); }
    case "massopt": { const mo = d.mass_options || {}; const r = { _id: 0, mode: d.mass_source_mode || "weight" }; for (const k of Object.keys(MASS_DEF)) r[k] = !!(mo[k] ?? MASS_DEF[k]); return [r]; }
    case "groupasn": {
      const out = [], L = { members: "frame", shells: "shell", links: "link" };
      for (const [gn, g] of Object.entries(d.groups || {})) for (const k of ["members", "shells", "links"]) for (const u of g[k] || []) out.push({ _id: [gn, k, u], group: gn, type: L[k], object: u });
      return out;
    }
    default: return [];
  }
}
function applyOne(t, d, rows, errs) {
  switch (t.tkind) {
    case "object": return objectApply(t, d, rows, errs);
    case "pattern": return patApply(t, d, rows, errs);
    case "child": return childApply(t, d, rows, errs);
    case "grid": return gridApply(t, d, rows, errs);
    case "joints": errs.push(E(t.key, null, null, "the joint table is derived (read only): edit the object coordinates instead")); return {};
    case "casesum": errs.push(E(t.key, null, null, "the load-case summary is read only: edit the per-type case tables")); return {};
    case "storydia": {
      const n0 = errs.length, nm = new Set(storiesOf(d)), built = {};
      rows.forEach((r, i) => {
        const st = r.story ?? r._id;
        if (!nm.has(st)) { errs.push(E(t.key, i, "story", `unknown story '${st}'`)); return; }
        try { built[st] = parseCell(t.cols[1], r.diaphragm, d); } catch (e) { errs.push(E(t.key, i, "diaphragm", e.message)); }
      });
      if (errs.length > n0) return {};
      const old = d.story_diaphragm || {}, nw = {};
      for (const k of Object.keys(old)) if (built[k] && built[k] !== "default") nw[k] = built[k]; else if (!(k in built)) nw[k] = old[k];
      for (const [k, v] of Object.entries(built)) if (v !== "default" && !(k in nw)) nw[k] = v;
      if (d.story_diaphragm || Object.keys(nw).length) d.story_diaphragm = nw;
      return { rows: rows.length };
    }
    case "massopt": {
      if (rows.length !== 1) { errs.push(E(t.key, null, null, "the mass source options table has exactly one row")); return {}; }
      const n0 = errs.length, vals = {};
      for (const c of t.cols) if (c.key in rows[0]) { try { vals[c.key] = parseCell(c, rows[0][c.key], d); } catch (e) { errs.push(E(t.key, 0, c.key, `${c.label}: ${e.message}`)); } }
      if (errs.length > n0) return {};
      if ("mode" in vals) { if (vals.mode !== (d.mass_source_mode || "weight")) d.mass_source_mode = vals.mode; delete vals.mode; }
      const eff = { ...MASS_DEF, ...(d.mass_options || {}) };
      if (Object.entries(vals).some(([k, v]) => eff[k] !== v)) d.mass_options = { ...eff, ...vals };
      return { rows: 1 };
    }
    case "groupasn": {
      const n0 = errs.length, groups = d.groups || {}, inv = { frame: "members", shell: "shells", link: "links" }, built = {}, seen = new Set();
      rows.forEach((r, i) => {
        if (!(r.group in groups)) { errs.push(E(t.key, i, "group", `unknown group '${r.group}'`)); return; }
        if (!(r.type in inv)) { errs.push(E(t.key, i, "type", `'${r.type}' is not one of ['frame', 'shell', 'link']`)); return; }
        const u = r.object == null ? "" : String(r.object).trim();
        if (!REF_POOL[inv[r.type]](d).has(u)) { errs.push(E(t.key, i, "object", `unknown ${r.type} '${u}'`)); return; }
        const k = `${r.group}\u0001${r.type}\u0001${u}`;
        if (seen.has(k)) { errs.push(E(t.key, i, "object", `${r.type} '${u}' is listed twice in '${r.group}'`)); return; }
        seen.add(k);
        ((built[r.group] = built[r.group] || {})[inv[r.type]] = built[r.group][inv[r.type]] || []).push(u);
      });
      if (errs.length > n0) return {};
      for (const [gn, g] of Object.entries(groups)) for (const k of ["members", "shells", "links"]) {
        const nw = (built[gn] || {})[k] || [];
        if (JSON.stringify(g[k] || []) !== JSON.stringify(nw)) g[k] = nw;
      }
      return { rows: rows.length };
    }
    default: errs.push(E(t.key, null, null, "unsupported table")); return {};
  }
}

/** Minimal model validation mirroring the backend's first checks. */
function validate(d) {
  for (const s of Object.values(d.sections || {})) if (!(s.material in (d.materials || {}))) return `Section ${s.name}: unknown material ${s.material}`;
  for (const s of Object.values(d.shell_sections || {})) if (!(s.material in (d.materials || {}))) return `Shell section ${s.name}: unknown material ${s.material}`;
  for (const m of d.members || []) {
    if (!(m.section in (d.sections || {}))) return `Member ${m.uid}: unknown section ${m.section}`;
    if (Math.hypot(m.pj[0] - m.pi[0], m.pj[1] - m.pi[1], m.pj[2] - m.pi[2]) < 1e-9) return `Member ${m.uid} has zero length`;
  }
  for (const r of d.shells || []) if ((r.corners || []).length < 3) return `Shell ${r.uid}: needs at least 3 corners`;
  for (const [cn, c] of Object.entries(d.cases || {})) for (const p of Object.keys(c.patterns || {})) if (!(p in (d.patterns || {}))) return `Case ${cn}: unknown pattern ${p}`;
  const pool = new Set([...allCases(d), ...Object.keys(d.combos || {})]);
  for (const [cn, c] of Object.entries(d.combos || {})) for (const k of Object.keys(c.cases || {})) if (!pool.has(k)) return `Combo ${cn}: unknown case or combo '${k}'`;
  for (const [n, rc] of Object.entries(d.rs_cases || {})) if (!rc.function && !(rc.spectrum || []).length) return `RS case ${n}: needs a spectrum or a function`;
  return null;
}
function locate(msg, tables) {
  for (const [key, rows] of Object.entries(tables)) {
    const t = table(key);
    if (!t) continue;
    for (const ck of t.cols.map(c => c.key).filter(k => ["name", "uid", "member_uid", "region_uid", "combo", "case", "group", "pattern", "story"].includes(k)))
      for (const [i, r] of rows.entries()) {
        const v = r && r[ck];
        if (typeof v === "string" && v && new RegExp(`(?<![\\w-])${v.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\w-])`).test(msg)) return [key, i, ck];
      }
  }
  return [null, null, null];
}
function translateRows(t, rows, renamed) {
  const pm = renamed.patterns || {};
  return rows.map(r => {
    if (!r || typeof r !== "object") return r;
    r = { ...r };
    for (const c of t.cols) {
      let mp = null;
      if (c.refc === "*cases") { mp = {}; for (const k of [...CASE_CONTS, "combos"]) Object.assign(mp, renamed[k] || {}); }
      else if (c.refc) mp = renamed[c.refc];
      if (mp && typeof r[c.key] === "string" && r[c.key] in mp) r[c.key] = mp[r[c.key]];
    }
    if (t.tkind === "pattern" && Array.isArray(r._id) && r._id[0] in pm) r._id = [pm[r._id[0]], ...r._id.slice(1)];
    return r;
  });
}
export function mockApplyTables(model, tables) {
  const order = catalogue().map(t => t.key);
  const work = clone(model), errs = [], summary = {}, renamed = {};
  for (const key of Object.keys(tables).sort((a, b) => order.indexOf(a) - order.indexOf(b))) {
    const t = table(key);
    if (!t) { errs.push(E(key, null, null, `unknown table '${key}'`)); continue; }
    if (!Array.isArray(tables[key])) { errs.push(E(key, null, null, "rows must be a list")); continue; }
    const rows = Object.keys(renamed).length ? translateRows(t, tables[key], renamed) : tables[key];
    summary[key] = applyOne(t, work, rows, errs);
    for (const [o, nn] of Object.entries((summary[key] || {}).renamed || {})) (renamed[t.container] = renamed[t.container] || {})[o] = nn;
  }
  if (errs.length) return { errors: errs };
  const msg = validate(work);
  if (msg) { const [tk, row, c] = locate(msg, tables); return { errors: [E(tk, row, c, msg)] }; }
  return { model: work, summary };
}

/* ------------------------------------------------------------ CSV + zip */
const csvQ = s => /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
const csvCell = (type, v) => v == null ? "" : typeof v === "boolean" ? (v ? "TRUE" : "FALSE")
  : type === "points" && Array.isArray(v) ? v.map(p => p.join(",")).join("; ") : String(v);
export function mockTableCsv(model, key) {
  const t = table(key);
  const lines = [["_id", ...t.cols.map(c => c.key)].map(csvQ).join(",")];
  for (const r of rowsOf(t, model)) lines.push([JSON.stringify(r._id), ...t.cols.map(c => csvCell(c.type, r[c.key]))].map(csvQ).join(","));
  return lines.join("\n") + "\n";
}
export function parseCsv(text) {
  const out = []; let row = [], cur = "", q = false;
  text = text.replace(/^﻿/, "");
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) { if (ch === '"') { if (text[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += ch; continue; }
    if (ch === '"') q = true;
    else if (ch === ",") { row.push(cur); cur = ""; }
    else if (ch === "\n" || ch === "\r") { if (ch === "\r" && text[i + 1] === "\n") i++; row.push(cur); out.push(row); row = []; cur = ""; }
    else cur += ch;
  }
  if (cur || row.length) { row.push(cur); out.push(row); }
  return out;
}
export function mockCsvRows(model, key, text) {
  const t = table(key), recs = parseCsv(text);
  if (!recs.length) return [];
  const by = {};
  for (const c of t.cols) { by[c.key] = c; by[c.label.toLowerCase()] = c; }
  const keys = recs[0].map(h => { const h0 = h.replace(/\s*\[[^\]]*\]\s*$/, "").trim(); return h0 === "_id" ? "_id" : ((by[h0] || by[h0.toLowerCase()] || {}).key ?? null); });
  const rows = [];
  for (const rec of recs.slice(1)) {
    if (!rec.some(s => s.trim())) continue;
    const r = {};
    rec.forEach((s, j) => {
      const k = keys[j];
      if (!k) return;
      s = s.trim();
      if (k === "_id") { if (s) { try { r._id = JSON.parse(s); } catch { r._id = s; } } return; }
      const c = by[k];
      if (!c.editable && c.key !== t.keycol) return;
      if (c.type === "number" || c.type === "int") r[k] = s === "" ? null : (isFinite(+s) ? +s : s);
      else if (c.type === "bool") r[k] = TRUE.has(s.toLowerCase()) ? true : FALSE.has(s.toLowerCase()) ? false : s;
      else r[k] = s;
    });
    rows.push(r);
  }
  return rows;
}
// --- tiny zip (STORE writer; STORE + deflate-raw reader)
const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
const crc32 = b => { let c = 0xffffffff; for (const x of b) c = CRC[(c ^ x) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
export function zipStore(files) {
  const enc = new TextEncoder(), parts = [], central = [];
  let off = 0;
  for (const [name, text] of files) {
    const nb = enc.encode(name), data = typeof text === "string" ? enc.encode(text) : text, crc = crc32(data);
    const h = new DataView(new ArrayBuffer(30));
    h.setUint32(0, 0x04034b50, true); h.setUint16(4, 20, true); h.setUint32(14, crc, true); h.setUint32(18, data.length, true); h.setUint32(22, data.length, true); h.setUint16(26, nb.length, true);
    parts.push(new Uint8Array(h.buffer), nb, data);
    const c = new DataView(new ArrayBuffer(46));
    c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint32(16, crc, true); c.setUint32(20, data.length, true); c.setUint32(24, data.length, true);
    c.setUint16(28, nb.length, true); c.setUint32(42, off, true);
    central.push(new Uint8Array(c.buffer), nb);
    off += 30 + nb.length + data.length;
  }
  const csize = central.reduce((s, p) => s + p.length, 0);
  const e = new DataView(new ArrayBuffer(22));
  e.setUint32(0, 0x06054b50, true); e.setUint16(8, files.length, true); e.setUint16(10, files.length, true); e.setUint32(12, csize, true); e.setUint32(16, off, true);
  return new Blob([...parts, ...central, new Uint8Array(e.buffer)], { type: "application/zip" });
}
export async function unzip(buf) {
  const u8 = new Uint8Array(buf), dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength), out = new Map(), dec = new TextDecoder();
  let eo = -1;
  for (let i = u8.length - 22; i >= 0; i--) if (dv.getUint32(i, true) === 0x06054b50) { eo = i; break; }
  if (eo < 0) throw new Error("not a zip archive");
  let p = dv.getUint32(eo + 16, true);
  const n = dv.getUint16(eo + 10, true);
  for (let k = 0; k < n; k++) {
    const method = dv.getUint16(p + 10, true), csz = dv.getUint32(p + 20, true), nl = dv.getUint16(p + 28, true),
      xl = dv.getUint16(p + 30, true), cl = dv.getUint16(p + 32, true), lo = dv.getUint32(p + 42, true);
    const name = dec.decode(u8.subarray(p + 46, p + 46 + nl));
    const ds = lo + 30 + dv.getUint16(lo + 26, true) + dv.getUint16(lo + 28, true);
    let data = u8.subarray(ds, ds + csz);
    if (method === 8) data = new Uint8Array(await new Response(new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"))).arrayBuffer());
    out.set(name, dec.decode(data));
    p += 46 + nl + xl + cl;
  }
  return out;
}
const sig = m => { const s = JSON.stringify(m); let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return `mock-${h}-${s.length}`; };
export function mockExportZip(model) {
  const files = [["_meta.json", JSON.stringify({ format: "skyframe-modeltables", version: 1, units: "SI (kN, m, kPa, tonne, degC)", model_sha256: sig(model) })]];
  const idx = ["table,title,group,editable,rows"];
  for (const t of catalogue()) { files.push([`${t.key}.csv`, mockTableCsv(model, t.key)]); idx.push([t.key, t.title, t.group, t.editable ? "True" : "False", rowsOf(t, model).length].map(String).map(csvQ).join(",")); }
  files.push(["_index.csv", idx.join("\n") + "\n"]);
  return zipStore(files);
}
export async function mockImportZip(model, buf) {
  let files;
  try { files = await unzip(buf); } catch { return { errors: [E(null, null, null, "not a zip archive")] }; }
  let sameModel = false;
  try { sameModel = JSON.parse(files.get("_meta.json") || "{}").model_sha256 === sig(model); } catch { /* foreign */ }
  const tables = {};
  for (const t of catalogue()) {
    if (!t.editable || !files.has(`${t.key}.csv`)) continue;
    const rows = mockCsvRows(model, t.key, files.get(`${t.key}.csv`));
    if (!sameModel) for (const r of rows) delete r._id;
    tables[t.key] = rows;
  }
  if (!Object.keys(tables).length) return { errors: [E(null, null, null, "the archive contains no editable table CSV")] };
  return mockApplyTables(model, tables);
}

/* ------------------------------------------------------------ router */
function meta(t, d) { return { key: t.key, title: t.title, group: t.group, editable: t.editable, can_add: t.canAdd, can_delete: t.canDelete, key_column: t.keycol || null }; }
function colMeta(c, d) {
  const o = { key: c.key, label: c.label, quantity: c.quantity, unit: QUANTITIES[c.quantity], type: c.type, editable: c.editable };
  const en = opts(c, d);
  if (en) o.enum = en;
  if (c.ref) o.ref = c.ref;
  if (c.required) o.required = true;
  if (c.optional) o.optional = true;
  if (c.def != null && c.editable) o.default = c.def;
  return o;
}
function fail(errors) {
  const f = errors[0];
  const where = f.table ? ` [${f.table}${f.row != null ? ` row ${f.row + 1}` : ""}${f.col ? `, ${f.col}` : ""}]` : "";
  const e = new Error(`${errors.length} error${errors.length !== 1 ? "s" : ""}: ${f.message}${where}`);
  e.errors = errors;
  throw e;
}
/** ?mock=1 router: same paths / bodies / shapes as the live endpoints. */
export async function mockDbEdit(model, path, body = {}) {
  const m = body.model || model;
  const p = path.replace(/^\/api\/modeltables\/?/, "");
  if (p === "list") return { tables: catalogue().map(t => ({ ...meta(t, m), rows: rowsOf(t, m).length })), quantities: { ...QUANTITIES } };
  if (p === "apply") { const r = mockApplyTables(m, body.tables || {}); if (r.errors) fail(r.errors); return { ok: true, model: r.model, summary: r.summary }; }
  if (p === "export") return { filename: "model_tables.zip", blob: mockExportZip(m) };
  if (p === "import") { const r = await mockImportZip(m, body.buffer); if (r.errors) fail(r.errors); return { ok: true, model: r.model, summary: r.summary }; }
  const [key, op] = p.split("/");
  const t = table(decodeURIComponent(key));
  if (!t) { const e = new Error(`unknown table '${key}'`); e.status = 404; throw e; }
  if (!op) return { ...meta(t, m), columns: t.cols.map(c => colMeta(c, m)), rows: rowsOf(t, m) };
  if (op === "apply") { const r = mockApplyTables(m, { [t.key]: body.rows || [] }); if (r.errors) fail(r.errors); return { ok: true, model: r.model, summary: r.summary }; }
  if (op === "csv") return { filename: `${t.key}.csv`, csv: mockTableCsv(m, t.key) };
  if (op === "csv_import") {
    const rows = mockCsvRows(m, t.key, body.csv || "");
    if (!body.apply) return { rows };
    const r = mockApplyTables(m, { [t.key]: rows }); if (r.errors) fail(r.errors); return { ok: true, model: r.model, summary: r.summary };
  }
  throw new Error(`no mock for ${path}`);
}
