import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, dcc, html, no_update
import dash, io
from callbacks.charts._helpers import empty_fig, base_layout, make_table, ENGINEERING_COLORS

app = dash.get_app()


@app.callback(
    Output("modal-T1-x",         "children"),
    Output("modal-T1-y",         "children"),
    Output("modal-T1-rz",        "children"),
    Output("modal-modes-90",     "children"),
    Output("modal-mass-bar",     "figure"),
    Output("modal-cumulative-chart","figure"),
    Output("modal-bubble-chart", "figure"),
    Output("modal-summary-table","children"),
    Input("etabs-data-store",    "data"),
    Input("theme-store",         "data"),
)
def update_modal(data, theme):
    theme = theme or "light"
    ef    = empty_fig(theme=theme)
    dash_vals = ("—", "—", "—", "—", ef, ef, ef, "")

    if not data or data.get("status") != "ok":
        return dash_vals

    periods = data.get("results", {}).get("modal_periods", [])
    ratios  = data.get("results", {}).get("modal_mass_ratios", [])
    if not periods or not ratios:
        return ("—", "—", "—", "—",
                empty_fig("No modal results.", theme=theme),
                empty_fig(theme=theme), empty_fig(theme=theme), "")

    pr_df = pd.DataFrame(periods)
    ra_df = pd.DataFrame(ratios)
    df    = pd.merge(pr_df, ra_df, on="mode", how="outer")

    # Dominant periods
    def _dom(col):
        if col not in df.columns:
            return "—"
        idx = df[col].idxmax() if not df[col].empty else None
        if idx is None:
            return "—"
        return f"{df.loc[idx, 'period']:.4f} s (Mode {int(df.loc[idx, 'mode'])})"

    T1x  = _dom("Ux")
    T1y  = _dom("Uy")
    T1rz = _dom("Rz")

    # Modes to 90%
    modes_90 = "—"
    if "sum_Ux" in df.columns:
        hits = df[df["sum_Ux"] >= 0.90]
        modes_90 = str(int(hits["mode"].min())) if not hits.empty else f">{len(df)}"

    # ── Mass participation bar ────────────────────────────────────────────
    bar_fig = go.Figure()
    for i, comp in enumerate(["Ux", "Uy", "Rz"]):
        if comp not in df.columns:
            continue
        bar_fig.add_trace(go.Bar(
            x=df["mode"].astype(str), y=(df[comp] * 100),
            name=comp,
            marker_color=ENGINEERING_COLORS[i % len(ENGINEERING_COLORS)],
            hovertemplate=f"Mode %{{x}}: {comp} = %{{y:.2f}}%<extra></extra>",
        ))
    base_layout(bar_fig, theme=theme)
    bar_fig.update_layout(barmode="group", xaxis_title="Mode", yaxis_title="Mass Participation (%)")

    # ── Cumulative chart ──────────────────────────────────────────────────
    cum_fig = go.Figure()
    for i, (comp, cum_comp) in enumerate([("Ux","sum_Ux"), ("Uy","sum_Uy")]):
        if cum_comp not in df.columns:
            continue
        cum_fig.add_trace(go.Scatter(
            x=df["mode"], y=(df[cum_comp] * 100),
            mode="lines+markers", name=f"Σ{comp}",
            line={"color": ENGINEERING_COLORS[i], "width": 2},
            marker={"size": 6},
        ))
    cum_fig.add_hline(y=90, line_dash="dash", line_color="#C62828", line_width=1.5,
                      annotation_text="90% threshold", annotation_position="right")
    base_layout(cum_fig, theme=theme)
    cum_fig.update_layout(xaxis_title="Mode Number", yaxis_title="Cumulative Mass (%)")

    # ── Bubble chart (Period vs Ux, size = Uy) ──────────────────────────
    bub_fig = go.Figure(go.Scatter(
        x=df.get("period", []),
        y=df.get("Ux", pd.Series([0]*len(df))) * 100,
        mode="markers+text",
        text=df["mode"].astype(str),
        textposition="top center",
        marker=dict(
            size=(df.get("Uy", pd.Series([0]*len(df))) * 200).clip(lower=6),
            color=df.get("Rz", pd.Series([0]*len(df))),
            colorscale="RdBu",
            showscale=True,
            colorbar={"title": "Rz"},
            line={"width": 1, "color": "#1565C0"},
        ),
        hovertemplate=(
            "Mode %{text}<br>"
            "T = %{x:.4f} s<br>"
            "Ux = %{y:.2f}%<extra></extra>"
        ),
    ))
    base_layout(bub_fig, theme=theme)
    bub_fig.update_layout(
        xaxis_title="Period T (s)",
        yaxis_title="Ux Mass Participation (%)",
        coloraxis_showscale=True,
    )

    # ── Table ─────────────────────────────────────────────────────────────
    show_cols = ["mode","period","frequency"] + [c for c in ["Ux","Uy","Uz","Rz","sum_Ux","sum_Uy"]
                                                  if c in df.columns]
    table = make_table(df[show_cols])

    return T1x, T1y, T1rz, modes_90, bar_fig, cum_fig, bub_fig, table


@app.callback(
    Output("modal-download-xlsx", "data"),
    Input("modal-dl-xlsx",        "n_clicks"),
    Input("etabs-data-store",     "data"),
    prevent_initial_call=True,
)
def dl_modal(n, data):
    from dash import ctx
    if ctx.triggered_id != "modal-dl-xlsx" or not n:
        return no_update
    periods = data.get("results", {}).get("modal_periods", [])
    ratios  = data.get("results", {}).get("modal_mass_ratios", [])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(periods).to_excel(w, index=False, sheet_name="ModalPeriods")
        pd.DataFrame(ratios).to_excel(w,  index=False, sheet_name="MassParticipation")
    return dcc.send_bytes(buf.getvalue(), "ETABS_Modal.xlsx")
