from dash import Input, Output, State, no_update, html
import dash
import plotly.graph_objects as go
from shared.helpers import empty_fig, base_layout
from data_store import get_story_names, get_story_elevations


app = dash.get_app()


@app.callback(
    Output("plan-story-select", "options"),
    Output("plan-story-select", "value"),
    Input("etabs-data-store", "data"),
)
def populate_story_dropdown(data):
    stories = get_story_names(data)
    if not stories:
        return [], None
    opts = [{"label": s, "value": s} for s in stories]
    return opts, stories[0]


@app.callback(
    Output("plan-view-chart",       "figure"),
    Output("plan-element-inspector","children"),
    Input("plan-story-select",   "value"),
    Input("plan-element-types",  "value"),
    Input("etabs-data-store",    "data"),
    Input("theme-store",         "data"),
)
def update_plan_view(story, element_types, data, theme):
    theme = theme or "light"
    if not data or data.get("status") != "ok" or not story:
        return empty_fig(theme=theme), html.P("No data.", className="text-muted small")

    elevs  = get_story_elevations(data)
    target = elevs.get(story)
    tol    = 0.05  # 50 mm tolerance

    joints_raw = data.get("joints", [])
    frames_raw = data.get("frames", [])
    shells_raw = data.get("shells", [])

    # Filter joints at this story elevation
    jdict = {j["name"]: j for j in joints_raw}
    story_joints = {n: j for n, j in jdict.items()
                    if abs(j.get("z", 0) - target) <= tol}

    fig = go.Figure()

    # ── Shells ────────────────────────────────────────────────────────────
    if "shells" in (element_types or []):
        for sh in shells_raw:
            coords = sh.get("coords", [])
            zs = [c[2] for c in coords]
            if not coords or not any(abs(z - target) <= tol for z in zs):
                continue
            xs = [c[0] for c in coords] + [coords[0][0]]
            ys = [c[1] for c in coords] + [coords[0][1]]
            fig.add_trace(go.Scatter(
                x=xs, y=ys, fill="toself",
                fillcolor="rgba(255,183,77,0.25)",
                line={"color": "#F57F17", "width": 1},
                mode="lines", hoverinfo="skip",
                showlegend=False,
                name="Shell",
            ))

    # ── Frames ────────────────────────────────────────────────────────────
    if "frames" in (element_types or []):
        # Build None-separated multi-line trace for speed
        xs, ys, labels, ids = [], [], [], []
        for fr in frames_raw:
            zi, zj = fr.get("zi", 0), fr.get("zj", 0)
            if abs(zi - target) > tol and abs(zj - target) > tol:
                continue
            xs  += [fr["xi"], fr["xj"], None]
            ys  += [fr["yi"], fr["yj"], None]
        # Clickable individual traces for small models; batch for large
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines",
            line={"color": "#1565C0", "width": 2},
            name="Frames",
            hoverinfo="skip",
        ))

        # Individual dots for clickable elements
        mid_x = []; mid_y = []; mid_ids = []
        for fr in frames_raw:
            zi, zj = fr.get("zi", 0), fr.get("zj", 0)
            if abs(zi - target) > tol and abs(zj - target) > tol:
                continue
            mid_x.append((fr["xi"] + fr["xj"]) / 2)
            mid_y.append((fr["yi"] + fr["yj"]) / 2)
            mid_ids.append(fr["name"])

        fig.add_trace(go.Scatter(
            x=mid_x, y=mid_y, mode="markers",
            marker={"size": 6, "color": "rgba(21,101,192,0)", "line": {"width": 0}},
            customdata=mid_ids,
            hovertemplate="Frame: %{customdata}<extra></extra>",
            name="Frame IDs",
            showlegend=False,
        ))

    # ── Joints ────────────────────────────────────────────────────────────
    if "joints" in (element_types or []):
        jx = [j["x"] for j in story_joints.values()]
        jy = [j["y"] for j in story_joints.values()]
        jn = list(story_joints.keys())
        fig.add_trace(go.Scatter(
            x=jx, y=jy, mode="markers",
            marker={"size": 5, "color": "#C62828", "symbol": "circle"},
            customdata=jn,
            hovertemplate="Joint: %{customdata}<br>X=%{x:.3f}, Y=%{y:.3f}<extra></extra>",
            name="Joints",
        ))

    base_layout(fig, theme=theme)
    fig.update_layout(
        xaxis={"title": "X (model units)", "scaleanchor": "y", "scaleratio": 1,
               "showgrid": True, "gridcolor": "rgba(128,128,128,0.15)"},
        yaxis={"title": "Y (model units)",
               "showgrid": True, "gridcolor": "rgba(128,128,128,0.15)"},
        dragmode="pan",
        legend={"orientation": "h"},
    )
    nf = sum(1 for fr in frames_raw
             if abs(fr.get("zi",0)-target)<=tol or abs(fr.get("zj",0)-target)<=tol)
    info = html.Div([
        html.P(f"Story: {story}", className="mb-1 fw-semibold"),
        html.P(f"Elevation: {target:.3f}", className="mb-1 small"),
        html.P(f"Joints at level: {len(story_joints)}", className="mb-1 small"),
        html.P(f"Frames at level: {nf}", className="mb-0 small"),
    ])
    return fig, info


@app.callback(
    Output("selected-frame-store", "data"),
    Input("plan-view-chart", "clickData"),
    prevent_initial_call=True,
)
def store_selected_frame(click_data):
    if not click_data:
        return no_update
    point = click_data.get("points", [{}])[0]
    return point.get("customdata")
