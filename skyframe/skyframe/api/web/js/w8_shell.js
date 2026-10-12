/* SkyFrame W8 — shell UI for engine features that had none.

   CONTRACT "Shell element types, one-way slabs and shell benchmarks" and
   "Curved frames, wall/floor mesh options, joint patterns and shell load
   sets" (core/shell_types.py, core/oneway.py, core/shellopts.py).

   * Slab / Wall Property Data: "Shell Type" selector (ShellSection.shell_type:
     shell | shell_thin | shell_thick | membrane | plate_thin | plate_thick,
     key emitted only when not "shell") and a one-way load-distribution
     selector that is written to the membrane slabs using the section
     (the engine keeps distribution / one_way_dir per region).
   * Assign Shell Properties: Auto Mesh (max size | n1 × n2 divisions |
     ceil max size → ShellRegion.mesh_divisions), Load Distribution (two-way |
     one-way along local 1 / 2 → distribution / one_way_dir) and Uniform Load
     Set (load_set). Untouched fields write nothing; defaults remove keys.
   * Define › Shell Uniform Load Sets… (model.shell_load_sets) and
     Assign › Shell Loads › Uniform Load Sets… (ShellRegion.load_set).

   Hooks: dlgstd_defs.js (section dialog fields), dlgstd_assign.js (assign
   fields / check / put). Numbers go through js/units.js; the model stays SI. */

import shU from "./units.js";
import { el as shEl, clone as shClone, dkForm as shForm, dkGroup as shGroup, dkNote as shNote,
  dkDialog as shDialog, dkList as shList, dkDraft as shDraft, dkCommit as shCommit, uniqueName as shUnique } from "./dlgkit.js";

export const SHELL_TYPES = [
  ["shell", "Shell (legacy MITC4 / DKGT)"],
  ["shell_thin", "Shell-Thin (Kirchhoff, DKGQ)"],
  ["shell_thick", "Shell-Thick (Mindlin, ASDShellQ4)"],
  ["membrane", "Membrane (in-plane only)"],
  ["plate_thin", "Plate-Thin (bending only)"],
  ["plate_thick", "Plate-Thick (bending only)"],
];
const LAYERED_OK = new Set(["shell", "shell_thin", "shell_thick"]);
const DIST = [["two_way", "Two-way (45° tributary)"], ["one_way:1", "One-way — span along local 1"], ["one_way:2", "One-way — span along local 2"]];
export const AREA_DIRS = ["gravity", "global_x", "global_y", "global_z", "local_1", "local_2", "local_3"];
const DIR_LABEL = { gravity: "Gravity", global_x: "Global X", global_y: "Global Y", global_z: "Global Z", local_1: "Local 1", local_2: "Local 2", local_3: "Local 3 (normal)" };

const distKey = r => (r.distribution === "one_way" ? `one_way:${r.one_way_dir || 1}` : "two_way");

/** One-way eligibility (core/shell_types.validate_region_distribution). */
export function oneWayProblem(r) {
  if (r.kind !== "slab" || (r.behavior || "shell") !== "membrane") return "one-way distribution needs a membrane-behaviour slab";
  if ((r.corners || []).length !== 4 || (r.openings || []).some(o => o.polygon)) return "one-way distribution needs a 4-corner region (no polygon openings)";
  const c = r.corners;
  let ref = null;
  for (let k = 0; k < 4; k++) {
    const a0 = c[k], a1 = c[(k + 1) % 4], a2 = c[(k + 2) % 4];
    const u = [0, 1, 2].map(i => a1[i] - a0[i]), v = [0, 1, 2].map(i => a2[i] - a1[i]);
    const x = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
    if (!ref) ref = x;
    else if (x[0] * ref[0] + x[1] * ref[1] + x[2] * ref[2] <= 0) return "one-way distribution needs a convex region";
  }
  return "";
}

/** Write one assign key onto a region (defaults remove the key). */
export function shellPut(s, k, v) {
  if (k === "mesh_divisions") { if (v == null) delete s.mesh_divisions; else s.mesh_divisions = { ...v }; return true; }
  if (k === "distribution") { if (v === "two_way") { delete s.distribution; delete s.one_way_dir; } else s.distribution = v; return true; }
  if (k === "one_way_dir") { if (v === 1 || s.distribution !== "one_way") delete s.one_way_dir; else s.one_way_dir = v; return true; }
  if (k === "load_set") { if (!v) delete s.load_set; else s.load_set = v; return true; }
  if (k === "distribution_override") { if (v) s.distribution_override = true; else delete s.distribution_override; return true; }   // Wave B
  return false;
}

/* ================================================================
   Slab / Wall Property Data: shell type + one-way distribution
   ================================================================ */
export function w8ShellSectionFields(F, s, D, name) {
  // Wave B: the load distribution is a SECTION property (ShellSection.distribution
  // / one_way_dir) inherited by every membrane slab using the section; a slab's
  // own value (Assign › Shell, distribution_override) stays an override.
  const st = { type: s.shell_type || "shell", dist: s.distribution === "one_way" ? `one_way:${s.one_way_dir || 1}` : "two_way" };
  const users = () => (D.shells || []).filter(r => name && r.section === name && r.kind === "slab" && (r.behavior || "shell") === "membrane");
  const u0 = users();
  const own = u0.filter(r => r.distribution === "one_way" || r.distribution_override).length;
  const typeRow = F.select(st, "type", SHELL_TYPES, {
    label: "Shell type", id: "w8ShType",
    title: "ETABS modelling type: element family + which stiffness (membrane / bending) is carried",
    validate: v => (s.layered && !LAYERED_OK.has(v)) ? "A layered section must be Shell, Shell-Thin or Shell-Thick." : "",
    write: v => { st.type = v; if (v === "shell") delete s.shell_type; else s.shell_type = v; },
  });
  const distRow = F.select(st, "dist", DIST, {
    label: "Load distribution", id: "w8ShDist",
    title: "Membrane slabs: how area loads go to the edge beams (section property, inherited by the slabs)",
    write: v => {
      st.dist = v;
      const [d, dir] = v.split(":");
      if (d === "two_way") { delete s.distribution; delete s.one_way_dir; }
      else { s.distribution = d; s.one_way_dir = dir ? +dir : 1; }
    },
  });
  const note = shNote(u0.length
    ? `Section property: inherited by the <b>${u0.length}</b> membrane slab${u0.length > 1 ? "s" : ""} using this section${own ? ` (${own} keep${own > 1 ? "" : "s"} its own value — Assign › Shell)` : ""}. Polygon / non-convex slabs stay two-way.`
    : "Section property: inherited by every membrane slab using this section (a slab's own value from Assign › Shell overrides it).", "w8-sh-note");
  return {
    nodes: [typeRow, distRow, note],
    /** kept for the editShellSection hook: the distribution is stored on the section itself */
    apply() { return ""; },
  };
}

/* ================================================================
   Assign Shell Properties: auto mesh, distribution, load set
   ================================================================ */
export function w8AssignShellFields(F, sh, P, model) {
  const common = f => { const v0 = JSON.stringify(f(sh[0])); return sh.every(x => JSON.stringify(f(x)) === v0) ? { v: f(sh[0]), mixed: false } : { v: undefined, mixed: true }; };
  const modeOf = r => !r.mesh_divisions ? "size" : (r.mesh_divisions.max_size != null ? "max" : "div");
  const cm = common(modeOf), cn1 = common(r => (r.mesh_divisions || {}).n1 ?? null), cn2 = common(r => (r.mesh_divisions || {}).n2 ?? null),
    cmx = common(r => (r.mesh_divisions || {}).max_size ?? null), cd = common(distKey), cl = common(r => r.load_set || "");
  const V = { mode: cm.v, n1: cn1.v ?? 4, n2: cn2.v ?? 4, max: cmx.v ?? null, dist: cd.v, ls: cl.v };
  const touched = { mesh: false };
  const meshOut = () => {
    if (V.mode === "size") return null;
    if (V.mode === "max") return { max_size: V.max };
    return { n1: V.n1, n2: V.n2 };
  };
  const syncMesh = () => { if (touched.mesh) P.mesh_divisions = meshOut(); };
  const slabsMembrane = sh.every(r => r.kind === "slab");
  const sets = Object.keys(model.shell_load_sets || {});
  const rows = [
    F.select(V, "mode", [["size", "Maximum mesh size (above)"], ["div", "n1 × n2 divisions"], ["max", "Maximum size (ceil per edge)"]],
      { label: "Auto mesh", id: "w8ShMeshMode", mixed: cm.mixed, write: v => { V.mode = v; touched.mesh = true; syncMesh(); } }),
    F.num(V, "n1", { label: "Divisions n1 (edge 0→1)", int: true, min: 1, id: "w8ShN1", mixed: cn1.mixed, write: v => { V.n1 = v; touched.mesh = true; syncMesh(); } }),
    F.num(V, "n2", { label: "Divisions n2 (edge 0→3)", int: true, min: 1, id: "w8ShN2", mixed: cn2.mixed, write: v => { V.n2 = v; touched.mesh = true; syncMesh(); } }),
    F.num(V, "max", { label: "Maximum element size", kind: "length", gt: 0, allowNull: true, nullText: "—", id: "w8ShMax", mixed: cmx.mixed, write: v => { V.max = v; touched.mesh = true; syncMesh(); } }),
  ];
  const distRows = slabsMembrane ? [F.select(V, "dist", DIST, { label: "Load distribution", id: "w8ShAsnDist", mixed: cd.mixed,
    title: "Membrane slabs: two-way (45°) or one-way along region local 1 / 2",
    write: v => { V.dist = v; const [d, dir] = v.split(":"); P.distribution = d; P.one_way_dir = dir ? +dir : 1;
      if (sh.some(r => ((model.shell_sections || {})[r.section] || {}).distribution)) P.distribution_override = true; } })] : [];   // Wave B: own value overrides the section
  const lsRow = F.select(V, "ls", [["", "None"], ...sets.map(n => [n, n])], { label: "Uniform load set", id: "w8ShLoadSet", mixed: cl.mixed,
    write: v => { V.ls = v; P.load_set = v; } });
  return [
    shGroup("Auto Mesh and Load Transfer", [...rows, ...distRows, lsRow], { id: "w8ShAssignGrp" }),
  ];
}

/** Assign check (one message or ""): the values P would write. */
export function w8AssignShellCheck(sh, P) {
  const beh = r => P.behavior || r.behavior || "shell";
  if ("mesh_divisions" in P && P.mesh_divisions) {
    const md = P.mesh_divisions;
    if (md.max_size !== undefined && !(md.max_size > 0)) return "Enter the maximum element size.";
    if (md.n1 !== undefined && !(Number.isInteger(md.n1) && md.n1 >= 1 && Number.isInteger(md.n2) && md.n2 >= 1)) return "Divisions n1 and n2 must be whole numbers ≥ 1.";
    if (sh.some(r => beh(r) !== "shell")) return "Auto mesh options need shell behaviour (membrane slabs are not meshed).";
  }
  if (P.distribution === "one_way") {
    for (const r of sh) { const msg = oneWayProblem({ ...r, behavior: beh(r) }); if (msg) return `Shell ${r.uid}: ${msg}.`; }
  }
  if (P.behavior === "membrane" && !("mesh_divisions" in P) && sh.some(r => r.mesh_divisions)) return "Clear the auto mesh option first: membrane slabs are not meshed.";
  return "";
}

/* ================================================================
   Define › Shell Uniform Load Sets
   ================================================================ */
export function openLoadSets(sky, { select } = {}) {
  const D = shDraft(sky);
  D.shell_load_sets = D.shell_load_sets || {};
  const hadKey = Object.prototype.hasOwnProperty.call(sky.store.model, "shell_load_sets");
  const usedBy = n => (D.shells || []).filter(r => r.load_set === n);
  const err = shEl("p", { class: "dk-err dk-side-err", role: "alert" });
  const list = shList({
    id: "w8LoadSetList",
    items: () => Object.entries(D.shell_load_sets).map(([k, s]) => ({ key: k, s })),
    columns: [
      { label: "Load Set", get: it => it.key },
      { label: "Patterns", get: it => Object.entries(it.s).map(([p, e]) => `${p} ${shU.fmt("pressure", typeof e === "number" ? e : e.q, 2)}`).join(" · ") },
      { label: "Used by", get: it => { const n = usedBy(it.key).length; return n ? `${n} shell${n > 1 ? "s" : ""}` : "—"; } },
    ],
    selected: select || null, onOpen: k => edit(k),
    empty: "No uniform load sets yet — add one (e.g. OFFICE: SDL 1.5 + LIVE 2.4 kPa).",
    actions: [
      { sep: "Add" },
      { label: "Add New Load Set…", id: "w8LsAdd", fn: () => edit(null) },
      { label: "Add Copy of Load Set…", id: "w8LsCopy", needsSel: true, fn: k => edit(null, { copyOf: k }) },
      { sep: "Selected" },
      { label: "Modify/Show Load Set…", id: "w8LsModify", needsSel: true, fn: k => edit(k) },
      { label: "Delete Load Set", id: "w8LsDelete", needsSel: true, fn: k => del(k) },
    ],
    extraSide: err,
  });
  const del = k => {
    const n = usedBy(k).length;
    if (n) { err.textContent = `${k} is used by ${n} shell${n > 1 ? "s" : ""} — assign another set first.`; return; }
    err.textContent = ""; delete D.shell_load_sets[k]; list.render();
  };
  const edit = (name, o = {}) => editLoadSet(sky, D, name, { ...o, done: nm => list.select(nm) });
  return shDialog({
    id: "w8LoadSets", title: "Shell Uniform Load Sets", iconId: "exleaf-shells", size: "lg",
    intro: "Named occupancy load sets: each assigns a uniform load in several load patterns at once (Assign › Shell Loads › Uniform Load Sets).",
    body: list,
    onOk: () => {
      if (!Object.keys(D.shell_load_sets).length && !hadKey) delete D.shell_load_sets;
      shCommit(sky, D);
    },
  });
}

function editLoadSet(sky, D, name, { copyOf, done } = {}) {
  const src = name ? D.shell_load_sets[name] : (copyOf ? D.shell_load_sets[copyOf] : null);
  const nm0 = name || shUnique(D.shell_load_sets, copyOf ? copyOf + "-copy" : "LSET1");
  const draftName = { v: nm0 };
  const pats = Object.keys(D.patterns || {});
  const rows = src ? Object.entries(src).map(([p, e]) => { const o = typeof e === "number" ? { q: e } : e; return { pattern: p, q: o.q, direction: o.direction || "gravity", projected: !!o.projected }; })
    : (pats.length ? [{ pattern: pats[0], q: 0, direction: "gravity", projected: false }] : []);
  const orig = shClone(rows);
  const F = shForm();
  const tbl = F.table(rows, [
    { key: "pattern", label: "Load Pattern", type: "select", options: () => pats.map(p => [p, p]) },
    { key: "q", label: "Load", type: "num", kind: "pressure" },
    { key: "direction", label: "Direction", type: "select", options: () => AREA_DIRS.map(d => [d, DIR_LABEL[d]]) },
  ], { id: "w8LsTable", addLabel: "+ Add pattern", minRows: 1, minMsg: "Add at least one load pattern.",
    newRow: rs => { const free = pats.find(p => !rs.some(r => r.pattern === p)); return free ? { pattern: free, q: 0, direction: "gravity", projected: false } : null; },
    validate: rs => { const s = new Set(); for (const r of rs) { if (s.has(r.pattern)) return `Pattern ${r.pattern} is listed twice.`; s.add(r.pattern); } return ""; } });
  const body = [
    shGroup("General", [F.text(draftName, "v", { label: "Load Set Name", id: "w8LsName",
      validate: v => (v !== name && Object.prototype.hasOwnProperty.call(D.shell_load_sets, v)) ? `“${v}” is already used — pick another name.` : "" })]),
    shGroup("Uniform Loads", [tbl, shNote("Membrane slabs take uniform gravity loads only (their load goes to the edge beams).")]),
  ];
  return shDialog({
    id: "w8LoadSetData", title: "Shell Uniform Load Set Data", iconId: "exleaf-shells", size: "md", form: F, body,
    onOk: () => {
      const nn = draftName.v.trim();
      if (!F.touched && name && JSON.stringify(rows) === JSON.stringify(orig)) { done && done(name); return; }
      const out = {};
      for (const r of rows) {
        const e = { q: +r.q };
        if (r.direction && r.direction !== "gravity") e.direction = r.direction;
        if (r.projected) e.projected = true;
        out[r.pattern] = e;
      }
      if (name && nn !== name) {
        const next = {};
        for (const [k, v] of Object.entries(D.shell_load_sets)) next[k === name ? nn : k] = v;
        D.shell_load_sets = next;
        for (const r of D.shells || []) if (r.load_set === name) r.load_set = nn;
      }
      const prev = D.shell_load_sets[nn];
      D.shell_load_sets[nn] = prev && JSON.stringify(prev) === JSON.stringify(out) ? prev : out;
      done && done(nn);
    },
  });
}

/* ================================================================
   Assign › Shell Loads › Uniform Load Sets
   ================================================================ */
export function openAssignLoadSet(sky) {
  const S = sky.store;
  const refs = (S.selection || []).filter(r => r.type === "shell");
  const sets = Object.keys(S.model.shell_load_sets || {});
  if (!refs.length || !sets.length) {
    return shDialog({ id: "w8AsnLoadSet", title: "Assign Shell Uniform Load Set", iconId: "exleaf-shells", size: "sm", noCancel: true,
      body: [shNote(!sets.length ? "No uniform load sets defined — use <b>Define › Section Properties › Shell Uniform Load Sets…</b> first."
        : "Nothing to assign — select one or more <b>walls or slabs</b> first (V).")] });
  }
  const sh = refs.map(r => S.model.shells.find(s => s.uid === r.uid)).filter(Boolean);
  const v0 = sh[0].load_set || "";
  const mixed = sh.some(s => (s.load_set || "") !== v0);
  const V = { ls: mixed ? undefined : v0 };
  const P = {};
  const F = shForm();
  const body = [shGroup("Load Set", [F.select(V, "ls", [["", "None (remove)"], ...sets.map(n => [n, n])], { label: "Uniform load set", id: "w8AsnLsSel", mixed, write: v => { P.load_set = v; } }),
    shNote("The set's loads are added to each pattern at analysis time; the shell's own area loads are unchanged.")])];
  const apply = () => {
    if (!("load_set" in P)) return "";
    const ls = P.load_set;
    if (ls) {
      const set = S.model.shell_load_sets[ls] || {};
      const bad = sh.find(r => (r.behavior || "shell") === "membrane" && Object.values(set).some(e => typeof e === "object" && ((e.direction || "gravity") !== "gravity" || e.projected)));
      if (bad) return `Shell ${bad.uid} is a membrane slab: it takes uniform gravity loads only (edit the set or the behaviour).`;
    }
    const D = shDraft(sky);
    const uids = new Set(sh.map(s => s.uid));
    for (const r of D.shells) if (uids.has(r.uid)) shellPut(r, "load_set", ls);
    shCommit(sky, D);
    return "";
  };
  return shDialog({ id: "w8AsnLoadSet", title: "Assign Shell Uniform Load Set", iconId: "exleaf-shells", size: "sm", form: F, body,
    intro: `${sh.length} shell object${sh.length > 1 ? "s" : ""} selected.`,
    onOk: () => { const m = apply(); if (m) return m; }, onApply: () => apply() });
}

export function initW8Shell(sky) {
  if (!sky.menu) return;
  sky.menu.add("Define/Section Properties", { label: "Shell Uniform Load Sets…", act: "def-w8-loadsets", hint: "occupancy load sets", fn: () => openLoadSets(sky) });
  sky.menu.add("Assign/Shell Loads", { label: "Uniform Load Sets…", act: "asn-w8-sloadset", fn: () => openAssignLoadSet(sky) });
  const assignShell = () => (sky.std && sky.std.assignShell ? sky.std.assignShell() : null);
  sky.menu.add("Assign/Shell", { label: "Auto Mesh / Load Distribution…", act: "asn-w8-smesh", hint: "n1 × n2 · one-way", fn: assignShell });
  sky.w8shell = { openLoadSets: o => openLoadSets(sky, o), openAssignLoadSet: () => openAssignLoadSet(sky), oneWayProblem, SHELL_TYPES };
}

export default { w8ShellSectionFields, w8AssignShellFields, w8AssignShellCheck, shellPut, openLoadSets, openAssignLoadSet, initW8Shell, oneWayProblem };
