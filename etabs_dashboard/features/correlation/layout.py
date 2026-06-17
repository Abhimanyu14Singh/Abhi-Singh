from dash import html, dcc
import dash_bootstrap_components as dbc


def layout():
    return html.Div([
        html.Div([
            html.H4("Correlation & Sensitivity Analysis", className="page-title"),
            html.P("How similar are load cases? Which ones dominate? Parallel coordinates and correlation matrix.", className="page-subtitle"),
        ], className="page-header"),

        dbc.Card([
            dbc.CardBody(dbc.Row([
                dbc.Col([
                    dbc.Label("Response Metrics to Include"),
                    dbc.Checklist(
                        id="corr-metrics",
                        options=[
                            {"label": "Max Drift X",  "value": "max_drift_X"},
                            {"label": "Max Drift Y",  "value": "max_drift_Y"},
                            {"label": "Max Vx",       "value": "max_Vx"},
                            {"label": "Max Vy",       "value": "max_Vy"},
                            {"label": "Base Fx",      "value": "base_Fx"},
                            {"label": "Base Fy",      "value": "base_Fy"},
                            {"label": "OTM Mx",       "value": "max_Mx"},
                        ],
                        value=["max_drift_X","max_drift_Y","max_Vx","max_Vy","base_Fx","base_Fy"],
                        inline=True,
                    ),
                ], md=10),
                dbc.Col([
                    dbc.Label("Colour by"),
                    dcc.Dropdown(id="corr-color-by",
                                 options=[{"label":"Max Drift X","value":"max_drift_X"},
                                          {"label":"Base Fx","value":"base_Fx"},
                                          {"label":"Max Vx","value":"max_Vx"}],
                                 value="max_drift_X", clearable=False),
                ], md=2),
            ], className="g-2")),
        ], className="shadow-sm mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-diagram-3 me-2"),
                                    "Parallel Coordinates — Load Case Comparison"]),
                    dbc.CardBody([
                        html.P("Each line = one load case. Drag axes to reorder. Brush an axis to filter. "
                               "Lines that cluster together have similar structural demand profiles.",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="corr-parallel-chart",
                                  config={"displayModeBar": True},
                                  style={"height": "420px"}),
                    ]),
                ], className="shadow-sm"),
            ]),
        ], className="g-3 mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-grid me-2"),
                                    "Load Case Correlation Matrix"]),
                    dbc.CardBody([
                        html.P("Pearson correlation of story-level demand between every pair of load cases. "
                               "+1 = identical demand profile, 0 = uncorrelated, −1 = opposing.",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="corr-matrix-chart",
                                  config={"displayModeBar": True},
                                  style={"height": "400px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-bar-chart-fill me-2"),
                                    "Sensitivity — Contribution to Peak Drift"]),
                    dbc.CardBody([
                        html.P("% share of each load case to the maximum drift observed across the building. "
                               "Identifies the governing load case instantly.",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="corr-sensitivity-chart",
                                  config={"displayModeBar": False},
                                  style={"height": "360px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3"),
    ], className="page-container")
