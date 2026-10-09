# Truss Lab — Virtual Work Explorer

An interactive, **educational** 2D truss app. Draw a truss, add supports and
loads, and it teaches you **which member attracts the most force, which member
controls the deflection, and why** — using the unit-load (virtual work) method
and Bill Baker's energy-based view of structural efficiency.

It runs entirely in the browser. **No server, no internet, no dependencies.**

## Install

**Download [`dist/TrussLab-Installer.zip`](dist/TrussLab-Installer.zip)**, unzip
it, and either:

- **Just double-click `TrussLab.html`** — that single file *is* the whole app.
  Copy it anywhere (Desktop, USB stick, email); it works offline.
- **Or run the installer** for a Desktop / Start-menu shortcut:
  - Windows: double-click `Install on Windows.bat`
    (if SmartScreen appears: *More info → Run anyway*)
  - Mac: right-click `Install on Mac.command` → *Open*
  - Linux: `sh install-linux.sh`

`INSTALL.txt` inside the zip has step-by-step instructions, including uninstall.
No admin rights are needed; the installers only copy one file into your user
folder and make shortcuts.

## What it does

- **Draw** joints on a snapping grid, connect members (click-click chains;
  clicking empty grid makes a joint), add **pin/roller** supports and joint
  **loads**. Drag joints (drop one on another to join them), pan, zoom, fit.
- **Solve** with the direct **stiffness method** — determinate *and*
  statically indeterminate trusses — with stability/determinacy checks and
  plain-language hints when a truss is a mechanism.
- **Explain** the results:
  - the most heavily loaded member and the load-path reason why;
  - the **unit-load / virtual-work** decomposition `δ = Σ N·n·L/(EA)` at **any
    joint and direction you choose**, with a sorted **contribution ranking**
    (members that work *against* the deflection are shown striped);
  - **Bill Baker's efficiency lens** — where adding material stiffens the joint
    most efficiently (uniform virtual-strain-energy density);
  - determinate vs indeterminate behaviour (forces independent of EA vs.
    stiffness-attracts-force).
- **Visualize**: tension blue / compression red (colourblind-safe Okabe–Ito),
  thickness ∝ force, reactions, the purple **virtual unit-load "probe"**, and an
  exaggerated **deflected shape**.
- **What-if loop**: give a member an **AISC shape** (W, HSS, pipe, angle) or
  edit its area/E and everything re-solves instantly — even while dragging.
- **Units**: Metric (kN, m) or Imperial (kip, ft, in); AISC shapes set A in in²
  and E = 29,000 ksi.
- **Presets**: Pratt, Howe roof, Warren, cantilever, an X-braced
  *indeterminate* panel, and a starter triangle. Save/load models as JSON.

**Keys:** `S` select · `N` joint · `M` member · `R` support · `L` load ·
`D` delete · `F` fit · `Esc` cancel · `Ctrl+Z` / `Ctrl+Y` undo/redo ·
`Del` delete selection.

## How it works

Member forces and joint displacements come from the **direct stiffness method**
(element `k = (AE/L)·[…]`, assemble K, apply supports, solve `K·u = F`). The
deflection at the chosen joint is then decomposed with the **unit-load method**
by solving the same structure under a virtual unit load — giving each member's
contribution `N·n·L/(EA)`. The two methods **agree**, and that agreement is the
whole teaching point.

See **[RESEARCH.md](RESEARCH.md)** for the cited research basis.

## Develop

Edit the sources (`index.html`, `css/`, `js/`) and open `index.html` directly.
To rebuild the single-file app and the installer zip:

```
node tools/build.js          # -> dist/TrussLab.html, dist/TrussLab-Installer.zip
```

## Tests

```
node tools/test-all.js       # runs every suite, one summary line each
```

| Suite | What it checks | Needs |
|---|---|---|
| `test/solver.test.js` | closed-form answers (single bar, two-bar, indeterminate three-bar), virtual work = stiffness, equilibrium, Maxwell–Betti, mechanism detection, bad input | Node |
| `test/examples.test.js` | every preset solves in SI & US **and** shows the behaviour its description teaches (e.g. Pratt diagonals all in tension) | Node |
| `test/ui.dom.test.js` | every UI operation driven by simulated events: drawing tools, drag/join/delete, undo/redo, probe picker, units, AISC, save/load (incl. corrupt files), keyboard, every preset | `npm i --no-save jsdom` |
| `test/browser.test.js` | the same in **real Chromium** with real mouse clicks and CSS, plus screenshots | `npm i --no-save playwright-core` + a Chromium |

The UI suites take a page path, so they also verify the built file:
`node test/browser.test.js dist/TrussLab.html`.

## Project layout

```
truss_app/
  index.html          single-page UI (source)
  css/styles.css
  js/
    solver.js         stiffness method + virtual-work decomposition (no deps)
    units.js          SI <-> US unit systems
    sections.js       AISC shape library (areas, E)
    model.js          truss data model + undo/redo
    examples.js       preset trusses
    view.js           SVG rendering, hit-testing, zoom/pan
    explain.js        turns numbers into teaching ("insight cards")
    app.js            controller: tools, interaction, panels, what-if loop
  installer/          Windows / Mac / Linux installers + INSTALL.txt
  tools/build.js      builds dist/ (single file + installer zip)
  tools/test-all.js   runs every test suite
  dist/               ready-to-install build output
  test/               test suites
  RESEARCH.md         cited deep-research report
```
