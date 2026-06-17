import pandas as pd
import numpy as np
import plotly.graph_objects as go
from dash import Input, Output, html, no_update
import dash
import dash_bootstrap_components as dbc
from shared.helpers import empty_fig, base_layout, make_table, ENGINEERING_COLORS
from data_store import get_all_cases_and_combos

app = dash.get_app()

# ASCE 7-22 Table 12.8-2  (Ct, x) for hn in METRES
SYSTEM_PARAMS_M = {
    "steel_mrf":  (0.0853, 0.80),
    "conc_mrf":   (0.0466, 0.90),
    "steel_ebf":  (0.0731, 0.75),
    "steel_brbf": (0.0731, 0.75),
    "other":      (0.0488, 0.75),
}
# Same in FEET
SYSTEM_PARAMS_FT = {
    "steel_mrf":  (0.0724, 0.80),
    "conc_mrf":   (0.0466, 0.90),
    "steel_ebf":  (0.0731, 0.75),
    "steel_brbf": (0.0731, 0.75),
    "other":      (0.0488, 0.75),
}
Cu = 1.4  # ASCE 7 upper bound coefficient


@app.callback(
    Output("cc-dead-case",    "options"),
    Output("cc-seismic-case", "options"),
    Input("etabs-data-store", "data"),
)
def populate_cc_cases(data):
    cases = get_all_cases_and_combos(data)
    opts  = [{"label": c, "value": c} for c in cases]
    return opts, opts


@app.callback(
    Output("cc-alert-row",          "children"),
    Output("cc-period-chart",       "figure"),
    Output("cc-period-table",       "children"),
    Output("cc-building-params",    "children"),
    Output("cc-baseshear-chart",    "figure"),
    Output("cc-period-trend-chart", "figure"),
    Input("etabs-data-store",   "data"),
    Input("cc-struct-system",   "value"),
    Input("cc-height-unit",     "value"),
    Input("cc-dead-case",       "value"),
    Input("cc-seismic-case",    "value"),
    Input("theme-store",        "data"),
)
def update_code_checks(data, system, h_unit, dead_case, seismic_case, theme):
    theme = theme or "light"
    ef    = empty_fig(theme=theme)

    if not data or data.get("status") != "ok":
        return "", ef, "", "", ef, ef

    stories = data.get("stories", [])
    if not stories:
        return "", empty_fig("No story data.", theme=theme), "", "", ef, ef

    max_elev = max(s.get("elevation", 0) for s in stories)
    hn = max_elev  # height in model units

    # Convert if needed: model unit vs Ta formula unit
    params = SYSTEM_PARAMS_M if h_unit == "m" else SYSTEM_PARAMS_FT
    Ct, x  = params.get(system, params["other"])
    Ta     = Ct * (hn ** x)
    Cu_Ta  = Cu * Ta

    # ── Modal periods ─────────────────────────────────────────────────────
    periods_recs = data.get("results", {}).get("modal_periods", [])
    ratios_recs  = data.get("results", {}).get("modal_mass_ratios", [])
    pr_df = pd.DataFrame(periods_recs) if periods_recs else pd.DataFrame()
    ra_df = pd.DataFrame(ratios_recs)  if ratios_recs  else pd.DataFrame()

    # Dominant T in X and Y
    T1x = T1y = None
    if not ra_df.empty and not pr_df.empty:
        merged = pd.merge(pr_df, ra_df, on="mode", how="left")
        if "Ux" in merged.columns:
            idx = merged["Ux"].idxmax()
            T1x = merged.loc[idx, "period"]
        if "Uy" in merged.columns:
            idx = merged["Uy"].idxmax()
            T1y = merged.loc[idx, "period"]

    # ── Alert row ─────────────────────────────────────────────────────────
    alerts = []
    for label, T1 in [("X", T1x), ("Y", T1y)]:
        if T1 is None:
            continue
        ratio = T1 / Ta if Ta > 0 else 0
        if T1 > Cu_Ta:
            alerts.append(dbc.Alert([
                html.I(className="bi bi-exclamation-triangle-fill me-2"),
                html.Strong(f"T₁{label} ({T1:.3f}s) exceeds Cu×Ta ({Cu_Ta:.3f}s) — "
                            f"ASCE 7 §12.8.2 requires T ≤ Cu×Ta for seismic design."),
            ], color="danger", className="py-2"))
        elif ratio > 1.2:
            alerts.append(dbc.Alert([
                html.I(className="bi bi-exclamation-triangle me-2"),
                f"T₁{label} ({T1:.3f}s) is {ratio:.2f}× Ta — approaching upper bound.",
            ], color="warning", className="py-2"))
        else:
            alerts.append(dbc.Alert([
                html.I(className="bi bi-check-circle-fill me-2"),
                f"T₁{label} ({T1:.3f}s) within bounds. T/Ta = {ratio:.2f}.",
            ], color="success", className="py-2"))

    # ── Period comparison chart ───────────────────────────────────────────
    period_fig = go.Figure()
    if not pr_df.empty:
        period_fig.add_trace(go.Scatter(
            x=pr_df["mode"], y=pr_df["period"],
            mode="markers+lines", name="Computed Periods",
            marker={"size": 8, "color": ENGINEERING_COLORS[0]},
            line={"width": 1.5},
        ))

    # Ta line (constant — same for all modes)
    n_modes = len(pr_df) if not pr_df.empty else 12
    period_fig.add_hline(y=Ta, line_dash="dash", line_color="#F57F17", line_width=2,
                         annotation_text=f"Ta = {Ta:.3f}s", annotation_position="right")
    period_fig.add_hline(y=Cu_Ta, line_dash="dot", line_color="#C62828", line_width=2,
                         annotation_text=f"Cu×Ta = {Cu_Ta:.3f}s", annotation_position="right")

    # Highlight T1x and T1y
    for label, T1, color in [("T₁X", T1x, ENGINEERING_COLORS[1]),
                              ("T₁Y", T1y, ENGINEERING_COLORS[2])]:
        if T1 is not None:
            period_fig.add_hline(y=T1, line_dash="dot", line_color=color, line_width=1.5,
                                 annotation_text=f"{label}={T1:.3f}s")

    base_layout(period_fig, theme=theme)
    period_fig.update_layout(xaxis_title="Mode Number", yaxis_title="Period T (s)")

    # ── Period table ──────────────────────────────────────────────────────
    if not pr_df.empty:
        tbl_df = pr_df[["mode","period","frequency"]].copy()
        tbl_df["Ta"]      = round(Ta, 4)
        tbl_df["Cu_Ta"]   = round(Cu_Ta, 4)
        tbl_df["T/Ta"]    = (tbl_df["period"] / Ta).round(3)
        tbl_df["Status"]  = tbl_df["period"].apply(
            lambda t: "✓ OK" if t <= Cu_Ta else "⚠ Exceeds Cu×Ta")
        period_table = make_table(tbl_df)
    else:
        period_table = html.P("No modal data.", className="text-muted small")

    # ── Building params card ──────────────────────────────────────────────
    params_div = dbc.Table([html.Tbody([
        html.Tr([html.Td(html.Strong("Height hn")),     html.Td(f"{hn:.2f} {h_unit}")]),
        html.Tr([html.Td(html.Strong("System")),        html.Td(system.replace("_"," ").title())]),
        html.Tr([html.Td(html.Strong("Ct")),            html.Td(f"{Ct}")]),
        html.Tr([html.Td(html.Strong("x")),             html.Td(f"{x}")]),
        html.Tr([html.Td(html.Strong("Ta = Ct×hn^x")), html.Td(f"{Ta:.4f} s")]),
        html.Tr([html.Td(html.Strong("Cu")),            html.Td(f"{Cu}")]),
        html.Tr([html.Td(html.Strong("Cu×Ta")),         html.Td(f"{Cu_Ta:.4f} s")]),
        html.Tr([html.Td(html.Strong("N Stories")),     html.Td(str(len(stories)))]),
    ])], size="sm", borderless=True)

    # ── Base shear chart ──────────────────────────────────────────────────
    bshr_fig = _base_shear_chart(data, dead_case, seismic_case, theme)

    # ── Period trend chart ────────────────────────────────────────────────
    trend_fig = _period_trend_chart(pr_df, T1x, theme)

    return (html.Div(alerts) if alerts else ""), period_fig, period_table, params_div, bshr_fig, trend_fig


def _base_shear_chart(data, dead_case, seismic_case, theme):
    ef = empty_fig(theme=theme)
    rxn = data.get("results", {}).get("base_reactions", [])
    if not rxn:
        return empty_fig("No base reaction data.", theme=theme)

    df = pd.DataFrame(rxn)
    fig = go.Figure()

    # Gravity weight from dead case
    W = None
    if dead_case and dead_case in df["load_case"].values:
        W = abs(df[df["load_case"] == dead_case]["Fz"].values[0])

    # Plot all base shears
    for comp, color in [("Fx", ENGINEERING_COLORS[0]), ("Fy", ENGINEERING_COLORS[1])]:
        if comp in df.columns:
            fig.add_trace(go.Bar(
                x=df["load_case"], y=df[comp].abs(),
                name=comp, marker_color=color, opacity=0.8,
            ))

    # Cs = V/W line for seismic case
    if W and W > 0 and seismic_case and seismic_case in df["load_case"].values:
        seismic_row = df[df["load_case"] == seismic_case].iloc[0]
        Vx = abs(seismic_row.get("Fx", 0))
        Cs = Vx / W
        fig.add_annotation(
            x=seismic_case, y=Vx,
            text=f"Cs = {Cs:.4f}",
            showarrow=True, arrowhead=2, font={"size": 11},
        )

    base_layout(fig, theme=theme)
    fig.update_layout(barmode="group", xaxis_title="Load Case",
                      yaxis_title="|Base Shear| (model units)")
    return fig


def _period_trend_chart(pr_df, T1, theme):
    ef = empty_fig(theme=theme)
    if pr_df.empty or T1 is None:
        return empty_fig("No modal periods.", theme=theme)

    fig = go.Figure()
    # Computed
    fig.add_trace(go.Scatter(
        x=pr_df["mode"], y=pr_df["period"],
        mode="markers", name="Computed",
        marker={"size": 9, "color": ENGINEERING_COLORS[0]},
    ))
    # Theoretical T1/n trend
    modes = pr_df["mode"].values
    trend = [T1 / n for n in modes]
    fig.add_trace(go.Scatter(
        x=modes, y=trend,
        mode="lines", name="T₁/n trend",
        line={"dash": "dash", "color": "#F57F17", "width": 2},
    ))
    base_layout(fig, theme=theme)
    fig.update_layout(xaxis_title="Mode Number", yaxis_title="Period T (s)")
    return fig
