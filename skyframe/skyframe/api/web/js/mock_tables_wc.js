/* SkyFrame Wave C — ?mock=1 Show Tables (mirrors skyframe/core/tables_wave_c.py):
   same keys / titles / groups / columns as the backend catalogue, rows
   computed from the mock model + results:
     joint velocities / accelerations (relative / absolute) — from the mock
       TH plot_functions joints (Max, Min, then every step);
     assembled joint masses, mass summary by story / diaphragm / group —
       the mock lumps each story mass at the plan centre (+ joint masses);
     material lists by section / story / material — exact (model only);
     story max / avg displacements, generalized displacements — from the
       mock node_disp of static cases / additive combos;
     modal load participation — mockLoadParticipation().
   Side-effect module: imported once by js/mock.js. SI values. */

import { registerMockTable as wcRegister, mockLoadParticipation as wcMockLp } from "./mock_tables.js";

const C = (key, label, quantity) => ({ key, label, quantity });
const DOF6 = ["UX", "UY", "UZ", "RX", "RY", "RZ"];
const G_JOINT_TH = "Joint Output > Time History", G_MASS = "Structure Output > Mass Summary",
  G_STORY = "Structure Output > Story Output", G_MODAL = "Modal Results", G_GD = "Structure Output > Other Output Items",
  G_MAT = "Structure Output > Material List";
const TOL = 1e-6;

const jtCols = (tq, rq) => [C("story", "Story", "text"), C("label", "Label", "text"), C("joint", "Unique Name", "id"),
  C("case", "Output Case", "text"), C("case_type", "Case Type", "text"), C("step_type", "Step Type", "text"),
  C("step", "Step Number", "id"), C("time", "Time", "time"),
  ...DOF6.slice(0, 3).map(k => C(k, k, tq)), ...DOF6.slice(3).map(k => C(k, k, rq))];
const MASS6 = [...DOF6.slice(0, 3).map(k => C(k, k, "mass")), ...DOF6.slice(3).map(k => C(k, k, "mass_moi"))];
const TAKE = [C("pieces", "Pieces", "id"), C("length", "Length", "length"), C("area", "Area", "area"),
  C("volume", "Volume", "volume"), C("weight", "Weight", "force")];

const storyAt = (model, z) => {
  for (const s of model.stories || []) if (Math.abs(s.elevation - z) < TOL) return s.name;
  const s0 = (model.stories || [])[0];
  return s0 && Math.abs(z - (s0.elevation - s0.height)) < TOL ? "Base" : "";
};
function sources(model, r, f) {
  const out = [];
  for (const [n, cd] of Object.entries(r.cases || {})) {
    if (f && !f.has(n)) continue;
    const lc = (model.cases || {})[n] || {};
    out.push([n, (lc.pdelta || (lc.geometric && lc.geometric !== "linear")) ? "NonStatic" : "LinStatic", cd]);
  }
  for (const [n, cd] of Object.entries(r.combos || {})) {
    if (f && !f.has(n)) continue;
    const cb = (model.combos || {})[n] || {};
    if ((cb.combo_type || "add") !== "add" || cd.min) continue;
    out.push([n, "Combination", cd]);
  }
  return out;
}
const thCaseType = (model, n) => ((model.th_cases || {})[n] || {}).nonlinear ? "NonDirHist" : "LinDirHist";

function jointTh(relKey, absKey) {
  return (model, r, f, warn) => {
    const rows = [];
    for (const [name, th] of Object.entries(r.th_cases || {})) {
      const pf = th && th.plot_functions;
      if (!pf) continue;
      const t = pf.t || [];
      for (const j of pf.joints || []) {
        const rel = j[relKey] || {}, ab = absKey ? (j[absKey] || {}) : {};
        const ser = Object.fromEntries(DOF6.map(k => [k, (absKey && k in ab ? ab[k] : rel[k]) || []]));
        const n = Math.max(0, ...DOF6.map(k => ser[k].length));
        const [x, y, z] = j.point.map(Number);
        const base = { story: storyAt(model, z), label: "", joint: +(j.node || 0), case: name, case_type: thCaseType(model, name) };
        for (const [st, fn] of [["Max", a => Math.max(...a)], ["Min", a => Math.min(...a)]])
          rows.push({ ...base, step_type: st, step: null, time: null, ...Object.fromEntries(DOF6.map(k => [k, ser[k].length ? fn(ser[k]) : null])) });
        for (let i = 0; i < n; i++)
          rows.push({ ...base, step_type: "Step", step: i, time: i < t.length ? t[i] : null, ...Object.fromEntries(DOF6.map(k => [k, i < ser[k].length ? ser[k][i] : null])) });
      }
    }
    if (!rows.length && Object.keys(r.th_cases || {}).length)
      warn.push("no recorded joints: add the joints to the time-history case's Output Requests (Plot Functions) to tabulate their velocities and accelerations");
    return rows;
  };
}

/* ---- masses: story masses lumped at the plan centre + explicit joint masses */
function massRows(model) {
  const xs = [], ys = [];
  for (const m of model.members || []) for (const p of [m.pi, m.pj]) { xs.push(p[0]); ys.push(p[1]); }
  for (const r of model.shells || []) for (const p of r.corners || []) { xs.push(p[0]); ys.push(p[1]); }
  const lx = xs.length ? Math.max(...xs) - Math.min(...xs) : 0, ly = ys.length ? Math.max(...ys) - Math.min(...ys) : 0;
  const cx = xs.length ? (Math.max(...xs) + Math.min(...xs)) / 2 : 0, cy = ys.length ? (Math.max(...ys) + Math.min(...ys)) / 2 : 0;
  const sm = { ...(model.story_masses || {}), ...(model.explicit_story_masses || {}) };
  const rows = [];
  for (const s of model.stories || []) {
    const m = +(sm[s.name] || 0);
    if (m > 0) rows.push({ story: s.name, label: "Diaphragm master", joint: null, UX: m, UY: m, UZ: 0, RX: 0, RY: 0, RZ: m * (lx * lx + ly * ly) / 12, x: cx, y: cy, z: s.elevation });
  }
  for (const nm of model.nodal_masses || []) {
    const [x, y, z] = nm.point;
    rows.push({ story: storyAt(model, z), label: "", joint: null, UX: nm.mx || 0, UY: nm.my || 0, UZ: nm.mz || 0, RX: 0, RY: 0, RZ: 0, x, y, z });
  }
  return rows;
}
const storyOfZ = (model, z) => {
  const ss = model.stories || [];
  if (!ss.length || z <= ss[0].elevation - ss[0].height + TOL) return "Base";
  for (const s of ss) if (z <= s.elevation + TOL) return s.name;
  return ss[ss.length - 1].name;
};

/* ---- material take-off (exact port) */
const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
function polyArea(cs) {
  let nx = 0, ny = 0, nz = 0;
  for (let i = 0; i < cs.length; i++) { const a = cs[i], b = cs[(i + 1) % cs.length]; nx += a[1] * b[2] - a[2] * b[1]; ny += a[2] * b[0] - a[0] * b[2]; nz += a[0] * b[1] - a[1] * b[0]; }
  return Math.hypot(nx, ny, nz) / 2;
}
function takeoff(model) {
  const items = [], mats = model.materials || {};
  for (const m of model.members || []) {
    const s = (model.sections || {})[m.section], mat = s && mats[s.material];
    if (!s || !mat) continue;
    const L = dist(m.pi, m.pj), vol = (+s.A || 0) * L;
    items.push({ section: m.section, material: s.material, object_type: m.kind ? m.kind[0].toUpperCase() + m.kind.slice(1) : "", story: m.story || "",
      length: L, area: 0, volume: vol, weight: vol * (+mat.unit_weight || 0) * (s.mod_weight ?? 1) });
  }
  for (const r of model.shells || []) {
    const s = (model.shell_sections || {})[r.section], mat = s && mats[s.material];
    if (!s || !mat) continue;
    let A = polyArea(r.corners || []);
    for (const o of r.openings || []) if (Array.isArray(o.polygon)) A -= polyArea(o.polygon);
    const t = s.layered ? (s.layered.layers || []).reduce((a, l) => a + (+l.t || 0), 0) : +s.thickness || 0;
    const vol = A * t;
    items.push({ section: r.section, material: s.material, object_type: r.kind === "slab" ? "Floor" : (r.kind ? r.kind[0].toUpperCase() + r.kind.slice(1) : ""),
      story: r.story || "", length: 0, area: A, volume: vol, weight: vol * (+mat.unit_weight || 0) * (s.weight ?? 1) });
  }
  return items;
}
function aggregate(items, keys) {
  const acc = new Map();
  for (const it of items) {
    const k = keys.map(x => it[x]).join("\u0001");
    if (!acc.has(k)) acc.set(k, { ...Object.fromEntries(keys.map(x => [x, it[x]])), pieces: 0, length: 0, area: 0, volume: 0, weight: 0 });
    const e = acc.get(k);
    e.pieces++; for (const f of ["length", "area", "volume", "weight"]) e[f] += it[f];
  }
  return [...acc.values()];
}
function withTotal(rows, keys) {
  if (!rows.length) return rows;
  const tot = Object.fromEntries(keys.map(k => [k, ""]));
  tot[keys[0]] = "Total";
  tot.pieces = rows.reduce((a, r) => a + r.pieces, 0);
  for (const f of ["length", "area", "volume", "weight"]) tot[f] = rows.reduce((a, r) => a + r[f], 0);
  return [...rows, tot];
}

/* ---- story max / avg */
function storyMaxAvg(model, r, f) {
  const rows = [], nodes = r.nodes || {};
  const by = {};
  for (const [t, p] of Object.entries(nodes)) { const s = storyAt(model, p[2]); if (s && s !== "Base") (by[s] = by[s] || []).push(t); }
  for (const [name, ctype, cd] of sources(model, r, f)) {
    const nd = cd.node_disp || {};
    for (const s of [...(model.stories || [])].reverse()) {
      const tags = (by[s.name] || []).filter(t => t in nd);
      if (!tags.length) continue;
      for (const [dir, k] of [["X", 0], ["Y", 1]]) {
        const vals = tags.map(t => [Math.abs(nd[t][k]), +t]);
        let mx = vals[0];
        for (const v of vals) if (v[0] > mx[0] || (v[0] === mx[0] && v[1] < mx[1])) mx = v;
        const mn = Math.min(...vals.map(v => v[0])), avg = 0.5 * (mx[0] + mn);
        if (mx[0] < 1e-14) continue;
        rows.push({ story: s.name, case: name, case_type: ctype, direction: dir, max_disp: mx[0], min_disp: mn, avg_disp: avg,
          ratio: avg > 1e-30 ? mx[0] / avg : 1, label_max: mx[1] });
      }
    }
  }
  return rows;
}

/* ---- generalized displacements */
function genDisp(model, r, f, warn) {
  const gds = model.generalized_displacements || {}, nodes = r.nodes || {}, rows = [];
  const tagAt = p => { for (const [t, c] of Object.entries(nodes)) if (Math.abs(c[0] - p[0]) < TOL && Math.abs(c[1] - p[1]) < TOL && Math.abs(c[2] - p[2]) < TOL) return t; return null; };
  for (const [name, gd] of Object.entries(gds)) {
    const tags = (gd.terms || []).map(t => tagAt(t.point));
    if (tags.some(t => t == null)) { warn.push(`${name}: a term's point is not a joint of the analysis model`); continue; }
    const trans = (gd.type || "translation") === "translation";
    const row = (c, ct, st, v) => ({ name, type: trans ? "Translation" : "Rotation", case: c, case_type: ct, step_type: st, translation: trans ? v : null, rotation: trans ? null : v });
    for (const [c, ct, cd] of sources(model, r, f)) {
      const nd = cd.node_disp || {};
      if (tags.some(t => !nd[t])) continue;
      rows.push(row(c, ct, "", gd.terms.reduce((a, t, i) => a + (t.sf ?? 1) * nd[tags[i]][DOF6.indexOf(String(t.dof).toUpperCase())], 0)));
    }
  }
  return rows;
}

const TABLES = [
  ["joint_velocities", "Joint Velocities", G_JOINT_TH, jtCols("velocity", "angular_velocity"), jointTh("vel", null)],
  ["joint_velocities_absolute", "Joint Velocities - Absolute", G_JOINT_TH, jtCols("velocity", "angular_velocity"), jointTh("vel", "vel_abs")],
  ["joint_accelerations", "Joint Accelerations", G_JOINT_TH, jtCols("accel", "angular_accel"), jointTh("acc", null)],
  ["joint_accelerations_absolute", "Joint Accelerations - Absolute", G_JOINT_TH, jtCols("accel", "angular_accel"), jointTh("acc", "acc_abs")],
  ["assembled_joint_masses", "Assembled Joint Masses", G_MASS,
    [C("story", "Story", "text"), C("label", "Label", "text"), C("joint", "Unique Name", "id"), ...MASS6, C("x", "X", "length"), C("y", "Y", "length"), C("z", "Z", "length")],
    model => {
      const rows = massRows(model);
      if (!rows.length) return rows;
      const tot = Object.fromEntries(DOF6.map(k => [k, rows.reduce((a, r) => a + r[k], 0)]));
      return [...rows, { story: "", label: "Sum", joint: null, ...tot, x: null, y: null, z: null }];
    }],
  ["mass_summary_by_story", "Mass Summary by Story", G_MASS, [C("story", "Story", "text"), ...MASS6], model => {
    const acc = {};
    for (const r of massRows(model)) { const s = storyOfZ(model, r.z); acc[s] = acc[s] || Object.fromEntries(DOF6.map(k => [k, 0])); for (const k of DOF6) acc[s][k] += r[k]; }
    return [...[...(model.stories || [])].reverse().map(s => s.name), "Base"].filter(s => acc[s]).map(s => ({ story: s, ...acc[s] }));
  }],
  ["mass_summary_by_diaphragm", "Mass Summary by Diaphragm", G_MASS,
    [C("story", "Story", "text"), C("diaphragm", "Diaphragm", "text"), C("mass_x", "Mass X", "mass"), C("mass_y", "Mass Y", "mass"),
      C("mmi", "Mass Moment of Inertia", "mass_moi"), C("x", "X Mass Center", "length"), C("y", "Y Mass Center", "length")],
    model => massRows(model).filter(r => r.label === "Diaphragm master").reverse().map(r => ({ story: r.story, diaphragm: "D1", mass_x: r.UX, mass_y: r.UY, mmi: r.RZ, x: r.x, y: r.y }))],
  ["mass_summary_by_group", "Mass Summary by Group", G_MASS, [C("group", "Group", "text"), C("n_joints", "Joints", "id"), ...MASS6],
    (model, r, f, warn) => {
      const rows = Object.entries(model.groups || {}).map(([g, grp]) => {
        const pts = grp.points || [], tot = Object.fromEntries(DOF6.map(k => [k, 0]));
        let n = 0;
        for (const nm of model.nodal_masses || []) if (pts.some(p => p.every((v, i) => Math.abs(v - nm.point[i]) < TOL))) { n++; tot.UX += nm.mx || 0; tot.UY += nm.my || 0; tot.UZ += nm.mz || 0; }
        return { group: g, n_joints: n, ...tot };
      });
      if (rows.length) warn.push("mass lumped on a rigid-diaphragm master belongs to no group (only joint masses are summed)");
      return rows;
    }],
  ["story_max_avg_displacements", "Story Max Over Avg Displacements", G_STORY,
    [C("story", "Story", "text"), C("case", "Output Case", "text"), C("case_type", "Case Type", "text"), C("direction", "Direction", "text"),
      C("max_disp", "Maximum", "length"), C("min_disp", "Minimum", "length"), C("avg_disp", "Average", "length"), C("ratio", "Ratio", "ratio"),
      C("label_max", "Joint (max)", "id")], storyMaxAvg],
  ["modal_load_participation", "Modal Load Participation Ratios", G_MODAL,
    [C("case", "Case", "text"), C("item_type", "Item Type", "text"), C("item", "Item", "text"), C("static", "Static", "percent"), C("dynamic", "Dynamic", "percent")],
    (model, r) => {
      let lp;
      try { lp = ((r.modal || {}).load_participation) || wcMockLp(model, r); } catch { return []; }
      return [...Object.entries(lp.acceleration || {}).map(([k, v]) => ({ case: "Modal", item_type: "Acceleration", item: k, static: v.static, dynamic: v.dynamic })),
        ...Object.entries(lp.patterns || {}).map(([k, v]) => ({ case: "Modal", item_type: "Load Pattern", item: k, static: v.static, dynamic: v.dynamic }))];
    }],
  ["generalized_displacements", "Generalized Displacements", G_GD,
    [C("name", "Name", "text"), C("type", "Type", "text"), C("case", "Output Case", "text"), C("case_type", "Case Type", "text"),
      C("step_type", "Step Type", "text"), C("translation", "Translation", "length"), C("rotation", "Rotation", "angle")], genDisp],
  ["material_list_by_section", "Material List by Section Property", G_MAT,
    [C("section", "Section", "text"), C("object_type", "Object Type", "text"), C("material", "Material", "text"), ...TAKE],
    model => withTotal(aggregate(takeoff(model), ["section", "object_type", "material"]), ["section", "object_type", "material"])],
  ["material_list_by_story", "Material List by Story", G_MAT,
    [C("story", "Story", "text"), C("object_type", "Object Type", "text"), C("material", "Material", "text"), ...TAKE],
    model => {
      const rank = Object.fromEntries((model.stories || []).map((s, i) => [s.name, i]));
      const rows = aggregate(takeoff(model), ["story", "object_type", "material"]).sort((a, b) => (rank[b.story] ?? -1) - (rank[a.story] ?? -1));
      return withTotal(rows, ["story", "object_type", "material"]);
    }],
  ["material_list_by_material", "Material List by Material", G_MAT,
    [C("material", "Material", "text"), C("object_type", "Object Type", "text"), ...TAKE],
    model => withTotal(aggregate(takeoff(model), ["material", "object_type"]), ["material", "object_type"])],
];
for (const [k, t, g, cols, fn] of TABLES) wcRegister(k, t, g, cols, fn);
export const WC_MOCK_TABLE_KEYS = TABLES.map(t => t[0]);
