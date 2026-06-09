# Analytics Pages

This document describes the 6 data-science analytics pages — the statistical
methods they use, the inputs they accept, and the charts they produce.

All analytics pages read from the same `dcc.Store("etabs-data-store")` as the
structural pages. No additional ETABS API calls are made.

---

## Statistical Summary (`/statistics`)

**Layout**: `layouts/pages/statistics.py`
**Callback**: `callbacks/charts/statistics_cb.py`

Applies classical statistical analysis to drift, shear, and reaction data.
Useful for understanding the spread and central tendency of structural response
across load cases.

### Controls

- **Data type dropdown** — `drift`, `Vx`, `Vy`, `Mx`, `My`, `base_Fx`, `base_Fy`
- **Load case filter** — multi-select; leave empty for all cases
- **Theme** — inherited from `theme-store`

### Callbacks

**Populate cases** — `populate_stats_cases(data)`

**Update statistics** — `update_stats(data, data_type, filter_cases, theme)`

### Data Assembly

| Data type | Source in store | Value column |
|---|---|---|
| `drift` | `results.story_drifts` | `drift` |
| `Vx` / `Vy` | `results.story_forces` | `Vx` / `Vy` |
| `Mx` / `My` | `results.story_forces` | `Mx` / `My` |
| `base_Fx` / `base_Fy` | `results.base_reactions` | `Fx` / `Fy` |

The data is grouped by **story** (or load case, depending on type) before
computing statistics.

### Charts

**Box plot** (`go.Box` with `boxmean="sd"`)

One box per story/case showing: minimum, Q1, median, Q3, maximum. The mean is
plotted as a dashed line inside the box. Whiskers extend to 1.5×IQR.
Overlaid scatter points show all individual values.

**Violin plot** (`go.Violin`)

Same data as the box plot but shown as a kernel-density estimate (KDE) mirrored
on both sides. Better for showing bimodal or skewed distributions.

**CoV (Coefficient of Variation) bar chart** (`go.Bar`)

CoV = σ / μ for each story/group. Bars are colour-coded:
- Green: CoV ≤ 0.30 (uniform distribution across cases)
- Orange: 0.30 < CoV ≤ 0.50 (moderate variation)
- Red: CoV > 0.50 (high variation — possible loading asymmetry or outlier)

**Percentile band chart** (`go.Scatter` with `fill="tonexty"`)

Plots five percentile lines against story elevation:
- 5th percentile (lower bound)
- 25th percentile (lower IQR)
- 50th percentile / median (solid line)
- 75th percentile (upper IQR)
- 95th percentile (upper bound)

The IQR band (25th–75th) and the 90% CI band (5th–95th) are shaded to show
the expected range of response across load cases.

### Summary Statistics Table

Computed per story/group: mean, median, std deviation, 95th percentile, max,
CoV, skewness.

### Excel Export

The `dl_stats` callback exports the full statistics table to an `.xlsx` file
when the download button is clicked.

---

## Heatmap Analysis (`/heatmaps`)

**Layout**: `layouts/pages/heatmaps.py`
**Callback**: `callbacks/charts/heatmaps_cb.py`

Produces a story × load-case pivot heatmap. Each cell shows the structural
response for a specific story under a specific load case, making it easy to
spot which combinations produce the highest demands.

### Controls

- **Metric dropdown** — `drift_X`, `drift_Y`, `Vx`, `Vy`, `Mx`, `My`
- **Colour scale dropdown** — YlOrRd, Viridis, Plasma, Blues, RdBu, etc.
- **Normalisation radio** — Raw values / % of max / Z-score

### Pivot Table Construction (`_build_pivot`)

```python
pivot = pd.pivot_table(df,
    values=value_col,
    index="story",
    columns="load_case",
    aggfunc="max")
```

The stories are sorted by elevation (bottom → top) so the heatmap reads
spatially correct (roof at top, base at bottom).

### Normalisation Options

| Mode | Transformation | Use case |
|---|---|---|
| `raw` | No transformation | Absolute comparison in model units |
| `pct` | `value / max × 100` | Relative demand, unit-independent |
| `zscore` | `(value − μ) / σ` | Identify statistically unusual story/case cells |

### Charts

**Main heatmap** (`go.Heatmap`)

Stories on y-axis, load cases on x-axis. Cell colour encodes value.
Numeric annotations are shown inside each cell (rounded to 3 significant figures).

**Column totals bar** (`go.Bar`)

Sum of absolute values per load case — shows which load case produces the
highest total demand across all stories.

**Row totals bar** (`go.Bar`, horizontal)

Sum of absolute values per story — shows which story is most heavily loaded.

**Governing load case table**

For each story, the load case that produces the maximum value, and that value.

---

## Code Checks (ASCE 7) (`/code-checks`)

**Layout**: `layouts/pages/code_checks.py`
**Callback**: `callbacks/charts/code_checks_cb.py`

Checks the ETABS model period against ASCE 7-22 Section 12.8.2 requirements.

### Controls

- **Structural system dropdown** — 5 systems, each with different Ct and x values
- **Height units radio** — metres or feet (affects Ta formula)
- **Gravity and seismic load case dropdowns**

### ASCE 7-22 Period Formula

```
Ta = Ct × hn^x
```

Where `hn` is the building height to the roof (maximum story elevation).

| Structural System | Ct (m) | Ct (ft) | x |
|---|---|---|---|
| Steel MRF | 0.0853 | 0.0724 | 0.80 |
| Concrete MRF | 0.0466 | 0.0466 | 0.90 |
| Steel EBF | 0.0731 | 0.0731 | 0.75 |
| Steel CBF / Other | 0.0488 | 0.0488 | 0.75 |
| Concrete SW / Other | 0.0488 | 0.0488 | 0.75 |

Upper bound: `T_limit = Cu × Ta` where `Cu = 1.4` (ASCE 7-22 Table 12.8-1,
for Sd1 ≥ 0.4g; conservative default).

The computed period T1 is taken as the mode with the highest Ux mass
participation ratio (dominant X translation mode).

### Checks Performed

1. **T1x vs Cu×Ta** — computed X-direction period vs code upper bound
2. **T1y vs Cu×Ta** — computed Y-direction period vs code upper bound
3. Alert banners shown in red if either period exceeds the limit

### Charts

**Period comparison** (`go.Bar`)

Three bars side by side: T1x (computed), T1y (computed), Ta (approximate),
Cu×Ta (upper bound). Horizontal reference line at Cu×Ta.

**Base shear check** (`go.Bar`)

For the seismic load case: Fx and Fy reactions plotted. A Cs annotation is
computed as `V = Cs × W` and shown.

**Period trend** (`go.Scatter`)

Plots the theoretical period trend `T = 0.1 × N` (where N = number of stories)
alongside T1 from analysis — a quick sanity check.

---

## Outlier Detection (`/outliers`)

**Layout**: `layouts/pages/outliers.py`
**Callback**: `callbacks/charts/outliers_cb.py`

Identifies stories or load cases with statistically anomalous structural response.

### Controls

- **Data type dropdown** — drift, Vx, Vy, Fx (reactions)
- **σ threshold slider** — 1.5 σ to 3 σ (default 2.0 σ)
- **Load case filter** — optional

### Z-score Detection (`_zscore`)

Implemented without scipy for portability:

```python
def _zscore(series):
    mu  = series.mean()
    std = series.std()
    if std == 0:
        return pd.Series(0.0, index=series.index)
    return (series - mu) / std
```

Values with `|z| > threshold` are flagged as outliers.

### Soft-Story Detection (`_soft_story_detection`)

Uses a stiffness proxy `K ≈ Vx / drift` per story (from story forces and
story drifts). A linear regression is fitted across stories using
`numpy.polyfit`. Stories whose stiffness deviates more than 1 σ from the
trend line are flagged as potential soft stories.

### Charts

**Z-score scatter** (`go.Scatter`)

Story/case index on x-axis, structural response value on y-axis.
Points are coloured:
- Blue: normal (|z| ≤ threshold)
- Orange: marginal (threshold < |z| ≤ threshold + 0.5)
- Red: outlier (|z| > threshold + 0.5)

Reference band shown at ±threshold σ.

**Z-score bar chart** (`go.Bar`)

One bar per story showing the maximum |z| score across load cases.
Bars above the threshold are shown in red.

**Stiffness regression chart** (`go.Scatter`)

Stiffness proxy vs story elevation. A linear regression trend line overlaid.
Stories flagged as soft story candidates shown in red.

**Flagged items table**

All outlier rows with story, load case, value, and z-score.

---

## Correlation & Sensitivity (`/correlation`)

**Layout**: `layouts/pages/correlation.py`
**Callback**: `callbacks/charts/correlation_cb.py`

Explores how different structural response metrics relate to each other across
load cases, and which metrics most strongly drive peak drift.

### Controls

- **Metrics multi-select** — `max_drift_X`, `max_drift_Y`, `max_Vx`, `max_Vy`,
  `base_Fx`, `base_Fy`
- **Colour-by dropdown** — which metric colours the parallel coordinates lines

### Feature Matrix (`_build_case_feature_matrix`)

For each load case, the following features are computed:

| Feature | Computation |
|---|---|
| `max_drift_X` | Max drift where direction == "X" |
| `max_drift_Y` | Max drift where direction == "Y" |
| `max_Vx` | Max absolute Vx across all stories |
| `max_Vy` | Max absolute Vy across all stories |
| `base_Fx` | Max absolute Fx in base reactions |
| `base_Fy` | Max absolute Fy in base reactions |

This creates a DataFrame where each row is a load case and each column is a
structural response metric.

### Charts

**Parallel coordinates** (`go.Parcoords`)

Each axis is one metric. Each line is one load case. Lines are coloured by
the selected "colour-by" metric using a continuous colour scale.

Useful for visually tracing which load cases are simultaneously extreme in
multiple metrics, and spotting load cases that are outliers in only one metric.

**Pearson correlation heatmap** (`go.Heatmap`, annotated)

Pearson correlation matrix computed on the drift pivot table
(story × load case). Positive correlation (red) means the two metrics tend
to be high in the same stories/cases. Negative (blue) means they are
inversely related.

**Sensitivity bar chart** (`go.Bar`)

For each metric, the % contribution to peak drift variability is estimated as:

```python
sensitivity = |Pearson correlation with max_drift_X| / sum(all correlations) × 100
```

Sorted descending so the most influential metric appears first.

---

## Performance Scorecard (`/scorecard`)

**Layout**: `layouts/pages/scorecard.py`
**Callback**: `callbacks/charts/scorecard_cb.py`

Runs 7 ASCE 7-based structural checks and produces a single overall score
(0–100) with traffic-light pass/warn/fail cards.

### Controls

- **Drift limit input** — numeric, default 0.025 (2.5%)
- **Structural system dropdown** — affects Ta formula
- **Height units radio** — metres or feet

### Scoring Logic (`_score`)

```python
def _score(value, thresholds):
    # thresholds = [(limit, score), ...] ascending
    for limit, sc in thresholds:
        if value <= limit:
            return sc
    return 0
```

### Traffic Light (`_traffic`)

| Score | Colour | Icon | Label |
|---|---|---|---|
| ≥ 80 | success (green) | check-circle | PASS |
| 50–79 | warning (amber) | exclamation-triangle | WARN |
| < 50 | danger (red) | x-circle | FAIL |

### The 7 Checks

**1. Max Story Drift**

| Condition | Score |
|---|---|
| drift ≤ 0.5 × limit | 100 |
| drift ≤ 0.8 × limit | 80 |
| drift ≤ limit | 60 |
| drift ≤ 1.2 × limit | 30 |
| drift > 1.2 × limit | 0 |

**2. Torsional Irregularity**

Ratio = max drift at story / average of X and Y drifts at that story.
ASCE 7 Type 1a: ratio ≥ 1.2. Type 1b: ratio ≥ 1.4.

| Condition | Score |
|---|---|
| ratio ≤ 1.0 | 100 |
| ratio ≤ 1.2 | 80 |
| ratio ≤ 1.4 | 40 |
| ratio > 1.4 | 0 |

**3. Soft Story Check**

Min stiffness ratio = min(K_i / K_{i+1}) where K = Vx / drift per story.
ASCE 7 Type 2a: stiffness < 70% of story above. Type 2b: < 60%.

| Condition | Score |
|---|---|
| min ratio drop ≤ 0% | 100 |
| min ratio drop ≤ 20% | 80 |
| min ratio drop ≤ 30% | 50 |
| min ratio drop > 30% | 0 |

**4. Modal Completeness (90% mass)**

Number of modes required to reach 90% cumulative Ux mass participation.

| Condition | Score |
|---|---|
| ≤ 6 modes | 100 |
| ≤ 10 modes | 90 |
| ≤ 15 modes | 70 |
| ≤ 20 modes | 50 |
| > 20 modes | 0 |

**5. Period Ratio T₁/Ta**

T₁ = computed fundamental period (dominant Ux mode). Ta = `Ct × hn^x`.
ASCE 7: T must not exceed Cu × Ta (Cu = 1.4).

| Condition | Score |
|---|---|
| ratio ≤ 1.0 | 100 |
| ratio ≤ 1.2 | 80 |
| ratio ≤ 1.4 | 50 |
| ratio > 1.4 | 0 |

**6. Drift Uniformity (CoV)**

Coefficient of variation of drifts per story across load cases.
High CoV suggests inconsistent load distribution or asymmetric loading.

| Condition | Score |
|---|---|
| max CoV ≤ 0.2 | 100 |
| max CoV ≤ 0.3 | 80 |
| max CoV ≤ 0.5 | 50 |
| max CoV > 0.5 | 0 |

**7. Base Shear Symmetry**

`asymmetry = |Fx_max / Fy_max − 1|`

High asymmetry may indicate plan irregularity or directional loading anomaly.

| Condition | Score |
|---|---|
| asymmetry ≤ 0.15 | 100 |
| asymmetry ≤ 0.30 | 70 |
| asymmetry ≤ 0.50 | 40 |
| asymmetry > 0.50 | 0 |

### Overall Score

The overall score is the arithmetic mean of all 7 check scores, rounded to
the nearest integer. Displayed as a large number with a traffic-light banner.

### Recommendations Panel

Checks with score < 50 appear as red danger alerts.
Checks with score 50–79 appear as amber warning alerts.
If all checks pass, a single green success alert is shown.

### Excel Export (`dl_scorecard`)

Triggered by the download button. Produces a single-sheet `.xlsx` file
with columns: Check, Value, Limit, Score, Status.

Uses `io.BytesIO` + `pandas.ExcelWriter` (openpyxl engine) + `dcc.send_bytes`.
