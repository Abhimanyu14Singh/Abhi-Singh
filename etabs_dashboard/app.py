import sys
import os
from multiprocessing import freeze_support

# PyInstaller: when running as a frozen .exe, move to the bundle root so
# that Dash can locate the 'assets/' folder via relative path.
if getattr(sys, 'frozen', False):
    os.chdir(sys._MEIPASS)

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, clientside_callback

LIGHT_THEME = dbc.themes.FLATLY
DARK_THEME  = "https://cdn.jsdelivr.net/npm/bootswatch@5/dist/darkly/bootstrap.min.css"

app = dash.Dash(
    __name__,
    external_stylesheets=[LIGHT_THEME, dbc.icons.BOOTSTRAP],
    suppress_callback_exceptions=True,
    title="ETABS Dashboard",
    update_title=None,
)
server = app.server

from layouts.main_layout import build_layout   # noqa: E402
app.layout = build_layout()

# ── Infrastructure callbacks ──────────────────────────────────────────────────
import callbacks.connection   # noqa: E402 F401
import callbacks.navigation   # noqa: E402 F401

# ── Structural feature callbacks ──────────────────────────────────────────────
import features.overview.callbacks        # noqa: E402 F401
import features.plan_view.callbacks       # noqa: E402 F401
import features.story_drifts.callbacks    # noqa: E402 F401
import features.story_forces.callbacks    # noqa: E402 F401
import features.base_reactions.callbacks  # noqa: E402 F401
import features.frame_forces.callbacks    # noqa: E402 F401
import features.modal.callbacks           # noqa: E402 F401
import features.displacements.callbacks   # noqa: E402 F401
import features.load_patterns.callbacks   # noqa: E402 F401
import features.torsion.callbacks         # noqa: E402 F401

# ── Analytics feature callbacks ───────────────────────────────────────────────
import features.statistics.callbacks      # noqa: E402 F401
import features.heatmaps.callbacks        # noqa: E402 F401
import features.code_checks.callbacks     # noqa: E402 F401
import features.outliers.callbacks        # noqa: E402 F401
import features.correlation.callbacks     # noqa: E402 F401
import features.scorecard.callbacks       # noqa: E402 F401

# ── Client-side dark/light theme switcher ────────────────────────────────────
clientside_callback(
    f"""
    function(n_clicks, current_theme) {{
        if (!n_clicks) return window.dash_clientside.no_update;
        const isDark   = current_theme === 'dark';
        const newTheme = isDark ? 'light' : 'dark';
        document.documentElement.setAttribute('data-bs-theme', newTheme);
        document.body.setAttribute('data-theme', newTheme);
        const sheets = document.querySelectorAll('link[rel="stylesheet"]');
        sheets.forEach(function(s) {{
            if (s.href && (s.href.includes('flatly') || s.href.includes('darkly'))) {{
                s.href = newTheme === 'dark'
                    ? '{DARK_THEME}'
                    : '{LIGHT_THEME}';
            }}
        }});
        return newTheme;
    }}
    """,
    Output("theme-store", "data"),
    Input("theme-toggle-btn", "n_clicks"),
    Input("theme-store", "data"),
    prevent_initial_call=True,
)

if __name__ == "__main__":
    freeze_support()
    app.run(debug=False, port=8050)
