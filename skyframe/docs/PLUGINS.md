# SkyFrame plugins

A plugin is a folder that holds a `plugin.json` manifest, an optional Python
module and optional ES-module JavaScript files. It can:

* add **menu items** (JavaScript, through `sky.menu.add`);
* run Python hooks **before and after the analysis**;
* serve **custom endpoints**;
* add **custom results tables** to Display › Show Tables, under the group
  "Plugins".

The ETABS equivalent is OAPI plugins, which ETABS lists under Tools ›
Add/Show Plugins.

## Where plugins live and how they are switched on

* Plugin folders are listed in `SKYFRAME_PLUGINS_DIR`, separated by
  `os.pathsep`. Without it, SkyFrame uses `~/.skyframe/plugins` and the
  bundled examples in `<repo>/plugins/`. When the same id appears in two
  folders, the first folder wins.
* **Options › Plugins…** (also **Tools › Plugins ▸ Plugin Manager…**)
  lists the plugins. **Enable** / **Disable** take effect when you press
  OK.
* The enabled ids are saved to `SKYFRAME_PLUGINS_STATE` (default
  `~/.skyframe/plugins_state.json`).
* Every plugin starts **disabled** unless its manifest says
  `"enabled_by_default": true`. While no plugin is enabled, models,
  results and tables are byte-identical to SkyFrame without plugins.

## Manifest (`plugin.json`)

```json
{
  "id": "example-story-summary",            // [a-z0-9][a-z0-9_-]{0,63}
  "name": "Story Summary (example)",
  "version": "1.0.0",
  "description": "…", "author": "…",
  "skyframe": ">=1.23",                      // minimum app version
  "python": "plugin.py",                     // hooks / endpoints / tables
  "js": ["plugin.js"],                       // ES modules (menu items, UI)
  "css": [],
  "hooks": ["pre_analysis", "post_analysis"],
  "endpoints": ["summary"],
  "tables": [{"key": "story_summary", "title": "Story Summary (plugin)",
              "columns": [{"key": "story", "label": "Story", "quantity": "text"},
                          {"key": "elevation", "label": "Elevation", "quantity": "length"}]}],
  "menus": [{"path": "Tools/Plugins", "label": "Story Summary…", "act": "tool-plg-story-summary"}],
  "permissions": ["model:read", "results:read"],
  "enabled_by_default": false
}
```

The manifest is checked when the folder is scanned. These are rejected:

* a bad id;
* a missing or out-of-folder file;
* an unknown hook, permission or table quantity (the quantities are those
  of `core/tables.QUANTITIES`, so the UI converts the units);
* a declared hook, endpoint or table without a Python module;
* a minimum version the app does not meet.

An invalid plugin is listed with its error and cannot be enabled.

## Python module

```python
def pre_analysis(model, ctx):            # before POST /api/analyze builds the engine
    return ["optional warning", ...]      # also go to the analysis run log

def post_analysis(model, results, ctx):   # results = results.to_dict()
    return {...}                          # stored at results["plugins"][id]

endpoints = {"summary": lambda model, body, ctx: {...}}    # POST /api/plugins/<id>/call/summary
tables = {"story_summary": lambda results, model: [row, ...]}  # POST /api/tables/plugin.<id>.story_summary
```

`ctx` is `{"id", "dir", "manifest", "log"}`. Call `ctx["log"](msg)` to add
a line to the plugin's log, which the manager dialog shows.

A `pre_analysis` hook may edit the model in place, as an OAPI plugin can.
Declare `model:write` if it does.

## JavaScript module

The host imports every file in `js` from `/api/plugins/<id>/files/<file>`
and calls `activate(api)`. On disable it calls `deactivate(api)` and removes
every menu item the plugin added through `api.menu.add`.

```js
export function activate(api) {
  api.menu.add("Tools/Plugins", { label: "Story Summary…", act: "tool-plg-story-summary",
    fn: async () => api.ui.showTable("Story Summary", await api.table("story_summary")) });
}
```

`api` contains:

| Member | What it does |
|---|---|
| `id`, `manifest` | the plugin's id and manifest |
| `sky` | the full `window.__sky` (an unstable surface) |
| `menu.add(path, item)` | the `sky.menu.add` contract of docs/MENU_ARCHITECTURE.md; `act` ids must be unique, prefer `tool-plg-…` |
| `call(name, body)` | runs a custom endpoint |
| `table(key)` | computes a plugin table on the current results |
| `ui.{dialog, el, esc, showTable}` | dlgkit dialogs; `showTable` converts each column by its quantity |
| `units` | `js/units.js` |
| `toast` | shows a toast |
| `onDeactivate(fn)` | registers a cleanup function |

## HTTP

| Endpoint | Result |
|---|---|
| `GET /api/plugins` | `{plugins: [Plugin], dirs, state_path}` |
| `POST /api/plugins/rescan` | the same listing, after re-reading every folder |
| `POST /api/plugins/<id>/enable` `{enabled}` | `Plugin` |
| `GET /api/plugins/<id>/files/<path>` | a web asset |
| `POST /api/plugins/<id>/call/<name>` | the endpoint's JSON |

`Plugin` = `{id, name, version, description, author, dir, enabled, loaded,
error, invalid, js, css, hooks, endpoints, tables, menus, permissions,
log}`.

## Sandboxing notes

Plugins are **trusted code**. SkyFrame contains plugin *failures*, but it
does not sandbox plugin *behaviour*:

* **Python runs in the server process** with the server's rights (files,
  network, the current model). There is no OS sandbox, timeout or memory
  limit. Install only plugins you would run as a script.
* **JavaScript runs in the SkyFrame page** with the same origin. It can
  read the model, call every endpoint and change the UI.
* **Failures are contained.** An exception in a hook, endpoint or table, or
  a broken import, marks only that plugin (`error`). The analysis and the
  other plugins continue: a failed `post_analysis` is stored as
  `{"error": …}`, and a failed endpoint returns 400.
* **File serving is restricted.** Only files inside the plugin folder are
  served, and only with web extensions (`.js .mjs .css .json .svg .png`).
  `..` and symbolic-link escapes are refused, and the Python source is
  never served.
* **`permissions` are declarative.** The manager dialog shows them to the
  user, but the server does not enforce them.

## Example

`plugins/example-story-summary/` adds:

* the menu item **Tools › Plugins › Story Summary…**;
* the table **Story Summary (plugin)**: per story, the elevation, the
  column / beam / brace counts and the largest drift ratio over the static
  cases;
* the endpoint `summary`;
* a `pre_analysis` warning when the model has no static case;
* `post_analysis` output: the governing story drift.

`tests/test_plugins.py` checks the example against closed-form member
counts and the analysis drifts. In `?mock=1`, `js/mock_plugins.js` serves
an in-browser twin of the example.
