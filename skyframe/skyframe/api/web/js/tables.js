/* SkyFrame — ETABS "Display > Show Tables…" (analysis results tables).

   Left: tree of every table from POST /api/tables/list, grouped by `group`
   ("Analysis Results > Joint Output > Displacements" …). Right: a virtualised
   grid for POST /api/tables/<key> {cases?} — only the visible rows are in the
   DOM, so tables with tens of thousands of rows scroll at frame rate. Case
   filter (finished static cases + additive combos; not-run / failed / skipped
   ones listed greyed), text filter, column sorting, unit-converted cells with
   unit labels in the headers (every number goes through units.js), Copy (TSV)
   and Export CSV (converted units, "Label [unit]" headers). The table's
   `warnings` and the last run's case_status / combo_status are shown.

   Also hosts the shared API helper (live fetch / ?mock=1 router) and boots the
   frequency-domain / energy / pushover / load-participation displays
   (js/freqplots.js). Registered from app.js with initTables(window.__sky). */

import U from "./units.js";
import { ICONS, MENUITEM_ICON, EXLEAF_ICON, icon } from "./icons.js";
import { mockTablesList, mockTable, mockLoadParticipation } from "./mock_tables.js";
import { initResultDisplays } from "./freqplots.js";

/* Glyphs for the new menu items / explorer leaves — registered at module
   evaluation (before the ETABS chrome builds its menus). Additive only. */
ICONS["show-tables"] = '<rect x="3" y="4" width="14" height="12" rx="1.2"/><path d="M3 8 H17 M3 12 H17 M8 4 V16"/>';
ICONS["freq-response"] = '<path d="M2.5 15.5 H17.5"/><path d="M3 14 C6 14 7 13 8.5 9 C9.3 6.5 9.8 4 10.5 4 C11.2 4 11.7 6.5 12.5 9 C14 13 15 14 17 14"/>';
MENUITEM_ICON["dis-tables"] = "show-tables";
MENUITEM_ICON["dis-freq"] = "freq-response";
EXLEAF_ICON["Show Tables"] = "show-tables";
EXLEAF_ICON["Frequency Domain"] = "freq-response";

export const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ================================================================
   API — live fetch, or the offline mock router under ?mock=1
   ================================================================ */
export async function skyApi(sky, path, body) {
  const S = sky.store;
  if (S.mock) {
    await new Promise(r => setTimeout(r, 60));
    if (path === "/api/tables/list") return mockTablesList();
    if (path.startsWith("/api/tables/")) return mockTable(S.model, S.results, path.slice(12), body || {});
    if (path === "/api/analyze/load_participation") return mockLoadParticipation(S.model, S.results);
    throw new Error(`no mock for ${path}`);
  }
  const res = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error page */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}

/* ================================================================
   Quantity → units.js kind (+ base decimals of the kN-m view)
   ================================================================ */
const COORD_KEYS = new Set(["x", "y", "z", "X", "Y", "Z", "height", "cm_x", "cm_y", "cr_x", "cr_y"]);
const FIXED = {                          // unitless quantities: units.js "none"
  ratio: ["", 5], factor: ["", 4], percent: ["%", 3],
  circular_frequency: ["rad/s", 4], eigenvalue: ["rad²/s²", 3],
};
/** {kind, unit, dec, text} for a table column. Lengths that are displacements
    (ux, disp_x, max_disp, story drift in m …) use the small-length "disp" kind
    (mm / in); coordinates use the model "length" kind. */
export function colSpec(col) {
  const q = col.quantity || "text";
  if (q === "text" || q === "id") return { kind: null, unit: "", dec: 0, text: true, id: q === "id" };
  if (q === "length") {
    const kind = COORD_KEYS.has(col.key) ? "length" : "disp";
    return { kind, unit: U.label(kind), dec: kind === "disp" ? 3 : 3 };
  }
  const map = { force: ["force", 2], moment: ["moment", 2], stiffness: ["stiffness", 1],
    mass: ["mass", 3], angle: ["rotation", 6], time: ["period", 4], frequency: ["frequency", 4] };
  if (map[q]) return { kind: map[q][0], unit: U.label(map[q][0]), dec: map[q][1] };
  const fx = FIXED[q] || ["", 4];
  return { kind: "none", unit: fx[0], dec: fx[1] };
}
/** Display number (converted) for a cell, or null. */
export function cellValue(spec, v) {
  if (spec.text) return v;
  if (v == null || v === "" || !isFinite(v)) return null;
  return U.toDisplay(spec.kind, +v);
}
export function cellText(spec, v) {
  if (spec.text) return v == null ? "" : String(v);
  const d = cellValue(spec, v);
  if (d == null) return "—";
  const dec = spec.kind === "none" ? spec.dec : U.dec(spec.kind, spec.dec);
  if (Math.abs(d) >= 1e12) return d.toExponential(4);
  const t = d.toFixed(dec);
  return /^-0\.?0*$/.test(t) ? t.slice(1) : t;            // no "-0.000" for round-off noise
}
const csvNum = v => v == null ? "" : String(+(+v).toPrecision(12));
const csvQuote = s => /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
export const headerLabel = (col, spec) => spec.unit ? `${col.label} [${spec.unit}]` : col.label;

/* ================================================================
   VirtualGrid — renders only the visible slice of rows
   ================================================================ */
const collator = new Intl.Collator("en", { numeric: true, sensitivity: "base" });
export class VirtualGrid {
  constructor(host, { rowH = 24, overscan = 8, emptyText = "No rows" } = {}) {
    this.host = host;
    this.rowH = rowH;
    this.overscan = overscan;
    this.emptyText = emptyText;
    this.columns = []; this.specs = []; this.rows = []; this.view = [];
    this.sort = { key: null, dir: 1 };
    this.filter = "";
    this.lastRenderMs = 0; this.maxRenderMs = 0; this.renders = 0;
    this._start = -1; this._raf = 0; this._search = null;
    this.el = document.createElement("div");
    this.el.className = "vg";
    this.el.tabIndex = 0;
    this.head = document.createElement("div");
    this.head.className = "vg-head";
    this.body = document.createElement("div");
    this.body.className = "vg-body";
    this.win = document.createElement("div");
    this.win.className = "vg-win";
    this.empty = document.createElement("div");
    this.empty.className = "vg-empty hidden";
    this.body.appendChild(this.win);
    this.el.append(this.head, this.body, this.empty);
    host.appendChild(this.el);
    this.el.addEventListener("scroll", () => {
      if (this._raf) return;
      this._raf = requestAnimationFrame(() => { this._raf = 0; this.renderRows(); });
    }, { passive: true });
    this.head.addEventListener("click", e => {
      const h = e.target.closest(".vg-h");
      if (!h) return;
      this.sortBy(h.dataset.key);
    });
    if (typeof ResizeObserver !== "undefined")
      new ResizeObserver(() => this.renderRows(true)).observe(this.el);
  }
  setData(columns, rows) {
    this.columns = columns || [];
    this.rows = rows || [];
    this._search = null;
    if (this.sort.key && !this.columns.some(c => c.key === this.sort.key)) this.sort = { key: null, dir: 1 };
    this.refreshUnits(false);
    this.applyView();
  }
  /** Recompute unit specs + header labels (after a units switch). */
  refreshUnits(render = true) {
    this.specs = this.columns.map(colSpec);
    this._search = null;
    const sample = this.rows.length > 400 ? this.rows.filter((_, i) => i % Math.ceil(this.rows.length / 400) === 0) : this.rows;
    const widths = this.columns.map((c, i) => {
      const s = this.specs[i];
      let chars = Math.max(c.label.length, (s.unit || "").length);
      for (const r of sample) { const v = r[c.key]; if (v != null) chars = Math.max(chars, cellText(s, v).length); }
      return Math.max(64, Math.min(s.text ? 260 : 200, chars * 7.6 + 24));
    });
    this.template = widths.map(w => `minmax(${Math.round(w)}px, 1fr)`).join(" ");
    this.totalW = widths.reduce((a, b) => a + b, 0);
    this.el.style.setProperty("--vg-cols", this.template);
    this.head.style.minWidth = this.win.style.minWidth = this.body.style.minWidth = this.totalW + "px";
    this.renderHead();
    if (render) { this._start = -1; this.renderRows(true); }
  }
  renderHead() {
    this.head.innerHTML = this.columns.map((c, i) => {
      const s = this.specs[i];
      const arrow = this.sort.key === c.key ? `<span class="sort-arrow">${this.sort.dir > 0 ? "▲" : "▼"}</span>` : "";
      return `<div class="vg-h${s.text ? " txt" : ""}" data-key="${esc(c.key)}" title="Sort by ${esc(c.label)}">` +
        `<span class="vg-hl">${esc(c.label)}${arrow}</span>` +
        `<span class="vg-hu">${esc(s.unit || (s.text ? "" : " "))}</span></div>`;
    }).join("");
  }
  _searchIndex() {
    if (this._search) return this._search;
    const cols = this.columns, specs = this.specs;
    this._search = this.rows.map(r => {
      let s = "";
      for (let i = 0; i < cols.length; i++) s += cellText(specs[i], r[cols[i].key]) + "\u0001";
      return s.toLowerCase();
    });
    return this._search;
  }
  setFilter(text) { this.filter = String(text || "").trim().toLowerCase(); this.applyView(); }
  sortBy(key, dir) {
    if (dir != null) this.sort = { key, dir };
    else if (this.sort.key !== key) this.sort = { key, dir: 1 };
    else if (this.sort.dir > 0) this.sort = { key, dir: -1 };
    else this.sort = { key: null, dir: 1 };
    this.renderHead();
    this.applyView();
  }
  applyView() {
    let idx = this.rows.map((_, i) => i);
    if (this.filter) {
      const terms = this.filter.split(/\s+/).filter(Boolean);
      const si = this._searchIndex();
      idx = idx.filter(i => terms.every(t => si[i].includes(t)));
    }
    if (this.sort.key) {
      const k = this.sort.key, d = this.sort.dir;
      const spec = this.specs[this.columns.findIndex(c => c.key === k)] || { text: true };
      const rows = this.rows;
      idx.sort((a, b) => {
        const va = rows[a][k], vb = rows[b][k];
        const na = va == null || va === "", nb = vb == null || vb === "";
        if (na || nb) return na === nb ? a - b : na ? 1 : -1;      // blanks last
        if (!spec.text || spec.id) {
          const x = +va, y = +vb;
          if (isFinite(x) && isFinite(y)) return (x - y) * d || a - b;
        }
        return collator.compare(String(va), String(vb)) * d || a - b;
      });
    }
    this.view = idx;
    this.body.style.height = (idx.length * this.rowH) + "px";
    this.empty.textContent = this.rows.length ? "No rows match the filter" : this.emptyText;
    this.empty.classList.toggle("hidden", idx.length > 0);
    this._start = -1;
    this.renderRows(true);
    this.onView && this.onView(this);
  }
  renderRows(force = false) {
    const t0 = performance.now();
    const n = this.view.length;
    const vh = this.el.clientHeight || 400;
    const first = Math.floor(this.el.scrollTop / this.rowH);
    const start = Math.max(0, first - this.overscan);
    const end = Math.min(n, first + Math.ceil(vh / this.rowH) + this.overscan);
    if (!force && start === this._start && end === this._end) return;
    this._start = start; this._end = end;
    const cols = this.columns, specs = this.specs, rows = this.rows;
    let html = "";
    for (let k = start; k < end; k++) {
      const r = rows[this.view[k]];
      html += `<div class="vg-r${k & 1 ? " odd" : ""}">`;
      for (let i = 0; i < cols.length; i++) {
        const s = specs[i];
        const t = cellText(s, r[cols[i].key]);
        html += `<div class="vg-c${s.text ? " txt" : ""}${t === "—" ? " dim" : ""}">${s.text ? esc(t) : t}</div>`;
      }
      html += "</div>";
    }
    this.win.style.transform = `translateY(${start * this.rowH}px)`;
    this.win.innerHTML = html;
    const ms = performance.now() - t0;
    this.lastRenderMs = ms; this.maxRenderMs = Math.max(this.maxRenderMs, ms); this.renders++;
  }
  renderedRowCount() { return this.win.childElementCount; }
  /** Rows of the current view as display-unit arrays (for CSV / copy). */
  exportRows() {
    const cols = this.columns, specs = this.specs;
    return this.view.map(i => cols.map((c, j) => {
      const v = this.rows[i][c.key];
      return specs[j].text ? (v == null ? "" : String(v)) : csvNum(cellValue(specs[j], v));
    }));
  }
  headers() { return this.columns.map((c, i) => headerLabel(c, this.specs[i])); }
  toCsv() {
    return [this.headers().map(csvQuote).join(","),
      ...this.exportRows().map(r => r.map(csvQuote).join(","))].join("\n") + "\n";
  }
  toTsv() {
    return [this.headers().join("\t"), ...this.exportRows().map(r => r.join("\t"))].join("\n");
  }
}

export function downloadText(text, name, type = "text/csv;charset=utf-8") {
  const blob = new Blob([text], { type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}
export async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
  } catch { /* fall back below */ }
  const ta = document.createElement("textarea");
  ta.value = text; ta.setAttribute("readonly", "");
  ta.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  return ok;
}

/* ================================================================
   Case bookkeeping (finished / not run / skipped)
   ================================================================ */
const STATUS_TXT = { finished: "finished", not_run: "not run", run_as_dependency: "run as dependency",
  failed: "failed", skipped: "skipped" };
/** Selectable output cases of the last run (static + additive combos) and the
    idle ones (not run / failed / skipped) for display. */
export function tableCases(model, results) {
  const out = [];
  if (!results) return out;
  const cs = results.case_status || {}, cbs = results.combo_status || {};
  const isEnv = n => ((model.combos || {})[n] || {}).combo_type === "envelope";
  for (const n of Object.keys((model && model.cases) || {})) {
    const st = cs[n] || ((results.cases || {})[n] ? "finished" : "not_run");
    out.push({ name: n, kind: "case", status: (results.cases || {})[n] ? (st === "not_run" ? "finished" : st) : st,
      ok: !!(results.cases || {})[n] });
  }
  for (const n of Object.keys(results.cases || {}))
    if (!out.some(o => o.name === n)) out.push({ name: n, kind: "case", status: cs[n] || "finished", ok: true });
  for (const n of Object.keys((model && model.combos) || {})) {
    if (isEnv(n)) continue;
    const ok = !!(results.combos || {})[n];
    out.push({ name: n, kind: "combo", status: cbs[n] || (ok ? "finished" : "skipped"), ok });
  }
  return out;
}
export function statusChip(name, st) {
  return `<span class="tbl-st tbl-st-${esc(st)}" title="${esc(name)}: ${esc(STATUS_TXT[st] || st)}">` +
    `${esc(name)} · ${esc(STATUS_TXT[st] || st)}</span>`;
}

/* ================================================================
   Show Tables dialog
   ================================================================ */
const ST = {
  list: null,            // catalogue
  key: null,             // selected table
  cases: null,           // null = all, else Set of names
  cache: new Map(),      // key|cases → table payload
  filterText: "",
  grid: null,
  dlg: null,
  reqId: 0,
  table: null,
  openGroups: null,
};

function buildTree(list) {
  const root = { name: "", kids: new Map(), tables: [] };
  for (const t of list) {
    const parts = String(t.group || "Other").split(">").map(s => s.trim()).filter(Boolean);
    let node = root;
    for (const p of parts) {
      if (!node.kids.has(p)) node.kids.set(p, { name: p, kids: new Map(), tables: [] });
      node = node.kids.get(p);
    }
    node.tables.push(t);
  }
  return root;
}

export function initTables(sky) {
  const S = sky.store;
  const $ = id => document.getElementById(id);
  const toast = (t, m, k = "info", ms = 3500) => sky.toast && sky.toast(t, m, k, ms);

  const clearCache = () => { ST.cache.clear(); };
  document.addEventListener("sky:results-changed", () => {
    clearCache();
    if (ST.dlg) { refreshCasesUi(); loadTable(); }
  });
  document.addEventListener("sky:model-changed", () => { /* cache is tied to results */ });
  document.addEventListener("sky:units-changed", () => {
    if (ST.grid) { ST.grid.refreshUnits(); syncCount(); }
    if (ST.dlg) { const u = ST.dlg.el.querySelector(".tbl-units"); if (u) u.textContent = U.getUnits(); }
  });

  /* ---------------- dialog shell (same markup as analysisdlg.js) */
  function closeDlg() {
    if (!ST.dlg) return;
    document.removeEventListener("keydown", ST.dlg.onKey, true);
    ST.dlg.el.remove();
    ST.dlg = null; ST.grid = null;
  }
  let els = {};
  function open(key) {
    if (ST.dlg) { if (key) select(key); return api; }
    const back = document.createElement("div");
    back.className = "modal-backdrop sky-dlg";
    back.id = "showTablesModal";
    back.innerHTML = `
      <div class="modal tbl-modal" role="dialog" aria-labelledby="showTablesTitle">
        <header class="modal-head">
          <h2 id="showTablesTitle" class="dlg-title">${icon("show-tables", "dlg-ico")}<span>Show Tables — Analysis Results</span></h2>
          <button class="icon-btn" title="Close" aria-label="Close Show Tables">×</button>
        </header>
        <div class="modal-body tbl-body">
          <aside class="tbl-tree" id="tblTree" aria-label="Tables"><div class="muted tbl-loading">Loading tables…</div></aside>
          <section class="tbl-main">
            <div class="tbl-titlebar">
              <div class="tbl-title"><b id="tblTitle">Select a table</b><span class="muted" id="tblGroup"></span></div>
              <span class="tbl-unitset" title="Display units (Options → Units…)">Units <b class="tbl-units">${esc(U.getUnits())}</b></span>
            </div>
            <div class="tbl-tools">
              <div class="tbl-cases-wrap">
                <button class="btn btn-small" id="tblCasesBtn" aria-haspopup="true" title="Filter output cases">Cases: All</button>
                <div class="tbl-cases-pop hidden" id="tblCasesPop" role="dialog" aria-label="Output cases"></div>
              </div>
              <input type="search" id="tblFilter" class="tbl-filter" placeholder="Filter rows…" aria-label="Filter rows">
              <span class="muted tbl-count" id="tblCount"></span>
              <span class="toolbar-spacer"></span>
              <button class="chip csv-btn" id="tblCopy" title="Copy the visible table (all filtered rows) as tab-separated text">Copy</button>
              <button class="chip csv-btn" id="tblCsv" title="Export the filtered table as CSV in the current display units">⬇ CSV</button>
            </div>
            <div class="tbl-status" id="tblStatus"></div>
            <div class="po-warnings hidden" id="tblWarnings"></div>
            <div class="tbl-grid-host" id="tblGridHost"></div>
          </section>
        </div>
        <footer class="modal-foot"><div class="dlg-foot">
          <span class="muted dlg-foot-note" id="tblFoot">Results are post-processed from the last analysis — the solver is not re-run.</span>
          <div class="modal-btns"><button class="btn btn-run" id="tblClose">Close</button></div>
        </div></footer>
      </div>`;
    (document.getElementById("app") || document.body).appendChild(back);
    const onKey = e => {
      if (e.key !== "Escape") return;
      const pop = $("tblCasesPop");
      e.stopImmediatePropagation(); e.preventDefault();
      if (pop && !pop.classList.contains("hidden")) { pop.classList.add("hidden"); return; }
      closeDlg();
    };
    document.addEventListener("keydown", onKey, true);
    ST.dlg = { el: back, onKey };
    els = { tree: $("tblTree"), title: $("tblTitle"), group: $("tblGroup"), count: $("tblCount"),
      warn: $("tblWarnings"), status: $("tblStatus"), host: $("tblGridHost"), filter: $("tblFilter"),
      casesBtn: $("tblCasesBtn"), casesPop: $("tblCasesPop") };
    back.querySelector(".modal-head .icon-btn").addEventListener("click", closeDlg);
    $("tblClose").addEventListener("click", closeDlg);
    back.addEventListener("mousedown", e => { if (e.target === back) closeDlg(); });
    ST.grid = new VirtualGrid(els.host, { emptyText: "No rows for this table / case selection" });
    ST.grid.onView = syncCount;
    els.filter.value = ST.filterText;
    let ft = 0;
    els.filter.addEventListener("input", () => {
      clearTimeout(ft);
      ft = setTimeout(() => { ST.filterText = els.filter.value; ST.grid.setFilter(ST.filterText); }, 90);
    });
    $("tblCsv").addEventListener("click", exportCsv);
    $("tblCopy").addEventListener("click", async () => {
      if (!ST.table) return;
      const ok = await copyText(ST.grid.toTsv());
      toast(ok ? "Copied" : "Copy failed", ok ? `${ST.grid.view.length} rows copied (tab-separated, ${U.getUnits()})`
        : "The browser blocked clipboard access", ok ? "info" : "error");
    });
    els.casesBtn.addEventListener("click", e => { e.stopPropagation(); els.casesPop.classList.toggle("hidden"); });
    back.addEventListener("mousedown", e => {
      if (!els.casesPop.classList.contains("hidden") && !e.target.closest(".tbl-cases-wrap")) els.casesPop.classList.add("hidden");
    });
    refreshCasesUi();
    loadList().then(() => select(key || ST.key || (ST.list && ST.list[0] && ST.list[0].key)));
    return api;
  }

  async function loadList() {
    try {
      if (!ST.list) ST.list = (await skyApi(sky, "/api/tables/list")).tables || [];
    } catch (err) {
      if (els.tree) els.tree.innerHTML = `<div class="field-error">Tables unavailable: ${esc(err.message)}</div>`;
      return;
    }
    renderTree();
  }
  function renderTree() {
    if (!ST.dlg || !ST.list) return;
    const root = buildTree(ST.list);
    if (!ST.openGroups) ST.openGroups = new Set();
    let path = [];
    const node = (n, depth) => {
      const id = [...path, n.name].join(">");
      const isOpen = !ST.openGroups.has("closed:" + id);
      let html = `<div class="tbl-node${isOpen ? " open" : ""}" data-gid="${esc(id)}">
        <button class="tbl-group" style="padding-left:${6 + depth * 12}px" aria-expanded="${isOpen}">
          <span class="ex-caret">&#9656;</span><span>${esc(n.name)}</span></button><div class="tbl-kids">`;
      path.push(n.name);
      for (const k of n.kids.values()) html += node(k, depth + 1);
      for (const t of n.tables)
        html += `<button class="tbl-leaf${t.key === ST.key ? " is-sel" : ""}" data-key="${esc(t.key)}"
          style="padding-left:${20 + (depth + 1) * 12}px" title="${esc(t.title)} — ${t.columns.length} columns">
          ${icon("show-tables", "tbl-leaf-ico")}<span>${esc(t.title)}</span></button>`;
      path.pop();
      return html + `</div></div>`;
    };
    let html = "";
    for (const k of root.kids.values()) html += node(k, 0);
    for (const t of root.tables) html += `<button class="tbl-leaf" data-key="${esc(t.key)}">${esc(t.title)}</button>`;
    els.tree.innerHTML = html + `<div class="muted tbl-tree-foot">${ST.list.length} tables</div>`;
    els.tree.querySelectorAll(".tbl-group").forEach(b => b.addEventListener("click", () => {
      const nd = b.parentElement;
      const open = nd.classList.toggle("open");
      b.setAttribute("aria-expanded", String(open));
      const gid = "closed:" + nd.dataset.gid;
      open ? ST.openGroups.delete(gid) : ST.openGroups.add(gid);
    }));
    els.tree.querySelectorAll(".tbl-leaf").forEach(b => b.addEventListener("click", () => select(b.dataset.key)));
  }

  function select(key) {
    if (!key) return;
    ST.key = key;
    if (els.tree) els.tree.querySelectorAll(".tbl-leaf").forEach(b => b.classList.toggle("is-sel", b.dataset.key === key));
    const meta = (ST.list || []).find(t => t.key === key);
    if (meta && els.title) {
      els.title.textContent = meta.title;
      els.group.textContent = meta.group.replace(/^Analysis Results\s*>\s*/, "");
    }
    return loadTable();
  }

  function casesSig() { return ST.cases ? [...ST.cases].sort().join("\u0001") : "*"; }
  async function loadTable() {
    if (!ST.dlg || !ST.key) return;
    const key = ST.key;
    const id = ++ST.reqId;
    if (!S.results) {
      ST.table = null;
      ST.grid.setData([], []);
      els.count.textContent = "";
      els.warn.classList.remove("hidden");
      els.warn.innerHTML = `<div class="po-warn-item">Run an analysis first — tables are computed from the last solve.</div>`;
      return;
    }
    const ck = key + "|" + casesSig();
    let t = ST.cache.get(ck);
    if (!t) {
      els.count.innerHTML = `<span class="spinner"></span> loading…`;
      els.host.classList.add("is-loading");
      try {
        const body = ST.cases ? { cases: [...ST.cases] } : {};
        t = await skyApi(sky, `/api/tables/${encodeURIComponent(key)}`, body);
        ST.cache.set(ck, t);
      } catch (err) {
        if (id !== ST.reqId || !ST.dlg) return;
        els.host.classList.remove("is-loading");
        ST.table = null;
        ST.grid.setData([], []);
        els.count.textContent = "";
        els.warn.classList.remove("hidden");
        els.warn.innerHTML = `<div class="po-warn-item">Table failed: ${esc(err.message)}</div>`;
        return;
      }
    }
    if (id !== ST.reqId || !ST.dlg) return;
    els.host.classList.remove("is-loading");
    ST.table = t;
    const scroll = ST.grid.el.scrollTop;
    ST.grid.setData(t.columns, t.rows);
    if (ST.filterText) ST.grid.setFilter(ST.filterText);
    ST.grid.el.scrollTop = Math.min(scroll, ST.grid.el.scrollHeight);
    const w = t.warnings || [];
    els.warn.classList.toggle("hidden", !w.length);
    els.warn.innerHTML = w.map(x => `<div class="po-warn-item">⚠ ${esc(x)}</div>`).join("");
    syncCount();
  }
  function syncCount() {
    if (!els.count || !ST.grid) return;
    const g = ST.grid;
    els.count.textContent = ST.table
      ? (g.view.length === g.rows.length ? `${g.rows.length.toLocaleString("en-US")} rows`
        : `${g.view.length.toLocaleString("en-US")} of ${g.rows.length.toLocaleString("en-US")} rows`) : "";
  }

  /* ---------------- case filter */
  function refreshCasesUi() {
    if (!ST.dlg) return;
    const all = tableCases(S.model || {}, S.results);
    const okNames = all.filter(c => c.ok).map(c => c.name);
    if (ST.cases) {                                  // drop names that no longer exist
      for (const n of [...ST.cases]) if (!okNames.includes(n)) ST.cases.delete(n);
      if (!ST.cases.size || ST.cases.size === okNames.length) ST.cases = null;
    }
    const nSel = ST.cases ? ST.cases.size : okNames.length;
    els.casesBtn.textContent = ST.cases ? `Cases: ${nSel} of ${okNames.length}` : `Cases: All (${okNames.length})`;
    els.casesBtn.classList.toggle("is-filtered", !!ST.cases);
    const row = c => {
      const on = c.ok && (!ST.cases || ST.cases.has(c.name));
      return `<label class="tbl-case${c.ok ? "" : " is-idle"}" title="${esc(c.name)} — ${esc(STATUS_TXT[c.status] || c.status)}">
        <input type="checkbox" data-case="${esc(c.name)}"${on ? " checked" : ""}${c.ok ? "" : " disabled"}>
        <span class="tbl-case-n">${esc(c.name)}</span>
        <span class="run-st run-st-${esc(c.status)}">${esc(STATUS_TXT[c.status] || c.status)}</span></label>`;
    };
    const cs = all.filter(c => c.kind === "case"), cb = all.filter(c => c.kind === "combo");
    els.casesPop.innerHTML = `
      <div class="tbl-cases-head"><b>Output cases</b>
        <button class="btn btn-small" data-all="1">All</button><button class="btn btn-small" data-all="0">None</button></div>
      <div class="tbl-cases-list">
        ${cs.length ? `<div class="tbl-cases-grp">Load cases</div>${cs.map(row).join("")}` : ""}
        ${cb.length ? `<div class="tbl-cases-grp">Combinations</div>${cb.map(row).join("")}` : ""}
        ${all.length ? "" : `<div class="muted">No static cases or additive combinations in the results.</div>`}
      </div>
      <div class="muted tbl-cases-note">Case-based tables list static cases and additive combinations
        (RS / TH / envelope results are not linear states). Modal tables ignore this filter.</div>
      <div class="tbl-cases-foot"><button class="btn btn-small btn-run" data-apply="1">Apply</button></div>`;
    els.casesPop.querySelectorAll("[data-all]").forEach(b => b.addEventListener("click", () => {
      els.casesPop.querySelectorAll("input[data-case]:not(:disabled)").forEach(i => { i.checked = b.dataset.all === "1"; });
    }));
    els.casesPop.querySelector("[data-apply]").addEventListener("click", () => {
      const sel = [...els.casesPop.querySelectorAll("input[data-case]:checked")].map(i => i.dataset.case);
      setCases(sel.length === okNames.length ? null : sel);
      els.casesPop.classList.add("hidden");
    });
    // not-run / skipped strip
    const idle = all.filter(c => !c.ok);
    const other = Object.entries((S.results && S.results.case_status) || {})
      .filter(([n, st]) => st !== "finished" && !all.some(c => c.name === n));
    els.status.innerHTML = (idle.length || other.length)
      ? `<span class="muted">Last run:</span> ${idle.map(c => statusChip(c.name, c.status)).join("")}` +
        other.map(([n, st]) => statusChip(n, st)).join("")
      : "";
    els.status.classList.toggle("hidden", !(idle.length || other.length));
  }
  function setCases(list) {
    const okNames = tableCases(S.model || {}, S.results).filter(c => c.ok).map(c => c.name);
    ST.cases = list == null ? null : new Set(list.filter(n => okNames.includes(n)));
    if (ST.cases && ST.cases.size === 0) ST.cases = new Set(["\u0000none"]);   // explicit "none"
    refreshCasesUi();
    return loadTable();
  }

  function exportCsv() {
    if (!ST.table) { toast("Nothing to export", "Select a table after an analysis", "error"); return; }
    const name = `${(S.model && S.model.name || "model").replace(/[^\w-]+/g, "_")}_${ST.key}_${U.getUnits()}.csv`;
    downloadText(ST.grid.toCsv(), name);
    toast("CSV exported", `${name} · ${ST.grid.view.length} rows`);
  }

  /** Test helper: load a synthetic table of n rows into the grid (no backend). */
  function loadSynthetic(n = 5000) {
    open();
    const cols = [
      { key: "story", label: "Story", quantity: "text" }, { key: "joint", label: "Unique Name", quantity: "id" },
      { key: "case", label: "Output Case", quantity: "text" },
      ...["ux", "uy", "uz"].map(k => ({ key: k, label: k.toUpperCase(), quantity: "length" })),
      ...["rx", "ry", "rz"].map(k => ({ key: k, label: k.toUpperCase(), quantity: "angle" })),
      { key: "FX", label: "FX", quantity: "force" }, { key: "MY", label: "MY", quantity: "moment" },
      { key: "x", label: "X", quantity: "length" }, { key: "drift_x", label: "Drift X", quantity: "ratio" },
    ];
    const rows = [];
    for (let i = 0; i < n; i++)
      rows.push({ story: `Story${1 + (i % 20)}`, joint: i + 1, case: ["DEAD", "LIVE", "EQX", "EQY"][i % 4],
        ux: Math.sin(i) * 0.01, uy: Math.cos(i) * 0.01, uz: -i * 1e-6, rx: 1e-4 * Math.sin(i * 0.3),
        ry: 2e-4, rz: -1e-5, FX: i * 1.5, MY: -i * 2.25, x: (i % 7) * 6, drift_x: 1e-3 * (i % 11) });
    ST.key = null;
    ST.table = { key: "synthetic", title: "Synthetic", group: "Test", columns: cols, rows, warnings: [] };
    els.title.textContent = `Synthetic ${n.toLocaleString("en-US")}-row table`;
    els.group.textContent = "test";
    ST.grid.setData(cols, rows);
    syncCount();
    return ST.grid;
  }

  const api = {
    open, close: closeDlg, isOpen: () => !!ST.dlg, select, setCases,
    setFilter: t => { ST.filterText = t || ""; if (els.filter) els.filter.value = ST.filterText; if (ST.grid) ST.grid.setFilter(ST.filterText); },
    sortBy: (k, d) => ST.grid && ST.grid.sortBy(k, d),
    grid: () => ST.grid, table: () => ST.table, list: () => ST.list, current: () => ST.key,
    csv: () => ST.grid ? ST.grid.toCsv() : "", tsv: () => ST.grid ? ST.grid.toTsv() : "",
    exportCsv, loadSynthetic, reload: () => { clearCache(); return loadTable(); },
    colSpec, cellText,
  };
  sky.tables = api;
  sky.openShowTables = key => open(key);
  sky.skyApi = (path, body) => skyApi(sky, path, body);

  /* boot the frequency / energy / pushover / load-participation displays */
  try { initResultDisplays(sky); }
  catch (err) { console.error("result displays init failed", err); }
  return api;
}
