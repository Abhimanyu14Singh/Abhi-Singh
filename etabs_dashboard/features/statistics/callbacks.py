import pandas as pd
import numpy as np
import plotly.graph_objects as go
from dash import Input, Output, dcc, no_update
import dash, io
from data_store import get_all_cases_and_combos, get_story_elevations
from shared.helpers import empty_fig, base_layout, make_table, ENGINEERING_COLORS

app = dash.get_app()


def _get_metric_df(data, metric, cases):
    elevs = get_story_elevations(data)
    if metric == "drift":
        recs = data.get("results", {}).get("story_drifts", [])
        if not recs:
            return pd.DataFrame()
        df = pd.DataFrame(recs)
        df = df[df["direction"] == "X"]          # max of X direction
        col = "drift"
    else:
        recs = data.get("results", {}).get("story_forces", [])
        if not recs:
            return pd.DataFrame()
        df = pd.DataFrame(recs)
        col = metric

    if cases:
        df = df[df["load_case"].isin(cases)]
    if col not in df.columns:
        return pd.DataFrame()

    df["value"] = pd.to_numeric(df[col], errors="coerce")
    df["elev"]  = df["story"].map(elevs).fillna(0)
    df = df.dropna(subset=["value"])
    return df


@app.callback(
    Output("stats-case-filter", "options"),
    Output("stats-case-filter", "value"),
    Input("etabs-data-store", "data"),
)
def populate_stats_cases(data):
    cases = get_all_cases_and_combos(data)
    return [{"label": c, "value": c} for c in cases], []


@app.callback(
    Output("stats-kpi-mean",   "children"),
    Output("stats-kpi-median", "children"),
    Output("stats-kpi-std",    "children"),
    Output("stats-kpi-p95",    "children"),
    Output("stats-kpi-cov",    "children"),
    Output("stats-kpi-skew",   "children"),
    Output("stats-box-chart",        "figure"),
    Output("stats-violin-chart",     "figure"),
    Output("stats-cov-chart",        "figure"),
    Output("stats-percentile-chart", "figure"),
    Input("etabs-data-store",  "data"),
    Input("stats-metric",      "value"),
    Input("stats-case-filter", "value"),
    Input("theme-store",       "data"),
)
def update_stats(data, metric, cases, theme):
    theme = theme or "light"
    ef    = empty_fig(theme=theme)
    nv    = ("—",) * 6

    if not data or data.get("status") != "ok":
        return *nv, ef, ef, ef, ef

    df = _get_metric_df(data, metric, cases or [])
    if df.empty:
        return *nv, empty_fig("No data for selected metric.", theme=theme), ef, ef, ef

    vals = df["value"]

    # ── KPIs ─────────────────────────────────────────────────────────────
    mean_v   = vals.mean()
    med_v    = vals.median()
    std_v    = vals.std()
    p95_v    = np.percentile(vals, 95)
    skew_v   = float(vals.skew()) if len(vals) > 2 else 0.0

    # CoV per story
    cov_df = df.groupby("story")["value"].agg(["mean","std"]).dropna()
    cov_df["cov"] = cov_df["std"] / cov_df["mean"].replace(0, np.nan)
    max_cov = cov_df["cov"].max()

    fmt = lambda v, d=4: f"{v:.{d}f}" if abs(v) >= 0.001 else f"{v:.2e}"

    kpis = (fmt(mean_v), fmt(med_v), fmt(std_v), fmt(p95_v),
            f"{max_cov:.3f}" if not np.isnan(max_cov) else "—",
            f"{skew_v:.3f}")

    stories_ordered = (df.groupby("story")["elev"].mean()
                         .sort_values().index.tolist())

    # ── Box plot ──────────────────────────────────────────────────────────
    box_fig = go.Figure()
    for i, story in enumerate(stories_ordered):
        sub = df[df["story"] == story]["value"]
        box_fig.add_trace(go.Box(
            y=sub, name=story,
            marker_color=ENGINEERING_COLORS[i % len(ENGINEERING_COLORS)],
            boxmean="sd",
            hovertemplate=f"Story: {story}<br>%{{y:.5f}}<extra></extra>",
        ))
    base_layout(box_fig, theme=theme)
    box_fig.update_layout(xaxis_title="Story", yaxis_title=metric,
                          showlegend=False)

    # ── Violin plot ───────────────────────────────────────────────────────
    vio_fig = go.Figure()
    for i, story in enumerate(stories_ordered):
        sub = df[df["story"] == story]["value"]
        if len(sub) < 2:
            continue
        vio_fig.add_trace(go.Violin(
            y=sub, name=story,
            box_visible=True, meanline_visible=True,
            fillcolor=ENGINEERING_COLORS[i % len(ENGINEERING_COLORS)],
            opacity=0.7,
            line_color=ENGINEERING_COLORS[i % len(ENGINEERING_COLORS)],
        ))
    base_layout(vio_fig, theme=theme)
    vio_fig.update_layout(xaxis_title="Story", yaxis_title=metric,
                          showlegend=False, violinmode="overlay")

    # ── CoV bar ───────────────────────────────────────────────────────────
    cov_df2 = cov_df.reset_index()
    # sort by elevation
    elev_map = df.groupby("story")["elev"].mean()
    cov_df2["elev"] = cov_df2["story"].map(elev_map).fillna(0)
    cov_df2 = cov_df2.sort_values("elev")

    cov_colors = ["#C62828" if v > 0.5 else "#F57F17" if v > 0.3 else "#2E7D32"
                  for v in cov_df2["cov"].fillna(0)]
    cov_fig = go.Figure(go.Bar(
        x=cov_df2["cov"], y=cov_df2["story"], orientation="h",
        marker_color=cov_colors,
        hovertemplate="Story: %{y}<br>CoV: %{x:.4f}<extra></extra>",
    ))
    cov_fig.add_vline(x=0.3, line_dash="dash", line_color="#F57F17",
                      annotation_text="Moderate (0.30)")
    cov_fig.add_vline(x=0.5, line_dash="dot", line_color="#C62828",
                      annotation_text="High (0.50)")
    base_layout(cov_fig, theme=theme)
    cov_fig.update_layout(xaxis_title="CoV (σ/μ)", yaxis_title="Story")

    # ── Percentile bands ─────────────────────────────────────────────────
    pct_rows = []
    for story in stories_ordered:
        sub = df[df["story"] == story]["value"]
        if sub.empty:
            continue
        elev = df[df["story"] == story]["elev"].iloc[0]
        pct_rows.append({
            "story": story, "elev": elev,
            "p5":  np.percentile(sub, 5),
            "p25": np.percentile(sub, 25),
            "p50": np.percentile(sub, 50),
            "p75": np.percentile(sub, 75),
            "p95": np.percentile(sub, 95),
        })
    pct_df = pd.DataFrame(pct_rows).sort_values("elev")

    pct_fig = go.Figure()
    # 90% band
    pct_fig.add_trace(go.Scatter(
        x=list(pct_df["p95"]) + list(pct_df["p5"])[::-1],
        y=list(pct_df["story"]) + list(pct_df["story"])[::-1],
        fill="toself", fillcolor="rgba(21,101,192,0.12)",
        line={"color": "rgba(0,0,0,0)"}, name="5th–95th %ile",
    ))
    # IQR band
    pct_fig.add_trace(go.Scatter(
        x=list(pct_df["p75"]) + list(pct_df["p25"])[::-1],
        y=list(pct_df["story"]) + list(pct_df["story"])[::-1],
        fill="toself", fillcolor="rgba(21,101,192,0.25)",
        line={"color": "rgba(0,0,0,0)"}, name="25th–75th %ile (IQR)",
    ))
    # Median line
    pct_fig.add_trace(go.Scatter(
        x=pct_df["p50"], y=pct_df["story"],
        mode="lines+markers", name="Median (50th)",
        line={"color": ENGINEERING_COLORS[0], "width": 2},
        marker={"size": 6},
    ))
    base_layout(pct_fig, theme=theme)
    pct_fig.update_layout(xaxis_title=metric, yaxis_title="Story")

    return *kpis, box_fig, vio_fig, cov_fig, pct_fig


@app.callback(
    Output("stats-download-xlsx", "data"),
    Input("stats-dl-xlsx",        "n_clicks"),
    Input("etabs-data-store",     "data"),
    Input("stats-metric",         "value"),
    Input("stats-case-filter",    "value"),
    prevent_initial_call=True,
)
def dl_stats(n, data, metric, cases):
    from dash import ctx
    if ctx.triggered_id != "stats-dl-xlsx" or not n or not data:
        return no_update
    df = _get_metric_df(data, metric, cases or [])
    if df.empty:
        return no_update

    # Per-story stats
    stats = df.groupby("story")["value"].agg(
        count="count", mean="mean", median="median",
        std="std", min="min", max="max",
        p5=lambda x: np.percentile(x, 5),
        p95=lambda x: np.percentile(x, 95),
    ).reset_index()
    stats["cov"] = stats["std"] / stats["mean"].replace(0, np.nan)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Raw")
        stats.to_excel(w, index=False, sheet_name="PerStoryStats")
    return dcc.send_bytes(buf.getvalue(), f"ETABS_Stats_{metric}.xlsx")
