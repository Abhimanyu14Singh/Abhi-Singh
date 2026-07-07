/* SkyFrame app shell — state store, API (with mock fallback), tabs,
   tables, overlay controls, model/draw mode. No frameworks. */

import { Viewer3D, SHELL_COMPONENTS } from "./viewer3d.js";
import { renderStoryCharts, stationDiagram, timeSeriesChart, pushoverChart } from "./charts.js";
import { mockModel, mockResults, mockSectionLibrary, mockModelFiles, mockWindPattern,
  mockDesignSteel, mockDesignConcrete, mockImport,
  mockSelfWeightPattern, mockAsce7Combos, mockCodeRsCase, mockElfPattern,
  mockRsDirectional, mockNotionalPattern, mockOptimize, mockLiveReduction,
  mockDesignWall, mockDesignPunching, mockVirtualWork,
  mockPatternLive, mockAutoSequence, mockPerformancePoint,
  mockDesignComposite, mockDesignSlab, mockVibration,
  mockDesignerUpsert, mockDesignerDelete, mockDesignerPmm } from "./mock.js";
import { PlanEditor } from "./draw.js";
import { SectionDesigner } from "./secdesigner.js";   // v0.21
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
  cmStory: null,         // v0.8 — story shown in the CM/CR plan diagram
  overlay: { deformed: false, modal: false, buckling: false, bucklingCase: null, modeIndex: 0, scaleMult: 1 },
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
  gridSel: 0,            // v0.14 — selected grid system in the Grid/Stories editor
  // v0.5 — elevation view, pushover
  view: "plan",          // model-mode editor: "plan" | "elev"
  elevLine: null,        // elevation grid line, e.g. "x:0" | "y:2"
  poCase: null,          // selected pushover case (results tab)
  perf: {},              // v0.19: pushover case -> performance-point result
  perfParams: { SDS: 1.0, SD1: 0.6, site: "D" },
  buckCase: null,        // v0.10 — selected buckling case (results tab)
  tdCase: null,          // v0.11 — selected gravity case for the load-takedown tab
  cutCase: null,         // v0.13 — selected case for the section-cut-forces tab
  pierCase: null,        // v0.15 — selected case for the wall-piers tab
  pierSort: { key: "elev", dir: -1 },   // v0.15 — story order within pier groups
  svcCase: null,         // v0.16 — selected case for the serviceability tab
  svcSort: { key: "ratioVal", dir: 1 }, // v0.16 — worst (smallest L/x) first
  llReduction: false,    // v0.16 — apply ASCE 7 §4.7 live-load reduction to design
  llrData: null,         // v0.16 — cached GET /api/live-reduction {uid:{KLL,At,R}}
  // v0.6 — design checks, import, template gallery, staged, nonlinear TH
  designKind: "steel",   // "steel" | "concrete"
  designCase: null,      // case/combo checked
  steelResult: null,     // last steel design response
  concreteResult: null,  // last concrete design response
  designSort: { key: "ratio", dir: -1 },
  designFilter: "",
  designAllCombos: false, // v0.9 — check the design envelope over all combos
  // v0.12 — auto section optimization (Steel sub-tab)
  optCase: null,          // case/combo used for optimization
  optTarget: 0.95,        // target D/C ratio
  optResult: null,        // last /api/design/optimize {apply:false} response
  optSort: { key: "weight_kg_per_m", dir: 1 },
  diagCase: null,         // v0.9 — selected lateral case for story diagnostics
  steelFy: 345000,       // kPa
  concreteFc: 30000,     // kPa
  rebar: {},             // per-uid rebar overrides (uid -> layout)
  rebarDefault: { n_top: 2, n_bot: 3, bar_dia: 20, cover: 40, fy: 420000,
    stirrup_dia: 10, stirrup_spacing: 150, stirrup_legs: 2 },
  importFmt: "dxf",
  importText: "",
  importFileName: "",
  importStories: [3.2, 3.2],
  // v0.18 — wall design, punching check, drift optimizer (virtual work)
  wallResult: null,        // last POST /api/design/wall response
  wallParams: { rho_v: 0.25, rho_h: 0.25, fy: 420000, fc: 30000 },  // % · % · kPa · kPa
  wallCombos: null,        // selected combo names (null → default all)
  punchResult: null,       // last POST /api/design/punching response
  punchParams: { fc: 30000, cover: 40 },   // kPa · mm
  punchCase: null,         // case/combo for the punching check
  punchSel: null,          // clicked punching column uid (plan + 3D highlight)
  vwResult: null,          // last POST /api/results/virtual-work response
  vwCase: null,            // drift-optimizer case/combo
  vwDir: "X",              // drift-optimizer direction
  // v0.20 — composite beams, slab design, floor vibration
  compositeResult: null,   // last POST /api/design/composite response
  compositeParams: { t_slab: 130, fc: 30000, hr: 75, stud_d: 19,
    rib_spacing: 300, shored: false },   // mm · kPa · mm · mm · mm · bool
  compositeCombos: null,   // selected combo names (null → default all)
  slabResult: null,        // last POST /api/design/slab response
  slabParams: { fc: 30000, bar_d: 16, cover: 25 },   // kPa · mm · mm
  slabCase: null,          // case/combo for the slab design
  vibResult: null,         // last POST /api/results/vibration response
  vibParams: { live_factor: 0.5, beta: 0.03, ap_limit: 0.005 },
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

/* ---- v0.6: design checks. Live path syncs the working model first (the
   backend runs a fresh analysis of the CURRENT model), then POSTs.
   Mock (or a missing endpoint) synthesizes checks from the mock results. */
async function designCheck(kind, body) {
  if (!store.mock) {
    try {
      const payload = JSON.parse(JSON.stringify(store.model));
      delete payload._mock_params;
      await postModel(payload);
      return await api(`/api/design/${kind}`, body);
    } catch (e) {
      console.warn(`Design ${kind} endpoint unavailable, using mock:`, e.message);
    }
  }
  await new Promise(r => setTimeout(r, 250));
  return kind === "steel" ? mockDesignSteel(store.model, body)
    : mockDesignConcrete(store.model, body);
}

/* ---- v0.12: auto section optimization. Mirrors designCheck — the live path
   syncs the working model first (the backend re-analyses the current model),
   then POSTs /api/design/optimize. Mock (or a missing endpoint) computes the
   suggestions / applied model locally. apply:true returns the updated MODEL
   dict; apply:false returns {suggestions:[...]}. */
async function designOptimize(body) {
  if (!store.mock) {
    try {
      const payload = JSON.parse(JSON.stringify(store.model));
      delete payload._mock_params;
      await postModel(payload);
      return await api("/api/design/optimize", body);
    } catch (e) {
      console.warn("Optimize endpoint unavailable, using mock:", e.message);
    }
  }
  await new Promise(r => setTimeout(r, 250));
  return mockOptimize(store.model, body);
}

/* ---- v0.21: section designer (polygon + rebar fiber sections). The live
   path syncs the working model, POSTs /api/sections/designer and returns the
   FULL model dict (properties computed server-side). Mock — or a missing
   endpoint — mutates the model locally via the client shoelace formulas. */
async function designerAction(action, section) {
  if (!store.mock) {
    try {
      const payload = JSON.parse(JSON.stringify(store.model));
      delete payload._mock_params;
      await postModel(payload);
      const modelDict = await api("/api/sections/designer", { action, section });
      return { model: modelDict, live: true };
    } catch (e) {
      console.warn("Section-designer endpoint unavailable, using mock:", e.message);
    }
  }
  await new Promise(r => setTimeout(r, 150));
  return {
    model: action === "delete"
      ? mockDesignerDelete(store.model, section.name)
      : mockDesignerUpsert(store.model, section),
    live: false,
  };
}

/** Run a designer upsert/delete and adopt the returned model everywhere.
    Live round-trip ⇒ the backend owns the model (clean); mock/fallback ⇒
    the change is local-only (dirty). */
async function applyDesignerAction(action, section) {
  const { model: md, live } = await designerAction(action, section);
  store.model = ME.normalizeModel(md);
  if (live) clearDirty(); else markDirty();
  store.modelEdited = false;
  viewer.setModel(store.model);
  refreshDrawViews();
  renderSectionMgr();
  renderProps();
  renderSummary();
  return store.model;
}

/* ---- v0.21: PMM interaction diagram for a saved designer section. */
async function designerPmmFetch(name, axis = "33") {
  if (!store.mock) {
    try {
      const payload = JSON.parse(JSON.stringify(store.model));
      delete payload._mock_params;
      await postModel(payload);
      return await api("/api/sections/designer/pmm", { name, axis });
    } catch (e) {
      console.warn("Designer PMM endpoint unavailable, using mock:", e.message);
    }
  }
  await new Promise(r => setTimeout(r, 150));
  return mockDesignerPmm(store.model, { name, axis });
}

function openSectionDesigner(name) {
  if (sectionDesigner) sectionDesigner.open(name);
}

/* ---- v0.18: wall-pier design + slab punching check. Same convention as
   designCheck — the live path syncs the working model (the backend re-analyses
   the CURRENT model), then POSTs; mock / missing endpoint synthesizes. */
async function designWall(body) {
  if (!store.mock) {
    try {
      const payload = JSON.parse(JSON.stringify(store.model));
      delete payload._mock_params;
      await postModel(payload);
      return await api("/api/design/wall", body);
    } catch (e) {
      console.warn("Wall design endpoint unavailable, using mock:", e.message);
    }
  }
  await new Promise(r => setTimeout(r, 250));
  return mockDesignWall(store.model, body);
}

async function designPunching(body) {
  if (!store.mock) {
    try {
      const payload = JSON.parse(JSON.stringify(store.model));
      delete payload._mock_params;
      await postModel(payload);
      return await api("/api/design/punching", body);
    } catch (e) {
      console.warn("Punching endpoint unavailable, using mock:", e.message);
    }
  }
  await new Promise(r => setTimeout(r, 250));
  return mockDesignPunching(store.model, body);
}

/* ---- v0.20: composite beam design + slab flexural design. Same convention
   as designCheck — the live path syncs the working model first (the backend
   re-analyses the CURRENT model), then POSTs; mock / missing endpoint
   synthesizes locally. */
async function designComposite(body) {
  if (!store.mock) {
    try {
      const payload = JSON.parse(JSON.stringify(store.model));
      delete payload._mock_params;
      await postModel(payload);
      return await api("/api/design/composite", body);
    } catch (e) {
      console.warn("Composite design endpoint unavailable, using mock:", e.message);
    }
  }
  await new Promise(r => setTimeout(r, 250));
  return mockDesignComposite(store.model, body);
}

async function designSlab(body) {
  if (!store.mock) {
    try {
      const payload = JSON.parse(JSON.stringify(store.model));
      delete payload._mock_params;
      await postModel(payload);
      return await api("/api/design/slab", body);
    } catch (e) {
      console.warn("Slab design endpoint unavailable, using mock:", e.message);
    }
  }
  await new Promise(r => setTimeout(r, 250));
  return mockDesignSlab(store.model, body);
}

/* ---- v0.20: floor vibration screening (results already solved on the
   backend — no model sync needed). POST /api/results/vibration. */
async function fetchVibration(body) {
  if (!store.mock) {
    try { return await api("/api/results/vibration", body); }
    catch (e) {
      console.warn("Vibration endpoint unavailable, using mock:", e.message);
    }
  }
  await new Promise(r => setTimeout(r, 250));
  return mockVibration(store.model, body);
}

/* ---- v0.18: virtual-work drift decomposition (results already solved on the
   backend — no model sync needed). POST /api/results/virtual-work. */
async function fetchVirtualWork(body) {
  if (!store.mock) {
    try { return await api("/api/results/virtual-work", body); }
    catch (e) {
      console.warn("Virtual-work endpoint unavailable, using mock:", e.message);
    }
  }
  await new Promise(r => setTimeout(r, 250));
  return mockVirtualWork(store.model, body);
}

/* ---- v0.16: live-load reduction factors (ASCE 7 §4.7). GET /api/live-reduction
   returns {uid: {KLL, At, R}} per column; the mock derives the tributary areas
   from the grid and computes R = 0.25 + 4.57/√(K_LL·A_T), clamped. Cached until
   the next fetch(force). */
async function fetchLiveReduction(force = false) {
  if (store.llrData && !force) return store.llrData;
  let data = null;
  if (!store.mock) {
    try { data = await api("/api/live-reduction"); }
    catch (e) { console.warn("Live-reduction endpoint unavailable, computing locally:", e.message); }
  }
  store.llrData = (data && typeof data === "object" && Object.keys(data).length)
    ? data : mockLiveReduction(store.model);
  return store.llrData;
}

/* ---- v0.6: model importers. Reads the returned {model, warnings}; the
   live backend also makes it the current model. */
async function importModelFile(fmt, body) {
  if (!store.mock) {
    try { return await api(`/api/import/${fmt}`, body); }
    catch (e) { console.warn(`Import ${fmt} endpoint unavailable, using mock:`, e.message); }
  }
  await new Promise(r => setTimeout(r, 250));
  return mockImport(fmt, body);
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

/* ---- v0.7: ASCE 7-16 code tools. Each mirrors generateWindPattern:
   the live path syncs the working model, POSTs the code endpoint and adopts
   the returned model dict; on failure / ?mock=1 it mutates the client model
   locally with a "computed locally" toast. All return store.model. */
async function codeToolLive(path, params) {
  const payload = JSON.parse(JSON.stringify(store.model));
  delete payload._mock_params;
  await postModel(payload);
  const echoed = await api(path, params);
  store.model = ME.normalizeModel(echoed);
  store.modelEdited = true;
  clearDirty();                                  // client == server state
  syncLoadsNav();
  renderSummary();
  return store.model;
}

async function addSelfWeightPattern(params) {
  if (!store.mock) {
    try {
      await codeToolLive("/api/pattern/selfweight", params);
      toast("Self-weight pattern added",
        `“${params.name}” · factor ${fmt(params.factor, 2)} via POST /api/pattern/selfweight`, "info", 5000);
      return store.model;
    } catch (e) { console.warn("Self-weight endpoint unavailable, computing locally:", e.message); }
  }
  mockSelfWeightPattern(store.model, params);
  ME.normalizeModel(store.model);
  markDirty();
  toast("Self-weight pattern added",
    `“${params.name}” computed locally (${store.mock ? "mock mode" : "backend lacks endpoint"})`, "info", 5000);
  return store.model;
}

/* v0.19 — pattern (skip) live loading: POST /api/loads/pattern-live
   {live_pattern} → server derives __ODD/__EVEN patterns, PLL_* cases and
   the PATTERN-LL envelope combo; mock computes the same split locally. */
async function generatePatternLive(params) {
  if (!store.mock) {
    try {
      await codeToolLive("/api/loads/pattern-live", params);
      toast("Skip patterns generated",
        `${params.live_pattern}__ODD / __EVEN + PATTERN-LL envelope via POST /api/loads/pattern-live`,
        "info", 6000);
      return store.model;
    } catch (e) { console.warn("pattern-live endpoint unavailable, computing locally:", e.message); }
  }
  mockPatternLive(store.model, params.live_pattern);
  ME.normalizeModel(store.model);
  markDirty();
  toast("Skip patterns generated",
    `${params.live_pattern}__ODD / __EVEN + PATTERN-LL envelope computed locally`, "info", 6000);
  return store.model;
}

/* v0.19 — auto construction sequence: POST /api/case/auto-sequence */
async function createAutoSequence(params) {
  if (!store.mock) {
    try {
      await codeToolLive("/api/case/auto-sequence", params);
      toast("Sequence case created",
        `“${params.name}” — one stage per story via POST /api/case/auto-sequence`, "info", 5000);
      return store.model;
    } catch (e) { console.warn("auto-sequence endpoint unavailable, computing locally:", e.message); }
  }
  mockAutoSequence(store.model, params.name, params.pattern);
  ME.normalizeModel(store.model);
  markDirty();
  toast("Sequence case created", `“${params.name}” computed locally`, "info", 5000);
  return store.model;
}

async function generateAsce7Combos(params) {
  const before = Object.keys(store.model.combos || {}).length;
  // v0.17 — optional SDS in the request folds Ev = 0.2·SDS·D into seismic combos
  const evTag = isFinite(params.SDS) ? ` · Ev @ SDS=${params.SDS}` : "";
  if (!store.mock) {
    try {
      await codeToolLive("/api/combos/asce7", params);
      const added = Object.keys(store.model.combos || {}).length - before;
      toast("ASCE 7 combinations generated",
        `${added} combo${added === 1 ? "" : "s"} (${params.standard}${evTag}) via POST /api/combos/asce7`, "info", 5000);
      return store.model;
    } catch (e) { console.warn("ASCE7 combos endpoint unavailable, computing locally:", e.message); }
  }
  const { added } = mockAsce7Combos(store.model, params.standard, params.SDS);
  ME.normalizeModel(store.model);
  markDirty();
  toast("ASCE 7 combinations generated",
    `${added.length} combo${added.length === 1 ? "" : "s"} (${params.standard}${evTag}) computed locally`, "info", 5000);
  return store.model;
}

async function createCodeRsCase(params) {
  if (!store.mock) {
    try {
      await codeToolLive("/api/case/rs-code", params);
      toast("Code RS case created",
        `“${params.name}” · ${params.direction} · ASCE 7-16 spectrum via POST /api/case/rs-code`, "info", 5000);
      return store.model;
    } catch (e) { console.warn("RS-code endpoint unavailable, computing locally:", e.message); }
  }
  mockCodeRsCase(store.model, params);
  ME.normalizeModel(store.model);
  markDirty();
  toast("Code RS case created",
    `“${params.name}” computed locally (${store.mock ? "mock mode" : "backend lacks endpoint"})`, "info", 5000);
  return store.model;
}

async function createElfPattern(params) {
  if (!store.mock) {
    try {
      await codeToolLive("/api/pattern/elf", params);
      toast("ELF seismic pattern created",
        `“${params.name}” · ${params.direction} · V=Cs·W via POST /api/pattern/elf`, "info", 5000);
      return store.model;
    } catch (e) { console.warn("ELF endpoint unavailable, computing locally:", e.message); }
  }
  mockElfPattern(store.model, params);
  ME.normalizeModel(store.model);
  markDirty();
  toast("ELF seismic pattern created",
    `“${params.name}” computed locally (${store.mock ? "mock mode" : "backend lacks endpoint"})`, "info", 5000);
  return store.model;
}

/* ---- v0.10: RS directional combination + notional loads.
   Both mirror generateWindPattern: the live path syncs the working model,
   POSTs the endpoint and adopts the echoed model; ?mock=1 / a missing
   endpoint mutates the client model locally. */
async function createRsDirectional(params) {
  if (!store.mock) {
    try {
      await codeToolLive("/api/case/rs-directional", params);
      toast("Directional RS combination created",
        `“${params.name}” · ${params.method === "SRSS" ? "SRSS" : "100/30"} via POST /api/case/rs-directional`, "info", 5000);
      return store.model;
    } catch (e) { console.warn("RS-directional endpoint unavailable, computing locally:", e.message); }
  }
  mockRsDirectional(store.model, params);
  ME.normalizeModel(store.model);
  markDirty();
  toast("Directional RS combination created",
    `“${params.name}” computed locally (${store.mock ? "mock mode" : "backend lacks endpoint"})`, "info", 5000);
  return store.model;
}

async function createNotionalPattern(params) {
  if (!store.mock) {
    try {
      await codeToolLive("/api/pattern/notional", params);
      toast("Notional pattern created",
        `“${params.name}” · ${params.direction} · ${fmt(params.coeff, 3)}×gravity via POST /api/pattern/notional`, "info", 5000);
      return store.model;
    } catch (e) { console.warn("Notional endpoint unavailable, computing locally:", e.message); }
  }
  mockNotionalPattern(store.model, params);
  ME.normalizeModel(store.model);
  markDirty();
  toast("Notional pattern created",
    `“${params.name}” computed locally (${store.mock ? "mock mode" : "backend lacks endpoint"})`, "info", 5000);
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
const isStagedCase = name => typeof name === "string" && name.startsWith("staged:");
const caseLabel = name => isRsCase(name) ? `RS: ${name.slice(3)}`
  : isStagedCase(name) ? `Staged: ${name.slice(7)}` : (name || "");

function caseNames() {
  if (!store.results) return [];
  return [
    ...Object.keys(store.results.cases || {}),
    ...Object.keys(store.results.combos || {}),
    ...Object.keys(store.results.rs_cases || {}).map(n => `rs:${n}`),
    ...Object.keys(store.results.staged || {}).map(n => `staged:${n}`),
  ];
}

function caseData() {
  const r = store.results;
  if (!r || !store.caseName) return null;
  if (isRsCase(store.caseName))
    return (r.rs_cases && r.rs_cases[store.caseName.slice(3)]) || null;
  if (isStagedCase(store.caseName))
    return (r.staged && r.staged[store.caseName.slice(7)]) || null;
  return (r.cases && r.cases[store.caseName]) || (r.combos && r.combos[store.caseName]) || null;
}

/** v0.6 — staged comparison badge (max column-axial vs one-shot). */
function syncStagedBadge() {
  const badge = $("stagedBadge");
  const cd = caseData();
  const cmp = cd && cd.comparison;
  const show = isStagedCase(store.caseName) && cmp;
  badge.classList.toggle("hidden", !show);
  if (show) {
    const pct = cmp.column_axial_max_diff_pct || 0;
    badge.textContent = `vs one-shot · Δ col-axial ${fmt(pct, 2)} %`;
  }
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
  mkGroup("Staged construction",
    Object.keys(r.staged || {}).map(n => [`staged:${n}`, `Staged: ${n}`]));
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
let sectionDesigner = null;   // v0.21 polygon + rebar section designer

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
  $("cnt-buckling").textContent = Object.keys(m.buckling_cases || {}).length;
  $("cnt-staged").textContent = Object.keys(m.staged_cases || {}).length;
  $("cnt-combos").textContent = Object.keys(m.combos || {}).length;
  $("cnt-functions").textContent =
    Object.keys(m.spectrum_functions || {}).length + Object.keys(m.th_functions || {}).length;
  $("cnt-cuts").textContent = (m.section_cuts || []).length;
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
  $("legendSpring").classList.toggle("hidden",
    !((store.model && store.model.spring_supports) || []).length);
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
  syncPanelZoneUI();
}

/* v0.17 — panel-zone joint model select + one-line explanation per choice */
const PANEL_ZONE_NOTES = {
  none: "Members span joint centerlines node to node — the current (v0.16) behavior.",
  rigid: "Auto rigid end zones sized to the joints — <b>stiffer</b> frame, smaller drift.",
  scissors: "Flexible panel-zone shear spring (scissors) — <b>softer</b> frame, adds drift.",
};
function syncPanelZoneUI() {
  if (!store.model) return;
  const pz = store.model.panel_zones || "none";
  $("panelZoneSelect").value = pz;
  $("panelZoneNote").innerHTML = PANEL_ZONE_NOTES[pz] || PANEL_ZONE_NOTES.none;
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
  // v0.8: spring supports are global base anchors — not per-story
  if (tool === "spring") {
    const base = m.stories.length ? m.stories[0].elevation - m.stories[0].height : 0;
    if (ME.addSpringSupport(m, payload.x, payload.y, base)) {
      markDirty();
      renderStaticViews();
    }
    return;
  }
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
  } else if (tool === "spring") {
    const p = payload.p;
    if (ME.addSpringSupport(m, p[0], p[1], p[2])) made++;
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
  const members = [], shells = [], links = [], springs = [];
  for (const ref of store.selection) {
    if (ref.type === "member") {
      const mm = m.members.find(x => x.uid === ref.uid);
      if (mm) members.push(mm);
    } else if (ref.type === "link") {
      const l = (m.links || []).find(x => x.uid === ref.uid);
      if (l) links.push(l);
    } else if (ref.type === "spring") {
      const s = ME.springByKey(m, ref.uid);
      if (s) springs.push(s);
    } else {
      const s = m.shells.find(x => x.uid === ref.uid);
      if (s) shells.push(s);
    }
  }
  return { members, shells, links, springs };
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
  const { members, shells, links, springs } = selObjects();
  const total = members.length + shells.length + links.length + springs.length;
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
    [springs.length, "spring"],
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
    // v0.12 — axial-limit behavior (tension/compression-only). Any FrameMember,
    // most useful on braces. Limited members make the analysis nonlinear.
    const axl = commonVal(members, x => ME.axialLimit(x));
    html += `
      <div class="field"><label for="propAxial">Axial behavior <span class="unit">tension / compression-only</span></label>
        <select id="propAxial">
          ${axl === undefined ? `<option value="" selected disabled>— mixed —</option>` : ""}
          <option value="both"${axl === "both" ? " selected" : ""}>Both (default)</option>
          <option value="tension"${axl === "tension" ? " selected" : ""}>Tension-only</option>
          <option value="compression"${axl === "compression" ? " selected" : ""}>Compression-only</option>
        </select>
      </div>
      ${axl && axl !== "both"
        ? `<p class="muted axial-note" style="font-size:11px">Tension/compression-only members carry a <b>${axl === "tension" ? "T-only" : "C-only"}</b> glyph in plan &amp; 3D and make the analysis <b>nonlinear</b>.</p>`
        : `<p class="muted" style="font-size:11px">Restricting a member to tension- or compression-only makes the analysis nonlinear.</p>`}`;
    // v0.19 — automatic ASCE 41 plastic hinges (pushover asce41 mode)
    // v0.21 — fiber PMM hinges (axial–biaxial-moment interaction fibers)
    const hng = commonVal(members, x => x.hinges || "none");
    html += `
      <div class="field"><label for="propHinges">Plastic hinges <span class="unit">ASCE 41 · pushover</span></label>
        <select id="propHinges">
          ${hng === undefined ? `<option value="" selected disabled>— mixed —</option>` : ""}
          <option value="none"${hng === "none" ? " selected" : ""}>None (elastic)</option>
          <option value="auto_m3"${hng === "auto_m3" ? " selected" : ""}>Auto M3 (ASCE 41-17)</option>
          <option value="fiber_pmm"${hng === "fiber_pmm" ? " selected" : ""}>Fiber PMM (ASCE 41)</option>
        </select>
      </div>
      <p class="muted" style="font-size:11px">Auto M3 members get trilinear ASCE 41-17 hinge backbones at both ends in a pushover case with hinge mode <b>asce41</b> (Table 9-7.1 steel / Table 10-7 concrete). Fiber PMM members get fiber hinges with axial–moment interaction — best with a designer (polygon + rebar) section.</p>`;
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
    // v0.9 — rigid end offsets (rigid zones) on any frame member
    const ri = commonVal(members, x => x.rigid_i ?? 0);
    const rj = commonVal(members, x => x.rigid_j ?? 0);
    const rf = commonVal(members, x => x.rigid_factor ?? 1);
    html += `
      <h3 class="group-title">Rigid end offsets <span class="unit">rigid zones at member ends</span></h3>
      <div class="field-row">
        <div class="field"><label for="propRigidI">i-end <span class="unit">m</span></label>
          <input id="propRigidI" class="rigid-in" type="number" step="0.05" min="0"
            value="${ri === undefined ? "" : ri}" placeholder="${ri === undefined ? "mixed" : ""}"></div>
        <div class="field"><label for="propRigidJ">j-end <span class="unit">m</span></label>
          <input id="propRigidJ" class="rigid-in" type="number" step="0.05" min="0"
            value="${rj === undefined ? "" : rj}" placeholder="${rj === undefined ? "mixed" : ""}"></div>
      </div>
      <div class="field"><label for="propRigidFactor">Rigid factor <span class="unit">0 = none · 1 = fully rigid</span></label>
        <div class="rigid-factor-row">
          <input id="propRigidFactorRange" class="rigid-range" type="range" min="0" max="1" step="0.05"
            value="${rf === undefined ? 1 : rf}">
          <input id="propRigidFactor" class="rigid-factor-num" type="number" min="0" max="1" step="0.05"
            value="${rf === undefined ? "" : rf}" placeholder="${rf === undefined ? "mixed" : ""}">
        </div>
      </div>
      <p class="muted" style="font-size:11px">Stiff end zones over rigid_i / rigid_j at each end; factor scales the added rigidity (ETABS end-length offset).</p>`;
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
    // v0.8 — thermal load (ΔT °C) into a pattern's thermal_loads
    const dT = commonVal(members, x => ME.getThermalLoad(m, store.loadPattern, x.uid) ?? 0);
    html += `
      <h3 class="group-title">Thermal load <span class="unit">uniform ΔT · α ${(m.thermal_alpha ?? 1.2e-5).toExponential(1)} /°C</span></h3>
      <div class="load-row">
        <div class="field"><label for="propThermPat">Pattern</label>
          <select id="propThermPat">${patOpts(store.loadPattern)}</select></div>
        <div class="field"><label for="propThermDT">ΔT <span class="unit">°C</span></label>
          <input id="propThermDT" type="number" step="5"
            value="${dT === undefined ? "" : dT}" placeholder="${dT === undefined ? "mixed" : "0 = none"}"></div>
      </div>
      <p class="muted" style="font-size:11px">Adds a uniform temperature change to the selected member${members.length > 1 ? "s" : ""} in the chosen pattern (ETABS-style thermal load).</p>`;
    // v0.11 — elastic (Winkler) foundation: subgrade modulus ks + bearing width
    const onFdn = commonVal(members, x => ME.onFoundation(x));
    const ks = commonVal(members, x => x.foundation_ks ?? 0);
    const fw = commonVal(members, x => x.foundation_width ?? 0);
    const fdnOn = onFdn === true;
    html += `
      <h3 class="group-title">Foundation (Winkler) <span class="unit">elastic soil bed</span></h3>
      <div class="field"><label class="fdn-check"><input type="checkbox" id="propFdnOn"${fdnOn ? " checked" : ""}${onFdn === undefined ? ' data-mixed="1"' : ""}> On elastic foundation</label></div>
      <div class="field-row${fdnOn ? "" : " fdn-off"}" id="propFdnFields">
        <div class="field"><label for="propFdnKs">Subgrade k<sub>s</sub> <span class="unit">kN/m³</span></label>
          <input id="propFdnKs" type="number" step="1000" min="0"${fdnOn ? "" : " disabled"}
            value="${ks === undefined ? "" : ks}" placeholder="${ks === undefined ? "mixed" : ""}"></div>
        <div class="field"><label for="propFdnW">Bearing width <span class="unit">m</span></label>
          <input id="propFdnW" type="number" step="0.05" min="0"${fdnOn ? "" : " disabled"}
            value="${fw === undefined ? "" : fw}" placeholder="${fw === undefined ? "mixed" : ""}"></div>
      </div>
      <p class="muted" style="font-size:11px">Members on a foundation carry a soil/spring-bed glyph in plan &amp; 3D. Active only when both k<sub>s</sub> and width &gt; 0.</p>`;
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
    /* v0.15 — wall piers: label + model-level auto-label toggle */
    if (walls.length) {
      const pierV = commonVal(walls, x => x.pier || "");
      html += `
      <h3 class="group-title">Wall pier <span class="unit">in-plane design forces</span></h3>
      <div class="field"><label for="propPier">Pier label</label>
        <input id="propPier" type="text" spellcheck="false" autocomplete="off"
          value="${pierV === undefined ? "" : esc(pierV)}"
          placeholder="${pierV === undefined ? "mixed" : "e.g. P1 (blank = not a pier)"}"></div>
      <div class="field"><label class="fdn-check"><input type="checkbox" id="propAutoPier"${m.auto_pier_walls ? " checked" : ""}> Auto-label all walls as piers <span class="unit">model-wide</span></label></div>
      <p class="muted" style="font-size:11px">Walls sharing a pier label are grouped into one vertical pier — story-wise P/V/M design forces appear in the <b>Wall Piers</b> results tab after a solve. Auto-label lets the backend assign a pier to every unlabeled wall.</p>`;
    }
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

  /* v0.5 — link stiffness (6 dof) · v0.15 — link device types + params */
  if (links.length) {
    const lt = commonVal(links, l => ME.linkTypeOf(l));
    html += `
      <h3 class="group-title">Link device <span class="unit">type &amp; parameters</span></h3>
      <div class="field"><label for="propLinkType">Type</label>
        <select id="propLinkType">
          ${lt === undefined ? `<option value="" selected disabled>— mixed —</option>` : ""}
          ${Object.entries(ME.LINK_TYPES).map(([k, def]) =>
            `<option value="${k}"${k === lt ? " selected" : ""}>${esc(def.label)}</option>`).join("")}
        </select>
      </div>`;
    if (lt === undefined) {
      html += `<p class="muted" style="font-size:11px">Mixed device types — pick a type above to unify, or select links of one type to edit parameters.</p>`;
    } else if (lt === "elastic") {
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
    } else {
      const def = ME.LINK_TYPES[lt];
      html += `<div class="link-stiff link-params">` +
        def.params.map(([k, unit]) => {
          const v = commonVal(links, l => (l.params || {})[k]);
          return `<label><span>${esc(k)} <span class="unit">${esc(unit)}</span></span>
          <input type="number" class="linkP" data-pk="${esc(k)}" step="any"
            value="${v === undefined ? "" : v}" placeholder="${v === undefined ? "mixed" : ""}"></label>`;
        }).join("") + `</div>`;
      // v0.21 — multilinear devices: editable (d, F) points table
      if (def.points) {
        if (links.length === 1) {
          const pts = ((links[0].params || {}).points) || [];
          html += `
      <div class="ml-points" id="mlPoints">
        <div class="ml-row head"><span>d (m)</span><span>F (kN)</span><span></span></div>` +
            pts.map((p, i) => `<div class="ml-row" data-i="${i}">
          <input type="number" step="0.01" data-mk="0" value="${p[0]}" title="Deformation d (m)">
          <input type="number" step="10" data-mk="1" value="${p[1]}" title="Force F (kN)">
          <button class="chip-x ml-del" data-del="${i}" title="Remove point">✕</button></div>`).join("") +
          `</div>
      <button class="btn btn-small btn-block" id="mlAdd" style="margin-top:6px">+ Point</button>`;
        } else {
          html += `<p class="muted" style="font-size:11px">Select a single link to edit the (d, F) points table.</p>`;
        }
      }
    }
    if (lt !== undefined)
      html += `<p class="muted link-note" style="font-size:11px">${esc(ME.LINK_TYPES[lt].note)}</p>`;
  }

  /* v0.8 — spring support stiffness (6 dof, grounded) */
  if (springs.length) {
    const SK = [
      ["Kx", "kN/m"], ["Ky", "kN/m"], ["Kz", "kN/m"],
      ["Krx", "kN·m/rad"], ["Kry", "kN·m/rad"], ["Krz", "kN·m/rad"],
    ];
    const pt = springs.length === 1
      ? ` <span class="unit">@ ${fmt(springs[0].point[0], 1)}, ${fmt(springs[0].point[1], 1)}, ${fmt(springs[0].point[2], 1)} m</span>` : "";
    html += `
      <h3 class="group-title">Spring support stiffness${pt}</h3>
      <div class="link-stiff spring-stiff">` +
      SK.map(([lbl, unit], i) => {
        const v = commonVal(springs, s => s.stiffness[i]);
        return `<label><span>${lbl} <span class="unit">${unit}</span></span>
          <input type="number" class="springK" data-si="${i}" step="10000" min="0"
            value="${v === undefined ? "" : v}" placeholder="${v === undefined ? "mixed" : ""}"></label>`;
      }).join("") + `</div>
      <p class="muted" style="font-size:11px;margin-top:6px">Replaces base fixity at these points with a 6-dof elastic support.</p>`;
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
  // v0.12 — axial-limit behavior (tension/compression-only)
  on("propAxial", "change", e => {
    const v = e.target.value;
    if (v !== "both" && v !== "tension" && v !== "compression") return;
    for (const mm of members) mm.axial_limit = v;
    markDirty();
    store.modelEdited = true;
    refreshDrawViews();     // T-only/C-only glyphs live in the label layer
    renderProps();          // refresh the nonlinear note
  });
  // v0.19 — plastic hinge assignment (v0.21: + fiber_pmm)
  on("propHinges", "change", e => {
    const v = e.target.value;
    if (v !== "none" && v !== "auto_m3" && v !== "fiber_pmm") return;
    for (const mm of members) mm.hinges = v;
    markDirty();
    store.modelEdited = true;
    renderProps();
  });
  // v0.9 — rigid end offsets + rigid factor
  const setRigid = (key, raw, hi) => {
    const v = parseFloat(raw);
    if (!isFinite(v) || v < 0 || (hi != null && v > hi)) return;
    for (const mm of members) mm[key] = v;
    markDirty();
    store.modelEdited = true;
    refreshDrawViews();          // rigid-zone glyphs live in the element layer
  };
  on("propRigidI", "change", e => setRigid("rigid_i", e.target.value));
  on("propRigidJ", "change", e => setRigid("rigid_j", e.target.value));
  on("propRigidFactor", "change", e => {
    setRigid("rigid_factor", e.target.value, 1);
    const rng = $("propRigidFactorRange");
    const v = parseFloat(e.target.value);
    if (rng && isFinite(v)) rng.value = String(Math.min(Math.max(v, 0), 1));
  });
  on("propRigidFactorRange", "input", e => {
    const num = $("propRigidFactor");
    if (num) num.value = e.target.value;
    setRigid("rigid_factor", e.target.value, 1);
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

  /* v0.15 — link device type + parameter wiring */
  on("propLinkType", "change", e => {
    const t = e.target.value;
    if (!ME.LINK_TYPES[t]) return;
    for (const l of links) ME.setLinkType(l, t);
    markDirty();
    store.modelEdited = true;
    refreshDrawViews();          // glyph silhouette + type letter change
    renderProps();               // swap the parameter form + note
  });
  box.querySelectorAll(".linkP").forEach(inp =>
    inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      if (!isFinite(v)) return;
      const k = inp.dataset.pk;
      for (const l of links) {
        // guard against a late blur-change from a replaced form: only write
        // keys that belong to the link's CURRENT device type
        if (!ME.LINK_TYPES[ME.linkTypeOf(l)].params.some(([pk]) => pk === k)) continue;
        l.params = l.params || {};
        l.params[k] = v;
      }
      markDirty();
    }));

  /* v0.21 — multilinear link (d, F) points table (single link selected) */
  if (links.length === 1 && $("mlPoints")) {
    const l0 = links[0];
    const pts = (l0.params = l0.params || {}).points ||
      (l0.params.points = ME.LINK_TYPES.multilinear.defaultPoints.map(p => [...p]));
    box.querySelectorAll("#mlPoints .ml-row:not(.head) input").forEach(inp =>
      inp.addEventListener("change", () => {
        const i = parseInt(inp.closest(".ml-row").dataset.i, 10);
        const v = parseFloat(inp.value);
        if (!isFinite(v) || !pts[i]) {
          inp.value = pts[i] ? String(pts[i][+inp.dataset.mk]) : "";
          return;
        }
        pts[i][+inp.dataset.mk] = v;
        markDirty();
      }));
    box.querySelectorAll(".ml-del").forEach(btn =>
      btn.addEventListener("click", () => {
        if (pts.length <= 1) {
          toast("Multilinear link", "At least one (d, F) point is required", "error", 3500);
          return;
        }
        pts.splice(parseInt(btn.dataset.del, 10), 1);
        markDirty();
        renderProps();
      }));
    on("mlAdd", "click", () => {
      const last = pts[pts.length - 1] || [0.05, 100];
      pts.push([+(last[0] + 0.05).toFixed(3), +(last[1] + 30).toFixed(1)]);
      markDirty();
      renderProps();
    });
  }

  /* v0.15 — wall pier label + model-level auto-label toggle */
  on("propPier", "change", e => {
    const label = e.target.value.trim();
    for (const w of walls) w.pier = label;
    markDirty();
  });
  on("propAutoPier", "change", e => {
    m.auto_pier_walls = !!e.target.checked;
    markDirty();
  });

  /* v0.8 — spring support stiffness wiring */
  box.querySelectorAll(".springK").forEach(inp =>
    inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      if (!isFinite(v) || v < 0) return;
      const i = parseInt(inp.dataset.si, 10);
      for (const s of springs) s.stiffness[i] = v;
      markDirty();
    }));

  /* v0.8 — member thermal-load wiring */
  on("propThermPat", "change", e => { store.loadPattern = e.target.value; renderProps(); });
  on("propThermDT", "change", e => {
    const v = parseFloat(e.target.value);
    if (!isFinite(v)) return;
    for (const mm of members) ME.setThermalLoad(m, $("propThermPat").value, mm.uid, v);
    markDirty();
    store.modelEdited = true;
    refreshDrawViews();     // ΔT badges live in the label layer
  });

  /* v0.11 — elastic (Winkler) foundation wiring. The checkbox enables/writes
     ks + width; unchecking clears both (member leaves the foundation). */
  const applyFoundation = () => {
    const on = $("propFdnOn").checked;
    if (on) {
      let ks = parseFloat($("propFdnKs").value);
      let fw = parseFloat($("propFdnW").value);
      if (!(isFinite(ks) && ks > 0)) ks = 30000;   // sensible default subgrade
      if (!(isFinite(fw) && fw > 0)) fw = 0.6;      // default bearing width (m)
      for (const mm of members) { mm.foundation_ks = ks; mm.foundation_width = fw; }
    } else {
      for (const mm of members) { mm.foundation_ks = 0; mm.foundation_width = 0; }
    }
    markDirty();
    store.modelEdited = true;
    refreshDrawViews();      // soil/spring-bed glyph lives in the element layer
    renderProps();           // re-sync the enabled/disabled fields
  };
  on("propFdnOn", "change", applyFoundation);
  on("propFdnKs", "change", applyFoundation);
  on("propFdnW", "change", applyFoundation);
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
      props.textContent = `${s.shape === "W" ? "W-shape" : s.shape === "designer" ? "designer" : "library"} · A ${sci(s.A)} · I33 ${sci(s.I33)}`;
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

  /* v0.21 — designer (polygon + rebar) sections: list + re-edit launcher */
  const dsBox = $("designerSectionRows");
  if (dsBox) {
    dsBox.textContent = "";
    const entries = Object.entries(m.designer_sections || {});
    if (!entries.length) {
      dsBox.innerHTML = `<p class="lib-none">No designer sections yet — click “Section Designer…” to draw one.</p>`;
    } else {
      dsBox.appendChild(mgrRow(["Name", "A (m²)", "I33 (m⁴)", "I22 (m⁴)", ""], "mgr-row lib head"));
      for (const [name] of entries) {
        const s = m.sections[name] || {};
        const edit = document.createElement("button");
        edit.className = "btn btn-small";
        edit.textContent = "Edit…";
        edit.title = `Open ${name} in the Section Designer`;
        edit.addEventListener("click", () => openSectionDesigner(name));
        dsBox.appendChild(mgrRow([name, sci(s.A), sci(s.I33), sci(s.I22), edit], "mgr-row lib"));
      }
    }
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

/* v0.14 — per-grid-system chip / preview tints (match draw.js order) */
const GRID_TINT_CSS = ["#8aa0b8", "#e0a020", "#34c384", "#a78bfa", "#35b5e5"];

function renderGridEditor() {
  const m = store.model;
  if (!m || !m.grid) return;
  ME.ensureGridSystems(m);
  const systems = ME.gridSystems(m);
  if (store.gridSel == null || store.gridSel < 0 || store.gridSel >= systems.length)
    store.gridSel = 0;
  renderGridSysList(systems);
  renderGridSysEditor(systems[store.gridSel]);
  renderGridPreview(systems);
  renderStoryRows();
}

/** Geometry changed inside a grid SYSTEM (position/origin/rotation/radii). */
function afterGridSysEdit() {
  ME.ensureGridSystems(store.model);
  markDirty();
  refreshDrawViews();
  renderSummary();
  renderGridEditor();
}

function renderGridSysList(systems) {
  const box = $("gridSysList");
  box.textContent = "";
  systems.forEach((sys, i) => {
    const chip = document.createElement("button");
    chip.className = "grid-sys-chip" + (i === store.gridSel ? " is-active" : "");
    chip.style.setProperty("--gs-tint", GRID_TINT_CSS[i % GRID_TINT_CSS.length]);
    const name = document.createElement("span");
    name.className = "gs-name"; name.textContent = sys.name;
    const kind = document.createElement("span");
    kind.className = "gs-kind"; kind.textContent = sys.kind === "radial" ? "radial" : "ortho";
    chip.append(name, kind);
    if (i === 0) { const p = document.createElement("span"); p.className = "gs-primary"; p.textContent = "primary"; p.title = "Legacy single grid"; chip.appendChild(p); }
    if (i > 0) {
      const x = document.createElement("span");
      x.className = "gs-del"; x.textContent = "✕"; x.title = "Delete grid system";
      x.addEventListener("click", ev => {
        ev.stopPropagation();
        if (ME.removeGridSystem(store.model, i)) {
          store.gridSel = Math.min(store.gridSel, ME.gridSystems(store.model).length - 1);
          afterGridSysEdit();
          toast("Grid system removed", `“${sys.name}” deleted`, "info", 3000);
        }
      });
      chip.appendChild(x);
    }
    chip.addEventListener("click", () => { store.gridSel = i; renderGridEditor(); });
    box.appendChild(chip);
  });
}

function renderGridSysEditor(sys) {
  const box = $("gridSysEditor");
  box.textContent = "";
  if (!sys) return;

  const field = (label, input) => {
    const wrap = document.createElement("label");
    wrap.className = "gs-field";
    const span = document.createElement("span"); span.textContent = label;
    wrap.append(span, input);
    return wrap;
  };
  const numInput = (val, step, onChange) => {
    const inp = document.createElement("input");
    inp.type = "number"; inp.step = String(step); inp.value = String(val);
    inp.addEventListener("change", () => {
      const nv = parseFloat(inp.value);
      if (isFinite(nv)) onChange(nv); else inp.value = String(val);
    });
    return inp;
  };

  /* name + origin + rotation */
  const meta = document.createElement("div");
  meta.className = "gs-meta";
  const nameIn = document.createElement("input");
  nameIn.type = "text"; nameIn.value = sys.name; nameIn.spellcheck = false;
  nameIn.addEventListener("change", () => {
    const nu = nameIn.value.trim();
    if (nu) { sys.name = nu; afterGridSysEdit(); } else nameIn.value = sys.name;
  });
  meta.append(
    field("Name", nameIn),
    field("Origin X (m)", numInput(sys.origin[0], 0.5, v => { sys.origin[0] = v; afterGridSysEdit(); })),
    field("Origin Y (m)", numInput(sys.origin[1], 0.5, v => { sys.origin[1] = v; afterGridSysEdit(); })),
    field("Rotation (° CCW)", numInput(sys.rotation, 5, v => { sys.rotation = v; afterGridSysEdit(); })),
  );
  box.appendChild(meta);

  if (sys.kind === "orthogonal") box.appendChild(orthoLineEditor(sys));
  else box.appendChild(radialEditor(sys));
}

/** Orthogonal system: the X/Y line-position lists (reuses the ge-cols look). */
function orthoLineEditor(sys) {
  const wrap = document.createElement("div");
  wrap.className = "ge-cols";
  const mkCol = (axis, title) => {
    const col = document.createElement("div");
    const head = document.createElement("div");
    head.className = "ge-col-head";
    const b = document.createElement("b"); b.textContent = title;
    const add = document.createElement("button");
    add.className = "btn btn-small"; add.textContent = "+ Line";
    add.addEventListener("click", () => { ME.addSysLine(sys, axis); afterGridSysEdit(); });
    head.append(b, add);
    const rows = document.createElement("div");
    rows.className = "ge-lines";
    const lines = axis === "x" ? sys.x_lines : sys.y_lines;
    const labels = axis === "x" ? sys.x_labels : sys.y_labels;
    lines.forEach((v, i) => {
      const row = document.createElement("div"); row.className = "ge-line";
      const lab = document.createElement("span");
      lab.className = "ge-label"; lab.textContent = (labels && labels[i]) || String(i + 1);
      const inp = document.createElement("input");
      inp.type = "number"; inp.step = "0.5"; inp.value = String(v);
      inp.addEventListener("change", () => {
        const nv = parseFloat(inp.value);
        if (ME.setSysLine(sys, axis, i, nv)) afterGridSysEdit();
        else { inp.value = String(v); toast("Grid edit rejected", "Positions must be numbers and can't collide with another line", "error", 4500); }
      });
      const del = document.createElement("button");
      del.className = "del"; del.textContent = "✕";
      del.disabled = lines.length <= 2;
      del.title = del.disabled ? "A grid keeps at least two lines per direction" : "Remove line";
      del.addEventListener("click", () => { if (ME.removeSysLine(sys, axis, i)) afterGridSysEdit(); });
      row.append(lab, inp, del);
      rows.appendChild(row);
    });
    col.append(head, rows);
    return col;
  };
  wrap.append(mkCol("x", "X lines"), mkCol("y", "Y lines"));
  return wrap;
}

/** Radial system: radii list + spoke-angle list. */
function radialEditor(sys) {
  const wrap = document.createElement("div");
  wrap.className = "ge-cols";
  const mkList = (title, arr, unit, addFn, setFn, delFn) => {
    const col = document.createElement("div");
    const head = document.createElement("div");
    head.className = "ge-col-head";
    const b = document.createElement("b"); b.textContent = title;
    const add = document.createElement("button");
    add.className = "btn btn-small"; add.textContent = "+";
    add.title = `Add ${title.toLowerCase()}`;
    add.addEventListener("click", () => { addFn(sys); afterGridSysEdit(); });
    head.append(b, add);
    const rows = document.createElement("div");
    rows.className = "ge-lines";
    arr.forEach((v, i) => {
      const row = document.createElement("div"); row.className = "ge-line";
      const lab = document.createElement("span");
      lab.className = "ge-label"; lab.textContent = unit;
      const inp = document.createElement("input");
      inp.type = "number"; inp.step = "0.5"; inp.value = String(v);
      inp.addEventListener("change", () => {
        const nv = parseFloat(inp.value);
        if (isFinite(nv) && setFn(sys, i, nv)) afterGridSysEdit();
        else inp.value = String(v);
      });
      const del = document.createElement("button");
      del.className = "del"; del.textContent = "✕";
      del.disabled = arr.length <= 1;
      del.addEventListener("click", () => { if (delFn(sys, i)) afterGridSysEdit(); });
      row.append(lab, inp, del);
      rows.appendChild(row);
    });
    col.append(head, rows);
    return col;
  };
  wrap.append(
    mkList("Radii", sys.radii, "m", ME.addSysRadius, ME.setSysRadius, ME.removeSysRadius),
    mkList("Spoke angles", sys.theta_deg, "°", ME.addSysTheta, ME.setSysTheta, ME.removeSysTheta),
  );
  return wrap;
}

/** Live plan preview of all systems (global coords), selected one highlighted. */
function renderGridPreview(systems) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = $("gridPreview");
  if (!svg) return;
  svg.textContent = "";
  // gather bounds from every system's intersections + circle extents
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  const geos = systems.map(s => ME.gridSystemGeometry(s));
  geos.forEach((geo) => {
    for (const [x, y] of geo.intersections) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
    for (const [cx, cy, r] of geo.circles) { x0 = Math.min(x0, cx - r); x1 = Math.max(x1, cx + r); y0 = Math.min(y0, cy - r); y1 = Math.max(y1, cy + r); }
  });
  if (!isFinite(x0)) return;
  const pad = Math.max((x1 - x0), (y1 - y0)) * 0.08 + 1;
  x0 -= pad; x1 += pad; y0 -= pad; y1 += pad;
  const W = 340, H = 220;
  const sc = Math.min(W / (x1 - x0 || 1), H / (y1 - y0 || 1));
  const ox = (W - (x1 - x0) * sc) / 2, oy = (H - (y1 - y0) * sc) / 2;
  const SX = x => ox + (x - x0) * sc;
  const SY = y => H - (oy + (y - y0) * sc);   // flip Y (north up)
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const mk = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };

  geos.forEach((geo, si) => {
    const sel = si === store.gridSel;
    const tint = GRID_TINT_CSS[si % GRID_TINT_CSS.length];
    const g = mk("g", { opacity: sel ? "1" : "0.4" });
    for (const [ax, ay, bx, by] of geo.segments)
      g.appendChild(mk("line", { x1: SX(ax), y1: SY(ay), x2: SX(bx), y2: SY(by), stroke: tint, "stroke-width": sel ? 1.4 : 1 }));
    for (const [cx, cy, r] of geo.circles)
      g.appendChild(mk("circle", { cx: SX(cx), cy: SY(cy), r: r * sc, fill: "none", stroke: tint, "stroke-width": sel ? 1.4 : 1 }));
    if (geo.center)
      g.appendChild(mk("circle", { cx: SX(geo.center[0]), cy: SY(geo.center[1]), r: 2.4, fill: tint }));
    if (sel) for (const [x, y] of geo.intersections)
      g.appendChild(mk("circle", { cx: SX(x), cy: SY(y), r: 1.6, fill: tint }));
    svg.appendChild(g);
  });
}

function renderStoryRows() {
  const m = store.model;
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
  store.steelResult = null;                       // v0.6
  store.concreteResult = null;
  store.designCase = null;
  store.svcCase = null;                           // v0.16
  store.llrData = null;                           // v0.16 — new geometry, new areas
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

/* ---- v0.6: template gallery (File → New) */
const TEMPLATES = [
  { id: "defaults", name: "Quick defaults", sub: "3×2 bays · 4 stories", stories: 4, bays: 3,
    params: {} },
  { id: "office4", name: "Low-rise office", sub: "4 stories · 4×3 bays", stories: 4, bays: 4,
    params: { name: "Office 4-story", stories: 4, bays_x: 4, bays_y: 3,
      bay_width_x: 7.5, bay_width_y: 6, story_height: 3.6 } },
  { id: "mid12", name: "Mid-rise tower", sub: "12 stories · 4×4 bays", stories: 12, bays: 4,
    params: { name: "Mid-rise 12-story", stories: 12, bays_x: 4, bays_y: 4,
      bay_width_x: 6.5, bay_width_y: 6.5, column_size: 0.65 } },
  { id: "tall20", name: "Tall core tower", sub: "20 stories · 3×3 bays", stories: 20, bays: 3,
    params: { name: "Tall core 20-story", stories: 20, bays_x: 3, bays_y: 3,
      bay_width_x: 6, bay_width_y: 6, column_size: 0.8, story_height: 3.4 } },
  { id: "portal", name: "Single-bay portal", sub: "1 story · 1×1 bay", stories: 1, bays: 1,
    params: { name: "Portal frame", stories: 1, bays_x: 1, bays_y: 1,
      bay_width_x: 8, bay_width_y: 6, story_height: 4 } },
];

/** Tiny elevation-style SVG thumbnail: stacked floors × bays. */
function templateThumb(stories, bays) {
  const W = 132, H = 92, P = 10;
  const gw = W - 2 * P, gh = H - 2 * P;
  const ns = Math.min(stories, 12), nb = Math.min(bays, 5);
  const dy = gh / ns, dx = gw / nb;
  let s = `<svg viewBox="0 0 ${W} ${H}" class="tpl-thumb" aria-hidden="true">`;
  s += `<rect x="0" y="0" width="${W}" height="${H}" rx="6" fill="rgba(53,181,229,0.05)"/>`;
  for (let i = 0; i <= nb; i++) {
    const x = P + i * dx;
    s += `<line x1="${x}" y1="${P}" x2="${x}" y2="${H - P}" stroke="rgba(125,168,216,0.7)" stroke-width="1.3"/>`;
  }
  for (let j = 0; j <= ns; j++) {
    const y = P + j * dy;
    s += `<line x1="${P}" y1="${y}" x2="${W - P}" y2="${y}" stroke="rgba(154,167,180,0.55)" stroke-width="1.1"/>`;
  }
  return s + `</svg>`;
}

function openGallery() {
  const grid = $("galleryGrid");
  grid.innerHTML = TEMPLATES.map(t => `
    <button class="gallery-card" data-id="${t.id}" title="Generate ${esc(t.name)}">
      ${templateThumb(t.stories, t.bays)}
      <div class="gallery-meta"><b>${esc(t.name)}</b><span class="muted">${esc(t.sub)}</span></div>
    </button>`).join("");
  grid.querySelectorAll(".gallery-card").forEach(btn =>
    btn.addEventListener("click", () => pickTemplate(btn.dataset.id)));
  $("galleryModal").classList.remove("hidden");
}

async function pickTemplate(id) {
  const tpl = TEMPLATES.find(t => t.id === id);
  if (!tpl) return;
  if (store.dirty) {
    const ok = await askConfirm("New model",
      "You have unsaved changes — start a new model and discard them?", "New model");
    if (!ok) return;
  }
  try {
    adoptModel(await generateModel(tpl.params), null);
    $("galleryModal").classList.add("hidden");
    toast("New model", `${tpl.name} generated`, "info", 4000);
  } catch (err) {
    toast("New model failed", err.message, "error");
  }
}

function fileNew() { openGallery(); }

/* ================================================================
   v0.6 — IMPORT DIALOG (DXF / e2k / IFC)
   ================================================================ */
function openImportDialog() {
  store.importText = "";
  store.importFileName = "";
  setImportFmt(store.importFmt || "dxf");
  $("importPreview").value = "";
  $("importFileName").textContent = "No file selected — a small demo fixture is used if left empty.";
  $("importWarnings").classList.add("hidden");
  $("importWarnings").innerHTML = "";
  $("importModal").classList.remove("hidden");
}

function setImportFmt(fmt) {
  store.importFmt = ["dxf", "e2k", "ifc"].includes(fmt) ? fmt : "dxf";
  document.querySelectorAll("#importFmtTabs .seg-btn").forEach(b =>
    b.classList.toggle("is-active", b.dataset.fmt === store.importFmt));
  $("importDxfOpts").classList.toggle("hidden", store.importFmt !== "dxf");
  if (store.importFmt === "dxf") renderImportStories();
  $("importHint").textContent = store.importFmt === "dxf"
    ? "DXF import maps LINE/POLYLINE entities to frames using the story heights & sections below."
    : `${store.importFmt.toUpperCase()} import reads the model geometry directly — no extra options.`;
}

function renderImportStories() {
  const box = $("importStoryRows");
  box.innerHTML = store.importStories.map((h, i) => `
    <div class="import-story-row" data-i="${i}">
      <span class="isr-label">Story ${i + 1}</span>
      <input type="number" step="0.1" min="0.5" value="${h}" data-i="${i}">
      <button class="chip-x" data-del="${i}" title="Remove story"${store.importStories.length <= 1 ? " disabled" : ""}>✕</button>
    </div>`).join("");
  box.querySelectorAll("input").forEach(inp =>
    inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      const i = parseInt(inp.dataset.i, 10);
      if (isFinite(v) && v > 0) store.importStories[i] = v;
      else inp.value = String(store.importStories[i]);
    }));
  box.querySelectorAll("[data-del]").forEach(btn =>
    btn.addEventListener("click", () => {
      if (store.importStories.length <= 1) return;
      store.importStories.splice(parseInt(btn.dataset.del, 10), 1);
      renderImportStories();
    }));
}

function readImportFile(file) {
  if (!file) return;
  store.importFileName = file.name;
  $("importFileName").textContent = `${file.name} · ${file.size} bytes`;
  const reader = new FileReader();
  reader.onload = () => {
    store.importText = String(reader.result || "");
    $("importPreview").value = store.importText.slice(0, 4000) +
      (store.importText.length > 4000 ? "\n… (truncated)" : "");
  };
  reader.onerror = () => toast("File read failed", file.name, "error");
  reader.readAsText(file);
}

async function doImport() {
  const btn = $("importDo");
  if (btn.disabled) return;
  const fmt = store.importFmt;
  const body = { text: store.importText || "SKYFRAME-DEMO-FIXTURE" };
  if (fmt === "dxf") {
    body.stories = store.importStories.slice();
    body.column_section = $("importColSec").value.trim() || "COL";
    body.beam_section = $("importBeamSec").value.trim() || "BEAM";
    body.wall_section = $("importWallSec").value.trim() || "SH200";
    body.unit_scale = $("importUnitScale").value;
  }
  if (store.dirty) {
    const ok = await askConfirm("Import model",
      "You have unsaved changes — import and discard them?", "Import");
    if (!ok) return;
  }
  btn.disabled = true;
  $("importSpinner").classList.remove("hidden");
  try {
    const res = await importModelFile(fmt, body);
    if (!res || !res.model) throw new Error("importer returned no model");
    adoptModel(res.model, null);
    const warnings = res.warnings || [];
    // model adopted; keep the dialog up to display the warnings expandable list
    toast("Model imported",
      `${fmt.toUpperCase()} · ${warnings.length} warning${warnings.length === 1 ? "" : "s"} · adopted as the working model`,
      "info", 5000);
    showImportWarnings(warnings, true);
  } catch (err) {
    // 400 / parse errors → error toast, keep the dialog open
    showImportWarnings([], false);
    toast("Import failed", err.message, "error", 8000);
  } finally {
    $("importSpinner").classList.add("hidden");
    btn.disabled = false;
  }
}

/** Render the warnings expandable list inside the import dialog. */
function showImportWarnings(warnings, ok) {
  const box = $("importWarnings");
  if (!warnings || !warnings.length) {
    box.classList.toggle("hidden", !ok);
    box.innerHTML = ok ? `<p class="import-ok">✓ Imported with no warnings.</p>` : "";
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML =
    `<details open class="warn-details"><summary>${ok ? "✓ Imported — " : ""}${warnings.length} warning${warnings.length === 1 ? "" : "s"}</summary>` +
    `<ul>${warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul></details>`;
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
// v0.16 — local transverse deflection diagram (mm), from member_deflections
const DIAG_DEFL = { key: "dy", title: "δy — deflection", unit: "mm", color: "#e5a50a", dec: 2 };

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
  // v0.16 — local transverse deflections (beams): {x, dy, dz} in m
  const md = cd && cd.member_deflections && cd.member_deflections[uid];
  const hasDefl = !!(md && Array.isArray(md.x) && Array.isArray(md.dy) &&
    md.dy.length === md.x.length);
  const main = $("memberDiagrams"), minor = $("memberDiagramsMinor");
  main.textContent = ""; minor.textContent = "";
  $("memberEmpty").classList.toggle("hidden", !!(st || hasDefl));
  $("memberMinor").classList.toggle("hidden", !st);
  const envBadge = isRsCase(store.caseName)
    ? ` <span class="env-badge" title="Response-spectrum values are positive envelopes — signs are indeterminate">envelope ±</span>` : "";
  $("memberCaseNote").innerHTML = ((st || hasDefl)
    ? `Station diagrams · case <b>${esc(caseLabel(store.caseName))}</b>`
    : `Case <b>${esc(caseLabel(store.caseName))}</b>`) + envBadge;
  if (st) {
    const vals = k => (st[k] && st[k].length === st.x.length) ? st[k] : st.x.map(() => 0);
    for (const d of DIAG)
      main.appendChild(stationDiagram(st.x, vals(d.key), d));
    for (const d of DIAG_MINOR)
      minor.appendChild(stationDiagram(st.x, vals(d.key), d));
  }
  if (hasDefl) {
    // δ diagram (mm) — appended alongside N/V2/M3; sag plots downward
    const card = stationDiagram(md.x, md.dy.map(v => v * 1000), DIAG_DEFL);
    card.classList.add("diagram-defl");
    main.appendChild(card);
    if (st && Array.isArray(md.dz) && md.dz.some(v => Math.abs(v) > 1e-9))
      minor.appendChild(stationDiagram(md.x, md.dz.map(v => v * 1000),
        { key: "dz", title: "δz — minor deflection", unit: "mm", color: "#77879b", dec: 2 }));
  }
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
  for (const t of ["story", "modal", "reactions", "forces", "design", "drift"]) {
    $(`empty-${t}`).classList.toggle("hidden", on);
    $(`content-${t}`).classList.toggle("hidden", !on);
  }
  if (on) {
    renderDesignForm(); renderDesignTable(); renderOptimizePanel(); renderLlrPanel();
    renderWallPanel(); renderPunchPanel(); renderDriftPanel();       // v0.18
    renderCompositePanel(); renderSlabPanel(); renderVibTable();     // v0.20
  }
  else if (store.tab === "design" || store.tab === "drift") switchTab("view3d");
  if (!on) {                                                         // v0.18
    clearVibTimer();                                                 // v0.20
    viewer.setMemberColors(null);
    $("driftLegend").classList.add("hidden");
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
  // v0.10: buckling tab appears only when results carry buckling factors
  const hasBuck = on && !!Object.keys(store.results?.buckling || {}).length;
  $("buckTabBtn").classList.toggle("hidden", !hasBuck);
  $("empty-buckling").classList.toggle("hidden", hasBuck);
  $("content-buckling").classList.toggle("hidden", !hasBuck);
  if (!hasBuck && store.tab === "buckling") switchTab("view3d");
  // v0.11: load-takedown tab appears only when results carry a takedown block
  const hasTd = on && !!Object.keys(store.results?.takedown || {}).length;
  $("tdTabBtn").classList.toggle("hidden", !hasTd);
  $("empty-takedown").classList.toggle("hidden", hasTd);
  $("content-takedown").classList.toggle("hidden", !hasTd);
  if (!hasTd && store.tab === "takedown") switchTab("view3d");
  // v0.13: section-cut-forces tab appears only when results carry section_cuts
  const hasCuts = on && !!Object.keys(store.results?.section_cuts || {}).length;
  $("cutTabBtn").classList.toggle("hidden", !hasCuts);
  $("empty-cuts").classList.toggle("hidden", hasCuts);
  $("content-cuts").classList.toggle("hidden", !hasCuts);
  if (!hasCuts && store.tab === "cuts") switchTab("view3d");
  // v0.15: wall-piers tab appears only when results carry a piers block
  const hasPiers = on && !!Object.keys(store.results?.piers || {}).length;
  $("pierTabBtn").classList.toggle("hidden", !hasPiers);
  $("empty-piers").classList.toggle("hidden", hasPiers);
  $("content-piers").classList.toggle("hidden", !hasPiers);
  if (!hasPiers && store.tab === "piers") switchTab("view3d");
  // v0.16: serviceability tab appears only when results carry deflection_checks
  const hasSvc = on && !!Object.keys(store.results?.deflection_checks || {}).length;
  $("svcTabBtn").classList.toggle("hidden", !hasSvc);
  $("empty-svc").classList.toggle("hidden", hasSvc);
  $("content-svc").classList.toggle("hidden", !hasSvc);
  if (!hasSvc && store.tab === "svc") switchTab("view3d");
  if (!on) {
    store.contour.on = false;
    syncContoursUI();
    syncEnvToggle();
  }
}

function renderResultsTabs() {
  if (!store.results || !caseData()) return;
  syncEnvToggle();
  syncStagedBadge();
  renderStoryTab();
  renderModalTab();
  renderReactionsTab();
  renderForcesTab();
  renderThTab();
  renderPoTab();
  renderBucklingTab();
  renderTakedownTab();
  renderCutsTab();
  renderPiersTab();
  renderSvcTab();
}

/* ---- story tab */
/** v0.8 — CM/CR for a story (case-independent story_props, or on the case's
    story dict). Returns the {cm_x,cm_y,cr_x,cr_y} object or null. */
function storyCmCr(s) {
  const r = store.results;
  if (!r) return null;
  const cd = tableCaseData();
  const sp = (r.story_props && r.story_props[s]) ||
    (cd && cd.story && cd.story[s]) || {};
  const has = ["cm_x", "cm_y", "cr_x", "cr_y"].some(k => isFinite(sp[k]));
  return has ? sp : null;
}
function hasCmCr() {
  const r = store.results;
  return !!(r && r.story_order.some(s => storyCmCr(s)));
}

function renderStoryTab() {
  const r = store.results, cd = tableCaseData();
  if (!cd) return;
  renderStoryCharts($("chartsRow"), r, cd, store.driftLimitPct);

  const cmcr = hasCmCr();
  const limRatio = store.driftLimitPct / 100;
  const head = `<thead><tr>
    <th class="txt">Story</th><th>Elev m</th>
    <th>ux mm</th><th>uy mm</th>
    <th>drift ‰ x</th><th>drift ‰ y</th>
    <th>Vx kN</th><th>Vy kN</th>` +
    (cmcr ? `<th>CM x m</th><th>CM y m</th><th>CR x m</th><th>CR y m</th><th>e m</th>` : "") +
    `</tr></thead>`;
  const rows = [...r.story_order].reverse().map(s => {
    const st = cd.story[s] || {};
    const exx = Math.abs(st.drift_x || 0) > limRatio, exy = Math.abs(st.drift_y || 0) > limRatio;
    const cc = cmcr ? storyCmCr(s) : null;
    const ecc = cc ? Math.hypot((cc.cm_x ?? 0) - (cc.cr_x ?? 0), (cc.cm_y ?? 0) - (cc.cr_y ?? 0)) : null;
    return `<tr${s === store.cmStory ? ` class="cm-active"` : ""}>
      <td class="txt">${esc(s)}</td>
      <td class="dim">${fmt(r.story_elev[s], 1)}</td>
      <td>${fmt((st.ux || 0) * 1000, 1)}</td>
      <td>${fmt((st.uy || 0) * 1000, 1)}</td>
      <td class="${exx ? "exceed" : ""}">${fmt(Math.abs(st.drift_x || 0) * 1000, 2)}</td>
      <td class="${exy ? "exceed" : ""}">${fmt(Math.abs(st.drift_y || 0) * 1000, 2)}</td>
      <td>${fmt(st.shear_x || 0, 1)}</td>
      <td>${fmt(st.shear_y || 0, 1)}</td>` +
      (cmcr ? (cc
        ? `<td>${fmt(cc.cm_x, 2)}</td><td>${fmt(cc.cm_y, 2)}</td>
           <td>${fmt(cc.cr_x, 2)}</td><td>${fmt(cc.cr_y, 2)}</td><td>${fmt(ecc, 3)}</td>`
        : `<td class="dim">—</td><td class="dim">—</td><td class="dim">—</td><td class="dim">—</td><td class="dim">—</td>`) : "") +
      `</tr>`;
  }).join("");
  $("storyTable").innerHTML = head + `<tbody>${rows}</tbody>`;

  renderDiagBlock();
  renderCmCrBlock();
}

/* ---- v0.9: story stiffness + irregularity diagnostics (ASCE 7 §12.3) */
/** Lateral case names that carry both story_stiffness and irregularity. */
function diagCaseNames() {
  const r = store.results;
  if (!r || !r.story_stiffness || !r.irregularity) return [];
  return Object.keys(r.story_stiffness).filter(cn => r.irregularity[cn]);
}
function hasDiagnostics() { return diagCaseNames().length > 0; }

const TORS_CHIP = { none: ["ok", "none"], torsional: ["warn", "torsional"], extreme: ["bad", "extreme"] };
const SOFT_CHIP = { none: ["ok", "none"], soft: ["warn", "soft"], extreme_soft: ["bad", "extreme soft"] };
function diagChip(map, flag) {
  const [cls, label] = map[flag] || map.none;
  return `<span class="diag-chip dc-${cls}">${esc(label)}</span>`;
}

function renderDiagBlock() {
  const block = $("storyDiagBlock");
  if (!block) return;
  const r = store.results;
  const names = diagCaseNames();
  block.classList.toggle("hidden", !names.length);
  if (!names.length) return;

  if (!store.diagCase || !names.includes(store.diagCase)) store.diagCase = names[0];
  const sel = $("diagCaseSelect");
  sel.innerHTML = names.map(n =>
    `<option value="${esc(n)}"${n === store.diagCase ? " selected" : ""}>${esc(n)}</option>`).join("");

  const stiff = r.story_stiffness[store.diagCase] || {};
  const irr = r.irregularity[store.diagCase] || {};
  const flagged = r.story_order.filter(s =>
    (irr[s] && (irr[s].flag !== "none" || (irr[s].soft_flag && irr[s].soft_flag !== "none")))).length;
  $("diagNote").innerHTML = `<b>${esc(store.diagCase)}</b> · ` +
    (flagged ? `<b class="diag-flagged">${flagged}</b> irregular stor${flagged > 1 ? "ies" : "y"} flagged`
      : `no irregularities flagged`);

  const head = `<thead><tr>
    <th class="txt">Story</th><th>Elev m</th>
    <th>kx kN/m</th><th>ky kN/m</th>
    <th>τ ratio x</th><th>τ ratio y</th><th class="txt">Torsion</th>
    <th>stiff ratio</th><th class="txt">Soft story</th></tr></thead>`;
  const rows = [...r.story_order].reverse().map(s => {
    const k = stiff[s] || {}, ir = irr[s] || {};
    const trx = ir.tors_ratio_x, tryy = ir.tors_ratio_y;
    const overX = isFinite(trx) && trx >= 1.2, overY = isFinite(tryy) && tryy >= 1.2;
    return `<tr>
      <td class="txt">${esc(s)}</td>
      <td class="dim">${fmt(r.story_elev[s], 1)}</td>
      <td>${fmt(k.kx, 0)}</td><td>${fmt(k.ky, 0)}</td>
      <td class="${overX ? "exceed" : ""}">${fmt(trx, 2)}</td>
      <td class="${overY ? "exceed" : ""}">${fmt(tryy, 2)}</td>
      <td class="txt">${diagChip(TORS_CHIP, ir.flag || "none")}</td>
      <td>${ir.stiff_ratio == null ? `<span class="dim">—</span>` : fmt(ir.stiff_ratio, 2)}</td>
      <td class="txt">${ir.stiff_ratio == null ? `<span class="dim">—</span>` : diagChip(SOFT_CHIP, ir.soft_flag || "none")}</td>
    </tr>`;
  }).join("");
  $("storyDiagTable").innerHTML = head + `<tbody>${rows}</tbody>`;
}

/** v0.8 — Center of mass / rigidity plan diagram for a selected story. */
function renderCmCrBlock() {
  const block = $("cmcrBlock");
  if (!block) return;
  const r = store.results;
  const avail = hasCmCr();
  block.classList.toggle("hidden", !avail);
  if (!avail) return;

  const stories = [...r.story_order].reverse().filter(s => storyCmCr(s));
  if (!store.cmStory || !stories.includes(store.cmStory)) store.cmStory = stories[0] || null;

  const sel = $("cmStorySelect");
  sel.innerHTML = stories.map(s =>
    `<option value="${esc(s)}"${s === store.cmStory ? " selected" : ""}>${esc(s)}</option>`).join("");

  const cc = storyCmCr(store.cmStory);
  const ecc = cc ? Math.hypot((cc.cm_x ?? 0) - (cc.cr_x ?? 0), (cc.cm_y ?? 0) - (cc.cr_y ?? 0)) : 0;
  $("cmcrNote").innerHTML =
    `<b>${esc(store.cmStory)}</b> · CM (${fmt(cc.cm_x, 2)}, ${fmt(cc.cm_y, 2)}) · ` +
    `CR (${fmt(cc.cr_x, 2)}, ${fmt(cc.cr_y, 2)}) · eccentricity <b>e = ${fmt(ecc, 3)} m</b>`;
  $("cmcrPlan").innerHTML = cmcrPlanSvg(cc);
}

/** SVG markup: story footprint outline + CM (filled circle), CR (target
    cross) and the eccentricity vector between them. */
function cmcrPlanSvg(cc) {
  const g = store.model.grid;
  const x0 = g.x_lines[0], x1 = g.x_lines[g.x_lines.length - 1];
  const y0 = g.y_lines[0], y1 = g.y_lines[g.y_lines.length - 1];
  const W = 300, H = 220, P = 26;
  const spanX = Math.max(x1 - x0, 1), spanY = Math.max(y1 - y0, 1);
  const sc = Math.min((W - 2 * P) / spanX, (H - 2 * P) / spanY);
  const px = x => P + (x - x0) * sc;
  const py = y => H - P - (y - y0) * sc;                 // y up
  const mline = "rgba(120,140,165,0.5)", flt = "rgba(120,140,165,0.22)";
  let svg = `<svg viewBox="0 0 ${W} ${H}" class="cmcr-svg" aria-label="Story CM/CR plan">`;
  // footprint + grid lines
  svg += `<rect x="${px(x0)}" y="${py(y1)}" width="${(x1 - x0) * sc}" height="${(y1 - y0) * sc}"
    fill="rgba(53,181,229,0.04)" stroke="${mline}" stroke-width="1.2"/>`;
  for (const x of g.x_lines)
    svg += `<line x1="${px(x)}" y1="${py(y0)}" x2="${px(x)}" y2="${py(y1)}" stroke="${flt}" stroke-width="1"/>`;
  for (const y of g.y_lines)
    svg += `<line x1="${px(x0)}" y1="${py(y)}" x2="${px(x1)}" y2="${py(y)}" stroke="${flt}" stroke-width="1"/>`;
  const cmx = px(cc.cm_x), cmy = py(cc.cm_y), crx = px(cc.cr_x), cry = py(cc.cr_y);
  // eccentricity vector CR → CM
  svg += `<line x1="${crx}" y1="${cry}" x2="${cmx}" y2="${cmy}"
    stroke="var(--amber)" stroke-width="1.6" stroke-dasharray="5 3"/>`;
  // CR — target cross
  svg += `<g stroke="var(--green)" stroke-width="1.8" fill="none">
    <circle cx="${crx}" cy="${cry}" r="6.5"/>
    <line x1="${crx - 9}" y1="${cry}" x2="${crx + 9}" y2="${cry}"/>
    <line x1="${crx}" y1="${cry - 9}" x2="${crx}" y2="${cry + 9}"/></g>`;
  // CM — filled circle
  svg += `<circle cx="${cmx}" cy="${cmy}" r="5.5" fill="var(--series-y)" stroke="#0d1117" stroke-width="1"/>`;
  // legend text
  svg += `<text x="${cmx + 8}" y="${cmy + 3}" fill="var(--series-y)" font-size="10" font-weight="700" font-family="inherit">CM</text>`;
  svg += `<text x="${crx + 9}" y="${cry - 8}" fill="var(--green)" font-size="10" font-weight="700" font-family="inherit">CR</text>`;
  svg += `</svg>`;
  return svg;
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

  /* v0.6 — nonlinear (plastic-hinge) run: yielded list + hinge-rotation table */
  const isNL = !!tc.nonlinear && (td.hinge_rotations || td.yielded);
  const nlBlock = $("thNlBlock");
  nlBlock.classList.toggle("hidden", !isNL);
  if (isNL) {
    const yielded = td.yielded || [];
    viewer.setHighlight(yielded, "#e0a020");        // amber yielded in 3D
    const memBy = {};
    for (const mm of (r.members || [])) memBy[mm.uid] = mm;
    const yl = $("thYieldedList");
    yl.innerHTML = yielded.length
      ? yielded.map(uid => `<button class="yield-chip" data-uid="${esc(uid)}" title="Show ${esc(uid)} in 3D">${esc(uid)}</button>`).join("")
      : `<span class="muted">No hinges reached yield in this record.</span>`;
    yl.querySelectorAll(".yield-chip").forEach(b =>
      b.addEventListener("click", () => selectMemberFrom3D(b.dataset.uid)));
    const yset = new Set(yielded);
    const hrows = Object.entries(td.hinge_rotations || {}).sort((a, b) => b[1] - a[1]);
    const hhead = `<thead><tr><th class="txt">Member</th><th class="txt">Kind</th>
      <th class="txt">Story</th><th>peak θ mrad</th><th class="txt">State</th></tr></thead>`;
    const hbody = hrows.map(([uid, rot]) => {
      const mm = memBy[uid] || {};
      const y = yset.has(uid);
      return `<tr class="${y ? "over" : ""}">
        <td class="txt">${esc(uid)}</td>
        <td class="txt dim">${esc(mm.kind || "—")}</td>
        <td class="txt dim">${esc(mm.story || "—")}</td>
        <td>${fmt(rot * 1000, 2)}</td>
        <td class="txt">${y ? `<span class="status-chip st-ng">yielded</span>` : `<span class="status-chip st-ok">elastic</span>`}</td></tr>`;
    }).join("");
    $("thHingeTable").innerHTML = hhead +
      `<tbody>${hbody || `<tr><td class="txt dim">No hinge rotations reported</td></tr>`}</tbody>`;
    $("thYieldNote").textContent =
      `${yielded.length} of ${hrows.length} hinges yielded · amber in 3D · ${store.thCase}`;
  } else {
    // clear a stale amber highlight when the active case is linear
    if (viewer.highlight.uids) viewer.setHighlight(null);
  }
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
  const perf = store.perf[store.poCase];
  box.appendChild(pushoverChart(pd.roof_disp || [], pd.base_shear || [], {
    title: `Capacity curve — ${store.poCase}`, H,
    // v0.19: ASCE 41 overlays when the performance point has been computed
    bilinear: perf ? { dy: perf.dy, Vy: perf.Vy, du: perf.du, Vu: perf.Vu }
                   : null,
    marker: perf ? { x: perf.delta_t,
                     label: `δt ${fmt(perf.delta_t * 1000, 0)} mm` } : null,
  }));
  renderPerfOut();

  /* hinge rotations, sorted descending. v0.19: asce41 pushovers carry a
     per-hinge state history — show the FINAL acceptance state per member
     (worst of the two ends) as a colored chip. */
  const memBy = {};
  for (const mm of (store.results.members || [])) memBy[mm.uid] = mm;
  const ORDER = ["elastic", "IO", "LS", "CP", "collapse"];
  const finalState = {};
  const myOf = {};
  for (const h of (pd.hinges || [])) {
    const st = h.state && h.state.length ? h.state[h.state.length - 1] : "elastic";
    const cur = finalState[h.uid];
    if (cur === undefined || ORDER.indexOf(st) > ORDER.indexOf(cur))
      finalState[h.uid] = st;
    myOf[h.uid] = h.My;
  }
  const hasStates = !!(pd.hinges || []).length;
  const rows = Object.entries(pd.hinge_rotations || {}).sort((a, b) => b[1] - a[1]);
  const head = `<thead><tr>
    <th class="txt">Member</th><th class="txt">Kind</th><th class="txt">Story</th>
    <th>θ mrad</th><th>My kN·m</th>${hasStates ? `<th class="txt">State</th>` : ""}</tr></thead>`;
  const body = rows.map(([uid, rot]) => {
    const mm = memBy[uid] || {};
    const my = (pc.My && pc.My[uid] != null) ? fmt(pc.My[uid], 0)
      : myOf[uid] != null ? fmt(myOf[uid], 0)
      : (pc.default_My != null ? `${fmt(pc.default_My, 0)} (default)` : "—");
    const st = finalState[uid];
    const stCell = hasStates
      ? `<td class="txt">${st ? `<span class="hstate hstate-${st}">${st}</span>` : `<span class="dim">—</span>`}</td>`
      : "";
    return `<tr>
      <td class="txt">${esc(uid)}</td>
      <td class="txt dim">${esc(mm.kind || "—")}</td>
      <td class="txt dim">${esc(mm.story || "—")}</td>
      <td>${fmt(rot * 1000, 2)}</td>
      <td class="dim">${my}</td>${stCell}</tr>`;
  }).join("");
  $("poHingeTable").innerHTML = head +
    `<tbody>${body || `<tr><td class="txt dim">No hinge rotations reported</td></tr>`}</tbody>`;
  $("poHingeNote").textContent =
    `${rows.length} hinges · ${store.poCase} — plastic rotations at target drift, sorted descending` +
    (hasStates ? " · ASCE 41 acceptance states at the final step" : "");
}

/* v0.19 — ASCE 41 performance point (POST /api/results/performance-point) */
async function runPerformancePoint() {
  const btn = $("perfRunBtn");
  if (!btn || btn.disabled || !store.poCase) return;
  const p = store.perfParams;
  p.SDS = parseFloat($("perfSds").value) || p.SDS;
  p.SD1 = parseFloat($("perfSd1").value) || p.SD1;
  p.site = $("perfSite").value || p.site;
  btn.disabled = true;
  $("perfSpinner").classList.remove("hidden");
  const body = { case: store.poCase, SDS: p.SDS, SD1: p.SD1,
                 site_class: p.site };
  try {
    let out;
    if (!store.mock) {
      try {
        out = await api("/api/results/performance-point", body);
      } catch (e) {
        console.warn("performance-point endpoint unavailable, mock:", e.message);
      }
    }
    if (!out) out = mockPerformancePoint(store.model, poData(), body);
    store.perf[store.poCase] = out;
    renderPoTab();                       // redraw curve with overlays
  } catch (err) {
    toast("Performance point failed", err.message, "error", 8000);
  } finally {
    btn.disabled = false;
    $("perfSpinner").classList.add("hidden");
  }
}

function renderPerfOut() {
  const out = $("perfOut"), chips = $("perfChips");
  if (!out) return;
  const perf = store.perf[store.poCase];
  out.classList.toggle("hidden", !perf);
  chips.classList.toggle("hidden", !perf || !perf.hinge_summary);
  if (!perf) return;
  const f = (v, d = 3) => fmt(v, d);
  out.innerHTML =
    `<span>T<sub>e</sub> <b>${f(perf.Te)} s</b></span>
     <span>K<sub>e</sub> <b>${f(perf.Ke, 0)} kN/m</b></span>
     <span>V<sub>y</sub> <b>${f(perf.Vy, 0)} kN</b></span>
     <span>S<sub>a</sub> <b>${f(perf.Sa)} g</b></span>
     <span>μ <b>${f(perf.mu, 2)}</b></span>
     <span>C<sub>0</sub> <b>${f(perf.C0, 2)}</b></span>
     <span>C<sub>1</sub> <b>${f(perf.C1, 3)}</b></span>
     <span>C<sub>2</sub> <b>${f(perf.C2, 3)}</b></span>
     <span class="perf-dt">δ<sub>t</sub> <b>${f(perf.delta_t * 1000, 1)} mm</b>
       <span class="dim">@ step ${perf.step ?? "—"}</span></span>
     ${perf.elastic ? `<span class="hstate hstate-elastic" title="The capacity curve never yielded within the pushover — the idealization degenerates to the elastic line (Vy = Vu, conservative in C1/C2)">elastic response</span>` : ""}`;
  const hs = perf.hinge_summary || {};
  chips.innerHTML = ["elastic", "IO", "LS", "CP", "collapse"]
    .map(s => `<span class="hstate hstate-${s}">${s} ${hs[s] || 0}</span>`)
    .join("") + `<span class="muted" style="font-size:11px;margin-left:6px">hinge acceptance states at δt</span>`;
}

/* ================================================================
   v0.10 — BUCKLING TAB
   Critical load factors λ per mode; clicking a mode animates the buckling
   mode shape in the 3D view (reusing the modal mode-shape animation).
   ================================================================ */
function buckData() {
  const bk = store.results && store.results.buckling;
  if (!bk || !Object.keys(bk).length) return null;
  if (!store.buckCase || !bk[store.buckCase]) store.buckCase = Object.keys(bk)[0];
  return bk[store.buckCase];
}

function rebuildBuckSelect() {
  const bk = (store.results && store.results.buckling) || {};
  const names = Object.keys(bk);
  const sel = $("buckCaseSelect");
  sel.textContent = "";
  for (const n of names) {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  }
  if (!store.buckCase || !names.includes(store.buckCase)) store.buckCase = names[0] || null;
  if (store.buckCase) sel.value = store.buckCase;
}

function renderBucklingTab() {
  const bd = buckData();
  if (!bd) return;
  const factors = bd.factors || [];
  const gravStr = Object.entries(bd.gravity || {})
    .map(([p, f]) => `${fmt(f, 2)}×${p}`).join(" + ") || "—";
  $("buckMeta").textContent =
    `gravity ${gravStr} · ${factors.length} mode${factors.length === 1 ? "" : "s"}` +
    (factors.length ? ` · λ₁ = ${fmt(factors[0], 3)}` : "");

  // warnings (e.g. λ < 1)
  const wbox = $("buckWarnings");
  wbox.textContent = "";
  const warns = bd.warnings || [];
  const belowOne = factors.some(l => l < 1);
  wbox.classList.toggle("hidden", !(warns.length || belowOne));
  for (const w of warns) {
    const div = document.createElement("div");
    div.className = "po-warn-item";
    div.textContent = `⚠ ${w}`;
    wbox.appendChild(div);
  }

  const active = store.overlay.buckling && store.overlay.bucklingCase === store.buckCase;
  const head = `<thead><tr>
    <th class="txt">Mode</th><th>λ (critical factor)</th><th class="txt">scaled gravity</th>
    <th class="txt"></th></tr></thead>`;
  const body = factors.map((lam, i) => {
    const cls = lam < 1 ? "buck-unsafe" : "";
    const on = active && store.overlay.modeIndex === i;
    return `<tr class="${cls}">
      <td class="txt">${i + 1}</td>
      <td class="${cls}">${fmt(lam, 3)}</td>
      <td class="txt dim">λ × gravity = ${fmt(lam, 2)} × gravity</td>
      <td class="txt"><button class="link-3d${on ? " is-on" : ""}" data-mode="${i}">animate mode →</button></td>
    </tr>`;
  }).join("");
  $("buckTable").innerHTML = head +
    `<tbody>${body || `<tr><td class="txt dim">No buckling factors reported</td></tr>`}</tbody>`;
  $("buckTable").querySelectorAll(".link-3d").forEach(btn =>
    btn.addEventListener("click", () =>
      viewBucklingModeIn3D(store.buckCase, parseInt(btn.dataset.mode, 10))));

  $("buckNote").textContent =
    "Critical load factor λ scales the applied gravity state to the buckling load. " +
    "λ < 1 means the structure buckles below the applied gravity.";
}

function viewBucklingModeIn3D(caseName, idx) {
  store.overlay.buckling = true;
  store.overlay.modal = false;
  store.overlay.deformed = false;
  store.overlay.bucklingCase = caseName;
  store.overlay.modeIndex = idx;
  rebuildBuckModeSelect();
  $("buckModeSelect").value = String(idx);
  syncOverlayUI();
  switchTab("view3d");
}

function rebuildBuckModeSelect() {
  const sel = $("buckModeSelect");
  sel.textContent = "";
  const bd = buckData();
  if (!bd) return;
  (bd.factors || []).forEach((lam, i) => {
    const o = document.createElement("option");
    o.value = String(i);
    o.textContent = `Mode ${i + 1} — λ ${fmt(lam, 3)}`;
    sel.appendChild(o);
  });
  if (store.overlay.buckling) sel.value = String(store.overlay.modeIndex);
}

/* ================================================================
   v0.11 — LOAD TAKEDOWN TAB
   Per gravity case/combo: where the gravity load lands at each support
   (grid label, x, y, FZ/FX/FY), a grand-total FZ with an applied-vs-total
   balance chip, and a bubble plan diagram sized/coloured by FZ. CSV export.
   ================================================================ */
function tdCaseNames() {
  const td = store.results && store.results.takedown;
  return td ? Object.keys(td) : [];
}
function tdData() {
  const td = store.results && store.results.takedown;
  if (!td || !Object.keys(td).length) return null;
  if (!store.tdCase || !td[store.tdCase]) store.tdCase = Object.keys(td)[0];
  return td[store.tdCase];
}
function rebuildTdSelect() {
  const names = tdCaseNames();
  const sel = $("tdCaseSelect");
  if (!sel) return;
  sel.textContent = "";
  for (const n of names) {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  }
  if (!store.tdCase || !names.includes(store.tdCase)) store.tdCase = names[0] || null;
  if (store.tdCase) sel.value = store.tdCase;
}

/** Support rows sorted by grid label (A-1, A-2 … natural order), grid-less
    supports last. */
function tdRows(td) {
  return (td.supports || []).slice().sort((a, b) => {
    // grid-labelled supports first (natural order), grid-less mesh nodes last
    if (!!a.grid !== !!b.grid) return a.grid ? -1 : 1;
    return (a.grid || "").localeCompare(b.grid || "", undefined, { numeric: true, sensitivity: "base" })
      || (a.y - b.y) || (a.x - b.x);
  });
}

function renderTakedownTab() {
  const td = tdData();
  if (!td) return;
  rebuildTdSelect();
  const rows = tdRows(td);

  $("tdMeta").textContent =
    `${rows.length} support${rows.length === 1 ? "" : "s"} · ${store.tdCase} · gravity landing (FZ ↓)`;

  // balance chip: green when the support ΣFZ matches the applied gravity
  const chip = $("tdBalance");
  const total = td.total_FZ || 0, applied = td.applied_FZ || 0;
  const ok = !!td.balance_ok;
  chip.className = `balance-chip ${ok ? "is-ok" : "is-bad"}`;
  chip.textContent = ok
    ? `● balanced · ΣFZ ${fmt(total, 1)} = applied ${fmt(applied, 1)} kN`
    : `▲ unbalanced · ΣFZ ${fmt(total, 1)} vs applied ${fmt(applied, 1)} kN (Δ ${fmt(total - applied, 1)})`;

  // table
  const head = `<thead><tr>
    <th class="txt">Grid</th><th class="txt">Node</th><th>X m</th><th>Y m</th>
    <th>FZ kN</th><th>FX kN</th><th>FY kN</th></tr></thead>`;
  const maxFz = Math.max(1e-9, ...rows.map(s => Math.abs(s.FZ || 0)));
  const body = rows.map(s => {
    const frac = Math.abs(s.FZ || 0) / maxFz;
    return `<tr>
      <td class="txt td-grid">${esc(s.grid || "—")}</td>
      <td class="txt dim">${esc(s.node)}</td>
      <td class="dim">${fmt(s.x, 2)}</td><td class="dim">${fmt(s.y, 2)}</td>
      <td class="td-fz"><span class="td-bar" style="--f:${(frac * 100).toFixed(1)}%"></span>${fmt(s.FZ, 1)}</td>
      <td>${fmt(s.FX, 1)}</td><td>${fmt(s.FY, 1)}</td></tr>`;
  }).join("");
  const totals = `<tr class="totals">
    <td class="txt">Σ total</td><td></td><td></td><td></td>
    <td>${fmt(total, 1)}</td>
    <td>${fmt(rows.reduce((a, s) => a + (s.FX || 0), 0), 1)}</td>
    <td>${fmt(rows.reduce((a, s) => a + (s.FY || 0), 0), 1)}</td></tr>`;
  $("tdTable").innerHTML = head + `<tbody>${body}${totals}</tbody>`;

  $("tdBubble").innerHTML = takedownBubbleSvg(rows);
  $("tdNote").textContent =
    "Gravity load takedown — where vertical load reaches the foundation. " +
    "Bubble size & colour scale with FZ; the balance chip checks support ΣFZ against the applied gravity.";
}

/** Bubble plan: support points sized & coloured by |FZ|, over the model grid. */
function takedownBubbleSvg(rows) {
  const g = store.model.grid;
  if (!g) return "";
  const x0 = g.x_lines[0], x1 = g.x_lines[g.x_lines.length - 1];
  const y0 = g.y_lines[0], y1 = g.y_lines[g.y_lines.length - 1];
  const W = 460, H = 300, P = 34;
  const spanX = Math.max(x1 - x0, 1), spanY = Math.max(y1 - y0, 1);
  const sc = Math.min((W - 2 * P) / spanX, (H - 2 * P) / spanY);
  const px = x => P + (x - x0) * sc;
  const py = y => H - P - (y - y0) * sc;                 // y up
  const flt = "rgba(120,140,165,0.20)", mline = "rgba(120,140,165,0.45)";
  const maxFz = Math.max(1e-9, ...rows.map(s => Math.abs(s.FZ || 0)));
  let svg = `<svg viewBox="0 0 ${W} ${H}" class="td-svg" aria-label="Load takedown plan — supports sized by FZ">`;
  svg += `<rect x="${px(x0)}" y="${py(y1)}" width="${(x1 - x0) * sc}" height="${(y1 - y0) * sc}"
    fill="rgba(53,181,229,0.03)" stroke="${mline}" stroke-width="1.1"/>`;
  for (let i = 0; i < g.x_lines.length; i++) {
    const x = g.x_lines[i];
    svg += `<line x1="${px(x)}" y1="${py(y0)}" x2="${px(x)}" y2="${py(y1)}" stroke="${flt}" stroke-width="1"/>`;
    svg += `<text x="${px(x)}" y="${py(y1) - 6}" fill="var(--text-3)" font-size="9" text-anchor="middle" font-family="inherit">${esc((g.x_labels || [])[i] || "")}</text>`;
  }
  for (let j = 0; j < g.y_lines.length; j++) {
    const y = g.y_lines[j];
    svg += `<line x1="${px(x0)}" y1="${py(y)}" x2="${px(x1)}" y2="${py(y)}" stroke="${flt}" stroke-width="1"/>`;
    svg += `<text x="${px(x0) - 8}" y="${py(y) + 3}" fill="var(--text-3)" font-size="9" text-anchor="end" font-family="inherit">${esc((g.y_labels || [])[j] || "")}</text>`;
  }
  // bubbles — radius 5..20 px, colour ramps cool→warm with FZ
  const ramp = f => {
    // f in 0..1 → blue (low) to amber/red (high)
    const stops = [[53, 181, 229], [52, 195, 132], [229, 165, 10], [230, 103, 103]];
    const t = Math.max(0, Math.min(1, f)) * (stops.length - 1);
    const i = Math.min(stops.length - 2, Math.floor(t)), k = t - i;
    const c = stops[i].map((v, n) => Math.round(v + (stops[i + 1][n] - v) * k));
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  };
  for (const s of rows) {
    const f = Math.abs(s.FZ || 0) / maxFz;
    const r = 5 + f * 15;
    const col = ramp(f);
    const cx = px(s.x), cy = py(s.y);
    svg += `<circle cx="${cx}" cy="${cy}" r="${r.toFixed(1)}" fill="${col}" fill-opacity="0.30" stroke="${col}" stroke-width="1.6"/>`;
    // label grid supports (grid-less mesh nodes stay uncluttered — bubble only)
    if (s.grid) {
      svg += `<text x="${cx}" y="${cy - r - 3}" fill="var(--text-2)" font-size="9" font-weight="700" text-anchor="middle" font-family="inherit">${esc(s.grid)}</text>`;
      svg += `<text x="${cx}" y="${cy + 3}" fill="var(--text-1)" font-size="8.5" text-anchor="middle" font-family="inherit">${fmt(s.FZ, 0)}</text>`;
    }
  }
  svg += `</svg>`;
  return svg;
}

/* ================================================================
   v0.13 — SECTION CUT FORCES tab
   Per selected case, a table of cuts with FX/FY/FZ/MX/MY/MZ resultants,
   n_members/n_shells, and warnings. CSV export. Selecting a cut highlights
   the members crossing it in 3D.
   ================================================================ */
function cutCaseNames() {
  const sc = store.results && store.results.section_cuts;
  return sc ? Object.keys(sc) : [];
}
function cutData() {
  const sc = store.results && store.results.section_cuts;
  if (!sc || !Object.keys(sc).length) return null;
  if (!store.cutCase || !sc[store.cutCase]) store.cutCase = Object.keys(sc)[0];
  return sc[store.cutCase];
}
function rebuildCutSelect() {
  const names = cutCaseNames();
  const sel = $("cutCaseSelect");
  if (!sel) return;
  sel.textContent = "";
  for (const n of names) {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  }
  if (!store.cutCase || !names.includes(store.cutCase)) store.cutCase = names[0] || null;
  if (store.cutCase) sel.value = store.cutCase;
}

/** Rows for the section-cut table (one per defined cut), in model order. */
function cutRows(cd) {
  const defs = (store.model.section_cuts || []).map(c => c.name);
  const names = defs.length ? defs.filter(n => cd[n]) : Object.keys(cd);
  return names.map(name => ({ name, ...cd[name], def: (store.model.section_cuts || []).find(c => c.name === name) }));
}

/** Member uids that cross a given cut (for 3D highlight). */
function membersCrossingCut(cutDef) {
  if (!cutDef) return [];
  const axisIdx = { x: 0, y: 1, z: 2 }[cutDef.axis] ?? 2;
  const inRange = p => {
    for (const [k, idx] of [["x_range", 0], ["y_range", 1], ["z_range", 2]]) {
      if (idx === axisIdx) continue;
      const rg = cutDef[k];
      if (rg && !(p[idx] >= rg[0] - 1e-6 && p[idx] <= rg[1] + 1e-6)) return false;
    }
    return true;
  };
  const out = [];
  for (const mm of store.model.members) {
    const a = mm.pi[axisIdx], b = mm.pj[axisIdx];
    const lo = Math.min(a, b), hi = Math.max(a, b);
    if (!(cutDef.coord > lo + 1e-6 && cutDef.coord < hi - 1e-6)) continue;
    const t = (cutDef.coord - a) / (b - a || 1);
    const cp = [0, 1, 2].map(i => mm.pi[i] + (mm.pj[i] - mm.pi[i]) * t);
    if (inRange(cp)) out.push(mm.uid);
  }
  return out;
}

function renderCutsTab() {
  const cd = cutData();
  if (!cd) return;
  rebuildCutSelect();
  const rows = cutRows(cd);

  $("cutMeta").textContent =
    `${rows.length} cut${rows.length === 1 ? "" : "s"} · ${store.cutCase} · resultants (kN, kN·m)`;

  const head = `<thead><tr>
    <th class="txt">Cut</th><th class="txt">Plane</th>
    <th>FX kN</th><th>FY kN</th><th>FZ kN</th>
    <th>MX kN·m</th><th>MY kN·m</th><th>MZ kN·m</th>
    <th>n·mem</th><th>n·shell</th><th class="txt">Warnings</th></tr></thead>`;
  const body = rows.map(row => {
    const d = row.def || {};
    const plane = d.axis ? `${d.axis.toUpperCase()}=${fmt(d.coord, 2)}` : "—";
    const warn = (row.warnings || []).length
      ? `<span class="cut-warn">⚠ ${esc((row.warnings || []).join(" · "))}</span>` : "";
    const sel = row.name === store.cutSel ? " is-sel" : "";
    return `<tr class="cut-row${sel}" data-cut="${esc(row.name)}" title="Click to highlight crossing members in 3D">
      <td class="txt cut-name">✂ ${esc(row.name)}</td>
      <td class="txt dim">${plane}</td>
      <td>${fmt(row.FX, 1)}</td><td>${fmt(row.FY, 1)}</td>
      <td class="cut-fz">${fmt(row.FZ, 1)}</td>
      <td>${fmt(row.MX, 1)}</td><td>${fmt(row.MY, 1)}</td><td>${fmt(row.MZ, 1)}</td>
      <td class="dim">${row.n_members ?? 0}</td><td class="dim">${row.n_shells ?? 0}</td>
      <td class="txt">${warn}</td></tr>`;
  }).join("");
  $("cutTable").innerHTML = head + `<tbody>${body}</tbody>`;

  // row → highlight members crossing that cut in the 3D view
  $("cutTable").querySelectorAll("tr.cut-row").forEach(tr => {
    tr.addEventListener("click", () => {
      const name = tr.dataset.cut;
      const def = (store.model.section_cuts || []).find(c => c.name === name);
      store.cutSel = store.cutSel === name ? null : name;
      const uids = store.cutSel ? membersCrossingCut(def) : [];
      if (viewer) viewer.setHighlight(uids, "#f5be3c");
      switchTab("view3d");
      renderCutsTab();
    });
  });

  $("cutNote").textContent =
    "Force resultant transmitted across each cutting plane (Σ of the internal " +
    "forces of members crossing it). Click a row to highlight the crossing members in 3D.";
}

/* ================================================================
   v0.15 — WALL PIERS tab
   results["piers"][case][pier][story] = {P, V, M} — in-plane pier design
   forces. Table grouped by pier (sortable within groups), a per-pier V-over-
   height sparkline, CSV export, report inclusion.
   ================================================================ */
function pierCaseNames() {
  const pr = store.results && store.results.piers;
  return pr ? Object.keys(pr) : [];
}
function pierData() {
  const pr = store.results && store.results.piers;
  if (!pr || !Object.keys(pr).length) return null;
  if (!store.pierCase || !pr[store.pierCase]) store.pierCase = Object.keys(pr)[0];
  return pr[store.pierCase];
}
function rebuildPierSelect() {
  const names = pierCaseNames();
  const sel = $("pierCaseSelect");
  if (!sel) return;
  sel.textContent = "";
  for (const n of names) {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  }
  if (!store.pierCase || !names.includes(store.pierCase)) store.pierCase = names[0] || null;
  if (store.pierCase) sel.value = store.pierCase;
}

/** Flat rows grouped by pier: [{pier, stories: [{story, elev, P, V, M}]}].
    Pier groups sort by name; stories inside a group follow store.pierSort
    (default: elevation descending — top story first, like every story table). */
function pierRows(pd) {
  const r = store.results;
  const { key, dir } = store.pierSort;
  const groups = Object.keys(pd)
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
    .map(pier => {
      const stories = Object.entries(pd[pier] || {}).map(([story, f]) => ({
        story, elev: (r.story_elev || {})[story] ?? 0,
        P: f.P || 0, V: f.V || 0, M: f.M || 0,
      }));
      stories.sort((a, b) => {
        const va = key === "story" ? a.story : a[key];
        const vb = key === "story" ? b.story : b[key];
        if (typeof va === "string") return va.localeCompare(vb, undefined, { numeric: true }) * dir;
        return (va - vb) * dir;
      });
      return { pier, stories };
    });
  return groups;
}

/** Mini V-over-height profile for one pier — one horizontal bar per story
    (top story first), lengths ∝ |V|. Stays legible when V is constant. */
function pierSparkSvg(stories) {
  if (stories.length < 2) return "";
  const byElev = [...stories].sort((a, b) => b.elev - a.elev);   // top → bottom
  const P = 3, W = 110;
  const bw = 7;                                                   // bar height
  const H = 2 * P + bw * byElev.length + (byElev.length - 1) * 2;
  const maxV = Math.max(1e-9, ...byElev.map(s => Math.abs(s.V)));
  const bars = byElev.map((s, i) => {
    const w = Math.max((Math.abs(s.V) / maxV) * (W - 2 * P - 2), 1.5);
    const y = P + i * (bw + 2);
    return `<rect x="${P + 1}" y="${y}" width="${w.toFixed(1)}" height="${bw}" rx="1.5"
      fill="rgba(53,181,229,0.32)" stroke="rgba(53,181,229,0.75)" stroke-width="0.8"/>`;
  }).join("");
  return `<svg class="pier-spark" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}"
    aria-label="Pier shear V per story, top story first">
    <line x1="${P}" y1="${P - 1}" x2="${P}" y2="${H - P + 1}" stroke="rgba(120,140,165,0.45)" stroke-width="1"/>
    ${bars}</svg>`;
}

const PIER_COLS = [
  { key: "story", label: "Story", txt: true },
  { key: "elev", label: "Elev m" },
  { key: "P", label: "P kN" },
  { key: "V", label: "V kN" },
  { key: "M", label: "M kN·m" },
];

function renderPiersTab() {
  const pd = pierData();
  if (!pd) return;
  rebuildPierSelect();
  const groups = pierRows(pd);
  const nStories = groups.reduce((a, g) => a + g.stories.length, 0);

  $("pierMeta").textContent =
    `${groups.length} pier${groups.length === 1 ? "" : "s"} · ${nStories} stor${nStories === 1 ? "y" : "ies"} · ${store.pierCase} · in-plane design forces`;

  const { key: sk, dir } = store.pierSort;
  const head = `<thead><tr><th class="txt">Pier</th>` + PIER_COLS.map(c =>
    `<th class="sortable ${c.txt ? "txt" : ""}" data-key="${c.key}">${c.label}` +
    (c.key === sk ? `<span class="sort-arrow">${dir > 0 ? "▲" : "▼"}</span>` : "") +
    `</th>`).join("") + `</tr></thead>`;

  const body = groups.map(g => {
    const spark = pierSparkSvg(g.stories);
    const header = `<tr class="pier-group">
      <td class="txt pier-name" colspan="3">▮ ${esc(g.pier)} <span class="pier-count">· ${g.stories.length} stor${g.stories.length === 1 ? "y" : "ies"}</span></td>
      <td colspan="3" class="pier-spark-cell">${spark ? `<span class="pier-spark-lbl">V over height</span>${spark}` : ""}</td></tr>`;
    const rows = g.stories.map(s => `<tr>
      <td class="txt dim"></td>
      <td class="txt">${esc(s.story)}</td>
      <td class="dim">${fmt(s.elev, 1)}</td>
      <td>${fmt(s.P, 1)}</td>
      <td>${fmt(s.V, 1)}</td>
      <td>${fmt(s.M, 1)}</td></tr>`).join("");
    return header + rows;
  }).join("");
  $("pierTable").innerHTML = head + `<tbody>${body}</tbody>`;

  $("pierTable").querySelectorAll("th.sortable").forEach(th =>
    th.addEventListener("click", () => {
      const k = th.dataset.key;
      if (store.pierSort.key === k) store.pierSort.dir *= -1;
      else store.pierSort = { key: k, dir: -1 };
      renderPiersTab();
    }));

  $("pierNote").textContent =
    "In-plane wall-pier design forces per story — P axial (compression −), " +
    "V in-plane shear, M in-plane moment at the story bottom. Piers group every " +
    "wall sharing a pier label; the sparkline profiles V over the pier height.";
}

/* ================================================================
   v0.16 — SERVICEABILITY tab
   results["deflection_checks"][case] = [{uid, story, L, max_abs_dy,
   ratio_str "L/412", limit "L/360", ok}] — beam deflection checks vs the
   model's serviceability limit. Editable limit writes model.deflection_limit
   (re-analysis refreshes the checks); sortable table, CSV, report inclusion,
   NG rows highlighted, row → 3D member select.
   ================================================================ */
function svcCaseNames() {
  const dc = store.results && store.results.deflection_checks;
  return dc ? Object.keys(dc) : [];
}
function svcData() {
  const dc = store.results && store.results.deflection_checks;
  if (!dc || !Object.keys(dc).length) return null;
  if (!store.svcCase || !dc[store.svcCase]) store.svcCase = Object.keys(dc)[0];
  return dc[store.svcCase];
}
function rebuildSvcSelect() {
  const names = svcCaseNames();
  const sel = $("svcCaseSelect");
  if (!sel) return;
  sel.textContent = "";
  for (const n of names) {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  }
  if (!store.svcCase || !names.includes(store.svcCase)) store.svcCase = names[0] || null;
  if (store.svcCase) sel.value = store.svcCase;
}

/** Sorted check rows for the selected case; ratioVal = the numeric L/δ so the
    worst beam (smallest L/x) sorts first by default. */
function svcRows(list) {
  const rows = (list || []).map(c => ({
    ...c,
    ratioVal: (c.max_abs_dy || 0) > 1e-12 ? (c.L || 0) / c.max_abs_dy : Infinity,
    dyMm: (c.max_abs_dy || 0) * 1000,
  }));
  const { key, dir } = store.svcSort;
  rows.sort((a, b) => {
    const va = a[key], vb = b[key];
    if (typeof va === "string") return va.localeCompare(vb, undefined, { numeric: true }) * dir;
    return ((va === Infinity ? 1e12 : va) - (vb === Infinity ? 1e12 : vb)) * dir;
  });
  return rows;
}

const SVC_COLS = [
  { key: "uid", label: "Beam", txt: true },
  { key: "story", label: "Story", txt: true },
  { key: "L", label: "L m" },
  { key: "dyMm", label: "max |δ| mm" },
  { key: "ratioVal", label: "Ratio" },
  { key: "limit", label: "Limit", txt: true },
  { key: "ok", label: "Status", txt: true },
];

function renderSvcTab() {
  const list = svcData();
  if (!list) return;
  rebuildSvcSelect();
  const rows = svcRows(list);
  const ng = rows.filter(r => !r.ok).length;

  $("svcMeta").innerHTML =
    `${rows.length} beam${rows.length === 1 ? "" : "s"} · ${esc(store.svcCase)} · ` +
    (ng ? `<b class="svc-ng-count">${ng} NG</b>` : `all OK`);
  const limInput = $("svcLimitInput");
  if (limInput && document.activeElement !== limInput)
    limInput.value = String(store.model.deflection_limit ?? 360);

  const { key: sk, dir } = store.svcSort;
  const head = `<thead><tr>` + SVC_COLS.map(c =>
    `<th class="sortable ${c.txt ? "txt" : ""}" data-key="${c.key}">${c.label}` +
    (c.key === sk ? `<span class="sort-arrow">${dir > 0 ? "▲" : "▼"}</span>` : "") +
    `</th>`).join("") + `</tr></thead>`;
  const body = rows.map(x => `
    <tr data-uid="${esc(x.uid)}" class="svc-row${x.ok ? "" : " over"}" title="Click to show ${esc(x.uid)} in 3D">
      <td class="txt">${esc(x.uid)}</td>
      <td class="txt dim">${esc(x.story || "—")}</td>
      <td class="dim">${fmt(x.L, 2)}</td>
      <td class="${x.ok ? "" : "exceed"}">${fmt(x.dyMm, 2)}</td>
      <td class="${x.ok ? "" : "exceed"}"><b>${esc(x.ratio_str || "—")}</b></td>
      <td class="txt dim">${esc(x.limit || "—")}</td>
      <td class="txt">${x.ok
        ? `<span class="status-chip st-ok">OK</span>`
        : `<span class="status-chip st-ng">NG</span>`}</td>
    </tr>`).join("");
  $("svcTable").innerHTML = head + `<tbody>${body ||
    `<tr><td class="txt dim">No beam deflection checks in this case</td></tr>`}</tbody>`;

  $("svcTable").querySelectorAll("th.sortable").forEach(th =>
    th.addEventListener("click", () => {
      const k = th.dataset.key;
      if (store.svcSort.key === k) store.svcSort.dir *= -1;
      else store.svcSort = { key: k, dir: (k === "uid" || k === "story" || k === "limit") ? 1 : k === "ratioVal" ? 1 : -1 };
      renderSvcTab();
    }));
  // row → highlight + open the member panel in 3D (deflection diagram there)
  $("svcTable").querySelectorAll("tr.svc-row").forEach(tr =>
    tr.addEventListener("click", () => {
      if (svcCaseNames().includes(store.svcCase) && store.caseName !== store.svcCase &&
          caseNames().includes(store.svcCase)) {
        store.caseName = store.svcCase;               // member panel shows this case
        $("caseSelect").value = store.svcCase;
        syncOverlayUI();
        syncContoursUI();
      }
      selectMemberFrom3D(tr.dataset.uid);
    }));

  $("svcNote").textContent =
    "Beam serviceability — max |local transverse deflection| vs the span limit " +
    `L/${store.model.deflection_limit ?? 360}. The achieved ratio L/x must stay above the limit ` +
    "(x ≥ limit ⇒ OK). Editing the limit writes model.deflection_limit; re-run the analysis " +
    "to refresh the checks against the new limit.";
}

/* ================================================================
   v0.6 — DESIGN CHECKS (steel / concrete)
   ================================================================ */
function designResult() {
  return store.designKind === "concrete" ? store.concreteResult : store.steelResult;
}

/** Case options for the design "Check" selectors (cases + combos only). */
function designCaseOptions() {
  const r = store.results;
  if (!r) return [];
  return [
    ...Object.keys(r.cases || {}),
    ...Object.keys(r.combos || {}),
  ];
}

function setDesignKind(kind) {
  store.designKind = ["concrete", "wall", "punching", "composite", "slab"].includes(kind) ? kind : "steel";
  document.querySelectorAll("#designKindToggle .seg-btn").forEach(b =>
    b.classList.toggle("is-active", b.dataset.dk === store.designKind));
  // v0.18 — Wall / Punching sub-tabs swap out the whole steel/concrete block
  const frame = store.designKind === "steel" || store.designKind === "concrete";
  $("designFrameWrap").classList.toggle("hidden", !frame);
  $("csvDesign").classList.toggle("hidden", !frame);
  if (frame) {
    renderDesignForm();
    renderDesignTable();
  }
  renderOptimizePanel();          // v0.12 — Steel sub-tab only
  renderWallPanel();              // v0.18
  renderPunchPanel();             // v0.18 (also refreshes plan halos)
  renderCompositePanel();         // v0.20
  renderSlabPanel();              // v0.20
}

/** The check control form: case selector, params (Fy or rebar), Check button. */
function renderDesignForm() {
  const form = $("designForm");
  if (!store.results) { form.innerHTML = ""; return; }
  // v0.18 — the wall/punching sub-tabs render their own forms
  if (store.designKind !== "steel" && store.designKind !== "concrete") return;
  const opts = designCaseOptions();
  if (!store.designCase || !opts.includes(store.designCase)) store.designCase = opts[0] || null;
  const nCombos = Object.keys((store.results && store.results.combos) || {}).length;
  const allOn = store.designAllCombos && nCombos > 0;
  const caseSel = `<label class="rs-field"><span>case / combo</span>
    <select id="designCaseSelect"${allOn ? " disabled" : ""}>${opts.map(n =>
      `<option value="${esc(n)}"${n === store.designCase ? " selected" : ""}>${esc(n)}</option>`).join("")}</select></label>` +
    `<label class="rs-field design-envelope" title="Check every load combination and report the governing one per member">
      <span>envelope</span>
      <label class="allcombos-check"><input type="checkbox" id="designAllCombos"${allOn ? " checked" : ""}${nCombos ? "" : " disabled"}>
        All combinations${nCombos ? ` <span class="unit">${nCombos}</span>` : ""}</label></label>`;

  if (store.designKind === "steel") {
    form.innerHTML = `<div class="design-form-row">
      ${caseSel}
      <label class="rs-field"><span>Fy <span class="unit">kPa</span></span>
        <input id="designFy" type="number" min="1" step="5000" value="${store.steelFy}"></label>
      <button class="btn btn-run design-check" id="designCheckBtn">
        <span class="spinner hidden" id="designSpinner"></span><span>Check steel</span></button>
    </div>
    <p class="muted design-note">AISC-H1 axial-flexure interaction screening — φPn, φMn from section properties.</p>`;
    $("designFy").addEventListener("change", e => {
      const v = parseFloat(e.target.value);
      if (isFinite(v) && v > 0) store.steelFy = v;
    });
  } else {
    const d = store.rebarDefault;
    const numField = (key, label, unit, step) =>
      `<label class="rs-field rebar-field"><span>${label}${unit ? ` <span class="unit">${unit}</span>` : ""}</span>
        <input data-rb="${key}" type="number" step="${step}" min="0" value="${d[key]}"></label>`;
    form.innerHTML = `<div class="design-form-row">
      ${caseSel}
      <label class="rs-field"><span>f'c <span class="unit">kPa</span></span>
        <input id="designFc" type="number" min="1" step="5000" value="${store.concreteFc}"></label>
    </div>
    <div class="rebar-panel">
      <div class="rebar-panel-head"><b>Default rebar</b>
        <span class="muted">applied to all beams &amp; columns for this screening</span></div>
      <div class="rebar-grid">
        ${numField("n_top", "n top", "", "1")}
        ${numField("n_bot", "n bot", "", "1")}
        ${numField("bar_dia", "bar ⌀", "mm", "2")}
        ${numField("cover", "cover", "mm", "5")}
        ${numField("fy", "fy", "kPa", "5000")}
        ${numField("stirrup_dia", "stirrup ⌀", "mm", "2")}
        ${numField("stirrup_spacing", "stirrup s", "mm", "25")}
        ${numField("stirrup_legs", "legs", "", "1")}
      </div>
    </div>
    <div class="design-form-row">
      <button class="btn btn-run design-check" id="designCheckBtn">
        <span class="spinner hidden" id="designSpinner"></span><span>Check concrete</span></button>
      <p class="muted design-note">P-M / flexure screening from the rebar layout — not a full column-design check.</p>
    </div>`;
    form.querySelectorAll("input[data-rb]").forEach(inp =>
      inp.addEventListener("change", () => {
        const v = parseFloat(inp.value);
        if (isFinite(v) && v >= 0) store.rebarDefault[inp.dataset.rb] = v;
      }));
    $("designFc").addEventListener("change", e => {
      const v = parseFloat(e.target.value);
      if (isFinite(v) && v > 0) store.concreteFc = v;
    });
  }
  $("designCaseSelect").addEventListener("change", e => { store.designCase = e.target.value; });
  const cb = $("designAllCombos");
  if (cb) cb.addEventListener("change", e => {
    store.designAllCombos = e.target.checked;
    renderDesignForm();           // enable/disable the case selector
  });
  $("designCheckBtn").addEventListener("click", runDesignCheck);
}

/* ================================================================
   v0.16 — LIVE-LOAD REDUCTION panel (ASCE 7 §4.7, Design tab)
   Toggle + per-column factor table from GET /api/live-reduction (mock
   computes R = 0.25 + 4.57/√(K_LL·A_T), clamped). When ON, design check
   requests carry {live_reduction:true, live_case:"LIVE"} and the summary
   strip notes "LL reduction applied". CSV of the factors.
   ================================================================ */
function renderLlrPanel() {
  const panel = $("llrPanel");
  if (!panel) return;
  panel.classList.toggle("hidden", !store.results);
  if (!store.results) return;
  $("llrToggle").checked = store.llReduction;
  $("llrAppliedChip").classList.toggle("hidden", !store.llReduction);
  const open = store.llReduction && !!store.llrData;
  $("llrBody").classList.toggle("hidden", !open);
  $("csvLlr").classList.toggle("hidden", !open);
  if (open) renderLlrTable();
}

function llrRows() {
  const data = store.llrData || {};
  const memBy = {};
  for (const mm of store.model.members || []) memBy[mm.uid] = mm;
  return Object.entries(data)
    .map(([uid, f]) => ({ uid, story: (memBy[uid] || {}).story || "—",
      KLL: f.KLL ?? 4, At: f.At ?? 0, R: f.R ?? 1 }))
    .sort((a, b) => a.uid.localeCompare(b.uid, undefined, { numeric: true }));
}

function renderLlrTable() {
  const rows = llrRows();
  const rMin = rows.length ? Math.min(...rows.map(r => r.R)) : 1;
  $("llrNote").innerHTML =
    `${rows.length} column${rows.length === 1 ? "" : "s"} · K<sub>LL</sub>·A<sub>T</sub> from the ` +
    `tributary plan areas · strongest reduction R = <b>${fmt(rMin, 3)}</b>. ` +
    `With the toggle on, design checks send <code>{live_reduction:true, live_case:"LIVE"}</code> ` +
    `— column live axial demand is multiplied by R.`;
  const head = `<thead><tr>
    <th class="txt">Column</th><th class="txt">Story</th>
    <th title="Live-load element factor (Table 4.7-1)">K<sub>LL</sub></th>
    <th title="Tributary area">A<sub>T</sub> m²</th>
    <th title="Reduction multiplier on L">R</th><th class="txt"></th></tr></thead>`;
  const body = rows.map(x => {
    const pct = Math.round((1 - x.R) * 100);
    return `<tr>
      <td class="txt">${esc(x.uid)}</td>
      <td class="txt dim">${esc(x.story)}</td>
      <td class="dim">${fmt(x.KLL, 0)}</td>
      <td>${fmt(x.At, 1)}</td>
      <td><b>${fmt(x.R, 3)}</b></td>
      <td class="txt"><span class="llr-bar-wrap"><span class="llr-bar" style="--r:${(x.R * 100).toFixed(1)}%"></span></span>
        <span class="dim llr-pct">${pct ? `−${pct} %` : "—"}</span></td>
    </tr>`;
  }).join("");
  $("llrTable").innerHTML = head + `<tbody>${body ||
    `<tr><td class="txt dim">No columns in the model</td></tr>`}</tbody>`;
}

async function toggleLlr(on) {
  store.llReduction = !!on;
  if (store.llReduction && !store.llrData) {
    try { await fetchLiveReduction(); }
    catch (e) { toast("Live-reduction fetch failed", e.message, "error", 6000); }
  }
  renderLlrPanel();
}

async function runDesignCheck() {
  const btn = $("designCheckBtn");
  if (btn.disabled) return;
  btn.disabled = true;
  $("designSpinner").classList.remove("hidden");
  // v0.9 — envelope over all combinations vs. a single case
  const nCombos = Object.keys((store.results && store.results.combos) || {}).length;
  const useCombos = store.designAllCombos && nCombos > 0;
  const target = useCombos ? { combos: true } : { case: store.designCase };
  // v0.16 — live-load reduction flags ride on every design request
  if (store.llReduction) Object.assign(target, { live_reduction: true, live_case: "LIVE" });
  try {
    if (store.designKind === "steel") {
      store.steelResult = await designCheck("steel", { ...target, Fy: store.steelFy });
    } else {
      // apply the default rebar to every beam & column
      const rebar = {};
      for (const mm of store.model.members) {
        if (mm.kind === "column" || mm.kind === "beam")
          rebar[mm.uid] = { ...store.rebarDefault };
      }
      store.rebar = rebar;
      store.concreteResult = await designCheck("concrete",
        { ...target, fc: store.concreteFc, rebar });
    }
    // v0.16 — remember whether this run carried the LL-reduction flags
    { const r0 = designResult(); if (r0) r0.ll_reduction = !!store.llReduction; }
    renderDesignTable();
    const res = designResult();
    toast("Design check complete",
      `${store.designKind} · ${res.summary.ok} OK / ${res.summary.ng} NG · max ratio ${fmt(res.summary.max_ratio, 2)}`,
      res.summary.ng ? "error" : "info", 5000);
  } catch (err) {
    toast("Design check failed", err.message, "error", 8000);
  } finally {
    btn.disabled = false;
    $("designSpinner").classList.add("hidden");
  }
}

const DESIGN_COLS = [
  { key: "uid", label: "Member", txt: true },
  { key: "kind", label: "Kind", txt: true },
  { key: "section", label: "Section", txt: true },
  { key: "Pu", label: "Pu kN" },
  { key: "Mu33", label: "M33 kN·m" },
  { key: "Mu22", label: "M22 kN·m" },
  { key: "phiPn", label: "φPn kN" },
  { key: "phiMn33", label: "φMn33" },
  { key: "ratio", label: "Ratio" },
  { key: "equation", label: "Eqn", txt: true },
  { key: "status", label: "Status", txt: true },
];

/** v0.16 — the governing D/C ratio of a check: ratio_biaxial when the check
    is biaxial (Bresler / load-contour columns), else the plain ratio. */
const designGovRatio = c =>
  (c && c.biaxial && isFinite(c.ratio_biaxial)) ? c.ratio_biaxial : ((c && c.ratio) || 0);

function designRows() {
  const res = designResult();
  if (!res) return [];
  const q = store.designFilter.trim().toLowerCase();
  let rows = res.checks;
  if (q) rows = rows.filter(c =>
    c.uid.toLowerCase().includes(q) || (c.kind || "").toLowerCase().includes(q) ||
    (c.status || "").toLowerCase().includes(q) ||
    (c.method || "").toLowerCase().includes(q));
  const { key, dir } = store.designSort;
  rows = [...rows].sort((a, b) => {
    const va = key === "ratio" ? designGovRatio(a) : a[key];
    const vb = key === "ratio" ? designGovRatio(b) : b[key];
    if (typeof va === "string") return String(va).localeCompare(String(vb), undefined, { numeric: true }) * dir;
    return ((va || 0) - (vb || 0)) * dir;
  });
  return rows;
}

function renderDesignTable() {
  const res = designResult();
  const summary = $("designSummary");
  const ctrls = $("designTableControls");
  const table = $("designTable");
  if (!res) {
    summary.classList.add("hidden");
    ctrls.classList.add("hidden");
    $("designFootnote").classList.add("hidden");
    table.innerHTML = `<tbody><tr><td class="txt dim">No ${store.designKind} check yet — set parameters and press “Check”.</td></tr></tbody>`;
    return;
  }
  const s = res.summary;
  const envelope = !!res.envelope;
  summary.classList.remove("hidden");
  summary.innerHTML =
    `<span class="ds-item"><b>${s.n}</b> checked</span>` +
    `<span class="ds-item ds-ok"><b>${s.ok}</b> OK</span>` +
    `<span class="ds-item ds-ng"><b>${s.ng}</b> NG</span>` +
    `<span class="ds-item ds-na"><b>${s.na}</b> N/A</span>` +
    `<span class="ds-item">max ratio <b class="${s.max_ratio > 1 ? "ds-over" : ""}">${fmt(s.max_ratio, 3)}</b></span>` +
    `<span class="ds-item">governing <b>${esc(s.governing || "—")}</b></span>` +
    (envelope
      ? `<span class="ds-item ds-envelope">envelope over <b>${(res.combos || []).length}</b> combos</span>`
      : "") +
    (res.ll_reduction
      ? `<span class="ds-item ds-llr" title="Checks ran with {live_reduction:true, live_case:'LIVE'} — column live axial demand reduced per ASCE 7 §4.7">LL reduction applied</span>`
      : "") +
    `<span class="ds-item ds-prelim">PRELIMINARY · ${esc(res.case)}</span>`;

  ctrls.classList.remove("hidden");
  const rows = designRows();
  // v0.16 — concrete checks carrying a biaxial method gain a Method column
  const hasMethod = res.checks.some(c => c.method);
  // v0.9 — insert a governing-combo column in envelope mode (after "equation")
  let cols = envelope
    ? DESIGN_COLS.flatMap(c => c.key === "equation"
        ? [c, { key: "governing_combo", label: "Gov. combo", txt: true }] : [c])
    : DESIGN_COLS;
  if (hasMethod)
    cols = cols.flatMap(c => c.key === "ratio"
      ? [{ key: "method", label: "Method", txt: true }, c] : [c]);
  const { key: sk, dir } = store.designSort;
  const head = `<thead><tr>` + cols.map(c =>
    `<th class="sortable ${c.txt ? "txt" : ""}" data-key="${c.key}">${c.label}` +
    (c.key === sk ? `<span class="sort-arrow">${dir > 0 ? "▲" : "▼"}</span>` : "") +
    `</th>`).join("") + `</tr></thead>`;
  const chip = st => `<span class="status-chip st-${st === "N/A" ? "na" : st.toLowerCase()}">${esc(st)}</span>`;
  const govCell = x => x.governing_combo && x.status !== "N/A"
    ? `<td class="txt gov-combo" data-combo="${esc(x.governing_combo)}" title="Switch the case selector to this combo">${esc(x.governing_combo)}</td>`
    : `<td class="txt dim">—</td>`;
  // v0.16 — method chip + governing-ratio cell (biaxial ⇒ ratio_biaxial governs)
  const methodCell = x => {
    if (x.status === "N/A") return `<td class="txt dim">—</td>`;
    const m = x.method === "bresler" ? ["mc-bresler", "Bresler"]
      : x.method === "contour" ? ["mc-contour", "contour"] : ["mc-uni", "uniaxial"];
    return `<td class="txt"><span class="method-chip ${m[0]}">${m[1]}</span></td>`;
  };
  const ratioCell = x => {
    const gov = designGovRatio(x);
    const bx = x.biaxial && isFinite(x.ratio_biaxial);
    return `<td class="${gov > 1 ? "exceed" : ""}"${bx
      ? ` title="biaxial (${esc(x.method)}) ratio governs · uniaxial P-M ratio ${fmt(x.ratio, 3)}"` : ""}>` +
      `<b>${fmt(gov, 3)}</b>${bx ? `<sup class="ratio-bx">bx</sup>` : ""}</td>`;
  };
  const body = rows.map(x => `<tr data-uid="${esc(x.uid)}" class="design-row${designGovRatio(x) > 1 ? " over" : ""}" title="${esc(x.notes || "")}">
    <td class="txt">${esc(x.uid)}</td>
    <td class="txt dim">${esc(x.kind)}</td>
    <td class="txt dim">${esc(x.section)}</td>
    <td>${fmt(x.Pu, 1)}</td>
    <td>${fmt(x.Mu33, 1)}</td>
    <td>${fmt(x.Mu22, 1)}</td>
    <td class="dim">${fmt(x.phiPn, 1)}</td>
    <td class="dim">${fmt(x.phiMn33, 1)}</td>
    ${hasMethod ? methodCell(x) : ""}${ratioCell(x)}
    <td class="txt dim">${esc(x.equation)}</td>${envelope ? govCell(x) : ""}
    <td class="txt">${chip(x.status)}</td></tr>`).join("");
  table.innerHTML = head + `<tbody>${body || `<tr><td class="txt dim">No members match the filter</td></tr>`}</tbody>`;
  $("designCount").textContent =
    `${rows.length} of ${res.checks.length} members · ${store.designKind} · ${caseLabel(res.case)}`;
  // v0.16 — biaxial-method footnote (visible when any biaxial check exists)
  $("designFootnote").classList.toggle("hidden",
    !res.checks.some(c => c.biaxial));

  table.querySelectorAll("th.sortable").forEach(th =>
    th.addEventListener("click", () => {
      const k = th.dataset.key;
      if (store.designSort.key === k) store.designSort.dir *= -1;
      else store.designSort = { key: k, dir: (k === "uid" || k === "kind" || k === "section" || k === "status" || k === "equation" || k === "governing_combo" || k === "method") ? 1 : -1 };
      renderDesignTable();
    }));
  // v0.9 — governing-combo cell → switch the case selector to that combo
  table.querySelectorAll("td.gov-combo").forEach(td =>
    td.addEventListener("click", ev => {
      ev.stopPropagation();
      store.designAllCombos = false;
      store.designCase = td.dataset.combo;
      renderDesignForm();
      toast("Case selector set", `Switched to combo “${td.dataset.combo}”`, "info", 3500);
    }));
  // row click → select the member in 3D
  table.querySelectorAll("tr.design-row").forEach(tr =>
    tr.addEventListener("click", () => selectMemberFrom3D(tr.dataset.uid)));
}

/** Jump to the 3D view, highlight a member and open its detail panel. */
function selectMemberFrom3D(uid) {
  if (!uid || !store.results) return;
  store.selectedMemberUid = uid;
  viewer.setHighlight([uid], "#35b5e5");
  switchTab("view3d");
  renderMemberPanel();
}

/* ================================================================
   v0.18 — WALL PIER DESIGN (Design → Wall)
   POST /api/design/wall {combos?, rho_v?, rho_h?, fy?, fc_prime?} →
   {piers: [{pier, story, P, V, M, ratio_pmm, ratio_shear, phiVn,
   boundary_required, sigma_max, status, combo}], params}.
   ρ is ENTERED in % and SENT as a ratio; fy / f'c in kPa (app units).
   ================================================================ */
const wallGovRatio = r => Math.max(r.ratio_pmm || 0, r.ratio_shear || 0);

function renderWallPanel() {
  const panel = $("wallPanel");
  const on = store.designKind === "wall" && !!store.results;
  panel.classList.toggle("hidden", !on);
  if (!on) return;
  const combos = Object.keys((store.results && store.results.combos) || {});
  if (!store.wallCombos) store.wallCombos = [...combos];       // default: all
  store.wallCombos = store.wallCombos.filter(n => combos.includes(n));
  const p = store.wallParams;
  $("wallForm").innerHTML = `<div class="design-form-row">
    <label class="rs-field wall-combo-field"><span>combos <span class="unit">multi-select · default all</span></span>
      <select id="wallComboSelect" multiple size="${Math.min(4, Math.max(2, combos.length || 2))}"
        title="Load combinations to check — ctrl/cmd-click to pick several">${combos.map(n =>
        `<option value="${esc(n)}"${store.wallCombos.includes(n) ? " selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
    <label class="rs-field"><span>ρv <span class="unit">% vertical</span></span>
      <input id="wallRhoV" type="number" min="0.1" max="4" step="0.05" value="${p.rho_v}"
        title="Vertical (flexural) reinforcement ratio — sent as rho_v = %/100"></label>
    <label class="rs-field"><span>ρh <span class="unit">% horizontal</span></span>
      <input id="wallRhoH" type="number" min="0.1" max="4" step="0.05" value="${p.rho_h}"
        title="Horizontal (shear) reinforcement ratio — sent as rho_h = %/100"></label>
    <label class="rs-field"><span>fy <span class="unit">kPa</span></span>
      <input id="wallFy" type="number" min="1" step="5000" value="${p.fy}"></label>
    <label class="rs-field"><span>f'c <span class="unit">kPa</span></span>
      <input id="wallFc" type="number" min="1" step="5000" value="${p.fc}"></label>
    <button class="btn btn-run design-check" id="wallCheckBtn" title="POST /api/design/wall">
      <span class="spinner hidden" id="wallSpinner"></span><span>Run wall checks</span></button>
    <button class="chip csv-btn${store.wallResult ? "" : " hidden"}" id="csvWall"
      title="Download the wall-pier checks as CSV (unrounded)">⬇ CSV</button>
  </div>
  <p class="muted design-note">RC wall-pier screening — PMM interaction and in-plane shear φVn
    per pier story; boundary elements flagged where σ<sub>max</sub> &gt; 0.2·f'c
    (ACI 318 §18.10.6 stress screen). Label walls with piers in Model mode → wall properties.</p>`;
  const bindNum = (id, key) => $(id).addEventListener("change", e => {
    const v = parseFloat(e.target.value);
    if (isFinite(v) && v > 0) store.wallParams[key] = v;
  });
  bindNum("wallRhoV", "rho_v"); bindNum("wallRhoH", "rho_h");
  bindNum("wallFy", "fy"); bindNum("wallFc", "fc");
  $("wallComboSelect").addEventListener("change", e => {
    store.wallCombos = [...e.target.selectedOptions].map(o => o.value);
  });
  $("wallCheckBtn").addEventListener("click", runWallCheck);
  const csv = $("csvWall");
  if (csv) csv.addEventListener("click", () => downloadCsv("wall"));
  renderWallTable();
}

async function runWallCheck() {
  const btn = $("wallCheckBtn");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  $("wallSpinner").classList.remove("hidden");
  try {
    const p = store.wallParams;
    store.wallResult = await designWall({
      combos: store.wallCombos && store.wallCombos.length ? store.wallCombos : undefined,
      rho_v: p.rho_v / 100, rho_h: p.rho_h / 100,          // % → ratio
      fy: p.fy, fc_prime: p.fc,
    });
    renderWallPanel();
    const rows = (store.wallResult && store.wallResult.piers) || [];
    const ng = rows.filter(r => r.status === "NG").length;
    const nb = rows.filter(r => r.boundary_required).length;
    toast("Wall checks complete",
      `${rows.length} pier stories · ${ng} NG · ${nb} boundary element${nb === 1 ? "" : "s"}`,
      ng ? "error" : "info", 5000);
  } catch (err) {
    toast("Wall check failed", err.message, "error", 8000);
  } finally {
    const b = $("wallCheckBtn"), s = $("wallSpinner");
    if (b) b.disabled = false;
    if (s) s.classList.add("hidden");
  }
}

/** Rows grouped by pier, stories top-first (matching the Wall Piers tab). */
function wallRows() {
  const rows = (store.wallResult && store.wallResult.piers) || [];
  const elev = (store.results && store.results.story_elev) || {};
  const groups = new Map();
  for (const r of rows) {
    if (!groups.has(r.pier)) groups.set(r.pier, []);
    groups.get(r.pier).push(r);
  }
  return [...groups.entries()]
    .sort((a, b) => a[0].localeCompare(b[0], undefined, { numeric: true }))
    .map(([pier, stories]) => ({
      pier,
      stories: [...stories].sort((a, b) => (elev[b.story] ?? 0) - (elev[a.story] ?? 0)),
    }));
}

function renderWallTable() {
  const table = $("wallTable"), summary = $("wallSummary");
  const res = store.wallResult;
  $("wallNote").textContent =
    "Wall-pier design screening — P axial (compression +), V / M in-plane at the story bottom. " +
    "PMM D/C is the axial-flexure interaction, Shear D/C is V / φVn. BOUNDARY marks lifts whose " +
    "extreme-fiber stress exceeds 0.2·f'c — confined boundary elements required. Screening only.";
  if (!res || !res.piers || !res.piers.length) {
    summary.classList.add("hidden");
    table.innerHTML = `<tbody><tr><td class="txt dim">No wall checks yet — pick combos, set ρ / fy / f'c and press “Run wall checks”.</td></tr></tbody>`;
    return;
  }
  const rows = res.piers;
  const worst = rows.reduce((a, r) => (wallGovRatio(r) > wallGovRatio(a) ? r : a), rows[0]);
  const ng = rows.filter(r => r.status === "NG").length;
  const nb = rows.filter(r => r.boundary_required).length;
  const nCombos = (res.params && res.params.combos && res.params.combos.length) ||
    Object.keys((store.results && store.results.combos) || {}).length;
  summary.classList.remove("hidden");
  summary.innerHTML =
    `<span class="ds-item"><b>${rows.length}</b> pier stories</span>` +
    `<span class="ds-item ds-ok"><b>${rows.length - ng}</b> OK</span>` +
    `<span class="ds-item ds-ng"><b>${ng}</b> NG</span>` +
    `<span class="ds-item"><b class="${nb ? "wall-bnd" : ""}">${nb}</b> boundary</span>` +
    `<span class="ds-item">worst <b class="${wallGovRatio(worst) > 1 ? "ds-over" : ""}">` +
      `${esc(worst.pier)} · ${esc(worst.story)} · ${fmt(wallGovRatio(worst), 3)}</b> ` +
      `<span class="dim">(${(worst.ratio_pmm || 0) >= (worst.ratio_shear || 0) ? "PMM" : "shear"} · ${esc(worst.combo || "")})</span></span>` +
    `<span class="ds-item ds-prelim">PRELIMINARY · ${nCombos} combo${nCombos === 1 ? "" : "s"}</span>`;

  const dcChip = r => {
    const cls = r > 1 ? "rc-over" : r > 0.85 ? "rc-near" : "rc-ok";
    return `<span class="ratio-chip ${cls}">${fmt(r, 3)}</span>`;
  };
  const chip = st => `<span class="status-chip st-${st === "NG" ? "ng" : "ok"}">${esc(st)}</span>`;
  const bnd = x => x.boundary_required
    ? `<span class="status-chip st-warn boundary-badge" title="σmax ${fmt(x.sigma_max, 0)} kPa > 0.2·f'c — confined boundary element required">BOUNDARY</span>`
    : `<span class="dim">—</span>`;
  const head = `<thead><tr>
    <th class="txt">Pier</th><th class="txt">Story</th>
    <th>P kN</th><th>V kN</th><th>M kN·m</th>
    <th title="Axial-flexure interaction demand/capacity">PMM D/C</th>
    <th title="V / φVn">Shear D/C</th>
    <th title="In-plane shear capacity">φVn kN</th>
    <th title="σmax > 0.2·f'c ⇒ confined boundary elements">Boundary?</th>
    <th class="txt">Status</th></tr></thead>`;
  const body = wallRows().map(g => {
    const header = `<tr class="pier-group">
      <td class="txt pier-name" colspan="10">▮ ${esc(g.pier)}
        <span class="pier-count">· ${g.stories.length} stor${g.stories.length === 1 ? "y" : "ies"}</span></td></tr>`;
    const rws = g.stories.map(x => `<tr class="wall-row${x.status === "NG" ? " over" : ""}"
        title="governing combo ${esc(x.combo || "—")} · σmax ${fmt(x.sigma_max, 0)} kPa">
      <td class="txt dim"></td>
      <td class="txt">${esc(x.story)}</td>
      <td>${fmt(x.P, 1)}</td>
      <td>${fmt(x.V, 1)}</td>
      <td>${fmt(x.M, 1)}</td>
      <td>${dcChip(x.ratio_pmm || 0)}</td>
      <td>${dcChip(x.ratio_shear || 0)}</td>
      <td class="dim">${fmt(x.phiVn, 1)}</td>
      <td class="txt">${bnd(x)}</td>
      <td class="txt">${chip(x.status)}</td></tr>`).join("");
    return header + rws;
  }).join("");
  table.innerHTML = head + `<tbody>${body}</tbody>`;
}

/* ================================================================
   v0.18 — PUNCHING CHECK (Design → Punching)
   POST /api/design/punching {case?, fc_prime?, cover?} → {columns:
   [{uid, story, Vu, vu, phi_vc, b0, d, ratio, status, case}]}.
   cover ENTERED in mm, SENT in metres. Row click highlights the column in the
   2D plan (selection) and the 3D view; D/C > 1 columns get a red halo
   at their plan position while this card is active.
   ================================================================ */
function renderPunchPanel() {
  const panel = $("punchPanel");
  const on = store.designKind === "punching" && !!store.results;
  panel.classList.toggle("hidden", !on);
  syncPunchHalos();
  if (!on) return;
  const opts = designCaseOptions();
  if (!store.punchCase || !opts.includes(store.punchCase)) {
    // prefer a gravity combo (punching is gravity-governed)
    store.punchCase = opts.find(n =>
      store.results.combos && store.results.combos[n] && !/E[XY]|EQ/i.test(n)) ||
      opts[0] || null;
  }
  const p = store.punchParams;
  $("punchForm").innerHTML = `<div class="design-form-row">
    <label class="rs-field"><span>case / combo</span>
      <select id="punchCaseSelect">${opts.map(n =>
        `<option value="${esc(n)}"${n === store.punchCase ? " selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
    <label class="rs-field"><span>f'c <span class="unit">kPa</span></span>
      <input id="punchFc" type="number" min="1" step="5000" value="${p.fc}"></label>
    <label class="rs-field"><span>cover <span class="unit">mm</span></span>
      <input id="punchCover" type="number" min="5" max="100" step="5" value="${p.cover}"
        title="Slab cover — effective depth d = slab thickness − cover − bar allowance"></label>
    <button class="btn btn-run design-check" id="punchCheckBtn" title="POST /api/design/punching">
      <span class="spinner hidden" id="punchSpinner"></span><span>Run punching check</span></button>
    <button class="chip csv-btn${store.punchResult ? "" : " hidden"}" id="csvPunch"
      title="Download the punching checks as CSV (unrounded)">⬇ CSV</button>
  </div>
  <p class="muted design-note">Two-way (punching) shear at slab–column connections —
    v<sub>u</sub> vs φv<sub>c</sub> = 0.75·0.33√f'c on the critical perimeter b0 at d/2 from the
    column face. Click a row to highlight the column in plan &amp; 3D; failing columns pulse red
    in the plan editor while this card is active.</p>`;
  $("punchCaseSelect").addEventListener("change", e => { store.punchCase = e.target.value; });
  const bindNum = (id, key) => $(id).addEventListener("change", e => {
    const v = parseFloat(e.target.value);
    if (isFinite(v) && v > 0) store.punchParams[key] = v;
  });
  bindNum("punchFc", "fc"); bindNum("punchCover", "cover");
  $("punchCheckBtn").addEventListener("click", runPunchCheck);
  const csv = $("csvPunch");
  if (csv) csv.addEventListener("click", () => downloadCsv("punching"));
  renderPunchTable();
}

async function runPunchCheck() {
  const btn = $("punchCheckBtn");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  $("punchSpinner").classList.remove("hidden");
  try {
    store.punchResult = await designPunching({
      case: store.punchCase,
      // form units are mm; the API validates cover in METRES (0 < cover < 1)
      fc_prime: store.punchParams.fc, cover: store.punchParams.cover / 1000,
    });
    store.punchSel = null;
    renderPunchPanel();                       // re-renders table + plan halos
    const cols = (store.punchResult && store.punchResult.columns) || [];
    const ng = cols.filter(c => c.status === "NG").length;
    toast("Punching check complete",
      `${cols.length} columns · ${ng} NG${ng ? " · red halos mark them in the plan editor" : ""}`,
      ng ? "error" : "info", 5000);
  } catch (err) {
    toast("Punching check failed", err.message, "error", 8000);
  } finally {
    const b = $("punchCheckBtn"), s = $("punchSpinner");
    if (b) b.disabled = false;
    if (s) s.classList.add("hidden");
  }
}

function punchRows() {
  const cols = (store.punchResult && store.punchResult.columns) || [];
  return [...cols].sort((a, b) => (b.ratio || 0) - (a.ratio || 0));
}

/** Plan halo positions for failing punching columns (D/C > 1) — only while
    the Punching card is the active design sub-tab, on the drawn story. */
function punchHalos() {
  if (store.designKind !== "punching") return [];
  const cols = (store.punchResult && store.punchResult.columns) || [];
  if (!cols.length || !store.model) return [];
  const memBy = {};
  for (const mm of store.model.members || []) memBy[mm.uid] = mm;
  return cols.filter(c => (c.ratio || 0) > 1)
    .map(c => ({ c, mm: memBy[c.uid] }))
    .filter(x => x.mm && x.mm.story === store.story)
    .map(x => ({ uid: x.c.uid, x: x.mm.pi[0], y: x.mm.pi[1], r: 0.65 }));
}
function syncPunchHalos() { if (planEditor) planEditor.renderStatic(); }

function renderPunchTable() {
  const table = $("punchTable"), summary = $("punchSummary");
  const res = store.punchResult;
  $("punchNote").textContent =
    "Punching screening — Vu the transferred shear, vu = Vu / (b0·d) the factored shear stress " +
    "on the critical section, φvc the two-way concrete capacity (no shear reinforcement). " +
    "D/C > 1 needs a thicker slab, a drop panel / column capital, or stud rails.";
  if (!res || !res.columns || !res.columns.length) {
    summary.classList.add("hidden");
    table.innerHTML = `<tbody><tr><td class="txt dim">No punching check yet — pick a gravity case/combo and press “Run punching check”.</td></tr></tbody>`;
    return;
  }
  const rows = punchRows();
  const ng = rows.filter(c => c.status === "NG").length;
  const worst = rows[0];
  summary.classList.remove("hidden");
  summary.innerHTML =
    `<span class="ds-item"><b>${rows.length}</b> columns</span>` +
    `<span class="ds-item ds-ok"><b>${rows.length - ng}</b> OK</span>` +
    `<span class="ds-item ds-ng"><b>${ng}</b> NG</span>` +
    `<span class="ds-item">worst <b class="${(worst.ratio || 0) > 1 ? "ds-over" : ""}">` +
      `${esc(worst.uid)} · ${fmt(worst.ratio, 3)}</b></span>` +
    `<span class="ds-item ds-prelim">PRELIMINARY · ${esc(worst.case || store.punchCase || "")}</span>`;

  const dcChip = r => {
    const cls = r > 1 ? "rc-over" : r > 0.85 ? "rc-near" : "rc-ok";
    return `<span class="ratio-chip ${cls}">${fmt(r, 3)}</span>`;
  };
  const chip = st => `<span class="status-chip st-${st === "NG" ? "ng" : "ok"}">${esc(st)}</span>`;
  const head = `<thead><tr>
    <th class="txt">Column</th><th class="txt">Story</th>
    <th title="Transferred shear">Vu kN</th>
    <th title="Factored shear stress on the critical section">vu kPa</th>
    <th title="φ·0.33·√f'c two-way capacity">φvc kPa</th>
    <th title="Critical perimeter at d/2">b0 m</th>
    <th title="Average effective depth">d m</th>
    <th>D/C</th><th class="txt">Status</th></tr></thead>`;
  const body = rows.map(x => `<tr data-uid="${esc(x.uid)}"
      class="design-row punch-row${(x.ratio || 0) > 1 ? " over" : ""}${store.punchSel === x.uid ? " is-active" : ""}"
      title="Click to highlight ${esc(x.uid)} in the 2D plan and 3D view">
    <td class="txt">${esc(x.uid)}${(x.ratio || 0) > 1 ? ` <span class="punch-halo-glyph" title="red halo shown at this column's plan position">◎</span>` : ""}</td>
    <td class="txt dim">${esc(x.story || "—")}</td>
    <td>${fmt(x.Vu, 1)}</td>
    <td>${fmt(x.vu, 1)}</td>
    <td class="dim">${fmt(x.phi_vc, 1)}</td>
    <td class="dim">${fmt(x.b0, 2)}</td>
    <td class="dim">${fmt(x.d, 3)}</td>
    <td>${dcChip(x.ratio || 0)}</td>
    <td class="txt">${chip(x.status)}</td></tr>`).join("");
  table.innerHTML = head + `<tbody>${body}</tbody>`;

  // row → member-highlight hooks: 2D plan selection + 3D highlight (no tab jump)
  table.querySelectorAll("tr.punch-row").forEach(tr =>
    tr.addEventListener("click", () => {
      const uid = tr.dataset.uid;
      store.punchSel = uid;
      handleSelect([{ type: "member", uid }], false);      // 2D plan highlight
      store.selectedMemberUid = uid;                       // 3D member panel target
      viewer.setHighlight([uid], "#e66767");               // 3D highlight (red)
      renderMemberPanel();
      renderPunchTable();
    }));
}

/* ================================================================
   v0.20 — COMPOSITE BEAM DESIGN (Design → Composite)
   POST /api/design/composite {combos?, fc_prime?, t_slab?, hr?, stud_d?,
   rib_spacing?, shored?} → {beams: [{uid, story, applicable, reason?,
   beff, tc, phiMn_full, n_studs, sumQn, ratio_composite, phiMn_partial,
   Mu, ratio, precomp_ratio, I_equiv, defl_LL, defl_limit_ok, status}],
   params}. Lengths ENTERED in mm, SENT in metres; fc' in kPa.
   ================================================================ */
const compGovRatio = b => Math.max(b.ratio || 0, b.precomp_ratio || 0);

function renderCompositePanel() {
  const panel = $("compositePanel");
  if (!panel) return;
  const on = store.designKind === "composite" && !!store.results;
  panel.classList.toggle("hidden", !on);
  if (!on) return;
  const combos = Object.keys((store.results && store.results.combos) || {});
  if (!store.compositeCombos) store.compositeCombos = [...combos];  // default: all
  store.compositeCombos = store.compositeCombos.filter(n => combos.includes(n));
  const p = store.compositeParams;
  $("compositeForm").innerHTML = `<div class="design-form-row">
    <label class="rs-field wall-combo-field"><span>combos <span class="unit">multi-select · default all</span></span>
      <select id="compComboSelect" multiple size="${Math.min(4, Math.max(2, combos.length || 2))}"
        title="Load combinations to check — ctrl/cmd-click to pick several">${combos.map(n =>
        `<option value="${esc(n)}"${store.compositeCombos.includes(n) ? " selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
    <label class="rs-field"><span>slab t <span class="unit">mm</span></span>
      <input id="compTslab" type="number" min="50" max="400" step="5" value="${p.t_slab}"
        title="Total slab thickness incl. deck ribs — sent as t_slab in metres"></label>
    <label class="rs-field"><span>f'c <span class="unit">kPa</span></span>
      <input id="compFc" type="number" min="1" step="5000" value="${p.fc}"></label>
    <label class="rs-field"><span>rib h <span class="unit">mm</span></span>
      <input id="compHr" type="number" min="0" max="150" step="5" value="${p.hr}"
        title="Metal deck rib height hr — sent in metres"></label>
    <label class="rs-field"><span>stud ⌀ <span class="unit">mm</span></span>
      <input id="compStudD" type="number" min="10" max="25" step="1" value="${p.stud_d}"></label>
    <label class="rs-field"><span>stud s <span class="unit">mm</span></span>
      <input id="compRibS" type="number" min="100" max="1000" step="25" value="${p.rib_spacing}"
        title="Stud (deck rib) spacing along the beam — sent in metres"></label>
    <label class="rs-field"><span>shored</span>
      <label class="allcombos-check comp-shored"><input type="checkbox" id="compShored"${p.shored ? " checked" : ""}
        title="Shored construction — the bare steel beam never carries the wet concrete alone"> shored</label></label>
    <button class="btn btn-run design-check" id="compCheckBtn" title="POST /api/design/composite">
      <span class="spinner hidden" id="compSpinner"></span><span>Run composite checks</span></button>
    <button class="chip csv-btn${store.compositeResult ? "" : " hidden"}" id="csvComposite"
      title="Download the composite checks as CSV (unrounded)">⬇ CSV</button>
  </div>
  <p class="muted design-note">Composite steel beam screening (AISC I3) — effective slab width
    b<sub>eff</sub>, full/partial composite φMn from the stud shear ΣQn, pre-composite (wet concrete)
    D/C for unshored construction and the live-load deflection of the transformed section.
    Beams without a slab at their level report n/a. Click a row to highlight the beam in plan &amp; 3D.</p>`;
  const bindNum = (id, key) => $(id).addEventListener("change", e => {
    const v = parseFloat(e.target.value);
    if (isFinite(v) && v > 0) store.compositeParams[key] = v;
  });
  bindNum("compTslab", "t_slab"); bindNum("compFc", "fc"); bindNum("compStudD", "stud_d");
  bindNum("compRibS", "rib_spacing");
  $("compHr").addEventListener("change", e => {      // rib height may be 0 (flat slab)
    const v = parseFloat(e.target.value);
    if (isFinite(v) && v >= 0) store.compositeParams.hr = v;
  });
  $("compShored").addEventListener("change", e => { store.compositeParams.shored = e.target.checked; });
  $("compComboSelect").addEventListener("change", e => {
    store.compositeCombos = [...e.target.selectedOptions].map(o => o.value);
  });
  $("compCheckBtn").addEventListener("click", runCompositeCheck);
  const csv = $("csvComposite");
  if (csv) csv.addEventListener("click", () => downloadCsv("composite"));
  renderCompositeTable();
}

async function runCompositeCheck() {
  const btn = $("compCheckBtn");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  $("compSpinner").classList.remove("hidden");
  try {
    const p = store.compositeParams;
    store.compositeResult = await designComposite({
      combos: store.compositeCombos && store.compositeCombos.length ? store.compositeCombos : undefined,
      fc_prime: p.fc,
      // form units are mm; the API takes metres
      t_slab: p.t_slab / 1000, hr: p.hr / 1000,
      stud_d: p.stud_d / 1000, rib_spacing: p.rib_spacing / 1000,
      shored: !!p.shored,
    });
    renderCompositePanel();
    const rows = (store.compositeResult && store.compositeResult.beams) || [];
    const ng = rows.filter(r => r.status === "NG").length;
    const na = rows.filter(r => r.status === "n/a" || r.applicable === false).length;
    toast("Composite checks complete",
      `${rows.length} beams · ${ng} NG · ${na} n/a`,
      ng ? "error" : "info", 5000);
  } catch (err) {
    toast("Composite check failed", err.message, "error", 8000);
  } finally {
    const b = $("compCheckBtn"), s = $("compSpinner");
    if (b) b.disabled = false;
    if (s) s.classList.add("hidden");
  }
}

/** Applicable beams sorted worst-first; n/a rows last. */
function compositeRows() {
  const rows = (store.compositeResult && store.compositeResult.beams) || [];
  return [...rows].sort((a, b) => {
    const na = a.applicable === false, nb = b.applicable === false;
    if (na !== nb) return na ? 1 : -1;
    return compGovRatio(b) - compGovRatio(a);
  });
}

function renderCompositeTable() {
  const table = $("compositeTable"), summary = $("compositeSummary");
  const res = store.compositeResult;
  $("compositeNote").textContent =
    "Composite screening — φMn (full) at 100 % composite action, D/C = Mu / φMn(partial) with " +
    "ΣQn studs, pre-comp D/C the bare-steel wet-concrete check (unshored), Δ_LL the live-load " +
    "deflection of the transformed section vs L/360. Screening only.";
  if (!res || !res.beams || !res.beams.length) {
    summary.classList.add("hidden");
    table.innerHTML = `<tbody><tr><td class="txt dim">No composite checks yet — set the slab / stud parameters and press “Run composite checks”.</td></tr></tbody>`;
    return;
  }
  const rows = compositeRows();
  const app = rows.filter(r => r.applicable !== false);
  const ng = app.filter(r => r.status === "NG").length;
  const na = rows.length - app.length;
  const worst = app[0];
  const prm = res.params || {};
  const nCombos = (prm.combos && prm.combos.length) ||
    Object.keys((store.results && store.results.combos) || {}).length;
  summary.classList.remove("hidden");
  summary.innerHTML =
    `<span class="ds-item"><b>${rows.length}</b> beams</span>` +
    `<span class="ds-item ds-ok"><b>${app.length - ng}</b> OK</span>` +
    `<span class="ds-item ds-ng"><b>${ng}</b> NG</span>` +
    `<span class="ds-item ds-na"><b>${na}</b> n/a</span>` +
    (worst ? `<span class="ds-item">worst <b class="${compGovRatio(worst) > 1 ? "ds-over" : ""}">` +
      `${esc(worst.uid)} · ${fmt(compGovRatio(worst), 3)}</b> ` +
      `<span class="dim">(${(worst.ratio || 0) >= (worst.precomp_ratio || 0) ? "composite" : "pre-comp"})</span></span>` : "") +
    `<span class="ds-item ds-prelim">PRELIMINARY · ${prm.shored ? "shored" : "unshored"} · ${nCombos} combo${nCombos === 1 ? "" : "s"}</span>`;

  const dcChip = r => {
    const cls = r > 1 ? "rc-over" : r > 0.85 ? "rc-near" : "rc-ok";
    return `<span class="ratio-chip ${cls}">${fmt(r, 3)}</span>`;
  };
  const chip = st => st === "n/a"
    ? `<span class="status-chip st-na">n/a</span>`
    : `<span class="status-chip st-${st === "NG" ? "ng" : "ok"}">${esc(st)}</span>`;
  const head = `<thead><tr>
    <th class="txt">Beam</th><th class="txt">Story</th>
    <th title="Effective slab width">beff m</th>
    <th title="Fully-composite design flexural strength">φMn full kN·m</th>
    <th title="Shear studs on the span">studs</th>
    <th title="Total stud shear strength">ΣQn kN</th>
    <th title="ΣQn / As·Fy — degree of composite action">% comp</th>
    <th>Mu kN·m</th>
    <th title="Mu / φMn(partial)">D/C</th>
    <th title="Bare steel beam under wet concrete (unshored construction)">pre-comp D/C</th>
    <th title="Live-load deflection of the transformed section">Δ_LL mm</th>
    <th class="txt">Status</th></tr></thead>`;
  const body = rows.map(x => {
    if (x.applicable === false) return `<tr data-uid="${esc(x.uid)}"
        class="design-row comp-row comp-na" title="${esc(x.reason || "not applicable")}">
      <td class="txt">${esc(x.uid)}</td>
      <td class="txt dim">${esc(x.story || "—")}</td>
      <td class="txt dim" colspan="9">n/a — ${esc(x.reason || "not applicable")}</td>
      <td class="txt">${chip("n/a")}</td></tr>`;
    return `<tr data-uid="${esc(x.uid)}" class="design-row comp-row${compGovRatio(x) > 1 ? " over" : ""}"
        title="Click to highlight ${esc(x.uid)} in the 2D plan and 3D view">
      <td class="txt">${esc(x.uid)}</td>
      <td class="txt dim">${esc(x.story || "—")}</td>
      <td class="dim">${fmt(x.beff, 2)}</td>
      <td class="dim">${fmt(x.phiMn_full, 1)}</td>
      <td>${fmt(x.n_studs, 0)}</td>
      <td class="dim">${fmt(x.sumQn, 0)}</td>
      <td>${fmt((x.ratio_composite || 0) * 100, 0)} %</td>
      <td>${fmt(x.Mu, 1)}</td>
      <td>${dcChip(x.ratio || 0)}</td>
      <td>${dcChip(x.precomp_ratio || 0)}</td>
      <td class="${x.defl_limit_ok === false ? "exceed" : "dim"}">${fmt((x.defl_LL || 0) * 1000, 1)}${x.defl_limit_ok === false ? " ✕" : ""}</td>
      <td class="txt">${chip(x.status || "OK")}</td></tr>`;
  }).join("");
  table.innerHTML = head + `<tbody>${body}</tbody>`;

  // row → member-highlight hooks: 2D plan selection + 3D highlight (no tab jump)
  table.querySelectorAll("tr.comp-row").forEach(tr =>
    tr.addEventListener("click", () => {
      const uid = tr.dataset.uid;
      handleSelect([{ type: "member", uid }], false);      // 2D plan highlight
      store.selectedMemberUid = uid;                       // 3D member panel target
      viewer.setHighlight([uid], "#35b5e5");               // 3D highlight
      renderMemberPanel();
    }));
}

/* ================================================================
   v0.20 — SLAB FLEXURAL DESIGN (Design → Slab)
   POST /api/design/slab {case?, fc_prime?, bar_d?, cover?} → {regions:
   [{uid, directions: [{dir, strips: [{strip, sections: [{x, Mu, As_req,
   As_min, spacing, status}]}]}]}], params}. bar Ø / cover ENTERED in mm,
   SENT in metres. The response shape may drift — everything renders
   through defensive accessors with graceful fallbacks.
   ================================================================ */
/** Defensive mm²/m: backends may send m²/m (tiny numbers) or mm²/m. */
const asMm2 = v => !isFinite(v) ? null : (Math.abs(v) < 0.05 ? v * 1e6 : v);
/** Defensive mm: metres (< 1) or already mm. */
const asMm = v => !isFinite(v) ? null : (Math.abs(v) < 1 ? v * 1000 : v);

function renderSlabPanel() {
  const panel = $("slabPanel");
  if (!panel) return;
  const on = store.designKind === "slab" && !!store.results;
  panel.classList.toggle("hidden", !on);
  if (!on) return;
  const opts = designCaseOptions();
  if (!store.slabCase || !opts.includes(store.slabCase)) {
    // prefer a gravity combo (slab flexure is gravity-governed)
    store.slabCase = opts.find(n =>
      store.results.combos && store.results.combos[n] && !/E[XY]|EQ/i.test(n)) ||
      opts[0] || null;
  }
  const p = store.slabParams;
  $("slabForm").innerHTML = `<div class="design-form-row">
    <label class="rs-field"><span>case / combo</span>
      <select id="slabCaseSelect">${opts.map(n =>
        `<option value="${esc(n)}"${n === store.slabCase ? " selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
    <label class="rs-field"><span>f'c <span class="unit">kPa</span></span>
      <input id="slabFc" type="number" min="1" step="5000" value="${p.fc}"></label>
    <label class="rs-field"><span>bar ⌀ <span class="unit">mm</span></span>
      <input id="slabBarD" type="number" min="8" max="32" step="2" value="${p.bar_d}"
        title="Flexural bar diameter — sent as bar_d in metres"></label>
    <label class="rs-field"><span>cover <span class="unit">mm</span></span>
      <input id="slabCover" type="number" min="10" max="75" step="5" value="${p.cover}"
        title="Clear cover — effective depth d = t − cover − ⌀/2, sent in metres"></label>
    <button class="btn btn-run design-check" id="slabCheckBtn" title="POST /api/design/slab">
      <span class="spinner hidden" id="slabSpinner"></span><span>Run slab design</span></button>
    <button class="chip csv-btn${store.slabResult ? "" : " hidden"}" id="csvSlab"
      title="Download the slab design sections as CSV (unrounded)">⬇ CSV</button>
  </div>
  <p class="muted design-note">Two-way slab flexural design by design strips — per region and
    span direction, column / middle strip moments at the support and midspan sections with the
    required steel A<sub>s</sub>, the code minimum and the resulting bar spacing.</p>`;
  $("slabCaseSelect").addEventListener("change", e => { store.slabCase = e.target.value; });
  const bindNum = (id, key) => $(id).addEventListener("change", e => {
    const v = parseFloat(e.target.value);
    if (isFinite(v) && v > 0) store.slabParams[key] = v;
  });
  bindNum("slabFc", "fc"); bindNum("slabBarD", "bar_d"); bindNum("slabCover", "cover");
  $("slabCheckBtn").addEventListener("click", runSlabDesign);
  const csv = $("csvSlab");
  if (csv) csv.addEventListener("click", () => downloadCsv("slab"));
  renderSlabRegions();
}

async function runSlabDesign() {
  const btn = $("slabCheckBtn");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  $("slabSpinner").classList.remove("hidden");
  try {
    store.slabResult = await designSlab({
      case: store.slabCase, fc_prime: store.slabParams.fc,
      // form units are mm; the API takes metres
      bar_d: store.slabParams.bar_d / 1000, cover: store.slabParams.cover / 1000,
    });
    renderSlabPanel();
    const secs = slabSectionRows();
    const ng = secs.filter(s => s.status === "NG").length;
    toast("Slab design complete",
      `${(store.slabResult.regions || []).length} region${(store.slabResult.regions || []).length === 1 ? "" : "s"} · ${secs.length} sections · ${ng} NG`,
      ng ? "error" : "info", 5000);
  } catch (err) {
    toast("Slab design failed", err.message, "error", 8000);
  } finally {
    const b = $("slabCheckBtn"), s = $("slabSpinner");
    if (b) b.disabled = false;
    if (s) s.classList.add("hidden");
  }
}

/** Flat section rows (region/dir/strip annotated) — CSV + summary source.
    Defensive against shape drift: missing arrays become empty. */
function slabSectionRows() {
  const out = [];
  for (const rg of (store.slabResult && store.slabResult.regions) || []) {
    for (const dd of rg.directions || []) {
      for (const st of dd.strips || []) {
        for (const sec of st.sections || []) {
          out.push({ region: rg.uid || "—", dir: dd.dir || "?",
            strip: st.strip || "?", ...sec });
        }
      }
    }
  }
  return out;
}

/** Plan sketch of a design region: column-strip bands (edges) + middle
    strip shaded, quarter-lines dashed, span-direction arrow. */
function slabStripSvg(rg) {
  const shells = (store.model && store.model.shells) || [];
  const sh = shells.find(s => s.uid === rg.uid);
  let Lx = 6, Ly = 6;
  if (sh && sh.corners && sh.corners.length >= 3) {
    Lx = Math.abs(sh.corners[1][0] - sh.corners[0][0]) || 6;
    Ly = Math.abs(sh.corners[2][1] - sh.corners[1][1]) || 6;
  }
  const W = 250, H = 170, P = 26;
  const sc = Math.min((W - 2 * P) / Lx, (H - 2 * P) / Ly);
  const w = Lx * sc, h = Ly * sc;
  const x0 = (W - w) / 2, y0 = (H - h) / 2;
  const band = Math.min(w, h) / 4;                       // column strip = ¼ span
  const colF = "rgba(53,181,229,0.14)", midF = "rgba(120,140,165,0.10)";
  const line = "rgba(120,140,165,0.45)", dash = "rgba(120,140,165,0.30)";
  let s = `<svg viewBox="0 0 ${W} ${H}" class="slab-strip-svg" aria-label="Design strips — region ${esc(rg.uid || "")}">`;
  // middle field then column-strip bands top/bottom (x-direction strips)
  s += `<rect x="${x0}" y="${y0 + band}" width="${w}" height="${h - 2 * band}" fill="${midF}"/>`;
  s += `<rect x="${x0}" y="${y0}" width="${w}" height="${band}" fill="${colF}"/>`;
  s += `<rect x="${x0}" y="${y0 + h - band}" width="${w}" height="${band}" fill="${colF}"/>`;
  s += `<rect x="${x0}" y="${y0}" width="${w}" height="${h}" fill="none" stroke="${line}" stroke-width="1.2"/>`;
  s += `<line x1="${x0}" y1="${y0 + band}" x2="${x0 + w}" y2="${y0 + band}" stroke="${dash}" stroke-width="1" stroke-dasharray="4 3"/>`;
  s += `<line x1="${x0}" y1="${y0 + h - band}" x2="${x0 + w}" y2="${y0 + h - band}" stroke="${dash}" stroke-width="1" stroke-dasharray="4 3"/>`;
  // labels
  s += `<text x="${x0 + w / 2}" y="${y0 + band / 2 + 3}" fill="var(--accent)" font-size="8.5" text-anchor="middle" font-family="inherit">column strip</text>`;
  s += `<text x="${x0 + w / 2}" y="${y0 + h / 2 + 3}" fill="var(--text-3)" font-size="8.5" text-anchor="middle" font-family="inherit">middle strip</text>`;
  s += `<text x="${x0 + w / 2}" y="${y0 + h - band / 2 + 3}" fill="var(--accent)" font-size="8.5" text-anchor="middle" font-family="inherit">column strip</text>`;
  // span-direction arrow (x)
  const ay = y0 + h + 13;
  s += `<line x1="${x0 + w / 2 - 22}" y1="${ay}" x2="${x0 + w / 2 + 16}" y2="${ay}" stroke="${line}" stroke-width="1.2"/>`;
  s += `<path d="M ${x0 + w / 2 + 16} ${ay - 3} l 6 3 l -6 3 z" fill="${line}"/>`;
  s += `<text x="${x0 + w / 2 - 30}" y="${ay + 3}" fill="var(--text-3)" font-size="8.5" text-anchor="end" font-family="inherit">span x · ${fmt(Lx, 1)} m</text>`;
  s += `<text x="${x0 - 8}" y="${y0 + h / 2 + 3}" fill="var(--text-3)" font-size="8.5" text-anchor="middle" font-family="inherit" transform="rotate(-90 ${x0 - 8} ${y0 + h / 2})">y · ${fmt(Ly, 1)} m</text>`;
  return s + "</svg>";
}

function renderSlabRegions() {
  const wrap = $("slabRegions"), summary = $("slabSummary");
  const res = store.slabResult;
  $("slabNote").textContent =
    "Slab design strips — Mu the factored strip moment per metre (negative at supports), " +
    "As req the flexural steel demand, As min the shrinkage/temperature floor, spacing the " +
    "resulting bar spacing (≤ 3t, ≤ 450 mm). NG sections are under-reinforced — thicken the " +
    "slab or use larger bars. Screening only.";
  if (!res || !Array.isArray(res.regions) || !res.regions.length) {
    summary.classList.add("hidden");
    wrap.innerHTML = `<p class="muted slab-empty">No slab design yet — pick a gravity case/combo, set f'c and the bar, and press “Run slab design”.</p>`;
    return;
  }
  const secs = slabSectionRows();
  const ng = secs.filter(s => s.status === "NG").length;
  const govern = secs.reduce((a, s) =>
    (asMm2(s.As_req) || 0) > (asMm2(a && a.As_req) || 0) ? s : a, secs[0]);
  summary.classList.remove("hidden");
  summary.innerHTML =
    `<span class="ds-item"><b>${res.regions.length}</b> region${res.regions.length === 1 ? "" : "s"}</span>` +
    `<span class="ds-item"><b>${secs.length}</b> sections</span>` +
    `<span class="ds-item ds-ok"><b>${secs.length - ng}</b> OK</span>` +
    `<span class="ds-item ds-ng"><b>${ng}</b> NG</span>` +
    (govern ? `<span class="ds-item">peak As <b>${fmt(asMm2(govern.As_req), 0)} mm²/m</b> <span class="dim">(${esc(govern.region)} · ${esc(govern.dir)} · ${esc(govern.strip)})</span></span>` : "") +
    `<span class="ds-item ds-prelim">PRELIMINARY · ${esc((res.params && res.params.case) || store.slabCase || "")}</span>`;

  const chip = st => `<span class="status-chip st-${st === "NG" ? "ng" : "ok"}">${esc(st || "OK")}</span>`;
  const stripTable = st => {
    const rows = (st.sections || []).map(sec => `<tr class="${sec.status === "NG" ? "over" : ""}">
      <td class="txt dim">x = ${fmt(sec.x, 2)} m</td>
      <td class="${(sec.Mu || 0) < 0 ? "dim" : ""}">${fmt(sec.Mu, 2)}</td>
      <td class="${sec.status === "NG" ? "exceed" : ""}"><b>${fmt(asMm2(sec.As_req), 0)}</b></td>
      <td class="dim">${fmt(asMm2(sec.As_min), 0)}</td>
      <td>${fmt(asMm(sec.spacing), 0)}</td>
      <td class="txt">${chip(sec.status)}</td></tr>`).join("");
    return `<div class="slab-strip-block">
      <div class="slab-strip-title">${esc(st.strip || "?")} strip</div>
      <table class="data-table slab-table">
        <thead><tr><th class="txt">Section</th><th>Mu kN·m/m</th>
          <th title="Required flexural steel">As req mm²/m</th>
          <th title="Minimum (shrinkage/temperature) steel">As min</th>
          <th title="Resulting bar spacing">spacing mm</th>
          <th class="txt">Status</th></tr></thead>
        <tbody>${rows || `<tr><td class="txt dim">no sections</td></tr>`}</tbody>
      </table></div>`;
  };
  wrap.innerHTML = res.regions.map((rg, i) => {
    const n = slabSectionRows().filter(s => s.region === (rg.uid || "—"));
    const rgNg = n.filter(s => s.status === "NG").length;
    const dirs = (rg.directions || []).map(dd => `<div class="slab-dir-block">
        <div class="slab-dir-title">direction <b>${esc(dd.dir || "?")}</b></div>
        ${(dd.strips || []).map(stripTable).join("")}</div>`).join("") ||
      `<p class="muted">no strip data returned for this region</p>`;
    return `<details class="slab-region"${i === 0 ? " open" : ""}>
      <summary>▮ ${esc(rg.uid || "region")} <span class="pier-count">· ${n.length} sections${rgNg ? ` · <b class="svc-ng-count">${rgNg} NG</b>` : ""}</span></summary>
      <div class="slab-region-body">
        <div class="slab-strip-diagram">${slabStripSvg(rg)}</div>
        <div class="slab-dir-wrap">${dirs}</div>
      </div></details>`;
  }).join("");
}

/* ================================================================
   v0.20 — FLOOR VIBRATION (Results → Serviceability card)
   POST /api/results/vibration {live_factor?, beta?, ap_limit?} →
   {beams: [{uid, story, fn, delta_mid, W_eff, ap_over_g, limit,
   status}]}. Beams over the limit pulse amber in the 3D view
   (setMemberColors alternated on a timer).
   ================================================================ */
let vibPulseTimer = null;
let vibPulseUids = [];

/** Stop the amber pulse timer without touching the member colors. */
function clearVibTimer() {
  if (vibPulseTimer) { clearInterval(vibPulseTimer); vibPulseTimer = null; }
  vibPulseUids = [];
}

/** Stop pulsing and hand the member colors back to the drift optimizer. */
function stopVibPulse() {
  const had = !!vibPulseTimer;
  clearVibTimer();
  if (had) applyVwColors();      // restores vw colors, or clears when none
}

/** Pulse the given beams amber in 3D while the vibration result stands. */
function startVibPulse(uids) {
  clearVibTimer();
  if (!uids || !uids.length) { applyVwColors(); return; }
  vibPulseUids = [...uids];
  let bright = true;
  const paint = () => {
    const c = bright ? "#e5a50a" : "#7a5c08";
    const map = {};
    for (const u of vibPulseUids) map[u] = c;
    viewer.setMemberColors(map);
    bright = !bright;
  };
  paint();
  vibPulseTimer = setInterval(paint, 650);
}

const vibLimitValue = () => {
  const sel = $("vibLimitSel");
  if (sel && sel.value === "custom") {
    const v = parseFloat($("vibLimitCustom").value);
    return (isFinite(v) && v > 0) ? v / 100 : 0.005;     // entered in %
  }
  return parseFloat(sel ? sel.value : "0.005") || 0.005;
};

async function runVibration() {
  const btn = $("vibRunBtn");
  if (!btn || btn.disabled) return;
  const p = store.vibParams;
  const lv = parseFloat($("vibLive").value);
  const bt = parseFloat($("vibBeta").value);
  if (isFinite(lv) && lv >= 0) p.live_factor = lv;
  if (isFinite(bt) && bt > 0) p.beta = bt;
  p.ap_limit = vibLimitValue();
  btn.disabled = true;
  $("vibSpinner").classList.remove("hidden");
  try {
    store.vibResult = await fetchVibration({
      live_factor: p.live_factor, beta: p.beta, ap_limit: p.ap_limit,
    });
    renderVibTable();
    const rows = (store.vibResult && store.vibResult.beams) || [];
    const bad = rows.filter(r => r.status === "NG");
    startVibPulse(bad.map(r => r.uid));
    toast("Vibration screening complete",
      `${rows.length} beams · ${bad.length} over the ap/g limit${bad.length ? " · pulsing amber in 3D" : ""}`,
      bad.length ? "error" : "info", 5000);
  } catch (err) {
    toast("Vibration screening failed", err.message, "error", 8000);
  } finally {
    const b = $("vibRunBtn"), s = $("vibSpinner");
    if (b) b.disabled = false;
    if (s) s.classList.add("hidden");
  }
}

function vibRows() {
  const rows = (store.vibResult && store.vibResult.beams) || [];
  return [...rows].sort((a, b) => (b.ap_over_g || 0) - (a.ap_over_g || 0));
}

function renderVibTable() {
  const table = $("vibTable"), summary = $("vibSummary"), csv = $("csvVib");
  if (!table) return;
  $("vibNote").textContent =
    "Floor vibration screening (AISC DG11 walking excitation) — fn = 0.18·√(g/Δ) from the " +
    "midspan deflection under D + (live factor)·L, peak acceleration ap/g = P0·e^(−0.35fn)/(β·W) " +
    "vs the occupancy comfort limit. Beams over the limit pulse amber in the 3D view. " +
    "Click a row to open the beam in 3D.";
  const res = store.vibResult;
  if (!res || !res.beams || !res.beams.length) {
    summary.classList.add("hidden");
    if (csv) csv.classList.add("hidden");
    table.innerHTML = `<tbody><tr><td class="txt dim">No vibration screening yet — set the walking-excitation parameters and press “Run”.</td></tr></tbody>`;
    return;
  }
  const rows = vibRows();
  const ng = rows.filter(r => r.status === "NG").length;
  const worst = rows[0];
  const prm = res.params || store.vibParams;
  summary.classList.remove("hidden");
  if (csv) csv.classList.remove("hidden");
  summary.innerHTML =
    `<span class="ds-item"><b>${rows.length}</b> beams</span>` +
    `<span class="ds-item ds-ok"><b>${rows.length - ng}</b> OK</span>` +
    `<span class="ds-item ds-ng"><b>${ng}</b> NG</span>` +
    `<span class="ds-item">worst <b class="${worst.status === "NG" ? "ds-over" : ""}">` +
      `${esc(worst.uid)} · ap/g ${fmt((worst.ap_over_g || 0) * 100, 2)} %</b></span>` +
    `<span class="ds-item ds-prelim">β ${fmt(prm.beta ?? store.vibParams.beta, 3)} · limit ${fmt((prm.ap_limit ?? store.vibParams.ap_limit) * 100, 2)} %</span>`;

  const chip = st => `<span class="status-chip st-${st === "NG" ? "ng" : "ok"}">${esc(st || "OK")}</span>`;
  const head = `<thead><tr>
    <th class="txt">Beam</th><th class="txt">Story</th>
    <th title="Natural frequency of the floor panel">fn Hz</th>
    <th title="Midspan deflection under the participating weight">Δmid mm</th>
    <th title="Effective panel weight">W_eff kN</th>
    <th title="Peak walking acceleration ratio">ap/g %</th>
    <th>limit %</th><th class="txt">Status</th></tr></thead>`;
  const body = rows.map(x => `<tr data-uid="${esc(x.uid)}"
      class="design-row vib-row${x.status === "NG" ? " over" : ""}"
      title="Click to show ${esc(x.uid)} in 3D${x.status === "NG" ? " — pulsing amber" : ""}">
    <td class="txt">${esc(x.uid)}${x.status === "NG" ? ` <span class="vib-pulse-glyph" title="pulsing amber in the 3D view">◉</span>` : ""}</td>
    <td class="txt dim">${esc(x.story || "—")}</td>
    <td>${fmt(x.fn, 2)}</td>
    <td class="dim">${fmt((x.delta_mid || 0) * 1000, 2)}</td>
    <td class="dim">${fmt(x.W_eff, 0)}</td>
    <td class="${x.status === "NG" ? "exceed" : ""}"><b>${fmt((x.ap_over_g || 0) * 100, 2)}</b></td>
    <td class="dim">${fmt((x.limit ?? prm.ap_limit ?? 0.005) * 100, 2)}</td>
    <td class="txt">${chip(x.status)}</td></tr>`).join("");
  table.innerHTML = head + `<tbody>${body}</tbody>`;
  table.querySelectorAll("tr.vib-row").forEach(tr =>
    tr.addEventListener("click", () => selectMemberFrom3D(tr.dataset.uid)));
}

/* ================================================================
   v0.18 — DRIFT OPTIMIZER (Results → Drift)
   POST /api/results/virtual-work {case, direction} → {contributions:
   {uid: m}, total, roof_disp}. Members are colored in the 3D viewer by
   their drift energy share (normalized 0…max); top-10 table below.
   ================================================================ */
const VW_STOPS = ["#3a4a5d", "#2c7fd6", "#e5a50a", "#e05252"];
/** t ∈ [0,1] → sequential slate → blue → amber → red. */
function vwColor(t) {
  t = Math.max(0, Math.min(1, isFinite(t) ? t : 0));
  const seg = Math.min(Math.floor(t * (VW_STOPS.length - 1)), VW_STOPS.length - 2);
  const f = t * (VW_STOPS.length - 1) - seg;
  const h = c => [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)];
  const a = h(VW_STOPS[seg]), b = h(VW_STOPS[seg + 1]);
  return `rgb(${Math.round(a[0] + (b[0] - a[0]) * f)},${Math.round(a[1] + (b[1] - a[1]) * f)},${Math.round(a[2] + (b[2] - a[2]) * f)})`;
}

function renderDriftPanel() {
  if (!store.results) return;
  const opts = designCaseOptions();
  if (!store.vwCase || !opts.includes(store.vwCase))
    store.vwCase = opts.find(n => /E[XY]|EQ|WIND|W[XY]/i.test(n)) || opts[0] || null;
  $("vwCaseSelect").innerHTML = opts.map(n =>
    `<option value="${esc(n)}"${n === store.vwCase ? " selected" : ""}>${esc(n)}</option>`).join("");
  document.querySelectorAll("#vwDirToggle .seg-btn").forEach(b =>
    b.classList.toggle("is-active", b.dataset.dir === store.vwDir));
  renderVwTable();
  applyVwColors();
}

async function runVirtualWork() {
  const btn = $("vwRunBtn");
  if (btn.disabled || !store.vwCase) return;
  btn.disabled = true;
  $("vwSpinner").classList.remove("hidden");
  try {
    const res = await fetchVirtualWork({ case: store.vwCase, direction: store.vwDir });
    store.vwResult = { case: store.vwCase, direction: store.vwDir, ...res };
    renderVwTable();
    applyVwColors();
    toast("Drift shares computed",
      `${Object.keys(res.contributions || {}).length} members · roof ${fmt((res.roof_disp || 0) * 1000, 1)} mm · 3D members colored`,
      "info", 5000);
  } catch (err) {
    toast("Virtual-work run failed", err.message, "error", 8000);
  } finally {
    btn.disabled = false;
    $("vwSpinner").classList.add("hidden");
  }
}

function clearVwColors() {
  store.vwResult = null;
  renderVwTable();
  applyVwColors();
}

/** Contributions sorted descending, annotated with kind/story and % of total. */
function vwRows() {
  const res = store.vwResult;
  if (!res || !res.contributions) return [];
  const total = res.total ||
    Object.values(res.contributions).reduce((a, b) => a + b, 0) || 1;
  const memBy = {};
  for (const mm of (store.model && store.model.members) || []) memBy[mm.uid] = mm;
  return Object.entries(res.contributions)
    .map(([uid, v]) => ({
      uid, v, pct: (v / total) * 100,
      kind: (memBy[uid] || {}).kind || "—",
      story: (memBy[uid] || {}).story || "—",
    }))
    .sort((a, b) => b.v - a.v);
}

function renderVwTable() {
  const table = $("vwTable"), summary = $("vwSummary");
  const res = store.vwResult;
  $("vwClearBtn").disabled = !res;
  if (!res) {
    summary.classList.add("hidden");
    table.innerHTML = `<tbody><tr><td class="txt dim">No virtual-work run yet — pick a lateral case + direction and press “Run”.</td></tr></tbody>`;
    $("vwNote").textContent =
      "Members are ranked by how much of the roof displacement they cause — " +
      "stiffening the top rows buys the most drift for the least material.";
    return;
  }
  const rows = vwRows();
  const top = rows.slice(0, 10);
  const total = res.total || rows.reduce((a, r) => a + r.v, 0);
  const roof = res.roof_disp || 0;
  const match = Math.abs(total - roof) <= Math.max(1e-9, Math.abs(roof) * 0.02);
  summary.classList.remove("hidden");
  summary.innerHTML =
    `<span class="ds-item"><b>${rows.length}</b> contributing members</span>` +
    `<span class="ds-item">Σ contributions <b>${fmt(total * 1000, 2)} mm</b></span>` +
    `<span class="ds-item">roof displacement <b>${fmt(roof * 1000, 2)} mm</b></span>` +
    `<span class="ds-item"><b class="${match ? "vw-match" : "ds-over"}" title="Σ member virtual-work contributions vs the analysis roof displacement — the two should match">${match ? "✓ totals match" : "≠ totals differ"}</b></span>` +
    `<span class="ds-item ds-prelim">${esc(caseLabel(res.case || ""))} · ${esc(res.direction || store.vwDir)}</span>`;

  const vmax = rows.length ? rows[0].v : 1;
  const head = `<thead><tr>
    <th>#</th><th class="txt">Member</th><th class="txt">Kind</th><th class="txt">Story</th>
    <th title="Contribution to the roof displacement">δ mm</th>
    <th title="Share of the total roof drift">% of total</th>
    <th class="txt">share</th></tr></thead>`;
  const body = top.map((x, i) => `<tr data-uid="${esc(x.uid)}" class="design-row vw-row"
      title="Click to show ${esc(x.uid)} in 3D">
    <td class="dim">${i + 1}</td>
    <td class="txt">${esc(x.uid)}</td>
    <td class="txt dim">${esc(x.kind)}</td>
    <td class="txt dim">${esc(x.story)}</td>
    <td>${fmt(x.v * 1000, 3)}</td>
    <td><b>${fmt(x.pct, 1)} %</b></td>
    <td class="txt"><span class="vw-bar-wrap"><span class="vw-bar"
      style="--w:${((x.v / (vmax || 1)) * 100).toFixed(1)}%; --c:${vwColor(x.v / (vmax || 1))}"></span></span></td>
  </tr>`).join("");
  table.innerHTML = head + `<tbody>${body}</tbody>`;
  table.querySelectorAll("tr.vw-row").forEach(tr =>
    tr.addEventListener("click", () => selectMemberFrom3D(tr.dataset.uid)));

  $("vwNote").textContent =
    `Top ${top.length} of ${rows.length} members by virtual-work share of the ` +
    `${res.direction || store.vwDir}-direction roof displacement under ${caseLabel(res.case || "")}. ` +
    `Σ contributions ${fmt(total * 1000, 2)} mm vs roof displacement ${fmt(roof * 1000, 2)} mm — ` +
    "the two should match. The 3D view colors every contributing member (legend: drift energy share).";
}

/** Push the drift-share colors + legend to the 3D viewer (falsy result clears). */
function applyVwColors() {
  const res = store.vwResult;
  const legend = $("driftLegend");
  if (!res || !res.contributions || !Object.keys(res.contributions).length) {
    // v0.20 — an active vibration pulse owns the member colors
    if (!vibPulseTimer) viewer.setMemberColors(null);
    legend.classList.add("hidden");
    return;
  }
  clearVibTimer();                 // v0.20 — vw coloring supersedes the pulse
  const vals = Object.values(res.contributions);
  const vmax = Math.max(...vals) || 1;
  const map = {};
  for (const [uid, v] of Object.entries(res.contributions)) map[uid] = vwColor(v / vmax);
  viewer.setMemberColors(map);
  const total = res.total || vals.reduce((a, b) => a + b, 0) || 1;
  $("vwLegendMax").textContent = `${fmt((vmax / total) * 100, 1)} %`;
  $("vwLegendCase").textContent =
    `${caseLabel(res.case || "")} · ${res.direction || store.vwDir}`;
  legend.classList.remove("hidden");
}

/* ================================================================
   v0.12 — AUTO SECTION OPTIMIZATION (Design → Steel)
   ================================================================ */

/** The optimize panel lives in the Steel sub-tab: a target-ratio input, a
    case/combo selector (reusing the design case list), Optimize + Apply. */
function renderOptimizePanel() {
  const panel = $("optimizePanel");
  const steel = store.designKind === "steel";
  panel.classList.toggle("hidden", !steel || !store.results);
  if (!steel || !store.results) return;

  const opts = designCaseOptions();
  if (!store.optCase || !opts.includes(store.optCase))
    store.optCase = store.designCase && opts.includes(store.designCase)
      ? store.designCase : (opts[0] || null);
  const form = $("optimizeForm");
  const hasSugg = !!(store.optResult && store.optResult.suggestions);
  const canApply = hasSugg && store.optResult.suggestions.some(s => s.status === "ok");
  form.innerHTML = `<div class="design-form-row">
    <label class="rs-field"><span>case / combo</span>
      <select id="optCaseSelect">${opts.map(n =>
        `<option value="${esc(n)}"${n === store.optCase ? " selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
    <label class="rs-field"><span>target D/C</span>
      <input id="optTarget" type="number" min="0.1" max="2" step="0.05" value="${store.optTarget}"></label>
    <button class="btn btn-run design-check" id="optRunBtn">
      <span class="spinner hidden" id="optSpinner"></span><span>Optimize</span></button>
    <button class="btn design-check" id="optApplyBtn"${canApply ? "" : " disabled"}
      title="${canApply ? "Adopt the suggested sections into the model" : "Run an optimization with downsizable members first"}">Apply suggestions</button>
  </div>
  <p class="muted design-note">Suggests lighter sections that keep each member's demand/capacity at or below the target ratio (POST /api/design/optimize). Braces are excluded.</p>`;

  $("optCaseSelect").addEventListener("change", e => { store.optCase = e.target.value; });
  $("optTarget").addEventListener("change", e => {
    const v = parseFloat(e.target.value);
    if (isFinite(v) && v > 0) store.optTarget = v;
  });
  $("optRunBtn").addEventListener("click", runOptimize);
  $("optApplyBtn").addEventListener("click", applyOptimize);
  renderOptimizeTable();
}

async function runOptimize() {
  const btn = $("optRunBtn");
  if (btn.disabled) return;
  btn.disabled = true;
  $("optSpinner").classList.remove("hidden");
  try {
    store.optResult = await designOptimize({
      case: store.optCase, target_ratio: store.optTarget, apply: false,
    });
    const s = optSummary();
    toast("Optimization complete",
      `${s.n} member${s.n === 1 ? "" : "s"} · ${s.downsized} downsized · ${fmt(s.totalWeight, 1)} kg/m`,
      "info", 5000);
    renderOptimizePanel();      // re-render enables Apply + fills the table
  } catch (err) {
    toast("Optimization failed", err.message, "error", 8000);
  } finally {
    btn.disabled = false;
    $("optSpinner").classList.add("hidden");
  }
}

async function applyOptimize() {
  const btn = $("optApplyBtn");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  const s = optSummary();                   // capture before optResult is cleared
  try {
    const updated = await designOptimize({
      case: store.optCase, target_ratio: store.optTarget, apply: true,
    });
    // adopt the returned model dict (members now carry the suggested sections)
    adoptModel(ME.normalizeModel(updated));
    store.steelResult = null;               // sections changed → checks stale
    store.optResult = null;
    toast("Sections applied",
      `${s.downsized} member${s.downsized === 1 ? "" : "s"} reassigned · re-run analysis to re-check`,
      "info", 6000);
    renderDesignForm();
    renderDesignTable();
    renderOptimizePanel();
  } catch (err) {
    toast("Apply failed", err.message, "error", 8000);
    btn.disabled = false;
  }
}

/** Summary numbers over the current suggestions: total members, count of
    downsized (lighter) members, and the net weight change (kg/m). */
function optSummary() {
  const sugg = (store.optResult && store.optResult.suggestions) || [];
  let downsized = 0, totalWeight = 0, nsp = 0, na = 0;
  for (const s of sugg) {
    if (s.status === "ok") { downsized++; totalWeight += s.weight_kg_per_m || 0; }
    else if (s.status === "no_section_passes") nsp++;
    else na++;
  }
  return { n: sugg.length, downsized, nsp, na, totalWeight };
}

const OPT_COLS = [
  { key: "uid", label: "Member", txt: true },
  { key: "current_section", label: "Current", txt: true },
  { key: "suggested_section", label: "Suggested", txt: true },
  { key: "current_ratio", label: "D/C now" },
  { key: "suggested_ratio", label: "D/C new" },
  { key: "weight_kg_per_m", label: "Δ weight kg/m" },
  { key: "status", label: "Status", txt: true },
];

function optimizeRows() {
  const sugg = (store.optResult && store.optResult.suggestions) || [];
  const { key, dir } = store.optSort;
  return [...sugg].sort((a, b) => {
    const va = a[key], vb = b[key];
    if (typeof va === "string") return String(va).localeCompare(String(vb), undefined, { numeric: true }) * dir;
    return ((va || 0) - (vb || 0)) * dir;
  });
}

function renderOptimizeTable() {
  const summary = $("optSummary");
  const table = $("optimizeTable");
  const csv = $("csvOptimize");
  if (!store.optResult || !store.optResult.suggestions) {
    summary.classList.add("hidden");
    csv.classList.add("hidden");
    table.innerHTML = `<tbody><tr><td class="txt dim">No optimization yet — set a target ratio and press “Optimize”.</td></tr></tbody>`;
    return;
  }
  const s = optSummary();
  const tgt = store.optResult.target_ratio ?? store.optTarget;
  summary.classList.remove("hidden");
  csv.classList.remove("hidden");
  const wCls = s.totalWeight < -0.05 ? "opt-lighter" : s.totalWeight > 0.05 ? "opt-heavier" : "";
  summary.innerHTML =
    `<span class="ds-item"><b>${s.n}</b> members</span>` +
    `<span class="ds-item ds-ok"><b>${s.downsized}</b> downsized</span>` +
    `<span class="ds-item"><b>${s.nsp}</b> no lighter section</span>` +
    (s.na ? `<span class="ds-item ds-na"><b>${s.na}</b> N/A</span>` : "") +
    `<span class="ds-item">total weight change <b class="${wCls}">${fmt(s.totalWeight, 1)} kg/m</b></span>` +
    `<span class="ds-item ds-prelim">target D/C ${fmt(tgt, 2)} · ${esc(store.optResult.case || "")}</span>`;

  const { key: sk, dir } = store.optSort;
  const head = `<thead><tr>` + OPT_COLS.map(c =>
    `<th class="sortable ${c.txt ? "txt" : ""}" data-key="${c.key}">${c.label}` +
    (c.key === sk ? `<span class="sort-arrow">${dir > 0 ? "▲" : "▼"}</span>` : "") +
    `</th>`).join("") + `</tr></thead>`;
  const ratioChip = r => {
    const cls = r > tgt ? "rc-over" : r > tgt * 0.85 ? "rc-near" : "rc-ok";
    return `<span class="ratio-chip ${cls}">${fmt(r, 3)}</span>`;
  };
  const statChip = st => {
    const map = { ok: ["st-ok", "downsized"], no_section_passes: ["st-warn", "no lighter"], "n/a": ["st-na", "N/A"] };
    const [cls, label] = map[st] || ["st-na", st];
    return `<span class="status-chip ${cls}">${esc(label)}</span>`;
  };
  const rows = optimizeRows();
  const body = rows.map(x => {
    const wc = (x.weight_kg_per_m || 0);
    const wcls = wc < -0.05 ? "opt-lighter" : wc > 0.05 ? "opt-heavier" : "dim";
    const arrow = x.status === "ok"
      ? `<span class="opt-arrow">→</span> ${esc(x.suggested_section)}`
      : `<span class="dim">${esc(x.suggested_section)}</span>`;
    return `<tr data-uid="${esc(x.uid)}" class="opt-row${x.status === "ok" ? " is-ok" : ""}">
      <td class="txt">${esc(x.uid)}</td>
      <td class="txt dim">${esc(x.current_section)}</td>
      <td class="txt">${arrow}</td>
      <td>${ratioChip(x.current_ratio)}</td>
      <td>${ratioChip(x.suggested_ratio)}</td>
      <td class="${wcls}"><b>${wc > 0 ? "+" : ""}${fmt(wc, 1)}</b></td>
      <td class="txt">${statChip(x.status)}</td></tr>`;
  }).join("");
  table.innerHTML = head + `<tbody>${body}</tbody>`;

  table.querySelectorAll("th.sortable").forEach(th =>
    th.addEventListener("click", () => {
      const k = th.dataset.key;
      if (store.optSort.key === k) store.optSort.dir *= -1;
      else store.optSort = { key: k, dir: (k === "uid" || k === "current_section" || k === "suggested_section" || k === "status") ? 1 : -1 };
      renderOptimizeTable();
    }));
  // row → select the member in 3D
  table.querySelectorAll("tr.opt-row").forEach(tr =>
    tr.addEventListener("click", () => selectMemberFrom3D(tr.dataset.uid)));
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
    const cmcr = hasCmCr();
    // v0.9 — diagnostics for the selected lateral case (if any)
    const diag = hasDiagnostics();
    const dcase = diag ? store.diagCase : null;
    const stiff = diag ? (r.story_stiffness[dcase] || {}) : {};
    const irr = diag ? (r.irregularity[dcase] || {}) : {};
    return [
      ["story", "elev_m", "ux_m", "uy_m", "drift_x", "drift_y", "shear_x_kN", "shear_y_kN",
        ...(cmcr ? ["cm_x_m", "cm_y_m", "cr_x_m", "cr_y_m", "ecc_m"] : []),
        ...(diag ? [`kx_kN_m(${dcase})`, "ky_kN_m", "tors_ratio_x", "tors_ratio_y",
          "tors_flag", "stiff_ratio", "soft_flag"] : [])],
      ...[...r.story_order].reverse().map(s => {
        const st = (cd.story && cd.story[s]) || {};
        const base = [s, r.story_elev[s], st.ux || 0, st.uy || 0,
          st.drift_x || 0, st.drift_y || 0, st.shear_x || 0, st.shear_y || 0];
        if (cmcr) {
          const cc = storyCmCr(s);
          const ecc = cc ? Math.hypot((cc.cm_x ?? 0) - (cc.cr_x ?? 0), (cc.cm_y ?? 0) - (cc.cr_y ?? 0)) : "";
          base.push(cc ? cc.cm_x ?? "" : "", cc ? cc.cm_y ?? "" : "",
            cc ? cc.cr_x ?? "" : "", cc ? cc.cr_y ?? "" : "", cc ? ecc : "");
        }
        if (diag) {
          const k = stiff[s] || {}, ir = irr[s] || {};
          base.push(k.kx ?? "", k.ky ?? "", ir.tors_ratio_x ?? "", ir.tors_ratio_y ?? "",
            ir.flag ?? "", ir.stiff_ratio ?? "", ir.soft_flag ?? "");
        }
        return base;
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
  if (kind === "design") {
    const res = designResult();
    if (!res) return null;
    const env = !!res.envelope;
    const hasMethod = res.checks.some(c => c.method);   // v0.16 — biaxial columns
    return [
      ["member", "kind", "section", "Pu_kN", "Mu33_kNm", "Mu22_kNm",
        "phiPn_kN", "phiMn33_kNm", "phiMn22_kNm", "ratio",
        ...(hasMethod ? ["method", "ratio_biaxial"] : []), "equation",
        ...(env ? ["governing_combo"] : []), "status", "notes"],
      ...designRows().map(x => [x.uid, x.kind, x.section, x.Pu, x.Mu33, x.Mu22,
        x.phiPn, x.phiMn33, x.phiMn22, x.ratio,
        ...(hasMethod ? [x.method || "uniaxial", x.ratio_biaxial ?? ""] : []), x.equation,
        ...(env ? [x.governing_combo || ""] : []), x.status, x.notes]),
    ];
  }
  if (kind === "wall") {
    const res = store.wallResult;
    if (!res || !res.piers) return null;
    const out = [["pier", "story", "P_kN", "V_kN", "M_kNm", "ratio_pmm",
      "ratio_shear", "phiVn_kN", "sigma_max_kPa", "boundary_required", "status", "combo"]];
    for (const g of wallRows())
      for (const x of g.stories)
        out.push([g.pier, x.story, x.P, x.V, x.M, x.ratio_pmm, x.ratio_shear,
          x.phiVn, x.sigma_max, x.boundary_required ? "true" : "false", x.status, x.combo || ""]);
    return out;
  }
  if (kind === "punching") {
    if (!store.punchResult || !store.punchResult.columns) return null;
    return [
      ["column", "story", "Vu_kN", "vu_kPa", "phi_vc_kPa", "b0_m", "d_m", "ratio", "status", "case"],
      ...punchRows().map(x => [x.uid, x.story, x.Vu, x.vu, x.phi_vc,
        x.b0, x.d, x.ratio, x.status, x.case || ""]),
    ];
  }
  if (kind === "composite") {
    if (!store.compositeResult || !store.compositeResult.beams) return null;
    return [
      ["beam", "story", "applicable", "reason", "beff_m", "tc_m", "phiMn_full_kNm",
        "n_studs", "sumQn_kN", "ratio_composite", "phiMn_partial_kNm", "Mu_kNm",
        "ratio", "precomp_ratio", "I_equiv_m4", "defl_LL_m", "defl_limit_ok", "status"],
      ...compositeRows().map(x => [x.uid, x.story ?? "",
        x.applicable === false ? "false" : "true", x.reason || "",
        x.beff ?? "", x.tc ?? "", x.phiMn_full ?? "", x.n_studs ?? "", x.sumQn ?? "",
        x.ratio_composite ?? "", x.phiMn_partial ?? "", x.Mu ?? "", x.ratio ?? "",
        x.precomp_ratio ?? "", x.I_equiv ?? "", x.defl_LL ?? "",
        x.defl_limit_ok == null ? "" : String(!!x.defl_limit_ok), x.status ?? ""]),
    ];
  }
  if (kind === "slab") {
    if (!store.slabResult || !Array.isArray(store.slabResult.regions)) return null;
    return [
      ["region", "dir", "strip", "x_m", "Mu_kNm_per_m", "As_req_mm2_per_m",
        "As_min_mm2_per_m", "spacing_mm", "status"],
      ...slabSectionRows().map(s => [s.region, s.dir, s.strip, s.x ?? "",
        s.Mu ?? "", asMm2(s.As_req) ?? "", asMm2(s.As_min) ?? "",
        asMm(s.spacing) ?? "", s.status ?? ""]),
    ];
  }
  if (kind === "vibration") {
    if (!store.vibResult || !store.vibResult.beams) return null;
    return [
      ["beam", "story", "fn_Hz", "delta_mid_m", "W_eff_kN", "ap_over_g", "limit", "status"],
      ...vibRows().map(x => [x.uid, x.story ?? "", x.fn ?? "", x.delta_mid ?? "",
        x.W_eff ?? "", x.ap_over_g ?? "", x.limit ?? "", x.status ?? ""]),
    ];
  }
  if (kind === "svc") {
    const list = svcData();
    if (!list) return null;
    return [
      ["beam", "story", "L_m", "max_abs_dy_m", "max_abs_dy_mm", "ratio", "limit", "ok"],
      ...svcRows(list).map(x => [x.uid, x.story, x.L, x.max_abs_dy,
        x.dyMm, x.ratio_str, x.limit, x.ok ? "OK" : "NG"]),
    ];
  }
  if (kind === "livered") {
    if (!store.llrData) return null;
    return [
      ["column", "story", "KLL", "At_m2", "R"],
      ...llrRows().map(x => [x.uid, x.story, x.KLL, x.At, x.R]),
    ];
  }
  if (kind === "optimize") {
    if (!store.optResult || !store.optResult.suggestions) return null;
    const s = optSummary();
    return [
      ["member", "current_section", "suggested_section", "current_ratio",
        "suggested_ratio", "weight_change_kg_per_m", "status"],
      ...optimizeRows().map(x => [x.uid, x.current_section, x.suggested_section,
        x.current_ratio, x.suggested_ratio, x.weight_kg_per_m, x.status]),
      ["TOTAL", "", "", "", "", +s.totalWeight.toFixed(1), `${s.downsized} downsized`],
    ];
  }
  if (kind === "buckling") {
    const bd = buckData();
    if (!bd) return null;
    return [
      ["mode", "lambda_critical_factor", "note"],
      ...(bd.factors || []).map((lam, i) => [
        i + 1, lam, lam < 1 ? "buckles below applied gravity" : "",
      ]),
    ];
  }
  if (kind === "takedown") {
    const td = tdData();
    if (!td) return null;
    const rows = tdRows(td);
    return [
      ["grid", "node", "x_m", "y_m", "FZ_kN", "FX_kN", "FY_kN"],
      ...rows.map(s => [s.grid || "", s.node, s.x, s.y, s.FZ, s.FX, s.FY]),
      ["TOTAL", "", "", "", td.total_FZ || 0,
        rows.reduce((a, s) => a + (s.FX || 0), 0), rows.reduce((a, s) => a + (s.FY || 0), 0)],
      ["APPLIED_FZ", "", "", "", td.applied_FZ || 0, "", ""],
      ["BALANCE_OK", "", "", "", td.balance_ok ? "true" : "false", "", ""],
    ];
  }
  if (kind === "cuts") {
    const cd = cutData();
    if (!cd) return null;
    return [
      ["cut", "axis", "coord_m", "FX_kN", "FY_kN", "FZ_kN",
        "MX_kNm", "MY_kNm", "MZ_kNm", "n_members", "n_shells", "warnings"],
      ...cutRows(cd).map(row => {
        const d = row.def || {};
        return [row.name, d.axis || "", d.coord ?? "", row.FX, row.FY, row.FZ,
          row.MX, row.MY, row.MZ, row.n_members ?? 0, row.n_shells ?? 0,
          (row.warnings || []).join("; ")];
      }),
    ];
  }
  if (kind === "piers") {
    const pd = pierData();
    if (!pd) return null;
    const out = [["pier", "story", "elev_m", "P_kN", "V_kN", "M_kNm"]];
    for (const g of pierRows(pd))
      for (const s of g.stories) out.push([g.pier, s.story, s.elev, s.P, s.V, s.M]);
    return out;
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
  const caseless = kind === "modal" || kind === "livered" || kind === "vibration";
  const caseTag = kind === "th" ? store.thCase
    : kind === "svc" ? store.svcCase
    : kind === "pushover" ? store.poCase
    : kind === "buckling" ? store.buckCase
    : kind === "takedown" ? store.tdCase
    : kind === "cuts" ? store.cutCase
    : kind === "piers" ? store.pierCase
    : kind === "optimize" ? `${store.optResult?.case || store.optCase || ""}`
    : kind === "wall" ? `${store.wallResult?.params?.combos?.length ?? "all"}combos`
    : kind === "punching" ? `${store.punchResult?.columns?.[0]?.case || store.punchCase || ""}`
    : kind === "composite" ? `${store.compositeResult?.params?.combos?.length ?? "all"}combos`
    : kind === "slab" ? `${store.slabResult?.params?.case || store.slabCase || ""}`
    : kind === "design" ? `${store.designKind}-${designResult()?.case || ""}`
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
  // v0.10 — buckling mode-shape animation controls
  $("buckModeGroup").hidden = !o.buckling;
  $("legendDeformed").classList.toggle("hidden", !(o.deformed || o.modal || o.buckling));
  $("envBadge").classList.toggle("hidden", !(o.deformed && isRsCase(store.caseName)));
  if (o.modal && store.results && store.results.modal) {
    const T = store.results.modal.periods[o.modeIndex];
    const f = store.results.modal.frequencies[o.modeIndex];
    $("modePeriodBadge").textContent = `T = ${fmt(T, 3)} s · ${fmt(f, 2)} Hz`;
  }
  if (o.buckling) {
    const bk = store.results && store.results.buckling && store.results.buckling[o.bucklingCase];
    const lam = bk && bk.factors ? bk.factors[o.modeIndex] : null;
    $("buckFactorBadge").textContent =
      `${esc(o.bucklingCase || "")} · λ = ${lam != null ? fmt(lam, 3) : "—"}`;
  }
  viewer.setOverlay({
    deformed: o.deformed, modal: o.modal,
    buckling: o.buckling, bucklingCase: o.bucklingCase,
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
    store.steelResult = null;                       // v0.6 — forces changed
    store.concreteResult = null;
    store.wallResult = null;                        // v0.18 — forces changed
    store.punchResult = null;
    store.vwResult = null;
    store.perf = {};                                // v0.19 — curves changed
    store.compositeResult = null;                   // v0.20 — forces changed
    store.slabResult = null;
    store.vibResult = null;
    clearVibTimer();
    viewer.setMemberColors(null);
    if (!caseNames().includes(store.caseName)) store.caseName = null;
    rebuildCaseSelect();
    rebuildModeSelect();
    rebuildThSelects();                            // v0.4
    rebuildPoSelect();                             // v0.5
    rebuildBuckSelect();                           // v0.10
    rebuildTdSelect();                             // v0.11
    rebuildSvcSelect();                            // v0.16
    $("svcLimitNote").classList.add("hidden");     // v0.16 — fresh checks
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
  $("perfRunBtn").addEventListener("click", runPerformancePoint);        // v0.19

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
  /* ---- v0.17: panel-zone joint model (round-trips via POST /api/model) */
  $("panelZoneSelect").addEventListener("change", e => {
    const v = e.target.value;
    store.model.panel_zones = (v === "rigid" || v === "scissors") ? v : "none";
    syncPanelZoneUI();
    markDirty();
    refreshDrawViews();          // plan joint glyphs follow the choice live
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
  /* ---- v0.14: grid-system manager (add orthogonal / radial systems) */
  $("addGridSysOrtho").addEventListener("click", () => {
    const sys = ME.addGridSystem(store.model, "orthogonal");
    store.gridSel = ME.gridSystems(store.model).indexOf(sys);
    afterGridSysEdit();
    toast("Grid system added", `“${sys.name}” · orthogonal`, "info", 3000);
  });
  $("addGridSysRadial").addEventListener("click", () => {
    const sys = ME.addGridSystem(store.model, "radial");
    store.gridSel = ME.gridSystems(store.model).indexOf(sys);
    afterGridSysEdit();
    toast("Grid system added", `“${sys.name}” · radial`, "info", 3000);
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
    else if (item.dataset.act === "import") openImportDialog();
    else if (item.dataset.act === "open") openFileDialog();
    else if (item.dataset.act === "save") fileSave();
    else if (item.dataset.act === "saveas") openSaveAs();
  });

  /* ---- v0.6: template gallery */
  $("galleryClose").addEventListener("click", () => hideModal("galleryModal"));
  $("galleryCancel").addEventListener("click", () => hideModal("galleryModal"));
  $("galleryModal").addEventListener("click", e => { if (e.target === $("galleryModal")) hideModal("galleryModal"); });

  /* ---- v0.6: import dialog */
  document.querySelectorAll("#importFmtTabs .seg-btn").forEach(b =>
    b.addEventListener("click", () => setImportFmt(b.dataset.fmt)));
  $("importBrowse").addEventListener("click", () => $("importFile").click());
  $("importDrop").addEventListener("click", e => {
    if (e.target.closest("button")) return;
    $("importFile").click();
  });
  $("importFile").addEventListener("change", e => readImportFile(e.target.files[0]));
  const drop = $("importDrop");
  ["dragover", "dragenter"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.remove("dragging");
  }));
  drop.addEventListener("drop", e => {
    if (e.dataTransfer && e.dataTransfer.files[0]) readImportFile(e.dataTransfer.files[0]);
  });
  $("importAddStory").addEventListener("click", () => {
    store.importStories.push(3.2);
    renderImportStories();
  });
  $("importDo").addEventListener("click", doImport);
  $("importClose").addEventListener("click", () => hideModal("importModal"));
  $("importCancel").addEventListener("click", () => hideModal("importModal"));
  $("importModal").addEventListener("click", e => { if (e.target === $("importModal")) hideModal("importModal"); });

  /* ---- v0.6: design tab controls */
  document.querySelectorAll("#designKindToggle .seg-btn").forEach(b =>
    b.addEventListener("click", () => setDesignKind(b.dataset.dk)));
  $("designFilter").addEventListener("input", e => {
    store.designFilter = e.target.value;
    renderDesignTable();
  });
  $("csvDesign").addEventListener("click", () => downloadCsv("design"));
  $("csvOptimize").addEventListener("click", () => downloadCsv("optimize"));
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

  /* ---- v0.21: section designer dialog */
  $("openDesignerBtn").addEventListener("click", () => openSectionDesigner());
  $("designerClose").addEventListener("click", () => sectionDesigner.close());
  $("designerDone").addEventListener("click", () => sectionDesigner.close());
  $("designerModal").addEventListener("click", e => {
    if (e.target === $("designerModal")) sectionDesigner.close();
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
    if (store.overlay.deformed) { store.overlay.modal = false; store.overlay.buckling = false; store.contour.on = false; }
    syncOverlayUI();
    syncContoursUI();
  });
  $("chipMode").addEventListener("click", () => {
    store.overlay.modal = !store.overlay.modal;
    if (store.overlay.modal) { store.overlay.deformed = false; store.overlay.buckling = false; store.contour.on = false; }
    syncOverlayUI();
    syncContoursUI();
  });
  $("chipContours").addEventListener("click", () => {
    store.contour.on = !store.contour.on;
    if (store.contour.on) {
      store.overlay.deformed = false;
      store.overlay.modal = false;
      store.overlay.buckling = false;
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
  // v0.10 — buckling case + mode-shape animation selectors
  $("buckCaseSelect").addEventListener("change", e => {
    store.buckCase = e.target.value;
    if (store.overlay.buckling) {
      store.overlay.bucklingCase = store.buckCase;
      store.overlay.modeIndex = 0;
      rebuildBuckModeSelect();
    }
    renderBucklingTab();
    syncOverlayUI();
  });
  $("buckModeSelect").addEventListener("change", e => {
    store.overlay.modeIndex = parseInt(e.target.value, 10);
    renderBucklingTab();
    syncOverlayUI();
  });
  $("csvBuck").addEventListener("click", () => downloadCsv("buckling"));

  // v0.11 — load takedown case selector + CSV
  $("tdCaseSelect").addEventListener("change", e => {
    store.tdCase = e.target.value;
    renderTakedownTab();
  });
  $("csvTakedown").addEventListener("click", () => downloadCsv("takedown"));

  // v0.13 — section-cut-forces case selector + CSV
  $("cutCaseSelect").addEventListener("change", e => {
    store.cutCase = e.target.value;
    store.cutSel = null;
    if (viewer) viewer.setHighlight(null);
    renderCutsTab();
  });
  $("csvCuts").addEventListener("click", () => downloadCsv("cuts"));

  // v0.15 — wall-piers case selector + CSV
  $("pierCaseSelect").addEventListener("change", e => {
    store.pierCase = e.target.value;
    renderPiersTab();
  });
  $("csvPiers").addEventListener("click", () => downloadCsv("piers"));

  // v0.16 — serviceability tab: case selector, CSV, editable deflection limit
  $("svcCaseSelect").addEventListener("change", e => {
    store.svcCase = e.target.value;
    renderSvcTab();
  });
  $("csvSvc").addEventListener("click", () => downloadCsv("svc"));
  $("svcLimitInput").addEventListener("change", e => {
    const v = parseFloat(e.target.value);
    if (!(isFinite(v) && v > 0)) {
      e.target.value = String(store.model.deflection_limit ?? 360);
      return;
    }
    store.model.deflection_limit = Math.round(v);
    markDirty();
    $("svcLimitNote").classList.remove("hidden");
    renderSvcTab();
    toast("Deflection limit updated",
      `model.deflection_limit = ${Math.round(v)} — re-run the analysis to refresh the checks`,
      "info", 5000);
  });

  // v0.20 — floor-vibration card (Serviceability tab): params, run, CSV
  $("vibRunBtn").addEventListener("click", runVibration);
  $("csvVib").addEventListener("click", () => downloadCsv("vibration"));
  $("vibLimitSel").addEventListener("change", e => {
    $("vibCustomWrap").classList.toggle("hidden", e.target.value !== "custom");
  });

  // v0.16 — live-load reduction toggle + factor CSV (Design tab)
  $("llrToggle").addEventListener("change", e => { toggleLlr(e.target.checked); });
  $("csvLlr").addEventListener("click", () => downloadCsv("livered"));

  // v0.18 — drift-optimizer controls (case, direction, run, clear)
  $("vwCaseSelect").addEventListener("change", e => { store.vwCase = e.target.value; });
  document.querySelectorAll("#vwDirToggle .seg-btn").forEach(b =>
    b.addEventListener("click", () => {
      store.vwDir = b.dataset.dir === "Y" ? "Y" : "X";
      document.querySelectorAll("#vwDirToggle .seg-btn").forEach(x =>
        x.classList.toggle("is-active", x === b));
    }));
  $("vwRunBtn").addEventListener("click", runVirtualWork);
  $("vwClearBtn").addEventListener("click", clearVwColors);

  // drift limit
  $("driftLimitInput").addEventListener("change", e => {
    const v = parseFloat(e.target.value);
    if (isFinite(v) && v > 0) { store.driftLimitPct = v; renderStoryTab(); }
  });

  // v0.8 — CM/CR plan story selector
  $("cmStorySelect").addEventListener("change", e => {
    store.cmStory = e.target.value;
    renderCmCrBlock();
  });

  // v0.9 — story-diagnostics lateral-case selector
  $("diagCaseSelect").addEventListener("change", e => {
    store.diagCase = e.target.value;
    renderDiagBlock();
  });

  // forces filter
  $("forcesFilter").addEventListener("input", e => {
    store.forcesFilter = e.target.value;
    renderForcesTab();
  });

  // keyboard
  const TABS = ["view3d", "story", "modal", "reactions", "forces", "design", "drift", "th", "pushover", "buckling", "takedown", "cuts", "piers"];
  const TOOL_KEYS = { v: "select", c: "column", b: "beam", x: "brace", w: "wall", s: "slab", l: "link", g: "spring", e: "erase" };
  document.addEventListener("keydown", e => {
    const tag = (e.target.tagName || "").toLowerCase();
    // v0.3 dialogs respond to Escape even while an input has focus
    if (e.key === "Escape") {
      if (confirmResolve) { settleConfirm(false); return; }
      if (!$("designerModal").classList.contains("hidden")) { sectionDesigner.handleEscape(); return; }
      if (!$("importModal").classList.contains("hidden")) { $("importModal").classList.add("hidden"); return; }
      if (!$("galleryModal").classList.contains("hidden")) { $("galleryModal").classList.add("hidden"); return; }
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

    if (e.key >= "1" && e.key <= "9") {
      const t = TABS[+e.key - 1];
      const hidden = (t === "th" && $("thTabBtn").classList.contains("hidden")) ||
        (t === "pushover" && $("poTabBtn").classList.contains("hidden")) ||
        (t === "buckling" && $("buckTabBtn").classList.contains("hidden")) ||
        (t === "takedown" && $("tdTabBtn").classList.contains("hidden")) ||
        (t === "cuts" && $("cutTabBtn").classList.contains("hidden")) ||
        (t === "piers" && $("pierTabBtn").classList.contains("hidden"));
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
    getHalos: () => punchHalos(),                  // v0.18 punching D/C > 1 rings
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
  sectionDesigner = new SectionDesigner({          // v0.21
    getModel: () => store.model,
    toast,
    onUpsert: section => applyDesignerAction("upsert", section),
    onDelete: name => applyDesignerAction("delete", { name }),
    onPmm: (name, axis) => designerPmmFetch(name, axis),
    isSectionInUse: name => ME.sectionInUse(store.model, name),
    onClose: () => { renderSectionMgr(); renderStaticViews(); renderProps(); },
  });
  loadsEditor = new LoadsEditor($("loadsPane"), {
    getModel: () => store.model,
    onChange: markDirty,
    toast,
    onWind: generateWindPattern,                   // v0.4
    // v0.7 — ASCE 7-16 code tools
    onSelfWeight: addSelfWeightPattern,
    onAutoCombos: generateAsce7Combos,
    onCodeRs: createCodeRsCase,
    onElf: createElfPattern,
    // v0.10 — RS directional combination + notional loads
    onRsDirectional: createRsDirectional,
    onNotional: createNotionalPattern,
    // v0.19 — pattern live loading + auto construction sequence
    onPatternLive: generatePatternLive,
    onAutoSequence: createAutoSequence,
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
    // v0.7 — code tools
    addSelfWeightPattern, generateAsce7Combos, createCodeRsCase, createElfPattern,
    // v0.5
    elevEditor, setView, setElevLine, elevPlane, rebuildElevSelect,
    handleElevDraw, renderPoTab, rebuildPoSelect, poData,
    renderOpeningPreview, syncDiaphragmUI,
    // v0.6
    setDesignKind, runDesignCheck, renderDesignForm, renderDesignTable,
    designResult, designRows, designCheck, isStagedCase, syncStagedBadge,
    openImportDialog, doImport, setImportFmt, readImportFile, importModelFile,
    renderImportStories, openGallery, pickTemplate, TEMPLATES,
    selectMemberFrom3D, renderThTab,
    // v0.8
    renderStoryTab, renderCmCrBlock, storyCmCr, hasCmCr, renderStoryCharts,
    // v0.9
    renderDiagBlock, diagCaseNames, hasDiagnostics,
    // v0.10 — buckling, RS directional, notional
    renderBucklingTab, rebuildBuckSelect, buckData, viewBucklingModeIn3D,
    createRsDirectional, createNotionalPattern, mockRsDirectional, mockNotionalPattern,
    // v0.11 — elastic foundation + load takedown
    renderTakedownTab, rebuildTdSelect, tdData, tdRows, takedownBubbleSvg,
    // v0.13 — section cuts + function library
    renderCutsTab, rebuildCutSelect, cutData, cutRows, cutCaseNames, membersCrossingCut,
    // v0.12 — auto section optimization + axial-limit behavior
    renderOptimizePanel, runOptimize, applyOptimize, optimizeRows, optSummary,
    renderOptimizeTable, designOptimize, mockOptimize,
    // v0.15 — link device types + wall piers
    renderPiersTab, rebuildPierSelect, pierData, pierRows, pierCaseNames,
    pierSparkSvg,
    // v0.16 — deflection diagrams, serviceability, live-load reduction, biaxial
    renderSvcTab, rebuildSvcSelect, svcData, svcRows, svcCaseNames,
    fetchLiveReduction, renderLlrPanel, toggleLlr, llrRows, mockLiveReduction,
    designGovRatio,
    // v0.17 — vertical seismic Ev in auto-combos + panel zones
    syncPanelZoneUI, mockAsce7Combos,
    // v0.18 — wall design, punching check, drift optimizer
    renderWallPanel, runWallCheck, wallRows, wallGovRatio, designWall,
    renderPunchPanel, runPunchCheck, punchRows, punchHalos, syncPunchHalos,
    designPunching, renderDriftPanel, runVirtualWork, clearVwColors, vwRows,
    applyVwColors, vwColor, fetchVirtualWork,
    mockDesignWall, mockDesignPunching, mockVirtualWork,
    // v0.19 — ASCE 41 hinges, performance point, pattern live, sequence
    runPerformancePoint, renderPerfOut, generatePatternLive,
    createAutoSequence, mockPatternLive, mockAutoSequence,
    mockPerformancePoint,
    // v0.20 — composite beams, slab design, floor vibration
    renderCompositePanel, runCompositeCheck, compositeRows, compGovRatio,
    designComposite, renderSlabPanel, runSlabDesign, slabSectionRows,
    slabStripSvg, designSlab, renderVibTable, runVibration, vibRows,
    fetchVibration, startVibPulse, stopVibPulse, clearVibTimer,
    mockDesignComposite, mockDesignSlab, mockVibration,
    // v0.21 — section designer, fiber PMM hinges, FP/multilinear links
    sectionDesigner, openSectionDesigner, designerAction, applyDesignerAction,
    designerPmmFetch, mockDesignerUpsert, mockDesignerDelete, mockDesignerPmm,
  };
}

boot();
