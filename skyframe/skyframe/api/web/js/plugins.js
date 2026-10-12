/* SkyFrame — plugin framework UI (CONTRACT "Plugins", docs/PLUGINS.md).

     Options › Plugins…                 enable / disable installed plugins
     Tools › Plugins ▸ Plugin Manager…  (same dialog) + the items plugins add
     Help › Scripting API (REST / OAPI)… endpoint list from /api/openapi.json

   At start-up every ENABLED plugin's ES modules are imported from
   /api/plugins/<id>/files/<file> and `activate(api)` is called with the
   plugin API below. Menu items added through api.menu are tracked and
   removed when the plugin is disabled (and deactivate() is called).

   Plugin API: { id, manifest, sky, menu: { add(path, item) },
     call(name, body) → POST /api/plugins/<id>/call/<name>,
     table(key, body) → POST /api/tables/plugin.<id>.<key>,
     ui: { dialog, el, esc, showTable(title, table, {id}) }, units, toast,
     onDeactivate(fn) }.
   ?mock=1: js/mock_plugins.js (an in-browser twin of the example plugin).
   Test hook: __sky.plugins. */

import plU from "./units.js";
import { el as plEl, esc as plEsc, dkDialog as plDialog, dkList as plList, dkGroup as plGroup, dkNote as plNote } from "./dlgkit.js";
import { mockPluginList as plMockList, mockPluginEnable as plMockEnable, mockPluginModuleUrl as plMockUrl,
  mockPluginCall as plMockCall, mockPluginTable as plMockTable } from "./mock_plugins.js";

const plState = { list: [], dirs: [], statePath: "", active: {}, errors: {}, lastTable: null };
const plMock = sky => !!(sky.store && sky.store.mock);
const PL_KIND = { text: "none", id: "none", ratio: "none", percent: "none" };

async function plFetch(path, opts) {
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}
const plPost = (path, body) => plFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

async function plRefresh(sky) {
  const d = plMock(sky) ? plMockList() : await plFetch("/api/plugins");
  plState.list = d.plugins || [];
  plState.dirs = d.dirs || [];
  plState.statePath = d.state_path || "";
  return plState.list;
}

/* ------------------------------------------------------------ table dialog */
function plCell(col, v) {
  if (v == null || v === "") return "";
  if (typeof v !== "number") return String(v);
  const kind = PL_KIND[col.quantity] || col.quantity;
  if (kind === "none" || !kind) return col.quantity === "ratio" ? v.toPrecision(4) : String(v);
  try { return plU.fmt(kind, v, 3); } catch { return String(v); }
}
function plShowTable(title, tbl, { id = "plTable" } = {}) {
  const cols = tbl.columns || [];
  const head = plEl("tr", {}, cols.map(c => {
    const kind = PL_KIND[c.quantity] || c.quantity;
    let u = "";
    try { u = kind && kind !== "none" ? plU.label(kind) : ""; } catch { u = ""; }
    return plEl("th", { text: c.label + (u ? ` (${u})` : "") });
  }));
  const rows = (tbl.rows || []).map(r => plEl("tr", {}, cols.map(c => plEl("td", { class: typeof r[c.key] === "number" ? "num" : "txt", text: plCell(c, r[c.key]) }))));
  const kids = [plEl("div", { class: "table-scroll" }, [plEl("table", { class: "data-table", id: id + "Tbl" }, [plEl("thead", {}, [head]), plEl("tbody", {}, rows)])])];
  if (tbl.warnings && tbl.warnings.length) kids.push(plNote(plEsc(tbl.warnings.join(" · "))));
  plState.lastTable = tbl;
  return plDialog({ id, title, size: "lg", noCancel: true, body: kids, intro: `${(tbl.rows || []).length} row(s) · values in the display units.` });
}

/* ------------------------------------------------------------ activation */
function plApi(sky, p) {
  const acts = [], offs = [];
  const api = {
    id: p.id, manifest: p, sky,
    menu: { add: (path, item) => { const r = sky.menu && sky.menu.add ? sky.menu.add(path, item) : ""; if (r && item.act) acts.push(item.act); return r; } },
    call: (name, body = {}) => plMock(sky) ? Promise.resolve(plMockCall(p.id, name, sky.store.model)) : plPost(`/api/plugins/${encodeURIComponent(p.id)}/call/${encodeURIComponent(name)}`, body),
    table: (key, body = {}) => plMock(sky) ? Promise.resolve(plMockTable(p.id, key, sky.store.model, sky.store.results))
      : plPost(`/api/tables/plugin.${p.id}.${key}`, sky.store.results ? { results: sky.store.results, ...body } : body),
    ui: { dialog: plDialog, el: plEl, esc: plEsc, showTable: plShowTable },
    units: plU, toast: (...a) => sky.toast && sky.toast(...a),
    onDeactivate: fn => offs.push(fn),
  };
  return { api, acts, offs };
}

async function plActivate(sky, p) {
  if (plState.active[p.id] || !p.enabled || p.invalid) return;
  const rec = { mods: [], ...plApi(sky, p) };
  plState.active[p.id] = rec;
  try {
    for (const f of p.js || []) {
      const url = plMock(sky) ? plMockUrl(p.id, f) : `/api/plugins/${encodeURIComponent(p.id)}/files/${f.split("/").map(encodeURIComponent).join("/")}`;
      if (!url) continue;
      const mod = await import(/* @vite-ignore */ url);
      rec.mods.push(mod);
      if (typeof mod.activate === "function") await mod.activate(rec.api);
    }
    delete plState.errors[p.id];
  } catch (err) {
    plState.errors[p.id] = String(err && err.message || err);
    console.warn(`plugin ${p.id}: ${plState.errors[p.id]}`);
  }
}

function plDeactivate(sky, id) {
  const rec = plState.active[id];
  if (!rec) return;
  for (const mod of rec.mods) { try { mod.deactivate && mod.deactivate(rec.api); } catch { /* plugin bug */ } }
  for (const fn of rec.offs) { try { fn(); } catch { /* plugin bug */ } }
  for (const act of rec.acts) sky.menu && sky.menu.remove && sky.menu.remove(act);
  delete plState.active[id];
}

async function plSetEnabled(sky, id, on) {
  const p = plMock(sky) ? plMockEnable(id, on) : await plPost(`/api/plugins/${encodeURIComponent(id)}/enable`, { enabled: !!on });
  if (p && p.error) throw new Error(p.error);
  await plRefresh(sky);
  const cur = plState.list.find(x => x.id === id);
  if (on && cur) await plActivate(sky, cur); else plDeactivate(sky, id);
  return cur;
}

/* ------------------------------------------------------------ manager dialog */
export async function plOpenManager(sky) {
  try { await plRefresh(sky); } catch (e) { sky.toast && sky.toast("Plugins", e.message, "error"); return null; }
  const draft = Object.fromEntries(plState.list.map(p => [p.id, !!p.enabled]));
  const status = p => p.invalid ? "invalid" : (draft[p.id] ? "enabled" : "disabled") + (draft[p.id] !== !!p.enabled ? " *" : "");
  const detail = plEl("div", { class: "muted", id: "plDetail" });
  const showDetail = id => {
    const p = plState.list.find(x => x.id === id);
    detail.innerHTML = p ? `<b>${plEsc(p.name)}</b> ${plEsc(p.version || "")} — ${plEsc(p.description || "")}<br>` +
      `Hooks: ${plEsc((p.hooks || []).join(", ") || "none")} · Endpoints: ${plEsc((p.endpoints || []).join(", ") || "none")} · ` +
      `Tables: ${plEsc((p.tables || []).join(", ") || "none")} · Permissions: ${plEsc((p.permissions || []).join(", ") || "none")}` +
      (p.error || plState.errors[p.id] ? `<br><span class="xc-warn">Error: ${plEsc(p.error || plState.errors[p.id])}</span>` : "") : "";
  };
  const list = plList({ id: "plList", items: () => plState.list.map(p => ({ key: p.id, ...p })), empty: "No plugins installed — add a folder with a plugin.json.",
    columns: [{ label: "Plugin", get: p => p.name }, { label: "Version", get: p => p.version }, { label: "Status", get: status }],
    actions: [
      { label: "Enable", id: "plEnable", needsSel: true, fn: id => { if (id) { draft[id] = true; list.render(); showDetail(id); } } },
      { label: "Disable", id: "plDisable", needsSel: true, fn: id => { if (id) { draft[id] = false; list.render(); showDetail(id); } } },
      { label: "Rescan", id: "plRescan", fn: async () => {
        if (!plMock(sky)) { try { await plPost("/api/plugins/rescan"); } catch { /* shown on refresh */ } }
        await plRefresh(sky); for (const p of plState.list) if (!(p.id in draft)) draft[p.id] = !!p.enabled; list.render(); } },
    ] });
  list.addEventListener("click", () => showDetail(list.selected()));
  if (plState.list.length) showDetail(list.selected());
  const dirs = plState.dirs.map(d => `<code>${plEsc(d)}</code>`).join(", ");
  return plDialog({ id: "plManager", title: "Plugins", size: "md", body: [
    plGroup("Installed Plugins", [list, detail]),
    plNote(`Plugin folders: ${dirs || "(none)"}. A plugin is trusted code: its Python runs inside the SkyFrame server and its JavaScript in this page — enable only plugins you trust (docs/PLUGINS.md).`),
  ], intro: "Enable or disable plugins; changes apply on OK.",
  onOk: async () => {
    try {
      for (const p of plState.list) if (!p.invalid && draft[p.id] !== !!p.enabled) await plSetEnabled(sky, p.id, draft[p.id]);
    } catch (e) { return e.message; }
    sky.menu && sky.menu.refresh && sky.menu.refresh();
    return true;
  } });
}

/* ------------------------------------------------------------ API reference */
export async function plOpenApiRef(sky) {
  let spec = null;
  if (!plMock(sky)) { try { spec = await plFetch("/api/openapi.json"); } catch { spec = null; } }
  const rows = [];
  if (spec) for (const [p, item] of Object.entries(spec.paths || {})) for (const [m, op] of Object.entries(item)) rows.push([m.toUpperCase(), p, op.summary || ""]);
  const tbl = rows.length ? plEl("div", { class: "table-scroll", style: "max-height:50vh" }, [plEl("table", { class: "data-table", id: "plApiTbl" }, [
    plEl("thead", {}, [plEl("tr", {}, ["Method", "Path", "Summary"].map(h => plEl("th", { text: h })))]),
    plEl("tbody", {}, rows.map(r => plEl("tr", {}, r.map(v => plEl("td", { class: "txt", text: v })))))])])
    : plNote("The endpoint list needs the live server (?mock=1 has no REST API).");
  return plDialog({ id: "plApiRef", title: "Scripting API", size: "lg", noCancel: true, body: [
    plNote(`REST: every endpoint under <code>/api/v1/…</code> (unversioned <code>/api/…</code> kept); OpenAPI 3 spec at <a href="/api/openapi.json" target="_blank" rel="noopener">/api/openapi.json</a>. ` +
      `Python: <code>from skyframe.oapi import comtypes</code> runs ETABS / SAP2000 OAPI scripts (docs/API.md).`), tbl],
  intro: spec ? `${rows.length} operations · version ${plEsc(spec.info && spec.info.version)}` : "" });
}

/* ------------------------------------------------------------ init */
export function initPlugins(sky) {
  if (!sky) return;
  sky.plugins = { state: plState, refresh: () => plRefresh(sky), openManager: () => plOpenManager(sky), openApiRef: () => plOpenApiRef(sky),
    setEnabled: (id, on) => plSetEnabled(sky, id, on), showTable: plShowTable, active: () => Object.keys(plState.active) };
  if (sky.menu && sky.menu.add) {
    sky.menu.add("Options", { label: "Plugins…", act: "opt-plg-manager", hint: "enable / disable", fn: () => plOpenManager(sky) });
    sky.menu.add("Tools/Plugins", { label: "Plugin Manager…", act: "tool-plg-manager", fn: () => plOpenManager(sky) });
    sky.menu.add("Help", { label: "Scripting API (REST / OAPI)…", act: "help-plg-apiref", fn: () => plOpenApiRef(sky) });
  }
  plRefresh(sky).then(list => Promise.all(list.filter(p => p.enabled).map(p => plActivate(sky, p))))
    .catch(() => { /* no plugin endpoint (older server): nothing to load */ });
}
