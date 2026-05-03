"""
Shared helpers for working with the dcc.Store data dict.
All functions expect the dict produced by etabs_connector.extract_all_data().
"""
import pandas as pd


def to_df(records):
    """Convert a list-of-dicts (from Store) to a DataFrame safely."""
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


def get_story_names(data):
    if not data or "stories" not in data:
        return []
    return [s["name"] for s in data["stories"]]


def get_story_elevations(data):
    if not data or "stories" not in data:
        return {}
    return {s["name"]: s["elevation"] for s in data["stories"]}


def get_load_cases(data):
    if not data:
        return []
    return data.get("load_cases", [])


def get_load_combos(data):
    if not data:
        return []
    return data.get("load_combos", [])


def get_all_cases_and_combos(data):
    return get_load_cases(data) + get_load_combos(data)


def is_attached(data):
    return bool(data) and data.get("status") == "ok"
