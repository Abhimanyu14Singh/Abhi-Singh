from dash import html
import dash_bootstrap_components as dbc


NAV_ITEMS = [
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


def build_sidebar():
    nav_links = []
    for item in NAV_ITEMS:
        nav_links.append(
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
        )

    return html.Div(
        [
            html.Div(
                html.P("NAVIGATION", className="sidebar-section-title"),
                className="px-3 pt-3 pb-1",
            ),
            dbc.Nav(nav_links, vertical=True, pills=True, className="sidebar-nav"),
            html.Hr(className="sidebar-divider"),
            html.Div(
                [
                    html.P("DATA", className="sidebar-section-title"),
                    html.Div(id="sidebar-data-summary", className="sidebar-summary"),
                ],
                className="px-3",
            ),
        ],
        id="sidebar",
        className="sidebar",
    )
