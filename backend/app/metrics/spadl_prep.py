from __future__ import annotations

import pandas as pd

from app.services.event_data_service import PITCH_LENGTH, PITCH_WIDTH

SPADL_ACTION_TYPES = {
    "pass": 0,
    "cross": 1,
    "throw_in": 2,
    "freekick_crossed": 3,
    "freekick_short": 4,
    "corner_crossed": 5,
    "corner_short": 6,
    "take_on": 7,
    "foul": 8,
    "tackle": 9,
    "interception": 10,
    "shot": 11,
    "shot_penalty": 12,
    "shot_freekick": 13,
    "keeper_save": 14,
    "keeper_claim": 15,
    "keeper_punch": 16,
    "clearance": 17,
    "bad_touch": 18,
    "dribble": 19,
}

SPADL_RESULTS = {
    "fail": 0,
    "success": 1,
    "offside": 2,
    "own_goal": 3,
    "yellow_card": 4,
    "red_card": 5,
}

SPADL_BODYPARTS = {
    "foot": 0,
    "head": 1,
    "other": 2,
}


def _type_name(row: pd.Series) -> str:
    event_type = str(row.get("type_l", ""))
    tags = {str(tag).strip().lower().replace(" ", "") for tag in row.get("qual_tags", []) if str(tag).strip()}

    if "corner" in tags and "cross" in tags:
        return "corner_crossed"
    if "corner" in tags:
        return "corner_short"
    if "freekick" in tags and "cross" in tags:
        return "freekick_crossed"
    if "freekick" in tags:
        return "freekick_short"
    if "throwin" in tags:
        return "throw_in"
    if "cross" in tags or event_type == "cross":
        return "cross"
    if event_type in {"take on", "takeon"}:
        return "take_on"
    if event_type in {"dribble", "carry", "run"}:
        return "dribble"
    if "shot" in event_type or row.get("is_shot_event"):
        if "penalty" in tags:
            return "shot_penalty"
        if "freekick" in tags:
            return "shot_freekick"
        return "shot"
    if "save" in event_type:
        return "keeper_save"
    if "claim" in event_type:
        return "keeper_claim"
    if "punch" in event_type:
        return "keeper_punch"
    if "interception" in event_type:
        return "interception"
    if "clearance" in event_type:
        return "clearance"
    if "tackle" in event_type or "challenge" in event_type:
        return "tackle"
    if "foul" in event_type:
        return "foul"
    if "touch" in event_type:
        return "bad_touch"
    return "pass"


def _result_name(row: pd.Series) -> str:
    outcome = str(row.get("outcome_l", ""))
    if row.get("is_goal"):
        return "success"
    if outcome in {"successful", "success", "won", "complete", "completed", "accurate"}:
        return "success"
    if "offside" in outcome:
        return "offside"
    return "fail"


def _bodypart_name(row: pd.Series) -> str:
    tags = {str(tag).strip().lower().replace(" ", "") for tag in row.get("qual_tags", []) if str(tag).strip()}
    if {"header", "head", "headed"} & tags:
        return "head"
    return "foot"


def prepare_whoscored_spadl(events_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a SPADL shaped action table from the saved WhoScored event export.

    This is a scaffold for a future VAEP pipeline rather than a drop in replacement
    for the official socceraction provider converters. The output is deliberately
    explicit so it can be audited before plugging it into XGBoost action valuation.
    """
    df = events_df.copy()
    if df.empty:
        return pd.DataFrame(
            columns=[
                "game_id",
                "period_id",
                "time_seconds",
                "team_id",
                "player_id",
                "start_x",
                "start_y",
                "end_x",
                "end_y",
                "type_id",
                "type_name",
                "result_id",
                "result_name",
                "bodypart_id",
                "bodypart_name",
                "original_event_type",
            ]
        )

    period = pd.to_numeric(df.get("period"), errors="coerce").fillna(1).astype(int)
    minute = pd.to_numeric(df.get("minute"), errors="coerce").fillna(0)
    second = pd.to_numeric(df.get("second"), errors="coerce").fillna(0)
    time_seconds = (minute * 60.0) + second

    start_x = pd.to_numeric(df.get("x_120"), errors="coerce") * (105.0 / PITCH_LENGTH)
    start_y = pd.to_numeric(df.get("y_80"), errors="coerce") * (68.0 / PITCH_WIDTH)
    end_x = pd.to_numeric(df.get("end_x_120"), errors="coerce") * (105.0 / PITCH_LENGTH)
    end_y = pd.to_numeric(df.get("end_y_80"), errors="coerce") * (68.0 / PITCH_WIDTH)

    type_names = df.apply(_type_name, axis=1)
    result_names = df.apply(_result_name, axis=1)
    bodypart_names = df.apply(_bodypart_name, axis=1)

    spadl = pd.DataFrame(
        {
            "game_id": pd.to_numeric(df.get("match_id"), errors="coerce").fillna(0).astype(int),
            "period_id": period,
            "time_seconds": time_seconds.round(3),
            "team_id": pd.to_numeric(df.get("team_id"), errors="coerce").fillna(-1).astype(int),
            "player_id": pd.to_numeric(df.get("player_id"), errors="coerce").fillna(-1).astype(int),
            "start_x": start_x.round(3),
            "start_y": start_y.round(3),
            "end_x": end_x.round(3),
            "end_y": end_y.round(3),
            "type_name": type_names,
            "type_id": type_names.map(lambda name: SPADL_ACTION_TYPES.get(name, SPADL_ACTION_TYPES["pass"])).astype(int),
            "result_name": result_names,
            "result_id": result_names.map(lambda name: SPADL_RESULTS.get(name, SPADL_RESULTS["fail"])).astype(int),
            "bodypart_name": bodypart_names,
            "bodypart_id": bodypart_names.map(lambda name: SPADL_BODYPARTS.get(name, SPADL_BODYPARTS["other"])).astype(int),
            "original_event_type": df.get("type", pd.Series([""] * len(df))),
        }
    )
    return spadl