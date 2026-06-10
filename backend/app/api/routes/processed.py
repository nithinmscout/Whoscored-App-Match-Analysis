from __future__ import annotations

import json
from typing import Iterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core.config import DATA_ROOT
from app.services.processed_event_store import build_processed_event_store, processed_store_status, stream_build_processed_event_store

router = APIRouter()


@router.get("/status")
def processed_status(
    nation: str = Query(...),
    tier: str = Query(...),
    season: str = Query(...),
):
    try:
        return processed_store_status(DATA_ROOT, nation=nation, tier=tier, season=season)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Processed store status failed: {type(exc).__name__}: {exc}") from exc


@router.post("/rebuild")
def processed_rebuild(
    nation: str = Query(...),
    tier: str = Query(...),
    season: str = Query(...),
    force: bool = Query(False),
):
    try:
        return build_processed_event_store(DATA_ROOT, nation=nation, tier=tier, season=season, force=force)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Processed store rebuild failed: {type(exc).__name__}: {exc}") from exc

@router.get("/rebuild-stream")
def processed_rebuild_stream(
    nation: str = Query(...),
    tier: str = Query(...),
    season: str = Query(...),
    force: bool = Query(False),
):
    def event_gen() -> Iterator[str]:
        try:
            for obj in stream_build_processed_event_store(DATA_ROOT, nation=nation, tier=tier, season=season, force=force):
                yield f"data: {json.dumps(obj, default=str)}\n\n"
        except Exception as exc:
            payload = {
                "kind": "error",
                "stage": "failed",
                "message": f"Processed store rebuild failed: {type(exc).__name__}: {exc}",
            }
            yield f"data: {json.dumps(payload, default=str)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
