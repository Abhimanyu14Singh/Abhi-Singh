/* SkyFrame Wave B — File › New Model from Template… (ETABS File › New Model
   structural templates).

   Waffle slab · ribbed (one-way joist) slab · two-way slab with beams ·
   staggered truss · flat slab with drop panels. Each template also sets up
   the code load patterns (DEAD with self weight + SDL, LIVE, EQX/EQY from
   the auto-lateral generator), a code response-spectrum function with
   RSX/RSY cases, the mass source and the code's default combinations —
   all done by the backend generators (POST /api/model/template,
   skyframe/core/templates.py); ?mock=1 runs js/mock_wb.js.
   Every length is typed in display units (js/units.js); the model stays SI.
   The five templates also appear as cards on the start screen
   (js/ux_start.js hook: a card with its own build()). */

import { el as tEl, dkForm as tForm, dkGroup as tGroup, dkAdvanced as tAdv, dkDialog as tDialog, dkNote as tNote, esc as tEsc } from "./dlgkit.js";
import { UX_TEMPLATES as tUxTemplates } from "./ux_start.js";
import { mockTemplate as tMockTemplate, mockTemplateCatalogue as tMockCatalogue } from "./mock_wb.js";

const ORDER = ["waffle", "ribbed", "two_way_beams", "staggered_truss", "flat_slab_drop"];
const SUB = {
  waffle: "columns + two-way waffle slab (equivalent ribbed shell)",
  ribbed: "beams on grid lines + one-way joist slab (ribs ∥ X)",
  two_way_beams: "beams on every grid line + solid two-way slab",
  staggered_truss: "steel · story-deep trusses on alternate lines / floors",
  flat_slab_drop: "flat slab + thickened drop panels at columns",
};
const ETABS = { waffle: "Waffle Slab", ribbed: "Two-Way / Ribbed Slab", two_way_beams: "Flat Slab with Perimeter Beams / Two-Way",
  staggered_truss: "Staggered Truss", flat_slab_drop: "Flat Slab (drop panels)" };
const CODES = [["asce7", "ASCE 7-22 (ELF + two-period spectrum, LRFD combos)"], ["ec8", "Eurocode 8 (EN 1990 combos)"],
  ["is1893", "IS 1893:2016 (IS 456 combos)"], ["none", "None (DEAD / LIVE only)"]];
const SECT = {
  waffle: [["column", "Column size"], ["depth", "Overall depth D"], ["top", "Top slab tf"], ["rib_width", "Rib width bw"], ["rib_spacing", "Rib spacing s"]],
  ribbed: [["column", "Column size"], ["beam_b", "Beam width"], ["beam_h", "Beam depth"], ["depth", "Overall depth D"], ["top", "Top slab tf"],
    ["rib_width", "Rib width bw"], ["rib_spacing", "Rib spacing s"]],
  two_way_beams: [["column", "Column size"], ["beam_b", "Beam width"], ["beam_h", "Beam depth"], ["slab", "Slab thickness"]],
  staggered_truss: [],
  flat_slab_drop: [["column", "Column size"], ["slab", "Slab thickness"], ["drop_thickness", "Drop panel extra depth"], ["drop_width", "Drop panel width"]],
};

function thumb(id) {
  const c = "var(--c-column,#5f8fc9)", b = "var(--c-beam,#77879b)", s = "rgba(154,167,180,.55)", br = "var(--c-brace,#c98500)";
  let g = "";
  if (id === "staggered_truss") {
    for (let i = 0; i < 3; i++) g += `<line x1="${8 + i * 24}" y1="8" x2="${8 + i * 24}" y2="52" stroke="${c}" stroke-width="1.6"/>`;
    for (let k = 0; k < 4; k++) { const y0 = 52 - k * 11, y1 = y0 - 11, x0 = k % 2 ? 8 : 32, x1 = x0 + 24;
      g += `<line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y0}" stroke="${b}"/><line x1="${x0}" y1="${y1}" x2="${x1}" y2="${y1}" stroke="${b}"/><line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}" stroke="${br}" stroke-width="1.3"/>`; }
  } else {
    for (let k = 1; k <= 3; k++) g += `<rect x="4" y="${52 - k * 14 - 1}" width="64" height="${id === "flat_slab_drop" ? 2 : 3}" fill="${s}"/>`;
    for (let i = 0; i < 3; i++) g += `<line x1="${8 + i * 28}" y1="10" x2="${8 + i * 28}" y2="52" stroke="${c}" stroke-width="1.6"/>`;
    if (id === "flat_slab_drop") for (let k = 1; k <= 3; k++) for (let i = 0; i < 3; i++) g += `<rect x="${3 + i * 28}" y="${52 - k * 14 + 1}" width="10" height="3" fill="${s}"/>`;
    if (id === "waffle" || id === "ribbed") for (let k = 1; k <= 3; k++) for (let j = 0; j < 9; j++) g += `<rect x="${7 + j * 7}" y="${52 - k * 14 + 2}" width="2" height="3" fill="${s}"/>`;
    if (id === "two_way_beams" || id === "ribbed") for (let k = 1; k <= 3; k++) g += `<line x1="4" y1="${52 - k * 14 + 3}" x2="68" y2="${52 - k * 14 + 3}" stroke="${b}" stroke-width="2"/>`;
  }
  return `<svg viewBox="0 0 72 56" class="wb-tpl-thumb" aria-hidden="true">${g}<line x1="2" y1="53" x2="70" y2="53" stroke="var(--text-3,#66727f)"/></svg>`;
}

export function initWbTemplates(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 5000) => sky.toast && sky.toast(t, m, k, ms);
  let catalogue = null;

  async function loadCatalogue() {
    if (catalogue) return catalogue;
    if (S.mock) catalogue = tMockCatalogue();
    else {
      try { catalogue = await (await fetch("/api/model/templates")).json(); }
      catch { catalogue = tMockCatalogue(); }
    }
    return catalogue;
  }

  /** Build a template → {model, summary} (live: POST /api/model/template). */
  async function build(id, params) {
    if (S.mock) return tMockTemplate(id, params);
    const res = await fetch("/api/model/template", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template: id, params }) });
    let d = null;
    try { d = await res.json(); } catch { /* not json */ }
    if (!res.ok) throw new Error((d && d.error) || `${res.status} ${res.statusText}`);
    return d;
  }

  async function create(id, params) {
    const r = await build(id, params);
    const md = S.mock ? r.model : (sky.keepSetup ? sky.keepSetup(r.model) : r.model);
    sky.adoptModel(md, null);
    sky.markDirty();
    await sky.saveModel();                      // the backend holds exactly the adopted model
    if (sky.etabs && sky.etabs.showResult) sky.etabs.showResult("view3d");
    try { sky.viewer && sky.viewer.fit && sky.viewer.fit(); } catch { /* ignore */ }
    const sm = r.summary || {};
    toast("New model from template", `${sm.members} frames · ${sm.shells} shells · ${sm.stories} stories · ${(sm.combos || []).length} combos · RS ${(sm.rs_cases || []).join(", ") || "—"} — press Run (F5)`);
    return r;
  }

  async function open(id0 = "waffle") {
    const cat = await loadCatalogue();
    const V = {};
    for (const c of cat) V[c.id] = { ...c.params };
    let cur = ORDER.includes(id0) ? id0 : "waffle";
    const F = tForm();
    const cards = tEl("div", { class: "wb-tpl-cards", role: "radiogroup", "aria-label": "Template", id: "wbTplCards" });
    const form = tEl("div", { class: "wb-tpl-form", id: "wbTplForm" });
    const renderCards = () => {
      cards.textContent = "";
      for (const t of ORDER) {
        const b = tEl("button", { type: "button", class: "wb-tpl-card" + (t === cur ? " is-sel" : ""), role: "radio", "aria-checked": String(t === cur),
          "data-tpl": t, title: `ETABS: ${ETABS[t]}` });
        b.innerHTML = thumb(t) + `<span><b>${tEsc((cat.find(c => c.id === t) || {}).label || t)}</b><span class="muted">${tEsc(SUB[t])}</span></span>`;
        b.addEventListener("click", () => { cur = t; renderCards(); renderForm(); });
        cards.appendChild(b);
      }
    };
    const renderForm = () => {
      F.forget(form);
      form.textContent = "";
      const p = V[cur];
      const L = "length";
      const geo = tGroup("Grid and Stories", [
        F.text(p, "name", { label: "Model name", id: "wbTplName" }),
        F.num(p, "bays_x", { label: "Bays along X", int: true, min: 1, max: 20, id: "wbTplBaysX" }),
        F.num(p, "bay_x", { label: "Bay width X", kind: L, gt: 0, id: "wbTplBayX" }),
        F.num(p, "bays_y", { label: "Bays along Y", int: true, min: 1, max: 20, id: "wbTplBaysY" }),
        F.num(p, "bay_y", { label: cur === "staggered_truss" ? "Building width Y (truss span)" : "Bay width Y", kind: L, gt: 0, id: "wbTplBayY" }),
        F.num(p, "stories", { label: "Stories", int: true, min: cur === "staggered_truss" ? 2 : 1, max: 60, id: "wbTplStories" }),
        F.num(p, "story_height", { label: "Typical story height", kind: L, gt: 0, id: "wbTplH" }),
        F.num(p, "first_story_height", { label: "First story height", kind: L, gt: 0, allowNull: true, nullText: "typical", id: "wbTplH1" }),
      ], { cols: 2 });
      const secRows = SECT[cur].map(([k, lbl]) => F.num(p, k, { label: lbl, kind: L, gt: 0, allowNull: k === "drop_width", nullText: "bay/3", id: "wbTpl_" + k }));
      if (cur === "staggered_truss") secRows.push(F.num(p, "panels", { label: "Truss panels", int: true, min: 2, max: 12, id: "wbTpl_panels" }),
        tNote("A992 W shapes: columns W14x90, spandrels / floor beams W18x50, chords W10x33, web members W8x31; 150 mm one-way deck."));
      const sect = tGroup("Sections", secRows, { cols: 2 });
      const loads = tGroup("Loads and Mass Source", [
        F.num(p, "sdl", { label: "Superimposed dead (in DEAD)", kind: "pressure", min: 0, id: "wbTplSdl" }),
        F.num(p, "live", { label: "Live (LIVE)", kind: "pressure", min: 0, id: "wbTplLive" }),
        F.num(p, "live_mass_factor", { label: "Live mass factor", min: 0, max: 1, id: "wbTplLmf" }),
      ], { cols: 2 });
      const codeBox = tEl("div", { class: "wb-code-params" });
      const renderCode = () => {
        F.forget(codeBox);
        codeBox.textContent = "";
        const rows = p.code === "asce7" ? [F.num(p, "SDS", { label: "SDS (g)", gt: 0 }), F.num(p, "SD1", { label: "SD1 (g)", min: 0 }), F.num(p, "R", { label: "R", gt: 0 }), F.num(p, "Ie", { label: "Ie", gt: 0 })]
          : p.code === "ec8" ? [F.num(p, "ag", { label: "agR (g)", gt: 0 }), F.num(p, "q", { label: "Behaviour factor q", gt: 0 }), F.select(p, "ground_type", "ABCDE".split("").map(x => [x, "Ground type " + x]), { label: "Ground type" })]
          : p.code === "is1893" ? [F.num(p, "Z", { label: "Zone factor Z", gt: 0 }), F.num(p, "R", { label: "R", gt: 0 }), F.num(p, "Ie", { label: "Importance I", gt: 0 }),
            F.select(p, "soil", [["I", "Type I (rock)"], ["II", "Type II (medium)"], ["III", "Type III (soft)"]], { label: "Soil" })] : [tNote("No seismic patterns, spectrum or combinations.")];
        rows.forEach(r => codeBox.appendChild(r));
      };
      const code = tGroup("Code Loads (ETABS template defaults)", [F.select(p, "code", CODES, { label: "Code", id: "wbTplCode", onChange: renderCode }), codeBox],
        { note: "Sets EQX / EQY (auto lateral), function RSF + RSX / RSY, mass source and the default design combinations." });
      renderCode();
      const adv = tAdv([F.num(p, "mesh_size", { label: "Shell mesh size", kind: L, gt: 0, allowNull: true, nullText: "auto (bay/4)", def: null, id: "wbTplMesh" })],
        { summary: "Advanced — meshing", countFn: () => (p.mesh_size != null ? 1 : 0) });
      form.append(geo, sect, loads, code, adv);
    };
    renderCards();
    renderForm();
    const dirty = S.dirty ? "Unsaved edits to the current model will be discarded." : "";
    return tDialog({
      id: "wbTplDlg", title: "New Model from Template", iconId: "ux-start", size: "lg", form: F,
      intro: "ETABS structural templates with code load patterns, response spectrum, mass source and default combinations.",
      body: [cards, form], okLabel: "Create", footNote: dirty,
      onUnits: () => F.refresh(),
      onOk: async () => {
        try { await create(cur, { ...V[cur] }); }
        catch (e) { return "Could not create the model: " + e.message; }
        return true;
      },
    });
  }

  if (sky.menu) sky.menu.add("File", { label: "New Model from Template…", act: "file-wb-template", hint: "waffle · ribbed · truss · drop panels", fn: () => open(), before: "file-open" });

  // start-screen cards (js/ux_start.js builds these through card.build)
  const DEF = { waffle: { baysX: 3, baysY: 3, sx: 8, sy: 8, stories: 4, h: 3.5 }, ribbed: { baysX: 3, baysY: 3, sx: 8, sy: 8, stories: 4, h: 3.5 },
    two_way_beams: { baysX: 3, baysY: 3, sx: 8, sy: 8, stories: 4, h: 3.5 }, staggered_truss: { baysX: 6, baysY: 1, sx: 6, sy: 18, stories: 6, h: 3 },
    flat_slab_drop: { baysX: 3, baysY: 3, sx: 8, sy: 8, stories: 4, h: 3.5 } };
  const LBL = { waffle: "Waffle Slab", ribbed: "Ribbed Slab", two_way_beams: "Two-Way Slab + Beams", staggered_truss: "Staggered Truss", flat_slab_drop: "Flat Slab + Drop Panels" };
  for (const id of ORDER) {
    if (tUxTemplates.some(t => t.id === "wb-" + id)) continue;
    tUxTemplates.push({ id: "wb-" + id, name: LBL[id], sub: SUB[id] + " · code loads + RS", etabs: ETABS[id] + " template", def: DEF[id],
      noY: false,
      build: async (_sky, p) => {
        const r = await build(id, { name: p.name, bays_x: p.baysX, bays_y: p.baysY, bay_x: p.sx, bay_y: p.sy, stories: Math.max(id === "staggered_truss" ? 2 : 1, p.stories), story_height: p.h });
        return S.mock ? r.model : (sky.keepSetup ? sky.keepSetup(r.model) : r.model);
      } });
  }
  sky.wb.templates = { open, build, create, catalogue: loadCatalogue, ids: ORDER.slice() };
}
