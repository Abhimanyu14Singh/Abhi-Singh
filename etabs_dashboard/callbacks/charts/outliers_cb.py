import pandas as pd
import numpy as np
import plotly.graph_objects as go
from dash import Input, Output, html
import dash
import dash_bootstrap_components as dbc
from data_store import get_all_cases_and_combos, get_story_elevations
from callbacks.charts._helpers import empty_fig, base_layout, make_table, ENGINEERING_COLORS

app = dash.get_app()


def _zscore(series):
    m, s = series.mean(), series.std()
    if s == 0:
        return pd.Series(0.0, index=series.index)
    return (series - m) / s


def _get_outlier_df(data, metric, cases):
    elevs = get_story_elevations(data)
    if metric == "drift":
        recs = data.get("results", {}).get("story_drifts", [])
        df   = pd.DataFrame(recs) if recs else pd.DataFrame()
        col  = "drift"
    elif metric in ("Vx", "Vy"):
        recs = data.get("results", {}).get("story_forces", [])
        df   = pd.DataFrame(recs) if recs else pd.DataFrame()
        col  = metric
    else:
        recs = data.get("results", {}).get("base_reactions", [])
        df   = pd.DataFrame(recs) if recs else pd.DataFrame()
        col  = metric

    if df.empty or col not in df.columns:
        return pd.DataFrame()

    if cases:
        df = df[df["load_case"].isin(cases)]

    df["value"] = pd.to_numeric(df[col], errors="coerce").abs()
    df = df.dropna(subset=["value"])
    df["elev"]   = df.get("story", pd.Series([""] * len(df))).map(
        lambda s: elevs.get(s, 0) if isinstance(s, str) else 0)
    df["zscore"] = _zscore(df["value"])
    return df


@app.callback(
    Output("out-case-filter", "options"),
    Output("out-case-filter", "value"),
    Input("etabs-data-store", "data"),
)
def populate_out_cases(data):
    cases = get_all_cases_and_combos(data)
    return [{"label": c, "value": c} for c in cases], []


@app.callback(
    Output("out-flag-banner",    "children"),
    Output("out-scatter-chart",  "figure"),
    Output("out-zscore-bar",     "figure"),
    Output("out-flagged-table",  "children"),
    Output("out-softstory-chart","figure"),
    Output("out-stats-table",    "children"),
    Input("etabs-data-store",      "data"),
    Input("out-metric",            "value"),
    Input("out-zscore-threshold",  "value"),
    Input("out-case-filter",       "value"),
    Input("theme-store",           "data"),
)
def update_outliers(data, metric, threshold, cases, theme):
    theme     = theme or "light"
    ef        = empty_fig(theme=theme)
    threshold = threshold or 2.0

    if not data or data.get("status") != "ok":
        return "", ef, ef, "", ef, ""

    df = _get_outlier_df(data, metric, cases or [])
    if df.empty:
        return "", empty_fig("No data.", theme=theme), ef, "", ef, ""

    outliers = df[df["zscore"].abs() >= threshold]
    marginal = df[(df["zscore"].abs() >= threshold * 0.8) & (df["zscore"].abs() < threshold)]
    normal   = df[df["zscore"].abs() < threshold * 0.8]

    # ── Flag banner ───────────────────────────────────────────────────────
    if outliers.empty:
        banner = dbc.Alert([
            html.I(className="bi bi-check-circle-fill me-2"),
            f"No outliers detected beyond {threshold}σ threshold ({len(df)} data points analysed).",
        ], color="success", className="py-2")
    else:
        banner = dbc.Alert([
            html.I(className="bi bi-exclamation-triangle-fill me-2"),
            html.Strong(f"{len(outliers)} outlier{'s' if len(outliers)>1 else ''} detected"),
            f" beyond {threshold}σ out of {len(df)} data points.",
        ], color="danger", className="py-2")

    # ── Scatter chart ─────────────────────────────────────────────────────
    story_col = "story" if "story" in df.columns else "load_case"
    scatter_fig = go.Figure()
    for subset, color, name in [
        (normal,   "#90CAF9", "Normal"),
        (marginal, "#FFB74D", "Marginal"),
        (outliers, "#EF5350", "Outlier"),
    ]:
        if subset.empty:
            continue
        scatter_fig.add_trace(go.Scatter(
            x=subset["value"],
            y=subset[story_col] if story_col in subset.columns else list(range(len(subset))),
            mode="markers",
            marker={"size": 8, "color": color,
                    "line": {"width": 1, "color": "rgba(0,0,0,0.3)"}},
            name=name,
            customdata=subset["zscore"].round(3),
            hovertemplate=f"Value: %{{x:.5f}}<br>z = %{{customdata}}<extra>{name}</extra>",
        ))
    # Mean and ±threshold×σ lines
    m, s = df["value"].mean(), df["value"].std()
    for val, label, color in [
        (m,             "Mean",        "#1565C0"),
        (m + threshold*s, f"+{threshold}σ","#C62828"),
        (m - threshold*s, f"−{threshold}σ","#C62828"),
    ]:
        if val >= 0:
            scatter_fig.add_vline(x=val, line_dash="dash", line_color=color,
                                  line_width=1.5, annotation_text=label)
    base_layout(scatter_fig, theme=theme)
    scatter_fig.update_layout(xaxis_title=metric, yaxis_title=story_col.title())

    # ── Z-score bar per story ─────────────────────────────────────────────
    if "story" in df.columns:
        zbar_df = df.groupby("story")["zscore"].apply(
            lambda x: x.abs().max()).reset_index()
        zbar_df.columns = ["story","max_abs_z"]
        zbar_df["elev"] = zbar_df["story"].map(get_story_elevations(data)).fillna(0)
        zbar_df = zbar_df.sort_values("elev")
        zbar_colors = ["#EF5350" if z >= threshold else
                       "#FFB74D" if z >= threshold * 0.8 else "#90CAF9"
                       for z in zbar_df["max_abs_z"]]
        zbar_fig = go.Figure(go.Bar(
            x=zbar_df["max_abs_z"], y=zbar_df["story"], orientation="h",
            marker_color=zbar_colors,
            hovertemplate="Story: %{y}<br>Max |z|: %{x:.3f}<extra></extra>",
        ))
        zbar_fig.add_vline(x=threshold, line_dash="dash", line_color="#C62828")
        base_layout(zbar_fig, theme=theme)
        zbar_fig.update_layout(xaxis_title="Max |z-score|", yaxis_title="Story",
                                margin={"t": 20, "b": 30, "l": 70, "r": 10})
    else:
        zbar_fig = ef

    # ── Flagged items table ───────────────────────────────────────────────
    if not outliers.empty:
        show_cols = [c for c in ["story","load_case","direction","value","zscore"]
                     if c in outliers.columns]
        flagged_table = make_table(
            outliers[show_cols].rename(columns={"zscore":"z-score"})
                               .sort_values("z-score", key=abs, ascending=False)
        )
    else:
        flagged_table = html.P("No flagged items.", className="text-muted small")

    # ── Statistical soft story detection ─────────────────────────────────
    soft_fig = _soft_story_detection(data, theme)

    # ── Full stats table ──────────────────────────────────────────────────
    summary = pd.DataFrame([{
        "Stat": k, "Value": round(v, 6)
    } for k, v in {
        "N":       len(df),
        "Mean":    m,
        "Std Dev": s,
        "Min":     df["value"].min(),
        "Max":     df["value"].max(),
        "P5":      np.percentile(df["value"], 5),
        "P95":     np.percentile(df["value"], 95),
        "Outliers (>{:.1f}σ)".format(threshold): len(outliers),
    }.items()])
    stats_table = make_table(summary)

    return banner, scatter_fig, zbar_fig, flagged_table, soft_fig, stats_table


def _soft_story_detection(data, theme):
    elevs  = get_story_elevations(data)
    drifts = data.get("results", {}).get("story_drifts", [])
    forces = data.get("results", {}).get("story_forces", [])
    if not drifts or not forces:
        return empty_fig("Need both drift and force data.", theme=theme)

    dr_df  = pd.DataFrame(drifts)
    sf_df  = pd.DataFrame(forces)
    dr_max = dr_df.groupby("story")["drift"].max().reset_index()
    sf_max = sf_df.groupby("story")["Vx"].max().reset_index() if "Vx" in sf_df.columns else pd.DataFrame()
    if sf_max.empty:
        return empty_fig("No Vx data for stiffness proxy.", theme=theme)

    merged = pd.merge(sf_max, dr_max, on="story")
    merged["K_proxy"] = merged["Vx"].abs() / merged["drift"].clip(lower=1e-9)
    merged["elev"]    = merged["story"].map(elevs).fillna(0)
    merged = merged.sort_values("elev").reset_index(drop=True)

    # Linear regression on K vs elevation
    x = merged["elev"].values.astype(float)
    y = merged["K_proxy"].values.astype(float)
    if len(x) < 2:
        return empty_fig("Need ≥2 stories.", theme=theme)

    coeffs = np.polyfit(x, y, 1)
    trend  = np.polyval(coeffs, x)
    resid  = y - trend
    std_r  = resid.std()
    merged["residual"] = resid
    merged["flag"]     = merged["residual"].abs() > std_r

    colors = ["#EF5350" if f else ENGINEERING_COLORS[0] for f in merged["flag"]]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=merged["K_proxy"], y=merged["story"],
        mode="markers", name="Story Stiffness (proxy)",
        marker={"size": 10, "color": colors,
                "line": {"width": 1, "color": "rgba(0,0,0,0.3)"}},
        hovertemplate="Story: %{y}<br>K: %{x:.1f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=trend, y=merged["story"], mode="lines",
        name="Regression trend",
        line={"dash": "dash", "color": "#F57F17", "width": 2},
    ))
    base_layout(fig, theme=theme)
    fig.update_layout(xaxis_title="Stiffness Proxy (Vx / drift)",
                      yaxis_title="Story",
                      legend={"orientation": "h"})
    return fig
