from dash import html, dcc
import dash_bootstrap_components as dbc


def layout():
    return html.Div([
        html.Div([
            html.H4("2D Plan View", className="page-title"),
            html.P("Interactive floor plan — click a frame element to inspect its forces.", className="page-subtitle"),
        ], className="page-header"),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardBody([
                        dbc.Row([
                            dbc.Col([
                                dbc.Label("Story Level", html_for="plan-story-select"),
                                dcc.Dropdown(id="plan-story-select", placeholder="Select story…", clearable=False),
                            ], md=4),
                            dbc.Col([
                                dbc.Label("Show Elements"),
                                dbc.Checklist(
                                    id="plan-element-types",
                                    options=[
                                        {"label": "Frames (Beams/Cols)", "value": "frames"},
                                        {"label": "Shell Elements",      "value": "shells"},
                                        {"label": "Joints",              "value": "joints"},
                                    ],
                                    value=["frames", "shells"],
                                    inline=True,
                                    className="mt-1",
                                ),
                            ], md=5),
                            dbc.Col([
                                dbc.Label(" "),
                                html.Div([
                                    dbc.Button(
                                        [html.I(className="bi bi-download me-1"), "PNG"],
                                        id="plan-download-png", size="sm", color="outline-secondary", className="me-1"
                                    ),
                                ], className="d-flex"),
                            ], md=3),
                        ], className="g-2"),
                    ])
                ], className="shadow-sm mb-3"),
            ])
        ]),

        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardBody(
                        dcc.Graph(
                            id="plan-view-chart",
                            config={"scrollZoom": True, "displayModeBar": True,
                                    "modeBarButtonsToRemove": ["select2d", "lasso2d"]},
                            style={"height": "600px"},
                            clear_on_unhover=True,
                        )
                    )
                ], className="shadow-sm"),
            ], md=8),

            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-info-circle me-2"), "Element Inspector"]),
                    dbc.CardBody(html.Div(id="plan-element-inspector",
                                         children=html.P("Click a frame element on the plan to inspect it.",
                                                         className="text-muted small"))),
                ], className="shadow-sm mb-3"),
                dbc.Card([
                    dbc.CardHeader([html.I(className="bi bi-rulers me-2"), "Quick Frame Forces"]),
                    dbc.CardBody([
                        html.Div(id="plan-quick-forces",
                                 children=html.P("Select a frame from the plan to load forces.",
                                                 className="text-muted small")),
                        dbc.Button(
                            [html.I(className="bi bi-arrow-right-circle me-1"), "Full Analysis →"],
                            id="plan-goto-frame-forces",
                            color="primary", outline=True, size="sm", className="mt-2",
                        ),
                    ]),
                ], className="shadow-sm"),
            ], md=4),
        ], className="g-3"),

        dcc.Download(id="plan-download-png-data"),
    ], className="page-container")
