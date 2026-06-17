import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, dcc, html, no_update
import dash, io
from data_store import get_all_cases_and_combos
from shared.helpers import empty_fig, base_layout, make_table, ENGINEERING_COLORS
import dash_bootstrap_components as dbc

app = dash.get_app()


@app.callback(
    Output("ff-frame-select", "options"),
    Input("etabs-data-store", "data"),
    Input("selected-frame-store", "data"),
)
def populate_ff_frames(data, selected):
    if not data or data.get("status") != "ok":
        return []
    frames = data.get("frames", [])
    opts   = [{"label": f["name"], "value": f["name"]} for f in frames]
    return opts


@app.callback(
    Output("ff-frame-select", "value"),
    Input("selected-frame-store", "data"),
)
def sync_frame_select(selected):
    return selected


@app.callback(
    Output("ff-case-select", "options"),
    Output("ff-case-select", "value"),
    Input("etabs-data-store", "data"),
)
def populate_ff_cases(data):
    cases = get_all_cases_and_combos(data)
    opts  = [{"label": c, "value": c} for c in cases]
    return opts, cases[0] if cases else None


@app.callback(
    Output("ff-forces-store",  "data"),
    Output("ff-element-info",  "children"),
    Input("ff-extract-btn",    "n_clicks"),
    State("ff-frame-select",   "value"),
    State("etabs-data-store",  "data"),
    prevent_initial_call=True,
)
def extract_frame_forces(n, frame_name, data):
    if not frame_name:
        return no_update, dbc.Alert("Select a frame element first.", color="warning", className="py-2")
    from etabs_connector import get_frame_forces
    rows = get_frame_forces(frame_name)
    if not rows:
        return (
            [],
            dbc.Alert(f"No forces extracted for frame '{frame_name}'. "
                       "Ensure ETABS is connected and analysis is run.",
                       color="warning", className="py-2"),
        )
    frames = data.get("frames", [])
    fr_info = next((f for f in frames if f["name"] == frame_name), {})
    info_div = dbc.Alert([
        html.Strong(f"Frame: {frame_name}"),
        html.Span(f"  |  Section: {fr_info.get('section', '—')}  "
                  f"|  {len(rows)} force stations extracted",
                  className="ms-2 small"),
    ], color="info", className="py-2 mb-0")
    return rows, info_div


@app.callback(
    Output("ff-axial-chart",   "figure"),
    Output("ff-shear-chart",   "figure"),
    Output("ff-moment-chart",  "figure"),
    Output("ff-torsion-chart", "figure"),
    Input("ff-forces-store",   "data"),
    Input("ff-case-select",    "value"),
    Input("theme-store",       "data"),
)
def update_ff_charts(forces_data, case, theme):
    theme = theme or "light"
    ef = empty_fig("Extract forces for a selected frame element.", theme=theme)
    if not forces_data:
        return ef, ef, ef, ef

    df = pd.DataFrame(forces_data)
    if case:
        df = df[df["load_case"] == case]
    if df.empty:
        return ef, ef, ef, ef

    def _line_fig(comps, title, yaxis_title):
        fig = go.Figure()
        for i, comp in enumerate(comps):
            if comp not in df.columns:
                continue
            color = ENGINEERING_COLORS[i % len(ENGINEERING_COLORS)]
            fig.add_trace(go.Scatter(
                x=df["station"], y=df[comp],
                mode="lines+markers", name=comp,
                line={"color": color, "width": 2}, marker={"size": 5},
                fill="tozeroy" if len(comps) == 1 else None,
                fillcolor=color.replace(")", ",0.15)").replace("rgb", "rgba") if "rgb" in color else None,
                hovertemplate=f"{comp}: %{{y:.3f}}<br>Station: %{{x:.3f}}<extra></extra>",
            ))
            # zero line
        fig.add_hline(y=0, line_dash="dot", line_color="gray", line_width=1)
        base_layout(fig, theme=theme)
        fig.update_layout(xaxis_title="Station (element length)", yaxis_title=yaxis_title)
        return fig

    axial  = _line_fig(["P"],      "Axial Force P",        "P (model units)")
    shear  = _line_fig(["V2","V3"],"Shear V2, V3",        "V (model units)")
    moment = _line_fig(["M2","M3"],"Moments M2, M3",       "M (model units)")
    tors   = _line_fig(["T"],      "Torsion T",            "T (model units)")
    return axial, shear, moment, tors


@app.callback(
    Output("ff-download-xlsx", "data"),
    Input("ff-dl-xlsx",        "n_clicks"),
    State("ff-forces-store",   "data"),
    State("ff-frame-select",   "value"),
    prevent_initial_call=True,
)
def dl_ff(n, forces_data, frame_name):
    from dash import ctx
    if ctx.triggered_id != "ff-dl-xlsx" or not n or not forces_data:
        return no_update
    df  = pd.DataFrame(forces_data)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name=f"Frame_{frame_name}")
    return dcc.send_bytes(buf.getvalue(), f"ETABS_Frame_{frame_name}.xlsx")
