# SkyFrame UX audit and usability pass

Scope: the web SPA (`skyframe/skyframe/api/web/`) at base `1335c69`: 9 menus,
139 menu commands, about 30 dialogs and 14 results tabs. I drove it with
Playwright (Chromium, headless) in `?mock=1` and against a live backend, at
1440×900 and 1100×700. The audit itself
removed no features. Every fix is additive and lives in new modules
(`js/ux*.js`, `js/helpdocs.js`, `ux.css`). The pass can be switched off with
`?ux=off`, which is how the "before" screenshots were taken.

## How it was measured

| Script (scratch folder, not committed) | What it does |
|---|---|
| `audit.mjs before/after [live]` | At both viewports it screenshots the start page, Plan, every menu, 30 dialogs, 7 results tabs and the deformed 3D view. It also records each dialog's size, overflow, footer buttons, Esc behaviour and untitled buttons, and each menu's height and tooltip count. |
| `smoke.mjs [live] [w h]` | Opens every menu with a real mouse click and clicks every item: 142 items after the pass (138 before). It fails on any console error. |
| `features.mjs [live]` | Functional checks for every item below: 43 checks in mock and 44 live. |
| `tpl.mjs [live]` | Builds the 5 templates through the UI and runs each one. It checks the POST /api/model round-trip, Esc leaving the model byte-identical, and kip-ft input. |
| `perf.mjs [off] [slow]` | Interaction timings on `quick_building(stories=10)`, from the action to the next paint plus a long-task observer. Runs at 1× and with 4× CPU throttle. |

## Findings

Severity: **H** = blocks or misleads the user, **M** = friction, **L** = polish.
**Status** is the state after this pass.

### Correctness and dead ends

| # | Sev | Finding | Status |
|---|---|---|---|
| F1 | **H** | **Run with unsaved edits analysed the stale backend model.** `doRun()` posts `null` to `/api/analyze`, and the server solves its last POSTed model. If you draw a beam and press R or Run, the beam is missing from the results, and nothing tells you. Verified: new member `B1` was absent from the results with `?ux=off`. | **Fixed.** `__sky.beforeRun` now saves first in live mode and chains to Check-Model-before-run. A failed save cancels the run. |
| F2 | **H** | At 1100 px the status-bar **RUN button was clipped off-screen**. In Model and Loads modes the top-bar Run button is hidden by design, so at that width Run was reachable only with the R key, which works only in Analyze mode. | **Fixed.** Run is now on the quick-access toolbar, F5 works everywhere, and the status bar drops duplicate items below 1280 px. |
| F3 | **H** | At 1440 px, once 10+ results tabs appear after a run, the tab labels wrap onto 2 lines and the **Case selector is pushed off the right edge**. | **Fixed.** The tab bar stays on one line and scrolls, and the Case selector is pinned to the right. |
| F4 | M | Ten Assign items (Frame · Section / Releases / Local Axis / Rigid End Offsets / Axial Limit / Hinges / Panel Zones, Shell · …) only show a toast pointing to the Properties panel. Insertion Point, Output Stations, Link Properties and Assign to Group need a selection and otherwise show a red "error" toast. | **Mitigated.** These toasts now produce an amber, actionable status-bar hint, and in Model mode the Select tool is armed automatically. Every menu item has a tooltip explaining it. |
| F5 | M | Analyze › "Analysis Options…" is a toast only, with no dialog. | Documented in the help text ("set per case in the Load Cases editor"). Left as is because a real dialog belongs to the analysis owners. |
| F6 | M | File › New opened a gallery of 5 concrete moment-frame presets with no parameters: no steel, braced, flat-plate or wall templates, and no recent files. | **Fixed.** New start screen with 5 parametric templates and a recent-models list. It opens on File › New, on Alt+N, and automatically when the model is empty. The classic gallery stays under File › Template Gallery. |
| F7 | M | The flat-plate and wall templates with ≤1 m shell meshes take **33 s and 26 s** to solve live, against **1.0 s and 1.8 s** at bay/2 and h/2 meshes. T1 changes by under 5 %. | Template defaults use the coarser meshes. The solver cost is a backend item. |
| F8 | L | The sidebar "Quick model" form always shows quick-building defaults, not the current model. A user who opened a template sees "Quick Building / 3 bays". | Open. It is app.js-owned; recommended next step. |
| F9 | L | In mock mode, OK on Mass Source adds `mass_source_mode: "weight"` because the mock model lacks the field, so it is not byte-identical in mock. Live is byte-identical. | Not changed. Pre-existing and mock-only; the test asserts that Enter == OK. |

### Keyboard access

| # | Sev | Finding | Status |
|---|---|---|---|
| K1 | **H** | **Single-key shortcuts leaked behind open dialogs.** With focus on a dialog button or on the body, C/B/W… changed the drawing tool, R started a run and Delete deleted the selection behind the dialog. | **Fixed.** Keys typed inside a dialog stay in the dialog. Keys pressed while a dialog is open and focus is outside it are dropped. |
| K2 | M | Dialogs opened without moving focus into them. | **Fixed.** On open, focus moves to the first field, or to OK if there is no field. |
| K3 | M | Enter did nothing in most dialogs. | **Fixed.** A global Enter triggers OK. It applies only to OK-type buttons, never Done/Close editors, and never from a textarea, button or form. |
| K4 | M | Esc did not close the Section Manager while an input had focus. Esc handling was implemented 5 different ways. | **Fixed.** A global fallback clicks Cancel or × when a dialog does not handle Esc itself. |
| K5 | M | The menu bar was mouse-only: no arrow keys, and no F10 or Alt access. | **Fixed.** F10 focuses the menu bar; ← → ↑ ↓, Home/End, Enter and Esc work. |
| K6 | M | No way to find a command without knowing its menu, across 139 items in 9 menus with Define at 28 items and Assign at 30. | **Fixed.** A command palette (Ctrl+K or Ctrl+Shift+P) fuzzy-searches every command, with synonyms and recents. |
| K7 | L | No list of shortcuts existed. ETABS F5 (Run) and F6 (Deformed) were missing. | **Fixed.** F1 or ? opens a shortcut overlay backed by a registry. Added F5, F6, Ctrl+S/O, Ctrl+Shift+S, Alt+N/C/T/U/Q and F10. |

### Dialog consistency

| # | Sev | Finding | Status |
|---|---|---|---|
| D1 | M | 6 different widths (400, 620, 880, 900, 1080, 1240 px) and a fixed `max-height: 86vh`. At 1100×700, 9 of 27 dialogs needed body scroll; Units, Section Manager and Grid wasted about 14 % of the height. | **Fixed.** All dialogs get `max-height: 100vh − 32px`, a scrollable body and a sticky footer. They are draggable and resizable, and size and position are remembered per dialog (localStorage, try/catch). Double-click the title to reset. |
| D2 | M | Footer conventions vary: "Cancel · OK", "Cancel · Apply · OK", "Run Now · Cancel · OK", "Done" (static dialogs), "Close", "Check" and "Import". | **Partly fixed.** OK and Cancel get "(Enter)" and "(Esc)" tooltips. The order was not changed, to avoid touching 20 modules; I recommend standardising on ETABS "OK · Cancel". |
| D3 | M | No in-dialog explanation of what a dialog does or what it is called in ETABS. | **Fixed.** Every dialog header has a "?" with ETABS-equivalent help (92 entries in `js/helpdocs.js`). F1 inside a dialog opens it. |
| D4 | L | The "?" was first inserted before "×", which broke scripts that close dialogs with `.modal-head .icon-btn`. | Fixed during the pass: "×" stays the first `.icon-btn` in DOM order, and CSS `order` displays "?" first. |

### Discoverability and naming against ETABS

| # | Sev | Finding | Status |
|---|---|---|---|
| N1 | M | 0 of 139 menu items had a tooltip. The palette and explorer leaves had short labels only. | **Fixed.** Every menu item, toolbar button, palette tool, explorer leaf and results tab has a tooltip (help text plus ETABS path, or label plus key). |
| N2 | M | No Help menu. | **Fixed.** Help › Search Commands, Keyboard Shortcuts, Start Screen, Quick-Access Toolbar. |
| N3 | M | Duplicates across menus: Buckling, Pushover, Staged and Frequency cases appear in both Define and Analyze. Materials, Frame Sections and Shell Sections all open the same Section Manager. Stories and Grid Systems open the same dialog. | Open (menu ownership). The palette and help text name the shared dialog. ETABS puts case definitions only under Define. |
| N4 | L | "Opening / Erase", "Run Ritz Vectors…" (navigates only) and "Run Cracked Analysis…" (scrolls to a card) read as commands but are navigation. | Open; noted for the menu owners. |
| N5 | L | The Select menu has 2 items, while ETABS has pointer, window, by-property, all, invert and more. | Open. Undo/Edit belongs to another agent. |
| N6 | L | The Define and Assign menus are taller than a 900 px screen and scroll. | Mitigated by the command palette. A future option is ETABS-style sub-menus. |

### Layout at 1100×700

| # | Sev | Finding | Status |
|---|---|---|---|
| L1 | M | The Plan canvas was squeezed to about 250 px: explorer, draw panel, canvas and properties all side by side. | **Fixed.** Below 1200 px the explorer starts collapsed; ◀ or the palette brings it back. |
| L2 | L | The Plan toolbar's elevation readout wrapped into a 4-line column, and the draw hint was clipped. | **Fixed** with nowrap and ellipsis. The hint is hidden below 1280 px, where the status bar shows it. |
| L3 | L | The status bar overflowed: solver text, units and labels were duplicated. | **Fixed.** Duplicates are hidden on narrow screens, and the model counts moved into a tooltip. |

### Status and feedback

| # | Sev | Finding | Status |
|---|---|---|---|
| S1 | M | Nothing told the user what the active tool expects, such as "click first point…". | **Fixed.** A status-bar hint follows the mode, the tool and the drawing step (first point picked or not), plus polygon instructions. |
| S2 | L | No selection count, and no time of the last analysis. | **Fixed.** An "N selected" chip (click it to clear) and "✓ 0.32 s" for the last run, with the time and model counts in a tooltip. |
| S3 | L | Dead-end toasts used the red error style and stacked over the content. | Mitigated by the status hint (see F4). |

### Performance (h)

`quick_building(stories=10)`, live, after a solve. Times are the median of 3, from the action to the next paint.

| Interaction | before (1×) | after (1×) | after JS+layout | before (4× CPU) |
|---|---|---|---|---|
| Open any menu | 14–16 ms | 10–16 ms | 0.2–2 ms | 8–23 ms |
| Open dialog (10 kinds) | 9–15 ms | 9–15 ms | ≤ 28 ms at 4× | 9–54 ms |
| Switch results tab (7) | 13–18 ms | 14–19 ms | ≤ 11 ms at 4× | 16–40 ms |
| Mode → Plan / → 3D | 13 / 19 ms | 15 / 15 ms | ≤ 40 ms at 4× | 73 / 86 ms |
| Select 1 / 40 members (Plan) | 14 / 15 ms | 15 / 14 ms | ≤ 49 ms at 4× | 42 / 90 ms |
| Palette open / search | n/a | 18 / 15 ms | n/a | n/a |

No common interaction exceeds 100 ms at normal speed, so nothing had to be
fixed in the shared views. Under a 4× CPU throttle, two max samples went over:
selecting 40 members (124 ms) and the forces tab (194 ms, one outlier). Both
were dominated by style and layout of the whole document plus `renderProps`
at 18 ms, not by script. A candidate follow-up is to render only the visible
draw editor in `handleSelect` (app.js). The UX layer adds no polling. Its updates
run from events, MutationObservers on a few class attributes and rAF throttling,
and its init time is exposed as `__sky.ux.initMs`.

## What was added (all additive)

* `js/ux.js` is the entry point, called from one hook line at the end of `app.js boot()`. It also adds "Run saves first" and collapses the explorer on narrow screens.
* `js/ux_dialogs.js` upgrades every dialog when it opens, detected by MutationObserver. It adds drag, resize and remembered geometry, the "?" help, Enter = OK and Esc = Cancel, focus-in, and stops keys from leaking to the workspace.
* `js/ux_keys.js` holds the shortcut registry (`__sky.ux.shortcuts.register`), the F1/? overlay and menu-bar keyboard navigation.
* `js/ux_cmdk.js` is the command palette. It reads the live menu DOM, so menus other modules add later are included.
* `js/ux_qat.js` is the quick-access toolbar. Undo and Redo appear automatically when an `edit-undo`/`edit-redo` act or `__sky.undo`/`__sky.redo` exists.
* `js/ux_status.js` adds the status hint, selection chip, last-run status and tooltips.
* `js/ux_start.js` is the start screen. Templates go through `/api/model/quick` (or the mock quick model) plus `modeledit.js` builders, then `adoptModel` and `saveModel`. Lengths are in display units.
* `js/ux_common.js` holds the DOM helpers and the dialog shell. Its extra icons are registered into `icons.js` `ICONS` at runtime.
* `js/helpdocs.js` holds 92 help entries: ETABS path and description, keyed by menu act, with dialog-id and title maps.
* `ux.css`.
* Shared-file hooks: `app.js` (1 import and 1 init line) and `etabs.js` (a Help menu block appended after Options).
