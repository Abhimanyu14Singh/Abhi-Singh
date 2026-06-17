from dash import html, dcc
import dash_bootstrap_components as dbc


def layout():
    return html.Div([
        html.Div([
            html.H4("Story Forces & Shears", className="page-title"),
            html.P("Story shear, overturning moment, and axial force distribution per load case.", className="page-subtitle"),
        ], className="page-header"),

        dbc.Card([
            dbc.CardBody(dbc.Row([
                dbc.Col([
                    dbc.Label("Load Case / Combo"),
                    dcc.Dropdown(id="sf-case-select", multi=True, placeholder="Select cases…"),
                ], md=5),
                dbc.Col([
                    dbc.Label("Plot Component"),
                    dbc.RadioItems(
                        id="sf-component",
                        options=[
                            {"label": "Vx", "value": "Vx"},
                            {"label": "Vy", "value": "Vy"},
                            {"label": "T (Torque)", "value": "T"},
                            {"label": "Mx (OTM)", "value": "Mx"},
                            {"label": "My (OTM)", "value": "My"},
                        ],
                        value="Vx", inline=True,
                    ),
                ], md=5),
                dbc.Col([
                    dbc.Label(" "),
                    html.Div([
                        dbc.Button([html.I(className="bi bi-download me-1"), "PNG"],
                                   id="sf-dl-png", size="sm", color="outline-secondary", className="me-1"),
                        dbc.Button([html.I(className="bi bi-file-earmark-excel me-1"), "Excel"],
                                   id="sf-dl-xlsx", size="sm", color="outline-success"),
                    ]),
                ], md=2),
            ], className="g-2")),
        ], className="shadow-sm mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-bar-chart-line me-2"), "Story Shear Profile"]),
                    dbc.CardBody(dcc.Graph(id="sf-shear-profile",
                                          config={"displayModeBar": True},
                                          style={"height": "480px"})),
                ], className="shadow-sm"),
            ], md=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-graph-up me-2"), "Overturning Moment Profile"]),
                    dbc.CardBody(dcc.Graph(id="sf-otm-profile",
                                          config={"displayModeBar": True},
                                          style={"height": "480px"})),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3 mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-arrow-repeat me-2"), "Torsional Story Force"]),
                    dbc.CardBody(dcc.Graph(id="sf-torsion-chart",
                                          config={"displayModeBar": True},
                                          style={"height": "360px"})),
                ], className="shadow-sm"),
            ], md=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-table me-2"), "Story Forces Table"]),
                    dbc.CardBody(html.Div(id="sf-data-table"),
                                 style={"maxHeight": "380px", "overflowY": "auto"}),
                ], className="shadow-sm"),
            ], md=6),
        ], className="g-3"),

        dcc.Download(id="sf-download-xlsx"),
    ], className="page-container")
