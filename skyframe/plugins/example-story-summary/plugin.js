/* Example SkyFrame plugin (docs/PLUGINS.md) — adds Tools › Plugins ›
   Story Summary… which calls the plugin's Python endpoint and shows the
   plugin's results table. `api` is the plugin API handed to activate():
   { id, manifest, sky, menu.add, call, table, ui.{dialog, el, esc,
   showTable}, units, toast, onDeactivate }. */

export function activate(api) {
  api.menu.add("Tools/Plugins", {
    label: "Story Summary…",
    act: "tool-plg-story-summary",
    hint: "example plugin",
    fn: () => openSummary(api),
  });
}

async function openSummary(api) {
  let table = null;
  try {
    table = await api.table("story_summary");   // needs results: runs the analysis when there are none
  } catch (err) {
    const s = await api.call("summary", {});    // model-only fallback
    table = {
      title: "Story Summary (plugin)",
      columns: [
        { key: "story", label: "Story", quantity: "text" },
        { key: "elevation", label: "Elevation", quantity: "length" },
        { key: "columns", label: "Columns", quantity: "id" },
        { key: "beams", label: "Beams", quantity: "id" },
        { key: "braces", label: "Braces", quantity: "id" },
      ],
      rows: s.stories,
      warnings: [String(err && err.message || err)],
    };
  }
  api.ui.showTable(table.title || "Story Summary", table, { id: "plgStorySummary" });
}

export function deactivate() { /* menu items added through api.menu are removed by the host */ }
