from __future__ import annotations

from typing import Any

import pandas as pd

from app.services.event_data_service import FINAL_THIRD_X, PITCH_LENGTH

PPDA_BUILD_ZONE_X = 72.0
PPDA_DEF_ACTION_ZONE_X = 48.0


def _team_metric_block(df: pd.DataFrame, team_name: str) -> pd.DataFrame:
    return df.loc[df["team"].astype(str).eq(team_name)].copy()


def _count_final_third_passes(team_df: pd.DataFrame) -> int:
    return int((team_df["is_pass_like"] & team_df["successful"] & team_df["x_120"].ge(FINAL_THIRD_X)).sum())


def _count_build_passes(team_df: pd.DataFrame) -> int:
    return int((team_df["is_pass_like"] & team_df["successful"] & team_df["x_120"].lt(PPDA_BUILD_ZONE_X)).sum())


def _count_pressing_actions(team_df: pd.DataFrame) -> int:
    return int((team_df["is_defensive_action"] & team_df["x_120"].ge(PPDA_DEF_ACTION_ZONE_X)).sum())


def compute_contextual_match_metrics(events_df: pd.DataFrame, fixture: dict[str, Any]) -> dict[str, Any]:
    home_team = str(fixture["home_team"])
    away_team = str(fixture["away_team"])

    home_df = _team_metric_block(events_df, home_team)
    away_df = _team_metric_block(events_df, away_team)

    home_final_third_passes = _count_final_third_passes(home_df)
    away_final_third_passes = _count_final_third_passes(away_df)
    total_final_third_passes = max(home_final_third_passes + away_final_third_passes, 1)

    home_ppda_denominator = _count_pressing_actions(home_df)
    away_ppda_denominator = _count_pressing_actions(away_df)
    home_ppda_numerator = _count_build_passes(away_df)
    away_ppda_numerator = _count_build_passes(home_df)

    home_ppda = None if home_ppda_denominator == 0 else round(home_ppda_numerator / home_ppda_denominator, 3)
    away_ppda = None if away_ppda_denominator == 0 else round(away_ppda_numerator / away_ppda_denominator, 3)

    def _summary(team_name: str, final_third_passes: int, opponent_build_passes: int, defensive_actions: int, ppda: float | None) -> dict[str, Any]:
        return {
            "team": team_name,
            "final_third_passes": final_third_passes,
            "field_tilt_pct": round((final_third_passes / total_final_third_passes) * 100.0, 2),
            "opponent_build_passes": opponent_build_passes,
            "defensive_actions": defensive_actions,
            "ppda": ppda,
        }

    return {
        "match_id": int(fixture["match_id"]),
        "home_team": home_team,
        "away_team": away_team,
        "pitch_length": PITCH_LENGTH,
        "metrics": {
            "home": _summary(home_team, home_final_third_passes, home_ppda_numerator, home_ppda_denominator, home_ppda),
            "away": _summary(away_team, away_final_third_passes, away_ppda_numerator, away_ppda_denominator, away_ppda),
        },
    }