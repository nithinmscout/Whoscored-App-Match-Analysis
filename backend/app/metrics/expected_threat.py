from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import math

import numpy as np
import pandas as pd

from app.metrics.expected_goals import _sorted_actions
from app.services.event_data_service import PITCH_LENGTH, PITCH_WIDTH

GRID_X = 16
GRID_Y = 12
ZONE_COUNT = GRID_X * GRID_Y
IDENTITY_COLS = [
    "nation",
    "team_folder",
    "season",
    "team_id",
    "team",
    "player_id",
    "player",
]


@dataclass(frozen=True)
class XTModel:
    xt: np.ndarray
    move_probability: np.ndarray
    shot_probability: np.ndarray
    goal_probability: np.ndarray
    transition_probability: np.ndarray
    include_set_pieces: bool = False


def _safe_number(value: object) -> float | None:
    try:
        numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    except Exception:
        return None
    if pd.isna(numeric):
        return None
    try:
        result = float(numeric)
    except Exception:
        return None
    if not math.isfinite(result):
        return None
    return result


def _clamp_coordinate(value: float, upper: float) -> float:
    return float(min(max(value, 0.0), np.nextafter(upper, 0.0)))


def zone_index(x: object, y: object) -> int | None:
    x_value = _safe_number(x)
    y_value = _safe_number(y)
    if x_value is None or y_value is None:
        return None
    x_safe = _clamp_coordinate(x_value, PITCH_LENGTH)
    y_safe = _clamp_coordinate(y_value, PITCH_WIDTH)
    x_bin = int(np.floor((x_safe / PITCH_LENGTH) * GRID_X))
    y_bin = int(np.floor((y_safe / PITCH_WIDTH) * GRID_Y))
    x_bin = min(max(x_bin, 0), GRID_X - 1)
    y_bin = min(max(y_bin, 0), GRID_Y - 1)
    return (y_bin * GRID_X) + x_bin


def _zone_edges(axis_max: float, bins: int) -> list[float]:
    return np.linspace(0.0, axis_max, bins + 1).round(4).tolist()


def _zone_centres(axis_max: float, bins: int) -> list[float]:
    edges = np.linspace(0.0, axis_max, bins + 1)
    return ((edges[:-1] + edges[1:]) / 2.0).round(4).tolist()


def _prepare_xt_actions(events_df: pd.DataFrame) -> pd.DataFrame:
    df = _sorted_actions(events_df)

    if df.empty:
        return df

    for col in ["x_120", "y_80", "end_x_120", "end_y_80"]:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")

    start_zones = [zone_index(x, y) for x, y in zip(df["x_120"], df["y_80"])]
    end_zones = [zone_index(x, y) for x, y in zip(df["end_x_120"], df["end_y_80"])]

    df["zone_start"] = pd.Series(
        [zone if zone is not None else -1 for zone in start_zones],
        index=df.index,
        dtype="int64",
    )
    df["zone_end"] = pd.Series(
        [zone if zone is not None else -1 for zone in end_zones],
        index=df.index,
        dtype="int64",
    )

    df["action_type"] = np.select(
        [
            df["is_carry"],
            df["is_cross"],
            df["is_pass_like"],
        ],
        [
            "carry",
            "cross",
            "pass",
        ],
        default="other",
    )
    return df


def build_xt_model(events_df: pd.DataFrame, include_set_pieces: bool = False) -> XTModel:
    df = _prepare_xt_actions(events_df)
    if df.empty:
        raise ValueError("No event rows were available for xT modelling.")

    base_mask = df["x_120"].notna() & df["y_80"].notna() & df["zone_start"].ge(0)
    if not include_set_pieces:
        base_mask &= ~df["is_set_piece_action"].astype(bool)
    df = df.loc[base_mask].copy()

    if df.empty:
        raise ValueError("No valid open play event rows were available for xT modelling.")

    is_move = (
        (df["is_pass_like"].astype(bool) | df["is_carry"].astype(bool))
        & df["successful"].astype(bool)
        & df["end_x_120"].notna()
        & df["end_y_80"].notna()
        & df["zone_start"].ge(0)
        & df["zone_end"].ge(0)
    )
    is_shot = df["is_shot_event"].astype(bool)
    is_goal = df["is_goal"].astype(bool)

    zone_action_counts = np.zeros(ZONE_COUNT, dtype=float)
    move_counts = np.zeros(ZONE_COUNT, dtype=float)
    shot_counts = np.zeros(ZONE_COUNT, dtype=float)
    goal_counts = np.zeros(ZONE_COUNT, dtype=float)
    transition_counts = np.zeros((ZONE_COUNT, ZONE_COUNT), dtype=float)

    for start_zone, count in df.groupby("zone_start").size().items():
        zone_action_counts[int(start_zone)] = float(count)

    df_moves = df.loc[is_move].copy()
    df_shots = df.loc[is_shot].copy()

    for start_zone, count in df_moves.groupby("zone_start").size().items():
        move_counts[int(start_zone)] = float(count)

    for start_zone, count in df_shots.groupby("zone_start").size().items():
        shot_counts[int(start_zone)] = float(count)

    for start_zone, count in df.loc[is_goal].groupby("zone_start").size().items():
        goal_counts[int(start_zone)] = float(count)

    if not df_moves.empty:
        for (start_zone, end_zone), count in df_moves.groupby(["zone_start", "zone_end"]).size().items():
            if int(end_zone) >= 0:
                transition_counts[int(start_zone), int(end_zone)] = float(count)

    move_probability = np.divide(
        move_counts,
        zone_action_counts,
        out=np.zeros_like(move_counts),
        where=zone_action_counts > 0,
    )
    shot_probability = np.divide(
        shot_counts,
        zone_action_counts,
        out=np.zeros_like(shot_counts),
        where=zone_action_counts > 0,
    )
    goal_probability = np.divide(
        goal_counts,
        shot_counts,
        out=np.zeros_like(goal_counts),
        where=shot_counts > 0,
    )
    transition_probability = np.divide(
        transition_counts,
        move_counts[:, None],
        out=np.zeros_like(transition_counts),
        where=move_counts[:, None] > 0,
    )

    transition_matrix = np.diag(move_probability) @ transition_probability
    immediate_payoff = shot_probability * goal_probability
    system_matrix = np.eye(ZONE_COUNT) - transition_matrix

    try:
        xt = np.linalg.solve(system_matrix, immediate_payoff)
    except np.linalg.LinAlgError:
        xt = np.linalg.lstsq(system_matrix, immediate_payoff, rcond=None)[0]

    xt = np.clip(xt, 0.0, 1.0)

    return XTModel(
        xt=xt,
        move_probability=move_probability,
        shot_probability=shot_probability,
        goal_probability=goal_probability,
        transition_probability=transition_probability,
        include_set_pieces=include_set_pieces,
    )


def value_actions(
    events_df: pd.DataFrame,
    model: XTModel,
    include_set_pieces: bool = True,
) -> pd.DataFrame:
    df = _prepare_xt_actions(events_df)
    if df.empty:
        return df

    mask = (
        (df["is_pass_like"].astype(bool) | df["is_carry"].astype(bool))
        & df["successful"].astype(bool)
        & df["end_x_120"].notna()
        & df["end_y_80"].notna()
        & df["zone_start"].ge(0)
        & df["zone_end"].ge(0)
    )
    if not include_set_pieces:
        mask &= ~df["is_set_piece_action"].astype(bool)

    actions = df.loc[mask].copy()
    if actions.empty:
        return actions

    actions["xt_start"] = actions["zone_start"].map(lambda z: float(model.xt[int(z)]))
    actions["xt_end"] = actions["zone_end"].map(lambda z: float(model.xt[int(z)]))
    actions["xt_added"] = actions["xt_end"] - actions["xt_start"]

    return actions


def aggregate_player_xt(events_df: pd.DataFrame) -> pd.DataFrame:
    actions = _prepare_xt_actions(events_df)
    if actions.empty:
        return pd.DataFrame(columns=IDENTITY_COLS + ["xt_raw"])

    model = build_xt_model(actions, include_set_pieces=False)
    valued_all = value_actions(actions, model=model, include_set_pieces=True)
    if valued_all.empty:
        return pd.DataFrame(columns=IDENTITY_COLS + ["xt_raw"])

    for col in IDENTITY_COLS:
        if col not in valued_all.columns:
            valued_all[col] = ""

    valued_all["open_play_xt_value"] = np.where(
        valued_all["is_set_piece_action"].astype(bool),
        0.0,
        valued_all["xt_added"],
    )
    valued_all["set_piece_xt_value"] = np.where(
        valued_all["is_set_piece_action"].astype(bool),
        valued_all["xt_added"],
        0.0,
    )
    valued_all["pass_xt_value"] = np.where(
        (~valued_all["is_set_piece_action"].astype(bool)) & valued_all["action_type"].eq("pass"),
        valued_all["xt_added"],
        0.0,
    )
    valued_all["cross_xt_value"] = np.where(
        (~valued_all["is_set_piece_action"].astype(bool)) & valued_all["action_type"].eq("cross"),
        valued_all["xt_added"],
        0.0,
    )
    valued_all["carry_xt_value"] = np.where(
        (~valued_all["is_set_piece_action"].astype(bool)) & valued_all["action_type"].eq("carry"),
        valued_all["xt_added"],
        0.0,
    )

    agg = (
        valued_all.groupby(IDENTITY_COLS, dropna=False)
        .agg(
            xt_raw=("open_play_xt_value", "sum"),
            open_play_xt_raw=("open_play_xt_value", "sum"),
            set_piece_xt_raw=("set_piece_xt_value", "sum"),
            xt_passes_raw=("pass_xt_value", "sum"),
            xt_crosses_raw=("cross_xt_value", "sum"),
            xt_carries_raw=("carry_xt_value", "sum"),
        )
        .reset_index()
    )

    agg["expected_threat_raw"] = agg["xt_raw"]
    return agg


def build_xt_surface_response(events_df: pd.DataFrame, fixture: dict[str, Any]) -> dict[str, Any]:
    model = build_xt_model(events_df, include_set_pieces=False)
    valued_actions = value_actions(events_df, model=model, include_set_pieces=False)
    xt_grid = model.xt.reshape(GRID_Y, GRID_X)

    payload_rows = []
    for row in valued_actions.itertuples(index=False):
        payload_rows.append(
            {
                "event_index": int(row.event_index),
                "team": str(getattr(row, "team", "")),
                "player": str(getattr(row, "player", "")),
                "action_type": str(getattr(row, "action_type", "")),
                "start": [round(float(row.x_120), 3), round(float(row.y_80), 3), 0.0],
                "end": [round(float(row.end_x_120), 3), round(float(row.end_y_80), 3), 0.0],
                "xt_start": round(float(row.xt_start), 6),
                "xt_end": round(float(row.xt_end), 6),
                "xt_added": round(float(row.xt_added), 6),
                "zone_start": int(row.zone_start),
                "zone_end": int(row.zone_end),
            }
        )

    payload_rows.sort(key=lambda item: item["xt_added"], reverse=True)
    pass_like_rows = [row for row in payload_rows if row["action_type"] in {"pass", "cross"}]

    return {
        "match_id": int(fixture["match_id"]),
        "home_team": str(fixture["home_team"]),
        "away_team": str(fixture["away_team"]),
        "grid_x": GRID_X,
        "grid_y": GRID_Y,
        "x_edges": _zone_edges(PITCH_LENGTH, GRID_X),
        "y_edges": _zone_edges(PITCH_WIDTH, GRID_Y),
        "x_centres": _zone_centres(PITCH_LENGTH, GRID_X),
        "y_centres": _zone_centres(PITCH_WIDTH, GRID_Y),
        "xt_grid": xt_grid.round(6).tolist(),
        "actions": payload_rows,
        "passes": pass_like_rows,
        "model": {
            "move_probability": model.move_probability.round(6).tolist(),
            "shot_probability": model.shot_probability.round(6).tolist(),
            "goal_probability": model.goal_probability.round(6).tolist(),
            "include_set_pieces": model.include_set_pieces,
        },
    }