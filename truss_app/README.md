# Truss Lab — Virtual Work Explorer

An interactive, **educational** 2D truss app. Draw a truss, add supports and
loads, and it teaches you **which member attracts the most force, which member
controls the deflection, and why** — using the unit-load (virtual work) method
and Bill Baker's energy-based view of structural efficiency.

It runs entirely in the browser. **No build step, no server, no dependencies.**

## Run it

Just open `index.html` in any modern browser:

```
# from this folder
xdg-open index.html      # Linux
open index.html          # macOS
# or drag index.html into a browser tab
```

To serve it (e.g. for sharing on a LAN or GitHub Pages):

```
python3 -m http.server 8000      # then visit http://localhost:8000
```

## What it does

- **Draw** joints on a snapping grid, connect members, add **pin/roller**
  supports and joint **loads**.
- **Solve** with the direct **stiffness method** — works for determinate *and*
  statically indeterminate trusses, and reports stability/determinacy.
- **Explain** the results in plain language:
  - the most heavily loaded member and the load-path reason why;
  - the **unit-load / virtual-work** decomposition `δ = Σ N·n·L/(EA)`, with a
    sorted **contribution ranking** showing which member controls the deflection;
  - **Bill Baker's efficiency lens** — where to add material to stiffen a joint
    most efficiently (uniform virtual-strain-energy density);
  - determinate vs indeterminate behaviour (forces independent of EA vs.
    stiffness-attracts-force).
- **Visualize**: tension (blue) / compression (red, colourblind-safe Okabe–Ito),
  force magnitude by line thickness, reactions, and an animated **deflected
  shape**.
- **What-if loop**: select a member, assign an **AISC shape** (W, HSS, pipe,
  angle) or edit its area/E, and watch forces and deflection update instantly.
- **Units**: switch between Metric (kN, m) and Imperial (kip, ft) — AISC shapes
  set area in in² and pin E to 29,000 ksi.
- **Presets**: Pratt, Warren, Howe, cantilever, an X-braced *indeterminate*
  panel, and a starter triangle. Save/load models as JSON.

## Keyboard

`S` select · `N` joint · `M` member · `R` support · `L` load · `D` delete ·
`Ctrl+Z`/`Ctrl+Y` undo/redo · `Del` delete selection.

## How it works (the method)

Member forces and joint displacements come from the **direct stiffness method**
(element `k = (AE/L)·[…]`, assemble K, apply supports, solve `K·u = F`). The
deflection of the most-displaced joint is then decomposed with the **unit-load
method** by solving the same structure under a virtual unit load — giving each
member's contribution `N·n·L/(EA)`. The two methods **agree**, and that
agreement is the whole teaching point.

See **[RESEARCH.md](RESEARCH.md)** for the cited research basis (virtual work,
solving algorithms, UX, indeterminate behaviour, Baker's papers, AISC data).

## Tests

Pure-logic tests, no browser needed:

```
node test/solver.test.js      # closed-form + virtual-work self-consistency
node test/examples.test.js     # every preset solves & stays stable (SI + US)
```

## Project layout

```
truss_app/
  index.html        # single-page UI
  css/styles.css
  js/
    solver.js       # stiffness method + virtual-work decomposition (no deps)
    units.js        # SI <-> US unit systems
    sections.js     # AISC shape library (areas, E)
    model.js        # truss data model + undo/redo
    examples.js     # preset trusses
    view.js         # SVG rendering + hit-testing
    explain.js      # turns numbers into teaching ("insight cards")
    app.js          # controller: tools, interaction, panels, what-if loop
  test/             # node smoke/integration tests
  RESEARCH.md       # cited deep-research report
```
