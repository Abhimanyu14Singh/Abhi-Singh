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
**Status** is the state after pass 2 (see "Pass 2" below).

### Correctness and dead ends

| # | Sev | Finding | Status |
|---|---|---|---|
| F1 | **H** | **Run with unsaved edits analysed the stale backend model.** `doRun()` posts `null` to `/api/analyze`, and the server solves its last POSTed model. If you draw a beam and press R or Run, the beam is missing from the results, and nothing tells you. Verified: new member `B1` was absent from the results with `?ux=off`. | **Fixed.** `__sky.beforeRun` now saves first in live mode and chains to Check-Model-before-run. A failed save cancels the run. |
| F2 | **H** | At 1100 px the status-bar **RUN button was clipped off-screen**. In Model and Loads modes the top-bar Run button is hidden by design, so at that width Run was reachable only with the R key, which works only in Analyze mode. | **Fixed.** Run is now on the quick-access toolbar, F5 works everywhere, and the status bar drops duplicate items below 1280 px. |
| F3 | **H** | At 1440 px, once 10+ results tabs appear after a run, the tab labels wrap onto 2 lines and the **Case selector is pushed off the right edge**. | **Fixed.** The tab bar stays on one line and scrolls, and the Case selector is pinned to the right. |
| F4 | M | Ten Assign items (Frame · Section / Releases / Local Axis / Rigid End Offsets / Axial Limit / Hinges / Panel Zones, Shell · …) only show a toast pointing to the Properties panel. Insertion Point, Output Stations, Link Properties and Assign to Group need a selection and otherwise show a red "error" toast. | **Mitigated.** These toasts now produce an amber, actionable status-bar hint, and in Model mode the Select tool is armed automatically. Every menu item has a tooltip explaining it. |
| F5 | M | Analyze › "Analysis Options…" is a toast only, with no dialog. | **Fixed (pass 2).** Analysis Options… opens an ETABS-style hub (Active DOF, P-Delta, Mass Source, Modal case, Cases to Run). Each row opens its own existing dialog, and no new analysis settings were invented. |
| F6 | M | File › New opened a gallery of 5 concrete moment-frame presets with no parameters: no steel, braced, flat-plate or wall templates, and no recent files. | **Fixed.** New start screen with 5 parametric templates and a recent-models list. It opens on File › New, on Alt+N, and automatically when the model is empty. The classic gallery stays under File › Template Gallery. |
| F7 | M | The flat-plate and wall templates with ≤1 m shell meshes take **33 s and 26 s** to solve live, against **1.0 s and 1.8 s** at bay/2 and h/2 meshes. T1 changes by under 5 %. | Template defaults use the coarser meshes. The solver cost is a backend item. |
| F8 | L | The sidebar "Quick model" form always shows quick-building defaults, not the current model. A user who opened a template sees "Quick Building / 3 bays". | **Fixed (pass 2).** The panel is now titled "New Model Generator". A note says it replaces the working model, and it has "Templates…" (the start screen), "Use current" and "Defaults" buttons. Until the user edits it, the form mirrors the open model: name, bays and bay widths, story count and height, column and beam sizes, E and base fixity. After a start-screen template it shows that template. |
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
| D2 | M | Footer conventions vary: "Cancel · OK", "Cancel · Apply · OK", "Run Now · Cancel · OK", "Done" (static dialogs), "Close", "Check" and "Import". | **Fixed (pass 2)**, centrally in the dialog enhancer, with no module edited. When a dialog opens, and again whenever a module re-renders its footer, its buttons are moved into the ETABS order "[other actions] OK · Cancel · Apply". The commit button (OK / Save / Import / Create / Generate / Check / Select / Show) gets the accent style, and Cancel and Apply are neutral. Of 40 surveyed dialogs, 38 had a non-ETABS order and there were 2 OK styles; both are now 0 and 1. |
| D3 | M | No in-dialog explanation of what a dialog does or what it is called in ETABS. | **Fixed.** Every dialog header has a "?" with ETABS-equivalent help (92 entries in `js/helpdocs.js`). F1 inside a dialog opens it. |
| D4 | L | The "?" was first inserted before "×", which broke scripts that close dialogs with `.modal-head .icon-btn`. | Fixed during the pass: "×" stays the first `.icon-btn` in DOM order, and CSS `order` displays "?" first. |

### Discoverability and naming against ETABS

| # | Sev | Finding | Status |
|---|---|---|---|
| N1 | M | 0 of 139 menu items had a tooltip. The palette and explorer leaves had short labels only. | **Fixed.** Every menu item, toolbar button, palette tool, explorer leaf and results tab has a tooltip (help text plus ETABS path, or label plus key). |
| N2 | M | No Help menu. | **Fixed.** Help › Search Commands, Keyboard Shortcuts, Start Screen, Quick-Access Toolbar. |
| N3 | M | Duplicates across menus: Buckling, Pushover, Staged and Frequency cases appear in both Define and Analyze. Materials, Frame Sections and Shell Sections all open the same Section Manager. Stories and Grid Systems open the same dialog. | **Fixed (pass 2).** Material Properties, Frame Sections and Slab/Wall Sections open the Section Manager on their own tab, with the other groups filtered out and an "All" tab to show everything. Grid Data and Story Data open the grid editor on their own pane, with a "Both" tab. This works the same from menus, the palette, the toolbar, shortcuts and the Model Explorer. The Section Manager and Grid toolbar buttons still open on All and Both. Case definitions are only under Define. |
| N4 | L | "Opening / Erase", "Run Ritz Vectors…" (navigates only) and "Run Cracked Analysis…" (scrolls to a card) read as commands but are navigation. | **Fixed (pass 2).** These items are renamed at runtime by `act` id: "Erase Object / Add Opening (tool)", "Ritz Vectors · go to Modal results", "Cracked Analysis · go to Story results" and "Time History (FNA) · go to TH results". |
| N5 | L | The Select menu has 2 items, while ETABS has pointer, window, by-property, all, invert and more. | **Fixed** by the Edit/Select owners (`js/editmenu.js`): the Select menu now has All, Invert, by Property, by Story, by Plane, Previous, by Group and Clear. |
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

## Pass 2: consistency, empty states, errors and accessibility

Pass 2 fixed every item that was still Open (D2, F5, F8, N3, N4), plus the
items below. It changes no module's logic. Everything lives in the UX layer
and is switched off by `?ux=off`, which still passes the click-every-menu
check. The "before" numbers come from base `49ff7eb`, served unchanged
next to the same backend. Both builds were measured with the same scripts
in `?mock=1` at 1440×900.

| Measure | Before | After |
|---|---|---|
| Dialogs whose footer is not "OK · Cancel · Apply" (40 surveyed) | 38 | **0** |
| Distinct styles of the commit (OK) button | 2 (accent-uppercase, plain) | **1** |
| Menu items with an ETABS-path description in the tooltip | 90 / 177 | **177 / 177** |
| Materials / Frame / Shell Sections menu items | all open "Section Manager" | own tab: "Material Properties", "Frame Sections", "Slab / Wall Sections" |
| Grid Systems / Stories menu items | both open "Edit Grid & Stories" | "Edit Grid Data" / "Edit Story Data" |
| Analyze › Analysis Options… | a toast | the Analysis Options hub dialog |
| Results empty states with an action button (14 panes) | 0 buttons | 20 buttons (Run on every pane, plus "Define … case" on 6) |
| Visible icon-only buttons without an accessible name | 120 | **0** |
| Tab presses (of 30) that leave an open dialog | 19 | **0** |
| Theme token pairs under 4.5:1 (text-1/2/3, accent, red, green, amber on 4 surfaces) | dark 4, light 20 | **0 / 0**. The worst is now 4.66 (dark) and 4.78 (light) |
| Model Explorer | buttons only | `role=tree`, one roving tab stop, arrow keys |
| Plan view by keyboard | not possible | arrows + Enter draw and select |
| Sidebar quick form after File › New › Braced Frame | "Quick Building / 3 × 2 bays" | "Braced Frame / 4 × 3 bays / 6 stories" (mirrors the model) |

### What changed

* **Dialog footers (D2)** are handled in `js/ux_dialogs.js` `normalizeFooter`, described above. Two dialogs build their own footer: Close next to an OK counts as that dialog's Cancel (Plot Functions), and Select / Show count as the commit button (Select by Property / Story / Plane, Force Diagrams).
  Pressing Enter in a dialog now commits the field being typed in first (blur, then the change event) and then presses OK. Before this, a value typed and confirmed with Enter was lost by dialogs that read their inputs on `change`, for example Additional Mass.
* **Menu ownership and ETABS naming (N3, N4)** are in `js/ux_menus.js`. Labels are renamed at runtime by `act`, so ids, the menu definitions and the command palette keep working: the palette indexes the new label and keeps the old one as a synonym. Examples: "Material Properties…", "Section Properties · Frame Sections…", "Stories and Grid Systems · Story Data…", "Load Cases · Response Spectrum…", "Quick Draw Columns", "Frame · Releases / Partial Fixity", "Joint Reactions".
  `js/helpdocs.js` gained 88 descriptions, so every one of the 177 menu items has a tooltip and an aria label with its ETABS path.
* **Quick-model form (F8)** is handled in `js/ux_quick.js`, described above. The values are written through `units.js`; `data-si` keeps the SI value.
* **Empty states** are in `js/ux_empty.js`:
  * Every results pane gets "Run Analysis (F5)", plus "Define a … Case…" where the tab needs one (TH, pushover, buckling, cuts, frequency, takedown).
  * Result dialogs and toasts that say "run the analysis first" get a Run button.
  * List dialogs with no items ("No groups yet", "No spring properties", an empty listbox, …) get an "Add…" button. It presses the dialog's own Add button.
  * "No groups defined — Define > Groups… first" toasts get a button that opens that command.
* **Plain-language errors** are in `js/ux_errors.js`. Backend 400 messages are rewritten wherever they appear: in error toasts and in dialog error lines. For example, `Material 'CONC': E must be finite and > 0 (got -5.0)` becomes "Material “CONC”: E must be a positive number (you entered -5)."
  Other patterns covered:
  * "not a number"
  * "must be one of …"
  * "unknown section / case / … X"
  * "must be > / ≥ / < …"
  * "is required"

  The server's text stays in the tooltip. When the message names a field, that input in the top dialog (or the sidebar form) is outlined, set to `aria-invalid`, given the message as its tooltip and focused. A collapsed row that names the object (a material, a `<details>` row) is expanded first.
  Verified live: a real `POST /api/model` 400 highlights the E field of material CONC in the Section Manager.
* **Accessibility** is split across these files:
  * `js/ux_dialogs.js`: focus trap, focus returns to the opener on close, `role=dialog`, `aria-modal` and `aria-labelledby` on every dialog.
  * `js/ux_a11y.js`: aria labels on icon-only buttons (from the title or a glyph map, and for buttons added later); the results tab bar is a tablist (`aria-selected`, `aria-controls`, ←/→); toasts are a live region.
  * `ux.css`: visible `:focus-visible` rings and contrast-fixed tokens.
    * Dark: `--text-3` `#66727f` → `#808d9b`.
    * Light: `--text-3` → `#5b6878`, `--accent` → `#0a6c9c`, `--accent-dim` → `#0b6593`, `--amber` → `#855400`, `--red` → `#b02e2e`, `--green` → `#16714a`. Accent-filled buttons use white text in light mode.
    * Everything is scoped to `.ux-on`.
  * `js/ux_menus.js`: the Model Explorer becomes a WAI-ARIA tree. ↑/↓ move, → opens or enters a group, ← closes or returns to the group, Home/End jump, a letter does type-ahead and `*` expands all.
  * `js/ux_kbddraw.js`: a keyboard cursor in the Plan view. Arrows step between grid lines, Shift+arrow takes half steps, and Enter or Space acts like a click at the cursor (draw a point, select, cycle). Focus moves to the canvas when a Draw command is chosen from the keyboard.

### Verification (pass 2)

| Script | Result |
|---|---|
| `smoke.mjs` click every menu item (177) with a real mouse, after a run | mock 1440×900, mock 1100×700, live 1440×900, live 1100×700: **177/177 clicked, 0 console errors**. Also `?ux=off`: 177/177, 0 errors |
| `feat.mjs` functional checks | **61/61** mock and **61/61** live. Covers: tab routing, hub, tooltips, footer order, OK-with-defaults byte-identical on 16 dialogs, focus trap, aria, focus ring, contrast, live 400 plain text + field highlight, quick form + template, explorer tree, empty-state Run and Add… |
| `kbd.mjs` keyboard-only walkthrough | **17/17** mock and live. Ctrl+Shift+S save → Ctrl+O open (Tab to the file, Enter) → F10 › Draw › Draw Beams → arrows + Enter draw a diagonal beam → V, Shift+arrows, Enter select it → F10 › Assign › Frame · Additional Mass…, type, Enter = OK → F5 run; the live results contain the new beam → 2 = Story Results, ←/→ on the tab list → explorer arrows + Enter open Story Data → Esc |
| `metrics.mjs before/after` | the table above |
| `perf.mjs` (mock, after a run, median of 5, action → next paint) | open Mass Source 13–18 ms (before 16–19), Show Tables 21 (21–23), Section Manager 15–19 (18–21), switch results tab 14–17 (16–17), select 40 members 17–25 (14–25). UX init is 15–17 ms (`__sky.ux.stepMs` gives the time per module). Observers watch only dialog subtrees, the explorer and added HTML nodes, never the plan or 3D redraws |
| `shots.mjs` | before (base) and after screenshots of the views and dialogs named above at both viewports (scratch folder, not committed) |

Not changed: F7 is a backend item. F9 is pre-existing and mock-only. Add Nonprismatic Section's OK always adds a section, which is pre-existing behaviour, so that dialog is excluded from the byte-identical check.

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
* Pass 2 modules, all started from `js/ux.js`:
  * `js/ux_menus.js`: tabs, ETABS labels, the Analysis Options hub, the explorer tree
  * `js/ux_empty.js`: empty states
  * `js/ux_errors.js`: plain-language errors
  * `js/ux_quick.js`: the sidebar form
  * `js/ux_a11y.js`: accessibility
  * `js/ux_kbddraw.js`: the Plan keyboard cursor

  Pass 2 also extended `js/ux_dialogs.js` (footers, focus trap, Enter commit), `js/helpdocs.js` (88 entries) and `ux.css`. It made no new edits to shared files.
* Shared-file hooks: `app.js` (1 import and 1 init line) and `etabs.js` (a Help menu block appended after Options).
