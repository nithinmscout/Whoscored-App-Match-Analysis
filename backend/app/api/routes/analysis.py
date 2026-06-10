from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core.config import DATA_ROOT
from app.services.match_analysis_service import get_match_analysis

router = APIRouter()


MATCH_ANALYSIS_RENDER_PHASES = [
    "Preparing match workspace",
    "Loading saved schedule",
    "Resolving selected fixture",
    "Reading saved event files",
    "Normalising event data",
    "Building team summaries",
    "Calculating attacking and defensive views",
    "Building momentum and territory views",
    "Building set piece and transition views",
    "Preparing best players and raw events",
    "Rendering dashboard",
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


def _render_meta(
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
        "phases": [{"label": label} for label in MATCH_ANALYSIS_RENDER_PHASES],
        "data_source_counts": {
            "fixtures": _safe_count(response.get("fixtures")),
            "raw_events": _safe_count(response.get("event_count")),
            "analytic_events": _safe_count(response.get("analytic_event_count")),
            "available_columns": _safe_count(response.get("available_columns")),
            "data_source": response.get("data_source", ""),
        },
        "message": "Match analysis dashboard prepared from saved schedule and event files.",
    }


@router.get("/status")
def analysis_status() -> dict[str, str]:
    return {
        "page": "Match Analysis",
        "message": "Match analysis route is ready",
    }


@router.get("/match-analysis")
def analysis_match_analysis(
    nation: str = Query(...),
    tier: str = Query(...),
    season: str = Query(...),
    match_id: int | None = Query(None),
    game_state: str = Query("all"),
    perspective: str = Query("home"),
):
    started_at = _utc_now_iso()
    started_perf = time.perf_counter()

    try:
        result = get_match_analysis(
            basedir=DATA_ROOT,
            nation=nation,
            tier=tier,
            season=season,
            match_id=match_id,
            game_state=game_state,
            perspective=perspective,
        )
        completed_at = _utc_now_iso()
        duration_ms = (time.perf_counter() - started_perf) * 1000
        if isinstance(result, dict):
            existing_render_meta = result.get("render_meta")
            if not isinstance(existing_render_meta, dict):
                existing_render_meta = {}
            route_render_meta = _render_meta(
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                response=result,
            )
            result["render_meta"] = {**existing_render_meta, **route_render_meta}
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Match analysis failed: {type(exc).__name__}: {exc}",
        ) from exc