/* SkyFrame app shell — state store, API (with mock fallback), tabs,
   tables, overlay controls, model/draw mode. No frameworks. */

import { Viewer3D, SHELL_COMPONENTS } from "./viewer3d.js";
import { renderStoryCharts, stationDiagram, timeSeriesChart, pushoverChart } from "./charts.js";
import { mockModel, mockResults, mockSectionLibrary, mockModelFiles, mockWindPattern } from "./mock.js";
import { PlanEditor } from "./draw.js";
import { ElevEditor } from "./elev.js";
import { LoadsEditor } from "./loads.js";
import { openReport, buildReportHtml } from "./report.js";
import * as ME from "./modeledit.js";

/* ------------------------------------------------ state */
const store = {
  model: null,
  results: null,
  mock: false,
  caseName: null,        // selected case/combo
  tab: "view3d",
  firstSolveDone: false,
  driftLimitPct: 0.5,    // % — 1/200
  overlay: { deformed: false, modal: false, modeIndex: 0, scaleMult: 1 },
  forcesSort: { key: "M3", dir: -1 },
  forcesFilter: "",
  lastSolveMs: null,
  // v0.2 — draw mode
  mode: "analyze",       // "model" | "loads" | "analyze"
  story: null,           // current story name in the plan editor
  applyAll: false,       // drawing applies to all stories
  tool: "select",
  selection: [],         // [{type:"member"|"shell", uid}]
  dirty: false,          // unsaved model edits
  modelEdited: false,    // 3D viewer needs a setModel refresh
  loadPattern: "DEAD",   // pattern for load assignment inputs
  selectedMemberUid: null, // member detail panel (analyze)
  // v0.3 — model files & section library
  fileName: null,        // current saved-model file name (null = unsaved)
  sectionLib: null,      // cached GET /api/sections/library
  libSearch: "",
  // v0.4 — braces, contours, envelope combos, time history
  braceXPair: false,     // brace tool adds the mirrored diagonal too
  contour: { on: false, comp: "M11" },
  envSide: "max",        // envelope-combo tables: "max" | "min"
  thCase: null,          // selected time-history case
  thStory: null,         // selected story for the TH displacement trace
  // v0.5 — elevation view, pushover
  view: "plan",          // model-mode editor: "plan" | "elev"
  elevLine: null,        // elevation grid line, e.g. "x:0" | "y:2"
  poCase: null,          // selected pushover case (results tab)
};

const $ = id => document.getElementById(id);
const fmt = (v, d = 1) => (v == null || !isFinite(v)) ? "—" :
  v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ------------------------------------------------ toasts + status */
function toast(title, msg, type = "info", ms = 6000) {
  const t = document.createElement("div");
  t.className = `toast ${type}`;
  t.innerHTML = `<b>${esc(title)}</b><span class="toast-msg">${esc(msg)}</span>`;
  $("toasts").appendChild(t);
  setTimeout(() => t.remove(), ms);
}

function setStatus(state, text) {
  const pill = $("statusPill");
  pill.className = `status-pill is-${state}`;
  $("statusText").textContent = text;
}

/* ------------------------------------------------ API layer */
const FORCE_MOCK = new URLSearchParams(location.search).has("mock");

async function api(path, body) {
  const res = await fetch(path, body === undefined ? undefined : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === null ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error page */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}

function enterMockMode() {
  store.mock = true;
  $("mockBadge").classList.remove("hidden");
}

async function fetchModel() {
  if (!FORCE_MOCK) {
    try { return await api("/api/model"); }
    catch (e) { console.warn("Backend unreachable, falling back to mock:", e.message); }
  }
  enterMockMode();
  return mockModel();
}

async function generateModel(params) {
  if (store.mock) return mockModel(params);
  return api("/api/model/quick", params);
}

async function analyze() {
  if (store.mock) {
    await new Promise(r => setTimeout(r, 500));   // let the spinner breathe
    return mockResults(store.model);
  }
  return api("/api/analyze", null);
}

async function postModel(payload) {
  if (store.mock) {
    await new Promise(r => setTimeout(r, 300));
    return payload;                                // mock backend accepts locally
  }
  return api("/api/model", payload);
}

/* ---- v0.4: wind pattern generation.
   Live path syncs the working model, POSTs /api/pattern/wind and adopts the
   echoed model. Mock (or missing endpoint) computes the profile locally. */
async function generateWindPattern(params) {
  if (!store.mock) {
    try {
      const payload = JSON.parse(JSON.stringify(store.model));
      delete payload._mock_params;
      await postModel(payload);
      const echoed = await api("/api/pattern/wind", params);
      store.model = ME.normalizeModel(echoed);
      store.modelEdited = true;
      clearDirty();                                // client == server state
      syncLoadsNav();
      renderSummary();
      toast("Wind pattern generated",
        `“${params.name}” added via POST /api/pattern/wind`, "info", 5000);
      return store.model;
    } catch (e) {
      console.warn("Wind endpoint unavailable, computing locally:", e.message);
    }
  }
  mockWindPattern(store.model, params);
  ME.normalizeModel(store.model);
  markDirty();
  toast("Wind pattern generated",
    `“${params.name}” computed locally (${store.mock ? "mock mode" : "backend lacks /api/pattern/wind"})`,
    "info", 5000);
  return store.model;
}

/* ---- v0.3: model files + section library.
   Each call tries the real endpoint, then falls back to the built-in mock
   store so the UI stays usable while the backend catches up. */
let filesUsingMock = false;
async function filesCall(real, mock) {
  if (!store.mock) {
    try { const r = await real(); filesUsingMock = false; return r; }
    catch (e) { console.warn("Model-files endpoint unavailable, using mock store:", e.message); }
  }
  filesUsingMock = true;
  return mock();
}
const filesApi = {
  list: () => filesCall(
    () => api("/api/models"),
    () => mockModelFiles.list()),
  save: name => filesCall(
    () => api(`/api/models/${encodeURIComponent(name)}`, null),
    () => mockModelFiles.save(name, store.model)),
  open: name => filesCall(
    () => api(`/api/models/${encodeURIComponent(name)}/open`, null),
    () => mockModelFiles.open(name)),
  remove: name => filesCall(
    () => fetch(`/api/models/${encodeURIComponent(name)}`, { method: "DELETE" })
      .then(r => { if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); return r.json().catch(() => ({})); }),
    () => mockModelFiles.remove(name)),
};

async function fetchSectionLibrary() {
  if (store.sectionLib) return store.sectionLib;
  let lib;
  if (!store.mock) {
    try { lib = await api("/api/sections/library"); }
    catch (e) { console.warn("Section library endpoint unavailable, using mock:", e.message); }
  }
  store.sectionLib = Array.isArray(lib) && lib.length ? lib : mockSectionLibrary();
  return store.sectionLib;
}

/* ------------------------------------------------ viewer */
let viewer = null;

function memberTooltip(seg) {
  let html = `<span class="tt-uid">${esc(seg.uid)}</span><br>` +
    `${esc(seg.kind)} · ${esc(seg.section)} · ${esc(seg.story)}`;
  const cd = caseData();
  if (cd && cd.member_forces && cd.member_forces[seg.uid]) {
    const f = cd.member_forces[seg.uid];
    const N = Math.max(Math.abs(f[0]), Math.abs(f[6]));
    const V = Math.max(Math.abs(f[1]), Math.abs(f[7]));
    const M = Math.max(Math.abs(f[5]), Math.abs(f[11]));
    html += `<br><span class="tt-forces">${esc(caseLabel(store.caseName))}</span>` +
      (isRsCase(store.caseName) ? ` <span style="color:var(--amber)">±</span>` : "") +
      ` · N ${fmt(N)} · V ${fmt(V)} kN · M ${fmt(M)} kN·m`;
  }
  return html;
}

/* ------------------------------------------------ case selection
   Response-spectrum cases are keyed "rs:<name>" internally so they can
   never collide with a static case/combo name; caseLabel() renders them
   as "RS: <name>". RS values are POSITIVE ENVELOPES. */
const isRsCase = name => typeof name === "string" && name.startsWith("rs:");
const caseLabel = name => isRsCase(name) ? `RS: ${name.slice(3)}` : (name || "");

function caseNames() {
  if (!store.results) return [];
  return [
    ...Object.keys(store.results.cases || {}),
    ...Object.keys(store.results.combos || {}),
    ...Object.keys(store.results.rs_cases || {}).map(n => `rs:${n}`),
  ];
}

function caseData() {
  const r = store.results;
  if (!r || !store.caseName) return null;
  if (isRsCase(store.caseName))
    return (r.rs_cases && r.rs_cases[store.caseName.slice(3)]) || null;
  return (r.cases && r.cases[store.caseName]) || (r.combos && r.combos[store.caseName]) || null;
}

/* ---- v0.4: envelope combos — tables honour the max/min toggle.
   Envelope results keep max in the standard keys and min in cd.min. */
function tableCaseData() {
  const cd = caseData();
  return (cd && cd.min && store.envSide === "min") ? cd.min : cd;
}

function syncEnvToggle() {
  const cd = caseData();
  const has = !!(cd && cd.min);
  $("envToggle").classList.toggle("hidden", !has);
  if (!has) store.envSide = "max";
  document.querySelectorAll("#envToggle .seg-btn").forEach(b =>
    b.classList.toggle("is-active", b.dataset.env === store.envSide));
}

function rebuildCaseSelect() {
  const sel = $("caseSelect");
  sel.textContent = "";
  const r = store.results;
  if (!r) { $("caseSelectWrap").hidden = true; return; }
  const mkGroup = (label, entries) => {
    if (!entries.length) return;
    const g = document.createElement("optgroup");
    g.label = label;
    for (const [value, text] of entries) {
      const o = document.createElement("option");
      o.value = value; o.textContent = text;
      g.appendChild(o);
    }
    sel.appendChild(g);
  };
  mkGroup("Cases", Object.keys(r.cases || {}).map(n => [n, n]));
  mkGroup("Combos", Object.keys(r.combos || {}).map(n => [n, n]));
  mkGroup("Response spectrum — envelopes",
    Object.keys(r.rs_cases || {}).map(n => [`rs:${n}`, `RS: ${n}`]));
  if (!store.caseName || !caseNames().includes(store.caseName)) {
    // prefer a lateral case (non-trivial story results) for the first look
    const names = caseNames();
    const lateral = names.find(n => {
      const cd = (r.cases && r.cases[n]) || (r.combos && r.combos[n]);
      return cd && cd.story && Object.values(cd.story).some(
        s => Math.abs(s.drift_x || 0) + Math.abs(s.drift_y || 0) > 1e-9);
    });
    store.caseName = lateral || Object.keys(r.cases || {})[0] || names[0] || null;
  }
  sel.value = store.caseName;
  $("caseSelectWrap").hidden = false;
}

/* ------------------------------------------------ tabs */
function switchTab(tab) {
  store.tab = tab;
  document.querySelectorAll(".tab").forEach(b =>
    b.classList.toggle("is-active", b.dataset.tab === tab));
  document.querySelectorAll(".tabpane").forEach(p =>
    p.classList.toggle("is-active", p.id === `pane-${tab}`));
  if (tab === "view3d" && viewer) viewer._resize();
}

/* ================================================================
   v0.2 — MODEL (draw) MODE
   ================================================================ */
let planEditor = null;
let elevEditor = null;    // v0.5 elevation (section) editor
let loadsEditor = null;   // v0.3 loads/cases/combos editor

/* v0.5 — both draw views share model + selection; keep them in sync. */
function activeEditor() {
  return store.view === "elev" ? elevEditor : planEditor;
}
function refreshDrawViews() {
  if (planEditor) planEditor.refresh();
  if (elevEditor) elevEditor.refresh();
}
function renderStaticViews() {
  if (planEditor) planEditor.renderStatic();
  if (elevEditor) elevEditor.renderStatic();
}

function syncLoadsNav() {
  const m = store.model;
  if (!m) return;
  $("cnt-patterns").textContent = Object.keys(m.patterns || {}).length;
  $("cnt-cases").textContent = Object.keys(m.cases || {}).length;
  $("cnt-rs").textContent = Object.keys(m.rs_cases || {}).length;
  $("cnt-th").textContent = Object.keys(m.th_cases || {}).length;
  $("cnt-pushover").textContent = Object.keys(m.pushover_cases || {}).length;
  $("cnt-combos").textContent = Object.keys(m.combos || {}).length;
}

function setMode(mode) {
  store.mode = mode;
  const model = mode === "model", loads = mode === "loads", editing = model || loads;
  document.querySelectorAll(".mode-btn").forEach(b =>
    b.classList.toggle("is-active", b.dataset.mode === mode));
  $("drawMain").classList.toggle("hidden", !model);
  $("loadsMain").classList.toggle("hidden", !loads);
  $("analyzeMain").classList.toggle("hidden", editing);
  $("drawSidebar").classList.toggle("hidden", !model);
  $("loadsSidebar").classList.toggle("hidden", !loads);
  $("analyzeSidebar").classList.toggle("hidden", editing);
  $("modelActions").classList.toggle("hidden", !editing);
  $("runBtn").classList.toggle("hidden", editing);
  $("reportBtn").classList.toggle("hidden", editing);
  if (model) {
    rebuildStorySelect();
    rebuildElevSelect();
    syncDiaphragmUI();
    refreshDrawViews();
  } else if (loads) {
    loadsEditor.render();
    syncLoadsNav();
  } else {
    if (store.modelEdited) {
      viewer.setModel(store.model);
      store.modelEdited = false;
      syncShellLegend();
    }
    renderSummary();
    if (store.tab === "view3d") viewer._resize();
  }
}

function markDirty() {
  store.dirty = true;
  store.modelEdited = true;
  renderSummary();
  syncDirtyUI();
  if (store.mode === "loads") syncLoadsNav();
}
function clearDirty() {
  store.dirty = false;
  syncDirtyUI();
}

/** File chip shows the saved-model name (+ amber dot when dirty). When no
    file is associated, the classic "● unsaved" badge does the job alone. */
function syncDirtyUI() {
  const hasFile = !!store.fileName;
  $("fileChip").classList.toggle("hidden", !hasFile);
  if (hasFile) $("fileChipName").textContent = store.fileName;
  $("fileDirtyDot").classList.toggle("hidden", !store.dirty);
  $("dirtyBadge").classList.toggle("hidden", !store.dirty || hasFile);
}

function syncShellLegend() {
  const shells = (store.model && store.model.shells) || [];
  $("legendWall").classList.toggle("hidden", !shells.some(s => s.kind === "wall"));
  $("legendSlab").classList.toggle("hidden", !shells.some(s => s.kind === "slab"));
  $("legendLink").classList.toggle("hidden",
    !((store.model && store.model.links) || []).length);
}

/* ================================================================
   v0.5 — PLAN | ELEVATION view toggle + diaphragm select
   ================================================================ */
function elevPlane() {
  if (!store.elevLine) return null;
  const [axis, idx] = store.elevLine.split(":");
  return { axis, index: parseInt(idx, 10) };
}

function rebuildElevSelect() {
  const sel = $("elevLineSelect");
  sel.textContent = "";
  const g = store.model && store.model.grid;
  if (!g) return;
  const mk = (label, axis, lines, labels) => {
    const grp = document.createElement("optgroup");
    grp.label = label;
    lines.forEach((v, i) => {
      const o = document.createElement("option");
      o.value = `${axis}:${i}`;
      o.textContent = `${(labels && labels[i]) || i + 1}  ·  ${axis} = ${v} m`;
      grp.appendChild(o);
    });
    sel.appendChild(grp);
  };
  mk("X lines", "x", g.x_lines, g.x_labels);
  mk("Y lines", "y", g.y_lines, g.y_labels);
  const all = [...sel.querySelectorAll("option")].map(o => o.value);
  if (!store.elevLine || !all.includes(store.elevLine)) store.elevLine = all[0] || null;
  if (store.elevLine) sel.value = store.elevLine;
}

function setView(view) {
  store.view = view === "elev" ? "elev" : "plan";
  const elev = store.view === "elev";
  document.querySelectorAll("#viewToggle .seg-btn").forEach(b =>
    b.classList.toggle("is-active", b.dataset.view === store.view));
  $("planSvg").classList.toggle("hidden", elev);
  $("elevSvg").classList.toggle("hidden", !elev);
  $("storyWrap").classList.toggle("hidden", elev);
  $("storySteps").classList.toggle("hidden", elev);
  $("storyElev").classList.toggle("hidden", elev);
  $("elevLineWrap").classList.toggle("hidden", !elev);
  $("drawHint").textContent = elev
    ? "brace: click two snapped points at different levels · right-drag pan · wheel zoom · dbl-click fit"
    : "click draws with active tool · right-drag pan · wheel zoom · dbl-click fit";
  // the slab tool has no meaning in a section — fall back to Select
  const slabBtn = document.querySelector('[data-tool="slab"]');
  if (slabBtn) slabBtn.disabled = elev;
  if (elev && store.tool === "slab") setTool("select");
  if (elev) {
    rebuildElevSelect();
    elevEditor._resize();
    elevEditor.fit();
  } else {
    planEditor.refresh();
  }
  syncStoryBadges();
}

function syncDiaphragmUI() {
  if (store.model) $("diaphragmSelect").value = store.model.diaphragm || "rigid";
}

/* ---- story selection */
function rebuildStorySelect() {
  const sel = $("storySelect");
  sel.textContent = "";
  const m = store.model;
  if (!m) return;
  const names = m.stories.map(s => s.name);
  if (!store.story || !names.includes(store.story)) store.story = names[0] || null;
  for (const n of [...names].reverse()) {     // top story first, like ETABS
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  }
  sel.value = store.story;
  syncStoryBadges();
}

function setStory(name) {
  if (!name || name === store.story) return;
  store.story = name;
  $("storySelect").value = name;
  syncStoryBadges();
  planEditor.refresh();
  renderProps();
}

function setElevLine(value) {
  store.elevLine = value;
  $("elevLineSelect").value = value;
  syncStoryBadges();
  elevEditor.fit();
}

function stepStory(dir) {
  const names = store.model.stories.map(s => s.name);
  const i = names.indexOf(store.story) + dir;
  if (i >= 0 && i < names.length) setStory(names[i]);
}

function syncStoryBadges() {
  if (store.view === "elev") {                     // v0.5 elevation badge
    const pl = elevEditor && elevEditor.plane();
    $("planStoryBadge").innerHTML = pl
      ? `<b>${pl.axis === "x" ? "X" : "Y"}-line ${esc(pl.label)}</b> · elevation @ ${esc(pl.axis)} = ${fmt(pl.coord, 1)} m`
      : "";
    return;
  }
  const st = ME.storyByName(store.model, store.story);
  if (!st) { $("storyElev").textContent = ""; $("planStoryBadge").textContent = ""; return; }
  const zb = st.elevation - st.height;
  $("storyElev").textContent = `z ${fmt(zb, 1)} – ${fmt(st.elevation, 1)} m`;
  $("planStoryBadge").innerHTML =
    `<b>${esc(st.name)}</b> · plan @ ${fmt(st.elevation, 1)} m` +
    (store.applyAll ? ` · <span style="color:var(--amber)">all stories</span>` : "");
}

/* ---- drawing actions (called by the plan editor) */
function targetStories() {
  return store.applyAll ? store.model.stories.map(s => s.name) : [store.story];
}

function handleDraw(tool, payload) {
  const m = store.model;
  let made = 0;
  for (const st of targetStories()) {
    let el = null;
    if (tool === "column") el = ME.addColumn(m, payload.x, payload.y, st);
    else if (tool === "beam") el = ME.addBeam(m, payload.p1, payload.p2, st);
    else if (tool === "brace") {
      el = ME.addBrace(m, payload.p1, payload.p2, st);
      if (el) made++;
      // X-pair: the mirrored diagonal (B bottom → A top)
      el = store.braceXPair ? ME.addBrace(m, payload.p2, payload.p1, st) : null;
    }
    else if (tool === "wall") el = ME.addWall(m, payload.p1, payload.p2, st);
    else if (tool === "slab") el = ME.addSlab(m, payload.x0, payload.y0, payload.x1, payload.y1, st);
    else if (tool === "link") {
      // v0.5: plan-view links live at the story's top (diaphragm) elevation
      const { zt } = ME.storyZ(m, st);
      el = ME.addLink(m,
        [payload.p1.x, payload.p1.y, zt], [payload.p2.x, payload.p2.y, zt]);
    }
    if (el) made++;
  }
  if (made) {
    markDirty();
    renderStaticViews();
  }
}

/* v0.5 — elevation-view drawing: payloads carry resolved 3D points. */
function handleElevDraw(tool, payload) {
  const m = store.model;
  let made = 0;
  if (tool === "column") {
    const st = ME.storyContainingZ(m, payload.z);
    if (st && ME.addColumn(m, payload.x, payload.y, st)) made++;
  } else if (tool === "beam") {
    const st = ME.storyAtLevel(m, payload.z);
    if (st && ME.addBeamAt(m, payload.p1, payload.p2, st)) made++;
  } else if (tool === "brace") {
    const zTop = Math.max(payload.p1[2], payload.p2[2]);
    const st = ME.storyContainingZ(m, zTop);
    if (ME.addBraceAt(m, payload.p1, payload.p2, st)) made++;
    if (store.braceXPair) {
      // mirrored diagonal: same two levels, plan ends swapped
      const r1 = [payload.p2[0], payload.p2[1], payload.p1[2]];
      const r2 = [payload.p1[0], payload.p1[1], payload.p2[2]];
      if (ME.addBraceAt(m, r1, r2, st)) made++;
    }
  } else if (tool === "wall") {
    let z0 = Math.min(payload.z1, payload.z2), z1 = Math.max(payload.z1, payload.z2);
    if (z1 - z0 < 1e-6) {
      // both clicks on one level: the wall fills that story's height
      const ref = z1 > 1e-9 ? z1 : (m.stories[0] ? m.stories[0].elevation : 3);
      const { zb, zt } = ME.storyZ(m, ME.storyContainingZ(m, ref));
      z0 = zb; z1 = zt;
    }
    const w3 = (s, z) => elevEditor.world3(s, z);
    const corners = [
      w3(payload.s1, z0), w3(payload.s2, z0),
      w3(payload.s2, z1), w3(payload.s1, z1),
    ];
    if (ME.addWallAt(m, corners, ME.storyContainingZ(m, z1))) made++;
  } else if (tool === "link") {
    if (ME.addLink(m, payload.p1, payload.p2)) made++;
  }
  if (made) {
    markDirty();
    renderStaticViews();
  }
}

function handleErase(ref) {
  if (ME.eraseElement(store.model, ref)) {
    store.selection = store.selection.filter(r => !(r.type === ref.type && r.uid === ref.uid));
    markDirty();
    refreshDrawViews();
    renderProps();
  }
}

function deleteSelection() {
  if (!store.selection.length) return;
  const n = store.selection.length;
  for (const ref of [...store.selection]) ME.eraseElement(store.model, ref);
  store.selection = [];
  markDirty();
  refreshDrawViews();
  renderProps();
  toast("Deleted", `${n} element${n > 1 ? "s" : ""} removed`, "info", 3000);
}

function handleSelect(refs, additive) {
  if (additive) {
    for (const ref of refs) {
      const i = store.selection.findIndex(r => r.type === ref.type && r.uid === ref.uid);
      if (i >= 0 && refs.length === 1) store.selection.splice(i, 1);   // shift-click toggles
      else if (i < 0) store.selection.push(ref);
    }
  } else {
    store.selection = refs;
  }
  renderStaticViews();
  renderProps();
}

function setTool(tool) {
  if (store.view === "elev" && tool === "slab") tool = "select";   // v0.5
  store.tool = tool;
  document.querySelectorAll(".tool-btn").forEach(b =>
    b.classList.toggle("is-active", b.dataset.tool === tool));
  $("braceOpts").classList.toggle("hidden", tool !== "brace");
  planEditor.setTool(tool);
  if (elevEditor) elevEditor.setTool(tool);
}

/* ---- properties / assignment panel */
function selObjects() {
  const m = store.model;
  const members = [], shells = [], links = [];
  for (const ref of store.selection) {
    if (ref.type === "member") {
      const mm = m.members.find(x => x.uid === ref.uid);
      if (mm) members.push(mm);
    } else if (ref.type === "link") {
      const l = (m.links || []).find(x => x.uid === ref.uid);
      if (l) links.push(l);
    } else {
      const s = m.shells.find(x => x.uid === ref.uid);
      if (s) shells.push(s);
    }
  }
  return { members, shells, links };
}

const commonVal = (arr, f) => {
  if (!arr.length) return undefined;
  const v = f(arr[0]);
  return arr.every(x => f(x) === v) ? v : undefined;
};

function optionList(names, selected, mixed) {
  let html = mixed ? `<option value="" selected disabled>— mixed —</option>` : "";
  for (const n of names)
    html += `<option value="${esc(n)}"${n === selected ? " selected" : ""}>${esc(n)}</option>`;
  return html;
}

function renderProps() {
  const box = $("propsContent");
  const { members, shells, links } = selObjects();
  const total = members.length + shells.length + links.length;
  if (!total) {
    box.innerHTML = `<div class="props-empty">
      <p>Nothing selected.</p>
      <p class="muted">Use the Select tool (V): click an element or drag a box, then edit sections, releases and loads here.</p>
    </div>`;
    return;
  }

  const m = store.model;
  const beams = members.filter(x => x.kind !== "column" && x.kind !== "brace");
  const columns = members.filter(x => x.kind === "column");
  const braces = members.filter(x => x.kind === "brace");
  const walls = shells.filter(x => x.kind === "wall");
  const slabs = shells.filter(x => x.kind === "slab");
  const kinds = [
    [columns.length, "column"], [beams.length, "beam"], [braces.length, "brace"],
    [walls.length, "wall"], [slabs.length, "slab"], [links.length, "link"],
  ].filter(([n]) => n).map(([n, k]) => `${n} ${k}${n > 1 ? "s" : ""}`).join(" · ");

  const pats = ME.patternNames(m);
  const patOpts = sel => optionList(pats, sel, false);

  let html = `
    <div class="sel-count"><span>${total} selected</span>
      <button class="link-3d" id="propClear">Clear</button></div>
    <div class="sel-kinds">${esc(kinds)}</div>`;

  if (members.length) {
    const sec = commonVal(members, x => x.section);
    html += `
      <h3 class="group-title">Frame assignments</h3>
      <div class="field"><label for="propFrameSection">Section</label>
        <select id="propFrameSection">${optionList(Object.keys(m.sections), sec, sec === undefined)}</select>
      </div>`;
    // v0.4: orientation angle for columns & braces (FrameMember.angle)
    const angMembers = [...columns, ...braces];
    if (angMembers.length) {
      const ang = commonVal(angMembers, x => x.angle ?? 0);
      html += `
      <div class="field"><label for="propAngle">Orientation angle <span class="unit">deg · columns &amp; braces</span></label>
        <input id="propAngle" type="number" step="5"
          value="${ang === undefined ? "" : ang}" placeholder="${ang === undefined ? "mixed" : ""}">
      </div>`;
    }
    if (beams.length) {
      const relOf = (x, tok) => (x.releases || "").split(",").map(s => s.trim()).includes(tok);
      const mi = commonVal(beams, x => relOf(x, "Mi"));
      const mj = commonVal(beams, x => relOf(x, "Mj"));
      const udl = commonVal(beams, x => ME.getMemberUdl(m, store.loadPattern, x.uid) ?? 0);
      html += `
      <div class="field"><label>End releases <span class="unit">moment, beams</span></label>
        <div class="check-row">
          <label><input type="checkbox" id="propRelMi"${mi ? " checked" : ""}> M<sub>i</sub> (start)</label>
          <label><input type="checkbox" id="propRelMj"${mj ? " checked" : ""}> M<sub>j</sub> (end)</label>
        </div>
      </div>
      <h3 class="group-title">Line load — beams</h3>
      <div class="load-row">
        <div class="field"><label for="propUdlPat">Pattern</label>
          <select id="propUdlPat">${patOpts(store.loadPattern)}</select></div>
        <div class="field"><label for="propUdlW">UDL <span class="unit">kN/m ↓</span></label>
          <input id="propUdlW" type="number" step="1" min="0"
            value="${udl === undefined ? "" : udl}" placeholder="${udl === undefined ? "mixed" : ""}"></div>
      </div>`;
    }
  }

  if (shells.length) {
    const sec = commonVal(shells, x => x.section);
    const beh = commonVal(shells, x => x.behavior);
    const mesh = commonVal(shells, x => x.mesh_size);
    html += `
      <h3 class="group-title">Shell assignments</h3>
      <div class="field"><label for="propShellSection">Shell section</label>
        <select id="propShellSection">${optionList(Object.keys(m.shell_sections), sec, sec === undefined)}</select>
      </div>
      <div class="field-row">
        <div class="field"><label for="propBehavior">Behavior</label>
          <select id="propBehavior">
            ${beh === undefined ? `<option value="" selected disabled>— mixed —</option>` : ""}
            <option value="shell"${beh === "shell" ? " selected" : ""} title="Meshed shell finite elements">shell (FE)</option>
            <option value="membrane"${beh === "membrane" ? " selected" : ""}${walls.length ? " disabled" : ""} title="No FE — two-way tributary load to edge beams (slabs only)">membrane</option>
          </select></div>
        <div class="field"><label for="propMesh">Mesh <span class="unit">m</span></label>
          <input id="propMesh" type="number" step="0.25" min="0.25"
            value="${mesh === undefined ? "" : mesh}" placeholder="${mesh === undefined ? "mixed" : ""}"></div>
      </div>`;
    if (slabs.length) {
      const q = commonVal(slabs, x => ME.getAreaLoad(m, store.loadPattern, x.uid) ?? 0);
      html += `
      <h3 class="group-title">Area load — slabs</h3>
      <div class="load-row">
        <div class="field"><label for="propAreaPat">Pattern</label>
          <select id="propAreaPat">${patOpts(store.loadPattern)}</select></div>
        <div class="field"><label for="propAreaQ">q <span class="unit">kPa ↓</span></label>
          <input id="propAreaQ" type="number" step="0.5" min="0"
            value="${q === undefined ? "" : q}" placeholder="${q === undefined ? "mixed" : ""}"></div>
      </div>`;
    }
    /* v0.5 — openings editor (single region selected) */
    if (shells.length === 1) {
      const sh = shells[0];
      const ops = sh.openings || (sh.openings = []);
      html += `
      <h3 class="group-title">Openings <span class="unit">u, v fractions 0–1</span></h3>
      <div class="open-rows" id="openRows">` +
        (ops.length
          ? `<div class="open-row head"><span>u0</span><span>v0</span><span>u1</span><span>v1</span><span></span></div>` +
            ops.map((o, i) => `<div class="open-row" data-i="${i}">` +
              ["u0", "v0", "u1", "v1"].map(k =>
                `<input type="number" min="0" max="1" step="0.05" data-k="${k}" value="${o[k]}" title="${k} — fraction of the region edge">`).join("") +
              `<button class="chip-x open-del" data-del="${i}" title="Remove opening">✕</button></div>`).join("")
          : `<p class="muted open-empty">No openings — cutouts (doors / windows) removed from the mesh.</p>`) +
      `</div>
      <button class="btn btn-small btn-block" id="openAdd">+ Add opening</button>
      <svg class="open-preview" id="openPreview" aria-label="Region preview with opening cutouts"></svg>`;
    } else if (shells.length > 1) {
      html += `<p class="muted" style="font-size:11px;margin-top:8px">Select a single wall/slab to edit its openings.</p>`;
    }
  }

  /* v0.5 — link stiffness (6 dof) */
  if (links.length) {
    const K_LABELS = ["kx", "ky", "kz", "krx", "kry", "krz"];
    html += `
      <h3 class="group-title">Link stiffness <span class="unit">kN/m · kN·m/rad</span></h3>
      <div class="link-stiff">` +
      K_LABELS.map((lbl, i) => {
        const v = commonVal(links, l => l.stiffness[i]);
        return `<label><span>${lbl}</span>
          <input type="number" class="linkK" data-si="${i}" step="1000" min="0"
            value="${v === undefined ? "" : v}" placeholder="${v === undefined ? "mixed" : ""}"></label>`;
      }).join("") + `</div>`;
  }

  html += `<h3 class="group-title"></h3>
    <button class="btn btn-danger btn-block" id="propDelete">Delete selection <kbd style="font-family:var(--mono);font-size:10px">Del</kbd></button>`;
  box.innerHTML = html;

  /* wiring */
  $("propClear").addEventListener("click", () => handleSelect([], false));
  $("propDelete").addEventListener("click", deleteSelection);

  const on = (id, ev, fn) => { const n = $(id); if (n) n.addEventListener(ev, fn); };

  on("propFrameSection", "change", e => {
    for (const mm of members) mm.section = e.target.value;
    markDirty(); planEditor.renderStatic();
  });
  on("propAngle", "change", e => {
    const v = parseFloat(e.target.value);
    if (!isFinite(v)) return;
    for (const mm of [...columns, ...braces]) mm.angle = v;
    markDirty();
  });
  const applyReleases = () => {
    const mi = $("propRelMi").checked, mj = $("propRelMj").checked;
    const toks = [...(mi ? ["Mi"] : []), ...(mj ? ["Mj"] : [])];
    for (const b of beams) b.releases = toks.join(",");
    markDirty();
  };
  on("propRelMi", "change", applyReleases);
  on("propRelMj", "change", applyReleases);
  on("propUdlPat", "change", e => { store.loadPattern = e.target.value; renderProps(); });
  on("propUdlW", "change", e => {
    const w = parseFloat(e.target.value);
    if (!isFinite(w) || w < 0) return;
    for (const b of beams) ME.setMemberUdl(m, $("propUdlPat").value, b.uid, w);
    markDirty();
  });
  on("propShellSection", "change", e => {
    for (const s of shells) s.section = e.target.value;
    markDirty();
  });
  on("propBehavior", "change", e => {
    const v = e.target.value;
    for (const s of shells) s.behavior = (v === "membrane" && s.kind === "wall") ? "shell" : v;
    markDirty();
  });
  on("propMesh", "change", e => {
    const v = parseFloat(e.target.value);
    if (!isFinite(v) || v <= 0) return;
    for (const s of shells) s.mesh_size = v;
    markDirty();
  });
  on("propAreaPat", "change", e => { store.loadPattern = e.target.value; renderProps(); });
  on("propAreaQ", "change", e => {
    const q = parseFloat(e.target.value);
    if (!isFinite(q) || q < 0) return;
    for (const s of slabs) ME.setAreaLoad(m, $("propAreaPat").value, s.uid, q);
    markDirty();
  });

  /* v0.5 — openings wiring (single shell) */
  if (shells.length === 1) {
    const sh = shells[0];
    renderOpeningPreview(sh);
    on("openAdd", "click", () => {
      ME.addOpening(sh);
      markDirty();
      renderStaticViews();
      renderProps();
    });
    box.querySelectorAll("#openRows .open-row:not(.head)").forEach(row => {
      const i = parseInt(row.dataset.i, 10);
      const o = sh.openings[i];
      if (!o) return;
      row.querySelectorAll("input").forEach(inp => {
        inp.addEventListener("change", () => {
          const v = parseFloat(inp.value);
          if (!ME.setOpeningField(o, inp.dataset.k, v)) {
            inp.value = String(o[inp.dataset.k]);
            return;
          }
          // re-sync all four inputs — setOpeningField may swap bounds
          row.querySelectorAll("input").forEach(x => { x.value = String(o[x.dataset.k]); });
          markDirty();
          renderStaticViews();
          renderOpeningPreview(sh);
        });
      });
    });
    box.querySelectorAll(".open-del").forEach(btn =>
      btn.addEventListener("click", () => {
        sh.openings.splice(parseInt(btn.dataset.del, 10), 1);
        markDirty();
        renderStaticViews();
        renderProps();
      }));
  }

  /* v0.5 — link stiffness wiring */
  box.querySelectorAll(".linkK").forEach(inp =>
    inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      if (!isFinite(v) || v < 0) return;
      const i = parseInt(inp.dataset.si, 10);
      for (const l of links) l.stiffness[i] = v;
      markDirty();
    }));
}

/** v0.5 — mini SVG preview of a shell region with opening cutouts. */
function renderOpeningPreview(sh) {
  const svg = $("openPreview");
  if (!svg) return;
  const du = Math.hypot(
    sh.corners[1][0] - sh.corners[0][0],
    sh.corners[1][1] - sh.corners[0][1],
    sh.corners[1][2] - sh.corners[0][2]) || 1;
  const dv = Math.hypot(
    sh.corners[3][0] - sh.corners[0][0],
    sh.corners[3][1] - sh.corners[0][1],
    sh.corners[3][2] - sh.corners[0][2]) || 1;
  const W = 236, H = Math.max(48, Math.min(236, W * dv / du));
  const P = 6;                                    // padding
  svg.setAttribute("viewBox", `0 0 ${W + 2 * P} ${H + 2 * P}`);
  const x = u => P + u * W;
  const y = v => P + (1 - v) * H;                 // v = 0 at the bottom
  let d = `M${x(0)},${y(0)} L${x(1)},${y(0)} L${x(1)},${y(1)} L${x(0)},${y(1)} Z`;
  let holes = "";
  for (const o of (sh.openings || [])) {
    const sub = `M${x(o.u0)},${y(o.v0)} L${x(o.u1)},${y(o.v0)} L${x(o.u1)},${y(o.v1)} L${x(o.u0)},${y(o.v1)} Z`;
    d += " " + sub;
    holes += `<path d="${sub}" fill="none" stroke="rgba(53,181,229,0.75)" stroke-width="1" stroke-dasharray="4 3"/>`;
  }
  svg.innerHTML =
    `<path d="${d}" fill-rule="evenodd" fill="rgba(95,143,201,0.28)"
       stroke="rgba(125,168,216,0.9)" stroke-width="1.2"/>` + holes +
    `<text x="${P + 3}" y="${P + H - 4}" fill="rgba(140,160,185,0.75)" font-size="8"
       font-family="inherit">${esc(sh.uid)} · ${(sh.openings || []).length} opening${(sh.openings || []).length === 1 ? "" : "s"}</text>`;
}

/* ---- section manager modal */
const sci = v => (v == null || !isFinite(v)) ? "—" : Number(v).toExponential(2);

function openSectionMgr() {
  renderSectionMgr();
  $("sectionModal").classList.remove("hidden");
  // v0.3: lazy-load the section library on first open
  $("libRows").innerHTML = `<p class="lib-none">Loading library…</p>`;
  fetchSectionLibrary()
    .then(() => renderSectionLib())
    .catch(err => { $("libRows").innerHTML = `<p class="lib-none">Library unavailable — ${esc(err.message)}</p>`; });
}

function renderSectionLib() {
  const m = store.model;
  // material picker for newly added library sections
  const matSel = $("libMaterial");
  const prev = matSel.value;
  matSel.textContent = "";
  for (const n of Object.keys(m.materials)) {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    matSel.appendChild(o);
  }
  if (prev && m.materials[prev]) matSel.value = prev;

  const box = $("libRows");
  box.textContent = "";
  const lib = store.sectionLib || [];
  const q = store.libSearch.trim().toLowerCase();
  const rows = q ? lib.filter(e => e.name.toLowerCase().includes(q)) : lib;
  if (!rows.length) {
    box.innerHTML = `<p class="lib-none">No library sections match “${esc(store.libSearch)}”.</p>`;
    return;
  }
  box.appendChild(mgrRow(["Name", "A (m²)", "I33 (m⁴)", "I22 (m⁴)", ""], "mgr-row lib head"));
  for (const entry of rows) {
    const exists = !!m.sections[entry.name];
    const btn = document.createElement("button");
    btn.className = "btn btn-small";
    btn.textContent = exists ? "Added" : "+ Add";
    btn.disabled = exists;
    btn.title = exists ? "Already a frame section in this model"
      : `Add ${entry.name} as a frame section (${matSel.value || "default material"})`;
    btn.addEventListener("click", () => {
      const name = ME.addLibraryFrameSection(m, entry, matSel.value || ME.defaultMaterial(m));
      if (name) {
        markDirty();
        renderSectionMgr();
        renderSectionLib();
        toast("Section added", `${name} is now available in all frame-section dropdowns`, "info", 4000);
      }
    });
    box.appendChild(mgrRow(
      [entry.name, sci(entry.A), sci(entry.I33), sci(entry.I22), btn],
      "mgr-row lib" + (exists ? " added" : "")));
  }
}
function closeSectionMgr() {
  $("sectionModal").classList.add("hidden");
  renderStaticViews();         // column plan sizes may have changed
  renderProps();               // dropdown option lists may have changed
}

function mgrRow(cells, cls = "mgr-row") {
  const div = document.createElement("div");
  div.className = cls;
  for (const c of cells) {
    if (typeof c === "string") {
      const s = document.createElement("span");
      s.textContent = c;
      div.appendChild(s);
    } else div.appendChild(c);
  }
  return div;
}
const mgrInput = (value, attrs = {}) => {
  const i = document.createElement("input");
  Object.assign(i, { type: "text", value }, attrs);
  return i;
};
const mgrNum = (value, step, onChange) => {
  const i = mgrInput(String(value), { type: "number" });
  i.step = step;
  i.addEventListener("change", () => {
    const v = parseFloat(i.value);
    if (isFinite(v) && v > 0) { onChange(v); markDirty(); }
    else i.value = String(value);
  });
  return i;
};
const mgrMatSelect = (model, value, onChange) => {
  const s = document.createElement("select");
  for (const n of Object.keys(model.materials)) {
    const o = document.createElement("option");
    o.value = n; o.textContent = n; o.selected = n === value;
    s.appendChild(o);
  }
  s.addEventListener("change", () => { onChange(s.value); markDirty(); });
  return s;
};
const mgrDel = (disabled, title, onDel) => {
  const b = document.createElement("button");
  b.className = "del"; b.textContent = "✕"; b.title = title; b.disabled = disabled;
  b.addEventListener("click", onDel);
  return b;
};

/** v0.4 — collapsed stiffness-modifier row for a frame section. */
function frameModsDetails(s) {
  const det = document.createElement("details");
  det.className = "mgr-mods";
  const sum = document.createElement("summary");
  const fm = v => (v == null || !isFinite(v)) ? "1" : String(+(+v).toFixed(3));
  const syncSum = () => {
    sum.textContent = "Modifiers · " +
      `A ×${fm(s.mod_A)} · I33 ×${fm(s.mod_I33)} · I22 ×${fm(s.mod_I22)} · J ×${fm(s.mod_J)}`;
  };
  syncSum();
  const row = document.createElement("div");
  row.className = "mods-row";
  for (const [key, lbl] of [["mod_A", "mod A"], ["mod_I33", "mod I33"],
                            ["mod_I22", "mod I22"], ["mod_J", "mod J"]]) {
    const wrap = document.createElement("label");
    const span = document.createElement("span");
    span.textContent = lbl;
    const i = mgrInput(fm(s[key]), { type: "number" });
    i.step = "0.05"; i.min = "0.01";
    i.title = "Stiffness modifier (multiplies the section property; default 1.0)";
    i.addEventListener("change", () => {
      const v = parseFloat(i.value);
      if (isFinite(v) && v > 0) { s[key] = v; markDirty(); syncSum(); }
      else i.value = fm(s[key]);
    });
    wrap.append(span, i);
    row.appendChild(wrap);
  }
  det.append(sum, row);
  return det;
}

function renderSectionMgr() {
  const m = store.model;

  const frameBox = $("frameSectionRows");
  frameBox.textContent = "";
  frameBox.appendChild(mgrRow(["Name", "b (m)", "h (m)", "Material", ""], "mgr-row head"));
  for (const [name, s] of Object.entries(m.sections)) {
    const nameIn = mgrInput(name);
    nameIn.addEventListener("change", () => {
      if (!ME.renameFrameSection(m, name, nameIn.value.trim())) {
        nameIn.value = name;
        toast("Rename failed", "Name empty or already in use", "error", 4000);
      } else { markDirty(); renderSectionMgr(); }
    });
    const used = ME.sectionInUse(m, name);
    const del = mgrDel(used, used ? "In use by members" : "Delete section", () => {
      delete m.sections[name]; markDirty(); renderSectionMgr();
    });
    const isLibrary = s.shape === "W" || !(s.b > 0 && s.h > 0);
    if (!isLibrary) {           // rectangular — b/h editable
      frameBox.appendChild(mgrRow([
        nameIn,
        mgrNum(s.b, "0.05", v => s.b = v),
        mgrNum(s.h, "0.05", v => s.h = v),
        mgrMatSelect(m, s.material, v => s.material = v),
        del,
      ]));
    } else {
      // v0.3: property-based section from the library (A / I33 / I22 / J)
      const props = document.createElement("span");
      props.className = "sec-props";
      props.title = `A ${sci(s.A)} m² · I33 ${sci(s.I33)} m⁴ · I22 ${sci(s.I22)} m⁴ · J ${sci(s.J)} m⁴`;
      props.textContent = `${s.shape === "W" ? "W-shape" : "library"} · A ${sci(s.A)} · I33 ${sci(s.I33)}`;
      frameBox.appendChild(mgrRow([
        nameIn,
        props,
        mgrMatSelect(m, s.material, v => s.material = v),
        del,
      ]));
    }
    frameBox.appendChild(frameModsDetails(s));   // v0.4 stiffness modifiers
  }

  const shellBox = $("shellSectionRows");
  shellBox.textContent = "";
  shellBox.appendChild(mgrRow(["Name", "Thickness (m)", "mod", "Material", ""], "mgr-row head"));
  for (const [name, s] of Object.entries(m.shell_sections)) {
    const nameIn = mgrInput(name);
    nameIn.addEventListener("change", () => {
      if (!ME.renameShellSection(m, name, nameIn.value.trim())) {
        nameIn.value = name;
        toast("Rename failed", "Name empty or already in use", "error", 4000);
      } else { markDirty(); renderSectionMgr(); }
    });
    const used = ME.shellSectionInUse(m, name);
    const modIn = mgrNum(s.mod ?? 1, "0.05", v => s.mod = v);
    modIn.title = "Stiffness modifier (multiplies the shell stiffness; default 1.0)";
    shellBox.appendChild(mgrRow([
      nameIn,
      mgrNum(s.thickness, "0.025", v => s.thickness = v),
      modIn,
      mgrMatSelect(m, s.material, v => s.material = v),
      mgrDel(used, used ? "In use by shell regions" : "Delete shell section", () => {
        delete m.shell_sections[name]; markDirty(); renderSectionMgr();
      }),
    ]));
  }

  const matBox = $("materialRows");
  matBox.textContent = "";
  matBox.appendChild(mgrRow(["Name", "E (kPa)", "Poisson ν", "", ""], "mgr-row head"));
  for (const [name, mat] of Object.entries(m.materials)) {
    const nameIn = mgrInput(name);
    nameIn.addEventListener("change", () => {
      if (!ME.renameMaterial(m, name, nameIn.value.trim())) {
        nameIn.value = name;
        toast("Rename failed", "Name empty or already in use", "error", 4000);
      } else { markDirty(); renderSectionMgr(); }
    });
    const nuIn = mgrInput(String(mat.nu), { type: "number" });
    nuIn.step = "0.05";
    nuIn.addEventListener("change", () => {
      const v = parseFloat(nuIn.value);
      if (isFinite(v) && v >= 0 && v < 0.5) { mat.nu = v; markDirty(); }
      else nuIn.value = String(mat.nu);
    });
    const used = ME.materialInUse(m, name);
    matBox.appendChild(mgrRow([
      nameIn,
      mgrNum(mat.E, "1000000", v => mat.E = v),
      nuIn,
      "",
      mgrDel(used, used ? "In use by sections" : "Delete material", () => {
        delete m.materials[name]; markDirty(); renderSectionMgr();
      }),
    ]));
  }
}

/* ================================================================
   v0.4 — GRID & STORY EDITOR
   ================================================================ */
function openGridEditor() {
  renderGridEditor();
  $("gridModal").classList.remove("hidden");
}

function closeGridEditor() {
  $("gridModal").classList.add("hidden");
  rebuildStorySelect();
  rebuildElevSelect();
  refreshDrawViews();
  renderProps();
  renderSummary();
}

/** Geometry changed: refresh model views + re-render the editor. */
function afterGeometryEdit() {
  markDirty();
  rebuildStorySelect();
  rebuildElevSelect();
  refreshDrawViews();
  renderSummary();
  renderGridEditor();
}

function renderGridEditor() {
  const m = store.model;
  if (!m || !m.grid) return;

  const mkLines = (axis, box) => {
    box.textContent = "";
    const lines = axis === "x" ? m.grid.x_lines : m.grid.y_lines;
    const labels = axis === "x" ? m.grid.x_labels : m.grid.y_labels;
    lines.forEach((v, i) => {
      const row = document.createElement("div");
      row.className = "ge-line";
      const lab = document.createElement("span");
      lab.className = "ge-label";
      lab.textContent = (labels && labels[i]) || String(i + 1);
      const inp = document.createElement("input");
      inp.type = "number"; inp.step = "0.5";
      inp.value = String(v);
      inp.addEventListener("change", () => {
        const nv = parseFloat(inp.value);
        if (ME.setGridLine(m, axis, i, nv)) afterGeometryEdit();
        else {
          inp.value = String(v);
          toast("Grid edit rejected", "Positions must be numbers and can't collide with another line", "error", 4500);
        }
      });
      const del = document.createElement("button");
      del.className = "del"; del.textContent = "✕";
      const blocked = lines.length <= 2;
      del.disabled = blocked;
      del.title = blocked ? "A grid keeps at least two lines per direction"
        : "Remove line — members outside the grid are kept";
      del.addEventListener("click", () => {
        if (ME.removeGridLine(m, axis, i)) {
          afterGeometryEdit();
          toast("Grid line removed", "Members outside the grid are kept", "info", 3500);
        }
      });
      row.append(lab, inp, del);
      box.appendChild(row);
    });
  };
  mkLines("x", $("gridXRows"));
  mkLines("y", $("gridYRows"));

  /* stories — top → bottom, like the story selector */
  const box = $("storyRows");
  box.textContent = "";
  const head = document.createElement("div");
  head.className = "ge-story head";
  head.innerHTML = `<span>Name</span><span>Height m</span>
    <span style="text-align:right">Elev m</span><span>Diaphragm</span><span>Insert</span><span></span>`;
  box.appendChild(head);

  for (const st of [...m.stories].reverse()) {
    const row = document.createElement("div");
    row.className = "ge-story";

    const nameIn = document.createElement("input");
    nameIn.type = "text"; nameIn.value = st.name; nameIn.spellcheck = false;
    nameIn.addEventListener("change", () => {
      const nu = nameIn.value.trim();
      if (ME.renameStory(m, st.name, nu)) afterGeometryEdit();
      else {
        nameIn.value = st.name;
        toast("Rename failed", "Name empty or already in use", "error", 4000);
      }
    });

    const hIn = document.createElement("input");
    hIn.type = "number"; hIn.step = "0.1"; hIn.min = "0.5";
    hIn.value = String(st.height);
    hIn.addEventListener("change", () => {
      const v = parseFloat(hIn.value);
      if (ME.setStoryHeight(m, st.name, v)) {
        store.modelEdited = true;
        afterGeometryEdit();
      } else hIn.value = String(st.height);
    });

    const elev = document.createElement("span");
    elev.className = "ge-elev";
    elev.textContent = `${fmt(st.elevation - st.height, 1)} – ${fmt(st.elevation, 1)}`;

    // v0.5 — per-story diaphragm override (blank = model default)
    const dsel = document.createElement("select");
    dsel.className = "ge-diaph";
    dsel.title = `Diaphragm override for ${st.name} — default follows the model-wide setting (${m.diaphragm || "rigid"})`;
    dsel.innerHTML = `<option value="">default</option>
      <option value="rigid">rigid</option><option value="none">none</option>`;
    dsel.value = (m.story_diaphragm || {})[st.name] || "";
    dsel.addEventListener("change", () => {
      m.story_diaphragm = m.story_diaphragm || {};
      if (!dsel.value) delete m.story_diaphragm[st.name];
      else m.story_diaphragm[st.name] = dsel.value;
      markDirty();
    });

    const ins = document.createElement("span");
    ins.className = "ge-ins";
    const mkIns = (where, glyph, title) => {
      const b = document.createElement("button");
      b.className = "chip"; b.textContent = glyph; b.title = title;
      b.addEventListener("click", () => {
        const name = ME.insertStory(m, st.name, where);
        if (name) {
          store.modelEdited = true;
          afterGeometryEdit();
          toast("Story inserted", `${name} added ${where} ${st.name}`, "info", 3500);
        }
      });
      return b;
    };
    ins.append(
      mkIns("above", "▲+", `Insert an empty story above ${st.name}`),
      mkIns("below", "▼+", `Insert an empty story below ${st.name}`));

    const del = document.createElement("button");
    del.className = "del"; del.textContent = "✕";
    if (m.stories.length <= 1) {
      del.disabled = true;
      del.title = "The last story can't be deleted";
    } else {
      del.title = `Delete ${st.name} (its members are deleted too)`;
      del.addEventListener("click", async () => {
        const n = ME.storyElementCounts(m, st.name);
        const ok = await askConfirm("Delete story",
          `Delete ${st.name}? ${n.members} member${n.members === 1 ? "" : "s"} and ` +
          `${n.shells} shell region${n.shells === 1 ? "" : "s"} on it will be deleted; ` +
          `stories above translate down.`, "Delete story");
        if (!ok) return;
        if (ME.deleteStory(m, st.name)) {
          store.modelEdited = true;
          store.selection = [];
          afterGeometryEdit();
          toast("Story deleted", `${st.name} removed with its elements`, "info", 4000);
        }
      });
    }

    row.append(nameIn, hIn, elev, dsel, ins, del);
    box.appendChild(row);
  }
}

/* ---- save / discard */
async function saveModel() {
  const btn = $("saveBtn");
  if (btn.disabled) return;
  btn.disabled = true;
  $("saveSpinner").classList.remove("hidden");
  try {
    const payload = JSON.parse(JSON.stringify(store.model));
    delete payload._mock_params;
    const echoed = await postModel(payload);
    if (echoed && !store.mock) store.model = ME.normalizeModel(echoed);
    clearDirty();
    toast("Model saved", store.mock
      ? "Accepted locally (mock mode)"
      : "POST /api/model accepted by the backend", "info", 4000);
    viewer.setModel(store.model);
    store.modelEdited = false;
    syncShellLegend();
    rebuildStorySelect();
    rebuildElevSelect();
    refreshDrawViews();
    renderProps();
    renderSummary();
    if (store.mode === "loads") { loadsEditor.render(); syncLoadsNav(); }
  } catch (err) {
    toast("Save failed", err.message, "error", 8000);
  } finally {
    btn.disabled = false;
    $("saveSpinner").classList.add("hidden");
  }
}

async function discardModel() {
  const btn = $("discardBtn");
  btn.disabled = true;
  try {
    store.model = ME.normalizeModel(await fetchModel());
    store.selection = [];
    clearDirty();
    store.modelEdited = false;
    viewer.setModel(store.model);
    syncShellLegend();
    rebuildStorySelect();
    rebuildElevSelect();
    syncDiaphragmUI();
    refreshDrawViews();
    renderProps();
    renderSummary();
    if (store.mode === "loads") { loadsEditor.render(); syncLoadsNav(); }
    toast("Model reloaded", "Local edits discarded", "info", 4000);
  } catch (err) {
    toast("Reload failed", err.message, "error");
  } finally {
    btn.disabled = false;
  }
}

/* ================================================================
   v0.3 — MODEL FILES (File menu: New / Open / Save / Save As)
   ================================================================ */
const FILE_NAME_RE = /^[A-Za-z0-9 _-]+$/;

/** Replace the working model and reset all dependent state. */
function adoptModel(modelDict, fileName) {
  store.model = ME.normalizeModel(modelDict);
  store.fileName = fileName || null;
  store.results = null;
  store.overlay = { deformed: false, modal: false, modeIndex: 0, scaleMult: 1 };
  store.lastSolveMs = null;
  store.selection = [];
  store.selectedMemberUid = null;
  store.modelEdited = false;
  store.contour = { on: false, comp: "M11" };     // v0.4
  store.envSide = "max";
  store.thCase = null;
  store.thStory = null;
  store.poCase = null;                            // v0.5
  clearDirty();
  closeMemberPanel();
  viewer.setResults(null);
  viewer.setModel(store.model);
  syncShellLegend();
  rebuildStorySelect();
  rebuildElevSelect();
  syncDiaphragmUI();
  refreshDrawViews();
  renderProps();
  syncOverlayUI();
  rebuildCaseSelect();
  setResultsAvailable(false);
  renderSummary();
  syncDirtyUI();
  if (store.mode === "loads") { loadsEditor.render(); syncLoadsNav(); }
  setStatus("ready", "Ready");
}

/* ---- tiny promise-based confirm modal */
let confirmResolve = null;
function askConfirm(title, msg, okLabel = "OK") {
  $("confirmTitle").textContent = title;
  $("confirmMsg").textContent = msg;
  $("confirmOk").textContent = okLabel;
  $("confirmModal").classList.remove("hidden");
  $("confirmOk").focus();
  return new Promise(res => { confirmResolve = res; });
}
function settleConfirm(ok) {
  if (!confirmResolve) return;
  $("confirmModal").classList.add("hidden");
  confirmResolve(ok);
  confirmResolve = null;
}

/* ---- File menu */
function toggleFileMenu(open) {
  const want = open !== undefined ? open : $("fileMenu").classList.contains("hidden");
  $("fileMenu").classList.toggle("hidden", !want);
  $("fileMenuBtn").setAttribute("aria-expanded", String(want));
}

async function fileNew() {
  const ok = await askConfirm("New model",
    (store.dirty ? "You have unsaved changes. " : "") +
    "Start a new model with quick-building defaults?", "New model");
  if (!ok) return;
  try {
    adoptModel(await generateModel({}), null);
    toast("New model", "Quick-building defaults loaded", "info", 4000);
  } catch (err) {
    toast("New model failed", err.message, "error");
  }
}

/* ---- Open dialog */
function fmtMtime(t) {
  if (!isFinite(t)) return "—";
  const d = new Date(t * 1000);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) +
    " " + d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
}

async function openFileDialog() {
  $("openModal").classList.remove("hidden");
  $("filesTable").innerHTML = `<tbody><tr><td class="txt dim">Loading…</td></tr></tbody>`;
  $("filesEmpty").classList.add("hidden");
  try {
    const files = await filesApi.list();
    renderFilesTable(files);
  } catch (err) {
    $("filesTable").innerHTML = "";
    toast("Couldn't list models", err.message, "error");
  }
}

function renderFilesTable(files) {
  $("filesMockNote").classList.toggle("hidden", !filesUsingMock);
  const table = $("filesTable");
  if (!files || !files.length) {
    table.innerHTML = "";
    $("filesEmpty").classList.remove("hidden");
    return;
  }
  $("filesEmpty").classList.add("hidden");
  table.innerHTML = `<thead><tr>
    <th class="txt">Name</th><th class="txt">Modified</th>
    <th>Stories</th><th>Members</th><th class="txt"></th></tr></thead>
    <tbody>` + files.map(f => `<tr data-name="${esc(f.name)}">
      <td class="txt"><b>${esc(f.name)}</b></td>
      <td class="txt dim">${esc(fmtMtime(f.mtime))}</td>
      <td>${fmt(f.stories, 0)}</td>
      <td>${fmt(f.members, 0)}</td>
      <td class="txt file-open">
        <button class="btn btn-small file-open-btn">Open</button>
        <button class="btn btn-small file-del-btn" title="Delete this saved model">Delete</button>
      </td></tr>`).join("") + `</tbody>`;

  table.querySelectorAll(".file-open-btn").forEach(btn =>
    btn.addEventListener("click", () => doOpenFile(btn.closest("tr").dataset.name)));
  table.querySelectorAll(".file-del-btn").forEach(btn =>
    btn.addEventListener("click", async () => {
      // two-click inline confirm
      if (!btn.classList.contains("del-confirm")) {
        btn.classList.add("del-confirm");
        btn.textContent = "Confirm?";
        setTimeout(() => {
          btn.classList.remove("del-confirm");
          btn.textContent = "Delete";
        }, 2600);
        return;
      }
      const name = btn.closest("tr").dataset.name;
      try {
        await filesApi.remove(name);
        if (store.fileName === name) { store.fileName = null; syncDirtyUI(); }
        toast("Deleted", `Saved model “${name}” removed`, "info", 3500);
        renderFilesTable(await filesApi.list());
      } catch (err) {
        toast("Delete failed", err.message, "error");
      }
    }));
}

async function doOpenFile(name) {
  if (store.dirty) {
    const ok = await askConfirm("Open model",
      `You have unsaved changes — open “${name}” and discard them?`, "Open anyway");
    if (!ok) return;
  }
  try {
    const m = await filesApi.open(name);
    $("openModal").classList.add("hidden");
    adoptModel(m, name);
    toast("Model opened", `“${name}” is now the working model`, "info", 4000);
  } catch (err) {
    toast("Open failed", err.message, "error");
  }
}

/* ---- Save / Save As */
async function saveToFile(name) {
  // sync the working model to the backend, then persist it under `name`
  const payload = JSON.parse(JSON.stringify(store.model));
  delete payload._mock_params;
  await postModel(payload);
  await filesApi.save(name);
  store.fileName = name;
  clearDirty();
  toast("Model saved", filesUsingMock
    ? `“${name}” saved to the mock file store`
    : `“${name}” saved via POST /api/models`, "info", 4000);
}

function fileSave() {
  if (!store.fileName) { openSaveAs(); return; }
  saveToFile(store.fileName).catch(err => toast("Save failed", err.message, "error", 8000));
}

function openSaveAs() {
  $("saveAsName").value = store.fileName || store.model?.name || "";
  $("saveAsError").classList.add("hidden");
  $("saveAsModal").classList.remove("hidden");
  validateSaveAs();
  $("saveAsName").focus();
  $("saveAsName").select();
}

function validateSaveAs() {
  const v = $("saveAsName").value.trim();
  const ok = FILE_NAME_RE.test(v);
  $("saveAsError").classList.toggle("hidden", ok || v === "");
  $("saveAsOk").disabled = !ok;
  return ok ? v : null;
}

async function submitSaveAs() {
  const name = validateSaveAs();
  if (!name) { $("saveAsError").classList.remove("hidden"); return; }
  $("saveAsOk").disabled = true;
  try {
    await saveToFile(name);
    $("saveAsModal").classList.add("hidden");
  } catch (err) {
    toast("Save failed", err.message, "error", 8000);
  } finally {
    $("saveAsOk").disabled = false;
  }
}

/* ================================================================
   v0.2 — MEMBER DETAIL PANEL (Analyze mode)
   ================================================================ */
const DIAG = [
  { key: "N", title: "N — axial", unit: "kN", color: "#34c384" },
  { key: "V2", title: "V2 — shear", unit: "kN", color: "#1e9ad4" },
  { key: "M3", title: "M3 — moment", unit: "kN·m", color: "#d55181" },
];
const DIAG_MINOR = [
  { key: "V3", title: "V3 — minor shear", unit: "kN", color: "#77879b" },
  { key: "M2", title: "M2 — minor moment", unit: "kN·m", color: "#77879b" },
  { key: "T", title: "T — torsion", unit: "kN·m", color: "#77879b" },
];

function onMemberClick(seg) {
  if (store.mode !== "analyze" || !store.results) return;
  if (!seg) return;
  store.selectedMemberUid = seg.uid;
  renderMemberPanel();
}

function closeMemberPanel() {
  store.selectedMemberUid = null;
  $("memberPanel").classList.add("hidden");
  viewer._resize();
}

function renderMemberPanel() {
  const uid = store.selectedMemberUid;
  const panel = $("memberPanel");
  const r = store.results;
  if (!uid || !r) { panel.classList.add("hidden"); return; }
  const rm = (r.members || []).find(x => x.uid === uid);
  if (!rm) { panel.classList.add("hidden"); return; }
  const wasHidden = panel.classList.contains("hidden");
  panel.classList.remove("hidden");
  if (wasHidden) viewer._resize();

  $("memberPanelTitle").textContent = uid;
  const pi = r.nodes[rm.ni], pj = r.nodes[rm.nj];
  const L = (pi && pj) ? Math.hypot(pj[0] - pi[0], pj[1] - pi[1], pj[2] - pi[2]) : null;
  $("memberMeta").innerHTML = [
    ["Kind", rm.kind], ["Section", rm.section],
    ["Story", rm.story], ["Length", L != null ? `${fmt(L, 2)} m` : "—"],
  ].map(([k, v]) => `<div><dt>${esc(k)}</dt><dd title="${esc(v)}">${esc(v)}</dd></div>`).join("");

  const cd = caseData();
  const st = cd && cd.member_stations && cd.member_stations[uid];
  const main = $("memberDiagrams"), minor = $("memberDiagramsMinor");
  main.textContent = ""; minor.textContent = "";
  $("memberEmpty").classList.toggle("hidden", !!st);
  $("memberMinor").classList.toggle("hidden", !st);
  const envBadge = isRsCase(store.caseName)
    ? ` <span class="env-badge" title="Response-spectrum values are positive envelopes — signs are indeterminate">envelope ±</span>` : "";
  $("memberCaseNote").innerHTML = (st
    ? `Station diagrams · case <b>${esc(caseLabel(store.caseName))}</b>`
    : `Case <b>${esc(caseLabel(store.caseName))}</b>`) + envBadge;
  if (!st) return;
  const vals = k => (st[k] && st[k].length === st.x.length) ? st[k] : st.x.map(() => 0);
  for (const d of DIAG)
    main.appendChild(stationDiagram(st.x, vals(d.key), d));
  for (const d of DIAG_MINOR)
    minor.appendChild(stationDiagram(st.x, vals(d.key), d));
}

/* ------------------------------------------------ renders */
function renderSummary() {
  const m = store.model;
  if (!m) return;
  $("modelName").textContent = m.name || "—";
  $("modelSummary").hidden = false;
  $("sum-stories").textContent = m.stories.length;
  $("sum-members").textContent = m.members.length;
  const g = m.grid;
  $("sum-footprint").textContent = g ?
    `${fmt(g.x_lines[g.x_lines.length - 1] - g.x_lines[0], 0)} × ${fmt(g.y_lines[g.y_lines.length - 1] - g.y_lines[0], 0)} m` : "—";
  const H = m.stories.length ? m.stories[m.stories.length - 1].elevation : 0;
  $("sum-height").textContent = `${fmt(H, 1)} m`;
  const mass = Object.values(m.story_masses || {}).reduce((a, b) => a + b, 0);
  $("sum-mass").textContent = `${fmt(mass, 1)} t`;
  $("sum-base").textContent = m.base_fixity;
  const nShells = (m.shells || []).length;
  $("footerInfo").textContent =
    `${m.members.length} members` +
    (nShells ? ` · ${nShells} shell${nShells > 1 ? "s" : ""}` : "") +
    ` · ${m.stories.length} stories` +
    (store.lastSolveMs != null ? ` · solved in ${fmt(store.lastSolveMs / 1000, 1)} s` : "");
}

function setResultsAvailable(on) {
  for (const t of ["story", "modal", "reactions", "forces"]) {
    $(`empty-${t}`).classList.toggle("hidden", on);
    $(`content-${t}`).classList.toggle("hidden", !on);
  }
  $("chipDeformed").disabled = !on;
  $("chipMode").disabled = !on;
  $("reportBtn").disabled = !on;                                  // v0.4
  const hasTh = on && !!Object.keys(store.results?.th_cases || {}).length;
  $("thTabBtn").classList.toggle("hidden", !hasTh);
  $("empty-th").classList.toggle("hidden", hasTh);
  $("content-th").classList.toggle("hidden", !hasTh);
  if (!hasTh && store.tab === "th") switchTab("view3d");
  // v0.5: pushover tab appears only when results carry pushover curves
  const hasPo = on && !!Object.keys(store.results?.pushover || {}).length;
  $("poTabBtn").classList.toggle("hidden", !hasPo);
  $("empty-pushover").classList.toggle("hidden", hasPo);
  $("content-pushover").classList.toggle("hidden", !hasPo);
  if (!hasPo && store.tab === "pushover") switchTab("view3d");
  if (!on) {
    store.contour.on = false;
    syncContoursUI();
    syncEnvToggle();
  }
}

function renderResultsTabs() {
  if (!store.results || !caseData()) return;
  syncEnvToggle();
  renderStoryTab();
  renderModalTab();
  renderReactionsTab();
  renderForcesTab();
  renderThTab();
  renderPoTab();
}

/* ---- story tab */
function renderStoryTab() {
  const r = store.results, cd = tableCaseData();
  if (!cd) return;
  renderStoryCharts($("chartsRow"), r, cd, store.driftLimitPct);

  const limRatio = store.driftLimitPct / 100;
  const head = `<thead><tr>
    <th class="txt">Story</th><th>Elev m</th>
    <th>ux mm</th><th>uy mm</th>
    <th>drift ‰ x</th><th>drift ‰ y</th>
    <th>Vx kN</th><th>Vy kN</th></tr></thead>`;
  const rows = [...r.story_order].reverse().map(s => {
    const st = cd.story[s] || {};
    const exx = Math.abs(st.drift_x || 0) > limRatio, exy = Math.abs(st.drift_y || 0) > limRatio;
    return `<tr>
      <td class="txt">${esc(s)}</td>
      <td class="dim">${fmt(r.story_elev[s], 1)}</td>
      <td>${fmt((st.ux || 0) * 1000, 1)}</td>
      <td>${fmt((st.uy || 0) * 1000, 1)}</td>
      <td class="${exx ? "exceed" : ""}">${fmt(Math.abs(st.drift_x || 0) * 1000, 2)}</td>
      <td class="${exy ? "exceed" : ""}">${fmt(Math.abs(st.drift_y || 0) * 1000, 2)}</td>
      <td>${fmt(st.shear_x || 0, 1)}</td>
      <td>${fmt(st.shear_y || 0, 1)}</td></tr>`;
  }).join("");
  $("storyTable").innerHTML = head + `<tbody>${rows}</tbody>`;
}

/* ---- modal tab */
function renderModalTab() {
  const modal = store.results.modal;
  if (!modal || !modal.periods || !modal.periods.length) {
    $("modalTable").innerHTML = `<tbody><tr><td class="txt dim">No modal results</td></tr></tbody>`;
    return;
  }
  const bar = (v, cls = "") =>
    `<span class="part-bar-wrap"><span class="part-bar"><i class="${cls}" style="width:${Math.min(100, v * 100).toFixed(1)}%"></i></span>${fmt(v * 100, 1)}</span>`;
  // v0.3: participation factors Γx / Γy, when the backend provides them
  const hasGamma = modal.participation.some(p => p.gamma_x != null || p.gamma_y != null);
  let cumX = 0, cumY = 0;
  const rows = modal.participation.map((p, i) => {
    cumX += p.ux || 0; cumY += p.uy || 0;
    return `<tr>
      <td class="txt">${p.mode}</td>
      <td>${fmt(modal.periods[i], 3)}</td>
      <td>${fmt(modal.frequencies[i], 2)}</td>
      <td>${bar(p.ux || 0)}</td>
      <td>${bar(p.uy || 0, "py")}</td>
      <td>${bar(p.rz || 0, "pr")}</td>
      ${hasGamma ? `<td>${fmt(p.gamma_x ?? 0, 2)}</td><td>${fmt(p.gamma_y ?? 0, 2)}</td>` : ""}
      <td class="dim">${fmt(cumX * 100, 1)}</td>
      <td class="dim">${fmt(cumY * 100, 1)}</td>
      <td class="txt"><button class="link-3d" data-mode="${i}">view in 3D →</button></td>
    </tr>`;
  }).join("");
  $("modalTable").innerHTML = `<thead><tr>
    <th class="txt">Mode</th><th>T s</th><th>f Hz</th>
    <th>UX %</th><th>UY %</th><th>RZ %</th>
    ${hasGamma ? `<th title="Modal participation factor, X">Γx</th><th title="Modal participation factor, Y">Γy</th>` : ""}
    <th>Σ UX %</th><th>Σ UY %</th><th class="txt"></th></tr></thead>
    <tbody>${rows}</tbody>`;
  $("modalTable").querySelectorAll(".link-3d").forEach(btn =>
    btn.addEventListener("click", () => {
      viewModeIn3D(parseInt(btn.dataset.mode, 10));
    }));
}

function viewModeIn3D(idx) {
  store.overlay.modal = true;
  store.overlay.deformed = false;
  store.overlay.modeIndex = idx;
  $("modeSelect").value = String(idx);
  syncOverlayUI();
  switchTab("view3d");
}

/* ---- reactions tab */
function renderReactionsTab() {
  const r = store.results, cd = tableCaseData();
  if (!cd) return;
  $("reactionsNote").textContent =
    `Support reactions · ${caseLabel(store.caseName)}` +
    (caseData()?.min ? ` · envelope ${store.envSide}` : "") +
    (isRsCase(store.caseName) ? " · envelope ±" : "");
  const head = `<thead><tr>
    <th class="txt">Node</th><th>X m</th><th>Y m</th>
    <th>FX kN</th><th>FY kN</th><th>FZ kN</th>
    <th>MX kN·m</th><th>MY kN·m</th><th>MZ kN·m</th></tr></thead>`;
  const tags = (r.supports || []).slice().sort((a, b) => {
    const pa = r.nodes[a] || [0, 0], pb = r.nodes[b] || [0, 0];
    return pa[1] - pb[1] || pa[0] - pb[0];
  });
  const rows = tags.map(t => {
    const p = r.nodes[t] || [0, 0, 0];
    const f = (cd.reactions && cd.reactions[t]) || [0, 0, 0, 0, 0, 0];
    return `<tr><td class="txt">${esc(t)}</td>
      <td class="dim">${fmt(p[0], 1)}</td><td class="dim">${fmt(p[1], 1)}</td>
      ${f.map(v => `<td>${fmt(v, 1)}</td>`).join("")}</tr>`;
  }).join("");
  const b = cd.base || {};
  const totals = `<tr class="totals">
    <td class="txt">Σ base</td><td></td><td></td>
    <td>${fmt(b.FX, 1)}</td><td>${fmt(b.FY, 1)}</td><td>${fmt(b.FZ, 1)}</td>
    <td>${fmt(b.MX, 1)}</td><td>${fmt(b.MY, 1)}</td><td>${fmt(b.MZ, 1)}</td></tr>`;
  $("reactionsTable").innerHTML = head + `<tbody>${rows}${totals}</tbody>`;
}

/* ---- member forces tab */
function forcesRows() {
  const r = store.results, cd = tableCaseData();
  if (!cd || !cd.member_forces) return [];
  const rows = [];
  for (const m of r.members) {
    const f = cd.member_forces[m.uid];
    if (!f) continue;
    rows.push({
      uid: m.uid, kind: m.kind, story: m.story, section: m.section,
      N: Math.max(Math.abs(f[0]), Math.abs(f[6])),
      V2: Math.max(Math.abs(f[1]), Math.abs(f[7])),
      M3: Math.max(Math.abs(f[5]), Math.abs(f[11])),
    });
  }
  const q = store.forcesFilter.trim().toLowerCase();
  const filtered = q ? rows.filter(x =>
    x.uid.toLowerCase().includes(q) || x.kind.toLowerCase().includes(q) ||
    x.story.toLowerCase().includes(q)) : rows;
  const { key, dir } = store.forcesSort;
  filtered.sort((a, b) => {
    const va = a[key], vb = b[key];
    if (typeof va === "string") return va.localeCompare(vb, undefined, { numeric: true }) * dir;
    return (va - vb) * dir;
  });
  return filtered;
}

const FORCE_COLS = [
  { key: "uid", label: "Member", txt: true },
  { key: "kind", label: "Kind", txt: true },
  { key: "story", label: "Story", txt: true },
  { key: "section", label: "Section", txt: true },
  { key: "N", label: "|N|max kN" },
  { key: "V2", label: "|V2|max kN" },
  { key: "M3", label: "|M3|max kN·m" },
];

function renderForcesTab() {
  const rows = forcesRows();
  const { key: sk, dir } = store.forcesSort;
  const head = `<thead><tr>` + FORCE_COLS.map(c =>
    `<th class="sortable ${c.txt ? "txt" : ""}" data-key="${c.key}">${c.label}` +
    (c.key === sk ? `<span class="sort-arrow">${dir > 0 ? "▲" : "▼"}</span>` : "") +
    `</th>`).join("") + `</tr></thead>`;
  const body = rows.map(x => `<tr>
    <td class="txt">${esc(x.uid)}</td>
    <td class="txt dim">${esc(x.kind)}</td>
    <td class="txt dim">${esc(x.story)}</td>
    <td class="txt dim">${esc(x.section)}</td>
    <td>${fmt(x.N, 1)}</td><td>${fmt(x.V2, 1)}</td><td>${fmt(x.M3, 1)}</td></tr>`).join("");
  $("forcesTable").innerHTML = head + `<tbody>${body}</tbody>`;
  $("forcesCount").textContent = `${rows.length} members · ${caseLabel(store.caseName)}` +
    (caseData()?.min ? ` · envelope ${store.envSide}` : "") +
    (isRsCase(store.caseName) ? " · envelope ±" : "");
  $("forcesTable").querySelectorAll("th.sortable").forEach(th =>
    th.addEventListener("click", () => {
      const k = th.dataset.key;
      if (store.forcesSort.key === k) store.forcesSort.dir *= -1;
      else store.forcesSort = { key: k, dir: k === "uid" || k === "kind" || k === "story" || k === "section" ? 1 : -1 };
      renderForcesTab();
    }));
}

/* ================================================================
   v0.4 — TIME HISTORY TAB
   ================================================================ */
function thData() {
  const th = store.results && store.results.th_cases;
  if (!th || !Object.keys(th).length) return null;
  if (!store.thCase || !th[store.thCase]) store.thCase = Object.keys(th)[0];
  return th[store.thCase];
}

function rebuildThSelects() {
  const th = (store.results && store.results.th_cases) || {};
  const names = Object.keys(th);
  const sel = $("thCaseSelect");
  sel.textContent = "";
  for (const n of names) {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  }
  if (!store.thCase || !names.includes(store.thCase)) store.thCase = names[0] || null;
  if (store.thCase) sel.value = store.thCase;

  const stories = (store.results && store.results.story_order) || [];
  const ssel = $("thStorySelect");
  ssel.textContent = "";
  for (const s of [...stories].reverse()) {       // roof first
    const o = document.createElement("option");
    o.value = s; o.textContent = s;
    ssel.appendChild(o);
  }
  if (!store.thStory || !stories.includes(store.thStory))
    store.thStory = stories[stories.length - 1] || null;
  if (store.thStory) ssel.value = store.thStory;
}

function renderThTab() {
  const td = thData();
  if (!td) return;
  const r = store.results;
  const tc = (store.model.th_cases || {})[store.thCase] || {};
  const dirX = tc.direction !== "Y";
  const story = store.thStory;

  $("thMeta").textContent =
    `${td.t.length} steps · ${fmt(td.t[td.t.length - 1] || 0, 1)} s · ` +
    `dir ${dirX ? "X" : "Y"} · ζ ${fmt(tc.damping ?? 0.05, 3)}`;

  const box = $("thCharts");
  box.textContent = "";
  const ux = (td.story_ux && td.story_ux[story]) || [];
  const uy = (td.story_uy && td.story_uy[story]) || [];
  box.appendChild(timeSeriesChart(td.t, [
    { label: `${story} ux`, values: ux.map(v => v * 1000), color: "#1e9ad4" },
    { label: `${story} uy`, values: uy.map(v => v * 1000), color: "#d55181" },
  ], { title: `Story displacement — ${story}`, unit: "mm", dec: 2 }));
  box.appendChild(timeSeriesChart(td.t, [
    { label: "base FX", values: td.base_FX || [], color: "#1e9ad4" },
    { label: "base FY", values: td.base_FY || [], color: "#d55181" },
  ], { title: "Base shear", unit: "kN", dec: 1 }));

  /* peaks table: per-story displacement peaks + base row */
  const head = `<thead><tr>
    <th class="txt">Story</th><th>Elev m</th>
    <th>peak |ux| mm</th><th>peak |uy| mm</th></tr></thead>`;
  const rows = [...r.story_order].reverse().map(s => {
    const p = (td.peaks && td.peaks.story && td.peaks.story[s]) || {};
    return `<tr${s === story ? ` class="th-active"` : ""}>
      <td class="txt">${esc(s)}</td>
      <td class="dim">${fmt(r.story_elev[s], 1)}</td>
      <td>${fmt((p.ux || 0) * 1000, 2)}</td>
      <td>${fmt((p.uy || 0) * 1000, 2)}</td></tr>`;
  }).join("");
  const pb = (td.peaks && td.peaks.base) || {};
  const totals = `<tr class="totals">
    <td class="txt">peak base shear</td><td></td>
    <td>${fmt(pb.FX || 0, 1)} kN</td><td>${fmt(pb.FY || 0, 1)} kN</td></tr>`;
  $("thPeaksTable").innerHTML = head + `<tbody>${rows}${totals}</tbody>`;
}

/* ================================================================
   v0.5 — PUSHOVER TAB
   ================================================================ */
function poData() {
  const po = store.results && store.results.pushover;
  if (!po || !Object.keys(po).length) return null;
  if (!store.poCase || !po[store.poCase]) store.poCase = Object.keys(po)[0];
  return po[store.poCase];
}

function rebuildPoSelect() {
  const po = (store.results && store.results.pushover) || {};
  const names = Object.keys(po);
  const sel = $("poCaseSelect");
  sel.textContent = "";
  for (const n of names) {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  }
  if (!store.poCase || !names.includes(store.poCase)) store.poCase = names[0] || null;
  if (store.poCase) sel.value = store.poCase;
}

function renderPoTab() {
  const pd = poData();
  if (!pd) return;
  const m = store.model;
  const pc = (m.pushover_cases || {})[store.poCase] || {};
  const H = m.stories.length ? m.stories[m.stories.length - 1].elevation : 1;

  $("poMeta").textContent =
    `dir ${pc.direction || "X"} · target ${fmt((pc.target_drift ?? 0.02) * 100, 1)} % drift · ` +
    `${(pd.roof_disp || []).length} steps · hardening ${fmt(pc.hardening ?? 0.02, 2)}`;

  const wbox = $("poWarnings");
  wbox.textContent = "";
  const warns = pd.warnings || [];
  wbox.classList.toggle("hidden", !warns.length);
  for (const w of warns) {
    const div = document.createElement("div");
    div.className = "po-warn-item";
    div.textContent = `⚠ ${w}`;
    wbox.appendChild(div);
  }

  const box = $("poChart");
  box.textContent = "";
  box.appendChild(pushoverChart(pd.roof_disp || [], pd.base_shear || [], {
    title: `Capacity curve — ${store.poCase}`, H,
  }));

  /* hinge rotations, sorted descending */
  const memBy = {};
  for (const mm of (store.results.members || [])) memBy[mm.uid] = mm;
  const rows = Object.entries(pd.hinge_rotations || {}).sort((a, b) => b[1] - a[1]);
  const head = `<thead><tr>
    <th class="txt">Member</th><th class="txt">Kind</th><th class="txt">Story</th>
    <th>θ mrad</th><th>My kN·m</th></tr></thead>`;
  const body = rows.map(([uid, rot]) => {
    const mm = memBy[uid] || {};
    const my = (pc.My && pc.My[uid] != null) ? fmt(pc.My[uid], 0)
      : (pc.default_My != null ? `${fmt(pc.default_My, 0)} (default)` : "—");
    return `<tr>
      <td class="txt">${esc(uid)}</td>
      <td class="txt dim">${esc(mm.kind || "—")}</td>
      <td class="txt dim">${esc(mm.story || "—")}</td>
      <td>${fmt(rot * 1000, 2)}</td>
      <td class="dim">${my}</td></tr>`;
  }).join("");
  $("poHingeTable").innerHTML = head +
    `<tbody>${body || `<tr><td class="txt dim">No hinge rotations reported</td></tr>`}</tbody>`;
  $("poHingeNote").textContent =
    `${rows.length} hinges · ${store.poCase} — plastic rotations at target drift, sorted descending`;
}

/* ================================================================
   v0.4 — SHELL FORCE CONTOURS
   ================================================================ */
function contourAvailability() {
  const r = store.results;
  if (!r || !r.shell_quads || !r.shell_quads.length)
    return { ok: false, why: "run an analysis with meshed shells first" };
  if (isRsCase(store.caseName) || (r.combos && r.combos[store.caseName]))
    return { ok: false, why: "static cases only" };
  const cd = r.cases && r.cases[store.caseName];
  if (!cd || !cd.shell_forces)
    return { ok: false, why: "no shell_forces in this case's results" };
  return { ok: true, why: "" };
}

function syncContoursUI() {
  const av = contourAvailability();
  const chip = $("chipContours");
  chip.disabled = !av.ok;
  chip.title = av.ok
    ? "Color shell elements by internal force"
    : `Contours unavailable — ${av.why}`;
  if (!av.ok) store.contour.on = false;
  chip.classList.toggle("is-on", store.contour.on);
  $("contourGroup").hidden = !store.contour.on;
  $("contourComp").value = store.contour.comp;
  viewer.setContours({
    on: store.contour.on,
    comp: store.contour.comp,
    caseName: store.caseName,
  });
  renderContourLegend();
}

function renderContourLegend() {
  const box = $("contourLegend");
  const av = contourAvailability();
  const show = store.contour.on && av.ok;
  box.classList.toggle("hidden", !show);
  if (!show) return;
  const comp = SHELL_COMPONENTS[store.contour.comp] || SHELL_COMPONENTS.M11;
  const sf = store.results.cases[store.caseName].shell_forces;
  let lo = 0, hi = 0;
  for (let i = 0; i < store.results.shell_quads.length; i++) {
    const arr = sf[i] !== undefined ? sf[i] : sf[String(i)];
    const v = (arr && isFinite(arr[comp.idx])) ? arr[comp.idx] : 0;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  const vmax = Math.max(Math.abs(lo), Math.abs(hi)) || 1;
  $("contourLegendTitle").innerHTML =
    `${esc(store.contour.comp)} <span class="unit">${esc(comp.unit)} · ${esc(caseLabel(store.caseName))}</span>`;
  // symmetric diverging scale about 0 — label the true data min/max
  $("clMin").textContent = fmt(-vmax, vmax < 10 ? 2 : 1);
  $("clMax").textContent = `+${fmt(vmax, vmax < 10 ? 2 : 1)}`;
}

/* ================================================================
   v0.4 — CSV EXPORT (client-side blob downloads, unrounded)
   ================================================================ */
const csvEsc = v => {
  const s = String(v ?? "");
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};
const toCsv = rows => rows.map(r => r.map(csvEsc).join(",")).join("\n") + "\n";
const slug = s => String(s || "x").trim().replace(/[^A-Za-z0-9_-]+/g, "_")
  .replace(/^_+|_+$/g, "").slice(0, 48) || "x";

/** Rows (incl. header) for each exportable table — numbers UNROUNDED. */
function csvRows(kind) {
  const r = store.results;
  if (!r) return null;
  const cd = tableCaseData();
  if (kind === "story") {
    if (!cd) return null;
    return [
      ["story", "elev_m", "ux_m", "uy_m", "drift_x", "drift_y", "shear_x_kN", "shear_y_kN"],
      ...[...r.story_order].reverse().map(s => {
        const st = (cd.story && cd.story[s]) || {};
        return [s, r.story_elev[s], st.ux || 0, st.uy || 0,
          st.drift_x || 0, st.drift_y || 0, st.shear_x || 0, st.shear_y || 0];
      }),
    ];
  }
  if (kind === "modal") {
    const modal = r.modal;
    if (!modal || !modal.periods || !modal.periods.length) return null;
    let cx = 0, cy = 0;
    return [
      ["mode", "T_s", "f_Hz", "ux", "uy", "rz", "gamma_x", "gamma_y", "cum_ux", "cum_uy"],
      ...modal.participation.map((p, i) => {
        cx += p.ux || 0; cy += p.uy || 0;
        return [p.mode, modal.periods[i], modal.frequencies[i],
          p.ux || 0, p.uy || 0, p.rz || 0,
          p.gamma_x ?? "", p.gamma_y ?? "", cx, cy];
      }),
    ];
  }
  if (kind === "reactions") {
    if (!cd) return null;
    const tags = (r.supports || []).slice().sort((a, b) => {
      const pa = r.nodes[a] || [0, 0], pb = r.nodes[b] || [0, 0];
      return pa[1] - pb[1] || pa[0] - pb[0];
    });
    const b = cd.base || {};
    return [
      ["node", "x_m", "y_m", "FX_kN", "FY_kN", "FZ_kN", "MX_kNm", "MY_kNm", "MZ_kNm"],
      ...tags.map(t => {
        const p = r.nodes[t] || [0, 0, 0];
        const f = (cd.reactions && cd.reactions[t]) || [0, 0, 0, 0, 0, 0];
        return [t, p[0], p[1], ...f];
      }),
      ["TOTAL", "", "", b.FX || 0, b.FY || 0, b.FZ || 0, b.MX || 0, b.MY || 0, b.MZ || 0],
    ];
  }
  if (kind === "forces") {
    if (!cd || !cd.member_forces) return null;
    // unrounded values, same filter+sort view the user sees
    return [
      ["member", "kind", "story", "section", "absN_max_kN", "absV2_max_kN", "absM3_max_kNm"],
      ...forcesRows().map(x => [x.uid, x.kind, x.story, x.section, x.N, x.V2, x.M3]),
    ];
  }
  if (kind === "pushover") {
    const pd = poData();
    if (!pd) return null;
    return [
      ["step", "roof_disp_m", "roof_drift", "base_shear_kN"],
      ...(pd.roof_disp || []).map((u, i) => [
        i, u, (pd.roof_drift || [])[i] ?? "", (pd.base_shear || [])[i] ?? 0,
      ]),
    ];
  }
  if (kind === "th") {
    const td = thData();
    if (!td) return null;
    const pb = (td.peaks && td.peaks.base) || {};
    return [
      ["story", "elev_m", "peak_ux_m", "peak_uy_m"],
      ...[...r.story_order].reverse().map(s => {
        const p = (td.peaks && td.peaks.story && td.peaks.story[s]) || {};
        return [s, r.story_elev[s], p.ux || 0, p.uy || 0];
      }),
      ["BASE_SHEAR_PEAK_kN", "", pb.FX || 0, pb.FY || 0],
    ];
  }
  return null;
}

function csvFileName(kind) {
  const caseless = kind === "modal";
  const caseTag = kind === "th" ? store.thCase
    : kind === "pushover" ? store.poCase
    : caseLabel(store.caseName) + (caseData()?.min ? `-${store.envSide}` : "");
  return `skyframe-${slug(store.model?.name)}-${kind}` +
    (caseless ? "" : `-${slug(caseTag)}`) + ".csv";
}

function downloadCsv(kind) {
  const rows = csvRows(kind);
  if (!rows) {
    toast("Nothing to export", "Run an analysis first", "error", 4000);
    return;
  }
  const blob = new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = csvFileName(kind);
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  toast("CSV exported", a.download, "info", 3500);
}

/* ================================================================
   v0.4 — REPORT
   ================================================================ */
function doReport() {
  if (!store.results) return null;
  const win = openReport(store.model, store.results, {
    caseName: isRsCase(store.caseName) ? null : store.caseName,
    driftLimitPct: store.driftLimitPct,
  });
  if (!win) toast("Popup blocked", "Allow popups for SkyFrame to open the report tab", "error", 8000);
  return win;
}

/* ------------------------------------------------ overlay UI */
function syncOverlayUI() {
  const o = store.overlay;
  $("chipDeformed").classList.toggle("is-on", o.deformed);
  $("chipMode").classList.toggle("is-on", o.modal);
  $("deformedGroup").hidden = !o.deformed;
  $("modeGroup").hidden = !o.modal;
  $("legendDeformed").classList.toggle("hidden", !(o.deformed || o.modal));
  $("envBadge").classList.toggle("hidden", !(o.deformed && isRsCase(store.caseName)));
  if (o.modal && store.results && store.results.modal) {
    const T = store.results.modal.periods[o.modeIndex];
    const f = store.results.modal.frequencies[o.modeIndex];
    $("modePeriodBadge").textContent = `T = ${fmt(T, 3)} s · ${fmt(f, 2)} Hz`;
  }
  viewer.setOverlay({
    deformed: o.deformed, modal: o.modal,
    caseName: store.caseName, modeIndex: o.modeIndex, scaleMult: o.scaleMult,
  });
}

function rebuildModeSelect() {
  const sel = $("modeSelect");
  sel.textContent = "";
  const modal = store.results && store.results.modal;
  if (!modal || !modal.periods) return;
  modal.periods.forEach((T, i) => {
    const o = document.createElement("option");
    o.value = String(i);
    o.textContent = `Mode ${i + 1} — ${fmt(T, 3)} s`;
    sel.appendChild(o);
  });
  sel.value = String(store.overlay.modeIndex);
}

/* ------------------------------------------------ actions */
async function doGenerate(e) {
  e.preventDefault();
  const fd = new FormData($("quickForm"));
  const params = {};
  for (const [k, v] of fd.entries()) {
    if (k === "name" || k === "base_fixity") params[k] = v;
    else {
      const n = parseFloat(v);
      if (isFinite(n)) params[k] = ["bays_x", "bays_y", "stories"].includes(k) ? Math.round(n) : n;
    }
  }
  const btn = $("generateBtn");
  btn.disabled = true;
  try {
    adoptModel(await generateModel(params), null);
  } catch (err) {
    toast("Model generation failed", err.message, "error");
    setStatus("error", "Error");
  } finally {
    btn.disabled = false;
  }
}

async function doRun() {
  const btn = $("runBtn");
  if (btn.disabled) return;
  btn.disabled = true;
  $("runSpinner").classList.remove("hidden");
  $("runBtnLabel").textContent = "Running…";
  setStatus("running", "Running…");
  const t0 = performance.now();
  try {
    const results = await analyze();
    store.results = results;
    store.lastSolveMs = performance.now() - t0;
    if (!caseNames().includes(store.caseName)) store.caseName = null;
    rebuildCaseSelect();
    rebuildModeSelect();
    rebuildThSelects();                            // v0.4
    rebuildPoSelect();                             // v0.5
    viewer.setResults(results);
    setResultsAvailable(true);
    renderResultsTabs();
    renderMemberPanel();
    syncOverlayUI();
    syncContoursUI();                              // v0.4
    renderSummary();
    setStatus("solved", "Solved ✓");
    if (!store.firstSolveDone) {
      store.firstSolveDone = true;
      switchTab("story");
    }
  } catch (err) {
    setStatus("error", "Error");
    toast("Analysis failed", err.message, "error", 9000);
  } finally {
    btn.disabled = false;
    $("runSpinner").classList.add("hidden");
    $("runBtnLabel").textContent = "Run Analysis";
  }
}

/* ------------------------------------------------ wiring */
function wire() {
  $("quickForm").addEventListener("submit", doGenerate);
  $("runBtn").addEventListener("click", doRun);

  document.querySelectorAll(".tab").forEach(b =>
    b.addEventListener("click", () => switchTab(b.dataset.tab)));

  $("caseSelect").addEventListener("change", e => {
    store.caseName = e.target.value;
    store.envSide = "max";                         // v0.4: reset envelope side
    renderResultsTabs();
    renderMemberPanel();
    syncOverlayUI();
    syncContoursUI();                              // v0.4
  });

  /* ---- v0.4: envelope max/min toggle */
  document.querySelectorAll("#envToggle .seg-btn").forEach(b =>
    b.addEventListener("click", () => {
      if (store.envSide === b.dataset.env) return;
      store.envSide = b.dataset.env;
      renderResultsTabs();
    }));

  /* ---- v0.4: report + CSV buttons */
  $("reportBtn").addEventListener("click", doReport);
  $("csvStory").addEventListener("click", () => downloadCsv("story"));
  $("csvModal").addEventListener("click", () => downloadCsv("modal"));
  $("csvReactions").addEventListener("click", () => downloadCsv("reactions"));
  $("csvForces").addEventListener("click", () => downloadCsv("forces"));
  $("csvTh").addEventListener("click", () => downloadCsv("th"));
  $("csvPo").addEventListener("click", () => downloadCsv("pushover"));   // v0.5

  /* ---- v0.4: time-history tab controls */
  $("thCaseSelect").addEventListener("change", e => {
    store.thCase = e.target.value;
    renderThTab();
  });
  $("thStorySelect").addEventListener("change", e => {
    store.thStory = e.target.value;
    renderThTab();
  });

  /* ---- v0.5: pushover tab, view toggle, elevation line, diaphragm */
  $("poCaseSelect").addEventListener("change", e => {
    store.poCase = e.target.value;
    renderPoTab();
  });
  document.querySelectorAll("#viewToggle .seg-btn").forEach(b =>
    b.addEventListener("click", () => setView(b.dataset.view)));
  $("elevLineSelect").addEventListener("change", e => setElevLine(e.target.value));
  $("diaphragmSelect").addEventListener("change", e => {
    store.model.diaphragm = e.target.value === "none" ? "none" : "rigid";
    markDirty();
  });

  /* ---- v0.4: brace layout toggle */
  document.querySelectorAll("#braceToggle .seg-btn").forEach(b =>
    b.addEventListener("click", () => {
      store.braceXPair = b.dataset.brace === "xpair";
      document.querySelectorAll("#braceToggle .seg-btn").forEach(x =>
        x.classList.toggle("is-active", x === b));
    }));

  /* ---- v0.4: grid & story editor */
  $("gridEditBtn").addEventListener("click", openGridEditor);
  $("gridModalClose").addEventListener("click", closeGridEditor);
  $("gridModalDone").addEventListener("click", closeGridEditor);
  $("gridModal").addEventListener("click", e => {
    if (e.target === $("gridModal")) closeGridEditor();
  });
  $("addGridX").addEventListener("click", () => {
    ME.addGridLine(store.model, "x");
    afterGeometryEdit();
  });
  $("addGridY").addEventListener("click", () => {
    ME.addGridLine(store.model, "y");
    afterGeometryEdit();
  });

  /* ---- v0.2: mode switch, draw tools, save/discard, member panel */
  document.querySelectorAll(".mode-btn").forEach(b =>
    b.addEventListener("click", () => setMode(b.dataset.mode)));

  document.querySelectorAll(".tool-btn").forEach(b =>
    b.addEventListener("click", () => setTool(b.dataset.tool)));

  document.querySelectorAll("#applyToggle .seg-btn").forEach(b =>
    b.addEventListener("click", () => {
      store.applyAll = b.dataset.apply === "all";
      document.querySelectorAll("#applyToggle .seg-btn").forEach(x =>
        x.classList.toggle("is-active", x === b));
      syncStoryBadges();
    }));

  $("storySelect").addEventListener("change", e => setStory(e.target.value));
  $("storyUp").addEventListener("click", () => stepStory(1));
  $("storyDown").addEventListener("click", () => stepStory(-1));

  $("saveBtn").addEventListener("click", saveModel);
  $("discardBtn").addEventListener("click", discardModel);

  /* ---- v0.3: File menu + model-file dialogs */
  $("fileMenuBtn").addEventListener("click", e => { e.stopPropagation(); toggleFileMenu(); });
  document.addEventListener("click", e => {
    if (!$("fileMenuWrap").contains(e.target)) toggleFileMenu(false);
  });
  $("fileMenu").addEventListener("click", e => {
    const item = e.target.closest(".menu-item");
    if (!item) return;
    toggleFileMenu(false);
    if (item.dataset.act === "new") fileNew();
    else if (item.dataset.act === "open") openFileDialog();
    else if (item.dataset.act === "save") fileSave();
    else if (item.dataset.act === "saveas") openSaveAs();
  });
  const hideModal = id => $(id).classList.add("hidden");
  $("openModalClose").addEventListener("click", () => hideModal("openModal"));
  $("openModalCancel").addEventListener("click", () => hideModal("openModal"));
  $("openModal").addEventListener("click", e => { if (e.target === $("openModal")) hideModal("openModal"); });
  $("saveAsClose").addEventListener("click", () => hideModal("saveAsModal"));
  $("saveAsCancel").addEventListener("click", () => hideModal("saveAsModal"));
  $("saveAsModal").addEventListener("click", e => { if (e.target === $("saveAsModal")) hideModal("saveAsModal"); });
  $("saveAsName").addEventListener("input", validateSaveAs);
  $("saveAsName").addEventListener("keydown", e => { if (e.key === "Enter") submitSaveAs(); });
  $("saveAsOk").addEventListener("click", submitSaveAs);
  $("confirmOk").addEventListener("click", () => settleConfirm(true));
  $("confirmCancel").addEventListener("click", () => settleConfirm(false));
  $("confirmModal").addEventListener("click", e => { if (e.target === $("confirmModal")) settleConfirm(false); });

  /* ---- v0.3: loads sidebar nav + section library search */
  document.querySelectorAll("#loadsNav button").forEach(b =>
    b.addEventListener("click", () => {
      const sec = document.getElementById(b.dataset.target);
      if (sec) sec.scrollIntoView({ behavior: "smooth", block: "start" });
    }));
  $("libSearch").addEventListener("input", e => {
    store.libSearch = e.target.value;
    renderSectionLib();
  });

  $("sectionMgrBtn").addEventListener("click", openSectionMgr);
  $("sectionModalClose").addEventListener("click", closeSectionMgr);
  $("sectionModalDone").addEventListener("click", closeSectionMgr);
  $("sectionModal").addEventListener("click", e => {
    if (e.target === $("sectionModal")) closeSectionMgr();
  });
  $("addFrameSection").addEventListener("click", () => {
    ME.addFrameSection(store.model); markDirty(); renderSectionMgr();
  });
  $("addShellSection").addEventListener("click", () => {
    ME.addShellSection(store.model); markDirty(); renderSectionMgr();
  });
  $("addMaterial").addEventListener("click", () => {
    ME.addMaterial(store.model); markDirty(); renderSectionMgr();
  });

  $("memberPanelClose").addEventListener("click", closeMemberPanel);

  // sidebar collapse
  const applySidebar = collapsed => {
    $("sidebar").classList.toggle("collapsed", collapsed);
    $("sidebarToggle").classList.toggle("collapsed", collapsed);
    setTimeout(() => viewer && viewer._resize(), 200);
  };
  $("sidebarToggle").addEventListener("click", () =>
    applySidebar(!$("sidebar").classList.contains("collapsed")));

  // overlay chips (deformed / mode / contours are mutually exclusive)
  $("chipDeformed").addEventListener("click", () => {
    store.overlay.deformed = !store.overlay.deformed;
    if (store.overlay.deformed) { store.overlay.modal = false; store.contour.on = false; }
    syncOverlayUI();
    syncContoursUI();
  });
  $("chipMode").addEventListener("click", () => {
    store.overlay.modal = !store.overlay.modal;
    if (store.overlay.modal) { store.overlay.deformed = false; store.contour.on = false; }
    syncOverlayUI();
    syncContoursUI();
  });
  $("chipContours").addEventListener("click", () => {
    store.contour.on = !store.contour.on;
    if (store.contour.on) {
      store.overlay.deformed = false;
      store.overlay.modal = false;
      syncOverlayUI();
    }
    syncContoursUI();
  });
  $("contourComp").addEventListener("change", e => {
    store.contour.comp = e.target.value;
    syncContoursUI();
  });
  $("chipLabels").addEventListener("click", () => {
    const on = !$("chipLabels").classList.contains("is-on");
    $("chipLabels").classList.toggle("is-on", on);
    viewer.setLabels(on);
  });
  $("defScale").addEventListener("input", e => {
    const mult = Math.pow(10, parseFloat(e.target.value));
    store.overlay.scaleMult = mult;
    $("defScaleVal").textContent = "×" + (mult >= 10 ? fmt(mult, 0) : fmt(mult, mult < 1 ? 2 : 1));
    syncOverlayUI();
  });
  $("modeSelect").addEventListener("change", e => {
    store.overlay.modeIndex = parseInt(e.target.value, 10);
    syncOverlayUI();
  });

  // drift limit
  $("driftLimitInput").addEventListener("change", e => {
    const v = parseFloat(e.target.value);
    if (isFinite(v) && v > 0) { store.driftLimitPct = v; renderStoryTab(); }
  });

  // forces filter
  $("forcesFilter").addEventListener("input", e => {
    store.forcesFilter = e.target.value;
    renderForcesTab();
  });

  // keyboard
  const TABS = ["view3d", "story", "modal", "reactions", "forces", "th", "pushover"];
  const TOOL_KEYS = { v: "select", c: "column", b: "beam", x: "brace", w: "wall", s: "slab", l: "link", e: "erase" };
  document.addEventListener("keydown", e => {
    const tag = (e.target.tagName || "").toLowerCase();
    // v0.3 dialogs respond to Escape even while an input has focus
    if (e.key === "Escape") {
      if (confirmResolve) { settleConfirm(false); return; }
      if (!$("saveAsModal").classList.contains("hidden")) { $("saveAsModal").classList.add("hidden"); return; }
      if (!$("openModal").classList.contains("hidden")) { $("openModal").classList.add("hidden"); return; }
      if (!$("gridModal").classList.contains("hidden")) { closeGridEditor(); return; }
      if (!$("fileMenu").classList.contains("hidden")) { toggleFileMenu(false); return; }
    }
    if (["input", "select", "textarea"].includes(tag)) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    if (e.key === "Escape") {
      if (!$("sectionModal").classList.contains("hidden")) closeSectionMgr();
      else if (store.mode === "model") {
        const ed = activeEditor();
        if (ed.pending || ed.box) ed.cancel();
        else if (store.selection.length) handleSelect([], false);
      } else if (store.selectedMemberUid) closeMemberPanel();
      return;
    }
    if (e.key === "\\") { $("sidebarToggle").click(); return; }

    if (store.mode === "model") {
      const t = TOOL_KEYS[e.key.toLowerCase()];
      if (t) setTool(t);
      else if (e.key === "ArrowUp") { e.preventDefault(); stepStory(1); }
      else if (e.key === "ArrowDown") { e.preventDefault(); stepStory(-1); }
      else if (e.key === "Delete" || e.key === "Backspace") deleteSelection();
      else if (e.key === "f" || e.key === "F") activeEditor().fit();
      return;
    }

    if (store.mode !== "analyze") return;   // loads mode: no analyze shortcuts

    if (e.key >= "1" && e.key <= "7") {
      const t = TABS[+e.key - 1];
      const hidden = (t === "th" && $("thTabBtn").classList.contains("hidden")) ||
        (t === "pushover" && $("poTabBtn").classList.contains("hidden"));
      if (t && !hidden) switchTab(t);
    }
    else if (e.key === "r" || e.key === "R") doRun();
    else if (e.key === "f" || e.key === "F") viewer.fit();
  });
}

/* ------------------------------------------------ boot */
async function boot() {
  viewer = new Viewer3D($("viewer3d"), {
    tooltipEl: $("viewerTooltip"),
    getMemberTooltip: memberTooltip,
    onMemberClick,
  });
  planEditor = new PlanEditor($("planSvg"), {
    getModel: () => store.model,
    getStory: () => store.story,
    getSelection: () => new Set(store.selection.map(r => `${r.type}:${r.uid}`)),
    onDraw: handleDraw,
    onErase: handleErase,
    onSelect: handleSelect,
    onReadout: t => { $("planReadout").textContent = t; },
  });
  elevEditor = new ElevEditor($("elevSvg"), {          // v0.5
    getModel: () => store.model,
    getPlane: () => elevPlane(),
    getSelection: () => new Set(store.selection.map(r => `${r.type}:${r.uid}`)),
    onDraw: handleElevDraw,
    onErase: handleErase,
    onSelect: handleSelect,
    onReadout: t => { $("planReadout").textContent = t; },
  });
  loadsEditor = new LoadsEditor($("loadsPane"), {
    getModel: () => store.model,
    onChange: markDirty,
    toast,
    onWind: generateWindPattern,                   // v0.4
  });
  wire();
  try {
    store.model = ME.normalizeModel(await fetchModel());
    viewer.setModel(store.model);
    syncShellLegend();
    rebuildStorySelect();
    rebuildElevSelect();
    syncDiaphragmUI();
    refreshDrawViews();
    renderSummary();
    setResultsAvailable(false);
    syncDirtyUI();
    syncLoadsNav();
    setStatus("ready", "Ready");
  } catch (err) {
    setStatus("error", "Error");
    toast("Failed to load model", err.message, "error");
  }

  // dev/test hook — lets automated checks drive the store directly
  window.__sky = {
    store, planEditor, viewer, loadsEditor,
    setMode, setTool, setStory, handleDraw, handleErase, handleSelect,
    saveModel, discardModel, renderProps, ME,
    // v0.3
    filesApi, saveToFile, adoptModel, caseLabel, isRsCase, caseData,
    renderSectionLib, fetchSectionLibrary, doRun, switchTab, syncOverlayUI,
    renderMemberPanel, closeMemberPanel, openSectionMgr,
    // v0.4
    openGridEditor, closeGridEditor, renderGridEditor, tableCaseData,
    syncContoursUI, syncEnvToggle, renderThTab, rebuildThSelects,
    csvRows, csvFileName, downloadCsv, doReport, buildReportHtml,
    generateWindPattern, renderSectionMgr, contourAvailability,
    // v0.5
    elevEditor, setView, setElevLine, elevPlane, rebuildElevSelect,
    handleElevDraw, renderPoTab, rebuildPoSelect, poData,
    renderOpeningPreview, syncDiaphragmUI,
  };
}

boot();
