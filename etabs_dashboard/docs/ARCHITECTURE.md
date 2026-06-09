# Architecture

This document explains how the dashboard is structured, how data moves through
the system, and how the callback and theme systems work.

---

## Technology Stack

| Layer | Library | Role |
|---|---|---|
| Web framework | Plotly Dash 2.x | Server, routing, reactive callbacks |
| UI components | dash-bootstrap-components 1.x | Layout, buttons, badges, modals, toasts |
| Charts | Plotly Graph Objects | All interactive charts |
| Data | pandas + numpy | In-callback data manipulation |
| ETABS API | comtypes / ETABSv1 | Windows COM automation |
| Exports | openpyxl + kaleido | Excel and PNG downloads |
| Styling | Bootstrap Flatly/Darkly + custom.css | Theming, typography |

---

## High-Level Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                        ETABS v23                            │
│   (running on Windows, model loaded, analysis complete)     │
└────────────────────────┬────────────────────────────────────┘
                         │  COM automation (comtypes / ETABSv1)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  etabs_connector.py                                         │
│  • get_etabs_model()   → connects, returns SapModel         │
│  • extract_all_data()  → pulls geometry + all results       │
│  Returns: plain Python dict, fully JSON-serialisable        │
└────────────────────────┬────────────────────────────────────┘
                         │  triggered by ATTACH button click
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  dcc.Store(id="etabs-data-store", storage_type="memory")    │
│  Single source of truth for all page callbacks.             │
│  Held in the browser tab's JavaScript memory.               │
│  Cleared when the tab is closed or refreshed.               │
└────────────────────────┬────────────────────────────────────┘
                         │  every page callback reads from here
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  callbacks/charts/*.py  (16 callback modules)               │
│  Each receives the store dict + UI control values.          │
│  Returns: Plotly figures + HTML components + tables         │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Browser — Dash renders the outputs into the page           │
└─────────────────────────────────────────────────────────────┘
```

---

## Application Boot Sequence

When `python app.py` is run:

1. `app.py` creates the `dash.Dash` instance with Bootstrap Flatly stylesheet.
2. `layouts/main_layout.py` builds the full page skeleton:
   - Three `dcc.Store` components (data, theme, selected-frame)
   - `dcc.Location` for URL tracking
   - Header navbar
   - Sidebar + page-content `div`
   - Loading modal (hidden by default)
   - Toast notification (hidden by default)
3. All callback modules are imported in order. Each module's `@app.callback`
   decorator registers the callback with Dash's server at import time.
4. A clientside callback is registered for the theme toggle button.
5. The Flask development server starts on port 8050.

---

## Callback System

Every interactive element in Dash has an `id`. Callbacks are pure functions
decorated with `@app.callback(Output(...), Input(...))`.

### Pattern: Read-only chart callbacks

All 16 chart callbacks follow the same pattern:

```python
@app.callback(
    Output("some-graph", "figure"),      # what to update
    Output("some-table", "children"),
    Input("etabs-data-store", "data"),   # triggers on ATTACH
    Input("some-dropdown", "value"),     # triggers on user interaction
    Input("theme-store", "data"),        # triggers on theme toggle
)
def update_chart(data, dropdown_value, theme):
    theme = theme or "light"
    if not data or data.get("status") != "ok":
        return empty_fig(theme=theme), "No data"

    # ... build figure from data dict ...
    return fig, table
```

Key properties:
- **Idempotent** — same inputs always produce the same outputs.
- **No side effects** — callbacks never write back to ETABS.
- **Graceful degradation** — if `data` is `None` or missing keys, an empty
  placeholder figure is returned rather than raising an exception.

### Pattern: Populate dropdowns

Several pages have a "populate" callback that fires on store update and fills
dropdown options. This runs before the chart callback so options are available:

```python
@app.callback(
    Output("my-dropdown", "options"),
    Output("my-dropdown", "value"),
    Input("etabs-data-store", "data"),
)
def populate_dropdown(data):
    cases = get_load_cases(data)
    return [{"label": c, "value": c} for c in cases], (cases[0] if cases else None)
```

### Connection callback (`callbacks/connection.py`)

The ATTACH button callback is the only callback that calls out to ETABS.
It updates nine outputs simultaneously:
- `etabs-data-store` (the full data dict)
- `connection-badge` children and colour
- `header-model-info` text
- `loading-modal` open/closed state
- `notif-toast-body` message, header, open state, and icon

---

## Page Routing

URL routing is handled by `callbacks/navigation.py`:

```python
PAGE_MAP = {
    "/":               overview.layout,
    "/overview":       overview.layout,
    "/plan-view":      plan_view.layout,
    # ... 14 more routes ...
}

@app.callback(Output("page-content","children"), Input("url","pathname"))
def render_page(pathname):
    return PAGE_MAP.get(pathname, overview.layout)()
```

Each `layout` value is a **function** (not a component). It is called every
time the route is hit, so the layout is freshly constructed on each navigation.
This ensures that `dcc.Dropdown` components always start with their default
values when you navigate to a page.

---

## Theme System

### Server-side: Plotly charts

Every chart callback takes `Input("theme-store","data")` and passes the theme
value to `callbacks/charts/_helpers.base_layout()`:

```python
def base_layout(fig, theme="light"):
    fig.update_layout(
        template="plotly_dark" if theme == "dark" else "plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",   # transparent — CSS controls the background
        ...
    )
```

### Client-side: Bootstrap stylesheet swap

A clientside callback (JavaScript) runs entirely in the browser — no round-trip
to the Python server:

```javascript
function(n_clicks, current_theme) {
    const newTheme = current_theme === "dark" ? "light" : "dark";
    // swap Bootstrap stylesheet link href: flatly ↔ darkly
    // set data-bs-theme and data-theme on <html> and <body>
    return newTheme;
}
```

### Custom properties in CSS (`assets/custom.css`)

```css
:root {
    --sidebar-bg: #1a2332;
    --text-primary: #212529;
}
[data-theme="dark"] {
    --sidebar-bg: #0d1117;
    --text-primary: #e6edf3;
}
```

The CSS variable approach means a single attribute change on `<body>` propagates
the theme to every custom component simultaneously.

---

## Store Data Structure

The dict stored in `etabs-data-store` has this shape:

```python
{
    "status": "ok",              # "ok" or "error: <traceback>"
    "model_info": {
        "filename": "Building.edb",
        "units": "kN, m, °C",
        "units_code": 6,
        "num_stories": 10,
        "num_joints": 850,
        "num_frames": 420,
        "num_shells": 200,
        "num_load_cases": 8,
        "num_load_combos": 4,
    },
    "stories":       [{"name": "Story1", "elevation": 3.0, "height": 3.0}, ...],
    "joints":        [{"name": "1", "x": 0.0, "y": 0.0, "z": 3.0}, ...],
    "frames":        [{"name": "1", "point_i": "1", "point_j": "2",
                       "section": "W14x90",
                       "xi": 0.0, "yi": 0.0, "zi": 3.0,
                       "xj": 5.0, "yj": 0.0, "zj": 3.0}, ...],
    "shells":        [{"name": "1", "points": ["1","2","3","4"],
                       "coords": [[0,0,3],[5,0,3],[5,5,3],[0,5,3]]}, ...],
    "load_cases":    ["DEAD", "LIVE", "EX", "EY", "WX", "1.2D+1.6L"],
    "load_combos":   ["1.2D+1.6L"],
    "load_patterns": [{"name": "DEAD", "type_code": 1,
                       "type_name": "Dead", "self_wt": 1.0}, ...],
    "results": {
        "base_reactions": [
            {"load_case": "EX", "step_type": "Max", "step_num": 1,
             "Fx": 1200.0, "Fy": 0.0, "Fz": -45000.0,
             "Mx": 0.0, "My": 0.0, "Mz": 0.0}, ...
        ],
        "story_drifts": [
            {"story": "Story5", "load_case": "EX", "step_type": "Max",
             "step_num": 1, "direction": "X", "drift": 0.0045,
             "label": "J1", "x": 0.0, "y": 0.0, "z": 15.0}, ...
        ],
        "story_forces": [
            {"story": "Story5", "load_case": "EX", "step_type": "Max",
             "step_num": 1, "location": "Bottom",
             "Px": 0.0, "Py": 0.0, "Vx": 3500.0, "Vy": 0.0,
             "T": 0.0, "Mx": 0.0, "My": 12000.0}, ...
        ],
        "modal_periods": [
            {"mode": 1, "load_case": "MODAL", "period": 1.42,
             "frequency": 0.70, "circ_freq": 4.42, "eigenvalue": 19.5}, ...
        ],
        "modal_mass_ratios": [
            {"mode": 1, "Ux": 0.82, "Uy": 0.0, "Uz": 0.0,
             "Rx": 0.0, "Ry": 0.0, "Rz": 0.05,
             "sum_Ux": 0.82, "sum_Uy": 0.0,
             "sum_Rx": 0.0, "sum_Ry": 0.0}, ...
        ],
        "joint_displacements": [
            {"joint": "42", "load_case": "EX", "step_type": "Max",
             "U1": 0.018, "U2": 0.002, "U3": -0.001,
             "R1": 0.0002, "R2": 0.0001, "R3": 0.0}, ...
        ],
    }
}
```

---

## File Responsibilities

| File | Responsibility |
|---|---|
| `app.py` | Create Dash app; import all callbacks; register clientside theme callback; run server |
| `etabs_connector.py` | All ETABS API calls; returns plain JSON-safe dict |
| `data_store.py` | Thin accessor helpers — `get_load_cases(data)`, `is_attached(data)`, etc. |
| `layouts/main_layout.py` | Root HTML structure; the three Stores; loading modal; toast |
| `layouts/header.py` | Top navbar HTML — brand, model info text, connection badge, ATTACH button, theme toggle |
| `layouts/sidebar.py` | Left navigation HTML — two `dbc.Nav` sections |
| `layouts/pages/*.py` | Per-page HTML layout — controls, empty graph placeholders, table placeholders |
| `callbacks/connection.py` | ATTACH button handler — calls connector, populates store, updates badge/toast |
| `callbacks/navigation.py` | URL → page layout router; sidebar model summary |
| `callbacks/charts/_helpers.py` | `empty_fig()`, `base_layout()`, `make_table()`, colour palettes |
| `callbacks/charts/*_cb.py` | Per-page chart logic — reads store, computes, returns figures + HTML |
| `assets/custom.css` | Inter font; CSS custom properties for light/dark; sidebar, navbar, KPI card styles |
| `run_tests.py` | Self-contained 80-test suite; stubs comtypes; uses mock data |
