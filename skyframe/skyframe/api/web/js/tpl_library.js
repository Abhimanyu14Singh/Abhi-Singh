/* SkyFrame D4 — Template Library (File › New Model from Template…, act
   file-wb-template, rebound here): browser of the structural-system
   templates (ETABS New Model quick templates / example-style buildings).

   Left: every template by category (steel · concrete · tall building ·
   special · slab systems) with a sketch thumbnail. Right: a live 3-D +
   plan sketch of the CURRENT parameters (element counts, inline
   constraint errors), the system description / ETABS equivalent and the
   parameter panel generated from the backend catalogue metadata
   (GET /api/model/templates `meta`, dlgkit form; every length / pressure /
   stiffness is typed in display units, js/units.js; the model stays SI).
   OK builds through POST /api/model/template (backend: skyframe/core/
   templates_sys.py + templates.py) — ?mock=1: js/mock_tpl_library.js
   (system ids) / js/mock_wbi.js (the five slab templates).

   Also adds start-screen cards for the 14 system templates (js/ux_start.js
   card.build / card.thumb hooks) and window.__sky.tplLib. */

import { el as tlEl, dkForm as tlForm, dkGroup as tlGroup, dkAdvanced as tlAdv, dkDialog as tlDialog, dkNote as tlNote, esc as tlEsc } from "./dlgkit.js";
import tlU from "./units.js";
import { tplSysGeometry as tlGeometry } from "./tpl_lib_geom.js";
import { mockTplSysCatalogue as tlMockCat, mockTplSysNormalize as tlMockNorm, mockTplSysBuild as tlMockBuild, mockTplSysHas as tlMockHas } from "./mock_tpl_library.js";
import { mockTemplate as tlMockSlab, mockTemplateCatalogue as tlMockSlabCat } from "./mock_wbi.js";
import { UX_TEMPLATES as tlUxTemplates } from "./ux_start.js";

const CATS = ["Steel", "Concrete", "Tall building", "Special", "Slab systems"];
const SLAB_IDS = ["waffle", "ribbed", "two_way_beams", "staggered_truss", "flat_slab_drop"];
const SLAB_INFO = {
  waffle: ["Columns with a two-way waffle slab modelled as one equivalent ribbed shell per bay.", "Waffle Slab"],
  ribbed: ["Beams on every grid line and a one-way joist (ribbed) slab, ribs parallel to X.", "Two-Way / Ribbed Slab"],
  two_way_beams: ["RC moment frames on every grid line with a solid two-way slab.", "Flat Slab with Perimeter Beams / Two-Way"],
  staggered_truss: ["Steel staggered-truss system: story-deep transverse trusses on alternate lines in alternate stories.", "Staggered Truss"],
  flat_slab_drop: ["RC flat slab with thickened drop panels at the columns (slab-column frames).", "Flat Slab (drop panels)"],
};
const SLAB_SECT = {
  column: "Column size", beam_b: "Beam width", beam_h: "Beam depth", depth: "Overall depth D", top: "Top slab tf", rib_width: "Rib width bw",
  rib_spacing: "Rib spacing s", slab: "Slab thickness", drop_thickness: "Drop panel extra depth", drop_width: "Drop panel width", panels: "Truss panels",
};
const GROUPS = [["geometry", "Grid and Stories"], ["system", "Structural System"], ["sections", "Sections"], ["materials", "Materials and Units"],
  ["loads", "Loads and Mass Source"], ["code", "Code Loads (ETABS template defaults)"]];
const CODE_OPTS = [["asce7", "ASCE 7-22 (ELF + two-period spectrum, LRFD combos)"], ["ec8", "Eurocode 8 (EN 1990 combos)"],
  ["is1893", "IS 1893:2016 (IS 456 combos)"], ["none", "None (gravity cases only)"]];
const CODE_KEYS = { asce7: ["SDS", "SD1", "R", "Ie"], ec8: ["ag", "q", "ground_type"], is1893: ["Z", "R", "Ie", "soil"], none: [] };
const CODE_LBL = { SDS: "SDS (g)", SD1: "SD1 (g)", R: "R", Ie: "Importance Ie", ag: "agR (g)", q: "Behaviour factor q", ground_type: "Ground type", Z: "Zone factor Z", soil: "Soil type" };
const CHOICE_LBL = {
  ground_type: "ABCDE".split("").map(x => [x, "Ground type " + x]),
  soil: [["I", "Type I (rock)"], ["II", "Type II (medium)"], ["III", "Type III (soft)"]],
  material_standard: [["astm_aci", "US (ASTM / ACI)"], ["eurocode", "Eurocode"], ["is", "India (IS)"], ["bs", "UK (BS)"], ["as_nzs", "AUS / NZ"]],
  steel_db: [["aisc", "AISC v16"], ["eurocode", "Eurocode (EN 10365)"], ["is808", "IS 808"], ["bs", "BS (Blue Book)"], ["as_nzs", "AS/NZS"]],
};

/** Metadata for the five slab templates (their catalogue has no `meta`). */
function slabMeta(ent) {
  const ints = new Set(["bays_x", "bays_y", "stories", "panels"]);
  const out = [];
  for (const k of Object.keys(ent.params)) {
    let kind = "num", unit = "none", group = "code";
    if (k === "name") { kind = "str"; group = "geometry"; }
    else if (["bays_x", "bays_y", "bay_x", "bay_y", "stories", "story_height", "first_story_height"].includes(k)) { group = "geometry"; if (!ints.has(k)) { kind = "len"; unit = "length"; } }
    else if (k in SLAB_SECT) { group = "sections"; if (!ints.has(k)) { kind = "len"; unit = "length"; } }
    else if (["sdl", "live", "live_mass_factor"].includes(k)) { group = "loads"; unit = k === "live_mass_factor" ? "none" : "pressure"; }
    else if (k === "mesh_size") { kind = "len"; unit = "length"; group = "advanced"; }
    else if (["code", "ground_type", "soil"].includes(k)) kind = "choice";
    if (ints.has(k)) kind = "int";
    const lbl = { bays_x: "Bays along X", bays_y: "Bays along Y", bay_x: "Bay width X", bay_y: "Bay width Y", stories: "Stories", story_height: "Typical story height",
      first_story_height: "First story height", sdl: "Superimposed dead (in DEAD)", live: "Live (LIVE)", live_mass_factor: "Live mass factor", mesh_size: "Shell mesh size", name: "Model name" }[k] || SLAB_SECT[k] || k;
    out.push({ key: k, kind, unit, group, label: lbl, ...(ints.has(k) ? { min: 1, max: k === "stories" ? 60 : 20 } : {}) });
  }
  return out;
}

/* ------------------------------------------------------------ sketches */
const COLORS = { column: "var(--c-column,#5f8fc9)", beam: "var(--c-beam,#8796aa)", brace: "var(--c-brace,#c98500)", link: "#d0663a" };

/** SVG sketch of a model-like {members, shells, links}: "iso" (3-D) or
    "plan". Lines are merged into one path per object kind. */
export function tplSketch(geo, { mode = "iso", w = 120, h = 80, pad = 6, cls = "tl-sketch" } = {}) {
  const proj = mode === "plan" ? p => [p[0], -p[1]] : p => [(p[0] - p[1]) * 0.866, (p[0] + p[1]) * 0.5 - p[2]];
  const pts = [];
  for (const m of geo.members || []) pts.push(proj(m.pi), proj(m.pj));
  for (const s of geo.shells || []) for (const c of s.corners) pts.push(proj(c));
  for (const l of geo.links || []) pts.push(proj(l.pi), proj(l.pj));
  if (!pts.length) return `<svg viewBox="0 0 ${w} ${h}" class="${cls}" aria-hidden="true"></svg>`;
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const [x, y] of pts) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  const sc = Math.min((w - 2 * pad) / Math.max(x1 - x0, 1e-6), (h - 2 * pad) / Math.max(y1 - y0, 1e-6));
  const ox = (w - sc * (x1 - x0)) / 2 - sc * x0, oy = (h - sc * (y1 - y0)) / 2 - sc * y0;
  const P = p => { const [x, y] = proj(p); return `${(ox + sc * x).toFixed(1)} ${(oy + sc * y).toFixed(1)}`; };
  const paths = { column: "", beam: "", brace: "", link: "" };
  let slabs = "", walls = "", cols = "";
  const seen = new Set();
  for (const s of geo.shells || []) {
    if (mode === "plan" && s.kind === "wall") { walls += `M${P(s.corners[0])}L${P(s.corners[1])}`; continue; }
    const d = "M" + s.corners.map(P).join("L") + "Z";
    if (s.kind === "wall") walls += d; else if (mode === "iso" || !seen.has(d)) { slabs += d; seen.add(d); }
  }
  for (const m of geo.members || []) {
    const k = m.kind === "column" ? "column" : m.kind === "brace" ? "brace" : "beam";
    if (mode === "plan" && k === "column" && Math.abs(m.pi[0] - m.pj[0]) < 1e-6 && Math.abs(m.pi[1] - m.pj[1]) < 1e-6) {
      const key = P(m.pi);
      if (!seen.has(key)) { seen.add(key); const [cx, cy] = key.split(" ").map(Number); cols += `M${(cx - 1.4).toFixed(1)} ${(cy - 1.4).toFixed(1)}h2.8v2.8h-2.8Z`; }
      continue;
    }
    paths[k] += `M${P(m.pi)}L${P(m.pj)}`;
  }
  for (const l of geo.links || []) paths.link += `M${P(l.pi)}L${P(l.pj)}`;
  const sw = mode === "plan" ? 0.9 : 0.8;
  let g = "";
  if (slabs) g += `<path d="${slabs}" fill="rgba(154,167,180,${mode === "plan" ? ".10" : ".16"})" stroke="rgba(154,167,180,.35)" stroke-width=".4"/>`;
  if (walls) g += mode === "plan" ? `<path d="${walls}" stroke="var(--accent,#35b5e5)" stroke-width="2.6" fill="none"/>`
    : `<path d="${walls}" fill="rgba(53,181,229,.30)" stroke="var(--accent,#35b5e5)" stroke-width=".5"/>`;
  g += `<path d="${paths.beam}" stroke="${COLORS.beam}" stroke-width="${sw}" fill="none"/>`;
  g += `<path d="${paths.column}" stroke="${COLORS.column}" stroke-width="${sw * 1.4}" fill="none"/>`;
  g += `<path d="${paths.brace}" stroke="${COLORS.brace}" stroke-width="${sw * 1.2}" fill="none"/>`;
  if (paths.link) g += `<path d="${paths.link}" stroke="${COLORS.link}" stroke-width="${sw * 2.4}" fill="none"/>`;
  if (cols) g += `<path d="${cols}" fill="${COLORS.column}"/>`;
  return `<svg viewBox="0 0 ${w} ${h}" class="${cls}" aria-hidden="true">${g}</svg>`;
}

export function initTplLibrary(sky) {
  if (!sky || !sky.store) return;
  if (!document.querySelector("link[data-tpl-lib-css]")) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/static/tpl_library.css"; l.dataset.tplLibCss = "1";
    document.head.appendChild(l);
  }
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 5000) => sky.toast && sky.toast(t, m, k, ms);
  // captured now: a later init (js/wb_links.js) replaces the sky.wb object
  const wbTemplates = sky.wb && sky.wb.templates;
  let catalogue = null;

  async function loadCatalogue() {
    if (catalogue) return catalogue;
    let cat = null;
    if (!S.mock) {
      try { const r = await fetch("/api/model/templates"); if (r.ok) cat = await r.json(); } catch { cat = null; }
    }
    if (!Array.isArray(cat)) cat = [...tlMockSlabCat(), ...tlMockCat()];
    catalogue = cat.map(c => {
      const sys = !!c.system;
      const info = SLAB_INFO[c.id] || [];
      return { ...c, category: sys ? c.category : "Slab systems", description: sys ? c.description : (info[0] || ""), etabs: sys ? c.etabs : (info[1] || ""),
        meta: c.meta || slabMeta(c), system: sys };
    });
    return catalogue;
  }

  /** Params to send: drop hidden keys (the backend derives them). */
  const sendable = (ent, p) => {
    const out = {};
    for (const m of ent.meta) if (!m.hidden && p[m.key] !== undefined) out[m.key] = p[m.key];
    return out;
  };

  /** Model-like geometry for the sketch + counts (throws on bad params). */
  function geometry(ent, p) {
    const prm = sendable(ent, p);
    if (ent.system) {
      const np = tlMockNorm(ent.id, prm);
      const meta = Object.fromEntries(ent.meta.map(m => [m.key, m]));
      return tlGeometry(ent.id, np, key => np[key] || meta[key].aisc_default);
    }
    return tlMockSlab(ent.id, prm).model;
  }

  async function build(id, params) {
    const cat = await loadCatalogue();
    const ent = cat.find(c => c.id === id);
    if (!ent) throw new Error("Unknown template " + id);
    const prm = sendable(ent, params);
    if (S.mock) return ent.system || tlMockHas(id) ? tlMockBuild(id, prm) : tlMockSlab(id, prm);
    const res = await fetch("/api/model/template", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ template: id, params: prm }) });
    let d = null;
    try { d = await res.json(); } catch { /* not json */ }
    if (!res.ok) throw new Error((d && d.error) || `${res.status} ${res.statusText}`);
    return d;
  }

  async function create(id, params) {
    const r = await build(id, params);
    const md = S.mock ? r.model : (sky.keepSetup ? sky.keepSetup(r.model) : r.model);
    if (params.units) md.display_units = params.units;
    sky.adoptModel(md, null);
    sky.markDirty();
    await sky.saveModel();
    if (sky.etabs && sky.etabs.showResult) sky.etabs.showResult("view3d");
    try { sky.viewer && sky.viewer.fit && sky.viewer.fit(); } catch { /* ignore */ }
    const sm = r.summary || {};
    const extra = [sm.piers && sm.piers.length ? `piers ${sm.piers.join(" ")}` : "", sm.links ? `${sm.links} isolators` : "", sm.towers && sm.towers.length ? `towers ${sm.towers.join(", ")}` : ""].filter(Boolean).join(" · ");
    toast("New model from template", `${sm.system || id}: ${sm.members} frames · ${sm.shells} shells · ${sm.stories} stories · ${(sm.combos || []).length} combos${extra ? " · " + extra : ""} — press Run (F5)`);
    return r;
  }

  const thumbCache = {};
  function cardThumb(ent) {
    if (thumbCache[ent.id]) return thumbCache[ent.id];
    let svg = "";
    try {
      const p = { ...ent.params };
      if (p.stories > 6) p.stories = 6;
      if (p.tower_stories > 4) p.tower_stories = 4;
      if (ent.id === "outrigger") p.outrigger_levels = "3,6";
      svg = tplSketch(geometry(ent, p), { w: 84, h: 60, cls: "tl-thumb" });
    } catch { svg = `<svg viewBox="0 0 84 60" class="tl-thumb" aria-hidden="true"></svg>`; }
    thumbCache[ent.id] = svg;
    return svg;
  }

  async function open(id0) {
    const cat = await loadCatalogue();
    const V = {};
    const units = tlU.getUnits ? tlU.getUnits() : "kN-m";
    for (const c of cat) { V[c.id] = { ...c.params }; if ("units" in V[c.id]) V[c.id].units = units; }
    let cur = cat.some(c => c.id === id0) ? id0 : (cat.find(c => c.system) || cat[0]).id;
    let filter = "All";
    const unitsSet = new Set();          // templates whose units the user picked
    const F = tlForm();
    const list = tlEl("div", { class: "tl-list", role: "radiogroup", "aria-label": "Template", id: "tlList" });
    const filt = tlEl("div", { class: "tl-filter", role: "tablist", "aria-label": "Category" });
    const prevIso = tlEl("div", { class: "tl-prev-iso", id: "tlPrevIso" });
    const prevPlan = tlEl("div", { class: "tl-prev-plan", id: "tlPrevPlan" });
    const stats = tlEl("div", { class: "tl-stats muted", id: "tlStats", "aria-live": "polite" });
    const desc = tlEl("div", { class: "tl-desc", id: "tlDesc" });
    const form = tlEl("div", { class: "tl-form", id: "tlForm" });
    const right = tlEl("div", { class: "tl-right" }, [tlEl("div", { class: "tl-prev" }, [prevIso, prevPlan]), stats, desc, form]);
    const left = tlEl("div", { class: "tl-left" }, [filt, list]);
    const ent = () => cat.find(c => c.id === cur);

    let pend = 0;
    const schedulePreview = () => { cancelAnimationFrame(pend); pend = requestAnimationFrame(renderPreview); };
    function renderPreview() {
      const e = ent();
      try {
        const g = geometry(e, V[cur]);
        prevIso.innerHTML = tplSketch(g, { mode: "iso", w: 300, h: 200, pad: 10, cls: "tl-sketch" });
        prevPlan.innerHTML = tplSketch(g, { mode: "plan", w: 150, h: 200, pad: 10, cls: "tl-sketch" });
        const nst = (g.stories || []).length, nl = (g.links || []).length;
        stats.classList.remove("is-err");
        stats.textContent = `${nst} stories · ${(g.members || []).length} frames · ${(g.shells || []).length} areas${nl ? ` · ${nl} isolator links` : ""}`
          + ((g.towers || []).length ? ` · towers ${(g.towers || []).map(t => t.name).join(", ")}` : "");
      } catch (err) {
        stats.classList.add("is-err");
        stats.textContent = String(err.message || err).replace(/^[a-z_0-9]+: /, "");
      }
    }

    function renderFilter() {
      filt.textContent = "";
      for (const c of ["All", ...CATS]) {
        if (c !== "All" && !cat.some(t => t.category === c)) continue;
        const b = tlEl("button", { type: "button", class: "tl-chip" + (c === filter ? " is-on" : ""), role: "tab", "aria-selected": String(c === filter), "data-cat": c }, [c]);
        b.addEventListener("click", () => { filter = c; renderFilter(); renderList(); });
        filt.appendChild(b);
      }
    }
    function renderList() {
      list.textContent = "";
      for (const c of cat) {
        if (filter !== "All" && c.category !== filter) continue;
        const b = tlEl("button", { type: "button", class: "tl-card" + (c.id === cur ? " is-sel" : ""), role: "radio", "aria-checked": String(c.id === cur), "data-tpl": c.id,
          title: `${c.label}\nETABS: ${c.etabs}` });
        b.innerHTML = cardThumb(c) + `<span class="tl-card-txt"><b>${tlEsc(c.label)}</b><span class="muted">${tlEsc(c.category)}${c.etabs ? " · " + tlEsc(c.etabs) : ""}</span></span>`;
        b.addEventListener("click", () => { cur = c.id; renderList(); renderRight(); });
        b.addEventListener("dblclick", () => { cur = c.id; dlg.ok.click(); });
        list.appendChild(b);
      }
    }

    function field(m, p) {
      const o = { label: m.label, id: "tl_" + m.key };
      if (m.kind === "int") return F.num(p, m.key, { ...o, int: true, min: m.min, max: m.max, onChange: schedulePreview });
      if (m.kind === "len" || m.kind === "num") {
        const nullable = ["first_story_height", "mesh_size", "drop_width"].includes(m.key);
        return F.num(p, m.key, { ...o, kind: m.unit && m.unit !== "none" ? m.unit : undefined, min: m.min, max: m.max, allowNull: nullable,
          nullText: m.key === "first_story_height" ? "typical" : m.key === "drop_width" ? "bay/3" : "auto", onChange: schedulePreview });
      }
      if (m.kind === "choice") {
        const opts = m.key === "code" ? CODE_OPTS : (CHOICE_LBL[m.key] || (m.options || []).map(x => [x, x]));
        return F.select(p, m.key, opts, { ...o, onChange: () => { if (m.key === "code") renderCode(); if (m.key === "units") unitsSet.add(cur); schedulePreview(); } });
      }
      if (m.kind === "steel") return F.text(p, m.key, { ...o, required: false, placeholder: m.aisc_default + (p.steel_db && p.steel_db !== "aisc" ? " (closest)" : ""), onChange: schedulePreview });
      if (m.key === "steel_material" || m.key === "concrete_material") return F.text(p, m.key, { ...o, required: false, placeholder: "library default", onChange: schedulePreview });
      return F.text(p, m.key, { ...o, required: m.key !== "name", onChange: schedulePreview });
    }

    let codeBox = null;
    function renderCode() {
      if (!codeBox) return;
      const e = ent(), p = V[cur];
      F.forget(codeBox);
      codeBox.textContent = "";
      const keys = CODE_KEYS[p.code] || [];
      if (!keys.length) { codeBox.appendChild(tlNote("No seismic patterns, spectrum or combinations.")); return; }
      for (const k of keys) {
        const m = e.meta.find(x => x.key === k);
        if (!m) continue;
        codeBox.appendChild(["ground_type", "soil"].includes(k) ? F.select(p, k, CHOICE_LBL[k], { label: CODE_LBL[k], id: "tl_" + k })
          : F.num(p, k, { label: CODE_LBL[k] || m.label, id: "tl_" + k, gt: k === "SD1" ? undefined : 0, min: k === "SD1" ? 0 : undefined }));
      }
    }

    function renderRight() {
      const e = ent(), p = V[cur];
      slabBtn.hidden = !SLAB_IDS.includes(cur);
      desc.innerHTML = `<b>${tlEsc(e.label)}</b> <span class="tl-etabs">ETABS: ${tlEsc(e.etabs || "—")}</span><p>${tlEsc(e.description || "")}</p>`;
      F.forget(form);
      form.textContent = "";
      const byGroup = {};
      for (const m of e.meta) {
        if (m.hidden) continue;
        if (m.group === "code" && m.key !== "code") continue;
        (byGroup[m.group] = byGroup[m.group] || []).push(m);
      }
      for (const [g, legend] of GROUPS) {
        const ms = byGroup[g];
        if (!ms || !ms.length) continue;
        const rows = ms.map(m => field(m, p));
        if (g === "code") { codeBox = tlEl("div", { class: "tl-code-params" }); rows.push(codeBox); }
        const note = g === "code" ? "Sets EQX / EQY (auto lateral), function RSF + RSX / RSY, the mass source and the default design combinations."
          : g === "sections" && e.system && e.meta.some(m => m.kind === "steel") ? "Steel shapes by database name (blank = the default shown)." : undefined;
        form.appendChild(tlGroup(legend, rows, { cols: 2, note }));
      }
      renderCode();
      const adv = (byGroup.advanced || []).map(m => field(m, p));
      if (adv.length) form.appendChild(tlAdv(adv, { summary: "Advanced — meshing", countFn: () => (p.mesh_size != null ? 1 : 0) }));
      schedulePreview();
    }

    const slabBtn = tlEl("button", { type: "button", class: "btn btn-small", id: "tlSlabForm", title: "The Wave B slab-template form (same backend generator)" }, ["Slab Template Form…"]);
    slabBtn.addEventListener("click", () => {
      const id = cur;
      dlg.close();
      if (wbTemplates && wbTemplates.open) wbTemplates.open(id);
    });
    renderFilter();
    renderList();
    renderRight();
    const dirty = S.dirty ? "Unsaved edits to the current model will be discarded." : "";
    const dlg = tlDialog({
      id: "tplLibDlg", title: "Template Library", iconId: "ux-start", size: "xl", form: F,
      intro: "Structural-system templates modelled on the ETABS New Model templates and example buildings — grids, stories, database sections, loads, mass source, diaphragms, code ELF + RS cases, combinations and pier / spandrel labels.",
      body: [tlEl("div", { class: "tl-wrap" }, [left, right])], okLabel: "Create", footNote: dirty, extra: [slabBtn],
      onUnits: () => {
        // the model's display units follow the working units until the
        // user picks them explicitly in "Materials and Units"
        const u = tlU.getUnits ? tlU.getUnits() : null;
        if (u) for (const c of cat) if ("units" in V[c.id] && !unitsSet.has(c.id)) V[c.id].units = u;
        F.refresh(); schedulePreview();
      },
      onOk: async () => {
        try { geometry(ent(), V[cur]); } catch (err) { return "Check the parameters: " + err.message; }
        try { await create(cur, { ...V[cur] }); } catch (err) { return "Could not create the model: " + err.message; }
        return true;
      },
    });
    return dlg;
  }

  // File › New Model from Template… (file-wb-template, js/wb_templates.js)
  // opens this library: it is a superset (the five slab templates + the 14
  // systems) and the File panel is at its 14-entry limit, so no new row is
  // added. The Wave B form stays one click away (footer "Slab Template
  // Form…") and as __sky.wb.templates.open().
  const wbItem = sky.menu && sky.menu.item ? sky.menu.item("file-wb-template") : null;
  if (wbItem) {
    wbItem.fn = () => open();
    wbItem.hint = "19 templates · structural systems + slab systems";
    try { sky.menu.refresh && sky.menu.refresh(); } catch { /* ignore */ }
  }
  else if (sky.menu) sky.menu.add("File", { label: "New Model from Template…", act: "file-tpl-library", hint: "19 templates · structural systems + slab systems", fn: () => open(), before: "file-open" });

  // start-screen cards (js/ux_start.js: card.build + card.thumb)
  const START = tlMockCat();
  for (const c of START) {
    const sid = "tpl-" + c.id;
    if (tlUxTemplates.some(t => t.id === sid)) continue;
    const p = c.params, hideY = (c.meta.find(m => m.key === "bays_y") || {}).hidden;
    tlUxTemplates.push({ id: sid, name: c.label, sub: `${c.category.toLowerCase()} · code loads + RS`, etabs: c.etabs,
      def: { baysX: p.bays_x, baysY: p.bays_y, sx: p.bay_x, sy: p.bay_y, stories: p.stories, h: p.story_height }, noY: !!hideY,
      thumb: () => cardThumb({ ...c, system: true }).replace("tl-thumb", "ux-tpl-thumb"),
      build: async (_sky, v) => {
        const prm = { ...p, name: v.name, bays_x: v.baysX, bay_x: v.sx, stories: v.stories, story_height: v.h };
        if (!hideY) { prm.bays_y = v.baysY; prm.bay_y = v.sy; }
        if (c.id === "industrial_shed") { prm.bays_x = 1; prm.stories = 1; prm.bays_y = v.baysY; prm.bay_y = v.sy; }
        const r = await build(c.id, prm);
        return S.mock ? r.model : (sky.keepSetup ? sky.keepSetup(r.model) : r.model);
      } });
  }
  sky.tplLib = { open, build, create, catalogue: loadCatalogue, sketch: tplSketch, ids: START.map(c => c.id) };
}

export default { initTplLibrary, tplSketch };
