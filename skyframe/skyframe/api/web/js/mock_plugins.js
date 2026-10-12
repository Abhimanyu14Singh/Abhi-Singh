/* SkyFrame — ?mock=1 stand-in for the plugin endpoints (CONTRACT "Plugins").

   One plugin is "installed": a client-side twin of plugins/example-story-
   summary (same id, manifest and behaviour). Its ES module is served from
   a Blob URL, its endpoint and table are answered from the store model /
   results, and the enable flag lives in memory (resets on reload). */

const MP_ID = "example-story-summary";
const MP_JS = `export function activate(api) {
  api.menu.add("Tools/Plugins", { label: "Story Summary…", act: "tool-plg-story-summary", hint: "example plugin",
    fn: async () => { const t = await api.table("story_summary"); api.ui.showTable(t.title || "Story Summary", t, { id: "plgStorySummary" }); } });
}
export function deactivate() {}
`;
const mpState = { enabled: { [MP_ID]: false } };

const MP_MANIFEST = {
  id: MP_ID, name: "Story Summary (example)", version: "1.0.0", author: "SkyFrame",
  description: "Example plugin: a Tools › Plugins menu item, a per-story summary table, a custom endpoint and pre/post analysis hooks.",
  js: ["plugin.js"], css: [], hooks: ["pre_analysis", "post_analysis"], endpoints: ["summary"],
  tables: [`plugin.${MP_ID}.story_summary`], permissions: ["model:read", "results:read"],
  menus: [{ path: "Tools/Plugins", label: "Story Summary…", act: "tool-plg-story-summary" }],
};

export function mockPluginList() {
  return { plugins: [{ ...MP_MANIFEST, dir: "(mock)", enabled: mpState.enabled[MP_ID], loaded: mpState.enabled[MP_ID], error: null, invalid: false, log: [] }],
    dirs: ["(mock)"], state_path: "(memory)" };
}

export function mockPluginEnable(id, on) {
  if (!(id in mpState.enabled)) return { error: `unknown plugin ${id}` };
  mpState.enabled[id] = !!on;
  return mockPluginList().plugins.find(p => p.id === id);
}

export function mockPluginModuleUrl(id, file) {
  if (id !== MP_ID || file !== "plugin.js") return null;
  return URL.createObjectURL(new Blob([MP_JS], { type: "text/javascript" }));
}

function mpCounts(model) {
  return (model.stories || []).map(s => {
    const mem = (model.members || []).filter(m => m.story === s.name);
    return { story: s.name, elevation: s.elevation, columns: mem.filter(m => m.kind === "column").length,
      beams: mem.filter(m => m.kind === "beam").length, braces: mem.filter(m => m.kind === "brace").length };
  });
}

export function mockPluginCall(id, name, model) {
  if (!mpState.enabled[id] || name !== "summary") return { error: "no such endpoint" };
  return { model: model.name, stories: mpCounts(model) };
}

export function mockPluginTable(id, key, model, results) {
  if (!mpState.enabled[id] || key !== "story_summary") throw new Error("unknown table");
  const rows = mpCounts(model);
  for (const r of rows) {
    let best = 0;
    for (const c of Object.values((results && results.cases) || {})) {
      const s = (c.story || {})[r.story] || {};
      best = Math.max(best, Math.abs(s.drift_x || 0), Math.abs(s.drift_y || 0));
    }
    r.max_drift = best;
  }
  return { key: `plugin.${id}.${key}`, title: "Story Summary (plugin)", group: "Plugins", warnings: [],
    columns: [{ key: "story", label: "Story", quantity: "text" }, { key: "elevation", label: "Elevation", quantity: "length" },
      { key: "columns", label: "Columns", quantity: "id" }, { key: "beams", label: "Beams", quantity: "id" },
      { key: "braces", label: "Braces", quantity: "id" }, { key: "max_drift", label: "Max drift", quantity: "ratio" }], rows };
}
