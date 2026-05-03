from dash import Input, Output, html, no_update
import dash
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from data_store import to_df
from callbacks.charts._helpers import empty_fig, base_layout, make_table


app = dash.get_app()


@app.callback(
    Output("kpi-stories",    "children"),
    Output("kpi-joints",     "children"),
    Output("kpi-frames",     "children"),
    Output("kpi-shells",     "children"),
    Output("kpi-load-cases", "children"),
    Output("kpi-combos",     "children"),
    Input("etabs-data-store", "data"),
)
def update_kpis(data):
    if not data or data.get("status") != "ok":
        return ["—"] * 6
    i = data.get("model_info", {})
    return (
        str(i.get("num_stories",    "—")),
        str(i.get("num_joints",     "—")),
        str(i.get("num_frames",     "—")),
        str(i.get("num_shells",     "—")),
        str(i.get("num_load_cases", "—")),
        str(i.get("num_load_combos","—")),
    )


@app.callback(
    Output("overview-model-table", "children"),
    Input("etabs-data-store", "data"),
)
def update_model_table(data):
    if not data or data.get("status") != "ok":
        return html.P("No model attached.", className="text-muted")
    i = data.get("model_info", {})
    rows = [
        ("Model File",    i.get("filename",       "—")),
        ("Units",         i.get("units",           "—")),
        ("Stories",       i.get("num_stories",     "—")),
        ("Joints",        i.get("num_joints",      "—")),
        ("Frame Elems",   i.get("num_frames",      "—")),
        ("Shell Elems",   i.get("num_shells",      "—")),
        ("Load Cases",    i.get("num_load_cases",  "—")),
        ("Combinations",  i.get("num_load_combos", "—")),
    ]
    return dbc.Table(
        [html.Tbody([html.Tr([html.Td(html.Strong(k), className="pe-3"), html.Td(v)]) for k, v in rows])],
        size="sm", borderless=True, className="mb-0",
    )


@app.callback(
    Output("overview-story-table", "children"),
    Input("etabs-data-store", "data"),
)
def update_story_table(data):
    if not data or data.get("status") != "ok":
        return html.P("No data.", className="text-muted")
    stories = data.get("stories", [])
    if not stories:
        return html.P("No stories found.", className="text-muted")
    import pandas as pd
    df = pd.DataFrame(stories)[["name", "elevation", "height"]]
    df.columns = ["Story", "Elevation", "Height"]
    return make_table(df)


@app.callback(
    Output("overview-lc-table",    "children"),
    Output("overview-combo-table", "children"),
    Input("etabs-data-store", "data"),
)
def update_lc_tables(data):
    if not data or data.get("status") != "ok":
        return html.P("—", className="text-muted"), html.P("—", className="text-muted")
    import pandas as pd
    lc  = data.get("load_cases",  [])
    co  = data.get("load_combos", [])
    lc_div  = make_table(pd.DataFrame({"Load Cases": lc}))  if lc  else html.P("None.", className="text-muted small")
    co_div  = make_table(pd.DataFrame({"Combinations": co})) if co else html.P("None.", className="text-muted small")
    return lc_div, co_div


@app.callback(
    Output("overview-pattern-table", "children"),
    Input("etabs-data-store", "data"),
)
def update_pattern_table(data):
    if not data or data.get("status") != "ok":
        return html.P("No data.", className="text-muted")
    import pandas as pd
    pats = data.get("load_patterns", [])
    if not pats:
        return html.P("No load patterns found.", className="text-muted")
    df = pd.DataFrame(pats)[["name", "type_name", "self_wt"]]
    df.columns = ["Pattern", "Type", "Self-Wt Mult."]
    return make_table(df)


@app.callback(
    Output("overview-composition-chart", "figure"),
    Input("etabs-data-store", "data"),
    Input("theme-store", "data"),
)
def update_composition_chart(data, theme):
    if not data or data.get("status") != "ok":
        return empty_fig(theme=theme or "light")
    i = data.get("model_info", {})
    labels = ["Joints", "Frames", "Shells"]
    values = [i.get("num_joints", 0), i.get("num_frames", 0), i.get("num_shells", 0)]
    fig = go.Figure(go.Pie(
        labels=labels, values=values,
        hole=0.45,
        marker_colors=["#1565C0", "#2E7D32", "#F57F17"],
        textinfo="label+percent",
        hoverinfo="label+value",
    ))
    base_layout(fig, theme=theme or "light")
    fig.update_layout(showlegend=True, margin={"t": 10, "b": 10, "l": 10, "r": 10})
    return fig
