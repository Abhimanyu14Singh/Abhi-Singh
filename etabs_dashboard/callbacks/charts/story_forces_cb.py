import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, dcc, no_update
import dash, io
from data_store import get_all_cases_and_combos, get_story_elevations, to_df
from callbacks.charts._helpers import empty_fig, base_layout, make_table, ENGINEERING_COLORS

app = dash.get_app()


def _sf_df(data, cases):
    recs = data.get("results", {}).get("story_forces", [])
    if not recs:
        return pd.DataFrame()
    df = pd.DataFrame(recs)
    if cases:
        df = df[df["load_case"].isin(cases)]
    return df


@app.callback(
    Output("sf-case-select", "options"),
    Output("sf-case-select", "value"),
    Input("etabs-data-store", "data"),
)
def populate_sf_cases(data):
    cases = get_all_cases_and_combos(data)
    opts  = [{"label": c, "value": c} for c in cases]
    return opts, cases[:3] if len(cases) >= 3 else cases


@app.callback(
    Output("sf-shear-profile",  "figure"),
    Output("sf-otm-profile",    "figure"),
    Output("sf-torsion-chart",  "figure"),
    Output("sf-data-table",     "children"),
    Input("etabs-data-store",   "data"),
    Input("sf-case-select",     "value"),
    Input("sf-component",       "value"),
    Input("theme-store",        "data"),
)
def update_sf(data, cases, component, theme):
    theme = theme or "light"
    ef    = empty_fig(theme=theme)
    if not data or data.get("status") != "ok":
        return ef, ef, ef, ""

    elevs = get_story_elevations(data)
    df    = _sf_df(data, cases)
    if df.empty:
        return empty_fig("No story force results.", theme=theme), ef, ef, ""

    df["elev"] = df["story"].map(elevs).fillna(0)
    df = df.sort_values("elev")

    def profile_trace(comp, title):
        fig = go.Figure()
        for i, case in enumerate(df["load_case"].unique()):
            sub = df[df["load_case"] == case].copy()
            color = ENGINEERING_COLORS[i % len(ENGINEERING_COLORS)]
            fig.add_trace(go.Scatter(
                x=sub[comp], y=sub["story"],
                mode="lines+markers", name=case,
                line={"color": color, "width": 2}, marker={"size": 6},
                hovertemplate=f"Story: %{{y}}<br>{comp}: %{{x:.2f}}<extra>{case}</extra>",
            ))
        base_layout(fig, theme=theme)
        fig.update_layout(xaxis_title=comp, yaxis_title="Story")
        return fig

    shear_comp = "Vx" if component in ("Vx", "T", "Mx") else "Vy"
    shear_fig   = profile_trace("Vx", "Story Shear Vx")
    otm_fig     = profile_trace("Mx", "Overturning Moment Mx")
    torsion_fig = profile_trace("T",  "Story Torsion T")

    cols = ["story","load_case","Px","Py","Vx","Vy","T","Mx","My"]
    show = [c for c in cols if c in df.columns]
    table = make_table(df[show])
    return shear_fig, otm_fig, torsion_fig, table


@app.callback(
    Output("sf-download-xlsx", "data"),
    Input("sf-dl-xlsx",        "n_clicks"),
    Input("etabs-data-store",  "data"),
    Input("sf-case-select",    "value"),
    prevent_initial_call=True,
)
def dl_sf(n, data, cases):
    from dash import ctx
    if ctx.triggered_id != "sf-dl-xlsx" or not n:
        return no_update
    df = _sf_df(data, cases)
    if df.empty:
        return no_update
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="StoryForces")
    return dcc.send_bytes(buf.getvalue(), "ETABS_StoryForces.xlsx")
