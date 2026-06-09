from dash import html, dcc
import dash_bootstrap_components as dbc


def layout():
    return html.Div([
        html.Div([
            html.H4("Base Reactions", className="page-title"),
            html.P("Global base reactions: forces and moments at the base for each load case.", className="page-subtitle"),
        ], className="page-header"),

        dbc.Card([
            dbc.CardBody(dbc.Row([
                dbc.Col([
                    dbc.Label("Load Cases / Combos"),
                    dcc.Dropdown(id="br-case-select", multi=True, placeholder="Select cases…"),
                ], md=6),
                dbc.Col([
                    dbc.Label("Components"),
                    dbc.Checklist(
                        id="br-components",
                        options=[
                            {"label": "Fx", "value": "Fx"}, {"label": "Fy", "value": "Fy"},
                            {"label": "Fz", "value": "Fz"}, {"label": "Mx", "value": "Mx"},
                            {"label": "My", "value": "My"}, {"label": "Mz", "value": "Mz"},
                        ],
                        value=["Fx", "Fy", "Fz"], inline=True,
                    ),
                ], md=4),
                dbc.Col([
                    dbc.Label(" "),
                    html.Div([
                        dbc.Button([html.I(className="bi bi-download me-1"), "PNG"],
                                   id="br-dl-png", size="sm", color="outline-secondary", className="me-1"),
                        dbc.Button([html.I(className="bi bi-file-earmark-excel me-1"), "Excel"],
                                   id="br-dl-xlsx", size="sm", color="outline-success"),
                    ]),
                ], md=2),
            ], className="g-2")),
        ], className="shadow-sm mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-bar-chart-fill me-2"), "Force Reactions (Fx, Fy, Fz)"]),
                    dbc.CardBody(dcc.Graph(id="br-force-chart",
                                          config={"displayModeBar": True},
                                          style={"height": "420px"})),
                ], className="shadow-sm"),
            ], md=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-arrow-repeat me-2"), "Moment Reactions (Mx, My, Mz)"]),
                    dbc.CardBody(dcc.Graph(id="br-moment-chart",
                                          config={"displayModeBar": True},
                                          style={"height": "420px"})),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3 mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-circle-fill me-2"), "Reaction Bubble Plot"]),
                    dbc.CardBody([
                        html.P("Bubble size = |Fz|, X = Fx, Y = Fy. Visualises resultant lateral vs. gravity demand.",
                               className="text-muted small mb-2"),
                        dcc.Graph(id="br-bubble-chart",
                                  config={"displayModeBar": True},
                                  style={"height": "380px"}),
                    ]),
                ], className="shadow-sm"),
            ], md=7),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-table me-2"), "Reactions Table"]),
                    dbc.CardBody(html.Div(id="br-data-table"),
                                 style={"maxHeight": "420px", "overflowY": "auto"}),
                ], className="shadow-sm"),
            ], md=5),
        ], className="g-3"),

        dcc.Download(id="br-download-xlsx"),
    ], className="page-container")
