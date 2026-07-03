/* SkyFrame app shell — state store, API (with mock fallback), tabs,
   tables, overlay controls. No frameworks. */

import { Viewer3D } from "./viewer3d.js";
import { renderStoryCharts } from "./charts.js";
import { mockModel, mockResults } from "./mock.js";

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
    html += `<br><span class="tt-forces">${esc(store.caseName)}</span> · ` +
      `N ${fmt(N)} · V ${fmt(V)} kN · M ${fmt(M)} kN·m`;
  }
  return html;
}

/* ------------------------------------------------ case selection */
function caseNames() {
  if (!store.results) return [];
  return [...Object.keys(store.results.cases || {}), ...Object.keys(store.results.combos || {})];
}

function caseData() {
  const r = store.results;
  if (!r || !store.caseName) return null;
  return (r.cases && r.cases[store.caseName]) || (r.combos && r.combos[store.caseName]) || null;
}

function rebuildCaseSelect() {
  const sel = $("caseSelect");
  sel.textContent = "";
  const r = store.results;
  if (!r) { $("caseSelectWrap").hidden = true; return; }
  const mkGroup = (label, names) => {
    if (!names.length) return;
    const g = document.createElement("optgroup");
    g.label = label;
    for (const n of names) {
      const o = document.createElement("option");
      o.value = n; o.textContent = n;
      g.appendChild(o);
    }
    sel.appendChild(g);
  };
  mkGroup("Cases", Object.keys(r.cases || {}));
  mkGroup("Combos", Object.keys(r.combos || {}));
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
  $("footerInfo").textContent =
    `${m.members.length} members · ${m.stories.length} stories` +
    (store.lastSolveMs != null ? ` · solved in ${fmt(store.lastSolveMs / 1000, 1)} s` : "");
}

function setResultsAvailable(on) {
  for (const t of ["story", "modal", "reactions", "forces"]) {
    $(`empty-${t}`).classList.toggle("hidden", on);
    $(`content-${t}`).classList.toggle("hidden", !on);
  }
  $("chipDeformed").disabled = !on;
  $("chipMode").disabled = !on;
}

function renderResultsTabs() {
  if (!store.results || !caseData()) return;
  renderStoryTab();
  renderModalTab();
  renderReactionsTab();
  renderForcesTab();
}

/* ---- story tab */
function renderStoryTab() {
  const r = store.results, cd = caseData();
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
      <td class="dim">${fmt(cumX * 100, 1)}</td>
      <td class="dim">${fmt(cumY * 100, 1)}</td>
      <td class="txt"><button class="link-3d" data-mode="${i}">view in 3D →</button></td>
    </tr>`;
  }).join("");
  $("modalTable").innerHTML = `<thead><tr>
    <th class="txt">Mode</th><th>T s</th><th>f Hz</th>
    <th>UX %</th><th>UY %</th><th>RZ %</th>
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
  const r = store.results, cd = caseData();
  if (!cd) return;
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
  const r = store.results, cd = caseData();
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
  $("forcesCount").textContent = `${rows.length} members · ${esc(store.caseName || "")}`;
  $("forcesTable").querySelectorAll("th.sortable").forEach(th =>
    th.addEventListener("click", () => {
      const k = th.dataset.key;
      if (store.forcesSort.key === k) store.forcesSort.dir *= -1;
      else store.forcesSort = { key: k, dir: k === "uid" || k === "kind" || k === "story" || k === "section" ? 1 : -1 };
      renderForcesTab();
    }));
}

/* ------------------------------------------------ overlay UI */
function syncOverlayUI() {
  const o = store.overlay;
  $("chipDeformed").classList.toggle("is-on", o.deformed);
  $("chipMode").classList.toggle("is-on", o.modal);
  $("deformedGroup").hidden = !o.deformed;
  $("modeGroup").hidden = !o.modal;
  $("legendDeformed").classList.toggle("hidden", !(o.deformed || o.modal));
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
    store.model = await generateModel(params);
    store.results = null;
    store.overlay = { deformed: false, modal: false, modeIndex: 0, scaleMult: 1 };
    store.lastSolveMs = null;
    viewer.setResults(null);
    viewer.setModel(store.model);
    syncOverlayUI();
    rebuildCaseSelect();
    setResultsAvailable(false);
    renderSummary();
    setStatus("ready", "Ready");
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
    viewer.setResults(results);
    setResultsAvailable(true);
    renderResultsTabs();
    syncOverlayUI();
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
    renderResultsTabs();
    syncOverlayUI();
  });

  // sidebar collapse
  const applySidebar = collapsed => {
    $("sidebar").classList.toggle("collapsed", collapsed);
    $("sidebarToggle").classList.toggle("collapsed", collapsed);
    setTimeout(() => viewer && viewer._resize(), 200);
  };
  $("sidebarToggle").addEventListener("click", () =>
    applySidebar(!$("sidebar").classList.contains("collapsed")));

  // overlay chips
  $("chipDeformed").addEventListener("click", () => {
    store.overlay.deformed = !store.overlay.deformed;
    if (store.overlay.deformed) store.overlay.modal = false;
    syncOverlayUI();
  });
  $("chipMode").addEventListener("click", () => {
    store.overlay.modal = !store.overlay.modal;
    if (store.overlay.modal) store.overlay.deformed = false;
    syncOverlayUI();
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
  const TABS = ["view3d", "story", "modal", "reactions", "forces"];
  document.addEventListener("keydown", e => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (["input", "select", "textarea"].includes(tag)) return;
    if (e.key >= "1" && e.key <= "5") switchTab(TABS[+e.key - 1]);
    else if (e.key === "r" || e.key === "R") doRun();
    else if (e.key === "\\") $("sidebarToggle").click();
    else if (e.key === "f" || e.key === "F") viewer.fit();
  });
}

/* ------------------------------------------------ boot */
async function boot() {
  viewer = new Viewer3D($("viewer3d"), {
    tooltipEl: $("viewerTooltip"),
    getMemberTooltip: memberTooltip,
  });
  wire();
  try {
    store.model = await fetchModel();
    viewer.setModel(store.model);
    renderSummary();
    setResultsAvailable(false);
    setStatus("ready", "Ready");
  } catch (err) {
    setStatus("error", "Error");
    toast("Failed to load model", err.message, "error");
  }
}

boot();
