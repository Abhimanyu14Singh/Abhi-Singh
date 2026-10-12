/* SkyFrame — Wave C ETABS-parity dialogs (CONTRACT "Wave C UI").

     Define › Groups, Cuts & Labels › Pier Labels…                   def-wc-piers
     Define › Auto Lateral Loads › Eccentricity Overrides…           def-wc-ecc
     Define › Auto Lateral Loads › Diaphragm Design Forces (Fpx)…    def-wc-fpx
     Assign › Frame › Pier Label…                                    asn-wc-fpier
     Assign › Shell › Pier Label…                                    asn-wc-spier
     Assign › Shell › Floor Auto Mesh Options…                       asn-wc-floormesh
     Assign › Shell › Auto Edge Constraints…                         asn-wc-edge
     Assign › Frame Loads › Distributed Moments…                     asn-wc-fmoment

   Model (SI; every key written ONLY when the user changes it, so OK with
   defaults leaves the model byte-identical):
     members[i].pier, shells[i].pier, pier_labels           frame / wall pier labels
     shells[i].floor_mesh {mode, max_size, at_beams, at_walls, at_grids}
     shells[i].edge_constraint (true | false; absent = model switch)
     patterns[p].member_loads[] {kind:"moment", moment_dist:"uniform"|"trapezoid", w, w2, a, b, direction}
     patterns[p].ecc_overrides {story | "story/diaphragm": ratio | {length}}
     fpx_settings {SDS, Ie, patterns, method, Rs, Cs, Omega0, zs, SD1}
   Backend: POST /api/fpx, POST /api/pattern/ecc-torques (live); the
   ?mock=1 ports live in js/mock_wavec.js. Every number goes through
   js/units.js. */

import wcU from "./units.js";
import { el as wcEl, esc as wcEsc, dkForm as wcForm, dkGroup as wcGroup, dkList as wcList, dkNote as wcNote,
  dkDialog as wcDialog, dkDraft as wcDraft, dkCommit as wcCommit, dkAdvanced as wcAdvanced, uniqueName as wcUnique,
  clone as wcClone } from "./dlgkit.js";
import { wcFpxCompute as wcMockFpx, wcFpxPatterns as wcFpxPats, wcFpxGenerate as wcGenFpx,
  wcEccTorques as wcMockEcc, wcRegisterMockTables as wcRegMockTables } from "./mock_wavec.js";

const WC_LABEL_RE = /^[^\s,;][^,;]{0,63}$/;
const wcSel = sky => (sky.store.selection || []);
const wcIsMock = sky => !!(sky && sky.store && sky.store.mock);
const wcModelBody = m => JSON.parse(JSON.stringify(m, (k, v) => k === "_mock_params" ? undefined : v));
const wcFmt = (kind, v, d = 2) => v == null || !isFinite(v) ? "—" : wcU.fmt(kind, v, d);
async function wcPost(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}
const isVertical = m => Math.abs((m.pj || [0, 0, 0])[2] - (m.pi || [0, 0, 0])[2]) > 1e-9;

/* ================================================================
   Pier labels (Define list + Assign Frame / Shell)
   ================================================================ */
export function wcPierLabels(m) {
  const out = [...((m && m.pier_labels) || [])];
  for (const o of [...((m && m.members) || []), ...((m && m.shells) || [])]) if (o.pier && !out.includes(o.pier)) out.push(o.pier);
  return out;
}
function wcPierUsage(m, lab) {
  const fr = (m.members || []).filter(x => x.pier === lab).map(x => x.uid);
  const sh = (m.shells || []).filter(x => x.pier === lab).map(x => x.uid);
  return { frames: fr, shells: sh, n: fr.length + sh.length };
}
const wcSetPierLabels = (D, labels) => { if (labels.length) D.pier_labels = labels; else delete D.pier_labels; };

export function wcOpenPierLabels(sky) {
  const D = wcDraft(sky);
  let labels = wcPierLabels(D);
  const items = () => labels.map(l => ({ key: l, ...wcPierUsage(D, l) }));
  const ask = (title, init, onOk) => {
    const F = wcForm();
    const V = { name: init };
    wcDialog({ id: "wcPierLabelData", title, size: "sm", form: F,
      body: [wcGroup("Pier Label", [F.text(V, "name", { label: "Label", id: "wcPierLabelName",
        validate: v => !WC_LABEL_RE.test(String(v || "").trim()) ? "1–64 characters, no comma or semicolon." :
          (String(v).trim() !== init && labels.includes(String(v).trim()) ? `“${String(v).trim()}” is already used — pick another name.` : "") })])],
      onOk: () => { const n = String(V.name || "").trim(); if (!WC_LABEL_RE.test(n)) return "Enter a valid label."; onOk(n); } });
  };
  const list = wcList({
    id: "wcPierList", items,
    columns: [{ label: "Pier", get: it => it.key }, { label: "Columns", get: it => it.frames.length }, { label: "Walls", get: it => it.shells.length }],
    empty: "No pier labels yet — add one, then assign it to columns or wall panels (Assign › Frame / Shell › Pier Label).",
    actions: [
      { label: "Add New Pier…", id: "wcPierAdd", fn: () => ask("Pier Label", wcUnique(new Set(labels), "P1"), n => { labels.push(n); wcSetPierLabels(D, labels); list.select(n); }) },
      { label: "Modify / Rename…", id: "wcPierRename", needsSel: true, fn: s => ask("Pier Label", s, n => {
        if (n === s) return;
        labels = labels.map(l => l === s ? n : l);
        for (const o of [...(D.members || []), ...(D.shells || [])]) if (o.pier === s) o.pier = n;
        wcSetPierLabels(D, labels); list.select(n); }) },
      { label: "Delete Pier", id: "wcPierDel", needsSel: true, fn: s => {
        const u = wcPierUsage(D, s);
        if (u.n) { dlg.setError(`“${s}” is assigned to ${u.n} object${u.n > 1 ? "s" : ""} (${[...u.frames, ...u.shells].slice(0, 6).join(", ")}${u.n > 6 ? " …" : ""}) — unassign first.`); return; }
        labels = labels.filter(l => l !== s); wcSetPierLabels(D, labels); dlg.setError(""); list.render(); } },
    ],
  });
  const dlg = wcDialog({ id: "wcPierLabels", title: "Pier Labels", iconId: "exleaf-shells", size: "md",
    intro: "ETABS piers: columns and wall panels sharing a label form one pier per story. Pier forces (P, V, M at the story bottom) are reported after a run (Display › Force Diagrams › Pier Forces).",
    body: [list], onOk: () => { wcCommit(sky, D); } });
  return dlg;
}

export function wcOpenPierAssign(sky, kind) {
  const S = sky.store;
  const isFrame = kind === "frame";
  const refs = wcSel(sky).filter(r => r.type === (isFrame ? "member" : "shell"));
  const objs = refs.map(r => (isFrame ? S.model.members : S.model.shells).find(o => o.uid === r.uid))
    .filter(o => o && (isFrame ? isVertical(o) : o.kind === "wall"));
  const title = isFrame ? "Assign Frame Pier Label" : "Assign Shell Pier Label";
  const id = isFrame ? "wcPierAssignFrame" : "wcPierAssignShell";
  if (!objs.length) {
    return wcDialog({ id, title, size: "sm", noCancel: true, iconId: isFrame ? "exleaf-frames" : "exleaf-shells",
      body: [wcNote(`Nothing to assign — select one or more <b>${isFrame ? "columns (vertical or inclined frames)" : "walls"}</b> first, then choose this command again.` +
        (isFrame ? " Horizontal beams belong to spandrels." : " Pier labels apply to walls only."))] });
  }
  const F = wcForm();
  const cur = new Set(objs.map(o => o.pier || ""));
  const V = { label: cur.size === 1 ? [...cur][0] : "", newName: "" };
  let touched = false;
  const opts = () => [["", "None"], ...wcPierLabels(S.model).map(l => [l, l]), ["\u0000new", "New label…"]];
  const body = [wcGroup("Pier", [
    F.select(V, "label", opts, { label: "Pier label", id: id + "Label", mixed: cur.size > 1, write: v => { V.label = v; touched = true; } }),
    F.text(V, "newName", { label: "New label", id: id + "New", placeholder: "e.g. P1", required: false,
      hint: "used when the label is “New label…”", write: v => { V.newName = v; } }),
  ], { note: isFrame ? "Columns sharing a label (and the walls with the same label) form one pier per story; it is cut at each story bottom."
    : "Wall panels sharing a label form one pier per story (in-plane P, V, M at the story bottom)." })];
  const apply = () => {
    if (!touched) return "";
    let lab = V.label;
    if (lab === "\u0000new") {
      lab = String(V.newName || "").trim();
      if (!WC_LABEL_RE.test(lab)) return "Enter a new label (1–64 characters, no comma or semicolon).";
    }
    const D = wcDraft(sky);
    const uids = new Set(objs.map(o => o.uid));
    for (const o of (isFrame ? D.members : D.shells) || []) {
      if (!uids.has(o.uid)) continue;
      if (lab) o.pier = lab; else if (isFrame) delete o.pier; else o.pier = "";
    }
    if (V.label === "\u0000new") {
      const defs = [...(D.pier_labels || [])];
      if (!defs.includes(lab)) { defs.push(lab); D.pier_labels = defs; }
    }
    wcCommit(sky, D);
    return "";
  };
  return wcDialog({ id, title, size: "sm", form: F, body, iconId: isFrame ? "exleaf-frames" : "exleaf-shells",
    intro: `${objs.length} ${isFrame ? "column" : "wall"}${objs.length > 1 ? "s" : ""} selected. Choose None to remove the label.`,
    onOk: () => { const m = apply(); if (m) return m; }, onApply: () => apply() });
}

/* ================================================================
   Assign › Shell › Floor Auto Mesh Options
   ================================================================ */
const WC_FM_MODES = [["", "Default — use the shell's mesh settings (no floor options)"],
  ["default", "Auto Cookie Cut at beams / walls + mesh lines"], ["cookie_cut", "Auto Cookie Cut (cuts only, no forced lines)"],
  ["rectangular", "Auto Rectangular Mesh (no cuts)"], ["none", "No Auto Mesh — use the object as drawn"]];
export function wcOpenFloorMesh(sky) {
  const S = sky.store;
  const slabs = wcSel(sky).filter(r => r.type === "shell").map(r => S.model.shells.find(o => o.uid === r.uid))
    .filter(o => o && o.kind === "slab" && (o.behavior || "shell") === "shell");
  const id = "wcFloorMesh";
  if (!slabs.length) {
    return wcDialog({ id, title: "Floor Auto Mesh Options", size: "sm", noCancel: true, iconId: "exleaf-shells",
      body: [wcNote("Nothing to assign — select one or more <b>slabs with shell behaviour</b> (membrane slabs are not meshed), then choose this command again.")] });
  }
  const common = f => { const s = new Set(slabs.map(f)); return s.size === 1 ? { v: [...s][0], mixed: false } : { v: undefined, mixed: true }; };
  const fmOf = o => o.floor_mesh || null;
  const cm = common(o => (fmOf(o) && (fmOf(o).mode || "default")) || "");
  const cmax = common(o => (fmOf(o) && fmOf(o).max_size) ?? null);
  const flags = ["at_beams", "at_walls", "at_grids"].map(k => [k, common(o => !fmOf(o) || fmOf(o)[k] !== false)]);
  const V = { mode: cm.v, max_size: cmax.v ?? null, at_beams: true, at_walls: true, at_grids: true };
  for (const [k, c] of flags) V[k] = c.v;
  const touched = new Set();
  const F = wcForm();
  const body = [
    wcGroup("Floor Meshing Option", [
      F.radio(V, "mode", WC_FM_MODES, { idPrefix: "wcFmMode_", write: v => { V.mode = v; touched.add("mode"); sync(); } }),
    ]),
    wcGroup("Auto Mesh Options", [
      F.num(V, "max_size", { label: "Maximum element size", kind: "length", gt: 0, allowNull: true, nullText: "shell mesh size", id: "wcFmMax",
        mixed: cmax.mixed, write: v => { V.max_size = v; touched.add("max_size"); } }),
      F.check(V, "at_beams", { label: "Mesh at beams (and other in-plane frames)", id: "wcFmBeams", mixed: flags[0][1].mixed, write: v => { V.at_beams = v; touched.add("at_beams"); } }),
      F.check(V, "at_walls", { label: "Mesh at wall traces", id: "wcFmWalls", mixed: flags[1][1].mixed, write: v => { V.at_walls = v; touched.add("at_walls"); } }),
      F.check(V, "at_grids", { label: "Mesh at grid lines", id: "wcFmGrids", mixed: flags[2][1].mixed, write: v => { V.at_grids = v; touched.add("at_grids"); } }),
    ], { id: "wcFmOpts", note: "Active only with an auto floor mesh option. No Auto Mesh makes a 4-corner slab ONE element (corners only) and a polygon slab the coarsest conforming mesh." }),
  ];
  const optsBox = body[1];
  const sync = () => {
    const off = !V.mode || V.mode === "none";
    optsBox.classList.toggle("dk-disabled", off);
    optsBox.querySelectorAll("input").forEach(i => { i.disabled = off; });
  };
  sync();
  const apply = () => {
    if (!touched.size) return "";
    if (V.mode === "none") {
      const bad = slabs.find(o => (o.corners || []).length === 4 && (o.openings || []).length);
      if (bad) return `Slab ${bad.uid} has openings — No Auto Mesh needs a 4-corner slab without openings.`;
      const md = slabs.find(o => o.mesh_divisions);
      if (md) return `Slab ${md.uid} has explicit mesh divisions (Assign › Shell › Auto Mesh) — clear them first.`;
    }
    const D = wcDraft(sky);
    const uids = new Set(slabs.map(o => o.uid));
    for (const o of D.shells || []) {
      if (!uids.has(o.uid)) continue;
      const mode = touched.has("mode") ? V.mode : (o.floor_mesh ? (o.floor_mesh.mode || "default") : "");
      if (!mode) { delete o.floor_mesh; continue; }
      const fm = { ...(o.floor_mesh || {}), mode };
      if (mode === "none") { delete fm.max_size; delete fm.at_beams; delete fm.at_walls; delete fm.at_grids; o.floor_mesh = { mode: "none" }; continue; }
      if (touched.has("max_size")) { if (V.max_size == null) delete fm.max_size; else fm.max_size = V.max_size; }
      for (const k of ["at_beams", "at_walls", "at_grids"]) if (touched.has(k)) { if (V[k]) delete fm[k]; else fm[k] = false; }
      o.floor_mesh = fm;
    }
    wcCommit(sky, D);
    return "";
  };
  return wcDialog({ id, title: "Floor Auto Mesh Options", size: "md", form: F, body, iconId: "exleaf-shells",
    intro: `${slabs.length} slab${slabs.length > 1 ? "s" : ""} selected (ETABS Assign › Shell › Floor Auto Mesh Options).`,
    onOk: () => { const m = apply(); if (m) return m; }, onApply: () => apply() });
}

/* ================================================================
   Assign › Shell › Auto Edge Constraints
   ================================================================ */
export function wcOpenEdge(sky) {
  const S = sky.store;
  const shells = wcSel(sky).filter(r => r.type === "shell").map(r => S.model.shells.find(o => o.uid === r.uid))
    .filter(o => o && (o.behavior || "shell") === "shell");
  const id = "wcEdge";
  if (!shells.length) {
    return wcDialog({ id, title: "Auto Edge Constraints", size: "sm", noCancel: true, iconId: "exleaf-shells",
      body: [wcNote("Nothing to assign — select one or more <b>meshed shells</b> (walls or slabs with shell behaviour), then choose this command again.")] });
  }
  const val = o => o.edge_constraint == null ? "model" : (o.edge_constraint ? "yes" : "no");
  const cs = new Set(shells.map(val));
  const V = { v: cs.size === 1 ? [...cs][0] : undefined };
  let touched = false;
  const F = wcForm();
  const model = S.model.edge_constraints ? "on" : "off";
  const body = [wcGroup("Edge Constraints", [
    F.radio(V, "v", [["model", `Use the model-wide setting (Options › Auto Edge Constraints — currently ${model})`],
      ["yes", "Create auto edge constraints for these areas"], ["no", "Do not create edge constraints for these areas"]],
    { idPrefix: "wcEdge_", write: v => { V.v = v; touched = true; } }),
  ], { note: "An area set to “create” ties the mid-edge nodes of mismatched neighbour meshes and frame ends landing on its edges to its own edges, and ties its own nodes that land on a coarser neighbour's edges (the v0.22 zipper)." })];
  const apply = () => {
    if (!touched || !V.v) return "";
    const D = wcDraft(sky);
    const uids = new Set(shells.map(o => o.uid));
    for (const o of D.shells || []) {
      if (!uids.has(o.uid)) continue;
      if (V.v === "model") delete o.edge_constraint; else o.edge_constraint = V.v === "yes";
    }
    wcCommit(sky, D);
    return "";
  };
  return wcDialog({ id, title: "Auto Edge Constraints", size: "md", form: F, body, iconId: "exleaf-shells",
    intro: `${shells.length} area${shells.length > 1 ? "s" : ""} selected.`,
    onOk: () => { const m = apply(); if (m) return m; }, onApply: () => apply() });
}

/* ================================================================
   Assign › Frame Loads › Distributed Moments
   ================================================================ */
const WC_MOM_DIRS = [["local_1", "Local 1 (torque)"], ["local_2", "Local 2"], ["local_3", "Local 3"],
  ["global_x", "Global X"], ["global_y", "Global Y"], ["global_z", "Global Z"]];
export function wcOpenDistMoment(sky) {
  const S = sky.store, m = S.model;
  const members = wcSel(sky).filter(r => r.type === "member").map(r => m.members.find(o => o.uid === r.uid)).filter(Boolean);
  const id = "wcDistMoment";
  if (!members.length) {
    return wcDialog({ id, title: "Assign Frame Loads — Distributed Moments", size: "sm", noCancel: true, iconId: "exleaf-frames",
      body: [wcNote("Nothing to assign — select one or more <b>frame objects</b> first (V), then choose this command again.")] });
  }
  const pats = Object.keys(m.patterns || {});
  const V = { pattern: S.loadPattern && pats.includes(S.loadPattern) ? S.loadPattern : (pats[0] || "DEAD"), shape: "uniform", direction: "local_3",
    w: 0, w2: 0, a: 0, b: 1, mode: "add" };
  const F = wcForm();
  const existing = wcEl("div", { class: "dk-exist", id: "wcDmExisting" });
  const lmKind = "line_moment";
  const renderExisting = () => {
    const p = (m.patterns || {})[V.pattern];
    const rows = [];
    for (const mm of members) for (const l of ((p && p.member_loads) || []).filter(l => l.member_uid === mm.uid && l.kind === "moment" && l.moment_dist)) {
      const dn = (WC_MOM_DIRS.find(d => d[0] === l.direction) || [0, l.direction])[1];
      rows.push(`${mm.uid}: ${l.moment_dist === "trapezoid" ? `${wcU.fmt(lmKind, l.w, 2)} → ${wcU.fmtU(lmKind, l.w2, 2)}` : wcU.fmtU(lmKind, l.w, 2)} on ${+(+l.a).toFixed(3)}–${+(+l.b).toFixed(3)}·L · about ${dn}`);
    }
    existing.innerHTML = rows.length ? rows.map(t => `<div>${wcEsc(t)}</div>`).join("") : `<span class="muted">none</span>`;
  };
  const w2Row = F.num(V, "w2", { label: "End moment m₂", kind: lmKind, id: "wcDmW2" });
  const sync = () => {
    w2Row.classList.toggle("hidden", V.shape !== "trapezoid");
    renderExisting();
  };
  const body = [
    wcGroup("Load Pattern", [F.select(V, "pattern", () => pats.length ? pats.map(p => [p, p]) : [["DEAD", "DEAD (new)"]], { label: "Load pattern", id: "wcDmPattern", onChange: () => renderExisting() })]),
    wcGroup("Load Type and Direction", [
      F.radio(V, "shape", [["uniform", "Uniform"], ["trapezoid", "Trapezoidal"]], { idPrefix: "wcDmShape_", onChange: () => sync() }),
      F.select(V, "direction", WC_MOM_DIRS, { label: "Moment about", id: "wcDmDir",
        hint: "Right-hand rule about the axis; Local 1 = torque along the member (i → j)." }),
    ]),
    wcGroup("Distributed Moment", [
      F.num(V, "w", { label: "Moment m (start m₁)", kind: lmKind, id: "wcDmW" }),
      w2Row,
      F.num(V, "a", { label: "Start distance a (0–1)", min: 0, max: 1, id: "wcDmA" }),
      F.num(V, "b", { label: "End distance b (0–1)", min: 0, max: 1, id: "wcDmB", validate: v => v < V.a ? "Must be at least the start distance a." : "" }),
    ]),
    wcGroup("Options", [F.radio(V, "mode", [["add", "Add to existing loads"], ["replace", "Replace existing loads"], ["delete", "Delete existing loads"]], { idPrefix: "wcDmMode_" })]),
    wcGroup("Existing distributed moments in pattern", [existing]),
  ];
  sync();
  const apply = () => {
    if (!F.touched) return "";
    const { pattern: pn, shape, mode } = V;
    const w = +V.w || 0, w2 = shape === "trapezoid" ? (+V.w2 || 0) : w, a = +V.a, b = +V.b;
    if (mode !== "delete" && !(a >= 0 && a <= b && b <= 1)) return "Distances must satisfy 0 ≤ a ≤ b ≤ 1.";
    const zero = w === 0 && w2 === 0;
    if (mode === "add" && zero) return "";
    const D = wcDraft(sky);
    D.patterns = D.patterns || {};
    if (!D.patterns[pn]) {
      if (mode === "delete" || zero) return "";
      if (sky.ME && sky.ME.ensurePattern) sky.ME.ensurePattern(D, pn);
      else D.patterns[pn] = { name: pn, kind: "other", member_udls: [], nodal_loads: [], story_forces: [], member_loads: [], area_loads: [], thermal_loads: [],
        accidental_torsion: false, ecc: 0.05, self_weight_factor: 0 };
    }
    const pat = D.patterns[pn];
    pat.member_loads = pat.member_loads || [];
    for (const mm of members) {
      if (mode !== "add") pat.member_loads = pat.member_loads.filter(l => !(l.member_uid === mm.uid && l.kind === "moment" && l.moment_dist));
      if (mode === "delete" || zero) continue;
      pat.member_loads.push({ member_uid: mm.uid, kind: "moment", w, w2, a, b, direction: V.direction, moment_dist: shape });
    }
    wcCommit(sky, D);
    S.loadPattern = pn;
    return "";
  };
  return wcDialog({ id, title: "Assign Frame Loads — Distributed Moments", size: "md", form: F, body, iconId: "exleaf-frames",
    intro: `${members.length} frame${members.length > 1 ? "s" : ""} selected. Distributed couple per unit length (ETABS Frame Loads › Distributed, Load Type Moment); positive by the right-hand rule.`,
    onOk: () => { const r = apply(); if (r) return r; }, onApply: () => apply() });
}

/* ================================================================
   Define › Auto Lateral Loads › Eccentricity Overrides
   ================================================================ */
const wcStoryForcePats = m => Object.entries(m.patterns || {}).filter(([, p]) => (p.story_forces || []).length).map(([n]) => n);
export function wcOpenEccOverrides(sky, pattern) {
  const D = wcDraft(sky);
  const pats = wcStoryForcePats(D);
  const id = "wcEcc";
  if (!pats.length) {
    return wcDialog({ id, title: "Diaphragm Eccentricity Overrides", size: "sm", noCancel: true, iconId: "exleaf-loads",
      body: [wcNote("No static lateral pattern yet — generate a seismic pattern first (Define › Auto Lateral Loads › Seismic / Wind Generators).")] });
  }
  const st = { pattern: pattern && pats.includes(pattern) ? pattern : pats[0] };
  const rowsOf = pn => Object.entries((D.patterns[pn] || {}).ecc_overrides || {}).map(([k, v]) => {
    const [s, ...rest] = String(k).split("/");
    return { story: s, diaphragm: rest.join("/"), ratio: typeof v === "object" ? null : v, length: typeof v === "object" ? v.length : null };
  });
  const tables = Object.fromEntries(pats.map(p => [p, rowsOf(p)]));
  const dirty = new Set();
  const F = wcForm();
  const stories = (D.stories || []).map(s => [s.name, s.name]);
  const dias = [["", "(story)"], ...Object.keys(D.diaphragms || {}).map(d => [d, d]), ["(free)", "(free nodes)"]];
  const host = wcEl("div", { id: "wcEccHost" });
  const info = wcEl("div", { class: "muted dk-note", id: "wcEccInfo" });
  const preview = wcEl("div", { id: "wcEccPreview" });
  const build = () => {
    for (const n of [...host.childNodes]) F.forget(n);
    host.textContent = "";
    const p = D.patterns[st.pattern];
    info.innerHTML = p.accidental_torsion
      ? `Accidental torsion is <b>on</b> (ecc ${wcEsc(String(p.ecc ?? 0.05))}${p.ecc_y != null ? `, ecc_y ${wcEsc(String(p.ecc_y))}` : ""}); overrides replace |ecc| at the listed stories / diaphragms and keep the pattern's sign.`
      : "Accidental torsion is <b>off</b> for this pattern — overrides take effect only when it is turned on (Load Pattern Data › eccentricity).";
    host.appendChild(F.table(tables[st.pattern], [
      { key: "story", label: "Story", type: "select", options: () => stories },
      { key: "diaphragm", label: "Diaphragm", type: "select", options: () => dias },
      { key: "ratio", label: "Ecc. ratio", type: "num" },
      { key: "length", label: "Ecc. length", type: "num", kind: "length" },
    ], { id: "wcEccTable", addLabel: "+ Add override", empty: "No overrides — every story uses the pattern eccentricity.",
      newRow: rs => ({ story: (stories[rs.length] || stories[0] || [""])[0], diaphragm: "", ratio: 0.05, length: null }),
      onChange: () => { dirty.add(st.pattern); runPreview(); },
      validateRow: (r, i) => {
        if ((r.ratio == null) === (r.length == null)) return `Row ${i + 1}: give either a ratio or a length (clear the other cell).`;
        if (r.ratio != null && !(r.ratio >= 0 && r.ratio <= 0.5)) return `Row ${i + 1}: ratio must be between 0 and 0.5.`;
        if (r.length != null && !(r.length >= 0)) return `Row ${i + 1}: length must be at least zero.`;
        return "";
      },
      validate: rs => { const k = rs.map(r => r.diaphragm ? `${r.story}/${r.diaphragm}` : r.story); const dup = k.find((x, i) => k.indexOf(x) !== i); return dup ? `“${dup}” is listed twice.` : ""; } }));
    runPreview();
  };
  // a cleared number cell reads as null: map "" → null before validation
  host.addEventListener("change", e => {
    const t = e.target;
    if (t && t.matches && t.matches("input.dk-num") && t.value.trim() === "") {
      const tr = t.closest("tr[data-row]");
      if (!tr) return;
      const row = tables[st.pattern][+tr.dataset.row];
      const lbl = t.getAttribute("aria-label") || "";
      if (row) { if (/ratio/i.test(lbl)) row.ratio = null; else row.length = null; dirty.add(st.pattern); runPreview(); }
    }
  }, true);
  let seq = 0;
  const overridesOf = pn => {
    const out = {};
    for (const r of tables[pn]) {
      const k = r.diaphragm ? `${r.story}/${r.diaphragm}` : r.story;
      if (r.ratio != null && r.length == null) out[k] = +r.ratio;
      else if (r.length != null && r.ratio == null) out[k] = { length: +r.length };
    }
    return out;
  };
  const runPreview = async () => {
    const my = ++seq;
    const M = wcClone(D);
    const ov = overridesOf(st.pattern);
    if (Object.keys(ov).length) M.patterns[st.pattern].ecc_overrides = ov; else delete M.patterns[st.pattern].ecc_overrides;
    let rows;
    try {
      rows = wcIsMock(sky) ? wcMockEcc(M, st.pattern) : (await wcPost("/api/pattern/ecc-torques", { pattern: st.pattern, model: wcModelBody(M) })).rows;
    } catch (e) { if (my === seq) preview.textContent = e.message || String(e); return; }
    if (my !== seq) return;
    const t = wcEl("table", { class: "data-table dk-table", id: "wcEccTorques" });
    const Fu = wcU.label("force"), Mu = wcU.label("moment");
    t.innerHTML = `<thead><tr><th class="txt">Story</th><th>Fx (${wcEsc(Fu)})</th><th>Fy (${wcEsc(Fu)})</th><th>Mz from Fx (${wcEsc(Mu)})</th><th>Mz from Fy (${wcEsc(Mu)})</th><th class="txt">Override</th></tr></thead><tbody>` +
      rows.map(r => `<tr><td class="txt">${wcEsc(r.story)}</td><td>${wcFmt("force", r.fx)}</td><td>${wcFmt("force", r.fy)}</td><td>${wcFmt("moment", r.mz_x)}</td><td>${wcFmt("moment", r.mz_y)}</td>` +
        `<td class="txt">${r.override == null ? "—" : (typeof r.override === "object" ? wcEsc(wcU.fmtU("length", r.override.length, 3)) : wcEsc("ratio " + r.override))}</td></tr>`).join("") + "</tbody>";
    preview.textContent = "";
    preview.appendChild(wcEl("div", { class: "dk-table-scroll", style: "max-height:200px;overflow:auto" }, [t]));
  };
  const body = [
    wcGroup("Load Pattern", [F.select(st, "pattern", pats.map(p => [p, p]), { label: "Static lateral pattern", id: "wcEccPattern", onChange: () => build() }), info]),
    wcGroup("Overwrite Eccentricities (per story / diaphragm)", [host]),
    wcAdvanced([preview, wcNote("Story torques on the rigid-diaphragm path, from the plan extents (named diaphragms use their own extents in the analysis).")],
      { summary: "Story torque check", open: true }),
  ];
  build();
  return wcDialog({ id, title: "Diaphragm Eccentricity Overrides", size: "lg", form: F, body, iconId: "exleaf-loads",
    intro: "ETABS Modify Lateral Load › Overwrite Eccentricities: a ratio (× the perpendicular plan dimension) or an absolute eccentricity per story or story / diaphragm.",
    onOk: () => {
      if (!dirty.size) return;
      for (const pn of dirty) {
        const ov = overridesOf(pn);
        if (Object.keys(ov).length) D.patterns[pn].ecc_overrides = ov; else delete D.patterns[pn].ecc_overrides;
      }
      wcCommit(sky, D);
    } });
}

/* ================================================================
   Define › Auto Lateral Loads › Diaphragm Design Forces (Fpx)
   ================================================================ */
export function wcOpenFpx(sky) {
  const D = wcDraft(sky);
  const had = D.fpx_settings != null;
  const s0 = had ? wcClone(D.fpx_settings) : {};
  const all = wcFpxPats(D);
  const V = { SDS: s0.SDS ?? 1.0, Ie: s0.Ie ?? 1.0, method: s0.method || "12.10.1", Rs: s0.Rs ?? 1.5, Cs: s0.Cs ?? 0.1, Omega0: s0.Omega0 ?? 3.0,
    zs: s0.zs ?? 1.0, SD1: s0.SD1 ?? 0.6 };
  const chosen = Object.fromEntries(all.map(n => [n, !s0.patterns || s0.patterns.includes(n)]));
  const F = wcForm();
  const out = wcEl("div", { id: "wcFpxOut" });
  const note = wcEl("div", { class: "muted dk-note", id: "wcFpxNote" });
  let reports = [];
  let seq = 0;
  const settings = () => {
    const s = { SDS: +V.SDS, Ie: +V.Ie };
    if (V.method !== "12.10.1") Object.assign(s, { method: V.method, Rs: +V.Rs, Cs: +V.Cs, Omega0: +V.Omega0, zs: +V.zs, SD1: +V.SD1 });
    const pp = all.filter(n => chosen[n]);
    if (pp.length !== all.length) s.patterns = pp;
    if (s0.weights) s.weights = s0.weights;
    if (s0.wpx) s.wpx = s0.wpx;
    return s;
  };
  const recompute = async () => {
    const my = ++seq;
    const s = settings();
    out.textContent = "";
    if (!(s.SDS > 0) || !(s.Ie > 0)) { note.textContent = "Enter SDS and Ie > 0."; return; }
    const names = s.patterns || all;
    try {
      reports = wcIsMock(sky) ? names.map(n => wcMockFpx(D, n, s))
        : (await wcPost("/api/fpx", { model: wcModelBody(D), ...s, patterns: names })).reports;
    } catch (e) { if (my === seq) note.textContent = e.message || String(e); return; }
    if (my !== seq) return;
    const Fu = wcU.label("force");
    for (const r of reports) {
      const t = wcEl("table", { class: "data-table dk-table wc-fpx-table", "data-pattern": r.pattern });
      t.innerHTML = `<caption class="txt">${wcEsc(r.pattern)} · ${r.direction} · ASCE 7 §${wcEsc(r.method)}</caption>` +
        `<thead><tr><th class="txt">Story</th><th>w<sub>i</sub></th><th>F<sub>i</sub></th><th>ΣF</th><th>Σw</th><th>w<sub>px</sub></th><th>F<sub>px</sub> eq.</th><th>min</th><th>max</th><th>F<sub>px</sub> (${wcEsc(Fu)})</th><th class="txt">Governs</th></tr></thead><tbody>` +
        r.stories.map(x => `<tr><td class="txt">${wcEsc(x.story)}</td><td>${wcFmt("force", x.w)}</td><td>${wcFmt("force", x.F)}</td><td>${wcFmt("force", x.sum_F)}</td><td>${wcFmt("force", x.sum_w)}</td><td>${wcFmt("force", x.wpx)}</td>` +
          `<td>${wcFmt("force", x.Fpx_eq)}</td><td>${wcFmt("force", x.Fpx_min)}</td><td>${wcFmt("force", x.Fpx_max)}</td><td><b>${wcFmt("force", x.Fpx)}</b></td><td class="txt">${wcEsc(x.governs)}</td></tr>`).join("") + "</tbody>";
      out.appendChild(wcEl("div", { class: "dk-table-scroll", style: "max-height:220px;overflow:auto" }, [t]));
    }
    note.textContent = reports.length ? `Forces in ${Fu}; weights w from the mass source (story seismic weight) unless set in fpx_settings.weights.` : "No seismic pattern with story forces — generate one first (Seismic / Wind Generators).";
  };
  const altBox = wcEl("div");
  const buildAlt = () => {
    for (const n of [...altBox.childNodes]) F.forget(n);
    altBox.textContent = "";
    if (V.method !== "12.10.3") return;
    altBox.appendChild(wcGroup("§12.10.3 Alternative Design Provisions", [
      F.num(V, "Rs", { label: "Rs — diaphragm force reduction factor", gt: 0, def: 1.5, id: "wcFpxRs", onChange: recompute, hint: "Table 12.10.3-1" }),
      F.num(V, "Cs", { label: "Cs — seismic response coefficient", gt: 0, id: "wcFpxCs", onChange: recompute }),
      F.num(V, "Omega0", { label: "Ω0 — overstrength factor", gt: 0, def: 3, id: "wcFpxOm", onChange: recompute }),
      F.num(V, "zs", { label: "zs — mode shape factor", gt: 0, def: 1, id: "wcFpxZs", onChange: recompute, hint: "0.3 BRBF · 0.7 moment frames · 0.85 dual · 1.0 other" }),
      F.num(V, "SD1", { label: "SD1", gt: 0, id: "wcFpxSd1", onChange: recompute }),
    ]));
  };
  const genBtn = wcEl("button", { type: "button", class: "btn btn-small", id: "wcFpxGen", title: "One story-force pattern per level carrying that level's Fpx" }, ["Generate Fpx Load Patterns"]);
  const showBtn = wcEl("button", { type: "button", class: "btn btn-small", id: "wcFpxShow", title: "Show Tables › Diaphragm Design Forces (ASCE 7 12.10)" }, ["Show Table…"]);
  let generated = [];
  genBtn.addEventListener("click", () => {
    if (!reports.length) { dlg.setError("Nothing to generate — check SDS / Ie and the selected patterns."); return; }
    generated = [];
    for (const r of reports) generated.push(...wcGenFpx(D, r));
    dlg.setError("");
    dlg.setNote(`Generated ${generated.length} pattern${generated.length === 1 ? "" : "s"}: ${wcEsc(generated.slice(0, 6).join(", "))}${generated.length > 6 ? " …" : ""} — press OK to keep them.`);
    F.touched = true;
  });
  let dlg = null;
  showBtn.addEventListener("click", () => { if (dlg && dlg.ok) dlg.ok.click(); setTimeout(() => sky.openShowTables && sky.openShowTables("diaphragm_design_forces"), 0); });
  const patChecks = all.map(n => F.check(chosen, n, { label: n, id: "wcFpxPat_" + n.replace(/\W/g, "_"), onChange: recompute }));
  const body = [
    wcGroup("Seismic Parameters", [
      F.num(V, "SDS", { label: "SDS", gt: 0, id: "wcFpxSds", onChange: recompute }),
      F.num(V, "Ie", { label: "Importance factor Ie", gt: 0, def: 1, id: "wcFpxIe", onChange: recompute }),
      F.select(V, "method", [["12.10.1", "§12.10.1.1 — Eq. 12.10-1 with 0.2 / 0.4 SDS Ie wpx limits"], ["12.10.3", "§12.10.3 — alternative (Cpx / Rs)"]],
        { label: "Method", id: "wcFpxMethod", onChange: () => { buildAlt(); recompute(); } }),
    ]),
    altBox,
    wcGroup("Seismic Load Patterns", patChecks.length ? patChecks : [wcNote("No seismic pattern with story forces yet.")]),
    wcGroup("Diaphragm Design Forces", [out, note]),
  ];
  buildAlt();
  recompute();
  dlg = wcDialog({ id: "wcFpx", title: "Diaphragm Design Forces (Fpx)", iconId: "exleaf-loads", size: "lg", form: F, body,
    intro: "ASCE 7-16 / 7-22 §12.10: Fpx = ΣF<sub>i</sub> / Σw<sub>i</sub> · w<sub>px</sub> (levels x to n), 0.2 S<sub>DS</sub> I<sub>e</sub> w<sub>px</sub> ≤ Fpx ≤ 0.4 S<sub>DS</sub> I<sub>e</sub> w<sub>px</sub>.",
    extra: [genBtn, showBtn],
    onOk: () => {
      if (!F.touched) return;
      D.fpx_settings = settings();
      wcCommit(sky, D);
    } });
  return dlg;
}

/* ================================================================
   install
   ================================================================ */
export function initWaveC(sky) {
  if (!sky) return;
  try { wcRegMockTables(); } catch (e) { console.warn("Wave C mock tables", e); }
  sky.waveC = {
    openPierLabels: () => wcOpenPierLabels(sky), openPierAssign: k => wcOpenPierAssign(sky, k), openFloorMesh: () => wcOpenFloorMesh(sky),
    openEdge: () => wcOpenEdge(sky), openDistMoment: () => wcOpenDistMoment(sky), openEcc: p => wcOpenEccOverrides(sky, p), openFpx: () => wcOpenFpx(sky),
    pierLabels: () => wcPierLabels(sky.store.model),
  };
  if (!(sky.menu && sky.menu.add)) return;
  sky.menu.add("Define/Groups, Cuts & Labels", { label: "Pier Labels…", act: "def-wc-piers", before: "def-a8-spandrels", hint: "columns · wall panels", fn: () => wcOpenPierLabels(sky) });
  sky.menu.add("Define/Auto Lateral Loads", { label: "Eccentricity Overrides…", act: "def-wc-ecc", hint: "per story / diaphragm", fn: () => wcOpenEccOverrides(sky) });
  sky.menu.add("Define/Auto Lateral Loads", { label: "Diaphragm Design Forces (Fpx)…", act: "def-wc-fpx", hint: "ASCE 7 §12.10", fn: () => wcOpenFpx(sky) });
  sky.menu.add("Assign/Frame", { label: "Pier Label…", act: "asn-wc-fpier", before: "asn-a8-fspandrel", hint: "selected columns", fn: () => wcOpenPierAssign(sky, "frame") });
  sky.menu.add("Assign/Shell", { label: "Pier Label…", act: "asn-wc-spier", before: "asn-a8-sspandrel", hint: "selected walls", fn: () => wcOpenPierAssign(sky, "shell") });
  sky.menu.add("Assign/Shell", { label: "Floor Auto Mesh Options…", act: "asn-wc-floormesh", hint: "selected slabs", fn: () => wcOpenFloorMesh(sky) });
  sky.menu.add("Assign/Shell", { label: "Auto Edge Constraints…", act: "asn-wc-edge", hint: "selected areas", fn: () => wcOpenEdge(sky) });
  sky.menu.add("Assign/Frame Loads", { label: "Distributed Moments…", act: "asn-wc-fmoment", before: "asn-fconc", hint: "uniform · trapezoidal", fn: () => wcOpenDistMoment(sky) });
}
