/* SkyFrame Wave B — slab property types and CLT laminates in Slab / Wall
   Property Data, plus their js/secpreview.js cross-section drawings.

   CONTRACT "Orthotropic materials, slab / deck property types, cold-formed
   and composite sections, named line / area springs" (skyframe/core/
   slab_props.py, core/ortho.py).

     ShellSection.slab      {type: ribbed | waffle | deck_filled |
                             deck_unfilled | deck_solid | drop, geometry…}
     ShellSection.laminate  {layers: [{t, material, angle 0 | 90}, …]}
     ShellSection.thickness the WEIGHT-equivalent thickness for these types
                            (computed here exactly like
                            slab_props.weight_thickness; read-only)

   A new deck section also gets the ETABS deck defaults: shell_type
   "membrane" and the section-level one-way distribution along the ribs
   (ShellSection.distribution / one_way_dir — every membrane slab using the
   section inherits it unless it carries its own value).

   Hooks: js/dlgstd_defs.js editShellSection (wbShellTypeFields: the
   Thickness row wrapper + the type / geometry node) and js/secpreview.js
   spvShellPanel (wbSlabSvg: cross-section of one pitch; wbSecOneWay: the
   section-level one-way arrow).  Numbers through js/units.js; SI store. */

import wsU from "./units.js";
import { el as wsEl, esc as wsEsc, isNum as wsIsNum, dkGroup as wsGroup, dkNote as wsNote } from "./dlgkit.js";
import { CLT_LAYUPS as wsLayups } from "./wb_ortho.js";

export const SLAB_TYPES = [
  ["", "Slab (plain thickness)"], ["ribbed", "Ribbed (one-way joists)"], ["waffle", "Waffle (two-way joists)"], ["drop", "Drop panel (thickness override)"],
  ["deck_filled", "Deck — filled (composite)"], ["deck_unfilled", "Deck — unfilled (steel only)"], ["deck_solid", "Deck — solid slab"],
  ["laminate", "Laminated / CLT (orthotropic layers)"],
];
const F_RIB = [["overall_depth", "Overall Depth"], ["slab_thickness", "Slab Thickness (top)"], ["stem_width_top", "Stem Width at Top"], ["stem_width_bottom", "Stem Width at Bottom"]];
const GEOM = {
  ribbed: [...F_RIB, ["rib_spacing", "Rib Spacing (⊥ ribs)"]],
  waffle: [...F_RIB, ["rib_spacing_1", "Rib Spacing along Local 1"], ["rib_spacing_2", "Rib Spacing along Local 2"]],
  deck_filled: [["slab_depth", "Slab Depth above Ribs, tc"], ["rib_depth", "Rib Depth, hr"], ["rib_width_top", "Rib Width Top, wrt"], ["rib_width_bottom", "Rib Width Bottom, wrb"],
    ["rib_spacing", "Rib Spacing, sr"], ["deck_thickness", "Deck Shear Thickness, td"], ["deck_unit_weight", "Deck Unit Weight", "pressure"]],
  deck_unfilled: [["rib_depth", "Rib Depth, hr"], ["rib_width_top", "Rib Width Top, wrt"], ["rib_width_bottom", "Rib Width Bottom, wrb"],
    ["rib_spacing", "Rib Spacing, sr"], ["deck_thickness", "Deck Shear Thickness, td"], ["deck_unit_weight", "Deck Unit Weight", "pressure"]],
  deck_solid: [["slab_depth", "Slab Depth"]],
  drop: [],
};
const DEFAULTS = {
  ribbed: { overall_depth: 0.4, slab_thickness: 0.1, stem_width_top: 0.15, stem_width_bottom: 0.12, rib_spacing: 0.8, rib_dir: 1 },
  waffle: { overall_depth: 0.35, slab_thickness: 0.08, stem_width_top: 0.15, stem_width_bottom: 0.12, rib_spacing_1: 0.9, rib_spacing_2: 0.9 },
  deck_filled: { slab_depth: 0.065, rib_depth: 0.05, rib_width_top: 0.15, rib_width_bottom: 0.11, rib_spacing: 0.3, deck_thickness: 0.0009, deck_unit_weight: 0.1, rib_dir: 1 },
  deck_unfilled: { rib_depth: 0.05, rib_width_top: 0.15, rib_width_bottom: 0.11, rib_spacing: 0.3, deck_thickness: 0.0009, deck_unit_weight: 0.1, rib_dir: 1 },
  deck_solid: { slab_depth: 0.15, rib_dir: 1 },
  drop: {},
};
const HAS_DIR = new Set(["ribbed", "deck_filled", "deck_unfilled", "deck_solid"]);
export const typeOf = s => (s && s.laminate ? "laminate" : (s && s.slab && s.slab.type) || "");

/** Weight-equivalent thickness (mirror of slab_props.weight_thickness). */
export function weightThickness(s, mats) {
  const g = (s.slab || {}), mat = (mats || {})[s.material] || {}, uw = mat.unit_weight ?? 24;
  if (s.laminate) {
    const L = s.laminate.layers || [];
    const w = L.reduce((a, l) => a + (+l.t || 0) * (((mats || {})[l.material] || {}).unit_weight ?? uw), 0);
    return uw > 0 ? w / uw : L.reduce((a, l) => a + (+l.t || 0), 0);
  }
  switch (g.type) {
    case "ribbed": { const hr = g.overall_depth - g.slab_thickness, ar = 0.5 * (g.stem_width_top + g.stem_width_bottom) * hr; return g.slab_thickness + ar / g.rib_spacing; }
    case "waffle": {
      const hr = g.overall_depth - g.slab_thickness, wb = 0.5 * (g.stem_width_top + g.stem_width_bottom), ar = wb * hr, s1 = g.rib_spacing_1, s2 = g.rib_spacing_2;
      return g.slab_thickness + ar / s1 + ar / s2 - wb * wb * hr / (s1 * s2);
    }
    case "deck_filled": { const ar = 0.5 * (g.rib_width_top + g.rib_width_bottom) * g.rib_depth; return g.slab_depth + ar / g.rib_spacing + g.deck_unit_weight / uw; }
    case "deck_unfilled": return g.deck_unit_weight / uw;
    case "deck_solid": return g.slab_depth;
  }
  return s.thickness;
}

/** Geometry problem ("" = ok) — mirror of slab_props.validate_slab. */
export function slabProblem(s) {
  const g = s.slab;
  if (!g) return "";
  for (const [k] of GEOM[g.type] || []) if (!(wsIsNum(g[k]) && g[k] > 0)) return `Enter ${k.replace(/_/g, " ")} (> 0).`;
  if (g.type === "ribbed" || g.type === "waffle") {
    if (g.slab_thickness >= g.overall_depth) return "The slab thickness must be smaller than the overall depth.";
    const sp = g.type === "ribbed" ? [g.rib_spacing] : [g.rib_spacing_1, g.rib_spacing_2];
    if (Math.max(g.stem_width_top, g.stem_width_bottom) >= Math.min(...sp)) return "The stems are wider than the rib spacing.";
  } else if (g.type === "deck_filled" || g.type === "deck_unfilled") {
    if (Math.max(g.rib_width_top, g.rib_width_bottom) > g.rib_spacing) return "The rib widths exceed the rib spacing.";
    if (g.deck_thickness >= g.rib_depth) return "The deck thickness must be smaller than the rib depth.";
  }
  return "";
}

function lamProblem(s, mats) {
  const L = (s.laminate || {}).layers || [];
  if (!L.length) return "Add at least one layer.";
  for (const [i, l] of L.entries()) {
    if (!(l.t > 0)) return `Layer ${i + 1}: thickness must be greater than zero.`;
    if (!(mats || {})[l.material]) return `Layer ${i + 1}: pick a material.`;
  }
  const n = L.length;
  for (let i = 0; i < n; i++) {
    const a = L[i], b = L[n - 1 - i];
    if (Math.abs(a.t - b.t) > 1e-12 || a.material !== b.material || (+a.angle || 0) !== (+b.angle || 0)) return "The lay-up must be symmetric about its mid-plane (membrane–bending coupling is not supported).";
  }
  return "";
}

/** Fields for editShellSection: { node, thickness(row) }. */
export function wbShellTypeFields(F, s, D, { onChange } = {}) {
  const st = { type: typeOf(s) };
  const node = wsEl("div", { class: "wbs-type", id: "wbsType" });
  let tRow = null;
  const mats = () => D.materials || {};
  const sync = () => {
    if (st.type) s.thickness = weightThickness(s, mats());
    if (tRow) tRow.style.display = st.type && st.type !== "drop" ? "none" : "";
    F.refresh();
    onChange && onChange();
  };
  const switchType = v => {
    st.type = v;
    delete s.laminate;
    if (!v) { delete s.slab; return; }
    if (v === "laminate") {
      delete s.slab;
      const mat = Object.keys(mats()).find(n => (mats()[n] || {}).symmetry === "orthotropic") || s.material;
      s.material = mat;
      const [, ts] = wsLayups["CLT 120 L3s"];
      s.laminate = { layers: ts.map((t, i) => ({ t, material: mat, angle: i % 2 ? 90 : 0 })) };
      return;
    }
    s.slab = { type: v, ...DEFAULTS[v] };
    if (v.startsWith("deck")) {
      if (!s.shell_type || s.shell_type === "shell") s.shell_type = "membrane";
      s.distribution = "one_way"; s.one_way_dir = 1;
    }
  };
  const typeRow = F.select(st, "type", SLAB_TYPES, {
    label: "Property Type", id: "wbsSlabType", title: "ETABS Slab / Deck property type",
    write: v => switchType(v), onChange: () => { rebuild(); sync(); },
  });
  const geomBox = wsEl("div", { id: "wbsGeom" });
  const readBox = wsEl("p", { class: "muted dk-note", id: "wbsWeightNote" });
  const err = wsEl("p", { class: "dk-err", role: "alert", id: "wbsErr" });
  const check = () => { err.textContent = st.type === "laminate" ? lamProblem(s, mats()) : slabProblem(s); return err.textContent; };
  const paintNote = () => {
    readBox.innerHTML = st.type && st.type !== "drop"
      ? `Weight-equivalent thickness <b>${wsEsc(wsU.fmt("dim", s.thickness, 4))} ${wsEsc(wsU.label("dim"))}</b> (self-weight and mass). Stiffness: equivalent orthotropic plate (ribs / deck along local ${s.slab && s.slab.rib_dir === 2 ? 2 : 1}).`
      : st.type === "drop" ? "Draw a slab with this section over a shell-behaviour slab: the elements of the host under its outline take the drop thickness (stiffness, weight, mass)." : "";
  };
  function rebuild() {
    F.forget(geomBox);
    geomBox.textContent = "";
    const t = st.type;
    if (!t) { paintNote(); err.textContent = ""; return; }
    const rows = [];
    if (t === "laminate") {
      const L = s.laminate.layers;
      rows.push(F.select({ v: "" }, "v", [["", "— apply a CLT lay-up —"], ...Object.keys(wsLayups).map(k => [k, k])], { label: "CLT Preset", id: "wbsLayup",
        write: v => { if (!v) return; const [, ts] = wsLayups[v]; const mat = L[0] ? L[0].material : s.material; L.splice(0, L.length, ...ts.map((tt, i) => ({ t: tt, material: mat, angle: i % 2 ? 90 : 0 }))); },
        onChange: () => { rebuild(); sync(); } }));
      rows.push(F.table(L, [
        { key: "t", label: "Thickness", type: "num", kind: "dim" },
        { key: "material", label: "Material", type: "select", options: () => Object.keys(mats()).map(n => [n, n]) },
        { key: "angle", label: "Angle", type: "select", options: [[0, "0° (along 1)"], [90, "90° (along 2)"]] },
      ], { id: "wbsLayers", newRow: () => ({ t: 0.02, material: (L[0] || {}).material || s.material, angle: 0 }), minRows: 1,
        onChange: () => { for (const l of L) l.angle = +l.angle || 0; sync(); check(); }, validate: () => lamProblem(s, mats()) }));
    } else {
      for (const [k, lbl, kind] of GEOM[t]) rows.push(F.num(s.slab, k, { label: lbl, kind: kind || "dim", gt: 0, id: "wbsDim_" + k,
        validate: v => slabProblem({ ...s, slab: { ...s.slab, [k]: v } }), onChange: () => { sync(); check(); paintNote(); } }));
      if (HAS_DIR.has(t)) rows.push(F.select(s.slab, "rib_dir", [[1, "Along local 1"], [2, "Along local 2"]], { label: t.startsWith("deck") ? "Deck Span (ribs) Direction" : "Rib Direction", id: "wbsRibDir",
        read: () => s.slab.rib_dir || 1, write: v => { s.slab.rib_dir = +v; if (s.distribution === "one_way") s.one_way_dir = +v; }, onChange: () => { sync(); paintNote(); } }));
    }
    geomBox.appendChild(wsGroup(t === "laminate" ? "Layers (bottom → top)" : "Section Geometry", rows, { id: "wbsGeomGrp" }));
    paintNote();
    check();
  }
  node.append(typeRow, geomBox, readBox, err);
  rebuild();
  return {
    node,
    thickness(row) { tRow = row; if (st.type && st.type !== "drop") row.style.display = "none"; return row; },
    problem: () => (st.type === "laminate" ? lamProblem(s, mats()) : slabProblem(s)),
  };
}

/* ================================================================
   secpreview: cross-section of one pitch (ribbed / waffle / deck) or the
   layer stack (laminate). null = not a Wave B type (legacy drawing).
   ================================================================ */
const r2 = v => Math.round(v * 100) / 100;
export function wbSlabSvg(model, sec) {
  const t = typeOf(sec);
  if (!t || t === "drop" || t === "deck_solid") return null;
  const W = 300, H = 128, NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("width", W); svg.setAttribute("height", H);
  svg.setAttribute("class", "spv-svg spv-shell-layers wbs-xsec"); svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Slab cross-section");
  svg.setAttribute("data-wb-slab", t);
  const dv = v => wsU.fmt("dim", v, 3);
  let s = "";
  if (t === "laminate") {
    const L = (sec.laminate || {}).layers || [], tt = L.reduce((a, l) => a + (+l.t || 0), 0) || 1;
    const x0 = 64, x1 = W - 12, y0 = 8, hh = H - 26;
    let y = y0;
    L.slice().reverse().forEach((l, i) => {
      const lh = Math.max(6, (+l.t || 0) / tt * hh), ninety = (+l.angle || 0) === 90;
      s += `<rect class="spv-face is-other" style="--spv-l:${ninety ? ".7" : ".88"}" x="${x0}" y="${r2(y)}" width="${x1 - x0}" height="${r2(lh)}" data-layer="${L.length - 1 - i}"/>`;
      s += ninety ? Array.from({ length: 22 }, (_, j) => `<circle class="spv-bar" cx="${x0 + 6 + j * 10}" cy="${r2(y + lh / 2)}" r="1.2"/>`).join("")
        : `<path class="spv-hatch" d="M${x0 + 4},${r2(y + lh / 2)}H${x1 - 4}"/>`;
      s += `<text class="spv-dimtxt" x="${x0 - 6}" y="${r2(y + lh / 2 + 4)}" text-anchor="end">${wsEsc(dv(+l.t || 0))}</text>`;
      y += lh;
    });
    s += `<text class="spv-dimtxt" x="${(x0 + x1) / 2}" y="${H - 4}" text-anchor="middle">Σt = ${wsEsc(dv(tt))} ${wsEsc(wsU.label("dim"))} · ${L.length} layers (— 0°, ∘ 90°)</text>`;
    svg.innerHTML = s;
    return svg;
  }
  const g = sec.slab;
  // one pitch (two half-ribs) seen along the ribs: flange on top, stems / flutes below
  let hf, hr, wt, wb, sp, label;
  if (t === "ribbed" || t === "waffle") { hf = g.slab_thickness; hr = g.overall_depth - g.slab_thickness; wt = g.stem_width_top; wb = g.stem_width_bottom; sp = t === "ribbed" ? g.rib_spacing : g.rib_spacing_2; label = t === "ribbed" ? "ribbed" : "waffle (section ∥ 1)"; }
  else { hf = t === "deck_filled" ? g.slab_depth : 0; hr = g.rib_depth; wt = g.rib_width_top; wb = g.rib_width_bottom; sp = g.rib_spacing; label = t === "deck_filled" ? "filled deck" : "unfilled deck"; }
  if (![hr, wt, wb, sp].every(v => v > 0) || !(hf >= 0)) { svg.innerHTML = `<text class="spv-dimtxt" x="10" y="20">Enter the geometry.</text>`; return svg; }
  const n = 2, Wt = n * sp, Ht = hf + hr;
  const sc = Math.min((W - 70) / Wt, (H - 40) / Ht), ox = 40, oy = 12;
  const X = x => r2(ox + x * sc), Y = y => r2(oy + (Ht - y) * sc);
  const conc = t !== "deck_unfilled";
  if (conc) {
    // concrete: flange + stems (trapezoids centred at sp/2 + k sp)
    let d = `M${X(0)},${Y(Ht)}L${X(Wt)},${Y(Ht)}L${X(Wt)},${Y(hr)}`;
    for (let k = n - 1; k >= 0; k--) {
      const c = (k + 0.5) * sp;
      d += `L${X(c + wt / 2)},${Y(hr)}L${X(c + wb / 2)},${Y(0)}L${X(c - wb / 2)},${Y(0)}L${X(c - wt / 2)},${Y(hr)}`;
    }
    d += `L${X(0)},${Y(hr)}Z`;
    s += `<path class="spv-face is-conc" style="--spv-l:.85" d="${d}" data-wb-part="concrete"/>`;
  }
  if (t.startsWith("deck")) {
    // steel deck sheet along the flute profile
    let d = `M${X(0)},${Y(hr)}`;
    for (let k = 0; k < n; k++) {
      const c = (k + 0.5) * sp;
      d += `L${X(c - wt / 2)},${Y(hr)}L${X(c - wb / 2)},${Y(0)}L${X(c + wb / 2)},${Y(0)}L${X(c + wt / 2)},${Y(hr)}`;
    }
    d += `L${X(Wt)},${Y(hr)}`;
    s += `<path class="spv-outline is-steel wbs-deck" fill="none" stroke-width="2.4" d="${d}" data-wb-part="deck"/>`;
  }
  // dimensions
  s += `<text class="spv-dimtxt" x="${X(Wt) + 4}" y="${(Y(Ht) + Y(0)) / 2 + 4}">h ${wsEsc(dv(Ht))}</text>`;
  if (hf > 0) s += `<text class="spv-dimtxt" x="${X(0) - 3}" y="${(Y(Ht) + Y(hr)) / 2 + 4}" text-anchor="end">${wsEsc(dv(hf))}</text>`;
  s += `<line class="spv-dim" x1="${X(0.5 * sp)}" y1="${H - 14}" x2="${X(1.5 * sp)}" y2="${H - 14}"/><text class="spv-dimtxt" x="${X(sp)}" y="${H - 3}" text-anchor="middle">s ${wsEsc(dv(sp))} ${wsEsc(wsU.label("dim"))} · ${label}</text>`;
  svg.innerHTML = s;
  return svg;
}

/** One-way list for the shell preview: the slabs' own one-way plus the
    section-level distribution (inherited by every membrane slab). */
export function wbSecOneWay(sec, own) {
  if (sec && sec.distribution === "one_way") return [{ one_way_dir: sec.one_way_dir === 2 ? 2 : 1, section: true }, ...own];
  return own;
}

export default { wbShellTypeFields, wbSlabSvg, wbSecOneWay, weightThickness, slabProblem, SLAB_TYPES, typeOf };
