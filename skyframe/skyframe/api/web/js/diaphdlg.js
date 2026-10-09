/* SkyFrame — ETABS multiple diaphragms per story + additional mass (analysis
   only). CONTRACT "Multiple diaphragms per story and additional mass".

     Define → Diaphragms…                  model.diaphragms {name: {type}}
     Assign → Joint · Diaphragm…           model.joint_diaphragms [{point, diaphragm}]
     Assign → Shell · Diaphragm…           ShellRegion.diaphragm
     Assign → Joint · Additional Mass…     NodalMass mx/my/mz + mrx/mry/mrz
     Assign → Frame · Additional Mass…     FrameMember.additional_mass (+ _mode)
     Assign → Shell · Additional Mass…     ShellRegion.additional_mass
     Story tab card                        results.diaphragms (per-diaphragm
                                           CM / CR, displacements, drifts,
                                           torsion ratio)

   Every key is written ONLY when it differs from the backend default and
   removed otherwise (the backend emits them only when used), so a model
   that does not use the feature — or a dialog closed with OK and no edit —
   round-trips byte-identically through POST /api/model. Numbers go through
   js/units.js (mass, mass_per_length, mass_per_area, mass_moi); the model
   stays SI (t, t/m, t/m², t·m²). Joints are taken from the current
   selection (member ends, shell corners, springs, links) or added by
   coordinates, as in Assign → Joint Loads. */

import U from "./units.js";
import { selectionJoints as dpSelectionJoints } from "./assigndlg.js";
import {
  esc, clone, isNum, dialog, closeDialog, btn, footBar, errorLine, showError,
  group, el, numInput, row, select, radio, samePt, storyAtZ, ptLabel, makeCtx,
  ensureCss, svgIcon,
} from "./nls_ui.js";

export const DIAPHRAGM_TYPES = [["rigid", "Rigid"], ["semi_rigid", "Semi-rigid"]];
const TYPE_LABEL = Object.fromEntries(DIAPHRAGM_TYPES);
const EPS = 1e-6;

const dias = m => (m && m.diaphragms) || {};
const isHorizontal = sh => {
  const zs = (sh.corners || []).map(c => c[2]);
  return zs.length > 0 && Math.max(...zs) - Math.min(...zs) <= EPS;
};
const atStory = (m, p) => !!storyAtZ(m, p[2]);

/** {name: count} of joint + shell assignments. */
export function diaphragmUse(m) {
  const out = {};
  for (const e of m.joint_diaphragms || []) out[e.diaphragm] = (out[e.diaphragm] || 0) + 1;
  for (const s of m.shells || []) if (s.diaphragm) out[s.diaphragm] = (out[s.diaphragm] || 0) + 1;
  return out;
}

/** Client mirror of validate_diaphragms (mock + pre-check). "" = ok. */
export function validateDiaphragms(m) {
  const d = dias(m);
  for (const [n, v] of Object.entries(d)) {
    if (!n) return "Diaphragm names must be non-empty.";
    if (!v || !TYPE_LABEL[v.type]) return `Diaphragm ${n}: type must be rigid or semi_rigid.`;
  }
  const seen = new Map();
  for (const e of m.joint_diaphragms || []) {
    if (!d[e.diaphragm]) return `Joint diaphragm: unknown diaphragm ${e.diaphragm}.`;
    if (!Array.isArray(e.point) || e.point.length !== 3) return "Joint diaphragm point must be [x, y, z].";
    if (!atStory(m, e.point)) return `Joint ${ptLabel(e.point)} is not at a story elevation.`;
    const k = e.point.map(v => (+v).toFixed(6)).join(",");
    if (seen.has(k) && seen.get(k) !== e.diaphragm) return `Joint ${ptLabel(e.point)} assigned to two diaphragms.`;
    seen.set(k, e.diaphragm);
  }
  for (const s of m.shells || []) {
    if (s.diaphragm) {
      if (!d[s.diaphragm]) return `Shell ${s.uid}: unknown diaphragm ${s.diaphragm}.`;
      if (!isHorizontal(s)) return `Shell ${s.uid}: only horizontal regions (slabs) can be assigned to a diaphragm.`;
    }
    if (s.additional_mass != null && !(isNum(s.additional_mass) && s.additional_mass >= 0)) return `Shell ${s.uid}: additional mass must be ≥ 0.`;
  }
  for (const mm of m.members || []) {
    if (mm.additional_mass != null && !(isNum(mm.additional_mass) && mm.additional_mass >= 0)) return `Member ${mm.uid}: additional mass must be ≥ 0.`;
    if (mm.additional_mass_mode != null && !["lumped", "distributed"].includes(mm.additional_mass_mode)) return `Member ${mm.uid}: bad additional mass mode.`;
  }
  for (const nm of m.nodal_masses || [])
    for (const k of ["mx", "my", "mz", "mrx", "mry", "mrz"])
      if (nm[k] != null && !(isNum(nm[k]) && nm[k] >= 0)) return `Nodal mass ${k} must be ≥ 0.`;
  return "";
}

/* ================================================================
   Define → Diaphragms…
   ================================================================ */
export function openDiaphragms(ctx, opts = {}) {
  const m = ctx.store.model;
  if (!m) return null;
  const rows = Object.entries(dias(m)).map(([n, v]) => ({ orig: n, name: n, type: v.type || "rigid" }));
  const use = diaphragmUse(m);
  const body = el("div", "dp-def");
  const err = errorLine("dpDefError");
  const draw = () => {
    body.textContent = "";
    body.appendChild(el("p", "muted dlg-intro",
      "Named diaphragms (ETABS Define → Diaphragms). Assign them to joints (Assign → Joint · Diaphragm) or to slabs " +
      "(Assign → Shell · Diaphragm); a story with assignments gets one master per <b>rigid</b> diaphragm, " +
      "<b>semi-rigid</b> diaphragms add no constraint (the slab carries the in-plane stiffness). Unassigned stories keep the model-wide option."));
    const g = group("Diaphragms");
    const tbl = el("table", "data-table dp-table");
    tbl.id = "dpTable";
    tbl.innerHTML = `<thead><tr><th class="txt">Name</th><th class="txt">Rigidity</th><th>Assigned objects</th><th></th></tr></thead>`;
    const tb = document.createElement("tbody");
    rows.forEach((r, i) => {
      const tr = document.createElement("tr");
      tr.dataset.dp = r.name;
      const nameIn = document.createElement("input");
      nameIn.type = "text"; nameIn.value = r.name; nameIn.id = `dpName${i}`; nameIn.spellcheck = false;
      nameIn.addEventListener("change", () => { r.name = nameIn.value.trim(); showError(err, ""); });
      const td1 = document.createElement("td"); td1.className = "txt"; td1.appendChild(nameIn);
      const td2 = document.createElement("td"); td2.className = "txt";
      td2.appendChild(select(DIAPHRAGM_TYPES, r.type, v => { r.type = v; }, `dpType${i}`));
      const n = r.orig ? (use[r.orig] || 0) : 0;
      const td3 = el("td", "", n ? String(n) : `<span class="dim">0</span>`);
      const td4 = document.createElement("td");
      const x = el("button", "chip-x"); x.textContent = "✕"; x.id = `dpDel${i}`;
      x.title = n ? `Assigned to ${n} object(s) — unassign first` : "Delete diaphragm";
      x.addEventListener("click", () => {
        if (n) { showError(err, `Can't delete ${r.orig}: assigned to ${n} joint(s) / shell(s). Assign "None" first.`); return; }
        rows.splice(i, 1); draw();
      });
      td4.appendChild(x);
      tr.append(td1, td2, td3, td4);
      tb.appendChild(tr);
    });
    if (!rows.length) tb.innerHTML = `<tr><td class="txt muted" colspan="4">No named diaphragms — every story uses the model-wide diaphragm option.</td></tr>`;
    tbl.appendChild(tb);
    g.appendChild(el("div", "table-scroll", "")).appendChild(tbl);
    g.appendChild(btn("+ Add New Diaphragm", "btn-small cd-add", () => {
      const taken = new Set(rows.map(r => r.name));
      let k = 1;
      while (taken.has(`D${k}`)) k++;
      rows.push({ orig: null, name: `D${k}`, type: "rigid" });
      draw();
    }, "Add a named diaphragm", "dpAdd"));
    body.appendChild(g);
    body.appendChild(err);
  };
  const commit = () => {
    const names = rows.map(r => r.name);
    if (names.some(n => !n)) { showError(err, "Every diaphragm needs a name."); return false; }
    if (new Set(names).size !== names.length) { showError(err, "Diaphragm names must be unique."); return false; }
    const next = {};
    for (const r of rows) next[r.name] = { type: r.type };
    const prev = dias(m);
    const renames = rows.filter(r => r.orig && r.orig !== r.name);
    const same = !renames.length && JSON.stringify(Object.keys(prev).sort()) === JSON.stringify(Object.keys(next).sort()) &&
      Object.keys(next).every(k => prev[k] && prev[k].type === next[k].type);
    if (same) { opts.onChange && opts.onChange(); return true; }
    const ren = Object.fromEntries(renames.map(r => [r.orig, r.name]));
    for (const e of m.joint_diaphragms || []) if (ren[e.diaphragm]) e.diaphragm = ren[e.diaphragm];
    for (const s of m.shells || []) if (s.diaphragm && ren[s.diaphragm]) s.diaphragm = ren[s.diaphragm];
    if (Object.keys(next).length) m.diaphragms = next;
    else delete m.diaphragms;
    ctx.markDirty();
    ctx.onChange && ctx.onChange("diaphragms");
    opts.onChange && opts.onChange();
    return true;
  };
  const fb = footBar("Define → Diaphragms", [
    btn("Cancel", "", () => dlg.close(), "", "dpCancel"),
    btn("OK", "btn-run", () => { if (commit()) dlg.close(); }, "", "dpOk"),
  ]);
  const dlg = dialog("dpDefModal", { title: "Diaphragms", glyph: "diaph", body, foot: fb.wrap });
  draw();
  return dlg;
}

/* ================================================================
   joint list widget (selection joints + add by coordinates)
   ================================================================ */
function jointList(m, joints, { filterStory = false, current = () => "", onChange = () => {} } = {}) {
  const wrap = el("div", "dp-joints");
  const list = el("div", "asn-jlist dp-jlist");
  list.id = "dpJoints";
  const add = el("div", "cd-inline dp-jadd");
  const xyz = [null, null, null];
  const ins = ["X", "Y", "Z"].map((ax, k) => numInput("length", null, { id: "dpAdd" + ax, placeholder: ax, onSet: v => { xyz[k] = v; } }));
  const addBtn = btn("+ Joint", "btn-small", () => {
    if (!xyz.every(isNum)) return;
    if (!joints.some(j => samePt(j.p, xyz))) joints.push({ p: xyz.slice(), on: true });
    render(); onChange();
  }, "Add a joint by its coordinates", "dpAddJoint");
  add.append(el("span", "cd-unit", esc(U.label("length"))), ...ins, addBtn,
    btn("Clear", "btn-small", () => { joints.length = 0; render(); onChange(); }, "", "dpClearJoints"));
  const render = () => {
    list.textContent = "";
    if (!joints.length) list.appendChild(el("p", "muted asn-empty",
      "No joints — select members / shells (their end and corner joints are listed) or add by coordinates."));
    joints.forEach((j, i) => {
      const bad = filterStory && !atStory(m, j.p);
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.checked = j.on && !bad; cb.disabled = bad;
      cb.addEventListener("change", () => { j.on = cb.checked; onChange(); });
      const lab = el("label", "dlg-chk");
      lab.append(cb, el("span", "asn-pt", esc(`${storyAtZ(m, j.p[2]) || "—"} · ${ptLabel(j.p)}`)));
      const cur = current(j.p);
      const r = el("div", "asn-jrow" + (bad ? " is-bad" : ""));
      r.append(lab);
      if (bad) r.appendChild(el("span", "asn-tag bad", "not at a story level"));
      r.appendChild(el("span", "asn-cur muted", cur ? esc(cur) : ""));
      const x = el("button", "chip-x"); x.textContent = "✕"; x.title = "Remove from list";
      x.addEventListener("click", () => { joints.splice(i, 1); render(); onChange(); });
      r.appendChild(x);
      list.appendChild(r);
    });
  };
  wrap.append(list, add);
  render();
  return { el: wrap, render };
}
const selJoints = (sky, m) => dpSelectionJoints(m, sky.store.selection || []).map(p => ({ p: p.slice(), on: true }));
const selObjs = sky => (sky.selObjects ? sky.selObjects() : { members: [], shells: [] });

/* ================================================================
   Assign → Joint · Diaphragm…  /  Assign → Shell · Diaphragm…
   ================================================================ */
export function openAssignDiaphragm(ctx, sky, target = "shell") {
  const m = ctx.store.model;
  if (!m) return null;
  const joints = target === "joint" ? selJoints(sky, m) : [];
  const shells = target === "shell" ? selObjs(sky).shells : [];
  const cur0 = target === "shell"
    ? (shells.length && shells.every(s => (s.diaphragm || "") === (shells[0].diaphragm || "")) ? (shells[0].diaphragm || "") : null)
    : null;
  let choice = cur0 ?? "";
  const body = el("div", "dp-asn");
  const err = errorLine("dpAsnError");
  const jointDia = p => ((m.joint_diaphragms || []).find(e => samePt(e.point, p)) || {}).diaphragm || "";
  let jl = null;
  const draw = () => {
    body.textContent = "";
    const names = Object.keys(dias(m));
    const g0 = group("Diaphragm");
    const r0 = el("div", "cd-inline");
    r0.append(select([["", "None (unassign)"], ...names.map(n => [n, `${n} · ${TYPE_LABEL[dias(m)[n].type] || ""}`])], choice, v => { choice = v; }, "dpAsnChoice"),
      btn("Define…", "btn-small", () => openDiaphragms(ctx, { onChange: draw }), "Define → Diaphragms…", "dpAsnDefine"));
    g0.appendChild(r0);
    if (!names.length) g0.appendChild(el("p", "muted cd-note", "No named diaphragms yet — click <b>Define…</b>."));
    body.appendChild(g0);
    if (target === "joint") {
      const g1 = group("Joints");
      jl = jointList(m, joints, { filterStory: true, current: p => { const d = jointDia(p); return d ? `current: ${d}` : ""; }, onChange: syncNote });
      g1.appendChild(jl.el);
      g1.appendChild(el("p", "muted cd-note", "A joint assignment wins over a slab assignment; only joints at a story elevation can be assigned."));
      body.appendChild(g1);
    } else {
      const g1 = group("Shells");
      const list = el("div", "asn-jlist dp-slist"); list.id = "dpShells";
      if (!shells.length) list.appendChild(el("p", "muted asn-empty", "No shells selected — select slab regions first."));
      for (const s of shells) {
        const ok = isHorizontal(s);
        list.appendChild(el("div", "asn-jrow" + (ok ? "" : " is-bad"),
          `<span class="asn-pt">${esc(s.uid)}</span> <span class="dim">${esc(s.kind || "")} · ${esc(s.story || "")}</span>` +
          (ok ? "" : ` <span class="asn-tag bad">not horizontal — skipped</span>`) +
          `<span class="asn-cur muted">${s.diaphragm ? "current: " + esc(s.diaphragm) : ""}</span>`));
      }
      g1.appendChild(list);
      g1.appendChild(el("p", "muted cd-note", "The slab captures every structural node inside or on its polygon at that elevation."));
      body.appendChild(g1);
    }
    body.appendChild(err);
    syncNote();
  };
  function syncNote() {
    if (!fb) return;
    fb.note.textContent = target === "joint" ? `${joints.filter(j => j.on && atStory(m, j.p)).length} joint(s)`
      : `${shells.filter(isHorizontal).length} slab(s)`;
  }
  const commit = () => {
    if (choice && !dias(m)[choice]) { showError(err, "Pick a defined diaphragm."); return false; }
    let changed = false;
    if (target === "joint") {
      const pts = joints.filter(j => j.on && atStory(m, j.p)).map(j => j.p);
      if (!pts.length) { showError(err, "No joint at a story level selected."); return false; }
      const before = JSON.stringify(m.joint_diaphragms || []);
      let list = (m.joint_diaphragms || []).filter(e => !pts.some(p => samePt(p, e.point)));
      if (choice) list = list.concat(pts.map(p => ({ point: p.map(Number), diaphragm: choice })));
      // keep untouched entries in place when the assignment did not change
      const old = m.joint_diaphragms || [];
      if (pts.every(p => (old.find(e => samePt(e.point, p)) || {}).diaphragm === (choice || undefined))) list = old;
      if (JSON.stringify(list) !== before) {
        if (list.length) m.joint_diaphragms = list; else delete m.joint_diaphragms;
        changed = true;
      }
    } else {
      const hs = shells.filter(isHorizontal);
      if (!hs.length) { showError(err, "No horizontal shell selected."); return false; }
      for (const s of hs) {
        if ((s.diaphragm || "") === choice) continue;
        if (choice) s.diaphragm = choice; else delete s.diaphragm;
        changed = true;
      }
    }
    if (changed) { ctx.markDirty(); ctx.onChange && ctx.onChange("diaphragms"); }
    return true;
  };
  const fb = footBar("", [
    btn("Cancel", "", () => dlg.close(), "", "dpAsnCancel"),
    btn("OK", "btn-run", () => { if (commit()) dlg.close(); }, "", "dpAsnOk"),
  ]);
  const dlg = dialog("dpAsnModal", { title: target === "joint" ? "Assign Joint Diaphragm" : "Assign Shell Diaphragm", glyph: "diaph", body, foot: fb.wrap, onUnits: draw });
  draw();
  return dlg;
}

/* ================================================================
   Assign → Joint / Frame / Shell · Additional Mass…
   ================================================================ */
const JM = [["mx", "Mass X", "mass"], ["my", "Mass Y", "mass"], ["mz", "Mass Z", "mass"],
  ["mrx", "Mass moment of inertia about X", "mass_moi"], ["mry", "Mass moment of inertia about Y", "mass_moi"],
  ["mrz", "Mass moment of inertia about Z", "mass_moi"]];
const nmText = e => JM.filter(([k]) => e[k]).map(([k, , kind]) => `${k} ${U.fmt(kind, e[k], 3)}`).join(", ") || "0";
/** NodalMass dict in the backend to_dict shape (mr* only when non-zero). */
function nodalMassDict(p, v) {
  const o = { point: p.map(Number), mx: v.mx || 0, my: v.my || 0, mz: v.mz || 0 };
  for (const k of ["mrx", "mry", "mrz"]) if (v[k]) o[k] = v[k];
  return o;
}

export function openAdditionalMass(ctx, sky, target = "joint") {
  const m = ctx.store.model;
  if (!m) return null;
  let mode = "add";
  const joints = target === "joint" ? selJoints(sky, m) : [];
  const members = target === "frame" ? selObjs(sky).members : [];
  const shells = target === "shell" ? selObjs(sky).shells : [];
  const vals = { mx: 0, my: 0, mz: 0, mrx: 0, mry: 0, mrz: 0 };
  const common = (arr, f) => arr.length && arr.every(x => f(x) === f(arr[0])) ? f(arr[0]) : null;
  let q = target === "frame" ? (common(members, x => x.additional_mass || 0) ?? 0) : target === "shell" ? (common(shells, x => x.additional_mass || 0) ?? 0) : 0;
  let fmode = target === "frame" ? (common(members, x => x.additional_mass_mode || "lumped") ?? "lumped") : "lumped";
  if (target !== "joint") mode = "replace";
  const body = el("div", "dp-mass");
  const err = errorLine("amError");
  const draw = () => {
    body.textContent = "";
    body.appendChild(el("p", "muted dlg-intro",
      "Additional mass goes straight into the mass matrix (never through load patterns); Define → Mass Source include-lateral / " +
      "include-vertical / lump-at-stories options apply."));
    if (target === "joint") {
      const g1 = group("Joints");
      const jl = jointList(m, joints, { current: p => { const e = (m.nodal_masses || []).find(x => samePt(x.point, p)); return e ? `current: ${nmText(e)}` : ""; }, onChange: syncNote });
      g1.appendChild(jl.el);
      body.appendChild(g1);
      const g2 = group("Mass (global axes)");
      const cols = el("div", "cd-cols");
      for (const [k, lbl, kind] of JM)
        cols.appendChild(row(lbl, numInput(kind, vals[k], { id: "am_" + k, min: 0, disabled: mode === "delete", onSet: v => { vals[k] = v; } }), kind));
      g2.appendChild(cols);
      body.appendChild(g2);
    } else {
      const objs = target === "frame" ? members : shells;
      const g1 = group(target === "frame" ? "Frames" : "Shells");
      const list = el("div", "asn-jlist dp-slist"); list.id = "amObjs";
      if (!objs.length) list.appendChild(el("p", "muted asn-empty", `No ${target === "frame" ? "frames" : "shells"} selected — select them first.`));
      const kind = target === "frame" ? "mass_per_length" : "mass_per_area";
      for (const o of objs)
        list.appendChild(el("div", "asn-jrow", `<span class="asn-pt">${esc(o.uid)}</span> <span class="dim">${esc(o.kind || "")} · ${esc(o.story || "")}</span>` +
          `<span class="asn-cur muted">${o.additional_mass ? "current: " + esc(U.fmtU(kind, o.additional_mass, 3)) +
            (o.additional_mass_mode === "distributed" ? " · distributed" : "") : ""}</span>`));
      g1.appendChild(list);
      body.appendChild(g1);
      const g2 = group("Additional Mass");
      g2.appendChild(row(target === "frame" ? "Mass per unit length" : "Mass per unit area",
        numInput(kind, q, { id: "amQ", min: 0, disabled: mode === "delete", onSet: v => { q = v; } }), kind));
      if (target === "frame") {
        const r = el("div", "cd-inline");
        r.append(el("span", "cd-lbl", "Distribution"),
          radio("amMode", "lumped", fmode === "lumped", "Lumped at member ends (mL/2 each)", () => { fmode = "lumped"; }, "amLumped"),
          radio("amMode", "distributed", fmode === "distributed", "Distributed (every FE segment)", () => { fmode = "distributed"; }, "amDistributed"));
        g2.appendChild(r);
      }
      body.appendChild(g2);
    }
    const g3 = group("Options");
    const r3 = el("div", "cd-inline");
    const modes = target === "joint" ? [["add", "Add to existing"], ["replace", "Replace existing"], ["delete", "Delete existing"]]
      : [["replace", "Replace existing"], ["delete", "Delete existing"]];
    for (const [v, t] of modes) r3.appendChild(radio("amOpt", v, mode === v, t, () => { mode = v; draw(); }, "amOpt-" + v));
    g3.appendChild(r3);
    body.appendChild(g3);
    body.appendChild(err);
    syncNote();
  };
  function syncNote() {
    if (!fb) return;
    fb.note.textContent = target === "joint" ? `${joints.filter(j => j.on).length} joint(s)` : `${(target === "frame" ? members : shells).length} object(s)`;
  }
  const commit = () => {
    let changed = false;
    if (target === "joint") {
      const pts = joints.filter(j => j.on).map(j => j.p);
      if (!pts.length) { showError(err, "Select at least one joint."); return false; }
      if (JM.some(([k]) => !(isNum(vals[k]) && vals[k] >= 0))) { showError(err, "Masses must be ≥ 0."); return false; }
      const zero = JM.every(([k]) => !vals[k]);
      let list = (m.nodal_masses || []).slice();
      for (const p of pts) {
        const i = list.findIndex(e => samePt(e.point, p));
        if (mode === "delete") {
          if (i >= 0) { list = list.filter(e => !samePt(e.point, p)); changed = true; }
        } else if (mode === "replace") {
          if (i >= 0) {
            const nu = nodalMassDict(list[i].point, vals);
            if (zero) { list = list.filter(e => !samePt(e.point, p)); changed = true; }
            else if (JSON.stringify(nu) !== JSON.stringify(nodalMassDict(list[i].point, list[i]))) {
              list = list.filter(e => !samePt(e.point, p)); list.push(nu); changed = true;
            }
          } else if (!zero) { list.push(nodalMassDict(p, vals)); changed = true; }
        } else if (!zero) {                                    // add
          if (i >= 0) {
            const e = list[i];
            const sum = {};
            for (const [k] of JM) sum[k] = (e[k] || 0) + (vals[k] || 0);
            list[i] = nodalMassDict(e.point, sum);
          } else list.push(nodalMassDict(p, vals));
          changed = true;
        }
      }
      if (changed) m.nodal_masses = list;
    } else {
      const objs = target === "frame" ? members : shells;
      if (!objs.length) { showError(err, "Nothing selected."); return false; }
      if (mode !== "delete" && !(isNum(q) && q >= 0)) { showError(err, "Mass must be ≥ 0."); return false; }
      for (const o of objs) {
        const nq = mode === "delete" ? 0 : q;
        const nm = mode === "delete" ? "lumped" : fmode;
        const before = JSON.stringify([o.additional_mass || 0, o.additional_mass_mode || "lumped"]);
        if (nq) o.additional_mass = nq; else delete o.additional_mass;
        if (target === "frame") { if (nm !== "lumped") o.additional_mass_mode = nm; else delete o.additional_mass_mode; }
        if (JSON.stringify([o.additional_mass || 0, o.additional_mass_mode || "lumped"]) !== before) changed = true;
      }
    }
    if (changed) { ctx.markDirty(); ctx.onChange && ctx.onChange("additional_mass"); }
    return true;
  };
  const fb = footBar("", [
    btn("Cancel", "", () => dlg.close(), "", "amCancel"),
    btn("OK", "btn-run", () => { if (commit()) dlg.close(); }, "", "amOk"),
  ]);
  const title = { joint: "Assign Joint Additional Mass", frame: "Assign Frame Additional Mass", shell: "Assign Shell Additional Mass" }[target];
  const dlg = dialog("amModal", { title, glyph: "mass", wide: target === "joint", body, foot: fb.wrap, onUnits: draw });
  draw();
  return dlg;
}

/* ================================================================
   Story tab — per-diaphragm results (results.diaphragms)
   ================================================================ */
function diaCaseBlock(r, caseName) {
  const d = r && r.diaphragms;
  if (!d || !caseName) return null;
  if (caseName.startsWith("rs:")) return (d.rs_cases || {})[caseName.slice(3)] || null;
  if (caseName.startsWith("nl:") || caseName.startsWith("staged:")) return null;
  return (d.cases || {})[caseName] || (d.combos || {})[caseName] || null;
}
export function diaphragmRows(r, caseName) {
  const d = r && r.diaphragms;
  if (!d || !d.stories) return [];
  const cb = diaCaseBlock(r, caseName) || {};
  const order = [...(r.story_order || Object.keys(d.stories))].reverse();
  const out = [];
  for (const s of order) {
    const ps = d.stories[s];
    if (!ps) continue;
    for (const [n, p] of Object.entries(ps)) out.push({ story: s, name: n, p, c: (cb[s] || {})[n] || null });
  }
  return out;
}
export function renderDiaphragmCard(sky) {
  ensureCss();
  const host = document.getElementById("content-story");
  if (!host) return;
  let card = document.getElementById("dpResCard");
  const r = sky.store.results;
  const rows = diaphragmRows(r, sky.store.caseName);
  if (!rows.length) { if (card) card.remove(); return; }
  if (!card) {
    card = el("div", "diag-block dp-card");
    card.id = "dpResCard";
    const t = document.getElementById("storyTable");
    const anchor = t && t.closest(".table-scroll");
    if (anchor && anchor.parentNode === host) anchor.after(card); else host.appendChild(card);
  }
  const cname = sky.store.caseName || "";
  const hasCase = rows.some(x => x.c);
  const L = esc(U.label("length")), D = esc(U.label("disp")), M = esc(U.label("mass")), MI = esc(U.label("mass_moi"));
  const f = (k, v, dd) => (v == null || !isFinite(v)) ? `<span class="dim">—</span>` : U.fmt(k, v, dd);
  const tr = v => (v == null || !isFinite(v)) ? `<span class="dim">—</span>` : `<span class="${v >= 1.2 ? "exceed" : ""}">${(+v).toFixed(3)}</span>`;
  card.innerHTML = `<div class="diag-head"><div><h3>${svgIcon("diaph", "nls-h-ico")} Diaphragms <span class="muted">per named diaphragm</span></h3>` +
    `<p class="muted">${hasCase ? `case <b>${esc(cname.replace(/^rs:/, "RS: "))}</b>` : `<span class="cd-warn">no per-diaphragm response for ${esc(cname || "this selection")}</span> — properties only`}` +
    ` · CM / CR from the unit-load method at each master; drift vs the same-name diaphragm below; τ ratio = max / average edge drift</p></div></div>` +
    `<div class="table-scroll"><table class="data-table dp-res" id="dpResTable"><thead><tr><th class="txt">Story</th><th class="txt">Diaphragm</th><th class="txt">Type</th>` +
    `<th>Mass ${M}</th><th>J ${MI}</th><th>CM x ${L}</th><th>CM y ${L}</th><th>CR x ${L}</th><th>CR y ${L}</th>` +
    `<th>ux ${D}</th><th>uy ${D}</th><th>rz rad</th><th>drift ‰ x</th><th>drift ‰ y</th><th>τ ratio x</th><th>τ ratio y</th></tr></thead><tbody>` +
    rows.map(({ story, name, p, c }) => `<tr><td class="txt">${esc(story)}</td><td class="txt"><b>${esc(name)}</b></td>` +
      `<td class="txt dim">${esc(TYPE_LABEL[p.type] || p.type || "")}${p.master == null ? "" : ` · m${esc(p.master)}`}</td>` +
      `<td>${f("mass", p.mass, 2)}</td><td>${f("mass_moi", p.mass_rz, 1)}</td>` +
      `<td>${f("length", p.cm_x, 2)}</td><td>${f("length", p.cm_y, 2)}</td><td>${f("length", p.cr_x, 2)}</td><td>${f("length", p.cr_y, 2)}</td>` +
      `<td>${c ? f("disp", c.ux, 2) : "—"}</td><td>${c ? f("disp", c.uy, 2) : "—"}</td><td>${c && isFinite(c.rz) ? (+c.rz).toExponential(2) : "—"}</td>` +
      `<td>${c && isFinite(c.drift_x) ? (Math.abs(c.drift_x) * 1000).toFixed(3) : "—"}</td><td>${c && isFinite(c.drift_y) ? (Math.abs(c.drift_y) * 1000).toFixed(3) : "—"}</td>` +
      `<td>${c ? tr(c.tors_ratio_x) : "—"}</td><td>${c ? tr(c.tors_ratio_y) : "—"}</td></tr>`).join("") +
    `</tbody></table></div>`;
}

/* ================================================================
   install
   ================================================================ */
export function installDiaphragms(sky) {
  if (!sky) return;
  ensureCss();
  Object.assign(sky, {
    openDiaphragms: () => openDiaphragms(makeCtx(sky)),
    openAssignJointDiaphragm: () => openAssignDiaphragm(makeCtx(sky), sky, "joint"),
    openAssignShellDiaphragm: () => openAssignDiaphragm(makeCtx(sky), sky, "shell"),
    openJointAddMass: () => openAdditionalMass(makeCtx(sky), sky, "joint"),
    openFrameAddMass: () => openAdditionalMass(makeCtx(sky), sky, "frame"),
    openShellAddMass: () => openAdditionalMass(makeCtx(sky), sky, "shell"),
    renderDiaphragmCard: () => renderDiaphragmCard(sky),
    validateDiaphragms,
    closeDiaphragmDialog: closeDialog,
  });
  const upd = () => { try { renderDiaphragmCard(sky); } catch (e) { console.warn("diaphragm card", e); } };
  document.addEventListener("sky:results-changed", upd);
  document.addEventListener("sky:units-changed", upd);
}
