from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd


FINAL_THIRD_X = 66.67
BOX_X = 83.0
BOX_Y_MIN = 21.1
BOX_Y_MAX = 78.9
ATTACKING_THIRD_X = 66.67

NULL_TEXT = {"", "nan", "none", "null", "<na>", "false", "0"}
SUCCESS_OUTCOMES = {"successful", "success", "won", "complete", "completed", "accurate"}
SHOT_TYPES = {
    "goal",
    "missedshots",
    "savedshot",
    "shotonpost",
    "blockedshot",
    "attemptsaved",
}
SHOT_ON_TARGET_TYPES = {"goal", "savedshot", "attemptsaved"}
VALID_CARD_VALUES = {
    "yellow",
    "yellowcard",
    "red",
    "redcard",
    "secondyellow",
    "secondyellowcard",
    "secondyellowred",
    "secondyellowredcard",
}
SECOND_YELLOW_VALUES = {"secondyellow", "secondyellowcard", "secondyellowred", "secondyellowredcard"}
YELLOW_VALUES = {"yellow", "yellowcard"}
RED_VALUES = {"red", "redcard"}
PROVIDER_XG_COLUMNS = [
    "xg",
    "xG",
    "expected_goals",
    "expectedGoals",
    "shot_xg",
    "shotXg",
    "np_xg",
    "non_penalty_xg",
]


def _clean_text_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_clean_text_value(item) for item in value if _clean_text_value(item))
    if isinstance(value, dict):
        return ", ".join(f"{_clean_text_value(k)}:{_clean_text_value(v)}" for k, v in value.items())
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    if text.lower() in NULL_TEXT:
        return ""
    return text


def _text_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index, dtype="object")
    return df[col].apply(_clean_text_value).astype(str)


def _first_text_series(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
    out = pd.Series([""] * len(df), index=df.index, dtype="object")
    for col in candidates:
        if col not in df.columns:
            continue
        values = _text_series(df, col)
        take = out.str.strip().eq("") & values.str.strip().ne("")
        out.loc[take] = values.loc[take]
    return out


def _compact_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().str.replace(r"[^a-z0-9]+", "", regex=True)


def _bool_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    raw = df[col]
    if pd.api.types.is_bool_dtype(raw):
        return raw.fillna(False).astype(bool)
    return raw.apply(_clean_text_value).astype(str).str.lower().isin({"true", "1", "yes", "y"})


def _num_series(df: pd.DataFrame, candidates: list[str], default: float | None = math.nan) -> pd.Series:
    out = pd.Series(default, index=df.index, dtype="float64")
    for col in candidates:
        if col not in df.columns:
            continue
        values = pd.to_numeric(df[col], errors="coerce")
        take = out.isna() & values.notna()
        out.loc[take] = values.loc[take]
    return out


def _pct_coordinate(df: pd.DataFrame, raw_col: str, scaled_col: str, scale: float) -> pd.Series:
    raw = _num_series(df, [raw_col])
    scaled = _num_series(df, [scaled_col])
    if raw.notna().any():
        values = raw.copy()
        finite = values.dropna()
        if not finite.empty and float(finite.max()) <= 1.5:
            values = values * 100.0
        if not finite.empty and float(finite.max()) > 105.0:
            values = values / scale
        return values.clip(lower=0.0, upper=100.0)
    if scaled.notna().any():
        return (scaled / scale).clip(lower=0.0, upper=100.0)
    return pd.Series(math.nan, index=df.index, dtype="float64")


def _json_number(value: object, digits: int | None = None) -> float | int:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric) or not math.isfinite(float(numeric)):
        return 0.0 if digits is not None else 0
    if digits is None:
        return int(numeric)
    return round(float(numeric), digits)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value) if math.isfinite(value) else 0.0
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    return value


def _empty_team_summary() -> dict[str, Any]:
    return {
        "passes": 0,
        "pass_completion_pct": 0.0,
        "shots": 0,
        "shots_on_target": 0,
        "shot_accuracy_pct": 0.0,
        "xg": 0.0,
        "xg_source": "none",
        "goals": 0,
        "crosses": 0,
        "take_ons": 0,
        "successful_take_ons": 0,
        "take_on_success_pct": 0.0,
        "carries": 0,
        "inferred_carries": 0,
        "progressive_carries": 0,
        "carry_final_third_entries": 0,
        "carry_box_entries": 0,
        "final_third_entries": 0,
        "penalty_area_entries": 0,
        "box_entries": 0,
        "open_play_box_entries": 0,
        "set_piece_box_entries": 0,
        "average_field_position": 0.0,
        "defensive_actions": 0,
        "touches_in_attacking_third": 0,
        "high_regains": 0,
        "transition_threat_events": 0,
        "transition_threat_proxy": 0.0,
        "set_piece_actions": 0,
        "set_piece_shots": 0,
        "set_piece_goals": 0,
        "corners": 0,
        "free_kicks": 0,
        "throw_ins": 0,
        "penalties": 0,
        "cards": 0,
        "yellow_cards": 0,
        "red_cards": 0,
        "second_yellow_red_cards": 0,
        "fouls": 0,
        "interceptions": 0,
        "stat_audit": {
            "status": "empty",
            "message": "No events were available for this team.",
        },
    }


def _prepare_stat_frame(events: pd.DataFrame) -> pd.DataFrame:
    out = events.copy()
    out["type_text"] = _first_text_series(out, ["event_type_l", "type_l", "event_type", "type"])
    out["outcome_text"] = _first_text_series(out, ["outcome_type_l", "outcome_l", "outcome_type", "outcome"])
    out["qual_text"] = _first_text_series(out, ["qualifier_tags", "qual_tags", "qualifiers"])
    out["card_text"] = _text_series(out, "card_type")

    out["type_compact"] = _compact_series(out["type_text"])
    out["outcome_compact"] = _compact_series(out["outcome_text"])
    out["qual_compact"] = _compact_series(out["qual_text"])
    out["card_compact"] = _compact_series(out["card_text"])
    out["all_compact"] = _compact_series(out["type_text"] + " " + out["outcome_text"] + " " + out["qual_text"] + " " + out["card_text"])

    out["x_pct"] = _pct_coordinate(out, "x", "x_120", 1.2)
    out["y_pct"] = _pct_coordinate(out, "y", "y_80", 0.8)
    out["end_x_pct"] = _pct_coordinate(out, "end_x", "end_x_120", 1.2)
    out["end_y_pct"] = _pct_coordinate(out, "end_y", "end_y_80", 0.8)

    out["is_success_strict"] = (
        _bool_series(out, "is_success")
        | _bool_series(out, "successful")
        | out["outcome_compact"].isin(SUCCESS_OUTCOMES)
    )
    return out


def _event_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    type_c = df["type_compact"]
    outcome_c = df["outcome_compact"]
    qual_c = df["qual_compact"]
    card_c = df["card_compact"]
    all_c = df["all_compact"]

    own_goal = all_c.str.contains("owngoal", na=False)
    goal = (_bool_series(df, "is_goal") | type_c.eq("goal")) & ~own_goal

    exact_shot_type = type_c.isin(SHOT_TYPES)
    shot_flag = _bool_series(df, "is_shot") | _bool_series(df, "is_shot_event")
    shot = goal | exact_shot_type | (shot_flag & exact_shot_type)

    blocked_shot = type_c.eq("blockedshot") | (shot & type_c.eq("shot") & all_c.str.contains("blockedshot", na=False))
    missed_shot = type_c.eq("missedshots")
    post_shot = type_c.eq("shotonpost")
    saved_shot = type_c.isin({"savedshot", "attemptsaved"})
    saved_support = type_c.eq("shot") & outcome_c.str.contains("saved|ontarget|attemptsaved", regex=True, na=False)
    shot_on_target = shot & (goal | saved_shot | saved_support) & ~missed_shot & ~post_shot & ~blocked_shot

    passes = type_c.eq("pass")
    crosses = type_c.eq("cross") | (passes & qual_c.str.contains("cross", na=False))
    take_ons = _bool_series(df, "is_take_on") | type_c.isin({"takeon", "dribble"})
    inferred_carries = _bool_series(df, "is_inferred_carry") | type_c.eq("inferredcarry")
    provider_carries = _bool_series(df, "is_carry") & type_c.str.contains("carry|ballcarry|run", regex=True, na=False) & ~take_ons
    carries = inferred_carries | provider_carries
    pass_or_carry = passes | crosses | carries

    defensive_actions = (
        _bool_series(df, "is_defensive_action")
        | type_c.isin({"tackle", "challenge", "clearance", "block", "ballrecovery", "recovery", "interception"})
        | type_c.str.contains("aerial|duel", regex=True, na=False)
    )
    fouls = type_c.isin({"foul", "foulcommitted"})
    interceptions = type_c.eq("interception")

    void_card = all_c.str.contains("voidyellowcard|voidredcard|voidsecondyellow|voidedcard", regex=True, na=False)
    valid_card_type = card_c.isin(VALID_CARD_VALUES) | type_c.isin(VALID_CARD_VALUES)
    card_event = (type_c.eq("card") | valid_card_type) & ~void_card
    second_yellow = (card_c.isin(SECOND_YELLOW_VALUES) | type_c.isin(SECOND_YELLOW_VALUES)) & card_event
    yellow = (card_c.isin(YELLOW_VALUES) | type_c.isin(YELLOW_VALUES)) & card_event
    straight_red = (card_c.isin(RED_VALUES) | type_c.isin(RED_VALUES)) & card_event
    red = straight_red | second_yellow

    corner_taken_evidence = (
        type_c.isin({"cornertaken", "corner"})
        | qual_c.str.contains("cornertaken|cornerkick", regex=True, na=False)
    )
    corner_awarded = type_c.eq("cornerawarded")
    corner_taken = corner_taken_evidence
    from_corner = qual_c.str.contains("fromcorner", na=False)

    free_kick_taken = (
        type_c.isin({"freekicktaken", "directfreekick", "indirectfreekicktaken", "freekick"})
        | qual_c.str.contains("freekicktaken|directfreekick|indirectfreekicktaken", regex=True, na=False)
    )
    from_free_kick = qual_c.str.contains("fromfreekick", na=False)

    throw_in_taken = (
        type_c.isin({"throwin", "throwintaken", "throwinsetpiece"})
        | qual_c.str.contains("throwintaken|throwinsetpiece", regex=True, na=False)
    )
    from_throw_in = qual_c.str.contains("fromthrowin", na=False)

    penalty_taken = (
        type_c.isin({"penalty", "penaltymissed", "penaltyscored", "penaltysaved"})
        | (shot & qual_c.str.contains("penalty", na=False))
        | qual_c.str.contains("penaltytaken|penaltyscored|penaltymissed|penaltysaved", regex=True, na=False)
    )
    penalty_awarded = type_c.eq("penaltyawarded") | qual_c.str.contains("penaltyawarded", na=False)

    set_piece_action = (
        corner_awarded
        | corner_taken
        | from_corner
        | free_kick_taken
        | from_free_kick
        | throw_in_taken
        | from_throw_in
        | penalty_taken
        | penalty_awarded
        | (shot & qual_c.str.contains("corner|freekick|penalty", regex=True, na=False))
    )

    existing_final_third = _bool_series(df, "final_third_entry")
    calculated_final_third = pass_or_carry & df["x_pct"].lt(FINAL_THIRD_X) & df["end_x_pct"].ge(FINAL_THIRD_X)
    final_third_entry = existing_final_third | calculated_final_third

    start_in_box = df["x_pct"].ge(BOX_X) & df["y_pct"].between(BOX_Y_MIN, BOX_Y_MAX, inclusive="both")
    end_in_box = df["end_x_pct"].ge(BOX_X) & df["end_y_pct"].between(BOX_Y_MIN, BOX_Y_MAX, inclusive="both")
    calculated_box_entry = pass_or_carry & ~start_in_box & end_in_box
    box_entry = _bool_series(df, "box_entry") | calculated_box_entry
    carry_final_third_entry = carries & df["x_pct"].lt(FINAL_THIRD_X) & df["end_x_pct"].ge(FINAL_THIRD_X)
    carry_box_entry = carries & ~start_in_box & end_in_box
    progressive_carry = carries & (df["end_x_pct"] - df["x_pct"]).ge(10.0)

    attacking_third_touch = _bool_series(df, "attacking_third_touch") | (_bool_series(df, "is_touch") & df["x_pct"].ge(ATTACKING_THIRD_X))
    high_regain = _bool_series(df, "high_regain") | (defensive_actions & df["x_pct"].ge(60.0))

    return {
        "goal": goal,
        "shot": shot,
        "shot_on_target": shot_on_target,
        "pass": passes,
        "cross": crosses,
        "take_on": take_ons,
        "successful_take_on": take_ons & df["is_success_strict"],
        "carry": carries,
        "inferred_carry": inferred_carries,
        "progressive_carry": progressive_carry,
        "carry_final_third_entry": carry_final_third_entry,
        "carry_box_entry": carry_box_entry,
        "defensive_action": defensive_actions,
        "foul": fouls,
        "interception": interceptions,
        "card": card_event,
        "yellow_card": yellow,
        "red_card": red,
        "second_yellow": second_yellow,
        "corner_awarded": corner_awarded,
        "corner_taken": corner_taken,
        "free_kick_taken": free_kick_taken,
        "throw_in_taken": throw_in_taken,
        "penalty_taken": penalty_taken,
        "penalty_awarded": penalty_awarded,
        "set_piece_action": set_piece_action,
        "final_third_entry": final_third_entry,
        "box_entry": box_entry,
        "open_play_box_entry": box_entry & ~set_piece_action,
        "set_piece_box_entry": box_entry & set_piece_action,
        "attacking_third_touch": attacking_third_touch,
        "high_regain": high_regain,
    }


def _provider_xg(df: pd.DataFrame, shot_mask: pd.Series) -> tuple[float | None, str | None, int, int]:
    if not shot_mask.any():
        return 0.0, "none", 0, 0

    shot_count = int(shot_mask.sum())
    for col in PROVIDER_XG_COLUMNS:
        if col not in df.columns:
            continue
        values = pd.to_numeric(df.loc[shot_mask, col], errors="coerce")
        valid = values.dropna()
        if valid.empty:
            continue
        return float(valid.clip(lower=0.0, upper=1.0).sum()), col, int(valid.count()), shot_count
    return None, None, 0, shot_count


def _fallback_shot_xg(row: pd.Series) -> float:
    type_c = str(row.get("type_compact", ""))
    qual_c = str(row.get("qual_compact", ""))
    all_c = str(row.get("all_compact", ""))

    if "penalty" in type_c or "penalty" in qual_c:
        return 0.76

    x = pd.to_numeric(pd.Series([row.get("x_pct")]), errors="coerce").iloc[0]
    y = pd.to_numeric(pd.Series([row.get("y_pct")]), errors="coerce").iloc[0]
    if pd.isna(x):
        x = 0.0
    if pd.isna(y):
        y = 50.0

    distance_to_goal = max(0.0, 100.0 - float(x))
    centrality = abs(float(y) - 50.0)

    if "directfreekick" in type_c or "directfreekick" in qual_c or "freekick" in qual_c:
        base = 0.055
    elif float(x) >= BOX_X and BOX_Y_MIN <= float(y) <= BOX_Y_MAX:
        base = 0.11
        if distance_to_goal <= 7.0 and centrality <= 12.0:
            base = 0.28
        elif distance_to_goal <= 13.0 and centrality <= 20.0:
            base = 0.19
        elif centrality >= 26.0:
            base = 0.065
    elif float(x) >= 76.0:
        base = 0.04
    else:
        base = 0.02

    if "bigchance" in all_c:
        base = max(base, 0.26)
    if "header" in all_c or "headed" in all_c:
        base *= 0.78
    if "blocked" in all_c:
        base *= 0.85

    return float(max(0.005, min(base, 0.76)))


def _xg_total(df: pd.DataFrame, shot_mask: pd.Series) -> tuple[float, str, dict[str, Any]]:
    provider_total, provider_col, provider_count, shot_count = _provider_xg(df, shot_mask)
    if provider_col is not None:
        audit = {
            "source": "provider",
            "provider_column": provider_col,
            "provider_values_used": provider_count,
            "shot_rows": shot_count,
            "note": "xG was summed from the first available provider xG column with valid numeric shot values. Missing provider values were not replaced with fallback estimates.",
        }
        return round(float(provider_total or 0.0), 2), "provider", audit

    if not shot_mask.any():
        audit = {
            "source": "none",
            "provider_column": None,
            "provider_values_used": 0,
            "shot_rows": 0,
            "note": "No shot rows were available, so xG is zero.",
        }
        return 0.0, "none", audit

    shot_rows = df.loc[shot_mask].copy()
    values = [_fallback_shot_xg(row) for _, row in shot_rows.iterrows()]
    audit = {
        "source": "fallback_estimate",
        "provider_column": None,
        "provider_values_used": 0,
        "shot_rows": int(len(shot_rows)),
        "note": "No provider xG column had valid shot values. A conservative coordinate based fallback was used, so this should not be read as Opta, FotMob or StatsBomb xG.",
    }
    return round(float(sum(values)), 2), "fallback_estimate", audit



def _primary_else_fallback_count(primary: pd.Series, fallback: pd.Series) -> int:
    primary_count = int(primary.fillna(False).sum())
    if primary_count > 0:
        return primary_count
    return int(fallback.fillna(False).sum())


def build_team_summary(events: pd.DataFrame) -> dict[str, Any]:
    summary = _empty_team_summary()
    if events is None or events.empty:
        return summary

    df = _prepare_stat_frame(events)
    masks = _event_masks(df)

    passes = int(masks["pass"].sum())
    successful_passes = int((masks["pass"] & df["is_success_strict"]).sum())
    shots = int(masks["shot"].sum())
    shots_on_target = int(masks["shot_on_target"].sum())
    xg, xg_source, xg_audit = _xg_total(df, masks["shot"])
    goals = int(masks["goal"].sum())
    crosses = int(masks["cross"].sum())
    take_ons = int(masks["take_on"].sum())
    successful_take_ons = int(masks["successful_take_on"].sum())
    carries = int(masks["carry"].sum())
    inferred_carries = int(masks["inferred_carry"].sum())
    progressive_carries = int(masks["progressive_carry"].sum())
    carry_final_third_entries = int(masks["carry_final_third_entry"].sum())
    carry_box_entries = int(masks["carry_box_entry"].sum())
    final_third_entries = int(masks["final_third_entry"].sum())
    box_entries = int(masks["box_entry"].sum())
    set_piece_box_entries = int(masks["set_piece_box_entry"].sum())
    open_play_box_entries = int(masks["open_play_box_entry"].sum())
    defensive_actions = int(masks["defensive_action"].sum())
    attacking_touches = int(masks["attacking_third_touch"].sum())
    high_regains = int(masks["high_regain"].sum())
    avg_x = float(df["x_pct"].dropna().mean()) if df["x_pct"].notna().any() else 0.0

    set_piece_actions = int(masks["set_piece_action"].sum())
    set_piece_shots = int((masks["set_piece_action"] & masks["shot"]).sum())
    set_piece_goals = int((masks["set_piece_action"] & masks["goal"]).sum())

    transition_events = int((masks["high_regain"] | masks["final_third_entry"] | masks["box_entry"] | masks["shot"]).sum())
    transition_score = (
        float(masks["high_regain"].sum()) * 1.0
        + float(masks["final_third_entry"].sum()) * 0.8
        + float(masks["box_entry"].sum()) * 1.6
        + float(masks["shot"].sum()) * 2.0
        + float(masks["cross"].sum()) * 0.5
    )

    summary.update(
        {
            "passes": passes,
            "pass_completion_pct": round((successful_passes / passes) * 100.0, 1) if passes else 0.0,
            "shots": shots,
            "shots_on_target": shots_on_target,
            "shot_accuracy_pct": round((shots_on_target / shots) * 100.0, 1) if shots else 0.0,
            "xg": xg,
            "xg_source": xg_source,
            "goals": goals,
            "crosses": crosses,
            "take_ons": take_ons,
            "successful_take_ons": successful_take_ons,
            "take_on_success_pct": round((successful_take_ons / take_ons) * 100.0, 1) if take_ons else 0.0,
            "carries": carries,
            "inferred_carries": inferred_carries,
            "progressive_carries": progressive_carries,
            "carry_final_third_entries": carry_final_third_entries,
            "carry_box_entries": carry_box_entries,
            "final_third_entries": final_third_entries,
            "penalty_area_entries": box_entries,
            "box_entries": box_entries,
            "open_play_box_entries": open_play_box_entries,
            "set_piece_box_entries": set_piece_box_entries,
            "average_field_position": _json_number(avg_x, 2),
            "defensive_actions": defensive_actions,
            "touches_in_attacking_third": attacking_touches,
            "high_regains": high_regains,
            "transition_threat_events": transition_events,
            "transition_threat_proxy": round(transition_score, 2),
            "set_piece_actions": set_piece_actions,
            "set_piece_shots": set_piece_shots,
            "set_piece_goals": set_piece_goals,
            "corners": _primary_else_fallback_count(masks["corner_awarded"], masks["corner_taken"]),
            "free_kicks": int(masks["free_kick_taken"].sum()),
            "throw_ins": int(masks["throw_in_taken"].sum()),
            "penalties": _primary_else_fallback_count(masks["penalty_taken"], masks["penalty_awarded"]),
            "cards": int(masks["card"].sum()),
            "yellow_cards": int(masks["yellow_card"].sum()),
            "red_cards": int(masks["red_card"].sum()),
            "second_yellow_red_cards": int(masks["second_yellow"].sum()),
            "fouls": int(masks["foul"].sum()),
            "interceptions": int(masks["interception"].sum()),
            "stat_audit": {
                "status": "ok",
                "rows_checked": int(len(df)),
                "discipline_rules": "Cards are counted only from Card event rows or valid card_type values. Empty, null, false, zero, nan and voided cards are excluded.",
                "shot_rules": "Shots use exact WhoScored shot event evidence only: Goal, MissedShots, SavedShot, ShotOnPost, BlockedShot or AttemptSaved. Shots on target are only goals and saved shots, with missed, post and blocked shots excluded.",
                "restart_rules": "Corners use CornerAwarded first and fall back to clear corner taken rows only when no awarded rows exist. Free kicks, throw ins and penalties use clear restart or taken evidence. FromCorner, FromFreekick and FromThrowIn contribute to set piece actions, not strict restart counts.",
                "carry_rules": "Take ons are provider logged TakeOn or Dribble events. Carries are provider carry/run events plus inferred same player ball movement rows created from consecutive same player actions within ten seconds and at least three pitch units.",
                "xg": xg_audit,
            },
        }
    )

    return _json_safe(summary)
