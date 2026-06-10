from __future__ import annotations

import ast
import csv
import json
import time
import re
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError, ParserError

PITCH_LENGTH = 120.0
PITCH_WIDTH = 80.0
FINAL_THIRD_X = 80.0
SEASON_EVENTS_CACHE_VERSION = "team_dashboard_cache_v1"
MAX_SEASON_EVENTS_MEMORY_CACHE_ITEMS = 4

_SEASON_EVENTS_MEMORY_CACHE: dict[str, pd.DataFrame] = {}


def _safe_slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return "".join(ch if ch.isalnum() or ch in {"_", "."} else "_" for ch in text).strip("_")


def _schedule_root(basedir: Path) -> Path:
    return basedir / "data" / "Schedule"


def _events_root(basedir: Path) -> Path:
    p1 = basedir / "data" / "Event Data"
    p2 = basedir / "data" / "Event data"
    if p1.exists():
        return p1
    if p2.exists():
        return p2
    return p1


def _cache_root(basedir: Path) -> Path:
    return basedir / "data" / "_cache" / "event_frames"


def _season_cache_dir(basedir: Path, nation: str, tier: str, season: str) -> Path:
    root = _cache_root(basedir) / _safe_slug(nation)
    if str(tier or "").strip():
        root = root / _safe_slug(tier)
    return root / _safe_slug(season)


def _source_file_signature(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(paths, key=lambda item: str(item).lower()):
        stat = path.stat()
        rows.append(
            {
                "path": str(path.resolve()),
                "mtime": float(stat.st_mtime),
                "mtime_ns": int(stat.st_mtime_ns),
                "size": int(stat.st_size),
            }
        )
    return rows


def _season_events_memory_key(nation: str, tier: str, season: str, source_files: list[dict[str, Any]]) -> str:
    payload = {
        "cache_version": SEASON_EVENTS_CACHE_VERSION,
        "nation": str(nation),
        "tier": str(tier),
        "season": str(season),
        "source_files": source_files,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _season_events_manifest(source_files: list[dict[str, Any]], row_count: int) -> dict[str, Any]:
    return {
        "cache_version": SEASON_EVENTS_CACHE_VERSION,
        "created_time": time.time(),
        "row_count": int(row_count),
        "source_csv_paths": [str(item["path"]) for item in source_files],
        "source_file_modified_times": [float(item["mtime"]) for item in source_files],
        "source_file_modified_time_ns": [int(item["mtime_ns"]) for item in source_files],
        "source_file_sizes": [int(item["size"]) for item in source_files],
        "source_files": source_files,
    }


def _manifest_matches_sources(manifest: dict[str, Any], source_files: list[dict[str, Any]]) -> bool:
    if manifest.get("cache_version") != SEASON_EVENTS_CACHE_VERSION:
        return False
    if manifest.get("source_files") == source_files:
        return True
    return (
        manifest.get("source_csv_paths") == [str(item["path"]) for item in source_files]
        and manifest.get("source_file_modified_time_ns") == [int(item["mtime_ns"]) for item in source_files]
        and manifest.get("source_file_sizes") == [int(item["size"]) for item in source_files]
    )


def _json_safe_container(value: object) -> object:
    if isinstance(value, (list, tuple, set)):
        return json.dumps(list(value), default=str, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, default=str, ensure_ascii=False)
    return value


def _frame_for_parquet(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["qual_tags", "qual_map"]:
        if col in out.columns:
            out[col] = out[col].map(_json_safe_container)
    return out


def _parse_json_container(value: object, fallback: object) -> object:
    if isinstance(value, (list, dict)):
        return value
    if value is None:
        return fallback
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>", "null"}:
        return fallback
    try:
        return json.loads(text)
    except Exception:
        return fallback


def _restore_parquet_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "qual_tags" in out.columns:
        out["qual_tags"] = out["qual_tags"].map(lambda value: _parse_json_container(value, []))
    if "qual_map" in out.columns:
        out["qual_map"] = out["qual_map"].map(lambda value: _parse_json_container(value, {}))
    return out


def _remember_season_frame(cache_key: str, frame: pd.DataFrame) -> None:
    if cache_key in _SEASON_EVENTS_MEMORY_CACHE:
        _SEASON_EVENTS_MEMORY_CACHE.pop(cache_key, None)
    _SEASON_EVENTS_MEMORY_CACHE[cache_key] = frame.copy()
    while len(_SEASON_EVENTS_MEMORY_CACHE) > MAX_SEASON_EVENTS_MEMORY_CACHE_ITEMS:
        oldest_key = next(iter(_SEASON_EVENTS_MEMORY_CACHE))
        _SEASON_EVENTS_MEMORY_CACHE.pop(oldest_key, None)


def _read_season_events_from_disk_cache(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    source_files: list[dict[str, Any]],
) -> pd.DataFrame | None:
    cache_dir = _season_cache_dir(basedir, nation, tier, season)
    parquet_path = cache_dir / "season_events.parquet"
    meta_path = cache_dir / "season_events.meta.json"
    if not parquet_path.exists() or not meta_path.exists():
        return None

    try:
        manifest = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not isinstance(manifest, dict) or not _manifest_matches_sources(manifest, source_files):
        return None

    try:
        return _restore_parquet_frame(pd.read_parquet(parquet_path))
    except Exception:
        return None


def _write_season_events_disk_cache(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    source_files: list[dict[str, Any]],
    frame: pd.DataFrame,
) -> None:
    cache_dir = _season_cache_dir(basedir, nation, tier, season)
    parquet_path = cache_dir / "season_events.parquet"
    meta_path = cache_dir / "season_events.meta.json"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _frame_for_parquet(frame).to_parquet(parquet_path, index=False)
        manifest = _season_events_manifest(source_files, len(frame))
        meta_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        parquet_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)


def _read_csv_with_repaired_field_counts(path: Path) -> pd.DataFrame:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
            if not header:
                return pd.DataFrame()

            clean_header: list[str] = []
            used: dict[str, int] = {}
            for index, raw_name in enumerate(header):
                name = str(raw_name or "").strip() or f"unnamed_{index}"
                count = used.get(name, 0)
                used[name] = count + 1
                clean_header.append(name if count == 0 else f"{name}_{count}")

            width = len(clean_header)
            rows: list[list[object]] = []
            for row in reader:
                if not row:
                    continue
                if len(row) > width:
                    row = row[:width]
                elif len(row) < width:
                    row = row + ([""] * (width - len(row)))
                rows.append(row)

        return pd.DataFrame(rows, columns=clean_header)
    except Exception:
        return pd.DataFrame()


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, low_memory=False)
    except EmptyDataError:
        return pd.DataFrame()
    except ParserError:
        repaired = _read_csv_with_repaired_field_counts(path)
        if not repaired.empty:
            repaired.attrs["csv_read_mode"] = "repaired_field_count"
            return repaired
        try:
            fallback = pd.read_csv(path, engine="python", on_bad_lines="skip")
            fallback.attrs["csv_read_mode"] = "python_skip_bad_lines"
            return fallback
        except EmptyDataError:
            return pd.DataFrame()


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _match_id_col(df: pd.DataFrame) -> str | None:
    return _find_col(df, ["match_id", "matchid", "matchId", "game_id", "gameId", "id"])


def _home_away_cols(df: pd.DataFrame) -> tuple[str | None, str | None]:
    return (
        _find_col(df, ["home_team", "home", "homeTeam", "home_team_name", "hometeam"]),
        _find_col(df, ["away_team", "away", "awayTeam", "away_team_name", "awayteam"]),
    )


def _rename_known_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "matchid": "match_id",
        "matchId": "match_id",
        "game_id": "match_id",
        "gameId": "match_id",
        "teamId": "team_id",
        "playerId": "player_id",
        "teamName": "team",
        "playerName": "player",
        "goalMouthX": "goal_mouth_x",
        "goalMouthY": "goal_mouth_y",
        "goalMouthZ": "goal_mouth_z",
        "blockedX": "blocked_x",
        "blockedY": "blocked_y",
    }
    out = df.copy()
    for src, dst in rename_map.items():
        if src in out.columns and dst not in out.columns:
            out = out.rename(columns={src: dst})
    return out


def _safe_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"true", "1", "yes", "y"}


def _coerce_period_value(value: object) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.notna(numeric):
        return int(numeric)

    key = re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())
    if not key or key in {"nan", "none", "null"}:
        return None

    period_map = {
        "firsthalf": 1,
        "firstperiod": 1,
        "1sthalf": 1,
        "1h": 1,
        "h1": 1,
        "fh": 1,
        "secondhalf": 2,
        "secondperiod": 2,
        "2ndhalf": 2,
        "2h": 2,
        "h2": 2,
        "sh": 2,
        "extratimefirsthalf": 3,
        "firsthalfofextratime": 3,
        "extratime1sthalf": 3,
        "et1": 3,
        "extratimesecondhalf": 4,
        "secondhalfofextratime": 4,
        "extratime2ndhalf": 4,
        "et2": 4,
        "penaltyshootout": 5,
        "penalties": 5,
        "shootout": 5,
    }
    return period_map.get(key)


def _coerce_period_series(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series(1, index=index, dtype="int64")

    coerced = series.apply(_coerce_period_value)
    return pd.to_numeric(coerced, errors="coerce").fillna(1).astype("int64")


def _first_non_blank(row: pd.Series, candidates: list[str]) -> str:
    for key in candidates:
        if key in row and str(row.get(key, "")).strip() not in {"", "nan", "None"}:
            return str(row.get(key)).strip()
    return ""


def _parse_qualifiers_cell(value: Any) -> list[dict[str, Any]]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]

    text = str(value).strip()
    if not text or text.lower() == "nan":
        return []

    for parser in (json.loads, ast.literal_eval):
        try:
            raw = parser(text)
            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, dict)]
            if isinstance(raw, dict):
                return [raw]
        except Exception:
            continue
    return []


def _qualifier_type_name(item: dict[str, Any]) -> str:
    raw = item.get("type")
    if isinstance(raw, dict):
        return str(raw.get("displayName") or raw.get("name") or "").strip()
    if isinstance(raw, str):
        return raw.strip()
    return ""


def _qualifier_map(items: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        name = _qualifier_type_name(item)
        if not name:
            continue
        value = item.get("value")
        out[name] = True if value in [None, "", "nan", "None"] else value
    return out


def _team_season_path(events_root: Path, nation: str, tier: str, team_name: str, season: str) -> Path:
    root = events_root / _safe_slug(nation)
    if str(tier or '').strip():
        root = root / _safe_slug(tier)
    return root / _safe_slug(team_name) / f"{_safe_slug(season)}.csv"

def _legacy_team_season_path(events_root: Path, nation: str, team_name: str, season: str) -> Path:
    return events_root / _safe_slug(nation) / _safe_slug(team_name) / f"{_safe_slug(season)}.csv"


def _normalise_team_key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", text)


def _source_team_side(source_team: str, home_team: str, away_team: str) -> str:
    source_key = _normalise_team_key(source_team)
    home_key = _normalise_team_key(home_team)
    away_key = _normalise_team_key(away_team)

    if not source_key:
        return ""

    home_hit = bool(home_key and (source_key == home_key or source_key.startswith(home_key) or home_key.startswith(source_key)))
    away_hit = bool(away_key and (source_key == away_key or source_key.startswith(away_key) or away_key.startswith(source_key)))

    if home_hit and not away_hit:
        return "home"
    if away_hit and not home_hit:
        return "away"
    return ""


def _event_scope_roots(events_root: Path, nation: str, tier: str) -> list[Path]:
    roots: list[Path] = []

    scoped_root = events_root / _safe_slug(nation)
    if str(tier or "").strip():
        scoped_root = scoped_root / _safe_slug(tier)
    roots.append(scoped_root)

    legacy_root = events_root / _safe_slug(nation)
    if legacy_root != scoped_root:
        roots.append(legacy_root)

    flat_folder_root = events_root / f"{_safe_slug(nation)} {_safe_slug(tier)}".strip()
    if flat_folder_root not in roots:
        roots.append(flat_folder_root)

    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        out.append(root)
    return out


def _iter_team_season_files(events_root: Path, nation: str, tier: str, season: str) -> list[tuple[Path, str]]:
    """Return every usable season event CSV for the selected scope.

    The app now supports both storage layouts:
    1. Team files: Event Data/England/T4/Bristol_Rovers/2025.csv
    2. Flat season files: Event Data/England/T4/2025.csv
    """
    out: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    season_file = f"{_safe_slug(season)}.csv"

    for root in _event_scope_roots(events_root, nation, tier):
        if not root.exists():
            continue

        direct_csv = root / season_file
        if direct_csv.exists() and direct_csv.is_file() and direct_csv not in seen:
            seen.add(direct_csv)
            out.append((direct_csv, "__season_event_file"))

        for team_dir in sorted(item for item in root.iterdir() if item.is_dir()):
            if team_dir.name.startswith("_") or re.fullmatch(r"T\d+", team_dir.name, flags=re.IGNORECASE):
                continue
            csv_path = team_dir / season_file
            if csv_path.exists() and csv_path not in seen:
                seen.add(csv_path)
                out.append((csv_path, team_dir.name))

    return out


def _match_rows_from_team_file(path: Path, match_id: int) -> pd.DataFrame:
    team_df = _read_csv(path)
    if team_df.empty:
        return pd.DataFrame()

    team_df = _rename_known_columns(team_df)
    match_col = _match_id_col(team_df)
    if not match_col:
        return pd.DataFrame()

    match_ids = pd.to_numeric(team_df[match_col], errors="coerce")
    mask = match_ids.eq(int(match_id))
    if not mask.any():
        return pd.DataFrame()

    return team_df.loc[mask].copy()


def load_schedule_frame(basedir: Path, nation: str, tier: str, season: str) -> pd.DataFrame:
    safe_season = _safe_slug(season)
    if "__backup" in safe_season.lower() or safe_season.lower().endswith("_backup"):
        raise ValueError("Backup schedule CSV files are not valid selectable seasons. Select the main season file instead.")

    folder_name = f"{nation} {tier}".strip()
    path = _schedule_root(basedir) / folder_name / f"{safe_season}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Saved schedule CSV not found: {path}")
    return _read_csv(path)


def resolve_match_row(schedule_df: pd.DataFrame, match_id: int) -> pd.Series:
    match_col = _match_id_col(schedule_df)
    if not match_col:
        raise ValueError("Schedule does not contain a match id column.")

    mask = pd.to_numeric(schedule_df[match_col], errors="coerce").eq(int(match_id))
    if not mask.any():
        raise ValueError(f"Match id {match_id} was not found in the selected schedule.")
    return schedule_df.loc[mask].iloc[0]


def normalise_event_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = _rename_known_columns(df)

    if "qualifiers" in out.columns:
        qualifier_lists = out["qualifiers"].apply(_parse_qualifiers_cell)
        qualifier_maps = qualifier_lists.apply(_qualifier_map)
        out["qual_tags"] = qualifier_maps.apply(lambda item: list(item.keys()))
        out["qual_map"] = qualifier_maps

        if "end_x" not in out.columns:
            out["end_x"] = pd.NA
        if "end_y" not in out.columns:
            out["end_y"] = pd.NA

        out["end_x"] = pd.to_numeric(out["end_x"], errors="coerce").fillna(
            qualifier_maps.apply(lambda item: pd.to_numeric(pd.Series([item.get("PassEndX")]), errors="coerce").iloc[0])
        )
        out["end_y"] = pd.to_numeric(out["end_y"], errors="coerce").fillna(
            qualifier_maps.apply(lambda item: pd.to_numeric(pd.Series([item.get("PassEndY")]), errors="coerce").iloc[0])
        )
    else:
        out["qual_tags"] = [[] for _ in range(len(out))]
        out["qual_map"] = [{} for _ in range(len(out))]

    numeric_cols = [
        "x",
        "y",
        "end_x",
        "end_y",
        "minute",
        "second",
        "expanded_minute",
        "goal_mouth_x",
        "goal_mouth_y",
        "goal_mouth_z",
    ]
    for col in numeric_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    out["period"] = _coerce_period_series(out["period"] if "period" in out.columns else None, out.index)

    for col in ["x", "y", "end_x", "end_y", "goal_mouth_x", "goal_mouth_y", "goal_mouth_z"]:
        if col in out.columns:
            mx = out[col].max(skipna=True)
            if pd.notna(mx) and mx <= 1.5:
                out[col] = out[col] * 100.0

    if "expanded_minute" not in out.columns:
        out["expanded_minute"] = pd.NA
    out["expanded_minute"] = out["expanded_minute"].fillna(out.get("minute"))
    if "second" in out.columns:
        out["expanded_minute"] = out["expanded_minute"] + (out["second"].fillna(0) / 60.0)

    out["team"] = out.apply(lambda row: _first_non_blank(row, ["team", "team_name", "teamName"]), axis=1)
    out["player"] = out.apply(lambda row: _first_non_blank(row, ["player", "player_name", "playerName", "name"]), axis=1)
    out["type"] = out.get("type", pd.Series([""] * len(out))).astype(str)
    out["outcome_type"] = out.get("outcome_type", pd.Series([""] * len(out))).astype(str)
    out["type_l"] = out["type"].str.lower().str.strip()
    out["outcome_l"] = out["outcome_type"].str.lower().str.strip()

    if "is_shot" not in out.columns:
        out["is_shot"] = False
    if "is_goal" not in out.columns:
        out["is_goal"] = False
    if "is_touch" not in out.columns:
        out["is_touch"] = False

    out["is_shot"] = out["is_shot"].map(_safe_bool)
    out["is_goal"] = out["is_goal"].map(_safe_bool)
    out["is_touch"] = out["is_touch"].map(_safe_bool)

    if "match_id" not in out.columns:
        source_match_col = next((col for col in ["matchid", "matchId", "game_id", "gameId", "id"] if col in out.columns), None)
        out["match_id"] = pd.to_numeric(out[source_match_col], errors="coerce") if source_match_col else pd.NA
    else:
        out["match_id"] = pd.to_numeric(out["match_id"], errors="coerce")

    for raw_col, scaled_col, scale in [
        ("x", "x_120", PITCH_LENGTH / 100.0),
        ("end_x", "end_x_120", PITCH_LENGTH / 100.0),
        ("y", "y_80", PITCH_WIDTH / 100.0),
        ("end_y", "end_y_80", PITCH_WIDTH / 100.0),
    ]:
        out[scaled_col] = pd.to_numeric(out.get(raw_col), errors="coerce") * scale

    out["successful"] = out["outcome_l"].isin({"successful", "success", "won", "complete", "completed", "accurate"}) | out["is_goal"]
    out["is_pass_like"] = out["type_l"].str.contains("pass", na=False) | out["type_l"].isin(["cross"])
    out["is_carry"] = out["type_l"].isin(["carry", "dribble", "take on", "takeon", "run"])
    out["is_shot_event"] = out["is_shot"] | out["is_goal"] | out["type_l"].str.contains("shot", na=False) | out["type_l"].eq("goal")
    out["is_defensive_action"] = out["type_l"].isin(["tackle", "challenge", "interception", "clearance", "block", "ballrecovery", "recovery"]) | out["type_l"].str.contains("aerial|duel|recovery|tackle|interception|clearance|block|foul", na=False)
    out["is_final_third_pass"] = out["is_pass_like"] & out["successful"] & out["x_120"].ge(FINAL_THIRD_X)

    return out



def load_season_events(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
) -> pd.DataFrame:
    events_root = _events_root(basedir)
    source_items = _iter_team_season_files(events_root, nation, tier, season)
    source_paths = [path for path, _source_team in source_items]
    source_files = _source_file_signature(source_paths)
    cache_key = _season_events_memory_key(nation, tier, season, source_files)

    cached = _SEASON_EVENTS_MEMORY_CACHE.get(cache_key)
    if cached is not None:
        return cached.copy()

    if not source_items:
        raise FileNotFoundError(f"No saved event CSV rows were found for {nation} {tier} {season}.")

    cached_from_disk = _read_season_events_from_disk_cache(
        basedir=basedir,
        nation=nation,
        tier=tier,
        season=season,
        source_files=source_files,
    )
    if cached_from_disk is not None:
        _remember_season_frame(cache_key, cached_from_disk)
        return cached_from_disk.copy()

    frames: list[pd.DataFrame] = []
    checked_paths: list[str] = []

    for path, source_team in source_items:
        checked_paths.append(str(path))
        team_df = _read_csv(path)
        if team_df.empty:
            continue

        team_df = team_df.copy()
        team_df["__source_team_file"] = source_team
        frames.append(team_df)

    if not frames:
        preview = " | ".join(checked_paths[:12])
        extra = f" Checked paths: {preview}" if preview else ""
        raise FileNotFoundError(f"No saved event CSV rows were found for {nation} {tier} {season}.{extra}")

    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined = normalise_event_frame(combined)

    if "match_id" in combined.columns:
        combined = combined.loc[pd.to_numeric(combined["match_id"], errors="coerce").notna()].copy()

    dedupe_candidates = [
        "match_id",
        "event_index",
        "team_id",
        "player_id",
        "team",
        "player",
        "period",
        "minute",
        "second",
        "type",
        "x",
        "y",
        "end_x",
        "end_y",
    ]
    dedupe_cols = [
        col
        for col in dedupe_candidates
        if col in combined.columns and not combined[col].map(lambda value: isinstance(value, (list, dict, set))).any()
    ]
    if "match_id" in dedupe_cols and len(dedupe_cols) >= 4:
        combined = combined.drop_duplicates(subset=dedupe_cols).reset_index(drop=True)
    else:
        combined = combined.reset_index(drop=True)

    sort_cols = [col for col in ["match_id", "period", "expanded_minute", "minute", "second"] if col in combined.columns]
    if sort_cols:
        combined = combined.sort_values(sort_cols, na_position="last").reset_index(drop=True)

    _remember_season_frame(cache_key, combined)
    _write_season_events_disk_cache(
        basedir=basedir,
        nation=nation,
        tier=tier,
        season=season,
        source_files=source_files,
        frame=combined,
    )

    return combined.copy()

def load_match_events(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    match_id: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    schedule_df = load_schedule_frame(basedir, nation, tier, season)
    row = resolve_match_row(schedule_df, match_id)
    home_col, away_col = _home_away_cols(schedule_df)
    if not home_col or not away_col:
        raise ValueError("Schedule does not contain home and away team columns.")

    home_team = str(row.get(home_col, "")).strip()
    away_team = str(row.get(away_col, "")).strip()
    if not home_team or not away_team:
        raise ValueError("Could not resolve the fixture teams for the requested match.")

    events_root = _events_root(basedir)
    frames: list[pd.DataFrame] = []
    checked_paths: list[str] = []
    found_sides: set[str] = set()
    seen_frame_keys: set[tuple[str, int]] = set()

    exact_candidates = [
        ("home", home_team, _team_season_path(events_root, nation, tier, home_team, season)),
        ("away", away_team, _team_season_path(events_root, nation, tier, away_team, season)),
    ]

    for side, team_name, path in exact_candidates:
        if not path.exists():
            legacy_path = _legacy_team_season_path(events_root, nation, team_name, season)
            path = legacy_path if legacy_path.exists() else path

        checked_paths.append(str(path))
        if not path.exists():
            continue

        subset = _match_rows_from_team_file(path, match_id)
        if subset.empty:
            continue

        subset["__source_team_file"] = team_name
        subset["team_side"] = side
        frames.append(subset)
        found_sides.add(side)
        seen_frame_keys.add((str(path), int(match_id)))

    if len(found_sides) < 2:
        for path, source_team in _iter_team_season_files(events_root, nation, tier, season):
            frame_key = (str(path), int(match_id))
            if frame_key in seen_frame_keys:
                continue

            checked_paths.append(str(path))
            subset = _match_rows_from_team_file(path, match_id)
            if subset.empty:
                continue

            side = _source_team_side(source_team, home_team, away_team)
            subset["__source_team_file"] = source_team
            if side:
                subset["team_side"] = side
                found_sides.add(side)

            frames.append(subset)
            seen_frame_keys.add(frame_key)

            if {"home", "away"}.issubset(found_sides):
                break

    if not frames:
        preview = " | ".join(checked_paths[:12])
        extra = f" Checked paths: {preview}" if preview else ""
        raise FileNotFoundError(f"No saved event CSV rows were found for match id {match_id}.{extra}")

    combined = pd.concat(frames, ignore_index=True)
    combined = normalise_event_frame(combined)

    if "team" in combined.columns:
        home_key = _normalise_team_key(home_team)
        away_key = _normalise_team_key(away_team)

        def _row_side(team_value: object, current_side: object = "") -> str:
            current = str(current_side or "").strip().lower()
            if current in {"home", "away"}:
                return current
            team_key = _normalise_team_key(team_value)
            if team_key and (team_key == home_key or team_key.startswith(home_key) or home_key.startswith(team_key)):
                return "home"
            if team_key and (team_key == away_key or team_key.startswith(away_key) or away_key.startswith(team_key)):
                return "away"
            return current

        current_side = combined["team_side"] if "team_side" in combined.columns else pd.Series([""] * len(combined), index=combined.index)
        combined["team_side"] = [
            _row_side(team_value, side_value)
            for team_value, side_value in zip(combined["team"], current_side)
        ]

    combined = combined.sort_values(["period", "expanded_minute", "minute", "second"], na_position="last")

    dedupe_exclude = {"__source_team_file", "qual_tags", "qual_map"}
    dedupe_cols = [
        col
        for col in combined.columns
        if col not in dedupe_exclude and not combined[col].map(lambda value: isinstance(value, (list, dict, set))).any()
    ]
    if dedupe_cols:
        combined = combined.drop_duplicates(subset=dedupe_cols).reset_index(drop=True)
    else:
        combined = combined.reset_index(drop=True)

    fixture = {
        "match_id": int(match_id),
        "home_team": home_team,
        "away_team": away_team,
        "kickoff": str(row.get("date") or row.get("start_time") or row.get("started_at_utc") or ""),
        "status": str(row.get("status") or row.get("elapsed") or ""),
        "event_source_files": sorted(
            {
                str(value)
                for value in combined.get("__source_team_file", pd.Series(dtype="object")).dropna().astype(str).unique()
                if str(value).strip()
            }
        ),
    }
    return combined, fixture