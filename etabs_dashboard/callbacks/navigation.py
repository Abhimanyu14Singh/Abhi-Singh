from dash import Input, Output, html
import dash

from layouts.pages import (
    overview, plan_view, story_drifts, story_forces,
    base_reactions, frame_forces, modal, displacements,
    load_patterns, torsion,
    statistics, heatmaps, code_checks, outliers, correlation, scorecard,
)


PAGE_MAP = {
    "/":               overview.layout,
    "/overview":       overview.layout,
    "/plan-view":      plan_view.layout,
    "/story-drifts":   story_drifts.layout,
    "/story-forces":   story_forces.layout,
    "/base-reactions": base_reactions.layout,
    "/frame-forces":   frame_forces.layout,
    "/modal":          modal.layout,
    "/displacements":  displacements.layout,
    "/load-patterns":  load_patterns.layout,
    "/torsion":        torsion.layout,
    # Analytics
    "/statistics":     statistics.layout,
    "/heatmaps":       heatmaps.layout,
    "/code-checks":    code_checks.layout,
    "/outliers":       outliers.layout,
    "/correlation":    correlation.layout,
    "/scorecard":      scorecard.layout,
}


@dash.get_app().callback(
    Output("page-content", "children"),
    Input("url", "pathname"),
)
def render_page(pathname):
    return PAGE_MAP.get(pathname, overview.layout)()


@dash.get_app().callback(
    Output("sidebar-data-summary", "children"),
    Input("etabs-data-store", "data"),
)
def update_sidebar_summary(data):
    if not data or data.get("status") != "ok":
        return html.P("No model attached.", className="text-muted small")
    info = data.get("model_info", {})
    return html.Div([
        html.Div([html.Small(f"📁 {info.get('filename', '—')}", className="d-block fw-semibold")]),
        html.Div([html.Small(f"⚖  {info.get('units', '—')}", className="d-block")]),
        html.Div([html.Small(f"🏢 {info.get('num_stories', 0)} stories", className="d-block")]),
        html.Div([html.Small(f"📐 {info.get('num_frames', 0)} frames / {info.get('num_shells', 0)} shells",
                             className="d-block")]),
        html.Div([html.Small(f"📋 {info.get('num_load_cases', 0)} cases / {info.get('num_load_combos', 0)} combos",
                             className="d-block")]),
    ], className="small text-muted")
