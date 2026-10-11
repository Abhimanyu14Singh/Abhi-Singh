/* SkyFrame — standard (dlgkit) Assign dialogs for the current selection:

     Assign › Frame · Section / Releases / Local Axis / Rigid End Offsets /
              Axial Limit / Hinges     → "Assign Frame Properties" (tabs)
     Assign › Shell · Section / Wind Cp → "Assign Shell Properties"
     Assign › Supports / Springs        → "Assign Joint Restraints" (+ the
                                          model-wide automatic base fixity)
     right-click › Info                 → "Object Information" (read-only)

   ETABS assign semantics: each field starts at the value common to the
   selection ("— mixed —" otherwise) and ONLY the fields the user changes
   are written, to every selected object. OK applies and closes, Apply
   applies and keeps the dialog open (live preview on the canvas). An
   untouched OK writes nothing (byte-identical). Numbers go through
   js/units.js; the model stays SI. */

import * as daME from "./modeledit.js";
import daU from "./units.js";
import { selectionJoints as daSelectionJoints } from "./assigndlg.js";
import {
  el, esc, isNum, dkForm, dkGroup, dkAdvanced, dkTabs, dkNote, dkDialog, dkInfo,
  dkDraft, dkCommit,
} from "./dlgkit.js";
import { spvFramePanel as daSpvFrame, spvSplit as daSpvSplit } from "./secpreview.js";   // preview of the chosen section (js/secpreview.js)

const sel = sky => (sky.store.selection || []);
const common = (arr, f) => {
  if (!arr.length) return { v: undefined, mixed: false };
  const v0 = JSON.stringify(f(arr[0]));
  const mixed = arr.some(x => JSON.stringify(f(x)) !== v0);
  return { v: mixed ? undefined : f(arr[0]), mixed };
};
const relHas = (mm, tok) => (mm.releases || "").split(",").map(s => s.trim()).filter(Boolean).includes(tok);
function setRel(mm, tok, on) {
  const toks = (mm.releases || "").split(",").map(s => s.trim()).filter(Boolean);
  const has = toks.includes(tok);
  if (on && !has) toks.push(tok);
  if (!on && has) toks.splice(toks.indexOf(tok), 1);
  mm.releases = toks.join(",");
}

function emptySelection(sky, id, title, what, iconId) {
  return dkDialog({
    id, title, iconId, size: "sm", noCancel: true,
    body: [dkNote(`Nothing to assign — select one or more <b>${esc(what)}</b> first (Select tool <kbd>V</kbd>: click, or drag a box), then choose this command again.`),
      el("div", { class: "dk-inline" }, [el("button", { type: "button", class: "btn btn-small", id: id + "SelAll", onclick: () => {
        sky.selectx && sky.selectx.all ? sky.selectx.all() : null; document.getElementById(id + "Cancel") && document.getElementById(id + "Cancel").click();
      } }, ["Select All"])])],
  });
}

/* ================================================================
   Frame assignments
   ================================================================ */
const TAB_OF = { section: "section", releases: "releases", axes: "axes", offsets: "offsets", nonlinear: "nonlinear" };

export function openAssignFrame(sky, { tab = "section" } = {}) {
  const refs = sel(sky).filter(r => r.type === "member");
  if (!refs.length) return emptySelection(sky, "daFrame", "Assign Frame Properties", "frame objects (columns, beams, braces)", "exleaf-frames");
  const S = sky.store;
  const live = () => refs.map(r => S.model.members.find(m => m.uid === r.uid)).filter(Boolean);
  const mems = live();
  const F = dkForm();
  const P = {};                        // pending values (only touched keys)
  const put = k => v => { P[k] = v; };
  const c = f => common(mems, f);
  const field = (k, f, d) => { const r = c(f); return { val: r.mixed ? d : (r.v ?? d), mixed: r.mixed }; };
  const vals = {
    section: field("section", m => m.section, ""),
    relMi: field("relMi", m => relHas(m, "Mi"), false), relMj: field("relMj", m => relHas(m, "Mj"), false),
    angle: field("angle", m => m.angle ?? 0, 0),
    rigid_i: field("rigid_i", m => m.rigid_i ?? 0, 0), rigid_j: field("rigid_j", m => m.rigid_j ?? 0, 0), rigid_factor: field("rigid_factor", m => m.rigid_factor ?? 1, 1),
    axial_limit: field("axial_limit", m => daME.axialLimit(m), "both"), hinges: field("hinges", m => m.hinges || "none", "none"),
    foundation_ks: field("foundation_ks", m => m.foundation_ks ?? 0, 0), foundation_width: field("foundation_width", m => m.foundation_width ?? 0, 0),
  };
  const V = Object.fromEntries(Object.entries(vals).map(([k, x]) => [k, x.val]));
  const mx = k => vals[k].mixed;
  const kinds = ["column", "beam", "brace"].map(k => [k, mems.filter(m => m.kind === k).length]).filter(([, n]) => n).map(([k, n]) => `${n} ${k}${n > 1 ? "s" : ""}`).join(" · ");
  const secOpts = () => Object.keys(S.model.sections || {}).map(n => [n, n]);
  const tabs = dkTabs([
    { id: "section", label: "Section", body: daSpvSplit(dkGroup("Frame Section", [
      F.select(V, "section", secOpts, { label: "Section Property", id: "daFrameSec", mixed: mx("section"), write: put("section") }),
      dkNote("Define › Frame Sections to add new sizes."),
    ]), daSpvFrame({ model: () => S.model, name: () => P.section ?? V.section, section: () => (S.model.sections || {})[P.section ?? V.section] || null,
      emptyTitle: "Mixed sections", emptyText: "The selected objects use different sections — pick one to preview it." })) },
    { id: "releases", label: "Releases", body: dkGroup("Moment Releases (M2 and M3)", [
      F.check(V, "relMi", { label: "Release moments at the start (i-end)", id: "daFrameRelI", mixed: mx("relMi"), write: put("relMi") }),
      F.check(V, "relMj", { label: "Release moments at the end (j-end)", id: "daFrameRelJ", mixed: mx("relMj"), write: put("relMj") }),
    ], { note: "A released end acts as a pin for bending; releasing both ends of a beam makes it simply supported." }) },
    { id: "axes", label: "Local Axes", body: dkGroup("Local Axis Orientation", [
      F.num(V, "angle", { label: "Rotation angle about local 1", unit: "deg", id: "daFrameAngle", mixed: mx("angle"), write: put("angle"), def: 0,
        hint: "rotates the section's local 2–3 axes (strong axis) about the member axis" }),
    ]) },
    { id: "offsets", label: "End Offsets", body: dkGroup("Rigid End Offsets", [
      F.num(V, "rigid_i", { label: "Length at i-end", kind: "length", min: 0, id: "daFrameRi", mixed: mx("rigid_i"), write: put("rigid_i"), def: 0 }),
      F.num(V, "rigid_j", { label: "Length at j-end", kind: "length", min: 0, id: "daFrameRj", mixed: mx("rigid_j"), write: put("rigid_j"), def: 0 }),
      F.num(V, "rigid_factor", { label: "Rigid-zone factor", min: 0, max: 1, id: "daFrameRf", mixed: mx("rigid_factor"), write: put("rigid_factor"), def: 1,
        hint: "0 = no extra stiffness · 1 = fully rigid" }),
    ], { note: "Stiff zones where beams frame into columns. The offsets must leave a positive clear span." }) },
    { id: "nonlinear", label: "Nonlinear", body: el("div", {}, [
      dkGroup("Axial Behaviour", [
        F.select(V, "axial_limit", [["both", "Tension and compression"], ["tension", "Tension only"], ["compression", "Compression only"]],
          { label: "Axial limit", id: "daFrameAxial", mixed: mx("axial_limit"), write: put("axial_limit"), hint: "Tension/compression-only members make the analysis nonlinear." }),
      ]),
      dkGroup("Plastic Hinges", [
        F.select(V, "hinges", [["none", "None (elastic)"], ["auto_m3", "Auto M3 (ASCE 41-17)"], ["fiber_pmm", "Fiber PMM"]],
          { label: "Hinge type", id: "daFrameHinges", mixed: mx("hinges"), write: put("hinges"), hint: "Used by pushover cases with ASCE 41 hinges." }),
      ]),
      dkAdvanced([
        F.num(V, "foundation_ks", { label: "Subgrade modulus ks", kind: "subgrade", min: 0, id: "daFrameKs", mixed: mx("foundation_ks"), write: put("foundation_ks"), def: 0 }),
        F.num(V, "foundation_width", { label: "Bearing width", kind: "length", min: 0, id: "daFrameKw", mixed: mx("foundation_width"), write: put("foundation_width"), def: 0 }),
      ], { summary: "Advanced — elastic (Winkler) foundation", note: "Grade beams on soil: active when both values are greater than zero.",
        countFn: () => mems.filter(m => (m.foundation_ks ?? 0) > 0).length }),
    ]) },
  ], { active: TAB_OF[tab] || "section", id: "daFrameTabs" });
  const apply = () => {
    const keys = Object.keys(P);
    if (!keys.length) return;
    const D = dkDraft(sky);
    const uids = new Set(refs.map(r => r.uid));
    let bad = "";
    for (const mm of D.members) {
      if (!uids.has(mm.uid)) continue;
      for (const k of keys) {
        if (k === "relMi") setRel(mm, "Mi", P[k]);
        else if (k === "relMj") setRel(mm, "Mj", P[k]);
        else mm[k] = P[k];
      }
      const L = Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1], mm.pj[2] - mm.pi[2]);
      if ((keys.includes("rigid_i") || keys.includes("rigid_j") || keys.includes("rigid_factor")) &&
        (mm.rigid_factor ?? 1) * ((mm.rigid_i || 0) + (mm.rigid_j || 0)) >= L) bad = `The end offsets of ${mm.uid} leave no clear span (length ${daU.fmtU ? daU.fmtU("length", L, 2) : L}).`;
    }
    if (bad) return bad;
    dkCommit(sky, D);
    return "";
  };
  return dkDialog({
    id: "daFrame", title: "Assign Frame Properties", iconId: "exleaf-frames", size: "md", form: F, tabs, body: tabs,
    intro: `${refs.length} frame object${refs.length > 1 ? "s" : ""} selected (${esc(kinds)}). Only the values you change are assigned.`,
    onOk: () => { const m = apply(); if (m) return m; },
    onApply: () => apply(),
  });
}

/* ================================================================
   Shell assignments
   ================================================================ */
export function openAssignShell(sky) {
  const refs = sel(sky).filter(r => r.type === "shell");
  if (!refs.length) return emptySelection(sky, "daShell", "Assign Shell Properties", "walls or slabs", "exleaf-shells");
  const S = sky.store;
  const sh = refs.map(r => S.model.shells.find(s => s.uid === r.uid)).filter(Boolean);
  const F = dkForm();
  const P = {};
  const put = k => v => { P[k] = v; };
  const cm = (f, d) => { const r = common(sh, f); return { val: r.mixed ? d : (r.v ?? d), mixed: r.mixed }; };
  const vals = { section: cm(s => s.section, ""), behavior: cm(s => s.behavior || "shell", "shell"), mesh_size: cm(s => s.mesh_size ?? 1, 1),
    wind_cp: cm(s => s.wind_cp ?? null, null), diaphragm: cm(s => s.diaphragm || "", "") };
  const V = Object.fromEntries(Object.entries(vals).map(([k, x]) => [k, x.val]));
  const walls = sh.filter(s => s.kind === "wall").length;
  const dias = Object.keys(S.model.diaphragms || {});
  const body = [
    dkGroup("Section", [
      F.select(V, "section", () => Object.keys(S.model.shell_sections || {}).map(n => [n, n]), { label: "Slab / wall section", id: "daShellSec", mixed: vals.section.mixed, write: put("section") }),
    ]),
    dkGroup("Meshing and Behaviour", [
      F.select(V, "behavior", walls ? [["shell", "Shell (finite elements)"]] : [["shell", "Shell (finite elements)"], ["membrane", "Membrane (load to edge beams)"]],
        { label: "Behaviour", id: "daShellBeh", mixed: vals.behavior.mixed, write: put("behavior") }),
      F.num(V, "mesh_size", { label: "Maximum mesh size", kind: "length", gt: 0, id: "daShellMesh", mixed: vals.mesh_size.mixed, write: put("mesh_size") }),
    ]),
    dkAdvanced([
      F.num(V, "wind_cp", { label: "Wind pressure coefficient Cp", allowNull: true, nullText: "none", def: null, id: "daShellCp", mixed: vals.wind_cp.mixed, write: put("wind_cp") }),
      dias.length ? F.select(V, "diaphragm", [["", "None"], ...dias.map(d => [d, d])], { label: "Diaphragm", id: "daShellDia", mixed: vals.diaphragm.mixed, write: put("diaphragm") }) : null,
    ], { summary: "Advanced — wind Cp" + (dias.length ? " and diaphragm" : ""), countFn: () => sh.filter(s => s.wind_cp != null).length }),
  ];
  const apply = () => {
    const keys = Object.keys(P);
    if (!keys.length) return "";
    const D = dkDraft(sky);
    const uids = new Set(refs.map(r => r.uid));
    for (const s of D.shells) {
      if (!uids.has(s.uid)) continue;
      for (const k of keys) {
        if (k === "diaphragm" && !P[k]) delete s.diaphragm;
        else s[k] = P[k];
      }
    }
    dkCommit(sky, D);
    return "";
  };
  return dkDialog({
    id: "daShell", title: "Assign Shell Properties", iconId: "exleaf-shells", size: "md", form: F, body,
    intro: `${refs.length} shell object${refs.length > 1 ? "s" : ""} selected. Only the values you change are assigned.`,
    onOk: () => { const m = apply(); if (m) return m; },
    onApply: () => apply(),
  });
}

/* ================================================================
   Joint restraints (supports)
   ================================================================ */
const PRESETS = [["fixed", "Fixed", [1, 1, 1, 1, 1, 1]], ["pinned", "Pinned", [1, 1, 1, 0, 0, 0]], ["roller", "Roller (vertical only)", [0, 0, 1, 0, 0, 0]], ["free", "Free (remove support)", [0, 0, 0, 0, 0, 0]]];
const DOFS = ["UX", "UY", "UZ", "RX", "RY", "RZ"];
const same3 = (a, b) => Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6 && Math.abs(a[2] - b[2]) < 1e-6;

export function openAssignSupports(sky) {
  const S = sky.store;
  const m = S.model;
  const all = daSelectionJoints(m, sel(sky));
  const zMin = all.length ? Math.min(...all.map(p => p[2])) : 0;
  const st = { scope: "base", preset: "fixed" };
  const r = [1, 1, 1, 1, 1, 1];
  const F = dkForm();
  const P = {};
  const target = () => st.scope === "base" ? all.filter(p => Math.abs(p[2] - zMin) < 1e-6) : all;
  const cnt = el("span", { class: "muted dk-note", id: "daSupCount" });
  const syncCnt = () => { cnt.textContent = all.length ? `${target().length} joint${target().length === 1 ? "" : "s"} will be assigned.` : ""; };
  const dofBox = el("div", { class: "dk-group dk-cols", style: "border:0;padding:0" });
  const drawDofs = () => {
    F.forget(dofBox); dofBox.textContent = "";
    DOFS.forEach((d, i) => dofBox.appendChild(F.check(null, d, { label: d, id: "daSup_" + d, read: () => !!r[i], write: v => { r[i] = v ? 1 : 0; P.r = true; } })));
  };
  const jointPart = all.length ? [
    dkGroup("Restraints for the selected joints", [
      F.radio(st, "scope", [["base", "Lowest joints of the selection", "e.g. column bases"], ["all", "Every joint of the selection"]], { label: "Apply to", idPrefix: "daSupScope_", onChange: syncCnt }),
      F.radio(st, "preset", PRESETS.map(([k, l]) => [k, l]), { label: "Fast restraints", idPrefix: "daSupPreset_",
        onChange: v => { const p = PRESETS.find(x => x[0] === v); p[2].forEach((x, i) => { r[i] = x; }); P.r = true; drawDofs(); } }),
      dofBox, cnt,
    ], { note: "Explicit supports replace the automatic base fixity for the whole model — assign every support you need." }),
  ] : [dkNote("No joints selected — select columns, braces or walls to restrain their joints, or change the automatic base supports below.")];
  drawDofs();
  syncCnt();
  const base = { v: m.base_fixity || "fixed" };
  const explicit = (m.supports || []).length;
  const body = [
    ...jointPart,
    dkGroup("Automatic base supports", [
      F.select(base, "v", [["fixed", "Fixed"], ["pinned", "Pinned"]], { label: "Joints at the base level", id: "daSupBase", write: v => { base.v = v; P.base = true; },
        hint: explicit ? `<b>Inactive</b> — the model has ${explicit} explicit support${explicit > 1 ? "s" : ""}.` : "Used while no explicit supports are assigned." }),
    ]),
  ];
  const apply = () => {
    if (!P.r && !P.base) return "";
    const D = dkDraft(sky);
    if (P.base) D.base_fixity = base.v;
    if (P.r && all.length) {
      D.supports = (D.supports || []).filter(s => !target().some(p => same3(p, s.point)));
      if (r.some(Boolean)) for (const p of target()) D.supports.push({ point: [p[0], p[1], p[2]], restraints: r.slice() });
    }
    dkCommit(sky, D);
    return "";
  };
  return dkDialog({
    id: "daSupports", title: "Assign Joint Restraints", iconId: "exleaf-supports", size: "md", form: F, body,
    intro: all.length ? `${all.length} joint${all.length > 1 ? "s" : ""} in the selection.` : null,
    onOk: () => { const msg = apply(); if (msg) return msg; },
    onApply: all.length ? () => apply() : null,
  });
}

/* ================================================================
   Object information (read-only)
   ================================================================ */
export function openObjectInfo(sky, ref) {
  const S = sky.store, m = S.model;
  ref = ref || sel(sky)[0];
  if (!ref) return null;
  const rows = [];
  let title = "Object Information";
  const loadsOn = uid => Object.values(m.patterns || {}).map(p => {
    const n = ["member_loads", "member_udls", "area_loads", "thermal_loads"].reduce((a, k) => a + (p[k] || []).filter(l => l.member_uid === uid || l.region_uid === uid || l.uid === uid).length, 0);
    return n ? `${p.name} (${n})` : null;
  }).filter(Boolean).join(", ") || "none";
  if (ref.type === "member") {
    const mm = m.members.find(x => x.uid === ref.uid);
    if (!mm) return null;
    const s = (m.sections || {})[mm.section] || {};
    const L = Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1], mm.pj[2] - mm.pi[2]);
    title = `Frame Information — ${mm.uid}`;
    rows.push(["Object", `${mm.uid} (${mm.kind})`], ["Story", mm.story], ["Section", mm.section], ["Material", s.material || "—"],
      ["Length", daU.fmt("length", L, 3), daU.label("length")],
      ["Start (i)", mm.pi.map(v => daU.fmt("length", v, 3)).join(", "), daU.label("length")],
      ["End (j)", mm.pj.map(v => daU.fmt("length", v, 3)).join(", "), daU.label("length")],
      ["Releases", mm.releases || "none"], ["Local axis angle", String(mm.angle ?? 0), "deg"],
      ["Rigid offsets i / j", `${daU.fmt("length", mm.rigid_i ?? 0, 3)} / ${daU.fmt("length", mm.rigid_j ?? 0, 3)}`, daU.label("length")],
      ["Axial limit", daME.axialLimit(mm)], ["Hinges", mm.hinges || "none"], ["Loads", loadsOn(mm.uid)]);
    const res = S.results && S.results.members && S.results.members[mm.uid];
    if (res) rows.push(["Results", "available — Display › Member Forces"]);
  } else if (ref.type === "shell") {
    const s = m.shells.find(x => x.uid === ref.uid);
    if (!s) return null;
    const sec = (m.shell_sections || {})[s.section] || {};
    title = `Shell Information — ${s.uid}`;
    rows.push(["Object", `${s.uid} (${s.kind})`], ["Story", s.story], ["Section", s.section],
      ["Thickness", daU.fmt("dim", sec.thickness, 3), daU.label("dim")], ["Material", sec.material || "—"],
      ["Corners", String((s.corners || []).length)], ["Openings", String((s.openings || []).length)],
      ["Behaviour", s.behavior || "shell"], ["Mesh size", daU.fmt("length", s.mesh_size ?? 1, 3), daU.label("length")],
      ["Diaphragm", s.diaphragm || "—"], ["Loads", loadsOn(s.uid)]);
  } else if (ref.type === "link") {
    const l = (m.links || []).find(x => x.uid === ref.uid);
    if (!l) return null;
    title = `Link Information — ${l.uid}`;
    rows.push(["Object", l.uid], ["Type", l.link_type || "linear"], ["i", l.pi.map(v => daU.fmt("length", v, 3)).join(", "), daU.label("length")],
      ["j", l.pj.map(v => daU.fmt("length", v, 3)).join(", "), daU.label("length")]);
  } else if (ref.type === "spring") {
    const s = daME.springByKey(m, ref.uid);
    if (!s) return null;
    title = "Point Spring Information";
    rows.push(["Location", s.point.map(v => daU.fmt("length", v, 3)).join(", "), daU.label("length")],
      ["kx, ky, kz", s.stiffness.slice(0, 3).map(v => daU.fmt("stiffness", v, 0)).join(", "), daU.label("stiffness")]);
  } else return null;
  return dkDialog({
    id: "daInfo", title, iconId: "exleaf-frames", size: "sm", noCancel: true,
    body: dkInfo(rows),
  });
}
