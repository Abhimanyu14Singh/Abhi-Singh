from dash import html, dcc
import dash_bootstrap_components as dbc


STRUCT_SYSTEMS = [
    {"label": "Steel Moment Resisting Frame",      "value": "steel_mrf"},
    {"label": "Concrete Moment Resisting Frame",   "value": "conc_mrf"},
    {"label": "Steel Eccentrically Braced Frame",  "value": "steel_ebf"},
    {"label": "Steel Buckling-Restrained Braced",  "value": "steel_brbf"},
    {"label": "All Other Structural Systems",      "value": "other"},
]


def layout():
    return html.Div([
        html.Div([
            html.H4("Empirical Code Checks", className="page-title"),
            html.P("ASCE 7-22 approximate period, base shear cross-check, and empirical comparisons.", className="page-subtitle"),
        ], className="page-header"),

        dbc.Card([
            dbc.CardBody(dbc.Row([
                dbc.Col([
                    dbc.Label("Structural System"),
                    dcc.Dropdown(id="cc-struct-system", options=STRUCT_SYSTEMS,
                                 value="other", clearable=False),
                ], md=4),
                dbc.Col([
                    dbc.Label("Height Units (for Ta formula)"),
                    dbc.RadioItems(
                        id="cc-height-unit",
                        options=[{"label": "metres (SI)", "value": "m"},
                                 {"label": "feet (US)",   "value": "ft"}],
                        value="m", inline=True,
                    ),
                ], md=3),
                dbc.Col([
                    dbc.Label("Dead Load Case (for seismic weight)"),
                    dcc.Dropdown(id="cc-dead-case", placeholder="Select DEAD case…"),
                ], md=3),
                dbc.Col([
                    dbc.Label("Seismic Load Case"),
                    dcc.Dropdown(id="cc-seismic-case", placeholder="Select EX/EY…"),
                ], md=2),
            ], className="g-2")),
        ], className="shadow-sm mb-3"),

        # Alert row
        html.Div(id="cc-alert-row", className="mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-activity me-2"),
                                    "Modal Period vs ASCE 7-22 Approximate Period (Ta)"]),
                    dbc.CardBody([
                        html.P("Solid dots = computed periods | Dashed = Ta | Dotted = Cu×Ta (upper bound = 1.4×Ta). "
                               "Computed T should be < Cu×Ta per ASCE 7 §12.8.2.",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="cc-period-chart",
                                  config={"displayModeBar": True},
                                  style={"height": "420px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=7),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-table me-2"), "Period Comparison Table"]),
                    dbc.CardBody(html.Div(id="cc-period-table"),
                                 style={"maxHeight": "200px", "overflowY": "auto"}),
                ], className="shadow-sm mb-3"),
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-building me-2"), "Building Parameters"]),
                    dbc.CardBody(html.Div(id="cc-building-params")),
                ], className="shadow-sm"),
            ], md=5),
        ], className="g-3 mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-arrows-collapse me-2"),
                                    "Base Shear Cross-Check (V = Cs × W proxy)"]),
                    dbc.CardBody([
                        html.P("Compares ETABS base shear to an empirical estimate using Cs = V/W from the model. "
                               "Large divergence may indicate missing mass or load definition issues.",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="cc-baseshear-chart",
                                  config={"displayModeBar": False},
                                  style={"height": "300px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-graph-up me-2"),
                                    "Period Scaling: Computed vs Empirical Trend"]),
                    dbc.CardBody([
                        html.P("Scatter of mode number vs period. "
                               "Theoretical trend line T_n ≈ T1/n shown for comparison.",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="cc-period-trend-chart",
                                  config={"displayModeBar": False},
                                  style={"height": "300px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3"),

    ], className="page-container")
