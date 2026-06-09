import pandas as pd
import numpy as np
import plotly.graph_objects as go
from dash import Input, Output
import dash
from data_store import get_story_elevations
from callbacks.charts._helpers import empty_fig, base_layout, ENGINEERING_COLORS

app = dash.get_app()


def _build_case_feature_matrix(data, metrics):
    """Build a DataFrame: rows = load_cases, columns = selected metrics."""
    elevs  = get_story_elevations(data)
    drifts = data.get("results", {}).get("story_drifts", [])
    forces = data.get("results", {}).get("story_forces", [])
    rxns   = data.get("results", {}).get("base_reactions", [])

    dr_df  = pd.DataFrame(drifts) if drifts else pd.DataFrame()
    sf_df  = pd.DataFrame(forces) if forces else pd.DataFrame()
    rx_df  = pd.DataFrame(rxns)   if rxns   else pd.DataFrame()

    rows = {}

    all_cases = sorted(set(
        list(dr_df["load_case"].unique() if not dr_df.empty else []) +
        list(sf_df["load_case"].unique() if not sf_df.empty else []) +
        list(rx_df["load_case"].unique() if not rx_df.empty else [])
    ))

    for case in all_cases:
        row = {"load_case": case}
        if "max_drift_X" in metrics and not dr_df.empty:
            sub = dr_df[(dr_df["load_case"] == case) & (dr_df["direction"] == "X")]
            row["max_drift_X"] = sub["drift"].max() if not sub.empty else np.nan
        if "max_drift_Y" in metrics and not dr_df.empty:
            sub = dr_df[(dr_df["load_case"] == case) & (dr_df["direction"] == "Y")]
            row["max_drift_Y"] = sub["drift"].max() if not sub.empty else np.nan
        for comp in ["max_Vx", "max_Vy", "max_Mx"]:
            col = comp.replace("max_", "")
            if comp in metrics and not sf_df.empty and col in sf_df.columns:
                sub = sf_df[sf_df["load_case"] == case]
                row[comp] = sub[col].abs().max() if not sub.empty else np.nan
        for comp in ["base_Fx", "base_Fy"]:
            col = comp.replace("base_", "")
            if comp in metrics and not rx_df.empty and col in rx_df.columns:
                sub = rx_df[rx_df["load_case"] == case]
                row[comp] = sub[col].abs().max() if not sub.empty else np.nan
        rows[case] = row

    feat_df = pd.DataFrame(list(rows.values())).set_index("load_case")
    # Drop columns that are all-NaN
    feat_df = feat_df.dropna(axis=1, how="all")
    return feat_df


@app.callback(
    Output("corr-parallel-chart",   "figure"),
    Output("corr-matrix-chart",     "figure"),
    Output("corr-sensitivity-chart","figure"),
    Input("etabs-data-store",       "data"),
    Input("corr-metrics",           "value"),
    Input("corr-color-by",          "value"),
    Input("theme-store",            "data"),
)
def update_correlation(data, metrics, color_by, theme):
    theme   = theme or "light"
    ef      = empty_fig(theme=theme)
    metrics = metrics or ["max_drift_X", "max_drift_Y", "max_Vx", "max_Vy"]

    if not data or data.get("status") != "ok":
        return ef, ef, ef

    feat_df = _build_case_feature_matrix(data, metrics)
    if feat_df.empty or len(feat_df) < 2:
        return empty_fig("Insufficient data.", theme=theme), ef, ef

    feat_clean = feat_df.fillna(0)

    # ── Parallel coordinates ──────────────────────────────────────────────
    color_vals = feat_clean.get(color_by, feat_clean.iloc[:, 0]).values
    color_vals_norm = (color_vals - color_vals.min()) / ((color_vals.max() - color_vals.min()) or 1)

    dimensions = []
    for col in feat_clean.columns:
        vals = feat_clean[col].values
        dimensions.append(dict(
            label=col.replace("_", " ").replace("max ", "Max ").replace("base ", ""),
            values=vals,
            range=[vals.min(), vals.max()],
        ))

    par_fig = go.Figure(go.Parcoords(
        line=dict(
            color=color_vals_norm,
            colorscale="Portland",
            showscale=True,
            colorbar={"title": (color_by or "").replace("_"," "), "thickness": 12},
        ),
        dimensions=dimensions,
        labelangle=15,
        labelside="bottom",
    ))
    base_layout(par_fig, theme=theme)
    par_fig.update_layout(
        margin={"t": 60, "b": 80, "l": 60, "r": 60},
    )
    # Add load case labels as annotation list
    par_fig.update_layout(title={"text": "  |  ".join(feat_clean.index.tolist()[:8]),
                                  "font": {"size": 10}, "x": 0.01})

    # ── Correlation matrix ────────────────────────────────────────────────
    # Correlate across stories (use per-story drift data for richer correlation)
    drifts = data.get("results", {}).get("story_drifts", [])
    if drifts:
        dr_df = pd.DataFrame(drifts)
        pivot = dr_df.pivot_table(index="story", columns="load_case",
                                  values="drift", aggfunc="max")
        pivot = pivot.fillna(0)
        if pivot.shape[1] >= 2:
            corr = pivot.corr()
            z_vals   = corr.values
            cases_list = list(corr.columns)
            cm_fig = go.Figure(go.Heatmap(
                z=z_vals,
                x=cases_list, y=cases_list,
                colorscale="RdBu", zmin=-1, zmax=1,
                hoverongaps=False,
                hovertemplate="Case A: %{y}<br>Case B: %{x}<br>r = %{z:.3f}<extra></extra>",
                colorbar={"title": "Pearson r", "thickness": 12},
                text=[[f"{v:.2f}" for v in row] for row in z_vals],
                texttemplate="%{text}", textfont={"size": 9},
            ))
            base_layout(cm_fig, theme=theme)
            cm_fig.update_layout(
                xaxis={"title": "Load Case", "tickangle": 45},
                yaxis={"title": "Load Case", "autorange": "reversed"},
                margin={"t": 30, "b": 80, "l": 80, "r": 20},
            )
        else:
            cm_fig = empty_fig("Need ≥2 load cases.", theme=theme)
    else:
        cm_fig = empty_fig("No drift data.", theme=theme)

    # ── Sensitivity chart ─────────────────────────────────────────────────
    if "max_drift_X" in feat_clean.columns:
        sens_col = "max_drift_X"
    elif feat_clean.shape[1] > 0:
        sens_col = feat_clean.columns[0]
    else:
        sens_col = None

    if sens_col:
        vals    = feat_clean[sens_col].abs()
        total   = vals.sum()
        pct     = (vals / total * 100).sort_values(ascending=True) if total > 0 else vals

        colors = [ENGINEERING_COLORS[i % len(ENGINEERING_COLORS)]
                  for i in range(len(pct))]
        sens_fig = go.Figure(go.Bar(
            x=pct.values, y=pct.index,
            orientation="h",
            marker_color=colors,
            hovertemplate="%{y}: %{x:.1f}%<extra></extra>",
        ))
        base_layout(sens_fig, theme=theme)
        sens_fig.update_layout(
            xaxis_title=f"% contribution to peak {sens_col.replace('_',' ')}",
            yaxis_title="Load Case",
            margin={"t": 20, "b": 40, "l": 100, "r": 20},
        )
    else:
        sens_fig = ef

    return par_fig, cm_fig, sens_fig
