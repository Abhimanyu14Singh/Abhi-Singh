from dash import html, dcc
import dash_bootstrap_components as dbc
from layouts.header import build_header
from layouts.sidebar import build_sidebar


def build_layout():
    return html.Div(
        id="app-root",
        children=[
            # ── Stores ────────────────────────────────────────────────────
            dcc.Store(id="etabs-data-store",    storage_type="memory"),
            dcc.Store(id="theme-store",          data="light"),
            dcc.Store(id="selected-frame-store", data=None),
            dcc.Location(id="url"),

            # ── Header ────────────────────────────────────────────────────
            build_header(),

            # ── Body: Sidebar + Page Content ──────────────────────────────
            html.Div(
                [
                    build_sidebar(),
                    html.Div(id="page-content", className="page-content"),
                ],
                className="app-body",
            ),

            # ── Global attach loading overlay ─────────────────────────────
            dbc.Modal(
                [
                    dbc.ModalBody(
                        html.Div([
                            dbc.Spinner(color="primary", size="lg"),
                            html.P("Connecting to ETABS and extracting data…",
                                   className="mt-3 text-center"),
                        ], className="d-flex flex-column align-items-center py-4"),
                    )
                ],
                id="loading-modal",
                is_open=False,
                centered=True,
                backdrop="static",
                keyboard=False,
            ),

            # ── Toast notifications ───────────────────────────────────────
            html.Div(
                dbc.Toast(
                    id="notif-toast-body",
                    header="Notification",
                    is_open=False,
                    dismissable=True,
                    duration=4000,
                    icon="primary",
                    style={"position": "fixed", "top": 70, "right": 20, "zIndex": 9999,
                           "minWidth": "300px"},
                ),
                id="toast-container",
            ),
        ],
        className="app-root",
    )
