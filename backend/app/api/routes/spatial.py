from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

import pandas as pd

from app.core.config import DATA_ROOT
from app.metrics.contextual import compute_contextual_match_metrics
from app.metrics.expected_threat import build_xt_model, build_xt_surface_response, zone_index
from app.metrics.spadl_prep import prepare_whoscored_spadl
from app.services.event_data_service import load_match_events

router = APIRouter()


def _latest_player_locations(events_df, minute: float | None, fixture: dict[str, object]) -> dict[str, object]:
    df = events_df.copy()
    if minute is not None:
        df = df.loc[df["expanded_minute"].le(float(minute))].copy()
    if df.empty:
        return {
            "match_id": int(fixture["match_id"]),
            "minute": minute,
            "home_team": str(fixture["home_team"]),
            "away_team": str(fixture["away_team"]),
            "players": [],
        }

    xt_model = build_xt_model(events_df)
    touch_like = df.loc[df["is_touch"] | df["is_pass_like"] | df["is_carry"]].copy()
    if touch_like.empty:
        touch_like = df.copy()

    touch_like = touch_like.sort_values("expanded_minute", ascending=True, na_position="last")
    latest = touch_like.groupby(["team", "player"], as_index=False).tail(1).copy()

    players: list[dict[str, object]] = []
    for row in latest.itertuples(index=False):
        zone = zone_index(getattr(row, "x_120", None), getattr(row, "y_80", None))
        if zone is None:
            continue
        x_value = pd.to_numeric(pd.Series([getattr(row, "x_120", None)]), errors="coerce").iloc[0]
        y_value = pd.to_numeric(pd.Series([getattr(row, "y_80", None)]), errors="coerce").iloc[0]
        if pd.isna(x_value) or pd.isna(y_value):
            continue
        local_xt = float(xt_model.xt[int(zone)])
        players.append(
            {
                "player_id": int(getattr(row, "player_id", -1)) if getattr(row, "player_id", None) is not None else None,
                "player": str(getattr(row, "player", "")),
                "team": str(getattr(row, "team", "")),
                "team_side": "home" if str(getattr(row, "team", "")) == str(fixture["home_team"]) else "away",
                "x": round(float(x_value), 3),
                "y": round(float(y_value), 3),
                "local_xt": round(local_xt, 6),
                "touches": int(
                    ((df["team"].astype(str) == str(getattr(row, "team", ""))) & (df["player"].astype(str) == str(getattr(row, "player", ""))) & (df["is_touch"])).sum()
                ),
            }
        )

    return {
        "match_id": int(fixture["match_id"]),
        "minute": minute,
        "home_team": str(fixture["home_team"]),
        "away_team": str(fixture["away_team"]),
        "players": players,
    }


@router.get("/status")
def spatial_status() -> dict[str, str]:
    return {"page": "Spatial", "message": "Spatial routes are ready"}


@router.get("/match-xt")
def get_match_xt(
    nation: str = Query(...),
    tier: str = Query(...),
    season: str = Query(...),
    match_id: int = Query(...),
):
    try:
        events_df, fixture = load_match_events(DATA_ROOT, nation, tier, season, match_id)
        return build_xt_surface_response(events_df, fixture)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"xT calculation failed: {type(exc).__name__}: {exc}") from exc


@router.get("/match-context")
def get_match_context(
    nation: str = Query(...),
    tier: str = Query(...),
    season: str = Query(...),
    match_id: int = Query(...),
):
    try:
        events_df, fixture = load_match_events(DATA_ROOT, nation, tier, season, match_id)
        return compute_contextual_match_metrics(events_df, fixture)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Contextual metric calculation failed: {type(exc).__name__}: {exc}") from exc


@router.get("/match-spadl")
def get_match_spadl(
    nation: str = Query(...),
    tier: str = Query(...),
    season: str = Query(...),
    match_id: int = Query(...),
    limit: int = Query(200, ge=10, le=2000),
):
    try:
        events_df, fixture = load_match_events(DATA_ROOT, nation, tier, season, match_id)
        spadl = prepare_whoscored_spadl(events_df)
        preview = spadl.head(int(limit))
        return {
            "match_id": int(fixture["match_id"]),
            "home_team": str(fixture["home_team"]),
            "away_team": str(fixture["away_team"]),
            "count": int(len(spadl)),
            "columns": preview.columns.tolist(),
            "rows": preview.fillna("").to_dict(orient="records"),
            "note": "This is a SPADL shaped scaffold ready for VAEP preparation, not the official provider specific socceraction converter.",
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"SPADL preparation failed: {type(exc).__name__}: {exc}") from exc


@router.get("/pitch-control-snapshot")
def get_pitch_control_snapshot(
    nation: str = Query(...),
    tier: str = Query(...),
    season: str = Query(...),
    match_id: int = Query(...),
    minute: float | None = Query(None),
):
    try:
        events_df, fixture = load_match_events(DATA_ROOT, nation, tier, season, match_id)
        return _latest_player_locations(events_df, minute, fixture)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Pitch control snapshot failed: {type(exc).__name__}: {exc}") from exc