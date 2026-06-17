from dash import Input, Output, html
import dash

import features.overview        as overview
import features.plan_view       as plan_view
import features.story_drifts    as story_drifts
import features.story_forces    as story_forces
import features.base_reactions  as base_reactions
import features.frame_forces    as frame_forces
import features.modal           as modal
import features.displacements   as displacements
import features.load_patterns   as load_patterns
import features.torsion         as torsion
import features.statistics      as statistics
import features.heatmaps        as heatmaps
import features.code_checks     as code_checks
import features.outliers        as outliers
import features.correlation     as correlation
import features.scorecard       as scorecard


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
