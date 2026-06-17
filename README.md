# ETABS Dashboard

A professional Plotly Dash application that connects live to ETABS v23, extracts every
structural result in one click, and presents it as an interactive, publication-quality
engineering dashboard — with a full data-science analytics layer on top.

---

## Features

**Structural Pages**
- **Overview** — model summary KPIs, element composition chart, load case / pattern tables
- **2D Plan View** — floor plan with clickable frames and joints, per-story selector
- **Story Drifts** — per-story drift profiles with ASCE 7 / IS 1893 limit overlays
- **Story Forces** — Vx, Vy, Mx, My envelope charts per story
- **Base Reactions** — Fx, Fy, Fz, Mx, My, Mz across all load cases and combinations
- **Frame Forces** — on-demand P, V2, V3, T, M2, M3 diagrams for any frame element
- **Modal Analysis** — period table, mode-shape mass participation, cumulative 90% check
- **Displacements** — U1, U2, U3 joint displacement envelopes per load case
- **Load Patterns** — Dead / Live / Seismic / Wind pattern visualiser
- **Torsion & Irregularity** — ASCE 7 Type 1a/1b torsional irregularity ratio chart

**Analytics Pages**
- **Statistical Summary** — box plots, violin plots, CoV (σ/μ) bar chart, percentile band chart
- **Heatmap Analysis** — story × load-case pivot; raw / % of max / z-score normalisation
- **Code Checks (ASCE 7)** — Ta period formula for 5 structural systems, Cu×Ta bound, alert banners
- **Outlier Detection** — z-score flagging per story/case, soft-story stiffness regression
- **Correlation & Sensitivity** — Pearson matrix, parallel coordinates, sensitivity bar chart
- **Performance Scorecard** — 7 ASCE 7 structural checks with traffic-light cards, Excel export

**UI / UX**
- Light / dark theme toggle (Bootstrap Flatly ↔ Darkly)
- PNG export on every chart via the Plotly toolbar
- Excel export on all data tables and the scorecard
- Responsive sidebar with Structural and Analytics navigation sections
- One-click ATTACH — no polling, no auto-refresh, no scheduled callbacks

---

## Requirements

| Requirement | Minimum |
|---|---|
| Python | 3.10 |
| ETABS | v23 (must be running on the same Windows machine) |
| OS for connection | Windows (ETABS COM API is Windows-only) |

The dashboard UI itself works cross-platform. Only the **ATTACH** button
requires Windows and a running ETABS v23 instance.

---

## Installation

**1. Clone the repository**

```
git clone https://github.com/Abhimanyu14Singh/Abhi-Singh.git
cd Abhi-Singh
```

**2. Create and activate a virtual environment**

```
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
```

**3. Install Python dependencies**

```
pip install -r etabs_dashboard/requirements.txt
```

**4. Verify the install (optional, no ETABS needed)**

```
cd etabs_dashboard
python run_tests.py
```

All 80 tests should pass on any platform.

---

## Running the Dashboard

### Option A — Double-click launcher (Windows)

Open `etabs_dashboard\run.bat`. A terminal window opens, the Dash server
starts, and the browser opens automatically at `http://localhost:8050`.

### Option B — Command line

```
cd etabs_dashboard
python app.py
```

Open your browser at `http://localhost:8050`.

---

## Connecting to an ETABS Model

1. Open **ETABS v23** and load your `.edb` model file.
2. Run the analysis — go to **Analyze → Run All**. Results must be present in memory.
3. Start the dashboard (see above).
4. Click the green **ATTACH** button in the top-right corner of the dashboard.
5. A loading spinner appears while data is extracted (typically 2–10 seconds).
6. The header bar updates with the filename, units, story count, and frame count.
7. All pages are now populated — navigate using the left sidebar.

### What happens when you click ATTACH

```
[ATTACH button click in browser]
        │
        ▼
callbacks/connection.py
        │
        ├──► etabs_connector.get_etabs_model()
        │         tries ETABSv1 Python module first
        │         falls back to comtypes COM automation
        │         returns SapModel object
        │
        ├──► etabs_connector.extract_all_data(SapModel)
        │         geometry  : stories, joints, frames, shells
        │         loading   : load cases, load patterns, combos
        │         results   : base reactions, story drifts,
        │                     story forces, modal periods,
        │                     modal mass ratios, joint displacements
        │
        └──► dcc.Store("etabs-data-store")
                  JSON dict held in browser memory
                  every page callback reads from this store
                  navigating between pages = zero ETABS calls
```

**Frame forces** are the only exception — they are fetched on-demand when you
select a frame element and click **Extract Forces** on the Frame Forces page,
because large models can have tens of thousands of frames.

---

## Repository Layout

```
Abhi-Singh/
├── README.md                          ← you are here
├── .github/
│   └── workflows/
│       └── build_release.yml          ← auto-build .exe on git tag push
│
└── etabs_dashboard/                   ← entire application
    ├── app.py                         ← Dash entry point; registers all callbacks
    ├── etabs_connector.py             ← ETABS v23 COM API connector
    ├── data_store.py                  ← helpers for querying the dcc.Store dict
    ├── requirements.txt
    ├── run.bat                        ← Windows one-click launcher
    ├── build_exe.bat                  ← local Windows .exe build script
    ├── etabs_dashboard.spec           ← PyInstaller spec for .exe packaging
    ├── run_tests.py                   ← 80-test suite (no ETABS required)
    │
    ├── assets/
    │   └── custom.css                 ← Inter font, CSS variables, dark/light theme
    │
    ├── docs/                          ← detailed technical documentation
    │   ├── ARCHITECTURE.md
    │   ├── ETABS_CONNECTOR.md
    │   ├── PAGES.md
    │   └── ANALYTICS.md
    │
    ├── shared/
    │   └── helpers.py                 ← shared Plotly utilities (empty_fig, make_table, etc.)
    │
    ├── features/                      ← one directory per page (16 total)
    │   ├── overview/
    │   │   ├── layout.py              ← HTML layout for this page
    │   │   └── callbacks.py           ← all Dash callbacks for this page
    │   ├── plan_view/
    │   ├── story_drifts/
    │   ├── story_forces/
    │   ├── base_reactions/
    │   ├── frame_forces/
    │   ├── modal/
    │   ├── displacements/
    │   ├── load_patterns/
    │   ├── torsion/
    │   ├── statistics/
    │   ├── heatmaps/
    │   ├── code_checks/
    │   ├── outliers/
    │   ├── correlation/
    │   └── scorecard/
    │
    ├── layouts/                       ← app chrome (not page-specific)
    │   ├── main_layout.py             ← root layout (Stores, header, sidebar, body)
    │   ├── header.py                  ← top navbar with ATTACH + theme buttons
    │   └── sidebar.py                 ← left nav (Structural / Analytics sections)
    │
    └── callbacks/                     ← non-page callbacks
        ├── connection.py              ← ATTACH button handler
        └── navigation.py             ← URL router + sidebar model summary
```

---

## Download the Windows App

Pre-built `.exe` files are available on the [Releases page](../../releases).

1. Go to **Releases** on the right side of the GitHub page.
2. Download `ETABS_Dashboard.exe` from the latest release.
3. Place it anywhere on your Windows PC — no Python installation required.
4. Double-click to launch. A console window opens and the dashboard appears at `http://localhost:8050`.

### Building the .exe yourself

**On Windows (local):**
```
etabs_dashboard\build_exe.bat
```
Output: `etabs_dashboard\dist\ETABS_Dashboard.exe`

**Automatically via GitHub Actions:**

Push a version tag and GitHub builds and publishes the `.exe` automatically:
```
git tag v1.0.0
git push origin v1.0.0
```
The Actions workflow (`build_release.yml`) runs on a Windows runner, builds the
executable with PyInstaller, and attaches it to a new GitHub Release.

---

## Detailed Documentation

| Document | What it covers |
|---|---|
| [Architecture](etabs_dashboard/docs/ARCHITECTURE.md) | Data flow, callback pattern, theme switching, Store design |
| [ETABS Connector](etabs_dashboard/docs/ETABS_CONNECTOR.md) | COM API connection sequence, every extracted field and its API call |
| [Structural Pages](etabs_dashboard/docs/PAGES.md) | All 10 structural pages — inputs, outputs, chart types |
| [Analytics Pages](etabs_dashboard/docs/ANALYTICS.md) | All 6 analytics pages — statistical methods and chart details |

---

## Development

Run the test suite without ETABS (works on Windows, macOS, Linux):

```
cd etabs_dashboard
python run_tests.py
```

The suite stubs `comtypes` and `ETABSv1`, builds realistic mock data
(5 stories, 60 frames, 6 load cases, 15 modes), and calls every callback
function directly. 80 tests, exits with code 1 on any failure.

---

## Troubleshooting

**"Could not connect to ETABS"**
- ETABS v23 must be open, not just installed.
- The model must be loaded and the analysis run — results must be in memory.
- Run the dashboard as the same Windows user that opened ETABS.
- If corporate IT blocks COM automation, try running both ETABS and Python as Administrator.

**Charts show "Attach to ETABS to load data"**
- Click the **ATTACH** button first; data is not loaded automatically.

**Slow ATTACH on large models**
- Joint displacements are automatically sampled to a maximum of 200 joints.
- Frame forces are never extracted on ATTACH — they are on-demand per element.

**`ModuleNotFoundError: No module named 'comtypes'`**
- Run `pip install comtypes` (Windows only package).

**PNG export fails**
- Run `pip install kaleido` and restart the server.
