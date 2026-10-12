/* SkyFrame Wave C — ?mock=1 port of skyframe/core/modeltables_wave_c.py
   (Interactive Database Editing): the same table keys, columns, row
   identity, default-removal rules and text formats (numbers inside text
   cells are Number#toString on both sides), so the mock produces identical
   rows and round-trips byte-identically like the backend.

   Exported:
     WC_QUANTITIES                 extra column quantities (merged by the hook)
     wcMockExtend(T, H)            hook in js/mock_dbedit.js: appends the
                                   staged-case time-dependent columns and the
                                   Wave C tables (H = its helpers)            */

import { buSectionProperties as wcBuProps, buFrameSection as wcBuFrame } from "./builtup_geom.js";

export const WC_QUANTITIES = { temp_delta: "degC", temp_gradient: "degC/m" };

const DOF6 = ["UX", "UY", "UZ", "RX", "RY", "RZ"];
const PDELTA_METHODS = ["none", "non_iterative_mass", "iterative_loads"];
const PD_DEF = () => ({ method: "none", load_factors: {}, max_iterations: 2, tolerance: 0.001, include_in: "all_linear" });
const SOLVERS = ["auto", "band", "umfpack", "mumps", "sparse_general"];
const TESTS = ["NormDispIncr", "EnergyIncr", "NormUnbalance"];
const ALGOS = ["Newton", "ModifiedNewton", "KrylovNewton", "NewtonLineSearch", "BFGS"];
const AO_DEF = () => ({ solver: "auto", eigen_solver: "auto", size_warnings: true, parallel: "auto",
  convergence: { mode: "legacy", algorithms: [...ALGOS], tests: [{ type: "NormDispIncr" }, { type: "EnergyIncr", tolerance: 1e-10, max_iterations: 100 }], max_halvings: 4 } });
const DMP_DEF = () => ({ rayleigh_on_links: "none", rayleigh_stiffness_basis: "committed" });
const MEMBER_HINGE = ["none", "auto_m3", "fiber_pmm"];
const KEEP = ["mod_A", "mod_I33", "mod_I22", "mod_J", "As2", "As3", "shear_deformation", "mod_As2", "mod_As3", "mod_mass", "mod_weight"];

const jsonEq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const isNum = v => typeof v === "number" && isFinite(v);
/** numeric-aware deep equality (1 == 1.0; key order significant). */
function sameNum(a, b) {
  if (isNum(a) && isNum(b)) return a === b;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => sameNum(x, b[i]));
  if (a && b && typeof a === "object" && typeof b === "object") {
    const ka = Object.keys(a), kb = Object.keys(b);
    return ka.length === kb.length && ka.every((k, i) => k === kb[i] && sameNum(a[k], b[k]));
  }
  return a === b;
}
const numText = vals => vals.map(String).join(", ");
function parseNums(s) {
  const out = [];
  for (const p of String(s ?? "").trim().split(/[,;\s]+/)) {
    if (!p) continue;
    const x = Number(p);
    if (!isFinite(x)) throw new Error(`'${p}' is not a number`);
    out.push(x);
  }
  return out;
}
const textList = s => String(s ?? "").split(/[,;]/).map(x => x.trim()).filter(Boolean);
const putOpt = key => (o, v) => { if (v == null || v === "") delete o[key]; else o[key] = v; };
const putDef = (key, def) => (o, v) => { if (v === def || v == null) delete o[key]; else o[key] = v; };

/* ---------------------------------------------------------------- normalizers */
function pdRead(d) {
  const o = { ...PD_DEF(), ...(d.pdelta_options && typeof d.pdelta_options === "object" ? d.pdelta_options : {}) };
  if (o.load_factors && typeof o.load_factors === "object") o.load_factors = { ...o.load_factors };
  return o;
}
function pdStore(d, o) {
  if (sameNum(o, PD_DEF()) && Object.keys(o.load_factors).length === 0 && o.method === "none") { delete d.pdelta_options; return; }
  d.pdelta_options = { method: o.method, load_factors: { ...o.load_factors }, max_iterations: o.max_iterations, tolerance: o.tolerance, include_in: o.include_in };
}
function aoNorm(stored) {
  const out = AO_DEF();
  const s = stored && typeof stored === "object" ? stored : {};
  for (const k of ["solver", "eigen_solver", "size_warnings", "parallel"]) if (k in s) out[k] = s[k];
  if (s.convergence && typeof s.convergence === "object") Object.assign(out.convergence, JSON.parse(JSON.stringify(s.convergence)));
  return out;
}
function aoStore(d, stored) {
  if (!stored || !Object.keys(stored).length || sameNum(aoNorm(stored), AO_DEF())) delete d.analysis_options;
  else d.analysis_options = stored;
}

/* ---------------------------------------------------------------- mirrors */
function polyIntegrals(v) {
  let A = 0, Sy = 0, Sz = 0, Iyy = 0, Izz = 0;
  for (let i = 0; i < v.length; i++) {
    const [y0, z0] = v[i], [y1, z1] = v[(i + 1) % v.length], c = y0 * z1 - y1 * z0;
    A += c / 2; Sy += (y0 + y1) * c / 6; Sz += (z0 + z1) * c / 6;
    Iyy += (y0 * y0 + y0 * y1 + y1 * y1) * c / 12; Izz += (z0 * z0 + z0 * z1 + z1 * z1) * c / 12;
  }
  return { A, Sy, Sz, Iyy, Izz };
}
function designerMirror(ds, prev) {
  let A = 0, Sy = 0, Sz = 0, Iyy = 0, Izz = 0, ymin = Infinity, ymax = -Infinity, zmin = Infinity, zmax = -Infinity;
  for (const p of ds.polygons || []) {
    const g = polyIntegrals(p.vertices || []), s = (g.A < 0 ? -1 : 1) * (p.hole ? -1 : 1);
    A += s * g.A; Sy += s * g.Sy; Sz += s * g.Sz; Iyy += s * g.Iyy; Izz += s * g.Izz;
    for (const [y, z] of p.vertices || []) { ymin = Math.min(ymin, y); ymax = Math.max(ymax, y); zmin = Math.min(zmin, z); zmax = Math.max(zmax, z); }
  }
  if (!(A > 0)) throw new Error(`Designer section ${ds.name}: net area must be > 0`);
  const cy = Sy / A, cz = Sz / A, I33 = Math.max(Izz - A * cz * cz, 0), I22 = Math.max(Iyy - A * cy * cy, 0);
  const out = { ...(prev || {}), name: ds.name, material: ds.material, A, I33, I22, J: A ** 4 / (40 * (I33 + I22)), b: ymax - ymin, h: zmax - zmin };
  return out;
}
function remirror(d, kind, names) {
  if (!names.length) return;
  d.sections = d.sections || {};
  for (const n of names) {
    const prev = d.sections[n];
    let sec;
    if (kind === "builtup") {
      const s = { ...d.builtup_sections[n], name: n };
      sec = wcBuFrame(s, wcBuProps(s, d.materials || {}), prev);
    } else sec = designerMirror({ ...d.designer_sections[n], name: n }, prev);
    if (prev) for (const k of KEEP) if (k in prev) sec[k] = prev[k];
    d.sections[n] = sec;
  }
}

/* ---------------------------------------------------------------- the hook */
export function wcMockExtend(T, H) {
  const { col, obj, plist, child, names, optNames, getv, parseCell, rowValues, same, E, clone, RowErr, mergeOrdered, CASC, REFS, G, pt } = H;
  const n = "number";
  const G_AOPT = "Model Definition > Analysis Options";
  const err = (t, i, c, m) => E(t.key, i, c, m);
  const cell = (t, i, key, raw, d, errs) => {
    const c = t.cols.find(x => x.key === key);
    try { return { v: parseCell(c, raw, d) }; } catch (e) { errs.push(err(t, i, key, `${c.label}: ${e.message}`)); return null; }
  };
  const custom = (key, title, group, cols, rows, apply, o = {}) => ({ key, title, group, cols, tkind: "wc", editable: true, canAdd: true, canDelete: true, keycol: null,
    ...o, w8rows: rows, w8apply: apply });

  /* ---- one-row options tables */
  const options = (key, title, group, cols, read, write) => custom(key, title, group, cols,
    (t, d) => { const cur = read(d); const r = { _id: 0 }; for (const c of cols) r[c.key] = cur[c.key] ?? null; return [r]; },
    (t, d, rows, errs) => {
      if (rows.length !== 1 || !rows[0] || typeof rows[0] !== "object") { errs.push(err(t, null, null, `the ${t.title} table has exactly one row`)); return {}; }
      const n0 = errs.length, cur = read(d), changed = {};
      for (const c of cols) {
        if (!c.editable || !(c.key in rows[0])) continue;
        const raw = rows[0][c.key];
        if (typeof raw === typeof cur[c.key] && same(raw, cur[c.key])) continue;
        const p = cell(t, 0, c.key, raw, d, errs);
        if (p && !same(p.v, cur[c.key])) changed[c.key] = p.v;
      }
      if (errs.length > n0) return {};
      if (Object.keys(changed).length) { try { write(d, changed); } catch (e) { errs.push(err(t, 0, null, e.message)); return {}; } }
      return { rows: 1, changed: Object.keys(changed).sort() };
    }, { canAdd: false, canDelete: false });

  const pdWrite = (d, ch) => { const o = pdRead(d); Object.assign(o, ch); pdStore(d, o); };
  const aoRead = d => {
    const o = aoNorm(d.analysis_options || {}), c = o.convergence;
    return { solver: o.solver, eigen_solver: o.eigen_solver, size_warnings: !!o.size_warnings,
      parallel: typeof o.parallel === "string" ? o.parallel : String(o.parallel), conv_mode: c.mode, algorithms: c.algorithms.join(", "), max_halvings: c.max_halvings };
  };
  const aoWrite = (d, ch) => {
    const stored = clone(d.analysis_options || {}), conv = { ...(stored.convergence || {}) };
    for (const [k, v] of Object.entries(ch)) {
      if (k === "parallel") {
        const s = String(v).trim();
        if (s === "auto" || s === "off") stored.parallel = s;
        else if (/^[-+]?\d+$/.test(s)) stored.parallel = parseInt(s, 10);
        else throw new Error("Parallel: use auto, off or 1..8");
      } else if (k === "conv_mode") conv.mode = v;
      else if (k === "algorithms") conv.algorithms = textList(v);
      else if (k === "max_halvings") conv.max_halvings = v;
      else stored[k] = v;
    }
    if (Object.keys(conv).length) stored.convergence = conv;
    aoStore(d, stored);
  };
  const dmpRead = d => ({ ...DMP_DEF(), ...(d.damping_options || {}) });
  const dmpWrite = (d, ch) => { const o = { ...dmpRead(d), ...ch }; const out = { rayleigh_on_links: o.rayleigh_on_links, rayleigh_stiffness_basis: o.rayleigh_stiffness_basis };
    if (sameNum(out, DMP_DEF())) delete d.damping_options; else d.damping_options = out; };
  const dofRead = d => { const a = new Set(d.active_dof || DOF6); return Object.fromEntries(DOF6.map(k => [k, a.has(k)])); };

  /* ---- staged construction: time-dependent columns */
  const tdGet = key => o => {
    const v = (o.time_dependent || {})[key];
    if (key === "aging") return v == null ? "" : (v ? "true" : "false");
    if (key === "materials") return v == null ? "" : v.map(String).join(", ");
    if (key === "output_times") return v == null ? "" : numText(v);
    if (key === "method") return v == null ? "" : String(v);
    return v ?? null;
  };
  const tdPut = key => (o, v) => {
    const td = { ...(o.time_dependent || {}) };
    if (v == null || v === "") delete td[key];
    else if (key === "aging") td[key] = v === "true";
    else if (key === "materials") td[key] = textList(v);
    else if (key === "output_times") td[key] = parseNums(v);
    else td[key] = v;
    o.time_dependent = Object.keys(td).length ? td : null;
  };
  const stg = T.find(t => t.key === "staged_cases");
  stg.cols.push(
    col("stage_mode", "Stages", "text", "none", { editable: false, get: o => { const s = o.stages ?? "per_story"; return typeof s === "string" ? s : `user (${s.length})`; } }),
    col("td_days_per_story", "TD Days per Story", n, "none", { optional: true, get: tdGet("days_per_story"), put: tdPut("days_per_story") }),
    col("td_creep_coeff", "TD Creep Coeff", n, "none", { optional: true, get: tdGet("creep_coeff"), put: tdPut("creep_coeff") }),
    col("td_shrinkage", "TD Shrinkage", n, "none", { optional: true, get: tdGet("shrinkage"), put: tdPut("shrinkage") }),
    col("td_aging", "TD Aging", "enum", "none", { enum: ["", "true", "false"], def: "", get: tdGet("aging"), put: tdPut("aging") }),
    col("td_t_eval", "TD Eval Time (days)", n, "none", { optional: true, get: tdGet("t_eval"), put: tdPut("t_eval") }),
    col("td_materials", "TD Materials", "text", "none", { def: "", get: tdGet("materials"), put: tdPut("materials") }),
    col("td_method", "TD Method", "enum", "none", { enum: ["", "aaem", "step"], def: "", get: tdGet("method"), put: tdPut("method") }),
    col("td_steps_per_stage", "TD Steps per Stage", "int", "none", { optional: true, get: tdGet("steps_per_stage"), put: tdPut("steps_per_stage") }),
    col("td_output_times", "TD Output Times", "text", "none", { def: "", get: tdGet("output_times"), put: tdPut("output_times") }));

  const userStages = sc => Array.isArray(sc.stages) ? sc.stages : [];
  const stagesTbl = custom("staged_stages", "Load Cases - Staged Construction - Stages", G.CASE, [
    col("case", "Case", "enum", "none", { enum: names("staged_cases"), required: true }),
    col("stage", "Stage", "text", "none", { required: true }),
    col("duration_days", "Duration (days)", n, "none", { def: 0 }),
  ], (t, d) => {
    const out = [];
    for (const [cn, sc] of Object.entries(d.staged_cases || {})) userStages(sc).forEach((st, k) =>
      out.push({ _id: [cn, k], case: cn, stage: st.name, duration_days: st.duration_days ?? 0 }));
    return out;
  }, (t, d, rows, errs) => {
    const n0 = errs.length, cases = d.staged_cases || {}, built = {}, seen = new Set();
    rows.forEach((row, i) => {
      const cn = typeof row.case === "string" ? row.case.trim() : row.case;
      if (!(cn in cases)) { errs.push(err(t, i, "case", `unknown staged case '${cn}'`)); return; }
      const rid = row._id; let orig = null;
      const us = userStages(cases[cn]);
      if (Array.isArray(rid) && rid.length === 2 && rid[0] === cn && Number.isInteger(rid[1]) && rid[1] >= 0 && rid[1] < us.length && !seen.has(`${cn}\u0001${rid[1]}`)) {
        orig = us[rid[1]]; seen.add(`${cn}\u0001${rid[1]}`);
      }
      const a = cell(t, i, "stage", row.stage, d, errs); if (!a) return;
      const b = cell(t, i, "duration_days", row.duration_days ?? 0, d, errs); if (!b) return;
      if (!a.v) { errs.push(err(t, i, "stage", "Stage is required")); return; }
      let st;
      if (orig) {
        st = orig;
        if (orig.name !== a.v || !same(orig.duration_days ?? 0, b.v)) { st = { ...orig, name: a.v }; if (!same(orig.duration_days ?? 0, b.v)) st.duration_days = b.v; }
      } else st = { name: a.v, duration_days: b.v, operations: [] };
      (built[cn] = built[cn] || []).push(st);
    });
    if (errs.length > n0) return {};
    let changed = 0;
    for (const [cn, sc] of Object.entries(cases)) {
      const old = userStages(sc), nw = built[cn] || [];
      if (old.length === nw.length && old.every((s, k) => s === nw[k])) continue;
      sc.stages = nw.length ? nw : "per_story"; changed++;
    }
    return { rows: rows.length, cases_changed: changed };
  });
  const OP_ORDER = { add: ["op", "group", "age_days"], remove: ["op", "group"], load: ["op", "group", "pattern", "scale"] };
  const opsTbl = custom("staged_operations", "Load Cases - Staged Construction - Operations", G.CASE, [
    col("case", "Case", "enum", "none", { enum: names("staged_cases"), required: true }),
    col("stage", "Stage", "text", "none", { required: true }),
    col("op", "Operation", "enum", "none", { enum: ["add", "remove", "load"], required: true }),
    col("group", "Group", "enum", "none", { enum: optNames("groups"), required: true }),
    col("age_days", "Age at Add (days)", n, "none", { optional: true }),
    col("pattern", "Load Pattern", "enum", "none", { enum: optNames("patterns"), def: "" }),
    col("scale", "Scale Factor", n, "none", { optional: true }),
  ], (t, d) => {
    const out = [];
    for (const [cn, sc] of Object.entries(d.staged_cases || {})) userStages(sc).forEach((st, k) => (st.operations || []).forEach((op, j) =>
      out.push({ _id: [cn, k, j], case: cn, stage: st.name, op: op.op, group: op.group, age_days: op.age_days ?? null, pattern: op.pattern || "", scale: op.scale ?? null })));
    return out;
  }, (t, d, rows, errs) => {
    const n0 = errs.length, cases = d.staged_cases || {}, built = new Map();
    rows.forEach((row, i) => {
      const cn = typeof row.case === "string" ? row.case.trim() : row.case;
      const stages = cn in cases ? userStages(cases[cn]) : null;
      if (!stages || !stages.length) { errs.push(err(t, i, "case", `'${cn}' is not a staged case with user-defined stages`)); return; }
      const sn = typeof row.stage === "string" ? row.stage.trim() : row.stage;
      const idx = stages.findIndex(s => s.name === sn);
      if (idx < 0) { errs.push(err(t, i, "stage", `unknown stage '${sn}' of '${cn}'`)); return; }
      const vals = {}; let bad = false;
      for (const c of t.cols) { if (c.key === "case" || c.key === "stage") continue; const p = cell(t, i, c.key, row[c.key], d, errs); if (!p) bad = true; else vals[c.key] = p.v; }
      if (bad) return;
      if (!vals.group) { errs.push(err(t, i, "group", "Group is required")); return; }
      if (vals.op === "load" && !vals.pattern) { errs.push(err(t, i, "pattern", "a load operation needs a Load Pattern")); return; }
      let op = {};
      for (const k of OP_ORDER[vals.op]) { const v = vals[k]; if (v == null || v === "") continue; op[k] = v; }
      const rid = row._id;
      if (Array.isArray(rid) && rid.length === 3 && rid[0] === cn && rid[1] === idx) {
        const ops0 = stages[idx].operations || [];
        if (Number.isInteger(rid[2]) && rid[2] >= 0 && rid[2] < ops0.length && sameNum(ops0[rid[2]], op)) op = ops0[rid[2]];
      }
      const key = `${cn}\u0001${idx}`;
      if (!built.has(key)) built.set(key, []);
      built.get(key).push(op);
    });
    if (errs.length > n0) return {};
    let changed = 0;
    for (const [cn, sc] of Object.entries(cases)) userStages(sc).forEach((st, k) => {
      const old = st.operations || [], nw = built.get(`${cn}\u0001${k}`) || [];
      if (old.length === nw.length && old.every((o, j) => o === nw[j])) return;
      st.operations = nw; changed++;
    });
    return { rows: rows.length, stages_changed: changed };
  });

  /* ---- pattern-list loads */
  const thermalTarget = (l, o) => { const out = { member_uid: o.member_uid, dT: o.dT ?? 0 }; for (const k of ["grad2", "grad3"]) if (o[k]) out[k] = o[k]; return ["thermal_loads", out]; };

  /* ---- cascades */
  const jpCascade = (d, mp) => { for (const p of Object.values(d.patterns || {})) for (const a of p.area_loads || []) if (typeof a.joint_pattern === "string" && a.joint_pattern in mp) a.joint_pattern = mp[a.joint_pattern]; };
  const jpRefs = (d, g) => { for (const [pn, p] of Object.entries(d.patterns || {})) for (const a of p.area_loads || []) if (g.has(a.joint_pattern)) return `joint pattern '${a.joint_pattern}' is used by an area load of pattern '${pn}'`; return null; };
  function* towerUsers(d) {
    for (const s of d.stories || []) yield [s, `story '${s.name}'`];
    for (const m of d.members || []) yield [m, `frame '${m.uid}'`];
    for (const r of d.shells || []) yield [r, `shell '${r.uid}'`];
    for (const j of d.joint_towers || []) yield [j, `joint ${JSON.stringify(j.point)}`];
  }
  const towerCascade = (d, mp) => {
    for (const [o] of towerUsers(d)) if (o.tower in mp) o.tower = mp[o.tower];
    for (const k of ["pushover_cases", "nonlinear_static_cases"]) for (const c of Object.values(d[k] || {})) if (c.control_tower in mp) c.control_tower = mp[c.control_tower];
  };
  const towerRefs = (d, g) => { for (const [o, w] of towerUsers(d)) if (g.has(o.tower)) return `tower '${o.tower}' is used by ${w}`; return null; };
  const towersEnum = d => (d.towers || []).map(t => t.name); towersEnum.refc = "towers";
  const gridsOpt = d => ["", ...(d.grid_systems || []).map(g => g.name)];
  const npNames = d => Object.entries(d.sections || {}).filter(([, s]) => s.kind === "nonprismatic").map(([k]) => k); npNames.refc = "sections";
  const prisNames = d => Object.entries(d.sections || {}).filter(([, s]) => (s.kind || "prismatic") !== "nonprismatic").map(([k]) => k); prisNames.refc = "sections";
  const hingeTargets = d => [...MEMBER_HINGE.slice(1), ...Object.keys(d.hinge_properties || {})];
  const mirrorRename = (d, mp) => {
    d.sections = Object.fromEntries(Object.entries(d.sections || {}).map(([k, v]) => [mp[k] ?? k, v]));
    for (const [k, v] of Object.entries(d.sections)) if (Object.values(mp).includes(k)) v.name = k;
    CASC.section(d, mp);
    for (const s of Object.values(d.sections || {})) for (const seg of s.segments || []) for (const k of ["start_section", "end_section"]) if (k in seg) seg[k] = mp[seg[k]] ?? seg[k];
  };
  const mirrorDelete = (d, gone) => { for (const k of gone) if (d.sections) delete d.sections[k]; };

  /* ---- list-child tables (port of modeltables_ext.ListChildTable) */
  const lcRows = (t, d) => {
    const out = [];
    for (const [pk, p] of t.parents(d)) (p[t.field] || []).forEach((e, k) => {
      const r = { _id: [pk, "", k], [t.parentCol]: pk };
      for (const c of t.cols) if (c.key !== t.parentCol) r[c.key] = getv(c, clone(e), d);
      out.push(r);
    });
    return out;
  };
  const lcApply = (t, d, rows, errs) => {
    const n0 = errs.length, parents = new Map(t.parents(d)), orig = new Map(), built = new Map(), seen = new Set();
    for (const [pk, p] of parents) (p[t.field] || []).forEach((e, k) => orig.set(JSON.stringify([pk, "", k]), e));
    const before = clone(d[t.container] || {});
    rows.forEach((row, i) => {
      const pk = typeof row[t.parentCol] === "string" ? row[t.parentCol].trim() : row[t.parentCol];
      if (!parents.has(pk)) { errs.push(err(t, i, t.parentCol, `unknown ${t.cols.find(c => c.key === t.parentCol).label} '${pk}'`)); return; }
      let rid = Array.isArray(row._id) && row._id.length === 3 ? JSON.stringify(row._id) : null;
      if (rid && (!orig.has(rid) || seen.has(rid))) rid = null;
      const base = rid ? clone(orig.get(rid)) : t.newEntry(d);
      rowValues(t, i, row, base, d, errs, !rid, [t.parentCol]);
      if (rid) seen.add(rid);
      if (!built.has(pk)) built.set(pk, []);
      built.get(pk).push(base);
    });
    if (errs.length > n0) return {};
    let changed = 0;
    for (const [pk, p] of parents) {
      const nw = built.get(pk) || [];
      if (jsonEq(p[t.field] || [], nw)) continue;
      p[t.field] = nw; changed++;
    }
    if (t.mirror) {
      const ch = Object.entries(d[t.container] || {}).filter(([k, o]) => !jsonEq(before[k], o)).map(([k]) => k);
      try { remirror(d, t.mirror, ch); } catch (e) { errs.push(err(t, null, null, e.message)); return {}; }
    }
    return { rows: rows.length, parents_changed: changed };
  };
  const listChild = (key, title, group, cols, o) => custom(key, title, group, cols, lcRows, lcApply,
    { parents: d => Object.entries(d[o.container] || {}), ...o });

  /* ---- mirrored definition tables (built-up / designer) */
  const mirrorObj = (key, title, cols, o) => {
    const t = obj(key, title, G.DEF, cols, { keycol: "name", onDelete: mirrorDelete,
      onRename: (d, mp) => { mirrorRename(d, mp); remirror(d, o.mirror, Object.values(mp)); },
      deleteRefs: (d, g) => { for (const m of d.members || []) if (g.has(m.section)) return `section '${m.section}' is used by frame '${m.uid}'`; return null; }, ...o });
    t.post = (d, pairs, orig) => {   // renamed rows are re-derived after the rename (key order kept)
      const ch = [];
      pairs.forEach(([, rid, ob]) => { if (rid == null || (rid === ob.name && !jsonEq(orig.get(rid), ob))) ch.push(ob.name); });
      remirror(d, o.mirror, ch);
    };
    return t;
  };

  /* ---- member / shell attribute tables */
  const attrTable = (key, title, cols, container, attr, objcol, build) => custom(key, title, G.ASN, cols, (t, d) => {
    const out = [];
    for (const o of d[container] || []) {
      const v = o[attr];
      if (v == null) continue;
      const r = { _id: o.uid, [objcol]: o.uid };
      for (const c of cols) if (c.key !== objcol) r[c.key] = getv(c, v, d);
      out.push(r);
    }
    return out;
  }, (t, d, rows, errs) => {
    const n0 = errs.length, built = new Map();
    rows.forEach((row, i) => {
      const vals = {}; let bad = false;
      for (const c of cols) { const p = cell(t, i, c.key, c.key in row ? row[c.key] : (c.def ?? null), d, errs); if (!p) bad = true; else vals[c.key] = p.v; }
      if (bad) return;
      const u = vals[objcol];
      if (!u) { errs.push(err(t, i, objcol, `${cols[0].label} is required`)); return; }
      if (built.has(u)) { errs.push(err(t, i, objcol, `'${u}' is listed twice`)); return; }
      built.set(u, vals);
    });
    if (errs.length > n0) return {};
    for (const o of d[container] || []) {
      const old = o[attr] ?? null, vals = built.get(o.uid);
      if (!vals) { if (old != null) o[attr] = null; continue; }
      const nw = build(vals, old);
      if (old == null || !sameNum(old, nw)) o[attr] = nw;
    }
    return { rows: rows.length };
  });

  const rangeCol = (ax, idx, label) => col(`${ax}_${idx ? "max" : "min"}`, label, n, "length", { optional: true,
    get: o => o[`${ax}_range`] == null ? null : o[`${ax}_range`][idx],
    put: (o, v) => { const r = [...(o[`${ax}_range`] || [null, null])]; r[idx] = v; o[`${ax}_range`] = r[0] == null && r[1] == null ? null : r; } });
  const dimsGet = o => (o.dims && typeof o.dims === "object") ? Object.entries(o.dims).map(([k, v]) => `${k}=${typeof v === "string" ? v : String(v)}`).join("; ") : "";
  const dimsPut = (o, v) => {
    const s = String(v ?? "").trim();
    if (!s) { delete o.dims; return; }
    const dims = {};
    for (let part of s.split(/[;,]/)) {
      part = part.trim(); if (!part) continue;
      if (!part.includes("=")) throw new RowErr(`dims entry '${part}' must be key=value`);
      const [k, val] = [part.slice(0, part.indexOf("=")).trim(), part.slice(part.indexOf("=") + 1).trim()];
      dims[k] = /^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(val) ? Number(val) : val;
    }
    o.dims = dims;
  };
  const vertsGet = o => (o.vertices || []).map(p => `${String(p[0])},${String(p[1])}`).join("; ");
  const vertsPut = (o, v) => {
    const pts = [];
    for (let ch of String(v ?? "").split(";")) { ch = ch.trim(); if (!ch) continue; const xs = parseNums(ch); if (xs.length !== 2) throw new RowErr("every vertex needs exactly 2 coordinates y,z"); pts.push(xs); }
    if (pts.length < 3) throw new RowErr("a polygon needs at least 3 vertices 'y,z; y,z; ...'");
    o.vertices = pts;
  };

  /* ---- hinge assignments */
  const hinges = custom("frame_hinge_assignments", "Frame Assignments - Hinges", G.ASN, [
    col("frame", "Frame", "ref", "none", { ref: "members", required: true }),
    col("hinge", "Hinge (mode or property)", "enum", "none", { enum: hingeTargets, required: true }),
    col("relative_distance", "Rel Dist", n, "none", { optional: true }),
  ], (t, d) => {
    const out = [];
    for (const m of d.members || []) {
      const h = m.hinges ?? "none";
      if (typeof h === "string") { if (h !== "none") out.push({ _id: [m.uid, 0], frame: m.uid, hinge: h, relative_distance: null }); }
      else h.forEach((e, k) => out.push({ _id: [m.uid, k], frame: m.uid, hinge: e.property, relative_distance: e.relative_distance ?? null }));
    }
    return out;
  }, (t, d, rows, errs) => {
    const n0 = errs.length, built = new Map();
    rows.forEach((row, i) => {
      const vals = {}; let bad = false;
      for (const c of t.cols) { const p = cell(t, i, c.key, row[c.key], d, errs); if (!p) bad = true; else vals[c.key] = p.v; }
      if (bad) return;
      if (!vals.frame) { errs.push(err(t, i, "frame", "Frame is required")); return; }
      if (!built.has(vals.frame)) built.set(vals.frame, []);
      built.get(vals.frame).push([i, vals.hinge, vals.relative_distance]);
    });
    if (errs.length > n0) return {};
    for (const m of d.members || []) {
      const ents = built.get(m.uid), cur = m.hinges ?? "none";
      let nw;
      if (!ents) nw = "none";
      else if (ents.length === 1 && MEMBER_HINGE.includes(ents[0][1])) {
        if (ents[0][2] != null) { errs.push(err(t, ents[0][0], "relative_distance", `'${ents[0][1]}' hinges sit at both ends (leave Rel Dist blank)`)); continue; }
        nw = ents[0][1];
      } else {
        nw = [];
        for (const [i, h, rd] of ents) {
          if (MEMBER_HINGE.includes(h) || rd == null) { errs.push(err(t, i, "relative_distance", `a hinge-property row needs a property and a Rel Dist; '${h}' cannot be mixed`)); break; }
          nw.push({ property: h, relative_distance: rd });
        }
      }
      if (!sameNum(cur, nw)) m.hinges = nw;
    }
    return errs.length > n0 ? {} : { rows: rows.length };
  });

  /* ---- convergence tests / P-Delta factors / NP subdivisions */
  const convTests = custom("analysis_convergence_tests", "Analysis Options - Convergence Tests", G_AOPT, [
    col("type", "Test", "enum", "none", { enum: TESTS, required: true }),
    col("tolerance", "Tolerance", n, "none", { optional: true }),
    col("max_iterations", "Max Iterations", "int", "none", { optional: true }),
  ], (t, d) => aoNorm(d.analysis_options || {}).convergence.tests.map((e, k) => ({ _id: k, type: e.type, tolerance: e.tolerance ?? null, max_iterations: e.max_iterations ?? null })),
  (t, d, rows, errs) => {
    const n0 = errs.length, built = [];
    rows.forEach((row, i) => {
      const e = {};
      for (const c of t.cols) { const p = cell(t, i, c.key, row[c.key], d, errs); if (!p) return; if (p.v != null) e[c.key] = p.v; }
      built.push(e);
    });
    if (errs.length > n0) return {};
    if (sameNum(aoNorm(d.analysis_options || {}).convergence.tests, built)) return { rows: rows.length };
    const stored = clone(d.analysis_options || {});
    stored.convergence = { ...(stored.convergence || {}), tests: built };
    aoStore(d, stored);
    return { rows: rows.length };
  });
  const pdFactors = custom("pdelta_load_factors", "P-Delta Options - Load Combination", G_AOPT, [
    col("pattern", "Load Pattern", "enum", "none", { enum: names("patterns"), required: true }),
    col("factor", "Scale Factor", n, "none", { required: true }),
  ], (t, d) => Object.entries(pdRead(d).load_factors).map(([p, f]) => ({ _id: [p], pattern: p, factor: f })),
  (t, d, rows, errs) => {
    const n0 = errs.length, built = {};
    rows.forEach((row, i) => {
      const a = cell(t, i, "pattern", row.pattern, d, errs); if (!a) return;
      const b = cell(t, i, "factor", row.factor, d, errs); if (!b) return;
      if (a.v in built) { errs.push(err(t, i, "pattern", `duplicate Load Pattern '${a.v}'`)); return; }
      built[a.v] = b.v;
    });
    if (errs.length > n0) return {};
    const o = pdRead(d), old = o.load_factors;
    if (jsonEq(Object.keys(old), Object.keys(built)) && Object.keys(old).every(k => same(old[k], built[k]))) return { rows: rows.length };
    o.load_factors = mergeOrdered(old, built);
    pdStore(d, o);
    return { rows: rows.length };
  });
  const npSub = custom("nonprismatic_subdivisions", "Frame Sections - Nonprismatic Subdivisions", G.DEF, [
    col("section", "Section", "text", "none", { editable: false }),
    col("subdivisions", "Sub-elements per Varying Segment", "int", "none", { def: 16 }),
  ], (t, d) => Object.entries(d.sections || {}).filter(([, s]) => s.kind === "nonprismatic").map(([k, s]) => ({ _id: k, section: k, subdivisions: s.subdivisions ?? 16 })),
  (t, d, rows, errs) => {
    const n0 = errs.length, secs = d.sections || {}, seen = new Set();
    rows.forEach((row, i) => {
      const nm = row.section ?? row._id;
      if (!(nm in secs) || secs[nm].kind !== "nonprismatic") { errs.push(err(t, i, "section", `'${nm}' is not a nonprismatic section`)); return; }
      if (seen.has(nm)) { errs.push(err(t, i, "section", `duplicate section '${nm}'`)); return; }
      seen.add(nm);
      if (!("subdivisions" in row)) return;
      const p = cell(t, i, "subdivisions", row.subdivisions, d, errs); if (!p) return;
      if (!same(p.v, secs[nm].subdivisions ?? 16)) secs[nm].subdivisions = p.v;
    });
    return errs.length > n0 ? {} : { rows: rows.length };
  }, { canAdd: false, canDelete: false, keycol: "section" });

  const gdPt = i => (o, v) => { const p = [...(o.point || [0, 0, 0])]; p[i] = v; o.point = p; };

  T.push(stagesTbl, opsTbl,
    child("staged_live", "Load Cases - Staged Construction - Final Live Loads", G.CASE, [
      col("case", "Case", "enum", "none", { enum: names("staged_cases"), required: true }),
      col("pattern", "Load Pattern", "enum", "none", { enum: names("patterns"), required: true }),
      col("factor", "Scale Factor", n, "none", { required: true }),
    ], { container: "staged_cases", field: "include_live", parentCol: "case", childCol: "pattern", valueCol: "factor" }),
    obj("hyperstatic_cases", "Load Cases - Hyperstatic", G.CASE, [
      col("name", "Case", "text", "none", { required: true }),
      col("case", "Linear Static Case", "enum", "none", { enum: names("cases"), required: true }),
    ], { container: "hyperstatic_cases", keycol: "name", nameInObj: false, onRename: CASC.case, deleteRefs: REFS.case, newObj: () => ({ case: "" }) }),
    obj("section_cuts", "Section Cut Definitions", G.CASE, [
      col("name", "Name", "text", "none", { required: true }),
      col("axis", "Axis", "enum", "none", { enum: ["x", "y", "z"], def: "z" }),
      col("coord", "Coordinate", n, "length", { required: true }),
      rangeCol("x", 0, "X Min"), rangeCol("x", 1, "X Max"), rangeCol("y", 0, "Y Min"), rangeCol("y", 1, "Y Max"), rangeCol("z", 0, "Z Min"), rangeCol("z", 1, "Z Max"),
      col("group", "Group", "enum", "none", { enum: optNames("groups"), def: "", get: o => o.group || "", put: putOpt("group") }),
    ], { container: "section_cuts", kind: "list", keycol: "name", newObj: () => ({ name: "", axis: "z", coord: 0, x_range: null, y_range: null, z_range: null }) }),
    plist("frame_temperature_loads", "Frame Loads - Temperature", [
      col("pattern", "Load Pattern", "enum", "none", { enum: names("patterns"), required: true }),
      col("member_uid", "Frame", "ref", "none", { ref: "members", required: true }),
      col("dT", "Temperature Change", n, "temp_delta", { required: true }),
      col("grad2", "Gradient 2", n, "temp_gradient", { def: 0 }),
      col("grad3", "Gradient 3", n, "temp_gradient", { def: 0 }),
    ], { lists: ["thermal_loads"], toRowObj: (l, e) => ({ grad2: 0, grad3: 0, ...clone(e) }), target: thermalTarget,
      newObj: () => ({ member_uid: "", dT: 0, grad2: 0, grad3: 0 }) }),
    plist("shell_temperature_loads", "Shell Loads - Temperature", [
      col("pattern", "Load Pattern", "enum", "none", { enum: names("patterns"), required: true }),
      col("region_uid", "Shell", "ref", "none", { ref: "shells", required: true }),
      col("dT", "Temperature Change", n, "temp_delta", { required: true }),
      col("grad3", "Gradient 3", n, "temp_gradient", { def: 0 }),
    ], { lists: ["shell_thermal_loads"], target: (l, o) => ["shell_thermal_loads", { region_uid: o.region_uid, dT: o.dT ?? 0, grad3: o.grad3 ?? 0 }],
      newObj: () => ({ region_uid: "", dT: 0, grad3: 0 }) }),
    plist("joint_temperatures", "Joint Loads - Temperature", [
      col("pattern", "Load Pattern", "enum", "none", { enum: names("patterns"), required: true }),
      ...pt("", "point", "Joint "),
      col("dT", "Temperature", n, "temp_delta", { required: true }),
    ], { lists: ["joint_temperatures"], target: (l, o) => ["joint_temperatures", { point: [...o.point], dT: o.dT ?? 0 }],
      newObj: () => ({ point: [0, 0, 0], dT: 0 }) }),
    plist("ground_displacements", "Joint Loads - Ground Displacement", [
      col("pattern", "Load Pattern", "enum", "none", { enum: names("patterns"), required: true }),
      ...pt("", "point", "Joint "),
      col("ux", "UX", n, "length", { def: 0 }), col("uy", "UY", n, "length", { def: 0 }), col("uz", "UZ", n, "length", { def: 0 }),
      col("rx", "RX (rad)", n, "none", { def: 0 }), col("ry", "RY (rad)", n, "none", { def: 0 }), col("rz", "RZ (rad)", n, "none", { def: 0 }),
    ], { lists: ["ground_displacements"], target: (l, o) => ["ground_displacements", { point: [...o.point], ...Object.fromEntries(["ux", "uy", "uz", "rx", "ry", "rz"].map(k => [k, o[k] ?? 0])) }],
      newObj: () => ({ point: [0, 0, 0] }) }),
    obj("joint_patterns", "Joint Patterns", G.LOAD, [
      col("name", "Name", "text", "none", { required: true }),
      col("type", "Type", "text", "none", { editable: false, get: o => o.type ?? "linear" }),
      col("a", "A (x)", n, "none", { def: 0 }), col("b", "B (y)", n, "none", { def: 0 }), col("c", "C (z)", n, "none", { def: 0 }), col("d", "D (constant)", n, "none", { def: 0 }),
      col("zero_negative", "Zero Negative Values", "bool", "none", { def: false, put: putDef("zero_negative", false) }),
      col("zero_positive", "Zero Positive Values", "bool", "none", { def: false, put: putDef("zero_positive", false) }),
    ], { container: "joint_patterns", keycol: "name", nameInObj: false, onRename: jpCascade, deleteRefs: jpRefs, newObj: () => ({ type: "linear", a: 0, b: 0, c: 0, d: 0 }) }),
    options("pdelta_options", "P-Delta Options", G_AOPT, [
      col("method", "Method", "enum", "none", { enum: PDELTA_METHODS }), col("max_iterations", "Max Iterations", "int"),
      col("tolerance", "Relative Tolerance", n), col("include_in", "Include In", "enum", "none", { enum: ["all_linear"] }),
    ], d => { const o = pdRead(d); delete o.load_factors; return o; }, pdWrite),
    pdFactors,
    options("active_dof", "Active Degrees of Freedom", G_AOPT, DOF6.map(k => col(k, k, "bool")), dofRead,
      (d, ch) => { const cur = { ...dofRead(d), ...ch }; d.active_dof = DOF6.filter(k => cur[k]); }),
    options("analysis_options", "Analysis Options - Solver and Convergence", G_AOPT, [
      col("solver", "Solver", "enum", "none", { enum: SOLVERS }), col("eigen_solver", "Eigen Solver", "enum", "none", { enum: ["auto", "legacy"] }),
      col("size_warnings", "Model-Size Warnings", "bool"), col("parallel", "Parallel (auto / off / 1..8)", "text"),
      col("conv_mode", "Convergence Mode", "enum", "none", { enum: ["legacy", "robust"] }), col("algorithms", "Algorithms", "text"),
      col("max_halvings", "Max Step Halvings", "int"),
    ], aoRead, aoWrite),
    convTests,
    options("damping_options", "Damping Options - Links and Springs", G_AOPT, [
      col("rayleigh_on_links", "Rayleigh on Links", "enum", "none", { enum: ["none", "elastic_only", "all"] }),
      col("rayleigh_stiffness_basis", "Stiffness Basis", "enum", "none", { enum: ["committed", "current", "initial"] }),
    ], dmpRead, dmpWrite),
    options("panel_zone_option", "Panel Zones - Model-Wide", G.ASN, [
      col("panel_zones", "Panel Zones", "enum", "none", { enum: ["none", "rigid", "scissors"] }),
    ], d => ({ panel_zones: d.panel_zones ?? "none" }), (d, ch) => { d.panel_zones = ch.panel_zones; }),
    obj("joint_panel_zones", "Joint Assignments - Panel Zones", G.ASN, [
      ...pt("", "point", "Joint "),
      col("property", "Property", "enum", "none", { enum: ["from_column", "elastic", "spring"], def: "from_column" }),
      col("k", "Spring K", n, "rot_stiffness", { optional: true, put: putOpt("k") }),
      col("doubler_t", "Doubler Thickness", n, "dim", { optional: true, put: putOpt("doubler_t") }),
      col("connectivity", "Connectivity", "enum", "none", { enum: ["beams_to_panel", "braces_to_panel", "beams_and_braces_to_panel"], def: "beams_to_panel" }),
    ], { container: "joint_panel_zones", kind: "list", keycol: null, newObj: () => ({ point: [0, 0, 0], property: "from_column" }) }),
    obj("towers", "Tower Definitions", G.DEF, [
      col("name", "Tower", "text", "none", { required: true }),
      col("grid_system", "Grid System", "enum", "none", { enum: gridsOpt, def: "", get: o => o.grid_system || "", put: (o, v) => { o.grid_system = v || null; } }),
      col("base_elevation", "Base Elevation", n, "length", { def: 0 }),
      col("stories", "Stories", "text", "none", { editable: false, get: o => (o.stories || []).join(", ") }),
    ], { container: "towers", kind: "list", keycol: "name", onRename: towerCascade, deleteRefs: towerRefs, newObj: () => ({ name: "", grid_system: null, base_elevation: 0, stories: [] }) }),
    obj("joint_towers", "Joint Assignments - Tower", G.ASN, [
      ...pt("", "point", "Joint "),
      col("tower", "Tower", "enum", "none", { enum: towersEnum, required: true }),
    ], { container: "joint_towers", kind: "list", keycol: null, newObj: () => ({ point: [0, 0, 0], tower: "" }) }),
    hinges,
    attrTable("frame_hinge_overwrites", "Frame Assignments - Hinge Overwrites", [
      col("frame", "Frame", "ref", "none", { ref: "members", required: true }),
      col("auto_subdivide", "Auto Subdivide", "bool", "none", { def: false, get: o => !!(o.auto_subdivide ?? false) }),
      col("relative_length", "Relative Length", n, "none", { optional: true, get: o => o.relative_length ?? null }),
    ], "members", "hinge_overwrites", "frame", (v, old) => {
      const out = {};
      if (v.auto_subdivide != null && (v.auto_subdivide || old == null || "auto_subdivide" in (old || {}))) out.auto_subdivide = !!v.auto_subdivide;
      if (v.relative_length != null) out.relative_length = v.relative_length;
      return out;
    }),
    attrTable("floor_mesh", "Shell Assignments - Floor Auto Mesh Options", [
      col("shell", "Shell", "ref", "none", { ref: "shells", required: true }),
      col("mode", "Mesh Option", "enum", "none", { enum: ["default", "rectangular", "cookie_cut"], def: "default", get: o => o.mode ?? "default" }),
      col("max_size", "Max Element Size", n, "length", { optional: true, get: o => o.max_size ?? null }),
      col("at_beams", "At Beams", "bool", "none", { def: true, get: o => !!(o.at_beams ?? true) }),
      col("at_walls", "At Walls", "bool", "none", { def: true, get: o => !!(o.at_walls ?? true) }),
      col("at_grids", "At Grids", "bool", "none", { def: true, get: o => !!(o.at_grids ?? true) }),
    ], "shells", "floor_mesh", "shell", v => ({ mode: v.mode, max_size: v.max_size, at_beams: v.at_beams, at_walls: v.at_walls, at_grids: v.at_grids })),
    listChild("nonprismatic_segments", "Frame Sections - Nonprismatic Segments", G.DEF, [
      col("section", "Section", "enum", "none", { enum: npNames, required: true }),
      col("start_section", "Start Section", "enum", "none", { enum: prisNames, required: true }),
      col("end_section", "End Section", "enum", "none", { enum: prisNames, required: true }),
      col("length", "Length", n, "none", { def: 1 }),
      col("length_type", "Length Type", "enum", "none", { enum: ["relative", "absolute"], def: "relative" }),
      col("EI33", "EI33 Variation", "enum", "none", { enum: ["linear", "parabolic", "cubic"], def: "linear" }),
      col("EI22", "EI22 Variation", "enum", "none", { enum: ["linear", "parabolic", "cubic"], def: "linear" }),
    ], { container: "sections", parentCol: "section", field: "segments", parents: d => Object.entries(d.sections || {}).filter(([, s]) => s.kind === "nonprismatic"),
      newEntry: () => ({ start_section: "", end_section: "", length: 1, length_type: "relative", EI33: "linear", EI22: "linear" }) }),
    npSub,
    mirrorObj("builtup_sections", "Frame Sections - Built-Up", [
      col("name", "Name", "text", "none", { required: true }),
      col("material", "Material", "enum", "none", { enum: names("materials"), required: true }),
      col("axes", "Analysis Axes", "enum", "none", { enum: ["geometric", "principal"], def: "geometric" }),
      col("torsion", "Torsion", "enum", "none", { enum: ["auto", "open", "fem", "exact"], def: "auto" }),
      col("template", "Template", "text", "none", { editable: false, get: o => (o.template && typeof o.template === "object") ? (o.template.id || "") : "" }),
      col("notes", "Notes", "text", "none", { def: "", put: putOpt("notes") }),
      col("n_components", "Components", "int", "none", { editable: false, get: o => (o.components || []).length }),
    ], { container: "builtup_sections", mirror: "builtup", newObj: () => ({ material: "", components: [], axes: "geometric", torsion: "auto" }) }),
    listChild("builtup_components", "Frame Sections - Built-Up Components", G.DEF, [
      col("section", "Section", "enum", "none", { enum: names("builtup_sections"), required: true }),
      col("kind", "Kind", "enum", "none", { enum: ["plate", "db", "param"], required: true }),
      col("type", "Shape Type", "text", "none", { def: "", put: putOpt("type") }),
      col("standard", "Database", "text", "none", { def: "", put: putOpt("standard") }),
      col("db_name", "Database Shape", "text", "none", { path: ["name"], def: "", put: putOpt("name") }),
      col("dims", "Dimensions (key=value)", "text", "none", { def: "", get: dimsGet, put: dimsPut }),
      col("b", "Plate b", n, "dim", { optional: true, put: putOpt("b") }),
      col("t", "Plate t", n, "dim", { optional: true, put: putOpt("t") }),
      col("x", "X", n, "dim", { def: 0 }), col("y", "Y", n, "dim", { def: 0 }),
      col("angle", "Plate Angle", n, "angle_deg", { optional: true, put: putOpt("angle") }),
      col("rotation", "Rotation", n, "angle_deg", { optional: true, put: putOpt("rotation") }),
      col("mirror", "Mirror", "bool", "none", { def: false, put: (o, v) => { if (v || "mirror" in o) o.mirror = v; } }),
      col("material", "Material", "enum", "none", { enum: optNames("materials"), def: "", get: o => o.material || "", put: putOpt("material") }),
      col("label", "Label", "text", "none", { def: "", get: o => o.label || "", put: putOpt("label") }),
    ], { container: "builtup_sections", parentCol: "section", field: "components", mirror: "builtup", newEntry: () => ({ kind: "plate", x: 0, y: 0 }) }),
    mirrorObj("designer_sections", "Frame Sections - Section Designer", [
      col("name", "Name", "text", "none", { required: true }),
      col("material", "Base Material", "enum", "none", { enum: names("materials"), required: true }),
      col("base", "Base", "enum", "none", { enum: ["concrete", "steel"], def: "concrete" }),
      col("torsion", "Torsion", "enum", "none", { enum: ["approx", "exact"], def: "approx", get: o => o.torsion ?? "approx", put: putDef("torsion", "approx") }),
      col("n_polygons", "Polygons", "int", "none", { editable: false, get: o => (o.polygons || []).length }),
      col("n_rebar", "Rebar", "int", "none", { editable: false, get: o => (o.rebar || []).length }),
    ], { container: "designer_sections", mirror: "designer", newObj: () => ({ material: "", base: "concrete", polygons: [], rebar: [] }) }),
    listChild("designer_polygons", "Frame Sections - Section Designer Polygons", G.DEF, [
      col("section", "Section", "enum", "none", { enum: names("designer_sections"), required: true }),
      col("material", "Material", "enum", "none", { enum: names("materials"), required: true }),
      col("hole", "Hole", "bool", "none", { def: false }),
      col("vertices", "Vertices (y,z; ...)", "text", "none", { required: true, get: vertsGet, put: vertsPut }),
    ], { container: "designer_sections", parentCol: "section", field: "polygons", mirror: "designer", newEntry: () => ({ vertices: [], material: "", hole: false }) }),
    listChild("designer_rebar", "Frame Sections - Section Designer Rebar", G.DEF, [
      col("section", "Section", "enum", "none", { enum: names("designer_sections"), required: true }),
      col("y", "Y", n, "dim", { required: true }), col("z", "Z", n, "dim", { required: true }),
      col("area", "Bar Area", n, "area", { required: true }),
      col("material", "Material", "enum", "none", { enum: names("materials"), required: true }),
    ], { container: "designer_sections", parentCol: "section", field: "rebar", mirror: "designer", newEntry: () => ({ y: 0, z: 0, area: 0, material: "" }) }),
    obj("generalized_displacements", "Generalized Displacements", G.DEF, [
      col("name", "Name", "text", "none", { required: true }),
      col("type", "Type", "enum", "none", { enum: ["translation", "rotation"], def: "translation" }),
      col("n_terms", "Terms", "int", "none", { editable: false, get: o => (o.terms || []).length }),
    ], { container: "generalized_displacements", keycol: "name", nameInObj: false, newObj: () => ({ type: "translation", terms: [] }) }),
    listChild("generalized_displacement_terms", "Generalized Displacements - Terms", G.DEF, [
      col("name", "Generalized Displacement", "enum", "none", { enum: names("generalized_displacements"), required: true }),
      col("x", "Joint X", n, "length", { path: ["point", 0], required: true, put: gdPt(0) }),
      col("y", "Joint Y", n, "length", { path: ["point", 1], required: true, put: gdPt(1) }),
      col("z", "Joint Z", n, "length", { path: ["point", 2], required: true, put: gdPt(2) }),
      col("dof", "DOF", "enum", "none", { enum: DOF6, required: true }),
      col("sf", "Scale Factor", n, "none", { def: 1 }),
    ], { container: "generalized_displacements", parentCol: "name", field: "terms", newEntry: () => ({ point: [0, 0, 0], dof: "UX", sf: 1 }) }),
  );
  return T;
}
