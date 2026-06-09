# Structural Pages

This document describes all 10 structural pages — their layout, controls,
callback inputs/outputs, and chart types.

Each page follows the same pattern:
1. A layout file under `layouts/pages/<name>.py` defines the HTML with empty
   placeholder components (graphs, tables, dropdowns).
2. A callback file under `callbacks/charts/<name>_cb.py` populates those
   components by reading from `dcc.Store("etabs-data-store")`.

---

## Overview (`/overview`)

**Layout file**: `layouts/pages/overview.py`
**Callback file**: `callbacks/charts/overview_cb.py`

The landing page. Shows a high-level summary of the attached model.

### Components and Callbacks

**KPI cards** — `update_kpis(data)`

Input: `etabs-data-store`
Output: `overview-kpis` (4 `dbc.Card` components)

Shows: Stories, Frames, Joints, Shells — each as a large number with a label.

**Model info table** — `update_model_table(data)`

Output: Dash Bootstrap HTML table with filename, units, element counts.

**Story table** — `update_story_table(data)`

Output: Table of all stories with name, elevation, and height.

**Load case / combo tables** — `update_lc_tables(data)`

Output: Two tables — load cases list and load combinations list.

**Load pattern table** — `update_pattern_table(data)`

Output: Table with pattern name, type, self-weight multiplier.

**Element composition chart** — `update_composition_chart(data, theme)`

Input: store + `theme-store`
Output: `go.Pie` chart showing Frames / Shells / Joints proportions.

---

## 2D Plan View (`/plan-view`)

**Layout**: `layouts/pages/plan_view.py`
**Callback**: `callbacks/charts/plan_view_cb.py`

Draws the structural floor plan for a selected story.

### Controls

- **Story dropdown** — selects which floor to display
- **Layer checkboxes** — toggle Frames, Joints, Shells layers

### Callbacks

**Populate story dropdown** — `populate_story_dropdown(data)`

Fills the story dropdown from the `stories` list in the store.

**Draw plan** — `update_plan_view(story, layers, data, theme)`

Inputs: story dropdown value, layer checkboxes, store, theme

Filters joints and frames to those matching the selected story's Z elevation.

Charts:
- Frames drawn as `go.Scatter` lines (xi,yi → xj,yj), coloured by section
- Joints drawn as `go.Scatter` markers
- Shells drawn as `go.Scatter` filled polygons (if layer enabled)
- Click on a frame → stores frame name in `selected-frame-store`

---

## Story Drifts (`/story-drifts`)

**Layout**: `layouts/pages/story_drifts.py`
**Callback**: `callbacks/charts/story_drifts_cb.py`

Interstory drift ratios plotted against story height (elevation) for
selected load cases, with code-limit overlays.

### Controls

- **Load case multi-select** — which cases to plot
- **Direction radio** — X, Y, or Both
- **Drift limit input** — numeric, default 0.025 (ASCE 7 / IS 1893)

### Callbacks

**Populate cases** — `populate_cases(data)`
Fills case dropdown from `load_cases` in store.

**Update drift charts** — `update_drift_charts(data, cases, direction, limit, theme)`

Charts produced:
- **Drift profile** (`go.Scatter`, horizontal bars): drift on x-axis, story
  elevation on y-axis, one line per load case. Red dashed vertical line at
  the drift limit.
- **Drift envelope bar** (`go.Bar`): max drift per story, colour-coded
  green/orange/red relative to the limit.
- **Data table**: raw drift values, story, load case, direction.

---

## Story Forces (`/story-forces`)

**Layout**: `layouts/pages/story_forces.py`
**Callback**: `callbacks/charts/story_forces_cb.py`

Shear forces and moments at each story level.

### Controls

- **Load case multi-select**
- **Component radio** — Vx, Vy, Mx, My, T

### Callbacks

**Populate cases** — `populate_sf_cases(data)`

**Update story forces** — `update_sf(data, cases, component, theme)`

Charts:
- **Story force profile** (`go.Bar`, horizontal): component value on x-axis,
  story on y-axis. Grouped bars for multiple load cases.
- **Force comparison** (`go.Scatter`): stories on y-axis, one line per case,
  shows the envelope trend.
- **Data table**: all story force values for selected cases.

---

## Base Reactions (`/base-reactions`)

**Layout**: `layouts/pages/base_reactions.py`
**Callback**: `callbacks/charts/base_reactions_cb.py`

Global base reactions (forces and moments at the foundation level).

### Controls

- **Load case multi-select**
- **Component checkboxes** — Fx, Fy, Fz, Mx, My, Mz

### Callbacks

**Populate cases** — `populate_br_cases(data)`

**Update base reactions** — `update_br(data, cases, components, theme)`

Charts:
- **Grouped bar chart** (`go.Bar`): one group per load case, bars for each
  selected component.
- **Force/moment ratio chart** (`go.Bar`): Fx vs Fy comparison; Mx vs My.
- **Data table**: full base reaction table.

---

## Frame Forces (`/frame-forces`)

**Layout**: `layouts/pages/frame_forces.py`
**Callback**: `callbacks/charts/frame_forces_cb.py`

On-demand force diagrams for a single frame element.

### Controls

- **Frame dropdown** — select element by name
- **Load case dropdown**
- **Extract Forces button** — triggers ETABS API call

### Callbacks

**Populate frames** — `populate_ff_frames(data, selected_frame)`

Input: store + `selected-frame-store` (set when user clicks on plan view)
Fills frame dropdown.

**Populate cases** — `populate_ff_cases(data)`

**Update frame forces** — `update_ff_charts(frame_forces_data, load_case, theme)`

Input: `frame-forces-store` (separate store for on-demand data), case, theme.

Charts (one per force component, arranged in a 2×3 grid):
- **Axial P** — station vs P (`go.Scatter`)
- **Shear V2** — station vs V2
- **Shear V3** — station vs V3
- **Torsion T** — station vs T
- **Moment M2** — station vs M2 (shaded area)
- **Moment M3** — station vs M3 (shaded area, typically largest for beams)

---

## Modal Analysis (`/modal`)

**Layout**: `layouts/pages/modal.py`
**Callback**: `callbacks/charts/modal_cb.py`

Modal periods, frequencies, and mass participation ratios.

### Callbacks

**Update modal** — `update_modal(data, theme)`

No controls — all modes are always shown.

Charts:
- **Period table**: mode, period (s), frequency (Hz), circular frequency,
  eigenvalue — formatted as monospace table.
- **Mass participation bar chart** (`go.Bar`): Ux and Uy per mode, with
  cumulative sum line overlay (`go.Scatter`). Red dashed line at 90% target.
- **Period spectrum** (`go.Bar`): mode number on x-axis, period on y-axis —
  useful for identifying closely-spaced modes.
- **Summary KPIs**: fundamental period T1, number of modes to reach 90%
  participation in X and Y.

---

## Displacements (`/displacements`)

**Layout**: `layouts/pages/displacements.py`
**Callback**: `callbacks/charts/displacements_cb.py`

Joint displacement envelopes across the height of the structure.

### Controls

- **Load case dropdown**
- **Component radio** — U1, U2, U3 (translations), R1, R2, R3 (rotations)

### Callbacks

**Populate cases** — `populate_disp_cases(data)`

**Update displacements** — `update_disp(data, load_case, component, theme)`

Charts:
- **Displacement profile** (`go.Scatter`): component value on x-axis, joint
  Z-coordinate on y-axis. Shows the deformed shape profile.
- **Max displacement per elevation** (`go.Bar`): aggregates to max absolute
  value at each Z level.
- **Data table**: joint, Z, component value for selected case.

---

## Load Patterns (`/load-patterns`)

**Layout**: `layouts/pages/load_patterns.py`
**Callback**: `callbacks/charts/load_patterns_cb.py`

Visualises the load pattern types and their distribution.

### Controls

- **Force component radio** — Fx, Fy, Fz, Mx, My, Mz

### Callbacks

**Update load patterns** — `update_load_patterns(data, component, theme)`

Charts:
- **Pattern type pie chart** (`go.Pie`): proportion of Dead / Live / Seismic /
  Wind / Other patterns.
- **Self-weight bar chart** (`go.Bar`): self-weight multiplier per pattern.
- **Pattern table**: name, type name, type code, self-weight multiplier.

---

## Torsion & Irregularity (`/torsion`)

**Layout**: `layouts/pages/torsion.py`
**Callback**: `callbacks/charts/torsion_cb.py`

ASCE 7 torsional irregularity checks (Types 1a and 1b) based on story drift data.

### Controls

- **Load case dropdown**

### Callbacks

**Populate cases** — `populate_torsion_cases(data)`

**Update torsion** — `update_torsion(data, load_case, theme)`

Torsional irregularity ratio = max drift at a story / average drift at that
story for the selected load case. ASCE 7 limits: ≥ 1.2 → Type 1a, ≥ 1.4 → Type 1b.

Charts:
- **Torsion ratio by story** (`go.Bar`): ratio per story, with horizontal
  reference lines at 1.0 (regular), 1.2 (Type 1a), 1.4 (Type 1b).
  Bars colour green/orange/red based on which threshold is exceeded.
- **X vs Y drift scatter** (`go.Scatter`): drift in X on x-axis, drift in Y
  on y-axis, one marker per story. Distance from the diagonal indicates torsion.
- **Stiffness proxy chart** (`go.Scatter`): approximate lateral stiffness
  (Vx/drift) vs story, helps identify soft stories visually.
- **Summary alert**: lists stories that exceed Type 1a or 1b thresholds.

---

## Shared Utilities (`callbacks/charts/_helpers.py`)

Used by every chart callback.

### `empty_fig(msg, theme)`

Returns a blank Plotly figure with a centred annotation message and no visible
axes. Used as the default output when no data is loaded.

### `base_layout(fig, title, theme, **kwargs)`

Applies consistent layout to any figure:
- Template: `plotly_white` (light) or `plotly_dark` (dark)
- Transparent background (CSS controls the card background)
- Horizontal legend above the chart
- Inter / Arial font at 12px
- Standard margins (t=40, b=40, l=50, r=20)

### `make_table(df, max_rows=500)`

Converts a pandas DataFrame to a `dbc.Table` component with Bootstrap styling.
Numbers are rounded to 4 decimal places. Handles `None` and empty DataFrames.

### Colour palettes

- `ENGINEERING_COLORS` — 8-colour list designed for structural engineering
  charts (blue, red, green, amber, purple, teal, brown, slate).
- `BLUE_PALETTE` — Plotly Bold qualitative palette for multi-series charts.
