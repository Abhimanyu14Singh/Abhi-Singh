from dash import html, dcc
import dash_bootstrap_components as dbc


def layout():
    return html.Div([
        html.Div([
            html.H4("Modal Analysis", className="page-title"),
            html.P("Natural periods, frequencies and participating mass ratios.", className="page-subtitle"),
        ], className="page-header"),

        # KPI cards for dominant periods
        dbc.Row([
            dbc.Col(dbc.Card([
                dbc.CardBody([
                    html.P("Dominant Period — X", className="kpi-title mb-0"),
                    html.H4("—", id="modal-T1-x", className="kpi-value mb-0"),
                    html.Small("seconds", className="text-muted"),
                ])
            ], className="kpi-card shadow-sm"), md=3),
            dbc.Col(dbc.Card([
                dbc.CardBody([
                    html.P("Dominant Period — Y", className="kpi-title mb-0"),
                    html.H4("—", id="modal-T1-y", className="kpi-value mb-0"),
                    html.Small("seconds", className="text-muted"),
                ])
            ], className="kpi-card shadow-sm"), md=3),
            dbc.Col(dbc.Card([
                dbc.CardBody([
                    html.P("Torsional Period", className="kpi-title mb-0"),
                    html.H4("—", id="modal-T1-rz", className="kpi-value mb-0"),
                    html.Small("seconds", className="text-muted"),
                ])
            ], className="kpi-card shadow-sm"), md=3),
            dbc.Col(dbc.Card([
                dbc.CardBody([
                    html.P("Modes to 90% Mass", className="kpi-title mb-0"),
                    html.H4("—", id="modal-modes-90", className="kpi-value mb-0"),
                    html.Small("modes", className="text-muted"),
                ])
            ], className="kpi-card shadow-sm"), md=3),
        ], className="g-3 mb-4"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-bar-chart me-2"), "Mass Participation per Mode"]),
                    dbc.CardBody(dcc.Graph(id="modal-mass-bar",
                                          config={"displayModeBar": True},
                                          style={"height": "380px"})),
                ], className="shadow-sm"),
            ], md=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-graph-up-arrow me-2"), "Cumulative Mass Participation"]),
                    dbc.CardBody(dcc.Graph(id="modal-cumulative-chart",
                                          config={"displayModeBar": True},
                                          style={"height": "380px"})),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3 mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-circle-fill me-2"),
                                    "Period–Mass Participation Bubble Plot"]),
                    dbc.CardBody([
                        html.P("Bubble size = Ux participation, colour = Uy participation. Each bubble = one mode.",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="modal-bubble-chart",
                                  config={"displayModeBar": True},
                                  style={"height": "380px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=7),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-table me-2"), "Modal Summary Table"]),
                    dbc.CardBody(html.Div(id="modal-summary-table"),
                                 style={"maxHeight": "400px", "overflowY": "auto"}),
                ], className="shadow-sm"),
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-download me-2"), "Export"]),
                    dbc.CardBody(dbc.Button(
                        [html.I(className="bi bi-file-earmark-excel me-1"), "Export Excel"],
                        id="modal-dl-xlsx", size="sm", color="outline-success"
                    )),
                ], className="shadow-sm mt-3"),
            ], md=5),
        ], className="g-3"),

        dcc.Download(id="modal-download-xlsx"),
    ], className="page-container")
