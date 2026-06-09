from dash import html, dcc
import dash_bootstrap_components as dbc


def layout():
    return html.Div([
        html.Div([
            html.H4("Performance Scorecard", className="page-title"),
            html.P("Traffic-light engineering checks — ASCE 7-22 compliant. Calculation-book ready.", className="page-subtitle"),
        ], className="page-header"),

        dbc.Card([
            dbc.CardBody(dbc.Row([
                dbc.Col([
                    dbc.Label("Drift Limit (%)"),
                    dcc.Dropdown(
                        id="sc-drift-limit",
                        options=[
                            {"label": "2.5% — ASCE 7 RC I/II, Moment Frame", "value": 0.025},
                            {"label": "2.0% — ASCE 7 RC I/II, Other",        "value": 0.020},
                            {"label": "1.5% — ASCE 7 RC IV",                  "value": 0.015},
                            {"label": "0.4% — IS 1893",                       "value": 0.004},
                        ],
                        value=0.025, clearable=False,
                    ),
                ], md=4),
                dbc.Col([
                    dbc.Label("Structural System"),
                    dcc.Dropdown(
                        id="sc-struct-system",
                        options=[
                            {"label": "Steel Moment Frame",   "value": "steel_mrf"},
                            {"label": "Concrete Moment Frame","value": "conc_mrf"},
                            {"label": "Other",                "value": "other"},
                        ],
                        value="other", clearable=False,
                    ),
                ], md=3),
                dbc.Col([
                    dbc.Label("Height Units"),
                    dbc.RadioItems(
                        id="sc-height-unit",
                        options=[{"label":"m","value":"m"},{"label":"ft","value":"ft"}],
                        value="m", inline=True,
                    ),
                ], md=2),
                dbc.Col([
                    dbc.Label(" "),
                    dbc.Button([html.I(className="bi bi-file-earmark-excel me-1"), "Export Report"],
                               id="sc-dl-xlsx", size="sm", color="outline-success"),
                ], md=3),
            ], className="g-2")),
        ], className="shadow-sm mb-3"),

        # Overall score banner
        html.Div(id="sc-overall-banner", className="mb-4"),

        # Check cards grid
        dbc.Row(id="sc-check-cards", className="g-3 mb-4"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-bar-chart me-2"), "Check Scores (0–100)"]),
                    dbc.CardBody(dcc.Graph(id="sc-scores-chart",
                                          config={"displayModeBar": False},
                                          style={"height": "320px"})),
                ], className="shadow-sm"),
            ], md=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-clipboard-check me-2"),
                                    "Recommendations"]),
                    dbc.CardBody(html.Div(id="sc-recommendations")),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3"),

        dcc.Download(id="sc-download-xlsx"),
    ], className="page-container")
