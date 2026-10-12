/* SkyFrame Wave B — named LINE and AREA spring properties (ETABS v22.5:
   Define › Spring Properties › Line / Area, incl. multilinear area springs).

   CONTRACT "Orthotropic materials, slab / deck property types, cold-formed
   and composite sections, named line / area springs" (skyframe/core/
   spring_dist.py):

     model.line_spring_properties / area_spring_properties  {name: {kind,
       k: [k1, k2, k3], nonlinear_dof?, curves?: {U1|U2|U3: [[d, f], …]}}}
     line_springs[i].property      (inline kx / ky / kz = 0)
     shells[i].area_spring = {property}

   Line k in kN/m per m, area k in kN/m per m²; curve forces per m / per m²;
   U1 / U2 / U3 = global X / Y / Z (U3 negative = settlement).  Each FE node
   gets the law × its tributary length / area.

   Dialogs (dlgkit standard): Define › Section Properties › Line / Area
   Spring Properties… (tabs Line | Area → Spring Property Data) and Assign ›
   Shell › Line / Area Spring Property… (selected line springs / slabs).
   OK with defaults is byte-identical; numbers through js/units.js. */

import wspU from "./units.js";
import { el as wspEl, esc as wspEsc, clone as wspClone, isNum as wspIsNum, dkForm as wspForm, dkGroup as wspGroup, dkTabs as wspTabs, dkList as wspList,
  dkNote as wspNote, dkDialog as wspDialog, dkDraft as wspDraft, dkCommit as wspCommit, uniqueName as wspUnique } from "./dlgkit.js";
import { lineSpringKey as wspLineKey } from "./modeledit.js";

export const WSP_KINDS = [["linear", "Linear"], ["compression_only", "Compression only"], ["tension_only", "Tension only"], ["multilinear", "Multilinear elastic"]];
const DOFS = ["U1", "U2", "U3"];
const KIND = {
  line: { key: "line_spring_properties", k: "line_spring", f: "line_force", title: "Line", per: "per unit length" },
  area: { key: "area_spring_properties", k: "subgrade", f: "pressure", title: "Area", per: "per unit area" },
};

/** Validation message ("" = ok) — mirror of spring_dist.validate. */
export function wspProblem(p) {
  const k = p.k || [0, 0, 0];
  if (p.kind !== "multilinear" && !(Array.isArray(p.k) && p.k.length === 3)) return "Enter the three stiffnesses.";
  if (k.some(v => !(wspIsNum(v) && v >= 0))) return "Stiffnesses must be zero or more.";
  if (p.kind === "linear" && !k.some(v => v > 0)) return "At least one stiffness must be greater than zero.";
  if (p.kind === "compression_only" || p.kind === "tension_only") {
    const d = DOFS.indexOf(p.nonlinear_dof || "U3");
    if (!(k[d] > 0)) return `The stiffness on ${p.nonlinear_dof || "U3"} must be greater than zero.`;
  }
  if (p.kind === "multilinear") {
    const cv = p.curves || {};
    if (!Object.keys(cv).length) return "Enter the force–displacement points.";
    for (const [lab, pts] of Object.entries(cv)) {
      if (!pts.length) return `${lab}: enter at least one point.`;
      for (let i = 1; i < pts.length; i++) if (!(pts[i][0] > pts[i - 1][0])) return `${lab}: displacements must increase.`;
      if (pts.every(q => q[0] > 0)) { if (!(pts[0][1] > 0)) return `${lab}: the first force must be greater than zero.`; }
      else if (!pts.some(q => q[0] === 0 && q[1] === 0)) return `${lab}: a curve with d ≤ 0 points must pass through (0, 0).`;
    }
  }
  return "";
}

/** Mirrored backbone points (odd-symmetric when every d > 0). */
const fullCurve = pts => (pts.every(q => q[0] > 0) ? [...pts.slice().reverse().map(q => [-q[0], -q[1]]), [0, 0], ...pts] : pts);

function curveSvg(pts, kindF) {
  const W = 300, H = 140, NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("width", W); svg.setAttribute("height", H);
  svg.setAttribute("class", "spv-svg wsp-curve"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Spring backbone");
  const c = fullCurve(pts.filter(q => wspIsNum(q[0]) && wspIsNum(q[1])));
  if (c.length < 2) { svg.innerHTML = `<text class="spv-dimtxt" x="10" y="20">Enter at least one point.</text>`; return svg; }
  const xs = c.map(q => q[0]), ys = c.map(q => q[1]);
  const x0 = Math.min(...xs, 0), x1 = Math.max(...xs, 0), y0 = Math.min(...ys, 0), y1 = Math.max(...ys, 0);
  const sx = (W - 40) / ((x1 - x0) || 1), sy = (H - 30) / ((y1 - y0) || 1);
  const X = x => 30 + (x - x0) * sx, Y = y => H - 18 - (y - y0) * sy;
  let s = `<line class="spv-ext" x1="${X(x0)}" y1="${Y(0)}" x2="${X(x1)}" y2="${Y(0)}"/><line class="spv-ext" x1="${X(0)}" y1="${Y(y0)}" x2="${X(0)}" y2="${Y(y1)}"/>`;
  s += `<polyline class="spv-dim wsp-backbone" fill="none" stroke-width="2" points="${c.map(q => `${X(q[0]).toFixed(1)},${Y(q[1]).toFixed(1)}`).join(" ")}"/>`;
  for (const q of pts) s += `<circle class="spv-bar" cx="${X(q[0]).toFixed(1)}" cy="${Y(q[1]).toFixed(1)}" r="2.5"/>`;
  s += `<text class="spv-dimtxt" x="${W - 4}" y="${Y(0) - 4}" text-anchor="end">d (${wspEsc(wspU.label("disp"))})</text><text class="spv-dimtxt" x="${X(0) + 4}" y="12">f (${wspEsc(wspU.label(kindF))})</text>`;
  svg.innerHTML = s;
  return svg;
}

/** Spring Property Data for one property of draft D (which: "line" | "area"). */
export function editSpringProp(sky, D, which, name, { copyOf, done } = {}) {
  const K = KIND[which], store = D[K.key] || {};
  const src = name ? store[name] : (copyOf ? store[copyOf] : null);
  const nm0 = name || wspUnique(store, copyOf ? copyOf + "-copy" : (which === "line" ? "LSPR1" : "ASPR1"));
  const p = src ? wspClone(src) : { kind: "linear", k: [0, 0, which === "area" ? 20000 : 10000] };
  const st = { name: nm0, dof: p.curves ? Object.keys(p.curves)[0] || "U3" : "U3" };
  const F = wspForm();
  const box = wspEl("div", { id: "wspBody" });
  const prev = wspEl("div", { class: "wsp-prev", id: "wspPrev" });
  const err = wspEl("p", { class: "dk-err", role: "alert", id: "wspErr" });
  const rows = { pts: (p.curves && p.curves[st.dof]) ? p.curves[st.dof].map(q => ({ d: q[0], f: q[1] })) : [{ d: 0.01, f: which === "area" ? 50 : 100 }, { d: 0.03, f: which === "area" ? 80 : 160 }] };
  const syncCurve = () => {
    if (p.kind !== "multilinear") return;
    p.curves = { [st.dof]: rows.pts.map(r => [+r.d, +r.f]) };
  };
  const paint = () => {
    prev.textContent = "";
    if (p.kind === "multilinear") prev.appendChild(curveSvg(rows.pts.map(r => [+r.d, +r.f]), K.f));
    err.textContent = wspProblem(p);
  };
  const rebuild = () => {
    F.forget(box);
    box.textContent = "";
    const kr = [0, 1, 2].map(i => F.num(p, "k" + i, { label: `Stiffness ${DOFS[i]} (${i < 2 ? "global " + "XY"[i] : "vertical Z"})`, kind: K.k, min: 0, id: "wspK" + (i + 1),
      read: () => (p.k || [0, 0, 0])[i], write: v => { p.k = (p.k || [0, 0, 0]).slice(); p.k[i] = v == null ? 0 : v; }, onChange: paint }));
    const g = [F.select(p, "kind", WSP_KINDS, { label: "Nonlinearity", id: "wspKind",
      write: v => {
        p.kind = v;
        if (v === "multilinear") { syncCurve(); delete p.nonlinear_dof; }
        else { delete p.curves; if (v === "linear") delete p.nonlinear_dof; if (!p.k) p.k = [0, 0, 0]; }
      }, onChange: () => { rebuild(); paint(); } })];
    box.appendChild(wspGroup(`Stiffness ${K.per}`, [...g, ...kr]));
    if (p.kind === "compression_only" || p.kind === "tension_only")
      box.appendChild(wspGroup("Nonlinear DOF", [F.select(p, "nonlinear_dof", DOFS.map(d => [d, d]), { label: "Acts on", id: "wspNlDof", read: () => p.nonlinear_dof || "U3",
        write: v => { if (v === "U3") delete p.nonlinear_dof; else p.nonlinear_dof = v; }, onChange: paint })]));
    if (p.kind === "multilinear") {
      box.appendChild(wspGroup("Force–Deformation (nonlinear elastic)", [
        F.select(st, "dof", DOFS.map(d => [d, d]), { label: "Curve DOF", id: "wspCurveDof", write: v => { st.dof = v; syncCurve(); }, onChange: paint }),
        F.table(rows.pts, [{ key: "d", label: "Displacement", type: "num", kind: "disp" }, { key: "f", label: `Force ${K.per}`, type: "num", kind: K.f }],
          { id: "wspPts", minRows: 1, newRow: r => ({ d: r.length ? +(r[r.length - 1].d * 2).toPrecision(6) : 0.01, f: r.length ? r[r.length - 1].f * 1.2 : 1 }),
            onChange: () => { syncCurve(); paint(); }, validate: () => wspProblem(p) }),
        wspNote("Points with only positive displacements are mirrored through the origin; stiffness of other DOFs comes from the table above."),
      ]));
    }
    paint();
  };
  rebuild();
  return wspDialog({
    id: "wspPropData", title: `${K.title} Spring Property Data`, iconId: "exleaf-joints", size: "md", form: F,
    body: [wspGroup("General", [F.text(st, "name", { label: "Property Name", id: "wspName", validate: v => (v !== name && store[v]) ? `“${v}” is already used — pick another name.` : "" })]), box, prev, err],
    onOk: () => {
      syncCurve();
      const msg = wspProblem(p);
      if (msg) return msg;
      if (name && !F.touched) { done && done(name); return; }
      const nn = st.name.trim();
      D[K.key] = D[K.key] || {};
      if (name && nn !== name) {
        delete D[K.key][name];
        if (which === "line") for (const ls of D.line_springs || []) { if (ls.property === name) ls.property = nn; }
        else for (const r of D.shells || []) { if (r.area_spring && r.area_spring.property === name) r.area_spring = { property: nn }; }
      }
      const out = { kind: p.kind };
      if (p.k) out.k = p.k.map(Number);
      if (p.nonlinear_dof) out.nonlinear_dof = p.nonlinear_dof;
      if (p.kind === "multilinear") out.curves = p.curves;
      D[K.key][nn] = out;
      done && done(nn);
    },
  });
}

/** Define › Spring Properties › Line / Area (tabbed list). */
export function openSpringProps(sky, { tab = "line" } = {}) {
  const D = wspDraft(sky);
  const had = { line: !!D.line_spring_properties, area: !!D.area_spring_properties };
  const err = wspEl("p", { class: "dk-err dk-side-err", role: "alert" });
  const used = (which, n) => which === "line" ? (D.line_springs || []).filter(ls => ls.property === n).length
    : (D.shells || []).filter(r => r.area_spring && r.area_spring.property === n).length;
  const mkList = which => {
    const K = KIND[which];
    const L = wspList({
      id: `wsp${K.title}List`,
      items: () => Object.entries(D[K.key] || {}).map(([k, p]) => ({ key: k, p })),
      columns: [
        { label: "Property", get: it => it.key },
        { label: "Nonlinearity", get: it => (WSP_KINDS.find(x => x[0] === it.p.kind) || [0, it.p.kind])[1] },
        { label: `k3 (${wspU.label(K.k)})`, get: it => it.p.k ? wspU.sci(K.k, it.p.k[2], 3) : "—" },
        { label: "Used by", get: it => used(which, it.key) || "—" },
      ],
      empty: `No ${which} spring properties yet.`,
      onOpen: k => edit(k),
      actions: [
        { sep: "Add" },
        { label: `Add New ${K.title} Spring…`, id: `wsp${K.title}Add`, fn: () => edit(null) },
        { label: "Add Copy…", id: `wsp${K.title}Copy`, needsSel: true, fn: k => edit(null, { copyOf: k }) },
        { sep: "Selected" },
        { label: "Modify/Show…", id: `wsp${K.title}Modify`, needsSel: true, fn: k => edit(k) },
        { label: "Delete", id: `wsp${K.title}Delete`, needsSel: true, fn: k => {
          const n = used(which, k);
          if (n) { err.textContent = `${k} is used by ${n} ${which === "line" ? "line spring" : "shell"}${n > 1 ? "s" : ""} — reassign first.`; return; }
          err.textContent = ""; delete D[K.key][k]; L.render();
        } },
      ],
      extraSide: which === "line" ? err : null,
    });
    const edit = (n, o = {}) => editSpringProp(sky, D, which, n, { ...o, done: nm => L.select(nm) });
    return L;
  };
  const tabs = wspTabs([
    { id: "line", label: "Line Springs", body: mkList("line") },
    { id: "area", label: "Area Springs", body: mkList("area") },
  ], { active: tab, id: "wspTabs" });
  return wspDialog({
    id: "wspProps", title: "Define Spring Properties — Line / Area", iconId: "exleaf-joints", size: "lg",
    intro: "Named line (per length) and area (per area) spring properties: linear, compression / tension only or multilinear elastic (ETABS v22.5). Assign them with Assign › Shell › Line / Area Spring Property….",
    body: [tabs, err],
    onOk: () => {
      for (const which of ["line", "area"]) { const k = KIND[which].key; if (D[k] && !Object.keys(D[k]).length && !had[which]) delete D[k]; }
      wspCommit(sky, D);
    },
  });
}

/** Assign › Shell › Line / Area Spring Property… (selected objects). */
export function openAssignSpringProps(sky) {
  const D = wspDraft(sky);
  const sel = sky.store.selection || [];
  const lsKeys = new Set(sel.filter(r => r.type === "linespring").map(r => r.uid));
  const shUids = new Set(sel.filter(r => r.type === "shell" || r.type === "slab" || r.type === "wall").map(r => r.uid));
  const lines = (D.line_springs || []).filter(ls => lsKeys.has(wspLineKey(ls)));
  const slabs = (D.shells || []).filter(r => shUids.has(r.uid));
  const F = wspForm();
  const common = (arr, f) => { if (!arr.length) return { v: "", mixed: false }; const v0 = f(arr[0]); return arr.every(x => f(x) === v0) ? { v: v0, mixed: false } : { v: "", mixed: true }; };
  const cl = common(lines, ls => ls.property || ""), ca = common(slabs, r => (r.area_spring && r.area_spring.property) || "");
  const V = { line: cl.v, area: ca.v }, touched = { line: false, area: false };
  const lp = () => [["", "None (inline kx / ky / kz)"], ...Object.keys(D.line_spring_properties || {}).map(n => [n, n])];
  const ap = () => [["", "None"], ...Object.keys(D.area_spring_properties || {}).map(n => [n, n])];
  const body = [];
  if (lines.length) body.push(wspGroup(`Line Springs (${lines.length})`, [F.select(V, "line", lp, { label: "Line Spring Property", id: "wspAsnLine", mixed: cl.mixed, write: v => { V.line = v; touched.line = true; } })]));
  if (slabs.length) body.push(wspGroup(`Shells (${slabs.length})`, [F.select(V, "area", ap, { label: "Area Spring Property", id: "wspAsnArea", mixed: ca.mixed, write: v => { V.area = v; touched.area = true; } })]));
  if (!body.length) body.push(wspNote("Select line springs and / or shell-behaviour slabs first (Draw › Links &amp; Springs › Line Spring, Draw › Floors)."));
  const apply = () => {
    if (touched.area && V.area && slabs.some(r => (r.behavior || "shell") !== "shell")) return "Area springs need shell-behaviour slabs (membrane slabs have no mesh nodes).";
    if (touched.line) for (const ls of lines) {
      if (V.line) { ls.property = V.line; ls.kx = 0; ls.ky = 0; ls.kz = 0; ls.compression_only = false; }
      else if (ls.property) { delete ls.property; if (!(ls.kz > 0 || ls.kx > 0 || ls.ky > 0)) ls.kz = 1000; }
    }
    if (touched.area) for (const r of slabs) r.area_spring = V.area ? { property: V.area } : (r.area_spring && r.area_spring.property ? null : r.area_spring);
    wspCommit(sky, D);
    return "";
  };
  return wspDialog({ id: "wspAssign", title: "Assign Line / Area Spring Property", iconId: "exleaf-joints", size: "sm", form: F, body,
    intro: `${lines.length} line spring${lines.length === 1 ? "" : "s"} · ${slabs.length} shell${slabs.length === 1 ? "" : "s"} selected.`,
    onOk: () => { const m = apply(); if (m) return m; }, onApply: () => apply() });
}

export function initWbSprings(sky) {
  if (!sky.menu) return;
  sky.menu.add("Define/Section Properties", { label: "Line / Area Spring Properties…", act: "def-wb-springprops", hint: "named line / area springs · multilinear",
    before: "Frame Hinge Properties…", fn: () => openSpringProps(sky) });
  sky.menu.add("Assign/Shell", { label: "Line / Area Spring Property…", act: "asn-wb-springprops", hint: "named line / area spring",
    fn: () => openAssignSpringProps(sky) });
  sky.wbSprings = { open: o => openSpringProps(sky, o), assign: () => openAssignSpringProps(sky), problem: wspProblem };
}

export default { openSpringProps, openAssignSpringProps, editSpringProp, initWbSprings, wspProblem };
