from dash import html, dcc
import dash_bootstrap_components as dbc


def layout():
    return html.Div([
        html.Div([
            html.H4("Heatmap Analysis", className="page-title"),
            html.P("Story × Load Case intensity maps — instantly see which combinations govern which stories.", className="page-subtitle"),
        ], className="page-header"),

        dbc.Card([
            dbc.CardBody(dbc.Row([
                dbc.Col([
                    dbc.Label("Primary Metric"),
                    dcc.Dropdown(
                        id="hm-metric",
                        options=[
                            {"label": "Story Drift X",       "value": "drift_X"},
                            {"label": "Story Drift Y",       "value": "drift_Y"},
                            {"label": "Story Shear Vx",      "value": "Vx"},
                            {"label": "Story Shear Vy",      "value": "Vy"},
                            {"label": "Story OTM Mx",        "value": "Mx"},
                            {"label": "Story OTM My",        "value": "My"},
                            {"label": "Story Torsion T",     "value": "T"},
                        ],
                        value="drift_X", clearable=False,
                    ),
                ], md=3),
                dbc.Col([
                    dbc.Label("Colour Scale"),
                    dcc.Dropdown(
                        id="hm-colorscale",
                        options=[
                            {"label": "YlOrRd (Engineering)",  "value": "YlOrRd"},
                            {"label": "RdYlGn (Traffic Light)","value": "RdYlGn_r"},
                            {"label": "Viridis",               "value": "Viridis"},
                            {"label": "Plasma",                "value": "Plasma"},
                            {"label": "Blues",                 "value": "Blues"},
                        ],
                        value="YlOrRd", clearable=False,
                    ),
                ], md=3),
                dbc.Col([
                    dbc.Label("Normalise"),
                    dbc.RadioItems(
                        id="hm-normalise",
                        options=[
                            {"label": "Raw values",           "value": "raw"},
                            {"label": "% of max",             "value": "pct"},
                            {"label": "Z-score (σ from mean)","value": "zscore"},
                        ],
                        value="raw", inline=True,
                    ),
                ], md=4),
                dbc.Col([
                    dbc.Label(" "),
                    dbc.Button([html.I(className="bi bi-download me-1"), "PNG"],
                               id="hm-dl-png", size="sm", color="outline-secondary"),
                ], md=2),
            ], className="g-2")),
        ], className="shadow-sm mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-grid-3x3 me-2"),
                                    "Story × Load Case Heatmap"]),
                    dbc.CardBody(dcc.Graph(id="hm-main-chart",
                                          config={"displayModeBar": True},
                                          style={"height": "520px"})),
                ], className="shadow-sm"),
            ], md=8),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-bar-chart me-2"), "Column Totals (per Load Case)"]),
                    dbc.CardBody(dcc.Graph(id="hm-col-totals",
                                          config={"displayModeBar": False},
                                          style={"height": "220px"})),
                ], className="shadow-sm mb-3"),
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-bar-chart-steps me-2"), "Row Totals (per Story)"]),
                    dbc.CardBody(dcc.Graph(id="hm-row-totals",
                                          config={"displayModeBar": False},
                                          style={"height": "220px"})),
                ], className="shadow-sm"),
            ], md=4),
        ], className="g-3 mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-trophy me-2"), "Governing Load Cases"]),
                    dbc.CardBody(html.Div(id="hm-governing-table")),
                ], className="shadow-sm"),
            ], md=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-info-circle me-2"), "Statistical Summary"]),
                    dbc.CardBody(html.Div(id="hm-stats-summary")),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3"),
    ], className="page-container")
