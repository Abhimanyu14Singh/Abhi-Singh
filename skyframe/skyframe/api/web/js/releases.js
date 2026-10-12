/* SkyFrame — frame end releases / partial fixity and joint local axes
   (CONTRACT "Frame end releases, partial fixity and joint local axes").

     Assign › Frame › Releases / Partial Fixity   [asn-frel]  → openFrameReleases
        6 × 2 grid (P V2 V3 T M2 M3 × Start / End): release checkbox +
        partial-fixity spring (kN/m or kN·m/rad through js/units.js),
        ETABS stability warning, Pinned / Clear presets.
     Assign › Joint › Local Axes…                [asn-jlocalaxes] → openJointLocalAxes
        angles A (about Z) · B (about 2) · C (about 1); restraints and
        springs at the joint act in these axes.
     Assign › Joint Loads › Force / Moment (Joint Local)… [asn-jforce-local]
        F1..M3 in the joint local axes, converted to the stored GLOBAL
        nodal load (joint_load_to_global).
     3D view: a small ring near each released end (double ring = partial
        fixity) via __sky.relDrawViewer (one hook line in viewer3d.js).

   Model JSON (SI, canonical): legacy "releases" tokens Mi / Mj stay the
   spelling of the plain M2+M3 pin; anything else lives in
   member.end_releases = {i: {dof: k}, j: {...}} (k = 0 full release,
   k > 0 partial fixity spring). Only touched fields are written, so OK
   with defaults is byte-identical. */

import relU from "./units.js";
import {
  el as relEl, esc as relEsc, dkForm as relForm, dkGroup as relGroup, dkNote as relNote,
  dkDialog as relDialog, dkDraft as relDraft, dkCommit as relCommit,
} from "./dlgkit.js";
import { selectionJoints as relSelectionJoints } from "./assigndlg.js";

export const REL_DOFS = ["P", "V2", "V3", "T", "M2", "M3"];
const REL_ENDS = ["i", "j"];
const REL_TRANS = new Set(["P", "V2", "V3"]);
const REL_LABEL = { P: "Axial Load", V2: "Shear Force 2 (Major)", V3: "Shear Force 3 (Minor)", T: "Torsion", M2: "Moment 22 (Minor)", M3: "Moment 33 (Major)" };
const REL_TOKEN = { i: "Mi", j: "Mj" };
const kindOf = d => REL_TRANS.has(d) ? "stiffness" : "rot_stiffness";

/* ------------------------------------------------------------ model helpers */
const relToks = mm => String(mm.releases || "").split(",").map(s => s.trim()).filter(Boolean);
/** {i: {dof: k}, j: {...}} — legacy tokens + end_releases. */
export function relEffective(mm) {
  const toks = relToks(mm), er = mm.end_releases || {};
  const out = {};
  for (const e of REL_ENDS) {
    const d = {};
    if (toks.includes(REL_TOKEN[e])) { d.M2 = 0; d.M3 = 0; }
    for (const [k, v] of Object.entries(er[e] || {})) d[k] = +v || 0;
    out[e] = d;
  }
  return out;
}
const isPin = d => Object.keys(d).length === 2 && d.M2 === 0 && d.M3 === 0;
/** Canonical {releases, end_releases|null} of an effective set. */
export function relCanonical(eff) {
  const toks = [], er = {};
  for (const e of REL_ENDS) {
    const d = eff[e] || {};
    if (!Object.keys(d).length) continue;
    if (isPin(d)) toks.push(REL_TOKEN[e]);
    else { er[e] = {}; for (const k of REL_DOFS) if (k in d) er[e][k] = +d[k] || 0; }
  }
  return { releases: toks.join(","), end_releases: Object.keys(er).length ? er : null };
}
export function relWrite(mm, eff) {
  const c = relCanonical(eff);
  mm.releases = c.releases;
  if (c.end_releases) mm.end_releases = c.end_releases; else delete mm.end_releases;
}
/** ETABS unstable release combinations (full releases only). */
export function relUnstable(eff) {
  const full = e => new Set(Object.entries(eff[e] || {}).filter(([, k]) => !k).map(([d]) => d));
  const I = full("i"), J = full("j");
  const both = d => I.has(d) && J.has(d), either = d => I.has(d) || J.has(d);
  const out = [];
  for (const d of ["P", "V2", "V3", "T"]) if (both(d)) out.push(`${d} released at both ends`);
  if (both("M2") && either("V3")) out.push("M2 released at both ends with V3 released");
  if (both("M3") && either("V2")) out.push("M3 released at both ends with V2 released");
  return out;
}

/* joint local axes: R = Rz(a)·Ry(b)·Rx(c); rows = local 1/2/3 (global comps) */
export function jlaRows(angles) {
  const [a, b, c] = (angles || [0, 0, 0]).map(v => (+v || 0) * Math.PI / 180);
  const Rz = [[Math.cos(a), -Math.sin(a), 0], [Math.sin(a), Math.cos(a), 0], [0, 0, 1]];
  const Ry = [[Math.cos(b), 0, Math.sin(b)], [0, 1, 0], [-Math.sin(b), 0, Math.cos(b)]];
  const Rx = [[1, 0, 0], [0, Math.cos(c), -Math.sin(c)], [0, Math.sin(c), Math.cos(c)]];
  const mul = (A, B) => A.map((r, i) => B[0].map((_, j) => r.reduce((s, _x, k) => s + A[i][k] * B[k][j], 0)));
  const R = mul(mul(Rz, Ry), Rx);
  return [0, 1, 2].map(j => [R[0][j], R[1][j], R[2][j]].map(v => Math.abs(v) < 1e-15 ? 0 : v));
}
const samePt = (a, b) => Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6 && Math.abs(a[2] - b[2]) < 1e-6;
export function jlaAt(model, p) {
  const e = (model.joint_local_axes || []).find(x => samePt(x.point, p));
  return e ? e.angles : null;
}
/** Joint-local [F1..M3] → global [FX..MZ] (identity without local axes). */
export function jlaToGlobal(model, p, f6) {
  const ang = jlaAt(model, p);
  if (!ang || ang.every(v => !v)) return f6.map(v => +v || 0);
  const R = jlaRows(ang);
  const g = j => R.reduce((s, r, i) => s + r[j] * (+f6[i] || 0), 0);
  const gm = j => R.reduce((s, r, i) => s + r[j] * (+f6[3 + i] || 0), 0);
  return [g(0), g(1), g(2), gm(0), gm(1), gm(2)];
}

const selRefs = sky => (sky.store.selection || []);
function relEmpty(sky, id, title, what) {
  return relDialog({
    id, title, size: "sm", noCancel: true,
    body: [relNote(`Nothing to assign — select one or more <b>${relEsc(what)}</b> first (Select tool <kbd>V</kbd>: click, or drag a box), then choose this command again.`)],
  });
}

/* ================================================================
   Assign › Frame › Releases / Partial Fixity
   ================================================================ */
export function openFrameReleases(sky) {
  const S = sky.store;
  const refs = selRefs(sky).filter(r => r.type === "member");
  if (!refs.length) return relEmpty(sky, "relFrame", "Assign Frame Releases / Partial Fixity", "frame objects (beams, columns, braces)");
  const mems = refs.map(r => S.model.members.find(m => m.uid === r.uid)).filter(Boolean);
  const effs = mems.map(relEffective);
  const F = relForm();
  const P = {};                                  // "e.d.rel" / "e.d.k" → value (touched only)
  const common = f => { const v0 = JSON.stringify(f(effs[0])); return effs.some(x => JSON.stringify(f(x)) !== v0) ? { mixed: true } : { v: f(effs[0]) }; };
  const cur = {};                                // displayed state
  for (const e of REL_ENDS) for (const d of REL_DOFS) {
    const r = common(x => d in x[e]), k = common(x => x[e][d] ?? 0);
    cur[`${e}.${d}.rel`] = r; cur[`${e}.${d}.k`] = k;
  }
  const relState = (e, d) => (`${e}.${d}.rel` in P) ? { v: P[`${e}.${d}.rel`] } : cur[`${e}.${d}.rel`];
  const kState = (e, d) => (`${e}.${d}.k` in P) ? { v: P[`${e}.${d}.k`] } : cur[`${e}.${d}.k`];

  const warn = relEl("div", { class: "dk-note rel-warn", id: "relWarn", role: "status", style: "color:var(--warn,#e0a030);min-height:1.2em" });
  const inputs = {};
  const syncRow = (e, d) => {
    const it = inputs[`${e}.${d}`]; if (!it) return;
    const r = relState(e, d), k = kState(e, d);
    it.cb.checked = !!r.v; it.cb.indeterminate = !!r.mixed;
    it.k.disabled = !(r.v || r.mixed);
    it.k.value = k.mixed ? "" : relU.inputValue(kindOf(d), k.v || 0);
    it.k.placeholder = k.mixed ? "— mixed —" : "";
  };
  const previewEff = () => {
    // the state of the first selected member after the pending edits
    const eff = JSON.parse(JSON.stringify(effs[0]));
    for (const [key, v] of Object.entries(P)) {
      const [e, d, f] = key.split(".");
      if (f === "rel") { if (v) eff[e][d] = eff[e][d] ?? 0; else delete eff[e][d]; }
      else if (d in eff[e]) eff[e][d] = v;
    }
    return eff;
  };
  const syncWarn = () => {
    const bad = relUnstable(previewEff());
    warn.innerHTML = bad.length ? `⚠ Unstable release combination: ${relEsc(bad.join("; "))} — the member is a mechanism (ETABS warns likewise).` : "";
  };
  const errEl = relEl("div", { class: "dk-err", id: "relErr", role: "alert" });
  const thead = relEl("tr", {}, [relEl("th", { text: "" }), relEl("th", { text: "Release Start" }), relEl("th", { text: "Release End" }),
    relEl("th", { text: "Spring Start" }), relEl("th", { text: "Spring End" }), relEl("th", { text: "Unit" })]);
  const rows = REL_DOFS.map(d => {
    const tds = [relEl("td", { class: "rel-dof" }, [relEl("b", { text: d }), " ", relEl("span", { class: "muted", text: REL_LABEL[d] })])];
    const cbs = {}, ks = {};
    for (const e of REL_ENDS) {
      const cb = relEl("input", { type: "checkbox", id: `relCb_${e}_${d}`, "aria-label": `Release ${d} at ${e === "i" ? "start" : "end"}` });
      cb.addEventListener("change", () => { P[`${e}.${d}.rel`] = cb.checked; F.touched = true; syncRow(e, d); syncWarn(); });
      cbs[e] = relEl("td", { class: "rel-cb" }, [cb]);
      const k = relEl("input", { type: "text", inputmode: "decimal", class: "dk-num rel-k", id: `relK_${e}_${d}`, autocomplete: "off", "aria-label": `Partial fixity spring ${d} at ${e === "i" ? "start" : "end"}` });
      k.addEventListener("change", () => {
        const txt = k.value.trim();
        if (txt === "" && kState(e, d).mixed) return;
        const v = txt === "" ? 0 : relU.parse(kindOf(d), txt);
        if (!isFinite(v) || v < 0) { errEl.textContent = `${d} spring: enter a number ≥ 0 in ${relU.label(kindOf(d))}.`; k.setAttribute("aria-invalid", "true"); return; }
        errEl.textContent = ""; k.removeAttribute("aria-invalid");
        P[`${e}.${d}.k`] = v; F.touched = true; syncWarn();
      });
      ks[e] = relEl("td", {}, [k]);
      inputs[`${e}.${d}`] = { cb, k };
    }
    const unit = relEl("td", { class: "dk-unit rel-unit", "data-kind": kindOf(d), text: relU.label(kindOf(d)) });
    return relEl("tr", { "data-dof": d }, [...tds, cbs.i, cbs.j, ks.i, ks.j, unit]);
  });
  const table = relEl("table", { class: "dk-table rel-grid", id: "relGrid" }, [relEl("thead", {}, [thead]), relEl("tbody", {}, rows)]);
  const syncAll = () => { for (const e of REL_ENDS) for (const d of REL_DOFS) syncRow(e, d); for (const tr of table.querySelectorAll(".rel-unit")) tr.textContent = relU.label(tr.dataset.kind); syncWarn(); };
  const preset = (on) => () => {
    for (const e of REL_ENDS) for (const d of REL_DOFS) {
      const want = on.includes(d);
      P[`${e}.${d}.rel`] = want;
      if (want) P[`${e}.${d}.k`] = 0;
    }
    F.touched = true; syncAll();
  };
  const presets = relEl("div", { class: "dk-inline" }, [
    relEl("button", { type: "button", class: "btn btn-small", id: "relPresetPin", onclick: preset(["M2", "M3"]) }, ["Pinned (M2, M3 both ends)"]),
    relEl("button", { type: "button", class: "btn btn-small", id: "relPresetNone", onclick: preset([]) }, ["No Releases"]),
  ]);
  const body = [
    relGroup("Releases and Partial Fixity", [table, errEl, warn], { note: "A released DOF carries no force at that end; a spring value &gt; 0 makes it a partial fixity (elastic spring between the joint and the member end, member local axes). Spring values are ignored on unreleased DOFs." }),
    relGroup("Fast Assign", [presets]),
  ];
  syncAll();
  const apply = () => {
    if (errEl.textContent) return errEl.textContent;
    const keys = Object.keys(P);
    if (!keys.length) return "";
    const D = relDraft(sky);
    const uids = new Set(refs.map(r => r.uid));
    for (const mm of D.members) {
      if (!uids.has(mm.uid)) continue;
      const eff = relEffective(mm);
      for (const key of keys) {
        const [e, d, f] = key.split(".");
        if (f === "rel") { if (P[key]) eff[e][d] = eff[e][d] ?? 0; else delete eff[e][d]; }
      }
      for (const key of keys) {
        const [e, d, f] = key.split(".");
        if (f === "k" && d in eff[e]) eff[e][d] = P[key];
      }
      relWrite(mm, eff);
    }
    relCommit(sky, D);
    return "";
  };
  let unitsNow = relU.getUnits ? relU.getUnits() : "";
  const unsub = relU.onUnitsChange ? relU.onUnitsChange(() => {
    const u = relU.getUnits ? relU.getUnits() : "";
    if (u !== unitsNow) { unitsNow = u; syncAll(); }      // re-display only on a real change
  }) : null;
  return relDialog({
    id: "relFrame", title: "Assign Frame Releases / Partial Fixity", iconId: "exleaf-frames", size: "lg", form: F, body,
    intro: `${refs.length} frame object${refs.length > 1 ? "s" : ""} selected. Only the values you change are assigned. Start = i-end, End = j-end.`,
    onOk: () => { const m = apply(); if (m) return m; },
    onApply: () => apply(),
    onClose: () => { unsub && unsub(); },
  });
}

/* ================================================================
   Assign › Joint › Local Axes
   ================================================================ */
export function openJointLocalAxes(sky) {
  const S = sky.store, m = S.model;
  const pts = relSelectionJoints(m, selRefs(sky));
  if (!pts.length) return relEmpty(sky, "relJla", "Assign Joint Local Axes", "joints (select frames, springs, links or shells to pick their joints)");
  const F = relForm();
  const angs = pts.map(p => jlaAt(m, p) || [0, 0, 0]);
  const v0 = JSON.stringify(angs[0]);
  const mixed = angs.some(a => JSON.stringify(a) !== v0);
  const V = { a: mixed ? 0 : angs[0][0], b: mixed ? 0 : angs[0][1], c: mixed ? 0 : angs[0][2] };
  const P = {};
  const show = relEl("div", { class: "dk-note mono", id: "relJlaAxes" });
  const sync = () => {
    const a = [P.a ?? V.a, P.b ?? V.b, P.c ?? V.c];
    const R = jlaRows(a);
    const f = v => (Math.abs(v) < 5e-7 ? 0 : v).toFixed(4);
    show.innerHTML = mixed && !Object.keys(P).length ? "Mixed local axes in the selection." :
      R.map((r, i) => `Local ${i + 1}: (${r.map(f).join(", ")})`).join("<br>");
  };
  const num = (k, label, hint) => F.num(V, k, { label, unit: "deg", id: "relJla" + k.toUpperCase(), mixed, def: 0, hint,
    write: v => { P[k] = v == null ? 0 : v; sync(); } });
  const body = [
    relGroup("Joint Local Axes (degrees)", [
      num("a", "Angle A — rotation about Z", "rotates local 1-2 in plan (counter-clockwise from +X)"),
      num("b", "Angle B — rotation about local 2", "tilts local 1 / 3 (e.g. a support on an incline)"),
      num("c", "Angle C — rotation about local 1", ""),
      show,
    ], { note: "Restraints and point springs at these joints act in the joint local axes 1/2/3. All angles 0 = global X/Y/Z (the entry is removed)." }),
  ];
  sync();
  const apply = () => {
    if (!Object.keys(P).length) return "";
    const D = relDraft(sky);
    const had = Array.isArray(D.joint_local_axes);
    let list = (D.joint_local_axes || []).filter(e => !pts.some(p => samePt(p, e.point)));
    for (let i = 0; i < pts.length; i++) {
      const a = [P.a ?? angs[i][0], P.b ?? angs[i][1], P.c ?? angs[i][2]].map(v => +v || 0);
      if (a.some(Boolean)) list.push({ point: pts[i].map(Number), angles: a });
    }
    if (list.length || had) D.joint_local_axes = list;
    if (!list.length && had) delete D.joint_local_axes;
    relCommit(sky, D);
    return "";
  };
  return relDialog({
    id: "relJla", title: "Assign Joint Local Axes", size: "md", form: F, body,
    intro: `${pts.length} joint${pts.length > 1 ? "s" : ""} in the selection.`,
    onOk: () => { const msg = apply(); if (msg) return msg; },
    onApply: () => apply(),
  });
}

/* ================================================================
   Assign › Joint Loads › Force / Moment (Joint Local Axes)
   ================================================================ */
export function openJointLoadLocal(sky) {
  const S = sky.store, m = S.model;
  const pts = relSelectionJoints(m, selRefs(sky));
  if (!pts.length) return relEmpty(sky, "relJl", "Assign Joint Loads (Joint Local Axes)", "joints");
  const pats = Object.keys(m.patterns || {});
  const F = relForm();
  const V = { pattern: S.loadPattern && pats.includes(S.loadPattern) ? S.loadPattern : (pats[0] || ""), mode: "add", f1: 0, f2: 0, f3: 0, m1: 0, m2: 0, m3: 0 };
  const touched = { v: false };
  const w = () => () => { touched.v = true; };
  const nAx = pts.filter(p => jlaAt(m, p)).length;
  const body = [
    relGroup("Load Pattern", [
      F.select(V, "pattern", pats.map(p => [p, p]), { label: "Pattern", id: "relJlPat" }),
      F.select(V, "mode", [["add", "Add to existing loads"], ["replace", "Replace existing loads"]], { label: "Option", id: "relJlMode" }),
    ]),
    relGroup("Loads in the Joint Local Axes", [
      ...[["f1", "Force 1"], ["f2", "Force 2"], ["f3", "Force 3"]].map(([k, l]) => F.num(V, k, { label: l, kind: "force", id: "relJl" + k.toUpperCase(), def: 0, onChange: w() })),
      ...[["m1", "Moment about 1"], ["m2", "Moment about 2"], ["m3", "Moment about 3"]].map(([k, l]) => F.num(V, k, { label: l, kind: "moment", id: "relJl" + k.toUpperCase(), def: 0, onChange: w() })),
    ], { note: `${nAx} of ${pts.length} joint${pts.length > 1 ? "s have" : " has"} local axes; joints without local axes take the values as global X/Y/Z. Loads are stored in global components (converted now).` }),
  ];
  const apply = () => {
    if (!touched.v || !V.pattern) return "";
    const D = relDraft(sky);
    const pat = D.patterns[V.pattern];
    if (!pat) return `Unknown load pattern ${V.pattern}`;
    const list = Array.isArray(pat.nodal_loads) ? pat.nodal_loads : [];
    const KS = ["fx", "fy", "fz", "mx", "my", "mz"];
    for (const p of pts) {
      const g = jlaToGlobal(D, p, [V.f1, V.f2, V.f3, V.m1, V.m2, V.m3]);
      const idx = list.findIndex(e => samePt(e.point, p));
      const ex = idx >= 0 ? list[idx] : null;
      const nv = KS.map((k, i) => (V.mode === "add" && ex ? (+ex[k] || 0) : 0) + g[i]);
      if (nv.every(v => v === 0)) { if (ex && V.mode === "replace") list.splice(idx, 1); continue; }
      const e = ex || { point: p.slice() };
      e.fx = nv[0]; e.fy = nv[1]; e.fz = nv[2];
      for (const [i, k] of [[3, "mx"], [4, "my"], [5, "mz"]]) { if (nv[i] !== 0) e[k] = nv[i]; else delete e[k]; }
      if (!ex) list.push(e);
    }
    pat.nodal_loads = list;
    relCommit(sky, D);
    return "";
  };
  return relDialog({
    id: "relJl", title: "Assign Joint Loads (Joint Local Axes)", size: "md", form: F, body,
    intro: `${pts.length} joint${pts.length > 1 ? "s" : ""} in the selection.`,
    onOk: () => { const msg = apply(); if (msg) return msg; },
  });
}

/* ================================================================
   3D view symbols (called from viewer3d.js after the frame lines)
   ================================================================ */
export function relDrawViewer(ctx, segsScreen, model, overlayActive) {
  if (overlayActive || !model || !segsScreen || !segsScreen.length) return;
  const byUid = new Map();
  for (const mm of model.members || []) {
    if (!(mm.end_releases || (mm.releases && mm.releases.trim()))) continue;
    byUid.set(mm.uid, relEffective(mm));
  }
  if (!byUid.size) return;
  ctx.save();
  ctx.strokeStyle = "#34c384"; ctx.lineWidth = 1.2;
  for (const s of segsScreen) {
    const eff = byUid.get(s.seg.uid);
    if (!eff) continue;
    const dx = s.x2 - s.x1, dy = s.y2 - s.y1, L = Math.hypot(dx, dy);
    if (L < 14) continue;
    const off = Math.min(9, L * 0.2) / L;
    for (const [e, x, y] of [["i", s.x1 + dx * off, s.y1 + dy * off], ["j", s.x2 - dx * off, s.y2 - dy * off]]) {
      const d = eff[e];
      if (!d || !Object.keys(d).length) continue;
      const partial = Object.values(d).some(k => k > 0);
      ctx.beginPath(); ctx.arc(x, y, 3.2, 0, 2 * Math.PI);
      ctx.fillStyle = "rgba(13,17,23,0.9)"; ctx.fill(); ctx.stroke();
      if (partial) { ctx.beginPath(); ctx.arc(x, y, 5.4, 0, 2 * Math.PI); ctx.stroke(); }
    }
  }
  ctx.restore();
}

/* ================================================================
   installer (one init line in app.js)
   ================================================================ */
export function initReleases(sky) {
  if (!sky) return;
  sky.relOpenReleases = () => openFrameReleases(sky);
  sky.relOpenJointAxes = () => openJointLocalAxes(sky);
  sky.relOpenJointLoadLocal = () => openJointLoadLocal(sky);
  sky.relDrawViewer = relDrawViewer;
  sky.rel = { effective: relEffective, canonical: relCanonical, unstable: relUnstable, jlaRows, jlaToGlobal };
  if (sky.menu && sky.menu.add) {
    try { sky.menu.add("Assign/Joint", { label: "Local Axes…", act: "asn-jlocalaxes", fn: () => openJointLocalAxes(sky), hint: "Joint local axes for restraints, springs and joint loads (ETABS Assign › Joint › Local Axes)" }); } catch (e) { console.warn("menu add asn-jlocalaxes", e); }
    try { sky.menu.add("Assign/Joint Loads", { label: "Force / Moment (Joint Local Axes)…", act: "asn-jforce-local", fn: () => openJointLoadLocal(sky), hint: "Joint loads entered in the joint local axes (stored as global components)" }); } catch (e) { console.warn("menu add asn-jforce-local", e); }
    // the legacy toast handler of asn-frel is replaced here; dlgstd.js routes it too
    try { const it = sky.menu.item && sky.menu.item("asn-frel"); if (it) it.fn = () => openFrameReleases(sky); } catch { /* ignore */ }
  }
}
