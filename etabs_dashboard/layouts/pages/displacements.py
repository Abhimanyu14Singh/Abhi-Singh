from dash import html, dcc
import dash_bootstrap_components as dbc


def layout():
    return html.Div([
        html.Div([
            html.H4("Joint Displacements", className="page-title"),
            html.P("Translational and rotational displacements per load case.", className="page-subtitle"),
        ], className="page-header"),

        dbc.Card([
            dbc.CardBody(dbc.Row([
                dbc.Col([
                    dbc.Label("Load Case / Combo"),
                    dcc.Dropdown(id="disp-case-select", placeholder="Select case…", clearable=False),
                ], md=4),
                dbc.Col([
                    dbc.Label("DOF"),
                    dbc.RadioItems(
                        id="disp-dof",
                        options=[
                            {"label": "U1", "value": "U1"}, {"label": "U2", "value": "U2"},
                            {"label": "U3", "value": "U3"}, {"label": "R1", "value": "R1"},
                            {"label": "R2", "value": "R2"}, {"label": "R3", "value": "R3"},
                        ],
                        value="U1", inline=True,
                    ),
                ], md=5),
                dbc.Col([
                    dbc.Label(" "),
                    html.Div([
                        dbc.Button([html.I(className="bi bi-download me-1"), "PNG"],
                                   id="disp-dl-png", size="sm", color="outline-secondary", className="me-1"),
                        dbc.Button([html.I(className="bi bi-file-earmark-excel me-1"), "Excel"],
                                   id="disp-dl-xlsx", size="sm", color="outline-success"),
                    ]),
                ], md=3),
            ], className="g-2")),
        ], className="shadow-sm mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-arrows-move me-2"), "Displacement Distribution"]),
                    dbc.CardBody(dcc.Graph(id="disp-distribution-chart",
                                          config={"displayModeBar": True},
                                          style={"height": "440px"})),
                ], className="shadow-sm"),
            ], md=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-graph-up me-2"), "Max/Min Envelope per Load Case"]),
                    dbc.CardBody(dcc.Graph(id="disp-envelope-chart",
                                          config={"displayModeBar": True},
                                          style={"height": "440px"})),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3 mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-circle-fill me-2"), "Displacement Scatter (U1 vs U2)"]),
                    dbc.CardBody([
                        html.P("Each point = one joint. Bubble size = |U3|. Colour = load case.",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="disp-scatter-chart",
                                  config={"displayModeBar": True},
                                  style={"height": "380px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-table me-2"), "Max Displacements Table"]),
                    dbc.CardBody(html.Div(id="disp-data-table"),
                                 style={"maxHeight": "400px", "overflowY": "auto"}),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3"),

        dcc.Download(id="disp-download-xlsx"),
    ], className="page-container")
