from dash import html, dcc
import dash_bootstrap_components as dbc


def layout():
    return html.Div([
        html.Div([
            html.H4("Story Drifts & Drift Profiles", className="page-title"),
            html.P("Interstory drift ratios with code limit overlays (ASCE 7-22 / IS 1893).", className="page-subtitle"),
        ], className="page-header"),

        # Controls
        dbc.Card([
            dbc.CardBody(dbc.Row([
                dbc.Col([
                    dbc.Label("Load Case / Combo"),
                    dcc.Dropdown(id="drift-case-select", multi=True, placeholder="Select cases…"),
                ], md=4),
                dbc.Col([
                    dbc.Label("Direction"),
                    dbc.RadioItems(
                        id="drift-direction",
                        options=[{"label": "X", "value": "X"},
                                 {"label": "Y", "value": "Y"},
                                 {"label": "Both", "value": "Both"}],
                        value="Both", inline=True,
                    ),
                ], md=3),
                dbc.Col([
                    dbc.Label("Code Drift Limit"),
                    dcc.Dropdown(
                        id="drift-limit-select",
                        options=[
                            {"label": "ASCE 7 RC I/II – 2.5% (Moment Frame)", "value": 0.025},
                            {"label": "ASCE 7 RC I/II – 2.0% (Other)",        "value": 0.020},
                            {"label": "ASCE 7 RC III – 2.0%",                 "value": 0.020},
                            {"label": "ASCE 7 RC IV – 1.5%",                  "value": 0.015},
                            {"label": "IS 1893 – 0.4%",                        "value": 0.004},
                            {"label": "None",                                  "value": 0},
                        ],
                        value=0.025, clearable=False,
                    ),
                ], md=3),
                dbc.Col([
                    dbc.Label(" "),
                    html.Div([
                        dbc.Button([html.I(className="bi bi-download me-1"), "PNG"],
                                   id="drift-dl-png", size="sm", color="outline-secondary", className="me-1"),
                        dbc.Button([html.I(className="bi bi-file-earmark-excel me-1"), "Excel"],
                                   id="drift-dl-xlsx", size="sm", color="outline-success"),
                    ]),
                ], md=2),
            ], className="g-2")),
        ], className="shadow-sm mb-3"),

        # Irregularity flags
        html.Div(id="drift-irregularity-flags", className="mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-bar-chart-steps me-2"), "Drift Profile (Story vs Drift Ratio)"]),
                    dbc.CardBody(dcc.Graph(id="drift-profile-chart",
                                          config={"displayModeBar": True},
                                          style={"height": "480px"})),
                ], className="shadow-sm"),
            ], md=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-bar-chart me-2"), "Max Drift per Story (Bar)"]),
                    dbc.CardBody(dcc.Graph(id="drift-bar-chart",
                                          config={"displayModeBar": True},
                                          style={"height": "480px"})),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3 mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-table me-2"), "Drift Data Table"]),
                    dbc.CardBody(html.Div(id="drift-data-table")),
                ], className="shadow-sm"),
            ]),
        ]),

        dcc.Download(id="drift-download-xlsx"),
        dcc.Download(id="drift-download-png"),
    ], className="page-container")
