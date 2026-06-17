from dash import html, dcc
import dash_bootstrap_components as dbc


def _kpi(icon, title, value_id, color="primary"):
    return dbc.Card([
        dbc.CardBody([
            html.Div([
                html.Div(html.I(className=f"bi {icon} kpi-icon"), className=f"kpi-icon-wrap bg-{color} bg-opacity-10"),
                html.Div([
                    html.P(title, className="kpi-title mb-0"),
                    html.H4("—", id=value_id, className="kpi-value mb-0"),
                ], className="ms-3"),
            ], className="d-flex align-items-center"),
        ])
    ], className="kpi-card shadow-sm h-100")


def layout():
    return html.Div([
        html.Div([
            html.H4("Model Overview", className="page-title"),
            html.P("Summary of the attached ETABS model.", className="page-subtitle"),
        ], className="page-header"),

        # KPI row
        dbc.Row([
            dbc.Col(_kpi("bi-buildings",         "Stories",     "kpi-stories",    "primary"),  md=2),
            dbc.Col(_kpi("bi-node-plus",         "Joints",      "kpi-joints",     "info"),     md=2),
            dbc.Col(_kpi("bi-slash-lg",          "Frame Elems", "kpi-frames",     "success"),  md=2),
            dbc.Col(_kpi("bi-square",            "Shell Elems", "kpi-shells",     "warning"),  md=2),
            dbc.Col(_kpi("bi-collection",        "Load Cases",  "kpi-load-cases", "danger"),   md=2),
            dbc.Col(_kpi("bi-intersect",         "Combos",      "kpi-combos",     "secondary"),md=2),
        ], className="g-3 mb-4"),

        dbc.Row([
            # Model info card
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-info-circle me-2"), "Model Information"]),
                    dbc.CardBody(html.Div(id="overview-model-table")),
                ], className="shadow-sm h-100"),
            ], md=4),

            # Story table
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-buildings me-2"), "Story Schedule"]),
                    dbc.CardBody(html.Div(id="overview-story-table"), style={"maxHeight": "360px", "overflowY": "auto"}),
                ], className="shadow-sm h-100"),
            ], md=4),

            # Load cases / combos
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-collection me-2"), "Load Cases & Combinations"]),
                    dbc.CardBody([
                        html.Div(id="overview-lc-table"),
                        html.Hr(),
                        html.Div(id="overview-combo-table"),
                    ], style={"maxHeight": "360px", "overflowY": "auto"}),
                ], className="shadow-sm h-100"),
            ], md=4),
        ], className="g-3 mb-4"),

        dbc.Row([
            # Load patterns
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-cloud-drizzle me-2"), "Load Patterns"]),
                    dbc.CardBody(html.Div(id="overview-pattern-table"), style={"maxHeight": "300px", "overflowY": "auto"}),
                ], className="shadow-sm"),
            ], md=6),

            # Element counts chart
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-pie-chart me-2"), "Model Composition"]),
                    dbc.CardBody(dcc.Graph(id="overview-composition-chart",
                                          config={"displayModeBar": False},
                                          style={"height": "260px"})),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3"),

    ], className="page-container")
