/* SkyFrame — frequency-dependent link properties (CONTRACT
   "Frequency-dependent link properties"), dlgkit standard.

     Define › Section Properties › Frequency-Dependent Link Properties…
       → Define Frequency-Dependent Link Properties (list)
       → Frequency-Dependent Link Property Data: per-DOF [f, K, C] tables
         (UX..RZ for elastic links, AXIAL for dampers), the links that use
         the property, and a K(f) / C(f) preview.

   Model field: link_freq_props {name: {links: [uid], dofs: {DOF: [[f_hz, K,
   C], ...]}}} — omitted when empty. The list edits a draft; only its OK
   commits (dkCommit → changed keys only; OK with defaults is byte-identical).
   K / C go through js/units.js (stiffness / damping_coeff, rotational DOFs
   kN·m/rad and kN·m·s/rad); the model stays SI. */

import lfU from "./units.js";
import {
  el as lfEl, esc as lfEsc, clone as lfClone, isNum as lfIsNum, dkForm as lfForm, dkGroup as lfGroup,
  dkList as lfList, dkNote as lfNote, dkDialog as lfDialog, dkDraft as lfDraft, dkCommit as lfCommit,
  uniqueName as lfUnique,
} from "./dlgkit.js";

export const LF_DOFS = ["UX", "UY", "UZ", "RX", "RY", "RZ", "AXIAL"];
const ROT = new Set(["RX", "RY", "RZ"]);
const kKind = dof => ROT.has(dof) ? "rot_stiffness" : "stiffness";
const cKind = dof => ROT.has(dof) ? "rot_stiffness" : "damping_coeff";   // rotational C: kN·m·s/rad (same factor as kN·m/rad)
const cLabel = dof => ROT.has(dof) ? "C (· s)" : "C";

/** Links a property may use: elastic (global DOFs) and damper (AXIAL). */
export const lfEligible = l => !l.link_type || l.link_type === "elastic" || l.link_type === "damper";

/** Client mirror of core.link_freq.validate for one property → message | null. */
export function lfValidateProp(D, name, p, owner = {}) {
  const w = `Frequency-dependent link property ${name}`;
  const dofs = p.dofs || {};
  if (!Object.keys(dofs).length) return `${w}: add at least one DOF table.`;
  for (const [dof, tab] of Object.entries(dofs)) {
    if (!LF_DOFS.includes(dof)) return `${w}: DOF ${dof} is not one of ${LF_DOFS.join(", ")}.`;
    if (!Array.isArray(tab) || !tab.length) return `${w} ${dof}: the table needs at least one row.`;
    for (let i = 0; i < tab.length; i++) {
      const r = tab[i];
      if (!Array.isArray(r) || r.length !== 3 || !r.every(lfIsNum)) return `${w} ${dof}: row ${i + 1} needs f, K and C numbers.`;
      if (r[2] < 0) return `${w} ${dof}: C must be ≥ 0 (row ${i + 1}).`;
      if (i === 0 && r[0] < 0) return `${w} ${dof}: frequencies must be ≥ 0.`;
      if (i > 0 && !(r[0] > tab[i - 1][0])) return `${w} ${dof}: frequencies must increase strictly (row ${i + 1}).`;
    }
  }
  const links = Object.fromEntries((D.links || []).map(l => [l.uid, l]));
  for (const u of p.links || []) {
    const l = links[u];
    if (!l) return `${w}: unknown link ${u}.`;
    if (owner[u] && owner[u] !== name) return `${w}: link ${u} already has property ${owner[u]}.`;
    const t = l.link_type || "elastic";
    if (t === "elastic" && "AXIAL" in dofs) return `${w}: AXIAL applies to damper links; ${u} is elastic (use UX..RZ).`;
    if (t === "damper" && (Object.keys(dofs).length !== 1 || !("AXIAL" in dofs))) return `${w}: damper link ${u} takes the AXIAL DOF only.`;
    if (t !== "elastic" && t !== "damper") return `${w}: link ${u} is ${t}; frequency-dependent properties apply to elastic and damper links.`;
  }
  return null;
}
/** Whole-model check (mock POST /api/model) → message | null. */
export function lfValidateModel(D) {
  const props = D.link_freq_props;
  if (props == null) return null;
  const owner = {};
  for (const [n, p] of Object.entries(props)) {
    const e = lfValidateProp(D, n, p, owner);
    if (e) return e;
    for (const u of p.links || []) owner[u] = n;
  }
  return null;
}

/** K(f), C(f) linear interpolation with end values held (core.link_freq.interp). */
export function lfInterp(tab, f) {
  if (!tab.length) return [0, 0];
  if (f <= tab[0][0]) return [tab[0][1], tab[0][2]];
  for (let i = 1; i < tab.length; i++) if (f <= tab[i][0]) {
    const [f0, k0, c0] = tab[i - 1], [f1, k1, c1] = tab[i], t = (f - f0) / (f1 - f0);
    return [k0 + t * (k1 - k0), c0 + t * (c1 - c0)];
  }
  const l = tab[tab.length - 1];
  return [l[1], l[2]];
}

function previewSvg(tab, dof) {
  const NS = "http://www.w3.org/2000/svg";
  const w = 300, h = 130;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("width", "100%"); svg.setAttribute("class", "lf-prev-svg");
  svg.setAttribute("role", "img"); svg.setAttribute("aria-label", `K and C versus frequency, ${dof}`);
  if (!tab.length) return svg;
  const fmax = Math.max(1, tab[tab.length - 1][0] * 1.25);
  const kmax = Math.max(1e-12, ...tab.map(r => Math.abs(r[1]))), cmax = Math.max(1e-12, ...tab.map(r => Math.abs(r[2])));
  const sx = f => 28 + f / fmax * (w - 40), sk = v => h - 20 - v / kmax * (h - 34), sc = v => h - 20 - v / cmax * (h - 34);
  const line = (pts, color, dash) => {
    const pl = document.createElementNS(NS, "polyline");
    pl.setAttribute("points", pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" "));
    pl.setAttribute("fill", "none"); pl.setAttribute("stroke", color); pl.setAttribute("stroke-width", "1.5");
    if (dash) pl.setAttribute("stroke-dasharray", "4 3");
    svg.appendChild(pl);
  };
  const ax = document.createElementNS(NS, "path");
  ax.setAttribute("d", `M28 8 V${h - 20} H${w - 10}`); ax.setAttribute("stroke", "currentColor"); ax.setAttribute("stroke-opacity", "0.3"); ax.setAttribute("fill", "none");
  svg.appendChild(ax);
  const fs = []; for (let i = 0; i <= 60; i++) fs.push(fmax * i / 60);
  line(fs.map(f => [sx(f), sk(lfInterp(tab, f)[0])]), "var(--accent, #3b82f6)");
  line(fs.map(f => [sx(f), sc(lfInterp(tab, f)[1])]), "#e8833a", true);
  const txt = (x, y, s, anchor = "start") => { const t = document.createElementNS(NS, "text"); t.setAttribute("x", x); t.setAttribute("y", y); t.setAttribute("font-size", "9"); t.setAttribute("fill", "currentColor"); t.setAttribute("text-anchor", anchor); t.textContent = s; svg.appendChild(t); };
  txt(w - 10, h - 6, `${+fmax.toPrecision(3)} Hz`, "end");
  txt(30, 14, `K max ${lfU.fmtU(kKind(dof), kmax, 1)} — solid · C max ${lfU.fmt(cKind(dof), cmax, 1)} — dashed`);
  return svg;
}

/* ================================================================
   Property data dialog
   ================================================================ */
export function editLinkFreqProp(sky, D, name, { copyOf, done } = {}) {
  const isNew = name == null;
  D.link_freq_props = D.link_freq_props || {};
  const taken = () => new Set(Object.keys(D.link_freq_props));
  const nm0 = isNew ? lfUnique(taken(), copyOf ? copyOf + "-copy" : "FD1") : name;
  const src = isNew ? (copyOf ? D.link_freq_props[copyOf] : null) : D.link_freq_props[name];
  const p = src ? lfClone(src) : { dofs: { UX: [[0, 1e5, 0], [10, 1e5, 0]] }, links: [] };   // key order = the backend echo (sorted)
  if (isNew && copyOf) p.links = [];          // a copy starts unassigned (a link has one property)
  const draftName = { v: nm0 };
  const F = lfForm();
  const owner = {};
  for (const [n, q] of Object.entries(D.link_freq_props)) if (n !== name) for (const u of q.links || []) owner[u] = n;

  const tabsBox = lfEl("div", { id: "lfTables" });
  const prevBox = lfEl("div", { class: "lf-prev", id: "lfPreview" });
  let curDof = Object.keys(p.dofs)[0] || "UX";
  const rowsByDof = {};
  const rowsOf = dof => (rowsByDof[dof] = rowsByDof[dof] || (p.dofs[dof] || []).map(r => ({ f: r[0], K: r[1], C: r[2] })));
  const sync = () => { for (const d of Object.keys(rowsByDof)) p.dofs[d] = rowsByDof[d].map(r => [r.f, r.K, r.C]); };
  const dofSel = lfEl("select", { class: "dk-select", id: "lfDof" });
  const renderDofSel = () => {
    dofSel.textContent = "";
    for (const d of LF_DOFS) dofSel.appendChild(lfEl("option", { value: d, selected: d === curDof }, [d + (p.dofs[d] ? " ✓" : "")]));
  };
  const addDof = lfEl("button", { type: "button", class: "btn btn-small", id: "lfDofAdd" }, ["Use this DOF"]);
  const delDof = lfEl("button", { type: "button", class: "btn btn-small", id: "lfDofDel" }, ["Remove DOF"]);
  const renderTable = () => {
    sync();
    for (const f of [...F.fields]) if (tabsBox.contains(f.rowEl)) F.fields.splice(F.fields.indexOf(f), 1);
    tabsBox.textContent = "";
    renderDofSel();
    addDof.disabled = !!p.dofs[curDof];
    delDof.disabled = !p.dofs[curDof];
    if (!p.dofs[curDof]) { tabsBox.appendChild(lfNote(`DOF ${curDof} is not used by this property — <b>Use this DOF</b> to add a table.`)); prevBox.textContent = ""; return; }
    const rows = rowsOf(curDof);
    const tbl = F.table(rows, [
      { key: "f", label: "Frequency", type: "num", kind: "frequency" },
      { key: "K", label: "K", type: "num", kind: kKind(curDof) },
      { key: "C", label: cLabel(curDof), type: "num", kind: cKind(curDof) },
    ], {
      id: "lfRows", addLabel: "+ Add row", minRows: 1,
      newRow: rs => { const l = rs[rs.length - 1]; return l ? { f: +(l.f + 1).toPrecision(6), K: l.K, C: l.C } : { f: 0, K: 1e5, C: 0 }; },
      validateRow: (r, i) => !(lfIsNum(r.f) && lfIsNum(r.K) && lfIsNum(r.C)) ? `Row ${i + 1}: enter f, K and C.`
        : r.C < 0 ? `Row ${i + 1}: C must be zero or more.` : (i === 0 && r.f < 0) ? "Frequencies must be zero or more."
          : (i > 0 && !(r.f > rows[i - 1].f)) ? `Row ${i + 1}: frequencies must increase.` : "",
      onChange: () => { sync(); prevBox.replaceChildren(previewSvg(p.dofs[curDof], curDof)); },
    });
    tabsBox.appendChild(tbl);
    prevBox.replaceChildren(previewSvg(p.dofs[curDof], curDof));
  };
  dofSel.addEventListener("change", () => { curDof = dofSel.value; renderTable(); });
  addDof.addEventListener("click", () => { p.dofs[curDof] = [[0, 1e5, 0]]; delete rowsByDof[curDof]; F.touched = true; renderTable(); });
  delDof.addEventListener("click", () => { delete p.dofs[curDof]; delete rowsByDof[curDof]; F.touched = true; renderTable(); });

  const linkBox = lfEl("div", { class: "lf-links", id: "lfLinks" });
  const eligible = (D.links || []).filter(lfEligible);
  if (!eligible.length) linkBox.appendChild(lfNote("No elastic or damper links in the model yet."));
  for (const l of eligible) {
    const cb = lfEl("input", { type: "checkbox", value: l.uid, "data-uid": l.uid });
    cb.checked = (p.links || []).includes(l.uid);
    if (owner[l.uid]) { cb.disabled = true; cb.title = `uses ${owner[l.uid]}`; }
    cb.addEventListener("change", () => {
      F.touched = true;
      p.links = (D.links || []).filter(x => x.uid === l.uid ? cb.checked : (p.links || []).includes(x.uid)).map(x => x.uid);
    });
    linkBox.appendChild(lfEl("label", { class: "dk-check" }, [cb, lfEl("span", { text: `${l.uid} · ${l.link_type || "elastic"}${owner[l.uid] ? ` (in ${owner[l.uid]})` : ""}` })]));
  }

  const body = [
    lfGroup("General", [F.text(draftName, "v", { label: "Property Name", id: "lfName",
      validate: v => v !== name && taken().has(v) ? `“${v}” is already a property — pick another name.` : "" })]),
    lfGroup("Frequency-Dependent Stiffness and Damping", [
      lfEl("div", { class: "dk-row" }, [lfEl("label", { class: "dk-label", for: "lfDof", text: "DOF" }), lfEl("div", { class: "dk-ctl lf-dofbar" }, [dofSel, addDof, delDof])]),
      tabsBox, prevBox,
    ], { note: "Total K and C at each frequency; linear in f, end values held. UX..RZ: global relative DOFs of elastic links; AXIAL: damper links. Used by Steady State and PSD cases only." }),
    lfGroup("Links Using This Property", [linkBox]),
  ];
  renderTable();
  return lfDialog({
    id: "lfPropData", title: "Frequency-Dependent Link Property Data", iconId: "tool-link", size: "md", form: F, body,
    onOk: () => {
      if (!F.touched && !isNew && draftName.v === name) { done && done(name); return; }
      sync();
      const nn = draftName.v.trim();
      const out = { dofs: lfClone(p.dofs), links: (p.links || []).slice() };
      const msg = lfValidateProp(D, nn, out, owner);
      if (msg) return msg;
      if (name && nn !== name) delete D.link_freq_props[name];
      D.link_freq_props[nn] = out;
      done && done(nn);
    },
  });
}

/* ================================================================
   List dialog
   ================================================================ */
export function openLinkFreqProps(sky, { select } = {}) {
  const D = lfDraft(sky);
  const had = "link_freq_props" in D;
  D.link_freq_props = D.link_freq_props || {};
  const list = lfList({
    id: "lfList",
    items: () => Object.entries(D.link_freq_props).map(([k, p]) => ({ key: k, p })),
    columns: [
      { label: "Property", get: it => it.key },
      { label: "DOFs", get: it => Object.keys(it.p.dofs || {}).join(", ") },
      { label: "Links", get: it => (it.p.links || []).join(", ") || "—" },
    ],
    selected: select ?? null,
    empty: "No frequency-dependent link properties — <b>Add New Property…</b> to give elastic or damper links K(f) / C(f) tables for Steady State and PSD cases.",
    onOpen: k => edit(k),
    actions: [
      { sep: "Add" },
      { label: "Add New Property…", id: "lfAdd", fn: () => edit(null) },
      { label: "Add Copy of Property…", id: "lfCopy", needsSel: true, fn: k => edit(null, k) },
      { sep: "Selected" },
      { label: "Modify/Show Property…", id: "lfModify", needsSel: true, fn: k => edit(k) },
      { label: "Delete Property", id: "lfDelete", needsSel: true, fn: k => { delete D.link_freq_props[k]; list.render(); } },
    ],
  });
  const edit = (name, copyOf) => editLinkFreqProp(sky, D, name, { copyOf, done: nm => { list.select(nm); } });
  return lfDialog({
    id: "lfProps", title: "Define Frequency-Dependent Link Properties", iconId: "tool-link", size: "lg",
    intro: "Frequency-dependent stiffness K(f) and damping C(f) of elastic / damper links, used in place of their linear values by Steady State and Power Spectral Density cases.",
    body: list,
    onOk: () => { if (!Object.keys(D.link_freq_props).length && !had) delete D.link_freq_props; else if (!Object.keys(D.link_freq_props).length) delete D.link_freq_props; lfCommit(sky, D); },
  });
}

export function initLinkFreq(sky) {
  sky.openLinkFreqProps = o => openLinkFreqProps(sky, o);
  return { open: sky.openLinkFreqProps, validateModel: lfValidateModel, interp: lfInterp };
}
