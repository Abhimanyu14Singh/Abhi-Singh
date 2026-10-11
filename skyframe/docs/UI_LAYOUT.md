# SkyFrame UI layout and design tokens

This document covers the application chrome: the title bar, the main toolbar,
the side panels, the canvas area and the status bar. Dialog internals and the
menu structure are documented elsewhere.

Files:

| File | Role |
|---|---|
| `skyframe/api/web/chrome.css` | Design tokens and every layout rule. Each rule is scoped to `:root.sf-chrome`. |
| `skyframe/api/web/js/ux_chrome.js` | Moves nodes into the layout, adds the panel toggles, the explorer filter, the status readout and the toolbar fit/overflow logic. |
| `skyframe/api/web/js/ux_qat.js` | The main toolbar: groups, the overflow popup and the `menuRows()` menu mirror. |
| `skyframe/api/web/js/ux.js` | One hook line, `step("chrome", uxInstallChrome)`. It runs after the toolbar, status and narrow-window steps. |

`?ux=off` disables the whole UX layer, this layout included. That gives the
untouched baseline for before/after audits.

## Layout

```
┌ title bar 34 px ─ brand · File Edit Define Draw Assign Select View Analyze Display Design Options Help ·········· model · MOCK ┐
├ toolbar 38 px ─── file ▾ │ undo redo ▾ │ [Plan|Elev|3D] ▾ │ ↖ col beam brace wall slab ▾ │ sel ▾ │ assign ▾ │ ▶Run check ▾ │ display ▾ ··· Save/Discard · search · ⌨ · ◧ ◨ ┤
├ explorer 240 ┬──────────────── canvas (plan / elevation / 3D / results tabs) ────────────────┬ properties 300 ┤
│ filter       │ canvas-local bar: story ▾ ▲▼ z-range · grid line ▾ · ⓘ  (or results tabs + chips) │ context panel  │
│ tree         │                                                                                  │                │
└ status 26 px ─ Plan · Story3 · x, y m · Snap · tool hint ··························· n selected · DOF · units · Check · ● Ready · last run ┘
```

### Rationale (ETABS, SAP2000 and Midas conventions)

* **A thin menu bar with the brand.** ETABS and SAP2000 show only the menus in
  the top row. The separate 48-px brand bar is merged into the menu row. Its
  tagline, status pill, Report and "RUN ANALYSIS" buttons were duplicates and
  are hidden. The model name and the MOCK badge stay at the right. The chrome
  above the canvas drops from 112 px to 72 px.
* **One toolbar row, grouped in workflow order.** The groups run file and undo,
  view, draw, select, assign, analyze, then display, left to right. That is the
  order a model is built and checked. Every button has a tooltip naming the
  command and its shortcut, plus the help text from `helpdocs.js`. Every group
  ends in a small ▾ that lists the rest of the matching menu. The list is read
  live from the menu bar, so it follows the menu owner's changes, and less
  frequent tools such as links, springs, polygons and tendons live there. Run is
  the only filled (accent) button on screen.
* **The toolbar never overflows.** The fit runs on resize, on mode changes and
  when buttons appear. First a compact step hides the view labels and the search
  text. If the row still overflows, whole groups fold into a "More" (⋯) popup in
  priority order: assign, then select, display, view/file, draw/undo. Run is
  never folded. The Select pointer sits at the head of the Draw group, as in
  ETABS, so it is the last to fold. At 1280×720 in Model mode with Save/Discard
  showing, only Assign and Select fold.
* **Model Explorer on the left.** It is collapsible and has a filter box that
  matches group names and leaves. Esc clears the filter. ↓ or Enter jumps into
  the tree, where the WAI-ARIA keyboard tree from `ux_menus.js` takes over.
  Collapse it with the icon in its header or the ◧ toggle in the toolbar.
* **Properties on the right, context-sensitive.**
  - Model mode: the selection Properties form sits above the "Draw options"
    section.
  - Loads mode: the load-definition navigator.
  - Analyze mode: the quick-model generator.

  Each mode remembers its own collapsed state. Analyze starts collapsed so the
  3D view gets the width. The ◨ toggle and the `\` key both drive the original
  `#sidebarToggle` handler.
* **A large canvas.** The left draw palette and the draw-tool list in the
  sidebar repeated the toolbar, so both are hidden. The canvas-local bars keep
  only context controls: story, story step and grid line, or the result tabs
  and display chips. Their long navigation hints moved into an ⓘ tooltip. At
  1366×768 in Model mode the plan grows from about 500 px to about 830 px wide.
* **A slim status bar.** It shows the plane (`Plan · Story3`,
  `Elevation · Grid A`, `3D view` or the result tab), the cursor coordinates,
  Snap and the tool hint. On the right it shows the selection count (always
  visible), active DOF, units (now the only units selector), Check status and
  the analysis state, with a coloured dot and the last solve time. The duplicate
  Plan/Elev/3D segment, Run button, undo and redo arrows and solver text were
  removed.

### Where moved controls went

Nothing was deleted. Each hidden duplicate is still in the DOM with its id and
handler, and stays reachable from the place shown below.

| Before | Now |
|---|---|
| Topbar **Run Analysis** (`#runBtn`) | Toolbar **Run** (`an-run`, F5, R). The run spinner state is mirrored. |
| Topbar **Report** (`#reportBtn`) | File › Report… (`file-report`, which clicks `#reportBtn`) and File ▾ in the toolbar |
| Topbar status pill | Status-bar analysis state (`#sbStatusText`) |
| Topbar Discard / Save Model (`#modelActions`) | Moved node, at the right of the toolbar in Model and Loads modes |
| Left draw palette (`#etabsPalette`) | Toolbar Draw group and Draw ▾ |
| Sidebar tool list (`#toolList`) | Toolbar Draw group. It stays in the DOM because `setTool` still toggles it. |
| Floating sidebar tab (`#sidebarToggle`) | Toolbar ◨, the header icon and `\`. All three click the original button. |
| Explorer ◀ glyph (`.ex-collapse`) | Header panel icon and toolbar ◧, both through `__sky.etabs.toggleExplorer()` |
| Draw-toolbar Plan/Elevation toggle (`#viewToggle`) | Toolbar View group, using `__sky.etabs.setView` |
| Status-bar Plan/Elev/3D, Run, undo/redo | Toolbar View group, Run and Undo/Redo |
| Status-bar Story select | Draw-toolbar Story select plus the status readout. `__sky.etabs.setStory` still works. |
| Quick-toolbar units select | Status-bar units select (`#sbUnitsSelect`) |

## Design tokens

The tokens live in `chrome.css`. The existing names (`--bg`, `--surface-*`,
`--text-*`, `--accent*`, `--border*`) are redefined, so every dialog and pane
inherits the new palette without being restyled.

### Spacing, radii, type and metrics

| Token | Value | Use |
|---|---|---|
| `--sf-space-1` … `--sf-space-6` | 4, 8, 12, 16, 24, 32 px | Every gap and padding in the chrome |
| `--sf-radius-sm` / `-md` / `-lg` / `-pill` | 4 / 6 / 8 / 999 px | sm for chips and status items, md for buttons and inputs, lg for popups and menus |
| `--sf-fs-xs` / `-sm` / `-md` / `-lg` | 11 / 12 / 13 / 15 px | xs for status bar and panel headers, sm for toolbar, menus and tree, md for body text, lg reserved |
| `--sf-fw-regular` / `-medium` / `-bold` | 450 / 550 / 650 | |
| `--sf-titlebar-h` / `--sf-toolbar-h` / `--sf-status-h` | 34 / 38 / 26 px | |
| `--sf-ctrl-h` / `--sf-ctrl-h-sm` | 28 / 22 px | Toolbar buttons / status-bar controls |
| `--sf-icon` / `--sf-icon-sm` | 16 / 14 px | Every toolbar glyph / tree, popup and segment glyphs |
| `--sf-explorer-w` / `--sf-props-w` | 240 / 300 px | |
| `--sf-shadow-pop` | Popup shadow | Menus and ▾ popups only. Panels use borders. |

### Colour

There is one accent. Neutrals are blue-greys. The contrast ratios in the table
are WCAG 2.x values, computed from the hex values.

| Token | Dark | Light | Contrast (worst surface) |
|---|---|---|---|
| `--bg` | `#0e1217` | `#e9edf2` | |
| `--surface-1` (panels, bars) | `#151a21` | `#f7f9fb` | |
| `--surface-2` (inputs, cards) | `#1b2129` | `#ffffff` | |
| `--surface-3` (pressed) | `#252d38` | `#e4e9ef` | |
| `--sf-hover` | `#232a34` | `#e9eef3` | |
| `--border` / `--border-soft` | `#2a333f` / `#1f262f` | `#cdd5de` / `#e0e5eb` | |
| `--text-1` | `#e6ebf1` | `#16212c` | 11.6:1 / 13.4:1 |
| `--text-2` | `#a9b4c1` | `#3e4c5d` | 6.6:1 / 7.2:1 |
| `--text-3` (labels, hints) | `#8b97a4` | `#556272` | 4.7:1 / 5.1:1, AA |
| `--accent` (text, active) | `#3bb3e3` | `#0a6a99` | 5.8:1 / 4.9:1, AA |
| `--accent-dim` (filled Run) | `#1f97cf` | `#0a6290` | |
| `--sf-on-accent` (text on Run) | `#04121b` | `#ffffff` | 5.8:1 / 6.6:1 |
| `--ux-focus` (focus ring) | `#6fd0f5` | `#0a6a99` | 9.3:1 / 5.0:1, which is at least 3:1 for non-text |
| `--accent-ghost` | accent at 14 % | accent at 10 % | Active and pressed backgrounds |

The focus ring is a 2-px outline in `--ux-focus` on `:focus-visible`, from
`ux.css`. The popup items use an inset ring.

### Icons

There is one style: the `icons.js` house style, a 20×20 viewBox with a 1.5
stroke in `currentColor`, round caps and no fill. Glyphs render at 16 px in the
toolbar and at 14 px in the tree, the popups and the segmented control. The
icons added for this pass are registered with an `sf-` prefix in `ux_qat.js`:
chevrons, more, select all/clear, assign section/support/load, mode shape,
panel left/right, info and report. The text glyphs in the chrome (◀ ▶ ▸ ▲ ▼)
were replaced by SVG or CSS chevrons. No emoji is used.

## Persistence

All reads and writes go through `lsGet` and `lsSet` in `ux_common.js`, which
wrap them in try/catch. With blocked storage the page renders with the
defaults.

| Key | Value |
|---|---|
| `skyframe.chrome.explorerCollapsed` | `true` / `false`. When unset, the window-width default from `ux.js` applies: collapsed below 1200 px. |
| `skyframe.chrome.propsCollapsed` | `{model, loads, analyze}`. Default `{false, false, true}`. |
| `skyframe.ux.toolbarHidden` | Alt+Q or Help › Quick-Access Toolbar (unchanged) |

## Test hooks

`__sky.ux.chrome` exposes the following, all additive:

* `toggleExplorer()`, `toggleProps()` and `setProps(bool)`
* `explorerCollapsed()` and `propsCollapsed()`
* `filterExplorer(q)`, `fit()` and `folded()`

`__sky.ux.toolbar` also gains `button(id)`, `groups()` and `group(id)`.
