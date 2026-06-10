from __future__ import annotations

import copy
import csv
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError, ParserError

from app.metrics.expected_threat import build_xt_model, value_actions
from app.services.event_data_service import load_match_events, load_schedule_frame, load_season_events
from app.services.match_stat_service import build_team_summary
from app.services.processed_event_store import load_processed_match_events, load_processed_season_events, processed_store_status


FINAL_THIRD_X = 66.67
BOX_X = 83.0
BOX_Y_MIN = 21.1
BOX_Y_MAX = 78.9
ATTACKING_THIRD_X = 66.67
GOAL_FRAME_CENTRE_Y = 50.0
GOAL_FRAME_HALF_WIDTH_Y = 5.382
GOAL_FRAME_LEFT_POST_Y = GOAL_FRAME_CENTRE_Y - GOAL_FRAME_HALF_WIDTH_Y
GOAL_FRAME_RIGHT_POST_Y = GOAL_FRAME_CENTRE_Y + GOAL_FRAME_HALF_WIDTH_Y


def _safe_slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return "".join(ch if ch.isalnum() or ch in {"_", "."} else "_" for ch in text).strip("_")


def _is_missing_scalar(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "nan", "none", "<na>", "nat", "null"}
    if isinstance(value, (dict, list, tuple, set)):
        return False
    try:
        result = pd.isna(value)
    except Exception:
        return False
    if isinstance(result, bool):
        return result
    try:
        return bool(result)
    except Exception:
        return False


def _normalise_missing_scalars(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df

    out = df.copy()
    for col in out.columns:
        try:
            missing_mask = out[col].isna()
        except Exception:
            try:
                missing_mask = out[col].map(_is_missing_scalar)
            except Exception:
                continue

        if pd.api.types.is_object_dtype(out[col]) or pd.api.types.is_string_dtype(out[col]):
            try:
                missing_mask = missing_mask | out[col].map(_is_missing_scalar)
            except Exception:
                pass

        try:
            has_missing = bool(missing_mask.any())
        except Exception:
            has_missing = False

        if has_missing:
            series = out[col].astype(object)
            series.loc[missing_mask] = math.nan
            out[col] = series

    return out


ANALYSIS_NUMERIC_COLUMNS = {
    "match_id",
    "event_index",
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
    "carry_distance",
    "carry_seconds",
    "take_on_distance",
    "take_on_seconds",
    "xg",
    "xa",
    "xt_added",
    "positive_xt",
    "xt_start",
    "xt_end",
}


def _coerce_analysis_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df

    out = _normalise_missing_scalars(df)
    for col in ANALYSIS_NUMERIC_COLUMNS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


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


def _period_sort_series(df: pd.DataFrame) -> pd.Series:
    if "period" not in df.columns:
        return pd.Series(1, index=df.index, dtype="int64")
    return pd.to_numeric(df["period"].apply(_period_to_int), errors="coerce").fillna(1).astype("int64")


def _sort_events_by_match_time(events: pd.DataFrame, extra_cols: list[str] | None = None) -> pd.DataFrame:
    if events.empty:
        return events.copy()

    out = events.copy()
    out["__period_sort"] = _period_sort_series(out)
    sort_cols = ["__period_sort"]
    for col in extra_cols or []:
        if col in out.columns:
            sort_cols.append(col)
    out = out.sort_values(sort_cols, na_position="last").drop(columns=["__period_sort"])
    return out


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


def _team_season_path(events_root: Path, nation: str, tier: str, team_name: str, season: str) -> Path:
    root = events_root / _safe_slug(nation)
    if str(tier or "").strip():
        root = root / _safe_slug(tier)
    return root / _safe_slug(team_name) / f"{_safe_slug(season)}.csv"


def _legacy_team_season_path(events_root: Path, nation: str, team_name: str, season: str) -> Path:
    return events_root / _safe_slug(nation) / _safe_slug(team_name) / f"{_safe_slug(season)}.csv"


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


def _iter_event_season_files(events_root: Path, nation: str, tier: str, season: str) -> list[tuple[Path, str]]:
    season_file = f"{_safe_slug(season)}.csv"
    out: list[tuple[Path, str]] = []
    seen: set[Path] = set()

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


def _event_team_col(df: pd.DataFrame) -> str | None:
    return _find_col(df, ["team", "team_name", "teamName", "club", "club_name"])


def _read_team_match_ids(path: Path) -> set[int]:
    if not path.exists():
        return set()
    try:
        df = _read_csv(path)
    except Exception:
        return set()
    col = _match_id_col(df)
    if not col:
        return set()
    ids = pd.to_numeric(df[col], errors="coerce").dropna().astype(int)
    return set(ids.unique().tolist())


def _score_value(row: pd.Series, side: str) -> int | None:
    candidates = {
        "home": ["home_score", "homescore", "homeGoals"],
        "away": ["away_score", "awayscore", "awayGoals"],
    }.get(side, [])
    for key in candidates:
        if key in row:
            value = pd.to_numeric(pd.Series([row.get(key)]), errors="coerce").iloc[0]
            if pd.notna(value):
                return int(value)
    return None


def _schedule_status_value(row: pd.Series) -> str:
    for key in ["status", "elapsed", "statusCode"]:
        if key in row and str(row.get(key, "")).strip() not in {"", "nan", "None"}:
            return str(row.get(key)).strip()
    return ""


def _schedule_time_value(row: pd.Series) -> str:
    for key in ["started_at_utc", "start_time", "date", "kickoff"]:
        if key in row and str(row.get(key, "")).strip() not in {"", "nan", "None"}:
            return str(row.get(key)).strip()
    return ""


def _build_team_match_index_uncached(events_root: Path, nation: str, tier: str, season: str, schedule_teams: list[str]) -> dict[str, set[int]]:
    team_key_to_schedule_name = {_norm_team_name(team): team for team in schedule_teams if str(team or "").strip()}
    index: dict[str, set[int]] = {_norm_team_name(team): set() for team in schedule_teams if str(team or "").strip()}

    for path, source_team in _iter_event_season_files(events_root, nation, tier, season):
        if not path.exists():
            continue
        try:
            df = _read_csv(path)
        except Exception:
            continue
        if df.empty:
            continue

        match_col = _match_id_col(df)
        if not match_col:
            continue

        ids = pd.to_numeric(df[match_col], errors="coerce")
        valid = ids.notna()
        if not valid.any():
            continue

        team_col = _event_team_col(df)
        if team_col:
            probe = df.loc[valid, [team_col]].copy()
            probe["__match_id"] = ids.loc[valid].astype(int).values
            for team_value, group in probe.groupby(team_col, dropna=True):
                raw_key = _norm_team_name(team_value)
                schedule_key = raw_key
                if raw_key not in index:
                    matches = [key for key in team_key_to_schedule_name if key and (raw_key == key or raw_key.startswith(key) or key.startswith(raw_key))]
                    if matches:
                        schedule_key = matches[0]
                index.setdefault(schedule_key, set()).update(group["__match_id"].astype(int).tolist())
        else:
            source_key = _norm_team_name(source_team)
            if source_key and not source_team.startswith("__"):
                index.setdefault(source_key, set()).update(ids.loc[valid].astype(int).tolist())

    return index


def _build_team_match_index(events_root: Path, nation: str, tier: str, season: str, schedule_teams: list[str]) -> dict[str, set[int]]:
    team_tuple = tuple(sorted(str(team) for team in schedule_teams if str(team or "").strip()))
    cache_token = _tree_stamp(events_root, (".csv",))
    try:
        cached = _build_team_match_index_cached(_normalised_path_key(events_root), nation, tier, season, team_tuple, cache_token)
        return {key: set(value) for key, value in cached.items()}
    except Exception:
        return _build_team_match_index_uncached(events_root, nation, tier, season, schedule_teams)


def _build_fixtures(schedule_df: pd.DataFrame, basedir: Path, nation: str, tier: str, season: str) -> list[dict[str, Any]]:
    match_id_col = _match_id_col(schedule_df)
    home_col, away_col = _home_away_cols(schedule_df)
    home_id_col = _find_col(schedule_df, ["home_team_id", "home_id", "homeTeamId"])
    away_id_col = _find_col(schedule_df, ["away_team_id", "away_id", "awayTeamId"])
    if not match_id_col or not home_col or not away_col:
        raise ValueError("Schedule CSV must contain match id, home team and away team columns.")

    events_root = _events_root(basedir)
    teams = sorted(set(schedule_df[home_col].dropna().astype(str)) | set(schedule_df[away_col].dropna().astype(str)))
    team_match_index = _build_team_match_index(events_root, nation, tier, season, teams)

    fixtures: list[dict[str, Any]] = []
    for _, row in schedule_df.iterrows():
        match_id_value = pd.to_numeric(pd.Series([row.get(match_id_col)]), errors="coerce").iloc[0]
        if pd.isna(match_id_value):
            continue

        home_team = str(row.get(home_col, "") or "").strip()
        away_team = str(row.get(away_col, "") or "").strip()
        home_key = _norm_team_name(home_team)
        away_key = _norm_team_name(away_team)
        match_id = int(match_id_value)
        has_home = match_id in team_match_index.get(home_key, set())
        has_away = match_id in team_match_index.get(away_key, set())

        fixtures.append(
            {
                "match_id": match_id,
                "home_team": home_team,
                "away_team": away_team,
                "home_score": _score_value(row, "home"),
                "away_score": _score_value(row, "away"),
                "home_team_id": _safe_int_or_none(row.get(home_id_col)) if home_id_col else None,
                "away_team_id": _safe_int_or_none(row.get(away_id_col)) if away_id_col else None,
                "status": _schedule_status_value(row),
                "kickoff": _schedule_time_value(row),
                "has_home_events": has_home,
                "has_away_events": has_away,
                "has_both_events": bool(has_home and has_away),
            }
        )

    fixtures.sort(key=lambda item: (str(item.get("kickoff") or ""), int(item["match_id"])))
    return fixtures

def _bool_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    raw = df[col]
    if pd.api.types.is_bool_dtype(raw):
        return raw.fillna(False).astype(bool)
    return raw.astype(str).str.lower().isin(["true", "1", "yes", "y"])


def _text_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index, dtype="object")
    return df[col].astype(str).fillna("")


def _num_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(math.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")


def _ensure_flags(events: pd.DataFrame) -> pd.DataFrame:
    out = events.copy()

    type_l = _text_series(out, "event_type_l")
    if type_l.str.strip().eq("").all():
        type_l = _text_series(out, "type_l")
    if type_l.str.strip().eq("").all():
        type_l = _text_series(out, "type").str.lower()

    outcome_l = _text_series(out, "outcome_type_l")
    if outcome_l.str.strip().eq("").all():
        outcome_l = _text_series(out, "outcome_l")
    if outcome_l.str.strip().eq("").all():
        outcome_l = _text_series(out, "outcome_type").str.lower()

    qual_text = _text_series(out, "qualifier_tags")
    if qual_text.str.strip().eq("").all():
        qual_text = _text_series(out, "qual_tags")
    qual_text = qual_text.str.lower()

    out["team"] = _text_series(out, "team")
    out["player"] = _text_series(out, "player")
    out["type"] = _text_series(out, "type")
    out["outcome_type"] = _text_series(out, "outcome_type")
    out["team_norm"] = out["team"].map(_norm_team_name)

    for col in ["x", "y", "end_x", "end_y", "goal_mouth_x", "goal_mouth_y", "goal_mouth_z", "blocked_x", "blocked_y", "minute", "second", "expanded_minute"]:
        out[col] = _num_series(out, col)

    out["event_index"] = _num_series(out, "event_index")
    if out["event_index"].isna().all():
        out = out.reset_index(drop=True)
        out["event_index"] = out.index

    out["is_goal"] = _bool_series(out, "is_goal") | type_l.eq("goal") | outcome_l.str.contains("goal", na=False)
    out["is_shot"] = _bool_series(out, "is_shot") | _bool_series(out, "is_shot_event") | out["is_goal"] | type_l.str.contains("shot", na=False)
    out["is_touch"] = _bool_series(out, "is_touch") | type_l.str.contains("touch", na=False)
    out["is_pass"] = _bool_series(out, "is_pass") | (type_l.str.contains("pass", na=False) & ~type_l.str.contains("cross", na=False))
    out["is_cross"] = _bool_series(out, "is_cross") | type_l.str.contains("cross", na=False) | qual_text.str.contains("cross", na=False)

    type_compact = type_l.astype(str).str.lower().str.replace(r"[^a-z0-9]+", "", regex=True)
    out["is_take_on"] = _bool_series(out, "is_take_on") | type_compact.isin({"takeon", "dribble"})
    out["is_provider_take_on"] = _bool_series(out, "is_provider_take_on") | out["is_take_on"]
    out["is_carry"] = _bool_series(out, "is_carry") | (type_compact.str.contains("carry|ballcarry|run", regex=True, na=False) & ~out["is_take_on"])
    out["is_inferred_carry"] = _bool_series(out, "is_inferred_carry") | type_compact.eq("inferredcarry")
    out["is_defensive_action"] = _bool_series(out, "is_defensive_action") | type_l.str.contains("tackle|interception|clearance|block|recovery|challenge|aerial|duel", regex=True, na=False)

    card_compact = _text_series(out, "card_type").apply(lambda value: "" if str(value).strip().lower() in {"", "nan", "none", "null", "<na>", "false", "0"} else str(value).strip()).str.lower().str.replace(r"[^a-z0-9]+", "", regex=True)
    card_context_compact = (type_l.astype(str) + " " + qual_text.astype(str) + " " + _text_series(out, "card_type").astype(str)).str.lower().str.replace(r"[^a-z0-9]+", "", regex=True)
    valid_card_values = {"yellow", "yellowcard", "red", "redcard", "secondyellow", "secondyellowcard", "secondyellowred", "secondyellowredcard"}
    second_yellow_values = {"secondyellow", "secondyellowcard", "secondyellowred", "secondyellowredcard"}
    yellow_values = {"yellow", "yellowcard"}
    red_values = {"red", "redcard"}
    void_card = card_context_compact.str.contains("voidyellowcard|voidredcard|voidsecondyellow|voidedcard", regex=True, na=False)
    valid_card_type = card_compact.isin(valid_card_values) | type_compact.isin(valid_card_values)
    out["is_card"] = (type_compact.eq("card") | valid_card_type) & ~void_card
    out["is_yellow_card"] = (card_compact.isin(yellow_values) | type_compact.isin(yellow_values)) & out["is_card"]
    out["is_second_yellow"] = (card_compact.isin(second_yellow_values) | type_compact.isin(second_yellow_values)) & out["is_card"]
    out["is_red_card"] = ((card_compact.isin(red_values) | type_compact.isin(red_values)) & out["is_card"]) | out["is_second_yellow"]
    out["is_corner"] = _bool_series(out, "is_corner") | type_l.str.contains("corner", na=False) | qual_text.str.contains("corner", na=False)
    out["is_free_kick"] = _bool_series(out, "is_free_kick") | type_l.str.contains("free kick|freekick", regex=True, na=False) | qual_text.str.contains("free kick|freekick", regex=True, na=False)
    out["is_penalty"] = _bool_series(out, "is_penalty") | qual_text.str.contains("penalty", na=False)
    out["is_throw_in"] = _bool_series(out, "is_throw_in") | type_l.str.contains("throw", na=False) | qual_text.str.contains("throw", na=False)
    out["is_set_piece"] = _bool_series(out, "is_set_piece") | out["is_corner"] | out["is_free_kick"] | out["is_penalty"] | out["is_throw_in"]
    out["is_success"] = _bool_series(out, "is_success") | outcome_l.isin({"successful", "success", "won", "complete", "completed", "accurate"}) | out["is_goal"]

    out["final_third_entry"] = _bool_series(out, "final_third_entry") | (
        (out["is_pass"] | out["is_cross"] | out["is_carry"])
        & out["x"].lt(FINAL_THIRD_X)
        & out["end_x"].ge(FINAL_THIRD_X)
    )
    out["box_entry"] = _bool_series(out, "box_entry") | (
        (out["is_pass"] | out["is_cross"] | out["is_carry"])
        & ~(out["x"].ge(BOX_X) & out["y"].between(BOX_Y_MIN, BOX_Y_MAX, inclusive="both"))
        & (out["end_x"].ge(BOX_X) & out["end_y"].between(BOX_Y_MIN, BOX_Y_MAX, inclusive="both"))
    )
    out["attacking_third_touch"] = _bool_series(out, "attacking_third_touch") | (out["is_touch"] & out["x"].ge(ATTACKING_THIRD_X))
    out["high_regain"] = _bool_series(out, "high_regain") | (out["is_defensive_action"] & out["x"].ge(60.0))

    if "shirt_no" not in out.columns:
        shirt_source = next((col for col in ["shirtNo", "shirt_number", "shirtNumber", "jersey_number", "jerseyNumber", "squad_number", "number"] if col in out.columns), None)
        out["shirt_no"] = out[shirt_source] if shirt_source else math.nan
    else:
        for shirt_source in ["shirtNo", "shirt_number", "shirtNumber", "jersey_number", "jerseyNumber", "squad_number", "number"]:
            if shirt_source in out.columns:
                missing = out["shirt_no"].map(_clean_shirt_no).eq("")
                out.loc[missing, "shirt_no"] = out.loc[missing, shirt_source]
    out["shirt_no"] = out["shirt_no"].map(_clean_shirt_no)

    return out


def _add_take_on_endpoints(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events.copy()

    out = events.copy()
    required_cols = ["match_id", "period", "expanded_minute", "event_index", "team", "player", "x", "y"]
    for col in required_cols:
        if col not in out.columns:
            return out

    for col in ["end_x", "end_y"]:
        if col not in out.columns:
            out[col] = math.nan
    if "end_x_120" not in out.columns:
        out["end_x_120"] = math.nan
    if "end_y_80" not in out.columns:
        out["end_y_80"] = math.nan
    if "take_on_end_inferred" not in out.columns:
        out["take_on_end_inferred"] = False
    if "take_on_distance" not in out.columns:
        out["take_on_distance"] = 0.0
    if "take_on_seconds" not in out.columns:
        out["take_on_seconds"] = 0.0

    type_compact = _text_series(out, "type").str.lower().str.replace(r"[^a-z0-9]+", "", regex=True)
    outcome_text = _text_series(out, "outcome_type").str.lower()
    take_on_candidate = _bool_series(out, "is_take_on") | _bool_series(out, "is_provider_take_on") | type_compact.isin({"takeon", "dribble"})
    break_context = (
        type_compact.str.contains("substitution|formation|card|start|end|offsidegiven|foul", regex=True, na=False)
        | outcome_text.str.contains("offside", regex=True, na=False)
    )

    ordered = out.sort_values(["match_id", "period", "expanded_minute", "event_index"], na_position="last").reset_index()

    for pos in range(len(ordered) - 1):
        current = ordered.iloc[pos]
        original_index = current["index"]
        if not bool(take_on_candidate.loc[original_index]):
            continue

        start_x = _safe_float(current.get("x"))
        start_y = _safe_float(current.get("y"))
        if start_x is None or start_y is None:
            continue

        existing_end_x = _safe_float(out.at[original_index, "end_x"])
        existing_end_y = _safe_float(out.at[original_index, "end_y"])
        if existing_end_x is not None and existing_end_y is not None:
            if math.hypot(existing_end_x - start_x, existing_end_y - start_y) >= 1.0:
                continue

        current_seconds = _event_seconds(current)
        for next_pos in range(pos + 1, min(len(ordered), pos + 9)):
            nxt = ordered.iloc[next_pos]
            next_original_index = nxt["index"]

            if str(current.get("match_id", "")) != str(nxt.get("match_id", "")):
                break
            if str(current.get("period", "")) != str(nxt.get("period", "")):
                break

            seconds_gap = _event_seconds(nxt) - current_seconds
            if seconds_gap < 0.0:
                continue
            if seconds_gap > 10.0:
                break
            if bool(break_context.loc[next_original_index]):
                break
            if _norm_team_name(current.get("team", "")) != _norm_team_name(nxt.get("team", "")):
                break
            if str(current.get("player", "")).strip().lower() != str(nxt.get("player", "")).strip().lower():
                continue

            end_x = _safe_float(nxt.get("x"))
            end_y = _safe_float(nxt.get("y"))
            if end_x is None or end_y is None:
                continue

            distance = math.hypot(float(end_x) - float(start_x), float(end_y) - float(start_y))
            if distance < 1.0 or distance > 60.0:
                continue

            out.at[original_index, "end_x"] = float(end_x)
            out.at[original_index, "end_y"] = float(end_y)
            out.at[original_index, "end_x_120"] = float(end_x) * 1.2
            out.at[original_index, "end_y_80"] = float(end_y) * 0.8
            out.at[original_index, "take_on_end_inferred"] = True
            out.at[original_index, "take_on_distance"] = round(float(distance), 3)
            out.at[original_index, "take_on_seconds"] = round(float(seconds_gap), 3)
            if not str(out.at[original_index, "event_kind"] if "event_kind" in out.columns else "").strip():
                out.at[original_index, "event_kind"] = "take_on"
            break

    return out

def _infer_carry_rows(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events.copy()

    out = events.copy()
    required_cols = ["match_id", "period", "expanded_minute", "event_index", "team", "player", "x", "y"]
    for col in required_cols:
        if col not in out.columns:
            return out

    type_text = _text_series(out, "type").str.lower()
    type_compact = type_text.str.replace(r"[^a-z0-9]+", "", regex=True)
    outcome_text = _text_series(out, "outcome_type").str.lower()

    start_types = {"ballrecovery", "balltouch", "goodskill", "shieldballopp", "takeon", "dribble"}
    end_types = {
        "pass",
        "cross",
        "offsidepass",
        "takeon",
        "dribble",
        "savedshot",
        "attemptsaved",
        "missedshots",
        "shotonpost",
        "blockedshot",
        "goal",
        "dispossessed",
        "balltouch",
        "blockedpass",
        "goodskill",
    }

    start_candidate = (
        type_compact.isin(start_types)
        | (_bool_series(out, "is_take_on") & _bool_series(out, "is_success"))
        | (type_compact.eq("ballrecovery"))
    )
    end_candidate = (
        type_compact.isin(end_types)
        | _bool_series(out, "is_pass")
        | _bool_series(out, "is_cross")
        | _bool_series(out, "is_shot")
        | _bool_series(out, "is_take_on")
    )
    bad_context = (
        type_compact.str.contains("substitution|formation|card|start|end|offsidegiven|foul", regex=True, na=False)
        | outcome_text.str.contains("unsuccessful|lost|offside", regex=True, na=False)
    )

    sort_cols = ["match_id", "period", "expanded_minute", "event_index"]
    ordered = out.sort_values(sort_cols, na_position="last").reset_index()
    inferred: list[pd.Series] = []

    for pos in range(len(ordered) - 1):
        current = ordered.iloc[pos]
        nxt = ordered.iloc[pos + 1]
        original_index = current["index"]
        next_original_index = nxt["index"]

        if not bool(start_candidate.loc[original_index]) or not bool(end_candidate.loc[next_original_index]):
            continue
        if bool(bad_context.loc[original_index]) or bool(bad_context.loc[next_original_index]):
            continue
        if str(current.get("match_id", "")) != str(nxt.get("match_id", "")):
            continue
        if str(current.get("period", "")) != str(nxt.get("period", "")):
            continue
        if _norm_team_name(current.get("team", "")) != _norm_team_name(nxt.get("team", "")):
            continue
        if str(current.get("player", "")).strip().lower() != str(nxt.get("player", "")).strip().lower():
            continue

        start_x = _safe_float(current.get("x"))
        start_y = _safe_float(current.get("y"))
        end_x = _safe_float(nxt.get("x"))
        end_y = _safe_float(nxt.get("y"))
        if start_x is None or start_y is None or end_x is None or end_y is None:
            continue

        seconds_gap = _event_seconds(nxt) - _event_seconds(current)
        if seconds_gap < 0.0 or seconds_gap > 10.0:
            continue

        distance = math.hypot(float(end_x) - float(start_x), float(end_y) - float(start_y))
        if distance < 3.0 or distance > 60.0:
            continue

        carry = current.copy()
        carry["event_index"] = float(_safe_float(current.get("event_index"), float(pos)) or float(pos)) + 0.001
        carry["type"] = "InferredCarry"
        carry["outcome_type"] = "Successful"
        carry["end_x"] = float(end_x)
        carry["end_y"] = float(end_y)
        carry["is_carry"] = True
        carry["is_inferred_carry"] = True
        carry["is_take_on"] = False
        carry["is_provider_take_on"] = False
        carry["is_pass"] = False
        carry["is_cross"] = False
        carry["is_shot"] = False
        carry["is_goal"] = False
        carry["is_success"] = True
        carry["successful"] = True
        carry["is_touch"] = False
        carry["event_kind"] = "inferred_carry"
        carry["carry_distance"] = round(float(distance), 3)
        carry["carry_seconds"] = round(float(seconds_gap), 3)
        carry["inferred_from_event_index"] = current.get("event_index")
        carry["inferred_to_event_index"] = nxt.get("event_index")
        carry["carry_start_type"] = str(current.get("type", ""))
        carry["carry_end_type"] = str(nxt.get("type", ""))
        carry["label"] = f"{current.get('player', '')} | inferred carry | {current.get('type', '')} to {nxt.get('type', '')}"
        carry["final_third_entry"] = bool(start_x < FINAL_THIRD_X and end_x >= FINAL_THIRD_X)
        carry["box_entry"] = bool(
            not (start_x >= BOX_X and BOX_Y_MIN <= start_y <= BOX_Y_MAX)
            and (end_x >= BOX_X and BOX_Y_MIN <= end_y <= BOX_Y_MAX)
        )
        carry["attacking_third_touch"] = False
        inferred.append(carry)

    if not inferred:
        out["is_take_on"] = _bool_series(out, "is_take_on")
        out["is_provider_take_on"] = _bool_series(out, "is_provider_take_on")
        out["is_inferred_carry"] = _bool_series(out, "is_inferred_carry")
        if "event_kind" not in out.columns:
            out["event_kind"] = ""
        return out

    inferred_df = pd.DataFrame(inferred)
    combined = pd.concat([out, inferred_df], ignore_index=True, sort=False)
    combined = combined.sort_values(["period", "expanded_minute", "event_index"], na_position="last").reset_index(drop=True)
    for col in ["is_take_on", "is_provider_take_on", "is_inferred_carry", "is_carry"]:
        combined[col] = _bool_series(combined, col)
    if "event_kind" not in combined.columns:
        combined["event_kind"] = ""
    combined["event_kind"] = combined["event_kind"].fillna("").astype(str)
    return combined


def _team_events(events: pd.DataFrame, team_name: str) -> pd.DataFrame:
    if events.empty:
        return events
    return events.loc[events["team_norm"].eq(_norm_team_name(team_name))].copy()



def _shot_on_target_row(row: pd.Series) -> bool:
    if bool(row.get("is_goal", False)):
        return True

    text = _normalise_goal_tag(
        f"{row.get('type', '')} {row.get('outcome_type', '')} {row.get('qualifier_tags', '')} {row.get('qual_tags', '')}"
    )

    if any(token in text for token in ["blocked", "block", "missed", "offtarget", "wide", "overbar", "high"]):
        return False

    return any(token in text for token in ["attemptsaved", "saved", "save", "ontarget", "keeper", "goalkeeper"])


def _empty_team_summary() -> dict[str, Any]:
    return {
        "passes": 0,
        "pass_completion_pct": 0.0,
        "shots": 0,
        "shots_on_target": 0,
        "shot_accuracy_pct": 0.0,
        "xg": 0.0,
        "goals": 0,
        "crosses": 0,
        "final_third_entries": 0,
        "penalty_area_entries": 0,
        "box_entries": 0,
        "open_play_box_entries": 0,
        "set_piece_box_entries": 0,
        "average_field_position": 0.0,
        "defensive_actions": 0,
        "touches_in_attacking_third": 0,
        "high_regains": 0,
        "transition_threat_events": 0,
        "transition_threat_proxy": 0.0,
        "set_piece_actions": 0,
        "set_piece_shots": 0,
        "set_piece_goals": 0,
        "corners": 0,
        "free_kicks": 0,
        "throw_ins": 0,
        "penalties": 0,
        "cards": 0,
        "red_cards": 0,
        "fouls": 0,
        "interceptions": 0,
    }


def _fallback_shot_xg(row: pd.Series) -> float:
    provided = _shot_xg_value(row)
    if provided is not None:
        return float(provided)

    if bool(row.get("is_penalty", False)):
        return 0.76

    x = _safe_float(row.get("x"), 0.0) or 0.0
    y = _safe_float(row.get("y"), 50.0) or 50.0
    distance_to_goal = max(0.0, 100.0 - x)
    centrality = abs(y - 50.0)

    if x >= BOX_X and BOX_Y_MIN <= y <= BOX_Y_MAX:
        base = 0.14
        if distance_to_goal <= 7.0 and centrality <= 12.0:
            base = 0.28
        elif distance_to_goal <= 13.0 and centrality <= 20.0:
            base = 0.20
        elif centrality >= 26.0:
            base = 0.08
    elif x >= 76.0:
        base = 0.045
    else:
        base = 0.025

    text = _normalise_goal_tag(f"{row.get('type', '')} {row.get('outcome_type', '')} {row.get('qualifier_tags', '')} {row.get('qual_tags', '')}")
    if "bigchance" in text:
        base = max(base, 0.32)
    if "header" in text or "headed" in text:
        base *= 0.78
    if "directfreekick" in text or "freekick" in text:
        base = min(base, 0.08)

    return round(float(max(0.005, min(base, 0.80))), 4)


def _shot_contact_type(row: pd.Series) -> str:
    text = _normalise_goal_tag(
        f"{row.get('type', '')} {row.get('event_type', '')} {row.get('outcome_type', '')} "
        f"{row.get('qualifier_tags', '')} {row.get('qual_tags', '')} {row.get('qualifiers', '')}"
    )
    if any(token in text for token in ["header", "headed", "headshot", "aerial"]):
        return "headed"
    return "ground"


def _team_summary(events: pd.DataFrame) -> dict[str, Any]:
    return build_team_summary(events)

def _summary_number(summary: dict[str, Any], key: str) -> float:
    try:
        value = pd.to_numeric(pd.Series([summary.get(key, 0.0)]), errors="coerce").iloc[0]
        if pd.isna(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _normalised_pair_scores(home_raw: float, away_raw: float) -> tuple[float, float]:
    ceiling = max(abs(float(home_raw)), abs(float(away_raw)), 1.0)
    return (
        round(max(0.0, min(100.0, (float(home_raw) / ceiling) * 100.0)), 1),
        round(max(0.0, min(100.0, (float(away_raw) / ceiling) * 100.0)), 1),
    )


def _lower_is_better_pair_scores(home_raw: float, away_raw: float) -> tuple[float, float]:
    def clean(value: float) -> float:
        try:
            number = float(value)
        except Exception:
            return 0.0
        if math.isnan(number) or math.isinf(number):
            return 0.0
        return max(0.0, number)

    home_value = clean(home_raw)
    away_value = clean(away_raw)

    if home_value <= 0.0 and away_value <= 0.0:
        return 100.0, 100.0

    positive_values = [value for value in [home_value, away_value] if value > 0.0]
    best_value = min(positive_values) if positive_values else 1.0

    def score(value: float) -> float:
        if value <= 0.0:
            return 100.0
        return round(max(0.0, min(100.0, (best_value / value) * 100.0)), 1)

    return score(home_value), score(away_value)


def _build_team_radar(
    home_team: str,
    away_team: str,
    home_summary: dict[str, Any],
    away_summary: dict[str, Any],
    home_against_summary: dict[str, Any] | None = None,
    away_against_summary: dict[str, Any] | None = None,
    scope: str = "selected_match",
    source: str = "selected_match",
    home_match_count: int | None = None,
    away_match_count: int | None = None,
    extra_notes: list[str] | None = None,
) -> dict[str, Any]:
    metric_defs = [
        {"id": "progression", "label": "Progression", "description": "Final third entries, progressive carries and carries into the final third."},
        {"id": "final_third", "label": "Final third", "description": "Final third entries and touches in the attacking third."},
        {"id": "box_threat", "label": "Box threat", "description": "Box entries, shots and xG created."},
        {"id": "possession_control", "label": "Control", "description": "Pass volume, completion and average field position."},
        {"id": "counter_pressure", "label": "Counter pressure", "description": "High regains and defensive actions in advanced areas."},
        {"id": "box_defending", "label": "Box defending", "description": "Inverse opponent box threat conceded across the same scope."},
        {"id": "set_pieces", "label": "Set pieces", "description": "Corners, free kicks, set piece shots and set piece goals."},
    ]

    def attacking_raw(summary: dict[str, Any]) -> dict[str, float]:
        progression = (
            _summary_number(summary, "final_third_entries")
            + _summary_number(summary, "progressive_carries")
            + _summary_number(summary, "carry_final_third_entries")
        )
        final_third = (
            _summary_number(summary, "final_third_entries")
            + (0.35 * _summary_number(summary, "touches_in_attacking_third"))
        )
        box_threat = _box_threat_raw(summary)
        possession_control = (
            (0.12 * _summary_number(summary, "passes"))
            + (0.55 * _summary_number(summary, "pass_completion_pct"))
            + (0.35 * _summary_number(summary, "average_field_position"))
        )
        counter_pressure = (
            (2.4 * _summary_number(summary, "high_regains"))
            + _summary_number(summary, "defensive_actions")
            + (1.2 * _summary_number(summary, "interceptions"))
        )
        set_pieces = (
            (0.6 * _summary_number(summary, "corners"))
            + (0.25 * _summary_number(summary, "free_kicks"))
            + (2.5 * _summary_number(summary, "set_piece_shots"))
            + (5.0 * _summary_number(summary, "set_piece_goals"))
        )
        return {
            "progression": progression,
            "final_third": final_third,
            "box_threat": box_threat,
            "possession_control": possession_control,
            "counter_pressure": counter_pressure,
            "set_pieces": set_pieces,
        }

    home_raw = attacking_raw(home_summary)
    away_raw = attacking_raw(away_summary)

    home_against = home_against_summary if home_against_summary is not None else away_summary
    away_against = away_against_summary if away_against_summary is not None else home_summary
    home_against_box_threat = _box_threat_raw(home_against)
    away_against_box_threat = _box_threat_raw(away_against)
    home_box_defending_score, away_box_defending_score = _lower_is_better_pair_scores(
        home_against_box_threat,
        away_against_box_threat,
    )

    home_raw["box_defending"] = home_box_defending_score
    away_raw["box_defending"] = away_box_defending_score

    home_values: list[dict[str, Any]] = []
    away_values: list[dict[str, Any]] = []
    for item in metric_defs:
        key = str(item["id"])
        if key == "box_defending":
            home_score = home_box_defending_score
            away_score = away_box_defending_score
        else:
            home_score, away_score = _normalised_pair_scores(home_raw.get(key, 0.0), away_raw.get(key, 0.0))

        home_payload = {**item, "score": home_score, "raw_value": round(float(home_raw.get(key, 0.0)), 3)}
        away_payload = {**item, "score": away_score, "raw_value": round(float(away_raw.get(key, 0.0)), 3)}
        if key == "box_defending":
            home_payload["raw_against"] = round(float(home_against_box_threat), 3)
            away_payload["raw_against"] = round(float(away_against_box_threat), 3)
            home_payload["lower_raw_against_is_better"] = True
            away_payload["lower_raw_against_is_better"] = True
        home_values.append(home_payload)
        away_values.append(away_payload)

    notes = [
        "Radar scores compare the two selected teams within the same scope, not against a league wide percentile benchmark.",
        "Box defending is scored from opponent box threat conceded. Lower conceded threat is better, and the weaker side is scaled by ratio rather than forced to zero.",
    ]
    if scope == "season_to_date":
        notes.insert(0, "Radar values use season totals from all saved event rows for each team, not only the selected fixture.")
    else:
        notes.insert(0, "Radar values use the selected match because a season event frame was not available.")
    if extra_notes:
        notes.extend([str(note) for note in extra_notes if str(note).strip()])

    return {
        "scope": scope,
        "source": source,
        "metrics": metric_defs,
        "home": {"team": home_team, "match_count": home_match_count, "values": home_values},
        "away": {"team": away_team, "match_count": away_match_count, "values": away_values},
        "confidence_notes": notes,
    }


def _box_threat_raw(summary: dict[str, Any]) -> float:
    return (
        _summary_number(summary, "box_entries")
        + (2.0 * _summary_number(summary, "shots"))
        + (7.5 * _summary_number(summary, "xg"))
    )


def _season_match_count(events: pd.DataFrame) -> int:
    if events is None or events.empty or "match_id" not in events.columns:
        return 0
    match_ids = pd.to_numeric(events["match_id"], errors="coerce").dropna()
    return int(match_ids.nunique())


def _opponent_events_for_team(season_events: pd.DataFrame, team_name: str) -> pd.DataFrame:
    if season_events is None or season_events.empty or "match_id" not in season_events.columns:
        return pd.DataFrame()

    team_norm = _norm_team_name(team_name)
    if "team_norm" not in season_events.columns:
        return pd.DataFrame()

    team_match_ids = pd.to_numeric(
        season_events.loc[season_events["team_norm"].eq(team_norm), "match_id"],
        errors="coerce",
    ).dropna()
    if team_match_ids.empty:
        return pd.DataFrame()

    match_id_set = set(team_match_ids.astype(int).tolist())
    season_match_ids = pd.to_numeric(season_events["match_id"], errors="coerce")
    return season_events.loc[season_match_ids.isin(match_id_set) & ~season_events["team_norm"].eq(team_norm)].copy()


def _load_team_radar_season_events(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
) -> tuple[pd.DataFrame | None, str]:
    try:
        processed = load_processed_season_events(basedir, nation, tier, season)
    except Exception:
        processed = None

    if processed is not None and not processed.empty:
        return processed, "processed_parquet"

    try:
        raw = load_season_events(basedir, nation, tier, season)
    except Exception:
        raw = None

    if raw is not None and not raw.empty:
        return raw, "raw_csv"

    return None, "selected_match_fallback"


def _build_season_team_radar(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    home_team: str,
    away_team: str,
    selected_home_summary: dict[str, Any],
    selected_away_summary: dict[str, Any],
) -> dict[str, Any]:
    season_events, source = _load_team_radar_season_events(basedir, nation, tier, season)
    if season_events is None or season_events.empty:
        return _build_team_radar(
            home_team,
            away_team,
            selected_home_summary,
            selected_away_summary,
            scope="selected_match_fallback",
            source=source,
            extra_notes=["No season event frame was available, so the radar fell back to the selected fixture."],
        )

    try:
        season_events = _ensure_flags(season_events)
        season_events = _add_take_on_endpoints(season_events)
        season_events = _infer_carry_rows(season_events)

        home_season_events = _team_events(season_events, home_team)
        away_season_events = _team_events(season_events, away_team)

        if home_season_events.empty or away_season_events.empty:
            raise ValueError("One of the selected teams had no saved season event rows.")

        home_summary = _team_summary(home_season_events)
        away_summary = _team_summary(away_season_events)
        home_against_summary = _team_summary(_opponent_events_for_team(season_events, home_team))
        away_against_summary = _team_summary(_opponent_events_for_team(season_events, away_team))

        return _build_team_radar(
            home_team,
            away_team,
            home_summary,
            away_summary,
            home_against_summary=home_against_summary,
            away_against_summary=away_against_summary,
            scope="season_to_date",
            source=source,
            home_match_count=_season_match_count(home_season_events),
            away_match_count=_season_match_count(away_season_events),
        )
    except Exception as exc:
        return _build_team_radar(
            home_team,
            away_team,
            selected_home_summary,
            selected_away_summary,
            scope="selected_match_fallback",
            source=source,
            extra_notes=[f"Season radar build failed, so the selected fixture was used instead: {type(exc).__name__}: {exc}"],
        )


def _grid_map(events: pd.DataFrame, x_bins: int = 6, y_bins: int = 5) -> list[dict[str, Any]]:
    if events.empty:
        return []
    frame = events.loc[events["is_touch"]].copy()
    if frame.empty:
        frame = events.copy()
    frame = frame.dropna(subset=["x", "y"])
    if frame.empty:
        return []
    frame["x_bin"] = pd.cut(frame["x"], bins=x_bins, labels=False, include_lowest=True, right=False)
    frame["y_bin"] = pd.cut(frame["y"], bins=y_bins, labels=False, include_lowest=True, right=False)
    grouped = frame.groupby(["x_bin", "y_bin"], dropna=True).size().reset_index(name="count").sort_values(["x_bin", "y_bin"])
    return [
        {"x_bin": int(row["x_bin"]), "y_bin": int(row["y_bin"]), "count": int(row["count"])}
        for _, row in grouped.iterrows()
        if pd.notna(row["x_bin"]) and pd.notna(row["y_bin"])
    ]



def _json_safe_value(value: object) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(float(value), 6)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return str(value)


def _raw_event_rows(events: pd.DataFrame, limit: int = 5000) -> list[dict[str, Any]]:
    if events.empty:
        return []

    frame = events.copy().head(int(limit))
    rows: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        rows.append({str(key): _json_safe_value(value) for key, value in row.items()})
    return rows

def _action_points(events: pd.DataFrame) -> list[dict[str, Any]]:
    if events.empty:
        return []

    frame = _coerce_analysis_numeric_columns(events)
    frame = frame.loc[frame["is_pass"] | frame["is_cross"] | frame["is_carry"] | frame["is_take_on"] | frame["is_shot"] | frame["is_defensive_action"]].copy()
    frame = frame.dropna(subset=["x", "y"])
    out: list[dict[str, Any]] = []
    for _, row in frame.head(520).iterrows():
        x_value = _safe_float(row.get("x"))
        y_value = _safe_float(row.get("y"))
        if x_value is None or y_value is None:
            continue

        raw_event_kind = row.get("event_kind", "")
        event_kind = "" if _is_missing_scalar(raw_event_kind) else str(raw_event_kind).strip()
        if not event_kind:
            if bool(row.get("is_inferred_carry", False)):
                event_kind = "inferred_carry"
            elif bool(row.get("is_take_on", False)):
                event_kind = "take_on"
        raw_label = row.get("label", "")
        label_text = "" if _is_missing_scalar(raw_label) else str(raw_label)
        carry_distance_value = _safe_float(row.get("carry_distance"), 0.0) or 0.0
        carry_seconds_value = _safe_float(row.get("carry_seconds"), 0.0) or 0.0
        take_on_distance_value = _safe_float(row.get("take_on_distance"), 0.0) or 0.0
        take_on_seconds_value = _safe_float(row.get("take_on_seconds"), 0.0) or 0.0
        event_index_value = _safe_float(row.get("event_index"))
        out.append(
            {
                "event_index": int(event_index_value) if event_index_value is not None else None,
                "x": round(float(x_value), 2),
                "y": round(float(y_value), 2),
                "end_x": _round_float_or_none(row.get("end_x"), 2),
                "end_y": _round_float_or_none(row.get("end_y"), 2),
                "minute": _round_float_or_none(row.get("expanded_minute"), 2),
                "team": str(row.get("team", "")),
                "player": str(row.get("player", "")),
                "type": str(row.get("type", "")),
                "event_type": event_kind or str(row.get("type", "")),
                "event_kind": event_kind,
                "outcome_type": str(row.get("outcome_type", "")),
                "label": label_text or f"{row.get('player', '')} | {row.get('type', '')} | {row.get('outcome_type', '')}",
                "successful": bool(row.get("is_success", False)),
                "is_success": bool(row.get("is_success", False)),
                "is_carry": bool(row.get("is_carry", False)),
                "is_inferred_carry": bool(row.get("is_inferred_carry", False)),
                "is_take_on": bool(row.get("is_take_on", False)),
                "is_provider_take_on": bool(row.get("is_provider_take_on", False)),
                "carry_distance": round(float(carry_distance_value), 2),
                "carry_seconds": round(float(carry_seconds_value), 2),
                "take_on_end_inferred": bool(row.get("take_on_end_inferred", False)),
                "take_on_distance": round(float(take_on_distance_value), 2),
                "take_on_seconds": round(float(take_on_seconds_value), 2),
            }
        )
    return out


def _shot_points(events: pd.DataFrame) -> list[dict[str, Any]]:
    if events.empty:
        return []
    frame = events.loc[events["is_shot"]].copy().dropna(subset=["x", "y"])
    points: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        shot_contact = _shot_contact_type(row)
        points.append(
            {
                "event_index": int(row.get("event_index", 0)) if pd.notna(row.get("event_index")) else None,
                "x": round(float(row["x"]), 2),
                "y": round(float(row["y"]), 2),
                "minute": round(float(row["expanded_minute"]), 2) if pd.notna(row.get("expanded_minute")) else None,
                "player": str(row.get("player", "")),
                "team": str(row.get("team", "")),
                "type": str(row.get("type", "")),
                "outcome_type": str(row.get("outcome_type", "")),
                "shot_contact": shot_contact,
                "shot_body_part": "head" if shot_contact == "headed" else "foot_or_other",
                "is_header": shot_contact == "headed",
                "is_goal": bool(row.get("is_goal", False)),
            }
        )
    return points


def _row_numeric_value(row: pd.Series, candidates: list[str]) -> float | None:
    for col in candidates:
        if col not in row:
            continue
        value = pd.to_numeric(pd.Series([row.get(col)]), errors="coerce").iloc[0]
        if pd.notna(value):
            return float(value)
    return None


def _shot_xg_value(row: pd.Series) -> float | None:
    value = _row_numeric_value(
        row,
        [
            "xg",
            "xG",
            "expected_goals",
            "expected_goals_raw",
            "shot_xg",
            "np_xg",
            "non_penalty_xg",
        ],
    )
    if value is None:
        return None
    return round(float(max(0.0, min(value, 1.0))), 4)


def _goal_zone(horizontal: float, vertical: float) -> str:
    horizontal = float(max(0.0, min(horizontal, 100.0)))
    vertical = float(max(0.0, min(vertical, 100.0)))

    if vertical < 33.333:
        height_zone = "low"
    elif vertical < 66.667:
        height_zone = "middle"
    else:
        height_zone = "high"

    if horizontal < 33.333:
        width_zone = "left"
    elif horizontal < 66.667:
        width_zone = "centre"
    else:
        width_zone = "right"

    return f"{height_zone}_{width_zone}"


def _normalise_goal_tag(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _goal_qualifier_tokens(row: pd.Series) -> tuple[set[str], list[str]]:
    tokens: set[str] = set()
    labels: list[str] = []

    for col in ["qualifier_tags", "qual_tags"]:
        if col not in row:
            continue

        raw_value = row.get(col)
        if raw_value is None or (isinstance(raw_value, float) and pd.isna(raw_value)):
            continue

        if isinstance(raw_value, (list, tuple, set)):
            values = list(raw_value)
        else:
            values = re.split(r"[,|;]", str(raw_value))

        for value in values:
            label = str(value).strip().strip("[]'\"")
            if not label or label.lower() in {"nan", "none", "null"}:
                continue
            labels.append(label)
            token = _normalise_goal_tag(label)
            if token:
                tokens.add(token)

    return tokens, labels


def _normalise_percent_value(value: float | None) -> float | None:
    if value is None:
        return None
    if 0.0 < float(value) <= 1.5:
        return float(value) * 100.0
    return float(value)


def _goal_frame_horizontal_from_pitch_y(pitch_y: float | None) -> float | None:
    if pitch_y is None:
        return None
    return ((float(pitch_y) - GOAL_FRAME_LEFT_POST_Y) / (GOAL_FRAME_RIGHT_POST_Y - GOAL_FRAME_LEFT_POST_Y)) * 100.0


def _goal_mouth_target_from_pitch(row: pd.Series) -> dict[str, Any]:
    target_x = _row_numeric_value(row, ["goal_mouth_x"])
    target_y = _row_numeric_value(row, ["goal_mouth_y"])
    source_columns = ["goal_mouth_x", "goal_mouth_y"] if target_y is not None else []

    if target_y is None:
        target_x = _row_numeric_value(row, ["blocked_x"])
        target_y = _row_numeric_value(row, ["blocked_y"])
        source_columns = ["blocked_x", "blocked_y"] if target_y is not None else []

    if target_y is None:
        target_x = _row_numeric_value(row, ["end_x"])
        target_y = _row_numeric_value(row, ["end_y"])
        source_columns = ["end_x", "end_y"] if target_y is not None else []

    if target_y is None:
        target_x = _row_numeric_value(row, ["x"])
        target_y = _row_numeric_value(row, ["y"])
        source_columns = ["x", "y"] if target_y is not None else []

    return {
        "pitch_x": _normalise_percent_value(target_x),
        "pitch_y": _normalise_percent_value(target_y),
        "source_columns": source_columns,
    }


def _estimated_goal_mouth_height(row: pd.Series) -> dict[str, Any]:
    raw_z = _row_numeric_value(row, ["goal_mouth_z"])
    if raw_z is not None:
        return {
            "vertical": _normalise_percent_value(raw_z),
            "source": "goal_mouth_z",
            "source_columns": ["goal_mouth_z"],
            "estimated": False,
        }

    tokens, _ = _goal_qualifier_tokens(row)
    event_text = _normalise_goal_tag(f"{row.get('type', '')} {row.get('outcome_type', '')} {row.get('qualifier_tags', '')} {row.get('qual_tags', '')}")

    if any(token in tokens for token in {"high", "over", "overbar"}) or any(token in event_text for token in ["over", "high"]):
        value = 108.0
    elif any(token in tokens for token in {"low", "lowleft", "lowright", "lowcentre", "lowcenter"}) or "low" in event_text:
        value = 18.0
    elif any(token in tokens for token in {"top", "upper", "upperleft", "upperright", "topcentre", "topcenter"}) or "top" in event_text:
        value = 82.0
    elif "blocked" in event_text or "block" in event_text:
        value = 35.0
    else:
        value = 50.0

    return {
        "vertical": value,
        "source": "estimated_from_outcome",
        "source_columns": [],
        "estimated": True,
    }


def _goalmouth_display_position(row: pd.Series, horizontal: float, vertical: float) -> dict[str, Any]:
    tokens, qualifier_labels = _goal_qualifier_tokens(row)
    type_text = str(row.get("type", "")).strip().lower()
    outcome_text = str(row.get("outcome_type", "")).strip().lower()
    event_text = _normalise_goal_tag(f"{type_text} {outcome_text}")

    is_goal = bool(row.get("is_goal", False))
    is_saved = any(token in event_text for token in ["attemptsaved", "saved", "save"])
    is_missed = any(token in event_text for token in ["missedshots", "missed", "offtarget"])
    is_blocked = any(token in event_text for token in ["blocked", "block"]) or "blocked" in tokens
    is_woodwork = any(token in tokens for token in ["hitwoodwork", "woodwork"]) or "shotonpost" in event_text

    off_high = "high" in tokens or "over" in tokens or "overbar" in tokens or vertical > 100.0
    off_left = any(token in tokens for token in ["left", "farwideleft", "wideleft", "missleft"]) or horizontal < 0.0
    off_right = any(token in tokens for token in ["right", "farwideright", "wideright", "missright"]) or horizontal > 100.0

    display_horizontal = float(horizontal)
    display_vertical = float(vertical)

    if off_left:
        display_horizontal = min(display_horizontal, -7.0)
    elif off_right:
        display_horizontal = max(display_horizontal, 107.0)

    if off_high:
        display_vertical = max(display_vertical, 108.0)

    on_target_plane = (
        0.0 <= horizontal <= 100.0
        and 0.0 <= vertical <= 100.0
        and not off_high
        and not off_left
        and not off_right
        and not is_missed
    )

    if is_goal:
        status = "goal"
        on_target_plane = True
    elif is_blocked:
        status = "blocked"
        on_target_plane = False
    elif is_woodwork:
        status = "woodwork"
        on_target_plane = False
    elif off_high and off_left:
        status = "over and left"
    elif off_high and off_right:
        status = "over and right"
    elif off_high:
        status = "over the bar"
    elif off_left:
        status = "wide left"
    elif off_right:
        status = "wide right"
    elif is_saved:
        status = "saved on target"
        on_target_plane = True
    elif is_missed:
        status = "missed"
        on_target_plane = False
    else:
        status = "on target plane" if on_target_plane else "off target"

    return {
        "display_horizontal": round(display_horizontal, 2),
        "display_vertical": round(display_vertical, 2),
        "on_target_plane": bool(on_target_plane),
        "goal_mouth_status": status,
        "goal_mouth_qualifiers": qualifier_labels[:12],
        "is_goal_mouth_high": bool(off_high),
        "is_goal_mouth_left": bool(off_left),
        "is_goal_mouth_right": bool(off_right),
        "is_goal_mouth_woodwork": bool(is_woodwork),
    }


def _goal_mouth_plot_position(horizontal: float, vertical: float, display: dict[str, Any]) -> dict[str, Any]:
    display_horizontal = float(display.get("display_horizontal", horizontal))
    display_vertical = float(display.get("display_vertical", vertical))
    plot_x = display_horizontal
    plot_y = display_vertical
    inside_frame = 0.0 <= plot_x <= 100.0 and 0.0 <= plot_y <= 100.0 and bool(display.get("on_target_plane", False))

    if bool(display.get("is_goal_mouth_left", False)):
        plot_x = min(plot_x, -8.0)
    if bool(display.get("is_goal_mouth_right", False)):
        plot_x = max(plot_x, 108.0)
    if bool(display.get("is_goal_mouth_high", False)):
        plot_y = max(plot_y, 108.0)

    return {
        "goal_mouth_plot_x": round(float(plot_x), 2),
        "goal_mouth_plot_y": round(float(plot_y), 2),
        "goal_mouth_inside_frame": bool(inside_frame),
        "goal_mouth_outcome_zone": str(display.get("goal_mouth_status", "shot")),
    }


def _goalmouth_points(events: pd.DataFrame) -> list[dict[str, Any]]:
    if events.empty:
        return []

    frame = events.loc[events["is_shot"]].copy()
    if frame.empty:
        return []

    points: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        target = _goal_mouth_target_from_pitch(row)
        target_pitch_y = target.get("pitch_y")
        horizontal = _goal_frame_horizontal_from_pitch_y(target_pitch_y if isinstance(target_pitch_y, (int, float)) else None)
        height_info = _estimated_goal_mouth_height(row)
        vertical = height_info.get("vertical")

        if horizontal is None or vertical is None:
            continue

        shot_contact = _shot_contact_type(row)
        display = _goalmouth_display_position(row, float(horizontal), float(vertical))
        display_horizontal = float(display["display_horizontal"])
        display_vertical = float(display["display_vertical"])
        plot = _goal_mouth_plot_position(float(horizontal), float(vertical), display)
        target_columns = target.get("source_columns", []) if isinstance(target.get("source_columns"), list) else []
        height_columns = height_info.get("source_columns", []) if isinstance(height_info.get("source_columns"), list) else []

        points.append(
            {
                "event_index": int(row.get("event_index", 0)) if pd.notna(row.get("event_index")) else None,
                "x": round(display_horizontal, 2),
                "y": round(display_vertical, 2),
                "pitch_x": _row_numeric_value(row, ["x"]),
                "pitch_y": _row_numeric_value(row, ["y"]),
                "goal_target_pitch_x": round(float(target["pitch_x"]), 2) if isinstance(target.get("pitch_x"), (int, float)) else None,
                "goal_target_pitch_y": round(float(target["pitch_y"]), 2) if isinstance(target.get("pitch_y"), (int, float)) else None,
                "goal_frame_left_post_y": round(float(GOAL_FRAME_LEFT_POST_Y), 3),
                "goal_frame_right_post_y": round(float(GOAL_FRAME_RIGHT_POST_Y), 3),
                "goal_mouth_horizontal": round(float(horizontal), 2),
                "goal_mouth_vertical": round(float(vertical), 2),
                "goal_mouth_display_x": round(display_horizontal, 2),
                "goal_mouth_display_y": round(display_vertical, 2),
                **plot,
                "raw_goal_mouth_x": _row_numeric_value(row, ["goal_mouth_x"]),
                "raw_goal_mouth_y": _row_numeric_value(row, ["goal_mouth_y"]),
                "raw_goal_mouth_z": _row_numeric_value(row, ["goal_mouth_z"]),
                "raw_blocked_x": _row_numeric_value(row, ["blocked_x"]),
                "raw_blocked_y": _row_numeric_value(row, ["blocked_y"]),
                "coordinate_source": "pitch_target_y_projected_to_goal_frame",
                "goal_mouth_coordinate_mode": "pitch_xy_projected_to_net",
                "goal_mouth_height_source": str(height_info.get("source", "")),
                "goal_mouth_height_estimated": bool(height_info.get("estimated", False)),
                "goal_mouth_source_columns": target_columns + height_columns,
                "on_target_plane": bool(display["on_target_plane"]),
                "goal_mouth_status": str(display["goal_mouth_status"]),
                "goal_mouth_qualifiers": display["goal_mouth_qualifiers"],
                "is_goal_mouth_high": bool(display["is_goal_mouth_high"]),
                "is_goal_mouth_left": bool(display["is_goal_mouth_left"]),
                "is_goal_mouth_right": bool(display["is_goal_mouth_right"]),
                "is_goal_mouth_woodwork": bool(display["is_goal_mouth_woodwork"]),
                "zone": _goal_zone(float(horizontal), float(vertical)),
                "xg": _shot_xg_value(row),
                "xt_added": round(float(row.get("xt_added", 0.0) or 0.0), 4),
                "positive_xt": round(float(row.get("positive_xt", 0.0) or 0.0), 4),
                "minute": round(float(row["expanded_minute"]), 2) if pd.notna(row.get("expanded_minute")) else None,
                "player": str(row.get("player", "")),
                "team": str(row.get("team", "")),
                "type": str(row.get("type", "")),
                "outcome_type": str(row.get("outcome_type", "")),
                "shot_contact": shot_contact,
                "shot_body_part": "head" if shot_contact == "headed" else "foot_or_other",
                "is_header": shot_contact == "headed",
                "is_goal": bool(row.get("is_goal", False)),
            }
        )

    return points


def _row_flag(row: pd.Series, key: str) -> bool:
    value = row.get(key, False)
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except Exception:
        pass
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) > 0.0
    return str(value).strip().lower() in {"true", "1", "yes", "y", "successful", "success", "won"}


def _danger_area_xy(x_value: object, y_value: object) -> bool:
    x = _safe_float(x_value)
    y = _safe_float(y_value)
    if x is None or y is None:
        return False
    if _in_penalty_box_xy(x, y):
        return True
    return x >= 78.0 and 24.0 <= y <= 76.0


def _same_phase_following_actions(ordered: pd.DataFrame, pos: int, seconds_window: float = 20.0, max_actions: int = 8) -> pd.DataFrame:
    current = ordered.iloc[pos]
    current_seconds = _event_seconds(current)
    rows = []
    current_match = str(current.get("match_id", ""))
    current_period = str(current.get("period", ""))
    current_team = _norm_team_name(current.get("team", ""))

    for next_pos in range(pos + 1, len(ordered)):
        nxt = ordered.iloc[next_pos]
        if str(nxt.get("match_id", "")) != current_match:
            break
        if str(nxt.get("period", "")) != current_period:
            break
        if _norm_team_name(nxt.get("team", "")) != current_team:
            break
        seconds_gap = _event_seconds(nxt) - current_seconds
        if seconds_gap < 0:
            continue
        if seconds_gap > seconds_window:
            break
        rows.append(nxt)
        if len(rows) >= max_actions:
            break

    if not rows:
        return pd.DataFrame(columns=ordered.columns)
    return pd.DataFrame(rows)


def _final_third_pass_outcome(row: pd.Series, following: pd.DataFrame) -> dict[str, Any]:
    start_x = _safe_float(row.get("x"))
    start_y = _safe_float(row.get("y"))
    end_x = _safe_float(row.get("end_x"), start_x)
    end_y = _safe_float(row.get("end_y"), start_y)
    successful = _row_flag(row, "is_success") or _row_flag(row, "successful")
    pass_backwards = bool(start_x is not None and end_x is not None and end_x <= start_x - 5.0)
    box_entry = bool(
        start_x is not None
        and start_y is not None
        and end_x is not None
        and end_y is not None
        and not _in_penalty_box_xy(start_x, start_y)
        and _in_penalty_box_xy(end_x, end_y)
    )
    ended_in_danger = _danger_area_xy(end_x, end_y)

    led_to_goal = False
    led_to_shot = False
    led_to_carry_danger = False
    led_to_take_on_danger = False
    followed_by_backward_pass = pass_backwards
    next_action_label = ""

    if following is not None and not following.empty:
        for _, nxt in following.iterrows():
            next_start_x = _safe_float(nxt.get("x"))
            next_start_y = _safe_float(nxt.get("y"))
            next_end_x = _safe_float(nxt.get("end_x"), next_start_x)
            next_end_y = _safe_float(nxt.get("end_y"), next_start_y)
            if not next_action_label:
                next_action_label = f"{nxt.get('player', '')} {nxt.get('type', '')}".strip()
            if _row_flag(nxt, "is_goal"):
                led_to_goal = True
            if _row_flag(nxt, "is_shot"):
                led_to_shot = True
            if _row_flag(nxt, "is_carry") and (_danger_area_xy(next_end_x, next_end_y) or _danger_area_xy(next_start_x, next_start_y)):
                led_to_carry_danger = True
            if _row_flag(nxt, "is_take_on") and (_danger_area_xy(next_end_x, next_end_y) or _danger_area_xy(next_start_x, next_start_y)):
                led_to_take_on_danger = True
            if (_row_flag(nxt, "is_pass") or _row_flag(nxt, "is_cross")) and next_start_x is not None and next_end_x is not None and next_end_x <= next_start_x - 5.0:
                followed_by_backward_pass = True

    if led_to_goal:
        outcome_class = "goal_chain"
        outcome_label = "Led to goal"
    elif led_to_shot:
        outcome_class = "shot_chain"
        outcome_label = "Led to shot"
    elif led_to_carry_danger:
        outcome_class = "carry_into_danger"
        outcome_label = "Led to dangerous carry"
    elif led_to_take_on_danger:
        outcome_class = "take_on_in_danger"
        outcome_label = "Led to dangerous take on"
    elif box_entry:
        outcome_class = "box_entry"
        outcome_label = "Entered penalty box"
    elif followed_by_backward_pass:
        outcome_class = "backward_recycle"
        outcome_label = "Recycled backwards"
    elif not successful:
        outcome_class = "incomplete"
        outcome_label = "Incomplete"
    else:
        outcome_class = "completed"
        outcome_label = "Completed"

    return {
        "outcome_class": outcome_class,
        "outcome_label": outcome_label,
        "successful": bool(successful),
        "is_incomplete": bool(not successful),
        "is_box_entry": bool(box_entry),
        "ended_in_danger": bool(ended_in_danger),
        "led_to_goal": bool(led_to_goal),
        "led_to_shot": bool(led_to_shot),
        "led_to_carry_danger": bool(led_to_carry_danger),
        "led_to_take_on_danger": bool(led_to_take_on_danger),
        "followed_by_backward_pass": bool(followed_by_backward_pass),
        "next_action_label": next_action_label,
    }


def _final_third_pass_map(events: pd.DataFrame) -> list[dict[str, Any]]:
    if events.empty:
        return []

    frame = events.copy()
    pass_mask = (_bool_series(frame, "is_pass") | _bool_series(frame, "is_cross"))
    frame = frame.loc[pass_mask].copy()
    frame = frame.dropna(subset=["x", "y"], how="any")
    if frame.empty:
        return []

    end_x = pd.to_numeric(frame.get("end_x"), errors="coerce")
    frame = frame.loc[(pd.to_numeric(frame["x"], errors="coerce").ge(FINAL_THIRD_X)) | (end_x.ge(FINAL_THIRD_X))].copy()
    if frame.empty:
        return []

    ordered = _sort_events_by_match_time(events.copy(), ["expanded_minute", "event_index"]).reset_index(drop=True)
    ordered_event_index = pd.to_numeric(ordered.get("event_index"), errors="coerce") if "event_index" in ordered.columns else pd.Series(range(len(ordered)), index=ordered.index)
    position_by_event_index = {float(value): int(pos) for pos, value in enumerate(ordered_event_index.tolist()) if pd.notna(value)}

    output: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        event_index_value = _safe_float(row.get("event_index"))
        pos = position_by_event_index.get(float(event_index_value)) if event_index_value is not None else None
        if pos is None:
            continue

        following = _same_phase_following_actions(ordered, pos, seconds_window=20.0, max_actions=8)
        outcome = _final_third_pass_outcome(row, following)
        start_x = _safe_float(row.get("x"))
        start_y = _safe_float(row.get("y"))
        end_x_value = _safe_float(row.get("end_x"), start_x)
        end_y_value = _safe_float(row.get("end_y"), start_y)
        if start_x is None or start_y is None:
            continue

        output.append(
            {
                "event_index": int(event_index_value) if event_index_value is not None else None,
                "x": round(float(start_x), 2),
                "y": round(float(start_y), 2),
                "end_x": round(float(end_x_value), 2) if end_x_value is not None else None,
                "end_y": round(float(end_y_value), 2) if end_y_value is not None else None,
                "minute": round(float(row["expanded_minute"]), 2) if pd.notna(row.get("expanded_minute")) else None,
                "team": str(row.get("team", "")),
                "player": str(row.get("player", "")),
                "type": str(row.get("type", "")),
                "event_type": "cross" if _row_flag(row, "is_cross") else "pass",
                "outcome_type": str(row.get("outcome_type", "")),
                "is_set_piece": _row_flag(row, "is_set_piece"),
                "is_cross": _row_flag(row, "is_cross"),
                "label": f"{row.get('player', '')} | {outcome['outcome_label']} | {row.get('type', '')}",
                **outcome,
            }
        )

    output.sort(
        key=lambda item: (
            {
                "goal_chain": 0,
                "shot_chain": 1,
                "carry_into_danger": 2,
                "take_on_in_danger": 3,
                "box_entry": 4,
                "incomplete": 5,
                "backward_recycle": 6,
                "completed": 7,
            }.get(str(item.get("outcome_class")), 8),
            float(item.get("minute") or 0.0),
        )
    )
    return output[:240]


def _empty_pass_network(team_name: str = "") -> dict[str, Any]:
    return {
        "team": team_name,
        "total_passes": 0,
        "players": [],
        "connections": [],
    }



def _pass_network_player_status(ordered: pd.DataFrame) -> dict[str, dict[str, Any]]:
    status: dict[str, dict[str, Any]] = {}

    if ordered.empty:
        return status

    for _, row in ordered.iterrows():
        player = str(row.get("player", "") or "").strip()
        if not player:
            continue

        current = status.setdefault(
            player,
            {
                "is_substitute": False,
                "substitution_minute": None,
                "shirt_no": "",
            },
        )

        shirt_no = _clean_shirt_no(row.get("shirt_no"))
        if shirt_no and not _clean_shirt_no(current.get("shirt_no")):
            current["shirt_no"] = shirt_no

        position_text = " ".join(
            [
                str(row.get("player_position", "") or ""),
                str(row.get("position", "") or ""),
                str(row.get("position_group", "") or ""),
                str(row.get("role", "") or ""),
                str(row.get("status", "") or ""),
            ]
        ).strip().lower()

        starter_text = ""
        for col in ["is_starter", "starter", "starting", "is_first_eleven", "isFirstEleven"]:
            if col in row and str(row.get(col, "")).strip() not in {"", "nan", "None", "<NA>"}:
                starter_text = str(row.get(col, "")).strip().lower()
                break

        position_is_sub = bool(re.search(r"(^|[^a-z])(sub|substitute|bench)([^a-z]|$)", position_text))
        starter_is_false = starter_text in {"false", "0", "no", "n", "sub", "substitute", "bench"}

        if position_is_sub or starter_is_false:
            current["is_substitute"] = True
            minute_value = _safe_float(row.get("expanded_minute"))
            if minute_value is not None:
                previous_minute = current.get("substitution_minute")
                if previous_minute is None or float(minute_value) < float(previous_minute):
                    current["substitution_minute"] = round(float(minute_value), 2)

    return status


def _next_same_team_receiver(ordered: pd.DataFrame, pos: int, row: pd.Series) -> tuple[str, float | None, float | None] | None:
    passer = str(row.get("player", "")).strip()
    team_norm = _norm_team_name(row.get("team", ""))
    period = _period_to_int(row.get("period"))
    current_seconds = _event_seconds(row)
    end_x = _safe_float(row.get("end_x"))
    end_y = _safe_float(row.get("end_y"))

    if end_x is None or end_y is None:
        return None

    for next_pos in range(pos + 1, min(len(ordered), pos + 9)):
        nxt = ordered.iloc[next_pos]
        if _period_to_int(nxt.get("period")) != period:
            break

        seconds_gap = _event_seconds(nxt) - current_seconds
        if seconds_gap < 0.0:
            continue
        if seconds_gap > 12.0:
            break

        if _norm_team_name(nxt.get("team", "")) != team_norm:
            break

        receiver = str(nxt.get("player", "")).strip()
        if not receiver:
            continue
        if receiver.lower() == passer.lower():
            continue

        type_compact = re.sub(r"[^a-z0-9]+", "", str(nxt.get("type", "")).lower())
        if type_compact in {"substitution", "formationchange", "card", "start", "end", "offsidegiven"}:
            continue

        next_x = _safe_float(nxt.get("x"))
        next_y = _safe_float(nxt.get("y"))
        if next_x is not None and next_y is not None:
            if math.hypot(float(next_x) - float(end_x), float(next_y) - float(end_y)) > 32.0 and receiver == passer:
                continue

        return receiver, end_x, end_y

    return None


def _build_pass_network(events: pd.DataFrame, team_name: str) -> dict[str, Any]:
    if events.empty:
        return _empty_pass_network(team_name)

    ordered = _sort_events_by_match_time(events.copy(), ["expanded_minute", "event_index"]).reset_index(drop=True)
    if ordered.empty:
        return _empty_pass_network(team_name)

    player_status = _pass_network_player_status(ordered)

    pass_mask = (_bool_series(ordered, "is_pass") | _bool_series(ordered, "is_cross"))
    pass_mask = pass_mask & _bool_series(ordered, "is_success")
    pass_mask = pass_mask & ~_bool_series(ordered, "is_set_piece")
    pass_mask = pass_mask & ordered["x"].notna() & ordered["y"].notna() & ordered["end_x"].notna() & ordered["end_y"].notna()

    records: list[dict[str, Any]] = []
    for pos, row in ordered.loc[pass_mask].iterrows():
        passer = str(row.get("player", "")).strip()
        if not passer:
            continue

        receiver_info = _next_same_team_receiver(ordered, int(pos), row)
        if receiver_info is None:
            continue

        receiver, receive_x, receive_y = receiver_info
        start_x = _safe_float(row.get("x"))
        start_y = _safe_float(row.get("y"))
        if start_x is None or start_y is None or receive_x is None or receive_y is None:
            continue

        records.append(
            {
                "passer": passer,
                "receiver": receiver,
                "start_x": float(start_x),
                "start_y": float(start_y),
                "receive_x": float(receive_x),
                "receive_y": float(receive_y),
                "minute": _safe_float(row.get("expanded_minute"), 0.0) or 0.0,
                "positive_xt": max(0.0, _safe_float(row.get("positive_xt"), _safe_float(row.get("xt_added"), 0.0)) or 0.0),
                "progressive": bool(_safe_float(row.get("end_x"), start_x) is not None and float(receive_x) - float(start_x) >= 10.0),
                "final_third_entry": bool(row.get("final_third_entry", False)),
                "box_entry": bool(row.get("box_entry", False)),
                "is_cross": bool(row.get("is_cross", False)),
            }
        )

    if not records:
        return _empty_pass_network(team_name)

    frame = pd.DataFrame(records)
    grouped = (
        frame.groupby(["passer", "receiver"], dropna=False)
        .agg(
            count=("passer", "size"),
            avg_start_x=("start_x", "mean"),
            avg_start_y=("start_y", "mean"),
            avg_receive_x=("receive_x", "mean"),
            avg_receive_y=("receive_y", "mean"),
            total_xt=("positive_xt", "sum"),
            progressive_passes=("progressive", "sum"),
            final_third_entries=("final_third_entry", "sum"),
            box_entries=("box_entry", "sum"),
            crosses=("is_cross", "sum"),
        )
        .reset_index()
    )

    player_totals: dict[str, dict[str, Any]] = {}

    def _player_record(player: str) -> dict[str, Any]:
        if player not in player_totals:
            player_totals[player] = {
                "player": player,
                "passes_made": 0,
                "passes_received": 0,
                "made_x_sum": 0.0,
                "made_y_sum": 0.0,
                "received_x_sum": 0.0,
                "received_y_sum": 0.0,
                "xt_involved": 0.0,
            }
        return player_totals[player]

    connections: list[dict[str, Any]] = []
    for _, row in grouped.iterrows():
        passer = str(row["passer"])
        receiver = str(row["receiver"])
        count = int(row["count"])
        avg_start_x = float(row["avg_start_x"])
        avg_start_y = float(row["avg_start_y"])
        avg_receive_x = float(row["avg_receive_x"])
        avg_receive_y = float(row["avg_receive_y"])
        total_xt = float(row["total_xt"])

        passer_item = _player_record(passer)
        passer_item["passes_made"] += count
        passer_item["made_x_sum"] += avg_start_x * count
        passer_item["made_y_sum"] += avg_start_y * count
        passer_item["xt_involved"] += total_xt

        receiver_item = _player_record(receiver)
        receiver_item["passes_received"] += count
        receiver_item["received_x_sum"] += avg_receive_x * count
        receiver_item["received_y_sum"] += avg_receive_y * count
        receiver_item["xt_involved"] += total_xt

        connections.append(
            {
                "connection_id": f"{_safe_slug(passer)}__to__{_safe_slug(receiver)}",
                "label": f"{passer} to {receiver}",
                "passer": passer,
                "receiver": receiver,
                "count": count,
                "avg_start_x": round(avg_start_x, 2),
                "avg_start_y": round(avg_start_y, 2),
                "avg_receive_x": round(avg_receive_x, 2),
                "avg_receive_y": round(avg_receive_y, 2),
                "total_xt": round(total_xt, 4),
                "progressive_passes": int(row["progressive_passes"]),
                "final_third_entries": int(row["final_third_entries"]),
                "box_entries": int(row["box_entries"]),
                "crosses": int(row["crosses"]),
                "forward_distance": round(avg_receive_x - avg_start_x, 2),
            }
        )

    players: list[dict[str, Any]] = []
    for item in player_totals.values():
        made = int(item["passes_made"])
        received = int(item["passes_received"])
        involved = made + received
        if involved <= 0:
            continue

        made_x = item["made_x_sum"] / made if made else None
        made_y = item["made_y_sum"] / made if made else None
        received_x = item["received_x_sum"] / received if received else None
        received_y = item["received_y_sum"] / received if received else None

        combined_x_sum = item["made_x_sum"] + item["received_x_sum"]
        combined_y_sum = item["made_y_sum"] + item["received_y_sum"]
        status = player_status.get(str(item["player"]), {})
        is_substitute = bool(status.get("is_substitute", False))
        players.append(
            {
                "player": str(item["player"]),
                "shirt_no": _clean_shirt_no(status.get("shirt_no")),
                "is_substitute": is_substitute,
                "is_subbed_in": is_substitute,
                "player_status": "Subbed in" if is_substitute else "Starter",
                "substitution_minute": status.get("substitution_minute"),
                "passes_made": made,
                "passes_received": received,
                "passes_involved": involved,
                "avg_x": round(combined_x_sum / involved, 2),
                "avg_y": round(combined_y_sum / involved, 2),
                "avg_made_x": round(float(made_x), 2) if made_x is not None else None,
                "avg_made_y": round(float(made_y), 2) if made_y is not None else None,
                "avg_received_x": round(float(received_x), 2) if received_x is not None else None,
                "avg_received_y": round(float(received_y), 2) if received_y is not None else None,
                "xt_involved": round(float(item["xt_involved"]), 4),
            }
        )

    connections.sort(key=lambda item: (-int(item["count"]), -float(item["total_xt"]), str(item["passer"]), str(item["receiver"])))
    players.sort(key=lambda item: (-int(item["passes_involved"]), str(item["player"])))

    return {
        "team": team_name,
        "total_passes": int(len(frame)),
        "players": players[:22],
        "connections": connections[:90],
    }

def _rolling_xt_total(frame: pd.DataFrame, minute_now: float) -> float:
    if frame.empty or "positive_xt" not in frame.columns:
        return 0.0
    delta = minute_now - pd.to_numeric(frame["expanded_minute"], errors="coerce").fillna(0.0)
    recency = delta.clip(lower=0.0).map(lambda value: math.exp(float(-value) / 4.0))
    values = pd.to_numeric(frame["positive_xt"], errors="coerce").fillna(0.0).clip(lower=0.0, upper=0.12)
    return round(float((values * recency).sum() * 34.0), 3)


def _rolling_window_score(frame: pd.DataFrame, minute_now: float, include_xt: bool = False) -> float:
    if frame.empty:
        return 0.0

    delta = minute_now - frame["expanded_minute"].fillna(0.0)
    recency = delta.clip(lower=0.0).map(lambda value: math.exp(float(-value) / 4.0))

    shot_xg = frame.apply(lambda row: _shot_xg_value(row) or 0.0, axis=1) if "is_shot" in frame.columns else pd.Series(0.0, index=frame.index)
    dangerous_touch = frame["attacking_third_touch"].astype(float) if "attacking_third_touch" in frame.columns else pd.Series(0.0, index=frame.index)

    weights = (
        frame["is_goal"].astype(float) * 8.0
        + frame["is_shot"].astype(float) * 4.0
        + shot_xg.astype(float) * 6.0
        + frame["box_entry"].astype(float) * 3.0
        + frame["final_third_entry"].astype(float) * 1.35
        + frame["is_cross"].astype(float) * 0.65
        + frame["high_regain"].astype(float) * 0.9
        + dangerous_touch * 0.12
    )
    pressure = float((weights * recency).sum())
    if include_xt:
        pressure += _rolling_xt_total(frame, minute_now)
    return round(pressure, 3)


def _rolling_momentum(events: pd.DataFrame, home_team: str, away_team: str) -> list[dict[str, Any]]:
    if events.empty:
        return []
    home_events = _team_events(events, home_team)
    away_events = _team_events(events, away_team)

    max_minute = int(max(90, math.ceil(float(events["expanded_minute"].dropna().max()) if events["expanded_minute"].notna().any() else 90)))
    max_minute = min(max(max_minute, 90), 130)

    points: list[dict[str, Any]] = []
    for minute in range(1, max_minute + 1):
        home_window = home_events[home_events["expanded_minute"].between(max(0, minute - 10), minute, inclusive="both")]
        away_window = away_events[away_events["expanded_minute"].between(max(0, minute - 10), minute, inclusive="both")]
        home_pressure = _rolling_window_score(home_window, float(minute), include_xt=False)
        away_pressure = _rolling_window_score(away_window, float(minute), include_xt=False)
        home_xt = _rolling_xt_total(home_window, float(minute))
        away_xt = _rolling_xt_total(away_window, float(minute))
        home_combined = round(home_pressure + home_xt, 3)
        away_combined = round(away_pressure + away_xt, 3)
        points.append(
            {
                "minute": minute,
                "home": home_combined,
                "away": away_combined,
                "net": round(home_combined - away_combined, 3),
                "home_pressure": home_pressure,
                "away_pressure": away_pressure,
                "home_xt": home_xt,
                "away_xt": away_xt,
                "home_combined": home_combined,
                "away_combined": away_combined,
                "net_pressure": round(home_pressure - away_pressure, 3),
                "net_xt": round(home_xt - away_xt, 3),
                "net_combined": round(home_combined - away_combined, 3),
            }
        )
    return points


def _rolling_possession_timeline(events: pd.DataFrame, home_team: str, away_team: str) -> list[dict[str, Any]]:
    if events.empty:
        return []

    ordered = _sort_events_by_match_time(events.copy(), ["expanded_minute", "event_index"]).copy()
    home_norm = _norm_team_name(home_team)
    away_norm = _norm_team_name(away_team)

    def _side_from_team(value: object) -> str:
        team_norm = _norm_team_name(value)
        if team_norm and (team_norm == home_norm or team_norm.startswith(home_norm) or home_norm.startswith(team_norm)):
            return "home"
        if team_norm and (team_norm == away_norm or team_norm.startswith(away_norm) or away_norm.startswith(team_norm)):
            return "away"
        return ""

    derived_sides = ordered["team"].map(_side_from_team) if "team" in ordered.columns else pd.Series([""] * len(ordered), index=ordered.index)
    if "team_side" in ordered.columns:
        existing_sides = ordered["team_side"].astype(str).str.strip().str.lower()
        ordered["team_side"] = [
            side if side in {"home", "away"} else derived
            for side, derived in zip(existing_sides, derived_sides)
        ]
    else:
        ordered["team_side"] = derived_sides

    if not ordered["team_side"].astype(str).isin(["home", "away"]).any():
        return []

    ball_mask = (
        _bool_series(ordered, "is_touch")
        | _bool_series(ordered, "is_pass")
        | _bool_series(ordered, "is_cross")
        | _bool_series(ordered, "is_carry")
        | _bool_series(ordered, "is_take_on")
        | _bool_series(ordered, "is_shot")
    )
    ball_events = ordered.loc[ball_mask & ordered["team_side"].astype(str).isin(["home", "away"]) & ordered["expanded_minute"].notna()].copy()
    if ball_events.empty:
        ball_events = ordered.loc[ordered["team_side"].astype(str).isin(["home", "away"]) & ordered["expanded_minute"].notna()].copy()
    if ball_events.empty:
        return []

    max_minute = int(max(90, math.ceil(float(ball_events["expanded_minute"].dropna().max()))))
    max_minute = min(max(max_minute, 90), 130)
    window_size = 3
    points: list[dict[str, Any]] = []

    for minute in range(1, max_minute + 1):
        window = ball_events.loc[ball_events["expanded_minute"].between(max(0, minute - window_size), minute, inclusive="both")].copy()
        if window.empty:
            last_event = ball_events.loc[ball_events["expanded_minute"].le(float(minute))].tail(1)
            dominant_side = str(last_event.iloc[0].get("team_side", "")) if not last_event.empty else "none"
            home_count = 1 if dominant_side == "home" else 0
            away_count = 1 if dominant_side == "away" else 0
            confidence = "last known event" if dominant_side in {"home", "away"} else "no event signal"
        else:
            home_count = int(window["team_side"].astype(str).eq("home").sum())
            away_count = int(window["team_side"].astype(str).eq("away").sum())
            if abs(home_count - away_count) <= 1:
                dominant_side = "even"
            else:
                dominant_side = "home" if home_count > away_count else "away"
            confidence = "three minute event window"

        total = max(home_count + away_count, 1)
        home_share = round((home_count / total) * 100.0, 2)
        away_share = round((away_count / total) * 100.0, 2)
        dominant_team = home_team if dominant_side == "home" else away_team if dominant_side == "away" else "Even"
        points.append(
            {
                "minute": minute,
                "home_events": home_count,
                "away_events": away_count,
                "home_share_pct": home_share,
                "away_share_pct": away_share,
                "dominant_side": dominant_side,
                "dominant_team": dominant_team,
                "signal": confidence,
            }
        )

    return points


def _match_markers(events: pd.DataFrame) -> list[dict[str, Any]]:
    if events.empty:
        return []
    marker_rows = events.loc[events["is_goal"] | events["is_red_card"]].copy()
    markers: list[dict[str, Any]] = []
    score_home = 0
    score_away = 0

    for _, row in _sort_events_by_match_time(events, ["expanded_minute", "event_index"]).iterrows():
        if bool(row.get("is_goal", False)):
            if str(row.get("team_side", "")) == "home":
                score_home += 1
            elif str(row.get("team_side", "")) == "away":
                score_away += 1

        if row.name not in marker_rows.index:
            continue

        marker_type = "goal" if bool(row.get("is_goal", False)) else "red_card"
        markers.append(
            {
                "event_index": int(row.get("event_index", 0)) if pd.notna(row.get("event_index")) else None,
                "minute": round(float(row["expanded_minute"]), 2) if pd.notna(row.get("expanded_minute")) else None,
                "period": _period_to_int(row.get("period")),
                "team": str(row.get("team", "")),
                "team_side": str(row.get("team_side", "")),
                "player": str(row.get("player", "")),
                "event_type": str(row.get("type", "")),
                "marker_type": marker_type,
                "card_type": str(row.get("card_type", "")),
                "score_after_event": f"{score_home}-{score_away}" if marker_type == "goal" else "",
            }
        )
    return markers



def _pct(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100.0, 1)


def _rate_per_100(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100.0, 1)


def _style_text_metric(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return "0"
    try:
        number = float(value)
    except Exception:
        return "0"
    if math.isnan(number) or math.isinf(number):
        return "0"
    if abs(number - round(number)) < 0.05:
        return str(int(round(number)))
    return f"{number:.{digits}f}"


def _style_event_second(row: pd.Series) -> float:
    return _event_seconds(row)


def _team_action_chains(events: pd.DataFrame) -> list[list[pd.Series]]:
    if events.empty:
        return []

    action_mask = (
        _bool_series(events, "is_pass")
        | _bool_series(events, "is_cross")
        | _bool_series(events, "is_carry")
        | _bool_series(events, "is_take_on")
        | _bool_series(events, "is_shot")
        | _bool_series(events, "box_entry")
        | _bool_series(events, "final_third_entry")
    )
    action_events = events.loc[action_mask].dropna(subset=["x"]).copy()
    if action_events.empty:
        return []

    action_events = _sort_events_by_match_time(action_events, ["expanded_minute", "event_index"])
    chains: list[list[pd.Series]] = []
    current: list[pd.Series] = []
    previous: pd.Series | None = None

    for _, row in action_events.iterrows():
        should_break = False
        if previous is not None:
            gap = _style_event_second(row) - _style_event_second(previous)
            should_break = (
                gap < 0.0
                or gap > 10.0
                or (_period_to_int(row.get("period")) != _period_to_int(previous.get("period")))
            )
        if should_break and current:
            chains.append(current)
            current = []
        current.append(row)
        previous = row

    if current:
        chains.append(current)

    return chains


def _chain_start_end_x(chain: list[pd.Series]) -> tuple[float | None, float | None]:
    if not chain:
        return None, None

    first = chain[0]
    last = chain[-1]
    start_x = _safe_float(first.get("x"))
    end_x = _safe_float(last.get("end_x"))
    if end_x is None:
        end_x = _safe_float(last.get("x"))
    return start_x, end_x


def _style_ip_profile(events: pd.DataFrame, summary: dict[str, Any]) -> dict[str, Any]:
    if events.empty:
        return {
            "label": "No sample",
            "evidence": "No event rows were available for this team.",
            "metrics": {},
        }

    pass_mask = _bool_series(events, "is_pass") | _bool_series(events, "is_cross")
    move_mask = pass_mask | _bool_series(events, "is_carry") | _bool_series(events, "is_take_on")
    successful_move_mask = move_mask & _bool_series(events, "is_success")

    passes = int(pass_mask.sum())
    successful_passes = int((pass_mask & _bool_series(events, "is_success")).sum())
    moves = int(move_mask.sum())
    shots = int(_bool_series(events, "is_shot").sum())
    final_third_entries = int(_bool_series(events, "final_third_entry").sum())
    box_entries = int(_bool_series(events, "box_entry").sum())
    attacking_third_touches = int(_bool_series(events, "attacking_third_touch").sum())

    forward_passes = int(
        (
            pass_mask
            & _bool_series(events, "is_success")
            & events["x"].notna()
            & events["end_x"].notna()
            & (pd.to_numeric(events["end_x"], errors="coerce") - pd.to_numeric(events["x"], errors="coerce")).ge(8.0)
        ).sum()
    )

    qual_context = (
        _text_series(events, "qualifier_tags")
        + " "
        + _text_series(events, "qual_tags")
        + " "
        + _text_series(events, "type")
    ).str.lower()
    long_balls = int((pass_mask & qual_context.str.contains("longball|long ball", regex=True, na=False)).sum())

    chains = _team_action_chains(events)
    chain_count = len(chains)
    chain_action_counts: list[int] = []
    chain_durations: list[float] = []
    long_build_chains = 0
    direct_chains = 0
    shot_chains = 0
    box_chains = 0

    for chain in chains:
        chain_action_counts.append(len(chain))
        start_second = _style_event_second(chain[0])
        end_second = _style_event_second(chain[-1])
        duration = max(0.0, end_second - start_second)
        chain_durations.append(duration)
        start_x, end_x = _chain_start_end_x(chain)
        x_gain = 0.0 if start_x is None or end_x is None else float(end_x) - float(start_x)
        has_shot = any(bool(row.get("is_shot", False)) for row in chain)
        has_box_entry = any(bool(row.get("box_entry", False)) for row in chain)

        if len(chain) >= 7 or duration >= 25.0:
            long_build_chains += 1
        if duration <= 15.0 and x_gain >= 25.0:
            direct_chains += 1
        if has_shot:
            shot_chains += 1
        if has_box_entry:
            box_chains += 1

    average_chain_actions = round(float(sum(chain_action_counts) / chain_count), 2) if chain_count else 0.0
    average_chain_duration = round(float(sum(chain_durations) / chain_count), 2) if chain_count else 0.0
    long_build_share = _pct(long_build_chains, chain_count)
    direct_attack_share = _pct(direct_chains, chain_count)
    shot_chain_share = _pct(shot_chains, chain_count)
    box_chain_share = _pct(box_chains, chain_count)
    forward_pass_share = _pct(forward_passes, successful_passes)
    long_ball_share = _pct(long_balls, passes)
    transition_threat_events = _summary_number(summary, "transition_threat_events")
    transition_rate = _rate_per_100(transition_threat_events, max(moves, 1))

    if chain_count < 5 and moves < 25:
        label = "Low control attack"
    elif transition_rate >= 8.0 and direct_attack_share >= 12.0:
        label = "Transition side"
    elif average_chain_actions >= 5.5 and long_build_share >= 18.0:
        label = "Possession side"
    elif direct_attack_share >= 20.0 or long_ball_share >= 18.0 or (forward_pass_share >= 45.0 and average_chain_actions < 4.8):
        label = "Direct side"
    elif average_chain_actions >= 4.5 or long_build_share >= 12.0:
        label = "Mixed possession/direct"
    elif transition_rate >= 7.0:
        label = "Transition side"
    else:
        label = "Direct side" if forward_pass_share >= 40.0 else "Low control attack"

    evidence = (
        f"{_style_text_metric(average_chain_actions)} actions per chain, "
        f"{_style_text_metric(long_build_share)}% long build ups, "
        f"{_style_text_metric(direct_attack_share)}% direct attacks, "
        f"{_style_text_metric(forward_pass_share)}% forward successful passes."
    )

    return {
        "label": label,
        "evidence": evidence,
        "metrics": {
            "passes": passes,
            "successful_passes": successful_passes,
            "pass_completion_pct": round(_pct(successful_passes, passes), 1),
            "chains": chain_count,
            "average_chain_actions": average_chain_actions,
            "average_chain_duration": average_chain_duration,
            "long_build_up_share_pct": long_build_share,
            "direct_attack_share_pct": direct_attack_share,
            "shot_chain_share_pct": shot_chain_share,
            "box_chain_share_pct": box_chain_share,
            "forward_pass_share_pct": forward_pass_share,
            "long_ball_share_pct": long_ball_share,
            "final_third_entries": final_third_entries,
            "box_entries": box_entries,
            "shots": shots,
            "attacking_third_touches": attacking_third_touches,
            "transition_events_per_100_moves": transition_rate,
        },
    }


def _style_oop_profile(team_events: pd.DataFrame, opponent_events: pd.DataFrame, summary: dict[str, Any]) -> dict[str, Any]:
    if team_events.empty:
        return {
            "label": "No sample",
            "evidence": "No event rows were available for this team.",
            "metrics": {},
        }

    defensive_mask = _bool_series(team_events, "is_defensive_action")
    defensive_actions = team_events.loc[defensive_mask].copy()
    defensive_count = int(len(defensive_actions))
    high_defensive_count = int(defensive_actions["x"].ge(60.0).sum()) if not defensive_actions.empty else 0
    middle_defensive_count = int(defensive_actions["x"].between(40.0, 60.0, inclusive="left").sum()) if not defensive_actions.empty else 0
    low_defensive_count = int(defensive_actions["x"].lt(40.0).sum()) if not defensive_actions.empty else 0
    average_defensive_x = float(defensive_actions["x"].dropna().mean()) if not defensive_actions.empty and defensive_actions["x"].notna().any() else 0.0
    high_regains = int(_bool_series(team_events, "high_regain").sum())
    high_defensive_share = _pct(high_defensive_count, defensive_count)
    middle_defensive_share = _pct(middle_defensive_count, defensive_count)
    low_defensive_share = _pct(low_defensive_count, defensive_count)
    high_regain_share = _pct(high_regains, defensive_count)

    opponent_pass_mask = _bool_series(opponent_events, "is_pass") | _bool_series(opponent_events, "is_cross")
    opponent_build_passes = int(
        (
            opponent_pass_mask
            & _bool_series(opponent_events, "is_success")
            & pd.to_numeric(opponent_events.get("x", pd.Series(0.0, index=opponent_events.index)), errors="coerce").lt(60.0)
        ).sum()
    ) if not opponent_events.empty else 0
    opponent_box_entries = int(_bool_series(opponent_events, "box_entry").sum()) if not opponent_events.empty else 0
    opponent_final_third_entries = int(_bool_series(opponent_events, "final_third_entry").sum()) if not opponent_events.empty else 0
    press_actions = int(defensive_actions["x"].ge(50.0).sum()) if not defensive_actions.empty else 0
    ppda_proxy = round(float(opponent_build_passes / max(press_actions, 1)), 2) if opponent_build_passes > 0 else 0.0

    if defensive_count < 8:
        label = "Low event sample"
    elif high_regain_share >= 18.0 and high_defensive_share >= 28.0:
        label = "Counter press side"
    elif high_defensive_share >= 36.0 or average_defensive_x >= 58.0 or ppda_proxy <= 5.0:
        label = "High press side"
    elif low_defensive_share >= 46.0 or average_defensive_x <= 42.0:
        label = "Low block side"
    elif high_defensive_share >= 22.0 and average_defensive_x >= 50.0:
        label = "Mid/high press side"
    else:
        label = "Mid block side"

    evidence = (
        f"Defensive action height {_style_text_metric(average_defensive_x)}, "
        f"{_style_text_metric(high_defensive_share)}% high defensive actions, "
        f"{_style_text_metric(low_defensive_share)}% low defensive actions, "
        f"{_style_text_metric(high_regain_share)}% high regain rate."
    )

    return {
        "label": label,
        "evidence": evidence,
        "metrics": {
            "defensive_actions": defensive_count,
            "average_defensive_x": round(average_defensive_x, 2),
            "high_defensive_action_share_pct": high_defensive_share,
            "middle_defensive_action_share_pct": middle_defensive_share,
            "low_defensive_action_share_pct": low_defensive_share,
            "high_regains": high_regains,
            "high_regain_share_pct": high_regain_share,
            "opponent_build_passes_allowed": opponent_build_passes,
            "opponent_final_third_entries_allowed": opponent_final_third_entries,
            "opponent_box_entries_allowed": opponent_box_entries,
            "ppda_proxy": ppda_proxy,
        },
    }


def _team_style_payload(
    team_name: str,
    team_events: pd.DataFrame,
    opponent_events: pd.DataFrame,
    scope: str,
    source: str,
    match_count: int | None = None,
) -> dict[str, Any]:
    summary = _team_summary(team_events) if not team_events.empty else _empty_team_summary()
    payload = {
        "team": team_name,
        "scope": scope,
        "source": source,
        "match_count": match_count,
        "in_possession": _style_ip_profile(team_events, summary),
        "out_of_possession": _style_oop_profile(team_events, opponent_events, summary),
    }
    return payload


def _style_shift_notes(match_payload: dict[str, Any], season_payload: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if not match_payload or not season_payload:
        return notes

    match_ip = str(match_payload.get("in_possession", {}).get("label", "")).strip()
    season_ip = str(season_payload.get("in_possession", {}).get("label", "")).strip()
    match_oop = str(match_payload.get("out_of_possession", {}).get("label", "")).strip()
    season_oop = str(season_payload.get("out_of_possession", {}).get("label", "")).strip()

    if match_ip and season_ip and match_ip != season_ip and season_ip not in {"No sample", "Low event sample"}:
        notes.append(f"In possession shift detected: match profile was {match_ip}, season profile is {season_ip}.")
    if match_oop and season_oop and match_oop != season_oop and season_oop not in {"No sample", "Low event sample"}:
        notes.append(f"Out of possession shift detected: match profile was {match_oop}, season profile is {season_oop}.")

    return notes


def _build_style_tags(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    home_team: str,
    away_team: str,
    match_events: pd.DataFrame,
) -> dict[str, Any]:
    empty = {
        "home": {
            "team": home_team,
            "match": _team_style_payload(home_team, pd.DataFrame(), pd.DataFrame(), "selected_match", "selected_match"),
            "season": _team_style_payload(home_team, pd.DataFrame(), pd.DataFrame(), "season_to_date", "unavailable"),
            "shift_notes": [],
        },
        "away": {
            "team": away_team,
            "match": _team_style_payload(away_team, pd.DataFrame(), pd.DataFrame(), "selected_match", "selected_match"),
            "season": _team_style_payload(away_team, pd.DataFrame(), pd.DataFrame(), "season_to_date", "unavailable"),
            "shift_notes": [],
        },
        "confidence_notes": ["Style tags are event based and describe the available event sample, not a full tactical guarantee."],
    }

    if match_events.empty:
        return empty

    match_frame = _ensure_flags(match_events)
    home_match_events = _team_events(match_frame, home_team)
    away_match_events = _team_events(match_frame, away_team)

    home_match = _team_style_payload(home_team, home_match_events, away_match_events, "selected_match", "selected_match")
    away_match = _team_style_payload(away_team, away_match_events, home_match_events, "selected_match", "selected_match")

    season_source = "selected_match_fallback"
    season_frame: pd.DataFrame | None = None
    season_notes: list[str] = ["Match tags use the currently filtered match events. Season tags use all saved season events when available."]

    try:
        season_frame, season_source = _load_prepared_season_events_for_analysis(basedir, nation, tier, season)
    except Exception as exc:
        season_notes.append(f"Season style build fell back to the selected match because prepared season events failed to load: {type(exc).__name__}: {exc}")
        season_frame = None
        season_source = "selected_match_fallback"

    if season_frame is None or season_frame.empty:
        home_season = _team_style_payload(home_team, home_match_events, away_match_events, "selected_match_fallback", "selected_match")
        away_season = _team_style_payload(away_team, away_match_events, home_match_events, "selected_match_fallback", "selected_match")
        season_notes.append("No full season event frame was available, so season tags fall back to this match.")
    else:
        home_season_events = _team_events(season_frame, home_team)
        away_season_events = _team_events(season_frame, away_team)

        if home_season_events.empty:
            home_season = _team_style_payload(home_team, home_match_events, away_match_events, "selected_match_fallback", "selected_match")
            season_notes.append(f"No saved season rows were found for {home_team}, so its season tag falls back to this match.")
        else:
            home_season = _team_style_payload(
                home_team,
                home_season_events,
                _opponent_events_for_team(season_frame, home_team),
                "season_to_date",
                season_source,
                match_count=_season_match_count(home_season_events),
            )

        if away_season_events.empty:
            away_season = _team_style_payload(away_team, away_match_events, home_match_events, "selected_match_fallback", "selected_match")
            season_notes.append(f"No saved season rows were found for {away_team}, so its season tag falls back to this match.")
        else:
            away_season = _team_style_payload(
                away_team,
                away_season_events,
                _opponent_events_for_team(season_frame, away_team),
                "season_to_date",
                season_source,
                match_count=_season_match_count(away_season_events),
            )

    return {
        "home": {
            "team": home_team,
            "match": home_match,
            "season": home_season,
            "shift_notes": _style_shift_notes(home_match, home_season),
        },
        "away": {
            "team": away_team,
            "match": away_match,
            "season": away_season,
            "shift_notes": _style_shift_notes(away_match, away_season),
        },
        "confidence_notes": season_notes + ["OOP tags use defensive event height, high regains and opponent build up allowed. IP tags use chains, forward pass share, direct attacks and transition rate."],
    }


def _phase_breakdown(team_name: str, events: pd.DataFrame, summary: dict[str, Any]) -> list[dict[str, Any]]:
    if events.empty:
        return []

    build_up = events.loc[events["is_pass"] & events["is_success"] & events["x"].lt(FINAL_THIRD_X)].copy()
    shot_avg_x = float(events.loc[events["is_shot"], "x"].dropna().mean()) if events.loc[events["is_shot"], "x"].notna().any() else 0.0
    defensive_avg_x = float(events.loc[events["is_defensive_action"], "x"].dropna().mean()) if events.loc[events["is_defensive_action"], "x"].notna().any() else 0.0

    return [
        {
            "title": "Attacking",
            "summary": f"{team_name} created {summary['shots']} shots from {summary['final_third_entries']} final third entries and {summary['penalty_area_entries']} box entries.",
            "metrics": {"shots": int(summary["shots"]), "final_third_entries": int(summary["final_third_entries"]), "box_entries": int(summary["penalty_area_entries"]), "average_shot_x": round(shot_avg_x, 2)},
        },
        {
            "title": "Defensive",
            "summary": f"{team_name} recorded {summary['defensive_actions']} defensive actions, including {summary['high_regains']} high regains.",
            "metrics": {"defensive_actions": int(summary["defensive_actions"]), "high_regains": int(summary["high_regains"]), "average_defensive_x": round(defensive_avg_x, 2)},
        },
        {
            "title": "Set pieces",
            "summary": f"{team_name} had {int(events['is_set_piece'].sum())} set piece tagged actions and {int((events['is_set_piece'] & events['is_shot']).sum())} set piece shots.",
            "metrics": {"set_piece_actions": int(events["is_set_piece"].sum()), "set_piece_shots": int((events["is_set_piece"] & events["is_shot"]).sum()), "corners": int(events["is_corner"].sum()), "free_kicks": int(events["is_free_kick"].sum())},
        },
        {
            "title": "Transitions",
            "summary": f"{team_name} generated a transition proxy score of {summary['transition_threat_proxy']} from high regains, entries, crosses and shots.",
            "metrics": {"transition_events": int(summary["transition_threat_events"]), "transition_proxy": float(summary["transition_threat_proxy"]), "build_up_passes": int(len(build_up))},
        },
    ]


def _lane_from_y(value: object) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "unknown"
    y = float(max(0.0, min(float(numeric), 100.0)))
    if y < 33.333:
        return "left"
    if y < 66.667:
        return "central"
    return "right"


def _lane_bounds(lane: str) -> tuple[float, float]:
    if lane == "left":
        return 0.0, 33.333
    if lane == "central":
        return 33.333, 66.667
    if lane == "right":
        return 66.667, 100.0
    return 0.0, 100.0


def _direction_arrows(events: pd.DataFrame) -> list[dict[str, Any]]:
    if events.empty:
        return []

    frame = _coerce_analysis_numeric_columns(events)
    progressive = (frame["is_pass"] | frame["is_cross"] | frame["is_carry"]) & frame["is_success"]
    attacking_value = (
        progressive
        & (
            frame["x"].ge(45.0)
            | frame["end_x"].ge(45.0)
            | frame["final_third_entry"]
            | frame["box_entry"]
            | frame["attacking_third_touch"]
        )
    ) | frame["is_shot"]

    frame = frame.loc[attacking_value].copy()
    if frame.empty:
        return []

    frame["lane_x"] = frame["end_x"].where(progressive.loc[frame.index] & frame["end_x"].notna(), frame["x"])
    frame["lane_y"] = frame["end_y"].where(progressive.loc[frame.index] & frame["end_y"].notna(), frame["y"])
    frame = frame.dropna(subset=["lane_y"])
    if frame.empty:
        return []

    frame["lane"] = frame["lane_y"].map(_lane_from_y)
    frame = frame.loc[frame["lane"].ne("unknown")].copy()
    if frame.empty:
        return []

    frame["lane_weight"] = (
        1.0
        + frame["final_third_entry"].astype(float) * 1.2
        + frame["box_entry"].astype(float) * 2.2
        + frame["is_shot"].astype(float) * 2.8
        + frame["is_goal"].astype(float) * 4.0
    )

    grouped = (
        frame.groupby("lane", dropna=True)
        .agg(
            count=("lane", "size"),
            weighted_count=("lane_weight", "sum"),
            final_third_entries=("final_third_entry", "sum"),
            box_entries=("box_entry", "sum"),
            shots=("is_shot", "sum"),
            goals=("is_goal", "sum"),
            average_x=("lane_x", "mean"),
            average_y=("lane_y", "mean"),
        )
        .reset_index()
    )

    total_weight = float(grouped["weighted_count"].sum()) if not grouped.empty else 0.0
    total_actions = int(grouped["count"].sum()) if not grouped.empty else 0
    lane_order = {"left": 0, "central": 1, "right": 2}

    rows: list[dict[str, Any]] = []
    for _, row in grouped.iterrows():
        lane = str(row["lane"])
        y_min, y_max = _lane_bounds(lane)
        weighted_count = float(row["weighted_count"])
        share_pct = (weighted_count / total_weight) * 100.0 if total_weight > 0 else 0.0
        action_share_pct = (float(row["count"]) / total_actions) * 100.0 if total_actions > 0 else 0.0
        rows.append(
            {
                "lane": lane,
                "label": "Centre" if lane == "central" else lane.title(),
                "y_min": round(y_min, 3),
                "y_max": round(y_max, 3),
                "y_mid": round((y_min + y_max) / 2.0, 3),
                "count": int(row["count"]),
                "weighted_count": round(weighted_count, 2),
                "share_pct": round(share_pct, 1),
                "action_share_pct": round(action_share_pct, 1),
                "final_third_entries": int(row["final_third_entries"]),
                "box_entries": int(row["box_entries"]),
                "shots": int(row["shots"]),
                "goals": int(row["goals"]),
                "average_x": round(float(row["average_x"]), 2) if pd.notna(row["average_x"]) else None,
                "average_y": round(float(row["average_y"]), 2) if pd.notna(row["average_y"]) else None,
                "rank": 0,
            }
        )

    rows.sort(key=lambda item: (-float(item["weighted_count"]), lane_order.get(str(item["lane"]), 99)))
    for index, item in enumerate(rows, start=1):
        item["rank"] = index

    return sorted(rows, key=lambda item: lane_order.get(str(item["lane"]), 99))


def _attacking_threat_lanes(events: pd.DataFrame) -> list[dict[str, Any]]:
    lane_order = {"left": 0, "central": 1, "right": 2}
    empty_rows = []
    for lane in ["left", "central", "right"]:
        y_min, y_max = _lane_bounds(lane)
        empty_rows.append(
            {
                "lane": lane,
                "label": "Centre" if lane == "central" else lane.title(),
                "y_min": round(y_min, 3),
                "y_max": round(y_max, 3),
                "y_mid": round((y_min + y_max) / 2.0, 3),
                "count": 0,
                "share_pct": 0.0,
                "action_share_pct": 0.0,
                "threat_score": 0.0,
                "xg": 0.0,
                "xt_created": 0.0,
                "final_third_entries": 0,
                "box_entries": 0,
                "shots": 0,
                "goals": 0,
                "rank": lane_order[lane] + 1,
            }
        )

    if events.empty:
        return empty_rows

    frame = _coerce_analysis_numeric_columns(events)
    progressive = (frame["is_pass"] | frame["is_cross"] | frame["is_carry"]) & frame["is_success"]
    threat_mask = (
        progressive
        & frame["end_x"].notna()
        & frame["end_y"].notna()
        & (
            frame["final_third_entry"]
            | frame["box_entry"]
            | frame["end_x"].ge(FINAL_THIRD_X)
            | frame["x"].ge(FINAL_THIRD_X)
            | pd.to_numeric(frame.get("positive_xt", pd.Series(0.0, index=frame.index)), errors="coerce").fillna(0.0).gt(0.0)
        )
    ) | frame["is_shot"]

    frame = frame.loc[threat_mask].copy()
    if frame.empty:
        return empty_rows

    frame["lane_x"] = frame["end_x"].where(progressive.loc[frame.index] & frame["end_x"].notna(), frame["x"])
    frame["lane_y"] = frame["end_y"].where(progressive.loc[frame.index] & frame["end_y"].notna(), frame["y"])
    frame = frame.dropna(subset=["lane_y"]).copy()
    if frame.empty:
        return empty_rows

    frame["lane"] = frame["lane_y"].map(_lane_from_y)
    frame = frame.loc[frame["lane"].ne("unknown")].copy()
    if frame.empty:
        return empty_rows

    if "positive_xt" in frame.columns:
        xt_source = frame["positive_xt"]
    elif "xt_added" in frame.columns:
        xt_source = frame["xt_added"]
    else:
        xt_source = pd.Series(0.0, index=frame.index)

    frame["xt_value"] = pd.to_numeric(xt_source, errors="coerce").fillna(0.0).clip(lower=0.0)
    frame["xg_value"] = frame.apply(lambda row: _fallback_shot_xg(row) if bool(row.get("is_shot", False)) else 0.0, axis=1)
    frame["threat_score"] = frame["xg_value"].astype(float) + frame["xt_value"].astype(float)
    frame = frame.loc[frame["threat_score"].gt(0.0) | frame["is_shot"] | frame["box_entry"] | frame["final_third_entry"]].copy()
    if frame.empty:
        return empty_rows

    grouped = (
        frame.groupby("lane", dropna=True)
        .agg(
            count=("lane", "size"),
            threat_score=("threat_score", "sum"),
            xg=("xg_value", "sum"),
            xt_created=("xt_value", "sum"),
            final_third_entries=("final_third_entry", "sum"),
            box_entries=("box_entry", "sum"),
            shots=("is_shot", "sum"),
            goals=("is_goal", "sum"),
        )
        .reset_index()
    )

    total_threat = float(grouped["threat_score"].sum()) if not grouped.empty else 0.0
    total_actions = int(grouped["count"].sum()) if not grouped.empty else 0
    rows_by_lane = {row["lane"]: row for row in empty_rows}

    ranked: list[dict[str, Any]] = []
    for _, row in grouped.iterrows():
        lane = str(row["lane"])
        y_min, y_max = _lane_bounds(lane)
        threat_score = float(row["threat_score"])
        ranked.append(
            {
                "lane": lane,
                "label": "Centre" if lane == "central" else lane.title(),
                "y_min": round(y_min, 3),
                "y_max": round(y_max, 3),
                "y_mid": round((y_min + y_max) / 2.0, 3),
                "count": int(row["count"]),
                "share_pct": round((threat_score / total_threat) * 100.0, 1) if total_threat > 0 else 0.0,
                "action_share_pct": round((float(row["count"]) / total_actions) * 100.0, 1) if total_actions > 0 else 0.0,
                "threat_score": round(threat_score, 4),
                "xg": round(float(row["xg"]), 4),
                "xt_created": round(float(row["xt_created"]), 4),
                "final_third_entries": int(row["final_third_entries"]),
                "box_entries": int(row["box_entries"]),
                "shots": int(row["shots"]),
                "goals": int(row["goals"]),
                "rank": 0,
            }
        )

    ranked.sort(key=lambda item: (-float(item["threat_score"]), lane_order.get(str(item["lane"]), 99)))
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
        rows_by_lane[str(item["lane"])] = item

    return [rows_by_lane[lane] for lane in ["left", "central", "right"]]


def _threat_box_indices(x_value: object, y_value: object, x_bins: int = 7, y_bins: int = 3) -> tuple[int, int] | None:
    x = _safe_float(x_value)
    y = _safe_float(y_value)
    if x is None or y is None:
        return None
    x_safe = max(0.0, min(float(x), 99.999))
    y_safe = max(0.0, min(float(y), 99.999))
    x_bin = int(math.floor((x_safe / 100.0) * x_bins))
    y_bin = int(math.floor((y_safe / 100.0) * y_bins))
    return min(max(x_bin, 0), x_bins - 1), min(max(y_bin, 0), y_bins - 1)


def _threat_box_label(x_bin: int, y_bin: int) -> str:
    lane_labels = ["Left", "Centre", "Right"]
    length_labels = ["Deep 1", "Deep 2", "Build", "Middle", "Advanced", "Final third", "Box edge"]
    lane = lane_labels[y_bin] if 0 <= y_bin < len(lane_labels) else f"Lane {y_bin + 1}"
    length = length_labels[x_bin] if 0 <= x_bin < len(length_labels) else f"Zone {x_bin + 1}"
    return f"{length} {lane}"


def _empty_threat_box_grid(x_bins: int = 7, y_bins: int = 3) -> dict[tuple[int, int], dict[str, Any]]:
    boxes: dict[tuple[int, int], dict[str, Any]] = {}
    for x_bin in range(x_bins):
        for y_bin in range(y_bins):
            x_min = (x_bin / x_bins) * 100.0
            x_max = ((x_bin + 1) / x_bins) * 100.0
            y_min = (y_bin / y_bins) * 100.0
            y_max = ((y_bin + 1) / y_bins) * 100.0
            boxes[(x_bin, y_bin)] = {
                "box_id": f"{x_bin}_{y_bin}",
                "x_bin": x_bin,
                "y_bin": y_bin,
                "x_min": round(x_min, 3),
                "x_max": round(x_max, 3),
                "y_min": round(y_min, 3),
                "y_max": round(y_max, 3),
                "x_mid": round((x_min + x_max) / 2.0, 3),
                "y_mid": round((y_min + y_max) / 2.0, 3),
                "label": _threat_box_label(x_bin, y_bin),
                "action_count": 0,
                "xt_created": 0.0,
                "attributed_xg": 0.0,
                "total_threat": 0.0,
                "value": 0.0,
                "share_pct": 0.0,
                "rank": 0,
                "top_box": False,
            }
    return boxes


def _shot_origin_for_threat(ordered_events: pd.DataFrame, shot_position: int) -> pd.Series | None:
    if shot_position <= 0 or ordered_events.empty:
        return None

    shot = ordered_events.iloc[shot_position]
    shot_period = str(shot.get("period", ""))
    shot_team = str(shot.get("team_norm", shot.get("team", "")))
    shot_seconds = _event_seconds(shot)

    for previous_position in range(shot_position - 1, max(-1, shot_position - 11), -1):
        candidate = ordered_events.iloc[previous_position]
        if str(candidate.get("period", "")) != shot_period:
            break
        if str(candidate.get("team_norm", candidate.get("team", ""))) != shot_team:
            break
        if shot_seconds - _event_seconds(candidate) > 15.0:
            break
        if bool(candidate.get("is_pass", False)) or bool(candidate.get("is_cross", False)) or bool(candidate.get("is_carry", False)) or bool(candidate.get("is_take_on", False)):
            has_end = _safe_float(candidate.get("end_x")) is not None and _safe_float(candidate.get("end_y")) is not None
            has_start = _safe_float(candidate.get("x")) is not None and _safe_float(candidate.get("y")) is not None
            if has_end or has_start:
                return candidate
    return None


def _attacking_threat_boxes(events: pd.DataFrame) -> dict[str, Any]:
    x_bins = 7
    y_bins = 3
    boxes = _empty_threat_box_grid(x_bins=x_bins, y_bins=y_bins)

    if events.empty:
        cells = list(boxes.values())
        return {"x_bins": x_bins, "y_bins": y_bins, "cells": cells, "top_boxes": [], "total_threat": 0.0}

    frame = _coerce_analysis_numeric_columns(events)
    positive_xt = pd.to_numeric(frame.get("positive_xt", pd.Series(0.0, index=frame.index)), errors="coerce").fillna(0.0).clip(lower=0.0)
    progressive = (frame["is_pass"] | frame["is_cross"] | frame["is_carry"] | frame["is_take_on"]) & frame["is_success"]
    progression_mask = progressive & frame["end_x"].notna() & frame["end_y"].notna() & (
        positive_xt.gt(0.0)
        | frame["final_third_entry"]
        | frame["box_entry"]
        | frame["end_x"].ge(FINAL_THIRD_X)
    )

    for idx, row in frame.loc[progression_mask].iterrows():
        box_key = _threat_box_indices(row.get("end_x"), row.get("end_y"), x_bins=x_bins, y_bins=y_bins)
        if box_key is None:
            continue
        value = float(positive_xt.loc[idx]) if idx in positive_xt.index else 0.0
        cell = boxes[box_key]
        cell["action_count"] = int(cell["action_count"]) + 1
        cell["xt_created"] = round(float(cell["xt_created"]) + value, 4)

    ordered = _sort_events_by_match_time(frame, ["expanded_minute", "event_index"]).reset_index(drop=True)
    for position, row in ordered.iterrows():
        if not bool(row.get("is_shot", False)):
            continue
        xg_value = _fallback_shot_xg(row)
        origin = _shot_origin_for_threat(ordered, position)
        if origin is not None:
            origin_x = origin.get("end_x") if _safe_float(origin.get("end_x")) is not None else origin.get("x")
            origin_y = origin.get("end_y") if _safe_float(origin.get("end_y")) is not None else origin.get("y")
        else:
            origin_x = row.get("x")
            origin_y = row.get("y")
        box_key = _threat_box_indices(origin_x, origin_y, x_bins=x_bins, y_bins=y_bins)
        if box_key is None:
            continue
        cell = boxes[box_key]
        cell["action_count"] = int(cell["action_count"]) + 1
        cell["attributed_xg"] = round(float(cell["attributed_xg"]) + float(xg_value), 4)

    for cell in boxes.values():
        total = float(cell["xt_created"]) + float(cell["attributed_xg"])
        cell["total_threat"] = round(total, 4)
        cell["value"] = round(total, 4)
        cell["count"] = int(cell["action_count"])

    total_threat = sum(float(cell["total_threat"]) for cell in boxes.values())
    ranked = sorted(boxes.values(), key=lambda item: (-float(item["total_threat"]), -int(item["action_count"]), int(item["x_bin"]), int(item["y_bin"])))
    for rank, cell in enumerate(ranked, start=1):
        cell["rank"] = rank
        cell["share_pct"] = round((float(cell["total_threat"]) / total_threat) * 100.0, 1) if total_threat > 0 else 0.0
        cell["top_box"] = rank <= 5 and float(cell["total_threat"]) > 0

    cells = [boxes[(x_bin, y_bin)] for x_bin in range(x_bins) for y_bin in range(y_bins)]
    top_boxes = [cell for cell in ranked if float(cell["total_threat"]) > 0][:5]
    return {
        "x_bins": x_bins,
        "y_bins": y_bins,
        "cells": cells,
        "top_boxes": top_boxes,
        "total_threat": round(float(total_threat), 4),
        "note": "Threat boxes combine positive xT from successful progressions with shot xG attributed to the final meaningful action before the shot.",
    }


def _event_label(row: pd.Series) -> str:
    minute = row.get("expanded_minute")
    minute_text = f"{float(minute):.1f}'" if pd.notna(minute) else ""
    player = str(row.get("player", "")).strip() or "Unknown"
    event_type = str(row.get("type", "")).strip() or "Event"
    outcome = str(row.get("outcome_type", "")).strip()
    return f"{minute_text} {player} {event_type}" + (f" {outcome}" if outcome else "")


def _is_sequence_break(row: pd.Series) -> bool:
    event_type = str(row.get("type", "")).lower()
    outcome = str(row.get("outcome_type", "")).lower()
    if any(token in event_type for token in ["start", "end", "substitution", "formation", "card"]):
        return True
    if any(token in event_type for token in ["foul", "offside", "clearance"]):
        return True
    if any(token in outcome for token in ["unsuccessful", "inaccurate", "lost", "offside"]):
        return True
    return False


def _shot_sequences(events: pd.DataFrame) -> list[dict[str, Any]]:
    if events.empty:
        return []

    sorted_events = _sort_events_by_match_time(_coerce_analysis_numeric_columns(events), ["expanded_minute", "event_index"]).reset_index(drop=True)
    targets = sorted_events.loc[sorted_events["is_shot"]].copy()
    sequences: list[dict[str, Any]] = []

    for _, shot in targets.iterrows():
        shot_team = str(shot.get("team", ""))
        shot_period = shot.get("period")
        shot_minute = _safe_float(shot.get("expanded_minute"), 0.0) or 0.0
        shot_order = int(shot.name)
        is_set_piece_shot = bool(shot.get("is_set_piece", False))

        chain: list[pd.Series] = [shot]
        previous_minute = shot_minute

        for idx in range(shot_order - 1, -1, -1):
            prev = sorted_events.iloc[idx]
            if str(prev.get("team", "")) != shot_team:
                break
            if prev.get("period") != shot_period:
                break

            current_minute = _safe_float(prev.get("expanded_minute"), 0.0) or 0.0
            if (previous_minute - current_minute) * 60.0 > 15.0:
                break

            if _is_sequence_break(prev) and not bool(prev.get("is_set_piece", False)):
                break

            chain.insert(0, prev)
            previous_minute = current_minute

            if is_set_piece_shot and bool(prev.get("is_set_piece", False)):
                break

            if len(chain) >= 10:
                break

        actions = []
        cumulative_xt = 0.0
        for order, action in enumerate(chain, start=1):
            action_xt = max(_safe_float(action.get("positive_xt", action.get("xt_added", 0.0)), 0.0) or 0.0, 0.0)
            if not bool(action.get("is_shot", False)):
                cumulative_xt += action_xt
            event_index_value = _safe_float(action.get("event_index"))
            actions.append(
                {
                    "order": order,
                    "event_index": int(event_index_value) if event_index_value is not None else None,
                    "minute": _round_float_or_none(action.get("expanded_minute"), 2),
                    "player": str(action.get("player", "")),
                    "team": str(action.get("team", "")),
                    "type": str(action.get("type", "")),
                    "outcome_type": str(action.get("outcome_type", "")),
                    "x": _round_float_or_none(action.get("x"), 2),
                    "y": _round_float_or_none(action.get("y"), 2),
                    "end_x": _round_float_or_none(action.get("end_x"), 2),
                    "end_y": _round_float_or_none(action.get("end_y"), 2),
                    "label": _event_label(action),
                    "is_goal": bool(action.get("is_goal", False)),
                    "is_shot": bool(action.get("is_shot", False)),
                    "is_set_piece": bool(action.get("is_set_piece", False)),
                    "period": _period_to_int(action.get("period")),
                    "xg": _fallback_shot_xg(action) if bool(action.get("is_shot", False)) else None,
                    "xt_added": round(_safe_float(action.get("xt_added"), 0.0) or 0.0, 4),
                    "positive_xt": round(action_xt, 4),
                    "cumulative_sequence_xt": round(cumulative_xt, 4),
                }
            )

        shot_xg = _fallback_shot_xg(shot)
        pre_shot_xt = round(sum(_safe_float(item.get("positive_xt"), 0.0) or 0.0 for item in actions if not bool(item.get("is_shot", False))), 4)
        sequence_threat_score = round(pre_shot_xt + float(shot_xg or 0.0), 4)
        shot_match_id = int(_safe_float(shot.get("match_id"), 0.0) or 0.0)
        shot_event_index = int(_safe_float(shot.get("event_index"), float(shot_order)) or float(shot_order))
        sequences.append(
            {
                "sequence_id": f"{shot_match_id}:{shot_event_index}",
                "team": shot_team,
                "player": str(shot.get("player", "")),
                "minute": round(shot_minute, 2),
                "outcome_type": str(shot.get("outcome_type", "")),
                "is_goal": bool(shot.get("is_goal", False)),
                "is_set_piece": is_set_piece_shot,
                "xg": shot_xg,
                "sequence_xt": pre_shot_xt,
                "pre_shot_xt": pre_shot_xt,
                "sequence_threat_score": sequence_threat_score,
                "period": _period_to_int(shot.get("period")),
                "start_reason": "Set piece delivery" if is_set_piece_shot else "Same possession chain",
                "actions": actions,
            }
        )

    sequences.sort(key=lambda item: (not item["is_goal"], -float(item.get("sequence_threat_score", 0.0)), item["minute"]))
    return sequences[:30]


def _safe_float(value: object, fallback: float | None = None) -> float | None:
    if _is_missing_scalar(value):
        return fallback
    try:
        numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    except Exception:
        return fallback
    if _is_missing_scalar(numeric):
        return fallback
    try:
        number = float(numeric)
    except Exception:
        return fallback
    if not math.isfinite(number):
        return fallback
    return number


def _event_seconds(row: pd.Series) -> float:
    minute = _safe_float(row.get("expanded_minute"))
    if minute is not None:
        return minute * 60.0
    raw_minute = _safe_float(row.get("minute"), 0.0) or 0.0
    raw_second = _safe_float(row.get("second"), 0.0) or 0.0
    return (raw_minute * 60.0) + raw_second


def _in_penalty_box_xy(x_value: object, y_value: object) -> bool:
    x = _safe_float(x_value)
    y = _safe_float(y_value)
    if x is None or y is None:
        return False
    return x >= BOX_X and BOX_Y_MIN <= y <= BOX_Y_MAX


def _detailed_lane_from_y(value: object) -> str:
    y = _safe_float(value)
    if y is None:
        return "unknown"
    y = max(0.0, min(float(y), 100.0))
    if y < 18.0:
        return "left_wide"
    if y < 38.0:
        return "left_half_space"
    if y < 62.0:
        return "central"
    if y < 82.0:
        return "right_half_space"
    return "right_wide"


def _lane_family(lane: str) -> str:
    if lane == "central":
        return "central"
    if "half_space" in lane:
        return "half_space"
    if "wide" in lane:
        return "wide"
    return "unknown"


def _lane_label(lane: str) -> str:
    labels = {
        "left_wide": "Left wide",
        "left_half_space": "Left half space",
        "central": "Central",
        "right_half_space": "Right half space",
        "right_wide": "Right wide",
        "wide": "Wide",
        "half_space": "Half space",
        "unknown": "Unknown",
    }
    return labels.get(lane, lane.replace("_", " ").title())


def _defensive_category(row: pd.Series) -> str:
    text = f"{row.get('type', '')} {row.get('event_type', '')} {row.get('event_type_l', '')}".lower()
    if "interception" in text:
        return "Interceptions"
    if "tackle" in text or "challenge" in text:
        return "Tackles and challenges"
    if "clearance" in text:
        return "Clearances"
    if "block" in text:
        return "Blocks"
    if "recovery" in text:
        return "Recoveries"
    if "aerial" in text or "duel" in text:
        return "Duels"
    if "foul" in text:
        return "Fouls"
    return "Other defensive actions"


def _is_stoppage_event(row: pd.Series) -> bool:
    text = f"{row.get('type', '')} {row.get('event_type', '')} {row.get('outcome_type', '')}".lower()
    return any(
        token in text
        for token in [
            "start",
            "end",
            "substitution",
            "formation",
            "card",
            "foul",
            "offside",
            "period",
        ]
    )


def _add_attack_chain_ids(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        out = events.copy()
        out["attack_chain_id"] = pd.Series(dtype="int64")
        return out

    out = events.copy().reset_index(drop=True)
    out = out.sort_values(["period", "expanded_minute", "event_index"], na_position="last").reset_index(drop=True)

    possession_col = None
    for candidate in ["possession_id", "possession", "sequence_id"]:
        if candidate in out.columns and out[candidate].notna().any():
            possession_col = candidate
            break

    if possession_col:
        raw = out[possession_col].astype(str).replace({"nan": "", "None": "", "<NA>": ""})
        if raw.str.strip().ne("").any():
            keys = out["match_id"].astype(str) + ":" + out["team_norm"].astype(str) + ":" + raw
            out["attack_chain_id"] = pd.factorize(keys)[0] + 1
            return out

    chain_ids: list[int] = []
    current_chain = 0
    previous_row: pd.Series | None = None

    for _, row in out.iterrows():
        current_seconds = _event_seconds(row)
        new_chain = previous_row is None

        if previous_row is not None:
            previous_seconds = _event_seconds(previous_row)
            gap_seconds = current_seconds - previous_seconds
            if row.get("period") != previous_row.get("period"):
                new_chain = True
            if str(row.get("team_norm", "")) != str(previous_row.get("team_norm", "")):
                new_chain = True
            if gap_seconds > 15.0:
                new_chain = True
            if _is_stoppage_event(row) or _is_stoppage_event(previous_row):
                new_chain = True

        if new_chain:
            current_chain += 1
        chain_ids.append(current_chain)
        previous_row = row

    out["attack_chain_id"] = chain_ids
    return out


def _serialise_event_brief(row: pd.Series | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "event_index": int(row.get("event_index", 0)) if pd.notna(row.get("event_index")) else None,
        "minute": round(float(row.get("expanded_minute", 0.0)), 2) if pd.notna(row.get("expanded_minute")) else None,
        "player": str(row.get("player", "")),
        "team": str(row.get("team", "")),
        "type": str(row.get("type", "")),
        "outcome_type": str(row.get("outcome_type", "")),
        "x": round(float(row.get("x")), 2) if pd.notna(row.get("x")) else None,
        "y": round(float(row.get("y")), 2) if pd.notna(row.get("y")) else None,
        "end_x": round(float(row.get("end_x")), 2) if pd.notna(row.get("end_x")) else None,
        "end_y": round(float(row.get("end_y")), 2) if pd.notna(row.get("end_y")) else None,
    }


def _progression_mask(events: pd.DataFrame) -> pd.Series:
    if events.empty:
        return pd.Series(False, index=events.index)
    return (
        (events["is_pass"] | events["is_cross"] | events["is_carry"])
        & events["is_success"]
        & events["x"].notna()
        & events["y"].notna()
        & events["end_x"].notna()
        & events["end_y"].notna()
    )


def _chain_reaches_middle(group: pd.DataFrame) -> bool:
    starts = group["x"].dropna()
    ends = group["end_x"].dropna()
    max_x = max(float(starts.max()) if not starts.empty else 0.0, float(ends.max()) if not ends.empty else 0.0)
    return max_x >= 33.33


def _chain_reaches_box(group: pd.DataFrame) -> bool:
    if bool(group["box_entry"].any()):
        return True
    for _, row in group.iterrows():
        if _in_penalty_box_xy(row.get("x"), row.get("y")) or _in_penalty_box_xy(row.get("end_x"), row.get("end_y")):
            return True
    return False


def _rate(part: int | float, total: int | float) -> float:
    total_value = float(total)
    if total_value <= 0:
        return 0.0
    return round((float(part) / total_value) * 100.0, 1)


def _defensive_control_funnel(opponent_events: pd.DataFrame) -> dict[str, Any]:
    chains = _add_attack_chain_ids(opponent_events)
    if chains.empty:
        return {
            "opponent_attacks": 0,
            "reached_middle_third": 0,
            "reached_final_third": 0,
            "entered_box": 0,
            "shots": 0,
            "goals": 0,
            "stopped_before_final_third": 0,
            "stopped_before_box": 0,
            "rates": {
                "final_third_reach_rate": 0.0,
                "box_entry_rate": 0.0,
                "shot_conversion_from_box_entry": 0.0,
                "goal_conversion_from_shot": 0.0,
                "early_stop_rate": 0.0,
                "box_stop_rate": 0.0,
            },
            "steps": [],
        }

    attacks = int(chains["attack_chain_id"].nunique())
    reached_middle = 0
    reached_final = 0
    entered_box = 0
    chain_shots = 0
    chain_goals = 0

    for _, group in chains.groupby("attack_chain_id", dropna=True):
        if _chain_reaches_middle(group):
            reached_middle += 1
        if bool(group["final_third_entry"].any()) or bool(group["x"].ge(FINAL_THIRD_X).any()) or bool(group["end_x"].ge(FINAL_THIRD_X).any()):
            reached_final += 1
        if _chain_reaches_box(group):
            entered_box += 1
        if bool(group["is_shot"].any()):
            chain_shots += 1
        if bool(group["is_goal"].any()):
            chain_goals += 1

    stopped_before_final = max(attacks - reached_final, 0)
    stopped_before_box = max(attacks - entered_box, 0)
    rates = {
        "final_third_reach_rate": _rate(reached_final, attacks),
        "box_entry_rate": _rate(entered_box, attacks),
        "shot_conversion_from_box_entry": _rate(chain_shots, entered_box),
        "goal_conversion_from_shot": _rate(chain_goals, chain_shots),
        "early_stop_rate": _rate(stopped_before_final, attacks),
        "box_stop_rate": _rate(stopped_before_box, attacks),
    }

    return {
        "opponent_attacks": attacks,
        "reached_middle_third": reached_middle,
        "reached_final_third": reached_final,
        "entered_box": entered_box,
        "shots": int(opponent_events["is_shot"].sum()),
        "goals": int(opponent_events["is_goal"].sum()),
        "chains_with_shots": chain_shots,
        "chains_with_goals": chain_goals,
        "stopped_before_final_third": stopped_before_final,
        "stopped_before_box": stopped_before_box,
        "rates": rates,
        "steps": [
            {"key": "opponent_attacks", "label": "Opponent attacks", "count": attacks, "share_pct": 100.0},
            {"key": "reached_final_third", "label": "Reached final third", "count": reached_final, "share_pct": rates["final_third_reach_rate"]},
            {"key": "entered_box", "label": "Entered box", "count": entered_box, "share_pct": rates["box_entry_rate"]},
            {"key": "shots", "label": "Shot", "count": int(opponent_events["is_shot"].sum()), "share_pct": _rate(chain_shots, attacks)},
            {"key": "goals", "label": "Goal", "count": int(opponent_events["is_goal"].sum()), "share_pct": _rate(chain_goals, attacks)},
        ],
    }


def _progression_allowed(opponent_events: pd.DataFrame) -> list[dict[str, Any]]:
    chains = _add_attack_chain_ids(opponent_events)
    if chains.empty:
        return []

    progressive = chains.loc[_progression_mask(chains)].copy()
    if progressive.empty:
        return []

    progressive["central"] = progressive["end_y"].map(lambda value: _lane_family(_detailed_lane_from_y(value)) in {"central", "half_space"})
    progressive["forward_distance"] = progressive["end_x"] - progressive["x"]
    progressive = progressive.loc[
        progressive["final_third_entry"]
        | progressive["box_entry"]
        | progressive["central"]
        | progressive["forward_distance"].ge(10.0)
    ].copy()

    if progressive.empty:
        return []

    rows: list[dict[str, Any]] = []
    for chain_id, group in chains.groupby("attack_chain_id", dropna=True):
        chain_progressions = progressive.loc[progressive["attack_chain_id"].eq(chain_id)].copy()
        if chain_progressions.empty:
            continue
        group_sorted = group.sort_values(["expanded_minute", "event_index"], na_position="last").reset_index(drop=True)
        shot_positions = group_sorted.index[group_sorted["is_shot"]].tolist()
        shot_times = [_event_seconds(group_sorted.iloc[pos]) for pos in shot_positions]
        shot_event_indexes = [int(group_sorted.iloc[pos].get("event_index", 0)) for pos in shot_positions]

        for _, row in chain_progressions.iterrows():
            event_index = int(row.get("event_index", 0)) if pd.notna(row.get("event_index")) else -1
            event_time = _event_seconds(row)
            led_to_shot = False
            for shot_pos, shot_time, shot_event_index in zip(shot_positions, shot_times, shot_event_indexes):
                if shot_event_index <= event_index:
                    continue
                position_in_chain = group_sorted.index[group_sorted["event_index"].eq(event_index)]
                actions_until_shot = 99
                if len(position_in_chain):
                    actions_until_shot = int(shot_pos - int(position_in_chain[0]))
                if 0 <= shot_time - event_time <= 10.0 or 0 < actions_until_shot <= 5:
                    led_to_shot = True
                    break

            lane = _detailed_lane_from_y(row.get("end_y"))
            rows.append(
                {
                    "event_index": event_index if event_index >= 0 else None,
                    "chain_id": int(chain_id),
                    "start_x": round(float(row["x"]), 2),
                    "start_y": round(float(row["y"]), 2),
                    "end_x": round(float(row["end_x"]), 2),
                    "end_y": round(float(row["end_y"]), 2),
                    "minute": round(float(row["expanded_minute"]), 2) if pd.notna(row.get("expanded_minute")) else None,
                    "team": str(row.get("team", "")),
                    "player": str(row.get("player", "")),
                    "event_type": str(row.get("type", "")),
                    "lane": lane,
                    "lane_family": _lane_family(lane),
                    "final_third_entry": bool(row.get("final_third_entry", False)),
                    "box_entry": bool(row.get("box_entry", False)),
                    "central": bool(_lane_family(lane) in {"central", "half_space"}),
                    "led_to_shot": led_to_shot,
                    "label": f"{row.get('player', '')} {row.get('type', '')}",
                }
            )

    rows.sort(key=lambda item: (not bool(item["led_to_shot"]), not bool(item["box_entry"]), not bool(item["final_third_entry"]), float(item.get("minute") or 0.0)))
    return rows[:160]


def _lane_protection(opponent_events: pd.DataFrame) -> dict[str, Any]:
    lane_order = ["left_wide", "left_half_space", "central", "right_half_space", "right_wide"]
    lane_bounds = {
        "left_wide": (0.0, 18.0),
        "left_half_space": (18.0, 38.0),
        "central": (38.0, 62.0),
        "right_half_space": (62.0, 82.0),
        "right_wide": (82.0, 100.0),
    }
    lane_rows: dict[str, dict[str, Any]] = {
        lane: {
            "lane": lane,
            "label": _lane_label(lane),
            "family": _lane_family(lane),
            "y_min": lane_bounds[lane][0],
            "y_max": lane_bounds[lane][1],
            "final_third_entries": 0,
            "box_entries": 0,
            "shots": 0,
            "goals": 0,
        }
        for lane in lane_order
    }

    progressions = opponent_events.loc[_progression_mask(opponent_events)].copy()
    for _, row in progressions.iterrows():
        if not bool(row.get("final_third_entry", False)) and not bool(row.get("box_entry", False)):
            continue
        lane = _detailed_lane_from_y(row.get("end_y"))
        if lane not in lane_rows:
            continue
        if bool(row.get("final_third_entry", False)):
            lane_rows[lane]["final_third_entries"] += 1
        if bool(row.get("box_entry", False)):
            lane_rows[lane]["box_entries"] += 1

    shots = opponent_events.loc[opponent_events["is_shot"]].copy()
    for _, row in shots.iterrows():
        lane = _detailed_lane_from_y(row.get("y"))
        if lane not in lane_rows:
            continue
        lane_rows[lane]["shots"] += 1
        if bool(row.get("is_goal", False)):
            lane_rows[lane]["goals"] += 1

    detail = [lane_rows[lane] for lane in lane_order]
    totals = {
        "final_third_entries": sum(int(row["final_third_entries"]) for row in detail),
        "box_entries": sum(int(row["box_entries"]) for row in detail),
        "shots": sum(int(row["shots"]) for row in detail),
        "goals": sum(int(row["goals"]) for row in detail),
    }

    for row in detail:
        for key in ["final_third_entries", "box_entries", "shots"]:
            row[f"{key}_share_pct"] = _rate(int(row[key]), totals[key])
        row["share_pct"] = row["box_entries_share_pct"]

    family_rows: dict[str, dict[str, Any]] = {
        "wide": {"family": "wide", "label": "Wide", "final_third_entries": 0, "box_entries": 0, "shots": 0, "goals": 0},
        "half_space": {"family": "half_space", "label": "Half spaces", "final_third_entries": 0, "box_entries": 0, "shots": 0, "goals": 0},
        "central": {"family": "central", "label": "Central", "final_third_entries": 0, "box_entries": 0, "shots": 0, "goals": 0},
    }
    for row in detail:
        family = str(row["family"])
        if family not in family_rows:
            continue
        for key in ["final_third_entries", "box_entries", "shots", "goals"]:
            family_rows[family][key] += int(row[key])

    family = [family_rows["wide"], family_rows["half_space"], family_rows["central"]]
    for row in family:
        for key in ["final_third_entries", "box_entries", "shots"]:
            row[f"{key}_share_pct"] = _rate(int(row[key]), totals[key])

    return {
        "lanes": detail,
        "families": family,
        "totals": totals,
        "central_final_third_entries_conceded": int(family_rows["central"]["final_third_entries"]),
        "half_space_final_third_entries_conceded": int(family_rows["half_space"]["final_third_entries"]),
        "wide_final_third_entries_conceded": int(family_rows["wide"]["final_third_entries"]),
        "central_box_entries_conceded": int(family_rows["central"]["box_entries"]),
        "half_space_box_entries_conceded": int(family_rows["half_space"]["box_entries"]),
        "wide_box_entries_conceded": int(family_rows["wide"]["box_entries"]),
        "central_shots_conceded": int(family_rows["central"]["shots"]),
        "wide_shots_conceded": int(family_rows["wide"]["shots"]),
    }




def _should_flip_second_half(value: object) -> bool:
    period = _period_to_int(value)
    return period in {2, 4}


def _flip_percent(value: object) -> float | None:
    numeric = _safe_float(value)
    if numeric is None:
        return None
    return round(100.0 - max(0.0, min(100.0, numeric)), 2)


def _clamp_percent(value: object) -> float | None:
    numeric = _safe_float(value)
    if numeric is None:
        return None
    return round(float(max(0.0, min(100.0, numeric))), 2)


def _terminal_attack_x(row: pd.Series) -> float | None:
    for candidate in ["visual_target_x", "shot_x", "end_x", "x"]:
        value = _safe_float(row.get(candidate))
        if value is not None:
            return value
    return None


def _should_flip_to_attack_right(row: pd.Series) -> bool:
    terminal_x = _terminal_attack_x(row)
    if terminal_x is None:
        return _should_flip_second_half(row.get("period"))

    # Some WhoScored exports are already normalised left to right for the attacking team,
    # while others keep second half actions in the opposite direction. The visual rule here
    # is intentionally based on the terminal danger location, not on rewriting raw data:
    # if the action finishes in the left half, flip it so conceded danger is read towards
    # the right hand goal in this one direction defensive view.
    return float(terminal_x) < 50.0


def _visual_xy(row: pd.Series, x_col: str = "x", y_col: str = "y") -> tuple[float | None, float | None, bool]:
    x_value = _safe_float(row.get(x_col))
    y_value = _safe_float(row.get(y_col))
    if x_value is None or y_value is None:
        return None, None, False

    flip = _should_flip_to_attack_right(row)
    if flip:
        return _flip_percent(x_value), _flip_percent(y_value), True
    return _clamp_percent(x_value), _clamp_percent(y_value), False


def _direction_terminal_series(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype="float64")

    terminal = pd.Series(math.nan, index=frame.index, dtype="float64")
    for col in ["visual_target_x", "end_x", "x"]:
        if col not in frame.columns:
            continue
        values = pd.to_numeric(frame[col], errors="coerce")
        terminal = terminal.combine_first(values)

    return terminal.astype("float64", copy=False)


def _direction_flip_for_group(frame: pd.DataFrame) -> tuple[bool, dict[str, Any]]:
    terminal = _direction_terminal_series(frame).dropna()
    if terminal.empty:
        return False, {
            "flipped": False,
            "terminal_events_used": 0,
            "median_terminal_x_before": None,
            "median_terminal_x_after": None,
        }
    median_before = float(terminal.median())
    flipped = median_before < 50.0
    median_after = _flip_percent(median_before) if flipped else _clamp_percent(median_before)
    return flipped, {
        "flipped": bool(flipped),
        "terminal_events_used": int(len(terminal)),
        "median_terminal_x_before": round(median_before, 2),
        "median_terminal_x_after": round(float(median_after), 2),
    }


def _with_one_direction_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        out = frame.copy()
        out["visual_x"] = pd.Series(dtype="float64")
        out["visual_y"] = pd.Series(dtype="float64")
        out["visual_end_x"] = pd.Series(dtype="float64")
        out["visual_end_y"] = pd.Series(dtype="float64")
        out["visual_flipped"] = pd.Series(dtype="bool")
        out["visual_direction_group"] = pd.Series(dtype="object")
        return out

    out = frame.copy()
    for col in ["x", "y", "end_x", "end_y"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    group_cols = []
    if "team_norm" in out.columns:
        group_cols.append("team_norm")
    elif "team" in out.columns:
        group_cols.append("team")
    if "period" in out.columns:
        group_cols.append("period")

    if not group_cols:
        group_keys = pd.Series("all", index=out.index)
    else:
        group_keys = out[group_cols].astype(str).agg("|".join, axis=1)

    out["visual_direction_group"] = group_keys
    out["visual_x"] = math.nan
    out["visual_y"] = math.nan
    out["visual_end_x"] = math.nan
    out["visual_end_y"] = math.nan
    out["visual_flipped"] = False

    for group_key, group in out.groupby("visual_direction_group", dropna=False):
        flipped, _audit = _direction_flip_for_group(group)
        idx = group.index
        x_values = pd.to_numeric(out.loc[idx, "x"], errors="coerce") if "x" in out.columns else pd.Series(math.nan, index=idx, dtype="float64")
        y_values = pd.to_numeric(out.loc[idx, "y"], errors="coerce") if "y" in out.columns else pd.Series(math.nan, index=idx, dtype="float64")
        end_x_values = pd.to_numeric(out.loc[idx, "end_x"], errors="coerce") if "end_x" in out.columns else pd.Series(math.nan, index=idx, dtype="float64")
        end_y_values = pd.to_numeric(out.loc[idx, "end_y"], errors="coerce") if "end_y" in out.columns else pd.Series(math.nan, index=idx, dtype="float64")

        if flipped:
            out.loc[idx, "visual_x"] = x_values.map(lambda value: _flip_percent(value) if pd.notna(value) else math.nan)
            out.loc[idx, "visual_y"] = y_values.map(lambda value: _flip_percent(value) if pd.notna(value) else math.nan)
            out.loc[idx, "visual_end_x"] = end_x_values.map(lambda value: _flip_percent(value) if pd.notna(value) else math.nan)
            out.loc[idx, "visual_end_y"] = end_y_values.map(lambda value: _flip_percent(value) if pd.notna(value) else math.nan)
        else:
            out.loc[idx, "visual_x"] = x_values.map(lambda value: _clamp_percent(value) if pd.notna(value) else math.nan)
            out.loc[idx, "visual_y"] = y_values.map(lambda value: _clamp_percent(value) if pd.notna(value) else math.nan)
            out.loc[idx, "visual_end_x"] = end_x_values.map(lambda value: _clamp_percent(value) if pd.notna(value) else math.nan)
            out.loc[idx, "visual_end_y"] = end_y_values.map(lambda value: _clamp_percent(value) if pd.notna(value) else math.nan)
        out.loc[idx, "visual_flipped"] = flipped

    return out


def _direction_audit(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    source = frame.copy()
    if "team_norm" not in source.columns:
        source["team_norm"] = source["team"].map(_norm_team_name) if "team" in source.columns else ""
    if "period" not in source.columns:
        source["period"] = 1
    rows: list[dict[str, Any]] = []
    for (team_norm, period), group in source.groupby(["team_norm", "period"], dropna=False):
        flipped, audit = _direction_flip_for_group(group)
        rows.append(
            {
                "team": str(group["team"].iloc[0]) if "team" in group.columns and len(group) else str(team_norm),
                "team_norm": str(team_norm),
                "period": _period_to_int(period),
                **audit,
            }
        )
    return rows


def _empty_heatmap(x_bins: int = 6, y_bins: int = 5) -> dict[str, Any]:
    return {"x_bins": x_bins, "y_bins": y_bins, "cells": [], "points": [], "total_value": 0.0, "total_count": 0}


def _binned_heatmap(points: pd.DataFrame, x_col: str = "x", y_col: str = "y", x_bins: int = 6, y_bins: int = 5, weight_col: str | None = None) -> dict[str, Any]:
    if points.empty or x_col not in points.columns or y_col not in points.columns:
        return _empty_heatmap(x_bins, y_bins)

    frame = points.dropna(subset=[x_col, y_col]).copy()
    if frame.empty:
        return _empty_heatmap(x_bins, y_bins)

    frame[x_col] = pd.to_numeric(frame[x_col], errors="coerce").clip(lower=0.0, upper=99.999)
    frame[y_col] = pd.to_numeric(frame[y_col], errors="coerce").clip(lower=0.0, upper=99.999)
    frame = frame.dropna(subset=[x_col, y_col]).copy()
    if frame.empty:
        return _empty_heatmap(x_bins, y_bins)

    if weight_col and weight_col in frame.columns:
        frame["__heat_value"] = pd.to_numeric(frame[weight_col], errors="coerce").fillna(0.0).clip(lower=0.0)
    else:
        frame["__heat_value"] = 1.0

    frame["x_bin"] = pd.cut(frame[x_col], bins=x_bins, labels=False, include_lowest=True, right=False)
    frame["y_bin"] = pd.cut(frame[y_col], bins=y_bins, labels=False, include_lowest=True, right=False)
    frame["x_bin"] = pd.to_numeric(frame["x_bin"], errors="coerce").clip(lower=0, upper=x_bins - 1)
    frame["y_bin"] = pd.to_numeric(frame["y_bin"], errors="coerce").clip(lower=0, upper=y_bins - 1)

    grouped = frame.groupby(["x_bin", "y_bin"], dropna=True).agg(count=("__heat_value", "size"), value=("__heat_value", "sum")).reset_index()
    cells = [
        {"x_bin": int(row["x_bin"]), "y_bin": int(row["y_bin"]), "count": int(row["count"]), "value": round(float(row["value"]), 4)}
        for _, row in grouped.iterrows()
        if pd.notna(row["x_bin"]) and pd.notna(row["y_bin"])
    ]

    point_rows = []
    for _, row in frame.head(180).iterrows():
        point_rows.append(
            {
                "x": round(float(row[x_col]), 2),
                "y": round(float(row[y_col]), 2),
                "raw_x": round(float(row["x"]), 2) if "x" in row and pd.notna(row.get("x")) else None,
                "raw_y": round(float(row["y"]), 2) if "y" in row and pd.notna(row.get("y")) else None,
                "period": _period_to_int(row.get("period")) if "period" in row else None,
                "visual_flipped": bool(row.get("visual_flipped", False)),
                "minute": round(float(row.get("expanded_minute", 0.0)), 2) if pd.notna(row.get("expanded_minute")) else None,
                "player": str(row.get("player", "")),
                "type": str(row.get("type", "")),
                "is_goal": bool(row.get("is_goal", False)),
                "is_set_piece": bool(row.get("is_set_piece", False)),
                "value": round(float(row.get("__heat_value", 0.0)), 4),
                "xt_added": round(float(row.get("positive_xt", row.get("xt_added", 0.0)) or 0.0), 4),
            }
        )
    return {"x_bins": x_bins, "y_bins": y_bins, "cells": cells, "points": point_rows, "total_value": round(float(frame["__heat_value"].sum()), 4), "total_count": int(len(frame))}


def _chance_origin_rows(sequences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    origins: list[dict[str, Any]] = []
    for sequence in sequences:
        actions = sequence.get("actions") if isinstance(sequence.get("actions"), list) else []
        if not actions:
            continue
        shot_action = actions[-1]
        preferred = None
        for action in reversed(actions[:-1]):
            if action.get("x") is not None and action.get("y") is not None:
                preferred = action
                break
        if preferred is None:
            preferred = actions[0]
        if preferred.get("x") is None or preferred.get("y") is None:
            continue
        origins.append(
            {
                "x": float(preferred["x"]),
                "y": float(preferred["y"]),
                "end_x": preferred.get("end_x"),
                "end_y": preferred.get("end_y"),
                "period": preferred.get("period", sequence.get("period")),
                "team": sequence.get("team", preferred.get("team", "")),
                "team_norm": _norm_team_name(sequence.get("team", preferred.get("team", ""))),
                "visual_target_x": shot_action.get("x"),
                "shot_x": shot_action.get("x"),
                "shot_y": shot_action.get("y"),
                "expanded_minute": preferred.get("minute"),
                "player": preferred.get("player", ""),
                "type": preferred.get("type", ""),
                "is_goal": bool(sequence.get("is_goal", False)),
                "is_set_piece": bool(sequence.get("is_set_piece", False)),
                "positive_xt": float(preferred.get("positive_xt", preferred.get("xt_added", 0.0)) or 0.0),
                "sequence_xt": float(sequence.get("sequence_xt", sequence.get("pre_shot_xt", 0.0)) or 0.0),
            }
        )
    return origins


def _box_entry_arrows(opponent_events: pd.DataFrame) -> list[dict[str, Any]]:
    chains = _add_attack_chain_ids(opponent_events)
    if chains.empty:
        return []
    entries = chains.loc[chains["box_entry"] & _progression_mask(chains)].copy()
    if entries.empty:
        return []
    entries["visual_target_x"] = entries["end_x"]
    visual_entries = _with_one_direction_columns(entries)
    rows: list[dict[str, Any]] = []
    for _, row in entries.iterrows():
        chain = chains.loc[chains["attack_chain_id"].eq(row.get("attack_chain_id"))].copy()
        event_time = _event_seconds(row)
        led_to_shot = False
        shot_xt_after_entry = 0.0
        for _, shot in chain.loc[chain["is_shot"]].iterrows():
            if _event_seconds(shot) >= event_time and _event_seconds(shot) - event_time <= 10.0:
                led_to_shot = True
                shot_xt_after_entry = float(chain.loc[(chain["event_index"].ge(row.get("event_index", -1))) & (chain["event_index"].le(shot.get("event_index", 10**9))), "positive_xt"].sum()) if "positive_xt" in chain.columns else 0.0
                break

        visual_row = visual_entries.loc[row.name]
        start_x = _safe_float(visual_row.get("visual_x"))
        start_y = _safe_float(visual_row.get("visual_y"))
        end_x = _safe_float(visual_row.get("visual_end_x"))
        end_y = _safe_float(visual_row.get("visual_end_y"))
        flipped = bool(visual_row.get("visual_flipped", False))
        if start_x is None or start_y is None or end_x is None or end_y is None:
            continue

        xt_added = float(row.get("positive_xt", row.get("xt_added", 0.0)) or 0.0)
        rows.append(
            {
                "event_index": int(row.get("event_index", 0)) if pd.notna(row.get("event_index")) else None,
                "chain_id": int(row.get("attack_chain_id", 0)),
                "start_x": start_x,
                "start_y": start_y,
                "end_x": end_x,
                "end_y": end_y,
                "raw_start_x": round(float(row["x"]), 2),
                "raw_start_y": round(float(row["y"]), 2),
                "raw_end_x": round(float(row["end_x"]), 2),
                "raw_end_y": round(float(row["end_y"]), 2),
                "visual_flipped": flipped,
                "visual_flip_reason": "team period direction normalised to the right" if flipped else "team period already attacks right",
                "minute": round(float(row["expanded_minute"]), 2) if pd.notna(row.get("expanded_minute")) else None,
                "period": _period_to_int(row.get("period")),
                "player": str(row.get("player", "")),
                "event_type": str(row.get("type", "")),
                "is_set_piece": bool(row.get("is_set_piece", False)),
                "lane": _detailed_lane_from_y(row.get("end_y")),
                "led_to_shot": led_to_shot,
                "xt_added": round(xt_added, 4),
                "positive_xt": round(max(xt_added, 0.0), 4),
                "shot_xt_after_entry": round(max(float(shot_xt_after_entry), 0.0), 4),
                "arrow_weight": round(max(xt_added, 0.0), 4),
            }
        )
    rows.sort(key=lambda item: (not bool(item["led_to_shot"]), -float(item.get("positive_xt") or 0.0), float(item.get("minute") or 0.0)))
    return rows[:120]


def _danger_heatmaps(opponent_events: pd.DataFrame, danger_sequences: list[dict[str, Any]]) -> dict[str, Any]:
    shots = opponent_events.loc[opponent_events["is_shot"]].copy()
    origins = pd.DataFrame(_chance_origin_rows(danger_sequences))

    shot_xt_by_event: dict[int, float] = {}
    for sequence in danger_sequences:
        actions = sequence.get("actions") if isinstance(sequence.get("actions"), list) else []
        if not actions:
            continue
        shot_action = actions[-1]
        event_index = shot_action.get("event_index")
        try:
            if event_index is not None:
                shot_xt_by_event[int(event_index)] = float(sequence.get("pre_shot_xt", sequence.get("sequence_xt", 0.0)) or 0.0)
        except Exception:
            continue

    if not shots.empty:
        shots["pre_shot_xt"] = shots["event_index"].map(lambda value: shot_xt_by_event.get(int(value), 0.0) if pd.notna(value) else 0.0)
        shots["shot_value"] = pd.to_numeric(shots.get("pre_shot_xt"), errors="coerce").fillna(0.0)

    def _subset_payload(source_shots: pd.DataFrame, source_origins: pd.DataFrame) -> dict[str, Any]:
        source_shots = source_shots.copy()
        if not source_shots.empty:
            source_shots["visual_target_x"] = source_shots["x"]
        visual_shots = _with_one_direction_columns(source_shots)
        box_shots = visual_shots.loc[visual_shots.apply(lambda row: _in_penalty_box_xy(row.get("visual_x"), row.get("visual_y")), axis=1)].copy()
        goals = visual_shots.loc[visual_shots["is_goal"]].copy() if "is_goal" in visual_shots.columns else visual_shots.iloc[0:0].copy()
        visual_origins = _with_one_direction_columns(source_origins) if not source_origins.empty else source_origins
        final_action_key = "final_actions_before_shot"

        shot_count = int(len(visual_shots))
        shot_xt = round(float(pd.to_numeric(visual_shots.get("shot_value", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()) if not visual_shots.empty else 0.0, 4)
        origin_xt = round(float(pd.to_numeric(visual_origins.get("sequence_xt", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()) if isinstance(visual_origins, pd.DataFrame) and not visual_origins.empty else 0.0, 4)
        return {
            "shots_conceded": _binned_heatmap(visual_shots, "visual_x", "visual_y"),
            "shots_conceded_xt": _binned_heatmap(visual_shots, "visual_x", "visual_y", weight_col="shot_value"),
            "box_shots_conceded": _binned_heatmap(box_shots, "visual_x", "visual_y"),
            "box_shots_conceded_xt": _binned_heatmap(box_shots, "visual_x", "visual_y", weight_col="shot_value"),
            "goals_conceded": _binned_heatmap(goals, "visual_x", "visual_y"),
            "chance_origins_conceded": _binned_heatmap(visual_origins, "visual_x", "visual_y") if isinstance(visual_origins, pd.DataFrame) else _empty_heatmap(),
            "chance_origins_conceded_xt": _binned_heatmap(visual_origins, "visual_x", "visual_y", weight_col="sequence_xt") if isinstance(visual_origins, pd.DataFrame) else _empty_heatmap(),
            final_action_key: _binned_heatmap(visual_origins, "visual_x", "visual_y") if isinstance(visual_origins, pd.DataFrame) else _empty_heatmap(),
            f"{final_action_key}_xt": _binned_heatmap(visual_origins, "visual_x", "visual_y", weight_col="sequence_xt") if isinstance(visual_origins, pd.DataFrame) else _empty_heatmap(),
            "metrics": {
                "shots": shot_count,
                "pre_shot_xt": shot_xt,
                "average_pre_shot_xt": round(shot_xt / shot_count, 4) if shot_count else 0.0,
                "final_action_xt": origin_xt,
                "direction_audit": _direction_audit(source_shots),
            },
        }

    open_play_shots = shots.loc[~shots["is_set_piece"]].copy() if "is_set_piece" in shots.columns else shots.copy()
    set_piece_shots = shots.loc[shots["is_set_piece"]].copy() if "is_set_piece" in shots.columns else shots.iloc[0:0].copy()
    open_play_origins = origins.loc[~origins["is_set_piece"]].copy() if not origins.empty and "is_set_piece" in origins.columns else origins.copy()
    set_piece_origins = origins.loc[origins["is_set_piece"]].copy() if not origins.empty and "is_set_piece" in origins.columns else origins.iloc[0:0].copy() if isinstance(origins, pd.DataFrame) else pd.DataFrame()

    all_payload = _subset_payload(shots, origins)
    return {
        **all_payload,
        "view_mode": "team_period_one_direction_right",
        "note": "Danger coordinates are normalised by opponent team and period so attacks read towards the right hand goal. Raw event rows remain unchanged.",
        "direction_audit": _direction_audit(shots),
        "all": all_payload,
        "open_play": _subset_payload(open_play_shots, open_play_origins),
        "set_piece": _subset_payload(set_piece_shots, set_piece_origins),
    }


def _regains_after_loss(events: pd.DataFrame, defending_team: str) -> int:
    if events.empty:
        return 0
    team_norm = _norm_team_name(defending_team)
    ordered = events.sort_values(["period", "expanded_minute", "event_index"], na_position="last").reset_index(drop=True)
    count = 0
    for idx, row in ordered.iterrows():
        if str(row.get("team_norm", "")) != team_norm or not bool(row.get("is_defensive_action", False)):
            continue
        action_time = _event_seconds(row)
        for prev_idx in range(idx - 1, max(-1, idx - 12), -1):
            prev = ordered.iloc[prev_idx]
            if action_time - _event_seconds(prev) > 5.0:
                break
            if str(prev.get("team_norm", "")) == team_norm and (bool(prev.get("is_pass", False)) or bool(prev.get("is_cross", False)) or bool(prev.get("is_carry", False))) and not bool(prev.get("is_success", False)):
                count += 1
                break
    return count


def _defensive_disruption(defending_events: pd.DataFrame, opponent_events: pd.DataFrame, all_events: pd.DataFrame, defending_team: str, funnel: dict[str, Any]) -> dict[str, Any]:
    defensive = defending_events.loc[defending_events["is_defensive_action"]].copy()
    high = int(defensive["x"].ge(66.67).sum()) if not defensive.empty else 0
    middle = int(defensive["x"].between(33.33, 66.67, inclusive="left").sum()) if not defensive.empty else 0
    low = int(defensive["x"].lt(33.33).sum()) if not defensive.empty else 0
    avg_height = round(float(defensive["x"].dropna().mean()), 2) if not defensive.empty and defensive["x"].notna().any() else 0.0
    opponent_progression = opponent_events.loc[_progression_mask(opponent_events)].copy()
    forced_backwards = int((opponent_progression["end_x"] < opponent_progression["x"]).sum()) if not opponent_progression.empty else 0
    recycled = int((opponent_progression["end_x"] <= opponent_progression["x"] + 3.0).sum()) if not opponent_progression.empty else 0
    return {
        "average_defensive_action_height": avg_height,
        "high_regains": int(defending_events["high_regain"].sum()) if not defending_events.empty else 0,
        "high_zone_actions": high,
        "middle_third_actions": middle,
        "low_block_actions": low,
        "regains_within_five_seconds_after_loss": _regains_after_loss(all_events, defending_team),
        "opponent_attacks_stopped_before_final_third": int(funnel.get("stopped_before_final_third", 0)),
        "opponent_attacks_stopped_before_box": int(funnel.get("stopped_before_box", 0)),
        "forced_backward_actions": forced_backwards,
        "recycled_or_backward_actions": recycled,
        "opponent_progression_events": int(len(opponent_progression)),
        "forced_backward_share_pct": _rate(forced_backwards, len(opponent_progression)),
    }


def _event_type_text(row: pd.Series) -> str:
    return " ".join(
        str(row.get(key, ""))
        for key in ["type", "event_type", "event_type_l", "outcome_type", "outcome_type_l", "label"]
    ).lower()


def _event_signature(row: pd.Series) -> str:
    event_index = row.get("event_index")
    if pd.notna(event_index):
        return f"idx:{int(float(event_index))}"
    minute = _safe_float(row.get("expanded_minute"), 0.0) or 0.0
    return f"{row.get('team_norm', '')}:{row.get('player', '')}:{minute:.2f}:{row.get('type', '')}"


def _add_unique_event(target: dict[str, pd.Series], row: pd.Series) -> None:
    target.setdefault(_event_signature(row), row)


def _is_forward_escape(row: pd.Series) -> bool:
    if not bool(row.get("is_success", False)):
        return False
    if not (bool(row.get("is_pass", False)) or bool(row.get("is_cross", False)) or bool(row.get("is_carry", False))):
        return False
    start_x = _safe_float(row.get("x"))
    end_x = _safe_float(row.get("end_x"))
    if start_x is None or end_x is None:
        return False
    return bool(row.get("final_third_entry", False)) or bool(row.get("box_entry", False)) or end_x - start_x >= 12.0


def _pressing_effect(defending_events: pd.DataFrame, opponent_events: pd.DataFrame, all_events: pd.DataFrame, defending_team: str) -> dict[str, Any]:
    empty_payload = {
        "total_press_actions": 0,
        "high_press_actions": 0,
        "middle_press_actions": 0,
        "high_regains": 0,
        "counterpress_regains": 0,
        "forced_back_passes": 0,
        "forced_lateral_passes": 0,
        "forced_long_clearances": 0,
        "forced_out_of_play": 0,
        "failed_build_up_after_press": 0,
        "press_to_shot_chains": 0,
        "press_escapes_allowed": 0,
        "forced_action_total": 0,
        "forced_action_rate_pct": 0.0,
        "escape_rate_pct": 0.0,
        "pressure_outcomes": [],
        "high_regain_events": [],
        "note": "No event feed was available for pressing effect analysis.",
    }
    if all_events.empty:
        return empty_payload

    team_norm = _norm_team_name(defending_team)
    ordered = all_events.sort_values(["period", "expanded_minute", "event_index"], na_position="last").reset_index(drop=True).copy()
    if "team_norm" not in ordered.columns:
        ordered["team_norm"] = ordered["team"].map(_norm_team_name) if "team" in ordered.columns else ""

    press_actions = ordered.loc[
        ordered["team_norm"].eq(team_norm)
        & ordered["is_defensive_action"].astype(bool)
        & pd.to_numeric(ordered["x"], errors="coerce").ge(45.0)
    ].copy()

    if press_actions.empty:
        payload = dict(empty_payload)
        payload["high_regains"] = int(defending_events["high_regain"].sum()) if not defending_events.empty and "high_regain" in defending_events.columns else 0
        payload["counterpress_regains"] = _regains_after_loss(all_events, defending_team)
        payload["note"] = "No clear pressing actions were found above the middle third line."
        return payload

    back_passes: dict[str, pd.Series] = {}
    lateral_passes: dict[str, pd.Series] = {}
    long_clearances: dict[str, pd.Series] = {}
    out_of_play: dict[str, pd.Series] = {}
    failed_build_ups: dict[str, pd.Series] = {}
    press_to_shots: dict[str, pd.Series] = {}
    escapes: dict[str, pd.Series] = {}

    for _, press in press_actions.iterrows():
        press_time = _event_seconds(press)
        press_period = _period_to_int(press.get("period"))
        later_rows = pd.Series(ordered.index, index=ordered.index).gt(int(press.name))
        after = ordered.loc[
            later_rows
            & ordered["team_norm"].ne(team_norm)
            & ordered["team_norm"].astype(str).str.strip().ne("")
        ].copy()
        if after.empty:
            continue
        after["__seconds_after_press"] = after.apply(lambda row: _event_seconds(row) - press_time, axis=1)
        after = after.loc[
            after["__seconds_after_press"].between(0.0, 8.0, inclusive="both")
            & after["period"].apply(_period_to_int).eq(press_period)
        ].copy()
        if after.empty:
            continue

        next_opponent = after.iloc[0]
        text = _event_type_text(next_opponent)
        start_x = _safe_float(next_opponent.get("x"))
        end_x = _safe_float(next_opponent.get("end_x"))
        start_y = _safe_float(next_opponent.get("y"))
        end_y = _safe_float(next_opponent.get("end_y"))
        is_pass = bool(next_opponent.get("is_pass", False)) or bool(next_opponent.get("is_cross", False))
        is_move = is_pass or bool(next_opponent.get("is_carry", False))
        is_success = bool(next_opponent.get("is_success", False))

        if is_pass and start_x is not None and end_x is not None and end_x <= start_x - 6.0:
            _add_unique_event(back_passes, next_opponent)
        if is_pass and start_x is not None and end_x is not None and start_y is not None and end_y is not None:
            if abs(end_x - start_x) < 6.0 and abs(end_y - start_y) >= 10.0:
                _add_unique_event(lateral_passes, next_opponent)
        if ("clearance" in text or "clear" in text) or (is_pass and start_x is not None and end_x is not None and end_x - start_x >= 35.0 and start_x < FINAL_THIRD_X):
            _add_unique_event(long_clearances, next_opponent)
        if (is_move and not is_success) or any(token in text for token in ["out", "throw", "blocked pass", "bad touch", "ball touch"]):
            _add_unique_event(out_of_play, next_opponent)
        if _is_forward_escape(next_opponent):
            _add_unique_event(escapes, next_opponent)

        next_team_event = ordered.loc[
            later_rows
            & ordered["team_norm"].eq(team_norm)
            & ordered["period"].apply(_period_to_int).eq(press_period)
        ].copy()
        if not next_team_event.empty:
            next_team_event["__seconds_after_press"] = next_team_event.apply(lambda row: _event_seconds(row) - press_time, axis=1)
            regain_window = next_team_event.loc[next_team_event["__seconds_after_press"].between(0.0, 10.0, inclusive="both")].copy()
            if not regain_window.empty:
                first_team_event = regain_window.iloc[0]
                if bool(first_team_event.get("is_defensive_action", False)) or bool(first_team_event.get("is_touch", False)) or bool(first_team_event.get("is_pass", False)):
                    if not bool(after.apply(_is_forward_escape, axis=1).any()):
                        _add_unique_event(failed_build_ups, press)

            shot_window = next_team_event.loc[next_team_event["__seconds_after_press"].between(0.0, 15.0, inclusive="both") & next_team_event["is_shot"].astype(bool)].copy()
            if not shot_window.empty:
                _add_unique_event(press_to_shots, shot_window.iloc[0])

    high_regain_frame = defending_events.loc[defending_events["high_regain"]].copy() if not defending_events.empty and "high_regain" in defending_events.columns else defending_events.iloc[0:0].copy()
    high_regain_events = [_serialise_event_brief(row) for _, row in high_regain_frame.head(80).iterrows()]
    high_regain_events = [item for item in high_regain_events if item is not None]

    outcome_rows: list[dict[str, Any]] = []
    for label, source in [
        ("Forced back pass", back_passes),
        ("Forced lateral pass", lateral_passes),
        ("Forced long clearance", long_clearances),
        ("Forced out of play", out_of_play),
        ("Press escape allowed", escapes),
    ]:
        for row in list(source.values())[:30]:
            event = _serialise_event_brief(row)
            if event is None:
                continue
            event["pressing_outcome"] = label
            event["event_kind"] = "pass" if "pass" in label.lower() else "clearance" if "clearance" in label.lower() else "other"
            outcome_rows.append(event)

    total_press = int(len(press_actions))
    forced_total = len(back_passes) + len(lateral_passes) + len(long_clearances) + len(out_of_play)
    return {
        "total_press_actions": total_press,
        "high_press_actions": int(pd.to_numeric(press_actions["x"], errors="coerce").ge(66.67).sum()),
        "middle_press_actions": int(pd.to_numeric(press_actions["x"], errors="coerce").between(45.0, 66.67, inclusive="left").sum()),
        "high_regains": int(defending_events["high_regain"].sum()) if not defending_events.empty and "high_regain" in defending_events.columns else 0,
        "counterpress_regains": _regains_after_loss(all_events, defending_team),
        "forced_back_passes": len(back_passes),
        "forced_lateral_passes": len(lateral_passes),
        "forced_long_clearances": len(long_clearances),
        "forced_out_of_play": len(out_of_play),
        "failed_build_up_after_press": len(failed_build_ups),
        "press_to_shot_chains": len(press_to_shots),
        "press_escapes_allowed": len(escapes),
        "forced_action_total": forced_total,
        "forced_action_rate_pct": _rate(forced_total, total_press),
        "escape_rate_pct": _rate(len(escapes), total_press),
        "pressure_outcomes": outcome_rows[:120],
        "high_regain_events": high_regain_events,
        "note": "Event based pressure effect indicators. They show what happened after pressure actions, not guaranteed causality.",
    }


def _defensive_player_event_points(defending_events: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    defensive = defending_events.loc[defending_events["is_defensive_action"]].copy()
    if defensive.empty:
        return {}

    rows: dict[str, list[dict[str, Any]]] = {}
    defensive = defensive.sort_values(["period", "expanded_minute", "event_index"], na_position="last")
    for _, row in defensive.iterrows():
        player = str(row.get("player", "")).strip() or "Unknown"
        category = _defensive_category(row)
        event = _serialise_event_brief(row)
        if event is None:
            continue
        event["category"] = category
        event["event_kind"] = (
            "interception" if category == "Interceptions" else
            "recovery" if category == "Recoveries" else
            "block" if category == "Blocks" else
            "clearance" if category == "Clearances" else
            "duel" if category == "Duels" else
            "foul" if category == "Fouls" else
            "tackle" if category == "Tackles and challenges" else
            "other"
        )
        event["is_high_regain"] = bool(row.get("high_regain", False))
        event["is_box_action"] = _in_penalty_box_xy(row.get("x"), row.get("y"))
        event["is_wide_action"] = _lane_family(_detailed_lane_from_y(row.get("y"))) == "wide"
        rows.setdefault(player, []).append(event)

    return {player: events[:160] for player, events in rows.items()}


def _defensive_block_map(defending_events: pd.DataFrame, defending_team: str) -> dict[str, Any]:
    defensive = defending_events.loc[defending_events["is_defensive_action"]].copy()
    if defensive.empty:
        return {
            "available": False,
            "team": defending_team,
            "summary": {
                "total_defensive_actions": 0,
                "located_defensive_actions": 0,
                "average_x": 0.0,
                "average_y": 0.0,
                "block_label": "No defensive action block available",
            },
            "players": [],
            "events": [],
            "category_mix": [],
            "note": "No defensive actions were available for this team in the selected game state.",
        }

    defensive["x"] = pd.to_numeric(defensive.get("x"), errors="coerce")
    defensive["y"] = pd.to_numeric(defensive.get("y"), errors="coerce")
    defensive["category"] = defensive.apply(_defensive_category, axis=1)
    located = defensive.dropna(subset=["x", "y"]).copy()

    if located.empty:
        return {
            "available": False,
            "team": defending_team,
            "summary": {
                "total_defensive_actions": int(len(defensive)),
                "located_defensive_actions": 0,
                "average_x": 0.0,
                "average_y": 0.0,
                "block_label": "No located defensive action block",
            },
            "players": [],
            "events": [],
            "category_mix": [],
            "note": "Defensive actions existed, but they did not contain usable pitch coordinates.",
        }

    located["x"] = located["x"].map(lambda value: _clamp_percent(value) if pd.notna(value) else None)
    located["y"] = located["y"].map(lambda value: _clamp_percent(value) if pd.notna(value) else None)
    located = located.dropna(subset=["x", "y"]).copy()

    total_actions = int(len(defensive))
    located_actions = int(len(located))
    average_x = round(float(located["x"].mean()), 2)
    average_y = round(float(located["y"].mean()), 2)
    horizontal_spread = round(float(located["x"].quantile(0.75) - located["x"].quantile(0.25)), 2) if located_actions > 1 else 0.0
    vertical_spread = round(float(located["y"].quantile(0.75) - located["y"].quantile(0.25)), 2) if located_actions > 1 else 0.0
    high_actions = int(located["x"].ge(66.67).sum())
    middle_actions = int(located["x"].between(33.33, 66.67, inclusive="left").sum())
    low_actions = int(located["x"].lt(33.33).sum())

    if average_x < 38.0:
        block_label = "Deep defensive block"
    elif average_x < 58.0:
        block_label = "Mid block defensive shape"
    elif high_actions >= max(4, located_actions * 0.35):
        block_label = "High defensive action block"
    else:
        block_label = "Mixed defensive action block"

    players: list[dict[str, Any]] = []
    for player, group in located.groupby("player", dropna=False):
        name = str(player).strip() or "Unknown"
        category_counts = group.groupby("category", dropna=False).size().to_dict()
        high_regains = int(group["high_regain"].sum()) if "high_regain" in group.columns else 0
        first_minute = _safe_float(group["expanded_minute"].min()) if "expanded_minute" in group.columns else None
        last_minute = _safe_float(group["expanded_minute"].max()) if "expanded_minute" in group.columns else None
        position_text = ""
        for candidate in ["player_position", "position", "position_group"]:
            if candidate in group.columns:
                values = group[candidate].dropna().astype(str).str.strip()
                values = values.loc[values.ne("") & values.ne("nan") & values.ne("Unknown")]
                if not values.empty:
                    position_text = str(values.iloc[0])
                    break

        shirt_no = ""
        if "shirt_no" in group.columns:
            shirt_values = group["shirt_no"].map(_clean_shirt_no)
            shirt_values = shirt_values.loc[shirt_values.astype(str).str.strip().ne("")]
            if not shirt_values.empty:
                shirt_no = str(shirt_values.iloc[0])

        players.append(
            {
                "player": name,
                "team": defending_team,
                "shirt_no": shirt_no,
                "position": position_text,
                "avg_x": round(float(group["x"].mean()), 2),
                "avg_y": round(float(group["y"].mean()), 2),
                "defensive_actions": int(len(group)),
                "share_pct": _rate(int(len(group)), located_actions),
                "high_actions": int(group["x"].ge(66.67).sum()),
                "middle_actions": int(group["x"].between(33.33, 66.67, inclusive="left").sum()),
                "low_actions": int(group["x"].lt(33.33).sum()),
                "high_regains": high_regains,
                "first_minute": round(float(first_minute), 2) if first_minute is not None else None,
                "last_minute": round(float(last_minute), 2) if last_minute is not None else None,
                "categories": {str(key): int(value) for key, value in category_counts.items()},
            }
        )

    players.sort(key=lambda item: (int(item["defensive_actions"]), int(item["high_regains"])), reverse=True)

    events: list[dict[str, Any]] = []
    for _, row in located.sort_values(["period", "expanded_minute", "event_index"], na_position="last").head(260).iterrows():
        category = str(row.get("category", "Other defensive actions"))
        events.append(
            {
                "event_index": int(row.get("event_index", 0)) if pd.notna(row.get("event_index")) else None,
                "minute": round(float(row.get("expanded_minute", 0.0)), 2) if pd.notna(row.get("expanded_minute")) else None,
                "player": str(row.get("player", "")) or "Unknown",
                "team": defending_team,
                "x": round(float(row.get("x")), 2),
                "y": round(float(row.get("y")), 2),
                "type": str(row.get("type", "")),
                "outcome_type": str(row.get("outcome_type", "")),
                "category": category,
                "event_kind": (
                    "interception" if category == "Interceptions" else
                    "recovery" if category == "Recoveries" else
                    "block" if category == "Blocks" else
                    "clearance" if category == "Clearances" else
                    "duel" if category == "Duels" else
                    "foul" if category == "Fouls" else
                    "tackle" if category == "Tackles and challenges" else
                    "other"
                ),
                "is_high_regain": bool(row.get("high_regain", False)),
            }
        )

    category_grouped = located.groupby("category", dropna=False).size().reset_index(name="count").sort_values("count", ascending=False)
    category_mix = [
        {
            "category": str(row["category"]),
            "count": int(row["count"]),
            "share_pct": _rate(int(row["count"]), located_actions),
        }
        for _, row in category_grouped.iterrows()
    ]

    return {
        "available": True,
        "team": defending_team,
        "summary": {
            "total_defensive_actions": total_actions,
            "located_defensive_actions": located_actions,
            "average_x": average_x,
            "average_y": average_y,
            "horizontal_spread": horizontal_spread,
            "vertical_spread": vertical_spread,
            "high_actions": high_actions,
            "middle_actions": middle_actions,
            "low_actions": low_actions,
            "block_label": block_label,
        },
        "players": players,
        "events": events,
        "category_mix": category_mix,
        "note": "This is an event based defensive block proxy. It shows where players made defensive actions, not their full off ball tracking position.",
    }



def _duel_event_text(row: pd.Series) -> str:
    parts = [
        row.get("type", ""),
        row.get("event_type", ""),
        row.get("outcome_type", ""),
        row.get("qualifier_tags", ""),
        row.get("qual_tags", ""),
        row.get("event_kind", ""),
    ]
    return " ".join(str(part) for part in parts).lower()


def _is_duel_event(row: pd.Series) -> bool:
    text = _duel_event_text(row)
    return bool(row.get("is_defensive_action", False)) and any(token in text for token in ["duel", "aerial", "challenge", "tackle"])


def _is_aerial_duel(row: pd.Series) -> bool:
    text = _duel_event_text(row)
    return "aerial" in text or "headed" in text or "header" in text


def _duel_won(row: pd.Series) -> bool:
    outcome = str(row.get("outcome_type", "")).strip().lower()
    text = _duel_event_text(row)
    if bool(row.get("is_success", False)):
        return True
    if any(token in outcome for token in ["won", "successful", "success", "complete", "completed"]):
        return True
    if "won" in text and "lost" not in text:
        return True
    return False


def _defensive_duel_control(defending_events: pd.DataFrame) -> dict[str, Any]:
    if defending_events.empty:
        return {"summary": {}, "players": [], "events": []}

    duels = defending_events.loc[defending_events.apply(_is_duel_event, axis=1)].copy()
    if duels.empty:
        return {
            "summary": {
                "total": 0,
                "won": 0,
                "lost": 0,
                "win_pct": 0.0,
                "loss_pct": 0.0,
                "aerial_total": 0,
                "aerial_won": 0,
                "aerial_lost": 0,
                "aerial_win_pct": 0.0,
                "ground_total": 0,
                "ground_won": 0,
                "ground_lost": 0,
                "ground_win_pct": 0.0,
            },
            "players": [],
            "events": [],
        }

    duels["duel_won"] = duels.apply(_duel_won, axis=1)
    duels["duel_lost"] = ~duels["duel_won"]
    duels["duel_type"] = duels.apply(lambda row: "aerial" if _is_aerial_duel(row) else "ground", axis=1)

    total = int(len(duels))
    won = int(duels["duel_won"].sum())
    lost = int(duels["duel_lost"].sum())
    aerial = duels.loc[duels["duel_type"].eq("aerial")]
    ground = duels.loc[duels["duel_type"].eq("ground")]

    summary = {
        "total": total,
        "won": won,
        "lost": lost,
        "win_pct": _rate(won, total),
        "loss_pct": _rate(lost, total),
        "aerial_total": int(len(aerial)),
        "aerial_won": int(aerial["duel_won"].sum()) if not aerial.empty else 0,
        "aerial_lost": int(aerial["duel_lost"].sum()) if not aerial.empty else 0,
        "aerial_win_pct": _rate(int(aerial["duel_won"].sum()) if not aerial.empty else 0, len(aerial)),
        "ground_total": int(len(ground)),
        "ground_won": int(ground["duel_won"].sum()) if not ground.empty else 0,
        "ground_lost": int(ground["duel_lost"].sum()) if not ground.empty else 0,
        "ground_win_pct": _rate(int(ground["duel_won"].sum()) if not ground.empty else 0, len(ground)),
    }

    players: list[dict[str, Any]] = []
    for player, group in duels.groupby("player", dropna=False):
        name = str(player).strip() or "Unknown"
        aerial_group = group.loc[group["duel_type"].eq("aerial")]
        ground_group = group.loc[group["duel_type"].eq("ground")]
        player_total = int(len(group))
        player_won = int(group["duel_won"].sum())
        player_lost = int(group["duel_lost"].sum())
        players.append(
            {
                "player": name,
                "total": player_total,
                "won": player_won,
                "lost": player_lost,
                "win_pct": _rate(player_won, player_total),
                "loss_pct": _rate(player_lost, player_total),
                "aerial_total": int(len(aerial_group)),
                "aerial_won": int(aerial_group["duel_won"].sum()) if not aerial_group.empty else 0,
                "ground_total": int(len(ground_group)),
                "ground_won": int(ground_group["duel_won"].sum()) if not ground_group.empty else 0,
            }
        )
    players.sort(key=lambda item: (-int(item["total"]), -float(item["win_pct"]), str(item["player"])))

    events: list[dict[str, Any]] = []
    duels = duels.sort_values(["period", "expanded_minute", "event_index"], na_position="last")
    for _, row in duels.iterrows():
        event = _serialise_event_brief(row)
        if event is None:
            continue
        won_value = bool(row.get("duel_won", False))
        duel_type = str(row.get("duel_type", "ground"))
        event.update(
            {
                "event_kind": "duel",
                "duel_type": duel_type,
                "outcome": "won" if won_value else "lost",
                "won": won_value,
                "lost": not won_value,
                "is_aerial": duel_type == "aerial",
                "order": len(events) + 1,
            }
        )
        events.append(event)

    return {"summary": summary, "players": players[:18], "events": events[:260]}


def _best_players_for_team(events: pd.DataFrame, xt_team: dict[str, Any], defensive_team: dict[str, Any], set_piece_team: dict[str, Any]) -> dict[str, Any]:
    if events.empty:
        return {"overall": [], "attacking": [], "defensive": [], "transitions": [], "set_pieces": []}

    def _blank(player: str) -> dict[str, Any]:
        return {
            "player": player,
            "team": str(events["team"].dropna().iloc[0]) if "team" in events.columns and events["team"].notna().any() else "",
            "overall_score": 0.0,
            "attacking_score": 0.0,
            "defensive_score": 0.0,
            "transition_score": 0.0,
            "set_piece_score": 0.0,
            "goals": 0,
            "shots": 0,
            "box_entries": 0,
            "final_third_entries": 0,
            "crosses": 0,
            "take_ons": 0,
            "carries": 0,
            "high_regains": 0,
            "defensive_actions": 0,
            "xt": 0.0,
            "reasons": [],
        }

    players: dict[str, dict[str, Any]] = {}
    for player, group in events.groupby("player", dropna=False):
        name = str(player).strip() or "Unknown"
        item = players.setdefault(name, _blank(name))
        goals = int(group["is_goal"].sum()) if "is_goal" in group.columns else 0
        shots = int(group["is_shot"].sum()) if "is_shot" in group.columns else 0
        box_entries = int(group["box_entry"].sum()) if "box_entry" in group.columns else 0
        final_third_entries = int(group["final_third_entry"].sum()) if "final_third_entry" in group.columns else 0
        crosses = int(group["is_cross"].sum()) if "is_cross" in group.columns else 0
        carries = int(group["is_carry"].sum()) if "is_carry" in group.columns else 0
        take_ons = int(group["is_take_on"].sum()) if "is_take_on" in group.columns else 0
        high_regains = int(group["high_regain"].sum()) if "high_regain" in group.columns else 0
        defensive_actions = int(group["is_defensive_action"].sum()) if "is_defensive_action" in group.columns else 0

        attacking_score = (goals * 5.0) + (shots * 1.25) + (box_entries * 1.15) + (final_third_entries * 0.55) + (crosses * 0.35) + ((carries + take_ons) * 0.35)
        defensive_score = (high_regains * 2.2) + (defensive_actions * 0.45)
        transition_score = (high_regains * 2.0) + (box_entries * 0.8) + (final_third_entries * 0.35) + ((carries + take_ons) * 0.35)

        item.update(
            {
                "goals": goals,
                "shots": shots,
                "box_entries": box_entries,
                "final_third_entries": final_third_entries,
                "crosses": crosses,
                "carries": carries,
                "take_ons": take_ons,
                "high_regains": high_regains,
                "defensive_actions": defensive_actions,
                "attacking_score": attacking_score,
                "defensive_score": defensive_score,
                "transition_score": transition_score,
            }
        )

    for row in xt_team.get("top_players", []) if isinstance(xt_team.get("top_players"), list) else []:
        name = str(row.get("player", "")).strip() or "Unknown"
        item = players.setdefault(name, _blank(name))
        xt_value = float(row.get("total_xt", 0.0) or 0.0)
        item["xt"] = round(xt_value, 4)
        item["attacking_score"] += max(0.0, xt_value) * 14.0
        item["transition_score"] += max(0.0, xt_value) * 7.0

    for row in defensive_team.get("top_defensive_players", []) if isinstance(defensive_team.get("top_defensive_players"), list) else []:
        name = str(row.get("player", "")).strip() or "Unknown"
        item = players.setdefault(name, _blank(name))
        item["defensive_score"] += float(row.get("score", 0.0) or 0.0)

    involvement = set_piece_team.get("involvement", {}) if isinstance(set_piece_team.get("involvement"), dict) else {}
    set_piece_sources = [
        ("takers", "set_pieces_taken", 0.75),
        ("first_contact_players", "first_contacts", 1.0),
        ("shot_takers", "set_piece_shots", 1.4),
        ("defensive_clearers", "clearances", 1.0),
        ("blockers", "blocks", 1.3),
    ]
    for source_key, value_key, weight in set_piece_sources:
        source_rows = involvement.get(source_key, [])
        if not isinstance(source_rows, list):
            continue
        for row in source_rows:
            name = str(row.get("player", "")).strip() or "Unknown"
            item = players.setdefault(name, _blank(name))
            item["set_piece_score"] += float(row.get(value_key, 0.0) or 0.0) * weight

    output = []
    for item in players.values():
        if not item["player"] or item["player"] == "nan":
            continue
        item["overall_score"] = item["attacking_score"] + item["defensive_score"] + item["transition_score"] + item["set_piece_score"]
        reasons: list[str] = []
        if item["goals"]:
            reasons.append(f"{item['goals']} goal")
        if item["shots"]:
            reasons.append(f"{item['shots']} shots")
        if item["box_entries"]:
            reasons.append(f"{item['box_entries']} box entries")
        if item["high_regains"]:
            reasons.append(f"{item['high_regains']} high regains")
        if item["defensive_actions"]:
            reasons.append(f"{item['defensive_actions']} defensive actions")
        if item["xt"] > 0:
            reasons.append(f"{item['xt']:.3f} xT")
        if item["set_piece_score"] > 0:
            reasons.append("set piece involvement")
        item["reasons"] = reasons[:4]
        for key in ["overall_score", "attacking_score", "defensive_score", "transition_score", "set_piece_score"]:
            item[key] = round(float(item[key]), 2)
        output.append(item)

    def _top(score_key: str) -> list[dict[str, Any]]:
        return sorted(output, key=lambda row: float(row.get(score_key, 0.0)), reverse=True)[:8]

    return {
        "overall": _top("overall_score"),
        "attacking": _top("attacking_score"),
        "defensive": _top("defensive_score"),
        "transitions": _top("transition_score"),
        "set_pieces": _top("set_piece_score"),
    }


def _sequence_problem_tag(actions: list[dict[str, Any]], is_set_piece: bool) -> str:
    if is_set_piece or any(bool(action.get("is_set_piece")) for action in actions):
        return "Set piece"
    if not actions:
        return "Other"

    text = " ".join(str(action.get("type", "")) for action in actions).lower()
    if any(token in text for token in ["rebound", "block", "clearance", "aerial"]):
        return "Second ball"

    final_pre_shot = actions[-2] if len(actions) >= 2 else None
    if final_pre_shot:
        pre_type = str(final_pre_shot.get("type", "")).lower()
        start_x = _safe_float(final_pre_shot.get("x"), 0.0) or 0.0
        end_x = _safe_float(final_pre_shot.get("end_x"), start_x) or start_x
        start_y = _safe_float(final_pre_shot.get("y"), 50.0) or 50.0
        end_y = _safe_float(final_pre_shot.get("end_y"), start_y) or start_y
        start_family = _lane_family(_detailed_lane_from_y(start_y))
        end_family = _lane_family(_detailed_lane_from_y(end_y))
        if any(token in pre_type for token in ["carry", "dribble", "take on", "run"]):
            return "Individual carry"
        if "cross" in pre_type and start_family == "wide":
            return "Cross from wide"
        if start_family in {"wide", "half_space"} and end_family == "central" and _in_penalty_box_xy(end_x, end_y):
            return "Cutback or low cross"
        if end_x - start_x >= 30.0:
            return "Direct ball"

    progression_actions = [action for action in actions if action.get("x") is not None and action.get("end_x") is not None]
    if progression_actions:
        central_count = 0
        for action in progression_actions:
            lane = _detailed_lane_from_y(action.get("end_y", action.get("y")))
            if _lane_family(lane) in {"central", "half_space"}:
                central_count += 1
        if central_count >= max(1, len(progression_actions) / 2):
            return "Central access"

    first = actions[0]
    last = actions[-1]
    first_minute = _safe_float(first.get("minute"), 0.0) or 0.0
    last_minute = _safe_float(last.get("minute"), first_minute) or first_minute
    if len(actions) <= 4 and (last_minute - first_minute) * 60.0 <= 10.0:
        return "Transition"

    if final_pre_shot:
        lane = _detailed_lane_from_y(final_pre_shot.get("y"))
        if _lane_family(lane) == "wide":
            return "Wide overload"

    return "Other"


def _defensive_danger_sequences(opponent_events: pd.DataFrame) -> list[dict[str, Any]]:
    base_sequences = _shot_sequences(opponent_events)
    rows: list[dict[str, Any]] = []
    for sequence in base_sequences:
        actions = sequence.get("actions") if isinstance(sequence.get("actions"), list) else []
        if not actions:
            continue
        shot_action = actions[-1]
        pre_shot = actions[-2] if len(actions) >= 2 else None
        tag = _sequence_problem_tag(actions, bool(sequence.get("is_set_piece", False)))
        players = []
        for action in actions:
            player = str(action.get("player", "")).strip()
            if player and player not in players:
                players.append(player)
        rows.append(
            {
                **sequence,
                "defensive_problem_tag": tag,
                "sequence_start": actions[0],
                "breakthrough_action": pre_shot or actions[0],
                "final_action": shot_action,
                "shot_location": {"x": shot_action.get("x"), "y": shot_action.get("y")},
                "players_involved": players[:6],
                "action_count": len(actions),
            }
        )
    return rows[:18]


def _defensive_event_audit(defending_events: pd.DataFrame) -> dict[str, Any]:
    defensive = defending_events.loc[defending_events["is_defensive_action"]].copy()
    category_rows: list[dict[str, Any]] = []
    player_rows: list[dict[str, Any]] = []

    if not defensive.empty:
        defensive["category"] = defensive.apply(_defensive_category, axis=1)
        grouped = defensive.groupby("category", dropna=False).size().reset_index(name="count").sort_values("count", ascending=False)
        total = max(int(grouped["count"].sum()), 1)
        category_rows = [
            {"category": str(row["category"]), "count": int(row["count"]), "share_pct": _rate(int(row["count"]), total)}
            for _, row in grouped.iterrows()
        ]

        player_grouped = (
            defensive.groupby("player", dropna=False)
            .agg(
                defensive_actions=("player", "size"),
                high_regains=("high_regain", "sum"),
            )
            .reset_index()
            .sort_values(["defensive_actions", "high_regains"], ascending=False)
            .head(12)
        )
        player_rows = [
            {
                "player": str(row["player"]),
                "defensive_actions": int(row["defensive_actions"]),
                "high_regains": int(row["high_regains"]),
            }
            for _, row in player_grouped.iterrows()
        ]

    return {
        "total_defensive_actions": int(len(defensive)),
        "high_regains": int(defending_events["high_regain"].sum()) if not defending_events.empty else 0,
        "categories": category_rows,
        "players": player_rows,
        "note": "This is an event volume audit. It helps explain involvement but is not proof of defensive quality by itself.",
    }


def _top_defensive_players(defending_events: pd.DataFrame) -> list[dict[str, Any]]:
    defensive = defending_events.loc[defending_events["is_defensive_action"]].copy()
    if defensive.empty:
        return []
    players: dict[str, dict[str, Any]] = {}
    for _, row in defensive.iterrows():
        player = str(row.get("player", "")).strip() or "Unknown"
        item = players.setdefault(
            player,
            {
                "player": player,
                "team": str(row.get("team", "")),
                "score": 0.0,
                "interceptions_recoveries": 0,
                "successful_tackles_challenges": 0,
                "high_regains": 0,
                "blocked_shots": 0,
                "box_clearances": 0,
                "defensive_actions": 0,
            },
        )
        category = _defensive_category(row)
        item["defensive_actions"] += 1
        item["score"] += 0.4
        if category in {"Interceptions", "Recoveries"}:
            item["interceptions_recoveries"] += 1
            item["score"] += 1.4
        if category == "Tackles and challenges" and bool(row.get("is_success", False)):
            item["successful_tackles_challenges"] += 1
            item["score"] += 1.5
        if bool(row.get("high_regain", False)):
            item["high_regains"] += 1
            item["score"] += 2.0
        if category == "Blocks":
            item["blocked_shots"] += 1
            item["score"] += 2.2
        if category == "Clearances" and _in_penalty_box_xy(row.get("x"), row.get("y")):
            item["box_clearances"] += 1
            item["score"] += 1.5
    output = []
    for item in players.values():
        copy = dict(item)
        copy["score"] = round(float(copy["score"]), 2)
        output.append(copy)
    output.sort(key=lambda item: float(item["score"]), reverse=True)
    return output[:8]


def _defensive_interpretation(defending_team: str, opponent_team: str, funnel: dict[str, Any], lane_protection: dict[str, Any], disruption: dict[str, Any]) -> list[str]:
    rates = funnel.get("rates", {}) if isinstance(funnel.get("rates"), dict) else {}
    box_rate = float(rates.get("box_entry_rate", 0.0))
    final_rate = float(rates.get("final_third_reach_rate", 0.0))
    shot_from_box = float(rates.get("shot_conversion_from_box_entry", 0.0))
    central_box = int(lane_protection.get("central_box_entries_conceded", 0))
    half_box = int(lane_protection.get("half_space_box_entries_conceded", 0))
    wide_box = int(lane_protection.get("wide_box_entries_conceded", 0))
    stopped_before_box = int(funnel.get("stopped_before_box", 0))
    attacks = int(funnel.get("opponent_attacks", 0))

    lines: list[str] = []
    if attacks == 0:
        return [f"{defending_team} had no clear opponent attack chains to assess from the event feed."]

    if box_rate <= 18.0:
        lines.append(f"{defending_team} restricted box access well, with only {box_rate:.1f} percent of {opponent_team} attacks entering the penalty area.")
    elif box_rate <= 32.0:
        lines.append(f"{defending_team} allowed some box access, but {stopped_before_box} opponent attacks were still stopped before the penalty area.")
    else:
        lines.append(f"{defending_team} allowed too many attacks to reach the box, with {box_rate:.1f} percent of opponent attacks entering the penalty area.")

    if final_rate <= 45.0:
        lines.append("Defensive control was strongest before the final third, so a good share of attacks were disrupted early rather than defended late.")
    else:
        lines.append("The opponent reached the final third often enough for the defensive read to focus on box protection and shot suppression, not only pressure height.")

    if central_box + half_box <= wide_box:
        lines.append("They forced more box access wide than through the centre, which is acceptable when those wide entries do not consistently survive into shots.")
    else:
        lines.append("The main concern was central or half space access before the shot, not simply the total number of entries conceded.")

    if shot_from_box <= 45.0:
        lines.append("Shot suppression after box entry was acceptable, with several entries failing to survive into a shot.")
    else:
        lines.append("Too many box entries survived into shots, so the defensive structure broke after the initial entry rather than only at the entry point.")

    if float(disruption.get("forced_backward_share_pct", 0.0)) >= 25.0:
        lines.append("The event data shows useful disruption, with a notable share of opponent progression forced backwards or recycled.")
    else:
        lines.append("The disruption signal was modest, so the tab should be read through progression allowed and danger sequence detail rather than action volume alone.")

    return lines



def _defensive_height_profile(defending_events: pd.DataFrame, opponent_events: pd.DataFrame) -> dict[str, Any]:
    defensive = defending_events.loc[defending_events["is_defensive_action"]].copy()
    opponent_progressions = opponent_events.loc[_progression_mask(opponent_events)].copy() if not opponent_events.empty else pd.DataFrame()
    opponent_xt = float(pd.to_numeric(opponent_progressions.get("positive_xt", pd.Series(dtype=float)), errors="coerce").fillna(0.0).clip(lower=0.0).sum()) if not opponent_progressions.empty else 0.0
    central_xt = 0.0
    if not opponent_progressions.empty:
        central_mask = opponent_progressions["end_y"].map(lambda value: _lane_family(_detailed_lane_from_y(value)) in {"central", "half_space"})
        central_xt = float(pd.to_numeric(opponent_progressions.loc[central_mask, "positive_xt"], errors="coerce").fillna(0.0).clip(lower=0.0).sum()) if "positive_xt" in opponent_progressions.columns else 0.0

    box_entries = int((opponent_events["box_entry"] & _progression_mask(opponent_events)).sum()) if not opponent_events.empty else 0
    shots = int(opponent_events["is_shot"].sum()) if not opponent_events.empty else 0
    box_arrows = _box_entry_arrows(opponent_events)
    entries_to_shot = int(sum(1 for item in box_arrows if bool(item.get("led_to_shot", False))))
    entry_to_shot_rate = _rate(entries_to_shot, box_entries)
    central_xt_share = _rate(central_xt, opponent_xt) if opponent_xt > 0 else 0.0

    if defensive.empty:
        return {
            "average_height": 0.0,
            "block_label": "No clear block profile",
            "zones": [],
            "points": [],
            "xt_heatmap_allowed": _empty_heatmap(),
            "block_profile": {
                "label": "No clear block profile",
                "confidence": 0.0,
                "opponent_xt_allowed": round(opponent_xt, 4),
                "central_xt_share_pct": round(central_xt_share, 1),
                "box_entry_to_shot_rate": entry_to_shot_rate,
            },
        }

    defensive = defensive.dropna(subset=["x", "y"]).copy()
    if defensive.empty:
        return {"average_height": 0.0, "block_label": "No located defensive actions", "zones": [], "points": [], "xt_heatmap_allowed": _empty_heatmap(), "block_profile": {"label": "No located defensive actions", "confidence": 0.0}}

    avg_x = round(float(defensive["x"].mean()), 2)
    deep_share = _rate(int(defensive["x"].lt(33.33).sum()), len(defensive))
    high_share = _rate(int(defensive["x"].ge(66.67).sum()), len(defensive))
    shot_suppression_rate = round(max(0.0, 100.0 - entry_to_shot_rate), 1)

    if avg_x < 38.0 and entry_to_shot_rate <= 32.0 and central_xt_share <= 42.0:
        block_label = "Deep compact block"
    elif avg_x < 38.0:
        block_label = "Deep pressure survival"
    elif avg_x < 58.0 and entry_to_shot_rate <= 38.0 and central_xt_share <= 45.0:
        block_label = "Mid block control"
    elif avg_x < 58.0:
        block_label = "Mid block exposed"
    elif high_share >= 35.0 and box_entries <= max(4, shots * 2):
        block_label = "High disruption profile"
    elif high_share >= 35.0:
        block_label = "High press exposed behind"
    else:
        block_label = "Unclear mixed profile"

    zones = [
        {"key": "low", "label": "Deep action zone", "x_min": 0.0, "x_max": 33.33, "count": int(defensive["x"].lt(33.33).sum())},
        {"key": "middle", "label": "Middle action zone", "x_min": 33.33, "x_max": 66.67, "count": int(defensive["x"].between(33.33, 66.67, inclusive="left").sum())},
        {"key": "high", "label": "Advanced disruption zone", "x_min": 66.67, "x_max": 100.0, "count": int(defensive["x"].ge(66.67).sum())},
    ]
    total = max(int(len(defensive)), 1)
    for zone in zones:
        zone["share_pct"] = _rate(int(zone["count"]), total)

    points = []
    for _, row in defensive.sort_values(["expanded_minute", "event_index"], na_position="last").head(220).iterrows():
        points.append(
            {
                "event_index": int(row.get("event_index", 0)) if pd.notna(row.get("event_index")) else None,
                "x": round(float(row["x"]), 2),
                "y": round(float(row["y"]), 2),
                "minute": round(float(row.get("expanded_minute", 0.0)), 2) if pd.notna(row.get("expanded_minute")) else None,
                "player": str(row.get("player", "")),
                "type": str(row.get("type", "")),
                "event_type": str(row.get("type", "")),
                "outcome_type": str(row.get("outcome_type", "")),
                "is_success": bool(row.get("is_success", False)),
            }
        )

    xt_visual = _with_one_direction_columns(opponent_progressions.copy()) if not opponent_progressions.empty else opponent_progressions
    block_profile = {
        "label": block_label,
        "confidence": round(min(100.0, 40.0 + min(len(defensive), 60) + min(box_entries * 2.0, 24.0)) / 100.0, 2),
        "block_depth_score": round(max(0.0, 100.0 - avg_x), 1),
        "territory_allowed_score": round(float(opponent_events["x"].dropna().mean()) if not opponent_events.empty and opponent_events["x"].notna().any() else 0.0, 1),
        "central_access_allowed_score": round(central_xt_share, 1),
        "box_access_allowed_score": box_entries,
        "xT_allowed_score": round(opponent_xt, 4),
        "shot_suppression_score": shot_suppression_rate,
        "entry_suppression_score": shot_suppression_rate,
        "pressure_height_score": round(high_share, 1),
        "opponent_xt_allowed": round(opponent_xt, 4),
        "central_xt_allowed": round(central_xt, 4),
        "central_xt_share_pct": round(central_xt_share, 1),
        "box_entries_allowed": box_entries,
        "box_entries_to_shot": entries_to_shot,
        "box_entry_to_shot_rate": entry_to_shot_rate,
    }

    return {
        "average_height": avg_x,
        "block_label": block_label,
        "zones": zones,
        "points": points,
        "xt_heatmap_allowed": _binned_heatmap(xt_visual, "visual_end_x", "visual_end_y", weight_col="positive_xt") if isinstance(xt_visual, pd.DataFrame) else _empty_heatmap(),
        "block_profile": block_profile,
        "note": "Block profile combines defensive action height with opponent territory, xT allowed, box access and entry survival.",
    }


def _prepare_xt_match_frame(events: pd.DataFrame) -> pd.DataFrame:
    out = events.copy()
    if out.empty:
        return out

    if "match_id" not in out.columns:
        out["match_id"] = 0
    out["period"] = _period_sort_series(out)
    out["expanded_minute"] = pd.to_numeric(out.get("expanded_minute"), errors="coerce").fillna(0.0)
    if "event_index" in out.columns:
        out["event_index"] = pd.to_numeric(out["event_index"], errors="coerce").fillna(pd.Series(range(len(out)), index=out.index)).astype(int)
    else:
        out["event_index"] = pd.Series(range(len(out)), index=out.index, dtype="int64")
    out["team"] = _text_series(out, "team")
    out["player"] = _text_series(out, "player")
    out["type_l"] = _text_series(out, "type").str.lower().str.strip()
    out["outcome_l"] = _text_series(out, "outcome_type").str.lower().str.strip()
    out["x_120"] = pd.to_numeric(out.get("x_120"), errors="coerce") if "x_120" in out.columns else pd.to_numeric(out.get("x"), errors="coerce") * 1.2
    out["y_80"] = pd.to_numeric(out.get("y_80"), errors="coerce") if "y_80" in out.columns else pd.to_numeric(out.get("y"), errors="coerce") * 0.8
    out["end_x_120"] = pd.to_numeric(out.get("end_x_120"), errors="coerce") if "end_x_120" in out.columns else pd.to_numeric(out.get("end_x"), errors="coerce") * 1.2
    out["end_y_80"] = pd.to_numeric(out.get("end_y_80"), errors="coerce") if "end_y_80" in out.columns else pd.to_numeric(out.get("end_y"), errors="coerce") * 0.8
    out["successful"] = out["is_success"].astype(bool) if "is_success" in out.columns else False
    out["is_pass_like"] = (out["is_pass"].astype(bool) | out["is_cross"].astype(bool)) if "is_pass" in out.columns and "is_cross" in out.columns else out["type_l"].str.contains("pass|cross", regex=True, na=False)
    out["is_carry"] = out["is_carry"].astype(bool) if "is_carry" in out.columns else out["type_l"].str.contains("carry|ballcarry|run", regex=True, na=False)
    out["is_take_on"] = out["is_take_on"].astype(bool) if "is_take_on" in out.columns else out["type_l"].str.contains("take on|takeon|dribble", regex=True, na=False)
    out["is_shot_event"] = out["is_shot"].astype(bool) if "is_shot" in out.columns else out["type_l"].str.contains("shot", na=False)
    out["is_goal"] = out["is_goal"].astype(bool) if "is_goal" in out.columns else out["type_l"].eq("goal")
    out["is_set_piece_action"] = out["is_set_piece"].astype(bool) if "is_set_piece" in out.columns else False
    if "qual_tags" not in out.columns:
        out["qual_tags"] = [[] for _ in range(len(out))]
    return out




def _attach_xt_values_to_events(events: pd.DataFrame) -> pd.DataFrame:
    out = events.copy()
    out["xt_added"] = 0.0
    out["positive_xt"] = 0.0
    out["xt_start"] = 0.0
    out["xt_end"] = 0.0
    out["xt_action_type"] = ""

    if out.empty:
        return out

    try:
        xt_frame = _prepare_xt_match_frame(out)
        model = build_xt_model(xt_frame, include_set_pieces=False)
        valued = value_actions(xt_frame, model=model, include_set_pieces=False)
    except Exception:
        return out

    if valued.empty or "event_index" not in valued.columns:
        return out

    valued = valued.copy()
    valued["match_id"] = pd.to_numeric(valued.get("match_id"), errors="coerce").fillna(0).astype(int)
    valued["event_index"] = pd.to_numeric(valued.get("event_index"), errors="coerce").fillna(-1).astype(int)
    valued["xt_added"] = pd.to_numeric(valued.get("xt_added"), errors="coerce").fillna(0.0)
    valued["positive_xt"] = valued["xt_added"].clip(lower=0.0)
    valued["xt_start"] = pd.to_numeric(valued.get("xt_start"), errors="coerce").fillna(0.0)
    valued["xt_end"] = pd.to_numeric(valued.get("xt_end"), errors="coerce").fillna(0.0)
    valued["xt_action_type"] = valued.get("action_type", pd.Series([""] * len(valued), index=valued.index)).astype(str)

    xt_values = (
        valued.groupby(["match_id", "event_index"], dropna=False)
        .agg(
            xt_added=("xt_added", "sum"),
            positive_xt=("positive_xt", "sum"),
            xt_start=("xt_start", "max"),
            xt_end=("xt_end", "max"),
            xt_action_type=("xt_action_type", "first"),
        )
        .reset_index()
    )

    out["match_id"] = pd.to_numeric(out.get("match_id"), errors="coerce").fillna(0).astype(int)
    out["event_index"] = pd.to_numeric(out.get("event_index"), errors="coerce").fillna(-1).astype(int)
    out = out.drop(columns=["xt_added", "positive_xt", "xt_start", "xt_end", "xt_action_type"], errors="ignore").merge(
        xt_values,
        on=["match_id", "event_index"],
        how="left",
    )
    for col in ["xt_added", "positive_xt", "xt_start", "xt_end"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    out["xt_action_type"] = out["xt_action_type"].fillna("").astype(str)
    return out


def _build_xt_analysis(events: pd.DataFrame, home_team: str, away_team: str) -> dict[str, Any]:
    empty_team = lambda team: {
        "team": team,
        "available": False,
        "total_xt": 0.0,
        "top_actions": [],
        "top_players": [],
        "action_mix": [],
    }

    if events.empty:
        return {"home": empty_team(home_team), "away": empty_team(away_team), "note": "No events available for xT."}

    xt_frame = _prepare_xt_match_frame(events)
    try:
        model = build_xt_model(xt_frame, include_set_pieces=False)
        valued = value_actions(xt_frame, model=model, include_set_pieces=False)
    except Exception as exc:
        return {
            "home": {**empty_team(home_team), "reason": f"xT unavailable: {type(exc).__name__}"},
            "away": {**empty_team(away_team), "reason": f"xT unavailable: {type(exc).__name__}"},
            "note": "xT could not be calculated from this match event sample.",
        }

    if valued.empty:
        return {"home": empty_team(home_team), "away": empty_team(away_team), "note": "No successful open play progression actions available for xT."}

    valued = valued.copy()
    valued["x_start_pct"] = (pd.to_numeric(valued["x_120"], errors="coerce") / 1.2).clip(0.0, 100.0)
    valued["y_start_pct"] = (pd.to_numeric(valued["y_80"], errors="coerce") / 0.8).clip(0.0, 100.0)
    valued["x_end_pct"] = (pd.to_numeric(valued["end_x_120"], errors="coerce") / 1.2).clip(0.0, 100.0)
    valued["y_end_pct"] = (pd.to_numeric(valued["end_y_80"], errors="coerce") / 0.8).clip(0.0, 100.0)
    valued["positive_xt"] = pd.to_numeric(valued["xt_added"], errors="coerce").fillna(0.0).clip(lower=0.0)

    def _team_xt(team_name: str) -> dict[str, Any]:
        team_frame = valued.loc[valued["team"].astype(str).map(_norm_team_name).eq(_norm_team_name(team_name))].copy()
        if team_frame.empty:
            return empty_team(team_name)

        top = team_frame.sort_values("positive_xt", ascending=False).head(35)
        top_actions = []
        for _, row in top.iterrows():
            if float(row.get("positive_xt", 0.0)) <= 0:
                continue
            top_actions.append(
                {
                    "event_index": int(row.get("event_index", 0)) if pd.notna(row.get("event_index")) else None,
                    "minute": round(float(row.get("expanded_minute", 0.0)), 2) if pd.notna(row.get("expanded_minute")) else None,
                    "player": str(row.get("player", "")),
                    "event_type": str(row.get("action_type", "progression")),
                    "start_x": round(float(row.get("x_start_pct", 0.0)), 2),
                    "start_y": round(float(row.get("y_start_pct", 0.0)), 2),
                    "end_x": round(float(row.get("x_end_pct", 0.0)), 2),
                    "end_y": round(float(row.get("y_end_pct", 0.0)), 2),
                    "xt_added": round(float(row.get("positive_xt", 0.0)), 4),
                    "led_to_shot": False,
                }
            )

        player_group = (
            team_frame.groupby("player", dropna=False)
            .agg(total_xt=("positive_xt", "sum"), actions=("positive_xt", "size"))
            .reset_index()
            .sort_values("total_xt", ascending=False)
            .head(8)
        )
        top_players = [
            {"player": str(row["player"]), "total_xt": round(float(row["total_xt"]), 4), "actions": int(row["actions"])}
            for _, row in player_group.iterrows()
            if float(row["total_xt"]) > 0
        ]

        mix_group = (
            team_frame.groupby("action_type", dropna=False)
            .agg(total_xt=("positive_xt", "sum"), actions=("positive_xt", "size"))
            .reset_index()
            .sort_values("total_xt", ascending=False)
        )
        action_mix = [
            {"action_type": str(row["action_type"]), "total_xt": round(float(row["total_xt"]), 4), "actions": int(row["actions"])}
            for _, row in mix_group.iterrows()
        ]

        return {
            "team": team_name,
            "available": True,
            "total_xt": round(float(team_frame["positive_xt"].sum()), 4),
            "top_actions": top_actions,
            "top_players": top_players,
            "action_mix": action_mix,
            "note": "Single match xT is useful for progression value, but should be read as event value rather than finishing quality.",
        }

    return {
        "home": _team_xt(home_team),
        "away": _team_xt(away_team),
        "note": "Expected Threat values successful open play passes, crosses and carries by the increase in zone value from start to end.",
    }

def _build_defensive_analysis(defending_team: str, opponent_team: str, defending_events: pd.DataFrame, opponent_events: pd.DataFrame, all_events: pd.DataFrame) -> dict[str, Any]:
    opponent_events = _add_attack_chain_ids(opponent_events)
    funnel = _defensive_control_funnel(opponent_events)
    progressions = _progression_allowed(opponent_events)
    lanes = _lane_protection(opponent_events)
    sequences = _defensive_danger_sequences(opponent_events)
    box_arrows = _box_entry_arrows(opponent_events)
    box_arrows_open_play = [item for item in box_arrows if not bool(item.get("is_set_piece", False))]
    box_arrows_set_piece = [item for item in box_arrows if bool(item.get("is_set_piece", False))]
    heatmaps = _danger_heatmaps(opponent_events, sequences)
    disruption = _defensive_disruption(defending_events, opponent_events, all_events, defending_team, funnel)
    pressing_effect = _pressing_effect(defending_events, opponent_events, all_events, defending_team)
    audit = _defensive_event_audit(defending_events)
    leaders = _top_defensive_players(defending_events)
    player_events = _defensive_player_event_points(defending_events)
    defensive_block_map = _defensive_block_map(defending_events, defending_team)
    defensive_height = _defensive_height_profile(defending_events, opponent_events)
    duel_control = _defensive_duel_control(defending_events)
    return {
        "defending_team": defending_team,
        "opponent_team": opponent_team,
        "control_funnel": funnel,
        "progression_allowed": progressions,
        "lane_protection": lanes,
        "danger_heatmaps": heatmaps,
        "box_entry_arrows": box_arrows,
        "box_entry_arrows_by_phase": {
            "all": box_arrows,
            "open_play": box_arrows_open_play,
            "set_piece": box_arrows_set_piece,
        },
        "defensive_height": defensive_height,
        "defensive_block_map": defensive_block_map,
        "disruption": disruption,
        "pressing_effect": pressing_effect,
        "danger_sequences": sequences,
        "top_defensive_players": leaders,
        "defensive_player_events": player_events,
        "duel_control": duel_control,
        "event_audit": audit,
        "interpretation": _defensive_interpretation(defending_team, opponent_team, funnel, lanes, disruption),
    }


def _set_piece_text(row: pd.Series) -> str:
    parts = [
        row.get("type", ""),
        row.get("event_type", ""),
        row.get("outcome_type", ""),
        row.get("qualifier_tags", ""),
        row.get("qual_tags", ""),
    ]
    return " ".join(str(part) for part in parts).lower()


def _is_set_piece_restart(row: pd.Series) -> bool:
    text = _set_piece_text(row)
    is_shot = bool(row.get("is_shot", False))
    is_delivery = bool(row.get("is_pass", False)) or bool(row.get("is_cross", False)) or bool(row.get("is_carry", False))
    is_penalty = bool(row.get("is_penalty", False)) or "penalty" in text
    is_corner = bool(row.get("is_corner", False)) or "corner" in text
    is_free_kick = bool(row.get("is_free_kick", False)) or "free kick" in text or "freekick" in text
    is_throw = bool(row.get("is_throw_in", False)) or "throw" in text

    if is_penalty:
        return True
    if is_corner:
        return not is_shot and (is_delivery or "corner" in text)
    if is_free_kick:
        return is_delivery or is_shot or "free kick" in text or "freekick" in text
    if is_throw:
        return not is_shot and (is_delivery or "throw" in text)
    return False


def _set_piece_type(row: pd.Series) -> str:
    text = _set_piece_text(row)
    x_value = _safe_float(row.get("x"), 0.0) or 0.0

    if bool(row.get("is_penalty", False)) or "penalty" in text:
        return "Penalty"

    if bool(row.get("is_corner", False)) or "corner" in text:
        return "Corner"

    if bool(row.get("is_free_kick", False)) or "free kick" in text or "freekick" in text:
        if bool(row.get("is_shot", False)) or "direct" in text or "shot" in text:
            return "Direct free kick"
        if x_value >= 55.0:
            return "Wide free kick"
        return "Free kick"

    if bool(row.get("is_throw_in", False)) or "throw" in text:
        if x_value >= FINAL_THIRD_X:
            return "Final third throw in"
        return "Throw in"

    return "Set piece"


def _set_piece_zone_label(x_value: object, y_value: object) -> str:
    x = _safe_float(x_value)
    y = _safe_float(y_value)

    if x is None or y is None:
        return "Unknown zone"

    if _in_penalty_box_xy(x, y):
        lane = _lane_family(_detailed_lane_from_y(y))
        if lane == "central":
            return "Central box"
        if lane == "half_space":
            return "Half space box"
        return "Wide box"

    if x >= FINAL_THIRD_X:
        return f"Final third {_lane_label(_detailed_lane_from_y(y)).lower()}"

    if x >= 33.33:
        return f"Middle third {_lane_label(_detailed_lane_from_y(y)).lower()}"

    return f"Deep {_lane_label(_detailed_lane_from_y(y)).lower()}"


def _set_piece_tag(sequence: dict[str, Any]) -> str:
    restart_type = str(sequence.get("restart_type", "Set piece"))
    actions = sequence.get("actions") if isinstance(sequence.get("actions"), list) else []
    first_contact = sequence.get("first_contact") if isinstance(sequence.get("first_contact"), dict) else None
    second_ball = sequence.get("second_ball") if isinstance(sequence.get("second_ball"), dict) else None
    delivery = sequence.get("delivery") if isinstance(sequence.get("delivery"), dict) else {}

    if restart_type == "Penalty":
        return "Penalty"
    if restart_type == "Direct free kick":
        return "Direct free kick"

    if restart_type == "Corner":
        start_x = _safe_float(delivery.get("start_x"), 0.0) or 0.0
        start_y = _safe_float(delivery.get("start_y"), 0.0) or 0.0
        end_x = _safe_float(delivery.get("end_x"), start_x) or start_x
        end_y = _safe_float(delivery.get("end_y"), start_y) or start_y
        distance = math.hypot(end_x - start_x, end_y - start_y)
        if distance <= 12.0:
            return "Short corner"
        if not bool(sequence.get("first_contact_won", False)) and not bool(sequence.get("led_to_shot", False)):
            return "Cleared first contact"
        if bool(sequence.get("second_ball_retained", False)) and bool(sequence.get("led_to_shot", False)):
            return "Second ball"
        if _lane_family(_detailed_lane_from_y(end_y)) == "central" and _in_penalty_box_xy(end_x, end_y):
            return "Central crowd"
        if end_y <= 33.0 or end_y >= 67.0:
            return "Near or far post delivery"
        return "Central delivery"

    if restart_type == "Final third throw in":
        return "Long throw" if bool(sequence.get("led_to_shot", False)) else "Final third throw"

    if restart_type in {"Wide free kick", "Free kick"}:
        if not first_contact and not bool(sequence.get("led_to_shot", False)):
            return "Poor delivery"
        if second_ball and bool(sequence.get("second_ball_retained", False)):
            return "Second ball"
        return "Wide free kick delivery" if restart_type == "Wide free kick" else "Free kick delivery"

    text = " ".join(str(action.get("type", "")) for action in actions).lower()
    if "clearance" in text and not bool(sequence.get("led_to_shot", False)):
        return "Cleared first contact"

    return "Set piece sequence"



def _set_piece_delivery_distance(sequence: dict[str, Any]) -> float:
    delivery = sequence.get("delivery") if isinstance(sequence.get("delivery"), dict) else {}
    start_x = _safe_float(delivery.get("start_x"))
    start_y = _safe_float(delivery.get("start_y"))
    end_x = _safe_float(delivery.get("end_x"), start_x)
    end_y = _safe_float(delivery.get("end_y"), start_y)
    if start_x is None or start_y is None or end_x is None or end_y is None:
        return 0.0
    return float(math.hypot(end_x - start_x, end_y - start_y))


def _set_piece_post_target(sequence: dict[str, Any]) -> str:
    delivery = sequence.get("delivery") if isinstance(sequence.get("delivery"), dict) else {}
    start_y = _safe_float(delivery.get("start_y"))
    end_x = _safe_float(delivery.get("end_x"))
    end_y = _safe_float(delivery.get("end_y"))
    if start_y is None or end_x is None or end_y is None:
        return "unknown"
    if not _in_penalty_box_xy(end_x, end_y):
        return "outside box"
    if 39.0 <= end_y <= 61.0:
        return "central box"
    if start_y <= 50.0:
        return "near post" if end_y <= 39.0 else "far post"
    return "near post" if end_y >= 61.0 else "far post"


def _set_piece_routine_detail(sequence: dict[str, Any]) -> dict[str, Any]:
    restart_type = str(sequence.get("restart_type", "Set piece"))
    delivery = sequence.get("delivery") if isinstance(sequence.get("delivery"), dict) else {}
    distance = _set_piece_delivery_distance(sequence)
    end_x = _safe_float(delivery.get("end_x"))
    end_y = _safe_float(delivery.get("end_y"))
    post_target = _set_piece_post_target(sequence)
    target_zone = str(delivery.get("target_zone", "Unknown zone"))
    box_delivery = bool(end_x is not None and end_y is not None and _in_penalty_box_xy(end_x, end_y))
    led_to_shot = bool(sequence.get("led_to_shot", False))
    led_to_goal = bool(sequence.get("led_to_goal", False))
    first_contact_won = bool(sequence.get("first_contact_won", False))
    second_ball_retained = bool(sequence.get("second_ball_retained", False))

    if restart_type == "Penalty":
        family = "Penalty"
        label = "Penalty"
        delivery_pattern = "Direct shot"
    elif restart_type == "Direct free kick":
        family = "Direct free kick"
        label = "Direct free kick shot"
        delivery_pattern = "Direct shot"
    elif restart_type == "Corner":
        family = "Corner routine"
        if distance <= 12.0:
            label = "Short corner"
            delivery_pattern = "Short corner"
        elif box_delivery:
            label = f"Corner to {post_target}"
            delivery_pattern = "Box delivery"
        else:
            label = "Corner recycled outside box"
            delivery_pattern = "Recycled delivery"
    elif restart_type == "Wide free kick":
        family = "Wide free kick routine"
        if box_delivery:
            label = f"Wide free kick to {post_target}"
            delivery_pattern = "Box delivery"
        elif distance <= 10.0:
            label = "Short wide free kick"
            delivery_pattern = "Short routine"
        else:
            label = "Wide free kick recycled outside box"
            delivery_pattern = "Recycled delivery"
    elif restart_type == "Final third throw in":
        family = "Throw in routine"
        if box_delivery and distance >= 16.0:
            label = "Long throw into box"
            delivery_pattern = "Long throw"
        elif distance <= 8.0:
            label = "Short throw routine"
            delivery_pattern = "Short routine"
        else:
            label = "Final third throw continuation"
            delivery_pattern = "Continuation"
    elif restart_type == "Throw in":
        family = "Throw in routine"
        label = "Throw in continuation"
        delivery_pattern = "Continuation"
    elif restart_type == "Free kick":
        family = "Free kick routine"
        label = "Free kick continuation"
        delivery_pattern = "Continuation"
    else:
        family = "Set piece routine"
        label = "Set piece continuation"
        delivery_pattern = "Continuation"

    outcome_parts: list[str] = []
    if led_to_goal:
        outcome_parts.append("goal")
    elif led_to_shot:
        outcome_parts.append("shot")
    if first_contact_won:
        outcome_parts.append("first contact won")
    if second_ball_retained:
        outcome_parts.append("second ball retained")

    return {
        "routine_family": family,
        "routine_label": label,
        "routine_key": _safe_slug(label).lower(),
        "delivery_pattern": delivery_pattern,
        "target_pattern": post_target,
        "target_zone": target_zone,
        "delivery_distance": round(distance, 2),
        "routine_outcome": ", ".join(outcome_parts) if outcome_parts else "no shot",
        "swing_note": "Inswing or outswing cannot be confirmed from this event file without taker footedness." if restart_type in {"Corner", "Wide free kick"} else "",
    }


def _set_piece_routine_groups(sequences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}

    for sequence in sequences:
        routine = _set_piece_routine_detail(sequence)
        label = str(routine.get("routine_label", "Set piece continuation"))
        key = str(routine.get("routine_key", _safe_slug(label).lower()))
        current = groups.get(key)
        if current is None:
            current = {
                "routine_key": key,
                "routine_label": label,
                "routine_family": routine.get("routine_family", "Set piece routine"),
                "delivery_pattern": routine.get("delivery_pattern", "Continuation"),
                "target_pattern": routine.get("target_pattern", "unknown"),
                "count": 0,
                "shots": 0,
                "goals": 0,
                "first_contact_won": 0,
                "second_ball_retained": 0,
                "cleared_or_blocked": 0,
                "delivery_distance_total": 0.0,
                "target_zones": {},
                "examples": [],
                "swing_note": routine.get("swing_note", ""),
            }
            groups[key] = current

        current["count"] += 1
        current["shots"] += 1 if bool(sequence.get("led_to_shot", False)) else 0
        current["goals"] += 1 if bool(sequence.get("led_to_goal", False)) else 0
        current["first_contact_won"] += 1 if bool(sequence.get("first_contact_won", False)) else 0
        current["second_ball_retained"] += 1 if bool(sequence.get("second_ball_retained", False)) else 0
        current["cleared_or_blocked"] += 1 if bool(sequence.get("cleared_or_blocked", False)) else 0
        current["delivery_distance_total"] += float(routine.get("delivery_distance", 0.0) or 0.0)

        target_zone = str(routine.get("target_zone", "Unknown zone"))
        zones = current["target_zones"] if isinstance(current.get("target_zones"), dict) else {}
        zones[target_zone] = int(zones.get(target_zone, 0)) + 1
        current["target_zones"] = zones

        examples = current["examples"] if isinstance(current.get("examples"), list) else []
        if len(examples) < 4:
            delivery = sequence.get("delivery") if isinstance(sequence.get("delivery"), dict) else {}
            examples.append(
                {
                    "sequence_id": sequence.get("sequence_id"),
                    "minute": sequence.get("minute"),
                    "restart_type": sequence.get("restart_type"),
                    "taker": sequence.get("taker"),
                    "target_zone": target_zone,
                    "led_to_shot": bool(sequence.get("led_to_shot", False)),
                    "led_to_goal": bool(sequence.get("led_to_goal", False)),
                    "first_contact_won": bool(sequence.get("first_contact_won", False)),
                    "delivery": delivery,
                }
            )
            current["examples"] = examples

    rows: list[dict[str, Any]] = []
    for group in groups.values():
        count = int(group.get("count", 0) or 0)
        group["shot_rate"] = _rate(int(group.get("shots", 0) or 0), count)
        group["goal_rate"] = _rate(int(group.get("goals", 0) or 0), count)
        group["first_contact_win_rate"] = _rate(int(group.get("first_contact_won", 0) or 0), count)
        group["average_delivery_distance"] = round(float(group.pop("delivery_distance_total", 0.0) or 0.0) / count, 2) if count else 0.0
        zones = group.get("target_zones") if isinstance(group.get("target_zones"), dict) else {}
        group["top_target_zones"] = [
            {"zone": zone, "count": value}
            for zone, value in sorted(zones.items(), key=lambda item: item[1], reverse=True)[:4]
        ]
        rows.append(group)

    rows.sort(key=lambda item: (int(item.get("goals", 0)), int(item.get("shots", 0)), int(item.get("count", 0))), reverse=True)
    return rows

def _set_piece_restart_rows(events: pd.DataFrame, team_name: str) -> pd.DataFrame:
    if events.empty:
        return events.copy()

    team_norm = _norm_team_name(team_name)
    frame = events.loc[events["team_norm"].eq(team_norm)].copy()
    if frame.empty:
        return frame

    mask = frame.apply(_is_set_piece_restart, axis=1)
    return frame.loc[mask].copy()


def _set_piece_sequence_actions(ordered: pd.DataFrame, start_index: int, max_seconds: float = 20.0, max_actions: int = 14) -> list[pd.Series]:
    if ordered.empty or start_index < 0 or start_index >= len(ordered):
        return []

    start = ordered.iloc[start_index]
    start_time = _event_seconds(start)
    start_period = start.get("period")
    actions: list[pd.Series] = []

    for idx in range(start_index, len(ordered)):
        row = ordered.iloc[idx]
        if row.get("period") != start_period:
            break

        elapsed = _event_seconds(row) - start_time
        if elapsed < -0.01:
            continue
        if elapsed > max_seconds:
            break

        if idx > start_index and _is_set_piece_restart(row):
            break

        actions.append(row)

        if idx > start_index and _is_stoppage_event(row) and not bool(row.get("is_set_piece", False)):
            break

        if len(actions) >= max_actions:
            break

    return actions


def _set_piece_sequence_from_actions(actions: list[pd.Series], restarting_team: str, defending_team: str, sequence_number: int) -> dict[str, Any] | None:
    if not actions:
        return None

    restart = actions[0]
    restart_type = _set_piece_type(restart)
    restart_team_norm = _norm_team_name(restarting_team)
    defending_team_norm = _norm_team_name(defending_team)

    meaningful_after = [
        action
        for action in actions[1:]
        if bool(action.get("is_touch", False))
        or bool(action.get("is_pass", False))
        or bool(action.get("is_cross", False))
        or bool(action.get("is_carry", False))
        or bool(action.get("is_shot", False))
        or bool(action.get("is_defensive_action", False))
    ]

    first_contact_row = meaningful_after[0] if meaningful_after else None
    second_ball_row = meaningful_after[1] if len(meaningful_after) >= 2 else None

    first_contact_team_norm = _norm_team_name(first_contact_row.get("team", "")) if first_contact_row is not None else ""
    second_ball_team_norm = _norm_team_name(second_ball_row.get("team", "")) if second_ball_row is not None else ""

    attacking_shots = [action for action in actions if _norm_team_name(action.get("team", "")) == restart_team_norm and bool(action.get("is_shot", False))]
    attacking_goals = [action for action in actions if _norm_team_name(action.get("team", "")) == restart_team_norm and bool(action.get("is_goal", False))]
    defending_clearances = [
        action for action in actions
        if _norm_team_name(action.get("team", "")) == defending_team_norm
        and ("clearance" in _set_piece_text(action) or "block" in _set_piece_text(action) or bool(action.get("is_defensive_action", False)))
    ]

    action_rows = []
    for order, action in enumerate(actions, start=1):
        action_rows.append(
            {
                "order": order,
                "event_index": int(action.get("event_index", 0)) if pd.notna(action.get("event_index")) else None,
                "minute": round(float(action.get("expanded_minute", 0.0)), 2) if pd.notna(action.get("expanded_minute")) else None,
                "team": str(action.get("team", "")),
                "player": str(action.get("player", "")),
                "type": str(action.get("type", "")),
                "outcome_type": str(action.get("outcome_type", "")),
                "x": round(float(action.get("x")), 2) if pd.notna(action.get("x")) else None,
                "y": round(float(action.get("y")), 2) if pd.notna(action.get("y")) else None,
                "end_x": round(float(action.get("end_x")), 2) if pd.notna(action.get("end_x")) else None,
                "end_y": round(float(action.get("end_y")), 2) if pd.notna(action.get("end_y")) else None,
                "is_shot": bool(action.get("is_shot", False)),
                "is_goal": bool(action.get("is_goal", False)),
                "is_set_piece": bool(action.get("is_set_piece", False)),
                "label": _event_label(action),
            }
        )

    delivery = {
        "event_index": int(restart.get("event_index", 0)) if pd.notna(restart.get("event_index")) else None,
        "minute": round(float(restart.get("expanded_minute", 0.0)), 2) if pd.notna(restart.get("expanded_minute")) else None,
        "start_x": round(float(restart.get("x")), 2) if pd.notna(restart.get("x")) else None,
        "start_y": round(float(restart.get("y")), 2) if pd.notna(restart.get("y")) else None,
        "end_x": round(float(restart.get("end_x")), 2) if pd.notna(restart.get("end_x")) else None,
        "end_y": round(float(restart.get("end_y")), 2) if pd.notna(restart.get("end_y")) else None,
        "player": str(restart.get("player", "")),
        "team": str(restart.get("team", "")),
        "event_type": str(restart.get("type", "")),
        "set_piece_type": restart_type,
        "successful": bool(restart.get("is_success", False)),
        "delivery_zone": _set_piece_zone_label(restart.get("x"), restart.get("y")),
        "target_zone": _set_piece_zone_label(restart.get("end_x"), restart.get("end_y")),
    }

    sequence: dict[str, Any] = {
        "sequence_id": f"{int(restart.get('match_id', 0))}:sp:{int(restart.get('event_index', sequence_number))}",
        "restart_type": restart_type,
        "attacking_team": restarting_team,
        "defending_team": defending_team,
        "minute": delivery["minute"],
        "taker": str(restart.get("player", "")),
        "delivery": delivery,
        "first_contact": _serialise_event_brief(first_contact_row),
        "second_ball": _serialise_event_brief(second_ball_row),
        "final_action": _serialise_event_brief(actions[-1]),
        "first_contact_won": first_contact_team_norm == restart_team_norm if first_contact_row is not None else False,
        "second_ball_retained": second_ball_team_norm == restart_team_norm if second_ball_row is not None else False,
        "led_to_shot": len(attacking_shots) > 0,
        "led_to_goal": len(attacking_goals) > 0,
        "cleared_or_blocked": len(defending_clearances) > 0,
        "shot": _serialise_event_brief(attacking_shots[0]) if attacking_shots else None,
        "goal": _serialise_event_brief(attacking_goals[0]) if attacking_goals else None,
        "actions": action_rows,
        "action_count": len(action_rows),
    }
    sequence.update(_set_piece_routine_detail(sequence))
    sequence["tag"] = _set_piece_tag(sequence)
    return sequence


def _set_piece_sequences_for_team(all_events: pd.DataFrame, restarting_team: str, defending_team: str) -> list[dict[str, Any]]:
    if all_events.empty:
        return []

    ordered = _sort_events_by_match_time(all_events, ["expanded_minute", "event_index"]).reset_index(drop=True)
    restart_team_norm = _norm_team_name(restarting_team)
    sequences: list[dict[str, Any]] = []

    for idx, row in ordered.iterrows():
        if _norm_team_name(row.get("team", "")) != restart_team_norm:
            continue
        if not _is_set_piece_restart(row):
            continue

        actions = _set_piece_sequence_actions(ordered, int(idx))
        sequence = _set_piece_sequence_from_actions(actions, restarting_team, defending_team, len(sequences) + 1)
        if sequence is not None:
            sequences.append(sequence)

    return sequences


def _set_piece_type_counts(sequences: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {
        "corners": 0,
        "wide_free_kicks": 0,
        "direct_free_kicks": 0,
        "free_kicks": 0,
        "final_third_throw_ins": 0,
        "throw_ins": 0,
        "penalties": 0,
    }

    for sequence in sequences:
        restart_type = str(sequence.get("restart_type", ""))
        if restart_type == "Corner":
            counts["corners"] += 1
        elif restart_type == "Wide free kick":
            counts["wide_free_kicks"] += 1
        elif restart_type == "Direct free kick":
            counts["direct_free_kicks"] += 1
        elif restart_type == "Free kick":
            counts["free_kicks"] += 1
        elif restart_type == "Final third throw in":
            counts["final_third_throw_ins"] += 1
        elif restart_type == "Throw in":
            counts["throw_ins"] += 1
        elif restart_type == "Penalty":
            counts["penalties"] += 1

    return counts


def _set_piece_summary(attacking_sequences: list[dict[str, Any]], defensive_sequences: list[dict[str, Any]]) -> dict[str, Any]:
    attack_counts = _set_piece_type_counts(attacking_sequences)
    defensive_counts = _set_piece_type_counts(defensive_sequences)

    attacking_shots = sum(1 for sequence in attacking_sequences if bool(sequence.get("led_to_shot", False)))
    attacking_goals = sum(1 for sequence in attacking_sequences if bool(sequence.get("led_to_goal", False)))
    defensive_shots = sum(1 for sequence in defensive_sequences if bool(sequence.get("led_to_shot", False)))
    defensive_goals = sum(1 for sequence in defensive_sequences if bool(sequence.get("led_to_goal", False)))

    attack_total = len(attacking_sequences)
    defensive_total = len(defensive_sequences)

    return {
        "attacking_set_pieces": attack_total,
        "defensive_set_pieces_faced": defensive_total,
        **attack_counts,
        "defensive_corners_faced": defensive_counts["corners"],
        "defensive_wide_free_kicks_faced": defensive_counts["wide_free_kicks"],
        "defensive_direct_free_kicks_faced": defensive_counts["direct_free_kicks"],
        "defensive_final_third_throw_ins_faced": defensive_counts["final_third_throw_ins"],
        "set_piece_shots": attacking_shots,
        "set_piece_goals": attacking_goals,
        "set_piece_shot_rate": _rate(attacking_shots, attack_total),
        "set_piece_goal_rate": _rate(attacking_goals, attack_total),
        "shots_conceded_from_set_pieces": defensive_shots,
        "goals_conceded_from_set_pieces": defensive_goals,
        "defensive_shot_concession_rate": _rate(defensive_shots, defensive_total),
        "defensive_goal_concession_rate": _rate(defensive_goals, defensive_total),
    }


def _set_piece_delivery_map(sequences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for sequence in sequences:
        delivery = sequence.get("delivery") if isinstance(sequence.get("delivery"), dict) else {}
        start_x = _safe_float(delivery.get("start_x"))
        start_y = _safe_float(delivery.get("start_y"))
        end_x = _safe_float(delivery.get("end_x"))
        end_y = _safe_float(delivery.get("end_y"))

        if start_x is None or start_y is None:
            continue

        if end_x is None or end_y is None:
            end_x = start_x
            end_y = start_y

        rows.append(
            {
                "event_index": delivery.get("event_index"),
                "sequence_id": sequence.get("sequence_id"),
                "start_x": round(float(start_x), 2),
                "start_y": round(float(start_y), 2),
                "end_x": round(float(end_x), 2),
                "end_y": round(float(end_y), 2),
                "minute": delivery.get("minute"),
                "player": str(delivery.get("player", "")),
                "event_type": str(delivery.get("event_type", "")),
                "set_piece_type": str(sequence.get("restart_type", "Set piece")),
                "routine_label": str(sequence.get("routine_label", "Set piece continuation")),
                "routine_family": str(sequence.get("routine_family", "Set piece routine")),
                "delivery_pattern": str(sequence.get("delivery_pattern", "Continuation")),
                "target_pattern": str(sequence.get("target_pattern", "unknown")),
                "delivery_distance": float(sequence.get("delivery_distance", 0.0) or 0.0),
                "successful": bool(delivery.get("successful", False)),
                "led_to_shot": bool(sequence.get("led_to_shot", False)),
                "led_to_goal": bool(sequence.get("led_to_goal", False)),
                "target_zone": str(delivery.get("target_zone", "Unknown zone")),
                "label": f"{delivery.get('player', '')} {sequence.get('routine_label', sequence.get('restart_type', ''))}",
            }
        )

    rows.sort(key=lambda item: (not bool(item["led_to_goal"]), not bool(item["led_to_shot"]), float(item.get("minute") or 0.0)))
    return rows[:160]


def _set_piece_contact_panel(attacking_sequences: list[dict[str, Any]], defensive_sequences: list[dict[str, Any]]) -> dict[str, Any]:
    attacking_first_contact_won = sum(1 for sequence in attacking_sequences if bool(sequence.get("first_contact_won", False)))
    attacking_first_contact_lost = sum(1 for sequence in attacking_sequences if sequence.get("first_contact") and not bool(sequence.get("first_contact_won", False)))
    attacking_second_ball_retained = sum(1 for sequence in attacking_sequences if bool(sequence.get("second_ball_retained", False)))
    attacking_shot_after_first_contact = sum(1 for sequence in attacking_sequences if bool(sequence.get("first_contact_won", False)) and bool(sequence.get("led_to_shot", False)))

    defensive_first_contact_won = sum(1 for sequence in defensive_sequences if sequence.get("first_contact") and not bool(sequence.get("first_contact_won", False)))
    defensive_first_contact_lost = sum(1 for sequence in defensive_sequences if bool(sequence.get("first_contact_won", False)))
    defensive_second_ball_won = sum(1 for sequence in defensive_sequences if sequence.get("second_ball") and not bool(sequence.get("second_ball_retained", False)))
    defensive_clearances_or_blocks = sum(1 for sequence in defensive_sequences if bool(sequence.get("cleared_or_blocked", False)))

    return {
        "attacking": {
            "first_contact_won": attacking_first_contact_won,
            "first_contact_lost": attacking_first_contact_lost,
            "first_contact_win_rate": _rate(attacking_first_contact_won, attacking_first_contact_won + attacking_first_contact_lost),
            "second_ball_retained": attacking_second_ball_retained,
            "shot_after_first_contact": attacking_shot_after_first_contact,
        },
        "defensive": {
            "first_contact_won": defensive_first_contact_won,
            "first_contact_lost": defensive_first_contact_lost,
            "first_contact_win_rate": _rate(defensive_first_contact_won, defensive_first_contact_won + defensive_first_contact_lost),
            "second_ball_won": defensive_second_ball_won,
            "clearances_or_blocks": defensive_clearances_or_blocks,
        },
    }


def _set_piece_involvement(attacking_sequences: list[dict[str, Any]], defensive_sequences: list[dict[str, Any]], team_name: str) -> dict[str, Any]:
    team_norm = _norm_team_name(team_name)

    takers: dict[str, int] = {}
    first_contacts: dict[str, int] = {}
    shot_takers: dict[str, int] = {}
    defensive_clearers: dict[str, int] = {}
    blockers: dict[str, int] = {}

    def add(target: dict[str, int], player: object) -> None:
        name = str(player or "").strip()
        if not name:
            return
        target[name] = target.get(name, 0) + 1

    for sequence in attacking_sequences:
        add(takers, sequence.get("taker"))
        first_contact = sequence.get("first_contact") if isinstance(sequence.get("first_contact"), dict) else None
        if first_contact and _norm_team_name(first_contact.get("team", "")) == team_norm:
            add(first_contacts, first_contact.get("player"))
        shot = sequence.get("shot") if isinstance(sequence.get("shot"), dict) else None
        if shot:
            add(shot_takers, shot.get("player"))

    for sequence in defensive_sequences:
        actions = sequence.get("actions") if isinstance(sequence.get("actions"), list) else []
        for action in actions:
            if _norm_team_name(action.get("team", "")) != team_norm:
                continue
            text = f"{action.get('type', '')} {action.get('outcome_type', '')}".lower()
            if "clearance" in text:
                add(defensive_clearers, action.get("player"))
            if "block" in text:
                add(blockers, action.get("player"))
            if action.get("is_set_piece"):
                continue
            if "aerial" in text or "duel" in text:
                add(first_contacts, action.get("player"))

    def rows(source: dict[str, int], value_key: str) -> list[dict[str, Any]]:
        return [
            {"player": player, value_key: count}
            for player, count in sorted(source.items(), key=lambda item: item[1], reverse=True)[:8]
        ]

    return {
        "takers": rows(takers, "set_pieces_taken"),
        "first_contact_players": rows(first_contacts, "first_contacts"),
        "shot_takers": rows(shot_takers, "set_piece_shots"),
        "defensive_clearers": rows(defensive_clearers, "clearances"),
        "blockers": rows(blockers, "blocks"),
    }


def _set_piece_heatmaps(sequences: list[dict[str, Any]]) -> dict[str, Any]:
    shot_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []

    for sequence in sequences:
        delivery = sequence.get("delivery") if isinstance(sequence.get("delivery"), dict) else {}
        target_x = _safe_float(delivery.get("end_x"))
        target_y = _safe_float(delivery.get("end_y"))
        if target_x is not None and target_y is not None:
            target_rows.append(
                {
                    "x": target_x,
                    "y": target_y,
                    "expanded_minute": delivery.get("minute"),
                    "player": delivery.get("player", ""),
                    "type": sequence.get("restart_type", ""),
                    "is_goal": bool(sequence.get("led_to_goal", False)),
                }
            )

        shot = sequence.get("shot") if isinstance(sequence.get("shot"), dict) else None
        if shot and shot.get("x") is not None and shot.get("y") is not None:
            shot_rows.append(
                {
                    "x": shot.get("x"),
                    "y": shot.get("y"),
                    "expanded_minute": shot.get("minute"),
                    "player": shot.get("player", ""),
                    "type": shot.get("type", "Shot"),
                    "is_goal": bool(sequence.get("led_to_goal", False)),
                }
            )

    return {
        "delivery_targets": _binned_heatmap(pd.DataFrame(target_rows)),
        "set_piece_shots": _binned_heatmap(pd.DataFrame(shot_rows)),
    }


def _set_piece_interpretation(team_name: str, opponent_team: str, summary: dict[str, Any], contacts: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    shot_rate = float(summary.get("set_piece_shot_rate", 0.0))
    defensive_shot_rate = float(summary.get("defensive_shot_concession_rate", 0.0))
    attacking_contact = contacts.get("attacking", {}) if isinstance(contacts.get("attacking", {}), dict) else {}
    defensive_contact = contacts.get("defensive", {}) if isinstance(contacts.get("defensive", {}), dict) else {}
    first_contact_rate = float(attacking_contact.get("first_contact_win_rate", 0.0))
    defensive_contact_rate = float(defensive_contact.get("first_contact_win_rate", 0.0))

    if int(summary.get("attacking_set_pieces", 0)) == 0:
        lines.append(f"{team_name} had no recorded attacking set pieces in this match.")
    elif shot_rate >= 25.0:
        lines.append(f"{team_name} turned restarts into shots at a strong rate, with {shot_rate:.1f} percent of attacking set pieces ending in a shot.")
    else:
        lines.append(f"{team_name} had restart volume, but only {shot_rate:.1f} percent became shots, so the threat was controlled rather than sustained.")

    if int(summary.get("defensive_set_pieces_faced", 0)) == 0:
        lines.append(f"{team_name} did not face a recorded defensive set piece sequence from {opponent_team}.")
    elif defensive_shot_rate <= 15.0:
        lines.append(f"Defensively, {team_name} restricted restart danger well, allowing shots from {defensive_shot_rate:.1f} percent of set pieces faced.")
    else:
        lines.append(f"Defensively, {team_name} allowed too many restarts to survive into shots, with a concession rate of {defensive_shot_rate:.1f} percent.")

    if first_contact_rate >= 55.0:
        lines.append(f"First contact was a useful attacking platform, won on {first_contact_rate:.1f} percent of recorded deliveries.")
    elif int(summary.get("attacking_set_pieces", 0)) > 0:
        lines.append(f"The main attacking issue was first contact, won on only {first_contact_rate:.1f} percent of recorded deliveries.")

    if defensive_contact_rate >= 55.0:
        lines.append(f"On defensive restarts, first contact protection was stable at {defensive_contact_rate:.1f} percent.")
    elif int(summary.get("defensive_set_pieces_faced", 0)) > 0:
        lines.append(f"On defensive restarts, first contact protection was the pressure point, with {defensive_contact_rate:.1f} percent won.")

    return lines


def _build_set_piece_analysis(team_name: str, opponent_team: str, all_events: pd.DataFrame) -> dict[str, Any]:
    attacking_sequences = _set_piece_sequences_for_team(all_events, team_name, opponent_team)
    defensive_sequences = _set_piece_sequences_for_team(all_events, opponent_team, team_name)
    summary = _set_piece_summary(attacking_sequences, defensive_sequences)
    contacts = _set_piece_contact_panel(attacking_sequences, defensive_sequences)

    return {
        "team": team_name,
        "opponent_team": opponent_team,
        "summary": summary,
        "attacking_threat": {
            "sequences": attacking_sequences[:20],
            "dangerous_sequences": [sequence for sequence in attacking_sequences if bool(sequence.get("led_to_shot", False)) or bool(sequence.get("led_to_goal", False))][:12],
            "heatmaps": _set_piece_heatmaps(attacking_sequences),
        },
        "defensive_protection": {
            "sequences": defensive_sequences[:20],
            "dangerous_sequences_conceded": [sequence for sequence in defensive_sequences if bool(sequence.get("led_to_shot", False)) or bool(sequence.get("led_to_goal", False))][:12],
            "heatmaps": _set_piece_heatmaps(defensive_sequences),
        },
        "delivery_map": _set_piece_delivery_map(attacking_sequences),
        "defensive_delivery_map": _set_piece_delivery_map(defensive_sequences),
        "routine_groups": _set_piece_routine_groups(attacking_sequences),
        "defensive_routine_groups": _set_piece_routine_groups(defensive_sequences),
        "first_contact_and_second_ball": contacts,
        "involvement": _set_piece_involvement(attacking_sequences, defensive_sequences, team_name),
        "interpretation": _set_piece_interpretation(team_name, opponent_team, summary, contacts),
        "audit_note": "Set piece analysis is sequence based. It separates restart volume from delivery quality, first contact, second ball pressure and whether the sequence survived into a shot.",
    }

def _fixture_context_from_schedule(schedule_df: pd.DataFrame, match_id: int, team_name: str) -> dict[str, Any]:
    match_col = _match_id_col(schedule_df)
    home_col, away_col = _home_away_cols(schedule_df)
    if not match_col or not home_col or not away_col:
        return {"opponent": "", "venue": "", "scoreline": "", "match_date": "", "status": ""}

    mask = pd.to_numeric(schedule_df[match_col], errors="coerce").eq(int(match_id))
    if not mask.any():
        return {"opponent": "", "venue": "", "scoreline": "", "match_date": "", "status": ""}

    row = schedule_df.loc[mask].iloc[0]
    home_team = str(row.get(home_col, "") or "").strip()
    away_team = str(row.get(away_col, "") or "").strip()
    is_home = _norm_team_name(home_team) == _norm_team_name(team_name)
    opponent = away_team if is_home else home_team
    venue = "Home" if is_home else "Away"
    home_score = _score_value(row, "home")
    away_score = _score_value(row, "away")
    scoreline = "" if home_score is None or away_score is None else f"{home_score}-{away_score}"

    return {
        "opponent": opponent,
        "venue": venue,
        "scoreline": scoreline,
        "home_team": home_team,
        "away_team": away_team,
        "match_date": _schedule_time_value(row),
        "status": _schedule_status_value(row),
    }


def _match_context_metrics(match_events: pd.DataFrame | None, team_name: str) -> dict[str, Any]:
    if match_events is None or match_events.empty:
        return {
            "possession_pct": None,
            "opponent_shots": None,
            "opponent_red_cards": None,
            "opponent_passes": None,
        }

    frame = _ensure_flags(match_events.copy())
    team_norm = _norm_team_name(team_name)
    team_frame = frame.loc[frame["team_norm"].eq(team_norm)].copy()
    opponent_frame = frame.loc[~frame["team_norm"].eq(team_norm)].copy()

    team_passes = int((team_frame["is_pass"] | team_frame["is_cross"]).sum()) if not team_frame.empty else 0
    opponent_passes = int((opponent_frame["is_pass"] | opponent_frame["is_cross"]).sum()) if not opponent_frame.empty else 0
    possession_pct = round((team_passes / max(team_passes + opponent_passes, 1)) * 100.0, 1) if team_passes + opponent_passes > 0 else None

    return {
        "possession_pct": possession_pct,
        "opponent_shots": int(opponent_frame["is_shot"].sum()) if not opponent_frame.empty else 0,
        "opponent_red_cards": int(opponent_frame["is_red_card"].sum()) if not opponent_frame.empty and "is_red_card" in opponent_frame.columns else 0,
        "opponent_passes": opponent_passes,
    }


def _team_match_summary(group: pd.DataFrame) -> dict[str, Any]:
    return {
        "shots": int(group["is_shot"].sum()) if "is_shot" in group.columns else 0,
        "goals": int(group["is_goal"].sum()) if "is_goal" in group.columns else 0,
        "final_third_entries": int(group["final_third_entry"].sum()) if "final_third_entry" in group.columns else 0,
        "box_entries": int(group["box_entry"].sum()) if "box_entry" in group.columns else 0,
        "crosses": int(group["is_cross"].sum()) if "is_cross" in group.columns else 0,
        "red_cards_for": int(group["is_red_card"].sum()) if "is_red_card" in group.columns else 0,
        "passes": int((group["is_pass"] | group["is_cross"]).sum()) if "is_pass" in group.columns and "is_cross" in group.columns else 0,
    }


def _recent_team_patterns(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    schedule_df: pd.DataFrame,
    all_team_events: pd.DataFrame,
    selected_events: pd.DataFrame,
    selected_match_id: int,
    selected_kickoff: str,
) -> dict[str, Any]:
    if all_team_events.empty:
        return {"available": False, "reason": "No processed season event store found."}

    team_name = str(selected_events["team"].dropna().iloc[0]) if not selected_events.empty and selected_events["team"].notna().any() else ""
    if not team_name:
        return {"available": False, "reason": "Team could not be resolved."}

    team_events = all_team_events.loc[all_team_events["team_norm"].eq(_norm_team_name(team_name))].copy()
    if team_events.empty:
        return {"available": False, "reason": "No season events found for this team."}

    team_events = _ensure_flags(team_events)

    if "match_date" in team_events.columns:
        team_events["_match_dt"] = pd.to_datetime(team_events["match_date"], errors="coerce", utc=True)
    else:
        team_events["_match_dt"] = pd.NaT

    selected_dt = pd.to_datetime(selected_kickoff, errors="coerce", utc=True)

    matches = []
    for match_id, group in team_events.groupby("match_id", dropna=True):
        match_id_int = int(match_id)
        match_dt = group["_match_dt"].dropna().min() if group["_match_dt"].notna().any() else pd.NaT
        if match_id_int == int(selected_match_id):
            continue
        if pd.notna(selected_dt) and pd.notna(match_dt) and match_dt >= selected_dt:
            continue
        matches.append({"match_id": match_id_int, "match_date": match_dt, "sort_key": match_dt if pd.notna(match_dt) else pd.Timestamp(match_id_int, unit="s", tz="UTC")})

    matches = sorted(matches, key=lambda item: item["sort_key"], reverse=True)[:5]
    if not matches:
        return {"available": False, "reason": "No previous matches available before the selected fixture."}

    recent_ids = [item["match_id"] for item in matches]
    recent = team_events.loc[team_events["match_id"].isin(recent_ids)].copy()

    def by_match_metric(mask_col: str) -> float:
        grouped = recent.groupby("match_id")[mask_col].sum()
        return round(float(grouped.mean()), 2) if not grouped.empty else 0.0

    selected = selected_events.copy()
    selected_summary = _team_match_summary(selected)
    recent_average = {
        "shots": by_match_metric("is_shot"),
        "goals": by_match_metric("is_goal"),
        "final_third_entries": by_match_metric("final_third_entry"),
        "box_entries": by_match_metric("box_entry"),
        "crosses": by_match_metric("is_cross"),
        "red_cards_for": by_match_metric("is_red_card") if "is_red_card" in recent.columns else 0.0,
    }

    match_rows: list[dict[str, Any]] = []
    for item in matches:
        mid = int(item["match_id"])
        group = recent.loc[pd.to_numeric(recent["match_id"], errors="coerce").eq(mid)].copy()
        fixture_context = _fixture_context_from_schedule(schedule_df, mid, team_name)
        try:
            full_match = load_processed_match_events(basedir, nation, tier, season, mid)
        except Exception:
            full_match = None
        context = _match_context_metrics(full_match, team_name)
        match_rows.append(
            {
                "match_id": mid,
                **fixture_context,
                **_team_match_summary(group),
                **context,
            }
        )

    try:
        selected_full_match = load_processed_match_events(basedir, nation, tier, season, int(selected_match_id))
    except Exception:
        selected_full_match = None

    selected_context = {
        "match_id": int(selected_match_id),
        **_fixture_context_from_schedule(schedule_df, int(selected_match_id), team_name),
        **selected_summary,
        **_match_context_metrics(selected_full_match, team_name),
    }

    return {
        "available": True,
        "team": team_name,
        "match_count": len(recent_ids),
        "recent_match_ids": recent_ids,
        "selected_match": selected_summary,
        "selected_context": selected_context,
        "recent_average": recent_average,
        "recent_matches": match_rows,
        "note": "Possession is estimated from pass share in the event feed. Red cards use event tags and may depend on provider card quality.",
    }


def _recent_patterns_from_processed(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    selected_fixture: dict[str, Any],
    home_events: pd.DataFrame,
    away_events: pd.DataFrame,
) -> dict[str, Any]:
    try:
        from app.services.processed_event_store import load_processed_team_events
        home_all = load_processed_team_events(basedir, nation, tier, season, str(selected_fixture["home_team"]))
        away_all = load_processed_team_events(basedir, nation, tier, season, str(selected_fixture["away_team"]))
    except Exception:
        home_all = None
        away_all = None

    schedule_df = load_schedule_frame(basedir, nation=nation, tier=tier, season=season)

    return {
        "home": _recent_team_patterns(
            basedir,
            nation,
            tier,
            season,
            schedule_df,
            home_all if home_all is not None else pd.DataFrame(),
            home_events,
            int(selected_fixture["match_id"]),
            str(selected_fixture.get("kickoff") or ""),
        ),
        "away": _recent_team_patterns(
            basedir,
            nation,
            tier,
            season,
            schedule_df,
            away_all if away_all is not None else pd.DataFrame(),
            away_events,
            int(selected_fixture["match_id"]),
            str(selected_fixture.get("kickoff") or ""),
        ),
    }



def _momentum_bucket(minute: float | None) -> str:
    if minute is None:
        return "unknown"
    value = float(minute)
    if value <= 15.0:
        return "0-15"
    if value <= 30.0:
        return "16-30"
    if value <= 45.0:
        return "31-45"
    if value <= 60.0:
        return "46-60"
    if value <= 75.0:
        return "61-75"
    if value <= 90.0:
        return "76-90"
    return "90+"


def _momentum_bucket_range(bucket: str) -> tuple[float, float]:
    ranges = {
        "0-15": (0.0, 15.0),
        "16-30": (16.0, 30.0),
        "31-45": (31.0, 45.0),
        "46-60": (46.0, 60.0),
        "61-75": (61.0, 75.0),
        "76-90": (76.0, 90.0),
        "90+": (91.0, 130.0),
    }
    return ranges.get(bucket, (0.0, 130.0))


def _momentum_interval_profile(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = ["0-15", "16-30", "31-45", "46-60", "61-75", "76-90"]
    output: list[dict[str, Any]] = []
    for bucket in buckets:
        start_minute, end_minute = _momentum_bucket_range(bucket)
        bucket_rows = [row for row in rows if start_minute <= float(row.get("minute", 0.0)) <= end_minute]
        if bucket_rows:
            team_value = round(float(sum(float(row.get("team_value", 0.0)) for row in bucket_rows) / len(bucket_rows)), 3)
            opponent_value = round(float(sum(float(row.get("opponent_value", 0.0)) for row in bucket_rows) / len(bucket_rows)), 3)
            net_value = round(float(sum(float(row.get("net", 0.0)) for row in bucket_rows) / len(bucket_rows)), 3)
        else:
            team_value = 0.0
            opponent_value = 0.0
            net_value = 0.0
        output.append(
            {
                "bucket": bucket,
                "start_minute": start_minute,
                "end_minute": end_minute,
                "team_value": team_value,
                "opponent_value": opponent_value,
                "net": net_value,
            }
        )
    return output


def _dedupe_momentum_windows(items: list[dict[str, Any]], value_key: str, reverse: bool, limit: int = 3, min_gap: float = 6.0) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    ranked = sorted(items, key=lambda item: float(item.get(value_key, 0.0)), reverse=reverse)
    for item in ranked:
        minute = _safe_float(item.get("minute"))
        if minute is None:
            continue
        if any(abs(float(minute) - float(_safe_float(prev.get("minute"), 0.0) or 0.0)) < min_gap for prev in selected):
            continue
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def _phase_average(rows: list[dict[str, Any]], start_minute: float, end_minute: float, key: str) -> float:
    values = [float(row.get(key, 0.0)) for row in rows if start_minute <= float(row.get("minute", 0.0)) <= end_minute]
    if not values:
        return 0.0
    return round(float(sum(values) / len(values)), 3)


def _momentum_profile(points: list[dict[str, Any]], markers: list[dict[str, Any]], team_name: str, opponent_name: str, team_side: str, fixture_context: dict[str, Any]) -> dict[str, Any]:
    if not points:
        return {
            "available": False,
            "team": team_name,
            "opponent": opponent_name,
            "reason": "No momentum points were available for this match.",
        }

    team_key = "home" if team_side == "home" else "away"
    opponent_key = "away" if team_key == "home" else "home"
    rows: list[dict[str, Any]] = []

    for point in points:
        minute = _safe_float(point.get("minute"))
        if minute is None:
            continue
        team_value = _safe_float(point.get(team_key), 0.0) or 0.0
        opponent_value = _safe_float(point.get(opponent_key), 0.0) or 0.0
        rows.append(
            {
                "minute": round(float(minute), 2),
                "team_value": round(float(team_value), 3),
                "opponent_value": round(float(opponent_value), 3),
                "net": round(float(team_value) - float(opponent_value), 3),
            }
        )

    if not rows:
        return {
            "available": False,
            "team": team_name,
            "opponent": opponent_name,
            "reason": "Momentum rows could not be resolved for this team side.",
        }

    peak_source = [row for row in rows if float(row.get("team_value", 0.0)) > 0.0]
    trough_source = [row for row in rows if float(row.get("opponent_value", 0.0)) > 0.0 or float(row.get("net", 0.0)) < 0.0]

    peaks = _dedupe_momentum_windows(peak_source, "team_value", reverse=True)
    troughs = _dedupe_momentum_windows(trough_source, "net", reverse=False)

    for item in peaks:
        item["bucket"] = _momentum_bucket(_safe_float(item.get("minute")))
    for item in troughs:
        item["bucket"] = _momentum_bucket(_safe_float(item.get("minute")))

    goals_for: list[dict[str, Any]] = []
    goals_against: list[dict[str, Any]] = []
    red_cards: list[dict[str, Any]] = []

    for marker in markers:
        marker_minute = _safe_float(marker.get("minute"))
        entry = {
            "minute": round(float(marker_minute), 2) if marker_minute is not None else None,
            "bucket": _momentum_bucket(marker_minute),
            "team": str(marker.get("team", "")),
            "player": str(marker.get("player", "")),
            "score_after_event": str(marker.get("score_after_event", "")),
            "marker_type": str(marker.get("marker_type", "")),
        }
        marker_team_side = str(marker.get("team_side", ""))
        if str(marker.get("marker_type", "")) == "red_card":
            red_cards.append(entry)
            continue
        if str(marker.get("marker_type", "")) != "goal":
            continue
        if marker_team_side == team_side:
            goals_for.append(entry)
        else:
            goals_against.append(entry)

    team_total = sum(float(row.get("team_value", 0.0)) for row in rows)
    opponent_total = sum(float(row.get("opponent_value", 0.0)) for row in rows)
    control_share = round((team_total / max(team_total + opponent_total, 1.0)) * 100.0, 1) if team_total + opponent_total > 0 else 0.0

    return {
        "available": True,
        "team": team_name,
        "opponent": opponent_name,
        "team_side": team_side,
        "match_id": fixture_context.get("match_id"),
        "match_date": fixture_context.get("match_date", ""),
        "venue": fixture_context.get("venue", ""),
        "scoreline": fixture_context.get("scoreline", ""),
        "status": fixture_context.get("status", ""),
        "peaks": peaks,
        "troughs": troughs,
        "goals_for": goals_for,
        "goals_against": goals_against,
        "red_cards": red_cards,
        "peak_count": len(peaks),
        "trough_count": len(troughs),
        "highest_peak": round(float(max((row.get("team_value", 0.0) for row in rows), default=0.0)), 3),
        "deepest_trough": round(float(min((row.get("net", 0.0) for row in rows), default=0.0)), 3),
        "control_share_pct": control_share,
        "phase_profile": {
            "start_0_15": _phase_average(rows, 0.0, 15.0, "net"),
            "first_half_16_45": _phase_average(rows, 16.0, 45.0, "net"),
            "second_half_46_75": _phase_average(rows, 46.0, 75.0, "net"),
            "late_76_90": _phase_average(rows, 76.0, 90.0, "net"),
        },
        "fifteen_minute_intervals": _momentum_interval_profile(rows),
    }


def _bucket_counts(items: list[dict[str, Any]], key: str = "bucket") -> list[dict[str, Any]]:
    order = ["0-15", "16-30", "31-45", "46-60", "61-75", "76-90", "90+", "unknown"]
    counts = {bucket: 0 for bucket in order}
    values = {bucket: [] for bucket in order}
    for item in items:
        bucket = str(item.get(key, "unknown")) or "unknown"
        if bucket not in counts:
            counts[bucket] = 0
            values[bucket] = []
        counts[bucket] += 1
        numeric = _safe_float(item.get("team_value"), _safe_float(item.get("net"), None))
        if numeric is not None:
            values[bucket].append(float(numeric))

    output: list[dict[str, Any]] = []
    for bucket in order:
        count = counts.get(bucket, 0)
        if count <= 0:
            continue
        bucket_values = values.get(bucket, [])
        output.append(
            {
                "bucket": bucket,
                "count": int(count),
                "average_value": round(float(sum(bucket_values) / len(bucket_values)), 3) if bucket_values else 0.0,
            }
        )
    return output


def _summarise_momentum_history(selected_profile: dict[str, Any], recent_profiles: list[dict[str, Any]]) -> dict[str, Any]:
    recent_peaks = [peak for profile in recent_profiles for peak in profile.get("peaks", [])]
    recent_troughs = [trough for profile in recent_profiles for trough in profile.get("troughs", [])]
    recent_goals_for = [goal for profile in recent_profiles for goal in profile.get("goals_for", [])]
    recent_goals_against = [goal for profile in recent_profiles for goal in profile.get("goals_against", [])]

    selected_peak_buckets = {str(item.get("bucket", "")) for item in selected_profile.get("peaks", []) if str(item.get("bucket", ""))}
    selected_trough_buckets = {str(item.get("bucket", "")) for item in selected_profile.get("troughs", []) if str(item.get("bucket", ""))}

    similar_peaks = [item for item in recent_peaks if str(item.get("bucket", "")) in selected_peak_buckets]
    similar_troughs = [item for item in recent_troughs if str(item.get("bucket", "")) in selected_trough_buckets]

    selected_phase = selected_profile.get("phase_profile", {}) if isinstance(selected_profile.get("phase_profile"), dict) else {}
    recent_phase_values: dict[str, list[float]] = {"start_0_15": [], "first_half_16_45": [], "second_half_46_75": [], "late_76_90": []}
    for profile in recent_profiles:
        phase = profile.get("phase_profile", {}) if isinstance(profile.get("phase_profile"), dict) else {}
        for key in recent_phase_values:
            value = _safe_float(phase.get(key))
            if value is not None:
                recent_phase_values[key].append(float(value))

    phase_comparison: list[dict[str, Any]] = []
    for key, values in recent_phase_values.items():
        recent_average = round(float(sum(values) / len(values)), 3) if values else 0.0
        selected_value = round(float(_safe_float(selected_phase.get(key), 0.0) or 0.0), 3)
        phase_comparison.append(
            {
                "phase": key,
                "selected_net": selected_value,
                "recent_average_net": recent_average,
                "difference": round(selected_value - recent_average, 3),
            }
        )

    selected_peak_minutes = [item.get("minute") for item in selected_profile.get("peaks", [])]
    selected_trough_minutes = [item.get("minute") for item in selected_profile.get("troughs", [])]

    return {
        "recent_match_count": len(recent_profiles),
        "similar_peak_count": len(similar_peaks),
        "similar_trough_count": len(similar_troughs),
        "selected_peak_minutes": selected_peak_minutes,
        "selected_trough_minutes": selected_trough_minutes,
        "recent_peak_buckets": _bucket_counts(recent_peaks),
        "recent_trough_buckets": _bucket_counts(recent_troughs),
        "recent_goal_for_buckets": _bucket_counts(recent_goals_for),
        "recent_goal_against_buckets": _bucket_counts(recent_goals_against),
        "similar_peak_windows": _bucket_counts(similar_peaks),
        "similar_trough_windows": _bucket_counts(similar_troughs),
        "phase_comparison": phase_comparison,
        "note": "Momentum uses the same rolling danger score as the match chart. Recent comparison uses the previous five processed matches before this fixture.",
    }


def _safe_xt_attached(events: pd.DataFrame) -> pd.DataFrame:
    try:
        return _attach_xt_values_to_events(events)
    except Exception:
        out = events.copy()
        if "positive_xt" not in out.columns:
            out["positive_xt"] = 0.0
        if "xt_added" not in out.columns:
            out["xt_added"] = 0.0
        return out


def _momentum_analysis_for_team(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    schedule_df: pd.DataFrame,
    all_team_events: pd.DataFrame,
    prepared_match_events: pd.DataFrame,
    selected_fixture: dict[str, Any],
    team_name: str,
    opponent_name: str,
    team_side: str,
) -> dict[str, Any]:
    selected_profile = _momentum_profile(
        _rolling_momentum(prepared_match_events, str(selected_fixture.get("home_team", "")), str(selected_fixture.get("away_team", ""))),
        _match_markers(prepared_match_events),
        team_name,
        opponent_name,
        team_side,
        {
            "match_id": int(selected_fixture.get("match_id", 0) or 0),
            "match_date": selected_fixture.get("kickoff", ""),
            "venue": "Home" if team_side == "home" else "Away",
            "scoreline": "" if selected_fixture.get("home_score") is None or selected_fixture.get("away_score") is None else f"{selected_fixture.get('home_score')}-{selected_fixture.get('away_score')}",
            "status": selected_fixture.get("status", ""),
        },
    )

    if all_team_events is None or all_team_events.empty:
        return {
            "available": selected_profile.get("available", False),
            "team": team_name,
            "selected_match": selected_profile,
            "recent_matches": [],
            "summary": {"recent_match_count": 0, "note": "No processed season event store found for recent momentum comparison."},
        }

    team_all = _ensure_flags(all_team_events.copy())
    team_all = team_all.loc[team_all["team_norm"].eq(_norm_team_name(team_name))].copy()
    if team_all.empty:
        return {
            "available": selected_profile.get("available", False),
            "team": team_name,
            "selected_match": selected_profile,
            "recent_matches": [],
            "summary": {"recent_match_count": 0, "note": "No processed season events found for this team."},
        }

    if "match_date" in team_all.columns:
        team_all["_match_dt"] = pd.to_datetime(team_all["match_date"], errors="coerce", utc=True)
    else:
        team_all["_match_dt"] = pd.NaT

    selected_dt = pd.to_datetime(selected_fixture.get("kickoff", ""), errors="coerce", utc=True)
    selected_match_id = int(selected_fixture.get("match_id", 0) or 0)

    matches: list[dict[str, Any]] = []
    for match_value, group in team_all.groupby("match_id", dropna=True):
        match_id_int = int(match_value)
        if match_id_int == selected_match_id:
            continue
        match_dt = group["_match_dt"].dropna().min() if group["_match_dt"].notna().any() else pd.NaT
        if pd.notna(selected_dt) and pd.notna(match_dt) and match_dt >= selected_dt:
            continue
        matches.append({"match_id": match_id_int, "match_date": match_dt, "sort_key": match_dt if pd.notna(match_dt) else pd.Timestamp(match_id_int, unit="s", tz="UTC")})

    matches = sorted(matches, key=lambda item: item["sort_key"], reverse=True)[:5]
    recent_profiles: list[dict[str, Any]] = []

    for item in matches:
        mid = int(item["match_id"])
        fixture_context = _fixture_context_from_schedule(schedule_df, mid, team_name)
        home_team = str(fixture_context.get("home_team", ""))
        away_team = str(fixture_context.get("away_team", ""))
        if not home_team or not away_team:
            continue

        try:
            full_match = load_processed_match_events(basedir, nation, tier, season, mid)
        except Exception:
            full_match = None
        if full_match is None or full_match.empty:
            continue

        full_match = _ensure_flags(full_match)
        if "team_side" not in full_match.columns or full_match["team_side"].astype(str).str.strip().eq("").all():
            home_norm = _norm_team_name(home_team)
            away_norm = _norm_team_name(away_team)
            full_match["team_side"] = full_match["team_norm"].map(lambda value: "home" if value == home_norm else ("away" if value == away_norm else ""))
        full_match = _safe_xt_attached(full_match)

        match_team_side = "home" if _norm_team_name(home_team) == _norm_team_name(team_name) else "away"
        profile = _momentum_profile(
            _rolling_momentum(full_match, home_team=home_team, away_team=away_team),
            _match_markers(full_match),
            team_name,
            str(fixture_context.get("opponent", "")),
            match_team_side,
            {"match_id": mid, **fixture_context},
        )
        if profile.get("available"):
            recent_profiles.append(profile)

    return {
        "available": True,
        "team": team_name,
        "opponent": opponent_name,
        "selected_match": selected_profile,
        "recent_matches": recent_profiles,
        "summary": _summarise_momentum_history(selected_profile, recent_profiles),
    }


def _momentum_analysis_from_processed(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    selected_fixture: dict[str, Any],
    prepared_match_events: pd.DataFrame,
) -> dict[str, Any]:
    try:
        from app.services.processed_event_store import load_processed_team_events
        home_all = load_processed_team_events(basedir, nation, tier, season, str(selected_fixture["home_team"]))
        away_all = load_processed_team_events(basedir, nation, tier, season, str(selected_fixture["away_team"]))
    except Exception:
        home_all = None
        away_all = None

    schedule_df = load_schedule_frame(basedir, nation=nation, tier=tier, season=season)
    home_team = str(selected_fixture.get("home_team", ""))
    away_team = str(selected_fixture.get("away_team", ""))

    return {
        "home": _momentum_analysis_for_team(
            basedir,
            nation,
            tier,
            season,
            schedule_df,
            home_all if home_all is not None else pd.DataFrame(),
            prepared_match_events,
            selected_fixture,
            home_team,
            away_team,
            "home",
        ),
        "away": _momentum_analysis_for_team(
            basedir,
            nation,
            tier,
            season,
            schedule_df,
            away_all if away_all is not None else pd.DataFrame(),
            prepared_match_events,
            selected_fixture,
            away_team,
            home_team,
            "away",
        ),
    }


GAME_STATE_OPTIONS = {
    "all",
    "first_half",
    "second_half",
    "before_first_goal",
    "after_first_goal",
    "level_score",
    "selected_team_leading",
    "selected_team_trailing",
    "after_first_red_card",
    "after_first_substitution",
}


def _safe_text_value(value: object, fallback: str = "") -> str:
    if value is None:
        return fallback
    try:
        if pd.isna(value):
            return fallback
    except Exception:
        pass
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null", "<na>"}:
        return fallback
    return text


def _clean_shirt_no(value: object) -> str:
    text = _safe_text_value(value)
    if not text:
        return ""
    numeric = pd.to_numeric(pd.Series([text]), errors="coerce").iloc[0]
    if pd.notna(numeric):
        number = float(numeric)
        if math.isfinite(number):
            if number.is_integer():
                return str(int(number))
            return (f"{number:.2f}").rstrip("0").rstrip(".")
    return text


def _safe_int_or_none(value: object) -> int | None:
    if _is_missing_scalar(value):
        return None
    try:
        numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    except Exception:
        return None
    if _is_missing_scalar(numeric):
        return None
    try:
        number = float(numeric)
    except Exception:
        return None
    if not math.isfinite(number):
        return None
    return int(number)


def _safe_float_or_none(value: object) -> float | None:
    if _is_missing_scalar(value):
        return None
    try:
        numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    except Exception:
        return None
    if _is_missing_scalar(numeric):
        return None
    try:
        number = float(numeric)
    except Exception:
        return None
    if not math.isfinite(number):
        return None
    return number


def _round_float_or_none(value: object, digits: int = 2) -> float | None:
    numeric = _safe_float_or_none(value)
    if numeric is None:
        return None
    return round(float(numeric), digits)


def _bool_value(value: object) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except Exception:
        pass
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y", "starter", "start", "starting", "starting_xi"}


def _lineup_compact_text(value: object) -> str:
    text = _safe_text_value(value).strip().lower()
    if text in {"", "nan", "none", "null", "<na>", "unknown"}:
        return ""
    return re.sub(r"[^a-z0-9]+", "", text)


def _lineup_is_sub_text(value: object) -> bool:
    compact = _lineup_compact_text(value)
    return compact in {"sub", "subs", "substitute", "substitutes", "bench"}


def _lineup_is_false_text(value: object) -> bool:
    compact = _lineup_compact_text(value)
    return compact in {"false", "0", "no", "n", "sub", "subs", "substitute", "substitutes", "bench"}


def _lineup_is_true_text(value: object) -> bool:
    compact = _lineup_compact_text(value)
    return compact in {"true", "1", "yes", "y", "starter", "start", "starting", "startingxi", "isstarter"}


def _lineup_row_has_sub_position(row: pd.Series) -> bool:
    return any(_lineup_is_sub_text(row.get(col)) for col in ["player_position", "position"] if col in row)


def _lineup_row_has_start_position(row: pd.Series) -> bool:
    for col in ["player_position", "position"]:
        if col not in row:
            continue
        compact = _lineup_compact_text(row.get(col))
        if compact and not _lineup_is_sub_text(row.get(col)):
            return True
    return False


def _lineup_row_explicit_true(row: pd.Series) -> bool:
    for col in ["is_starter", "starter", "starting", "is_first_eleven", "isFirstEleven", "starting_xi"]:
        if col in row and _lineup_is_true_text(row.get(col)):
            return True
    return False


def _lineup_row_explicit_false(row: pd.Series) -> bool:
    for col in ["is_starter", "starter", "starting", "is_first_eleven", "isFirstEleven", "starting_xi"]:
        if col in row and _lineup_is_false_text(row.get(col)):
            return True
    return False


def _lineup_starter_value_for_group(group: pd.DataFrame) -> bool | str:
    if group.empty:
        return ""
    explicit_true = any(_lineup_row_explicit_true(row) for _, row in group.iterrows())
    explicit_false_or_sub = any(_lineup_row_explicit_false(row) or _lineup_row_has_sub_position(row) for _, row in group.iterrows())
    has_start_position = any(_lineup_row_has_start_position(row) for _, row in group.iterrows())

    if explicit_true:
        return True
    if explicit_false_or_sub:
        return False
    if has_start_position:
        return True
    return ""


def _best_lineup_row(group: pd.DataFrame) -> pd.Series:
    work = group.copy()
    work["__is_sub_position"] = work.apply(_lineup_row_has_sub_position, axis=1).astype(int)
    work["__start_position"] = work.apply(_lineup_row_has_start_position, axis=1).astype(int)
    work["__position_quality"] = work.get("position", pd.Series([""] * len(work), index=work.index)).astype(str).str.strip().replace({"Unknown": "", "nan": "", "None": "", "<NA>": ""}).str.len()
    work["__starter_quality"] = work.get("is_starter", pd.Series([""] * len(work), index=work.index)).astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""}).str.len()
    work = work.sort_values(
        ["__start_position", "__is_sub_position", "__position_quality", "__starter_quality"],
        ascending=[False, True, False, False],
        na_position="last",
    )
    return work.iloc[0].drop(labels=[col for col in work.columns if col.startswith("__")], errors="ignore")


def _fixture_team_id(fixture: dict[str, Any], side: str) -> int | None:
    for key in (["home_team_id", "home_id", "homeTeamId"] if side == "home" else ["away_team_id", "away_id", "awayTeamId"]):
        if key in fixture:
            value = _safe_int_or_none(fixture.get(key))
            if value is not None:
                return value
    return None


def _team_side_for_row(row: pd.Series, home_team: str, away_team: str, home_id: int | None = None, away_id: int | None = None) -> str:
    existing = _safe_text_value(row.get("team_side"))
    if existing in {"home", "away"}:
        return existing

    team_name = _safe_text_value(row.get("team"))
    if team_name:
        normalised = _norm_team_name(team_name)
        if normalised == _norm_team_name(home_team):
            return "home"
        if normalised == _norm_team_name(away_team):
            return "away"

    team_id = _safe_int_or_none(row.get("team_id"))
    if team_id is not None:
        if home_id is not None and team_id == home_id:
            return "home"
        if away_id is not None and team_id == away_id:
            return "away"
    return ""


def _event_summary(row: pd.Series, home_team: str, away_team: str, home_id: int | None = None, away_id: int | None = None) -> dict[str, Any]:
    return {
        "event_index": _safe_int_or_none(row.get("event_index")),
        "minute": _round_float_or_none(row.get("expanded_minute")),
        "period": _period_to_int(row.get("period")),
        "team": _safe_text_value(row.get("team")),
        "team_side": _team_side_for_row(row, home_team, away_team, home_id, away_id),
        "player": _safe_text_value(row.get("player")),
        "type": _safe_text_value(row.get("type")),
        "outcome_type": _safe_text_value(row.get("outcome_type")),
        "card_type": _safe_text_value(row.get("card_type")),
        "x": _round_float_or_none(row.get("x")),
        "y": _round_float_or_none(row.get("y")),
        "end_x": _round_float_or_none(row.get("end_x")),
        "end_y": _round_float_or_none(row.get("end_y")),
        "is_goal": bool(row.get("is_goal", False)),
        "is_red_card": bool(row.get("is_red_card", False)),
        "is_yellow_card": bool(row.get("is_yellow_card", False)),
    }


def _substitution_text(row: pd.Series) -> str:
    return " ".join(
        _safe_text_value(row.get(col))
        for col in ["type", "event_type", "outcome_type", "qualifier_tags", "qual_tags", "qualifiers"]
    ).lower()


def _is_substitution_event(row: pd.Series) -> bool:
    text = re.sub(r"[^a-z0-9]+", "", _substitution_text(row))
    return any(token in text for token in ["substitution", "substitutionon", "substitutionoff", "subon", "suboff", "playeron", "playeroff"])


def _is_subbed_on_event(row: pd.Series) -> bool:
    text = re.sub(r"[^a-z0-9]+", "", _substitution_text(row))
    return any(token in text for token in ["substitutionon", "subon", "playeron"])


def _is_subbed_off_event(row: pd.Series) -> bool:
    text = re.sub(r"[^a-z0-9]+", "", _substitution_text(row))
    return any(token in text for token in ["substitutionoff", "suboff", "playeroff"])


def _positions_csv_path_for_match_setup(basedir: Path, nation: str, tier: str, season: str) -> Path:
    tier_part = _safe_slug(tier or "T1")
    return _events_root(basedir) / "_positions" / _safe_slug(nation) / tier_part / f"{_safe_slug(season)}.csv"


def _load_saved_positions_frame(basedir: Path, nation: str, tier: str, season: str, match_id: int) -> pd.DataFrame:
    path = _positions_csv_path_for_match_setup(basedir, nation, tier, season)
    if not path.exists():
        return pd.DataFrame()
    try:
        df = _read_csv(path)
    except Exception:
        return pd.DataFrame()
    if df.empty or "match_id" not in df.columns:
        return pd.DataFrame()
    mask = pd.to_numeric(df["match_id"], errors="coerce").eq(int(match_id))
    return df.loc[mask].copy()


def _lineup_candidate_rows(events: pd.DataFrame, saved_positions: pd.DataFrame, home_team: str, away_team: str, home_id: int | None, away_id: int | None) -> pd.DataFrame:
    candidates: list[pd.DataFrame] = []
    saved_lineup_sides: set[str] = set()
    position_cols = ["player_id", "player", "shirt_no", "player_position", "position", "position_group", "is_starter", "mins_played", "team", "team_id", "team_side"]

    event_team_side_by_id: dict[int, str] = {}
    event_team_name_by_id: dict[int, str] = {}

    if not events.empty:
        ev_map = events.copy()
        for col in ["team", "team_id", "team_side"]:
            if col not in ev_map.columns:
                ev_map[col] = pd.NA

        if ev_map["team_side"].astype(str).str.strip().eq("").all():
            home_norm = _norm_team_name(home_team)
            away_norm = _norm_team_name(away_team)
            ev_map["team_side"] = ev_map["team"].map(lambda value: "home" if _norm_team_name(value) == home_norm else ("away" if _norm_team_name(value) == away_norm else ""))

        ev_map["team_id_num"] = pd.to_numeric(ev_map["team_id"], errors="coerce")
        for _, row in ev_map.loc[ev_map["team_id_num"].notna()].iterrows():
            team_id = int(row["team_id_num"])
            side = _safe_text_value(row.get("team_side"))
            team = _safe_text_value(row.get("team"))
            if side in {"home", "away"}:
                event_team_side_by_id.setdefault(team_id, side)
            if team:
                event_team_name_by_id.setdefault(team_id, team)

    if not saved_positions.empty:
        pos = saved_positions.copy()
        for col in position_cols:
            if col not in pos.columns:
                pos[col] = pd.NA

        pos["team_id_num"] = pd.to_numeric(pos.get("team_id"), errors="coerce")

        blank_team = pos["team"].astype(str).str.strip().isin(["", "nan", "None", "<NA>"])
        if event_team_name_by_id:
            pos.loc[blank_team, "team"] = pos.loc[blank_team, "team_id_num"].map(lambda value: event_team_name_by_id.get(int(value), "") if pd.notna(value) else "")

        pos["team_side"] = pos.apply(lambda row: _team_side_for_row(row, home_team, away_team, home_id, away_id), axis=1)
        missing_side = ~pos["team_side"].astype(str).isin(["home", "away"])
        if event_team_side_by_id:
            pos.loc[missing_side, "team_side"] = pos.loc[missing_side, "team_id_num"].map(lambda value: event_team_side_by_id.get(int(value), "") if pd.notna(value) else "")

        saved_lineup_sides = set(pos.loc[pos["team_side"].astype(str).isin(["home", "away"]), "team_side"].astype(str).tolist())
        candidates.append(pos[position_cols])

    if not events.empty:
        ev = events.copy()
        for col in position_cols:
            if col not in ev.columns:
                ev[col] = pd.NA
        ev["team_side"] = ev.apply(lambda row: _team_side_for_row(row, home_team, away_team, home_id, away_id), axis=1)
        ev = ev.loc[ev["player"].astype(str).str.strip().ne("")].copy()
        if not ev.empty:
            sort_cols = [col for col in ["team_side", "player_id", "player", "expanded_minute"] if col in ev.columns]
            ev = ev.sort_values(sort_cols, na_position="last")
            dedupe = [col for col in ["team_side", "player_id", "player"] if col in ev.columns]
            ev = ev.drop_duplicates(subset=dedupe, keep="first") if dedupe else ev

            for side in ["home", "away"]:
                side_mask = ev["team_side"].astype(str).eq(side)
                has_saved_side = side in saved_lineup_sides
                if not has_saved_side:
                    side_rows = ev.loc[side_mask].sort_values("expanded_minute", na_position="last")
                    starter_idx = side_rows.head(11).index
                    if len(starter_idx):
                        missing_starter = ev.loc[starter_idx, "is_starter"].astype(str).str.strip().isin(["", "nan", "None", "<NA>"])
                        ev.loc[starter_idx[missing_starter], "is_starter"] = True

            candidates.append(ev[position_cols])

    if not candidates:
        return pd.DataFrame(columns=position_cols)

    out = pd.concat(candidates, ignore_index=True, sort=False)
    out["player_id_num"] = pd.to_numeric(out.get("player_id"), errors="coerce")
    out["player_key"] = out["player"].astype(str).str.strip().str.lower()
    out["team_side"] = out["team_side"].astype(str)

    resolved_rows: list[pd.Series] = []
    with_id = out["player_id_num"].notna()

    for _key, group in out.loc[with_id].groupby(["team_side", "player_id_num"], dropna=False):
        row = _best_lineup_row(group)
        row["is_starter"] = _lineup_starter_value_for_group(group)
        resolved_rows.append(row)

    for _key, group in out.loc[~with_id].groupby(["team_side", "player_key"], dropna=False):
        row = _best_lineup_row(group)
        row["is_starter"] = _lineup_starter_value_for_group(group)
        resolved_rows.append(row)

    if not resolved_rows:
        return pd.DataFrame(columns=position_cols)

    resolved = pd.DataFrame(resolved_rows)
    for col in position_cols:
        if col not in resolved.columns:
            resolved[col] = pd.NA
    return resolved[position_cols].reset_index(drop=True)


def _substitution_minutes_by_player(events: pd.DataFrame) -> dict[tuple[str, str], float]:
    minutes: dict[tuple[str, str], float] = {}
    if events.empty:
        return minutes
    for _, row in events.iterrows():
        if not _is_substitution_event(row):
            continue
        player = _safe_text_value(row.get("player"))
        side = _safe_text_value(row.get("team_side"))
        minute = _safe_float_or_none(row.get("expanded_minute"))
        if player and side and minute is not None:
            key = (side, player.strip().lower())
            minutes[key] = min(minutes.get(key, minute), minute)
    return minutes


def _card_counts_by_player(events: pd.DataFrame) -> dict[tuple[str, str], dict[str, int]]:
    counts: dict[tuple[str, str], dict[str, int]] = {}
    if events.empty:
        return counts
    for _, row in events.loc[_bool_series(events, "is_card")].iterrows():
        player = _safe_text_value(row.get("player"))
        side = _safe_text_value(row.get("team_side"))
        if not player or not side:
            continue
        key = (side, player.strip().lower())
        current = counts.setdefault(key, {"yellow": 0, "red": 0})
        if bool(row.get("is_red_card", False)):
            current["red"] += 1
        elif bool(row.get("is_yellow_card", False)):
            current["yellow"] += 1
    return counts


def _goal_counts_by_player(events: pd.DataFrame) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    if events.empty:
        return counts
    for _, row in events.loc[_bool_series(events, "is_goal")].iterrows():
        player = _safe_text_value(row.get("player"))
        side = _safe_text_value(row.get("team_side"))
        if not player or not side:
            continue
        key = (side, player.strip().lower())
        counts[key] = counts.get(key, 0) + 1
    return counts



def _lineup_position_group_from_position(position: str, fallback: str = "") -> str:
    s = _safe_text_value(position).upper()
    compact = re.sub(r"[^A-Z0-9]+", "", s)
    current = _safe_text_value(fallback)

    if not s or s in {"UNKNOWN", "SUB"}:
        return current
    if s == "GK":
        return "GK"
    if s in {"CB", "LCB", "RCB", "DCL", "DCR", "DC", "D(C)", "D(CL)", "D(CR)"} or compact in {"DC", "DCL", "DCR", "DLC", "DRC"}:
        return "CB"
    if s in {"LWB", "RWB", "WB", "WBL", "WBR", "D(WL)", "D(WR)"} or compact in {"DWL", "DWR", "WBL", "WBR"}:
        return "WB"
    if s in {"LB", "RB", "DL", "DR", "FB", "D(L)", "D(R)"} or compact in {"DL", "DR"}:
        return "FB"
    if s.startswith("DM"):
        return "DM"
    if s in {"CM", "CMF", "MC", "LCM", "RCM", "LCMF", "RCMF", "MCL", "MCR", "MF"}:
        return "CM"
    if s in {"AM", "AMC", "AMF", "CAM", "SS", "10"}:
        return "AM"
    if s in {"LM", "RM", "AML", "AMR", "LAM", "RAM", "ML", "MR"}:
        return "WM"
    if s in {"LW", "RW", "LWF", "RWF", "FWL", "FWR", "WF", "W"}:
        return "WF"
    if s in {"CF", "ST", "FW", "FWC", "LF", "RF", "9"}:
        return "CF"
    if s.startswith("D"):
        if "WB" in s or compact in {"DWL", "DWR"}:
            return "WB"
        if "C" in compact:
            return "CB"
        if "L" in compact or "R" in compact:
            return "FB"
        return "DEF"
    return current

def _lineup_player_from_row(row: pd.Series, side: str, sub_minutes: dict[tuple[str, str], float], card_counts: dict[tuple[str, str], dict[str, int]], goal_counts: dict[tuple[str, str], int]) -> dict[str, Any]:
    player = _safe_text_value(row.get("player"), "Unknown")
    player_key = (side, player.strip().lower())
    position = _safe_text_value(row.get("position"), _safe_text_value(row.get("player_position"), "Unknown"))
    position_group = _lineup_position_group_from_position(position, _safe_text_value(row.get("position_group")))
    starter_raw = row.get("is_starter")
    explicit_true = _lineup_is_true_text(starter_raw)
    explicit_false = _lineup_is_false_text(starter_raw) or _lineup_row_has_sub_position(row)
    has_start_position = _lineup_row_has_start_position(row)

    if explicit_true:
        is_starter = True
    elif explicit_false:
        is_starter = False
    elif has_start_position:
        is_starter = True
    else:
        is_starter = False

    substitution_minute = _round_float_or_none(sub_minutes.get(player_key))
    if substitution_minute is not None and not explicit_true:
        is_starter = False

    is_substitute = not is_starter or substitution_minute is not None
    cards = card_counts.get(player_key, {"yellow": 0, "red": 0})
    goals = goal_counts.get(player_key, 0)

    return {
        "player_id": _safe_int_or_none(row.get("player_id")),
        "player": player,
        "shirt_no": _clean_shirt_no(row.get("shirt_no")),
        "position": position,
        "position_group": position_group,
        "is_starter": bool(is_starter),
        "is_substitute": bool(is_substitute),
        "mins_played": _safe_int_or_none(row.get("mins_played")),
        "substitution_minute": substitution_minute,
        "cards": cards,
        "goals": goals,
    }


def _formation_for_side(events: pd.DataFrame, fixture: dict[str, Any], side: str) -> str | None:
    side_events = events.loc[events.get("team_side", pd.Series([], dtype=str)).astype(str).eq(side)].copy() if not events.empty else pd.DataFrame()
    for source in [fixture, *(side_events.to_dict(orient="records")[:20] if not side_events.empty else [])]:
        for key in ["formation", f"{side}_formation", "team_formation", "formation_name", "formationName"]:
            value = _safe_text_value(source.get(key) if isinstance(source, dict) else "")
            if value:
                return value
    return None


def _build_match_setup(basedir: Path, nation: str, tier: str, season: str, fixture: dict[str, Any], events: pd.DataFrame) -> dict[str, Any]:
    home_team = str(fixture.get("home_team", "Home"))
    away_team = str(fixture.get("away_team", "Away"))
    home_id = _fixture_team_id(fixture, "home")
    away_id = _fixture_team_id(fixture, "away")
    match_id = int(fixture.get("match_id", 0))

    saved_positions = _load_saved_positions_frame(basedir, nation, tier, season, match_id)
    candidates = _lineup_candidate_rows(events, saved_positions, home_team, away_team, home_id, away_id)
    sub_minutes = _substitution_minutes_by_player(events)
    card_counts = _card_counts_by_player(events)
    goal_counts = _goal_counts_by_player(events)

    def side_payload(side: str, team_name: str) -> dict[str, Any]:
        side_rows = candidates.loc[candidates["team_side"].astype(str).eq(side)].copy() if not candidates.empty else pd.DataFrame()
        players = [_lineup_player_from_row(row, side, sub_minutes, card_counts, goal_counts) for _, row in side_rows.iterrows()]
        starters = [player for player in players if bool(player["is_starter"])]
        bench = [player for player in players if not bool(player["is_starter"])]
        formation = _formation_for_side(events, fixture, side)
        notes = ["Lineups depend on saved WhoScored loader position data."]
        if formation is None:
            notes.append("Formation was not available from the current saved data.")
        if not players:
            notes.append("No saved lineup rows were available, so the section can only show event based player involvement when present.")
        elif len(starters) < 11:
            notes.append("Starting lineup is incomplete in the saved provider data; remaining names are inferred from first event involvement where possible.")

        side_events = events.loc[events["team_side"].astype(str).eq(side)].copy() if "team_side" in events.columns else pd.DataFrame()
        substitutions = [_event_summary(row, home_team, away_team, home_id, away_id) for _, row in side_events.iterrows() if _is_substitution_event(row)]
        cards = [_event_summary(row, home_team, away_team, home_id, away_id) for _, row in side_events.loc[_bool_series(side_events, "is_card")].iterrows()] if not side_events.empty else []
        goals = [_event_summary(row, home_team, away_team, home_id, away_id) for _, row in side_events.loc[_bool_series(side_events, "is_goal")].iterrows()] if not side_events.empty else []
        starters.sort(key=lambda item: (_safe_text_value(item.get("position_group")), _safe_text_value(item.get("position")), _safe_int_or_none(item.get("shirt_no")) or 999, _safe_text_value(item.get("player"))))
        bench.sort(key=lambda item: (_safe_int_or_none(item.get("substitution_minute")) if item.get("substitution_minute") is not None else 999, _safe_int_or_none(item.get("shirt_no")) or 999, _safe_text_value(item.get("player"))))
        return {
            "team": team_name,
            "formation": formation,
            "starting_xi": starters,
            "bench": bench,
            "substitutions": substitutions,
            "cards": cards,
            "goals": goals,
            "confidence_notes": notes,
        }

    return {"home": side_payload("home", home_team), "away": side_payload("away", away_team)}


def _match_setup_player_lookup(match_setup: dict[str, Any]) -> dict[tuple[str, str], str]:
    lookup: dict[tuple[str, str], str] = {}
    for side in ["home", "away"]:
        side_data = match_setup.get(side, {}) if isinstance(match_setup, dict) else {}
        if not isinstance(side_data, dict):
            continue
        for group_name in ["starting_xi", "bench"]:
            players = side_data.get(group_name, [])
            if not isinstance(players, list):
                continue
            for player in players:
                if not isinstance(player, dict):
                    continue
                shirt_no = _clean_shirt_no(player.get("shirt_no"))
                if not shirt_no:
                    continue
                player_id = _safe_int_or_none(player.get("player_id"))
                player_name = _safe_text_value(player.get("player"))
                if player_id is not None:
                    lookup.setdefault((side, f"id:{player_id}"), shirt_no)
                if player_name:
                    lookup.setdefault((side, f"name:{_norm_team_name(player_name)}"), shirt_no)
    return lookup


def _shirt_no_for_event_row(row: pd.Series, lookup: dict[tuple[str, str], str]) -> str:
    side = _safe_text_value(row.get("team_side"))
    if side not in {"home", "away"}:
        return ""
    player_id = _safe_int_or_none(row.get("player_id"))
    if player_id is not None:
        shirt_no = lookup.get((side, f"id:{player_id}"), "")
        if shirt_no:
            return shirt_no
    player_name = _safe_text_value(row.get("player"))
    if player_name:
        return lookup.get((side, f"name:{_norm_team_name(player_name)}"), "")
    return ""


def _enrich_events_with_match_setup_shirts(events: pd.DataFrame, match_setup: dict[str, Any]) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    out = events.copy()
    if "shirt_no" not in out.columns:
        out["shirt_no"] = pd.NA
    out["shirt_no"] = out["shirt_no"].map(_clean_shirt_no)
    lookup = _match_setup_player_lookup(match_setup)
    if not lookup:
        return out
    missing = out["shirt_no"].astype(str).str.strip().eq("")
    if missing.any():
        out.loc[missing, "shirt_no"] = out.loc[missing].apply(lambda row: _shirt_no_for_event_row(row, lookup), axis=1)
        out["shirt_no"] = out["shirt_no"].map(_clean_shirt_no)
    return out


def _score_state_frame(events: pd.DataFrame, home_team: str, away_team: str) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    out = _sort_events_by_match_time(events, ["expanded_minute", "event_index"]).copy()
    if "team_side" not in out.columns:
        home_norm = _norm_team_name(home_team)
        away_norm = _norm_team_name(away_team)
        out["team_side"] = out["team_norm"].map(lambda value: "home" if value == home_norm else ("away" if value == away_norm else ""))
    home_before: list[int] = []
    away_before: list[int] = []
    home_after: list[int] = []
    away_after: list[int] = []
    home_score = 0
    away_score = 0
    for _, row in out.iterrows():
        home_before.append(home_score)
        away_before.append(away_score)
        if bool(row.get("is_goal", False)):
            if _safe_text_value(row.get("team_side")) == "home":
                home_score += 1
            elif _safe_text_value(row.get("team_side")) == "away":
                away_score += 1
        home_after.append(home_score)
        away_after.append(away_score)
    out["home_score_before"] = home_before
    out["away_score_before"] = away_before
    out["home_score_after"] = home_after
    out["away_score_after"] = away_after
    return out.sort_index()


def _first_event_minute(events: pd.DataFrame, mask: pd.Series) -> float | None:
    if events.empty or not bool(mask.any()):
        return None
    values = pd.to_numeric(events.loc[mask, "expanded_minute"], errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.min())


def _apply_game_state_filter(events: pd.DataFrame, game_state: str, perspective: str, home_team: str, away_team: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    requested_state = str(game_state or "all").strip().lower()
    requested_perspective = str(perspective or "home").strip().lower()
    if requested_state not in GAME_STATE_OPTIONS:
        requested_state = "all"
    if requested_perspective not in {"home", "away"}:
        requested_perspective = "home"

    notes: list[str] = []
    before_count = int(len(events))
    if events.empty:
        return events.copy(), {"game_state": requested_state, "perspective": requested_perspective, "event_count_before": before_count, "event_count_after": 0, "notes": ["No event rows were available for this match."]}

    scored = _score_state_frame(events, home_team, away_team)
    mask = pd.Series(True, index=scored.index)

    if requested_state == "first_half":
        mask = _period_sort_series(scored).eq(1)
    elif requested_state == "second_half":
        mask = _period_sort_series(scored).eq(2)
    elif requested_state in {"before_first_goal", "after_first_goal"}:
        first_goal = _first_event_minute(scored, _bool_series(scored, "is_goal"))
        if first_goal is None:
            notes.append("Goal timing was not available, so the selected goal state filter was not applied.")
            mask = pd.Series(True, index=scored.index)
        elif requested_state == "before_first_goal":
            mask = pd.to_numeric(scored["expanded_minute"], errors="coerce").lt(first_goal)
        else:
            mask = pd.to_numeric(scored["expanded_minute"], errors="coerce").ge(first_goal)
    elif requested_state == "level_score":
        mask = pd.to_numeric(scored["home_score_before"], errors="coerce").eq(pd.to_numeric(scored["away_score_before"], errors="coerce"))
    elif requested_state in {"selected_team_leading", "selected_team_trailing"}:
        selected = requested_perspective
        selected_score = scored["home_score_before"] if selected == "home" else scored["away_score_before"]
        opponent_score = scored["away_score_before"] if selected == "home" else scored["home_score_before"]
        if requested_state == "selected_team_leading":
            mask = pd.to_numeric(selected_score, errors="coerce").gt(pd.to_numeric(opponent_score, errors="coerce"))
        else:
            mask = pd.to_numeric(selected_score, errors="coerce").lt(pd.to_numeric(opponent_score, errors="coerce"))
    elif requested_state == "after_first_red_card":
        first_red = _first_event_minute(scored, _bool_series(scored, "is_red_card"))
        if first_red is None:
            notes.append("No red card event was available, so the selected red card filter was not applied.")
            mask = pd.Series(True, index=scored.index)
        else:
            mask = pd.to_numeric(scored["expanded_minute"], errors="coerce").ge(first_red)
    elif requested_state == "after_first_substitution":
        sub_mask = scored.apply(_is_substitution_event, axis=1)
        first_sub = _first_event_minute(scored, sub_mask)
        if first_sub is None:
            notes.append("No substitution event was available, so the selected substitution filter was not applied.")
            mask = pd.Series(True, index=scored.index)
        else:
            mask = pd.to_numeric(scored["expanded_minute"], errors="coerce").ge(first_sub)

    filtered = scored.loc[mask.fillna(False)].copy()
    if requested_state == "all":
        notes.append("All event rows are included.")
    elif len(filtered) == before_count and not notes:
        notes.append("The selected game state matched the full available event set.")
    elif not notes:
        notes.append("The selected game state was applied before rebuilding the match analysis numbers.")

    return filtered, {
        "game_state": requested_state,
        "perspective": requested_perspective,
        "event_count_before": before_count,
        "event_count_after": int(len(filtered)),
        "notes": notes,
    }


def _event_time_seconds_from_row(row: pd.Series) -> float:
    period = _period_to_int(row.get("period")) or 1
    minute = _safe_float_or_none(row.get("expanded_minute"))
    if minute is None:
        minute = _safe_float_or_none(row.get("minute")) or 0.0
    return (max(1, int(period)) - 1) * 45.0 * 60.0 + float(minute) * 60.0


def _danger_event_mask(events: pd.DataFrame) -> pd.Series:
    if events.empty:
        return pd.Series(False, index=events.index)
    return _bool_series(events, "is_shot") | _bool_series(events, "box_entry") | _bool_series(events, "final_third_entry")


def _move_event_mask(events: pd.DataFrame) -> pd.Series:
    if events.empty:
        return pd.Series(False, index=events.index)
    return _bool_series(events, "is_pass") | _bool_series(events, "is_cross") | _bool_series(events, "is_carry") | _bool_series(events, "is_take_on")


def _row_point(row: pd.Series, label_prefix: str = "") -> dict[str, Any]:
    label = f"{label_prefix}{_safe_text_value(row.get('player'))} | {_safe_text_value(row.get('type'))}".strip()
    return {
        "event_index": _safe_int_or_none(row.get("event_index")),
        "minute": _round_float_or_none(row.get("expanded_minute")),
        "period": _period_to_int(row.get("period")),
        "team": _safe_text_value(row.get("team")),
        "player": _safe_text_value(row.get("player")),
        "type": _safe_text_value(row.get("type")),
        "event_type": _safe_text_value(row.get("type")),
        "outcome_type": _safe_text_value(row.get("outcome_type")),
        "x": _round_float_or_none(row.get("x")),
        "y": _round_float_or_none(row.get("y")),
        "end_x": _round_float_or_none(row.get("end_x")),
        "end_y": _round_float_or_none(row.get("end_y")),
        "label": label,
        "is_goal": bool(row.get("is_goal", False)),
        "is_shot": bool(row.get("is_shot", False)),
        "box_entry": bool(row.get("box_entry", False)),
        "xt_added": round(float(_safe_float_or_none(row.get("xt_added")) or 0.0), 4),
        "positive_xt": round(float(_safe_float_or_none(row.get("positive_xt")) or 0.0), 4),
    }


def _first_following_event(events: pd.DataFrame, start_row: pd.Series, team_side: str | None, seconds: float = 15.0, mask: pd.Series | None = None) -> pd.Series | None:
    if events.empty:
        return None
    start_seconds = _event_time_seconds_from_row(start_row)
    frame = events.copy()
    if team_side is not None:
        frame = frame.loc[frame["team_side"].astype(str).eq(team_side)].copy()
    frame = frame.loc[frame.apply(lambda row: 0.0 < (_event_time_seconds_from_row(row) - start_seconds) <= seconds, axis=1)].copy()
    if mask is not None:
        frame = frame.loc[mask.reindex(frame.index).fillna(False)].copy()
    if frame.empty:
        return None
    return _sort_events_by_match_time(frame, ["expanded_minute", "event_index"]).iloc[0]


def _transition_map_item(start: pd.Series, danger: pd.Series | None, label: str) -> dict[str, Any]:
    item = _row_point(start, label_prefix=label)
    if danger is not None:
        item["end_x"] = _round_float_or_none(danger.get("x"), 2)
        item["end_y"] = _round_float_or_none(danger.get("y"), 2)
        item["led_to_shot"] = bool(danger.get("is_shot", False))
        item["led_to_goal"] = bool(danger.get("is_goal", False))
        item["danger_player"] = _safe_text_value(danger.get("player"))
        item["danger_type"] = _safe_text_value(danger.get("type"))
    return item


def _build_transition_side(team_name: str, opponent_name: str, team_side: str, opponent_side: str, events: pd.DataFrame) -> dict[str, Any]:
    if events.empty:
        return {
            "team": team_name,
            "summary": {},
            "best_regains": [],
            "worst_losses": [],
            "first_pass_after_regain": [],
            "top_transition_players": [],
            "maps": {"regain_to_danger": [], "loss_to_danger": []},
            "confidence_notes": ["Transition exposure is derived from event sequences and does not prove full rest defence structure."],
        }

    ordered = _sort_events_by_match_time(events, ["expanded_minute", "event_index"]).copy()
    team_events = ordered.loc[ordered["team_side"].astype(str).eq(team_side)].copy()
    opponent_events = ordered.loc[ordered["team_side"].astype(str).eq(opponent_side)].copy()
    regains = team_events.loc[_bool_series(team_events, "is_defensive_action")].copy()
    high_regains = regains.loc[pd.to_numeric(regains.get("x"), errors="coerce").ge(60.0)].copy()
    losses = team_events.loc[_move_event_mask(team_events) & ~_bool_series(team_events, "is_success")].copy()
    danger_mask = _danger_event_mask(ordered)
    team_danger_mask = danger_mask & ordered["team_side"].astype(str).eq(team_side)
    opponent_danger_mask = danger_mask & ordered["team_side"].astype(str).eq(opponent_side)

    best_regains: list[dict[str, Any]] = []
    regain_maps: list[dict[str, Any]] = []
    first_pass_after_regain: list[dict[str, Any]] = []
    regains_to_shot = 0
    regains_to_box = 0
    counter_attacks = 0

    for _, row in regains.iterrows():
        shot = _first_following_event(ordered, row, team_side, 15.0, _bool_series(ordered, "is_shot"))
        box = _first_following_event(ordered, row, team_side, 15.0, _bool_series(ordered, "box_entry"))
        danger = shot if shot is not None else box
        if shot is not None:
            regains_to_shot += 1
        if box is not None:
            regains_to_box += 1
        if shot is not None or box is not None:
            counter_attacks += 1
            item = _row_point(row, "Regain to danger | ")
            item["danger_type"] = _safe_text_value(danger.get("type")) if danger is not None else ""
            item["danger_player"] = _safe_text_value(danger.get("player")) if danger is not None else ""
            item["seconds_to_danger"] = round(max(0.0, _event_time_seconds_from_row(danger) - _event_time_seconds_from_row(row)), 2) if danger is not None else None
            best_regains.append(item)
            regain_maps.append(_transition_map_item(row, danger, "Regain to danger | "))
        first_pass = _first_following_event(ordered, row, team_side, 15.0, _bool_series(ordered, "is_pass") | _bool_series(ordered, "is_cross"))
        if first_pass is not None:
            first_pass_after_regain.append(_row_point(first_pass, "First pass after regain | "))

    worst_losses: list[dict[str, Any]] = []
    loss_maps: list[dict[str, Any]] = []
    losses_to_shot = 0
    losses_to_box = 0
    counter_attacks_against = 0
    for _, row in losses.iterrows():
        shot = _first_following_event(ordered, row, opponent_side, 15.0, _bool_series(ordered, "is_shot"))
        box = _first_following_event(ordered, row, opponent_side, 15.0, _bool_series(ordered, "box_entry"))
        danger = shot if shot is not None else box
        if shot is not None:
            losses_to_shot += 1
        if box is not None:
            losses_to_box += 1
        if shot is not None or box is not None:
            counter_attacks_against += 1
            item = _row_point(row, "Loss to danger | ")
            item["danger_type"] = _safe_text_value(danger.get("type")) if danger is not None else ""
            item["danger_player"] = _safe_text_value(danger.get("player")) if danger is not None else ""
            item["seconds_to_danger"] = round(max(0.0, _event_time_seconds_from_row(danger) - _event_time_seconds_from_row(row)), 2) if danger is not None else None
            worst_losses.append(item)
            loss_maps.append(_transition_map_item(row, danger, "Loss to danger | "))

    player_rows: dict[str, dict[str, Any]] = {}
    for _, row in regains.iterrows():
        player = _safe_text_value(row.get("player"), "Unknown")
        item = player_rows.setdefault(player, {"player": player, "regains": 0, "high_regains": 0, "losses": 0, "transition_score": 0.0})
        item["regains"] += 1
        item["transition_score"] += 1.0
        if float(_safe_float_or_none(row.get("x")) or 0.0) >= 60.0:
            item["high_regains"] += 1
            item["transition_score"] += 0.75
    for _, row in losses.iterrows():
        player = _safe_text_value(row.get("player"), "Unknown")
        item = player_rows.setdefault(player, {"player": player, "regains": 0, "high_regains": 0, "losses": 0, "transition_score": 0.0})
        item["losses"] += 1
        item["transition_score"] -= 0.35
    top_players = sorted(player_rows.values(), key=lambda item: (float(item["transition_score"]), int(item["regains"])), reverse=True)[:8]

    return {
        "team": team_name,
        "opponent": opponent_name,
        "summary": {
            "regains": int(len(regains)),
            "high_regains": int(len(high_regains)),
            "regains_leading_to_shot_15s": int(regains_to_shot),
            "regains_leading_to_box_entry_15s": int(regains_to_box),
            "losses": int(len(losses)),
            "losses_leading_to_opponent_shot_15s": int(losses_to_shot),
            "losses_leading_to_opponent_box_entry_15s": int(losses_to_box),
            "counter_attacks": int(counter_attacks),
            "counter_attacks_against": int(counter_attacks_against),
        },
        "best_regains": sorted(best_regains, key=lambda item: (item.get("seconds_to_danger") is None, item.get("seconds_to_danger") or 99))[:8],
        "worst_losses": sorted(worst_losses, key=lambda item: (item.get("seconds_to_danger") is None, item.get("seconds_to_danger") or 99))[:8],
        "first_pass_after_regain": first_pass_after_regain[:8],
        "top_transition_players": top_players,
        "maps": {"regain_to_danger": regain_maps[:90], "loss_to_danger": loss_maps[:90]},
        "confidence_notes": ["Transition exposure is derived from event sequences and does not prove full rest defence structure."],
    }


def _build_transition_analysis(events: pd.DataFrame, home_team: str, away_team: str) -> dict[str, Any]:
    return {
        "home": _build_transition_side(home_team, away_team, "home", "away", events),
        "away": _build_transition_side(away_team, home_team, "away", "home", events),
        "confidence_notes": ["Transition exposure is derived from event sequences and does not prove full rest defence structure."],
    }


def _chain_action_from_row(row: pd.Series, order: int) -> dict[str, Any]:
    return {
        "order": int(order),
        "event_index": _safe_int_or_none(row.get("event_index")),
        "minute": _round_float_or_none(row.get("expanded_minute")),
        "player": _safe_text_value(row.get("player")),
        "type": _safe_text_value(row.get("type")),
        "event_type": _safe_text_value(row.get("type")),
        "x": _round_float_or_none(row.get("x")),
        "y": _round_float_or_none(row.get("y")),
        "end_x": _round_float_or_none(row.get("end_x")),
        "end_y": _round_float_or_none(row.get("end_y")),
        "outcome_type": _safe_text_value(row.get("outcome_type")),
        "is_shot": bool(row.get("is_shot", False)),
        "is_goal": bool(row.get("is_goal", False)),
        "is_box_entry": bool(row.get("box_entry", False)),
        "xt_added": round(float(_safe_float_or_none(row.get("xt_added")) or 0.0), 4),
        "positive_xt": round(float(_safe_float_or_none(row.get("positive_xt")) or 0.0), 4),
        "xg": round(float(_fallback_shot_xg(row)) if bool(row.get("is_shot", False)) else 0.0, 4),
    }


def _chain_payload(chain_rows: list[pd.Series], chain_id: str, team_name: str) -> dict[str, Any]:
    ordered = chain_rows
    first = ordered[0]
    last = ordered[-1]
    start_second = _event_time_seconds_from_row(first)
    end_second = _event_time_seconds_from_row(last)
    actions = [_chain_action_from_row(row, index + 1) for index, row in enumerate(ordered)]
    xg = sum(float(action.get("xg") or 0.0) for action in actions)
    xt_added = sum(float(action.get("xt_added") or 0.0) for action in actions)
    players = []
    seen_players: set[str] = set()
    for row in ordered:
        player = _safe_text_value(row.get("player"))
        if player and player not in seen_players:
            players.append(player)
            seen_players.add(player)
    has_shot = any(bool(action.get("is_shot")) for action in actions)
    has_goal = any(bool(action.get("is_goal")) for action in actions)
    has_box = any(bool(action.get("is_box_entry")) for action in actions)
    if has_goal:
        outcome_label = "Goal"
    elif has_shot:
        outcome_label = "Shot"
    elif has_box:
        outcome_label = "Box entry"
    elif not bool(last.get("is_success", False)) and (_move_event_mask(pd.DataFrame([last])).iloc[0] if len(pd.DataFrame([last])) else False):
        outcome_label = "Failed progression"
    else:
        outcome_label = "Retained progression"
    return {
        "chain_id": chain_id,
        "team": team_name,
        "start_minute": _round_float_or_none(first.get("expanded_minute")),
        "end_minute": _round_float_or_none(last.get("expanded_minute")),
        "duration_seconds": round(max(0.0, end_second - start_second), 2),
        "action_count": int(len(actions)),
        "start_x": _round_float_or_none(first.get("x")),
        "start_y": _round_float_or_none(first.get("y")),
        "end_x": _round_float_or_none(last.get("end_x"), 2) if _safe_float_or_none(last.get("end_x")) is not None else _round_float_or_none(last.get("x")),
        "end_y": _round_float_or_none(last.get("end_y"), 2) if _safe_float_or_none(last.get("end_y")) is not None else _round_float_or_none(last.get("y")),
        "outcome_label": outcome_label,
        "xg": round(float(xg), 4),
        "xt_added": round(float(xt_added), 4),
        "players": players,
        "actions": actions,
    }


def _build_team_possession_chains(events: pd.DataFrame, team_name: str, team_side: str) -> dict[str, Any]:
    if events.empty:
        return {
            "team": team_name,
            "best_attacking_chains": [],
            "long_build_ups": [],
            "direct_attacks": [],
            "failed_progressions": [],
            "chains_ending_in_shot": [],
            "chains_ending_in_box_entry": [],
            "confidence_notes": ["Possession chains are inferred from same team event continuity when provider possession id is not available."],
        }
    ordered = _sort_events_by_match_time(events, ["expanded_minute", "event_index"]).copy()
    action_events = ordered.loc[_move_event_mask(ordered) | _bool_series(ordered, "is_shot") | _bool_series(ordered, "box_entry")].copy()
    action_events = action_events.loc[action_events["team_side"].astype(str).eq(team_side)].dropna(subset=["x", "y"]).copy()
    chains: list[list[pd.Series]] = []
    current: list[pd.Series] = []
    previous: pd.Series | None = None
    for _, row in action_events.iterrows():
        should_break = False
        if previous is not None:
            gap = _event_time_seconds_from_row(row) - _event_time_seconds_from_row(previous)
            should_break = gap < 0.0 or gap > 10.0 or (_period_to_int(row.get("period")) != _period_to_int(previous.get("period")))
        if should_break and current:
            chains.append(current)
            current = []
        current.append(row)
        previous = row
    if current:
        chains.append(current)

    payloads = [
        _chain_payload(chain, f"{team_side}_{index + 1}", team_name)
        for index, chain in enumerate(chains)
        if len(chain) >= 2 or any(bool(row.get("is_shot", False)) or bool(row.get("box_entry", False)) for row in chain)
    ]
    payloads.sort(key=lambda item: (float(item["xg"]) + max(0.0, float(item["xt_added"])), int(item["action_count"])), reverse=True)

    def top(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return items[:10]

    return {
        "team": team_name,
        "best_attacking_chains": top(payloads),
        "long_build_ups": top([item for item in payloads if int(item["action_count"]) >= 7 or float(item["duration_seconds"]) >= 25.0]),
        "direct_attacks": top([item for item in payloads if float(item["duration_seconds"]) <= 15.0 and (float(item["end_x"] or 0.0) - float(item["start_x"] or 0.0)) >= 25.0]),
        "failed_progressions": top([item for item in payloads if item["outcome_label"] == "Failed progression"]),
        "chains_ending_in_shot": top([item for item in payloads if item["outcome_label"] in {"Shot", "Goal"}]),
        "chains_ending_in_box_entry": top([item for item in payloads if item["outcome_label"] in {"Box entry", "Shot", "Goal"}]),
        "confidence_notes": ["Possession chains are inferred from same team event continuity when provider possession id is not available."],
    }


def _build_possession_chains(events: pd.DataFrame, home_team: str, away_team: str) -> dict[str, Any]:
    return {
        "home": _build_team_possession_chains(events, home_team, "home"),
        "away": _build_team_possession_chains(events, away_team, "away"),
        "confidence_notes": ["Possession chains are inferred from same team event continuity when provider possession id is not available."],
    }


def _file_stamp(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
        return int(stat.st_mtime_ns), int(stat.st_size)
    except Exception:
        return 0, 0


def _tree_stamp(root: Path, suffixes: tuple[str, ...]) -> tuple[int, int, int]:
    if not root.exists():
        return 0, 0, 0

    latest_mtime = 0
    total_size = 0
    file_count = 0
    try:
        suffix_set = {item.lower() for item in suffixes}
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


def _schedule_file_path(basedir: Path, nation: str, tier: str, season: str) -> Path:
    folder_name = f"{nation} {tier}".strip()
    return basedir / "data" / "Schedule" / folder_name / f"{_safe_slug(season)}.csv"


def _processed_scope_root(basedir: Path, nation: str, tier: str, season: str) -> Path:
    return basedir / "data" / "Processed" / _safe_slug(nation) / _safe_slug(tier or "T1") / _safe_slug(season)


def _prepared_match_events_cache_path(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    match_id: int,
) -> Path:
    return _processed_scope_root(basedir, nation, tier, season) / "prepared_by_match" / f"match_id={int(match_id)}" / "events.parquet"


def _prepared_season_events_cache_path(basedir: Path, nation: str, tier: str, season: str) -> Path:
    return _processed_scope_root(basedir, nation, tier, season) / "prepared_season_events.parquet"


def _parquet_cache_ready(cache_path: Path, source_path: Path | None = None) -> bool:
    if not cache_path.exists():
        return False
    if source_path is None or not source_path.exists():
        return True
    try:
        return cache_path.stat().st_mtime_ns >= source_path.stat().st_mtime_ns
    except Exception:
        return True


def _match_source_parquet_path(basedir: Path, nation: str, tier: str, season: str, match_id: int) -> Path:
    return _processed_scope_root(basedir, nation, tier, season) / "events_by_match" / f"match_id={int(match_id)}" / "events.parquet"


def _season_source_parquet_path(basedir: Path, nation: str, tier: str, season: str) -> Path:
    return _processed_scope_root(basedir, nation, tier, season) / "events_clean.parquet"


def _serialisable_analysis_cache_frame(df: pd.DataFrame) -> pd.DataFrame:
    keep = df.copy()
    if keep.empty:
        return keep

    keep.columns = [str(col).strip() for col in keep.columns]
    if keep.columns.duplicated().any():
        keep = keep.loc[:, ~keep.columns.duplicated()].copy()

    for col in keep.columns:
        if keep[col].map(lambda value: isinstance(value, (dict, list, set, tuple))).any():
            keep[col] = keep[col].apply(lambda value: "" if value is None else str(value))

    for col in keep.columns:
        if pd.api.types.is_datetime64_any_dtype(keep[col]):
            keep[col] = pd.to_datetime(keep[col], errors="coerce", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ").fillna("")
        elif pd.api.types.is_object_dtype(keep[col]) or pd.api.types.is_string_dtype(keep[col]):
            keep[col] = keep[col].astype(str).replace({"nan": "", "None": "", "<NA>": "", "NaT": ""}).fillna("")

    return keep


def _read_analysis_cache_frame(cache_path: Path, source_path: Path | None = None) -> pd.DataFrame | None:
    if not _parquet_cache_ready(cache_path, source_path):
        return None
    try:
        frame = pd.read_parquet(cache_path)
    except Exception:
        return None
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    return _coerce_analysis_numeric_columns(frame)


def _write_analysis_cache_frame(frame: pd.DataFrame, cache_path: Path) -> None:
    if frame.empty:
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_suffix(".parquet.tmp")
        _serialisable_analysis_cache_frame(frame).to_parquet(tmp_path, index=False)
        tmp_path.replace(cache_path)
    except Exception:
        try:
            if "tmp_path" in locals() and tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass


def _read_prepared_match_events_cache(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    match_id: int,
) -> pd.DataFrame | None:
    cache_path = _prepared_match_events_cache_path(basedir, nation, tier, season, match_id)
    source_path = _match_source_parquet_path(basedir, nation, tier, season, match_id)
    cached = _read_analysis_cache_frame(cache_path, source_path)
    if cached is None:
        return None
    return _sort_events_by_match_time(cached, ["expanded_minute", "event_index"]).reset_index(drop=True)


def _write_prepared_match_events_cache(
    events: pd.DataFrame,
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    match_id: int,
) -> None:
    cache_path = _prepared_match_events_cache_path(basedir, nation, tier, season, match_id)
    _write_analysis_cache_frame(events, cache_path)


def _load_prepared_season_events_for_analysis(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
) -> tuple[pd.DataFrame | None, str]:
    cache_path = _prepared_season_events_cache_path(basedir, nation, tier, season)
    source_path = _season_source_parquet_path(basedir, nation, tier, season)

    cached = _read_analysis_cache_frame(cache_path, source_path)
    if cached is not None:
        return _sort_events_by_match_time(cached, ["match_id", "expanded_minute", "event_index"]).reset_index(drop=True), "prepared_season_parquet"

    loaded_season_events, season_source = _load_team_radar_season_events(basedir, nation, tier, season)
    if loaded_season_events is None or loaded_season_events.empty:
        return None, season_source

    loaded_season_events = _normalise_missing_scalars(loaded_season_events)
    season_frame = _ensure_flags(loaded_season_events)
    season_frame = _add_take_on_endpoints(season_frame)
    season_frame = _infer_carry_rows(season_frame)
    _write_analysis_cache_frame(season_frame, cache_path)
    return season_frame, f"{season_source}_prepared"


def _event_scope_stamp(basedir: Path, nation: str, tier: str) -> tuple[int, int, int]:
    latest_mtime = 0
    total_size = 0
    file_count = 0
    try:
        for root in _event_scope_roots(_events_root(basedir), nation, tier):
            mtime, size, count = _tree_stamp(root, (".csv",))
            latest_mtime = max(latest_mtime, mtime)
            total_size += size
            file_count += count
    except Exception:
        return 0, 0, 0
    return latest_mtime, total_size, file_count


def _analysis_cache_token(basedir: Path, nation: str, tier: str, season: str) -> tuple[tuple[int, int], tuple[int, int, int], tuple[int, int, int]]:
    return (
        _file_stamp(_schedule_file_path(basedir, nation, tier, season)),
        _tree_stamp(_processed_scope_root(basedir, nation, tier, season), (".parquet",)),
        _event_scope_stamp(basedir, nation, tier),
    )


def _match_events_cache_token(basedir: Path, nation: str, tier: str, season: str, match_id: int) -> tuple[tuple[int, int], tuple[int, int, int], tuple[int, int, int]]:
    processed_match_root = _processed_scope_root(basedir, nation, tier, season) / "events_by_match" / f"match_id={int(match_id)}"
    return (
        _file_stamp(_schedule_file_path(basedir, nation, tier, season)),
        _tree_stamp(processed_match_root, (".parquet",)),
        _event_scope_stamp(basedir, nation, tier),
    )


def _normalised_path_key(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except Exception:
        return str(path)


def _prepare_match_events_uncached(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    match_id: int,
    home_team: str,
    away_team: str,
) -> tuple[pd.DataFrame, str]:
    cached_events = _read_prepared_match_events_cache(basedir, nation, tier, season, int(match_id))
    if cached_events is not None:
        return cached_events.copy(), "prepared_match_parquet"

    events = load_processed_match_events(basedir, nation, tier, season, int(match_id))
    data_source = "processed_parquet"
    if events is None:
        events, _fixture_from_raw = load_match_events(basedir, nation, tier, season, int(match_id))
        data_source = "raw_csv"

    if events.empty:
        return events.copy(), data_source

    events = _normalise_missing_scalars(events)
    events = _ensure_flags(events)
    events = _add_take_on_endpoints(events)
    events = _infer_carry_rows(events)
    if "team_side" not in events.columns or events["team_side"].astype(str).str.strip().eq("").all():
        home_norm = _norm_team_name(home_team)
        away_norm = _norm_team_name(away_team)
        events["team_side"] = events["team_norm"].map(lambda value: "home" if value == home_norm else ("away" if value == away_norm else ""))

    events = _attach_xt_values_to_events(events)
    events = _normalise_missing_scalars(events)
    _write_prepared_match_events_cache(events, basedir, nation, tier, season, int(match_id))
    return events, data_source


@lru_cache(maxsize=48)
def _prepare_match_events_cached(
    basedir_key: str,
    nation: str,
    tier: str,
    season: str,
    match_id: int,
    home_team: str,
    away_team: str,
    cache_token: tuple[tuple[int, int], tuple[int, int, int], tuple[int, int, int]],
) -> tuple[pd.DataFrame, str]:
    return _prepare_match_events_uncached(Path(basedir_key), nation, tier, season, int(match_id), home_team, away_team)


def _load_prepared_match_events(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    match_id: int,
    home_team: str,
    away_team: str,
) -> tuple[pd.DataFrame, str]:
    cache_token = _match_events_cache_token(basedir, nation, tier, season, int(match_id))
    events, data_source = _prepare_match_events_cached(
        _normalised_path_key(basedir),
        str(nation),
        str(tier),
        str(season),
        int(match_id),
        str(home_team),
        str(away_team),
        cache_token,
    )
    return _normalise_missing_scalars(events).copy(), data_source


@lru_cache(maxsize=32)
def _build_team_match_index_cached(
    events_root_key: str,
    nation: str,
    tier: str,
    season: str,
    schedule_teams: tuple[str, ...],
    cache_token: tuple[int, int, int],
) -> dict[str, frozenset[int]]:
    raw = _build_team_match_index_uncached(Path(events_root_key), nation, tier, season, list(schedule_teams))
    return {key: frozenset(value) for key, value in raw.items()}


def _build_match_analysis_uncached(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    match_id: int | None = None,
    game_state: str = "all",
    perspective: str = "home",
) -> dict[str, Any]:
    schedule_df = load_schedule_frame(basedir, nation=nation, tier=tier, season=season)
    fixtures = _build_fixtures(schedule_df, basedir=basedir, nation=nation, tier=tier, season=season)
    processed_status = processed_store_status(basedir, nation=nation, tier=tier, season=season)

    active_filter = {
        "game_state": str(game_state or "all").strip().lower() or "all",
        "perspective": str(perspective or "home").strip().lower() or "home",
        "event_count_before": 0,
        "event_count_after": 0,
        "notes": ["No match has been selected yet."],
    }

    payload: dict[str, Any] = {
        "nation": nation,
        "tier": tier,
        "season": season,
        "processed_store": processed_status,
        "fixtures": fixtures,
        "selected_fixture": None,
        "raw_events": [],
        "available_columns": [],
        "event_count": 0,
        "team_summaries": {},
        "team_radar": {"metrics": [], "home": {"team": "Home", "values": []}, "away": {"team": "Away", "values": []}, "confidence_notes": []},
        "momentum": [],
        "momentum_possession": [],
        "match_markers": [],
        "territory": {"x_bins": 6, "y_bins": 5, "home": [], "away": []},
        "action_maps": {"home": [], "away": []},
        "shot_maps": {"home": [], "away": []},
        "goalmouth_maps": {"home": [], "away": []},
        "final_third_pass_maps": {"home": [], "away": []},
        "pass_networks": {"home": _empty_pass_network(), "away": _empty_pass_network()},
        "phase_summaries": {"home": [], "away": []},
        "style_tags": {
            "home": {},
            "away": {},
            "confidence_notes": ["No match has been selected yet."],
        },
        "attacking_direction": {"home": [], "away": []},
        "attacking_threat_lanes": {"home": [], "away": []},
        "attacking_threat_boxes": {"home": {}, "away": {}},
        "shot_sequences": [],
        "recent_patterns": {"home": {"available": False}, "away": {"available": False}},
        "xt_analysis": {"home": {}, "away": {}},
        "defensive_analysis": {"home": {}, "away": {}},
        "set_piece_analysis": {"home": {}, "away": {}},
        "best_players_analysis": {"home": {}, "away": {}},
        "momentum_analysis": {"home": {}, "away": {}},
        "match_setup": {
            "home": {"team": "Home", "formation": None, "starting_xi": [], "bench": [], "substitutions": [], "cards": [], "goals": [], "confidence_notes": ["No match has been selected yet."]},
            "away": {"team": "Away", "formation": None, "starting_xi": [], "bench": [], "substitutions": [], "cards": [], "goals": [], "confidence_notes": ["No match has been selected yet."]},
        },
        "active_filter": active_filter,
        "transition_analysis": {
            "home": {},
            "away": {},
            "confidence_notes": ["Transition exposure is derived from event sequences and does not prove full rest defence structure."],
        },
        "possession_chains": {
            "home": {},
            "away": {},
            "confidence_notes": ["Possession chains are inferred from same team event continuity when provider possession id is not available."],
        },
    }

    if match_id is None:
        return payload

    fixture = next((item for item in fixtures if int(item["match_id"]) == int(match_id)), None)
    if fixture is None:
        raise ValueError(f"Match {match_id} was not found in the saved schedule.")

    home_team = str(fixture["home_team"])
    away_team = str(fixture["away_team"])

    events, data_source = _load_prepared_match_events(basedir, nation, tier, season, int(match_id), home_team, away_team)
    events = _normalise_missing_scalars(events)

    payload["selected_fixture"] = fixture
    payload["data_source"] = data_source

    if events.empty:
        payload["match_setup"] = _build_match_setup(basedir, nation, tier, season, fixture, events)
        payload["active_filter"] = {
            "game_state": str(game_state or "all"),
            "perspective": str(perspective or "home"),
            "event_count_before": 0,
            "event_count_after": 0,
            "notes": ["No event rows were available for this match."],
        }
        return payload

    full_events_for_setup = events.copy()
    payload["match_setup"] = _build_match_setup(basedir, nation, tier, season, fixture, full_events_for_setup)
    events = _enrich_events_with_match_setup_shirts(events, payload["match_setup"])
    events = _normalise_missing_scalars(events)

    events, active_filter = _apply_game_state_filter(events, game_state, perspective, home_team, away_team)
    events = _normalise_missing_scalars(events)
    payload["active_filter"] = active_filter

    home_events = _team_events(events, home_team)
    away_events = _team_events(events, away_team)
    home_summary = _team_summary(home_events)
    away_summary = _team_summary(away_events)

    payload["available_columns"] = list(events.columns)
    payload["event_count"] = int(len(events))
    payload["analytic_event_count"] = int(len(events))
    payload["raw_events"] = _raw_event_rows(events)
    payload["team_summaries"] = {
        "home": {"team": home_team, **home_summary},
        "away": {"team": away_team, **away_summary},
    }
    payload["team_radar"] = _build_season_team_radar(basedir, nation, tier, season, home_team, away_team, home_summary, away_summary)
    payload["momentum"] = _rolling_momentum(events, home_team=home_team, away_team=away_team)
    payload["momentum_possession"] = _rolling_possession_timeline(events, home_team=home_team, away_team=away_team)
    payload["match_markers"] = _match_markers(events)
    payload["territory"] = {"x_bins": 6, "y_bins": 5, "home": _grid_map(home_events), "away": _grid_map(away_events)}
    payload["action_maps"] = {"home": _action_points(home_events), "away": _action_points(away_events)}
    payload["shot_maps"] = {"home": _shot_points(home_events), "away": _shot_points(away_events)}
    payload["goalmouth_maps"] = {"home": _goalmouth_points(home_events), "away": _goalmouth_points(away_events)}
    payload["final_third_pass_maps"] = {"home": _final_third_pass_map(home_events), "away": _final_third_pass_map(away_events)}
    payload["pass_networks"] = {"home": _build_pass_network(home_events, home_team), "away": _build_pass_network(away_events, away_team)}
    payload["phase_summaries"] = {
        "home": _phase_breakdown(home_team, home_events, home_summary),
        "away": _phase_breakdown(away_team, away_events, away_summary),
    }
    payload["style_tags"] = _build_style_tags(
        basedir=basedir,
        nation=nation,
        tier=tier,
        season=season,
        home_team=home_team,
        away_team=away_team,
        match_events=events,
    )
    payload["attacking_direction"] = {"home": _direction_arrows(home_events), "away": _direction_arrows(away_events)}
    payload["attacking_threat_lanes"] = {"home": _attacking_threat_lanes(home_events), "away": _attacking_threat_lanes(away_events)}
    payload["attacking_threat_boxes"] = {"home": _attacking_threat_boxes(home_events), "away": _attacking_threat_boxes(away_events)}
    payload["shot_sequences"] = _shot_sequences(events)
    payload["recent_patterns"] = _recent_patterns_from_processed(
        basedir=basedir,
        nation=nation,
        tier=tier,
        season=season,
        selected_fixture=fixture,
        home_events=home_events,
        away_events=away_events,
    )
    payload["momentum_analysis"] = _momentum_analysis_from_processed(
        basedir=basedir,
        nation=nation,
        tier=tier,
        season=season,
        selected_fixture=fixture,
        prepared_match_events=events,
    )
    payload["xt_analysis"] = _build_xt_analysis(events, home_team, away_team)
    payload["defensive_analysis"] = {
        "home": _build_defensive_analysis(home_team, away_team, home_events, away_events, events),
        "away": _build_defensive_analysis(away_team, home_team, away_events, home_events, events),
    }
    payload["set_piece_analysis"] = {
        "home": _build_set_piece_analysis(home_team, away_team, events),
        "away": _build_set_piece_analysis(away_team, home_team, events),
    }
    payload["transition_analysis"] = _build_transition_analysis(events, home_team, away_team)
    payload["possession_chains"] = _build_possession_chains(events, home_team, away_team)
    payload["best_players_analysis"] = {
        "home": _best_players_for_team(
            home_events,
            payload["xt_analysis"].get("home", {}),
            payload["defensive_analysis"].get("home", {}),
            payload["set_piece_analysis"].get("home", {}),
        ),
        "away": _best_players_for_team(
            away_events,
            payload["xt_analysis"].get("away", {}),
            payload["defensive_analysis"].get("away", {}),
            payload["set_piece_analysis"].get("away", {}),
        ),
    }
    return payload


@lru_cache(maxsize=32)
def _get_match_analysis_cached(
    basedir_key: str,
    nation: str,
    tier: str,
    season: str,
    match_id_key: int,
    game_state: str,
    perspective: str,
    cache_token: tuple[tuple[int, int], tuple[int, int, int], tuple[int, int, int]],
) -> dict[str, Any]:
    match_id = None if int(match_id_key) < 0 else int(match_id_key)
    return _build_match_analysis_uncached(
        basedir=Path(basedir_key),
        nation=nation,
        tier=tier,
        season=season,
        match_id=match_id,
        game_state=game_state,
        perspective=perspective,
    )


def get_match_analysis(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    match_id: int | None = None,
    game_state: str = "all",
    perspective: str = "home",
) -> dict[str, Any]:
    cache_token = _analysis_cache_token(basedir, nation, tier, season)
    payload = _get_match_analysis_cached(
        _normalised_path_key(basedir),
        str(nation),
        str(tier),
        str(season),
        int(match_id) if match_id is not None else -1,
        str(game_state or "all").strip().lower() or "all",
        str(perspective or "home").strip().lower() or "home",
        cache_token,
    )
    return copy.deepcopy(payload)