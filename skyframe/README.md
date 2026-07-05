# SkyFrame 1.0 — Building Analysis Studio

**Your personal ETABS-style structural analysis app, powered by [OpenSees](https://opensees.berkeley.edu/).**

> **v1.0** — 340 benchmark tests · full model → draw → assign → analyze → design
> → report workflow · builds to a self-contained desktop app. See
> [`CHANGELOG.md`](CHANGELOG.md) for the complete feature list and
> [`ROADMAP.md`](ROADMAP.md) for the wave-by-wave history.

SkyFrame wraps the research-grade, open-source OpenSees finite-element framework
(the solver trusted by earthquake-engineering researchers worldwide) in a fast,
modern, building-centric interface: grids, stories, frames, rigid diaphragms,
load patterns/cases/combinations, story drifts and modal results — the ETABS
workflow, with a solver whose source code you can actually read.

```
skyframe/
├── skyframe/
│   ├── core/          # building data model + parametric building generator
│   │   ├── model.py   #   materials, sections, stories, members, loads, combos
│   │   └── builder.py #   quick_building(): grid × stories wizard
│   ├── engine/        # OpenSeesPy translation layer + analyses
│   └── api/           # Flask REST API + single-page web app
│       └── web/       #   3D viewer, story charts, modal/reaction/force tables
├── tests/             # validation suite vs published benchmarks
├── examples/          # scripted example buildings
└── CONTRACT.md        # internal API contract
```

## Why SkyFrame over a black-box commercial tool?

* **Transparent solver** — every stiffness term comes from OpenSees, an
  open-access framework developed at UC Berkeley (PEER). No license dongle,
  no black box: the analysis code is auditable and citable.
* **Validated, not just trusted** — `tests/` reproduces published OpenSees
  example results *exactly* and cross-checks the engine against independent
  closed-form hand solutions (the same kind CSI's own ETABS *Software
  Verification* manual uses). Run `pytest` yourself, any time.
* **Modern UX** — a dark, keyboard-friendly single-page app with a 60 fps 3D
  viewer, animated mode shapes, linked story-result charts and sortable force
  tables. No ribbon toolbars from 2003.
* **Scriptable by design** — the whole model is plain Python dataclasses;
  every result is plain JSON. Automate parametric studies in a for-loop.

## Screenshots (live OpenSees solves)

| 3D model | Story results (EQX) |
|---|---|
| ![3D view](docs/screenshots/live-01-3d.png) | ![Story results](docs/screenshots/live-02-story.png) |
| **Deformed shape overlay** | **Modal results** |
| ![Deformed](docs/screenshots/live-05-deformed.png) | ![Modal](docs/screenshots/live-03-modal.png) |

## Install the desktop app (built by GitHub Actions)

Every push to the main branches (and every `v*` tag) triggers the
**Build SkyFrame desktop app** workflow (`.github/workflows/build-desktop.yml`),
which runs the full 42-benchmark validation suite and then produces
ready-to-run installers:

1. GitHub → **Actions** → *Build SkyFrame desktop app* → latest green run
   (or **Run workflow** to build on demand).
2. Download the artifact for your OS: `SkyFrame-windows` (zip) or
   `SkyFrame-linux` (tar.gz). Tagged releases (`v0.2.0` …) get the same files
   attached on the **Releases** page.
3. Unpack anywhere and run `SkyFrame` (`SkyFrame.exe` on Windows). No Python,
   no license server — the OpenSees solver is bundled and smoke-tested in CI.

The desktop shell is [pywebview](https://pywebview.flowrl.com/) — a native OS
webview (Edge WebView2 / WebKitGTK / WKWebView) around the same app, an
Electron-style experience without shipping a browser. Linux needs
`libwebkit2gtk-4.1` from your package manager; `./SkyFrame --server-only`
runs it headless in any case.

## Quickstart (from source)

```bash
pip install -e .[desktop]              # openseespy, numpy, flask, pywebview
# Linux: openseespy needs BLAS/LAPACK: apt-get install libblas3 liblapack3

skyframe                               # native desktop window
skyframe --server-only                 # or serve → http://127.0.0.1:8600
python3 -m skyframe.api.server         # same, without installing
```

Or script it:

```python
from skyframe import quick_building, OpenSeesEngine

model = quick_building(bays_x=4, bays_y=3, stories=8, quake_coeff=0.1)
results = OpenSeesEngine(model).run().to_dict()
print(results["modal"]["periods"][:3])
print(results["cases"]["EQX"]["story"]["Story1"]["drift_x"])
```

See `examples/four_story_office.py` for a complete scripted run.

## Analysis capabilities

Validated by a **204-test** suite (published OpenSees examples, closed-form
mechanics, and independent numpy cross-checks). ✅ = full UI; ⚙️ = engine +
HTTP API done, dedicated UI panel pending.

**Modelling & drawing**

| Feature | Status |
|---|---|
| ETABS-style plan **and elevation** drawing (columns, beams, braces, walls, slabs) | ✅ |
| Grid & story editors; section / material / stiffness-modifier manager | ✅ |
| Shell walls & slabs (OpenSees ShellMITC4), auto quad meshing, **openings** | ✅ |
| Frame–shell mesh compatibility (members auto-split, results re-aggregated) | ✅ |
| Membrane slabs: two-way 45° tributary load distribution | ✅ |
| Member end releases, column orientation angle, link (spring) elements | ✅ |
| Point / partial / trapezoidal member loads (exact fixed-end forces); area loads | ✅ |
| Rigid / semi-rigid (none) diaphragms with per-story override | ✅ |
| Steel W-shape library; DXF / ETABS `.e2k` / IFC import | ✅ / ⚙️ |

**Analysis**

| Feature | Status |
|---|---|
| Linear static cases, additive **and envelope** combinations | ✅ |
| Eigenvalue / modal analysis with mass-participation and Γ factors | ✅ |
| Response-spectrum analysis (CQC / SRSS, spectrum editor) | ✅ |
| Linear **and nonlinear** time-history (Newmark, Rayleigh, Steel01 hinges) | ✅ / ⚙️ |
| P-Delta; equivalent-static seismic; auto ASCE 7-style wind | ✅ |
| Nonlinear static **pushover** (displacement-controlled, plastic hinges) | ✅ |
| Staged (sequential) construction with one-shot comparison | ⚙️ |
| Mass source (e.g. DEAD + 0.25·LIVE) | ✅ |

**Results & output**

| Feature | Status |
|---|---|
| Story drifts / shears, base reactions, 11-station member force diagrams | ✅ |
| 3D deformed & mode shapes, animated modes, shell force contours | ✅ |
| Printable report generator; CSV export on every table | ✅ |
| Preliminary AISC 360 steel & ACI 318 concrete design checks | ⚙️ |

## Validation

Run the suite:

```bash
cd skyframe && python3 -m pytest tests/ -v
```

The suite pins SkyFrame's results to:

1. **Published OpenSees example outputs** — e.g. the official *Basic Truss*
   example (OpenSees wiki): computed `ux = 0.53009277 in`,
   `uy = -0.17789364 in` must match to 8 significant figures.
2. **Independent closed-form mechanics** — cantilever `PL³/3EI`, simply
   supported beam `5wL⁴/384EI` and `wL²/8`, portal-frame drift via an
   independent numpy stiffness assembly, SDOF/shear-building periods via
   independent eigenvalue solutions — the same style of independent hand
   verification CSI publishes for ETABS.
3. **Invariants** — static equilibrium of base reactions vs applied loads,
   exact linear superposition of combinations, modal mass participation
   closure.

See `tests/VALIDATION.md` for the benchmark-by-benchmark matrix with sources.

## Units

Consistent kN · m · tonne · s (E in kPa). The UI reports mm and ‰ drift where
conventional.

## License / attribution

SkyFrame is an independent open-source application built on
[OpenSeesPy](https://github.com/zhuminjie/OpenSeesPy). "ETABS" is a trademark
of Computers & Structures, Inc. — SkyFrame is not affiliated with CSI; ETABS
verification examples are referenced only as independent benchmarks.
