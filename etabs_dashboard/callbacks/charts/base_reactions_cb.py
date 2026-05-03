import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, dcc, no_update
import dash, io
from data_store import get_all_cases_and_combos, to_df
from callbacks.charts._helpers import empty_fig, base_layout, make_table, ENGINEERING_COLORS

app = dash.get_app()


def _br_df(data, cases):
    recs = data.get("results", {}).get("base_reactions", [])
    if not recs:
        return pd.DataFrame()
    df = pd.DataFrame(recs)
    if cases:
        df = df[df["load_case"].isin(cases)]
    return df


@app.callback(
    Output("br-case-select", "options"),
    Output("br-case-select", "value"),
    Input("etabs-data-store", "data"),
)
def populate_br_cases(data):
    cases = get_all_cases_and_combos(data)
    opts  = [{"label": c, "value": c} for c in cases]
    return opts, cases


@app.callback(
    Output("br-force-chart",  "figure"),
    Output("br-moment-chart", "figure"),
    Output("br-bubble-chart", "figure"),
    Output("br-data-table",   "children"),
    Input("etabs-data-store", "data"),
    Input("br-case-select",   "value"),
    Input("br-components",    "value"),
    Input("theme-store",      "data"),
)
def update_br(data, cases, components, theme):
    theme = theme or "light"
    ef = empty_fig(theme=theme)
    if not data or data.get("status") != "ok":
        return ef, ef, ef, ""

    df = _br_df(data, cases)
    if df.empty:
        return empty_fig("No base reaction results.", theme=theme), ef, ef, ""

    components = components or ["Fx", "Fy", "Fz"]

    # ── Force bar chart ───────────────────────────────────────────────────
    force_fig = go.Figure()
    for i, comp in enumerate(["Fx", "Fy", "Fz"]):
        if comp not in components or comp not in df.columns:
            continue
        force_fig.add_trace(go.Bar(
            x=df["load_case"], y=df[comp], name=comp,
            marker_color=ENGINEERING_COLORS[i % len(ENGINEERING_COLORS)],
            hovertemplate=f"{comp}: %{{y:.3f}}<br>Case: %{{x}}<extra></extra>",
        ))
    base_layout(force_fig, theme=theme)
    force_fig.update_layout(barmode="group", xaxis_title="Load Case",
                             yaxis_title="Force (model units)")

    # ── Moment bar chart ──────────────────────────────────────────────────
    mom_fig = go.Figure()
    for i, comp in enumerate(["Mx", "My", "Mz"]):
        if comp not in components or comp not in df.columns:
            continue
        mom_fig.add_trace(go.Bar(
            x=df["load_case"], y=df[comp], name=comp,
            marker_color=ENGINEERING_COLORS[(i + 3) % len(ENGINEERING_COLORS)],
            hovertemplate=f"{comp}: %{{y:.3f}}<br>Case: %{{x}}<extra></extra>",
        ))
    base_layout(mom_fig, theme=theme)
    mom_fig.update_layout(barmode="group", xaxis_title="Load Case",
                          yaxis_title="Moment (model units)")

    # ── Bubble chart (Fx, Fy, size=|Fz|) ────────────────────────────────
    bub = df.copy()
    bub["abs_Fz"] = bub.get("Fz", pd.Series(0, index=bub.index)).abs()
    bubble_fig = go.Figure(go.Scatter(
        x=bub.get("Fx", []), y=bub.get("Fy", []),
        mode="markers+text",
        marker=dict(
            size=bub["abs_Fz"].clip(lower=1),
            sizemode="area", sizeref=2.0 * bub["abs_Fz"].max() / (40**2),
            color=list(range(len(bub))),
            colorscale="Blues",
            showscale=False,
            line={"width": 1, "color": "#1565C0"},
        ),
        text=bub["load_case"],
        textposition="top center",
        hovertemplate=(
            "Case: %{text}<br>Fx: %{x:.3f}<br>Fy: %{y:.3f}"
            "<br>|Fz|: %{marker.size:.3f}<extra></extra>"
        ),
    ))
    base_layout(bubble_fig, theme=theme)
    bubble_fig.update_layout(xaxis_title="Fx (horizontal)", yaxis_title="Fy (horizontal)")

    table = make_table(df)
    return force_fig, mom_fig, bubble_fig, table


@app.callback(
    Output("br-download-xlsx", "data"),
    Input("br-dl-xlsx",        "n_clicks"),
    Input("etabs-data-store",  "data"),
    Input("br-case-select",    "value"),
    prevent_initial_call=True,
)
def dl_br(n, data, cases):
    from dash import ctx
    if ctx.triggered_id != "br-dl-xlsx" or not n:
        return no_update
    df = _br_df(data, cases)
    if df.empty:
        return no_update
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="BaseReactions")
    return dcc.send_bytes(buf.getvalue(), "ETABS_BaseReactions.xlsx")
