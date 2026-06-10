from __future__ import annotations

import csv
import json
import re
import shutil
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
from pandas.errors import EmptyDataError, ParserError

from app.services.event_data_service import normalise_event_frame


PROCESSED_STORE_MANIFEST_VERSION = 2


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


def _processed_root(basedir: Path, nation: str, tier: str, season: str) -> Path:
    return basedir / "data" / "Processed" / _safe_slug(nation) / _safe_slug(tier or "T1") / _safe_slug(season)


def processed_paths(basedir: Path, nation: str, tier: str, season: str) -> dict[str, Path]:
    root = _processed_root(basedir, nation, tier, season)
    return {
        "root": root,
        "events": root / "events_clean.parquet",
        "match_index": root / "match_index.parquet",
        "events_by_match": root / "events_by_match",
        "prepared_by_match": root / "prepared_by_match",
        "prepared_season_events": root / "prepared_season_events.parquet",
        "team_match_summary": root / "team_match_summary.parquet",
        "team_style_profiles": root / "team_style_profiles.parquet",
        "momentum_profiles": root / "momentum_profiles.parquet",
        "manifest": root / "manifest.json",
    }


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


def _score_col(df: pd.DataFrame, side: str) -> str | None:
    if side == "home":
        return _find_col(df, ["home_score", "homescore", "homeGoals"])
    return _find_col(df, ["away_score", "awayscore", "awayGoals"])


def _kickoff_col(df: pd.DataFrame) -> str | None:
    return _find_col(df, ["started_at_utc", "start_time", "date", "kickoff"])


def _status_col(df: pd.DataFrame) -> str | None:
    return _find_col(df, ["status", "elapsed", "statusCode"])


def _normalise_team(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", text)


def _period_to_int(value: object) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.notna(numeric):
        return int(numeric)

    key = re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())
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


def _ensure_period_column(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "period" not in out.columns:
        out["period"] = 1
    out["period"] = out["period"].apply(_period_to_int)
    out["period"] = pd.to_numeric(out["period"], errors="coerce").fillna(1).astype("int64")
    return out


def _is_backup_csv(path: Path) -> bool:
    name = path.name.lower()
    stem = path.stem.lower()
    return "__backup" in stem or stem.endswith("_backup") or name.endswith(".tmp")


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


def _read_csv_resilient(path: Path) -> tuple[pd.DataFrame, str]:
    try:
        return pd.read_csv(path, low_memory=False), "normal"
    except EmptyDataError:
        return pd.DataFrame(), "empty"
    except ParserError:
        repaired = _read_csv_with_repaired_field_counts(path)
        if not repaired.empty:
            return repaired, "repaired_field_count"
        try:
            return pd.read_csv(path, engine="python", on_bad_lines="skip"), "python_skip_bad_lines"
        except EmptyDataError:
            return pd.DataFrame(), "empty"


def _read_schedule(basedir: Path, nation: str, tier: str, season: str) -> pd.DataFrame:
    safe_season = _safe_slug(season)
    if "__backup" in safe_season.lower() or safe_season.lower().endswith("_backup"):
        raise ValueError("Backup schedule CSV files are not valid selectable seasons. Select the main season file instead.")

    path = _schedule_root(basedir) / f"{nation} {tier}".strip() / f"{safe_season}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Saved schedule CSV not found: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"Saved schedule CSV is empty: {path}")

    schedule_df, mode = _read_csv_resilient(path)
    if schedule_df.empty:
        raise ValueError(f"Saved schedule CSV has no readable rows: {path}")
    if mode == "python_skip_bad_lines":
        schedule_df.attrs["csv_read_warning"] = f"Schedule CSV required the Python parser and skipped malformed rows: {path}"
    elif mode == "repaired_field_count":
        schedule_df.attrs["csv_read_warning"] = f"Schedule CSV had inconsistent field counts and was repaired while reading: {path}"
    return schedule_df


def _schedule_index(schedule_df: pd.DataFrame) -> pd.DataFrame:
    match_col = _match_id_col(schedule_df)
    home_col, away_col = _home_away_cols(schedule_df)
    if not match_col or not home_col or not away_col:
        raise ValueError("Schedule CSV must contain match id, home team and away team columns.")

    kickoff_col = _kickoff_col(schedule_df)
    status_col = _status_col(schedule_df)
    home_score_col = _score_col(schedule_df, "home")
    away_score_col = _score_col(schedule_df, "away")

    idx = pd.DataFrame()
    idx["match_id"] = pd.to_numeric(schedule_df[match_col], errors="coerce")
    idx["home_team"] = schedule_df[home_col].astype(str)
    idx["away_team"] = schedule_df[away_col].astype(str)
    idx["home_team_norm"] = idx["home_team"].map(_normalise_team)
    idx["away_team_norm"] = idx["away_team"].map(_normalise_team)
    idx["kickoff"] = schedule_df[kickoff_col].astype(str) if kickoff_col else ""
    idx["match_date"] = pd.to_datetime(idx["kickoff"], errors="coerce", utc=True)
    idx["status"] = schedule_df[status_col].astype(str) if status_col else ""
    idx["home_score"] = pd.to_numeric(schedule_df[home_score_col], errors="coerce") if home_score_col else pd.NA
    idx["away_score"] = pd.to_numeric(schedule_df[away_score_col], errors="coerce") if away_score_col else pd.NA
    idx = idx.dropna(subset=["match_id"]).copy()
    idx["match_id"] = idx["match_id"].astype(int)
    return idx


def _events_scope_root(basedir: Path, nation: str, tier: str) -> Path:
    scoped = _events_root(basedir) / _safe_slug(nation) / _safe_slug(tier or "T1")
    if scoped.exists():
        return scoped
    return _events_root(basedir) / _safe_slug(nation)


def _schedule_csv_path(basedir: Path, nation: str, tier: str, season: str) -> Path:
    return _schedule_root(basedir) / f"{nation} {tier}".strip() / f"{_safe_slug(season)}.csv"


def _file_fingerprint(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {
            "path": str(path),
            "exists": False,
            "size": 0,
            "mtime_ns": 0,
        }

    return {
        "path": str(path),
        "exists": True,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _collect_event_csv_jobs(basedir: Path, nation: str, tier: str, season: str) -> tuple[Path, list[tuple[Path, str]]]:
    events_root = _events_scope_root(basedir, nation, tier)
    csv_jobs: list[tuple[Path, str]] = []

    if not events_root.exists():
        return events_root, csv_jobs

    season_file_name = f"{_safe_slug(season)}.csv"
    direct_csv = events_root / season_file_name
    if direct_csv.exists() and direct_csv.is_file() and not _is_backup_csv(direct_csv):
        csv_jobs.append((direct_csv, "__season_event_file"))

    for team_dir in sorted([item for item in events_root.iterdir() if item.is_dir()]):
        if team_dir.name.startswith("_") or re.fullmatch(r"T\d+", team_dir.name, flags=re.IGNORECASE):
            continue

        csv_path = team_dir / season_file_name
        if csv_path.exists() and csv_path.is_file() and not _is_backup_csv(csv_path):
            csv_jobs.append((csv_path, team_dir.name))

    seen: set[Path] = set()
    deduped: list[tuple[Path, str]] = []
    for csv_path, team_folder in csv_jobs:
        resolved = csv_path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append((csv_path, team_folder))

    return events_root, deduped


def _source_snapshot(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    csv_jobs: list[tuple[Path, str]],
) -> dict[str, Any]:
    schedule_path = _schedule_csv_path(basedir, nation, tier, season)
    return {
        "version": PROCESSED_STORE_MANIFEST_VERSION,
        "nation": str(nation),
        "tier": str(tier),
        "season": str(season),
        "schedule": _file_fingerprint(schedule_path),
        "source_files": [
            {
                **_file_fingerprint(csv_path),
                "team_folder": str(team_folder),
            }
            for csv_path, team_folder in sorted(csv_jobs, key=lambda item: str(item[0]))
        ],
    }


def _read_manifest(paths: dict[str, Path]) -> dict[str, Any] | None:
    manifest_path = paths.get("manifest")
    if manifest_path is None or not manifest_path.exists():
        return None

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    return raw if isinstance(raw, dict) else None


def _write_manifest(paths: dict[str, Path], payload: dict[str, Any]) -> None:
    manifest_path = paths["manifest"]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = manifest_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp_path.replace(manifest_path)


def _fingerprint_key(item: dict[str, Any]) -> tuple[str, int, int]:
    return (
        str(item.get("path", "")),
        int(item.get("size") or 0),
        int(item.get("mtime_ns") or 0),
    )


def _source_change_summary(manifest: dict[str, Any] | None, snapshot: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    missing_outputs = [
        name
        for name in ["events", "match_index"]
        if not paths[name].exists()
    ]

    if missing_outputs:
        return {
            "up_to_date": False,
            "reason": "missing_outputs",
            "message": "Processed Parquet outputs are missing.",
            "changed_files": [],
            "missing_outputs": missing_outputs,
        }

    if manifest is None:
        return {
            "up_to_date": False,
            "reason": "missing_manifest",
            "message": "Processed manifest is missing, so source freshness cannot be trusted.",
            "changed_files": [],
            "missing_outputs": [],
        }

    if int(manifest.get("version") or 0) != PROCESSED_STORE_MANIFEST_VERSION:
        return {
            "up_to_date": False,
            "reason": "manifest_version_changed",
            "message": "Processed manifest version changed.",
            "changed_files": [],
            "missing_outputs": [],
        }

    previous = manifest.get("source_snapshot")
    if not isinstance(previous, dict):
        return {
            "up_to_date": False,
            "reason": "missing_source_snapshot",
            "message": "Processed manifest does not contain a source snapshot.",
            "changed_files": [],
            "missing_outputs": [],
        }

    changed_files: list[str] = []

    previous_schedule = previous.get("schedule") if isinstance(previous.get("schedule"), dict) else {}
    current_schedule = snapshot.get("schedule") if isinstance(snapshot.get("schedule"), dict) else {}
    if _fingerprint_key(previous_schedule) != _fingerprint_key(current_schedule):
        changed_files.append(str(current_schedule.get("path") or "schedule"))

    previous_files = previous.get("source_files") if isinstance(previous.get("source_files"), list) else []
    current_files = snapshot.get("source_files") if isinstance(snapshot.get("source_files"), list) else []

    previous_map = {str(item.get("path", "")): item for item in previous_files if isinstance(item, dict)}
    current_map = {str(item.get("path", "")): item for item in current_files if isinstance(item, dict)}

    for path_text, current_item in current_map.items():
        previous_item = previous_map.get(path_text)
        if previous_item is None or _fingerprint_key(previous_item) != _fingerprint_key(current_item):
            changed_files.append(path_text)

    for path_text in previous_map:
        if path_text not in current_map:
            changed_files.append(path_text)

    if changed_files:
        return {
            "up_to_date": False,
            "reason": "sources_changed",
            "message": f"{len(changed_files)} source file or schedule item changed.",
            "changed_files": sorted(dict.fromkeys(changed_files))[:25],
            "missing_outputs": [],
        }

    return {
        "up_to_date": True,
        "reason": "current",
        "message": "Processed Parquet store is current.",
        "changed_files": [],
        "missing_outputs": [],
    }


def _stringify_list(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null", "<na>"}:
        return ""
    return text


def _clean_text_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_clean_text_value(item) for item in value if _clean_text_value(item))
    if isinstance(value, dict):
        return str(value)

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null", "<na>"}:
        return ""
    return text


def _clean_text_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index, dtype="object")
    return df[col].apply(_clean_text_value).astype(str)


def _compact_text(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().str.replace(r"[^a-z0-9]+", "", regex=True)


def _event_type_text(df: pd.DataFrame) -> pd.Series:
    if "type_l" in df.columns and _clean_text_series(df, "type_l").str.strip().ne("").any():
        return _clean_text_series(df, "type_l").str.lower()
    if "type" in df.columns:
        return _clean_text_series(df, "type").str.lower()
    return pd.Series([""] * len(df), index=df.index)


def _outcome_text(df: pd.DataFrame) -> pd.Series:
    if "outcome_l" in df.columns and _clean_text_series(df, "outcome_l").str.strip().ne("").any():
        return _clean_text_series(df, "outcome_l").str.lower()
    if "outcome_type" in df.columns:
        return _clean_text_series(df, "outcome_type").str.lower()
    return pd.Series([""] * len(df), index=df.index)


def _bool_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    raw = df[col]
    if pd.api.types.is_bool_dtype(raw):
        return raw.fillna(False).astype(bool)
    return raw.astype(str).str.lower().isin(["true", "1", "yes", "y"])


def _add_clean_flags(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    type_l = _event_type_text(out)
    outcome_l = _outcome_text(out)

    qual_raw = out.get("qual_tags", pd.Series([""] * len(out), index=out.index)).apply(_stringify_list)
    if qual_raw.astype(str).str.strip().eq("").all() and "qualifier_tags" in out.columns:
        qual_raw = _clean_text_series(out, "qualifier_tags")
    qual_text = qual_raw.apply(_clean_text_value).str.lower()

    type_compact = _compact_text(type_l)
    qual_compact = _compact_text(qual_text)
    card_text = _clean_text_series(out, "card_type").apply(
        lambda value: "" if str(value).strip().lower() in {"", "nan", "none", "null", "<na>", "false", "0"} else str(value).strip()
    )
    card_compact = _compact_text(card_text)

    out["event_type"] = _clean_text_series(out, "type")
    out["event_type_l"] = type_l
    out["outcome_type_l"] = outcome_l
    out["qualifier_tags"] = qual_raw.apply(_clean_text_value)

    strict_shot_types = {"goal", "missedshots", "savedshot", "shotonpost", "blockedshot", "attemptsaved"}

    out["is_goal"] = _bool_series(out, "is_goal") | type_compact.eq("goal")
    out["is_shot"] = (
        out["is_goal"]
        | type_compact.isin(strict_shot_types)
        | (_bool_series(out, "is_shot") & type_compact.isin(strict_shot_types))
    )
    out["is_touch"] = _bool_series(out, "is_touch") | type_compact.str.contains("touch", na=False)

    out["is_pass"] = type_compact.eq("pass")
    out["is_cross"] = type_compact.str.contains("cross", na=False) | qual_compact.str.contains("cross", na=False)
    out["is_carry"] = type_compact.str.contains("carry|dribble|takeon|run", regex=True, na=False)
    out["is_defensive_action"] = type_compact.str.contains(
        "tackle|interception|clearance|block|recovery|challenge|aerial|duel",
        regex=True,
        na=False,
    )

    void_card = qual_compact.str.contains("voidyellowcard|voidredcard|voidsecondyellow", regex=True, na=False)
    card_type_present = card_compact.isin({"yellow", "secondyellow", "red"})
    card_event = type_compact.eq("card") | card_type_present | qual_compact.str.contains("(?:^|[^a-z])yellow|secondyellow|(?:^|[^a-z])red", regex=True, na=False)
    out["is_card"] = card_event & ~void_card
    out["is_yellow_card"] = (card_compact.isin({"yellow", "secondyellow"}) | qual_compact.str.contains("yellow", na=False)) & ~void_card
    out["is_second_yellow"] = (card_compact.eq("secondyellow") | qual_compact.str.contains("secondyellow", na=False)) & ~void_card
    out["is_red_card"] = (card_compact.isin({"red", "secondyellow"}) | qual_compact.str.contains("(?:^|[^a-z])red|secondyellow", regex=True, na=False)) & ~void_card

    out["is_corner_awarded"] = type_compact.eq("cornerawarded")
    out["is_corner_taken"] = type_compact.isin({"cornertaken", "corner"}) | qual_compact.str.contains("cornertaken|cornerkick", regex=True, na=False)
    out["is_from_corner"] = qual_compact.str.contains("fromcorner", na=False)
    out["is_free_kick_taken"] = (
        type_compact.isin({"freekicktaken", "directfreekick", "indirectfreekicktaken", "freekick"})
        | qual_compact.str.contains("freekicktaken|indirectfreekicktaken|directfreekick", regex=True, na=False)
    )
    out["is_from_free_kick"] = qual_compact.str.contains("fromfreekick", na=False)
    out["is_throw_in"] = type_compact.isin({"throwin", "throwintaken", "throwinsetpiece"}) | qual_compact.str.contains("throwintaken|throwinsetpiece", regex=True, na=False)
    out["is_from_throw_in"] = qual_compact.str.contains("fromthrowin", na=False)
    out["is_penalty"] = type_compact.str.contains("penalty", na=False) | qual_compact.str.contains("penaltytaken|penaltyawarded|penaltyscored|penaltymissed|penaltysaved", regex=True, na=False)

    out["is_corner"] = out["is_corner_awarded"] | out["is_corner_taken"] | out["is_from_corner"]
    out["is_free_kick"] = out["is_free_kick_taken"] | out["is_from_free_kick"]
    out["is_set_piece_restart"] = out["is_corner_awarded"] | out["is_corner_taken"] | out["is_free_kick_taken"] | out["is_throw_in"] | out["is_penalty"]
    out["is_set_piece"] = out["is_corner"] | out["is_free_kick"] | out["is_penalty"] | out["is_throw_in"] | out["is_from_throw_in"]

    for col in ["x", "y", "end_x", "end_y", "goal_mouth_x", "goal_mouth_y", "goal_mouth_z", "blocked_x", "blocked_y", "minute", "second", "expanded_minute"]:
        if col not in out.columns:
            out[col] = pd.NA
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out["final_third_entry"] = (
        (out["is_pass"] | out["is_cross"] | out["is_carry"])
        & out["x"].lt(66.67)
        & out["end_x"].ge(66.67)
    )
    out["box_entry"] = (
        (out["is_pass"] | out["is_cross"] | out["is_carry"])
        & ~(out["x"].ge(83.0) & out["y"].between(21.1, 78.9, inclusive="both"))
        & (out["end_x"].ge(83.0) & out["end_y"].between(21.1, 78.9, inclusive="both"))
    )
    out["attacking_third_touch"] = out["is_touch"] & out["x"].ge(66.67)
    out["high_regain"] = out["is_defensive_action"] & out["x"].ge(60.0)
    out["is_success"] = outcome_l.isin({"successful", "success", "won", "complete", "completed", "accurate"}) | out["is_goal"]

    return out

def _attach_match_context(events: pd.DataFrame, match_index: pd.DataFrame, team_folder: str) -> pd.DataFrame:
    out = events.copy()
    out["team_folder"] = team_folder
    out["team_norm"] = out.get("team", pd.Series([""] * len(out), index=out.index)).map(_normalise_team)
    out = out.merge(
        match_index[
            [
                "match_id",
                "home_team",
                "away_team",
                "home_team_norm",
                "away_team_norm",
                "kickoff",
                "match_date",
                "status",
                "home_score",
                "away_score",
            ]
        ],
        on="match_id",
        how="left",
    )
    out["opponent"] = out.apply(
        lambda row: row["away_team"] if row.get("team_norm") == row.get("home_team_norm") else row.get("home_team", ""),
        axis=1,
    )
    out["team_side"] = out.apply(
        lambda row: "home" if row.get("team_norm") == row.get("home_team_norm") else ("away" if row.get("team_norm") == row.get("away_team_norm") else ""),
        axis=1,
    )
    return out


def _normalised_column_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _is_numeric_column_name(col: object) -> bool:
    key = _normalised_column_key(col)
    numeric_keys = {
        "matchid",
        "gameid",
        "id",
        "teamid",
        "playerid",
        "x",
        "y",
        "endx",
        "endy",
        "x120",
        "y80",
        "endx120",
        "endy80",
        "minute",
        "second",
        "expandedminute",
        "period",
        "goalmouthx",
        "goalmouthy",
        "goalmouthz",
        "blockedx",
        "blockedy",
        "homescore",
        "awayscore",
        "eventindex",
    }
    return key in numeric_keys


def _is_bool_column_name(col: object) -> bool:
    text = str(col or "").strip().lower()
    key = _normalised_column_key(col)

    if text.startswith(("is_", "has_")):
        return True

    explicit = {
        "istouch",
        "isshot",
        "isgoal",
        "ispass",
        "iscross",
        "iscarry",
        "istackle",
        "isinterception",
        "isclearance",
        "isfoul",
        "iscorner",
        "isfreekick",
        "issetpiece",
        "hasassist",
    }
    return key in explicit


def _clean_bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).astype(bool)

    text = series.astype(str).str.strip().str.lower()
    return text.isin(["true", "1", "yes", "y"]).fillna(False).astype(bool)


def _serialisable_events(df: pd.DataFrame) -> pd.DataFrame:
    keep = df.copy()

    if keep.empty:
        return keep

    keep.columns = [str(col).strip() for col in keep.columns]

    if keep.columns.duplicated().any():
        keep = keep.loc[:, ~keep.columns.duplicated()].copy()

    for col in ["qual_map", "qual_tags"]:
        if col in keep.columns:
            keep = keep.drop(columns=[col])

    for col in keep.columns:
        if keep[col].map(lambda value: isinstance(value, (dict, list, set, tuple))).any():
            keep[col] = keep[col].apply(_stringify_list)

    for col in list(keep.columns):
        if _is_numeric_column_name(col):
            keep[col] = pd.to_numeric(keep[col], errors="coerce")
            continue

        if _is_bool_column_name(col):
            keep[col] = _clean_bool_series(keep[col])
            continue

        if pd.api.types.is_datetime64_any_dtype(keep[col]):
            keep[col] = pd.to_datetime(keep[col], errors="coerce", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ").fillna("")
            continue

        if pd.api.types.is_object_dtype(keep[col]) or pd.api.types.is_string_dtype(keep[col]):
            keep[col] = keep[col].apply(_clean_text_value).astype(str)

    if "match_date" in keep.columns:
        keep["match_date"] = pd.to_datetime(keep["match_date"], errors="coerce", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ").fillna("")

    for col in keep.columns:
        if pd.api.types.is_object_dtype(keep[col]) or pd.api.types.is_string_dtype(keep[col]):
            keep[col] = keep[col].apply(_clean_text_value).astype(str)

    return keep


def _write_parquet_frame(df: pd.DataFrame, path: Path) -> None:
    safe = _serialisable_events(df)
    safe.to_parquet(path, index=False)


def _metric_num(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col not in df.columns:
        return pd.Series(default, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce").fillna(default)


def _metric_bool(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    return _clean_bool_series(df[col])


def _metric_text(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index, dtype="object")
    return df[col].astype(str).replace({"nan": "", "None": "", "<NA>": "", "NaT": ""}).fillna("").str.strip()


def _safe_pct(numerator: float, denominator: float) -> float:
    try:
        den = float(denominator)
        if den <= 0:
            return 0.0
        return round((float(numerator) / den) * 100.0, 3)
    except Exception:
        return 0.0


def _build_team_match_summary_cache(events: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "match_id",
        "team",
        "team_norm",
        "team_side",
        "opponent",
        "home_team",
        "away_team",
        "match_date",
        "passes",
        "successful_passes",
        "pass_completion_pct",
        "shots",
        "goals",
        "box_entries",
        "final_third_entries",
        "attacking_third_touches",
        "defensive_actions",
        "high_regains",
        "average_defensive_action_height",
        "direct_attack_events",
        "transition_attack_events",
        "long_ball_events",
        "total_events",
    ]
    if events.empty or "match_id" not in events.columns or "team" not in events.columns:
        return pd.DataFrame(columns=columns)

    work = events.copy()
    work["team"] = _metric_text(work, "team")
    work["team_norm"] = _metric_text(work, "team_norm")
    if work["team_norm"].str.strip().eq("").all():
        work["team_norm"] = work["team"].map(_normalise_team)
    work["team_side"] = _metric_text(work, "team_side")
    work["opponent"] = _metric_text(work, "opponent")
    work["home_team"] = _metric_text(work, "home_team")
    work["away_team"] = _metric_text(work, "away_team")
    work["match_date"] = _metric_text(work, "match_date")
    work["match_id"] = pd.to_numeric(work["match_id"], errors="coerce")
    work = work.loc[work["match_id"].notna() & work["team"].str.strip().ne("")].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)

    pass_mask = _metric_bool(work, "is_pass") | _metric_bool(work, "is_cross")
    success_mask = _metric_bool(work, "is_success")
    defensive_mask = _metric_bool(work, "is_defensive_action")
    long_ball_context = (_metric_text(work, "qualifier_tags") + " " + _metric_text(work, "qual_tags") + " " + _metric_text(work, "type")).str.lower()
    x = _metric_num(work, "x", default=float("nan"))
    end_x = _metric_num(work, "end_x", default=float("nan"))

    work["_passes"] = pass_mask.astype(int)
    work["_successful_passes"] = (pass_mask & success_mask).astype(int)
    work["_shots"] = _metric_bool(work, "is_shot").astype(int)
    work["_goals"] = _metric_bool(work, "is_goal").astype(int)
    work["_box_entries"] = _metric_bool(work, "box_entry").astype(int)
    work["_final_third_entries"] = _metric_bool(work, "final_third_entry").astype(int)
    work["_attacking_third_touches"] = _metric_bool(work, "attacking_third_touch").astype(int)
    work["_defensive_actions"] = defensive_mask.astype(int)
    work["_high_regains"] = _metric_bool(work, "high_regain").astype(int)
    work["_defensive_x_total"] = x.where(defensive_mask, 0.0).fillna(0.0)
    work["_direct_attack_events"] = (pass_mask & success_mask & x.notna() & end_x.notna() & (end_x - x).ge(25.0)).astype(int)
    work["_transition_attack_events"] = (_metric_bool(work, "high_regain") | (_metric_bool(work, "box_entry") & x.lt(60.0))).astype(int)
    work["_long_ball_events"] = (pass_mask & long_ball_context.str.contains("longball|long ball", regex=True, na=False)).astype(int)
    work["_total_events"] = 1

    grouped = (
        work.groupby(["match_id", "team"], dropna=False)
        .agg(
            team_norm=("team_norm", "first"),
            team_side=("team_side", "first"),
            opponent=("opponent", "first"),
            home_team=("home_team", "first"),
            away_team=("away_team", "first"),
            match_date=("match_date", "first"),
            passes=("_passes", "sum"),
            successful_passes=("_successful_passes", "sum"),
            shots=("_shots", "sum"),
            goals=("_goals", "sum"),
            box_entries=("_box_entries", "sum"),
            final_third_entries=("_final_third_entries", "sum"),
            attacking_third_touches=("_attacking_third_touches", "sum"),
            defensive_actions=("_defensive_actions", "sum"),
            high_regains=("_high_regains", "sum"),
            defensive_x_total=("_defensive_x_total", "sum"),
            direct_attack_events=("_direct_attack_events", "sum"),
            transition_attack_events=("_transition_attack_events", "sum"),
            long_ball_events=("_long_ball_events", "sum"),
            total_events=("_total_events", "sum"),
        )
        .reset_index()
    )

    grouped["match_id"] = pd.to_numeric(grouped["match_id"], errors="coerce").fillna(0).astype(int)
    grouped["pass_completion_pct"] = [
        _safe_pct(successful, attempted)
        for successful, attempted in zip(grouped["successful_passes"], grouped["passes"])
    ]
    grouped["average_defensive_action_height"] = [
        round(float(total) / max(float(count), 1.0), 3)
        for total, count in zip(grouped["defensive_x_total"], grouped["defensive_actions"])
    ]
    grouped = grouped.drop(columns=["defensive_x_total"])

    for col in columns:
        if col not in grouped.columns:
            grouped[col] = "" if col in {"team", "team_norm", "team_side", "opponent", "home_team", "away_team", "match_date"} else 0
    return grouped[columns].sort_values(["match_id", "team"]).reset_index(drop=True)


def _build_team_style_profile_cache(team_match_summary: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "team",
        "team_norm",
        "match_count",
        "in_possession_tag",
        "out_of_possession_tag",
        "direct_score",
        "possession_score",
        "transition_score",
        "press_score",
        "low_block_score",
        "confidence",
    ]
    if team_match_summary.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, Any]] = []
    for team, group in team_match_summary.groupby("team", dropna=False):
        if not str(team).strip():
            continue

        match_count = int(group["match_id"].nunique()) if "match_id" in group.columns else int(len(group))
        passes = float(pd.to_numeric(group.get("passes"), errors="coerce").fillna(0.0).mean())
        direct = float(pd.to_numeric(group.get("direct_attack_events"), errors="coerce").fillna(0.0).mean())
        transition = float(pd.to_numeric(group.get("transition_attack_events"), errors="coerce").fillna(0.0).mean())
        long_balls = float(pd.to_numeric(group.get("long_ball_events"), errors="coerce").fillna(0.0).mean())
        defensive_height = float(pd.to_numeric(group.get("average_defensive_action_height"), errors="coerce").fillna(0.0).mean())
        high_regains = float(pd.to_numeric(group.get("high_regains"), errors="coerce").fillna(0.0).mean())
        defensive_actions = float(pd.to_numeric(group.get("defensive_actions"), errors="coerce").fillna(0.0).mean())

        direct_score = round(direct + long_balls, 3)
        possession_score = round(passes, 3)
        transition_score = round(transition, 3)
        press_score = round(defensive_height + high_regains, 3)
        low_block_score = round(max(0.0, 50.0 - defensive_height) + max(0.0, defensive_actions - high_regains), 3)

        if match_count < 2:
            ip_tag = "Low sample"
            oop_tag = "Low sample"
            confidence = "low"
        else:
            confidence = "medium" if match_count < 6 else "high"
            if transition_score >= max(possession_score * 0.12, direct_score):
                ip_tag = "Transition side"
            elif possession_score >= 220 and possession_score >= direct_score * 8:
                ip_tag = "Possession side"
            elif direct_score >= 18:
                ip_tag = "Direct side"
            else:
                ip_tag = "Mixed possession/direct"

            if press_score >= 65:
                oop_tag = "High press side"
            elif low_block_score >= 55:
                oop_tag = "Low block side"
            else:
                oop_tag = "Mid block side"

        rows.append(
            {
                "team": str(team),
                "team_norm": str(group.get("team_norm", pd.Series([""])).iloc[0] or ""),
                "match_count": match_count,
                "in_possession_tag": ip_tag,
                "out_of_possession_tag": oop_tag,
                "direct_score": direct_score,
                "possession_score": possession_score,
                "transition_score": transition_score,
                "press_score": press_score,
                "low_block_score": low_block_score,
                "confidence": confidence,
            }
        )

    return pd.DataFrame(rows, columns=columns).sort_values("team").reset_index(drop=True)


def _build_momentum_profile_cache(events: pd.DataFrame, window_minutes: int = 10) -> pd.DataFrame:
    columns = ["match_id", "team", "window_start", "window_end", "event_count", "shots", "box_entries", "final_third_entries", "danger_events"]
    if events.empty or "match_id" not in events.columns or "team" not in events.columns:
        return pd.DataFrame(columns=columns)

    work = events.copy()
    work["match_id"] = pd.to_numeric(work["match_id"], errors="coerce")
    minute = pd.to_numeric(work.get("expanded_minute", work.get("minute")), errors="coerce").fillna(0.0)
    work["window_start"] = (minute // float(window_minutes)).astype(int) * int(window_minutes)
    work["window_end"] = work["window_start"] + int(window_minutes)
    work["team"] = _metric_text(work, "team")
    work = work.loc[work["match_id"].notna() & work["team"].str.strip().ne("")].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)

    work["_event_count"] = 1
    work["_shots"] = _metric_bool(work, "is_shot").astype(int)
    work["_box_entries"] = _metric_bool(work, "box_entry").astype(int)
    work["_final_third_entries"] = _metric_bool(work, "final_third_entry").astype(int)
    work["_danger_events"] = (work["_shots"] + work["_box_entries"] + work["_final_third_entries"]).astype(int)

    grouped = (
        work.groupby(["match_id", "team", "window_start", "window_end"], dropna=False)
        .agg(
            event_count=("_event_count", "sum"),
            shots=("_shots", "sum"),
            box_entries=("_box_entries", "sum"),
            final_third_entries=("_final_third_entries", "sum"),
            danger_events=("_danger_events", "sum"),
        )
        .reset_index()
    )
    grouped["match_id"] = pd.to_numeric(grouped["match_id"], errors="coerce").fillna(0).astype(int)
    return grouped[columns].sort_values(["match_id", "window_start", "team"]).reset_index(drop=True)


def _format_eta(seconds: float | None) -> str:
    if seconds is None or not isinstance(seconds, (int, float)) or seconds < 0 or pd.isna(seconds):
        return "Calculating"
    seconds_int = int(round(float(seconds)))
    if seconds_int < 60:
        return f"{seconds_int}s"
    minutes, secs = divmod(seconds_int, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"



def datetime_utc_string() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except Exception:
        return 0


def _progress_payload(
    *,
    kind: str,
    stage: str,
    message: str,
    started_at: float,
    completed: int = 0,
    total: int = 0,
    completed_files: int | None = None,
    total_files: int | None = None,
    completed_bytes: int | None = None,
    total_bytes: int | None = None,
    rows: int | None = None,
    current_file: str = "",
    **extra: Any,
) -> dict[str, Any]:
    elapsed = max(0.0, time.monotonic() - started_at)
    completed_file_count = int(completed if completed_files is None or (completed_files <= 0 and completed > 0) else completed_files)
    total_file_count = int(total if total_files is None or (total_files <= 0 and total > 0) else total_files)
    completed_byte_count = int(max(0, completed_bytes or 0))
    total_byte_count = int(max(0, total_bytes or 0))

    progress_done = float(completed_file_count)
    progress_total = float(total_file_count)
    if total_byte_count > 0:
        progress_done = float(min(completed_byte_count, total_byte_count))
        progress_total = float(total_byte_count)

    percent = round((progress_done / progress_total) * 100.0, 1) if progress_total > 0 else 0.0

    eta_seconds: float | None = None
    if progress_done > 0 and progress_total > 0 and progress_done < progress_total:
        rate = elapsed / max(progress_done, 1.0)
        eta_seconds = max(0.0, rate * float(progress_total - progress_done))
    elif progress_done >= progress_total and progress_total > 0:
        eta_seconds = 0.0

    payload: dict[str, Any] = {
        "kind": kind,
        "stage": stage,
        "message": message,
        "completed": completed_file_count,
        "total": total_file_count,
        "completed_files": completed_file_count,
        "total_files": total_file_count,
        "completed_bytes": completed_byte_count,
        "total_bytes": total_byte_count,
        "rows": int(rows or 0),
        "current_file": current_file,
        "percent": percent,
        "elapsed_seconds": round(elapsed, 1),
        "eta_seconds": round(float(eta_seconds), 1) if eta_seconds is not None else None,
        "eta_label": _format_eta(eta_seconds),
    }
    payload.update(extra)
    return payload

def build_processed_event_store(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    force: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] | None = None
    for event in stream_build_processed_event_store(basedir, nation=nation, tier=tier, season=season, force=force):
        if event.get("kind") == "complete":
            result = event
    if result is None:
        raise RuntimeError("Processed event store rebuild did not return a completion payload.")
    return result


def stream_build_processed_event_store(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    force: bool = False,
) -> Iterator[dict[str, Any]]:
    started_at = time.monotonic()
    paths = processed_paths(basedir, nation, tier, season)

    yield _progress_payload(
        kind="start",
        stage="scan",
        message="Checking whether the processed Parquet store is current.",
        started_at=started_at,
        completed=0,
        total=1,
        nation=nation,
        tier=tier,
        season=season,
        force=bool(force),
    )

    schedule_df = _read_schedule(basedir, nation, tier, season)
    match_index = _schedule_index(schedule_df)

    events_root, csv_jobs = _collect_event_csv_jobs(basedir, nation, tier, season)
    if not events_root.exists():
        raise FileNotFoundError(f"Event data folder not found: {events_root}")

    source_file_sizes = {csv_path: _safe_file_size(csv_path) for csv_path, _team_folder in csv_jobs}
    total_source_bytes = int(sum(source_file_sizes.values()))

    source_snapshot = _source_snapshot(basedir, nation, tier, season, csv_jobs)
    manifest = _read_manifest(paths)
    freshness = _source_change_summary(manifest, source_snapshot, paths)

    if freshness["up_to_date"] and not force:
        yield _progress_payload(
            kind="complete",
            stage="ready",
            message="Processed Parquet store is already current. No rebuild needed.",
            started_at=started_at,
            completed=1,
            total=1,
            rebuilt=False,
            smart_rebuild=True,
            freshness=freshness,
            source_files=len(csv_jobs),
            completed_files=len(csv_jobs),
            total_files=len(csv_jobs),
            completed_bytes=total_source_bytes,
            total_bytes=total_source_bytes,
            matches=int(match_index["match_id"].nunique()),
            paths={key: str(value) for key, value in paths.items()},
        )
        return

    yield _progress_payload(
        kind="status",
        stage="rebuild_required",
        message=(
            "Force rebuild requested."
            if force
            else str(freshness.get("message") or "Processed source files changed.")
        ),
        started_at=started_at,
        completed=0,
        total=max(len(csv_jobs), 1),
        completed_files=0,
        total_files=len(csv_jobs),
        completed_bytes=0,
        total_bytes=total_source_bytes,
        smart_rebuild=True,
        freshness=freshness,
        changed_files=freshness.get("changed_files", []),
        source_files=len(csv_jobs),
    )

    yield _progress_payload(
        kind="summary",
        stage="scan",
        message=f"Found {len(csv_jobs)} event source files to process.",
        started_at=started_at,
        completed=0,
        total=max(len(csv_jobs), 1),
        completed_files=0,
        total_files=len(csv_jobs),
        completed_bytes=0,
        total_bytes=total_source_bytes,
        candidate_files=len(csv_jobs),
        matches=int(match_index["match_id"].nunique()),
        events_root=str(events_root),
    )

    frames: list[pd.DataFrame] = []
    source_files = 0
    skipped_files = 0
    total_rows_read = 0
    completed_source_bytes = 0

    for index, (csv_path, team_folder) in enumerate(csv_jobs, start=1):
        file_size = int(source_file_sizes.get(csv_path, _safe_file_size(csv_path)))
        if csv_path.stat().st_size == 0:
            skipped_files += 1
            completed_source_bytes += file_size
            yield _progress_payload(
                kind="progress",
                stage="read",
                message=f"Skipped empty file for {team_folder}.",
                started_at=started_at,
                completed=index,
                total=len(csv_jobs),
                current_file=str(csv_path),
                team=team_folder,
                rows=total_rows_read,
                completed_files=index,
                total_files=len(csv_jobs),
                completed_bytes=completed_source_bytes,
                total_bytes=total_source_bytes,
                source_files=source_files,
                skipped_files=skipped_files,
            )
            continue

        raw, read_mode = _read_csv_resilient(csv_path)
        if read_mode == "empty":
            skipped_files += 1
            completed_source_bytes += file_size
            yield _progress_payload(
                kind="progress",
                stage="read",
                message=f"Skipped file with no readable columns for {team_folder}.",
                started_at=started_at,
                completed=index,
                total=len(csv_jobs),
                current_file=str(csv_path),
                team=team_folder,
                rows=total_rows_read,
                completed_files=index,
                total_files=len(csv_jobs),
                completed_bytes=completed_source_bytes,
                total_bytes=total_source_bytes,
                source_files=source_files,
                skipped_files=skipped_files,
            )
            continue
        if read_mode in {"python_skip_bad_lines", "repaired_field_count"}:
            recovery_message = (
                f"CSV parser repaired {team_folder} by aligning inconsistent field counts."
                if read_mode == "repaired_field_count"
                else f"CSV parser recovered {team_folder} by skipping malformed rows."
            )
            yield _progress_payload(
                kind="warning",
                stage="read",
                message=recovery_message,
                started_at=started_at,
                completed=index,
                total=len(csv_jobs),
                current_file=str(csv_path),
                team=team_folder,
                rows=total_rows_read,
                completed_files=index,
                total_files=len(csv_jobs),
                completed_bytes=completed_source_bytes,
                total_bytes=total_source_bytes,
                source_files=source_files,
                skipped_files=skipped_files,
            )

        if raw.empty:
            skipped_files += 1
            completed_source_bytes += file_size
            yield _progress_payload(
                kind="progress",
                stage="read",
                message=f"Skipped empty event table for {team_folder}.",
                started_at=started_at,
                completed=index,
                total=len(csv_jobs),
                current_file=str(csv_path),
                team=team_folder,
                rows=total_rows_read,
                completed_files=index,
                total_files=len(csv_jobs),
                completed_bytes=completed_source_bytes,
                total_bytes=total_source_bytes,
                source_files=source_files,
                skipped_files=skipped_files,
            )
            continue

        match_col = _match_id_col(raw)
        if not match_col:
            skipped_files += 1
            completed_source_bytes += file_size
            yield _progress_payload(
                kind="progress",
                stage="read",
                message=f"Skipped {team_folder}. No match id column found.",
                started_at=started_at,
                completed=index,
                total=len(csv_jobs),
                current_file=str(csv_path),
                team=team_folder,
                rows=total_rows_read,
                completed_files=index,
                total_files=len(csv_jobs),
                completed_bytes=completed_source_bytes,
                total_bytes=total_source_bytes,
                source_files=source_files,
                skipped_files=skipped_files,
            )
            continue

        raw = raw.copy()
        if match_col != "match_id":
            raw["match_id"] = pd.to_numeric(raw[match_col], errors="coerce")
        else:
            raw["match_id"] = pd.to_numeric(raw["match_id"], errors="coerce")

        raw = raw.dropna(subset=["match_id"]).copy()
        if raw.empty:
            skipped_files += 1
            completed_source_bytes += file_size
            yield _progress_payload(
                kind="progress",
                stage="read",
                message=f"Skipped {team_folder}. No valid match ids found.",
                started_at=started_at,
                completed=index,
                total=len(csv_jobs),
                current_file=str(csv_path),
                team=team_folder,
                rows=total_rows_read,
                completed_files=index,
                total_files=len(csv_jobs),
                completed_bytes=completed_source_bytes,
                total_bytes=total_source_bytes,
                source_files=source_files,
                skipped_files=skipped_files,
            )
            continue

        raw["match_id"] = raw["match_id"].astype(int)

        normalised = normalise_event_frame(raw)
        normalised = _ensure_period_column(normalised)
        normalised = _add_clean_flags(normalised)
        normalised = _attach_match_context(normalised, match_index, team_folder)
        normalised["nation"] = nation
        normalised["tier"] = tier
        normalised["season"] = season
        normalised["source_file"] = str(csv_path)
        frames.append(normalised)
        source_files += 1
        total_rows_read += int(len(normalised))
        completed_source_bytes += file_size

        yield _progress_payload(
            kind="progress",
            stage="normalise",
            message=f"Processed {team_folder}: {len(normalised):,} rows.",
            started_at=started_at,
            completed=index,
            total=len(csv_jobs),
            current_file=str(csv_path),
            team=team_folder,
            rows=total_rows_read,
            completed_files=index,
            total_files=len(csv_jobs),
            completed_bytes=completed_source_bytes,
            total_bytes=total_source_bytes,
            source_files=source_files,
            skipped_files=skipped_files,
        )

    yield _progress_payload(
        kind="progress",
        stage="combine",
        message="Combining cleaned event frames.",
        started_at=started_at,
        completed=len(csv_jobs),
        total=max(len(csv_jobs), 1),
        completed_files=len(csv_jobs),
        total_files=len(csv_jobs),
        completed_bytes=completed_source_bytes,
        total_bytes=total_source_bytes,
        rows=total_rows_read,
        source_files=source_files,
        skipped_files=skipped_files,
    )

    if frames:
        events = pd.concat(frames, ignore_index=True)
        events = _ensure_period_column(events)
        events = events.sort_values(["match_id", "period", "expanded_minute", "minute", "second"], na_position="last").reset_index(drop=True)
        events["event_index"] = events.groupby("match_id").cumcount()
        events["event_uid"] = (
            events["match_id"].astype(str)
            + "::"
            + events["event_index"].astype(str)
            + "::"
            + events.get("team", pd.Series([""] * len(events), index=events.index)).astype(str)
        )
        events = _serialisable_events(events)
    else:
        events = pd.DataFrame()

    paths["root"].mkdir(parents=True, exist_ok=True)

    yield _progress_payload(
        kind="progress",
        stage="write_season",
        message="Writing season level events_clean.parquet.",
        started_at=started_at,
        completed=0,
        total=1,
        completed_files=len(csv_jobs),
        total_files=len(csv_jobs),
        completed_bytes=completed_source_bytes,
        total_bytes=total_source_bytes,
        rows=int(len(events)),
        source_files=source_files,
    )
    _write_parquet_frame(events, paths["events"])

    match_index_out = _serialisable_events(match_index.copy())
    _write_parquet_frame(match_index_out, paths["match_index"])

    for derived_dir_key in ["events_by_match", "prepared_by_match"]:
        if paths[derived_dir_key].exists():
            shutil.rmtree(paths[derived_dir_key])
        paths[derived_dir_key].mkdir(parents=True, exist_ok=True)

    for derived_file_key in ["prepared_season_events", "team_match_summary", "team_style_profiles", "momentum_profiles"]:
        if paths[derived_file_key].exists():
            paths[derived_file_key].unlink()

    match_count = int(events["match_id"].nunique()) if not events.empty and "match_id" in events.columns else 0
    if match_count > 0:
        for match_index_number, (match_id_value, match_events) in enumerate(events.groupby("match_id", sort=True), start=1):
            match_dir = paths["events_by_match"] / f"match_id={int(match_id_value)}"
            match_dir.mkdir(parents=True, exist_ok=True)
            _write_parquet_frame(match_events, match_dir / "events.parquet")
            yield _progress_payload(
                kind="progress",
                stage="partition",
                message=f"Stored match {int(match_id_value)} as a separate Parquet file.",
                started_at=started_at,
                completed=match_index_number,
                total=match_count,
                completed_files=len(csv_jobs),
                total_files=len(csv_jobs),
                completed_bytes=completed_source_bytes,
                total_bytes=total_source_bytes,
                current_match_id=int(match_id_value),
                rows=int(len(events)),
                source_files=source_files,
                skipped_files=skipped_files,
            )
    else:
        yield _progress_payload(
            kind="progress",
            stage="partition",
            message="No match rows were available for per match Parquet partitioning.",
            started_at=started_at,
            completed=1,
            total=1,
            completed_files=len(csv_jobs),
            total_files=len(csv_jobs),
            completed_bytes=completed_source_bytes,
            total_bytes=total_source_bytes,
            rows=0,
            source_files=source_files,
            skipped_files=skipped_files,
        )

    yield _progress_payload(
        kind="progress",
        stage="write_derived",
        message="Writing derived analysis Parquet caches.",
        started_at=started_at,
        completed=0,
        total=3,
        completed_files=len(csv_jobs),
        total_files=len(csv_jobs),
        completed_bytes=completed_source_bytes,
        total_bytes=total_source_bytes,
        rows=int(len(events)),
        source_files=source_files,
    )
    team_match_summary = _build_team_match_summary_cache(events)
    _write_parquet_frame(team_match_summary, paths["team_match_summary"])

    team_style_profiles = _build_team_style_profile_cache(team_match_summary)
    _write_parquet_frame(team_style_profiles, paths["team_style_profiles"])

    momentum_profiles = _build_momentum_profile_cache(events)
    _write_parquet_frame(momentum_profiles, paths["momentum_profiles"])

    manifest_payload = {
        "version": PROCESSED_STORE_MANIFEST_VERSION,
        "rebuilt_at": datetime_utc_string(),
        "nation": str(nation),
        "tier": str(tier),
        "season": str(season),
        "rows": int(len(events)),
        "matches": int(match_index["match_id"].nunique()),
        "source_files": int(source_files),
        "skipped_files": int(skipped_files),
        "partitioned_matches": int(match_count),
        "team_match_summary_rows": int(len(team_match_summary)),
        "team_style_profile_rows": int(len(team_style_profiles)),
        "momentum_profile_rows": int(len(momentum_profiles)),
        "source_snapshot": source_snapshot,
    }
    _write_manifest(paths, manifest_payload)

    yield _progress_payload(
        kind="complete",
        stage="complete",
        message="Processed event store rebuilt.",
        started_at=started_at,
        completed=max(match_count, 1),
        total=max(match_count, 1),
        completed_files=len(csv_jobs),
        total_files=len(csv_jobs),
        completed_bytes=completed_source_bytes,
        total_bytes=total_source_bytes,
        rebuilt=True,
        smart_rebuild=True,
        rows=int(len(events)),
        matches=int(match_index["match_id"].nunique()),
        source_files=source_files,
        skipped_files=skipped_files,
        partitioned_matches=match_count,
        team_match_summary_rows=int(len(team_match_summary)),
        team_style_profile_rows=int(len(team_style_profiles)),
        momentum_profile_rows=int(len(momentum_profiles)),
        freshness=freshness,
        paths={key: str(value) for key, value in paths.items()},
    )


def processed_store_status(basedir: Path, nation: str, tier: str, season: str) -> dict[str, Any]:
    paths = processed_paths(basedir, nation, tier, season)
    events_exists = paths["events"].exists()
    match_index_exists = paths["match_index"].exists()
    manifest = _read_manifest(paths)

    rows = 0
    if events_exists:
        try:
            from pyarrow import parquet as pq

            rows = int(pq.read_metadata(paths["events"]).num_rows)
        except Exception:
            try:
                rows = int(len(pd.read_parquet(paths["events"], columns=["match_id"])))
            except Exception:
                rows = 0

    source_files = 0
    freshness: dict[str, Any] = {
        "up_to_date": False,
        "reason": "status_unavailable",
        "message": "Source freshness could not be checked.",
        "changed_files": [],
        "missing_outputs": [],
    }

    try:
        _schedule_df = _read_schedule(basedir, nation, tier, season)
        _events_root_path, csv_jobs = _collect_event_csv_jobs(basedir, nation, tier, season)
        source_files = len(csv_jobs)
        snapshot = _source_snapshot(basedir, nation, tier, season, csv_jobs)
        freshness = _source_change_summary(manifest, snapshot, paths)
    except Exception as exc:
        freshness = {
            "up_to_date": False,
            "reason": "status_check_failed",
            "message": f"Source freshness check failed: {type(exc).__name__}: {exc}",
            "changed_files": [],
            "missing_outputs": [],
        }

    return {
        "exists": bool(events_exists and match_index_exists),
        "events_exists": events_exists,
        "match_index_exists": match_index_exists,
        "manifest_exists": paths["manifest"].exists(),
        "up_to_date": bool(freshness.get("up_to_date")),
        "stale": not bool(freshness.get("up_to_date")),
        "stale_reason": str(freshness.get("reason") or ""),
        "stale_message": str(freshness.get("message") or ""),
        "changed_files": freshness.get("changed_files", []),
        "missing_outputs": freshness.get("missing_outputs", []),
        "rows": rows,
        "source_files": source_files,
        "partitioned_matches": len(list(paths["events_by_match"].glob("match_id=*/events.parquet"))) if paths["events_by_match"].exists() else 0,
        "prepared_matches": len(list(paths["prepared_by_match"].glob("match_id=*/events.parquet"))) if paths["prepared_by_match"].exists() else 0,
        "team_match_summary_exists": paths["team_match_summary"].exists(),
        "team_style_profiles_exists": paths["team_style_profiles"].exists(),
        "momentum_profiles_exists": paths["momentum_profiles"].exists(),
        "paths": {key: str(value) for key, value in paths.items()},
    }




def load_processed_season_events(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
) -> pd.DataFrame | None:
    paths = processed_paths(basedir, nation, tier, season)
    if not paths["events"].exists():
        return None
    try:
        df = pd.read_parquet(paths["events"])
    except Exception:
        return None

    if df.empty or "match_id" not in df.columns:
        return None

    df = _ensure_period_column(df)
    return df.sort_values(["match_id", "period", "expanded_minute", "minute", "second"], na_position="last").reset_index(drop=True)


def load_processed_match_events(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    match_id: int,
) -> pd.DataFrame | None:
    paths = processed_paths(basedir, nation, tier, season)
    match_path = paths["events_by_match"] / f"match_id={int(match_id)}" / "events.parquet"
    if match_path.exists():
        try:
            out = pd.read_parquet(match_path)
        except Exception:
            out = pd.DataFrame()
        if not out.empty:
            out = _ensure_period_column(out)
            return out.sort_values(["period", "expanded_minute", "minute", "second"], na_position="last").reset_index(drop=True)

    if not paths["events"].exists():
        return None
    try:
        df = pd.read_parquet(paths["events"], filters=[("match_id", "=", int(match_id))])
    except Exception:
        try:
            df = pd.read_parquet(paths["events"])
        except Exception:
            return None

    if df.empty or "match_id" not in df.columns:
        return None

    df = _ensure_period_column(df)
    mask = pd.to_numeric(df["match_id"], errors="coerce").eq(int(match_id))
    out = df.loc[mask].copy()
    if out.empty:
        return None
    return out.sort_values(["period", "expanded_minute", "minute", "second"], na_position="last").reset_index(drop=True)


def load_processed_team_events(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    team: str,
) -> pd.DataFrame | None:
    paths = processed_paths(basedir, nation, tier, season)
    if not paths["events"].exists():
        return None
    try:
        df = pd.read_parquet(paths["events"])
    except Exception:
        return None

    if df.empty or "team_norm" not in df.columns:
        return None

    df = _ensure_period_column(df)
    team_norm = _normalise_team(team)
    out = df.loc[df["team_norm"].eq(team_norm)].copy()
    if out.empty:
        return None
    return out