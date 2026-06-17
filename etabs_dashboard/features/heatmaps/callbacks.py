import pandas as pd
import numpy as np
import plotly.graph_objects as go
from dash import Input, Output, html
import dash
from data_store import get_story_elevations
from shared.helpers import empty_fig, base_layout, make_table, ENGINEERING_COLORS

app = dash.get_app()


def _build_pivot(data, metric):
    """Return (pivot_df, unit_label) with stories as rows, load cases as columns."""
    elevs = get_story_elevations(data)

    if metric.startswith("drift_"):
        direction = metric.split("_")[1]
        recs = data.get("results", {}).get("story_drifts", [])
        if not recs:
            return None, ""
        df = pd.DataFrame(recs)
        df = df[df["direction"] == direction]
        val_col = "drift"
        unit = "drift ratio"
    else:
        recs = data.get("results", {}).get("story_forces", [])
        if not recs:
            return None, ""
        df = pd.DataFrame(recs)
        val_col = metric
        unit = metric

    if val_col not in df.columns:
        return None, unit

    df["value"] = pd.to_numeric(df[val_col], errors="coerce").abs()
    df["elev"]  = df["story"].map(elevs).fillna(0)
    df = df.dropna(subset=["value"])

    pivot = df.groupby(["story", "load_case"])["value"].max().unstack("load_case")
    # Sort stories by elevation
    story_order = df.drop_duplicates("story").sort_values("elev")["story"].tolist()
    pivot = pivot.reindex([s for s in story_order if s in pivot.index])
    return pivot, unit


@app.callback(
    Output("hm-main-chart",    "figure"),
    Output("hm-col-totals",    "figure"),
    Output("hm-row-totals",    "figure"),
    Output("hm-governing-table","children"),
    Output("hm-stats-summary", "children"),
    Input("etabs-data-store",  "data"),
    Input("hm-metric",         "value"),
    Input("hm-colorscale",     "value"),
    Input("hm-normalise",      "value"),
    Input("theme-store",       "data"),
)
def update_heatmaps(data, metric, colorscale, normalise, theme):
    theme = theme or "light"
    ef    = empty_fig(theme=theme)

    if not data or data.get("status") != "ok":
        return ef, ef, ef, "", ""

    pivot, unit = _build_pivot(data, metric)
    if pivot is None or pivot.empty:
        return empty_fig("No data for selected metric.", theme=theme), ef, ef, "", ""

    z = pivot.values.copy().astype(float)

    if normalise == "pct":
        global_max = np.nanmax(z)
        if global_max > 0:
            z = z / global_max * 100
        unit += " (% of max)"
    elif normalise == "zscore":
        flat = z[~np.isnan(z)]
        if flat.std() > 0:
            z = (z - flat.mean()) / flat.std()
        unit += " (σ)"

    # ── Main heatmap ──────────────────────────────────────────────────────
    hm_fig = go.Figure(go.Heatmap(
        z=z,
        x=list(pivot.columns),
        y=list(pivot.index),
        colorscale=colorscale,
        hoverongaps=False,
        hovertemplate="Story: %{y}<br>Case: %{x}<br>Value: %{z:.5f}<extra></extra>",
        colorbar={"title": unit, "thickness": 12, "len": 0.9},
        text=[[f"{v:.4f}" if not np.isnan(v) else "—" for v in row] for row in z],
        texttemplate="%{text}",
        textfont={"size": 9},
    ))
    base_layout(hm_fig, theme=theme)
    hm_fig.update_layout(
        xaxis={"title": "Load Case", "side": "bottom"},
        yaxis={"title": "Story", "autorange": "reversed"},
        margin={"t": 30, "b": 60, "l": 80, "r": 20},
    )

    # ── Column totals (sum per load case) ────────────────────────────────
    col_sums = np.nansum(z, axis=0)
    col_fig  = go.Figure(go.Bar(
        x=list(pivot.columns), y=col_sums,
        marker_color=ENGINEERING_COLORS[0],
        hovertemplate="Case: %{x}<br>Sum: %{y:.4f}<extra></extra>",
    ))
    base_layout(col_fig, theme=theme)
    col_fig.update_layout(xaxis_title="Load Case", yaxis_title="Σ", showlegend=False,
                          margin={"t": 20, "b": 50, "l": 50, "r": 10})

    # ── Row totals (sum per story) ────────────────────────────────────────
    row_sums = np.nansum(z, axis=1)
    row_fig  = go.Figure(go.Bar(
        x=row_sums, y=list(pivot.index), orientation="h",
        marker_color=ENGINEERING_COLORS[1],
        hovertemplate="Story: %{y}<br>Sum: %{x:.4f}<extra></extra>",
    ))
    base_layout(row_fig, theme=theme)
    row_fig.update_layout(xaxis_title="Σ", yaxis_title="Story", showlegend=False,
                          margin={"t": 20, "b": 40, "l": 70, "r": 10})

    # ── Governing load case per story ─────────────────────────────────────
    governing = []
    for i, story in enumerate(pivot.index):
        row = pivot.iloc[i]
        valid = row.dropna()
        if valid.empty:
            continue
        gov_case = valid.idxmax()
        gov_val  = valid.max()
        governing.append({"Story": story, "Governing Case": gov_case,
                          "Value": round(gov_val, 6)})
    gov_table = make_table(pd.DataFrame(governing)) if governing else html.P("—")

    # ── Stats summary ─────────────────────────────────────────────────────
    flat = z[~np.isnan(z)]
    stats = {
        "Global Max":    round(float(np.nanmax(z)), 6),
        "Global Min":    round(float(np.nanmin(z[z > 0])) if np.any(z > 0) else 0, 6),
        "Mean":          round(float(np.nanmean(z)), 6),
        "Std Dev":       round(float(np.nanstd(z)),  6),
        "95th Percentile": round(float(np.nanpercentile(z, 95)), 6),
    }
    import dash_bootstrap_components as dbc
    stats_div = dbc.Table(
        [html.Tbody([html.Tr([html.Td(html.Strong(k)), html.Td(v)]) for k, v in stats.items()])],
        size="sm", borderless=True,
    )

    return hm_fig, col_fig, row_fig, gov_table, stats_div
