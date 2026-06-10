from __future__ import annotations

import math
import time
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core.config import DATA_ROOT
from app.services.league_analysis_service import get_league_analysis
from app.services.viewer_service import (
    get_match_events_table,
    get_team_events_table,
    get_team_summary,
    list_saved_teams,
    rebuild_team_analysis_profile_store,
)

router = APIRouter()


TEAM_ANALYSIS_RENDER_PHASES = [
    "Preparing team profiling workspace",
    "Loading saved schedule",
    "Reading selected team files",
    "Resolving saved opponent context",
    "Building club profile overview",
    "Building attacking profile",
    "Building defensive profile",
    "Building transitions and set pieces",
    "Building player contribution table",
    "Building season comparison",
    "Rendering profiling dashboard",
]

LEAGUE_ANALYSIS_RENDER_PHASES = [
    "Preparing league analysis workspace",
    "Loading saved season event files",
    "Building team style profiles",
    "Scoring xG and xT context",
    "Running correlation analysis",
    "Running PCA and clustering",
    "Preparing league style dashboard",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_count(value: object) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    try:
        return int(value)  # type: ignore[arg-type]
    except Exception:
        return 0


def _json_safe(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, (str, int, bool)):
        return value

    try:
        import numpy as np

        if isinstance(value, np.generic):
            return _json_safe(value.item())
    except Exception:
        pass

    try:
        import pandas as pd

        missing = pd.isna(value)
        if isinstance(missing, bool) and missing:
            return None
    except Exception:
        pass

    if isinstance(value, float):
        return value if math.isfinite(value) else None

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]

    try:
        return str(value)
    except Exception:
        return None


def _team_render_meta(
    *,
    started_at: str,
    completed_at: str,
    duration_ms: float,
    response: dict[str, Any],
) -> dict[str, Any]:
    return {
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": round(duration_ms, 2),
        "phases": [{"label": label} for label in TEAM_ANALYSIS_RENDER_PHASES],
        "data_source_counts": {
            "rows": _safe_count(response.get("rows")),
            "matches": _safe_count(response.get("matches")),
            "columns": _safe_count(response.get("columns")),
            "event_types": _safe_count(response.get("type_counts")),
            "players": _safe_count(response.get("player_counts")),
            "top_players": _safe_count(response.get("top_players")),
        },
        "message": "Team profiling dashboard prepared from saved schedule and event files.",
    }


def _league_render_meta(
    *,
    started_at: str,
    completed_at: str,
    duration_ms: float,
    response: dict[str, Any],
) -> dict[str, Any]:
    overview = response.get("overview") if isinstance(response.get("overview"), dict) else {}
    correlations = response.get("correlations") if isinstance(response.get("correlations"), dict) else {}
    return {
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": round(duration_ms, 2),
        "phases": [{"label": label} for label in LEAGUE_ANALYSIS_RENDER_PHASES],
        "data_source_counts": {
            "teams": _safe_count(overview.get("teams_compared")),
            "event_rows": _safe_count(overview.get("event_rows")),
            "schedule_matches": _safe_count(overview.get("schedule_matches")),
            "correlation_pairs": _safe_count(correlations.get("matrix")),
        },
        "message": "League style analysis prepared from saved event files.",
    }


@router.get("/status")
def viewer_status() -> dict[str, str]:
    return {"page": "Viewer", "message": "Viewer route is ready"}


@router.get("/match-events")
def viewer_match_events(
    nation: str = Query(...),
    tier: str = Query(...),
    season: str = Query(...),
    match_id: int = Query(...),
    limit: int = Query(5000, ge=1, le=20000),
):
    try:
        result = get_match_events_table(
            basedir=DATA_ROOT,
            nation=nation,
            tier=tier,
            season=season,
            match_id=match_id,
            limit=limit,
        )
        return _json_safe(result)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Match viewer failed: {type(exc).__name__}: {exc}") from exc


@router.get("/teams")
def viewer_teams(
    nation: str = Query(...),
    tier: str = Query(...),
    season: str = Query(...),
):
    try:
        result = list_saved_teams(DATA_ROOT, nation=nation, tier=tier, season=season)
        return _json_safe(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Team list failed: {type(exc).__name__}: {exc}") from exc


@router.get("/league-analysis")
def viewer_league_analysis(
    nation: str = Query(...),
    tier: str = Query(...),
    season: str = Query(...),
    method: str = Query("pearson"),
    min_matches: int = Query(1, ge=1, le=60),
):
    started_at = _utc_now_iso()
    started_perf = time.perf_counter()

    try:
        result = get_league_analysis(
            basedir=DATA_ROOT,
            nation=nation,
            tier=tier,
            season=season,
            method=method,
            min_matches=min_matches,
        )
        completed_at = _utc_now_iso()
        duration_ms = (time.perf_counter() - started_perf) * 1000
        if isinstance(result, dict):
            existing_render_meta = result.get("render_meta")
            if not isinstance(existing_render_meta, dict):
                existing_render_meta = {}
            route_render_meta = _league_render_meta(
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                response=result,
            )
            result["render_meta"] = {**existing_render_meta, **route_render_meta}
        return _json_safe(result)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"League analysis failed: {type(exc).__name__}: {exc}") from exc


@router.post("/team-analysis-cache/rebuild")
def viewer_team_analysis_cache_rebuild(
    nation: str = Query(...),
    tier: str = Query(...),
    season: str = Query(...),
    force: bool = Query(True),
):
    try:
        return _json_safe(
            rebuild_team_analysis_profile_store(
                basedir=DATA_ROOT,
                nation=nation,
                tier=tier,
                season=season,
                force=force,
            )
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Team Analysis profile store rebuild failed: {type(exc).__name__}: {exc}") from exc


@router.get("/team-events")
def viewer_team_events(
    nation: str = Query(...),
    tier: str = Query(...),
    season: str = Query(...),
    team: str = Query(...),
    match_id: int | None = Query(None),
    event_type: str | None = Query(None),
    player: str | None = Query(None),
    limit: int = Query(5000, ge=1, le=20000),
):
    try:
        result = get_team_events_table(
            basedir=DATA_ROOT,
            nation=nation,
            tier=tier,
            season=season,
            team=team,
            match_id=match_id,
            event_type=event_type,
            player=player,
            limit=limit,
        )
        return _json_safe(result)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Team viewer failed: {type(exc).__name__}: {exc}") from exc


@router.get("/team-summary")
def viewer_team_summary(
    nation: str = Query(...),
    tier: str = Query(...),
    season: str = Query(...),
    team: str = Query(...),
):
    started_at = _utc_now_iso()
    started_perf = time.perf_counter()

    try:
        result = get_team_summary(
            basedir=DATA_ROOT,
            nation=nation,
            tier=tier,
            season=season,
            team=team,
        )
        completed_at = _utc_now_iso()
        duration_ms = (time.perf_counter() - started_perf) * 1000
        if isinstance(result, dict):
            existing_render_meta = result.get("render_meta")
            if not isinstance(existing_render_meta, dict):
                existing_render_meta = {}
            route_render_meta = _team_render_meta(
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                response=result,
            )
            result["render_meta"] = {**existing_render_meta, **route_render_meta}
        return _json_safe(result)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Team summary failed: {type(exc).__name__}: {exc}") from exc