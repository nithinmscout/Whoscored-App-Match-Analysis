from __future__ import annotations

import importlib.metadata
import math
import os
import platform
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from fastapi import APIRouter, Query

from app.core.config import DATA_DIR, DATA_ROOT, EXPORTS_DIR, WHO_CACHE_DIR, guess_browser_path
from app.services import match_analysis_service as match_analysis
from app.services.event_data_service import load_schedule_frame
from app.services.processed_event_store import processed_paths, processed_store_status

router = APIRouter()


PACKAGE_NAMES = [
    "fastapi",
    "uvicorn",
    "pandas",
    "numpy",
    "soccerdata",
    "seleniumbase",
    "sklearn",
    "pyarrow",
]


NUMERIC_DEBUG_COLUMNS = [
    "match_id",
    "period",
    "minute",
    "second",
    "expanded_minute",
    "x",
    "y",
    "end_x",
    "end_y",
    "x_120",
    "y_80",
    "end_x_120",
    "end_y_80",
    "goal_mouth_x",
    "goal_mouth_y",
    "goal_mouth_z",
    "blocked_x",
    "blocked_y",
    "team_id",
    "player_id",
    "shirt_no",
    "xt_added",
    "xg",
]


def _package_version(name: str) -> str:
    try:
        lookup_name = "scikit-learn" if name == "sklearn" else name
        return importlib.metadata.version(lookup_name)
    except Exception:
        return "not installed"


def _count_csv_files(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return sum(1 for item in path.rglob("*.csv") if item.is_file())
    except Exception:
        return 0


def _safe_list_dirs(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return sorted([item.name for item in path.iterdir() if item.is_dir()])
    except Exception:
        return []


def _is_missing_like(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "nan", "none", "<na>", "nat", "null"}
    if isinstance(value, (list, tuple, set, dict)):
        return False
    try:
        missing = pd.isna(value)
    except Exception:
        return False
    if isinstance(missing, bool):
        return missing
    return False


def _json_safe_value(value: object) -> Any:
    if _is_missing_like(value):
        return None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item) for item in value]
    try:
        import numpy as np

        if isinstance(value, np.generic):
            value = value.item()
    except Exception:
        pass
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    try:
        return str(value)
    except Exception:
        return None


def _safe_records(df: pd.DataFrame, limit: int = 8) -> list[dict[str, Any]]:
    if df.empty:
        return []
    preview = df.head(limit).copy()
    rows: list[dict[str, Any]] = []
    for row in preview.to_dict(orient="records"):
        rows.append({str(key): _json_safe_value(value) for key, value in row.items()})
    return rows


def _column_audit(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for col in df.columns:
        series = df[col]
        missing_count = int(series.map(_is_missing_like).sum())
        blank_string_count = int(series.map(lambda value: isinstance(value, str) and value.strip() == "").sum())
        na_type_count = int(series.map(lambda value: type(value).__name__ == "NAType").sum())
        invalid_numeric_count = ""
        invalid_numeric_examples = ""

        should_probe_numeric = (
            col in NUMERIC_DEBUG_COLUMNS
            or col.endswith("_x")
            or col.endswith("_y")
            or col.endswith("_id")
            or col.endswith("_minute")
            or col.endswith("_seconds")
            or col.endswith("_value")
            or col.endswith("_raw")
        )

        if should_probe_numeric:
            numeric = pd.to_numeric(series, errors="coerce")
            invalid_mask = numeric.isna() & ~series.map(_is_missing_like)
            invalid_numeric_count = int(invalid_mask.sum())
            examples = []
            if invalid_mask.any():
                for value in series.loc[invalid_mask].head(5).tolist():
                    examples.append(str(value))
            invalid_numeric_examples = " | ".join(examples)

        rows.append(
            {
                "column": str(col),
                "dtype": str(series.dtype),
                "missing_like": missing_count,
                "blank_strings": blank_string_count,
                "pd_na_type": na_type_count,
                "invalid_numeric": invalid_numeric_count,
                "invalid_examples": invalid_numeric_examples,
            }
        )
    return rows


def _bad_numeric_samples(df: pd.DataFrame, limit: int = 40) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for col in df.columns:
        if col not in NUMERIC_DEBUG_COLUMNS and not (
            col.endswith("_x")
            or col.endswith("_y")
            or col.endswith("_id")
            or col.endswith("_minute")
            or col.endswith("_seconds")
            or col.endswith("_value")
            or col.endswith("_raw")
        ):
            continue

        series = df[col]
        numeric = pd.to_numeric(series, errors="coerce")
        bad_mask = numeric.isna() & ~series.map(_is_missing_like)
        if not bad_mask.any():
            continue

        sample_df = df.loc[bad_mask].head(5)
        for index, row in sample_df.iterrows():
            rows.append(
                {
                    "column": str(col),
                    "row_index": int(index) if isinstance(index, int) else str(index),
                    "bad_value": _json_safe_value(row.get(col)),
                    "team": _json_safe_value(row.get("team")),
                    "player": _json_safe_value(row.get("player")),
                    "type": _json_safe_value(row.get("type")),
                    "period": _json_safe_value(row.get("period")),
                    "minute": _json_safe_value(row.get("minute")),
                    "event_index": _json_safe_value(row.get("event_index")),
                }
            )
            if len(rows) >= limit:
                return rows
    return rows


def _frame_audit(df: pd.DataFrame) -> dict[str, Any]:
    return {
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "columns": [str(col) for col in df.columns],
        "column_audit": _column_audit(df),
        "bad_numeric_samples": _bad_numeric_samples(df),
        "sample_rows": _safe_records(df),
    }


def _result_overview(result: object) -> dict[str, Any]:
    if isinstance(result, pd.DataFrame):
        return {
            "result_type": "DataFrame",
            "row_count": int(len(result)),
            "column_count": int(len(result.columns)),
            "columns": [str(col) for col in result.columns[:30]],
        }
    if isinstance(result, tuple):
        return {
            "result_type": "tuple",
            "items": len(result),
            "item_types": [type(item).__name__ for item in result],
        }
    if isinstance(result, list):
        return {
            "result_type": "list",
            "count": int(len(result)),
            "first_item_type": type(result[0]).__name__ if result else "",
        }
    if isinstance(result, dict):
        return {
            "result_type": "dict",
            "keys": [str(key) for key in list(result.keys())[:40]],
        }
    return {"result_type": type(result).__name__}


def _stage(
    stages: list[dict[str, Any]],
    name: str,
    callback: Callable[[], Any],
    *,
    stop_on_error: bool = False,
) -> Any:
    started = time.perf_counter()
    try:
        result = callback()
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        row: dict[str, Any] = {
            "stage": name,
            "ok": True,
            "duration_ms": duration_ms,
            "message": "ok",
        }
        row.update(_result_overview(result))
        stages.append(row)
        return result
    except Exception as exc:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        stages.append(
            {
                "stage": name,
                "ok": False,
                "duration_ms": duration_ms,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        if stop_on_error:
            raise
        return None


def _first_failed_stage(stages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for stage in stages:
        if not bool(stage.get("ok")):
            return stage
    return None


@router.get("/status")
def debug_status() -> dict[str, str]:
    return {"page": "Debug", "message": "Debug route is ready"}


@router.get("/health")
def debug_health() -> dict[str, Any]:
    schedule_root = DATA_DIR / "Schedule"
    events_root = DATA_DIR / "Event Data"

    return {
        "ok": True,
        "python": sys.version,
        "platform": platform.platform(),
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "data_root": str(DATA_ROOT),
        "data_dir": str(DATA_DIR),
        "exports_dir": str(EXPORTS_DIR),
        "who_cache_dir": str(WHO_CACHE_DIR),
        "browser_path": guess_browser_path(),
        "schedule_root_exists": schedule_root.exists(),
        "events_root_exists": events_root.exists(),
        "schedule_csv_count": _count_csv_files(schedule_root),
        "event_csv_count": _count_csv_files(events_root),
        "schedule_folders": _safe_list_dirs(schedule_root),
        "event_nations": _safe_list_dirs(events_root),
    }


@router.get("/environment")
def debug_environment() -> dict[str, Any]:
    return {
        "packages": {name: _package_version(name) for name in PACKAGE_NAMES},
        "env": {
            "WS_DATA_ROOT": os.environ.get("WS_DATA_ROOT", ""),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
            "PATH_HEAD": os.environ.get("PATH", "").split(os.pathsep)[:8],
        },
    }


@router.get("/match-analysis")
def debug_match_analysis(
    nation: str = Query(...),
    tier: str = Query(...),
    season: str = Query(...),
    match_id: int = Query(...),
    game_state: str = Query("all"),
    perspective: str = Query("home"),
) -> dict[str, Any]:
    stages: list[dict[str, Any]] = []
    started = time.perf_counter()

    paths = processed_paths(DATA_ROOT, nation=nation, tier=tier, season=season)
    path_payload = {key: str(value) for key, value in paths.items()}
    path_payload.update({f"{key}_exists": value.exists() for key, value in paths.items()})

    schedule_df = _stage(
        stages,
        "load_schedule_frame",
        lambda: load_schedule_frame(DATA_ROOT, nation=nation, tier=tier, season=season),
    )

    fixtures = None
    fixture = None
    home_team = ""
    away_team = ""

    if isinstance(schedule_df, pd.DataFrame):
        fixtures = _stage(
            stages,
            "build_fixtures",
            lambda: match_analysis._build_fixtures(schedule_df, basedir=DATA_ROOT, nation=nation, tier=tier, season=season),
        )
        if isinstance(fixtures, list):
            def _resolve_fixture() -> dict[str, Any]:
                selected = next((item for item in fixtures if int(item["match_id"]) == int(match_id)), None)
                if selected is None:
                    raise ValueError(f"Match {match_id} was not found in the saved schedule.")
                return selected

            fixture = _stage(stages, "resolve_selected_fixture", _resolve_fixture)

    if isinstance(fixture, dict):
        home_team = str(fixture.get("home_team") or "")
        away_team = str(fixture.get("away_team") or "")

    processed_status_payload = _stage(
        stages,
        "processed_store_status",
        lambda: processed_store_status(DATA_ROOT, nation=nation, tier=tier, season=season),
    )

    events = None
    data_source = None
    if home_team and away_team:
        loaded = _stage(
            stages,
            "load_prepared_match_events",
            lambda: match_analysis._load_prepared_match_events(
                DATA_ROOT,
                nation,
                tier,
                season,
                int(match_id),
                home_team,
                away_team,
            ),
        )
        if isinstance(loaded, tuple) and len(loaded) >= 2 and isinstance(loaded[0], pd.DataFrame):
            events = loaded[0]
            data_source = loaded[1]

    frame_audits: dict[str, Any] = {}
    if isinstance(events, pd.DataFrame):
        frame_audits["loaded_prepared_events"] = _frame_audit(events)
        events = _stage(stages, "normalise_loaded_missing_scalars", lambda: match_analysis._normalise_missing_scalars(events))
        if isinstance(events, pd.DataFrame):
            frame_audits["after_initial_normalise"] = _frame_audit(events)

    match_setup = None
    if isinstance(events, pd.DataFrame) and isinstance(fixture, dict):
        match_setup = _stage(
            stages,
            "build_match_setup",
            lambda: match_analysis._build_match_setup(DATA_ROOT, nation, tier, season, fixture, events.copy()),
        )

    if isinstance(events, pd.DataFrame) and isinstance(match_setup, dict):
        enriched = _stage(
            stages,
            "enrich_match_setup_shirts",
            lambda: match_analysis._enrich_events_with_match_setup_shirts(events, match_setup),
        )
        if isinstance(enriched, pd.DataFrame):
            events = _stage(stages, "normalise_after_shirt_enrichment", lambda: match_analysis._normalise_missing_scalars(enriched))
            if isinstance(events, pd.DataFrame):
                frame_audits["after_shirt_enrichment"] = _frame_audit(events)

    active_filter = None
    if isinstance(events, pd.DataFrame) and home_team and away_team:
        filtered = _stage(
            stages,
            "apply_game_state_filter",
            lambda: match_analysis._apply_game_state_filter(events, game_state, perspective, home_team, away_team),
        )
        if isinstance(filtered, tuple) and len(filtered) >= 2 and isinstance(filtered[0], pd.DataFrame):
            events = filtered[0]
            active_filter = filtered[1]
            frame_audits["after_game_state_filter"] = _frame_audit(events)

    home_events = None
    away_events = None
    home_summary = None
    away_summary = None

    if isinstance(events, pd.DataFrame) and home_team and away_team:
        home_events = _stage(stages, "home_team_events", lambda: match_analysis._team_events(events, home_team))
        away_events = _stage(stages, "away_team_events", lambda: match_analysis._team_events(events, away_team))

    if isinstance(home_events, pd.DataFrame):
        home_summary = _stage(stages, "home_team_summary", lambda: match_analysis._team_summary(home_events))
    if isinstance(away_events, pd.DataFrame):
        away_summary = _stage(stages, "away_team_summary", lambda: match_analysis._team_summary(away_events))

    if (
        isinstance(events, pd.DataFrame)
        and isinstance(home_events, pd.DataFrame)
        and isinstance(away_events, pd.DataFrame)
        and isinstance(home_summary, dict)
        and isinstance(away_summary, dict)
        and isinstance(fixture, dict)
    ):
        xt_analysis = None
        defensive_analysis = None
        set_piece_analysis = None

        _stage(stages, "raw_event_rows_serialisation", lambda: match_analysis._raw_event_rows(events))
        _stage(stages, "team_radar", lambda: match_analysis._build_season_team_radar(DATA_ROOT, nation, tier, season, home_team, away_team, home_summary, away_summary))
        _stage(stages, "rolling_momentum", lambda: match_analysis._rolling_momentum(events, home_team=home_team, away_team=away_team))
        _stage(stages, "rolling_possession_timeline", lambda: match_analysis._rolling_possession_timeline(events, home_team=home_team, away_team=away_team))
        _stage(stages, "match_markers", lambda: match_analysis._match_markers(events))
        _stage(stages, "territory_grids", lambda: {"home": match_analysis._grid_map(home_events), "away": match_analysis._grid_map(away_events)})
        _stage(stages, "action_maps", lambda: {"home": match_analysis._action_points(home_events), "away": match_analysis._action_points(away_events)})
        _stage(stages, "shot_maps", lambda: {"home": match_analysis._shot_points(home_events), "away": match_analysis._shot_points(away_events)})
        _stage(stages, "goalmouth_maps", lambda: {"home": match_analysis._goalmouth_points(home_events), "away": match_analysis._goalmouth_points(away_events)})
        _stage(stages, "final_third_pass_maps", lambda: {"home": match_analysis._final_third_pass_map(home_events), "away": match_analysis._final_third_pass_map(away_events)})
        _stage(stages, "pass_networks", lambda: {"home": match_analysis._build_pass_network(home_events, home_team), "away": match_analysis._build_pass_network(away_events, away_team)})
        _stage(stages, "phase_summaries", lambda: {"home": match_analysis._phase_breakdown(home_team, home_events, home_summary), "away": match_analysis._phase_breakdown(away_team, away_events, away_summary)})
        _stage(
            stages,
            "style_tags",
            lambda: match_analysis._build_style_tags(
                basedir=DATA_ROOT,
                nation=nation,
                tier=tier,
                season=season,
                home_team=home_team,
                away_team=away_team,
                match_events=events,
            ),
        )
        _stage(stages, "attacking_direction", lambda: {"home": match_analysis._direction_arrows(home_events), "away": match_analysis._direction_arrows(away_events)})
        _stage(stages, "attacking_threat_lanes", lambda: {"home": match_analysis._attacking_threat_lanes(home_events), "away": match_analysis._attacking_threat_lanes(away_events)})
        _stage(stages, "attacking_threat_boxes", lambda: {"home": match_analysis._attacking_threat_boxes(home_events), "away": match_analysis._attacking_threat_boxes(away_events)})
        _stage(stages, "shot_sequences", lambda: match_analysis._shot_sequences(events))
        _stage(
            stages,
            "recent_patterns_from_processed",
            lambda: match_analysis._recent_patterns_from_processed(
                basedir=DATA_ROOT,
                nation=nation,
                tier=tier,
                season=season,
                selected_fixture=fixture,
                home_events=home_events,
                away_events=away_events,
            ),
        )
        _stage(
            stages,
            "momentum_analysis_from_processed",
            lambda: match_analysis._momentum_analysis_from_processed(
                basedir=DATA_ROOT,
                nation=nation,
                tier=tier,
                season=season,
                selected_fixture=fixture,
                prepared_match_events=events,
            ),
        )

        xt_analysis = _stage(stages, "xt_analysis", lambda: match_analysis._build_xt_analysis(events, home_team, away_team))
        defensive_analysis = _stage(
            stages,
            "defensive_analysis",
            lambda: {
                "home": match_analysis._build_defensive_analysis(home_team, away_team, home_events, away_events, events),
                "away": match_analysis._build_defensive_analysis(away_team, home_team, away_events, home_events, events),
            },
        )
        set_piece_analysis = _stage(
            stages,
            "set_piece_analysis",
            lambda: {
                "home": match_analysis._build_set_piece_analysis(home_team, away_team, events),
                "away": match_analysis._build_set_piece_analysis(away_team, home_team, events),
            },
        )
        _stage(stages, "transition_analysis", lambda: match_analysis._build_transition_analysis(events, home_team, away_team))
        _stage(stages, "possession_chains", lambda: match_analysis._build_possession_chains(events, home_team, away_team))

        if isinstance(xt_analysis, dict) and isinstance(defensive_analysis, dict) and isinstance(set_piece_analysis, dict):
            _stage(
                stages,
                "best_players_analysis",
                lambda: {
                    "home": match_analysis._best_players_for_team(
                        home_events,
                        xt_analysis.get("home", {}),
                        defensive_analysis.get("home", {}),
                        set_piece_analysis.get("home", {}),
                    ),
                    "away": match_analysis._best_players_for_team(
                        away_events,
                        xt_analysis.get("away", {}),
                        defensive_analysis.get("away", {}),
                        set_piece_analysis.get("away", {}),
                    ),
                },
            )

    _stage(
        stages,
        "full_get_match_analysis",
        lambda: match_analysis.get_match_analysis(
            basedir=DATA_ROOT,
            nation=nation,
            tier=tier,
            season=season,
            match_id=int(match_id),
            game_state=game_state,
            perspective=perspective,
        ),
    )

    first_failed = _first_failed_stage(stages)
    payload = {
        "ok": first_failed is None,
        "first_failed_stage": first_failed,
        "params": {
            "nation": nation,
            "tier": tier,
            "season": season,
            "match_id": int(match_id),
            "game_state": game_state,
            "perspective": perspective,
        },
        "teams": {
            "home": home_team,
            "away": away_team,
        },
        "fixture": _json_safe_value(fixture),
        "data_source": _json_safe_value(data_source),
        "processed_store_status": _json_safe_value(processed_status_payload),
        "processed_paths": _json_safe_value(path_payload),
        "active_filter": _json_safe_value(active_filter),
        "stages": _json_safe_value(stages),
        "frame_audits": _json_safe_value(frame_audits),
        "total_duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    return _json_safe_value(payload)
