import pandas as pd
import numpy as np
import plotly.graph_objects as go
from dash import Input, Output, dcc, html, no_update
import dash, io
import dash_bootstrap_components as dbc
from data_store import get_all_cases_and_combos, get_story_elevations
from shared.helpers import empty_fig, base_layout, make_table, ENGINEERING_COLORS

app = dash.get_app()


@app.callback(
    Output("torsion-case-select", "options"),
    Output("torsion-case-select", "value"),
    Input("etabs-data-store",     "data"),
)
def populate_torsion_cases(data):
    cases = get_all_cases_and_combos(data)
    opts  = [{"label": c, "value": c} for c in cases]
    return opts, cases[0] if cases else None


@app.callback(
    Output("torsion-irregularity-cards", "children"),
    Output("torsion-ratio-chart",        "figure"),
    Output("torsion-stiffness-chart",    "figure"),
    Output("torsion-bubble-chart",       "figure"),
    Output("torsion-mass-chart",         "figure"),
    Input("etabs-data-store",            "data"),
    Input("torsion-case-select",         "value"),
    Input("theme-store",                 "data"),
)
def update_torsion(data, case, theme):
    theme = theme or "light"
    ef    = empty_fig(theme=theme)

    if not data or data.get("status") != "ok":
        return "", ef, ef, ef, ef

    elevs  = get_story_elevations(data)
    drifts = data.get("results", {}).get("story_drifts", [])
    stories = data.get("stories", [])

    if not drifts:
        return (
            dbc.Alert("No story drift data available for irregularity checks.", color="warning"),
            ef, ef, ef, ef,
        )

    dr_df = pd.DataFrame(drifts)
    if case:
        dr_df = dr_df[dr_df["load_case"] == case]

    dr_df["elev"] = dr_df["story"].map(elevs).fillna(0)
    dr_df = dr_df.sort_values("elev")

    # ── Torsional irregularity ratio per story ───────────────────────────
    torsion_rows = []
    for story in dr_df["story"].unique():
        sub = dr_df[dr_df["story"] == story]
        for direc in ["X", "Y"]:
            vals = sub[sub["direction"] == direc]["drift"]
            if vals.empty:
                continue
            max_v = vals.max()
            avg_v = vals.mean()
            ratio = max_v / avg_v if avg_v > 0 else 1.0
            torsion_rows.append({
                "story":   story,
                "direction": direc,
                "max_drift": max_v,
                "avg_drift": avg_v,
                "ratio":     round(ratio, 4),
                "elev":      elevs.get(story, 0),
            })

    tor_df = pd.DataFrame(torsion_rows).sort_values("elev")

    # Irregularity flag cards
    cards = _make_irregularity_cards(tor_df)

    # Torsional ratio chart
    ratio_fig = go.Figure()
    for direc, color in [("X", ENGINEERING_COLORS[0]), ("Y", ENGINEERING_COLORS[1])]:
        sub = tor_df[tor_df["direction"] == direc]
        ratio_fig.add_trace(go.Bar(
            x=sub["ratio"], y=sub["story"], orientation="h", name=f"Dir {direc}",
            marker_color=color,
            hovertemplate=f"Story: %{{y}}<br>Ratio: %{{x:.4f}}<extra>Dir {direc}</extra>",
        ))
    ratio_fig.add_vline(x=1.2, line_dash="dash", line_color="#F57F17", line_width=2,
                        annotation_text="Type 1a (1.2)", annotation_position="top right")
    ratio_fig.add_vline(x=1.4, line_dash="dot",  line_color="#C62828", line_width=2,
                        annotation_text="Type 1b (1.4)", annotation_position="top right")
    base_layout(ratio_fig, theme=theme)
    ratio_fig.update_layout(barmode="overlay", xaxis_title="Max/Avg Drift Ratio",
                            yaxis_title="Story", bargap=0.3)

    # ── Story stiffness (Vx/drift as proxy) ──────────────────────────────
    sf_recs = data.get("results", {}).get("story_forces", [])
    stiff_fig = _stiffness_chart(sf_recs, dr_df, elevs, case, theme)

    # ── Torsion bubble chart ──────────────────────────────────────────────
    if not tor_df.empty:
        x_df = tor_df[tor_df["direction"] == "X"].set_index("story")["max_drift"]
        y_df = tor_df[tor_df["direction"] == "Y"].set_index("story")["max_drift"]
        r_df = tor_df.groupby("story")["ratio"].max()
        story_order = tor_df.drop_duplicates("story").sort_values("elev")["story"].tolist()

        bub_x = [x_df.get(s, 0) for s in story_order]
        bub_y = [y_df.get(s, 0) for s in story_order]
        bub_r = [r_df.get(s, 1) for s in story_order]
        colors = list(range(len(story_order)))

        bubble_fig = go.Figure(go.Scatter(
            x=bub_x, y=bub_y, mode="markers+text",
            text=story_order, textposition="top center",
            marker=dict(
                size=[max(8, r * 20) for r in bub_r],
                color=colors, colorscale="RdYlGn_r",
                showscale=True, colorbar={"title": "Story"},
                line={"width": 1},
            ),
            hovertemplate=(
                "Story: %{text}<br>Drift X: %{x:.4f}<br>Drift Y: %{y:.4f}"
                "<extra></extra>"
            ),
        ))
        base_layout(bubble_fig, theme=theme)
        bubble_fig.update_layout(xaxis_title="Max Drift X", yaxis_title="Max Drift Y")
    else:
        bubble_fig = ef

    # ── Story mass chart ──────────────────────────────────────────────────
    mass_fig = _mass_chart(stories, elevs, theme)

    return cards, ratio_fig, stiff_fig, bubble_fig, mass_fig


def _make_irregularity_cards(tor_df):
    cards = []
    if tor_df.empty:
        return dbc.Alert("No torsional data computed.", color="secondary")

    max_ratio = tor_df["ratio"].max()
    if max_ratio >= 1.4:
        cards.append(dbc.Alert([
            html.I(className="bi bi-exclamation-octagon-fill me-2"),
            html.Strong("Extreme Torsional Irregularity — Type 1b"),
            html.Span(f" | Max ratio: {max_ratio:.3f} ≥ 1.4", className="ms-2"),
        ], color="danger", className="py-2"))
    elif max_ratio >= 1.2:
        cards.append(dbc.Alert([
            html.I(className="bi bi-exclamation-triangle-fill me-2"),
            html.Strong("Torsional Irregularity — Type 1a"),
            html.Span(f" | Max ratio: {max_ratio:.3f} ≥ 1.2", className="ms-2"),
        ], color="warning", className="py-2"))
    else:
        cards.append(dbc.Alert([
            html.I(className="bi bi-check-circle-fill me-2"),
            html.Strong("No Torsional Irregularity"),
            html.Span(f" | Max ratio: {max_ratio:.3f} < 1.2", className="ms-2"),
        ], color="success", className="py-2"))

    return html.Div(cards)


def _stiffness_chart(sf_recs, dr_df, elevs, case, theme):
    ef = empty_fig(theme=theme)
    if not sf_recs:
        return empty_fig("No story force data for stiffness check.", theme=theme)
    sf_df = pd.DataFrame(sf_recs)
    if case:
        sf_df = sf_df[sf_df["load_case"] == case]
    if sf_df.empty or dr_df.empty:
        return ef

    # Proxy: Kx = Vx / drift_x
    sf_max = sf_df.groupby("story")[["Vx","Vy"]].max().reset_index()
    drift_col = "max_drift" if "max_drift" in dr_df.columns else "drift"
    dr_max = dr_df.groupby("story")[drift_col].max().reset_index()
    dr_max.columns = ["story", "max_drift"]
    merged = pd.merge(sf_max, dr_max, on="story", how="inner")
    merged["Kx_proxy"] = merged["Vx"].abs() / merged["max_drift"].clip(lower=1e-9)
    merged["elev"]     = merged["story"].map(elevs).fillna(0)
    merged = merged.sort_values("elev")

    ratio = merged["Kx_proxy"] / merged["Kx_proxy"].shift(-1).fillna(merged["Kx_proxy"])
    merged["stiff_ratio"] = ratio

    colors = []
    for r in merged["stiff_ratio"]:
        if r < 0.60:
            colors.append("#C62828")
        elif r < 0.70:
            colors.append("#F57F17")
        else:
            colors.append("#2E7D32")

    fig = go.Figure(go.Bar(
        x=merged["stiff_ratio"], y=merged["story"], orientation="h",
        marker_color=colors,
        hovertemplate="Story: %{y}<br>Stiffness Ratio: %{x:.3f}<extra></extra>",
    ))
    fig.add_vline(x=0.70, line_dash="dash", line_color="#F57F17", line_width=2,
                  annotation_text="Type 2a (0.70)")
    fig.add_vline(x=0.60, line_dash="dot",  line_color="#C62828", line_width=2,
                  annotation_text="Type 2b (0.60)")
    base_layout(fig, theme=theme)
    fig.update_layout(xaxis_title="Stiffness Ratio (story / story above)", yaxis_title="Story")
    return fig


def _mass_chart(stories, elevs, theme):
    if not stories:
        return empty_fig("No story data.", theme=theme)
    # Use story height as a mass proxy if actual mass not available
    df = pd.DataFrame(stories).sort_values("elevation")
    fig = go.Figure(go.Bar(
        x=df["height"], y=df["name"], orientation="h",
        marker_color=ENGINEERING_COLORS[0],
        hovertemplate="Story: %{y}<br>Height: %{x:.3f}<extra></extra>",
        name="Story Height (mass proxy)",
    ))
    base_layout(fig, theme=theme)
    fig.update_layout(xaxis_title="Story Height (model units)", yaxis_title="Story")
    return fig


@app.callback(
    Output("torsion-download-xlsx", "data"),
    Input("torsion-dl-xlsx",        "n_clicks"),
    Input("etabs-data-store",       "data"),
    prevent_initial_call=True,
)
def dl_torsion(n, data):
    from dash import ctx
    if ctx.triggered_id != "torsion-dl-xlsx" or not n:
        return no_update
    drifts = data.get("results", {}).get("story_drifts", [])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(drifts).to_excel(w, index=False, sheet_name="TorsionData")
    return dcc.send_bytes(buf.getvalue(), "ETABS_Torsion.xlsx")
