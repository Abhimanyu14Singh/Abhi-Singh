"""Shared plotting helpers for all chart callbacks."""
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

LIGHT_TEMPLATE = "plotly_white"
DARK_TEMPLATE  = "plotly_dark"

BLUE_PALETTE = px.colors.qualitative.Bold
ENGINEERING_COLORS = [
    "#1565C0", "#C62828", "#2E7D32", "#F57F17",
    "#6A1B9A", "#00838F", "#4E342E", "#37474F",
]


def get_template(theme="light"):
    return DARK_TEMPLATE if theme == "dark" else LIGHT_TEMPLATE


def empty_fig(msg="Attach to ETABS to load data.", theme="light"):
    fig = go.Figure()
    fig.update_layout(
        template=get_template(theme),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        annotations=[{
            "text": msg, "xref": "paper", "yref": "paper",
            "x": 0.5, "y": 0.5, "showarrow": False,
            "font": {"size": 14, "color": "#aaa"},
        }],
        xaxis={"visible": False}, yaxis={"visible": False},
    )
    return fig


def base_layout(fig, title="", theme="light", **kwargs):
    fig.update_layout(
        template=get_template(theme),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin={"t": 40, "b": 40, "l": 50, "r": 20},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
        font={"family": "Inter, Arial, sans-serif", "size": 12},
        title={"text": title, "font": {"size": 13}, "x": 0.01} if title else None,
        **kwargs,
    )
    return fig


def make_table(df, max_rows=500):
    """Convert DataFrame to a Dash Bootstrap styled HTML table."""
    from dash import html
    import dash_bootstrap_components as dbc
    if df is None or df.empty:
        return html.P("No data available.", className="text-muted small")
    df = df.head(max_rows).round(4)
    header = html.Thead(html.Tr([html.Th(c, className="table-col-header") for c in df.columns]))
    rows   = [html.Tr([html.Td(v) for v in row]) for row in df.values]
    body   = html.Tbody(rows)
    return dbc.Table([header, body],
                     striped=True, bordered=False, hover=True,
                     responsive=True, size="sm", className="data-table")
