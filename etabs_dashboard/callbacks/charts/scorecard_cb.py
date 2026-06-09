import pandas as pd
import numpy as np
import plotly.graph_objects as go
from dash import Input, Output, html, dcc, no_update
import dash, io
import dash_bootstrap_components as dbc
from callbacks.charts._helpers import empty_fig, base_layout, ENGINEERING_COLORS

app = dash.get_app()

# ASCE 7 period Ct/x (metres)
SYSTEM_PARAMS_M  = {"steel_mrf":(0.0853,0.80),"conc_mrf":(0.0466,0.90),"other":(0.0488,0.75)}
SYSTEM_PARAMS_FT = {"steel_mrf":(0.0724,0.80),"conc_mrf":(0.0466,0.90),"other":(0.0488,0.75)}


def _score(value, thresholds):
    """thresholds = [(limit, score), ...] ascending. Returns first match."""
    for limit, sc in thresholds:
        if value <= limit:
            return sc
    return 0


def _traffic(score):
    if score >= 80:
        return "success", "bi-check-circle-fill", "PASS"
    if score >= 50:
        return "warning", "bi-exclamation-triangle-fill", "WARN"
    return "danger", "bi-x-circle-fill", "FAIL"


def _check_card(title, description, value_str, score, limit_str=""):
    color, icon, label = _traffic(score)
    return dbc.Col([
        dbc.Card([
            dbc.CardBody([
                html.Div([
                    html.Div([
                        html.I(className=f"bi {icon} me-2 text-{color}",
                               style={"fontSize": "1.3rem"}),
                        html.Span(label, className=f"fw-bold text-{color}"),
                    ], className="d-flex align-items-center mb-2"),
                    html.P(title, className="fw-semibold mb-1", style={"fontSize":"0.85rem"}),
                    html.P(description, className="text-muted mb-2",
                           style={"fontSize":"0.72rem", "lineHeight":"1.4"}),
                    html.Div([
                        html.Span(value_str, className="fw-bold",
                                  style={"fontFamily":"monospace","fontSize":"0.9rem"}),
                        html.Span(f" {limit_str}", className="text-muted ms-1",
                                  style={"fontSize":"0.72rem"}) if limit_str else None,
                    ]),
                    # Score bar
                    dbc.Progress(value=score, color=color,
                                 style={"height":"4px","marginTop":"8px"}),
                ])
            ])
        ], className=f"shadow-sm border-{color} check-card h-100"),
    ], md=4, className="mb-1")


@app.callback(
    Output("sc-overall-banner", "children"),
    Output("sc-check-cards",    "children"),
    Output("sc-scores-chart",   "figure"),
    Output("sc-recommendations","children"),
    Input("etabs-data-store",   "data"),
    Input("sc-drift-limit",     "value"),
    Input("sc-struct-system",   "value"),
    Input("sc-height-unit",     "value"),
    Input("theme-store",        "data"),
)
def update_scorecard(data, drift_limit, system, h_unit, theme):
    theme = theme or "light"
    ef    = empty_fig(theme=theme)

    if not data or data.get("status") != "ok":
        return _no_data_banner(), [], ef, ""

    checks = _run_all_checks(data, drift_limit, system, h_unit)
    if not checks:
        return _no_data_banner(), [], ef, ""

    overall = round(np.mean([c["score"] for c in checks]))
    banner  = _overall_banner(overall)
    cards   = [_check_card(c["title"], c["desc"], c["value_str"],
                            c["score"], c.get("limit_str",""))
               for c in checks]

    # Scores bar chart
    names  = [c["title"] for c in checks]
    scores = [c["score"] for c in checks]
    colors = ["#2E7D32" if s >= 80 else "#F57F17" if s >= 50 else "#C62828"
              for s in scores]
    fig = go.Figure(go.Bar(
        y=names, x=scores, orientation="h",
        marker_color=colors,
        hovertemplate="%{y}: %{x}<extra></extra>",
    ))
    fig.add_vline(x=80, line_dash="dash", line_color="#2E7D32",
                  annotation_text="Pass threshold")
    fig.add_vline(x=50, line_dash="dot", line_color="#F57F17")
    base_layout(fig, theme=theme)
    fig.update_layout(xaxis={"range": [0, 105], "title": "Score (0–100)"},
                      yaxis_title="", margin={"l": 170})

    recs = _recommendations(checks)
    return banner, cards, fig, recs


def _no_data_banner():
    return dbc.Alert([
        html.I(className="bi bi-info-circle me-2"),
        "Attach to an ETABS model to generate the scorecard.",
    ], color="secondary")


def _overall_banner(score):
    color, icon, label = _traffic(score)
    return dbc.Alert([
        dbc.Row([
            dbc.Col([
                html.H2(f"{score}", className="display-4 fw-bold mb-0",
                        style={"color": f"var(--bs-{color})"}),
                html.Small("/ 100", className="text-muted"),
            ], width="auto", className="d-flex align-items-end"),
            dbc.Col([
                html.H5([html.I(className=f"bi {icon} me-2"), f"Overall: {label}"],
                        className=f"text-{color} mb-1"),
                html.P("Weighted average of all structural checks below.",
                       className="mb-0 small text-muted"),
            ]),
        ], align="center"),
    ], color=color, className="py-3 mb-0")


def _run_all_checks(data, drift_limit, system, h_unit):
    checks = []
    drifts = pd.DataFrame(data.get("results",{}).get("story_drifts",    []))
    forces = pd.DataFrame(data.get("results",{}).get("story_forces",    []))
    rxns   = pd.DataFrame(data.get("results",{}).get("base_reactions",  []))
    modal  = pd.DataFrame(data.get("results",{}).get("modal_periods",   []))
    ratios = pd.DataFrame(data.get("results",{}).get("modal_mass_ratios",[]))
    stories = data.get("stories", [])

    # ── 1. Max Story Drift ────────────────────────────────────────────────
    if not drifts.empty and "drift" in drifts.columns:
        max_d = drifts["drift"].max()
        limit = drift_limit or 0.025
        score = _score(max_d, [(limit*0.5, 100),(limit*0.8, 80),(limit, 60),(limit*1.2, 30)])
        checks.append({
            "title": "Max Story Drift",
            "desc":  "Maximum interstory drift ratio across all stories and load cases.",
            "value_str": f"{max_d:.5f} ({max_d*100:.3f}%)",
            "limit_str": f"Limit: {limit*100:.1f}%",
            "score": score,
        })

    # ── 2. Torsional Irregularity ─────────────────────────────────────────
    if not drifts.empty and "direction" in drifts.columns:
        max_ratio = 1.0
        for story in drifts["story"].unique():
            sub = drifts[drifts["story"] == story]
            for case in sub["load_case"].unique():
                sx = sub[(sub["load_case"]==case)&(sub["direction"]=="X")]["drift"]
                sy = sub[(sub["load_case"]==case)&(sub["direction"]=="Y")]["drift"]
                if not sx.empty and not sy.empty:
                    avg = (sx.max() + sy.max()) / 2
                    if avg > 0:
                        max_ratio = max(max_ratio, max(sx.max(), sy.max()) / avg)
        score = _score(max_ratio, [(1.0, 100),(1.2, 80),(1.4, 40)])
        checks.append({
            "title": "Torsional Irregularity",
            "desc":  "ASCE 7 Type 1a (≥1.2) and Type 1b (≥1.4). Ratio = max drift / avg drift.",
            "value_str": f"{max_ratio:.3f}",
            "limit_str": "Type 1a ≥ 1.2 | Type 1b ≥ 1.4",
            "score": score,
        })

    # ── 3. Soft Story (Stiffness) ─────────────────────────────────────────
    if not drifts.empty and not forces.empty and "Vx" in forces.columns:
        dr_max = drifts.groupby("story")["drift"].max()
        sf_max = forces.groupby("story")["Vx"].max().abs()
        merged = pd.concat([sf_max, dr_max], axis=1).dropna()
        merged.columns = ["Vx","drift"]
        merged["K"] = merged["Vx"] / merged["drift"].clip(lower=1e-9)
        K_ratio = merged["K"] / merged["K"].shift(-1).fillna(merged["K"])
        min_ratio = K_ratio.min()
        score = _score(1 - min_ratio, [(0.0, 100),(0.20, 80),(0.30, 50)])
        checks.append({
            "title": "Soft Story Check",
            "desc":  "ASCE 7 Type 2a: story stiffness < 70% of story above.",
            "value_str": f"Min stiffness ratio: {min_ratio:.3f}",
            "limit_str": "ASCE 7: > 0.70 (Type 2a), > 0.60 (Type 2b)",
            "score": score,
        })

    # ── 4. Modal Completeness (90% mass) ─────────────────────────────────
    if not ratios.empty and "sum_Ux" in ratios.columns:
        hits = ratios[ratios["sum_Ux"] >= 0.90]
        n_modes = int(hits["mode"].min()) if not hits.empty else len(ratios) + 1
        score = _score(n_modes, [(6, 100),(10, 90),(15, 70),(20, 50)])
        checks.append({
            "title": "Modal Completeness",
            "desc":  "Modes needed to reach 90% cumulative mass participation in X.",
            "value_str": f"{n_modes} modes",
            "limit_str": "Target: ≤ 12 modes",
            "score": score,
        })

    # ── 5. Period Ratio T₁/Ta ─────────────────────────────────────────────
    if not modal.empty and stories:
        max_elev = max(s.get("elevation", 0) for s in stories)
        params = SYSTEM_PARAMS_M if h_unit == "m" else SYSTEM_PARAMS_FT
        Ct, x  = params.get(system, params["other"])
        Ta     = Ct * (max_elev ** x)
        if not ratios.empty and "Ux" in ratios.columns:
            merged_m = pd.merge(modal, ratios, on="mode", how="left")
            idx = merged_m["Ux"].idxmax()
            T1  = merged_m.loc[idx, "period"]
            ratio = T1 / Ta if Ta > 0 else 0
            score = _score(ratio, [(1.0, 100),(1.2, 80),(1.4, 50)])
            checks.append({
                "title": "Period Ratio T₁/Ta",
                "desc":  "ASCE 7 §12.8.2: computed T must not exceed Cu×Ta (Cu=1.4).",
                "value_str": f"T₁={T1:.3f}s, Ta={Ta:.3f}s, ratio={ratio:.2f}",
                "limit_str": "Limit: ≤ Cu×Ta = 1.4×Ta",
                "score": score,
            })

    # ── 6. Drift Uniformity (CoV) ─────────────────────────────────────────
    if not drifts.empty:
        cov_df = drifts.groupby("story")["drift"].agg(["mean","std"])
        cov_df["cov"] = cov_df["std"] / cov_df["mean"].replace(0, np.nan)
        max_cov = cov_df["cov"].max()
        if not np.isnan(max_cov):
            score = _score(max_cov, [(0.2, 100),(0.3, 80),(0.5, 50)])
            checks.append({
                "title": "Drift Uniformity (CoV)",
                "desc":  "Coefficient of Variation of drifts across load cases per story. "
                         "High CoV suggests inconsistent load distribution.",
                "value_str": f"Max CoV = {max_cov:.3f}",
                "limit_str": "< 0.3 good | > 0.5 poor",
                "score": score,
            })

    # ── 7. Base Shear Symmetry ────────────────────────────────────────────
    if not rxns.empty and "Fx" in rxns.columns and "Fy" in rxns.columns:
        max_fx = rxns["Fx"].abs().max()
        max_fy = rxns["Fy"].abs().max()
        if max_fy > 0:
            asym = abs(max_fx / max_fy - 1)
            score = _score(asym, [(0.15, 100),(0.30, 70),(0.50, 40)])
            checks.append({
                "title": "Base Shear Symmetry",
                "desc":  "Ratio of max Fx to max Fy across all load cases. "
                         "High asymmetry may indicate plan irregularity.",
                "value_str": f"|Fx/Fy − 1| = {asym:.3f}  (Fx={max_fx:.1f}, Fy={max_fy:.1f})",
                "limit_str": "< 0.30 acceptable",
                "score": score,
            })

    return checks


def _recommendations(checks):
    recs = []
    for c in checks:
        if c["score"] < 50:
            recs.append(dbc.Alert([
                html.I(className="bi bi-x-circle-fill me-2 text-danger"),
                html.Strong(f"{c['title']}: "), c["value_str"],
                html.Br(),
                html.Small(c["desc"], className="text-muted"),
            ], color="danger", className="py-2 mb-2"))
        elif c["score"] < 80:
            recs.append(dbc.Alert([
                html.I(className="bi bi-exclamation-triangle-fill me-2 text-warning"),
                html.Strong(f"{c['title']}: "), c["value_str"],
            ], color="warning", className="py-2 mb-2"))

    if not recs:
        return dbc.Alert([
            html.I(className="bi bi-check-circle-fill me-2"),
            "All checks pass. Model meets the selected code criteria.",
        ], color="success")
    return html.Div(recs)


@app.callback(
    Output("sc-download-xlsx", "data"),
    Input("sc-dl-xlsx",        "n_clicks"),
    Input("etabs-data-store",  "data"),
    Input("sc-drift-limit",    "value"),
    Input("sc-struct-system",  "value"),
    Input("sc-height-unit",    "value"),
    prevent_initial_call=True,
)
def dl_scorecard(n, data, drift_limit, system, h_unit):
    from dash import ctx
    if ctx.triggered_id != "sc-dl-xlsx" or not n or not data:
        return no_update
    checks = _run_all_checks(data, drift_limit, system, h_unit)
    df = pd.DataFrame([{
        "Check": c["title"], "Value": c["value_str"],
        "Limit": c.get("limit_str",""), "Score": c["score"],
        "Status": _traffic(c["score"])[2],
    } for c in checks])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Scorecard")
    return dcc.send_bytes(buf.getvalue(), "ETABS_Scorecard.xlsx")
