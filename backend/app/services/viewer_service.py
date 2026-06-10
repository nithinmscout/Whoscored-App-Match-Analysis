from __future__ import annotations

import copy
import csv
import hashlib
import json
import time
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError, ParserError

from app.metrics.expected_assists import aggregate_player_xa, link_shots_to_assists
from app.metrics.expected_goals import aggregate_player_xg, fit_xg_models, score_shots_with_models
from app.metrics.expected_threat import aggregate_player_xt, build_xt_model, value_actions
from app.services.event_data_service import (
    _iter_team_season_files,
    load_match_events,
    load_schedule_frame,
    load_season_events,
    normalise_event_frame,
)


PITCH_LENGTH = 120.0
PITCH_WIDTH = 80.0
FINAL_THIRD_X = 80.0
BOX_X = 100.0
BOX_Y_MIN = 18.0
BOX_Y_MAX = 62.0
TEAM_SUMMARY_CACHE_VERSION = "team_profile_payload_v8_parquet_profile_store"
TEAM_ANALYSIS_PROCESSED_CACHE_VERSION = "team_analysis_profile_store_v2"
TEAM_SUMMARY_DISK_CACHE_VERSION = "team_summary_json_v1"
TEAM_PROFILE_MEMORY_LIMIT = 24
_TEAM_PROFILE_MEMORY_CACHE: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}


def _safe_slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return "".join(ch if ch.isalnum() or ch in {"_", "."} else "_" for ch in text).strip("_")


def _events_root(basedir: Path) -> Path:
    p1 = basedir / "data" / "Event Data"
    p2 = basedir / "data" / "Event data"
    if p1.exists():
        return p1
    if p2.exists():
        return p2
    return p1


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

        repaired = pd.DataFrame(rows, columns=clean_header)
        repaired.attrs["csv_read_mode"] = "repaired_field_count"
        return repaired
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
            return repaired
        try:
            fallback = pd.read_csv(path, engine="python", on_bad_lines="skip")
            fallback.attrs["csv_read_mode"] = "python_skip_bad_lines"
            return fallback
        except EmptyDataError:
            return pd.DataFrame()


def _norm_team_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip().replace("&", "and")
    return "".join(ch for ch in text if ch.isalnum())


def _match_id_col(df: pd.DataFrame) -> str | None:
    for col in ["match_id", "matchid", "matchId", "game_id", "gameId", "id"]:
        if col in df.columns:
            return col
    return None


def _team_col(df: pd.DataFrame) -> str | None:
    for col in ["team", "team_name", "teamName", "club", "club_name"]:
        if col in df.columns:
            return col
    return None


def _player_col(df: pd.DataFrame) -> str | None:
    for col in ["player", "player_name", "playerName", "name"]:
        if col in df.columns:
            return col
    return None


def _type_col(df: pd.DataFrame) -> str | None:
    for col in ["type", "type_l", "event_type"]:
        if col in df.columns:
            return col
    return None


def _home_away_cols(df: pd.DataFrame) -> tuple[str | None, str | None]:
    home_col = next((col for col in ["home_team", "home", "homeTeam", "home_team_name", "hometeam"] if col in df.columns), None)
    away_col = next((col for col in ["away_team", "away", "awayTeam", "away_team_name", "awayteam"] if col in df.columns), None)
    return home_col, away_col


def _serialise_frame(df: pd.DataFrame, limit: int = 5000) -> dict[str, Any]:
    preview = df.head(max(1, min(int(limit), 20000))).copy()
    return {
        "count": int(len(df)),
        "columns": preview.columns.tolist(),
        "rows": preview.fillna("").to_dict(orient="records"),
    }


def _number_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(0.0, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce").fillna(0.0)


def _text_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index, dtype="object")
    return df[col].astype(str).fillna("")


def _bool_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(False, index=df.index, dtype="bool")
    raw = df[col]
    if pd.api.types.is_bool_dtype(raw):
        return raw.fillna(False).astype(bool)
    return raw.astype(str).str.lower().isin(["true", "1", "yes", "y", "successful", "success", "won"])


def _pct(value: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round((float(value) / float(denominator)) * 100.0, 2)


def _clean_number(value: object) -> int | float | None:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return None
    as_float = float(numeric)
    return int(as_float) if as_float.is_integer() else round(as_float, 3)


def _unique_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _source_file_signature(paths: list[Path]) -> tuple[tuple[str, float, int, int], ...]:
    rows: list[tuple[str, float, int, int]] = []
    for path in sorted(paths, key=lambda item: str(item).lower()):
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append((str(path.resolve()), float(stat.st_mtime), int(stat.st_mtime_ns), int(stat.st_size)))
    return tuple(rows)


def _first_existing_direct_team_path(events_root: Path, nation: str, tier: str, team: str, season: str) -> Path | None:
    for path in _direct_team_paths(events_root, nation, tier, team, season):
        if path.exists() and path.is_file():
            return path
    return None


def _schedule_source_paths(basedir: Path, nation: str, tier: str, season: str) -> list[Path]:
    folder_name = f"{nation} {tier}".strip()
    safe_folder_name = f"{_safe_slug(nation)} {_safe_slug(tier)}".strip()
    season_name = f"{_safe_slug(season)}.csv"
    candidates = [
        basedir / "data" / "Schedule" / folder_name / season_name,
        basedir / "data" / "Schedule" / safe_folder_name / season_name,
    ]
    return [path for path in _unique_paths(candidates) if path.exists() and path.is_file()]



def _normalised_path_key(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except Exception:
        return str(path)


def _file_stamp(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
        return str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size)
    except Exception:
        return str(path), 0, 0


def _tree_stamp(root: Path, suffixes: tuple[str, ...]) -> tuple[int, int, int]:
    if not root.exists():
        return 0, 0, 0

    latest_mtime = 0
    total_size = 0
    file_count = 0
    suffix_set = {suffix.lower() for suffix in suffixes}
    try:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in suffix_set:
                continue
            try:
                stat = path.stat()
            except Exception:
                continue
            latest_mtime = max(latest_mtime, int(stat.st_mtime_ns))
            total_size += int(stat.st_size)
            file_count += 1
    except Exception:
        return 0, 0, 0
    return latest_mtime, total_size, file_count


def _processed_scope_root(basedir: Path, nation: str, tier: str, season: str) -> Path:
    return basedir / "data" / "Processed" / _safe_slug(nation) / _safe_slug(tier or "T1") / _safe_slug(season)


def _processed_store_stamp(basedir: Path, nation: str, tier: str, season: str) -> tuple[int, int, int]:
    return _tree_stamp(_processed_scope_root(basedir, nation, tier, season), (".parquet", ".json"))


def _event_scope_paths(basedir: Path, nation: str, tier: str, season: str) -> list[Path]:
    events_root = _events_root(basedir)
    return [path for path, _source_team in _iter_team_season_files(events_root, nation, tier, season)]


def _event_scope_stamp(basedir: Path, nation: str, tier: str, season: str) -> tuple[tuple[str, float, int, int], ...]:
    return _source_file_signature(_event_scope_paths(basedir, nation, tier, season))


def _json_safe_cache_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, bool)):
        return value
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return _json_safe_cache_value(value.item())
    except Exception:
        pass
    if isinstance(value, float):
        return value if pd.notna(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe_cache_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_cache_value(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return str(value)
    except Exception:
        return None


def _stable_hash_payload(value: Any) -> str:
    raw = json.dumps(_json_safe_cache_value(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _direct_team_paths(events_root: Path, nation: str, tier: str, team: str, season: str) -> list[Path]:
    exact_nation = str(nation or "").strip()
    exact_tier = str(tier or "").strip()
    exact_team = str(team or "").strip()
    exact_season = str(season or "").strip()
    safe_nation = _safe_slug(exact_nation)
    safe_tier = _safe_slug(exact_tier)
    safe_team = _safe_slug(exact_team)
    safe_season = _safe_slug(exact_season)

    candidates: list[Path] = []
    if exact_nation and exact_tier and exact_team and exact_season:
        candidates.append(events_root / exact_nation / exact_tier / exact_team / f"{exact_season}.csv")
    if safe_nation and safe_tier and safe_team and safe_season:
        candidates.append(events_root / safe_nation / safe_tier / safe_team / f"{safe_season}.csv")
    if exact_nation and exact_team and exact_season:
        candidates.append(events_root / exact_nation / exact_team / f"{exact_season}.csv")
    if safe_nation and safe_team and safe_season:
        candidates.append(events_root / safe_nation / safe_team / f"{safe_season}.csv")
    if exact_nation and exact_tier and exact_team and exact_season:
        candidates.append(events_root / f"{exact_nation} {exact_tier}".strip() / exact_team / f"{exact_season}.csv")
    if safe_nation and safe_tier and safe_team and safe_season:
        candidates.append(events_root / f"{safe_nation} {safe_tier}".strip() / safe_team / f"{safe_season}.csv")
    return _unique_paths(candidates)


def _team_filter(df: pd.DataFrame, team: str) -> pd.Series:
    if df.empty:
        return pd.Series(False, index=df.index, dtype="bool")
    key = _norm_team_name(team)
    if not key:
        return pd.Series(False, index=df.index, dtype="bool")

    mask = pd.Series(False, index=df.index, dtype="bool")
    if "team" in df.columns:
        mask = mask | df["team"].map(_norm_team_name).eq(key)
    if "__source_team_file" in df.columns:
        mask = mask | df["__source_team_file"].map(_norm_team_name).eq(key)
    return mask


def _load_direct_team_scope(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    team: str,
) -> tuple[pd.DataFrame, pd.DataFrame, str, int] | None:
    events_root = _events_root(basedir)
    for path in _direct_team_paths(events_root, nation, tier, team, season):
        if not path.exists() or not path.is_file():
            continue
        raw = _read_csv(path)
        if raw.empty:
            continue
        raw = raw.copy()
        raw["__source_team_file"] = path.parent.name
        normalised = normalise_event_frame(raw)
        team_mask = _team_filter(normalised, team)
        if team_mask.any():
            team_df = normalised.loc[team_mask].copy()
        else:
            team_df = normalised.copy()
            if "team" not in team_df.columns or team_df["team"].astype(str).str.strip().eq("").all():
                team_df["team"] = team
        return team_df.reset_index(drop=True), normalised.reset_index(drop=True), str(path), int(len(raw))
    return None


def _load_team_scope(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    team: str,
) -> tuple[pd.DataFrame, pd.DataFrame, str, int, str]:
    direct = _load_direct_team_scope(basedir, nation, tier, season, team)
    if direct is not None:
        team_df, source_df, path, raw_count = direct
        return team_df, source_df, path, raw_count, "direct_team_file"

    season_df = load_season_events(basedir, nation, tier, season)
    team_mask = _team_filter(season_df, team)
    team_df = season_df.loc[team_mask].copy() if team_mask.any() else pd.DataFrame(columns=season_df.columns)
    return team_df.reset_index(drop=True), season_df.reset_index(drop=True), "season event store", int(len(season_df)), "full_season_fallback"



def _team_analysis_cache_root(basedir: Path, nation: str, tier: str, season: str) -> Path:
    return basedir / "data" / "_cache" / "team_analysis" / _safe_slug(nation or "nation") / _safe_slug(tier or "tier") / _safe_slug(season or "season")


def _team_analysis_processed_cache_root(basedir: Path, nation: str, tier: str, season: str) -> Path:
    return _team_analysis_cache_root(basedir, nation, tier, season)


def _team_analysis_manifest_path(basedir: Path, nation: str, tier: str, season: str) -> Path:
    return _team_analysis_cache_root(basedir, nation, tier, season) / "manifest.json"


def _team_analysis_cache_frame_paths(basedir: Path, nation: str, tier: str, season: str) -> dict[str, Path]:
    root = _team_analysis_cache_root(basedir, nation, tier, season)
    return {
        "cleaned_season_events": root / "cleaned_season_events.parquet",
        "team_match_summary": root / "team_match_summary.parquet",
        "team_profiles": root / "team_profiles.parquet",
        "league_ranking_metrics": root / "league_ranking_metrics.parquet",
        "player_contribution_metrics": root / "player_contribution_metrics.parquet",
        "expected_metrics": root / "expected_metrics.parquet",
        "xg": root / "xg.parquet",
        "xa": root / "xa.parquet",
        "xt": root / "xt.parquet",
        "set_piece_profiles": root / "set_piece_profiles.parquet",
        "transition_profiles": root / "transition_profiles.parquet",
        "common_lineup_data": root / "common_lineup_data.parquet",
        "shot_tables": root / "shot_tables.parquet",
        "action_value_tables": root / "action_value_tables.parquet",
        "attacking_profiles": root / "attacking_profiles.parquet",
        "defensive_profiles": root / "defensive_profiles.parquet",
        "phase_radar": root / "phase_radar.parquet",
        "phase_kpi_breakdowns": root / "phase_kpi_breakdowns.parquet",
        "player_influence": root / "player_influence.parquet",
        "multi_season_profiles": root / "multi_season_profiles.parquet",
        "data_quality": root / "data_quality.parquet",
        "visual_payloads": root / "visual_payloads.parquet",
        "club_profiles": root / "club_profiles.parquet",
    }


def _source_signature_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, mtime, mtime_ns, size in _source_file_signature(paths):
        rows.append(
            {
                "path": path,
                "modified_time": mtime,
                "modified_time_ns": mtime_ns,
                "size": size,
            }
        )
    return rows


def _team_analysis_source_state(basedir: Path, nation: str, tier: str, season: str) -> dict[str, Any]:
    event_rows = _source_signature_rows(_event_scope_paths(basedir, nation, tier, season))
    schedule_rows = _source_signature_rows(_schedule_source_paths(basedir, nation, tier, season))
    processed_mtime_ns, processed_size, processed_files = _processed_store_stamp(basedir, nation, tier, season)
    return {
        "cache_version": TEAM_ANALYSIS_PROCESSED_CACHE_VERSION,
        "nation": str(nation),
        "tier": str(tier),
        "season": str(season),
        "source_event_csv_paths": event_rows,
        "source_event_csv_modified_time_ns": max((int(row.get("modified_time_ns") or 0) for row in event_rows), default=0),
        "source_event_csv_size": sum(int(row.get("size") or 0) for row in event_rows),
        "schedule_files": schedule_rows,
        "schedule_file_path": str(schedule_rows[0].get("path", "")) if schedule_rows else "",
        "schedule_file_modified_time_ns": max((int(row.get("modified_time_ns") or 0) for row in schedule_rows), default=0),
        "schedule_file_size": sum(int(row.get("size") or 0) for row in schedule_rows),
        "processed_store_stamp": {
            "modified_time_ns": int(processed_mtime_ns),
            "size": int(processed_size),
            "files": int(processed_files),
        },
    }


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _source_fingerprint_from_state(source_state: dict[str, Any]) -> str:
    return _stable_hash_payload(source_state)


def _team_analysis_manifest_fingerprint(manifest: dict[str, Any] | None) -> str:
    if not isinstance(manifest, dict):
        return ""
    return _stable_hash_payload(
        {
            "cache_version": manifest.get("cache_version"),
            "source_fingerprint": manifest.get("source_fingerprint"),
            "created_at": manifest.get("created_at"),
            "club_profiles_rows": manifest.get("row_counts", {}).get("club_profiles"),
        }
    )


def _team_analysis_cache_is_current(basedir: Path, nation: str, tier: str, season: str) -> bool:
    manifest = _read_json_dict(_team_analysis_manifest_path(basedir, nation, tier, season))
    if not isinstance(manifest, dict):
        return False
    if manifest.get("cache_version") != TEAM_ANALYSIS_PROCESSED_CACHE_VERSION:
        return False

    source_state = _team_analysis_source_state(basedir, nation, tier, season)
    if manifest.get("source_state") != source_state:
        return False
    if manifest.get("source_fingerprint") != _source_fingerprint_from_state(source_state):
        return False

    row_counts = manifest.get("row_counts") if isinstance(manifest.get("row_counts"), dict) else {}
    if int(row_counts.get("club_profiles") or 0) <= 0:
        return False

    club_profiles = _team_analysis_cache_frame_paths(basedir, nation, tier, season).get("club_profiles")
    return bool(club_profiles and club_profiles.exists() and club_profiles.is_file())


def _team_analysis_manifest_is_valid(basedir: Path, nation: str, tier: str, season: str) -> bool:
    return _team_analysis_cache_is_current(basedir, nation, tier, season)


def _json_dumps_cache_value(value: Any) -> str:
    try:
        return json.dumps(_json_safe_cache_value(value), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except Exception:
        return json.dumps(str(value), ensure_ascii=False, allow_nan=False)


def _json_loads_cache_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return _json_safe_cache_value(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return value


def _serialisable_cache_frame(df: pd.DataFrame) -> pd.DataFrame:
    keep = df.copy()
    if keep.empty:
        return keep
    keep.columns = [str(col).strip() for col in keep.columns]
    if keep.columns.duplicated().any():
        keep = keep.loc[:, ~keep.columns.duplicated()].copy()
    for col in keep.columns:
        try:
            has_nested = keep[col].map(lambda value: isinstance(value, (dict, list, set, tuple))).any()
        except Exception:
            has_nested = False
        if has_nested:
            keep[col] = keep[col].apply(lambda value: "" if value is None else _json_dumps_cache_value(value))
    for col in keep.columns:
        if pd.api.types.is_datetime64_any_dtype(keep[col]):
            keep[col] = pd.to_datetime(keep[col], errors="coerce", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ").fillna("")
        elif pd.api.types.is_object_dtype(keep[col]) or pd.api.types.is_string_dtype(keep[col]):
            keep[col] = keep[col].astype(str).replace({"nan": "", "None": "", "<NA>": "", "NaT": ""}).fillna("")
    return keep


def _write_team_analysis_parquet_frame(df: pd.DataFrame, path: Path) -> int:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame = _serialisable_cache_frame(df)
        tmp_path = path.with_suffix(".tmp.parquet")
        frame.to_parquet(tmp_path, index=False)
        tmp_path.replace(path)
        return int(len(frame))
    except Exception:
        try:
            if "tmp_path" in locals() and tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        return 0


def _read_team_analysis_parquet_frame(path: Path) -> pd.DataFrame:
    try:
        if not path.exists() or not path.is_file():
            return pd.DataFrame()
        frame = pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()
    return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()


def _write_cache_frame(df: pd.DataFrame, path: Path) -> None:
    _write_team_analysis_parquet_frame(df, path)


def _read_cache_frame(path: Path) -> pd.DataFrame:
    return _read_team_analysis_parquet_frame(path)


def _table_from_dict_rows(rows: list[dict[str, Any]], default_columns: list[str] | None = None) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=default_columns or [])
    return pd.DataFrame(rows)


def _build_team_match_summary_table(league_df: pd.DataFrame) -> pd.DataFrame:
    if league_df.empty:
        return pd.DataFrame(columns=["team", "match_id", "events", "shots", "passes", "carries", "defensive_actions", "set_piece_events"])

    work = normalise_event_frame(league_df)
    masks = _event_masks(work)
    team_col = _team_col(work) or "team"
    match_col = _match_id_col(work) or "match_id"
    if team_col not in work.columns:
        work[team_col] = ""
    if match_col not in work.columns:
        work[match_col] = 0

    calc = work[[team_col, match_col]].copy()
    calc["shots"] = masks["is_shot"].astype(int)
    calc["passes"] = masks["is_pass"].astype(int)
    calc["carries"] = masks["is_carry"].astype(int)
    calc["defensive_actions"] = masks["is_defensive"].astype(int)
    calc["set_piece_events"] = masks["is_set_piece"].astype(int)
    grouped = (
        calc.groupby([team_col, match_col], dropna=False)
        .agg(
            events=(team_col, "size"),
            shots=("shots", "sum"),
            passes=("passes", "sum"),
            carries=("carries", "sum"),
            defensive_actions=("defensive_actions", "sum"),
            set_piece_events=("set_piece_events", "sum"),
        )
        .reset_index()
        .rename(columns={team_col: "team", match_col: "match_id"})
    )
    return grouped


def _build_team_profiles_table(league_df: pd.DataFrame) -> pd.DataFrame:
    match_summary = _build_team_match_summary_table(league_df)
    if match_summary.empty:
        return pd.DataFrame(columns=["team", "matches", "events", "shots", "passes", "carries", "defensive_actions", "set_piece_events"])
    grouped = (
        match_summary.groupby("team", dropna=False)
        .agg(
            matches=("match_id", "nunique"),
            events=("events", "sum"),
            matches_involved=("match_id", "nunique"),
            shots=("shots", "sum"),
            passes=("passes", "sum"),
            carries=("carries", "sum"),
            defensive_actions=("defensive_actions", "sum"),
            set_piece_events=("set_piece_events", "sum"),
        )
        .reset_index()
    )
    return grouped


def _build_league_ranking_metrics_table(league_df: pd.DataFrame, metric_context: dict[str, Any]) -> pd.DataFrame:
    profiles = _build_team_profiles_table(league_df)
    if profiles.empty:
        return profiles

    xg = metric_context.get("league_scored_shots", pd.DataFrame())
    xa = metric_context.get("league_xa_links", pd.DataFrame())
    xt = metric_context.get("league_xt_actions", pd.DataFrame())

    out = profiles.copy()
    out["xg"] = 0.0
    out["xa"] = 0.0
    out["xt"] = 0.0

    if isinstance(xg, pd.DataFrame) and not xg.empty and "team" in xg.columns:
        xg_group = xg.groupby("team", dropna=False)["xg"].sum() if "xg" in xg.columns else pd.Series(dtype=float)
        out["xg"] = out["team"].map(xg_group).fillna(0.0)
    if isinstance(xa, pd.DataFrame) and not xa.empty and "team" in xa.columns:
        xa_group = xa.groupby("team", dropna=False)["xa_raw"].sum() if "xa_raw" in xa.columns else pd.Series(dtype=float)
        out["xa"] = out["team"].map(xa_group).fillna(0.0)
    if isinstance(xt, pd.DataFrame) and not xt.empty and "team" in xt.columns:
        xt_group = xt.groupby("team", dropna=False)["xt_added"].sum() if "xt_added" in xt.columns else pd.Series(dtype=float)
        out["xt"] = out["team"].map(xt_group).fillna(0.0)

    for col in ["events", "shots", "passes", "carries", "defensive_actions", "set_piece_events", "xg", "xa", "xt"]:
        if col in out.columns:
            out[f"{col}_rank"] = pd.to_numeric(out[col], errors="coerce").rank(ascending=False, method="min").astype("Int64")
    return out


def _build_player_contribution_metrics_table(metric_context: dict[str, Any]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for metric_name, frame_key in [("xg", "player_xg"), ("xa", "player_xa"), ("xt", "player_xt")]:
        frame = metric_context.get(frame_key, pd.DataFrame())
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            temp = frame.copy()
            temp["metric_family"] = metric_name
            frames.append(temp)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _metric_quality_table(metric_context: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for key in ["xg_quality", "xa_quality", "xt_quality"]:
        value = metric_context.get(key)
        if isinstance(value, dict):
            row = {"metric": key.replace("_quality", "")}
            row.update(_json_safe_cache_value(value))
            rows.append(row)
    return _table_from_dict_rows(rows, ["metric", "status", "note"])


def _build_set_piece_profiles_table(league_df: pd.DataFrame) -> pd.DataFrame:
    if league_df.empty:
        return pd.DataFrame(columns=["team", "corners", "free_kicks", "throw_ins", "set_piece_events"])
    work = normalise_event_frame(league_df)
    masks = _event_masks(work)
    team_col = _team_col(work) or "team"
    if team_col not in work.columns:
        work[team_col] = ""
    calc = work[[team_col]].copy()
    calc["corners"] = masks["is_corner"].astype(int)
    calc["free_kicks"] = masks["is_free_kick"].astype(int)
    calc["throw_ins"] = masks["is_throw_in"].astype(int)
    calc["set_piece_events"] = masks["is_set_piece"].astype(int)
    return calc.groupby(team_col, dropna=False).sum(numeric_only=True).reset_index().rename(columns={team_col: "team"})


def _build_transition_profiles_table(league_df: pd.DataFrame) -> pd.DataFrame:
    if league_df.empty:
        return pd.DataFrame(columns=["team", "high_regains", "final_third_entries", "box_entries"])
    work = normalise_event_frame(league_df)
    masks = _event_masks(work)
    team_col = _team_col(work) or "team"
    if team_col not in work.columns:
        work[team_col] = ""
    calc = work[[team_col]].copy()
    calc["high_regains"] = masks["high_regain"].astype(int)
    calc["final_third_entries"] = masks["final_third_entry"].astype(int)
    calc["box_entries"] = masks["box_entry"].astype(int)
    return calc.groupby(team_col, dropna=False).sum(numeric_only=True).reset_index().rename(columns={team_col: "team"})


def _build_common_lineup_table(league_df: pd.DataFrame) -> pd.DataFrame:
    if league_df.empty:
        return pd.DataFrame(columns=["team", "player", "events", "avg_x", "avg_y", "matches"])
    work = normalise_event_frame(league_df)
    team_col = _team_col(work) or "team"
    player_col = _player_col(work) or "player"
    match_col = _match_id_col(work) or "match_id"
    if team_col not in work.columns or player_col not in work.columns:
        return pd.DataFrame(columns=["team", "player", "events", "avg_x", "avg_y", "matches"])
    masks = _event_masks(work)
    calc = work[[team_col, player_col]].copy()
    calc["match_id"] = pd.to_numeric(work[match_col], errors="coerce") if match_col in work.columns else 0
    calc["x"] = masks["x"]
    calc["y"] = masks["y"]
    grouped = (
        calc.groupby([team_col, player_col], dropna=False)
        .agg(events=(player_col, "size"), avg_x=("x", "mean"), avg_y=("y", "mean"), matches=("match_id", "nunique"))
        .reset_index()
        .rename(columns={team_col: "team", player_col: "player"})
    )
    return grouped


def _team_names_from_league_frame(league_df: pd.DataFrame) -> list[str]:
    if not isinstance(league_df, pd.DataFrame) or league_df.empty:
        return []
    team_col = _team_col(league_df) or "team"
    if team_col not in league_df.columns:
        return []
    teams = []
    seen: set[str] = set()
    for value in league_df[team_col].dropna().astype(str).tolist():
        team_name = value.strip()
        key = _norm_team_name(team_name)
        if not team_name or not key or key in seen:
            continue
        seen.add(key)
        teams.append(team_name)
    return sorted(teams, key=lambda item: item.lower())


def _build_team_analysis_base_frames(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[str, Any]]:
    league_df = normalise_event_frame(load_season_events(basedir, nation, tier, season))
    metric_context = _build_expected_metric_context(
        league_df=league_df,
        selected_match_df=league_df,
    )

    frames: dict[str, pd.DataFrame] = {
        "cleaned_season_events": league_df,
        "team_match_summary": _build_team_match_summary_table(league_df),
        "team_profiles": _build_team_profiles_table(league_df),
        "league_ranking_metrics": _build_league_ranking_metrics_table(league_df, metric_context),
        "player_contribution_metrics": _build_player_contribution_metrics_table(metric_context),
        "expected_metrics": _metric_quality_table(metric_context),
        "xg": metric_context.get("league_scored_shots", pd.DataFrame()),
        "xa": metric_context.get("league_xa_links", pd.DataFrame()),
        "xt": metric_context.get("league_xt_actions", pd.DataFrame()),
        "set_piece_profiles": _build_set_piece_profiles_table(league_df),
        "transition_profiles": _build_transition_profiles_table(league_df),
        "common_lineup_data": _build_common_lineup_table(league_df),
        "shot_tables": metric_context.get("league_scored_shots", pd.DataFrame()),
        "action_value_tables": metric_context.get("league_xt_actions", pd.DataFrame()),
    }

    metric_quality = _json_safe_cache_value(
        {
            "xg_quality": metric_context.get("xg_quality", {}),
            "xa_quality": metric_context.get("xa_quality", {}),
            "xt_quality": metric_context.get("xt_quality", {}),
        }
    )
    return league_df, frames, metric_quality


def _profile_store_processed_cache(
    root: Path,
    frames: dict[str, pd.DataFrame],
    metric_quality: dict[str, Any],
    *,
    rebuilt: bool,
) -> dict[str, Any]:
    return {
        "cache_hit": not rebuilt,
        "rebuilt": rebuilt,
        "root": str(root),
        "frames": frames,
        "metric_quality": metric_quality,
    }


def _team_profile_payload_row(
    *,
    payload: dict[str, Any],
    nation: str,
    tier: str,
    season: str,
    team: str,
    source_fingerprint: str,
    manifest_fingerprint: str,
) -> dict[str, Any]:
    overview = payload.get("overview") if isinstance(payload.get("overview"), dict) else {}
    render_meta = payload.get("render_meta") if isinstance(payload.get("render_meta"), dict) else {}
    return {
        "nation": str(nation),
        "tier": str(tier),
        "season": str(season),
        "team": str(team),
        "team_key": _norm_team_name(team),
        "matches": int(payload.get("matches") or 0),
        "rows": int(payload.get("rows") or 0),
        "source_fingerprint": source_fingerprint,
        "manifest_fingerprint": manifest_fingerprint,
        "profile_generated_at": render_meta.get("generated_at"),
        "overview_json": _json_dumps_cache_value(overview),
        "style_tags_json": _json_dumps_cache_value(payload.get("style_tags", [])),
        "match_log_json": _json_dumps_cache_value(payload.get("match_log", [])),
        "attacking_profile_json": _json_dumps_cache_value(payload.get("attacking_profile") or payload.get("attacking") or {}),
        "defensive_profile_json": _json_dumps_cache_value(payload.get("defensive_profile") or payload.get("defensive") or {}),
        "transition_profile_json": _json_dumps_cache_value(payload.get("transition_profile") or payload.get("transitions") or {}),
        "set_piece_profile_json": _json_dumps_cache_value(payload.get("set_piece_profile") or payload.get("set_pieces") or {}),
        "metric_radar_json": _json_dumps_cache_value(payload.get("metric_radar", [])),
        "phase_radar_groups_json": _json_dumps_cache_value(payload.get("phase_radar_groups", [])),
        "phase_kpi_breakdowns_json": _json_dumps_cache_value(payload.get("phase_kpi_breakdowns", {})),
        "common_lineup_json": _json_dumps_cache_value(payload.get("common_lineup", {})),
        "in_possession_shape_json": _json_dumps_cache_value(payload.get("in_possession_shape", {})),
        "defensive_shape_json": _json_dumps_cache_value(payload.get("defensive_shape", {})),
        "attacking_territory_json": _json_dumps_cache_value(payload.get("attacking_territory", {})),
        "shot_maps_json": _json_dumps_cache_value(payload.get("shot_maps", {})),
        "pass_maps_json": _json_dumps_cache_value(payload.get("pass_maps", {})),
        "carry_maps_json": _json_dumps_cache_value(payload.get("carry_maps", {})),
        "lane_kpis_json": _json_dumps_cache_value(payload.get("lane_kpis", {})),
        "seasonal_defensive_dashboard_json": _json_dumps_cache_value(payload.get("seasonal_defensive_dashboard", {})),
        "player_influence_dashboard_json": _json_dumps_cache_value(payload.get("player_influence_dashboard", {})),
        "multi_season_profile_json": _json_dumps_cache_value(payload.get("multi_season_profile", {})),
        "player_profile_json": _json_dumps_cache_value(payload.get("player_profile", {})),
        "data_quality_json": _json_dumps_cache_value(payload.get("data_quality", {})),
        "visual_payload_metadata_json": _json_dumps_cache_value(
            {
                "has_shot_maps": bool(payload.get("shot_maps")),
                "has_pass_maps": bool(payload.get("pass_maps")),
                "has_carry_maps": bool(payload.get("carry_maps")),
                "has_phase_radar": bool(payload.get("phase_radar_groups")),
                "raw_rows_loaded_by_default": False,
            }
        ),
        "payload_json": _json_dumps_cache_value(payload),
    }


def _frame_from_profile_rows(rows: list[dict[str, Any]], column_name: str) -> pd.DataFrame:
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        out_rows.append(
            {
                "nation": row.get("nation", ""),
                "tier": row.get("tier", ""),
                "season": row.get("season", ""),
                "team": row.get("team", ""),
                "team_key": row.get("team_key", ""),
                "source_fingerprint": row.get("source_fingerprint", ""),
                column_name: row.get(column_name, "{}"),
            }
        )
    return pd.DataFrame(out_rows)


def _build_visual_payloads_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    keys = [
        "metric_radar_json",
        "phase_radar_groups_json",
        "phase_kpi_breakdowns_json",
        "common_lineup_json",
        "in_possession_shape_json",
        "defensive_shape_json",
        "attacking_territory_json",
        "shot_maps_json",
        "pass_maps_json",
        "carry_maps_json",
        "lane_kpis_json",
        "seasonal_defensive_dashboard_json",
        "player_influence_dashboard_json",
        "visual_payload_metadata_json",
    ]
    return pd.DataFrame([{key: row.get(key, "{}") for key in ["nation", "tier", "season", "team", "team_key", *keys]} for row in rows])


def _build_team_analysis_parquet_store(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    root = _team_analysis_cache_root(basedir, nation, tier, season)
    paths = _team_analysis_cache_frame_paths(basedir, nation, tier, season)
    source_state = _team_analysis_source_state(basedir, nation, tier, season)
    source_fingerprint = _source_fingerprint_from_state(source_state)

    if not force and _team_analysis_cache_is_current(basedir, nation, tier, season):
        manifest = _read_json_dict(_team_analysis_manifest_path(basedir, nation, tier, season)) or {}
        return {
            "cache_hit": True,
            "rebuilt": False,
            "root": str(root),
            "manifest": manifest,
            "source_fingerprint": manifest.get("source_fingerprint", source_fingerprint),
            "row_counts": manifest.get("row_counts", {}),
        }

    league_df, frames, metric_quality = _build_team_analysis_base_frames(basedir, nation, tier, season)
    processed_cache = _profile_store_processed_cache(root, frames, metric_quality, rebuilt=True)
    team_names = _team_names_from_league_frame(league_df)

    profile_rows: list[dict[str, Any]] = []
    manifest_seed = {
        "cache_version": TEAM_ANALYSIS_PROCESSED_CACHE_VERSION,
        "source_fingerprint": source_fingerprint,
        "created_at": time.time(),
    }
    manifest_fingerprint = _stable_hash_payload(manifest_seed)

    for team_name in team_names:
        team_mask = _team_filter(league_df, team_name)
        team_df = league_df.loc[team_mask].copy() if team_mask.any() else pd.DataFrame(columns=league_df.columns)
        try:
            payload = _build_dashboard_payload(
                team_df=team_df.reset_index(drop=True),
                source_df=league_df.reset_index(drop=True),
                basedir=basedir,
                nation=nation,
                tier=tier,
                season=season,
                team=team_name,
                path=str(paths["cleaned_season_events"]),
                raw_count=int(len(league_df)),
                load_mode="team_analysis_parquet_profile_rebuild",
                processed_cache=processed_cache,
            )
        except Exception as exc:
            payload = {
                "team": team_name,
                "nation": nation,
                "tier": tier,
                "season": season,
                "path": str(paths["cleaned_season_events"]),
                "rows": int(len(team_df)),
                "matches": 0,
                "overview": {},
                "style_tags": [],
                "match_log": [],
                "attacking_profile": {},
                "defensive_profile": {},
                "transition_profile": {},
                "set_piece_profile": {},
                "players": [],
                "data_quality": {
                    "load_mode": "team_analysis_parquet_profile_rebuild",
                    "source_path": str(paths["cleaned_season_events"]),
                    "source_rows": int(len(league_df)),
                    "own_team_rows": int(len(team_df)),
                    "team_rows": int(len(team_df)),
                    "notes": [f"Profile build failed for {team_name}: {type(exc).__name__}: {exc}"],
                    "xg_model_status": metric_quality.get("xg_quality", {}) if isinstance(metric_quality, dict) else {},
                    "xa_model_status": metric_quality.get("xa_quality", {}) if isinstance(metric_quality, dict) else {},
                    "xt_model_status": metric_quality.get("xt_quality", {}) if isinstance(metric_quality, dict) else {},
                },
                "render_meta": {
                    "generated_at": time.time(),
                    "raw_rows_loaded_by_default": False,
                    "load_mode": "team_analysis_parquet_profile_rebuild_failed",
                    "processed_cache_root": str(root),
                    "processed_cache_error": f"{type(exc).__name__}: {exc}",
                },
            }

        render_meta = payload.get("render_meta")
        if not isinstance(render_meta, dict):
            render_meta = {}
        render_meta.update(
            {
                "raw_rows_loaded_by_default": False,
                "profile_store_path": str(root),
                "source_fingerprint": source_fingerprint,
                "cache_version": TEAM_ANALYSIS_PROCESSED_CACHE_VERSION,
            }
        )
        payload["render_meta"] = render_meta

        profile_rows.append(
            _team_profile_payload_row(
                payload=_json_safe_cache_value(payload),
                nation=nation,
                tier=tier,
                season=season,
                team=team_name,
                source_fingerprint=source_fingerprint,
                manifest_fingerprint=manifest_fingerprint,
            )
        )

    frames.update(
        {
            "attacking_profiles": _frame_from_profile_rows(profile_rows, "attacking_profile_json"),
            "defensive_profiles": _frame_from_profile_rows(profile_rows, "defensive_profile_json"),
            "phase_radar": _frame_from_profile_rows(profile_rows, "phase_radar_groups_json"),
            "phase_kpi_breakdowns": _frame_from_profile_rows(profile_rows, "phase_kpi_breakdowns_json"),
            "player_influence": _frame_from_profile_rows(profile_rows, "player_influence_dashboard_json"),
            "multi_season_profiles": _frame_from_profile_rows(profile_rows, "multi_season_profile_json"),
            "data_quality": _frame_from_profile_rows(profile_rows, "data_quality_json"),
            "visual_payloads": _build_visual_payloads_frame(profile_rows),
            "club_profiles": pd.DataFrame(profile_rows),
        }
    )

    row_counts: dict[str, int] = {}
    for key, frame in frames.items():
        if not isinstance(frame, pd.DataFrame):
            frame = pd.DataFrame()
        row_counts[key] = _write_team_analysis_parquet_frame(frame, paths[key])

    match_col = _match_id_col(league_df) or "match_id"
    match_count = int(pd.to_numeric(league_df[match_col], errors="coerce").nunique()) if match_col in league_df.columns else 0

    manifest = {
        "cache_version": TEAM_ANALYSIS_PROCESSED_CACHE_VERSION,
        "nation": str(nation),
        "tier": str(tier),
        "season": str(season),
        "source_state": source_state,
        "source_fingerprint": source_fingerprint,
        "source_event_csv_paths": source_state["source_event_csv_paths"],
        "source_event_csv_modified_time_ns": source_state["source_event_csv_modified_time_ns"],
        "source_event_csv_size": source_state["source_event_csv_size"],
        "schedule_file_path": source_state["schedule_file_path"],
        "schedule_file_modified_time_ns": source_state["schedule_file_modified_time_ns"],
        "schedule_file_size": source_state["schedule_file_size"],
        "processed_store_stamp": source_state["processed_store_stamp"],
        "row_counts": row_counts,
        "team_count": int(len(profile_rows)),
        "match_count": match_count,
        "frames": {key: str(path) for key, path in paths.items()},
        "metric_quality": metric_quality,
        "created_at": time.time(),
    }

    try:
        root.mkdir(parents=True, exist_ok=True)
        manifest_path = _team_analysis_manifest_path(basedir, nation, tier, season)
        tmp_path = manifest_path.with_name(f"{manifest_path.name}.tmp")
        tmp_path.write_text(json.dumps(manifest, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        tmp_path.replace(manifest_path)
    except Exception:
        pass

    _clear_team_profile_memory_cache(nation=nation, tier=tier, season=season)

    return {
        "cache_hit": False,
        "rebuilt": True,
        "root": str(root),
        "manifest": manifest,
        "source_fingerprint": source_fingerprint,
        "row_counts": row_counts,
        "team_count": int(len(profile_rows)),
        "match_count": match_count,
    }


def _build_or_refresh_team_analysis_processed_cache(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
) -> dict[str, Any]:
    store = _build_team_analysis_parquet_store(basedir, nation, tier, season, force=True)
    frames = {key: _read_team_analysis_parquet_frame(path) for key, path in _team_analysis_cache_frame_paths(basedir, nation, tier, season).items()}
    manifest = store.get("manifest") if isinstance(store.get("manifest"), dict) else {}
    return {
        "cache_hit": False,
        "rebuilt": True,
        "root": store.get("root", str(_team_analysis_cache_root(basedir, nation, tier, season))),
        "frames": frames,
        "metric_quality": manifest.get("metric_quality", {}),
    }


def _read_team_analysis_processed_cache(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
) -> dict[str, Any] | None:
    if not _team_analysis_cache_is_current(basedir, nation, tier, season):
        return None

    paths = _team_analysis_cache_frame_paths(basedir, nation, tier, season)
    frames = {key: _read_team_analysis_parquet_frame(path) for key, path in paths.items()}
    if frames.get("cleaned_season_events", pd.DataFrame()).empty:
        return None

    manifest = _read_json_dict(_team_analysis_manifest_path(basedir, nation, tier, season)) or {}
    return {
        "cache_hit": True,
        "rebuilt": False,
        "root": str(_team_analysis_cache_root(basedir, nation, tier, season)),
        "frames": frames,
        "metric_quality": manifest.get("metric_quality", {}),
    }


def _get_team_analysis_processed_cache(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
) -> dict[str, Any]:
    cached = _read_team_analysis_processed_cache(basedir, nation, tier, season)
    if cached is not None:
        return cached
    try:
        return _build_or_refresh_team_analysis_processed_cache(basedir, nation, tier, season)
    except Exception as exc:
        return {
            "cache_hit": False,
            "rebuilt": False,
            "root": str(_team_analysis_cache_root(basedir, nation, tier, season)),
            "frames": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def _clear_team_profile_memory_cache(
    nation: str | None = None,
    tier: str | None = None,
    season: str | None = None,
) -> None:
    if nation is None and tier is None and season is None:
        _TEAM_PROFILE_MEMORY_CACHE.clear()
        return

    prefix = (
        None,
        str(nation or ""),
        str(tier or ""),
        str(season or ""),
    )
    for key in list(_TEAM_PROFILE_MEMORY_CACHE.keys()):
        if key[1:4] == prefix[1:4]:
            _TEAM_PROFILE_MEMORY_CACHE.pop(key, None)


def _team_profile_memory_key(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    team: str,
    manifest_fingerprint: str,
) -> tuple[str, str, str, str, str, str]:
    return (
        _normalised_path_key(basedir),
        str(nation),
        str(tier),
        str(season),
        _norm_team_name(team),
        manifest_fingerprint,
    )


def _remember_team_profile_payload(key: tuple[str, str, str, str, str, str], payload: dict[str, Any]) -> None:
    if key in _TEAM_PROFILE_MEMORY_CACHE:
        _TEAM_PROFILE_MEMORY_CACHE.pop(key, None)
    _TEAM_PROFILE_MEMORY_CACHE[key] = copy.deepcopy(payload)
    overflow = max(0, len(_TEAM_PROFILE_MEMORY_CACHE) - TEAM_PROFILE_MEMORY_LIMIT)
    for old_key in list(_TEAM_PROFILE_MEMORY_CACHE.keys())[:overflow]:
        _TEAM_PROFILE_MEMORY_CACHE.pop(old_key, None)


def _load_team_profile_from_parquet(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    team: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    manifest = _read_json_dict(_team_analysis_manifest_path(basedir, nation, tier, season)) or {}
    paths = _team_analysis_cache_frame_paths(basedir, nation, tier, season)
    club_profiles_path = paths["club_profiles"]
    club_profiles = _read_team_analysis_parquet_frame(club_profiles_path)
    if club_profiles.empty:
        return None, {"manifest": manifest, "club_profiles_rows": 0, "profile_store_path": str(_team_analysis_cache_root(basedir, nation, tier, season))}

    team_key = _norm_team_name(team)
    if "team_key" in club_profiles.columns:
        match = club_profiles.loc[club_profiles["team_key"].astype(str).eq(team_key)].copy()
    elif "team" in club_profiles.columns:
        match = club_profiles.loc[club_profiles["team"].map(_norm_team_name).eq(team_key)].copy()
    else:
        match = pd.DataFrame()

    if match.empty:
        return None, {
            "manifest": manifest,
            "club_profiles_rows": int(len(club_profiles)),
            "profile_store_path": str(_team_analysis_cache_root(basedir, nation, tier, season)),
        }

    row = match.iloc[0].to_dict()
    payload = _json_loads_cache_value(row.get("payload_json"))
    if not isinstance(payload, dict):
        payload = {
            "team": row.get("team", team),
            "nation": nation,
            "tier": tier,
            "season": season,
            "overview": _json_loads_cache_value(row.get("overview_json")) or {},
            "style_tags": _json_loads_cache_value(row.get("style_tags_json")) or [],
            "match_log": _json_loads_cache_value(row.get("match_log_json")) or [],
            "attacking_profile": _json_loads_cache_value(row.get("attacking_profile_json")) or {},
            "defensive_profile": _json_loads_cache_value(row.get("defensive_profile_json")) or {},
            "transition_profile": _json_loads_cache_value(row.get("transition_profile_json")) or {},
            "set_piece_profile": _json_loads_cache_value(row.get("set_piece_profile_json")) or {},
            "metric_radar": _json_loads_cache_value(row.get("metric_radar_json")) or [],
            "phase_radar_groups": _json_loads_cache_value(row.get("phase_radar_groups_json")) or [],
            "phase_kpi_breakdowns": _json_loads_cache_value(row.get("phase_kpi_breakdowns_json")) or {},
            "common_lineup": _json_loads_cache_value(row.get("common_lineup_json")) or {},
            "player_influence_dashboard": _json_loads_cache_value(row.get("player_influence_dashboard_json")) or {},
            "multi_season_profile": _json_loads_cache_value(row.get("multi_season_profile_json")) or {},
            "player_profile": _json_loads_cache_value(row.get("player_profile_json")) or {},
            "data_quality": _json_loads_cache_value(row.get("data_quality_json")) or {},
        }

    return payload, {
        "manifest": manifest,
        "club_profiles_rows": int(len(club_profiles)),
        "profile_store_path": str(_team_analysis_cache_root(basedir, nation, tier, season)),
    }


def _build_team_summary_from_profile_cache(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    team: str,
    *,
    parquet_rebuilt: bool = False,
) -> dict[str, Any] | None:
    manifest = _read_json_dict(_team_analysis_manifest_path(basedir, nation, tier, season)) or {}
    manifest_fingerprint = _team_analysis_manifest_fingerprint(manifest)
    source_fingerprint = str(manifest.get("source_fingerprint") or "")
    memory_key = _team_profile_memory_key(basedir, nation, tier, season, team, manifest_fingerprint)

    if memory_key in _TEAM_PROFILE_MEMORY_CACHE:
        payload = copy.deepcopy(_TEAM_PROFILE_MEMORY_CACHE[memory_key])
        render_meta = payload.get("render_meta") if isinstance(payload.get("render_meta"), dict) else {}
        render_meta.update(
            {
                "cache_hit": True,
                "memory_cache_hit": True,
                "parquet_profile_hit": True,
                "parquet_rebuilt": bool(parquet_rebuilt),
                "load_mode": "team_analysis_memory_profile",
                "cache_version": TEAM_ANALYSIS_PROCESSED_CACHE_VERSION,
                "profile_store_path": str(_team_analysis_cache_root(basedir, nation, tier, season)),
                "club_profiles_rows": int(manifest.get("row_counts", {}).get("club_profiles") or 0),
                "source_fingerprint": source_fingerprint,
            }
        )
        payload["render_meta"] = render_meta
        return payload

    payload, meta = _load_team_profile_from_parquet(basedir, nation, tier, season, team)
    if payload is None:
        return None

    render_meta = payload.get("render_meta") if isinstance(payload.get("render_meta"), dict) else {}
    render_meta.update(
        {
            "cache_hit": True,
            "memory_cache_hit": False,
            "parquet_profile_hit": True,
            "parquet_rebuilt": bool(parquet_rebuilt),
            "load_mode": "team_analysis_parquet_profile",
            "cache_version": TEAM_ANALYSIS_PROCESSED_CACHE_VERSION,
            "profile_store_path": str(meta.get("profile_store_path") or _team_analysis_cache_root(basedir, nation, tier, season)),
            "club_profiles_rows": int(meta.get("club_profiles_rows") or 0),
            "source_fingerprint": source_fingerprint,
        }
    )
    payload["render_meta"] = render_meta
    _remember_team_profile_payload(memory_key, payload)
    return copy.deepcopy(payload)


def rebuild_team_analysis_profile_store(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    *,
    force: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    result = _build_team_analysis_parquet_store(basedir, nation, tier, season, force=force)
    _clear_team_profile_memory_cache(nation=nation, tier=tier, season=season)
    result["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    result["cache_version"] = TEAM_ANALYSIS_PROCESSED_CACHE_VERSION
    result["profile_store_path"] = str(_team_analysis_cache_root(basedir, nation, tier, season))
    return _json_safe_cache_value(result)


def _filter_metric_frame_by_match_ids(frame: pd.DataFrame, match_ids: list[int]) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty or not match_ids:
        return pd.DataFrame(columns=frame.columns if isinstance(frame, pd.DataFrame) else [])
    match_col = _match_id_col(frame) or ("match_id" if "match_id" in frame.columns else "")
    if not match_col:
        return frame.copy()
    ids = pd.to_numeric(frame[match_col], errors="coerce")
    return frame.loc[ids.isin(set(int(match_id) for match_id in match_ids))].copy()


def _aggregate_player_xt_from_valued_actions(valued_actions: pd.DataFrame) -> pd.DataFrame:
    identity_cols = ["nation", "team_folder", "season", "team_id", "team", "player_id", "player"]
    if not isinstance(valued_actions, pd.DataFrame) or valued_actions.empty:
        return pd.DataFrame(columns=identity_cols + ["xt_raw"])

    work = valued_actions.copy()
    for col in identity_cols:
        if col not in work.columns:
            work[col] = ""

    if "xt_added" not in work.columns:
        work["xt_added"] = 0.0
    work["xt_added"] = pd.to_numeric(work["xt_added"], errors="coerce").fillna(0.0)
    set_piece = _bool_series(work, "is_set_piece_action")
    action_type = _text_series(work, "action_type").str.lower()
    work["open_play_xt_value"] = work["xt_added"].where(~set_piece, 0.0)
    work["set_piece_xt_value"] = work["xt_added"].where(set_piece, 0.0)
    work["pass_xt_value"] = work["xt_added"].where((~set_piece) & action_type.eq("pass"), 0.0)
    work["cross_xt_value"] = work["xt_added"].where((~set_piece) & action_type.eq("cross"), 0.0)
    work["carry_xt_value"] = work["xt_added"].where((~set_piece) & action_type.eq("carry"), 0.0)

    grouped = (
        work.groupby(identity_cols, dropna=False)
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
    grouped["expected_threat_raw"] = grouped["xt_raw"]
    return grouped


def _selected_metric_context_from_processed_cache(
    processed_cache: dict[str, Any],
    selected_match_df: pd.DataFrame,
    match_ids: list[int],
) -> dict[str, Any] | None:
    frames = processed_cache.get("frames") if isinstance(processed_cache, dict) else None
    if not isinstance(frames, dict):
        return None

    league_scored = frames.get("xg", pd.DataFrame())
    league_xa_links = frames.get("xa", pd.DataFrame())
    league_xt_actions = frames.get("xt", pd.DataFrame())

    if not any(isinstance(frame, pd.DataFrame) and not frame.empty for frame in [league_scored, league_xa_links, league_xt_actions]):
        return None

    selected_scored = _filter_metric_frame_by_match_ids(league_scored, match_ids) if isinstance(league_scored, pd.DataFrame) else pd.DataFrame()
    selected_xa_links = _filter_metric_frame_by_match_ids(league_xa_links, match_ids) if isinstance(league_xa_links, pd.DataFrame) else pd.DataFrame()
    selected_xt_actions = _filter_metric_frame_by_match_ids(league_xt_actions, match_ids) if isinstance(league_xt_actions, pd.DataFrame) else pd.DataFrame()

    metric_quality = processed_cache.get("metric_quality") if isinstance(processed_cache.get("metric_quality"), dict) else {}
    return {
        "team_scored_shots": pd.DataFrame(),
        "league_scored_shots": league_scored if isinstance(league_scored, pd.DataFrame) else pd.DataFrame(),
        "selected_scored_shots": selected_scored,
        "team_xa_links": pd.DataFrame(),
        "league_xa_links": league_xa_links if isinstance(league_xa_links, pd.DataFrame) else pd.DataFrame(),
        "selected_xa_links": selected_xa_links,
        "team_xt_actions": pd.DataFrame(),
        "league_xt_actions": league_xt_actions if isinstance(league_xt_actions, pd.DataFrame) else pd.DataFrame(),
        "selected_xt_actions": selected_xt_actions,
        "player_xg": aggregate_player_xg(selected_scored) if not selected_scored.empty else pd.DataFrame(),
        "player_xa": aggregate_player_xa(selected_xa_links) if not selected_xa_links.empty else pd.DataFrame(),
        "player_xt": _aggregate_player_xt_from_valued_actions(selected_xt_actions),
        "xg_quality": metric_quality.get("xg_quality", _score_model_quality("xG", "cached", "xG loaded from Team Analysis processed cache.")),
        "xa_quality": metric_quality.get("xa_quality", _score_model_quality("xA", "cached", "xA loaded from Team Analysis processed cache.")),
        "xt_quality": metric_quality.get("xt_quality", _score_model_quality("xT", "cached", "xT loaded from Team Analysis processed cache.")),
    }



def _event_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    type_l = _text_series(df, "type_l").str.lower()
    if type_l.str.strip().eq("").all():
        type_l = _text_series(df, "type").str.lower()
    outcome_l = _text_series(df, "outcome_l").str.lower()
    if outcome_l.str.strip().eq("").all():
        outcome_l = _text_series(df, "outcome_type").str.lower()
    qual_l = _text_series(df, "qual_tags").str.lower()

    x = _number_series(df, "x_120")
    if x.eq(0).all() and "x" in df.columns:
        x = _number_series(df, "x") * (PITCH_LENGTH / 100.0)
    y = _number_series(df, "y_80")
    if y.eq(0).all() and "y" in df.columns:
        y = _number_series(df, "y") * (PITCH_WIDTH / 100.0)
    end_x = _number_series(df, "end_x_120")
    if end_x.eq(0).all() and "end_x" in df.columns:
        end_x = _number_series(df, "end_x") * (PITCH_LENGTH / 100.0)
    end_y = _number_series(df, "end_y_80")
    if end_y.eq(0).all() and "end_y" in df.columns:
        end_y = _number_series(df, "end_y") * (PITCH_WIDTH / 100.0)

    is_goal = _bool_series(df, "is_goal") | type_l.eq("goal") | outcome_l.str.contains("goal", na=False)
    is_shot = _bool_series(df, "is_shot_event") | _bool_series(df, "is_shot") | is_goal | type_l.str.contains("shot", na=False)
    is_on_target = is_shot & (
        is_goal
        | outcome_l.str.contains("on target|ontarget|saved|save", regex=True, na=False)
        | type_l.str.contains("saved", na=False)
    )
    is_pass = _bool_series(df, "is_pass_like") | type_l.str.contains("pass", na=False) | type_l.eq("cross")
    is_cross = type_l.str.contains("cross", na=False) | qual_l.str.contains("cross", na=False)
    is_carry = _bool_series(df, "is_carry") | type_l.str.contains("carry|dribble|take on|takeon|run", regex=True, na=False)
    is_move = is_pass | is_carry | is_cross
    successful = _bool_series(df, "successful") | outcome_l.str.contains("successful|success|complete|completed|accurate|won", regex=True, na=False)
    is_defensive = _bool_series(df, "is_defensive_action") | type_l.str.contains("tackle|interception|clearance|block|recovery|challenge|aerial|duel|foul", regex=True, na=False)
    is_set_piece = qual_l.str.contains("corner|free kick|freekick|throwin|throw in|penalty", regex=True, na=False) | type_l.str.contains("corner|free kick|freekick|penalty", regex=True, na=False)
    is_corner = qual_l.str.contains("corner", na=False) | type_l.str.contains("corner", na=False)
    is_free_kick = qual_l.str.contains("free kick|freekick", regex=True, na=False) | type_l.str.contains("free kick|freekick", regex=True, na=False)
    is_throw_in = qual_l.str.contains("throwin|throw in", regex=True, na=False) | type_l.str.contains("throwin|throw in", regex=True, na=False)

    start_in_box = x.ge(BOX_X) & y.between(BOX_Y_MIN, BOX_Y_MAX, inclusive="both")
    end_in_box = end_x.ge(BOX_X) & end_y.between(BOX_Y_MIN, BOX_Y_MAX, inclusive="both")
    final_third_entry = is_move & end_x.ge(FINAL_THIRD_X) & x.lt(FINAL_THIRD_X)
    box_entry = is_move & end_in_box & ~start_in_box
    high_regain = is_defensive & x.ge(72.0)
    wide_action = (y.le(PITCH_WIDTH / 3.0) | y.ge(PITCH_WIDTH * 2.0 / 3.0)) & is_move
    central_action = y.between(PITCH_WIDTH / 3.0, PITCH_WIDTH * 2.0 / 3.0, inclusive="both") & is_move

    return {
        "x": x,
        "y": y,
        "end_x": end_x,
        "end_y": end_y,
        "is_goal": is_goal,
        "is_shot": is_shot,
        "is_on_target": is_on_target,
        "is_pass": is_pass,
        "is_cross": is_cross,
        "is_carry": is_carry,
        "is_move": is_move,
        "successful": successful,
        "is_defensive": is_defensive,
        "is_set_piece": is_set_piece,
        "is_corner": is_corner,
        "is_free_kick": is_free_kick,
        "is_throw_in": is_throw_in,
        "final_third_entry": final_third_entry,
        "box_entry": box_entry,
        "high_regain": high_regain,
        "wide_action": wide_action,
        "central_action": central_action,
        "start_in_box": start_in_box,
        "end_in_box": end_in_box,
    }


def _lane_key(y_value: float) -> str:
    if y_value < PITCH_WIDTH / 3.0:
        return "left"
    if y_value <= PITCH_WIDTH * 2.0 / 3.0:
        return "central"
    return "right"


def _lane_label(key: str) -> str:
    return {"left": "Left lane", "central": "Central lane", "right": "Right lane"}.get(key, key.title())


def _lane_range_pct(key: str) -> tuple[float, float]:
    if key == "left":
        return 0.0, 33.333
    if key == "central":
        return 33.333, 66.667
    return 66.667, 100.0


def _lane_summary(df: pd.DataFrame, mask: pd.Series, value_mask: pd.Series | None = None) -> list[dict[str, Any]]:
    if df.empty:
        return []
    y = _event_masks(df)["y"]
    total = int(mask.sum())
    rows: list[dict[str, Any]] = []
    for key in ["left", "central", "right"]:
        lane_mask = mask & y.map(_lane_key).eq(key)
        y_min, y_max = _lane_range_pct(key)
        rows.append(
            {
                "lane": key,
                "label": _lane_label(key),
                "count": int(lane_mask.sum()),
                "share_pct": _pct(int(lane_mask.sum()), max(total, 1)),
                "final_third_entries": int((lane_mask & _event_masks(df)["final_third_entry"]).sum()),
                "box_entries": int((lane_mask & _event_masks(df)["box_entry"]).sum()),
                "shots": int((lane_mask & _event_masks(df)["is_shot"]).sum()),
                "value": int((lane_mask & value_mask).sum()) if value_mask is not None else int(lane_mask.sum()),
                "y_min": y_min,
                "y_max": y_max,
            }
        )
    return rows


def _pitch_points(df: pd.DataFrame, mask: pd.Series, limit: int = 220) -> list[dict[str, Any]]:
    if df.empty or not mask.any():
        return []
    sample = df.loc[mask].copy().head(limit)
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(sample.to_dict(orient="records")):
        x_120 = _clean_number(row.get("x_120"))
        y_80 = _clean_number(row.get("y_80"))
        end_x_120 = _clean_number(row.get("end_x_120"))
        end_y_80 = _clean_number(row.get("end_y_80"))

        if x_120 is None or y_80 is None:
            x_100 = _clean_number(row.get("x"))
            y_100 = _clean_number(row.get("y"))
        else:
            x_100 = round(float(x_120) * (100.0 / PITCH_LENGTH), 3)
            y_100 = round(float(y_80) * (100.0 / PITCH_WIDTH), 3)

        if end_x_120 is None or end_y_80 is None:
            end_x_100 = _clean_number(row.get("end_x"))
            end_y_100 = _clean_number(row.get("end_y"))
        else:
            end_x_100 = round(float(end_x_120) * (100.0 / PITCH_LENGTH), 3)
            end_y_100 = round(float(end_y_80) * (100.0 / PITCH_WIDTH), 3)

        if x_100 is None or y_100 is None:
            continue

        rows.append(
            {
                "event_index": row.get("event_index", index),
                "match_id": row.get("match_id"),
                "minute": row.get("minute"),
                "second": row.get("second"),
                "team": row.get("team"),
                "player": row.get("player"),
                "type": row.get("type"),
                "event_type": row.get("type"),
                "outcome_type": row.get("outcome_type"),
                "qual_tags": row.get("qual_tags"),
                "x": x_100,
                "y": y_100,
                "end_x": end_x_100,
                "end_y": end_y_100,
                "is_goal": row.get("is_goal"),
                "is_shot": row.get("is_shot_event", row.get("is_shot")),
                "xg": _clean_number(row.get("xg")),
                "xa": _clean_number(row.get("xa")),
                "assisted_shot_xg": _clean_number(row.get("assisted_shot_xg_raw", row.get("assisted_shot_xg"))),
                "xt_added": _clean_number(row.get("xt_added")),
                "threat_value": _clean_number(row.get("threat_value")),
                "led_to_shot": row.get("led_to_shot"),
                "led_to_goal": row.get("led_to_goal"),
                "action_type": row.get("action_type"),
                "shot_family": row.get("shot_family"),
            }
        )
    return rows


def _heatmap(df: pd.DataFrame, mask: pd.Series, x_bins: int = 6, y_bins: int = 5) -> dict[str, Any]:
    if df.empty or not mask.any():
        return {"x_bins": x_bins, "y_bins": y_bins, "cells": []}
    masks = _event_masks(df)
    x_pct = (masks["x"] * (100.0 / PITCH_LENGTH)).clip(0, 99.999)
    y_pct = (masks["y"] * (100.0 / PITCH_WIDTH)).clip(0, 99.999)
    work = pd.DataFrame({"x_bin": (x_pct / (100 / x_bins)).astype(int), "y_bin": (y_pct / (100 / y_bins)).astype(int)})
    work = work.loc[mask]
    cells = (
        work.groupby(["x_bin", "y_bin"], dropna=False)
        .size()
        .reset_index(name="count")
        .assign(value=lambda frame: frame["count"])
        .to_dict(orient="records")
    )
    return {"x_bins": x_bins, "y_bins": y_bins, "cells": cells}


def _group_players(df: pd.DataFrame, masks: dict[str, pd.Series]) -> list[dict[str, Any]]:
    player_col = _player_col(df)
    if df.empty or not player_col:
        return []

    xg_values = pd.Series(0.0, index=df.index, dtype="float64")
    for col in ["xg", "xG", "expected_goals", "expectedGoals"]:
        if col in df.columns:
            xg_values = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
            break

    xt_values = pd.Series(0.0, index=df.index, dtype="float64")
    for col in ["xt_added", "xT", "xt", "expected_threat", "expectedThreat"]:
        if col in df.columns:
            xt_values = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
            break

    xa_values = pd.Series(0.0, index=df.index, dtype="float64")
    for col in ["xa", "xA", "expected_assists", "expectedAssists"]:
        if col in df.columns:
            xa_values = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
            break

    match_col = _match_id_col(df)
    match_values = pd.to_numeric(df[match_col], errors="coerce").fillna(0).astype(int) if match_col and match_col in df.columns else pd.Series(0, index=df.index, dtype="int64")

    work = pd.DataFrame(
        {
            "player": df[player_col].astype(str).replace({"": "Unknown"}),
            "match_id": match_values,
            "events": 1,
            "shots": masks["is_shot"].astype(int),
            "goals": masks["is_goal"].astype(int),
            "passes": masks["is_pass"].astype(int),
            "carries": masks["is_carry"].astype(int),
            "crosses": masks["is_cross"].astype(int),
            "final_third_entries": masks["final_third_entry"].astype(int),
            "box_entries": masks["box_entry"].astype(int),
            "defensive_actions": masks["is_defensive"].astype(int),
            "high_regains": masks["high_regain"].astype(int),
            "set_piece_involvement": masks["is_set_piece"].astype(int),
            "corner_involvement": masks["is_corner"].astype(int),
            "free_kick_involvement": masks["is_free_kick"].astype(int),
            "throw_in_involvement": masks["is_throw_in"].astype(int),
            "progressive_actions": (masks["final_third_entry"] | masks["box_entry"]).astype(int),
            "xg": xg_values,
            "xa": xa_values,
            "xt": xt_values,
        }
    )
    grouped = (
        work.groupby("player", dropna=False)
        .agg(
            events=("events", "sum"),
            matches_involved=("match_id", "nunique"),
            shots=("shots", "sum"),
            goals=("goals", "sum"),
            passes=("passes", "sum"),
            carries=("carries", "sum"),
            crosses=("crosses", "sum"),
            final_third_entries=("final_third_entries", "sum"),
            box_entries=("box_entries", "sum"),
            defensive_actions=("defensive_actions", "sum"),
            high_regains=("high_regains", "sum"),
            set_piece_involvement=("set_piece_involvement", "sum"),
            corner_involvement=("corner_involvement", "sum"),
            free_kick_involvement=("free_kick_involvement", "sum"),
            throw_in_involvement=("throw_in_involvement", "sum"),
            progressive_actions=("progressive_actions", "sum"),
            xg=("xg", "sum"),
            xa=("xa", "sum"),
            xt=("xt", "sum"),
        )
        .reset_index()
    )
    grouped["involvement_score"] = (
        grouped["events"] * 0.08
        + grouped["final_third_entries"] * 1.15
        + grouped["box_entries"] * 1.65
        + grouped["shots"] * 2.05
        + grouped["goals"] * 4.0
        + grouped["defensive_actions"] * 0.55
        + grouped["high_regains"] * 1.35
        + grouped["set_piece_involvement"] * 0.35
        + grouped["xg"] * 1.6
        + grouped["xa"] * 1.6
        + grouped["xt"] * 1.25
    )
    grouped = grouped.sort_values("involvement_score", ascending=False).reset_index(drop=True)
    grouped["ranking_within_team"] = grouped.index + 1
    return grouped.round({"involvement_score": 2, "xg": 3, "xa": 3, "xt": 3}).to_dict(orient="records")


def _top_from_players(players: list[dict[str, Any]], key: str, limit: int = 8) -> list[dict[str, Any]]:
    return sorted(players, key=lambda item: _safe_float(item.get(key), 0.0), reverse=True)[:limit]


def _style_tags(masks: dict[str, pd.Series], match_count: int) -> list[str]:
    events = max(len(masks["is_shot"]), 1)
    moves = max(int(masks["is_move"].sum()), 1)
    defensive = max(int(masks["is_defensive"].sum()), 1)
    final_third_entries = int(masks["final_third_entry"].sum())
    box_entries = int(masks["box_entry"].sum())
    high_regains = int(masks["high_regain"].sum())
    set_piece_shots = int((masks["is_set_piece"] & masks["is_shot"]).sum())
    wide_share = _pct(int(masks["wide_action"].sum()), moves)
    central_share = _pct(int(masks["central_action"].sum()), moves)
    pass_share = _pct(int(masks["is_pass"].sum()), events)
    defensive_height = float(masks["x"].loc[masks["is_defensive"]].mean()) if defensive else 0.0

    tags: list[str] = []
    if pass_share >= 48:
        tags.append("Possession heavy")
    if final_third_entries and box_entries / max(final_third_entries, 1) >= 0.42:
        tags.append("Direct")
    if wide_share >= 52:
        tags.append("Wing focused")
    if central_share >= 38:
        tags.append("Central progression")
    if high_regains >= max(6, match_count * 2):
        tags.append("High pressing")
    if high_regains >= max(4, match_count) and box_entries >= max(3, match_count):
        tags.append("Transition threat")
    if set_piece_shots >= max(2, match_count):
        tags.append("Set piece threat")
    if defensive_height and defensive_height < 45 and high_regains < max(4, match_count):
        tags.append("Deep defending")
    return tags[:7]


def _match_log(team_df: pd.DataFrame, masks: dict[str, pd.Series], basedir: Path, nation: str, tier: str, season: str, team: str) -> list[dict[str, Any]]:
    match_col = _match_id_col(team_df)
    if team_df.empty or not match_col:
        return []

    schedule_df = pd.DataFrame()
    try:
        schedule_df = load_schedule_frame(basedir, nation, tier, season)
    except Exception:
        schedule_df = pd.DataFrame()

    schedule_by_id: dict[int, dict[str, Any]] = {}
    home_col, away_col = _home_away_cols(schedule_df) if not schedule_df.empty else (None, None)
    schedule_match_col = _match_id_col(schedule_df) if not schedule_df.empty else None
    if schedule_match_col:
        for item in schedule_df.to_dict(orient="records"):
            raw_match_id = _clean_number(item.get(schedule_match_col))
            if raw_match_id is not None:
                schedule_by_id[int(raw_match_id)] = item

    rows: list[dict[str, Any]] = []
    match_ids = pd.to_numeric(team_df[match_col], errors="coerce")
    for raw_match_id, group in team_df.assign(__match_id_numeric=match_ids).dropna(subset=["__match_id_numeric"]).groupby("__match_id_numeric", dropna=False):
        match_id = int(raw_match_id)
        idx = group.index
        schedule_row = schedule_by_id.get(match_id, {})
        home_team = str(schedule_row.get(home_col, "") if home_col else "").strip()
        away_team = str(schedule_row.get(away_col, "") if away_col else "").strip()
        team_key = _norm_team_name(team)
        home_away = ""
        opponent = ""
        if home_team or away_team:
            if _norm_team_name(home_team) == team_key:
                home_away = "Home"
                opponent = away_team
            elif _norm_team_name(away_team) == team_key:
                home_away = "Away"
                opponent = home_team

        home_score = _clean_number(schedule_row.get("home_score"))
        away_score = _clean_number(schedule_row.get("away_score"))
        score = ""
        result = ""
        if home_score is not None and away_score is not None:
            score = f"{home_score} {away_score}"
            team_score = home_score if home_away == "Home" else away_score if home_away == "Away" else None
            opponent_score = away_score if home_away == "Home" else home_score if home_away == "Away" else None
            if team_score is not None and opponent_score is not None:
                result = "W" if team_score > opponent_score else "D" if team_score == opponent_score else "L"

        date_value = ""
        for date_col in ["date", "start_time", "startTime", "utcDate", "kickoff", "time"]:
            if date_col in schedule_row and str(schedule_row.get(date_col, "")).strip():
                date_value = str(schedule_row.get(date_col, "")).strip()
                break

        rows.append(
            {
                "match_id": match_id,
                "date": date_value,
                "opponent": opponent,
                "home_away": home_away,
                "score": score,
                "result": result,
                "events": int(len(group)),
                "shots": int(masks["is_shot"].loc[idx].sum()),
                "box_entries": int(masks["box_entry"].loc[idx].sum()),
                "final_third_entries": int(masks["final_third_entry"].loc[idx].sum()),
                "defensive_actions": int(masks["is_defensive"].loc[idx].sum()),
                "set_piece_events": int(masks["is_set_piece"].loc[idx].sum()),
            }
        )

    return sorted(rows, key=lambda item: str(item.get("date") or item.get("match_id") or ""), reverse=True)


def _fast_attacks_after_regains(df: pd.DataFrame, masks: dict[str, pd.Series]) -> int:
    match_col = _match_id_col(df)
    if df.empty or not match_col or "expanded_minute" not in df.columns:
        return 0
    work = df.copy()
    work["__expanded"] = pd.to_numeric(work["expanded_minute"], errors="coerce")
    work["__match"] = pd.to_numeric(work[match_col], errors="coerce")
    work["__danger"] = (masks["final_third_entry"] | masks["box_entry"] | masks["is_shot"]).astype(bool)
    work["__high_regain"] = masks["high_regain"].astype(bool)
    total = 0
    for _match_id, group in work.dropna(subset=["__match", "__expanded"]).sort_values(["__match", "__expanded"]).groupby("__match", dropna=False):
        regain_times = group.loc[group["__high_regain"], "__expanded"].tolist()
        danger_times = group.loc[group["__danger"], "__expanded"].tolist()
        for regain_time in regain_times:
            if any(0 < float(danger_time) - float(regain_time) <= 0.25 for danger_time in danger_times):
                total += 1
    return int(total)


def _match_ids_from_frame(df: pd.DataFrame) -> list[int]:
    match_col = _match_id_col(df)
    if df.empty or not match_col:
        return []
    ids = pd.to_numeric(df[match_col], errors="coerce").dropna().astype(int).tolist()
    return sorted(dict.fromkeys(ids))


def _filter_matches(df: pd.DataFrame, match_ids: list[int]) -> pd.DataFrame:
    match_col = _match_id_col(df)
    if df.empty or not match_col or not match_ids:
        return pd.DataFrame(columns=df.columns)
    match_id_set = set(int(item) for item in match_ids)
    mask = pd.to_numeric(df[match_col], errors="coerce").isin(match_id_set)
    return df.loc[mask].copy()


def _drop_duplicate_event_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.reset_index(drop=True)
    dedupe_exclude = {"qual_tags", "qual_map"}
    dedupe_cols = [
        col
        for col in df.columns
        if col not in dedupe_exclude and not df[col].map(lambda value: isinstance(value, (list, dict, set))).any()
    ]
    preferred = [
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
    subset = [col for col in preferred if col in dedupe_cols]
    if len(subset) >= 4:
        return df.drop_duplicates(subset=subset).reset_index(drop=True)
    if dedupe_cols:
        return df.drop_duplicates(subset=dedupe_cols).reset_index(drop=True)
    return df.reset_index(drop=True)


def _source_files_for_scope(basedir: Path, nation: str, tier: str, season: str, direct_path: str = "") -> list[str]:
    events_root = _events_root(basedir)
    paths = [path for path, _source_team in _iter_team_season_files(events_root, nation, tier, season)]
    if direct_path and direct_path not in {"season event store", "direct team file unavailable"}:
        paths.append(Path(direct_path))
    paths.extend(_schedule_source_paths(basedir, nation, tier, season))
    existing = [path for path in _unique_paths(paths) if path.exists() and path.is_file()]
    return [str(path) for path in existing]


def _season_match_context(
    *,
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    team: str,
    team_df: pd.DataFrame,
    source_df: pd.DataFrame,
    season_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    selected_match_ids = _match_ids_from_frame(team_df)
    context_notes: list[str] = []
    frames: list[pd.DataFrame] = []
    source_modes: list[str] = []

    if not selected_match_ids:
        return {
            "team_events": team_df.reset_index(drop=True),
            "opponent_events": pd.DataFrame(columns=team_df.columns),
            "all_selected_matches_events": team_df.reset_index(drop=True),
            "match_ids": [],
            "matches_with_opponent_rows": [],
            "matches_without_opponent_rows": [],
            "source_modes": [],
            "notes": ["No match ids were available in the selected team rows, so opponent context could not be resolved."],
        }

    if not source_df.empty:
        scoped_source = _filter_matches(normalise_event_frame(source_df), selected_match_ids)
        if not scoped_source.empty:
            frames.append(scoped_source)
            source_modes.append("selected_source_frame")

    cached_season_df = season_df if isinstance(season_df, pd.DataFrame) and not season_df.empty else pd.DataFrame()
    if not cached_season_df.empty:
        scoped_season = _filter_matches(normalise_event_frame(cached_season_df), selected_match_ids)
        if not scoped_season.empty:
            frames.append(scoped_season)
            source_modes.append("cached_full_season_event_store")
    else:
        try:
            loaded_season_df = load_season_events(basedir, nation, tier, season)
            scoped_season = _filter_matches(loaded_season_df, selected_match_ids)
            if not scoped_season.empty:
                frames.append(scoped_season)
                source_modes.append("full_season_event_store")
        except Exception as exc:
            context_notes.append(f"Full season event store was not available for opponent context: {type(exc).__name__}: {exc}")

    combined = _drop_duplicate_event_rows(pd.concat(frames, ignore_index=True, sort=False)) if frames else normalise_event_frame(team_df)
    combined_team_mask = _team_filter(combined, team)
    opponent_df = combined.loc[~combined_team_mask].copy() if not combined.empty else pd.DataFrame(columns=combined.columns)
    opponent_match_ids = _match_ids_from_frame(opponent_df)
    missing_opponent_match_ids = [match_id for match_id in selected_match_ids if match_id not in set(opponent_match_ids)]

    if missing_opponent_match_ids:
        fallback_frames: list[pd.DataFrame] = []
        for match_id in missing_opponent_match_ids:
            try:
                match_events, _fixture = load_match_events(basedir, nation, tier, season, int(match_id))
            except Exception as exc:
                context_notes.append(f"Match {match_id} opponent fallback failed from saved files: {type(exc).__name__}: {exc}")
                continue
            if not match_events.empty:
                fallback_frames.append(match_events)
        if fallback_frames:
            frames.extend(fallback_frames)
            source_modes.append("saved_match_event_fallback")
            combined = _drop_duplicate_event_rows(pd.concat(frames, ignore_index=True, sort=False))

    final_team_mask = _team_filter(combined, team)
    team_events = combined.loc[final_team_mask].copy() if not combined.empty else normalise_event_frame(team_df)
    opponent_events = combined.loc[~final_team_mask].copy() if not combined.empty else pd.DataFrame(columns=combined.columns)
    match_col = _match_id_col(opponent_events)
    if match_col:
        opponent_ids = pd.to_numeric(opponent_events[match_col], errors="coerce")
        matches_with_opponent_rows = sorted(opponent_ids.dropna().astype(int).unique().tolist())
    else:
        matches_with_opponent_rows = []
    matches_without_opponent_rows = [match_id for match_id in selected_match_ids if match_id not in set(matches_with_opponent_rows)]

    if not opponent_events.empty:
        context_notes.append("Opponent rows were resolved from saved season or opponent team files for the selected matches.")
    elif selected_match_ids:
        context_notes.append("No opponent rows were found in saved season files, opponent direct team files, or saved match file fallbacks for the selected team matches.")

    return {
        "team_events": team_events.reset_index(drop=True),
        "opponent_events": opponent_events.reset_index(drop=True),
        "all_selected_matches_events": combined.reset_index(drop=True),
        "match_ids": selected_match_ids,
        "matches_with_opponent_rows": matches_with_opponent_rows,
        "matches_without_opponent_rows": matches_without_opponent_rows,
        "source_modes": sorted(dict.fromkeys(source_modes)),
        "notes": context_notes,
    }


def _flip_visual_coordinates(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    flip_specs = [
        ("x", 100.0),
        ("y", 100.0),
        ("end_x", 100.0),
        ("end_y", 100.0),
        ("blocked_x", 100.0),
        ("blocked_y", 100.0),
        ("x_120", PITCH_LENGTH),
        ("end_x_120", PITCH_LENGTH),
        ("y_80", PITCH_WIDTH),
        ("end_y_80", PITCH_WIDTH),
    ]
    for col, upper in flip_specs:
        if col in out.columns:
            values = pd.to_numeric(out[col], errors="coerce")
            out[col] = values.where(values.isna(), upper - values)
    out["__visual_direction"] = "flipped_for_conceded_view"
    return out


def _frame_numeric_column(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
    for col in candidates:
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return pd.Series(0.0, index=df.index, dtype="float64")


def _shot_xg_values(df: pd.DataFrame, shot_mask: pd.Series, masks: dict[str, pd.Series]) -> pd.Series:
    for col in ["xg", "xG", "expected_goals", "expectedGoals"]:
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    x = masks["x"].clip(0, PITCH_LENGTH)
    y = masks["y"].clip(0, PITCH_WIDTH)
    distance_to_goal = ((PITCH_LENGTH - x) ** 2 + (PITCH_WIDTH / 2.0 - y) ** 2) ** 0.5
    close_bonus = (1.0 - (distance_to_goal / 92.0)).clip(0.02, 0.38)
    box_bonus = masks["start_in_box"].astype(float) * 0.07
    goal_bonus = masks["is_goal"].astype(float) * 0.18
    estimated = (close_bonus + box_bonus + goal_bonus).clip(0.01, 0.78)
    return estimated.where(shot_mask, 0.0)


def _goalmouth_points(df: pd.DataFrame, mask: pd.Series, limit: int = 180) -> list[dict[str, Any]]:
    if df.empty or not mask.any():
        return []
    if "goal_mouth_y" not in df.columns and "goal_mouth_z" not in df.columns and "goal_mouth_x" not in df.columns:
        return []
    sample = df.loc[mask].copy().head(limit)
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(sample.to_dict(orient="records")):
        mouth_y = _clean_number(row.get("goal_mouth_y"))
        mouth_z = _clean_number(row.get("goal_mouth_z"))
        mouth_x = _clean_number(row.get("goal_mouth_x"))
        if mouth_y is None and mouth_z is None and mouth_x is None:
            continue
        y_value = mouth_y if mouth_y is not None else mouth_x if mouth_x is not None else 50
        z_value = mouth_z if mouth_z is not None else 50
        rows.append(
            {
                "event_index": row.get("event_index", index),
                "match_id": row.get("match_id"),
                "minute": row.get("minute"),
                "team": row.get("team"),
                "player": row.get("player"),
                "type": row.get("type"),
                "event_type": row.get("type"),
                "outcome_type": row.get("outcome_type"),
                "x": 100,
                "y": max(0, min(100, float(y_value))),
                "goal_mouth_y": y_value,
                "goal_mouth_z": z_value,
                "is_goal": row.get("is_goal"),
                "is_shot": row.get("is_shot_event", row.get("is_shot")),
                "xg": _clean_number(row.get("xg")),
                "xa": _clean_number(row.get("xa")),
                "assisted_shot_xg": _clean_number(row.get("assisted_shot_xg_raw", row.get("assisted_shot_xg"))),
                "xt_added": _clean_number(row.get("xt_added")),
                "action_type": row.get("action_type"),
                "shot_family": row.get("shot_family"),
            }
        )
    return rows


def _event_count_by_match(df: pd.DataFrame) -> dict[int, int]:
    match_col = _match_id_col(df)
    if df.empty or not match_col:
        return {}
    work = pd.DataFrame({"match_id": pd.to_numeric(df[match_col], errors="coerce")}).dropna()
    if work.empty:
        return {}
    return {int(match_id): int(count) for match_id, count in work.groupby("match_id").size().items()}


def _candidate_scope_roots(events_root: Path, nation: str, tier: str) -> list[Path]:
    roots = [
        events_root / str(nation or "").strip() / str(tier or "").strip(),
        events_root / _safe_slug(str(nation or "")) / _safe_slug(str(tier or "")),
        events_root / str(nation or "").strip(),
        events_root / _safe_slug(str(nation or "")),
        events_root / f"{str(nation or '').strip()} {str(tier or '').strip()}".strip(),
        events_root / f"{_safe_slug(str(nation or ''))} {_safe_slug(str(tier or ''))}".strip(),
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key and key not in seen:
            seen.add(key)
            out.append(root)
    return out


def _saved_seasons_for_team(basedir: Path, nation: str, tier: str, team: str) -> list[str]:
    seasons: set[str] = set()
    schedule_root = basedir / "data" / "Schedule"
    for folder in [f"{nation} {tier}".strip(), f"{_safe_slug(nation)} {_safe_slug(tier)}".strip()]:
        root = schedule_root / folder
        if root.exists():
            seasons.update(path.stem for path in root.glob("*.csv") if path.is_file() and "backup" not in path.stem.lower())

    events_root = _events_root(basedir)
    team_keys = {_safe_slug(team), str(team or "").strip()}
    for root in _candidate_scope_roots(events_root, nation, tier):
        if not root.exists():
            continue
        seasons.update(path.stem for path in root.glob("*.csv") if path.is_file() and "backup" not in path.stem.lower())
        for team_dir in root.iterdir():
            if not team_dir.is_dir():
                continue
            if team_dir.name in team_keys or _norm_team_name(team_dir.name) == _norm_team_name(team):
                seasons.update(path.stem for path in team_dir.glob("*.csv") if path.is_file() and "backup" not in path.stem.lower())
    return sorted(seasons, reverse=True)


def _lightweight_direct_team_season_summary(
    basedir: Path,
    nation: str,
    tier: str,
    team: str,
    season: str,
) -> dict[str, Any] | None:
    events_root = _events_root(basedir)
    direct_path = _first_existing_direct_team_path(events_root, nation, tier, team, season)
    if direct_path is None:
        return None

    raw = _read_csv(direct_path)
    if raw.empty:
        return None

    normalised = normalise_event_frame(raw.copy())
    team_mask = _team_filter(normalised, team)
    team_df = normalised.loc[team_mask].copy() if team_mask.any() else normalised.copy()
    masks = _event_masks(team_df)
    shot_mask = masks["is_shot"]
    set_piece_mask = masks["is_set_piece"]
    match_ids = _match_ids_from_frame(team_df)
    return {
        "season": season,
        "matches_covered": len(match_ids),
        "goals_for": int(masks["is_goal"].sum()),
        "goals_against": None,
        "shots_for": int(shot_mask.sum()),
        "shots_against": None,
        "box_entries_for": int(masks["box_entry"].sum()),
        "box_entries_against": None,
        "final_third_entries_for": int(masks["final_third_entry"].sum()),
        "final_third_entries_against": None,
        "set_piece_shots_for": int((set_piece_mask & shot_mask).sum()),
        "set_piece_shots_against": None,
        "high_regains": int(masks["high_regain"].sum()),
        "defensive_actions": int(masks["is_defensive"].sum()),
        "opponent_rows_available": False,
        "data_quality_status": "Own team direct file summary only. Opponent rows were not loaded for this comparison row on initial render.",
        "source_path": str(direct_path),
    }


def _build_multi_season_profile(
    *,
    basedir: Path,
    nation: str,
    tier: str,
    team: str,
    current_season: str,
    overview: dict[str, Any],
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    seasons = _saved_seasons_for_team(basedir, nation, tier, team)
    if current_season not in seasons:
        seasons.insert(0, current_season)

    rows: list[dict[str, Any]] = []
    for candidate in seasons[:8]:
        if candidate == current_season:
            rows.append(
                {
                    "season": candidate,
                    "matches_covered": int(overview.get("matches_covered") or 0),
                    "goals_for": overview.get("goals"),
                    "goals_against": overview.get("goals_against"),
                    "shots_for": overview.get("shots"),
                    "shots_against": overview.get("shots_against"),
                    "box_entries_for": overview.get("box_entries"),
                    "box_entries_against": overview.get("box_entries_against"),
                    "final_third_entries_for": overview.get("final_third_entries"),
                    "final_third_entries_against": overview.get("final_third_entries_against"),
                    "set_piece_shots_for": overview.get("set_piece_threat"),
                    "set_piece_shots_against": overview.get("set_piece_shots_against"),
                    "high_regains": overview.get("high_regains"),
                    "defensive_actions": overview.get("defensive_actions"),
                    "opponent_rows_available": bool(data_quality.get("opponent_rows_available")),
                    "data_quality_status": "Full selected season profile from the active summary payload.",
                    "source_path": data_quality.get("source_path", ""),
                }
            )
            continue

        row = _lightweight_direct_team_season_summary(basedir, nation, tier, team, candidate)
        if row is not None:
            rows.append(row)

    note = "No other saved seasons are available for comparison." if len(rows) <= 1 else "Season comparison uses the active season in full and lightweight own team summaries for other saved seasons."
    return {
        "available": len(rows) > 1,
        "rows": rows,
        "note": note,
    }




def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except Exception:
        return default
    if not pd.notna(numeric):
        return default
    return numeric


def _safe_mean(series: pd.Series) -> float | None:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return None
    return float(numeric.mean())


def _sum_numeric(df: pd.DataFrame, col: str) -> float:
    if df.empty or col not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0.0).sum())


def _first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _player_identity_key(row: dict[str, Any]) -> str:
    player_id = str(row.get("player_id") or "").strip()
    if player_id and player_id.lower() not in {"nan", "none", "<na>"}:
        return f"id:{player_id}"
    return f"name:{str(row.get('player') or '').strip().lower()}"


def _merge_player_metric_frame(players: list[dict[str, Any]], frame: pd.DataFrame, mapping: dict[str, str]) -> None:
    if frame.empty or not players:
        return

    metric_by_player: dict[str, dict[str, float]] = {}
    for row in frame.fillna("").to_dict(orient="records"):
        key = _player_identity_key(row)
        if not key or key == "name:":
            continue
        target = metric_by_player.setdefault(key, {})
        for source_col, target_col in mapping.items():
            target[target_col] = target.get(target_col, 0.0) + _safe_float(row.get(source_col), 0.0)

    for player in players:
        key = _player_identity_key(player)
        fallback_key = f"name:{str(player.get('player') or '').strip().lower()}"
        values = metric_by_player.get(key) or metric_by_player.get(fallback_key)
        if not values:
            continue
        for col, value in values.items():
            player[col] = round(value, 3)


def _recalculate_player_rankings(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for player in players:
        player["involvement_score"] = round(
            _safe_float(player.get("events")) * 0.08
            + _safe_float(player.get("final_third_entries")) * 1.15
            + _safe_float(player.get("box_entries")) * 1.65
            + _safe_float(player.get("shots")) * 2.05
            + _safe_float(player.get("goals")) * 4.0
            + _safe_float(player.get("defensive_actions")) * 0.55
            + _safe_float(player.get("high_regains")) * 1.35
            + _safe_float(player.get("set_piece_involvement")) * 0.35
            + _safe_float(player.get("xg")) * 1.6
            + _safe_float(player.get("xa")) * 1.6
            + _safe_float(player.get("xt")) * 1.25,
            2,
        )
    ranked = sorted(players, key=lambda item: _safe_float(item.get("involvement_score")), reverse=True)
    for index, player in enumerate(ranked, start=1):
        player["ranking_within_team"] = index
    return ranked


def _score_model_quality(kind: str, status: str, note: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "kind": kind,
        "status": status,
        "note": note,
    }
    payload.update(extra)
    return payload


def _build_expected_metric_context(
    *,
    league_df: pd.DataFrame,
    selected_match_df: pd.DataFrame,
) -> dict[str, Any]:
    training_df = league_df if not league_df.empty else selected_match_df
    context: dict[str, Any] = {
        "team_scored_shots": pd.DataFrame(),
        "league_scored_shots": pd.DataFrame(),
        "selected_scored_shots": pd.DataFrame(),
        "team_xa_links": pd.DataFrame(),
        "league_xa_links": pd.DataFrame(),
        "selected_xa_links": pd.DataFrame(),
        "team_xt_actions": pd.DataFrame(),
        "league_xt_actions": pd.DataFrame(),
        "selected_xt_actions": pd.DataFrame(),
        "player_xg": pd.DataFrame(),
        "player_xa": pd.DataFrame(),
        "player_xt": pd.DataFrame(),
        "xg_quality": _score_model_quality("xG", "empty", "xG was not attempted."),
        "xa_quality": _score_model_quality("xA", "empty", "xA was not attempted."),
        "xt_quality": _score_model_quality("xT", "empty", "xT was not attempted."),
    }

    if training_df.empty:
        note = "No saved event rows were available to train xG, xA or xT models."
        context["xg_quality"] = _score_model_quality("xG", "unavailable", note)
        context["xa_quality"] = _score_model_quality("xA", "unavailable", note)
        context["xt_quality"] = _score_model_quality("xT", "unavailable", note)
        return context

    try:
        xg_model = fit_xg_models(training_df)
        selected_scored, _ = score_shots_with_models(selected_match_df, xg_model)
        league_scored, _ = score_shots_with_models(league_df, xg_model) if not league_df.empty else (pd.DataFrame(), xg_model)
        context["selected_scored_shots"] = selected_scored
        context["league_scored_shots"] = league_scored
        context["player_xg"] = aggregate_player_xg(selected_scored)
        context["xg_quality"] = _score_model_quality(
            "xG",
            "trained" if xg_model.shots_used > 0 else "limited",
            "xG model trained from saved local event rows." if xg_model.shots_used > 0 else "No shot rows were available after feature preparation.",
            shots_used=int(xg_model.shots_used),
            goals_seen=int(xg_model.goals_seen),
            penalty_rate=round(float(xg_model.penalty_rate), 3),
        )
    except Exception as exc:
        context["xg_quality"] = _score_model_quality(
            "xG",
            "fallback",
            f"xG model could not be trained safely from saved rows: {type(exc).__name__}: {exc}",
        )

    try:
        selected_scored = context["selected_scored_shots"]
        league_scored = context["league_scored_shots"]
        selected_xa = link_shots_to_assists(selected_match_df, selected_scored if isinstance(selected_scored, pd.DataFrame) and not selected_scored.empty else None)
        league_xa = link_shots_to_assists(league_df, league_scored if isinstance(league_scored, pd.DataFrame) and not league_scored.empty else None) if not league_df.empty else pd.DataFrame()
        context["selected_xa_links"] = selected_xa
        context["league_xa_links"] = league_xa
        context["player_xa"] = aggregate_player_xa(selected_xa)
        context["xa_quality"] = _score_model_quality(
            "xA",
            "applied" if not selected_xa.empty or not league_xa.empty else "limited",
            "xA linked assists to scored shots after xG scoring." if not selected_xa.empty or not league_xa.empty else "xA found no linked assisted shots in the saved rows.",
            linked_actions=int(len(selected_xa)),
        )
    except Exception as exc:
        context["xa_quality"] = _score_model_quality(
            "xA",
            "fallback",
            f"xA links could not be applied safely after xG scoring: {type(exc).__name__}: {exc}",
        )

    try:
        xt_model = build_xt_model(training_df, include_set_pieces=False)
        selected_xt = value_actions(selected_match_df, model=xt_model, include_set_pieces=True)
        league_xt = value_actions(league_df, model=xt_model, include_set_pieces=True) if not league_df.empty else pd.DataFrame()
        context["selected_xt_actions"] = selected_xt
        context["league_xt_actions"] = league_xt
        context["player_xt"] = aggregate_player_xt(selected_match_df)
        context["xt_quality"] = _score_model_quality(
            "xT",
            "trained",
            "xT model trained from saved local event rows and applied to successful passes, carries and crosses.",
            valued_actions=int(len(selected_xt)),
            grid="16x12",
        )
    except Exception as exc:
        context["xt_quality"] = _score_model_quality(
            "xT",
            "fallback",
            f"xT model could not be trained safely from saved rows: {type(exc).__name__}: {exc}",
        )

    return context


def _attach_shot_metric_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "xg" not in out.columns:
        out["xg"] = 0.0
    return out


def _team_scored_shots(scored: pd.DataFrame, team: str) -> pd.DataFrame:
    if scored.empty:
        return scored
    return scored.loc[_team_filter(scored, team)].copy()


def _opponent_scored_shots(scored: pd.DataFrame, team: str) -> pd.DataFrame:
    if scored.empty:
        return scored
    return scored.loc[~_team_filter(scored, team)].copy()


def _team_metric_frame(frame: pd.DataFrame, team: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame.loc[_team_filter(frame, team)].copy()


def _opponent_metric_frame(frame: pd.DataFrame, team: str, match_ids: list[int] | None = None) -> pd.DataFrame:
    if frame.empty:
        return frame
    work = _filter_matches(frame, match_ids or _match_ids_from_frame(frame)) if match_ids else frame
    if work.empty:
        return work
    return work.loc[~_team_filter(work, team)].copy()


def _metric_team_value(frame: pd.DataFrame, team: str, col: str) -> float:
    team_frame = _team_metric_frame(frame, team)
    return _sum_numeric(team_frame, col)


def _metric_points_with_value(frame: pd.DataFrame, mask: pd.Series | None = None, value_col: str = "xg", limit: int = 260) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    work = frame.copy()
    if mask is None:
        local_mask = pd.Series(True, index=work.index, dtype="bool")
    else:
        local_mask = mask.reindex(work.index, fill_value=False)
    points = _pitch_points(work, local_mask, limit=limit)
    return points


def _build_common_lineup(df: pd.DataFrame, team: str, match_count: int) -> dict[str, Any]:
    player_col = _player_col(df)
    if df.empty or not player_col:
        return {
            "formation_guess": "Unknown",
            "confidence": "low",
            "method": "No player column was available, so a common XI could not be estimated.",
            "players": [],
            "note": "Common lineup unavailable because saved event rows do not contain player names.",
        }

    work = df.copy()
    masks = _event_masks(work)
    work["__x_pct"] = (masks["x"] * (100.0 / PITCH_LENGTH)).clip(0, 100)
    work["__y_pct"] = (masks["y"] * (100.0 / PITCH_WIDTH)).clip(0, 100)
    work["__player"] = work[player_col].astype(str).replace({"": "Unknown"})

    shirt_col = _first_existing_column(work, ["shirt_no", "shirt_number", "jersey_number", "number"])
    position_col = _first_existing_column(work, ["position", "position_label", "player_position"])

    grouped = (
        work.groupby("__player", dropna=False)
        .agg(
            events=("__player", "size"),
            avg_x=("__x_pct", "mean"),
            avg_y=("__y_pct", "mean"),
            matches=("match_id", lambda value: int(pd.to_numeric(value, errors="coerce").dropna().nunique()) if "match_id" in work.columns else 0),
        )
        .reset_index()
        .sort_values(["matches", "events"], ascending=False)
        .head(11)
    )

    players: list[dict[str, Any]] = []
    for row in grouped.to_dict(orient="records"):
        avg_x = _safe_float(row.get("avg_x"), 50.0)
        avg_y = _safe_float(row.get("avg_y"), 50.0)
        player_name = str(row.get("__player") or "Unknown")
        sample = work.loc[work["__player"].eq(player_name)].head(1)
        sample_row = sample.iloc[0].to_dict() if not sample.empty else {}

        if avg_x < 26:
            role_group = "Defender"
            role_slot = "DF"
        elif avg_x < 55:
            role_group = "Midfielder"
            role_slot = "MF"
        elif avg_x < 75:
            role_group = "Attacking midfielder or winger"
            role_slot = "AM"
        else:
            role_group = "Forward"
            role_slot = "FW"

        players.append(
            {
                "player": player_name,
                "player_id": sample_row.get("player_id"),
                "position_label": sample_row.get(position_col) if position_col else role_slot,
                "role_group": role_group,
                "shirt_no": sample_row.get(shirt_col) if shirt_col else None,
                "appearances_covered": int(row.get("matches") or 0),
                "starts_estimated": None,
                "events": int(row.get("events") or 0),
                "minutes_proxy": int(row.get("matches") or 0) * 90 if row.get("matches") else None,
                "avg_x": round(avg_x, 2),
                "avg_y": round(avg_y, 2),
                "pitch_x": round(avg_x, 2),
                "pitch_y": round(avg_y, 2),
                "confidence": "medium" if int(row.get("matches") or 0) >= max(2, min(match_count, 4)) else "low",
            }
        )

    defenders = sum(1 for item in players if item["role_group"] == "Defender")
    midfielders = sum(1 for item in players if item["role_group"] == "Midfielder")
    forwards = sum(1 for item in players if item["role_group"] == "Forward")
    attackers = len(players) - defenders - midfielders - forwards
    formation_guess = f"{max(defenders, 1)}-{max(midfielders, 1)}-{max(attackers + forwards, 1)}" if players else "Unknown"

    return {
        "formation_guess": formation_guess,
        "confidence": "medium" if len(players) >= 9 and match_count >= 3 else "low",
        "method": "Estimated from most used players by event involvement and average action location.",
        "note": "This is a common involvement XI, not a confirmed starting XI, because confirmed lineup/start data was not available in the saved event rows.",
        "players": players,
    }


def _build_player_metric_enrichment(
    players: list[dict[str, Any]],
    metric_context: dict[str, Any],
) -> list[dict[str, Any]]:
    enriched = copy.deepcopy(players)
    _merge_player_metric_frame(
        enriched,
        metric_context.get("player_xg", pd.DataFrame()),
        {
            "xg_raw": "xg",
            "np_xg_raw": "np_xg",
            "xg_goals_raw": "xg_goals",
            "xg_shots_raw": "xg_shots",
        },
    )
    _merge_player_metric_frame(
        enriched,
        metric_context.get("player_xa", pd.DataFrame()),
        {
            "xa_raw": "xa",
            "open_play_xa_raw": "open_play_xa",
            "set_piece_xa_raw": "set_piece_xa",
            "shot_assists_linked_raw": "shot_assists_linked",
            "assisted_shot_xg_raw": "assisted_shot_xg",
        },
    )
    _merge_player_metric_frame(
        enriched,
        metric_context.get("player_xt", pd.DataFrame()),
        {
            "xt_raw": "xt",
            "open_play_xt_raw": "open_play_xt",
            "set_piece_xt_raw": "set_piece_xt",
            "xt_passes_raw": "xt_by_pass",
            "xt_crosses_raw": "xt_by_cross",
            "xt_carries_raw": "xt_by_carry",
        },
    )
    return _recalculate_player_rankings(enriched)


def _clip_percentile(value: object, fallback: float = 50.0) -> float:
    numeric = _safe_float(value, fallback)
    return round(max(0.0, min(100.0, numeric)), 1)


def _strength_label(score: float) -> str:
    if score >= 72.0:
        return "Strong"
    if score >= 48.0:
        return "Competitive"
    return "Needs attention"


def _radar_percentile_lookup(metric_radar: list[dict[str, Any]]) -> dict[str, float]:
    return {str(row.get("key")): _clip_percentile(row.get("percentile"), 50.0) for row in metric_radar}


def _breakdown_item(label: str, value: object, percentile: float | None = None, note: str = "") -> dict[str, Any]:
    score = _clip_percentile(percentile if percentile is not None else value, 50.0)
    return {
        "label": label,
        "value": _clean_number(value),
        "score": score,
        "percentile": score,
        "strength": _strength_label(score),
        "note": note,
    }


def _build_phase_visuals(
    *,
    overview: dict[str, Any],
    metric_radar: list[dict[str, Any]],
    players: list[dict[str, Any]],
    transitions: dict[str, Any],
    set_pieces: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    radar = _radar_percentile_lookup(metric_radar)

    def pct(metric_key: str, fallback: float = 50.0) -> float:
        return _clip_percentile(radar.get(metric_key, fallback), fallback)

    def inverse(metric_key: str, fallback: float = 50.0) -> float:
        return _clip_percentile(radar.get(metric_key, fallback), fallback)

    player_depth_score = min(100.0, len([row for row in players if _safe_float(row.get("involvement_score"), 0.0) > 0]) * 6.0)
    set_piece_overview = set_pieces.get("overview", {}) if isinstance(set_pieces.get("overview"), dict) else {}

    phase_specs: list[tuple[str, str, list[dict[str, Any]]]] = [
        (
            "in_possession",
            "In possession",
            [
                _breakdown_item("Build up volume", overview.get("possession_proxy"), max(pct("field_tilt_proxy"), _safe_float(overview.get("possession_proxy"), 50.0))),
                _breakdown_item("Progression", overview.get("final_third_entries"), pct("final_third_entries_for_per_match")),
                _breakdown_item("Final third entries", overview.get("final_third_entries"), pct("final_third_entries_for_per_match")),
                _breakdown_item("Pass security proxy", overview.get("possession_proxy"), _safe_float(overview.get("possession_proxy"), 50.0)),
                _breakdown_item("Carries and movement value", overview.get("xt"), pct("xt_per_match")),
            ],
        ),
        (
            "chance_creation",
            "Chance creation",
            [
                _breakdown_item("Shots", overview.get("shots"), pct("shots_for_per_match")),
                _breakdown_item("xG", overview.get("xg_for"), pct("xg_for_per_match")),
                _breakdown_item("xA", overview.get("xa"), pct("xa_per_match")),
                _breakdown_item("Shot assists", overview.get("xa"), pct("xa_per_match")),
                _breakdown_item("Box entries", overview.get("box_entries"), pct("box_entries_for_per_match")),
            ],
        ),
        (
            "final_third_penalty_area_threat",
            "Final third and penalty area threat",
            [
                _breakdown_item("Final third entries", overview.get("final_third_entries"), pct("final_third_entries_for_per_match")),
                _breakdown_item("Box entries", overview.get("box_entries"), pct("box_entries_for_per_match")),
                _breakdown_item("Shot quality", overview.get("xg_per_shot"), pct("xg_for_per_match")),
                _breakdown_item("Penalty area threat", overview.get("xg_for"), pct("xg_for_per_match")),
                _breakdown_item("Territory value", overview.get("xt"), pct("xt_per_match")),
            ],
        ),
        (
            "defensive_control",
            "Defensive control",
            [
                _breakdown_item("Shots conceded control", overview.get("shots_against"), inverse("shots_against_per_match")),
                _breakdown_item("xG conceded control", overview.get("xg_against"), inverse("xg_against_per_match")),
                _breakdown_item("High regains", overview.get("high_regains"), pct("high_regains_per_match")),
                _breakdown_item("Defensive actions", overview.get("defensive_actions"), pct("defensive_actions_per_match")),
                _breakdown_item("Box entry control", overview.get("box_entries_against"), inverse("box_entries_against_per_match")),
            ],
        ),
        (
            "transitions",
            "Transitions",
            [
                _breakdown_item("High regains", overview.get("high_regains"), pct("high_regains_per_match")),
                _breakdown_item("Regain to attack", transitions.get("regain_to_attack_sequences"), min(100.0, _safe_float(transitions.get("regain_to_attack_sequences"), 0.0) * 8.0)),
                _breakdown_item("Regain to shot", transitions.get("regain_to_shot_sequences"), min(100.0, _safe_float(transitions.get("regain_to_shot_sequences"), 0.0) * 10.0)),
                _breakdown_item("Fast attacks", transitions.get("fast_attacks_after_regains"), min(100.0, _safe_float(transitions.get("fast_attacks_after_regains"), 0.0) * 8.0)),
                _breakdown_item("Threat conceded control", transitions.get("opponent_transition_threat"), 100.0 - min(100.0, _safe_float(transitions.get("opponent_transition_threat"), 0.0) * 3.0)),
            ],
        ),
        (
            "set_pieces",
            "Set pieces",
            [
                _breakdown_item("Corners", set_pieces.get("corners_for"), min(100.0, _safe_float(set_pieces.get("corners_for"), 0.0) * 3.0)),
                _breakdown_item("Free kicks", set_pieces.get("free_kicks_for"), min(100.0, _safe_float(set_pieces.get("free_kicks_for"), 0.0) * 2.0)),
                _breakdown_item("Throw ins", set_pieces.get("throw_ins_for"), min(100.0, _safe_float(set_pieces.get("throw_ins_for"), 0.0) * 1.2)),
                _breakdown_item("Set piece xG", set_piece_overview.get("set_piece_xg"), min(100.0, _safe_float(set_piece_overview.get("set_piece_xg"), 0.0) * 25.0)),
                _breakdown_item("Set piece xG conceded control", set_piece_overview.get("set_piece_xg_conceded"), 100.0 - min(100.0, _safe_float(set_piece_overview.get("set_piece_xg_conceded"), 0.0) * 25.0)),
            ],
        ),
        (
            "player_influence",
            "Player influence",
            [
                _breakdown_item("Overall influence depth", len(players), player_depth_score),
                _breakdown_item("Creators", len([row for row in players if _safe_float(row.get("xa"), 0.0) > 0]), min(100.0, len([row for row in players if _safe_float(row.get("xa"), 0.0) > 0]) * 15.0)),
                _breakdown_item("Progressors", len([row for row in players if _safe_float(row.get("xt"), 0.0) > 0]), min(100.0, len([row for row in players if _safe_float(row.get("xt"), 0.0) > 0]) * 12.0)),
                _breakdown_item("Defensive contributors", len([row for row in players if _safe_float(row.get("defensive_actions"), 0.0) > 0]), min(100.0, len([row for row in players if _safe_float(row.get("defensive_actions"), 0.0) > 0]) * 8.0)),
                _breakdown_item("Set piece specialists", len([row for row in players if _safe_float(row.get("set_piece_involvement"), 0.0) > 0]), min(100.0, len([row for row in players if _safe_float(row.get("set_piece_involvement"), 0.0) > 0]) * 16.0)),
            ],
        ),
    ]

    phase_radar_groups: list[dict[str, Any]] = []
    phase_kpi_breakdowns: dict[str, Any] = {}
    for key, title, items in phase_specs:
        score = round(sum(_safe_float(item.get("score"), 0.0) for item in items) / max(len(items), 1), 1)
        phase_radar_groups.append(
            {
                "key": key,
                "title": title,
                "score": score,
                "strength": _strength_label(score),
                "metrics": items,
            }
        )
        phase_kpi_breakdowns[key] = {
            "key": key,
            "title": title,
            "score": score,
            "strength": _strength_label(score),
            "items": items,
        }

    return phase_radar_groups, phase_kpi_breakdowns


def _build_shape_profile(
    *,
    df: pd.DataFrame,
    mask: pd.Series,
    mode: str,
    title: str,
    match_count: int,
    method: str,
) -> dict[str, Any]:
    player_col = _player_col(df)
    if df.empty or not player_col or not mask.any():
        return {
            "mode": mode,
            "title": title,
            "formation_guess": "Unknown",
            "confidence": "low",
            "method": method,
            "players": [],
            "note": "No usable player and location rows were available for this shape view.",
        }

    masks = _event_masks(df)
    work = df.loc[mask].copy()
    local_masks = _event_masks(work)
    work["__x_pct"] = (local_masks["x"] * (100.0 / PITCH_LENGTH)).clip(0, 100)
    work["__y_pct"] = (local_masks["y"] * (100.0 / PITCH_WIDTH)).clip(0, 100)
    work["__player"] = work[player_col].astype(str).replace({"": "Unknown"})

    shirt_col = _first_existing_column(work, ["shirt_no", "shirt_number", "jersey_number", "number"])
    position_col = _first_existing_column(work, ["position", "position_label", "player_position"])
    match_col = _match_id_col(work)

    agg_spec: dict[str, Any] = {
        "events": ("__player", "size"),
        "avg_x": ("__x_pct", "mean"),
        "avg_y": ("__y_pct", "mean"),
    }
    if match_col:
        agg_spec["matches"] = (match_col, lambda value: int(pd.to_numeric(value, errors="coerce").dropna().nunique()))
    else:
        agg_spec["matches"] = ("__player", lambda value: 0)

    grouped = (
        work.groupby("__player", dropna=False)
        .agg(**agg_spec)
        .reset_index()
        .sort_values(["matches", "events"], ascending=False)
        .head(11)
    )

    players: list[dict[str, Any]] = []
    for row in grouped.to_dict(orient="records"):
        avg_x = _safe_float(row.get("avg_x"), 50.0)
        avg_y = _safe_float(row.get("avg_y"), 50.0)
        player_name = str(row.get("__player") or "Unknown")
        sample = work.loc[work["__player"].eq(player_name)].head(1)
        sample_row = sample.iloc[0].to_dict() if not sample.empty else {}
        if avg_x < 26:
            role_group = "Defender"
            role_slot = "DF"
        elif avg_x < 55:
            role_group = "Midfielder"
            role_slot = "MF"
        elif avg_x < 75:
            role_group = "Attacking midfielder or winger"
            role_slot = "AM"
        else:
            role_group = "Forward"
            role_slot = "FW"
        players.append(
            {
                "player": player_name,
                "player_id": sample_row.get("player_id"),
                "position_label": sample_row.get(position_col) if position_col else role_slot,
                "role_group": role_group,
                "shirt_no": sample_row.get(shirt_col) if shirt_col else None,
                "appearances_covered": int(row.get("matches") or 0),
                "starts_estimated": None,
                "events": int(row.get("events") or 0),
                "minutes_proxy": int(row.get("matches") or 0) * 90 if row.get("matches") else None,
                "avg_x": round(avg_x, 2),
                "avg_y": round(avg_y, 2),
                "pitch_x": round(avg_x, 2),
                "pitch_y": round(avg_y, 2),
                "confidence": "medium" if int(row.get("matches") or 0) >= max(2, min(match_count, 4)) else "low",
            }
        )

    defenders = sum(1 for item in players if item["role_group"] == "Defender")
    midfielders = sum(1 for item in players if item["role_group"] == "Midfielder")
    forwards = sum(1 for item in players if item["role_group"] == "Forward")
    attackers = len(players) - defenders - midfielders - forwards
    formation_guess = f"{max(defenders, 1)}-{max(midfielders, 1)}-{max(attackers + forwards, 1)}" if players else "Unknown"

    return {
        "mode": mode,
        "title": title,
        "formation_guess": formation_guess,
        "confidence": "medium" if len(players) >= 9 and match_count >= 3 else "low",
        "method": method,
        "note": "Position labels are used where present. Otherwise the shape is inferred from event locations and role behaviour.",
        "players": players,
    }


def _with_threat_values(df: pd.DataFrame, base_mask: pd.Series, shot_mask: pd.Series) -> pd.DataFrame:
    if df.empty or not base_mask.any():
        return df.iloc[0:0].copy()
    masks = _event_masks(df)
    work = df.loc[base_mask].copy()
    local_masks = {key: value.reindex(work.index, fill_value=False) for key, value in masks.items()}
    threat = pd.Series(0.08, index=work.index, dtype="float64")
    for col in ["xt_added", "xa", "xA", "assisted_shot_xg", "assisted_shot_xg_raw", "xg"]:
        if col in work.columns:
            threat = threat.combine(pd.to_numeric(work[col], errors="coerce").fillna(0.0), max)
    threat = threat.where(~local_masks["end_in_box"].astype(bool), threat + 0.22)
    threat = threat.where(~local_masks["final_third_entry"].astype(bool), threat + 0.16)
    threat = threat.where(~local_masks["box_entry"].astype(bool), threat + 0.24)
    threat = threat.where(~shot_mask.reindex(work.index, fill_value=False).astype(bool), threat + 0.35)
    work["threat_value"] = threat.clip(lower=0.0, upper=1.4)
    work["led_to_shot"] = shot_mask.reindex(work.index, fill_value=False).astype(bool) | local_masks["end_in_box"].astype(bool) | local_masks["box_entry"].astype(bool)
    work["led_to_goal"] = local_masks["is_goal"].astype(bool)
    return work.sort_values("threat_value", ascending=False)


def _build_lane_kpis(df: pd.DataFrame, masks: dict[str, pd.Series], team_xt_actions: pd.DataFrame) -> dict[str, Any]:
    return {
        "touches": _lane_summary(df, masks["is_move"] | masks["is_shot"]),
        "final_third_entries": _lane_summary(df, masks["final_third_entry"]),
        "box_entries": _lane_summary(df, masks["box_entry"]),
        "xT": _lane_summary(team_xt_actions, pd.Series(True, index=team_xt_actions.index, dtype="bool")) if not team_xt_actions.empty else [],
        "shots": _lane_summary(df, masks["is_shot"]),
        "xG": _lane_summary(df, masks["is_shot"]),
        "crosses": _lane_summary(df, masks["is_cross"]),
        "carries": _lane_summary(df, masks["is_carry"]),
        "progressive_passes": _lane_summary(df, masks["is_pass"] & (masks["final_third_entry"] | masks["box_entry"])),
    }


def _build_player_influence_dashboard(players: list[dict[str, Any]]) -> dict[str, Any]:
    def enriched_rows(rows: list[dict[str, Any]], main_metric: str, secondary_metric: str, why: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows[:6]:
            item = dict(row)
            item["main_metric"] = main_metric
            item["main_metric_value"] = _clean_number(row.get(main_metric))
            item["secondary_metric"] = secondary_metric
            item["secondary_metric_value"] = _clean_number(row.get(secondary_metric))
            item["why_he_appears"] = why
            item["matches_involved"] = _clean_number(row.get("matches_involved"))
            item["previous_season_movement"] = "Previous season player movement is unavailable unless player level saved seasons are loaded."
            out.append(item)
        return out

    def category(key: str, title: str, metric: str, secondary_metric: str, why: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "key": key,
            "title": title,
            "metric": metric,
            "secondary_metric": secondary_metric,
            "why": why,
            "players": enriched_rows(rows, metric, secondary_metric, why),
        }

    sorted_overall = sorted(players, key=lambda row: _safe_float(row.get("involvement_score"), 0.0), reverse=True)
    return {
        "categories": [
            category("top_attackers", "Top attackers", "xg", "shots", "Shot volume, xG, goals, box entries and final third involvement.", sorted(players, key=lambda row: (_safe_float(row.get("xg"), 0.0), _safe_float(row.get("shots"), 0.0), _safe_float(row.get("box_entries"), 0.0)), reverse=True)),
            category("top_creators", "Top creators", "xa", "final_third_entries", "xA, shot assists and final third pass involvement.", sorted(players, key=lambda row: (_safe_float(row.get("xa"), 0.0), _safe_float(row.get("final_third_entries"), 0.0)), reverse=True)),
            category("top_progressors", "Top progressors", "xt", "progressive_actions", "xT, progressive actions, final third entries and box entries.", sorted(players, key=lambda row: (_safe_float(row.get("xt"), 0.0), _safe_float(row.get("progressive_actions"), 0.0)), reverse=True)),
            category("top_ball_carriers", "Top ball carriers", "carries", "box_entries", "Carry volume and progressive actions from ball carrying.", sorted(players, key=lambda row: (_safe_float(row.get("carries"), 0.0), _safe_float(row.get("progressive_actions"), 0.0)), reverse=True)),
            category("top_defensive_stabilisers", "Top defensive stabilisers", "defensive_actions", "events", "Defensive action volume and repeat involvement across matches.", sorted(players, key=lambda row: (_safe_float(row.get("defensive_actions"), 0.0), _safe_float(row.get("events"), 0.0)), reverse=True)),
            category("top_transition_players", "Top transition players", "high_regains", "carries", "High regains, defensive actions and immediate carry value.", sorted(players, key=lambda row: (_safe_float(row.get("high_regains"), 0.0), _safe_float(row.get("carries"), 0.0)), reverse=True)),
            category("top_set_piece_players", "Top set piece players", "set_piece_involvement", "shots", "Set piece involvement, delivery actions and target value.", sorted(players, key=lambda row: (_safe_float(row.get("set_piece_involvement"), 0.0), _safe_float(row.get("shots"), 0.0)), reverse=True)),
            category("most_influential_overall", "Most influential overall", "involvement_score", "events", "Blends total involvement, threat, goals, creation, progression and defensive actions.", sorted_overall),
        ]
    }


def _build_set_piece_section(
    *,
    name: str,
    own_df: pd.DataFrame,
    opponent_df: pd.DataFrame,
    own_mask: pd.Series,
    opponent_mask: pd.Series,
    own_shot_mask: pd.Series,
    opponent_shot_mask: pd.Series,
    own_scored_shots: pd.DataFrame,
    opponent_scored_shots: pd.DataFrame,
    players: list[dict[str, Any]],
    opponent_available: bool,
) -> dict[str, Any]:
    own_local_masks = _event_masks(own_df)
    opponent_local_masks = _event_masks(opponent_df) if opponent_available else {}
    own_delivery_mask = own_mask & (own_local_masks["is_pass"] | own_local_masks["is_cross"])
    opponent_delivery_mask = opponent_mask & (opponent_local_masks["is_pass"] | opponent_local_masks["is_cross"]) if opponent_available else pd.Series(False, index=opponent_df.index, dtype="bool")

    own_delivery_frame = _with_threat_values(own_df, own_delivery_mask, own_shot_mask)
    own_delivery_lines = _pitch_points(own_delivery_frame, pd.Series(True, index=own_delivery_frame.index, dtype="bool"), limit=220)
    opponent_delivery_frame = _with_threat_values(opponent_df, opponent_delivery_mask, opponent_shot_mask) if opponent_available else opponent_df.iloc[0:0].copy()
    opponent_delivery_lines = _pitch_points(opponent_delivery_frame, pd.Series(True, index=opponent_delivery_frame.index, dtype="bool"), limit=220) if opponent_available else []

    own_shots = own_scored_shots.loc[own_scored_shots.index.intersection(own_scored_shots.index)]
    opponent_shots = opponent_scored_shots.loc[opponent_scored_shots.index.intersection(opponent_scored_shots.index)]

    return {
        "name": name,
        "for": {
            "events": int(own_mask.sum()),
            "delivery_zones": _lane_summary(own_df, own_delivery_mask),
            "delivery_locations": _pitch_points(own_df, own_delivery_mask, limit=220),
            "delivery_lines": own_delivery_lines,
            "shot_ending_deliveries": [row for row in own_delivery_lines if bool(row.get("led_to_shot"))],
            "high_threat_deliveries": [row for row in own_delivery_lines if _safe_float(row.get("threat_value"), 0.0) >= 0.35],
            "shots": int((own_mask & own_shot_mask).sum()),
            "shot_locations": _pitch_points(own_df, own_mask & own_shot_mask, limit=180),
            "xg": round(float(_sum_numeric(own_shots, "xg")), 3) if not own_shots.empty else 0.0,
            "xa": round(float(_sum_numeric(own_delivery_frame, "xa")), 3),
            "main_takers": _top_from_players(players, "set_piece_involvement", 8),
            "main_targets": _top_from_players(players, "shots", 8),
            "dangerous_zones": _lane_summary(own_df, own_delivery_mask),
        },
        "against": {
            "events": int(opponent_mask.sum()) if opponent_available else None,
            "delivery_zones": _lane_summary(opponent_df, opponent_delivery_mask) if opponent_available else [],
            "delivery_locations": _pitch_points(opponent_df, opponent_delivery_mask, limit=220) if opponent_available else [],
            "delivery_lines": opponent_delivery_lines,
            "shot_ending_deliveries": [row for row in opponent_delivery_lines if bool(row.get("led_to_shot"))],
            "high_threat_deliveries": [row for row in opponent_delivery_lines if _safe_float(row.get("threat_value"), 0.0) >= 0.35],
            "shots": int((opponent_mask & opponent_shot_mask).sum()) if opponent_available else None,
            "shot_locations": _pitch_points(opponent_df, opponent_mask & opponent_shot_mask, limit=180) if opponent_available else [],
            "xg": round(float(_sum_numeric(opponent_shots, "xg")), 3) if opponent_available and not opponent_shots.empty else (None if not opponent_available else 0.0),
            "dangerous_zones": _lane_summary(opponent_df, opponent_delivery_mask) if opponent_available else [],
        },
    }


def _actions_by_type(frame: pd.DataFrame, team: str, action_type: str | None = None) -> pd.DataFrame:
    if frame.empty:
        return frame
    work = _team_metric_frame(frame, team)
    if action_type and "action_type" in work.columns:
        work = work.loc[work["action_type"].astype(str).eq(action_type)].copy()
    return work


def _build_xt_action_points(actions: pd.DataFrame, limit: int = 260) -> list[dict[str, Any]]:
    if actions.empty:
        return []
    work = actions.sort_values("xt_added", ascending=False).head(limit).copy() if "xt_added" in actions.columns else actions.head(limit).copy()
    return _pitch_points(work, pd.Series(True, index=work.index, dtype="bool"), limit=limit)


def _build_metric_radar(
    *,
    selected_team: str,
    league_df: pd.DataFrame,
    league_scored_shots: pd.DataFrame,
    league_xa_links: pd.DataFrame,
    league_xt_actions: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if league_df.empty or "team" not in league_df.columns:
        return [], {
            "available": False,
            "teams_compared": 0,
            "note": "League radar unavailable because full league season rows were not available.",
        }

    teams = [
        str(team)
        for team in league_df["team"].dropna().astype(str).unique().tolist()
        if str(team).strip()
    ]
    teams = sorted(dict.fromkeys(teams), key=lambda item: item.lower())
    rows: list[dict[str, Any]] = []

    for team_name in teams:
        team_events = league_df.loc[_team_filter(league_df, team_name)].copy()
        if team_events.empty:
            continue
        match_ids = _match_ids_from_frame(team_events)
        matches = max(len(match_ids), 1)
        selected_matches = _filter_matches(league_df, match_ids)
        opp_events = selected_matches.loc[~_team_filter(selected_matches, team_name)].copy() if not selected_matches.empty else pd.DataFrame(columns=league_df.columns)
        team_masks = _event_masks(team_events)
        opp_masks = _event_masks(opp_events) if not opp_events.empty else {}
        team_shots = _team_scored_shots(league_scored_shots, team_name)
        opp_shots = _opponent_metric_frame(league_scored_shots, team_name, match_ids)
        team_xa = _team_metric_frame(league_xa_links, team_name)
        team_xt = _team_metric_frame(league_xt_actions, team_name)

        final_third_for = int(team_masks["final_third_entry"].sum())
        final_third_against = int(opp_masks["final_third_entry"].sum()) if opp_masks else 0

        rows.append(
            {
                "team": team_name,
                "matches": matches,
                "goals_for_per_match": int(team_masks["is_goal"].sum()) / matches,
                "goals_against_per_match": (int(opp_masks["is_goal"].sum()) / matches) if opp_masks else 0.0,
                "shots_for_per_match": int(team_masks["is_shot"].sum()) / matches,
                "shots_against_per_match": (int(opp_masks["is_shot"].sum()) / matches) if opp_masks else 0.0,
                "xg_for_per_match": _sum_numeric(team_shots, "xg") / matches,
                "xg_against_per_match": _sum_numeric(opp_shots, "xg") / matches,
                "xa_per_match": _sum_numeric(team_xa, "xa_raw") / matches,
                "xt_per_match": _sum_numeric(team_xt, "xt_added") / matches,
                "box_entries_for_per_match": int(team_masks["box_entry"].sum()) / matches,
                "box_entries_against_per_match": (int(opp_masks["box_entry"].sum()) / matches) if opp_masks else 0.0,
                "final_third_entries_for_per_match": final_third_for / matches,
                "final_third_entries_against_per_match": (final_third_against / matches) if opp_masks else 0.0,
                "field_tilt_proxy": _pct(final_third_for, max(final_third_for + final_third_against, 1)),
                "high_regains_per_match": int(team_masks["high_regain"].sum()) / matches,
                "defensive_actions_per_match": int(team_masks["is_defensive"].sum()) / matches,
                "set_piece_shots_for_per_match": int((team_masks["is_set_piece"] & team_masks["is_shot"]).sum()) / matches,
                "set_piece_shots_against_per_match": (int((opp_masks["is_set_piece"] & opp_masks["is_shot"]).sum()) / matches) if opp_masks else 0.0,
            }
        )

    if not rows:
        return [], {
            "available": False,
            "teams_compared": 0,
            "note": "League radar unavailable because no team rows could be grouped from saved rows.",
        }

    selected_key = _norm_team_name(selected_team)
    selected_row = next((row for row in rows if _norm_team_name(row["team"]) == selected_key), None)
    if selected_row is None:
        return [], {
            "available": False,
            "teams_compared": len(rows),
            "note": "League radar unavailable because the selected team was not found in the league comparison group.",
        }

    specs = [
        ("goals_for_per_match", "Goals for per match", True, "Scoring"),
        ("goals_against_per_match", "Goals against per match", False, "Defending"),
        ("shots_for_per_match", "Shots for per match", True, "Shooting"),
        ("shots_against_per_match", "Shots against per match", False, "Defending"),
        ("xg_for_per_match", "xG for per match", True, "Chance quality"),
        ("xg_against_per_match", "xG against per match", False, "Chance quality conceded"),
        ("xa_per_match", "xA per match", True, "Chance creation"),
        ("xt_per_match", "xT per match", True, "Territory value"),
        ("box_entries_for_per_match", "Box entries for per match", True, "Territory"),
        ("box_entries_against_per_match", "Box entries against per match", False, "Territory conceded"),
        ("final_third_entries_for_per_match", "Final third entries for per match", True, "Territory"),
        ("final_third_entries_against_per_match", "Final third entries against per match", False, "Territory conceded"),
        ("field_tilt_proxy", "Field tilt proxy", True, "Territory"),
        ("high_regains_per_match", "High regains per match", True, "Pressing"),
        ("defensive_actions_per_match", "Defensive actions per match", True, "Defending"),
        ("set_piece_shots_for_per_match", "Set piece shots for per match", True, "Set pieces"),
        ("set_piece_shots_against_per_match", "Set piece shots against per match", False, "Set pieces conceded"),
    ]

    radar_rows: list[dict[str, Any]] = []
    teams_compared = len(rows)
    for key, label, higher_is_better, category in specs:
        values = [_safe_float(row.get(key), 0.0) for row in rows]
        selected_value = _safe_float(selected_row.get(key), 0.0)
        sorted_values = sorted(values, reverse=higher_is_better)
        rank = sorted_values.index(selected_value) + 1 if selected_value in sorted_values else teams_compared
        if teams_compared <= 1:
            percentile = 100.0
        else:
            percentile = 100.0 * (teams_compared - rank) / (teams_compared - 1)
        league_min = min(values) if values else 0.0
        league_max = max(values) if values else 0.0
        league_average = sum(values) / len(values) if values else 0.0

        radar_rows.append(
            {
                "key": key,
                "label": label,
                "value": round(selected_value, 3),
                "per_match": round(selected_value, 3),
                "rank": int(rank),
                "rank_text": f"{rank} of {teams_compared}",
                "teams_compared": teams_compared,
                "percentile": round(percentile, 1),
                "league_min": round(league_min, 3),
                "league_max": round(league_max, 3),
                "league_average": round(league_average, 3),
                "higher_is_better": higher_is_better,
                "category": category,
            }
        )

    return radar_rows, {
        "available": True,
        "teams_compared": teams_compared,
        "selected_team": selected_team,
        "comparison_scope": "Selected nation, tier and season from saved local event rows.",
        "rows": rows,
    }


def _build_league_context(metric_radar: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": bool(metric_radar),
        "teams_compared": int(context.get("teams_compared") or 0),
        "selected_team": context.get("selected_team"),
        "comparison_scope": context.get("comparison_scope", ""),
        "note": context.get("note", "League ranks are calculated from saved local event data only."),
    }



def _filter_scored_shots_by_keywords(scored: pd.DataFrame, keywords: list[str]) -> pd.DataFrame:
    if scored.empty:
        return scored
    probes = [item.lower().replace(" ", "").replace("_", "") for item in keywords]
    def _row_matches(row: pd.Series) -> bool:
        values = []
        for col in ["qual_tags", "tag_set", "type", "type_l", "shot_family"]:
            if col in row.index:
                values.append(str(row.get(col) or ""))
        haystack = " ".join(values).lower().replace(" ", "").replace("_", "")
        return any(probe in haystack for probe in probes)
    return scored.loc[scored.apply(_row_matches, axis=1)].copy()


def _build_set_piece_profile(
    *,
    own_df: pd.DataFrame,
    opponent_df: pd.DataFrame,
    own_masks: dict[str, pd.Series],
    opponent_masks: dict[str, pd.Series],
    own_scored_shots: pd.DataFrame,
    opponent_scored_shots: pd.DataFrame,
    players: list[dict[str, Any]],
    opponent_available: bool,
) -> dict[str, Any]:
    own_shot_mask = own_masks["is_shot"]
    opp_shot_mask = opponent_masks["is_shot"] if opponent_available else pd.Series(False, index=opponent_df.index, dtype="bool")
    empty_opp = pd.Series(False, index=opponent_df.index, dtype="bool")

    corners = _build_set_piece_section(
        name="Corners",
        own_df=own_df,
        opponent_df=opponent_df,
        own_mask=own_masks["is_corner"],
        opponent_mask=opponent_masks["is_corner"] if opponent_available else empty_opp,
        own_shot_mask=own_shot_mask,
        opponent_shot_mask=opp_shot_mask,
        own_scored_shots=_filter_scored_shots_by_keywords(own_scored_shots, ["corner"]),
        opponent_scored_shots=_filter_scored_shots_by_keywords(opponent_scored_shots, ["corner"]),
        players=players,
        opponent_available=opponent_available,
    )
    free_kicks = _build_set_piece_section(
        name="Free kicks",
        own_df=own_df,
        opponent_df=opponent_df,
        own_mask=own_masks["is_free_kick"],
        opponent_mask=opponent_masks["is_free_kick"] if opponent_available else empty_opp,
        own_shot_mask=own_shot_mask,
        opponent_shot_mask=opp_shot_mask,
        own_scored_shots=_filter_scored_shots_by_keywords(own_scored_shots, ["freekick", "free kick", "direct_free_kick"]),
        opponent_scored_shots=_filter_scored_shots_by_keywords(opponent_scored_shots, ["freekick", "free kick", "direct_free_kick"]),
        players=players,
        opponent_available=opponent_available,
    )
    throw_ins = _build_set_piece_section(
        name="Throw ins",
        own_df=own_df,
        opponent_df=opponent_df,
        own_mask=own_masks["is_throw_in"],
        opponent_mask=opponent_masks["is_throw_in"] if opponent_available else empty_opp,
        own_shot_mask=own_shot_mask,
        opponent_shot_mask=opp_shot_mask,
        own_scored_shots=own_scored_shots.iloc[0:0],
        opponent_scored_shots=opponent_scored_shots.iloc[0:0],
        players=players,
        opponent_available=opponent_available,
    )

    set_piece_mask = own_masks["is_set_piece"]
    opponent_set_piece_mask = opponent_masks["is_set_piece"] if opponent_available else empty_opp
    return {
        "overview": {
            "set_piece_volume": int(set_piece_mask.sum()),
            "set_piece_shot_creation": int((set_piece_mask & own_shot_mask).sum()),
            "set_piece_xg": round(float(_sum_numeric(own_scored_shots, "xg")), 3),
            "set_piece_shots_conceded": int((opponent_set_piece_mask & opp_shot_mask).sum()) if opponent_available else None,
            "set_piece_xg_conceded": round(float(_sum_numeric(opponent_scored_shots, "xg")), 3) if opponent_available else None,
            "best_takers": _top_from_players(players, "set_piece_involvement", 5),
            "most_targeted_players": _top_from_players(players, "shots", 5),
        },
        "throw_ins": throw_ins,
        "corners": corners,
        "free_kicks": free_kicks,
        "corners_for": corners["for"]["events"],
        "corners_against": corners["against"]["events"],
        "free_kicks_for": free_kicks["for"]["events"],
        "free_kicks_against": free_kicks["against"]["events"],
        "throw_ins_for": throw_ins["for"]["events"],
        "throw_ins_against": throw_ins["against"]["events"],
        "set_piece_shots": int((set_piece_mask & own_shot_mask).sum()),
        "set_piece_shots_against": int((opponent_set_piece_mask & opp_shot_mask).sum()) if opponent_available else None,
        "delivery_zones": _lane_summary(own_df, set_piece_mask & (own_masks["is_pass"] | own_masks["is_cross"])),
        "delivery_locations": _pitch_points(own_df, set_piece_mask & (own_masks["is_pass"] | own_masks["is_cross"]), limit=220),
        "delivery_zones_against": _lane_summary(opponent_df, opponent_set_piece_mask & (opponent_masks["is_pass"] | opponent_masks["is_cross"])) if opponent_available else [],
        "delivery_locations_against": _pitch_points(opponent_df, opponent_set_piece_mask & (opponent_masks["is_pass"] | opponent_masks["is_cross"]), limit=220) if opponent_available else [],
        "main_takers": _top_from_players(players, "set_piece_involvement", 8),
        "main_targets": _top_from_players(players, "shots", 8),
        "defensive_set_piece_events": int(opponent_set_piece_mask.sum()) if opponent_available else None,
        "defensive_set_piece_shots": int((opponent_set_piece_mask & opp_shot_mask).sum()) if opponent_available else None,
        "defensive_set_piece_shot_locations": _pitch_points(opponent_df, opponent_set_piece_mask & opp_shot_mask, limit=180) if opponent_available else [],
    }


def _radar_row_by_key(metric_radar: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("key") or ""): row for row in metric_radar if str(row.get("key") or "").strip()}


def _previous_season_row(multi_season_profile: dict[str, Any], current_season: str) -> dict[str, Any] | None:
    rows = multi_season_profile.get("rows") if isinstance(multi_season_profile, dict) else []
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("season") or "") != str(current_season or ""):
            return row
    return None


def _trend_arrow(current: object, previous: object, *, lower_is_better: bool = False) -> str:
    if previous is None:
        return "flat"
    current_value = _safe_float(current, 0.0)
    previous_value = _safe_float(previous, 0.0)
    if abs(current_value - previous_value) < 0.001:
        return "flat"
    improved = current_value < previous_value if lower_is_better else current_value > previous_value
    return "up" if improved else "down"


def _build_seasonal_summary_panel(
    overview: dict[str, Any],
    metric_radar: list[dict[str, Any]],
    multi_season_profile: dict[str, Any],
    current_season: str,
) -> dict[str, Any]:
    radar = _radar_row_by_key(metric_radar)
    previous = _previous_season_row(multi_season_profile, current_season) or {}
    specs = [
        ("goals_for", "Goals for", "goals", "goals_for", "goals_for_per_match", True),
        ("goals_against", "Goals against", "goals_against", "goals_against", "goals_against_per_match", False),
        ("shots_for", "Shots for", "shots", "shots_for", "shots_for_per_match", True),
        ("shots_against", "Shots against", "shots_against", "shots_against", "shots_against_per_match", False),
        ("xg_for", "xG for", "xg_for", "xg_for", "xg_for_per_match", True),
        ("xg_against", "xG against", "xg_against", "xg_against", "xg_against_per_match", False),
        ("xa", "xA", "xa", "xa", "xa_per_match", True),
        ("xt", "xT", "xt", "xt", "xt_per_match", True),
        ("final_third_entries", "Final third entries", "final_third_entries", "final_third_entries_for", "final_third_entries_for_per_match", True),
        ("box_entries", "Box entries", "box_entries", "box_entries_for", "box_entries_for_per_match", True),
        ("high_regains", "High regains", "high_regains", "high_regains", "high_regains_per_match", True),
        ("set_piece_shots", "Set piece shots", "set_piece_threat", "set_piece_shots_for", "set_piece_shots_for_per_match", True),
        ("defensive_actions", "Defensive actions", "defensive_actions", "defensive_actions", "defensive_actions_per_match", True),
    ]
    rows: list[dict[str, Any]] = []
    for key, label, overview_key, previous_key, radar_key, higher_is_better in specs:
        radar_row = radar.get(radar_key, {})
        value = overview.get(overview_key)
        previous_value = previous.get(previous_key)
        rows.append(
            {
                "key": key,
                "label": label,
                "value": _clean_number(value),
                "previous_value": _clean_number(previous_value),
                "league_average": _clean_number(radar_row.get("league_average")),
                "rank": _clean_number(radar_row.get("rank")),
                "rank_text": radar_row.get("rank_text"),
                "percentile": _clean_number(radar_row.get("percentile")),
                "teams_compared": _clean_number(radar_row.get("teams_compared")),
                "higher_is_better": bool(higher_is_better),
                "trend": _trend_arrow(value, previous_value, lower_is_better=not higher_is_better),
                "comparison_note": "Previous season available." if previous else "No previous season row available.",
            }
        )
    return {
        "rows": rows,
        "note": "Season values are built from saved event rows. League rank uses available teams in the same saved season.",
        "previous_season": previous.get("season") if previous else None,
    }


def _build_seasonal_radar_comparison(
    metric_radar: list[dict[str, Any]],
    multi_season_profile: dict[str, Any],
    current_season: str,
) -> dict[str, Any]:
    previous = _previous_season_row(multi_season_profile, current_season) or {}
    mapping = {
        "goals_for_per_match": "goals_for",
        "goals_against_per_match": "goals_against",
        "shots_for_per_match": "shots_for",
        "shots_against_per_match": "shots_against",
        "xg_for_per_match": "xg_for",
        "xg_against_per_match": "xg_against",
        "xa_per_match": "xa",
        "xt_per_match": "xt",
        "box_entries_for_per_match": "box_entries_for",
        "final_third_entries_for_per_match": "final_third_entries_for",
        "high_regains_per_match": "high_regains",
        "defensive_actions_per_match": "defensive_actions",
        "set_piece_shots_for_per_match": "set_piece_shots_for",
    }
    axes: list[dict[str, Any]] = []
    for row in metric_radar[:14]:
        key = str(row.get("key") or "")
        previous_key = mapping.get(key)
        league_average = _safe_float(row.get("league_average"), 0.0)
        previous_total = _safe_float(previous.get(previous_key), 0.0) if previous_key else 0.0
        previous_matches = max(_safe_float(previous.get("matches_covered"), 0.0), 1.0)
        previous_per_match = previous_total / previous_matches if previous_key else None
        higher_is_better = bool(row.get("higher_is_better", True))
        if previous_per_match is None or league_average <= 0:
            previous_score = None
        else:
            ratio = previous_per_match / league_average
            previous_score = 50.0 + ((ratio - 1.0) * 50.0)
            if not higher_is_better:
                previous_score = 100.0 - previous_score
            previous_score = round(max(0.0, min(100.0, previous_score)), 1)
        axes.append(
            {
                "key": key,
                "label": row.get("label"),
                "current_score": _clip_percentile(row.get("percentile"), 50.0),
                "league_average_score": 50.0,
                "previous_score": previous_score,
                "current_value": _clean_number(row.get("per_match", row.get("value"))),
                "league_average": _clean_number(row.get("league_average")),
                "previous_value": _clean_number(previous_per_match) if previous_per_match is not None else None,
                "higher_is_better": higher_is_better,
            }
        )
    return {
        "axes": axes,
        "previous_season": previous.get("season") if previous else None,
        "note": "Current score uses league percentile. League average is the fixed 50 baseline. Previous season is estimated from saved team totals where available.",
    }


def _event_order_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    work = df.copy()
    match_col = _match_id_col(work)
    if match_col and match_col != "match_id":
        work["match_id"] = pd.to_numeric(work[match_col], errors="coerce")
    elif "match_id" in work.columns:
        work["match_id"] = pd.to_numeric(work["match_id"], errors="coerce")
    else:
        work["match_id"] = 0
    period_raw = work["period"] if "period" in work.columns else pd.Series(1, index=work.index)
    work["__period"] = pd.to_numeric(period_raw, errors="coerce").fillna(1)
    if "expanded_minute" in work.columns:
        work["__minute"] = pd.to_numeric(work["expanded_minute"], errors="coerce").fillna(0.0)
    else:
        minute_raw = work["minute"] if "minute" in work.columns else pd.Series(0, index=work.index)
        second_raw = work["second"] if "second" in work.columns else pd.Series(0, index=work.index)
        minute = pd.to_numeric(minute_raw, errors="coerce").fillna(0.0)
        second = pd.to_numeric(second_raw, errors="coerce").fillna(0.0)
        work["__minute"] = minute + (second / 60.0)
    if "event_index" in work.columns:
        work["__event_index"] = pd.to_numeric(work["event_index"], errors="coerce").fillna(work.index.to_series())
    else:
        work["__event_index"] = work.index
    return work.sort_values(["match_id", "__period", "__minute", "__event_index"], kind="stable").reset_index(drop=True)


def _danger_score_from_masks(masks: dict[str, pd.Series]) -> pd.Series:
    return (
        masks["is_shot"].astype(float) * 3.0
        + masks["box_entry"].astype(float) * 2.0
        + masks["final_third_entry"].astype(float) * 1.0
        + masks["high_regain"].astype(float) * 0.7
    )


def _match_danger_table(df: pd.DataFrame, side: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["match_id", f"{side}_danger", f"{side}_shots", f"{side}_box_entries", f"{side}_final_third_entries"])
    work = _event_order_frame(df)
    masks = _event_masks(work)
    work["__danger"] = _danger_score_from_masks(masks)
    work["__shots"] = masks["is_shot"].astype(int)
    work["__box_entries"] = masks["box_entry"].astype(int)
    work["__final_third_entries"] = masks["final_third_entry"].astype(int)
    return (
        work.dropna(subset=["match_id"])
        .groupby("match_id", dropna=False)
        .agg(
            **{
                f"{side}_danger": ("__danger", "sum"),
                f"{side}_shots": ("__shots", "sum"),
                f"{side}_box_entries": ("__box_entries", "sum"),
                f"{side}_final_third_entries": ("__final_third_entries", "sum"),
            }
        )
        .reset_index()
    )


def _build_interval_momentum(df: pd.DataFrame, opponent_df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, frame in [("team", df), ("opponent", opponent_df)]:
        if frame.empty:
            continue
        work = _event_order_frame(frame)
        masks = _event_masks(work)
        work["__interval"] = (pd.to_numeric(work["__minute"], errors="coerce").fillna(0.0) // 15).astype(int) * 15
        work["__danger"] = _danger_score_from_masks(masks)
        grouped = work.groupby("__interval", dropna=False)["__danger"].sum()
        for interval, value in grouped.items():
            existing = next((row for row in rows if row["interval_start"] == int(interval)), None)
            if existing is None:
                existing = {"interval_start": int(interval), "interval_label": f"{int(interval)} to {int(interval) + 15}", "team_danger": 0.0, "opponent_danger": 0.0, "net": 0.0}
                rows.append(existing)
            existing[f"{label}_danger"] = round(float(value), 3)
    for row in rows:
        row["net"] = round(_safe_float(row.get("team_danger"), 0.0) - _safe_float(row.get("opponent_danger"), 0.0), 3)
    return sorted(rows, key=lambda item: int(item["interval_start"]))


def _build_home_away_split(match_log: list[dict[str, Any]], match_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {int(row.get("match_id")): row for row in match_log if row.get("match_id") is not None}
    splits: dict[str, list[dict[str, Any]]] = {"Home": [], "Away": [], "Unknown": []}
    for row in match_rows:
        log = by_id.get(int(row.get("match_id") or 0), {})
        key = str(log.get("home_away") or "Unknown")
        if key not in splits:
            key = "Unknown"
        splits[key].append(row)
    out: list[dict[str, Any]] = []
    for key, rows in splits.items():
        if not rows:
            continue
        out.append(
            {
                "split": key,
                "matches": len(rows),
                "attacking_momentum": round(sum(_safe_float(row.get("team_danger"), 0.0) for row in rows) / max(len(rows), 1), 2),
                "defensive_exposure": round(sum(_safe_float(row.get("opponent_danger"), 0.0) for row in rows) / max(len(rows), 1), 2),
                "net": round(sum(_safe_float(row.get("net_danger"), 0.0) for row in rows) / max(len(rows), 1), 2),
            }
        )
    return out


def _build_seasonal_momentum(
    df: pd.DataFrame,
    opponent_df: pd.DataFrame,
    match_log: list[dict[str, Any]],
) -> dict[str, Any]:
    own_table = _match_danger_table(df, "team")
    opp_table = _match_danger_table(opponent_df, "opponent")
    if own_table.empty:
        return {"available": False, "note": "No match by match own team event rows were available for seasonal momentum."}
    merged = own_table.merge(opp_table, on="match_id", how="left").fillna(0.0)
    log_order = {int(row.get("match_id")): index for index, row in enumerate(reversed(match_log)) if row.get("match_id") is not None}
    merged["__order"] = merged["match_id"].map(lambda value: log_order.get(int(value), len(log_order) + int(value)))
    merged = merged.sort_values("__order").reset_index(drop=True)
    merged["net_danger"] = merged["team_danger"] - merged["opponent_danger"]
    merged["rolling_five_attack"] = merged["team_danger"].rolling(5, min_periods=1).mean()
    merged["rolling_five_defensive_exposure"] = merged["opponent_danger"].rolling(5, min_periods=1).mean()
    rows = [
        {
            "match_id": int(row["match_id"]),
            "match_number": int(index + 1),
            "team_danger": round(float(row["team_danger"]), 3),
            "opponent_danger": round(float(row["opponent_danger"]), 3),
            "net_danger": round(float(row["net_danger"]), 3),
            "rolling_five_attack": round(float(row["rolling_five_attack"]), 3),
            "rolling_five_defensive_exposure": round(float(row["rolling_five_defensive_exposure"]), 3),
            "team_shots": int(row.get("team_shots", 0)),
            "opponent_shots": int(row.get("opponent_shots", 0)),
        }
        for index, row in merged.iterrows()
    ]
    recent = rows[-5:]
    previous = rows[-10:-5]
    def avg(rows_in: list[dict[str, Any]], key: str) -> float:
        return round(sum(_safe_float(row.get(key), 0.0) for row in rows_in) / max(len(rows_in), 1), 3)
    return {
        "available": True,
        "match_by_match": rows[-24:],
        "rolling_five": [{"match_id": row["match_id"], "match_number": row["match_number"], "attack": row["rolling_five_attack"], "defensive_exposure": row["rolling_five_defensive_exposure"]} for row in rows[-24:]],
        "interval_momentum": _build_interval_momentum(df, opponent_df),
        "recent_five": {"matches": len(recent), "attacking_momentum": avg(recent, "team_danger"), "defensive_exposure": avg(recent, "opponent_danger"), "net": avg(recent, "net_danger")},
        "previous_five": {"matches": len(previous), "attacking_momentum": avg(previous, "team_danger"), "defensive_exposure": avg(previous, "opponent_danger"), "net": avg(previous, "net_danger")},
        "home_away_split": _build_home_away_split(match_log, rows),
        "note": "Danger trend is an event based momentum proxy using shots, box entries, final third entries and high regains. It is not tracking data.",
    }


def _metric_lookup_value(metric_radar: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return _radar_row_by_key(metric_radar).get(key, {})


def _build_phase_verdicts(
    overview: dict[str, Any],
    metric_radar: list[dict[str, Any]],
    style_tags: list[str],
    opponent_rows_available: bool,
) -> dict[str, Any]:
    def metric_context(key: str) -> str:
        row = _metric_lookup_value(metric_radar, key)
        if not row:
            return "League context is unavailable."
        return f"{row.get('rank_text') or 'rank unavailable'} with percentile {format(_safe_float(row.get('percentile'), 0.0), '.0f')}."
    wide_tag = "Wing focused" in style_tags
    central_tag = "Central progression" in style_tags
    direct_tag = "Direct" in style_tags
    set_piece_tag = "Set piece threat" in style_tags
    defensive_warning = "" if opponent_rows_available else " Opponent rows are incomplete, so conceded context needs video confirmation."
    return {
        "items": [
            {
                "phase": "Attacking",
                "strongest_route": "Wide progression and deliveries" if wide_tag else "Central progression through the final third" if central_tag else "Direct entries into the box" if direct_tag else "Balanced event based progression",
                "main_risk": "Chance volume must be checked against shot quality and opponent territory control.",
                "repeatability": "Repeatable when the same lanes and player contributors appear across the match log.",
                "league_context": metric_context("xg_for_per_match"),
                "video_check": "Check if entries come from controlled possession or isolated transition actions.",
            },
            {
                "phase": "Defensive",
                "strongest_route": "High regains and active defending" if _safe_float(overview.get("high_regains"), 0.0) > 0 else "Defensive volume and box protection",
                "main_risk": f"High defensive action volume can reflect exposure rather than control.{defensive_warning}",
                "repeatability": "Repeatable only if the team controls territory before the defensive action volume appears.",
                "league_context": metric_context("shots_against_per_match"),
                "video_check": "Check territory, opponent possession share, pressure exposure and whether danger reaches the box before actions occur.",
            },
            {
                "phase": "Transitions",
                "strongest_route": "High regains creating immediate forward access" if _safe_float(overview.get("high_regains"), 0.0) > 0 else "Limited transition signal in the event data",
                "main_risk": "Failed pressure can leave space behind the first line.",
                "repeatability": "Repeatable when regains are followed by box entries or shots within the next phase.",
                "league_context": metric_context("high_regains_per_match"),
                "video_check": "Check the first five seconds after regains and the protection behind the press.",
            },
            {
                "phase": "Set pieces",
                "strongest_route": "Shot creation from routines" if set_piece_tag else "Routine volume more than clear shot threat",
                "main_risk": "Delivery volume can overstate routine quality if first contact and second ball retention are weak.",
                "repeatability": "Repeatable when the same delivery zones, takers and targets appear across matches.",
                "league_context": metric_context("set_piece_shots_for_per_match"),
                "video_check": "Check delivery type, first contact, second ball structure and rest defence.",
            },
        ],
        "note": "Verdicts are generated from event data and should be treated as scouting prompts, not final tactical truth.",
    }


def _style_tag_evidence(tag: str, overview: dict[str, Any], metric_radar: list[dict[str, Any]], previous: dict[str, Any]) -> dict[str, Any]:
    tag_l = tag.lower()
    metric_keys: list[str]
    if "wing" in tag_l or "wide" in tag_l:
        metric_keys = ["final_third_entries_for_per_match", "box_entries_for_per_match"]
        video_note = "Check whether width is created by winger isolation, full back overlap or switches."
    elif "central" in tag_l:
        metric_keys = ["final_third_entries_for_per_match", "xt_per_match"]
        video_note = "Check if central entries are controlled combinations or loose transition carries."
    elif "direct" in tag_l:
        metric_keys = ["box_entries_for_per_match", "shots_for_per_match"]
        video_note = "Check whether direct play is planned or a response to pressure."
    elif "press" in tag_l:
        metric_keys = ["high_regains_per_match", "defensive_actions_per_match"]
        video_note = "Check height of pressure, rest defence and opponent escape routes."
    elif "set piece" in tag_l:
        metric_keys = ["set_piece_shots_for_per_match", "xg_for_per_match"]
        video_note = "Check routine design, first contact and second ball retention."
    elif "deep" in tag_l:
        metric_keys = ["shots_against_per_match", "defensive_actions_per_match"]
        video_note = "Check if low defensive action locations come from compact defending or long exposure."
    else:
        metric_keys = ["field_tilt_proxy", "xt_per_match"]
        video_note = "Check whether the event signal matches the actual game model."
    radar = _radar_row_by_key(metric_radar)
    evidence = []
    for key in metric_keys:
        row = radar.get(key, {})
        evidence.append(
            {
                "metric": row.get("label") or key.replace("_", " "),
                "value": _clean_number(row.get("per_match", row.get("value"))),
                "rank_text": row.get("rank_text"),
                "percentile": _clean_number(row.get("percentile")),
            }
        )
    movement = "No previous season comparison available."
    if previous:
        movement = "Compare with previous season row before using this as a stable identity claim."
    return {
        "tag": tag,
        "evidence_metrics": evidence,
        "previous_season_movement": movement,
        "video_check_note": video_note,
    }


def _build_style_evidence_panel(
    style_tags: list[str],
    overview: dict[str, Any],
    metric_radar: list[dict[str, Any]],
    multi_season_profile: dict[str, Any],
    current_season: str,
) -> dict[str, Any]:
    previous = _previous_season_row(multi_season_profile, current_season) or {}
    tags = style_tags or []
    if not tags:
        tags = ["Event based profile"]
    rows = [_style_tag_evidence(tag, overview, metric_radar, previous) for tag in tags]
    return {
        "rows": rows,
        "note": "Style tags are evidence prompts. They should be checked on video before being used as a tactical label.",
    }


def _infer_goalkeeper_candidates(df: pd.DataFrame) -> list[str]:
    if df.empty:
        return []
    player_col = _player_col(df)
    if not player_col:
        return []
    position_col = _first_existing_column(df, ["position", "position_label", "player_position", "mapped_position"])
    shirt_col = _first_existing_column(df, ["shirt_no", "shirt_number", "jersey_number", "number"])
    type_l = _text_series(df, "type_l").str.lower()
    if type_l.str.strip().eq("").all():
        type_l = _text_series(df, "type").str.lower()
    keeper_mask = type_l.str.contains("keeper|save|claim|punch|smother", regex=True, na=False)
    if position_col:
        keeper_mask = keeper_mask | _text_series(df, position_col).str.lower().str.contains("gk|goalkeeper|keeper", regex=True, na=False)
    if shirt_col:
        keeper_mask = keeper_mask | pd.to_numeric(df[shirt_col], errors="coerce").eq(1)
    candidates = (
        df.loc[keeper_mask, player_col]
        .astype(str)
        .replace({"": "Unknown"})
        .value_counts()
        .head(3)
        .index
        .tolist()
    )
    return [item for item in candidates if item and item != "Unknown"]


def _build_goalkeeper_distribution(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"available": False, "note": "No team rows were available for goalkeeper distribution."}
    player_col = _player_col(df)
    if not player_col:
        return {"available": False, "note": "Player names are unavailable, so goalkeeper distribution is skipped."}
    goalkeepers = _infer_goalkeeper_candidates(df)
    if not goalkeepers:
        return {
            "available": False,
            "note": "No goalkeeper could be inferred safely from position, shirt number or goalkeeper event types.",
        }
    masks = _event_masks(df)
    keeper_mask = df[player_col].astype(str).isin(goalkeepers)
    pass_mask = keeper_mask & masks["is_pass"]
    if not pass_mask.any():
        return {"available": False, "goalkeepers": goalkeepers, "note": "Goalkeeper was inferred, but no goalkeeper pass rows were available."}
    pass_df = df.loc[pass_mask].copy()
    local_masks = _event_masks(pass_df)
    dx = local_masks["end_x"] - local_masks["x"]
    dy = local_masks["end_y"] - local_masks["y"]
    distance = ((dx ** 2) + (dy ** 2)) ** 0.5
    short = distance.lt(20.0)
    medium = distance.ge(20.0) & distance.lt(45.0)
    long = distance.ge(45.0)
    launched = distance.ge(55.0)
    progressive = dx.ge(20.0) | local_masks["final_third_entry"]
    target_rows: list[dict[str, Any]] = []
    for zone_key in ["left", "central", "right"]:
        zone_mask = local_masks["end_y"].map(_lane_key).eq(zone_key)
        target_rows.append({"zone": zone_key, "label": _lane_label(zone_key), "passes": int(zone_mask.sum()), "completion_pct": _pct(int((zone_mask & local_masks["successful"]).sum()), max(int(zone_mask.sum()), 1))})
    rows = []
    for goalkeeper, group in pass_df.groupby(player_col, dropna=False):
        idx = group.index
        rows.append(
            {
                "goalkeeper": str(goalkeeper),
                "passes": int(len(group)),
                "completion_pct": _pct(int(masks["successful"].loc[idx].sum()), max(int(len(group)), 1)),
                "long_passes": int(long.reindex(idx, fill_value=False).sum()),
                "progressive_distribution": int(progressive.reindex(idx, fill_value=False).sum()),
            }
        )
    return {
        "available": True,
        "goalkeepers": goalkeepers,
        "summary": {
            "short_distribution": int(short.sum()),
            "medium_distribution": int(medium.sum()),
            "long_distribution": int(long.sum()),
            "launched_passes": int(launched.sum()),
            "completion_pct": _pct(int(local_masks["successful"].sum()), max(int(len(pass_df)), 1)),
            "progressive_distribution": int(progressive.sum()),
        },
        "target_zones": target_rows,
        "pass_arrows": _pitch_points(pass_df, pd.Series(True, index=pass_df.index, dtype="bool"), limit=180),
        "top_rows": sorted(rows, key=lambda item: int(item["passes"]), reverse=True),
        "note": "Goalkeeper distribution is inferred from event data and does not prove tactical intent without video.",
    }


def _possession_chain_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    work = _event_order_frame(df)
    team_col = _team_col(work) or "team"
    if team_col not in work.columns:
        work[team_col] = ""
    work["__team_key"] = work[team_col].astype(str).map(_norm_team_name)
    work["__new_chain"] = work["match_id"].ne(work["match_id"].shift(1)) | work["__team_key"].ne(work["__team_key"].shift(1))
    work["__chain_id"] = work["__new_chain"].cumsum()
    return work


def _classify_final_third_pass_maps(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"maps": {}, "note": "No event rows available for final third pass classification."}
    work = _possession_chain_frame(df)
    masks = _event_masks(work)
    pass_mask = masks["is_pass"] & (masks["x"].ge(FINAL_THIRD_X) | masks["end_x"].ge(FINAL_THIRD_X))
    if not pass_mask.any():
        return {"maps": {}, "note": "No final third pass rows were found."}
    work["__pass"] = pass_mask
    work["__shot"] = masks["is_shot"]
    work["__goal"] = masks["is_goal"]
    work["__box_entry"] = masks["box_entry"]
    work["__danger"] = masks["box_entry"] | masks["is_shot"] | masks["is_goal"]
    work["__backwards"] = masks["end_x"] < masks["x"]
    work["__incomplete"] = ~masks["successful"]
    classified = work.loc[pass_mask].copy()
    flags: dict[int, dict[str, bool]] = {}
    for chain_id, group in work.groupby("__chain_id", dropna=False):
        group = group.reset_index()
        for _, row in group.loc[group["__pass"]].iterrows():
            original_index = int(row["index"])
            later = group.loc[group["index"].gt(original_index)].head(8)
            flags[original_index] = {
                "shot_chain": bool(later["__shot"].any()),
                "goal_chain": bool(later["__goal"].any()),
                "box_entry_only": bool(later["__box_entry"].any()),
                "danger_only": bool(later["__danger"].any()),
            }
    for key in ["shot_chain", "goal_chain", "box_entry_only", "danger_only"]:
        classified[key] = classified.index.map(lambda idx: flags.get(int(idx), {}).get(key, False))
    classified["backwards_recycle"] = work["__backwards"].reindex(classified.index, fill_value=False).astype(bool)
    classified["incomplete"] = work["__incomplete"].reindex(classified.index, fill_value=False).astype(bool)
    classified["open_play_only"] = ~masks["is_set_piece"].reindex(classified.index, fill_value=False).astype(bool)
    options = {
        "all": pd.Series(True, index=classified.index, dtype="bool"),
        "open_play_only": classified["open_play_only"],
        "danger_only": classified["danger_only"],
        "box_entry_only": classified["box_entry_only"],
        "shot_chain": classified["shot_chain"],
        "goal_chain": classified["goal_chain"],
        "backwards_recycle": classified["backwards_recycle"],
        "incomplete": classified["incomplete"],
    }
    maps = {key: _pitch_points(classified, mask, limit=240) for key, mask in options.items()}
    return {
        "maps": maps,
        "counts": {key: int(mask.sum()) for key, mask in options.items()},
        "note": "Classification looks forward within the same event possession chain where the saved rows allow it.",
    }


def _chain_action_rows(group: pd.DataFrame, limit: int = 10) -> list[dict[str, Any]]:
    sample = group.head(limit)
    rows: list[dict[str, Any]] = []
    for order, row in enumerate(sample.to_dict(orient="records"), start=1):
        rows.append(
            {
                "order": order,
                "minute": _clean_number(row.get("minute", row.get("__minute"))),
                "player": row.get("player"),
                "type": row.get("type"),
                "outcome_type": row.get("outcome_type"),
                "x": _clean_number(row.get("x")),
                "y": _clean_number(row.get("y")),
                "end_x": _clean_number(row.get("end_x")),
                "end_y": _clean_number(row.get("end_y")),
                "xg": _clean_number(row.get("xg")),
                "xt_added": _clean_number(row.get("xt_added")),
            }
        )
    return rows


def _chain_family(group: pd.DataFrame) -> str:
    masks = _event_masks(group)
    start_lane = _lane_key(float(masks["y"].iloc[0])) if not group.empty else "central"
    end_lane = _lane_key(float(masks["end_y"].dropna().iloc[-1])) if not group.empty and len(masks["end_y"].dropna()) else "central"
    types = _text_series(group, "type").str.lower()
    if types.str.contains("cross", na=False).any():
        route = "cross"
    elif masks["is_carry"].sum() >= 2:
        route = "carry progression"
    elif masks["box_entry"].any():
        route = "box entry"
    else:
        route = "final third progression"
    return f"{_lane_label(start_lane)} to {_lane_label(end_lane)} {route}"


def _build_repeated_possession_chains(df: pd.DataFrame, team_xt_actions: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"families": [], "examples": [], "note": "No event rows were available for possession chain grouping."}
    work = _possession_chain_frame(df)
    masks = _event_masks(work)
    work["__shot"] = masks["is_shot"]
    work["__goal"] = masks["is_goal"]
    work["__box_entry"] = masks["box_entry"]
    work["__final_third_entry"] = masks["final_third_entry"]
    if "xt_added" not in work.columns and isinstance(team_xt_actions, pd.DataFrame) and not team_xt_actions.empty and "event_index" in work.columns and "event_index" in team_xt_actions.columns:
        xt_map = pd.to_numeric(team_xt_actions.set_index("event_index").get("xt_added"), errors="coerce").fillna(0.0).to_dict()
        work["xt_added"] = work["event_index"].map(xt_map).fillna(0.0)
    if "xt_added" not in work.columns:
        work["xt_added"] = 0.0
    examples: list[dict[str, Any]] = []
    family_rows: dict[str, dict[str, Any]] = {}
    for chain_id, group in work.groupby("__chain_id", dropna=False):
        local = group.copy()
        if len(local) < 2:
            continue
        ended_danger = bool(local["__shot"].any() or local["__goal"].any() or local["__box_entry"].any() or local["__final_third_entry"].any() or _sum_numeric(local, "xt_added") >= 0.12)
        if not ended_danger:
            continue
        family = _chain_family(local)
        entry = family_rows.setdefault(family, {"chain_family": family, "count": 0, "shots": 0, "goals": 0, "xg": 0.0, "xT": 0.0, "example_matches": []})
        entry["count"] += 1
        entry["shots"] += int(local["__shot"].sum())
        entry["goals"] += int(local["__goal"].sum())
        entry["xg"] = round(_safe_float(entry["xg"], 0.0) + _sum_numeric(local, "xg"), 3)
        entry["xT"] = round(_safe_float(entry["xT"], 0.0) + _sum_numeric(local, "xt_added"), 3)
        match_id = int(local["match_id"].iloc[0]) if pd.notna(local["match_id"].iloc[0]) else None
        if match_id is not None and match_id not in entry["example_matches"]:
            entry["example_matches"].append(match_id)
        if len(examples) < 10:
            examples.append(
                {
                    "chain_id": int(chain_id),
                    "chain_family": family,
                    "match_id": match_id,
                    "minute": _clean_number(local["__minute"].iloc[0]),
                    "outcome": "goal" if bool(local["__goal"].any()) else "shot" if bool(local["__shot"].any()) else "box entry" if bool(local["__box_entry"].any()) else "high xT or final third entry",
                    "pitch_path": _pitch_points(local, pd.Series(True, index=local.index, dtype="bool"), limit=12),
                    "actions": _chain_action_rows(local, limit=12),
                }
            )
    families = sorted(family_rows.values(), key=lambda row: (_safe_float(row.get("goals"), 0.0), _safe_float(row.get("shots"), 0.0), _safe_float(row.get("count"), 0.0)), reverse=True)[:12]
    return {
        "families": families,
        "examples": examples,
        "note": "Possession chains are approximated from consecutive same team event rows and are limited to keep the page fast.",
    }


def _build_goal_shot_sequence_browser(df: pd.DataFrame, team_scored_shots: pd.DataFrame, team_xt_actions: pd.DataFrame) -> dict[str, Any]:
    work = _possession_chain_frame(df)
    if work.empty:
        return {"categories": {}, "note": "No event rows available for sequence browsing."}
    masks = _event_masks(work)
    if "xg" not in work.columns and not team_scored_shots.empty and "event_index" in work.columns and "event_index" in team_scored_shots.columns:
        xg_map = pd.to_numeric(team_scored_shots.set_index("event_index").get("xg"), errors="coerce").fillna(0.0).to_dict()
        work["xg"] = work["event_index"].map(xg_map).fillna(0.0)
    if "xt_added" not in work.columns and not team_xt_actions.empty and "event_index" in work.columns and "event_index" in team_xt_actions.columns:
        xt_map = pd.to_numeric(team_xt_actions.set_index("event_index").get("xt_added"), errors="coerce").fillna(0.0).to_dict()
        work["xt_added"] = work["event_index"].map(xt_map).fillna(0.0)
    if "xg" not in work.columns:
        work["xg"] = 0.0
    if "xt_added" not in work.columns:
        work["xt_added"] = 0.0

    def sequence_from_group(group: pd.DataFrame, label: str) -> dict[str, Any]:
        players = [str(item) for item in group.get("player", pd.Series(dtype=str)).dropna().astype(str).unique().tolist() if str(item).strip()]
        shots = group.loc[_event_masks(group)["is_shot"]].copy()
        final = shots.sort_values("xg", ascending=False).head(1).to_dict(orient="records")
        return {
            "match_id": int(group["match_id"].iloc[0]) if pd.notna(group["match_id"].iloc[0]) else None,
            "minute": _clean_number(group["__minute"].min()),
            "players_involved": players[:8],
            "action_path": _pitch_points(group, pd.Series(True, index=group.index, dtype="bool"), limit=14),
            "actions": _chain_action_rows(group, limit=14),
            "final_shot": final[0] if final else {},
            "xg": round(_sum_numeric(group, "xg"), 3),
            "xT": round(_sum_numeric(group, "xt_added"), 3),
            "outcome": label,
        }

    buckets = {"goals": [], "big_chances": [], "highest_xg_shots": [], "highest_xt_chains": []}
    scored_groups: list[tuple[float, float, pd.DataFrame]] = []
    for _chain_id, group in work.groupby("__chain_id", dropna=False):
        if len(group) < 2:
            continue
        group_masks = _event_masks(group)
        if not group_masks["is_shot"].any() and not group_masks["is_goal"].any() and _sum_numeric(group, "xt_added") < 0.12:
            continue
        scored_groups.append((_sum_numeric(group, "xg"), _sum_numeric(group, "xt_added"), group))
        if group_masks["is_goal"].any() and len(buckets["goals"]) < 8:
            buckets["goals"].append(sequence_from_group(group, "goal"))
        if (_sum_numeric(group, "xg") >= 0.20 or group_masks["start_in_box"].sum() > 0) and len(buckets["big_chances"]) < 8:
            buckets["big_chances"].append(sequence_from_group(group, "big chance"))
    for _xg, _xt, group in sorted(scored_groups, key=lambda item: item[0], reverse=True)[:8]:
        buckets["highest_xg_shots"].append(sequence_from_group(group, "highest xG shot"))
    for _xg, _xt, group in sorted(scored_groups, key=lambda item: item[1], reverse=True)[:8]:
        buckets["highest_xt_chains"].append(sequence_from_group(group, "highest xT chain"))
    return {
        "categories": buckets,
        "note": "Sequence browser is compact and uses the saved event order within each possession chain.",
    }


def _build_goalmouth_view(df: pd.DataFrame, own_shot_mask: pd.Series) -> dict[str, Any]:
    points = _goalmouth_points(df, own_shot_mask, limit=220)
    if not points:
        return {
            "available": False,
            "points": [],
            "note": "Goalmouth coordinates are missing in the saved event rows, so the page falls back to pitch based shot maps.",
        }
    return {
        "available": True,
        "points": points,
        "goals": [row for row in points if bool(row.get("is_goal"))],
        "saved_shots": [row for row in points if "save" in str(row.get("outcome_type") or row.get("type") or "").lower()],
        "blocked_shots": [row for row in points if "block" in str(row.get("outcome_type") or row.get("type") or "").lower()],
        "off_target_shots": [row for row in points if "off" in str(row.get("outcome_type") or row.get("type") or "").lower()],
        "note": "Markers are xG weighted where xG is available in the event payload.",
    }


def _build_defensive_control_funnel(
    opponent_df: pd.DataFrame,
    opponent_masks: dict[str, pd.Series],
    opponent_rows_available: bool,
    opp_xg_total: float,
    opp_shot_mask: pd.Series,
) -> dict[str, Any]:
    if not opponent_rows_available or opponent_df.empty:
        return {"available": False, "warning": "Opponent rows are incomplete, so the defensive control funnel is unavailable."}
    opponent_attacks = int((opponent_masks["is_move"] | opponent_masks["is_shot"]).sum())
    final_third = int(opponent_masks["final_third_entry"].sum())
    box_entries = int(opponent_masks["box_entry"].sum())
    shots = int(opp_shot_mask.sum())
    goals = int(opponent_masks["is_goal"].sum())
    return {
        "available": True,
        "warning": "",
        "steps": [
            {"label": "Opponent attacks", "value": opponent_attacks},
            {"label": "Opponent final third entries", "value": final_third},
            {"label": "Opponent box entries", "value": box_entries},
            {"label": "Opponent shots", "value": shots},
            {"label": "Opponent goals", "value": goals},
        ],
        "metrics": {
            "stopped_before_final_third": max(opponent_attacks - final_third, 0),
            "stopped_before_box": max(final_third - box_entries, 0),
            "box_entry_to_shot_rate": _pct(shots, max(box_entries, 1)),
            "shot_to_goal_rate": _pct(goals, max(shots, 1)),
            "xg_conceded_per_shot": round(float(opp_xg_total) / max(shots, 1), 3),
        },
        "note": "Opponent attacks are event based. This does not measure every possession that failed before becoming an event.",
    }


def _build_duel_control(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"available": False, "note": "No rows available for duel control."}
    type_l = _text_series(df, "type_l").str.lower()
    if type_l.str.strip().eq("").all():
        type_l = _text_series(df, "type").str.lower()
    qual_l = _text_series(df, "qual_tags").str.lower()
    outcome_l = _text_series(df, "outcome_l").str.lower()
    if outcome_l.str.strip().eq("").all():
        outcome_l = _text_series(df, "outcome_type").str.lower()
    duel_mask = type_l.str.contains("duel|aerial|challenge|tackle", regex=True, na=False) | qual_l.str.contains("duel|aerial", regex=True, na=False)
    if not duel_mask.any():
        return {"available": False, "note": "Duel tags or event types were not present in the saved rows."}
    won = duel_mask & outcome_l.str.contains("won|success|successful|complete", regex=True, na=False)
    lost = duel_mask & outcome_l.str.contains("lost|fail|unsuccessful|incomplete", regex=True, na=False)
    aerial = duel_mask & (type_l.str.contains("aerial", na=False) | qual_l.str.contains("aerial|header", regex=True, na=False))
    ground = duel_mask & ~aerial
    player_col = _player_col(df)
    rows = []
    if player_col:
        local = pd.DataFrame({"player": df[player_col].astype(str), "duel": duel_mask.astype(int), "won": won.astype(int)})
        grouped = local.loc[duel_mask].groupby("player", dropna=False).agg(total_duels=("duel", "sum"), duels_won=("won", "sum")).reset_index()
        grouped["win_rate"] = grouped.apply(lambda row: _pct(row["duels_won"], max(row["total_duels"], 1)), axis=1)
        rows = grouped.sort_values(["duels_won", "total_duels"], ascending=False).head(10).to_dict(orient="records")
    zone_rows: list[dict[str, Any]] = []
    masks = _event_masks(df)
    for zone in ["left", "central", "right"]:
        zone_mask = duel_mask & masks["y"].map(_lane_key).eq(zone)
        zone_rows.append({"zone": zone, "label": _lane_label(zone), "duels": int(zone_mask.sum()), "win_rate": _pct(int((zone_mask & won).sum()), max(int(zone_mask.sum()), 1))})
    return {
        "available": True,
        "total_duels": int(duel_mask.sum()),
        "duels_won": int(won.sum()),
        "duels_lost": int(lost.sum()),
        "aerial_duels_won": int((aerial & won).sum()),
        "ground_duels_won": int((ground & won).sum()),
        "duel_locations": _pitch_points(df, duel_mask, limit=240),
        "top_duel_players": rows,
        "duel_win_rate_by_zone": zone_rows,
        "note": "Duel control depends on provider event and qualifier availability.",
    }


def _build_pressing_effect(
    df: pd.DataFrame,
    opponent_df: pd.DataFrame,
    opponent_rows_available: bool,
) -> dict[str, Any]:
    masks = _event_masks(df)
    type_l = _text_series(df, "type_l").str.lower()
    if type_l.str.strip().eq("").all():
        type_l = _text_series(df, "type").str.lower()
    pressure_actions = masks["high_regain"] | (masks["is_defensive"] & masks["x"].ge(55.0)) | type_l.str.contains("pressure|press|challenge", regex=True, na=False)
    result = {
        "pressure_actions": int(pressure_actions.sum()),
        "high_regains": int(masks["high_regain"].sum()),
        "regain_to_shot_within_15_seconds": _fast_attacks_after_regains(df, masks),
        "regain_to_box_entry_within_15_seconds": 0,
        "forced_backwards": 0,
        "forced_long": 0,
        "forced_out_of_play": 0,
        "opponent_escaped_pressure": 0,
        "shots_conceded_after_failed_pressure": 0,
        "note": "Pressing effect is inferred from event order, regains, defensive actions and next opponent actions where available.",
    }
    if not opponent_rows_available or opponent_df.empty:
        result["warning"] = "Opponent rows are incomplete, so forced actions and escapes are limited."
        return result
    ordered_own = _event_order_frame(df)
    ordered_opp = _event_order_frame(opponent_df)
    own_masks = _event_masks(ordered_own)
    opp_masks = _event_masks(ordered_opp)
    own_pressure = ordered_own.loc[own_masks["high_regain"] | (own_masks["is_defensive"] & own_masks["x"].ge(55.0))].copy()
    forced_back = forced_long = forced_out = escaped = failed_shots = box_after = 0
    for row in own_pressure.head(350).to_dict(orient="records"):
        match_id = _safe_float(row.get("match_id"), -1.0)
        minute = _safe_float(row.get("__minute"), -1.0)
        nxt = ordered_opp.loc[(pd.to_numeric(ordered_opp["match_id"], errors="coerce").eq(match_id)) & (pd.to_numeric(ordered_opp["__minute"], errors="coerce").gt(minute)) & (pd.to_numeric(ordered_opp["__minute"], errors="coerce").le(minute + 0.25))].head(3)
        if nxt.empty:
            continue
        nm = _event_masks(nxt)
        dx = nm["end_x"] - nm["x"]
        forced_back += int((dx < -8).sum())
        forced_long += int((((dx ** 2) + ((nm["end_y"] - nm["y"]) ** 2)) ** 0.5 > 45).sum())
        forced_out += int(_text_series(nxt, "outcome_type").str.lower().str.contains("out|throw|corner", regex=True, na=False).sum())
        escaped += int((nm["final_third_entry"] | nm["box_entry"]).sum())
        failed_shots += int(nm["is_shot"].sum())
        box_after += int(nm["box_entry"].sum())
    result.update(
        {
            "forced_backwards": forced_back,
            "forced_long": forced_long,
            "forced_out_of_play": forced_out,
            "opponent_escaped_pressure": escaped,
            "shots_conceded_after_failed_pressure": failed_shots,
            "regain_to_box_entry_within_15_seconds": box_after,
            "warning": "",
        }
    )
    return result


def _build_defensive_sequence_browser(opponent_df: pd.DataFrame, opponent_rows_available: bool) -> dict[str, Any]:
    if not opponent_rows_available or opponent_df.empty:
        return {"available": False, "warning": "Opponent rows are incomplete, so danger conceded sequences are unavailable.", "sequences": []}
    work = _possession_chain_frame(opponent_df)
    sequences: list[dict[str, Any]] = []
    for chain_id, group in work.groupby("__chain_id", dropna=False):
        masks = _event_masks(group)
        high_xt = _sum_numeric(group, "xt_added") >= 0.12
        if not (masks["is_shot"].any() or masks["is_goal"].any() or masks["box_entry"].any() or high_xt):
            continue
        danger_type = "goal" if masks["is_goal"].any() else "shot" if masks["is_shot"].any() else "box entry" if masks["box_entry"].any() else "high xT action"
        if masks["box_entry"].any() and masks["is_shot"].any():
            problem = "box entry became a shot"
        elif high_xt:
            problem = "opponent moved through a valuable lane"
        elif masks["final_third_entry"].any():
            problem = "opponent reached the final third"
        else:
            problem = "opponent danger chain"
        players = [str(item) for item in group.get("player", pd.Series(dtype=str)).dropna().astype(str).unique().tolist() if str(item).strip()]
        sequences.append(
            {
                "chain_id": int(chain_id),
                "match_id": int(group["match_id"].iloc[0]) if pd.notna(group["match_id"].iloc[0]) else None,
                "minute": _clean_number(group["__minute"].min()),
                "danger_type": danger_type,
                "problem_tag": problem,
                "action_path": _pitch_points(group, pd.Series(True, index=group.index, dtype="bool"), limit=14),
                "players_involved": players[:8],
                "final_outcome": danger_type,
                "xT": round(_sum_numeric(group, "xt_added"), 3),
                "xg": round(_sum_numeric(group, "xg"), 3),
            }
        )
    return {
        "available": True,
        "sequences": sorted(sequences, key=lambda row: (_safe_float(row.get("xg"), 0.0), _safe_float(row.get("xT"), 0.0)), reverse=True)[:16],
        "warning": "",
        "note": "Opponent sequences use available opponent rows and must be checked with video before judging structure.",
    }


def _routine_groups_for_mask(
    frame: pd.DataFrame,
    base_mask: pd.Series,
    shot_mask: pd.Series,
    xg_frame: pd.DataFrame,
    label: str,
) -> list[dict[str, Any]]:
    if frame.empty or not base_mask.any():
        return []
    masks = _event_masks(frame)
    delivery_mask = base_mask & (masks["is_pass"] | masks["is_cross"])
    rows: dict[str, dict[str, Any]] = {}
    for zone in ["left", "central", "right"]:
        zone_mask = delivery_mask & masks["y"].map(_lane_key).eq(zone)
        if not zone_mask.any():
            continue
        group_key = f"{label} {_lane_label(zone)} delivery"
        group_shots = int((base_mask & shot_mask & masks["y"].map(_lane_key).eq(zone)).sum())
        xg_zone_total = 0.0
        if not xg_frame.empty:
            xg_masks = _event_masks(xg_frame)
            xg_zone_total = _sum_numeric(xg_frame.loc[xg_masks["y"].map(_lane_key).eq(zone)].copy(), "xg")
        rows[group_key] = {
            "routine_group": group_key,
            "count_used": int(zone_mask.sum()),
            "shots_created": group_shots,
            "goals_created": int((base_mask & masks["is_goal"] & masks["y"].map(_lane_key).eq(zone)).sum()),
            "xg_created": round(xg_zone_total, 3),
            "xa_created": round(_sum_numeric(frame.loc[zone_mask].copy(), "xa"), 3),
            "first_contact_won": int((zone_mask & masks["successful"]).sum()),
            "second_ball_retained": int((zone_mask & masks["successful"]).sum()),
            "shot_rate": _pct(group_shots, max(int(zone_mask.sum()), 1)),
            "main_target_zones": _lane_summary(frame, zone_mask),
            "examples": _pitch_points(frame, zone_mask, limit=8),
        }
    return sorted(rows.values(), key=lambda row: (int(row["shots_created"]), int(row["count_used"])), reverse=True)[:8]


def _enhance_set_piece_profile(
    set_pieces: dict[str, Any],
    df: pd.DataFrame,
    opponent_df: pd.DataFrame,
    masks: dict[str, pd.Series],
    opponent_masks: dict[str, pd.Series],
    own_shot_mask: pd.Series,
    opponent_shot_mask: pd.Series,
    team_scored_shots: pd.DataFrame,
    opponent_scored_shots: pd.DataFrame,
    opponent_rows_available: bool,
) -> dict[str, Any]:
    out = copy.deepcopy(set_pieces)
    section_specs = [
        ("throw_ins", "Throw in", masks["is_throw_in"], opponent_masks.get("is_throw_in", pd.Series(False, index=opponent_df.index, dtype="bool")) if opponent_rows_available else pd.Series(False, index=opponent_df.index, dtype="bool")),
        ("corners", "Corner", masks["is_corner"], opponent_masks.get("is_corner", pd.Series(False, index=opponent_df.index, dtype="bool")) if opponent_rows_available else pd.Series(False, index=opponent_df.index, dtype="bool")),
        ("free_kicks", "Free kick", masks["is_free_kick"], opponent_masks.get("is_free_kick", pd.Series(False, index=opponent_df.index, dtype="bool")) if opponent_rows_available else pd.Series(False, index=opponent_df.index, dtype="bool")),
    ]
    examples: dict[str, list[dict[str, Any]]] = {"best_attacking_routines": [], "worst_conceded_routines": [], "highest_xg_routines": [], "goal_routines": []}
    for key, label, own_mask, opp_mask in section_specs:
        section = out.get(key, {}) if isinstance(out.get(key), dict) else {}
        section_for = section.get("for", {}) if isinstance(section.get("for"), dict) else {}
        section_against = section.get("against", {}) if isinstance(section.get("against"), dict) else {}
        routine_groups = _routine_groups_for_mask(df, own_mask, own_shot_mask, team_scored_shots, label)
        conceded_groups = _routine_groups_for_mask(opponent_df, opp_mask, opponent_shot_mask, opponent_scored_shots, f"Conceded {label}") if opponent_rows_available else []
        section_for["routine_groups"] = routine_groups
        section_against["routine_groups"] = conceded_groups
        section_for["first_contact_won"] = sum(int(row.get("first_contact_won") or 0) for row in routine_groups)
        section_for["second_ball_retained"] = sum(int(row.get("second_ball_retained") or 0) for row in routine_groups)
        section_for["shot_rate"] = _pct(_safe_float(section_for.get("shots"), 0.0), max(_safe_float(section_for.get("events"), 0.0), 1.0))
        section_for["main_target_zones"] = _lane_summary(df, own_mask)
        section_against["first_contact_lost"] = sum(int(row.get("first_contact_won") or 0) for row in conceded_groups)
        section_against["second_ball_lost"] = sum(int(row.get("second_ball_retained") or 0) for row in conceded_groups)
        section_against["danger_zones"] = _lane_summary(opponent_df, opp_mask) if opponent_rows_available else []
        section["for"] = section_for
        section["against"] = section_against
        out[key] = section
        for row in routine_groups[:2]:
            examples["best_attacking_routines"].append({"type": label, **row})
            if _safe_float(row.get("xg_created"), 0.0) > 0:
                examples["highest_xg_routines"].append({"type": label, **row})
            if int(row.get("goals_created") or 0) > 0:
                examples["goal_routines"].append({"type": label, **row})
        for row in conceded_groups[:2]:
            examples["worst_conceded_routines"].append({"type": label, **row})
    out["routine_examples"] = {key: rows[:8] for key, rows in examples.items()}
    out["routine_grouping_note"] = "Routine groups use delivery lane and set piece type from event data. Contact and second ball are proxies based on successful follow up events."
    return out


def _build_season_comparison_visual(multi_season_profile: dict[str, Any], metric_radar: list[dict[str, Any]], current_season: str) -> dict[str, Any]:
    rows = multi_season_profile.get("rows") if isinstance(multi_season_profile, dict) else []
    if not isinstance(rows, list):
        rows = []
    radar = _radar_row_by_key(metric_radar)
    metrics = [
        ("goals_for", "Goals for", True),
        ("goals_against", "Goals against", False),
        ("shots_for", "Shots for", True),
        ("shots_against", "Shots against", False),
        ("xg_for", "xG for", True),
        ("xg_against", "xG against", False),
        ("xa", "xA", True),
        ("xt", "xT", True),
        ("final_third_entries_for", "Final third entries", True),
        ("box_entries_for", "Box entries", True),
        ("high_regains", "High regains", True),
        ("set_piece_shots_for", "Set piece threat", True),
        ("defensive_actions", "Defensive exposure", False),
    ]
    current = next((row for row in rows if isinstance(row, dict) and str(row.get("season")) == str(current_season)), {})
    previous = _previous_season_row(multi_season_profile, current_season) or {}
    visual_rows = []
    for key, label, higher_is_better in metrics:
        rank_row = radar.get(f"{key}_per_match", {})
        if key == "set_piece_shots_for":
            rank_row = radar.get("set_piece_shots_for_per_match", {})
        if key == "final_third_entries_for":
            rank_row = radar.get("final_third_entries_for_per_match", {})
        if key == "box_entries_for":
            rank_row = radar.get("box_entries_for_per_match", {})
        visual_rows.append(
            {
                "key": key,
                "label": label,
                "current_value": _clean_number(current.get(key)),
                "previous_value": _clean_number(previous.get(key)),
                "trend": _trend_arrow(current.get(key), previous.get(key), lower_is_better=not higher_is_better),
                "rank_context": rank_row.get("rank_text"),
                "percentile": _clean_number(rank_row.get("percentile")),
                "higher_is_better": higher_is_better,
            }
        )
    return {
        "rows": visual_rows,
        "previous_season": previous.get("season") if previous else None,
        "note": "Previous season rows are shown only when saved files exist for the selected team.",
    }


def _build_video_checks_required(
    overview: dict[str, Any],
    style_tags: list[str],
    data_quality: dict[str, Any],
    opponent_rows_available: bool,
) -> dict[str, Any]:
    checks = [
        {
            "category": "Attacking patterns",
            "check": "Verify whether final third and box entries come from repeatable structures rather than one off transition moments.",
            "trigger": f"{overview.get('final_third_entries', 0)} final third entries and {overview.get('box_entries', 0)} box entries in saved rows.",
        },
        {
            "category": "Defensive exposure",
            "check": "Do not overread defensive action volume. Check territory, opponent possession, pressure exposure and whether attacks were stopped before danger developed.",
            "trigger": f"{overview.get('defensive_actions', 0)} defensive actions logged.",
        },
        {
            "category": "Set piece routines",
            "check": "Verify delivery type, first contact, second ball spacing, screen blocks and rest defence before calling it a strong routine.",
            "trigger": f"{overview.get('set_piece_threat', 0)} set piece shots created.",
        },
        {
            "category": "Transition risk",
            "check": "Check failed pressure moments and protection behind the first line, especially after high regains or attempted counter pressure.",
            "trigger": f"{overview.get('high_regains', 0)} high regains logged.",
        },
        {
            "category": "Player influence",
            "check": "Check whether top player influence comes from role responsibility, team dependency or genuinely high value actions.",
            "trigger": "Player contribution categories are event based and need role context.",
        },
        {
            "category": "Data coverage",
            "check": "Confirm missing opponent rows, goalkeeper inference and goalmouth coordinate gaps before using conceded or goalmouth conclusions.",
            "trigger": "Opponent rows available." if opponent_rows_available else "Opponent rows incomplete.",
        },
    ]
    if style_tags:
        checks.insert(1, {"category": "Style tags", "check": "Use video to confirm that the generated style tags match the actual team behaviour.", "trigger": ", ".join(style_tags)})
    return {
        "checks": checks,
        "survivorship_bias_warning": "Visible defensive events are actions after danger or pressure has already reached the team. They do not prove team shape without territory, possession, pressure exposure and missing context checks.",
        "short_data_quality_warning": "; ".join(str(item) for item in data_quality.get("notes", [])[:2]) if isinstance(data_quality.get("notes"), list) else "",
    }

def _build_dashboard_payload(
    team_df: pd.DataFrame,
    source_df: pd.DataFrame,
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    team: str,
    path: str,
    raw_count: int,
    load_mode: str,
    processed_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    processed_cache = processed_cache if isinstance(processed_cache, dict) else {}
    processed_frames = processed_cache.get("frames") if isinstance(processed_cache.get("frames"), dict) else {}
    cached_league_df = processed_frames.get("cleaned_season_events", pd.DataFrame()) if isinstance(processed_frames, dict) else pd.DataFrame()
    league_df = normalise_event_frame(cached_league_df) if isinstance(cached_league_df, pd.DataFrame) and not cached_league_df.empty else pd.DataFrame()

    context = _season_match_context(
        basedir=basedir,
        nation=nation,
        tier=tier,
        season=season,
        team=team,
        team_df=team_df,
        source_df=source_df,
        season_df=league_df,
    )
    df = normalise_event_frame(context["team_events"]) if not context["team_events"].empty else normalise_event_frame(team_df)
    all_selected_events = normalise_event_frame(context["all_selected_matches_events"]) if not context["all_selected_matches_events"].empty else df.copy()
    opponent_raw_df = normalise_event_frame(context["opponent_events"]) if not context["opponent_events"].empty else pd.DataFrame(columns=all_selected_events.columns)
    opponent_visual_df = _flip_visual_coordinates(opponent_raw_df)

    if league_df.empty:
        try:
            league_df = normalise_event_frame(load_season_events(basedir, nation, tier, season))
        except Exception:
            league_df = pd.DataFrame(columns=all_selected_events.columns)

    metric_context = _selected_metric_context_from_processed_cache(
        processed_cache,
        selected_match_df=all_selected_events,
        match_ids=context["match_ids"],
    )
    if metric_context is None:
        metric_context = _build_expected_metric_context(
            league_df=normalise_event_frame(league_df) if not league_df.empty else pd.DataFrame(),
            selected_match_df=all_selected_events,
        )

    selected_scored = metric_context["selected_scored_shots"] if isinstance(metric_context.get("selected_scored_shots"), pd.DataFrame) else pd.DataFrame()
    league_scored = metric_context["league_scored_shots"] if isinstance(metric_context.get("league_scored_shots"), pd.DataFrame) else pd.DataFrame()
    selected_xa_links = metric_context["selected_xa_links"] if isinstance(metric_context.get("selected_xa_links"), pd.DataFrame) else pd.DataFrame()
    league_xa_links = metric_context["league_xa_links"] if isinstance(metric_context.get("league_xa_links"), pd.DataFrame) else pd.DataFrame()
    selected_xt_actions = metric_context["selected_xt_actions"] if isinstance(metric_context.get("selected_xt_actions"), pd.DataFrame) else pd.DataFrame()
    league_xt_actions = metric_context["league_xt_actions"] if isinstance(metric_context.get("league_xt_actions"), pd.DataFrame) else pd.DataFrame()

    team_scored_shots = _team_scored_shots(selected_scored, team)
    opponent_scored_shots = _opponent_scored_shots(selected_scored, team)
    team_xa_links = _team_metric_frame(selected_xa_links, team)
    team_xt_actions = _team_metric_frame(selected_xt_actions, team)
    opponent_xt_actions = _opponent_metric_frame(selected_xt_actions, team)

    masks = _event_masks(df)
    opponent_masks = _event_masks(opponent_visual_df) if not opponent_visual_df.empty else {}
    match_ids = context["match_ids"]
    match_count = len(match_ids)
    opponent_rows_available = not opponent_visual_df.empty and bool(opponent_masks)
    source_files_used = _source_files_for_scope(basedir, nation, tier, season, path)

    own_shot_mask = masks["is_shot"]
    opp_shot_mask = opponent_masks["is_shot"] if opponent_rows_available else pd.Series(False, index=opponent_visual_df.index, dtype="bool")
    own_xg_total = _sum_numeric(team_scored_shots, "xg")
    own_np_xg_total = _sum_numeric(team_scored_shots.loc[~team_scored_shots.get("shot_family", pd.Series("", index=team_scored_shots.index)).astype(str).eq("penalty")], "xg") if not team_scored_shots.empty else 0.0
    opp_xg_total = _sum_numeric(opponent_scored_shots, "xg")
    opp_np_xg_total = _sum_numeric(opponent_scored_shots.loc[~opponent_scored_shots.get("shot_family", pd.Series("", index=opponent_scored_shots.index)).astype(str).eq("penalty")], "xg") if not opponent_scored_shots.empty else 0.0
    team_xa_total = _sum_numeric(team_xa_links, "xa_raw")
    team_open_play_xa = _sum_numeric(team_xa_links, "open_play_xa_raw")
    team_set_piece_xa = _sum_numeric(team_xa_links, "set_piece_xa_raw")
    team_xt_total = _sum_numeric(team_xt_actions, "xt_added")
    team_open_play_xt = _sum_numeric(team_xt_actions.loc[~team_xt_actions.get("is_set_piece_action", pd.Series(False, index=team_xt_actions.index)).astype(bool)], "xt_added") if not team_xt_actions.empty else 0.0
    team_set_piece_xt = _sum_numeric(team_xt_actions.loc[team_xt_actions.get("is_set_piece_action", pd.Series(False, index=team_xt_actions.index)).astype(bool)], "xt_added") if not team_xt_actions.empty else 0.0
    opponent_xt_proxy = _sum_numeric(opponent_xt_actions, "xt_added")

    opponent_final_third_entries = int(opponent_masks["final_third_entry"].sum()) if opponent_rows_available else 0
    opponent_box_entries = int(opponent_masks["box_entry"].sum()) if opponent_rows_available else 0
    opponent_set_piece_mask = opponent_masks["is_set_piece"] if opponent_rows_available else pd.Series(False, index=opponent_visual_df.index, dtype="bool")

    total_final_third = int(masks["final_third_entry"].sum()) + opponent_final_third_entries
    total_passes = int(masks["is_pass"].sum()) + (int(opponent_masks["is_pass"].sum()) if opponent_rows_available else 0)
    total_events = int(len(df)) + int(len(opponent_raw_df))

    danger_note = "Opponent rows were resolved from saved season or opponent team files."
    if not opponent_rows_available:
        danger_note = "Shots conceded is unavailable because no opponent rows were found in saved season files, opponent direct team files, or saved match file fallbacks for the selected team's match ids."

    players = _build_player_metric_enrichment(_group_players(df, masks), metric_context)
    metric_radar, radar_context = _build_metric_radar(
        selected_team=team,
        league_df=normalise_event_frame(league_df) if not league_df.empty else pd.DataFrame(),
        league_scored_shots=league_scored,
        league_xa_links=league_xa_links,
        league_xt_actions=league_xt_actions,
    )
    league_context = _build_league_context(metric_radar, radar_context)
    common_lineup = _build_common_lineup(df, team, match_count)
    in_possession_shape = _build_shape_profile(
        df=df,
        mask=masks["is_move"] | own_shot_mask,
        mode="in_possession",
        title="In possession positions",
        match_count=match_count,
        method="Average position from own possession actions across the selected season.",
    )
    defensive_shape = _build_shape_profile(
        df=df,
        mask=masks["is_defensive"],
        mode="defensive",
        title="Defensive positions",
        match_count=match_count,
        method="Average position from defensive actions across the selected season.",
    )

    top_xt_passes = _build_xt_action_points(team_xt_actions.loc[team_xt_actions.get("action_type", pd.Series("", index=team_xt_actions.index)).astype(str).isin(["pass", "cross"])] if not team_xt_actions.empty else pd.DataFrame(), limit=90)
    top_xt_carries = _build_xt_action_points(team_xt_actions.loc[team_xt_actions.get("action_type", pd.Series("", index=team_xt_actions.index)).astype(str).eq("carry")] if not team_xt_actions.empty else pd.DataFrame(), limit=90)
    progressive_passes = _pitch_points(df, masks["is_pass"] & masks["successful"] & (masks["final_third_entry"] | masks["box_entry"]), limit=160)
    progressive_carries = _pitch_points(df, masks["is_carry"] & masks["successful"] & (masks["final_third_entry"] | masks["box_entry"]), limit=160)
    pass_maps = {
        "top_xt_passes": top_xt_passes,
        "top_progressive_passes": progressive_passes,
        "final_third_entries": _pitch_points(df, masks["is_pass"] & masks["successful"] & masks["final_third_entry"], limit=160),
        "box_entries": _pitch_points(df, masks["is_pass"] & masks["successful"] & masks["box_entry"], limit=160),
        "all_successful_actions": _pitch_points(df, masks["is_pass"] & masks["successful"], limit=220),
        "all_unsuccessful_actions": _pitch_points(df, masks["is_pass"] & ~masks["successful"], limit=220),
    }
    carry_maps = {
        "top_xt_passes": top_xt_carries,
        "top_progressive_passes": progressive_carries,
        "top_xt_carries": top_xt_carries,
        "progressive_carries": progressive_carries,
        "final_third_entries": _pitch_points(df, masks["is_carry"] & masks["successful"] & masks["final_third_entry"], limit=160),
        "box_entries": _pitch_points(df, masks["is_carry"] & masks["successful"] & masks["box_entry"], limit=160),
        "all_successful_actions": _pitch_points(df, masks["is_carry"] & masks["successful"], limit=220),
        "all_unsuccessful_actions": _pitch_points(df, masks["is_carry"] & ~masks["successful"], limit=220),
    }
    lane_kpis = _build_lane_kpis(df, masks, team_xt_actions)

    overview = {
        "matches_covered": match_count,
        "events_covered": int(len(df)),
        "match_ids_covered": match_ids,
        "goals": int(masks["is_goal"].sum()),
        "goals_against": int(opponent_masks["is_goal"].sum()) if opponent_rows_available else None,
        "shots": int(own_shot_mask.sum()),
        "shots_against": int(opp_shot_mask.sum()) if opponent_rows_available else None,
        "shots_on_target": int(masks["is_on_target"].sum()),
        "shots_on_target_against": int(opponent_masks["is_on_target"].sum()) if opponent_rows_available else None,
        "box_entries": int(masks["box_entry"].sum()),
        "box_entries_against": opponent_box_entries if opponent_rows_available else None,
        "final_third_entries": int(masks["final_third_entry"].sum()),
        "final_third_entries_against": opponent_final_third_entries if opponent_rows_available else None,
        "possession_proxy": round(_pct(int(masks["is_pass"].sum()), max(total_passes, 1)), 2) if total_passes else round(_pct(int(masks["is_pass"].sum()), max(total_events, 1)), 2),
        "field_tilt_proxy": round(_pct(int(masks["final_third_entry"].sum()), max(total_final_third, 1)), 2) if total_final_third else None,
        "defensive_actions": int(masks["is_defensive"].sum()),
        "high_regains": int(masks["high_regain"].sum()),
        "set_piece_threat": int((masks["is_set_piece"] & own_shot_mask).sum()),
        "set_piece_shots_against": int((opponent_set_piece_mask & opp_shot_mask).sum()) if opponent_rows_available else None,
        "xg_for": round(float(own_xg_total), 3),
        "non_penalty_xg_for": round(float(own_np_xg_total), 3),
        "xg_against": round(float(opp_xg_total), 3) if opponent_rows_available else None,
        "non_penalty_xg_against": round(float(opp_np_xg_total), 3) if opponent_rows_available else None,
        "xg_per_shot": round(float(own_xg_total) / max(int(own_shot_mask.sum()), 1), 3),
        "xg_conceded_per_shot": round(float(opp_xg_total) / max(int(opp_shot_mask.sum()), 1), 3) if opponent_rows_available else None,
        "xg_overperformance": round(float(int(masks["is_goal"].sum()) - own_xg_total), 3),
        "xg_underperformance": round(float(own_xg_total - int(masks["is_goal"].sum())), 3),
        "xg_conceded": round(float(opp_xg_total), 3) if opponent_rows_available else None,
        "xg_conceded_per_shot": round(float(opp_xg_total) / max(int(opp_shot_mask.sum()), 1), 3) if opponent_rows_available else None,
        "xa": round(float(team_xa_total), 3),
        "open_play_xa": round(float(team_open_play_xa), 3),
        "set_piece_xa": round(float(team_set_piece_xa), 3),
        "xt": round(float(team_xt_total), 3),
        "open_play_xt": round(float(team_open_play_xt), 3),
        "set_piece_xt": round(float(team_set_piece_xt), 3),
        "xt_conceded_proxy": round(float(opponent_xt_proxy), 3) if opponent_rows_available else None,
    }

    attacking = {
        "shot_locations": _metric_points_with_value(team_scored_shots, limit=260) if not team_scored_shots.empty else _pitch_points(df, own_shot_mask, limit=260),
        "goalmouth_locations": _goalmouth_points(df, own_shot_mask, limit=180),
        "xg_shot_quality_map": _metric_points_with_value(team_scored_shots.sort_values("xg", ascending=False) if not team_scored_shots.empty and "xg" in team_scored_shots.columns else team_scored_shots, limit=260),
        "final_third_pass_locations": _pitch_points(df, masks["is_pass"] & masks["end_x"].ge(FINAL_THIRD_X), limit=260),
        "final_third_entries_by_lane": _lane_summary(df, masks["final_third_entry"]),
        "box_entries_by_lane": _lane_summary(df, masks["box_entry"]),
        "box_entry_locations": _pitch_points(df, masks["box_entry"], limit=220),
        "chance_creation_locations": _pitch_points(team_xa_links.rename(columns={"xa_raw": "xa"}) if not team_xa_links.empty else df, pd.Series(True, index=team_xa_links.index, dtype="bool") if not team_xa_links.empty else (masks["final_third_entry"] | masks["box_entry"] | own_shot_mask), limit=260),
        "xa_chance_creation_map": _pitch_points(team_xa_links.rename(columns={"xa_raw": "xa"}) if not team_xa_links.empty else pd.DataFrame(), pd.Series(True, index=team_xa_links.index, dtype="bool") if not team_xa_links.empty else pd.Series(dtype="bool"), limit=260),
        "xt_action_map": _build_xt_action_points(team_xt_actions, limit=260),
        "xt_heatmap": _heatmap(team_xt_actions, pd.Series(True, index=team_xt_actions.index, dtype="bool")) if not team_xt_actions.empty else {"x_bins": 6, "y_bins": 5, "cells": []},
        "xt_progression_lanes": _lane_summary(team_xt_actions, pd.Series(True, index=team_xt_actions.index, dtype="bool")) if not team_xt_actions.empty else [],
        "top_xt_actions": _build_xt_action_points(team_xt_actions, limit=30),
        "territory_heatmap": _heatmap(df, masks["is_move"] | own_shot_mask),
        "progression_lane_map": _lane_summary(df, masks["final_third_entry"] | masks["box_entry"]),
        "crossing_profile": {
            "crosses": int(masks["is_cross"].sum()),
            "wide_deliveries": int((masks["is_cross"] & masks["wide_action"]).sum()),
            "locations": _pitch_points(df, masks["is_cross"], limit=220),
            "by_lane": _lane_summary(df, masks["is_cross"]),
        },
        "xg": round(float(own_xg_total), 3),
        "non_penalty_xg": round(float(own_np_xg_total), 3),
        "xa": round(float(team_xa_total), 3),
        "open_play_xa": round(float(team_open_play_xa), 3),
        "set_piece_xa": round(float(team_set_piece_xa), 3),
        "xt": round(float(team_xt_total), 3),
        "open_play_xt": round(float(team_open_play_xt), 3),
        "set_piece_xt": round(float(team_set_piece_xt), 3),
        "top_players": _top_from_players(players, "final_third_entries", 8),
        "top_creators": _top_from_players(players, "xa", 8),
        "top_xt_players": _top_from_players(players, "xt", 8),
        "summary_text": f"{team} created {int(own_shot_mask.sum())} shots, {int(masks['box_entry'].sum())} box entries, {round(float(own_xg_total), 2)} xG, {round(float(team_xa_total), 2)} xA and {round(float(team_xt_total), 2)} xT in the covered data.",
    }

    defensive_height = float(masks["x"].loc[masks["is_defensive"]].mean()) if int(masks["is_defensive"].sum()) else None
    defensive = {
        "defensive_action_locations": _pitch_points(df, masks["is_defensive"], limit=260),
        "defensive_height": round(defensive_height, 2) if defensive_height is not None else None,
        "central_protection": int((masks["is_defensive"] & masks["y"].between(PITCH_WIDTH / 3.0, PITCH_WIDTH * 2.0 / 3.0, inclusive="both")).sum()),
        "central_protection_locations": _pitch_points(df, masks["is_defensive"] & masks["y"].between(PITCH_WIDTH / 3.0, PITCH_WIDTH * 2.0 / 3.0, inclusive="both"), limit=220),
        "wide_forcing": int((masks["is_defensive"] & (masks["y"].le(PITCH_WIDTH / 3.0) | masks["y"].ge(PITCH_WIDTH * 2.0 / 3.0))).sum()),
        "wide_forcing_locations": _pitch_points(df, masks["is_defensive"] & (masks["y"].le(PITCH_WIDTH / 3.0) | masks["y"].ge(PITCH_WIDTH * 2.0 / 3.0)), limit=220),
        "central_protection_summary": f"{int((masks['is_defensive'] & masks['y'].between(PITCH_WIDTH / 3.0, PITCH_WIDTH * 2.0 / 3.0, inclusive='both')).sum())} defensive actions were recorded in central zones.",
        "wide_forcing_summary": f"{int((masks['is_defensive'] & (masks['y'].le(PITCH_WIDTH / 3.0) | masks['y'].ge(PITCH_WIDTH * 2.0 / 3.0))).sum())} defensive actions were recorded in wide zones.",
        "shots_conceded_locations": _metric_points_with_value(opponent_scored_shots, limit=260) if opponent_rows_available and not opponent_scored_shots.empty else [],
        "xg_conceded_shot_quality_map": _metric_points_with_value(opponent_scored_shots.sort_values("xg", ascending=False) if opponent_rows_available and not opponent_scored_shots.empty and "xg" in opponent_scored_shots.columns else opponent_scored_shots, limit=260),
        "opponent_xt_threat_map": _build_xt_action_points(opponent_xt_actions, limit=260),
        "high_regain_locations": _pitch_points(df, masks["high_regain"], limit=220),
        "box_entries_conceded": opponent_box_entries if opponent_rows_available else None,
        "box_entries_conceded_by_lane": _lane_summary(opponent_visual_df, opponent_masks["box_entry"]) if opponent_rows_available else [],
        "final_third_entries_conceded": opponent_final_third_entries if opponent_rows_available else None,
        "final_third_entries_conceded_by_lane": _lane_summary(opponent_visual_df, opponent_masks["final_third_entry"]) if opponent_rows_available else [],
        "danger_conceded_heatmap": _heatmap(opponent_visual_df, opponent_masks["is_move"] | opponent_masks["is_shot"]) if opponent_rows_available else {"x_bins": 6, "y_bins": 5, "cells": []},
        "top_players": _top_from_players(players, "defensive_actions", 8),
        "danger_conceded": {
            "available": opponent_rows_available,
            "shots_conceded": int(opp_shot_mask.sum()) if opponent_rows_available else None,
            "shots_on_target_conceded": int(opponent_masks["is_on_target"].sum()) if opponent_rows_available else None,
            "goals_conceded": int(opponent_masks["is_goal"].sum()) if opponent_rows_available else None,
            "shot_locations": _metric_points_with_value(opponent_scored_shots, limit=260) if opponent_rows_available and not opponent_scored_shots.empty else _pitch_points(opponent_visual_df, opp_shot_mask, limit=260) if opponent_rows_available else [],
            "goalmouth_locations": _goalmouth_points(opponent_visual_df, opp_shot_mask, limit=180) if opponent_rows_available else [],
            "box_shots_conceded": int((opp_shot_mask & opponent_masks["start_in_box"]).sum()) if opponent_rows_available else None,
            "set_piece_shots_conceded": int((opp_shot_mask & opponent_masks["is_set_piece"]).sum()) if opponent_rows_available else None,
            "open_play_shots_conceded": int((opp_shot_mask & ~opponent_masks["is_set_piece"]).sum()) if opponent_rows_available else None,
            "xg_conceded": round(float(opp_xg_total), 3) if opponent_rows_available else None,
            "non_penalty_xg_conceded": round(float(opp_np_xg_total), 3) if opponent_rows_available else None,
            "xg_conceded_per_shot": round(float(opp_xg_total) / max(int(opp_shot_mask.sum()), 1), 3) if opponent_rows_available else None,
            "note": danger_note,
        },
        "interpretation_note": "Visible defensive actions are exposure dependent. Read tackles, interceptions and blocks alongside territory, opponent pressure and shots conceded before judging the structure.",
    }

    regain_to_attack = _fast_attacks_after_regains(df, masks)
    transitions = {
        "high_regains": int(masks["high_regain"].sum()),
        "regain_locations": _pitch_points(df, masks["high_regain"], limit=220),
        "regain_to_attack_sequences": regain_to_attack,
        "regain_to_shot_sequences": regain_to_attack,
        "regain_to_box_entry_sequences": regain_to_attack,
        "fast_attacks_after_regains": _fast_attacks_after_regains(df, masks),
        "opponent_high_regains": int(opponent_masks["high_regain"].sum()) if opponent_rows_available else None,
        "opponent_high_regain_locations": _pitch_points(opponent_visual_df, opponent_masks["high_regain"], limit=220) if opponent_rows_available else [],
        "opponent_transition_threat": int((opponent_masks["high_regain"] | opponent_masks["final_third_entry"] | opponent_masks["box_entry"] | opponent_masks["is_shot"]).sum()) if opponent_rows_available else None,
        "opponent_transition_heatmap": _heatmap(opponent_visual_df, opponent_masks["high_regain"] | opponent_masks["final_third_entry"] | opponent_masks["box_entry"] | opponent_masks["is_shot"]) if opponent_rows_available else {"x_bins": 6, "y_bins": 5, "cells": []},
        "opponent_xt_threat_map": _build_xt_action_points(opponent_xt_actions, limit=260),
        "top_players": _top_from_players(players, "high_regains", 8),
        "note": "Fast attacks are counted as a transparent event proxy when a dangerous action follows a high regain within roughly 15 seconds.",
    }

    set_pieces = _build_set_piece_profile(
        own_df=df,
        opponent_df=opponent_visual_df,
        own_masks=masks,
        opponent_masks=opponent_masks,
        own_scored_shots=team_scored_shots.loc[team_scored_shots.get("is_set_piece_action", pd.Series(False, index=team_scored_shots.index)).astype(bool)] if not team_scored_shots.empty else team_scored_shots,
        opponent_scored_shots=opponent_scored_shots.loc[opponent_scored_shots.get("is_set_piece_action", pd.Series(False, index=opponent_scored_shots.index)).astype(bool)] if not opponent_scored_shots.empty else opponent_scored_shots,
        players=players,
        opponent_available=opponent_rows_available,
    )
    set_pieces = _enhance_set_piece_profile(
        set_pieces=set_pieces,
        df=df,
        opponent_df=opponent_visual_df,
        masks=masks,
        opponent_masks=opponent_masks,
        own_shot_mask=own_shot_mask,
        opponent_shot_mask=opp_shot_mask,
        team_scored_shots=team_scored_shots,
        opponent_scored_shots=opponent_scored_shots,
        opponent_rows_available=opponent_rows_available,
    )

    phase_radar_groups, phase_kpi_breakdowns = _build_phase_visuals(
        overview=overview,
        metric_radar=metric_radar,
        players=players,
        transitions=transitions,
        set_pieces=set_pieces,
    )

    attacking_territory = {
        "touches": _heatmap(df, masks["is_move"] | own_shot_mask),
        "final_third_entries": _heatmap(df, masks["final_third_entry"]),
        "box_entries": _heatmap(df, masks["box_entry"]),
        "xT": _heatmap(team_xt_actions, pd.Series(True, index=team_xt_actions.index, dtype="bool")) if not team_xt_actions.empty else {"x_bins": 6, "y_bins": 5, "cells": []},
        "xg_chain": _heatmap(team_scored_shots, pd.Series(True, index=team_scored_shots.index, dtype="bool")) if not team_scored_shots.empty else _heatmap(df, own_shot_mask),
    }
    shot_heatmap = _heatmap(df, own_shot_mask, x_bins=8, y_bins=6)
    xg_map = {
        "points": attacking.get("xg_shot_quality_map", []),
        "heatmap": _heatmap(team_scored_shots, pd.Series(True, index=team_scored_shots.index, dtype="bool"), x_bins=8, y_bins=6) if not team_scored_shots.empty else shot_heatmap,
    }
    shot_maps = {
        "points": attacking.get("shot_locations", []),
        "shot_map": attacking.get("shot_locations", []),
        "shot_heatmap": shot_heatmap,
        "xg_map": xg_map,
    }
    seasonal_defensive_dashboard = {
        "shots_conceded_map": defensive.get("shots_conceded_locations", []),
        "xg_conceded_map": defensive.get("xg_conceded_shot_quality_map", []),
        "defensive_action_heatmap": _heatmap(df, masks["is_defensive"]),
        "high_regains_map": defensive.get("high_regain_locations", []),
        "opponent_box_entry_map": _pitch_points(opponent_visual_df, opponent_masks["box_entry"], limit=220) if opponent_rows_available else [],
        "opponent_final_third_entries": _pitch_points(opponent_visual_df, opponent_masks["final_third_entry"], limit=220) if opponent_rows_available else [],
        "pressure_or_defensive_action_zones": _lane_summary(df, masks["is_defensive"]),
        "defensive_transition_threat_conceded": transitions.get("opponent_transition_heatmap"),
        "danger_conceded_pitch_view": defensive.get("danger_conceded_heatmap"),
        "set_piece_danger_conceded": (
            set_pieces.get("corners", {}).get("against", {}).get("shot_locations", [])
            + set_pieces.get("free_kicks", {}).get("against", {}).get("shot_locations", [])
            + set_pieces.get("throw_ins", {}).get("against", {}).get("shot_locations", [])
        ),
        "defensive_player_contribution_lists": defensive.get("top_players", []),
        "data_warning": None if opponent_rows_available else danger_note,
    }
    set_piece_delivery_maps = {
        "throw_ins": set_pieces.get("throw_ins"),
        "corners": set_pieces.get("corners"),
        "free_kicks": set_pieces.get("free_kicks"),
    }
    player_influence_dashboard = _build_player_influence_dashboard(players)

    phase_buckets = [
        {"key": "scoreline", "title": "Season scoreline", "value": int(masks["is_goal"].sum()), "metric": "goals for", "note": f"{overview['goals_against'] if overview['goals_against'] is not None else 'n/a'} goals against from covered opponent rows."},
        {"key": "shooting", "title": "Shooting balance", "value": int(own_shot_mask.sum()), "metric": "shots for", "note": f"{overview['shots_against'] if overview['shots_against'] is not None else 'n/a'} shots conceded."},
        {"key": "xg", "title": "xG profile", "value": round(float(own_xg_total), 2), "metric": "xG", "note": f"{overview['xg_against'] if overview['xg_against'] is not None else 'n/a'} xG conceded."},
        {"key": "chance_creation", "title": "Chance creation", "value": round(float(team_xa_total), 2), "metric": "xA", "note": f"{round(float(team_xt_total), 2)} xT from valued actions."},
        {"key": "territory", "title": "Territory", "value": int(masks["final_third_entry"].sum()), "metric": "F3 entries", "note": f"Field tilt proxy: {overview['field_tilt_proxy'] if overview['field_tilt_proxy'] is not None else 'n/a'}%."},
        {"key": "box_threat", "title": "Box threat", "value": int(masks["box_entry"].sum()), "metric": "entries", "note": f"{int(own_shot_mask.sum())} shots and {int(masks['is_goal'].sum())} goals in the covered data."},
        {"key": "defensive_work", "title": "Defensive work", "value": int(masks["is_defensive"].sum()), "metric": "actions", "note": "Tackles, interceptions, recoveries, blocks, clearances, fouls and duels."},
        {"key": "transition", "title": "Transitions", "value": int(masks["high_regain"].sum()), "metric": "high regains", "note": "Regain actions in advanced zones."},
        {"key": "set_pieces", "title": "Set pieces", "value": int(masks["is_set_piece"].sum()), "metric": "events", "note": f"{int((masks['is_set_piece'] & own_shot_mask).sum())} set piece shots for."},
    ]

    data_quality_notes: list[str] = []
    if load_mode == "direct_team_file":
        data_quality_notes.append("Loaded the selected team file directly for own team actions, then resolved opponent context from saved season and opponent files.")
    else:
        data_quality_notes.append("Direct team file was not found, so the endpoint used the full season event store and filtered the selected team.")
    data_quality_notes.extend(context["notes"])
    if int(len(df)) == 0:
        data_quality_notes.append("No event rows were available for the selected team after filtering.")
    if not opponent_rows_available:
        data_quality_notes.append(danger_note)
    for model_key in ["xg_quality", "xa_quality", "xt_quality"]:
        model_quality = metric_context.get(model_key)
        if isinstance(model_quality, dict) and model_quality.get("note"):
            data_quality_notes.append(str(model_quality["note"]))

    match_event_counts = _event_count_by_match(all_selected_events)
    style_tags = _style_tags(masks, match_count)
    profile = {
        "team": team,
        "nation": nation,
        "tier": tier,
        "season": season,
        "matches_covered": match_count,
        "event_rows": int(len(df)),
        "opponent_rows": int(len(opponent_raw_df)),
        "style_tags": style_tags,
        "summary_text": f"{team} profile built from {match_count} covered matches and {int(len(df))} own team event rows.",
    }
    season_profile = {
        "overview": overview,
        "phase_buckets": phase_buckets,
        "match_event_counts": match_event_counts,
        "context": {
            "team_events": int(len(df)),
            "opponent_events": int(len(opponent_raw_df)),
            "all_selected_matches_events": int(len(all_selected_events)),
            "match_ids": match_ids,
            "source_modes": context["source_modes"],
        },
    }
    data_quality = {
        "load_mode": load_mode,
        "source_path": path,
        "source_rows": int(raw_count),
        "own_team_rows": int(len(df)),
        "team_rows": int(len(df)),
        "opponent_rows": int(len(opponent_raw_df)),
        "league_rows_used_for_radar": int(len(league_df)),
        "league_teams_compared": int(league_context.get("teams_compared") or 0),
        "match_ids_covered": match_ids,
        "matches_covered": match_count,
        "matches_with_opponent_rows": context["matches_with_opponent_rows"],
        "matches_without_opponent_rows": context["matches_without_opponent_rows"],
        "source_files_used": source_files_used,
        "opponent_rows_available": opponent_rows_available,
        "xg_model_status": metric_context.get("xg_quality"),
        "xa_model_status": metric_context.get("xa_quality"),
        "xt_model_status": metric_context.get("xt_quality"),
        "notes": data_quality_notes,
    }
    multi_season_profile = _build_multi_season_profile(
        basedir=basedir,
        nation=nation,
        tier=tier,
        team=team,
        current_season=season,
        overview=overview,
        data_quality=data_quality,
    )
    for row in multi_season_profile.get("rows", []):
        if row.get("season") == season:
            row["xg_for"] = overview.get("xg_for")
            row["xg_against"] = overview.get("xg_against")
            row["xa"] = overview.get("xa")
            row["xt"] = overview.get("xt")

    seasonal_summary_panel = _build_seasonal_summary_panel(overview, metric_radar, multi_season_profile, season)
    seasonal_radar = _build_seasonal_radar_comparison(metric_radar, multi_season_profile, season)
    seasonal_momentum = _build_seasonal_momentum(df, opponent_visual_df, _match_log(df, masks, basedir, nation, tier, season, team))
    season_phase_verdicts = _build_phase_verdicts(overview, metric_radar, style_tags, opponent_rows_available)
    style_evidence_panel = _build_style_evidence_panel(style_tags, overview, metric_radar, multi_season_profile, season)
    goalkeeper_distribution = _build_goalkeeper_distribution(df)
    final_third_pass_classification = _classify_final_third_pass_maps(df)
    repeated_possession_chains = _build_repeated_possession_chains(df, team_xt_actions)
    goal_shot_sequence_browser = _build_goal_shot_sequence_browser(df, team_scored_shots, team_xt_actions)
    goalmouth_view = _build_goalmouth_view(df, own_shot_mask)
    defensive_control_funnel = _build_defensive_control_funnel(opponent_visual_df, opponent_masks, opponent_rows_available, opp_xg_total, opp_shot_mask)
    duel_control = _build_duel_control(df)
    pressing_effect = _build_pressing_effect(df, opponent_visual_df, opponent_rows_available)
    defensive_sequence_browser = _build_defensive_sequence_browser(opponent_visual_df, opponent_rows_available)
    season_comparison_visual = _build_season_comparison_visual(multi_season_profile, metric_radar, season)

    attacking["goalkeeper_distribution"] = goalkeeper_distribution
    attacking["final_third_pass_classification"] = final_third_pass_classification
    attacking["repeated_possession_chains"] = repeated_possession_chains
    attacking["goal_shot_sequence_browser"] = goal_shot_sequence_browser
    attacking["goalmouth_view"] = goalmouth_view
    defensive["control_funnel"] = defensive_control_funnel
    defensive["duel_control"] = duel_control
    defensive["danger_conceded_sequences"] = defensive_sequence_browser
    transitions["pressing_effect"] = pressing_effect
    seasonal_defensive_dashboard["control_funnel"] = defensive_control_funnel
    seasonal_defensive_dashboard["duel_control"] = duel_control
    seasonal_defensive_dashboard["pressing_effect"] = pressing_effect
    seasonal_defensive_dashboard["danger_conceded_sequences"] = defensive_sequence_browser

    video_checks_required = _build_video_checks_required(overview, style_tags, data_quality, opponent_rows_available)

    player_profile = {
        "players": players,
        "top_players": players[:15],
        "top_attacking_players": _top_from_players(players, "final_third_entries", 8),
        "top_defensive_players": _top_from_players(players, "defensive_actions", 8),
        "top_transition_players": _top_from_players(players, "high_regains", 8),
        "top_set_piece_players": _top_from_players(players, "set_piece_involvement", 8),
        "top_xg_players": _top_from_players(players, "xg", 8),
        "top_xa_players": _top_from_players(players, "xa", 8),
        "top_xt_players": _top_from_players(players, "xt", 8),
    }

    return {
        "team": team,
        "nation": nation,
        "tier": tier,
        "season": season,
        "path": path,
        "rows": int(len(df)),
        "matches": match_count,
        "columns": df.columns.tolist(),
        "type_counts": [{"type": str(k), "count": int(v)} for k, v in df["type"].astype(str).value_counts().head(30).items()] if "type" in df.columns else [],
        "player_counts": [{"player": str(k), "events": int(v)} for k, v in df["player"].astype(str).replace({"": "Unknown"}).value_counts().head(40).items()] if "player" in df.columns else [],
        "phase_buckets": phase_buckets,
        "top_players": players[:15],
        "overview": overview,
        "style_tags": style_tags,
        "seasonal_summary_panel": seasonal_summary_panel,
        "seasonal_radar": seasonal_radar,
        "seasonal_momentum": seasonal_momentum,
        "season_phase_verdicts": season_phase_verdicts,
        "style_evidence_panel": style_evidence_panel,
        "video_checks_required": video_checks_required,
        "season_comparison_visual": season_comparison_visual,
        "match_log": _match_log(df, masks, basedir, nation, tier, season, team),
        "attacking": attacking,
        "defensive": defensive,
        "transitions": transitions,
        "set_pieces": set_pieces,
        "players": players,
        "profile": profile,
        "season_profile": season_profile,
        "league_context": league_context,
        "metric_radar": metric_radar,
        "phase_radar_groups": phase_radar_groups,
        "phase_kpi_breakdowns": phase_kpi_breakdowns,
        "common_lineup": common_lineup,
        "in_possession_shape": in_possession_shape,
        "defensive_shape": defensive_shape,
        "attacking_territory": attacking_territory,
        "shot_maps": shot_maps,
        "shot_heatmap": shot_heatmap,
        "xg_map": xg_map,
        "pass_maps": pass_maps,
        "carry_maps": carry_maps,
        "lane_kpis": lane_kpis,
        "seasonal_defensive_dashboard": seasonal_defensive_dashboard,
        "set_piece_delivery_maps": set_piece_delivery_maps,
        "player_influence_dashboard": player_influence_dashboard,
        "multi_season_profile": multi_season_profile,
        "attacking_profile": attacking,
        "defensive_profile": defensive,
        "transition_profile": transitions,
        "set_piece_profile": set_pieces,
        "player_profile": player_profile,
        "data_quality": data_quality,
        "render_meta": {
            "generated_at": time.time(),
            "raw_rows_loaded_by_default": False,
            "raw_preview_default_limit": 500,
            "model_version": TEAM_SUMMARY_CACHE_VERSION,
            "load_mode": load_mode,
            "processed_cache_hit": bool(processed_cache.get("cache_hit")) if isinstance(processed_cache, dict) else False,
            "processed_cache_rebuilt": bool(processed_cache.get("rebuilt")) if isinstance(processed_cache, dict) else False,
            "processed_cache_root": str(processed_cache.get("root") or "") if isinstance(processed_cache, dict) else "",
            "processed_cache_error": str(processed_cache.get("error") or "") if isinstance(processed_cache, dict) else "",
        },
    }



def get_match_events_table(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    match_id: int,
    limit: int = 5000,
) -> dict[str, Any]:
    events, fixture = load_match_events(
        basedir=basedir,
        nation=nation,
        tier=tier,
        season=season,
        match_id=match_id,
    )
    payload = _serialise_frame(events, limit=limit)
    payload["fixture"] = fixture
    return payload


def get_team_events_table(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    team: str,
    match_id: int | None = None,
    event_type: str | None = None,
    player: str | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    cache_current = _team_analysis_cache_is_current(basedir, nation, tier, season)
    parquet_path = _team_analysis_cache_frame_paths(basedir, nation, tier, season)["cleaned_season_events"]
    if cache_current and parquet_path.exists():
        source_df = _read_team_analysis_parquet_frame(parquet_path)
        team_mask = _team_filter(source_df, team)
        df = source_df.loc[team_mask].copy() if team_mask.any() else pd.DataFrame(columns=source_df.columns)
        path = str(parquet_path)
        raw_count = int(len(source_df))
        load_mode = "team_analysis_parquet_events"
    else:
        df, _source_df, path, raw_count, load_mode = _load_team_scope(basedir, nation, tier, season, team)

    match_col = _match_id_col(df)
    if match_id is not None:
        if not match_col:
            raise ValueError("Season event data does not contain a match id column.")
        df = df.loc[pd.to_numeric(df[match_col], errors="coerce").eq(int(match_id))].copy()

    if event_type:
        type_col = _type_col(df)
        if type_col:
            probe = str(event_type).strip().lower()
            df = df.loc[df[type_col].astype(str).str.lower().str.contains(probe, na=False)].copy()

    if player:
        player_col = _player_col(df)
        if player_col:
            probe = str(player).strip().lower()
            df = df.loc[df[player_col].astype(str).str.lower().str.contains(probe, na=False)].copy()

    payload = _serialise_frame(df, limit=limit)
    payload.update(
        {
            "team": team,
            "path": path,
            "raw_count": int(raw_count),
            "filtered_count": int(len(df)),
            "load_mode": load_mode,
        }
    )
    return payload



def get_team_summary(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    team: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    parquet_rebuilt = False

    if not _team_analysis_cache_is_current(basedir, nation, tier, season):
        _build_team_analysis_parquet_store(basedir, nation, tier, season, force=True)
        parquet_rebuilt = True

    payload = _build_team_summary_from_profile_cache(
        basedir=basedir,
        nation=nation,
        tier=tier,
        season=season,
        team=team,
        parquet_rebuilt=parquet_rebuilt,
    )

    if payload is None:
        _build_team_analysis_parquet_store(basedir, nation, tier, season, force=True)
        parquet_rebuilt = True
        payload = _build_team_summary_from_profile_cache(
            basedir=basedir,
            nation=nation,
            tier=tier,
            season=season,
            team=team,
            parquet_rebuilt=True,
        )

    if payload is None:
        raise ValueError(f"Team '{team}' was not found in the Team Analysis profile store.")

    result = copy.deepcopy(payload)
    manifest = _read_json_dict(_team_analysis_manifest_path(basedir, nation, tier, season)) or {}
    render_meta = result.get("render_meta")
    if not isinstance(render_meta, dict):
        render_meta = {}

    memory_cache_hit = bool(render_meta.get("memory_cache_hit"))
    parquet_profile_hit = bool(render_meta.get("parquet_profile_hit"))
    render_meta.update(
        {
            "cache_hit": bool(memory_cache_hit or parquet_profile_hit),
            "memory_cache_hit": memory_cache_hit,
            "parquet_profile_hit": parquet_profile_hit,
            "parquet_rebuilt": bool(parquet_rebuilt or render_meta.get("parquet_rebuilt")),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "cache_version": TEAM_ANALYSIS_PROCESSED_CACHE_VERSION,
            "profile_store_path": str(_team_analysis_cache_root(basedir, nation, tier, season)),
            "club_profiles_rows": int(manifest.get("row_counts", {}).get("club_profiles") or render_meta.get("club_profiles_rows") or 0),
            "source_fingerprint": str(manifest.get("source_fingerprint") or render_meta.get("source_fingerprint") or ""),
            "load_mode": str(render_meta.get("load_mode") or "team_analysis_parquet_profile"),
            "raw_rows_loaded_by_default": False,
        }
    )
    result["render_meta"] = render_meta
    return result



def list_saved_teams(basedir: Path, nation: str, tier: str, season: str) -> dict[str, Any]:
    events_root = _events_root(basedir)
    scoped_root = events_root / _safe_slug(nation) / _safe_slug(tier)
    legacy_root = events_root / _safe_slug(nation)

    roots: list[Path] = []
    for root in [scoped_root, legacy_root, events_root / f"{_safe_slug(nation)} {_safe_slug(tier)}".strip()]:
        if root not in roots:
            roots.append(root)

    teams_by_key: dict[str, dict[str, Any]] = {}
    season_file = f"{_safe_slug(season)}.csv"

    for root in roots:
        if not root.exists():
            continue

        direct_csv = root / season_file
        if direct_csv.exists():
            raw = _read_csv(direct_csv)
            team_col = _team_col(raw)
            if team_col:
                for team_name in raw[team_col].dropna().astype(str).unique().tolist():
                    key = _norm_team_name(team_name)
                    if key:
                        teams_by_key[key] = {"team": str(team_name), "path": str(direct_csv)}

        for team_dir in sorted([item for item in root.iterdir() if item.is_dir()]):
            if team_dir.name.startswith("_") or team_dir.name.upper() in {"T1", "T2", "T3", "T4", "T5"}:
                continue
            csv_path = team_dir / season_file
            if csv_path.exists():
                key = _norm_team_name(team_dir.name)
                teams_by_key.setdefault(key, {"team": team_dir.name, "path": str(csv_path)})

    teams = sorted(teams_by_key.values(), key=lambda item: str(item.get("team", "")).lower())
    root = scoped_root if scoped_root.exists() else legacy_root
    return {"teams": teams, "root": str(root)}