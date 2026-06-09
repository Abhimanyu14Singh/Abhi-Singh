from dash import html
import dash_bootstrap_components as dbc


STRUCTURAL_NAV = [
    {"id": "overview",       "icon": "bi-grid-1x2-fill",      "label": "Overview"},
    {"id": "plan-view",      "icon": "bi-map-fill",            "label": "2D Plan View"},
    {"id": "story-drifts",   "icon": "bi-bar-chart-steps",     "label": "Story Drifts"},
    {"id": "story-forces",   "icon": "bi-stack",               "label": "Story Forces"},
    {"id": "base-reactions", "icon": "bi-arrows-collapse",     "label": "Base Reactions"},
    {"id": "frame-forces",   "icon": "bi-rulers",              "label": "Frame Forces"},
    {"id": "modal",          "icon": "bi-activity",            "label": "Modal Analysis"},
    {"id": "displacements",  "icon": "bi-arrows-move",         "label": "Displacements"},
    {"id": "load-patterns",  "icon": "bi-cloud-drizzle-fill",  "label": "Load Patterns"},
    {"id": "torsion",        "icon": "bi-arrow-repeat",        "label": "Torsion & Irregularity"},
]

ANALYTICS_NAV = [
    {"id": "statistics",  "icon": "bi-box",              "label": "Statistical Summary"},
    {"id": "heatmaps",    "icon": "bi-grid-3x3",         "label": "Heatmap Analysis"},
    {"id": "code-checks", "icon": "bi-shield-check",     "label": "Code Checks (ASCE 7)"},
    {"id": "outliers",    "icon": "bi-exclamation-diamond","label": "Outlier Detection"},
    {"id": "correlation", "icon": "bi-diagram-3",        "label": "Correlation & Sensitivity"},
    {"id": "scorecard",   "icon": "bi-clipboard2-check", "label": "Performance Scorecard"},
]


def _nav_links(items):
    return [
        dbc.NavLink(
            [
                html.I(className=f"bi {item['icon']} nav-icon me-2"),
                html.Span(item["label"], className="nav-label"),
            ],
            id=f"nav-{item['id']}",
            href=f"/{item['id']}",
            active="exact",
            className="sidebar-link",
        )
        for item in items
    ]


def build_sidebar():
    return html.Div(
        [
            html.Div(
                html.P("STRUCTURAL", className="sidebar-section-title"),
                className="px-3 pt-3 pb-1",
            ),
            dbc.Nav(_nav_links(STRUCTURAL_NAV), vertical=True, pills=True,
                    className="sidebar-nav"),

            html.Hr(className="sidebar-divider"),

            html.Div(
                html.P("ANALYTICS", className="sidebar-section-title"),
                className="px-3 pb-1",
            ),
            dbc.Nav(_nav_links(ANALYTICS_NAV), vertical=True, pills=True,
                    className="sidebar-nav"),

            html.Hr(className="sidebar-divider"),
            html.Div(
                [
                    html.P("MODEL", className="sidebar-section-title"),
                    html.Div(id="sidebar-data-summary", className="sidebar-summary"),
                ],
                className="px-3",
            ),
        ],
        id="sidebar",
        className="sidebar",
    )
