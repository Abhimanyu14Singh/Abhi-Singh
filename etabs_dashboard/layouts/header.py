from dash import html, dcc
import dash_bootstrap_components as dbc


def build_header():
    return dbc.Navbar(
        dbc.Container([
            # Brand
            html.A(
                dbc.Row([
                    dbc.Col(html.Img(src="/assets/logo.svg", height="32px"), width="auto"),
                    dbc.Col(dbc.NavbarBrand(
                        [html.Span("ETABS", className="brand-main"),
                         html.Span(" Dashboard", className="brand-sub")],
                        className="ms-2"
                    )),
                ], align="center", className="g-0"),
                href="/", style={"textDecoration": "none"},
            ),

            # Centre: model info
            dbc.Row([
                dbc.Col(
                    html.Div(id="header-model-info",
                             className="header-model-info text-center"),
                    width="auto"
                ),
            ], className="mx-auto", align="center"),

            # Right controls
            dbc.Row([
                dbc.Col(
                    dbc.Badge(id="connection-badge",
                              children=[html.I(className="bi bi-circle-fill me-1"), "Disconnected"],
                              color="danger", pill=True,
                              className="connection-badge me-3"),
                    width="auto"
                ),
                dbc.Col(
                    dbc.Button(
                        [html.I(className="bi bi-plug-fill me-2"), "ATTACH"],
                        id="attach-btn",
                        color="success",
                        size="sm",
                        className="attach-btn me-2",
                        n_clicks=0,
                    ),
                    width="auto"
                ),
                dbc.Col(
                    dbc.Button(
                        html.I(className="bi bi-moon-stars-fill", id="theme-icon"),
                        id="theme-toggle-btn",
                        color="light",
                        outline=True,
                        size="sm",
                        className="theme-btn",
                        n_clicks=0,
                    ),
                    width="auto"
                ),
            ], align="center", className="flex-nowrap"),

        ], fluid=True),
        id="main-navbar",
        color="primary",
        dark=True,
        sticky="top",
        className="main-navbar shadow-sm",
    )
