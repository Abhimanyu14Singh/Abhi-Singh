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

# ── Structural callbacks ──────────────────────────────────────────────────────
import callbacks.connection              # noqa: E402 F401
import callbacks.navigation              # noqa: E402 F401
import callbacks.charts.overview_cb     # noqa: E402 F401
import callbacks.charts.plan_view_cb    # noqa: E402 F401
import callbacks.charts.story_drifts_cb # noqa: E402 F401
import callbacks.charts.story_forces_cb # noqa: E402 F401
import callbacks.charts.base_reactions_cb   # noqa: E402 F401
import callbacks.charts.frame_forces_cb     # noqa: E402 F401
import callbacks.charts.modal_cb            # noqa: E402 F401
import callbacks.charts.displacements_cb    # noqa: E402 F401
import callbacks.charts.load_patterns_cb    # noqa: E402 F401
import callbacks.charts.torsion_cb          # noqa: E402 F401

# ── Data science / analytics callbacks ───────────────────────────────────────
import callbacks.charts.statistics_cb   # noqa: E402 F401
import callbacks.charts.heatmaps_cb     # noqa: E402 F401
import callbacks.charts.code_checks_cb  # noqa: E402 F401
import callbacks.charts.outliers_cb     # noqa: E402 F401
import callbacks.charts.correlation_cb  # noqa: E402 F401
import callbacks.charts.scorecard_cb    # noqa: E402 F401

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
    app.run(debug=False, port=8050)
