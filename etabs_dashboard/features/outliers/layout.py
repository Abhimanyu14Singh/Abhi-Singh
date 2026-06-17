from dash import html, dcc
import dash_bootstrap_components as dbc


def layout():
    return html.Div([
        html.Div([
            html.H4("Outlier & Anomaly Detection", className="page-title"),
            html.P("Z-score analysis to automatically flag unusual structural responses across stories and load cases.", className="page-subtitle"),
        ], className="page-header"),

        dbc.Card([
            dbc.CardBody(dbc.Row([
                dbc.Col([
                    dbc.Label("Metric"),
                    dbc.RadioItems(
                        id="out-metric",
                        options=[
                            {"label": "Story Drift",   "value": "drift"},
                            {"label": "Story Shear Vx","value": "Vx"},
                            {"label": "Story Shear Vy","value": "Vy"},
                            {"label": "Base Reaction Fx","value": "Fx"},
                        ],
                        value="drift", inline=True,
                    ),
                ], md=5),
                dbc.Col([
                    dbc.Label("Z-score Threshold (σ)"),
                    dcc.Slider(
                        id="out-zscore-threshold",
                        min=1.0, max=3.5, step=0.5, value=2.0,
                        marks={1.0:"1σ", 1.5:"1.5σ", 2.0:"2σ", 2.5:"2.5σ", 3.0:"3σ", 3.5:"3.5σ"},
                        tooltip={"placement": "bottom"},
                    ),
                ], md=5),
                dbc.Col([
                    dbc.Label("Cases"),
                    dcc.Dropdown(id="out-case-filter", multi=True, placeholder="All…"),
                ], md=2),
            ], className="g-2")),
        ], className="shadow-sm mb-3"),

        # Outlier flag banner
        html.Div(id="out-flag-banner", className="mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-scatter-chart me-2"),
                                    "Z-Score Distribution (Story vs Metric)"]),
                    dbc.CardBody([
                        html.P("Red = outlier beyond threshold. Orange = marginal (0.8× threshold). Grey = normal.",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="out-scatter-chart",
                                  config={"displayModeBar": True},
                                  style={"height": "440px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=7),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-bar-chart me-2"), "Z-Score per Story"]),
                    dbc.CardBody(dcc.Graph(id="out-zscore-bar",
                                          config={"displayModeBar": False},
                                          style={"height": "200px"})),
                ], className="shadow-sm mb-3"),
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-exclamation-triangle me-2"), "Flagged Items"]),
                    dbc.CardBody(html.Div(id="out-flagged-table"),
                                 style={"maxHeight": "180px", "overflowY": "auto"}),
                ], className="shadow-sm"),
            ], md=5),
        ], className="g-3 mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-graph-up me-2"),
                                    "Statistical Soft Story Detection"]),
                    dbc.CardBody([
                        html.P("Linear regression fit on story stiffness proxy (Vx/drift). "
                               "Stories deviating >1σ from trend line are flagged.",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="out-softstory-chart",
                                  config={"displayModeBar": True},
                                  style={"height": "320px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-clipboard-data me-2"),
                                    "Full Statistical Summary"]),
                    dbc.CardBody(html.Div(id="out-stats-table"),
                                 style={"maxHeight": "340px", "overflowY": "auto"}),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3"),
    ], className="page-container")
