from dash import html, dcc
import dash_bootstrap_components as dbc


def layout():
    return html.Div([
        html.Div([
            html.H4("Statistical Summary", className="page-title"),
            html.P("Box plots, violin plots, CoV and percentile analysis of structural responses across all load cases.", className="page-subtitle"),
        ], className="page-header"),

        dbc.Card([
            dbc.CardBody(dbc.Row([
                dbc.Col([
                    dbc.Label("Metric"),
                    dbc.RadioItems(
                        id="stats-metric",
                        options=[
                            {"label": "Story Drift",  "value": "drift"},
                            {"label": "Story Shear Vx","value": "Vx"},
                            {"label": "Story Shear Vy","value": "Vy"},
                            {"label": "OTM Mx",        "value": "Mx"},
                        ],
                        value="drift", inline=True,
                    ),
                ], md=5),
                dbc.Col([
                    dbc.Label("Load Cases"),
                    dcc.Dropdown(id="stats-case-filter", multi=True, placeholder="All cases (default)"),
                ], md=5),
                dbc.Col([
                    dbc.Label(" "),
                    dbc.Button([html.I(className="bi bi-file-earmark-excel me-1"), "Export"],
                               id="stats-dl-xlsx", size="sm", color="outline-success"),
                ], md=2),
            ], className="g-2")),
        ], className="shadow-sm mb-3"),

        # KPI stats row
        dbc.Row([
            dbc.Col(dbc.Card(dbc.CardBody([
                html.P("Mean (all stories)", className="kpi-title mb-0"),
                html.H5("—", id="stats-kpi-mean", className="kpi-value mb-0"),
            ]), className="kpi-card shadow-sm"), md=2),
            dbc.Col(dbc.Card(dbc.CardBody([
                html.P("Median", className="kpi-title mb-0"),
                html.H5("—", id="stats-kpi-median", className="kpi-value mb-0"),
            ]), className="kpi-card shadow-sm"), md=2),
            dbc.Col(dbc.Card(dbc.CardBody([
                html.P("Std Dev", className="kpi-title mb-0"),
                html.H5("—", id="stats-kpi-std", className="kpi-value mb-0"),
            ]), className="kpi-card shadow-sm"), md=2),
            dbc.Col(dbc.Card(dbc.CardBody([
                html.P("95th Percentile", className="kpi-title mb-0"),
                html.H5("—", id="stats-kpi-p95", className="kpi-value mb-0"),
            ]), className="kpi-card shadow-sm"), md=2),
            dbc.Col(dbc.Card(dbc.CardBody([
                html.P("Max CoV (story)", className="kpi-title mb-0"),
                html.H5("—", id="stats-kpi-cov", className="kpi-value mb-0"),
            ]), className="kpi-card shadow-sm"), md=2),
            dbc.Col(dbc.Card(dbc.CardBody([
                html.P("Skewness", className="kpi-title mb-0"),
                html.H5("—", id="stats-kpi-skew", className="kpi-value mb-0"),
            ]), className="kpi-card shadow-sm"), md=2),
        ], className="g-3 mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-box me-2"), "Box Plot — Response Distribution by Story"]),
                    dbc.CardBody(dcc.Graph(id="stats-box-chart",
                                          config={"displayModeBar": True},
                                          style={"height": "460px"})),
                ], className="shadow-sm"),
            ], md=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-activity me-2"), "Violin Plot — Density by Story"]),
                    dbc.CardBody(dcc.Graph(id="stats-violin-chart",
                                          config={"displayModeBar": True},
                                          style={"height": "460px"})),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3 mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-bar-chart me-2"),
                                    "Coefficient of Variation (CoV) per Story"]),
                    dbc.CardBody([
                        html.P("CoV = σ/μ across load cases per story. "
                               "High CoV → inconsistent demand → review load combinations.",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="stats-cov-chart",
                                  config={"displayModeBar": False},
                                  style={"height": "320px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=5),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-graph-up me-2"),
                                    "Percentile Bands (5th / 25th / 75th / 95th)"]),
                    dbc.CardBody([
                        html.P("Shaded bands show interquartile range and 90% confidence interval of response.",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="stats-percentile-chart",
                                  config={"displayModeBar": True},
                                  style={"height": "320px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=7),
        ], className="g-3"),

        dcc.Download(id="stats-download-xlsx"),
    ], className="page-container")
