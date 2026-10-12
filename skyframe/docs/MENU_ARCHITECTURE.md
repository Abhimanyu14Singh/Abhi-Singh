# Menu architecture (v1.20)

SkyFrame's menu bar follows the **ETABS drop-down flow**, with a few changes where
ETABS gets cluttered. The tree is declared in `skyframe/api/web/js/etabs.js` (`menus`).
It is rendered by `skyframe/api/web/js/menutree.js`, which handles cascading submenus,
the keyboard, touch, viewport clamping and the runtime registry, and is styled by
`skyframe/api/web/menutree.css`.

## Rules

1. **Top-level order matches ETABS:** File · Edit · View · Define · Draw · Select · Assign ·
   Analyze · Display · Options · Tools · Help. SkyFrame is analysis-only, so there is no
   *Design* menu. The member code checks that were in it now sit under **Tools › Code Checks ▸**
   as a secondary, post-analysis utility.
2. **No more than 14 entries per panel.** The largest panel now has 13 entries; before this
   change the largest flat list had 38. Each family of commands gets its own cascading
   submenu (Section Properties ▸, Load Cases ▸, Joint / Frame / Shell ▸, Frame Loads ▸, …).
   Commands go at most 3 levels deep (Assign › Frame › Hinges ▸).
3. **The first entries of a menu are the most common task.** Separators split the menu into
   task groups, in the order the user works: *define geometry → properties → loads → cases*,
   or *check → run → log → advanced*.
4. **"…" appears only on items that open a dialog or an editor form.** Commands that act
   immediately have none: switching a tool, view or result tab, toggling a check, or
   running the analysis.
5. **Keyboard shortcuts are right-aligned**, in plain monospace, and only shortcuts that
   really exist are shown (Ctrl+Z/Y/C/V/A, Del, Ctrl+O/S, Ctrl+Shift+S, Alt+N/C/T/U/Q,
   F1, F5, F6, Ctrl+K, and the draw-tool keys V C B X W S L G K E). The longer descriptions
   that used to show as grey hints in the menu now appear in the tooltip, together with
   the help text and the equivalent ETABS command.
6. **Disabled items say why.** A disabled item is dimmed, has `aria-disabled`, and its
   tooltip starts with *"Unavailable — …"*:
   | rule | items | reason shown |
   |---|---|---|
   | `results` | Display result views (deformed, modes, force diagrams, story / modal / buckling / TH / frequency / pushover tabs), Quick Results Report, Tools › Code Checks, Section Optimization | run the analysis first (F5) — no results yet |
   | `selection` | Copy, Delete, Clear Selection | select one or more objects first (V) |
   | `undo` / `redo` | Undo / Redo | nothing to undo / redo |
   | `clipboard` | Paste… | copy objects first (Ctrl+C) |
   The menu re-checks these rules each time it opens and on `sky:model-changed`,
   `sky:results-changed`, `sky:units-changed` and `sky:model-synced`. A disabled item
   ignores clicks, but scripts can still call it through `__sky.etabs.clickItem(act)`, so
   F6 before a run still shows the existing "run first" toast.
7. **File › Recent** lists the last 5 models that were opened or saved. The list is kept
   by `js/ux_start.js`. The model that is currently open is ticked. If there are unsaved
   edits, opening another model asks for confirmation first.
8. **Every act id is unchanged** (191 before, 191 kept, plus 2 new ones: `view-3d` and
   `view-explorer`). Only the position in the tree and the label shown there changed.
   The command palette, quick-access toolbar, shortcuts, empty-state buttons, the routing
   in `js/ux_menus.js` and tests all address commands by act id, so all of them keep
   working.

## Interaction

| input | behaviour |
|---|---|
| mouse | Click a menu name to open it. Once a menu is open, hovering over another menu name switches to it. Hovering a ▸ item opens its submenu after 140 ms. When the pointer moves to a sibling item, the open submenu closes after 260 ms, which leaves time to cross over into it diagonally. |
| touch | Tapping a menu name or a ▸ item opens it; tapping it again closes it. Tapping a command runs it. Items are at least 36 px tall when the device has a coarse pointer. |
| keyboard | **Alt+letter** opens a menu with its first item focused. The letters are F E V D R S A Y P O L H (Draw = d**R**aw, Analyze = anal**Y**ze, Display = dis**P**lay, Tools = too**L**s, chosen so they do not clash with the existing Alt+N/C/T/U/Q shortcuts). Holding Alt underlines the letters. **F10** focuses the menu bar. **← →** move between menus, and → also opens a submenu. **↑ ↓ Home End** move within a panel. **Enter / Space** run an item or open a submenu. **Esc** closes one level and returns focus to the item that opened it. Typing a letter jumps to the next item that starts with it. **Tab** closes the menus. |
| viewport | Every panel stays inside the window. A top-level panel moves left if it would cross the right edge and gets a max-height at the bottom. A submenu opens to the right, flips to the left at the right edge, moves up at the bottom edge, and scrolls if it is taller than the window. Scrolling a panel or resizing the window closes open submenus. |

Keyboard activation sends a real DOM `click` with `detail = 0` on the item. As a result,
the capture-phase routes in `js/ux_menus.js` (Section Manager tabs, grid tabs, Analysis
Options hub) and the plan-focus logic in `js/ux_kbddraw.js` work exactly as they do
for mouse clicks.

## Runtime registry

```js
__sky.menu.add("Assign/Frame Loads", { label: "Wind on Frames…", act: "asn-x-wind", fn, key?, hint?, check?, radio?, needs? });
// → "Assign/Frame Loads"
__sky.menu.add("Define/Something New", item)   // → "Define/More"   (unknown sub-path → that menu's More ▸)
__sky.menu.add("Nowhere", item)                // → "Tools/More"    (unknown menu)
__sky.menu.add(null, { act: "dis-x", … })      // → "Display/More"  (menu inferred from the act prefix)
__sky.menu.remove(act); __sky.menu.path(act); __sky.menu.open("Assign/Frame/Hinges");
__sky.menu.stats();   // entries per panel; __sky.menu.disabledReason(act); __sky.menu.acts()
```

- Path segments match labels or aliases, ignoring case and "…". For example,
  `"Area Loads"` matches **Shell Loads** and `"Design"` matches **Code Checks**.
- A duplicate act id is refused with a `console.warn`.
- **More ▸** appears only once something has been added to it.
- `__sky.etabs.openPath(path)` and `__sky.etabs.menuPath(act)` are also available.

## Full tree

`act` ids are shown in brackets. ▸ marks a submenu, and — marks a separator.

```
File
  New Model…                         Alt+N        [file-new]
  Open…                              Ctrl+O       [file-open]
  —
  Save                               Ctrl+S       [file-save]
  Save As…                           Ctrl+Shift+S [file-saveas]
  —
  Import…                                         [file-import]
  Template Gallery…                               [file-gallery]
  —
  Create Report…                                  [file-rx-create]
  Quick Results Report…    (needs results)        [file-report]
  Model Information…                              [file-rx-info]
  —
  RECENT  (up to 5 saved models)

Edit
  Undo                               Ctrl+Z       [edit-undo]
  Redo                               Ctrl+Y       [edit-redo]
  —
  Copy                               Ctrl+C       [edit-copy]
  Paste…                             Ctrl+V       [edit-paste]
  Delete                             Del          [edit-delete]
  —
  Replicate…                                      [edit-replicate]
  Move…                                           [edit-move]
  Extrude…                                        [edit-extrude]
  Edit Points ▸   Merge Joints… [edit-merge] · Align Points / Trim-Extend… [edit-align]
  Edit Frames ▸   Divide Frames… [edit-divide] · Join Frames [edit-join]
  —
  Interactive Database Editing…                   [edit-dbedit]

View
  Set Plan View                                   [draw-plan]
  Set Elevation View                              [draw-elev]
  Set 3D View                                     [view-3d]  (new)
  —
  Set Display Options…               Ctrl+W       [vx-display-options]  (D2: per window, js/dispopts.js)
  ✓ Extruded View                                 [vx-extrude]
  ✓ Show Selection Only                           [vx-selonly]
  ✓ Section Cut Plane View                        [vx-cut]
  Reset Display Options                           [vx-reset]
  —
  ✓ Model Explorer                                [view-explorer]  (new)
  ✓ Quick-Access Toolbar             Alt+Q        [help-toolbar]

Define
  Material Properties…                            [def-materials]
  Section Properties ▸
      Frame Sections… [def-frame] · Nonprismatic Frame Sections… [def-np-section] ·
      Section Designer… [def-designer] · Slab / Wall Sections… [def-shell]
      —
      Spring Properties… [def-b9-springprops] · Frame Hinge Properties… [def-uh-hingeprops] ·
      Tendon Properties… [def-pt-tendons]
  Stories, Grids & Towers ▸  Grid Data… [def-grid] · Story Data… [def-stories] · Towers… [def-c9-towers]
  Diaphragms…                                     [def-diaphragms]
  Groups…                                         [def-groups]
  Section Cuts…                                   [def-cuts]
  —
  Mass Source…                                    [def-mass]
  P-Delta Options…                                [def-pdelta]
  —
  Functions ▸  Response Spectrum / Time History… [def-functions] · Steady State / PSD Frequency… [def-freqfn]
  Load Patterns…                                  [def-patterns]
  Auto Lateral Loads ▸  Seismic / Wind Generators… [def-l116-autolat] ·
                        Open Structure Wind Pattern… [def-ow-pattern] · Code Tools… [def-codetools]
  Load Cases ▸
      All Load Cases… [def-loadcases]
      —  Linear Static… [def-cases] · Modal… [def-modal] · Response Spectrum… [def-rs] ·
         Time History… [def-th] · Buckling… [def-buckling]
      —  Nonlinear Static… [def-nls] · Pushover… [def-pushover] · Staged Construction… [def-staged]
      —  Steady State… [def-ss] · Power Spectral Density… [def-psd] · Hyperstatic (PT)… [def-pt-hyper]
  Load Combinations…                              [def-combos]

Draw
  Select Object                      V            [draw-select]
  —
  Frames ▸           Columns C [draw-column] · Beams B [draw-beam] · Braces X [draw-brace]
  Floors & Walls ▸   Rectangular Floor S [draw-slab] · Wall W [draw-wall] —
                     Floor (Polygon, Plan) [draw-polyfloor] · Wall (Polygon, Elevation) [draw-polywall] ·
                     Opening (Polygon) [draw-polyopen]
  Links & Springs ▸  Link / Device L [draw-link] · Point Spring G [draw-spring] · Line Spring K [draw-linespring]
  Tendon…                                         [draw-pt-tendon]
  —
  Section Cut…                                    [draw-cut]
  Grid Lines…                                     [draw-grid]
  —
  Erase Object / Add Opening         E            [draw-erase]

Select
  Select All                         Ctrl+A       [sel-all]
  Invert Selection                                [sel-invert]
  Previous Selection                              [sel-prev]
  Clear Selection          (needs selection)      [sel-clear]
  —
  By Property… [sel-prop] · By Story… [sel-story] · By Plane… [sel-plane] · By Group… [sel-group]

Assign
  Joint ▸   Restraints / Springs [asn-support] · Springs (Named Property)… [asn-b9-jsprings] ·
            Panel Zone… [asn-np-jpanelzone] · Diaphragm… [asn-jdiaph] · Additional Mass… [asn-jmass]
  Frame ▸   Section Property [asn-fsec] · Releases / Partial Fixity [asn-frel] · Local Axes [asn-forient]
            —  Insertion Point… [asn-finsert] · End Length Offsets… [asn-fendoff] · Rigid End Offsets [asn-foff] ·
               Tension / Compression Limits [asn-faxial] · Panel Zone [asn-fpz] · Additional Mass… [asn-fmass]
            —  Hinges ▸ (Hinges (User-Defined)… [asn-uh-hinges] · Hinge Overwrites… [asn-uh-overwrites] ·
                         Auto Hinges (Properties Panel) [asn-fhinge])
               Frame Auto Mesh Options… [asn-b9-automesh] · Output Stations… [asn-b9-stations]
  Shell ▸   Slab / Wall Section [asn-ssec] · Layered Section [asn-slayer] · Area Springs [asn-sspring] ·
            Wind Pressure Coefficients [asn-scp] — Diaphragm… [asn-sdiaph] · Additional Mass… [asn-smass]
  Link ▸    Link Properties… [asn-b9-linkprops]
  —
  Joint Loads ▸  Force / Moment… [asn-jforce] · Ground Displacement… [asn-jground] · Temperature… [asn-l116-jtemp]
  Frame Loads ▸  Distributed… [asn-l116-fdist] · Point (Concentrated)… [asn-fconc] · Temperature… [asn-l116-ftemp] ·
                 Open Structure Wind Parameters… [asn-ow-params] — Member Loads (Properties Panel) [asn-fload]
  Shell Loads ▸  Uniform… [asn-suniform] · Temperature… [asn-l116-stemp] — Area Loads (Properties Panel) [asn-aload]
  —
  Assign Objects to Group…                        [asn-group]

Analyze
  Set Active Degrees of Freedom…                  [an-dof]
  Set Load Cases to Run…                          [an-cases-run]
  —
  Check Model…                       Alt+C        [an-check]
  Check Stability…                                [an-stability]
  Fix Model…                                      [an-repair]
  —
  Run Analysis                       F5           [an-run]
  Last Analysis Run Log…                          [an-log]
  —
  Advanced ▸  Analysis Options… [an-opts] · Accuracy Advisor… [an-acc-advisor] · Mesh Sensitivity Study… [an-acc-meshstudy]
              —  Time History (FNA) [an-fna] · Ritz Vectors [an-ritz] · Cracked Analysis [an-cracked]

Display
  Undeformed Shape                                [dis-view3d]
  Deformed Shape                     F6           [dis-deformed]
  Mode Shapes                                     [dis-mode]
  Force Diagrams ▸   Frame Force Diagrams (3D)… [dis-pf-forces3d] · ✓ Show Forces in 3D View [dis-pf-forces3d-toggle]
                     —  Frame Forces [dis-forces] · Joint Reactions [dis-reactions] · Section Cut Forces [dis-cuts] ·
                        Pier Forces [dis-piers] · Tendon Forces… [dis-pt-forces]
  Shell Forces / Stresses                         [dis-contours]
  —
  Story Response ▸     Story Response Plots… [dis-pf-story] · Drifts & Shears [dis-story] · Drift Optimizer [dis-drift] ·
                       Load Takedown [dis-takedown] · Serviceability [dis-svc]
  Modal & Buckling ▸   Modal Information [dis-modal] · Buckling Factors [dis-buckling]
  Plot Functions ▸     Show Plot Functions… [dis-pf-plotfn] · Time History Results [dis-th] · Frequency Domain (SS · PSD) [dis-freq]
  Nonlinear Results ▸  Static Pushover Curve [dis-pushover] · Nonlinear Static Results… [dis-nls] · Hinge Results… [dis-uh-hinges] ·
                       Staged Construction Results… [dis-stages] · Hyperstatic Results… [dis-pt-hyper]
  —
  Show Tables…                       Alt+T        [dis-tables]
  Show Group…                                     [dis-group]

Options
  Units…                             Alt+U        [opt-units]
  —
  ✓ Snap to Grid [opt-snap] · ✓ Check Model Before Run [opt-check-before-run] · ✓ Auto Edge Constraints [opt-edge]
  Frame Auto Mesh Defaults…                       [opt-b9-automesh]
  —
  Default Diaphragm ▸  ● Rigid [opt-diaph-rigid] · ● None (Semi-Rigid) [opt-diaph-none]
  Panel Zones ▸        ● Centerline [opt-pz-none] · ● Rigid [opt-pz-rigid] · ● Flexible (Scissors) [opt-pz-scissors]
  Theme ▸              ● Dark [opt-theme-dark] · ● Light [opt-theme-light]

Tools
  Code Checks ▸   Steel Frame [des-steel] · Steel Frame · Seismic (AISC 341) [des-s341] · Concrete Frame [des-concrete] ·
                  Composite Beam [des-composite] — Shear Wall (Pier) [des-wall] · Slab · Punching Shear [des-punching] ·
                  Slab · Two-Way [des-slab]
  Section Optimization                            [des-optimize]
  (More ▸ — only when a module adds an unknown command)

Help
  Search Commands…                   Ctrl+K       [help-palette]
  Keyboard Shortcuts…                F1           [help-shortcuts]
  Start Screen / Templates…                       [help-start]
```

## Before / after

| menu | before (one flat level) | after: top level | after: submenus (entries) |
|---|---|---|---|
| File | 9 | 9 + Recent | — |
| Edit | 13 | 11 | Edit Points 2 · Edit Frames 2 |
| View | 5 | 10 | — (gained Plan / Elevation / 3D view, Explorer, Toolbar) |
| Define | **35** | 13 | Section Properties 7 · Stories, Grids & Towers 3 · Functions 2 · Auto Lateral Loads 3 · Load Cases 12 |
| Draw | 18 | 8 | Frames 3 · Floors & Walls 5 · Links & Springs 3 |
| Select | 8 | 8 | — |
| Assign | **38** | 8 | Joint 5 · Frame 12 (› Hinges 3) · Shell 6 · Link 1 · Joint Loads 3 · Frame Loads 5 · Shell Loads 3 |
| Analyze | 13 | 8 | Advanced 6 |
| Display | **28** | 11 | Force Diagrams 7 · Story Response 5 · Modal & Buckling 2 · Plot Functions 3 · Nonlinear Results 5 |
| Design | 8 | — (moved) | — |
| Options | 12 | 8 | Default Diaphragm 2 · Panel Zones 3 · Theme 2 |
| Tools | — | 2 | Code Checks 7 |
| Help | 4 | 3 | — |
| **total** | 191 commands in 12 flat lists, max **38** per list | 99 top-level entries | 28 submenus; max **13** per panel; 193 commands (+2 new) |

## Gaps vs ETABS (no matching command yet)

- Define › Pier / Spandrel Labels
- Define › Reinforcing Bar Sizes
- Draw › Dimension Lines / Reference Points
- View › Set Building View Limits

These can be added later with `__sky.menu.add("Define", …)` once dialogs for them exist.
