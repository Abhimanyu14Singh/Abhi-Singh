"""Attach button — connects to ETABS and populates dcc.Store."""
from dash import Input, Output, no_update
from etabs_connector import get_etabs_model, extract_all_data
import datetime
import dash


@dash.get_app().callback(
    Output("etabs-data-store",   "data"),
    Output("connection-badge",   "children"),
    Output("connection-badge",   "color"),
    Output("header-model-info",  "children"),
    Output("loading-modal",      "is_open"),
    Output("notif-toast-body",   "children"),
    Output("notif-toast-body",   "header"),
    Output("notif-toast-body",   "is_open"),
    Output("notif-toast-body",   "icon"),
    Input("attach-btn",          "n_clicks"),
    prevent_initial_call=True,
)
def attach_to_etabs(n_clicks):
    from dash import html
    import dash_bootstrap_components as dbc

    SapModel, err = get_etabs_model()
    if err:
        return (
            no_update,
            [html.I(className="bi bi-circle-fill me-1"), "Disconnected"], "danger",
            no_update,
            False,
            err, "Connection Failed", True, "danger",
        )

    data = extract_all_data(SapModel)

    if data.get("status") != "ok":
        return (
            no_update,
            [html.I(className="bi bi-circle-fill me-1"), "Error"], "warning",
            no_update,
            False,
            str(data.get("status")), "Extraction Error", True, "warning",
        )

    info = data.get("model_info", {})
    ts   = datetime.datetime.now().strftime("%H:%M:%S")
    badge_txt = [html.I(className="bi bi-circle-fill me-1"), "Connected"]
    model_info_div = html.Div([
        html.Span(info.get("filename", ""), className="model-filename me-2"),
        html.Span(f"│ {info.get('units', '')} │ "
                  f"{info.get('num_stories', 0)} stories │ "
                  f"{info.get('num_frames', 0)} frames",
                  className="model-meta"),
        html.Span(f" @ {ts}", className="model-ts ms-2"),
    ], className="d-flex align-items-center")

    toast_msg = (
        f"Attached to {info.get('filename', 'model')}. "
        f"{info.get('num_joints', 0)} joints, "
        f"{info.get('num_frames', 0)} frames extracted."
    )

    return (
        data,
        badge_txt, "success",
        model_info_div,
        False,
        toast_msg, "ETABS Connected", True, "success",
    )
