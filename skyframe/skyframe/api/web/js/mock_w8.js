/* SkyFrame W8 — ?mock=1 support: model validation of the W8 fields and the
   Interactive Database Editing tables of skyframe/core/modeltables_ext.py
   (same keys, columns, row identity and default-removal rules), so the mock
   round-trips byte-identically like the backend.

   Exported: mockValidateW8(model) → message | null (postModel hook) and
   mdbW8Extend(tables, H) (hook in js/mock_dbedit.js: H = its helpers). */

const SHELL_TYPES = ["shell", "shell_thin", "shell_thick", "membrane", "plate_thin", "plate_thick"];
const AREA_DIRS = ["gravity", "global_x", "global_y", "global_z", "local_1", "local_2", "local_3"];

/* ------------------------------------------------------------ validation */
export function mockValidateW8(d) {
  if (!d) return null;
  const ss = d.stories || [];
  const by = new Map(ss.map(s => [s.name, s]));
  for (const s of ss) {
    if ("master" in s && typeof s.master !== "boolean") return `Story ${s.name}: master must be true or false`;
    const sim = s.similar_to || "";
    if (!sim) continue;
    if (s.master) return `Story ${s.name}: a master story cannot be similar to another story ('${sim}')`;
    if (sim === s.name) return `Story ${s.name}: similar_to names itself`;
    if (!by.has(sim)) return `Story ${s.name}: similar_to names unknown story '${sim}'`;
    if (!by.get(sim).master) return `Story ${s.name}: similar_to must name a master story ('${sim}' is not a master)`;
  }
  for (const [n, s] of Object.entries(d.shell_sections || {})) {
    const t = s.shell_type || "shell";
    if (!SHELL_TYPES.includes(t)) return `Shell section ${n}: shell_type must be one of ${SHELL_TYPES.join("|")} (got '${t}')`;
    if (s.layered && !["shell", "shell_thin", "shell_thick"].includes(t)) return `Shell section ${n}: a layered section needs shell_type shell|shell_thin|shell_thick (got '${t}')`;
  }
  const sets = d.shell_load_sets || {};
  for (const [sn, set] of Object.entries(sets)) for (const p of Object.keys(set)) if (!(p in (d.patterns || {}))) return `shell load set '${sn}': unknown load pattern '${p}'`;
  for (const r of d.shells || []) {
    const tag = `Shell ${r.uid}`;
    const md = r.mesh_divisions;
    if (md != null) {
      const k = Object.keys(md).sort().join(",");
      if (k === "max_size") { if (!(md.max_size > 0)) return `${tag}: mesh_divisions.max_size must be a finite value > 0`; }
      else if (k === "n1,n2") { if (!(Number.isInteger(md.n1) && md.n1 >= 1 && Number.isInteger(md.n2) && md.n2 >= 1)) return `${tag}: mesh_divisions.n1 / n2 must be integers >= 1`; }
      else return `${tag}: mesh_divisions must be {"n1", "n2"} or {"max_size"}`;
      if ((r.behavior || "shell") !== "shell") return `${tag}: mesh options need shell behavior (membrane regions are not meshed)`;
    }
    const dist = r.distribution || "two_way";
    if (!["two_way", "one_way"].includes(dist)) return `Shell ${r.uid}: distribution must be two_way|one_way (got '${dist}')`;
    if (r.one_way_dir != null && ![1, 2].includes(r.one_way_dir)) return `Shell ${r.uid}: one_way_dir must be 1 or 2 (got ${r.one_way_dir})`;
    if (dist === "one_way") {
      if (r.behavior !== "membrane" || r.kind !== "slab") return `Shell ${r.uid}: one_way distribution needs a membrane-behavior slab`;
      if ((r.corners || []).length !== 4 || (r.openings || []).some(o => o.polygon)) return `Shell ${r.uid}: one_way distribution needs a 4-corner region (no polygon openings)`;
    }
    if (r.load_set) {
      if (!(r.load_set in sets)) return `${tag}: unknown shell load set '${r.load_set}'`;
      if (r.behavior === "membrane" && Object.values(sets[r.load_set]).some(e => e && typeof e === "object" && ((e.direction || "gravity") !== "gravity" || e.projected)))
        return `${tag}: a membrane region takes uniform gravity loads only (load set '${r.load_set}')`;
    }
  }
  for (const m of d.members || []) {
    const c = m.curve;
    if (c == null) continue;
    if (typeof c !== "object") return `Member ${m.uid}: curve must be an object or null`;
    if ((c.via != null) === (c.center != null)) return `Member ${m.uid}: curve: give exactly one of "via" or "center"`;
    const v3 = v => Array.isArray(v) && v.length === 3 && v.every(x => typeof x === "number" && isFinite(x));
    if (c.via != null && !v3(c.via)) return `Member ${m.uid}: curve.via must be three finite numbers`;
    if (c.center != null && !(v3(c.center) && v3(c.plane_normal))) return `Member ${m.uid}: curve: "center" needs "plane_normal"`;
    if (c.segments != null && !(Number.isInteger(c.segments) && c.segments >= 1 && c.segments <= 720)) return `Member ${m.uid}: curve.segments must be in [1, 720]`;
    if (c.local2 != null && !["in_plane", "normal"].includes(c.local2)) return `Member ${m.uid}: curve.local2 must be in_plane|normal`;
  }
  return null;
}

/* ------------------------------------------------------------ DB-edit tables */
const fmtNum = v => (Number.isInteger(v) && Math.abs(v) < 1e15) ? String(v) : String(v);
export function curveText(c) {
  if (!c) return "";
  const parts = [];
  if (c.via != null) parts.push("via " + c.via.map(fmtNum).join(","));
  else if (c.center != null) { parts.push("center " + c.center.map(fmtNum).join(",")); parts.push("normal " + (c.plane_normal || []).map(fmtNum).join(",")); }
  if (c.segments != null) parts.push(`segments ${c.segments}`);
  if ((c.local2 || "in_plane") !== "in_plane") parts.push(`local2 ${c.local2}`);
  return parts.join(" ");
}
export function parseCurveText(s) {
  s = String(s || "").trim();
  if (!s) return null;
  const toks = s.replace(/;/g, " ").split(/\s+/);
  const out = { type: "arc" };
  const vec = t => { const v = t.split(",").map(Number); if (v.length !== 3 || !v.every(isFinite)) throw new Error(`'${t}' is not x,y,z`); return v; };
  for (let i = 0; i < toks.length; i += 2) {
    const k = toks[i].toLowerCase(), v = toks[i + 1];
    if (v === undefined) throw new Error(`'${toks[i]}' needs a value`);
    if (k === "via") out.via = vec(v);
    else if (k === "center") out.center = vec(v);
    else if (k === "normal") out.plane_normal = vec(v);
    else if (k === "segments") { if (v.toLowerCase() === "auto") out.segments = null; else { const n = Number(v); if (!Number.isInteger(n)) throw new Error("segments must be a whole number or auto"); out.segments = n; } }
    else if (k === "local2") out.local2 = v;
    else throw new Error(`unknown curve keyword '${toks[i]}' (use via, center, normal, segments, local2)`);
  }
  if (("via" in out) === ("center" in out)) throw new Error('give "via x,y,z" or "center x,y,z normal nx,ny,nz"');
  if (!("segments" in out)) out.segments = null;
  if (!("local2" in out)) out.local2 = "in_plane";
  const o = {};
  for (const k of ["type", "via", "center", "plane_normal", "segments", "local2"]) if (k in out) o[k] = out[k];
  return o;
}

export function mdbW8Extend(T, H) {
  const { col, obj, names, optNames, optStories, getv, parseCell, rowValues, same, E, clone, RowErr, mergeOrdered, CASC, REFS, isNum, G } = H;
  const n = "number";
  const at = key => T.findIndex(t => t.key === key);
  const insertAfter = (key, ...tbls) => { const i = at(key); T.splice(i + 1, 0, ...tbls); };
  const tbl = key => T[at(key)];
  const putDef = (key, def) => (o, v) => { if (v === def || v == null) delete o[key]; else o[key] = v; };
  const chain = (...fs) => (d, x) => { for (const f of fs) if (f) f(d, x); };
  const chainRefs = (...fs) => (d, x) => { for (const f of fs) { const m = f && f(d, x); if (m) return m; } return null; };
  const sameJson = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const G_FN = "Model Definition > Functions";

  /* ---- appended columns */
  const so = tbl("shell_sections");
  so.cols.push(col("shell_type", "Shell Type", "enum", "none", { enum: SHELL_TYPES, def: "shell", get: o => o.shell_type || "shell", put: putDef("shell_type", "shell") }));
  const fo = tbl("frame_objects");
  fo.cols.splice(fo.cols.findIndex(c => c.key === "length"), 0, col("curve", "Curve", "text", "none", { def: "", get: o => curveText(o.curve),
    put: (o, v) => { const c = parseCurveText(v); if (c == null) delete o.curve; else o.curve = c; } }));
  const mdGet = k => o => (o.mesh_divisions && k in o.mesh_divisions) ? o.mesh_divisions[k] : null;
  const mdPut = k => (o, v) => {
    let md = { ...(o.mesh_divisions || {}) };
    if (k === "max_size") md = v != null ? { max_size: v } : Object.fromEntries(Object.entries(md).filter(([kk]) => kk !== "max_size"));
    else if (v == null) delete md[k];
    else { delete md.max_size; md[k] = v; }
    if (Object.keys(md).length) o.mesh_divisions = md; else delete o.mesh_divisions;
  };
  const sh = tbl("shell_objects");
  sh.cols.splice(sh.cols.findIndex(c => c.key === "area"), 0,
    col("mesh_n1", "Mesh n1", "int", "none", { optional: true, get: mdGet("n1"), put: mdPut("n1") }),
    col("mesh_n2", "Mesh n2", "int", "none", { optional: true, get: mdGet("n2"), put: mdPut("n2") }),
    col("mesh_max", "Mesh Max Size", n, "length", { optional: true, get: mdGet("max_size"), put: mdPut("max_size") }),
    col("distribution", "Load Distribution", "enum", "none", { enum: ["two_way", "one_way"], def: "two_way", get: o => o.distribution || "two_way", put: putDef("distribution", "two_way") }),
    col("one_way_dir", "Span Direction", "int", "none", { enum: [1, 2], def: 1, get: o => o.one_way_dir ?? 1, put: putDef("one_way_dir", 1) }),
    col("load_set", "Uniform Load Set", "text", "none", { def: "", get: o => o.load_set || "", put: putDef("load_set", "") }));
  const hostRename = (d, mp) => { for (const t of d.tendons || []) if (Array.isArray(t.host)) t.host = t.host.map(u => mp[u] ?? u); };
  const hostDelete = (d, gone) => { for (const t of d.tendons || []) if (Array.isArray(t.host) && t.host.some(u => gone.has(u))) t.host = t.host.filter(u => !gone.has(u)); };
  fo.onRename = chain(fo.onRename, hostRename); fo.onDelete = chain(fo.onDelete, hostDelete);
  sh.onRename = chain(sh.onRename, hostRename); sh.onDelete = chain(sh.onDelete, hostDelete);
  const st = tbl("stories");
  st.onRename = chain(st.onRename, (d, mp) => {
    for (const s of d.stories || []) if (s.similar_to in mp) s.similar_to = mp[s.similar_to];
    for (const c of Object.values(d.nonlinear_static_cases || {})) if (c.control_story in mp) c.control_story = mp[c.control_story];
  });
  st.onDelete = chain(st.onDelete, (d, gone) => { for (const s of d.stories || []) if (gone.has(s.similar_to)) delete s.similar_to; });
  const lp = tbl("load_patterns");
  lp.onRename = chain(lp.onRename, (d, mp) => {
    if (d.shell_load_sets) d.shell_load_sets = Object.fromEntries(Object.entries(d.shell_load_sets).map(([k, v]) => [k, Object.fromEntries(Object.entries(v).map(([p, e]) => [mp[p] ?? p, e]))]));
    for (const k of ["nonlinear_static_cases", "steady_state_cases", "psd_cases"]) for (const c of Object.values(d[k] || {})) for (const ld of c.loads || []) if (ld.pattern in mp) ld.pattern = mp[ld.pattern];
    for (const t of d.tendons || []) if (t.pattern in mp) t.pattern = mp[t.pattern];
  });
  lp.deleteRefs = chainRefs(lp.deleteRefs, (d, g) => {
    for (const [sn, s] of Object.entries(d.shell_load_sets || {})) for (const p of Object.keys(s)) if (g.has(p)) return `load pattern '${p}' is used by shell load set '${sn}'`;
    for (const k of ["nonlinear_static_cases", "steady_state_cases", "psd_cases"]) for (const [cn, c] of Object.entries(d[k] || {})) for (const ld of c.loads || []) if (g.has(ld.pattern)) return `load pattern '${ld.pattern}' is used by load case '${cn}'`;
    for (const t of d.tendons || []) if (g.has(t.pattern)) return `load pattern '${t.pattern}' is used by tendon '${t.uid}'`;
    return null;
  });

  /* ---- generic list-child table */
  const listChild = (key, title, group, cols, o) => ({ key, title, group, cols, tkind: "w8", editable: true, canAdd: true, canDelete: true, keycol: null,
    kind: "dict", entry: "dict", tagCol: null, sublists: p => [["", p[o.field] || []]], store: (p, bt) => { p[o.field] = bt[""] || []; },
    newEntry: () => ({}), ...o, w8rows: lcRows, w8apply: lcApply });
  const wrap = (t, e) => t.entry === "pair" ? [...e] : t.entry === "scalar" ? { v: e } : clone(e);
  const unwrap = (t, e) => t.entry === "scalar" ? e.v : e;
  function lcParents(t, d) { return Object.entries(d[t.container] || {}); }
  function lcRows(t, d) {
    const out = [];
    for (const [pk, p] of lcParents(t, d)) for (const [tag, lst] of t.sublists(p)) lst.forEach((e, k) => {
      const r = { _id: [pk, tag, k], [t.parentCol]: pk };
      if (t.tagCol) r[t.tagCol] = tag;
      const w = wrap(t, e);
      for (const c of t.cols) if (c.key !== t.parentCol && c.key !== t.tagCol) r[c.key] = getv(c, w, d);
      if (t.rowExtra) Object.assign(r, t.rowExtra(p, tag, k, e));
      out.push(r);
    });
    return out;
  }
  function lcApply(t, d, rows, errs) {
    const n0 = errs.length, parents = new Map(lcParents(t, d)), orig = new Map(), built = new Map(), seen = new Set();
    for (const [pk, p] of parents) for (const [tag, lst] of t.sublists(p)) lst.forEach((e, k) => orig.set(JSON.stringify([pk, tag, k]), e));
    const tcol = t.tagCol ? t.cols.find(c => c.key === t.tagCol) : null;
    rows.forEach((row, i) => {
      let pk = row[t.parentCol]; pk = typeof pk === "string" ? pk.trim() : pk;
      if (!parents.has(pk)) { errs.push(E(t.key, i, t.parentCol, `unknown ${t.cols.find(c => c.key === t.parentCol).label} '${pk}'`)); return; }
      let tag = "";
      if (tcol) { try { tag = parseCell(tcol, row[t.tagCol], d); } catch (e) { errs.push(E(t.key, i, t.tagCol, `${tcol.label}: ${e.message}`)); return; } }
      let rid = Array.isArray(row._id) && row._id.length === 3 ? JSON.stringify(row._id) : null;
      if (rid && (!orig.has(rid) || seen.has(rid))) rid = null;
      const base = rid ? wrap(t, orig.get(rid)) : wrap(t, t.newEntry(d));
      rowValues(t, i, row, base, d, errs, !rid, [t.parentCol, t.tagCol]);
      if (rid) seen.add(rid);
      if (!built.has(pk)) built.set(pk, {});
      (built.get(pk)[tag] = built.get(pk)[tag] || []).push(unwrap(t, base));
    });
    if (errs.length > n0) return {};
    let changed = 0;
    for (const [pk, p] of parents) {
      const old = Object.fromEntries(t.sublists(p)), nw = built.get(pk) || {};
      if (Object.keys(old).every(k => k in nw) && Object.keys(nw).every(k => k in old) && Object.keys(old).every(k => sameJson(old[k], nw[k]))) continue;
      try { t.store(p, nw); } catch (e) { errs.push(E(t.key, null, t.tagCol, e.message)); return {}; }
      changed++;
    }
    return { rows: rows.length, parents_changed: changed };
  }

  /* ---- properties */
  const kCols = ["k1", "k2", "k3", "kr1", "kr2", "kr3"].map((lb, i) => col(lb, lb.toUpperCase(), n, i < 3 ? "stiffness" : "rot_stiffness",
    { def: 0, path: ["k", i], put: (o, v) => { const k = [...(o.k || [0, 0, 0, 0, 0, 0])]; k[i] = v; o.k = k; } }));
  const springs = obj("spring_properties", "Spring Properties - Point", G.DEF, [
    col("name", "Name", "text", "none", { required: true }),
    col("kind", "Kind", "enum", "none", { enum: ["linear", "multilinear", "compression_only", "tension_only", "gap"], def: "linear" }),
    ...kCols,
    col("nonlinear_dof", "Nonlinear DOF", "enum", "none", { enum: ["", "U1", "U2", "U3", "R1", "R2", "R3"], def: "", get: o => o.nonlinear_dof || "", put: putDef("nonlinear_dof", "") }),
    col("gap", "Gap", n, "length", { optional: true, put: putDef("gap", null) }),
    col("angle_deg", "Local Axis Angle", n, "angle_deg", { optional: true, get: o => (o.local_axes && !Array.isArray(o.local_axes)) ? (o.local_axes.angle_deg ?? 0) : (o.local_axes == null ? 0 : null),
      put: (o, v) => { if (v == null) return; if (v === 0) { if (o.local_axes && !Array.isArray(o.local_axes)) delete o.local_axes; } else o.local_axes = { angle_deg: v }; } }),
  ], { container: "spring_properties", keycol: "name", nameInObj: false,
    onRename: (d, mp) => { for (const s of d.spring_supports || []) if (s.property in mp) s.property = mp[s.property]; },
    deleteRefs: (d, g) => { for (const s of d.spring_supports || []) if (g.has(s.property)) return `spring property '${s.property}' is used by the spring at ${JSON.stringify(s.point)}`; return null; },
    newObj: () => ({ kind: "linear", k: [0, 0, 0, 0, 0, 0] }) });
  const acc = k => col(k, `Acceptance ${k}`, n, "none", { optional: true, get: o => (o.acceptance || {})[k] ?? null,
    put: (o, v) => { const a = { ...(o.acceptance || {}) }; if (v == null) delete a[k]; else a[k] = v; if (Object.keys(a).length) o.acceptance = a; else delete o.acceptance; } });
  const hinges = obj("hinge_properties", "Frame Hinge Properties", G.DEF, [
    col("name", "Name", "text", "none", { required: true }),
    col("type", "Type", "enum", "none", { enum: ["M3", "M2", "P", "V2", "PMM_fiber"], def: "M3" }),
    col("hysteresis", "Hysteresis", "enum", "none", { enum: ["kinematic", "takeda", "pivot", "isotropic", "concrete"], def: "kinematic" }),
    col("drop_strength", "After Point E", "enum", "none", { enum: ["drops", "holds"], def: "drops" }),
    col("yield_value", "Force SF", n, "none", { def: 1, get: o => (o.scale || {}).yield_value ?? 1,
      put: (o, v) => { o.scale = { yield_value: v, yield_deformation: (o.scale || {}).yield_deformation ?? null }; } }),
    col("yield_deformation", "Deformation SF", "text", "none", { def: "", get: o => { const v = (o.scale || {}).yield_deformation; return v == null ? "" : typeof v === "string" ? v : fmtNum(v); },
      put: (o, v) => { const s = { ...(o.scale || { yield_value: 1 }) }; const t = String(v || "").trim(); if (!t) s.yield_deformation = null; else if (t.toLowerCase() === "auto") s.yield_deformation = "auto"; else { const x = Number(t); if (!isFinite(x)) throw new RowErr("a number, auto or blank"); s.yield_deformation = x; } o.scale = s; } }),
    acc("IO"), acc("LS"), acc("CP"),
    col("k_elastic", "Elastic Stiffness", n, "none", { optional: true }),
    col("n_points", "Points", "int", "none", { editable: false, get: o => Array.isArray(o.backbone) ? o.backbone.length : ((o.backbone || {}).positive || []).length }),
  ], { container: "hinge_properties", keycol: "name", nameInObj: false,
    onRename: (d, mp) => { for (const m of d.members || []) if (Array.isArray(m.hinges)) for (const h of m.hinges) if (h && h.property in mp) h.property = mp[h.property]; },
    deleteRefs: (d, g) => { for (const m of d.members || []) for (const h of Array.isArray(m.hinges) ? m.hinges : []) if (h && g.has(h.property)) return `hinge property '${h.property}' is used by frame '${m.uid}'`; return null; },
    newObj: () => ({ type: "M3", backbone: [], scale: { yield_value: 1, yield_deformation: null }, hysteresis: "kinematic", drop_strength: "drops", k_elastic: null, hysteresis_params: null }) });
  const backbone = listChild("hinge_backbones", "Frame Hinge Properties - Backbone Points", G.DEF, [
    col("hinge", "Hinge Property", "enum", "none", { enum: names("hinge_properties"), required: true }),
    col("side", "Side", "enum", "none", { enum: ["both", "positive", "negative"], def: "both" }),
    col("d", "Plastic Deformation", n, "none", { path: [0], required: true }), col("f", "Force / Moment", n, "none", { path: [1], required: true }),
  ], { container: "hinge_properties", parentCol: "hinge", tagCol: "side", entry: "pair", newEntry: () => [0, 0],
    sublists: p => { const b = p.backbone; return (b && !Array.isArray(b)) ? [["positive", b.positive || []], ["negative", b.negative || []]] : [["both", b || []]]; },
    store: (p, bt) => {
      if (Object.keys(bt).every(k => k === "both")) { p.backbone = bt.both || []; return; }
      if ("both" in bt) throw new Error("a backbone is either symmetric (side both) or positive + negative, not both");
      p.backbone = { positive: bt.positive || [], negative: bt.negative || [] };
    } });
  insertAfter("shell_sections", springs, hinges, backbone);

  /* ---- functions */
  const fnCascade = keys => (d, mp) => { for (const k of keys) for (const c of Object.values(d[k] || {})) { if (c.function in mp) c.function = mp[c.function]; for (const ld of c.loads || []) if (ld && ld.function in mp) ld.function = mp[ld.function]; } };
  const fnRefs = keys => (d, g) => { for (const k of keys) for (const [cn, c] of Object.entries(d[k] || {})) for (const f of [c.function, ...(c.loads || []).map(l => l && l.function)]) if (f && g.has(f)) return `function '${f}' is used by load case '${cn}'`; return null; };
  const count = f => o => (o[f] || []).length;
  insertAfter("hinge_backbones",
    obj("rs_functions", "Functions - Response Spectrum", G_FN, [col("name", "Name", "text", "none", { required: true }), col("damping", "Damping", n, "none", { def: 0.05 }),
      col("n_points", "Points", "int", "none", { editable: false, get: count("points") })],
    { container: "spectrum_functions", keycol: "name", onRename: fnCascade(["rs_cases"]), deleteRefs: fnRefs(["rs_cases"]), newObj: () => ({ points: [], damping: 0.05 }) }),
    listChild("rs_function_points", "Functions - Response Spectrum - Points", G_FN, [col("function", "Function", "enum", "none", { enum: names("spectrum_functions"), required: true }),
      col("period", "Period", n, "period", { path: [0], required: true }), col("value", "Value", n, "none", { path: [1], required: true })],
    { container: "spectrum_functions", parentCol: "function", field: "points", entry: "pair", newEntry: () => [0, 0] }),
    obj("th_functions", "Functions - Time History", G_FN, [col("name", "Name", "text", "none", { required: true }), col("dt", "Time Step", n, "period", { required: true }),
      col("n_values", "Values", "int", "none", { editable: false, get: count("values") })],
    { container: "th_functions", keycol: "name", onRename: fnCascade(["th_cases"]), deleteRefs: fnRefs(["th_cases"]), newObj: () => ({ values: [], dt: 0.01 }) }),
    listChild("th_function_values", "Functions - Time History - Values", G_FN, [col("function", "Function", "enum", "none", { enum: names("th_functions"), required: true }),
      col("t", "Time", n, "period", { editable: false }), col("value", "Acceleration", n, "none", { path: ["v"], required: true })],
    { container: "th_functions", parentCol: "function", field: "values", entry: "scalar", newEntry: () => 0, rowExtra: (p, tag, k) => ({ t: k * (+p.dt || 0) }) }),
    obj("frequency_functions", "Functions - Frequency (Steady State / PSD)", G_FN, [col("name", "Name", "text", "none", { required: true }),
      col("kind", "Kind", "enum", "none", { enum: ["steady_state", "psd"], def: "steady_state" }), col("n_points", "Points", "int", "none", { editable: false, get: count("points") })],
    { container: "frequency_functions", keycol: "name", onRename: fnCascade(["steady_state_cases", "psd_cases"]), deleteRefs: fnRefs(["steady_state_cases", "psd_cases"]), newObj: () => ({ kind: "steady_state", points: [] }) }),
    listChild("frequency_function_points", "Functions - Frequency - Points", G_FN, [col("function", "Function", "enum", "none", { enum: names("frequency_functions"), required: true }),
      col("f", "Frequency (Hz)", n, "none", { path: [0], required: true }), col("value", "Value", n, "none", { path: [1], required: true })],
    { container: "frequency_functions", parentCol: "function", field: "points", entry: "pair", newEntry: () => [0, 0] }));

  /* ---- story master / similar */
  const simCols = [col("story", "Story", "text", "none", { editable: false }), col("master", "Master Story", "bool", "none", { def: false }),
    col("similar_to", "Similar To", "enum", "none", { enum: optStories, def: "" })];
  simCols[0].refc = "stories";
  insertAfter("stories", { key: "story_similarity", title: "Story Data - Master / Similar Stories", group: G.DEF, tkind: "w8", editable: true, canAdd: false, canDelete: false, keycol: "story", cols: simCols,
    w8rows: (t, d) => (d.stories || []).map(s => ({ _id: s.name, story: s.name, master: !!s.master, similar_to: s.similar_to || "" })),
    w8apply: (t, d, rows, errs) => {
      const n0 = errs.length, by = new Map((d.stories || []).map(s => [s.name, s])), vals = new Map();
      rows.forEach((r, i) => {
        const nm = typeof (r.story ?? r._id) === "string" ? (r.story ?? r._id).trim() : r.story;
        if (!by.has(nm)) { errs.push(E(t.key, i, "story", `unknown story '${nm}'`)); return; }
        if (vals.has(nm)) { errs.push(E(t.key, i, "story", `story '${nm}' is listed twice`)); return; }
        const v = {};
        for (const c of t.cols.slice(1)) if (c.key in r) { try { v[c.key] = parseCell(c, r[c.key], d); } catch (e) { errs.push(E(t.key, i, c.key, `${c.label}: ${e.message}`)); } }
        vals.set(nm, v);
      });
      if (errs.length > n0) return {};
      for (const [nm, v] of vals) {
        const s = by.get(nm);
        if ("master" in v && !!s.master !== v.master) { if (v.master) s.master = true; else delete s.master; }
        if ("similar_to" in v && (s.similar_to || "") !== v.similar_to) { if (v.similar_to) s.similar_to = v.similar_to; else delete s.similar_to; }
      }
      const msg = mockValidateW8({ stories: d.stories });
      if (msg) { errs.push(E(t.key, null, "similar_to", msg)); return {}; }
      return { rows: rows.length };
    } });

  /* ---- link parameters + frame curves */
  const linkp = { key: "link_properties", title: "Link Properties - Parameters", group: G.OBJ, tkind: "w8", editable: true, canAdd: true, canDelete: true, keycol: null,
    cols: [col("link", "Link", "ref", "none", { ref: "links", required: true }), col("param", "Parameter", "text", "none", { required: true }), col("value", "Value", n, "none", { required: true })],
    w8rows: (t, d) => (d.links || []).flatMap(l => Object.entries(l.params || {}).filter(([, v]) => isNum(v)).map(([k, v]) => ({ _id: [l.uid, k], link: l.uid, param: k, value: v }))),
    w8apply: (t, d, rows, errs) => {
      const n0 = errs.length, links = new Map((d.links || []).map(l => [l.uid, l])), built = new Map(), seen = new Set();
      rows.forEach((r, i) => {
        const u = typeof r.link === "string" ? r.link.trim() : r.link;
        if (!links.has(u)) { errs.push(E(t.key, i, "link", `unknown link '${u}'`)); return; }
        let k, v;
        try { k = parseCell(t.cols[1], r.param, d); if (!k) throw new RowErr("is required"); } catch (e) { errs.push(E(t.key, i, "param", `Parameter: ${e.message}`)); return; }
        try { v = parseCell(t.cols[2], r.value, d); } catch (e) { errs.push(E(t.key, i, "value", `Value: ${e.message}`)); return; }
        if (seen.has(`${u}\u0001${k}`)) { errs.push(E(t.key, i, "param", `duplicate parameter '${k}' on link '${u}'`)); return; }
        seen.add(`${u}\u0001${k}`);
        if (!built.has(u)) built.set(u, {});
        built.get(u)[k] = v;
      });
      if (errs.length > n0) return {};
      for (const [u, l] of links) {
        const old = l.params || {}, keep = {}, nums = {};
        for (const [k, v] of Object.entries(old)) (isNum(v) ? nums : keep)[k] = v;
        const merged = mergeOrdered(nums, built.get(u) || {});
        const nw = {};
        for (const k of Object.keys(old)) if (k in keep) nw[k] = keep[k]; else if (k in merged) nw[k] = merged[k];
        for (const [k, v] of Object.entries(merged)) if (!(k in nw)) nw[k] = v;
        if (!sameJson(old, nw)) l.params = nw;
      }
      return { rows: rows.length };
    } };
  const cvVals = c => { const p = c.via != null ? c.via : c.center, nn = c.plane_normal || [null, null, null];
    return { form: c.via != null ? "via" : "center", px: p[0], py: p[1], pz: p[2], nx: nn[0], ny: nn[1], nz: nn[2], segments: c.segments ?? null, local2: c.local2 || "in_plane" }; };
  const curves = { key: "frame_curves", title: "Frame Objects - Curved Geometry", group: G.OBJ, tkind: "w8", editable: true, canAdd: true, canDelete: true, keycol: null,
    cols: [col("member", "Frame", "ref", "none", { ref: "members", required: true }), col("form", "Form", "enum", "none", { enum: ["via", "center"], def: "via" }),
      col("px", "Point X (via / center)", n, "length", { required: true }), col("py", "Point Y", n, "length", { required: true }), col("pz", "Point Z", n, "length", { required: true }),
      col("nx", "Normal X", n, "none", { optional: true }), col("ny", "Normal Y", n, "none", { optional: true }), col("nz", "Normal Z", n, "none", { optional: true }),
      col("segments", "Segments (blank = auto)", "int", "none", { optional: true }), col("local2", "Local 2 Axis", "enum", "none", { enum: ["in_plane", "normal"], def: "in_plane" })],
    w8rows: (t, d) => (d.members || []).filter(m => m.curve).map(m => ({ _id: m.uid, member: m.uid, ...cvVals(m.curve) })),
    w8apply: (t, d, rows, errs) => {
      const n0 = errs.length, mem = new Map((d.members || []).map(m => [m.uid, m])), nw = new Map();
      rows.forEach((r, i) => {
        const u = typeof r.member === "string" ? r.member.trim() : r.member;
        if (!mem.has(u)) { errs.push(E(t.key, i, "member", `unknown frame '${u}'`)); return; }
        if (nw.has(u)) { errs.push(E(t.key, i, "member", `frame '${u}' is listed twice`)); return; }
        const cur = mem.get(u).curve;
        const v = cur ? cvVals(cur) : { form: "via", px: null, py: null, pz: null, nx: null, ny: null, nz: null, segments: null, local2: "in_plane" };
        let ok = true;
        for (const c of t.cols.slice(1)) if (c.key in r) { try { const x = parseCell(c, r[c.key], d); if (!same(x, v[c.key])) v[c.key] = x; } catch (e) { errs.push(E(t.key, i, c.key, `${c.label}: ${e.message}`)); ok = false; } }
        if (!ok) return;
        for (const k of ["px", "py", "pz"]) if (v[k] == null) { errs.push(E(t.key, i, k, `${t.cols.find(c => c.key === k).label} is required`)); ok = false; }
        if (v.form === "center" && [v.nx, v.ny, v.nz].includes(null)) { errs.push(E(t.key, i, "nx", "the center form needs the plane normal nx, ny, nz")); ok = false; }
        if (!ok) return;
        const c = { type: "arc" };
        if (v.form === "via") c.via = [v.px, v.py, v.pz]; else { c.center = [v.px, v.py, v.pz]; c.plane_normal = [v.nx, v.ny, v.nz]; }
        c.segments = v.segments; c.local2 = v.local2;
        nw.set(u, cur && sameJson(cur, c) ? cur : c);
      });
      if (errs.length > n0) return {};
      for (const [u, m] of mem) { const c = nw.get(u); if (c == null) delete m.curve; else if (c !== m.curve) m.curve = c; }
      return { rows: rows.length };
    } };
  insertAfter("links", linkp, curves);

  /* ---- loads: shell load sets, tendons */
  const lsets = { key: "shell_load_sets", title: "Shell Uniform Load Sets", group: G.LOAD, tkind: "w8", editable: true, canAdd: true, canDelete: true, keycol: null,
    cols: [col("set", "Load Set", "text", "none", { required: true }), col("pattern", "Load Pattern", "enum", "none", { enum: names("patterns"), required: true }),
      col("q", "Load", n, "pressure", { required: true }), col("direction", "Direction", "enum", "none", { enum: AREA_DIRS, def: "gravity" }),
      col("projected", "Projected", "bool", "none", { def: false })],
    w8rows: (t, d) => Object.entries(d.shell_load_sets || {}).flatMap(([sn, s]) => Object.entries(s).map(([p, e]) => { e = typeof e === "object" ? e : { q: e };
      return { _id: [sn, p], set: sn, pattern: p, q: e.q, direction: e.direction || "gravity", projected: !!e.projected }; })),
    w8apply: (t, d, rows, errs) => {
      const n0 = errs.length, old = d.shell_load_sets || {}, built = {};
      rows.forEach((r, i) => {
        const v = {}; const m0 = errs.length;
        for (const c of t.cols) { if (!(c.key in r)) { if (c.required) errs.push(E(t.key, i, c.key, `${c.label} is required`)); continue; } try { v[c.key] = parseCell(c, r[c.key], d); } catch (e) { errs.push(E(t.key, i, c.key, `${c.label}: ${e.message}`)); } }
        if (errs.length > m0) return;
        if (!v.set) { errs.push(E(t.key, i, "set", "Load Set is required")); return; }
        if (built[v.set] && v.pattern in built[v.set]) { errs.push(E(t.key, i, "pattern", `pattern '${v.pattern}' is listed twice in '${v.set}'`)); return; }
        let e = { q: v.q };
        if ((v.direction || "gravity") !== "gravity") e.direction = v.direction;
        if (v.projected) e.projected = true;
        const prev = (old[v.set] || {})[v.pattern];
        if (prev && typeof prev === "object" && Object.keys(prev).length === Object.keys(e).length && Object.keys(e).every(k => same(prev[k], e[k]))) e = prev;
        else if (isNum(prev) && Object.keys(e).length === 1 && prev === e.q) e = prev;
        (built[v.set] = built[v.set] || {})[v.pattern] = e;
      });
      if (errs.length > n0) return {};
      let nw = mergeOrdered(old, built);
      nw = Object.fromEntries(Object.entries(nw).map(([k, v]) => [k, k in old ? mergeOrdered(old[k], v) : v]));
      if (!sameJson(old, nw)) { if (Object.keys(nw).length) d.shell_load_sets = nw; else delete d.shell_load_sets; }
      return { rows: rows.length };
    } };
  const loss = (k, label, q = "none") => col(k, label, n, q, { def: 0, get: o => (o.losses || {})[k] ?? 0, put: (o, v) => { o.losses = { ...(o.losses || {}), [k]: v }; } });
  const tendons = obj("tendons", "Tendons (as loads)", G.LOAD, [
    col("uid", "Tendon", "text"), col("pattern", "Load Pattern", "enum", "none", { enum: names("patterns"), required: true }),
    col("host", "Host Objects", "text", "none", { required: true, get: o => (o.host || []).join(", "), put: (o, v) => { o.host = String(v || "").split(/[,;]/).map(x => x.trim()).filter(Boolean); } }),
    col("points", "Profile Points", "points", "length", { required: true }),
    col("sags", "Segment Sags", "text", "none", { def: "", get: o => (o.sags || []).map(fmtNum).join(", "),
      put: (o, v) => { const xs = String(v || "").split(/[,;\s]+/).filter(Boolean).map(Number); if (!xs.every(isFinite)) throw new RowErr("sags must be numbers"); o.sags = xs; } }),
    col("material", "Material", "text", "none", { def: "A416Gr270" }), col("area", "Area", n, "area", { required: true }),
    col("jacking_stress", "Jacking Stress", n, "stress", { required: true }),
    col("jacking_end", "Jacking End", "enum", "none", { enum: ["start", "end", "both"], def: "start" }),
    loss("friction_mu", "Friction μ"), loss("wobble_k", "Wobble k"), loss("anchor_set", "Anchor Set", "length"), loss("long_term_fraction", "Long-term Loss"),
    col("n_sub", "Sub-pieces", "int", "none", { def: 16 }),
  ], { container: "tendons", kind: "list", keycol: "uid", autoPrefix: "T", newObj: () => ({ uid: "", sags: [], host: [] }) });
  insertAfter("area_loads", lsets, tendons);

  /* ---- cases */
  const caseRename = (d, mp) => { CASC.case(d, mp); for (const k of ["nonlinear_static_cases", "pushover_cases"]) for (const c of Object.values(d[k] || {})) if (c.start_from in mp) c.start_from = mp[c.start_from]; if (d.modal_from_case in mp) d.modal_from_case = mp[d.modal_from_case]; };
  const optNone = (key, label, en) => col(key, label, "enum", "none", { enum: en, def: "", get: o => o[key] || "", put: (o, v) => { o[key] = v || null; } });
  const nls = obj("nonlinear_static_cases", "Load Cases - Nonlinear Static", G.CASE, [
    col("name", "Case", "text", "none", { required: true }),
    col("load_application", "Load Application", "enum", "none", { enum: ["load_control", "displacement_control"], def: "load_control" }),
    col("steps", "Steps", "int", "none", { def: 10 }), optNone("control_story", "Control Story", optStories),
    col("control_dof", "Control DOF", "enum", "none", { enum: ["UX", "UY", "UZ", "RX", "RY", "RZ"], def: "UX" }),
    col("target_disp", "Target Displacement", n, "length", { optional: true }),
    col("geometric", "Geometric Nonlinearity", "enum", "none", { enum: ["none", "p_delta", "large_displacement"], def: "none" }),
    optNone("start_from", "Start From", optNames("nonlinear_static_cases")),
    col("hinges", "Hinges", "enum", "none", { enum: ["column_base", "all_ends", "asce41"], def: "column_base" }),
    col("hardening", "Hardening", n, "none", { def: 0.02 }),
  ], { container: "nonlinear_static_cases", keycol: "name", onRename: caseRename, deleteRefs: REFS.case,
    newObj: () => ({ loads: [], load_application: "load_control", steps: 10, control_point: null, control_story: null, control_dof: "UX", target_disp: null,
      geometric: "none", start_from: null, hinges: "column_base", My: {}, default_My: null, hardening: 0.02, hinge_params: {} }) });
  const nlsLoads = listChild("nonlinear_static_loads", "Load Cases - Nonlinear Static - Loads", G.CASE, [
    col("case", "Case", "enum", "none", { enum: names("nonlinear_static_cases"), required: true }),
    col("pattern", "Load Pattern", "enum", "none", { enum: names("patterns"), required: true }), col("scale", "Scale Factor", n, "none", { required: true })],
  { container: "nonlinear_static_cases", parentCol: "case", field: "loads", newEntry: () => ({ pattern: "", scale: 1 }) });
  const freqCase = (key, title, container) => obj(key, title, G.CASE, [
    col("name", "Case", "text", "none", { required: true }), col("freq_start_hz", "Start Frequency (Hz)", n, "none", { def: 0 }),
    col("freq_end_hz", "End Frequency (Hz)", n, "none", { def: 10 }), col("n_freq", "Frequencies", "int", "none", { def: 101 }),
    col("modal_refine", "Modal Refinement", "bool", "none", { def: true }), col("method", "Method", "enum", "none", { enum: ["modal", "direct"], def: "modal" }),
    col("num_modes", "Modes (0 = model)", "int", "none", { def: 0 }), col("damping", "Damping", n, "none", { def: 0.05 }),
    col("damping_type", "Damping Type", "enum", "none", { enum: ["modal", "hysteretic"], def: "modal" }),
  ], { container, keycol: "name", onRename: CASC.case, deleteRefs: REFS.case,
    newObj: () => ({ loads: [], freq_start_hz: 0, freq_end_hz: 10, n_freq: 101, frequencies: [], modal_refine: true, method: "modal", num_modes: 0, damping: 0.05, damping_type: "modal", output_points: [] }) });
  const fpats = d => [...Object.keys(d.patterns || {}), "accel"]; fpats.refc = "patterns";
  const freqLoads = (key, title, container) => listChild(key, title, G.CASE, [
    col("case", "Case", "enum", "none", { enum: names(container), required: true }),
    col("pattern", "Load Pattern / accel", "enum", "none", { enum: fpats, required: true }),
    col("direction", "Accel Direction", "enum", "none", { enum: ["", "UX", "UY", "UZ"], def: "" }), col("scale", "Scale Factor", n, "none", { def: 1 }),
    col("function", "Function", "enum", "none", { enum: optNames("frequency_functions"), def: "" }), col("phase_deg", "Phase", n, "angle_deg", { def: 0 })],
  { container, parentCol: "case", field: "loads", newEntry: () => ({ pattern: "", direction: "", scale: 1, function: "", phase_deg: 0 }) });
  insertAfter("pushover_cases", nls, nlsLoads,
    freqCase("steady_state_cases", "Load Cases - Steady State", "steady_state_cases"), freqLoads("steady_state_loads", "Load Cases - Steady State - Loads", "steady_state_cases"),
    freqCase("psd_cases", "Load Cases - Power Spectral Density", "psd_cases"), freqLoads("psd_loads", "Load Cases - Power Spectral Density - Loads", "psd_cases"));
  return T;
}
