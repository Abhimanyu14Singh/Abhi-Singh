import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, dcc, no_update
import dash, io
from data_store import get_all_cases_and_combos
from callbacks.charts._helpers import empty_fig, base_layout, make_table, ENGINEERING_COLORS

app = dash.get_app()


@app.callback(
    Output("disp-case-select", "options"),
    Output("disp-case-select", "value"),
    Input("etabs-data-store",  "data"),
)
def populate_disp_cases(data):
    cases = get_all_cases_and_combos(data)
    opts  = [{"label": c, "value": c} for c in cases]
    return opts, cases[0] if cases else None


@app.callback(
    Output("disp-distribution-chart", "figure"),
    Output("disp-envelope-chart",     "figure"),
    Output("disp-scatter-chart",      "figure"),
    Output("disp-data-table",         "children"),
    Input("etabs-data-store",  "data"),
    Input("disp-case-select",  "value"),
    Input("disp-dof",          "value"),
    Input("theme-store",       "data"),
)
def update_disp(data, case, dof, theme):
    theme = theme or "light"
    ef    = empty_fig(theme=theme)
    if not data or data.get("status") != "ok":
        return ef, ef, ef, ""

    recs = data.get("results", {}).get("joint_displacements", [])
    if not recs:
        return empty_fig("No displacement results.", theme=theme), ef, ef, ""

    df_all = pd.DataFrame(recs)
    df     = df_all[df_all["load_case"] == case].copy() if case else df_all.copy()
    if df.empty:
        return ef, ef, ef, ""

    dof = dof or "U1"

    # ── Distribution chart (histogram) ───────────────────────────────────
    dist_fig = go.Figure(go.Histogram(
        x=df[dof], nbinsx=40,
        marker_color=ENGINEERING_COLORS[0],
        opacity=0.8,
        hovertemplate=f"{dof}: %{{x:.4f}}<br>Count: %{{y}}<extra></extra>",
    ))
    base_layout(dist_fig, theme=theme)
    dist_fig.update_layout(xaxis_title=f"{dof} (model units)", yaxis_title="Count",
                           bargap=0.05)

    # ── Max/Min envelope per load case ────────────────────────────────────
    env = df_all.groupby("load_case")[dof].agg(["max","min"]).reset_index()
    env_fig = go.Figure()
    env_fig.add_trace(go.Bar(x=env["load_case"], y=env["max"], name="Max",
                              marker_color=ENGINEERING_COLORS[0]))
    env_fig.add_trace(go.Bar(x=env["load_case"], y=env["min"], name="Min",
                              marker_color=ENGINEERING_COLORS[1]))
    base_layout(env_fig, theme=theme)
    env_fig.update_layout(barmode="group", xaxis_title="Load Case",
                          yaxis_title=f"{dof} (model units)")

    # ── Scatter (U1 vs U2) ────────────────────────────────────────────────
    if all(c in df.columns for c in ["U1", "U2", "U3"]):
        scatter_fig = go.Figure(go.Scatter(
            x=df["U1"], y=df["U2"],
            mode="markers",
            marker=dict(
                size=df["U3"].abs().clip(lower=2) * 200,
                sizemode="area",
                sizeref=2.0 * df["U3"].abs().max() / (15**2),
                color=df["U3"],
                colorscale="RdBu_r",
                showscale=True, colorbar={"title": "U3"},
                opacity=0.7,
            ),
            hovertemplate="U1: %{x:.4f}<br>U2: %{y:.4f}<extra></extra>",
        ))
        base_layout(scatter_fig, theme=theme)
        scatter_fig.update_layout(xaxis_title="U1", yaxis_title="U2")
    else:
        scatter_fig = ef

    # ── Summary table ─────────────────────────────────────────────────────
    show = df_all.groupby("load_case")[["U1","U2","U3"]].agg(["max","min"])
    show.columns = ["_".join(c) for c in show.columns]
    show = show.reset_index()
    table = make_table(show)

    return dist_fig, env_fig, scatter_fig, table


@app.callback(
    Output("disp-download-xlsx", "data"),
    Input("disp-dl-xlsx",        "n_clicks"),
    Input("etabs-data-store",    "data"),
    prevent_initial_call=True,
)
def dl_disp(n, data):
    from dash import ctx
    if ctx.triggered_id != "disp-dl-xlsx" or not n:
        return no_update
    recs = data.get("results", {}).get("joint_displacements", [])
    if not recs:
        return no_update
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(recs).to_excel(w, index=False, sheet_name="Displacements")
    return dcc.send_bytes(buf.getvalue(), "ETABS_Displacements.xlsx")
