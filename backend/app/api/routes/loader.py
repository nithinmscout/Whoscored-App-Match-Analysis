import json
import pandas as pd
from typing import Iterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core.config import DATA_ROOT
from app.schemas.loader import ScheduleRequest, SaveScheduleRequest, LoadScheduleCsvRequest
from app.services.loader_service import (
    load_schedule,
    get_league_presets,
    get_schedule_folders,
    get_schedule_seasons,
    save_schedule_csv,
    get_event_coverage_audit,
    get_event_coverage_overview,
    stream_fetch_events,
    stream_load_schedule,
)

router = APIRouter()


@router.get("/status")
def loader_status():
    return {"page": "Loader", "message": "Loader route is ready"}



@router.post("/schedule")
def loader_schedule(payload: ScheduleRequest):
    try:
        df = load_schedule(
            league=payload.league,
            season=payload.season,
            headless=payload.headless,
            browserpath=payload.browserpath,
        )
        return {
            "count": len(df),
            "columns": df.columns.tolist(),
            "rows": df.fillna("").to_dict(orient="records"),
        }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Schedule scrape failed: {type(e).__name__}: {e}"
        )



@router.get("/schedule-stream")
def loader_schedule_stream(
    league: str,
    season: str,
    headless: bool = True,
    browserpath: str = "",
):
    def event_gen() -> Iterator[str]:
        for obj in stream_load_schedule(
            league=league,
            season=season,
            headless=headless,
            browserpath=browserpath or None,
        ):
            yield f"data: {json.dumps(obj, default=str)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/league-presets")
def league_presets():
    return {"leagues": get_league_presets()}

@router.get("/schedule-folders")
def schedule_folders():
    return {"folders": get_schedule_folders(DATA_ROOT)}


@router.get("/schedule-seasons")
def schedule_seasons(nation: str, tier: str):
    return {"seasons": get_schedule_seasons(DATA_ROOT, nation, tier)}


@router.post("/save-schedule")
def save_schedule(payload: SaveScheduleRequest):
    result = save_schedule_csv(
        basedir=DATA_ROOT,
        nation=payload.nation,
        tier=payload.tier,
        season=payload.season,
        rows=payload.rows,
        league=payload.league,
    )
    return {
        "path": str(result["path"]),
        "mode": result["mode"],
        "folder": result.get("folder", ""),
        "nation": result.get("nation", payload.nation),
        "tier": result.get("tier", payload.tier),
        "auto_resolved_folder": result.get("auto_resolved_folder", False),
        "message": result["message"],
    }


@router.post("/load-schedule-csv")
def load_schedule_csv(payload: LoadScheduleCsvRequest):
    folder_name = f"{payload.nation} {payload.tier}".strip()
    schedule_path = DATA_ROOT / "data" / "Schedule" / folder_name / f"{payload.season}.csv"

    if not schedule_path.exists():
        raise HTTPException(status_code=404, detail=f"Schedule CSV not found: {schedule_path}")

    try:
        df = pd.read_csv(schedule_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed reading schedule CSV: {e}")

    return {
        "count": len(df),
        "columns": df.columns.tolist(),
        "rows": df.fillna("").to_dict(orient="records"),
    }



@router.get("/events-coverage-overview")
def events_coverage_overview(
    season: str = Query(""),
    only_finished: bool = Query(True),
    overwrite: bool = Query(False),
    retry_failed: bool = Query(False),
):
    try:
        return get_event_coverage_overview(
            basedir=DATA_ROOT,
            season=season,
            only_finished=only_finished,
            overwrite=overwrite,
            retry_failed=retry_failed,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Event coverage overview failed: {type(exc).__name__}: {exc}",
        ) from exc


@router.get("/events-coverage")
def events_coverage(
    league: str,
    season: str,
    nation: str = "",
    tier: str = "",
    only_finished: bool = True,
    overwrite: bool = False,
    retry_failed: bool = False,
):
    try:
        return get_event_coverage_audit(
            basedir=DATA_ROOT,
            league=league,
            season=season,
            nation=nation,
            tier=tier,
            only_finished=only_finished,
            overwrite=overwrite,
            retry_failed=retry_failed,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Event coverage audit failed: {type(exc).__name__}: {exc}") from exc


@router.get("/fetch-events-stream")
def fetch_events_stream(
    league: str,
    season: str,
    headless: bool = True,
    browserpath: str = "",
    nation: str = "",
    tier: str = "",
    only_finished: bool = True,
    overwrite: bool = False,
    retry_failed: bool = False,
    fail_fast: bool = True,
    scrape_positions: bool = True,
):
    def event_gen() -> Iterator[str]:
        for obj in stream_fetch_events(
            basedir=DATA_ROOT,
            league=league,
            season=season,
            headless=headless,
            browserpath=browserpath or None,
            nation=nation,
            tier=tier,
            only_finished=only_finished,
            overwrite=overwrite,
            retry_failed=retry_failed,
            fail_fast=fail_fast,
            scrape_positions=scrape_positions,
        ):
            yield f"data: {json.dumps(obj, default=str)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )