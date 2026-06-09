from dash import html, dcc
import dash_bootstrap_components as dbc


def layout():
    return html.Div([
        html.Div([
            html.H4("Load Patterns", className="page-title"),
            html.P("Load pattern types, self-weight multipliers, and demand bubble visualisation.", className="page-subtitle"),
        ], className="page-header"),

        dbc.Row([
            # Pattern summary table
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-cloud-drizzle-fill me-2"), "Defined Load Patterns"]),
                    dbc.CardBody(html.Div(id="lp-pattern-table"),
                                 style={"maxHeight": "340px", "overflowY": "auto"}),
                ], className="shadow-sm h-100"),
            ], md=4),

            # Type distribution chart
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-pie-chart me-2"), "Pattern Type Distribution"]),
                    dbc.CardBody(dcc.Graph(id="lp-type-pie",
                                          config={"displayModeBar": False},
                                          style={"height": "300px"})),
                ], className="shadow-sm h-100"),
            ], md=4),

            # Self-weight multiplier bar
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-bar-chart me-2"), "Self-Weight Multipliers"]),
                    dbc.CardBody(dcc.Graph(id="lp-sw-bar",
                                          config={"displayModeBar": False},
                                          style={"height": "300px"})),
                ], className="shadow-sm h-100"),
            ], md=4),
        ], className="g-3 mb-3"),

        # Base shear per pattern (bubble plot)
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-circle-fill me-2"),
                                    "Load Demand Bubble Plot — Base Shear vs. Pattern"]),
                    dbc.CardBody([
                        html.P(
                            "Bubble size = |Fz| (vertical reaction), X = |Fx|, Y = |Fy|. "
                            "Each bubble represents one load pattern/case.",
                            className="text-muted small mb-2",
                        ),
                        dcc.Graph(id="lp-bubble-chart",
                                  config={"displayModeBar": True},
                                  style={"height": "420px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=7),

            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-bar-chart-fill me-2"), "Base Reactions by Load Pattern"]),
                    dbc.CardBody([
                        dbc.Label("Component"),
                        dbc.RadioItems(
                            id="lp-reaction-component",
                            options=[{"label": c, "value": c} for c in ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]],
                            value="Fx", inline=True, className="mb-2",
                        ),
                        dcc.Graph(id="lp-reaction-bar",
                                  config={"displayModeBar": False},
                                  style={"height": "330px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=5),
        ], className="g-3"),

    ], className="page-container")
