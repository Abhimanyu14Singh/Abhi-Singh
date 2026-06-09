from dash import html, dcc
import dash_bootstrap_components as dbc


def layout():
    return html.Div([
        html.Div([
            html.H4("Frame Element Forces", className="page-title"),
            html.P("Axial force, shear, torsion and bending moment diagrams along element length.", className="page-subtitle"),
        ], className="page-header"),

        dbc.Card([
            dbc.CardBody(dbc.Row([
                dbc.Col([
                    dbc.Label("Frame Element ID"),
                    dcc.Dropdown(id="ff-frame-select", placeholder="Select frame element…", clearable=False),
                ], md=4),
                dbc.Col([
                    dbc.Label("Load Case / Combo"),
                    dcc.Dropdown(id="ff-case-select", placeholder="Select load case…"),
                ], md=4),
                dbc.Col([
                    dbc.Label(" "),
                    html.Div([
                        dbc.Button(
                            [html.I(className="bi bi-arrow-clockwise me-1"), "Extract Forces"],
                            id="ff-extract-btn", color="primary", size="sm", className="me-2",
                            n_clicks=0,
                        ),
                        dbc.Button([html.I(className="bi bi-download me-1"), "PNG"],
                                   id="ff-dl-png", size="sm", color="outline-secondary", className="me-1"),
                        dbc.Button([html.I(className="bi bi-file-earmark-excel me-1"), "Excel"],
                                   id="ff-dl-xlsx", size="sm", color="outline-success"),
                    ]),
                ], md=4),
            ], className="g-2")),
        ], className="shadow-sm mb-3"),

        # Element info banner
        html.Div(id="ff-element-info", className="mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-arrows-collapse me-2"), "Axial Force (P)"]),
                    dbc.CardBody(dcc.Graph(id="ff-axial-chart",
                                          config={"displayModeBar": False},
                                          style={"height": "260px"})),
                ], className="shadow-sm"),
            ], md=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-arrow-down-up me-2"), "Shear V2 & V3"]),
                    dbc.CardBody(dcc.Graph(id="ff-shear-chart",
                                          config={"displayModeBar": False},
                                          style={"height": "260px"})),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3 mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-bezier me-2"), "Bending Moments M2 & M3"]),
                    dbc.CardBody(dcc.Graph(id="ff-moment-chart",
                                          config={"displayModeBar": False},
                                          style={"height": "260px"})),
                ], className="shadow-sm"),
            ], md=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-arrow-repeat me-2"), "Torsion (T)"]),
                    dbc.CardBody(dcc.Graph(id="ff-torsion-chart",
                                          config={"displayModeBar": False},
                                          style={"height": "260px"})),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3"),

        dcc.Store(id="ff-forces-store"),
        dcc.Download(id="ff-download-xlsx"),
    ], className="page-container")
