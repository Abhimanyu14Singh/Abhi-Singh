from dash import html, dcc
import dash_bootstrap_components as dbc


def layout():
    return html.Div([
        html.Div([
            html.H4("Torsion & Structural Irregularities", className="page-title"),
            html.P("Torsional eccentricity, story stiffness ratios, mass irregularity and ASCE 7 / IS 1893 checks.", className="page-subtitle"),
        ], className="page-header"),

        # Irregularity flags row
        html.Div(id="torsion-irregularity-cards", className="mb-4"),

        dbc.Row([
            # Torsional irregularity chart
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-arrow-repeat me-2"),
                                    "Torsional Irregularity Ratio per Story"]),
                    dbc.CardBody([
                        html.P("Ratio = max drift / average drift for X and Y. "
                               "ASCE 7 Type 1a ≥ 1.2, Type 1b ≥ 1.4.",
                               className="text-muted small mb-2"),
                        dcc.Dropdown(id="torsion-case-select", placeholder="Select load case…",
                                     className="mb-2"),
                        dcc.Graph(id="torsion-ratio-chart",
                                  config={"displayModeBar": True},
                                  style={"height": "380px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=6),

            # Story stiffness
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-layers me-2"), "Story Stiffness Ratio (Soft Story)"]),
                    dbc.CardBody([
                        html.P("Ratio = stiffness of story / stiffness of story above. "
                               "ASCE 7 Type 2a < 70%, Type 2b < 60%.",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="torsion-stiffness-chart",
                                  config={"displayModeBar": True},
                                  style={"height": "380px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3 mb-3"),

        dbc.Row([
            # Eccentricity bubble plot
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-circle-fill me-2"),
                                    "Torsional Eccentricity Bubble Plot"]),
                    dbc.CardBody([
                        html.P("X = story drift ratio X, Y = story drift ratio Y. "
                               "Bubble size = torsional irregularity ratio. "
                               "Colour = story level (bottom = blue → top = red).",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="torsion-bubble-chart",
                                  config={"displayModeBar": True},
                                  style={"height": "400px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=7),

            dbc.Col([
                # Mass irregularity
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-bar-chart me-2"), "Story Mass (Irregularity Check)"]),
                    dbc.CardBody([
                        html.P("ASCE 7 Type 3: mass > 150% of adjacent story.",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="torsion-mass-chart",
                                  config={"displayModeBar": False},
                                  style={"height": "340px"}),
                    ]),
                ], className="shadow-sm mb-3"),

                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-download me-2"), "Export"]),
                    dbc.CardBody(dbc.Button(
                        [html.I(className="bi bi-file-earmark-excel me-1"), "Export Irregularity Report"],
                        id="torsion-dl-xlsx", size="sm", color="outline-success"
                    )),
                ], className="shadow-sm"),
            ], md=5),
        ], className="g-3"),

        dcc.Download(id="torsion-download-xlsx"),
    ], className="page-container")
