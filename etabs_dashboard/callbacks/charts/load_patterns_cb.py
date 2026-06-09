import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from dash import Input, Output
import dash
from callbacks.charts._helpers import empty_fig, base_layout, make_table, ENGINEERING_COLORS

app = dash.get_app()

# Map load type code → colour family
TYPE_COLORS = {
    "Dead":        "#37474F", "Super Dead":  "#607D8B",
    "Live":        "#1565C0", "Roof Live":   "#1E88E5",
    "Snow":        "#90CAF9", "Wind":        "#2E7D32",
    "Seismic":     "#C62828", "Other":       "#6A1B9A",
}


@app.callback(
    Output("lp-pattern-table",  "children"),
    Output("lp-type-pie",       "figure"),
    Output("lp-sw-bar",         "figure"),
    Output("lp-bubble-chart",   "figure"),
    Output("lp-reaction-bar",   "figure"),
    Input("etabs-data-store",         "data"),
    Input("lp-reaction-component",    "value"),
    Input("theme-store",              "data"),
)
def update_load_patterns(data, reaction_comp, theme):
    theme = theme or "light"
    ef    = empty_fig(theme=theme)
    if not data or data.get("status") != "ok":
        return "", ef, ef, ef, ef

    patterns = data.get("load_patterns", [])
    reactions = data.get("results", {}).get("base_reactions", [])

    if not patterns:
        return "", empty_fig("No load patterns.", theme=theme), ef, ef, ef

    pat_df = pd.DataFrame(patterns)

    # ── Table ─────────────────────────────────────────────────────────────
    table = make_table(pat_df.rename(columns={"name":"Pattern","type_name":"Type","self_wt":"Self-Wt"}))

    # ── Pie: type distribution ────────────────────────────────────────────
    type_counts = pat_df["type_name"].value_counts().reset_index()
    type_counts.columns = ["Type","Count"]
    colors = [TYPE_COLORS.get(t, "#9E9E9E") for t in type_counts["Type"]]
    pie_fig = go.Figure(go.Pie(
        labels=type_counts["Type"], values=type_counts["Count"],
        marker_colors=colors, hole=0.4,
        textinfo="label+percent",
    ))
    base_layout(pie_fig, theme=theme)
    pie_fig.update_layout(showlegend=False, margin={"t": 20, "b": 10, "l": 10, "r": 10})

    # ── Bar: self-weight multipliers ──────────────────────────────────────
    sw_df = pat_df[pat_df["self_wt"] != 0]
    sw_fig = go.Figure(go.Bar(
        x=sw_df["name"], y=sw_df["self_wt"],
        marker_color=[TYPE_COLORS.get(t, "#9E9E9E") for t in sw_df["type_name"]],
        hovertemplate="Pattern: %{x}<br>SW Mult.: %{y:.3f}<extra></extra>",
    ))
    base_layout(sw_fig, theme=theme)
    sw_fig.update_layout(xaxis_title="Pattern", yaxis_title="Self-Wt Multiplier")

    # ── Bubble chart: |Fx| vs |Fy|, size=|Fz| for each reaction ─────────
    if reactions:
        r_df = pd.DataFrame(reactions)
        r_df["abs_Fx"] = r_df.get("Fx", pd.Series(0, index=r_df.index)).abs()
        r_df["abs_Fy"] = r_df.get("Fy", pd.Series(0, index=r_df.index)).abs()
        r_df["abs_Fz"] = r_df.get("Fz", pd.Series(0, index=r_df.index)).abs()
        bubble_fig = go.Figure(go.Scatter(
            x=r_df["abs_Fx"], y=r_df["abs_Fy"],
            mode="markers+text",
            text=r_df["load_case"],
            textposition="top center",
            marker=dict(
                size=r_df["abs_Fz"].clip(lower=1),
                sizemode="area",
                sizeref=2.0 * (r_df["abs_Fz"].max() or 1) / (40**2),
                color=list(range(len(r_df))),
                colorscale="Portland",
                showscale=False,
                line={"width": 1},
            ),
            hovertemplate=(
                "%{text}<br>|Fx|: %{x:.2f}<br>|Fy|: %{y:.2f}"
                "<br>|Fz|: %{marker.size:.2f}<extra></extra>"
            ),
        ))
        base_layout(bubble_fig, theme=theme)
        bubble_fig.update_layout(xaxis_title="|Fx| Lateral", yaxis_title="|Fy| Lateral")
    else:
        bubble_fig = empty_fig("No base reaction data.", theme=theme)

    # ── Reaction bar by pattern ───────────────────────────────────────────
    reaction_comp = reaction_comp or "Fx"
    if reactions and reaction_comp:
        r_df2 = pd.DataFrame(reactions)
        if reaction_comp in r_df2.columns:
            bar_fig = go.Figure(go.Bar(
                x=r_df2["load_case"], y=r_df2[reaction_comp],
                marker_color=ENGINEERING_COLORS[0],
                hovertemplate=f"{reaction_comp}: %{{y:.3f}}<br>Case: %{{x}}<extra></extra>",
            ))
            base_layout(bar_fig, theme=theme)
            bar_fig.update_layout(xaxis_title="Load Case / Pattern",
                                  yaxis_title=reaction_comp)
        else:
            bar_fig = ef
    else:
        bar_fig = ef

    return table, pie_fig, sw_fig, bubble_fig, bar_fig
