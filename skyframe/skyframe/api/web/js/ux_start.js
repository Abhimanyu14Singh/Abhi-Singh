/* SkyFrame UX — start screen / model templates (File › New, or automatically
   when the working model is empty).
   Five ETABS-style templates (2D frame, 3D steel moment frame, concrete flat
   plate, braced frame, shear-wall core) with bays / spacing / stories / story
   height. Each one is generated through the EXISTING quick-model path
   (POST /api/model/quick, or mock.js mockModel in ?mock=1), then finished
   client-side with the shared modeledit.js builders (addBrace / addWall /
   addSlab / addLibraryFrameSection / eraseElement), adopted with
   __sky.adoptModel and POSTed with __sky.saveModel so the backend runs exactly
   what is shown. All lengths are typed in display units (js/units.js); the
   model stays SI. Recent files come from the saved-model store. */

import { el as uxEl, esc as uxEsc, uxDialog, closeUxDialog, isUxDialogOpen, lsGet as uxLsGet, lsSet as uxLsSet } from "./ux_common.js";
import { mockModel as uxMockModel } from "./mock.js";
import * as uxME from "./modeledit.js";
import uxU from "./units.js";

const LS_MRU = "skyframe.ux.recentFiles";
const LS_LAST = "skyframe.ux.lastTemplate";

export const UX_TEMPLATES = [
  { id: "frame2d", name: "2D Frame", sub: "plane frame · XZ · moment connections", etabs: "Grid Only + 2D (XZ) active DOF",
    def: { baysX: 3, baysY: 1, sx: 6, sy: 6, stories: 4, h: 3.2 }, noY: true },
  { id: "steel3d", name: "3D Steel Moment Frame", sub: "AISC W shapes · A992 · rigid diaphragm", etabs: "Steel Deck template",
    def: { baysX: 3, baysY: 3, sx: 7.5, sy: 7.5, stories: 5, h: 3.8 } },
  { id: "flatplate", name: "Concrete Flat Plate", sub: "columns + 250 mm slabs · no beams", etabs: "Flat Slab template",
    def: { baysX: 3, baysY: 3, sx: 7, sy: 7, stories: 6, h: 3.2 } },
  { id: "braced", name: "Braced Frame", sub: "steel frame + X-braces in perimeter bays", etabs: "Steel Deck + braced bays",
    def: { baysX: 4, baysY: 3, sx: 7, sy: 7, stories: 6, h: 3.8 } },
  { id: "core", name: "Shear-Wall Core", sub: "concrete frame + 300 mm core walls", etabs: "Flat Slab + core walls",
    def: { baysX: 3, baysY: 3, sx: 7, sy: 7, stories: 10, h: 3.2 } },
];

/* small elevation-style thumbnails */
function thumb(id) {
  const W = 120, H = 80, P = 10, nb = 3, ns = 4;
  const dx = (W - 2 * P) / nb, dy = (H - 2 * P) / ns;
  let s = `<svg viewBox="0 0 ${W} ${H}" class="ux-tpl-thumb" aria-hidden="true">`;
  const line = (x1, y1, x2, y2, c, w = 1.3, extra = "") => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${c}" stroke-width="${w}" ${extra}/>`;
  const col = "var(--c-column, #5f8fc9)", beam = "var(--c-beam, #77879b)", brace = "var(--c-brace, #c98500)";
  if (id === "steel3d" || id === "braced") {
    // a little iso box
    const ox = 18, oy = 12;
    for (let j = 0; j <= ns; j++) s += line(P, H - P - j * dy, W - P - ox, H - P - j * dy, beam) + line(P + ox, H - P - j * dy - oy, W - P, H - P - j * dy - oy, beam, 1, 'opacity=".5"');
    for (let i = 0; i <= nb; i++) { const x = P + i * (W - 2 * P - ox) / nb; s += line(x, P + oy, x, H - P, col, 1.5) + line(x + ox, P, x + ox, H - P - oy, col, 1, 'opacity=".5"'); }
    if (id === "braced") {
      const x0 = P + (W - 2 * P - ox) / nb, x1 = P + 2 * (W - 2 * P - ox) / nb;
      for (let j = 0; j < ns; j++) { const y0 = H - P - j * dy, y1 = y0 - dy; s += line(x0, y0, x1, y1, brace, 1.4) + line(x1, y0, x0, y1, brace, 1.4); }
    }
  } else {
    if (id === "flatplate") for (let j = 1; j <= ns; j++) s += `<rect x="${P - 2}" y="${H - P - j * dy - 2}" width="${W - 2 * P + 4}" height="4" fill="rgba(154,167,180,.45)"/>`;
    for (let i = 0; i <= nb; i++) s += line(P + i * dx, P, P + i * dx, H - P, col, 1.5);
    if (id !== "flatplate") for (let j = 1; j <= ns; j++) s += line(P, H - P - j * dy, W - P, H - P - j * dy, beam);
    if (id === "core") s += `<rect x="${P + dx}" y="${P}" width="${dx}" height="${H - 2 * P}" fill="rgba(53,181,229,.22)" stroke="var(--accent,#35b5e5)" stroke-width="1.2"/>`;
  }
  s += line(P - 4, H - P, W - P + 4, H - P, "var(--text-3,#66727f)", 1.2);
  return s + "</svg>";
}

/* ---------------- builders (SI) ---------------- */
const near = (a, b) => Math.abs(a - b) < 1e-6;

function pickLib(lib, prefs) {
  for (const p of prefs) {
    const exact = lib.find(e => e.name === p);
    if (exact) return exact;
    const pre = lib.find(e => e.name.startsWith(p));
    if (pre) return pre;
  }
  return lib[0] || null;
}

function cleanBase(m) {
  // the mock demo model carries showcase objects (wing/radial grids, walls,
  // links, springs); a template starts from the plain frame only
  m.shells = []; m.links = []; m.spring_supports = []; m.line_springs = [];
  if (Array.isArray(m.grid_systems) && m.grid_systems.length > 1) m.grid_systems = m.grid_systems.slice(0, 1);
  m.section_cuts = [];
  for (const p of Object.values(m.patterns || {})) if (p.area_loads) p.area_loads = [];
  const keep = new Set(["column", "beam"]);
  m.members = (m.members || []).filter(mm => keep.has(mm.kind));
  const uids = new Set(m.members.map(x => x.uid));
  for (const p of Object.values(m.patterns || {})) {
    if (p.member_udls) p.member_udls = p.member_udls.filter(u => uids.has(u.member_uid));
    if (p.member_loads) p.member_loads = p.member_loads.filter(u => uids.has(u.member_uid));
  }
  return m;
}

async function toSteel(m, sky) {
  const mat = uxME.addLibraryMaterial(m, uxME.DEFAULT_LIBRARY.find(e => e.material_type === "steel"));
  let lib = [];
  try { lib = (await sky.fetchSectionLibrary()) || []; } catch { lib = []; }
  const colE = pickLib(lib, ["W14x90", "W14x82", "W14x", "W12x"]);
  const beamE = pickLib(lib, ["W18x35", "W18x40", "W18x", "W16x", "W21x"]);
  const braceE = pickLib(lib, ["W8x31", "W8x", "W10x33"]);
  const colN = colE ? uxME.addLibraryFrameSection(m, colE, mat) || colE.name : null;
  const beamN = beamE ? uxME.addLibraryFrameSection(m, beamE, mat) || beamE.name : null;
  const braceN = braceE ? uxME.addLibraryFrameSection(m, braceE, mat) || braceE.name : null;
  if (colN && beamN) {
    for (const mm of m.members) mm.section = mm.kind === "column" ? colN : beamN;
    for (const n of ["COL", "BEAM"]) if (m.sections[n] && !m.members.some(x => x.section === n)) delete m.sections[n];
    const usedMats = new Set([...Object.values(m.sections), ...Object.values(m.shell_sections || {})].map(s => s.material));
    if (m.materials.CONC && !usedMats.has("CONC")) delete m.materials.CONC;
  }
  return { mat, braceN };
}

function plan(m) {
  const g = m.grid || {};
  const xs = (g.x_lines || []).slice().sort((a, b) => a - b);
  const ys = (g.y_lines || []).slice().sort((a, b) => a - b);
  return { xs, ys };
}

export async function buildTemplate(sky, id, p) {
  const S = sky.store;
  const tpl = UX_TEMPLATES.find(t => t.id === id);
  if (!tpl) throw new Error("Unknown template " + id);
  const concrete = id === "flatplate" || id === "core" || id === "frame2d";
  const q = {
    name: p.name || tpl.name, bays_x: p.baysX, bay_width_x: p.sx,
    bays_y: tpl.noY ? 1 : p.baysY, bay_width_y: p.sy,
    stories: p.stories, story_height: p.h,
  };
  if (id === "flatplate") Object.assign(q, { column_size: 0.6 });
  if (id === "core") Object.assign(q, { column_size: 0.6 });
  let m;
  if (S.mock) m = uxMockModel(q);
  else {
    const r = await fetch("/api/model/quick", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(q) });
    let data = null; try { data = await r.json(); } catch { /* not json */ }
    if (!r.ok) throw new Error((data && data.error) || `${r.status} ${r.statusText}`);
    m = data;
  }
  m = cleanBase(uxME.normalizeModel(JSON.parse(JSON.stringify(m))));
  delete m._mock_params;
  const { xs, ys } = plan(m);
  const stories = m.stories.map(s => s.name);

  if (id === "frame2d") {
    const y0 = ys[0] || 0;
    for (const mm of m.members.slice()) if (!near(mm.pi[1], y0) || !near(mm.pj[1], y0)) uxME.eraseElement(m, { type: "member", uid: mm.uid });
    if (m.grid) { m.grid.y_lines = [y0]; if (m.grid.y_labels) m.grid.y_labels = m.grid.y_labels.slice(0, 1); }
    if (Array.isArray(m.grid_systems) && m.grid_systems[0] && m.grid_systems[0].y_lines) {
      m.grid_systems[0].y_lines = [y0]; if (m.grid_systems[0].y_labels) m.grid_systems[0].y_labels = m.grid_systems[0].y_labels.slice(0, 1);
    }
    m.active_dof = uxME.DOF_PRESETS.XZ.dofs.slice();
  }
  if (id === "steel3d" || id === "braced") {
    const { braceN } = await toSteel(m, sky);
    if (id === "braced") {
      const bx = Math.floor((xs.length - 1) / 2), by = Math.floor((ys.length - 1) / 2);
      const pairs = [];
      if (xs.length > 1) for (const y of [ys[0], ys[ys.length - 1]]) pairs.push([{ x: xs[bx], y }, { x: xs[bx + 1], y }]);
      if (ys.length > 1) for (const x of [xs[0], xs[xs.length - 1]]) pairs.push([{ x, y: ys[by] }, { x, y: ys[by + 1] }]);
      for (const st of stories) for (const [a, b] of pairs) {
        for (const [p1, p2] of [[a, b], [b, a]]) {
          const br = uxME.addBrace(m, p1, p2, st);
          if (br && braceN) br.section = braceN;
        }
      }
    }
  }
  if (id === "flatplate") {
    for (const mm of m.members.slice()) if (mm.kind === "beam") uxME.eraseElement(m, { type: "member", uid: mm.uid });
    m.shell_sections.SLAB250 = { name: "SLAB250", material: Object.keys(m.materials)[0], thickness: 0.25 };
    for (const st of stories) for (let i = 0; i + 1 < xs.length; i++) for (let j = 0; j + 1 < ys.length; j++) {
      const sl = uxME.addSlab(m, xs[i], ys[j], xs[i + 1], ys[j + 1], st);
      if (!sl) continue;
      sl.section = "SLAB250";
      // 2×2 per bay: a ≤1 m mesh makes the live solve 20–30× slower for ~5 % on T1
      sl.mesh_size = Math.max(1, Math.min(xs[i + 1] - xs[i], ys[j + 1] - ys[j]) / 2);
      uxME.setAreaLoad(m, "DEAD", sl.uid, 1.5);      // superimposed dead, kPa
      uxME.setAreaLoad(m, "LIVE", sl.uid, 2.4);      // office live, kPa
    }
  }
  if (id === "core") {
    m.shell_sections.WALL300 = { name: "WALL300", material: Object.keys(m.materials)[0], thickness: 0.3 };
    const ix = Math.max(0, Math.floor((xs.length - 2) / 2)), iy = Math.max(0, Math.floor((ys.length - 2) / 2));
    const x0 = xs[ix], x1 = xs[Math.min(ix + 1, xs.length - 1)], y0 = ys[iy], y1 = ys[Math.min(iy + 1, ys.length - 1)];
    const sides = [[{ x: x0, y: y0 }, { x: x1, y: y0 }], [{ x: x1, y: y0 }, { x: x1, y: y1 }], [{ x: x1, y: y1 }, { x: x0, y: y1 }], [{ x: x0, y: y1 }, { x: x0, y: y0 }]];
    for (const st of stories) for (const [a, b] of sides) {
      const w = uxME.addWall(m, a, b, st);
      if (w) { w.section = "WALL300"; w.mesh_size = Math.max(1.5, p.h / 2); }
    }
  }
  if (concrete && !Object.keys(m.materials).length) throw new Error("template material missing");
  return m;
}

/* ---------------- dialog ---------------- */
export function installStart(sky) {
  const S = sky.store;
  const U = uxU;

  // remember opened / saved files (most recent first)
  let lastName = S.fileName || null;
  const mru = () => { const v = uxLsGet(LS_MRU, []); return Array.isArray(v) ? v : []; };
  const touch = name => uxLsSet(LS_MRU, [name, ...mru().filter(n => n !== name)].slice(0, 12));
  const onChange = () => { if (S.fileName && S.fileName !== lastName) touch(S.fileName); lastName = S.fileName; };
  document.addEventListener("sky:model-changed", onChange);
  const chip = document.getElementById("fileChipName");
  if (chip) new MutationObserver(onChange).observe(chip, { childList: true, characterData: true, subtree: true });

  function open() {
    if (isUxDialogOpen("uxStartModal")) return;
    let cur = uxLsGet(LS_LAST, "steel3d");
    if (!UX_TEMPLATES.some(t => t.id === cur)) cur = "steel3d";
    const vals = {};
    UX_TEMPLATES.forEach(t => { vals[t.id] = { ...t.def }; });

    const cards = uxEl("div", { class: "ux-tpl-grid", role: "radiogroup", "aria-label": "Template" });
    const form = uxEl("div", { class: "ux-tpl-form" });
    const err = uxEl("p", { class: "field-error hidden dlg-error" });
    const recentBox = uxEl("div", { class: "ux-recent" }, [uxEl("p", { class: "muted", text: "Loading…" })]);

    const field = (key, label, kind, attrs = {}) => {
      const t = UX_TEMPLATES.find(x => x.id === cur);
      const v = vals[cur][key];
      const inp = uxEl("input", { type: "number", class: "ux-num", "data-key": key, step: kind === "length" ? "any" : "1", min: attrs.min ?? (kind === "length" ? "0.5" : "1"), max: attrs.max || null,
        value: kind === "length" ? U.inputValue("length", v) : String(v), disabled: attrs.disabled || null, "aria-label": label });
      inp.addEventListener("input", () => {
        const n = kind === "length" ? U.parse("length", inp.value) : parseInt(inp.value, 10);
        if (isFinite(n)) vals[cur][key] = n;
        validate();
      });
      return uxEl("label", { class: "ux-field" + (attrs.disabled ? " is-off" : "") }, [
        uxEl("span", { class: "ux-field-lbl", html: `${uxEsc(label)}${kind === "length" ? ` <i class="muted">${uxEsc(U.label("length"))}</i>` : ""}` }), inp,
      ].concat(t && attrs.note ? [uxEl("span", { class: "muted ux-field-note", text: attrs.note })] : []));
    };
    const nameInp = uxEl("input", { type: "text", class: "ux-name", "aria-label": "Model name", maxlength: "60" });

    function renderCards() {
      cards.textContent = "";
      for (const t of UX_TEMPLATES) {
        const c = uxEl("button", { type: "button", class: "ux-tpl-card" + (t.id === cur ? " is-sel" : ""), role: "radio", "aria-checked": t.id === cur ? "true" : "false", "data-tpl": t.id,
          title: `${t.name} — ${t.sub}\nETABS: ${t.etabs}` });
        c.innerHTML = thumb(t.id) + `<span class="ux-tpl-meta"><b>${uxEsc(t.name)}</b><span class="muted">${uxEsc(t.sub)}</span></span>`;
        c.addEventListener("click", () => { cur = t.id; renderCards(); renderForm(); });
        c.addEventListener("dblclick", () => { cur = t.id; create(); });
        cards.appendChild(c);
      }
    }
    function renderForm() {
      const t = UX_TEMPLATES.find(x => x.id === cur);
      if (!nameInp.value || UX_TEMPLATES.some(x => x.name === nameInp.value)) nameInp.value = t.name;
      form.textContent = "";
      form.append(
        uxEl("label", { class: "ux-field ux-field-wide" }, [uxEl("span", { class: "ux-field-lbl", text: "Model name" }), nameInp]),
        field("baysX", "Bays X", "count", { max: "20" }),
        field("sx", "Spacing X", "length"),
        field("baysY", "Bays Y", "count", { max: "20", disabled: t.noY }),
        field("sy", "Spacing Y", "length", { disabled: t.noY }),
        field("stories", "Stories", "count", { max: "60" }),
        field("h", "Story height", "length"),
        uxEl("p", { class: "muted ux-tpl-note ux-field-wide", text: `${t.sub}. ETABS equivalent: ${t.etabs}.` }),
        err,
      );
      validate();
    }
    function problems() {
      const v = vals[cur], t = UX_TEMPLATES.find(x => x.id === cur);
      const bad = [];
      if (!(v.baysX >= 1 && v.baysX <= 20)) bad.push("Bays X must be 1–20");
      if (!t.noY && !(v.baysY >= 1 && v.baysY <= 20)) bad.push("Bays Y must be 1–20");
      if (!(v.stories >= 1 && v.stories <= 60)) bad.push("Stories must be 1–60");
      for (const [k, n] of [["sx", "Spacing X"], ["sy", "Spacing Y"], ["h", "Story height"]])
        if (!(v[k] >= 0.5 && v[k] <= 50)) bad.push(`${n} must be ${U.fmtU("length", 0.5, 2)}–${U.fmtU("length", 50, 1)}`);
      return bad;
    }
    function validate() {
      const bad = problems();
      err.textContent = bad.join(" · ");
      err.classList.toggle("hidden", !bad.length);
      okBtn.disabled = !!bad.length || busy;
    }

    async function renderRecent() {
      let files = [];
      try { files = (await sky.filesApi.list()) || []; } catch { files = []; }
      const order = mru();
      files.sort((a, b) => {
        const ia = order.indexOf(a.name), ib = order.indexOf(b.name);
        if (ia >= 0 || ib >= 0) return (ia < 0 ? 1e9 : ia) - (ib < 0 ? 1e9 : ib);
        return String(b.mtime || "").localeCompare(String(a.mtime || ""));
      });
      recentBox.textContent = "";
      if (!files.length) {
        recentBox.appendChild(uxEl("p", { class: "muted ux-recent-empty", text: "No saved models yet. Use File › Save As… (Ctrl+Shift+S) to keep a model; it will appear here." }));
      }
      for (const f of files.slice(0, 7)) {
        const b = uxEl("button", { type: "button", class: "ux-recent-item", title: `Open “${f.name}”` });
        b.innerHTML = `<b>${uxEsc(f.name)}</b><span class="muted">${f.stories != null ? uxEsc(f.stories) + " stories · " : ""}${f.members != null ? uxEsc(f.members) + " members" : ""}</span>`;
        b.addEventListener("click", async () => {
          if (S.dirty && !b.classList.contains("is-confirm")) {
            b.classList.add("is-confirm");
            b.querySelector("span").textContent = "Unsaved edits will be lost — click again to open";
            return;
          }
          try {
            const m = await sky.filesApi.open(f.name);
            d.close();
            sky.adoptModel(m, f.name);
            touch(f.name);
            sky.toast("Model opened", `“${f.name}” is now the working model`, "info", 3500);
          } catch (e2) { sky.toast("Open failed", e2.message, "error"); }
        });
        recentBox.appendChild(b);
      }
      const more = uxEl("div", { class: "ux-recent-more" }, [
        uxEl("button", { type: "button", class: "btn btn-small", text: "Open other…", onclick: () => { d.close(); sky.etabs.clickItem("file-open"); } }),
        uxEl("button", { type: "button", class: "btn btn-small", text: "Import…", onclick: () => { d.close(); sky.etabs.clickItem("file-import"); } }),
        uxEl("button", { type: "button", class: "btn btn-small", text: "Classic gallery…", title: "The preset buildings of the previous New Model gallery", onclick: () => { d.close(); sky.openGallery(); } }),
      ]);
      recentBox.appendChild(more);
    }

    let busy = false;
    async function create() {
      if (busy || problems().length) return validate();
      busy = true;
      okBtn.disabled = true;
      okBtn.textContent = "Creating…";
      const v = { ...vals[cur], name: nameInp.value.trim() || UX_TEMPLATES.find(x => x.id === cur).name };
      try {
        const m = await buildTemplate(sky, cur, v);
        uxLsSet(LS_LAST, cur);
        d.close();
        sky.adoptModel(m, null);
        sky.markDirty();
        await sky.saveModel();                          // backend now holds exactly this model
        if (sky.etabs) sky.etabs.showResult("view3d");
        if (sky.viewer && sky.viewer.fit) try { sky.viewer.fit(); } catch { /* ignore */ }
        sky.toast("New model", `${v.name}: ${m.members.length} frames · ${m.shells.length} shells · ${m.stories.length} stories — press Run (F5)`, "info", 5000);
      } catch (e) {
        busy = false;
        okBtn.textContent = "Create";
        validate();
        err.textContent = "Could not create the model: " + e.message;
        err.classList.remove("hidden");
      }
    }

    const okBtn = uxEl("button", { type: "button", class: "btn btn-run", text: "Create" });
    const cancel = uxEl("button", { type: "button", class: "btn", text: "Cancel" });
    const dirtyNote = S.dirty ? "Unsaved edits to the current model will be discarded." : "Tip: double-click a template to create it with these values.";
    const body = uxEl("div", { class: "ux-start" }, [
      uxEl("section", { class: "ux-start-tpls" }, [uxEl("h3", { class: "ux-h", text: "Templates" }), cards]),
      uxEl("section", { class: "ux-start-params" }, [uxEl("h3", { class: "ux-h", text: "Parameters" }), form]),
      uxEl("section", { class: "ux-start-recent" }, [uxEl("h3", { class: "ux-h", text: "Recent models" }), recentBox]),
    ]);
    const d = uxDialog("uxStartModal", {
      title: "New Model — Start", iconId: "ux-start", cls: "ux-start-dlg", body,
      foot: [uxEl("span", { class: "muted dlg-foot-note" + (S.dirty ? " ux-warn" : ""), text: dirtyNote }), uxEl("div", { class: "modal-btns" }, [cancel, okBtn])],
    });
    okBtn.addEventListener("click", create);
    cancel.addEventListener("click", d.close);
    renderCards();
    renderForm();
    renderRecent();
  }

  // File › New and the toolbar "New" → start screen (the classic gallery stays on File › Template Gallery…)
  const legacyNew = sky.fileNew;
  sky.fileNew = () => open();
  sky.ux = sky.ux || {};
  sky.ux.start = { open, close: () => closeUxDialog("uxStartModal"), isOpen: () => isUxDialogOpen("uxStartModal"), build: (id, p) => buildTemplate(sky, id, p), templates: UX_TEMPLATES, legacyNew };

  // empty model at startup → show the start screen
  const m = S.model;
  const empty = !m || (!(m.members || []).length && !(m.shells || []).length);
  let forced = false;
  try { forced = new URLSearchParams(location.search).get("start") === "1"; } catch { /* ignore */ }
  if (empty || forced) setTimeout(open, 0);
}
