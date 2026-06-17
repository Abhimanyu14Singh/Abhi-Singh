import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, html, dcc, no_update
import dash
import io, base64
from data_store import get_all_cases_and_combos, get_story_elevations, to_df
from shared.helpers import empty_fig, base_layout, make_table, ENGINEERING_COLORS

app = dash.get_app()

LIMIT_COLORS = {"pass": "#2E7D32", "warn": "#F57F17", "fail": "#C62828"}


def _build_drift_df(data, cases, direction):
    recs = data.get("results", {}).get("story_drifts", [])
    if not recs:
        return pd.DataFrame()
    df = pd.DataFrame(recs)
    df["drift_pct"] = df["drift"] * 100
    if cases:
        df = df[df["load_case"].isin(cases)]
    if direction != "Both":
        df = df[df["direction"] == direction]
    return df


@app.callback(
    Output("drift-case-select", "options"),
    Output("drift-case-select", "value"),
    Input("etabs-data-store", "data"),
)
def populate_cases(data):
    cases = get_all_cases_and_combos(data)
    opts = [{"label": c, "value": c} for c in cases]
    return opts, cases[:3] if len(cases) >= 3 else cases


@app.callback(
    Output("drift-profile-chart",     "figure"),
    Output("drift-bar-chart",         "figure"),
    Output("drift-irregularity-flags","children"),
    Output("drift-data-table",        "children"),
    Input("etabs-data-store",         "data"),
    Input("drift-case-select",        "value"),
    Input("drift-direction",          "value"),
    Input("drift-limit-select",       "value"),
    Input("theme-store",              "data"),
)
def update_drift_charts(data, cases, direction, limit, theme):
    theme = theme or "light"
    if not data or data.get("status") != "ok":
        ef = empty_fig(theme=theme)
        return ef, ef, "", ""

    elevs = get_story_elevations(data)
    df = _build_drift_df(data, cases, direction)
    if df.empty:
        ef = empty_fig("No story drift results available.", theme=theme)
        return ef, ef, "", ""

    # ── Profile chart (horizontal bars) ──────────────────────────────────
    profile_fig = go.Figure()
    color_idx = 0
    for case in df["load_case"].unique():
        for direc in df["direction"].unique():
            sub = df[(df["load_case"] == case) & (df["direction"] == direc)].copy()
            sub["elev"] = sub["story"].map(elevs).fillna(0)
            sub = sub.sort_values("elev")
            lbl = f"{case} ({direc})"
            color = ENGINEERING_COLORS[color_idx % len(ENGINEERING_COLORS)]
            color_idx += 1
            profile_fig.add_trace(go.Scatter(
                x=sub["drift_pct"], y=sub["story"],
                mode="lines+markers", name=lbl,
                line={"color": color, "width": 2},
                marker={"size": 7},
                hovertemplate=f"<b>{lbl}</b><br>Story: %{{y}}<br>Drift: %{{x:.4f}}%<extra></extra>",
            ))

    if limit:
        limit_pct = limit * 100
        profile_fig.add_vline(
            x=limit_pct, line_dash="dash", line_color="#C62828", line_width=2,
            annotation_text=f"Limit {limit_pct:.2f}%",
            annotation_position="top right",
        )

    base_layout(profile_fig, theme=theme)
    profile_fig.update_layout(
        xaxis_title="Drift Ratio (%)",
        yaxis_title="Story",
        xaxis={"showgrid": True},
    )

    # ── Bar chart (max drift per story) ──────────────────────────────────
    max_df = df.groupby("story")["drift_pct"].max().reset_index()
    elev_map = elevs
    max_df["elev"] = max_df["story"].map(elev_map).fillna(0)
    max_df = max_df.sort_values("elev")

    bar_colors = [
        ("#C62828" if (limit and d/100 > limit) else "#1565C0")
        for d in max_df["drift_pct"]
    ]
    bar_fig = go.Figure(go.Bar(
        x=max_df["drift_pct"],
        y=max_df["story"],
        orientation="h",
        marker_color=bar_colors,
        hovertemplate="Story: %{y}<br>Max Drift: %{x:.4f}%<extra></extra>",
        name="Max Drift",
    ))
    if limit:
        bar_fig.add_vline(
            x=limit * 100, line_dash="dash", line_color="#C62828", line_width=2,
            annotation_text=f"Limit {limit*100:.2f}%",
        )
    base_layout(bar_fig, theme=theme)
    bar_fig.update_layout(xaxis_title="Max Drift Ratio (%)", yaxis_title="Story")

    # ── Irregularity flags ────────────────────────────────────────────────
    flags = _build_irregularity_flags(df, limit)

    # ── Data table ───────────────────────────────────────────────────────
    show_df = df[["story","load_case","direction","drift","drift_pct"]].copy()
    show_df.columns = ["Story","Load Case","Dir","Drift Ratio","Drift (%)"]
    table = make_table(show_df)

    return profile_fig, bar_fig, flags, table


def _build_irregularity_flags(df, limit):
    import dash_bootstrap_components as dbc
    badges = []
    if limit:
        fails = df[df["drift"] > limit]
        if fails.empty:
            badges.append(dbc.Alert(
                [html.I(className="bi bi-check-circle-fill me-2"),
                 f"All stories comply with drift limit {limit*100:.2f}%."],
                color="success", className="py-2 mb-0",
            ))
        else:
            stories = ", ".join(fails["story"].unique()[:5])
            badges.append(dbc.Alert(
                [html.I(className="bi bi-exclamation-triangle-fill me-2"),
                 f"Drift limit exceeded at: {stories}"],
                color="danger", className="py-2 mb-0",
            ))

    # Torsional irregularity check
    if "direction" in df.columns and len(df["direction"].unique()) > 1:
        for case in df["load_case"].unique():
            sub = df[df["load_case"] == case]
            for story in sub["story"].unique():
                sx = sub[(sub["story"] == story) & (sub["direction"] == "X")]["drift"]
                sy = sub[(sub["story"] == story) & (sub["direction"] == "Y")]["drift"]
                if not sx.empty and not sy.empty:
                    mx, my = sx.max(), sy.max()
                    avg = (mx + my) / 2
                    if avg > 0 and max(mx, my) / avg > 1.4:
                        badges.append(dbc.Alert(
                            [html.I(className="bi bi-exclamation-octagon-fill me-2"),
                             f"Extreme Torsional Irregularity (Type 1b) at Story {story} — {case}"],
                            color="danger", className="py-2 mt-1 mb-0",
                        ))
                        break
                    elif avg > 0 and max(mx, my) / avg > 1.2:
                        badges.append(dbc.Alert(
                            [html.I(className="bi bi-exclamation-triangle-fill me-2"),
                             f"Torsional Irregularity (Type 1a) at Story {story} — {case}"],
                            color="warning", className="py-2 mt-1 mb-0",
                        ))
                        break
    return badges if badges else ""


@app.callback(
    Output("drift-download-xlsx", "data"),
    Input("drift-dl-xlsx",        "n_clicks"),
    Input("etabs-data-store",     "data"),
    Input("drift-case-select",    "value"),
    Input("drift-direction",      "value"),
    prevent_initial_call=True,
)
def download_drift_excel(n_clicks, data, cases, direction):
    from dash import ctx
    if ctx.triggered_id != "drift-dl-xlsx" or not n_clicks:
        return no_update
    df = _build_drift_df(data, cases, direction)
    if df.empty:
        return no_update
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="StoryDrifts")
    return dcc.send_bytes(buf.getvalue(), "ETABS_StoryDrifts.xlsx")
