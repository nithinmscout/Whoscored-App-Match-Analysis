from __future__ import annotations
from app.services.custom_ws_scraper import (
    BUILTIN_SOCCERDATA_WS_LEAGUES,
    CUSTOM_WS_LEAGUES,
    LEAGUE_PRESETS,
    SOCCERDATA_BACKED_LEAGUES,
    SOCCERDATA_LEAGUE_DICT,
    get_league_presets_payload,
    load_schedule_custom,
    resolve_league_folder,
)
from app.services.scraper_service import seleniumbase_local_cwd, setup_driver

from app.core.config import guess_browser_path
from datetime import datetime
from pathlib import Path
from types import MethodType
from typing import Any, Callable, Iterator
from queue import Empty, Queue
import csv
from threading import Thread
import json
import logging
import os
import random
import re
import shutil
import time
import unicodedata


def _soccerdata_config_dir() -> Path:
    configured_root = os.environ.get("SOCCERDATA_DIR")
    root = Path(configured_root).expanduser() if configured_root else Path.home() / "soccerdata"
    return root / "config"


def _ensure_soccerdata_league_dict_file() -> dict[str, Any]:
    config_dir = _soccerdata_config_dir()
    league_dict_path = config_dir / "league_dict.json"
    status: dict[str, Any] = {
        "path": str(league_dict_path),
        "created": False,
        "updated": False,
        "leagues_written": 0,
        "error": "",
    }

    try:
        config_dir.mkdir(parents=True, exist_ok=True)

        if league_dict_path.exists():
            try:
                existing = json.loads(league_dict_path.read_text(encoding="utf-8"))
                if not isinstance(existing, dict):
                    existing = {}
            except Exception:
                existing = {}
        else:
            existing = {}
            status["created"] = True

        changed = False
        leagues_written = 0

        for league, mapping in SOCCERDATA_LEAGUE_DICT.items():
            if not isinstance(mapping, dict):
                continue

            current = existing.get(league)
            if not isinstance(current, dict):
                existing[league] = dict(mapping)
                changed = True
                leagues_written += 1
                continue

            for key, value in mapping.items():
                if key not in current or str(current.get(key, "")).strip() == "":
                    current[key] = value
                    changed = True
                    leagues_written += 1

        if changed or status["created"]:
            league_dict_path.write_text(
                json.dumps(existing, indent=2, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            status["updated"] = True

        status["leagues_written"] = leagues_written
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"

    return status


SOCCERDATA_CONFIG_STATUS = _ensure_soccerdata_league_dict_file()

import numpy as np
import pandas as pd
import soccerdata as sd


def _safe_slug(s: str) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return "".join(ch if ch.isalnum() or ch in {"_", "."} else "_" for ch in s).strip("_")


def _split_nation_tier(folder_name: str) -> tuple[str, str]:
    m = re.match(r"^(.*)\s+(T\d+)$", folder_name.strip())
    if not m:
        return folder_name.strip(), ""
    return m.group(1).strip(), m.group(2).strip()


def _schedule_root(base_dir: Path) -> Path:
    return base_dir / "data" / "Schedule"


def _events_root(base_dir: Path) -> Path:
    p1 = base_dir / "data" / "Event Data"
    p2 = base_dir / "data" / "Event data"
    if p1.exists():
        return p1
    if p2.exists():
        return p2
    p1.mkdir(parents=True, exist_ok=True)
    return p1


def _failed_root(base_dir: Path) -> Path:
    return _events_root(base_dir) / "_failed"


def _events_scope_root(base_dir: Path, nation: str, tier: str = '') -> Path:
    root = _events_root(base_dir) / _safe_slug(nation)
    if str(tier or '').strip():
        root = root / _safe_slug(tier)
    return root

def _failed_csv_path(base_dir: Path, nation: str, tier: str, season_choice: str) -> Path:
    return _failed_root(base_dir) / _safe_slug(nation) / _safe_slug(tier or 'T1') / f"{_safe_slug(season_choice)}.csv"


def _positions_root(base_dir: Path) -> Path:
    return _events_root(base_dir) / "_positions"


def _positions_csv_path(base_dir: Path, nation: str, tier: str, season_choice: str) -> Path:
    return _positions_root(base_dir) / _safe_slug(nation) / _safe_slug(tier or 'T1') / f"{_safe_slug(season_choice)}.csv"


def _schedule_finished_mask(df: pd.DataFrame) -> pd.Series:
    mask = pd.Series(False, index=df.index)

    if "status" in df.columns:
        status_num = pd.to_numeric(df["status"], errors="coerce")
        status_txt = df["status"].astype(str).str.upper().str.strip()
        mask = mask | status_num.eq(6)
        mask = mask | status_txt.isin(["FT", "AET", "PEN", "FT."])

    if "elapsed" in df.columns:
        elapsed_txt = df["elapsed"].astype(str).str.upper().str.strip()
        mask = mask | elapsed_txt.isin(["FT", "AET", "PEN", "FULL TIME", "FULLTIME"])

    return mask.fillna(False)


def _schedule_sort_datetime(df: pd.DataFrame) -> pd.Series:
    for col in ["started_at_utc", "start_time", "date"]:
        if col in df.columns:
            dt = pd.to_datetime(df[col], errors="coerce", utc=True)
            if dt.notna().any():
                return dt
    return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")


def _list_schedule_folders(schedule_root: Path) -> list[str]:
    if not schedule_root.exists():
        return []
    return sorted([p.name for p in schedule_root.iterdir() if p.is_dir()])


def _nation_to_folders(schedule_root: Path) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for folder in _list_schedule_folders(schedule_root):
        nation, _tier = _split_nation_tier(folder)
        mapping.setdefault(nation, []).append(folder)
    for k in mapping:
        mapping[k] = sorted(mapping[k])
    return dict(sorted(mapping.items(), key=lambda x: x[0]))


def _is_selectable_schedule_csv(path: Path) -> bool:
    stem = path.stem.lower().strip()
    name = path.name.lower().strip()

    if not path.is_file():
        return False
    if name.endswith(".tmp") or name.endswith(".crdownload"):
        return False
    if "__backup" in stem or stem.endswith("_backup") or "backup" in stem:
        return False
    if stem.startswith("~") or stem.startswith("."):
        return False

    return True


def _list_seasons_for_folder(folder_path: Path) -> list[str]:
    if not folder_path.exists():
        return []

    seasons = []
    for path in folder_path.glob("*.csv"):
        if _is_selectable_schedule_csv(path):
            seasons.append(path.stem)

    return sorted(dict.fromkeys(seasons))


def _norm_team_name(s: str) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = s.replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def _url_slug_part(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _season_part_for_match_url(league: str, season: str | int) -> str:
    text = str(season or "").strip()
    mode = str(LEAGUE_PRESETS.get(_league_key(league), {}).get("season_mode", "split")).strip().lower()

    if mode == "calendar":
        year_match = re.search(r"20\d{2}", text)
        return year_match.group(0) if year_match else _url_slug_part(text)

    if re.fullmatch(r"\d{4}", text) and text.startswith("20"):
        year = int(text)
        return f"{year}-{year + 1}"

    if re.fullmatch(r"\d{4}", text):
        return f"20{text[:2]}-20{text[2:]}"

    if re.fullmatch(r"\d{4}/\d{4}", text) or re.fullmatch(r"\d{4}-\d{4}", text):
        years = re.findall(r"\d{4}", text)
        if len(years) >= 2:
            return f"{years[0]}-{years[1]}"

    return _url_slug_part(text)


def _league_part_for_match_url(league: str) -> str:
    league_key = _league_key(league)
    meta = LEAGUE_PRESETS.get(league_key, {})
    raw_slug = str(meta.get("slug") or league_key or league or "")
    return _url_slug_part(raw_slug)


def _build_match_url_slug(league: str, season: str | int, home_team: str, away_team: str) -> str:
    parts = [
        _league_part_for_match_url(league),
        _season_part_for_match_url(league, season),
        _url_slug_part(home_team),
        _url_slug_part(away_team),
    ]
    return "-".join(part for part in parts if part)


def _make_cached_ws(**kwargs) -> sd.WhoScored:
    ws = sd.WhoScored(**kwargs)
    _schedule_cache: dict[tuple[bool], pd.DataFrame] = {}
    _original_read_schedule = ws.read_schedule

    def _cached_read_schedule(self, force_cache: bool = False) -> pd.DataFrame:
        key = (bool(force_cache),)
        cached = _schedule_cache.get(key)
        if isinstance(cached, pd.DataFrame):
            return cached.copy()
        df = _original_read_schedule(force_cache=force_cache)
        _schedule_cache[key] = df.copy()
        return df

    ws.read_schedule = MethodType(_cached_read_schedule, ws)
    return ws


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


def _read_csv_resilient(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    except pd.errors.ParserError:
        repaired = _read_csv_with_repaired_field_counts(path)
        if not repaired.empty:
            return repaired
        try:
            return pd.read_csv(path, engine="python", on_bad_lines="skip")
        except pd.errors.EmptyDataError:
            return pd.DataFrame()


def _read_schedule_csv(path: Path) -> pd.DataFrame:
    return _read_csv_resilient(path)


def _get_match_id_col(df: pd.DataFrame) -> str | None:
    for c in ["game_id", "match_id", "matchid", "id"]:
        if c in df.columns:
            return c
    return None


def _get_home_away_id_cols(df: pd.DataFrame) -> tuple[str | None, str | None]:
    home_candidates = ["home_team_id", "home_id", "homeTeamId"]
    away_candidates = ["away_team_id", "away_id", "awayTeamId"]
    home_id_col = next((c for c in home_candidates if c in df.columns), None)
    away_id_col = next((c for c in away_candidates if c in df.columns), None)
    return home_id_col, away_id_col


def _get_event_team_id_col(df: pd.DataFrame) -> str | None:
    for c in ["team_id", "teamId"]:
        if c in df.columns:
            return c
    return None


def _get_home_away_cols(df: pd.DataFrame) -> tuple[str | None, str | None]:
    home_candidates = ["home_team", "home", "homeTeam", "home_team_name"]
    away_candidates = ["away_team", "away", "awayTeam", "away_team_name"]
    home_col = next((c for c in home_candidates if c in df.columns), None)
    away_col = next((c for c in away_candidates if c in df.columns), None)
    return home_col, away_col


def _get_event_team_col(df: pd.DataFrame) -> str | None:
    for c in ["team", "team_name", "teamName"]:
        if c in df.columns:
            return c
    return None


def _first_existing_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _coalesce_text_cols(df: pd.DataFrame, candidates: list[str]) -> tuple[pd.Series, list[str]]:
    present = [c for c in candidates if c in df.columns]
    if not present:
        return pd.Series([""] * len(df), index=df.index, dtype="object"), []

    out = pd.Series([""] * len(df), index=df.index, dtype="object")
    for c in present:
        s = (
            df[c]
            .astype(str)
            .replace({"nan": "", "None": "", "<NA>": ""})
            .fillna("")
            .str.strip()
        )
        take = out.eq("") & s.ne("")
        out.loc[take] = s.loc[take]
    return out, present


def _normalise_ws_position(raw: Any) -> str:
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return "Unknown"

    s = unicodedata.normalize("NFKD", str(raw)).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\s+", " ", s).strip(" ,;|")
    if not s:
        return "Unknown"

    m = re.match(r"^([A-Z]{1,4}(?:\([A-Z]+\))?(?:,[A-Z]{1,4}(?:\([A-Z]+\))?)*)\s+\(", s)
    if m:
        s = m.group(1).strip()

    token_re = re.compile(
        r"(Sub|GK|(?:FW|AM|WB|DM|D|M)(?:\([CLR]+\)|[CLR])?(?:,(?:FW|AM|WB|DM|D|M)(?:\([CLR]+\)|[CLR])?)*)"
    )
    hits = token_re.findall(s)
    if hits:
        s = hits[0].strip()

    s = s.strip(" ,;|")
    return s if s else "Unknown"


def _primary_ws_position(raw: Any) -> str:
    s = _normalise_ws_position(raw)
    if s == "Unknown":
        return s
    return s.split(",")[0].strip() or s


def _position_group_from_ws(pos: str) -> str:
    s = str(pos or "").strip().upper()

    if s in {"UNKNOWN", "", "SUB"}:
        return ""

    if s == "GK":
        return "GK"

    # Centre backs
    if s in {"CB", "LCB", "RCB", "DCL", "DCR", "DC"}:
        return "CB"

    # Full backs
    if s in {"LB", "RB", "DL", "DR", "FB"}:
        return "FB"

    # Wing backs
    if s in {"LWB", "RWB", "WB", "WBL", "WBR"}:
        return "WB"

    # Defensive midfield
    if s.startswith("DM"):
        return "DM"

    # Central midfield
    if s in {"CM", "CMF", "MC", "LCM", "RCM", "LCMF", "RCMF", "MCL", "MCR", "MF"}:
        return "CM"

    # Attacking midfield
    if s in {"AM", "AMC", "AMF", "CAM", "SS", "10"}:
        return "AM"

    # Wide midfielders
    if s in {"LM", "RM", "AML", "AMR", "LAM", "RAM", "ML", "MR"}:
        return "WM"

    # Wide forwards
    if s in {"LW", "RW", "LWF", "RWF", "FWL", "FWR", "WF", "W"}:
        return "WF"

    # Centre forwards
    if s in {"CF", "ST", "FW", "FWC", "LF", "RF", "9"}:
        return "CF"

    # Older WhoScored style generic defensive strings
    if s.startswith("D"):
        if "WB" in s:
            return "WB"
        if "C" in s and "L" not in s and "R" not in s:
            return "CB"
        if "L" in s or "R" in s:
            return "FB"
        return "DEF"

    # Older WhoScored attacking mid strings
    if s.startswith("AM"):
        if "L" in s or "R" in s:
            return "WM"
        if "C" in s:
            return "AM"
        return "AM"

    # Older WhoScored midfield strings
    if s.startswith("M"):
        if "L" in s or "R" in s:
            return "WM"
        if "C" in s:
            return "CM"
        return "MID"

    # Older WhoScored forward strings
    if s.startswith("FW"):
        if "L" in s or "R" in s:
            return "WF"
        return "CF"

    return ""

def _display_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("displayName") or value.get("name") or value.get("field") or value.get("value") or "")
    return str(value or "")


def _to_int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        if pd.isna(number):
            return None
        return int(number)
    except Exception:
        return None


def _extract_balanced_js_object(text: str, start_pos: int) -> str | None:
    open_pos = text.find("{", start_pos)
    if open_pos < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    quote_char = ""

    for idx in range(open_pos, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote_char:
                in_string = False
            continue

        if ch in {"'", '"'}:
            in_string = True
            quote_char = ch
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_pos: idx + 1]

    return None


def _extract_match_centre_data_from_source(source: str) -> dict[str, Any] | None:
    if not source:
        return None

    assignment_names = [
        "matchCentreData",
        "matchCenterData",
        "window.matchCentreData",
        "window.matchCenterData",
        "require.config.params['args'].matchCentreData",
        'require.config.params["args"].matchCentreData',
    ]

    for name in assignment_names:
        for match in re.finditer(rf"(?:var\s+)?{re.escape(name)}\s*=", source):
            raw = _extract_balanced_js_object(source, match.end())
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if isinstance(data, dict) and isinstance(data.get("events"), list) and data.get("events"):
                return data

    for key_pattern in [
        r'"matchCentreData"\s*:',
        r"'matchCentreData'\s*:",
        r'"matchCenterData"\s*:',
        r"'matchCenterData'\s*:",
    ]:
        for match in re.finditer(key_pattern, source):
            raw = _extract_balanced_js_object(source, match.end())
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if isinstance(data, dict) and isinstance(data.get("events"), list) and data.get("events"):
                return data

    return None


def _json_dict_from_maybe_string(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if value is None:
        return None

    text = str(value).strip()
    if not text or text in {"null", "undefined"}:
        return None

    try:
        parsed = json.loads(text)
    except Exception:
        return None

    return parsed if isinstance(parsed, dict) else None


def _valid_match_centre_data(value: Any) -> dict[str, Any] | None:
    data = _json_dict_from_maybe_string(value)
    if isinstance(data, dict) and isinstance(data.get("events"), list) and data.get("events"):
        return data
    return None


def _read_match_centre_data_from_browser_context(sb: Any) -> dict[str, Any] | None:
    script = """
        (() => {
            const safeJson = (value) => {
                try {
                    return value ? JSON.stringify(value) : "";
                } catch (error) {
                    return "";
                }
            };

            const fromRequire =
                window.require &&
                window.require.config &&
                window.require.config.params &&
                window.require.config.params.args
                    ? window.require.config.params.args.matchCentreData
                    : null;

            const candidates = [
                window.matchCentreData || null,
                window.matchCenterData || null,
                fromRequire
            ];

            for (const item of candidates) {
                if (item && item.events && item.events.length) {
                    return safeJson(item);
                }
            }

            return "";
        })()
    """

    cdp = getattr(sb, "cdp", None)

    if cdp is not None:
        for method_name in ["evaluate", "execute_script"]:
            method = getattr(cdp, method_name, None)
            if method is None:
                continue
            try:
                data = _valid_match_centre_data(method(script))
                if data is not None:
                    return data
            except Exception:
                continue

        js_dumps = getattr(cdp, "js_dumps", None)
        if js_dumps is not None:
            for obj_name in [
                "require.config.params['args'].matchCentreData",
                'require.config.params["args"].matchCentreData',
                "window.matchCentreData",
                "window.matchCenterData",
            ]:
                try:
                    data = _valid_match_centre_data(js_dumps(obj_name))
                    if data is not None:
                        return data
                except Exception:
                    continue

    try:
        data = _valid_match_centre_data(
            sb.execute_script(
                "const fromRequire = window.require && window.require.config && "
                "window.require.config.params && window.require.config.params.args "
                "? window.require.config.params.args.matchCentreData : null; "
                "const data = window.matchCentreData || window.matchCenterData || fromRequire || null; "
                "return data ? JSON.stringify(data) : '';"
            )
        )
        if data is not None:
            return data
    except Exception:
        pass

    return None


def _read_title_and_source_from_browser(sb: Any) -> tuple[str, str]:
    title = ""
    source = ""

    cdp = getattr(sb, "cdp", None)
    if cdp is not None:
        try:
            title = cdp.get_title() or ""
        except Exception:
            title = ""
        try:
            source = cdp.get_page_source() or ""
        except Exception:
            source = ""

    if not title:
        try:
            title = sb.get_title() or ""
        except Exception:
            title = ""

    if not source:
        try:
            source = sb.get_page_source() or ""
        except Exception:
            source = ""

    return title, source


def _open_url_for_match_centre(sb: Any, url: str) -> None:
    driver = getattr(sb, "driver", None) or getattr(sb, "_driver", None)

    if driver is not None:
        opener = getattr(driver, "uc_open_with_reconnect", None)
        if opener is not None:
            try:
                opener(url, reconnect_time=4)
                return
            except TypeError:
                try:
                    opener(url, 4)
                    return
                except Exception:
                    pass
            except Exception:
                pass

    try:
        sb.activate_cdp_mode(url)
        return
    except Exception:
        pass

    cdp = getattr(sb, "cdp", None)
    if cdp is not None:
        try:
            cdp.open(url)
            return
        except Exception:
            pass

    if driver is not None:
        default_get = getattr(driver, "default_get", None)
        if default_get is not None:
            try:
                default_get(url)
                return
            except Exception:
                pass

    sb.open(url)


def _team_meta_from_match_centre(data: dict[str, Any]) -> dict[int, str]:
    teams: dict[int, str] = {}
    for side in ["home", "away"]:
        item = data.get(side)
        if not isinstance(item, dict):
            continue

        team_id = item.get("teamId") or item.get("id")
        team_name = item.get("name") or item.get("teamName")
        try:
            if team_id is not None and team_name:
                teams[int(team_id)] = str(team_name)
        except Exception:
            continue

    return teams


def _player_name_map_from_match_centre(data: dict[str, Any]) -> dict[int, str]:
    out: dict[int, str] = {}

    raw_map = data.get("playerIdNameDictionary")
    if isinstance(raw_map, dict):
        for key, value in raw_map.items():
            try:
                out[int(key)] = str(value)
            except Exception:
                continue

    for side in ["home", "away"]:
        team_obj = data.get(side)
        if not isinstance(team_obj, dict):
            continue

        players = team_obj.get("players")
        if not isinstance(players, list):
            continue

        for player in players:
            if not isinstance(player, dict):
                continue
            pid = player.get("playerId") or player.get("id")
            name = player.get("name") or player.get("playerName")
            try:
                if pid is not None and name:
                    out[int(pid)] = str(name)
            except Exception:
                continue

    return out


def _period_display(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("displayName") or value.get("value") or value.get("name")
    return value


def _event_type_display(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("displayName") or value.get("name") or value.get("value") or "")
    return str(value or "")


def _qualifier_display(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    q_type = item.get("type")
    if isinstance(q_type, dict):
        q_type = q_type.get("displayName") or q_type.get("name") or q_type.get("value")

    name = str(q_type or "").strip()
    if not name:
        return None

    return {
        "type": {"displayName": name},
        "value": item.get("value"),
    }


def _events_dataframe_from_match_centre(match_id: int, data: dict[str, Any]) -> pd.DataFrame:
    events = data.get("events")
    if not isinstance(events, list) or not events:
        return pd.DataFrame()

    team_map = _team_meta_from_match_centre(data)
    player_map = _player_name_map_from_match_centre(data)

    rows: list[dict[str, Any]] = []

    for raw in events:
        if not isinstance(raw, dict):
            continue

        team_id = raw.get("teamId") or raw.get("team_id")
        player_id = raw.get("playerId") or raw.get("player_id")
        team_id_int = _to_int_or_none(team_id)
        player_id_int = _to_int_or_none(player_id)
        event_type = _event_type_display(raw.get("type"))
        outcome = _event_type_display(raw.get("outcomeType"))

        qualifiers = []
        for qualifier in raw.get("qualifiers") or []:
            cleaned = _qualifier_display(qualifier)
            if cleaned is not None:
                qualifiers.append(cleaned)

        row = dict(raw)
        row.update(
            {
                "match_id": int(match_id),
                "team_id": team_id_int if team_id_int is not None else np.nan,
                "team": team_map.get(team_id_int, "") if team_id_int is not None else "",
                "player_id": player_id_int if player_id_int is not None else np.nan,
                "player": player_map.get(player_id_int, "") if player_id_int is not None else "",
                "type": event_type,
                "outcome_type": outcome,
                "period": _period_display(raw.get("period")),
                "minute": raw.get("minute"),
                "second": raw.get("second"),
                "expanded_minute": raw.get("expandedMinute") or raw.get("expanded_minute"),
                "x": raw.get("x"),
                "y": raw.get("y"),
                "end_x": raw.get("endX") or raw.get("end_x"),
                "end_y": raw.get("endY") or raw.get("end_y"),
                "qualifiers": json.dumps(qualifiers, ensure_ascii=False),
                "is_touch": bool(raw.get("isTouch") or raw.get("is_touch") or False),
                "is_shot": bool(raw.get("isShot") or raw.get("is_shot") or False),
                "is_goal": bool(raw.get("isGoal") or raw.get("is_goal") or False),
                "goal_mouth_x": raw.get("goalMouthX") or raw.get("goal_mouth_x"),
                "goal_mouth_y": raw.get("goalMouthY") or raw.get("goal_mouth_y"),
                "goal_mouth_z": raw.get("goalMouthZ") or raw.get("goal_mouth_z"),
                "blocked_x": raw.get("blockedX") or raw.get("blocked_x"),
                "blocked_y": raw.get("blockedY") or raw.get("blocked_y"),
            }
        )
        rows.append(row)

    return pd.DataFrame(rows)


def _positions_from_match_centre(data: dict[str, Any], match_id: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for side in ["home", "away"]:
        team_obj = data.get(side)
        if not isinstance(team_obj, dict):
            continue

        team_id = team_obj.get("teamId") or team_obj.get("id")
        team_name = team_obj.get("name") or team_obj.get("teamName") or ""

        players = team_obj.get("players")
        if not isinstance(players, list):
            continue

        for player in players:
            if not isinstance(player, dict):
                continue

            player_id = player.get("playerId") or player.get("id")
            raw_pos = (
                player.get("position")
                or player.get("positionText")
                or player.get("field")
                or player.get("usualPosition")
                or player.get("role")
                or ""
            )

            rows.append(
                {
                    "match_id": int(match_id),
                    "team_id": team_id,
                    "team": team_name,
                    "player_id": player_id,
                    "player": player.get("name") or player.get("playerName") or "",
                    "is_starter": player.get("isFirstEleven") or player.get("isStarter") or "",
                    "mins_played": player.get("minsPlayed") or player.get("minutes") or np.nan,
                    "shirt_no": player.get("shirtNo") or player.get("shirtNumber") or "",
                    "player_position": _normalise_ws_position(raw_pos),
                }
            )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["position"] = df["player_position"].map(_primary_ws_position)
    df["position_group"] = df["position"].map(_position_group_from_ws)
    return df


def _open_match_centre_data_from_browser(
    match_id: int,
    browserpath: str | None,
    headless: bool,
    match_slug: str = "",
) -> dict[str, Any] | None:
    urls: list[str] = []

    clean_slug = _url_slug_part(match_slug)
    if clean_slug:
        urls.extend(
            [
                f"https://www.whoscored.com/matches/{int(match_id)}/live/{clean_slug}",
                f"https://www.whoscored.com/matches/{int(match_id)}/show/{clean_slug}",
                f"https://www.whoscored.com/matches/{int(match_id)}/matchreport/{clean_slug}",
                f"https://www.whoscored.com/matches/{int(match_id)}/livestatistics/{clean_slug}",
            ]
        )

    urls.extend(
        [
            f"https://www.whoscored.com/Matches/{int(match_id)}/Live",
            f"https://www.whoscored.com/Matches/{int(match_id)}/LiveStatistics",
            f"https://www.whoscored.com/Matches/{int(match_id)}/MatchReport",
            f"https://www.whoscored.com/matches/{int(match_id)}/live",
            f"https://www.whoscored.com/matches/{int(match_id)}/show",
        ]
    )
    urls = list(dict.fromkeys(urls))

    last_title = ""
    last_hint = ""

    with setup_driver(headless=headless, browserpath=browserpath, uc=True) as sb:
        for url in urls:
            _open_url_for_match_centre(sb, url)

            source = ""
            for _attempt in range(10):
                time.sleep(random.uniform(2.5, 4.0))

                data = _read_match_centre_data_from_browser_context(sb)
                if isinstance(data, dict) and isinstance(data.get("events"), list) and data.get("events"):
                    return data

                last_title, source = _read_title_and_source_from_browser(sb)

                hint = f"{last_title} {source[:2500]}".lower()
                if "just a moment" in hint or "challenges.cloudflare.com" in hint:
                    last_hint = "WhoScored returned a browser challenge page."
                    cdp = getattr(sb, "cdp", None)
                    click_captcha = getattr(cdp, "gui_click_captcha", None) if cdp is not None else None
                    if click_captcha is not None:
                        try:
                            click_captcha()
                        except Exception:
                            pass
                    continue

                data = _extract_match_centre_data_from_source(source)
                if isinstance(data, dict) and isinstance(data.get("events"), list) and data.get("events"):
                    return data

                if any(marker in source for marker in [
                    "matchCentreData",
                    "matchCenterData",
                    "require.config.params",
                ]):
                    break

    if last_hint:
        raise RuntimeError(
            f"{last_hint} Open the match page in the visible Chromium window, clear the challenge, then retry with Headless unticked."
        )

    raise RuntimeError(f"Could not extract matchCentreData for match {match_id}. Last page title: {last_title or 'unknown'}")


def _read_events_force_cached(ws_obj: Any, match_id: int, output_fmt: str):
    call_variants = [
        {
            "match_id": match_id,
            "output_fmt": output_fmt,
            "force_cache": True,
            "retry_missing": True,
            "on_error": "raise",
        },
        {
            "match_id": match_id,
            "output_fmt": output_fmt,
            "force_cache": True,
            "retry_missing": True,
        },
        {
            "match_id": match_id,
            "output_fmt": output_fmt,
            "force_cache": True,
        },
        {
            "match_id": match_id,
            "output_fmt": output_fmt,
        },
    ]

    last_type_error: TypeError | None = None
    for kwargs in call_variants:
        try:
            return ws_obj.read_events(**kwargs)
        except TypeError as exc:
            last_type_error = exc
            continue

    if last_type_error is not None:
        raise last_type_error

    return ws_obj.read_events(match_id=match_id, output_fmt=output_fmt)


def _scrape_match_bundle_from_match_page(
    match_id: int,
    browserpath: str | None,
    headless: bool,
    require_positions: bool = True,
    league: str = "",
    season: str | int = "",
    home_team: str = "",
    away_team: str = "",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    match_slug = _build_match_url_slug(league, season, home_team, away_team)
    data = _open_match_centre_data_from_browser(
        match_id=match_id,
        browserpath=browserpath,
        headless=headless,
        match_slug=match_slug,
    )

    df_events = _events_dataframe_from_match_centre(match_id, data)
    if not isinstance(df_events, pd.DataFrame) or df_events.empty:
        raise RuntimeError("Custom WhoScored match page fallback returned an empty events DataFrame.")

    pos_df = _positions_from_match_centre(data, match_id) if require_positions else pd.DataFrame()
    return df_events, pos_df


def _scrape_match_bundle(ws_obj, match_id: int, require_positions: bool = True):
    df_events = _read_events_force_cached(ws_obj, match_id=match_id, output_fmt="events")

    if not isinstance(df_events, pd.DataFrame) or df_events.empty:
        raise RuntimeError("WhoScored returned an empty events DataFrame.")

    pos_df = pd.DataFrame()

    if require_positions:
        try:
            loader = _read_events_force_cached(ws_obj, match_id=match_id, output_fmt="loader")
            df_players = loader.players(game_id=match_id)
            pos_df = _build_positions_from_players_df(df_players, match_id)
        except Exception:
            pos_df = pd.DataFrame()

    return df_events, pos_df

def _build_positions_from_players_df(df_players: pd.DataFrame, match_id: int) -> pd.DataFrame:
    empty = pd.DataFrame(
        columns=[
            "match_id",
            "team_id",
            "team",
            "player_id",
            "player",
            "is_starter",
            "mins_played",
            "shirt_no",
            "player_position",
            "position",
            "position_group",
        ]
    )

    if df_players is None or not isinstance(df_players, pd.DataFrame) or df_players.empty:
        return empty

    df = df_players.copy()

    pid_col = _first_existing_col(df, ["player_id", "playerId", "wyId", "ws_player_id", "id"])
    tid_col = _first_existing_col(df, ["team_id", "teamId", "ws_team_id"])
    team_col = _first_existing_col(df, ["team", "team_name", "teamName", "team_short_name", "teamShortName", "squad", "club", "club_name"])
    name_col = _first_existing_col(df, ["player", "player_name", "playerName", "name", "full_name", "fullName", "shortName", "short_name"])
    starter_col = _first_existing_col(df, ["is_starter", "isStarter", "starter", "is_first_eleven", "isFirstEleven", "starting", "starting_xi"])
    mins_col = _first_existing_col(df, ["mins_played", "minutes_played", "minsPlayed", "minutes"])
    shirt_col = _first_existing_col(df, ["shirt_no", "shirtNo", "jersey_number", "shirt"])

    pos_series, _used_pos_cols = _coalesce_text_cols(
        df,
        [
            "starting_position",
            "startingPosition",
            "position",
            "player_position",
            "position_name",
            "positionName",
            "position_text",
            "positionText",
            "pos",
            "role",
            "role_name",
            "roleName",
            "start_position",
            "startPosition",
            "primary_position",
            "primaryPosition",
        ],
    )

    out = pd.DataFrame(index=df.index)
    out["match_id"] = int(match_id)
    out["team_id"] = pd.to_numeric(df[tid_col], errors="coerce") if tid_col else np.nan
    out["team"] = df[team_col].astype(str) if team_col else ""
    out["player_id"] = pd.to_numeric(df[pid_col], errors="coerce") if pid_col else np.nan
    out["player"] = df[name_col].astype(str) if name_col else ""
    out["is_starter"] = df[starter_col].astype(str) if starter_col else ""
    out["mins_played"] = pd.to_numeric(df[mins_col], errors="coerce") if mins_col else np.nan
    out["shirt_no"] = df[shirt_col].astype(str) if shirt_col else ""
    out["player_position"] = pos_series.map(_normalise_ws_position)
    out["position"] = out["player_position"].map(_primary_ws_position)
    out["position_group"] = out["position"].map(_position_group_from_ws)

    out = out.dropna(subset=["player_id"], how="all")
    return out.reset_index(drop=True)


def _append_positions_csv(out_path: Path, df_pos: pd.DataFrame) -> int:
    if df_pos is None or df_pos.empty:
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists():
        try:
            prev = _read_csv_resilient(out_path)
            df_all = pd.concat([prev, df_pos], ignore_index=True, sort=False)
        except Exception:
            df_all = df_pos.copy()
    else:
        df_all = df_pos.copy()

    for c in ["match_id", "team_id", "player_id"]:
        if c in df_all.columns:
            df_all[c] = pd.to_numeric(df_all[c], errors="coerce")

    dedupe_cols = [c for c in ["match_id", "team_id", "player_id"] if c in df_all.columns]
    if dedupe_cols:
        df_all["_rank"] = df_all["player_position"].astype(str).eq("Unknown").astype(int)
        df_all = (
            df_all.sort_values(["_rank"])
            .drop_duplicates(subset=dedupe_cols, keep="first")
            .drop(columns=["_rank"], errors="ignore")
        )

    df_all.to_csv(out_path, index=False)
    return len(df_pos)

def _append_positions_to_events(df_ev: pd.DataFrame, pos_df: pd.DataFrame) -> pd.DataFrame:
    if df_ev is None or df_ev.empty or pos_df is None or pos_df.empty:
        return df_ev

    out = df_ev.copy()
    pos = pos_df.copy()

    for c, default in {
        "player_position": "Unknown",
        "position": "Unknown",
        "position_group": "",
    }.items():
        if c not in out.columns:
            out[c] = default

    evt_pid_col = _first_existing_col(out, ["player_id", "playerId"])
    evt_tid_col = _first_existing_col(out, ["team_id", "teamId"])
    evt_name_col = _first_existing_col(out, ["player", "player_name", "playerName", "name"])

    # First pass: merge on ids
    if evt_pid_col and "player_id" in pos.columns:
        out["_merge_player_id"] = pd.to_numeric(out[evt_pid_col], errors="coerce")
        pos["_merge_player_id"] = pd.to_numeric(pos["player_id"], errors="coerce")

        left_keys = ["_merge_player_id"]
        right_keys = ["_merge_player_id"]

        if evt_tid_col and "team_id" in pos.columns:
            out["_merge_team_id"] = pd.to_numeric(out[evt_tid_col], errors="coerce")
            pos["_merge_team_id"] = pd.to_numeric(pos["team_id"], errors="coerce")
            left_keys.append("_merge_team_id")
            right_keys.append("_merge_team_id")

        pos_id = (
            pos[right_keys + ["player_position", "position", "position_group"]]
            .drop_duplicates(subset=right_keys)
            .rename(
                columns={
                    "player_position": "player_position__map",
                    "position": "position__map",
                    "position_group": "position_group__map",
                }
            )
        )

        out = out.merge(pos_id, left_on=left_keys, right_on=right_keys, how="left")

        for src, dst in [
            ("player_position__map", "player_position"),
            ("position__map", "position"),
            ("position_group__map", "position_group"),
        ]:
            if src in out.columns:
                out[dst] = out[dst].where(
                    out[dst].astype(str).str.strip().ne("")
                    & out[dst].notna()
                    & out[dst].astype(str).ne("Unknown"),
                    out[src],
                )

    # Second pass: fallback on player name
    if evt_name_col and "player" in pos.columns:
        out["_merge_player_name"] = out[evt_name_col].astype(str).str.strip().str.lower()
        pos["_merge_player_name"] = pos["player"].astype(str).str.strip().str.lower()

        name_keys_left = ["_merge_player_name"]
        name_keys_right = ["_merge_player_name"]

        if evt_tid_col and "team_id" in pos.columns and "_merge_team_id" in out.columns:
            name_keys_left.append("_merge_team_id")
            name_keys_right.append("_merge_team_id")

        pos_name = (
            pos[name_keys_right + ["player_position", "position", "position_group"]]
            .drop_duplicates(subset=name_keys_right)
            .rename(
                columns={
                    "player_position": "player_position__name",
                    "position": "position__name",
                    "position_group": "position_group__name",
                }
            )
        )

        out = out.merge(pos_name, left_on=name_keys_left, right_on=name_keys_right, how="left")

        for src, dst in [
            ("player_position__name", "player_position"),
            ("position__name", "position"),
            ("position_group__name", "position_group"),
        ]:
            if src in out.columns:
                out[dst] = out[dst].where(
                    out[dst].astype(str).str.strip().ne("")
                    & out[dst].notna()
                    & out[dst].astype(str).ne("Unknown"),
                    out[src],
                )

    out["player_position"] = out["player_position"].fillna("Unknown").astype(str)
    out["position"] = out["position"].fillna("Unknown").astype(str)
    out["position_group"] = out["position_group"].fillna("").astype(str)

    drop_cols = [
        "_merge_player_id",
        "_merge_team_id",
        "_merge_player_name",
        "player_position__map",
        "position__map",
        "position_group__map",
        "player_position__name",
        "position__name",
        "position_group__name",
    ]
    return out.drop(columns=[c for c in drop_cols if c in out.columns], errors="ignore")

def _read_existing_match_ids(team_season_csv: Path) -> set[int]:
    if not team_season_csv.exists():
        return set()

    try:
        df = _read_csv_resilient(team_season_csv)
    except Exception:
        return set()

    col = _get_match_id_col(df)
    if not col:
        return set()

    s = pd.to_numeric(df[col], errors="coerce").dropna().astype(int)
    return set(s.unique().tolist())


def _merge_existing_flat_season_ids(existing_by_team: dict[str, set[int]], scope_root: Path, season: str) -> None:
    season_file = scope_root / f"{_safe_slug(season)}.csv"
    if not season_file.exists():
        return

    try:
        df = _read_csv_resilient(season_file)
    except Exception:
        return
    if df.empty:
        return

    match_col = _get_match_id_col(df)
    team_col = _get_event_team_col(df)
    if not match_col or not team_col:
        return

    team_lookup = {_norm_team_name(team): team for team in existing_by_team}
    ids = pd.to_numeric(df[match_col], errors="coerce")
    work = df.loc[ids.notna(), [team_col]].copy()
    if work.empty:
        return
    work["__match_id"] = ids.loc[ids.notna()].astype(int).values

    for team_value, group in work.groupby(team_col, dropna=True):
        raw_key = _norm_team_name(team_value)
        canonical_team = team_lookup.get(raw_key)
        if canonical_team is None:
            matches = [team for key, team in team_lookup.items() if key and (raw_key == key or raw_key.startswith(key) or key.startswith(raw_key))]
            canonical_team = matches[0] if matches else None
        if canonical_team is None:
            continue
        existing_by_team.setdefault(canonical_team, set()).update(group["__match_id"].astype(int).tolist())


def _read_existing_match_row_counts(team_season_csv: Path) -> dict[int, int]:
    if not team_season_csv.exists():
        return {}

    try:
        df = _read_csv_resilient(team_season_csv)
    except Exception:
        return {}

    if df.empty:
        return {}

    match_col = _get_match_id_col(df)
    if not match_col:
        return {}

    ids = pd.to_numeric(df[match_col], errors="coerce")
    work = df.loc[ids.notna()].copy()
    if work.empty:
        return {}

    work["__match_id"] = ids.loc[ids.notna()].astype(int).values
    return {int(match_id): int(count) for match_id, count in work.groupby("__match_id").size().items()}


def _merge_existing_flat_season_row_counts(
    row_counts_by_team: dict[str, dict[int, int]],
    scope_root: Path,
    season: str,
) -> None:
    season_file = scope_root / f"{_safe_slug(season)}.csv"
    if not season_file.exists():
        return

    try:
        df = _read_csv_resilient(season_file)
    except Exception:
        return
    if df.empty:
        return

    match_col = _get_match_id_col(df)
    team_col = _get_event_team_col(df)
    if not match_col or not team_col:
        return

    team_lookup = {_norm_team_name(team): team for team in row_counts_by_team}
    ids = pd.to_numeric(df[match_col], errors="coerce")
    work = df.loc[ids.notna(), [team_col]].copy()
    if work.empty:
        return
    work["__match_id"] = ids.loc[ids.notna()].astype(int).values

    for team_value, group in work.groupby(team_col, dropna=True):
        raw_key = _norm_team_name(team_value)
        canonical_team = team_lookup.get(raw_key)
        if canonical_team is None:
            matches = [team for key, team in team_lookup.items() if key and (raw_key == key or raw_key.startswith(key) or key.startswith(raw_key))]
            canonical_team = matches[0] if matches else None
        if canonical_team is None:
            continue

        team_counts = row_counts_by_team.setdefault(canonical_team, {})
        for match_id, count in group.groupby("__match_id").size().items():
            team_counts[int(match_id)] = team_counts.get(int(match_id), 0) + int(count)


def get_event_coverage_audit(
    basedir: Path,
    league: str,
    season: str,
    nation: str = "",
    tier: str = "",
    only_finished: bool = True,
    overwrite: bool = False,
    retry_failed: bool = False,
) -> dict[str, Any]:
    league = _league_key(league)
    nation, tier, auto_resolved_folder = resolve_league_folder(league, nation, tier)
    if not nation or not tier:
        raise ValueError("Could not resolve a schedule folder. Select a known league or provide nation and tier.")

    folder_name = f"{nation} {tier}".strip()
    schedule_path = _schedule_root(basedir) / folder_name / f"{season}.csv"
    if not schedule_path.exists():
        raise FileNotFoundError(f"Schedule CSV not found: {schedule_path}")

    sch_df = _read_schedule_csv(schedule_path)
    if sch_df.empty:
        raise ValueError("Schedule CSV is empty.")

    match_id_col = _get_match_id_col(sch_df)
    home_col, away_col = _get_home_away_cols(sch_df)

    if not match_id_col or not home_col or not away_col:
        raise ValueError("Schedule CSV must contain match id, home team and away team columns.")

    out_root = _events_scope_root(basedir, nation, tier)
    failed_csv = _failed_csv_path(basedir, nation, tier, season)
    failed_records = _load_failed_records(failed_csv)
    prev_failed = set(failed_records.keys())

    meta = sch_df.copy()
    meta[match_id_col] = pd.to_numeric(meta[match_id_col], errors="coerce")
    meta = meta.dropna(subset=[match_id_col]).copy()
    meta[match_id_col] = meta[match_id_col].astype(int)
    meta["_finished"] = _schedule_finished_mask(sch_df).reindex(meta.index).fillna(False).values
    meta["_sort_dt"] = _schedule_sort_datetime(sch_df).reindex(meta.index).values

    teams = sorted(
        set(
            meta[home_col].dropna().astype(str).tolist() +
            meta[away_col].dropna().astype(str).tolist()
        )
    )

    team_paths: dict[str, Path] = {}
    existing_by_team: dict[str, set[int]] = {}
    row_counts_by_team: dict[str, dict[int, int]] = {}

    for team in teams:
        team_path = out_root / _safe_slug(team) / f"{_safe_slug(season)}.csv"
        team_paths[team] = team_path
        existing_ids = _read_existing_match_ids(team_path)
        row_counts = _read_existing_match_row_counts(team_path)
        existing_by_team[team] = set(existing_ids)
        row_counts_by_team[team] = dict(row_counts)

    _merge_existing_flat_season_ids(existing_by_team, out_root, season)
    _merge_existing_flat_season_row_counts(row_counts_by_team, out_root, season)

    finished_ids = set(meta.loc[meta["_finished"].fillna(False), match_id_col].astype(int).tolist())

    if retry_failed:
        candidate_ids = set(prev_failed)
        if only_finished:
            candidate_ids = candidate_ids & finished_ids
    else:
        candidate_ids = finished_ids if only_finished else set(meta[match_id_col].astype(int).tolist())

    rows: list[dict[str, Any]] = []
    failed_rows: list[dict[str, Any]] = []

    counts: dict[str, int] = {
        "matches_in_schedule": int(len(meta)),
        "finished_matches": int(meta["_finished"].fillna(False).sum()),
        "not_finished_matches": int((~meta["_finished"].fillna(False)).sum()),
        "unique_teams": int(len(teams)),
        "with_both_team_events": 0,
        "with_one_team_events": 0,
        "with_no_saved_events": 0,
        "failed_logged": int(len(prev_failed)),
        "scrape_candidates": int(len(candidate_ids)),
        "to_fetch_now": 0,
        "already_complete": 0,
        "skipped_not_finished": 0,
        "skipped_not_in_failed_log": 0,
        "missing_team_names": 0,
        "overwrite_refetches": 0,
    }

    for _, schedule_row in meta.sort_values(["_sort_dt", match_id_col], na_position="last").iterrows():
        match_id = int(schedule_row.get(match_id_col))
        home_team = str(schedule_row.get(home_col, "") or "").strip()
        away_team = str(schedule_row.get(away_col, "") or "").strip()
        is_finished = bool(schedule_row.get("_finished", False))
        sort_dt = schedule_row.get("_sort_dt", "")

        home_saved_current = bool(home_team) and match_id in existing_by_team.get(home_team, set())
        away_saved_current = bool(away_team) and match_id in existing_by_team.get(away_team, set())
        home_rows = int(row_counts_by_team.get(home_team, {}).get(match_id, 0)) if home_team else 0
        away_rows = int(row_counts_by_team.get(away_team, {}).get(match_id, 0)) if away_team else 0

        home_saved_for_plan = home_saved_current
        away_saved_for_plan = away_saved_current
        if overwrite and not retry_failed and match_id in candidate_ids:
            home_saved_for_plan = False
            away_saved_for_plan = False

        saved_sides = int(home_saved_current) + int(away_saved_current)
        if saved_sides == 2:
            data_status = "complete"
            counts["with_both_team_events"] += 1
        elif saved_sides == 1:
            data_status = "partial"
            counts["with_one_team_events"] += 1
        else:
            data_status = "missing"
            counts["with_no_saved_events"] += 1

        failure_record = failed_records.get(match_id, {})
        failure_reason = str(
            failure_record.get("reason")
            or failure_record.get("message")
            or failure_record.get("last_error")
            or ""
        )

        scrape_status = "not_needed"
        if not home_team or not away_team:
            scrape_status = "missing_team_names"
            counts["missing_team_names"] += 1
        elif retry_failed and match_id not in prev_failed:
            scrape_status = "not_in_failed_log"
            counts["skipped_not_in_failed_log"] += 1
        elif only_finished and not is_finished:
            scrape_status = "not_finished"
            counts["skipped_not_finished"] += 1
        elif match_id not in candidate_ids:
            scrape_status = "not_candidate"
        elif home_saved_for_plan and away_saved_for_plan:
            scrape_status = "already_complete"
            counts["already_complete"] += 1
        else:
            scrape_status = "to_fetch"
            counts["to_fetch_now"] += 1
            if overwrite and not retry_failed:
                counts["overwrite_refetches"] += 1

        kicked_off = ""
        if pd.notna(sort_dt):
            try:
                kicked_off = pd.Timestamp(sort_dt).isoformat()
            except Exception:
                kicked_off = str(sort_dt)

        audit_row = {
            "match_id": match_id,
            "kickoff": kicked_off,
            "home_team": home_team,
            "away_team": away_team,
            "finished": is_finished,
            "home_saved": home_saved_current,
            "away_saved": away_saved_current,
            "home_event_rows": home_rows,
            "away_event_rows": away_rows,
            "data_status": data_status,
            "failed_logged": match_id in prev_failed,
            "failure_reason": failure_reason,
            "scrape_status": scrape_status,
        }
        rows.append(audit_row)

        if match_id in prev_failed:
            failed_rows.append(audit_row)

    to_fetch_preview = [row for row in rows if row["scrape_status"] == "to_fetch"][:20]
    missing_preview = [row for row in rows if row["data_status"] in {"missing", "partial"}][:20]
    failed_preview = failed_rows[:20]

    return {
        "kind": "coverage",
        "message": (
            f"{counts['to_fetch_now']} match(es) need event scraping. "
            f"{counts['with_both_team_events']} already have both teams saved. "
            f"{counts['with_no_saved_events']} have no saved event data."
        ),
        "league": league,
        "season": str(season),
        "nation": nation,
        "tier": tier,
        "folder": folder_name,
        "auto_resolved_folder": auto_resolved_folder,
        "paths": {
            "schedule_path": str(schedule_path),
            "events_root": str(out_root),
            "failed_csv": str(failed_csv),
        },
        "options": {
            "only_finished": bool(only_finished),
            "overwrite": bool(overwrite),
            "retry_failed": bool(retry_failed),
        },
        "counts": counts,
        "columns": [
            "match_id",
            "kickoff",
            "home_team",
            "away_team",
            "finished",
            "home_saved",
            "away_saved",
            "home_event_rows",
            "away_event_rows",
            "data_status",
            "failed_logged",
            "scrape_status",
            "failure_reason",
        ],
        "rows": rows,
        "to_fetch_preview": to_fetch_preview,
        "missing_preview": missing_preview,
        "failed_rows": failed_rows,
        "failed_preview": failed_preview,
    }


def _default_current_season_for_meta(meta: dict[str, Any]) -> str:
    mode = str(meta.get("season_mode", "split") or "split").strip().lower()
    now = datetime.now()
    if mode == "calendar":
        return str(now.year)
    return str(now.year if now.month >= 7 else now.year - 1)


def _coverage_overview_status(
    counts: dict[str, int],
    *,
    retry_failed: bool,
) -> tuple[str, int, str]:
    matches_in_schedule = int(counts.get("matches_in_schedule", 0) or 0)
    finished_matches = int(counts.get("finished_matches", 0) or 0)
    not_completed_matches = int(counts.get("not_finished_matches", 0) or 0)
    with_both_team_events = int(counts.get("with_both_team_events", 0) or 0)
    with_one_team_events = int(counts.get("with_one_team_events", 0) or 0)
    with_no_saved_events = int(counts.get("with_no_saved_events", 0) or 0)
    failed_matches = int(counts.get("failed_logged", 0) or 0)
    to_fetch_now = int(counts.get("to_fetch_now", 0) or 0)

    missing_or_partial = with_one_team_events + with_no_saved_events
    has_completed_and_missing = with_both_team_events > 0 and missing_or_partial > 0

    if to_fetch_now > 0:
        if has_completed_and_missing:
            return (
                "partial",
                1,
                f"{to_fetch_now} finished match(es) need scraping, with {with_both_team_events} already complete.",
            )
        return (
            "needs_scrape",
            1,
            f"{to_fetch_now} finished match(es) need event data before this league is covered.",
        )

    if failed_matches > 0 and missing_or_partial == 0 and not retry_failed:
        return (
            "failed_only",
            3,
            f"{failed_matches} failed match(es) are logged. Turn on retry failed to queue them again.",
        )

    if finished_matches == 0 and not_completed_matches > 0:
        return (
            "pending_fixtures",
            4,
            f"No finished matches are ready yet. {not_completed_matches} fixture(s) are still not completed.",
        )

    if missing_or_partial > 0 and with_both_team_events > 0:
        return (
            "partial",
            2,
            f"{with_both_team_events} finished match(es) are complete, but {missing_or_partial} still need attention.",
        )

    if missing_or_partial > 0:
        return (
            "needs_scrape",
            1,
            f"{missing_or_partial} finished match(es) do not have complete event data.",
        )

    if not_completed_matches > 0 and finished_matches < matches_in_schedule:
        return (
            "pending_fixtures",
            4,
            f"Finished matches are covered. {not_completed_matches} fixture(s) are still not completed.",
        )

    return (
        "complete",
        5,
        "All eligible finished matches have both teams saved.",
    )


def _coverage_pct_from_counts(counts: dict[str, int]) -> float:
    finished_matches = int(counts.get("finished_matches", 0) or 0)
    with_both_team_events = int(counts.get("with_both_team_events", 0) or 0)
    if finished_matches <= 0:
        return 0.0
    return round((with_both_team_events / finished_matches) * 100.0, 2)


def _no_schedule_overview_row(
    *,
    league: str,
    group: str,
    nation: str,
    tier: str,
    folder: str,
    season: str,
    schedule_path: Path,
) -> dict[str, Any]:
    return {
        "league": league,
        "group": group,
        "nation": nation,
        "tier": tier,
        "folder": folder,
        "season": str(season),
        "has_schedule": False,
        "schedule_path": str(schedule_path),
        "matches_in_schedule": 0,
        "finished_matches": 0,
        "not_completed_matches": 0,
        "with_both_team_events": 0,
        "with_one_team_events": 0,
        "with_no_saved_events": 0,
        "failed_matches": 0,
        "to_fetch_now": 0,
        "coverage_pct": 0.0,
        "status": "no_schedule",
        "priority": 6,
        "message": "No saved schedule CSV was found for this league and season.",
    }


def _overview_row_from_audit(
    *,
    audit: dict[str, Any],
    league: str,
    group: str,
    nation: str,
    tier: str,
    folder: str,
    season: str,
    schedule_path: Path,
    retry_failed: bool,
) -> dict[str, Any]:
    raw_counts = audit.get("counts") if isinstance(audit.get("counts"), dict) else {}
    counts = {str(key): int(value or 0) for key, value in raw_counts.items() if isinstance(value, (int, float))}
    status, priority, message = _coverage_overview_status(counts, retry_failed=retry_failed)

    return {
        "league": league,
        "group": group,
        "nation": nation,
        "tier": tier,
        "folder": folder,
        "season": str(season),
        "has_schedule": True,
        "schedule_path": str(schedule_path),
        "matches_in_schedule": int(counts.get("matches_in_schedule", 0) or 0),
        "finished_matches": int(counts.get("finished_matches", 0) or 0),
        "not_completed_matches": int(counts.get("not_finished_matches", 0) or 0),
        "with_both_team_events": int(counts.get("with_both_team_events", 0) or 0),
        "with_one_team_events": int(counts.get("with_one_team_events", 0) or 0),
        "with_no_saved_events": int(counts.get("with_no_saved_events", 0) or 0),
        "failed_matches": int(counts.get("failed_logged", 0) or 0),
        "to_fetch_now": int(counts.get("to_fetch_now", 0) or 0),
        "coverage_pct": _coverage_pct_from_counts(counts),
        "status": status,
        "priority": priority,
        "message": message,
    }


def _audit_failed_overview_row(
    *,
    league: str,
    group: str,
    nation: str,
    tier: str,
    folder: str,
    season: str,
    schedule_path: Path,
    error: Exception,
) -> dict[str, Any]:
    return {
        "league": league,
        "group": group,
        "nation": nation,
        "tier": tier,
        "folder": folder,
        "season": str(season),
        "has_schedule": schedule_path.exists(),
        "schedule_path": str(schedule_path),
        "matches_in_schedule": 0,
        "finished_matches": 0,
        "not_completed_matches": 0,
        "with_both_team_events": 0,
        "with_one_team_events": 0,
        "with_no_saved_events": 0,
        "failed_matches": 0,
        "to_fetch_now": 0,
        "coverage_pct": 0.0,
        "status": "audit_failed",
        "priority": 7,
        "message": f"Audit failed: {type(error).__name__}: {error}",
    }


def _coverage_overview_targets(basedir: Path, season: str | None = None) -> list[dict[str, str]]:
    requested_season = str(season or "").strip()
    schedule_root = _schedule_root(basedir)
    saved_by_folder: dict[str, list[str]] = {}

    if schedule_root.exists():
        for folder_path in sorted([path for path in schedule_root.iterdir() if path.is_dir()], key=lambda path: path.name.lower()):
            saved_by_folder[folder_path.name] = _list_seasons_for_folder(folder_path)

    targets: dict[tuple[str, str, str], dict[str, str]] = {}
    known_folders: set[str] = set()

    for league, meta in LEAGUE_PRESETS.items():
        nation = str(meta.get("nation", "") or "").strip()
        tier = str(meta.get("tier", "") or "").strip()
        folder = f"{nation} {tier}".strip()
        group = str(meta.get("group", "Other") or "Other")
        known_folders.add(folder)

        if requested_season:
            seasons = [requested_season]
        else:
            seasons = saved_by_folder.get(folder, [])
            if not seasons:
                seasons = [_default_current_season_for_meta(meta)]

        for item_season in seasons:
            clean_season = str(item_season or "").strip()
            if not clean_season:
                continue
            targets[(league, folder, clean_season)] = {
                "league": league,
                "group": group,
                "nation": nation,
                "tier": tier,
                "folder": folder,
                "season": clean_season,
            }

    for folder, saved_seasons in saved_by_folder.items():
        if requested_season:
            seasons = [requested_season] if requested_season in saved_seasons else []
        else:
            seasons = saved_seasons
        if not seasons:
            continue

        matching_presets = [
            (league, meta)
            for league, meta in LEAGUE_PRESETS.items()
            if f"{meta.get('nation', '')} {meta.get('tier', '')}".strip() == folder
        ]

        if matching_presets:
            for league, meta in matching_presets:
                for item_season in seasons:
                    clean_season = str(item_season or "").strip()
                    if not clean_season:
                        continue
                    targets.setdefault(
                        (league, folder, clean_season),
                        {
                            "league": league,
                            "group": str(meta.get("group", "Other") or "Other"),
                            "nation": str(meta.get("nation", "") or "").strip(),
                            "tier": str(meta.get("tier", "") or "").strip(),
                            "folder": folder,
                            "season": clean_season,
                        },
                    )
            continue

        folder_nation, folder_tier = _split_nation_tier(folder)
        for item_season in seasons:
            clean_season = str(item_season or "").strip()
            if not clean_season:
                continue
            targets.setdefault(
                (folder, folder, clean_season),
                {
                    "league": folder,
                    "group": "Saved schedules",
                    "nation": folder_nation,
                    "tier": folder_tier,
                    "folder": folder,
                    "season": clean_season,
                },
            )

    return sorted(
        targets.values(),
        key=lambda item: (
            item.get("group", ""),
            item.get("league", ""),
            item.get("season", ""),
        ),
    )


def get_event_coverage_overview(
    basedir: Path,
    season: str | None = None,
    only_finished: bool = True,
    overwrite: bool = False,
    retry_failed: bool = False,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    targets = _coverage_overview_targets(basedir, season=season)
    schedule_root = _schedule_root(basedir)

    for target in targets:
        league = target["league"]
        group = target["group"]
        nation = target["nation"]
        tier = target["tier"]
        folder = target["folder"]
        item_season = target["season"]
        schedule_path = schedule_root / folder / f"{item_season}.csv"

        if not schedule_path.exists():
            rows.append(
                _no_schedule_overview_row(
                    league=league,
                    group=group,
                    nation=nation,
                    tier=tier,
                    folder=folder,
                    season=item_season,
                    schedule_path=schedule_path,
                )
            )
            continue

        try:
            audit = get_event_coverage_audit(
                basedir=basedir,
                league=league,
                season=item_season,
                nation=nation,
                tier=tier,
                only_finished=only_finished,
                overwrite=overwrite,
                retry_failed=retry_failed,
            )
            rows.append(
                _overview_row_from_audit(
                    audit=audit,
                    league=league,
                    group=group,
                    nation=nation,
                    tier=tier,
                    folder=folder,
                    season=item_season,
                    schedule_path=schedule_path,
                    retry_failed=retry_failed,
                )
            )
        except Exception as exc:
            rows.append(
                _audit_failed_overview_row(
                    league=league,
                    group=group,
                    nation=nation,
                    tier=tier,
                    folder=folder,
                    season=item_season,
                    schedule_path=schedule_path,
                    error=exc,
                )
            )

    rows.sort(
        key=lambda row: (
            int(row.get("priority", 99) or 99),
            -int(row.get("to_fetch_now", 0) or 0),
            float(row.get("coverage_pct", 0.0) or 0.0),
            str(row.get("league", "")),
            str(row.get("season", "")),
        )
    )

    summary = {
        "total_leagues": int(len(rows)),
        "saved_schedules": int(sum(1 for row in rows if bool(row.get("has_schedule")))),
        "complete": int(sum(1 for row in rows if row.get("status") == "complete")),
        "needs_scrape": int(sum(1 for row in rows if row.get("status") == "needs_scrape")),
        "partial": int(sum(1 for row in rows if row.get("status") == "partial")),
        "failed_only": int(sum(1 for row in rows if row.get("status") == "failed_only")),
        "failed_match_leagues": int(sum(1 for row in rows if int(row.get("failed_matches", 0) or 0) > 0)),
        "pending_fixtures": int(sum(1 for row in rows if row.get("status") == "pending_fixtures")),
        "no_schedule": int(sum(1 for row in rows if row.get("status") == "no_schedule")),
        "audit_failed": int(sum(1 for row in rows if row.get("status") == "audit_failed")),
        "to_fetch_now": int(sum(int(row.get("to_fetch_now", 0) or 0) for row in rows)),
    }

    return {
        "kind": "coverage_overview",
        "message": (
            f"{summary['to_fetch_now']} match(es) are ready to fetch across "
            f"{summary['needs_scrape'] + summary['partial']} league season(s)."
        ),
        "options": {
            "season": str(season or "").strip(),
            "only_finished": bool(only_finished),
            "overwrite": bool(overwrite),
            "retry_failed": bool(retry_failed),
        },
        "summary": summary,
        "columns": [
            "league",
            "group",
            "nation",
            "tier",
            "folder",
            "season",
            "has_schedule",
            "schedule_path",
            "matches_in_schedule",
            "finished_matches",
            "not_completed_matches",
            "with_both_team_events",
            "with_one_team_events",
            "with_no_saved_events",
            "failed_matches",
            "to_fetch_now",
            "coverage_pct",
            "status",
            "priority",
            "message",
        ],
        "rows": rows,
    }


def _load_failed_records(failed_csv: Path) -> dict[int, dict[str, Any]]:
    if not failed_csv.exists():
        return {}

    try:
        df = _read_csv_resilient(failed_csv)
    except Exception:
        return {}

    if df.empty:
        return {}

    col = "match_id" if "match_id" in df.columns else ("matchid" if "matchid" in df.columns else (df.columns[0] if len(df.columns) else None))
    if not col:
        return {}

    ids = pd.to_numeric(df[col], errors="coerce")
    records: dict[int, dict[str, Any]] = {}

    for index, row in df.loc[ids.notna()].iterrows():
        match_id = int(ids.loc[index])
        record: dict[str, Any] = {"match_id": match_id}
        for key, value in row.to_dict().items():
            if key == col:
                continue
            if value is None:
                continue
            if isinstance(value, float) and pd.isna(value):
                continue
            text_value = str(value).strip()
            if text_value and text_value.lower() not in {"nan", "none", "<na>", "nat", "null"}:
                record[str(key)] = text_value
        records[match_id] = record

    return records


def _load_failed_ids(failed_csv: Path) -> list[int]:
    return sorted(_load_failed_records(failed_csv).keys())


def _save_failed_ids(
    failed_csv: Path,
    match_ids: set[int],
    failure_details: dict[int, dict[str, Any]] | None = None,
) -> None:
    failed_csv.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    details = failure_details or {}
    for match_id in sorted(match_ids):
        row: dict[str, Any] = {"match_id": int(match_id)}
        extra = details.get(int(match_id), {})
        for key, value in extra.items():
            if key == "match_id":
                continue
            row[str(key)] = value
        rows.append(row)

    pd.DataFrame(rows).to_csv(failed_csv, index=False)



def get_schedule_folders(basedir: Path) -> dict[str, list[str]]:
    return _nation_to_folders(_schedule_root(basedir))


def get_schedule_seasons(basedir: Path, nation: str, tier: str) -> list[str]:
    folder_name = f"{nation} {tier}".strip()
    return _list_seasons_for_folder(_schedule_root(basedir) / folder_name)


def get_league_presets() -> list[dict[str, Any]]:
    return get_league_presets_payload()


def save_schedule_csv(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    rows: list[dict[str, Any]],
    league: str | None = None,
) -> dict[str, Any]:
    resolved_nation, resolved_tier, auto_resolved = resolve_league_folder(league or "", nation, tier)
    if not resolved_nation or not resolved_tier:
        raise ValueError("Could not resolve a schedule folder. Select a known league or provide nation and tier.")

    folder_name = f"{resolved_nation} {resolved_tier}".strip()
    out_dir = _schedule_root(basedir) / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_season = _safe_slug(season)
    out_path = out_dir / f"{safe_season}.csv"
    df_new = pd.DataFrame(rows)

    if df_new.empty:
        return {
            "path": out_path,
            "mode": "skipped",
            "folder": folder_name,
            "nation": resolved_nation,
            "tier": resolved_tier,
            "auto_resolved_folder": auto_resolved,
            "message": "New schedule payload is empty. Existing file was left untouched.",
        }

    if out_path.exists():
        try:
            df_old = _read_csv_resilient(out_path)
        except Exception:
            df_old = pd.DataFrame()

        # keep a backup before overwrite
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = out_dir / f"{safe_season}__backup_{ts}.csv"
        try:
            shutil.copy2(out_path, backup_path)
        except Exception:
            backup_path = None

        # union columns so new richer payloads do not lose older columns
        all_cols = list(dict.fromkeys(list(df_old.columns) + list(df_new.columns)))
        if all_cols:
            df_old = df_old.reindex(columns=all_cols)
            df_new = df_new.reindex(columns=all_cols)

        # choose best dedupe key available
        key_cols = [c for c in ["game_id", "match_id", "matchid", "id"] if c in df_new.columns or c in df_old.columns]
        key_col = key_cols[0] if key_cols else None

        if key_col:
            if key_col not in df_old.columns:
                df_old[key_col] = pd.NA
            if key_col not in df_new.columns:
                df_new[key_col] = pd.NA

            combined = pd.concat([df_old, df_new], ignore_index=True)

            # prefer the new scrape row for duplicate fixtures
            combined["_is_new"] = 0
            combined.loc[combined.index >= len(df_old), "_is_new"] = 1

            combined[key_col] = pd.to_numeric(combined[key_col], errors="coerce")
            combined = (
                combined.sort_values(["_is_new"], ascending=False)
                .drop_duplicates(subset=[key_col], keep="first")
                .drop(columns=["_is_new"])
                .reset_index(drop=True)
            )
        else:
            # fallback if no stable id exists
            combined = df_new.copy()

        tmp_path = out_path.with_suffix(".csv.tmp")
        combined.to_csv(tmp_path, index=False)
        tmp_path.replace(out_path)

        backup_msg = f" Backup saved at {backup_path}." if backup_path else ""
        return {
            "path": out_path,
            "mode": "updated",
            "folder": folder_name,
            "nation": resolved_nation,
            "tier": resolved_tier,
            "auto_resolved_folder": auto_resolved,
            "message": f"Updated main schedule file: {out_path}.{backup_msg}",
        }

    df_new.to_csv(out_path, index=False)
    return {
        "path": out_path,
        "mode": "main",
        "folder": folder_name,
        "nation": resolved_nation,
        "tier": resolved_tier,
        "auto_resolved_folder": auto_resolved,
        "message": f"Saved: {out_path}",
    }


SOCCERDATA_WS_LEAGUES = frozenset({
    "ENG-Premier League", "ESP-La Liga", "FRA-Ligue 1",
    "GER-Bundesliga", "ITA-Serie A", "INT-World Cup",
    "INT-European Championship", "INT-Women's World Cup",
})


def _soccerdata_season_value(league: str, season: str | int) -> str | int:
    """
    Keep the UI season format simple while sending soccerdata a format that
    matches its documented API.

    Split seasons:
        2025      -> 2025
        2025/2026 -> 2025-26
        2025-2026 -> 2025-26
        2526      -> 25-26

    Calendar seasons:
        2026 -> 2026
    """
    text = str(season or "").strip()
    mode = str(LEAGUE_PRESETS.get(str(league or ""), {}).get("season_mode", "split")).strip().lower()

    if not text:
        return season

    if mode == "calendar":
        if re.fullmatch(r"\d{4}", text):
            return int(text)
        if re.fullmatch(r"\d{2}", text):
            return int(f"20{text}")
        return text

    if re.fullmatch(r"\d{4}", text) and not text.startswith("20"):
        return f"{text[:2]}-{text[2:]}"

    if re.fullmatch(r"\d{4}", text):
        return int(text)

    if re.fullmatch(r"\d{4}/\d{4}", text) or re.fullmatch(r"\d{4}-\d{4}", text):
        start, end = re.split(r"[/-]", text)
        return f"{start}-{end[-2:]}"

    return text


def _league_key(value: str) -> str:
    alias_map = {
        "TUR-Super Lig": "TUR-Süper Lig",
        "BRA-Serie A": "BRA-Série A",
        "ARG-Primera División": "ARG-Liga Profesional",
        "POR-Liga NOS": "POR-Liga Portugal",
        "ESP-Segunda División": "ESP-Segunda Division",
    }
    return alias_map.get(str(value or "").strip(), str(value or "").strip())


def _is_soccerdata_backed_league(league: str) -> bool:
    key = _league_key(league)
    return key in SOCCERDATA_WS_LEAGUES or key in SOCCERDATA_BACKED_LEAGUES or str(league or "").strip() in SOCCERDATA_BACKED_LEAGUES


def _soccerdata_available_leagues_safe() -> set[str]:
    try:
        return set(sd.WhoScored.available_leagues())
    except Exception:
        return set()


ScheduleStatusCallback = Callable[[dict[str, Any]], None]


def _emit_schedule_status(
    status_callback: ScheduleStatusCallback | None,
    kind: str,
    message: str,
    **extra: Any,
) -> None:
    payload: dict[str, Any] = {"kind": kind, "message": message}
    payload.update(extra)
    if status_callback is not None:
        try:
            status_callback(payload)
        except Exception:
            pass


def _schedule_value_is_empty(value: Any) -> bool:
    if value is None:
        return True

    if isinstance(value, str):
        return value.strip().lower() in {"", "nan", "none", "<na>", "nat", "null"}

    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0

    if isinstance(value, np.ndarray):
        return value.size == 0

    try:
        missing = pd.isna(value)
    except Exception:
        return False

    if isinstance(missing, (bool, np.bool_)):
        return bool(missing)

    return False


def _json_safe_schedule_value(value: Any) -> Any:
    if _schedule_value_is_empty(value):
        return ""

    if isinstance(value, np.generic):
        return value.item()

    if isinstance(value, np.ndarray):
        return [_json_safe_schedule_value(item) for item in value.tolist()]

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if isinstance(value, dict):
        return {str(key): _json_safe_schedule_value(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_json_safe_schedule_value(item) for item in value]

    return value


def _schedule_table_payload(df: pd.DataFrame, kind: str = "complete", message: str = "Schedule scrape complete.") -> dict[str, Any]:
    records = df.to_dict(orient="records")
    safe_rows = [
        {str(key): _json_safe_schedule_value(value) for key, value in row.items()}
        for row in records
    ]

    return {
        "kind": kind,
        "stage": "complete",
        "message": message,
        "count": int(len(df)),
        "columns": [str(col) for col in df.columns.tolist()],
        "rows": safe_rows,
    }


def _schedule_match_id_col(df: pd.DataFrame) -> str | None:
    for col in ["game_id", "match_id", "matchid", "id"]:
        if col in df.columns:
            return col
    return None


def _merge_schedule_sources(primary: pd.DataFrame, enrichment: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if enrichment is None or not isinstance(enrichment, pd.DataFrame) or enrichment.empty:
        return primary, 0
    if primary is None or not isinstance(primary, pd.DataFrame) or primary.empty:
        return enrichment.reset_index(drop=True), len(enrichment)

    primary_key = _schedule_match_id_col(primary)
    enrichment_key = _schedule_match_id_col(enrichment)
    if not primary_key or not enrichment_key:
        return primary, 0

    primary_ids = primary[primary_key].astype(str).str.strip()
    enrichment_ids = enrichment[enrichment_key].astype(str).str.strip()
    primary_id_set = {value for value in primary_ids if value and value.lower() not in {"nan", "none"}}
    enrichment_id_set = {value for value in enrichment_ids if value and value.lower() not in {"nan", "none"}}
    added_count = len(enrichment_id_set - primary_id_set)

    all_columns = list(dict.fromkeys(list(enrichment.columns) + list(primary.columns)))
    enrichment_work = enrichment.reindex(columns=all_columns).copy()
    primary_work = primary.reindex(columns=all_columns).copy()

    if primary_key != enrichment_key and enrichment_key in enrichment_work.columns:
        enrichment_work[primary_key] = enrichment_work[enrichment_key]

    enrichment_work["_schedule_source_rank"] = 0
    primary_work["_schedule_source_rank"] = 1

    combined = pd.concat([enrichment_work, primary_work], ignore_index=True, sort=False)
    combined[primary_key] = combined[primary_key].astype(str).str.strip()
    combined = combined.loc[combined[primary_key].str.lower().ne("nan") & combined[primary_key].ne("")].copy()
    combined = combined.sort_values("_schedule_source_rank")

    def first_non_empty(series: pd.Series) -> Any:
        for value in series:
            if _schedule_value_is_empty(value):
                continue
            text = str(value).strip()
            if text and text.lower() not in {"nan", "none", "<na>", "nat", "null"}:
                return value
        return series.iloc[0] if len(series) else pd.NA

    grouped = (
        combined.groupby(primary_key, sort=False, dropna=False)
        .agg(first_non_empty)
        .reset_index()
    )

    if "_schedule_source_rank" in grouped.columns:
        grouped = grouped.drop(columns=["_schedule_source_rank"], errors="ignore")

    if "date" in grouped.columns:
        sort_dt = pd.to_datetime(grouped["date"], errors="coerce", utc=True)
        if sort_dt.notna().any():
            grouped["_sort_dt"] = sort_dt
            grouped = grouped.sort_values("_sort_dt", na_position="last").drop(columns=["_sort_dt"]).reset_index(drop=True)

    return grouped.reset_index(drop=True), added_count


def _enrich_schedule_with_custom_stages(
    df: pd.DataFrame,
    league_key: str,
    season: str | int,
    headless: bool,
    resolved_browser: str,
    status_callback: ScheduleStatusCallback | None,
) -> pd.DataFrame:
    if league_key not in CUSTOM_WS_LEAGUES or league_key in BUILTIN_SOCCERDATA_WS_LEAGUES:
        return df

    _emit_schedule_status(
        status_callback,
        "status",
        "Checking the custom WhoScored scraper for extra stages such as playoffs before returning the schedule.",
        stage="custom_stage_enrichment_start",
        league=league_key,
    )

    try:
        custom_df = load_schedule_custom(
            league_key,
            str(season),
            headless,
            resolved_browser or None,
            status_callback=status_callback,
        )
    except Exception as exc:
        _emit_schedule_status(
            status_callback,
            "warning",
            f"Custom stage enrichment failed, so the soccerdata schedule will be used as-is: {type(exc).__name__}: {exc}",
            stage="custom_stage_enrichment_failed",
            league=league_key,
        )
        return df

    merged, added_count = _merge_schedule_sources(df, custom_df)
    playoff_count = int(merged["is_playoff"].fillna(False).astype(bool).sum()) if "is_playoff" in merged.columns else 0

    _emit_schedule_status(
        status_callback,
        "status",
        f"Custom stage enrichment complete. Added {added_count} extra match row(s), with {playoff_count} playoff or postseason row(s) in the merged schedule.",
        stage="custom_stage_enrichment_complete",
        league=league_key,
        added_count=int(added_count),
        total_count=int(len(merged)),
        playoff_count=int(playoff_count),
    )
    return merged


def load_schedule(
    league: str,
    season: str | int,
    headless: bool,
    browserpath: str | None = None,
    status_callback: ScheduleStatusCallback | None = None,
) -> pd.DataFrame:
    resolved_browser = str(browserpath).strip() if browserpath else guess_browser_path()
    league_key = _league_key(league)
    soccerdata_season = _soccerdata_season_value(league_key, season)

    def make_kwargs(use_headless: bool) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "leagues": league_key,
            "seasons": soccerdata_season,
            "headless": use_headless,
        }
        if resolved_browser:
            kwargs["path_to_browser"] = str(Path(resolved_browser))
        return kwargs

    _emit_schedule_status(
        status_callback,
        "status",
        f"Starting schedule scrape for {league_key} {season}.",
        stage="schedule_start",
        league=league_key,
        season=str(season),
        headless=bool(headless),
        browser_path=resolved_browser or "auto detect",
        soccerdata_season=str(soccerdata_season),
        soccerdata_config_path=str(SOCCERDATA_CONFIG_STATUS.get("path", "")),
    )

    if SOCCERDATA_CONFIG_STATUS.get("error"):
        _emit_schedule_status(
            status_callback,
            "warning",
            f"Could not update soccerdata league_dict.json: {SOCCERDATA_CONFIG_STATUS['error']}",
            stage="soccerdata_config_warning",
            path=str(SOCCERDATA_CONFIG_STATUS.get("path", "")),
        )
    elif SOCCERDATA_CONFIG_STATUS.get("updated"):
        _emit_schedule_status(
            status_callback,
            "status",
            "soccerdata custom league dictionary is ready. Restart the backend if this is the first time it was created.",
            stage="soccerdata_config_ready",
            path=str(SOCCERDATA_CONFIG_STATUS.get("path", "")),
            leagues_written=int(SOCCERDATA_CONFIG_STATUS.get("leagues_written", 0) or 0),
        )

    available_leagues = _soccerdata_available_leagues_safe()
    soccerdata_backed = _is_soccerdata_backed_league(league_key)

    if soccerdata_backed and available_leagues and league_key not in available_leagues:
        _emit_schedule_status(
            status_callback,
            "warning",
            (
                f"{league_key} is configured for soccerdata, but it is not visible in "
                "sd.WhoScored.available_leagues() in this running Python process. "
                "Restart the backend so soccerdata reloads league_dict.json."
            ),
            stage="soccerdata_league_not_loaded",
            league=league_key,
            config_path=str(SOCCERDATA_CONFIG_STATUS.get("path", "")),
        )

    def soccerdata_attempt(use_headless: bool, attempt_label: str) -> tuple[pd.DataFrame | None, Exception | None]:
        kwargs = make_kwargs(use_headless)
        try:
            _emit_schedule_status(
                status_callback,
                "status",
                f"Trying soccerdata WhoScored schedule reader ({attempt_label}).",
                stage="soccerdata_start",
                attempt=attempt_label,
                headless=bool(use_headless),
            )
            with seleniumbase_local_cwd():
                ws = sd.WhoScored(**kwargs)
                _emit_schedule_status(
                    status_callback,
                    "status",
                    "WhoScored calendar opened through soccerdata. Reading fixture table now.",
                    stage="soccerdata_read_schedule",
                    attempt=attempt_label,
                )
                df = ws.read_schedule()

            if isinstance(df, pd.DataFrame) and not df.empty:
                _emit_schedule_status(
                    status_callback,
                    "status",
                    f"soccerdata returned {len(df)} schedule rows.",
                    stage="soccerdata_complete",
                    count=int(len(df)),
                    attempt=attempt_label,
                )
                return df, None

            return None, RuntimeError(f"soccerdata returned an empty schedule for '{league_key}'")
        except Exception as exc:
            _emit_schedule_status(
                status_callback,
                "warning",
                f"soccerdata failed for {league_key} on {attempt_label}: {type(exc).__name__}: {exc}",
                stage="soccerdata_failed",
                attempt=attempt_label,
            )
            return None, exc

    df, soccerdata_error = soccerdata_attempt(bool(headless), "selected browser mode")
    if df is not None:
        return _enrich_schedule_with_custom_stages(
            df=df,
            league_key=league_key,
            season=season,
            headless=headless,
            resolved_browser=resolved_browser,
            status_callback=status_callback,
        )

    if bool(headless):
        _emit_schedule_status(
            status_callback,
            "status",
            "Headless soccerdata failed. Retrying once with a visible browser before stopping or using fallback.",
            stage="soccerdata_visible_retry",
        )
        df, visible_error = soccerdata_attempt(False, "visible browser retry")
        if df is not None:
            return _enrich_schedule_with_custom_stages(
                df=df,
                league_key=league_key,
                season=season,
                headless=False,
                resolved_browser=resolved_browser,
                status_callback=status_callback,
            )
        soccerdata_error = visible_error or soccerdata_error

    if league_key in BUILTIN_SOCCERDATA_WS_LEAGUES:
        _emit_schedule_status(
            status_callback,
            "error",
            (
                f"{league_key} is a built in soccerdata WhoScored league, so the custom fallback was skipped. "
                "For top five leagues the failure is normally a WhoScored block, bad cached response, or browser session issue."
            ),
            stage="soccerdata_only_failed",
            league=league_key,
            config_path=str(SOCCERDATA_CONFIG_STATUS.get("path", "")),
        )
        raise RuntimeError(f"soccerdata failed for '{league_key}': {soccerdata_error}")

    if league_key in CUSTOM_WS_LEAGUES:
        try:
            _emit_schedule_status(
                status_callback,
                "status",
                (
                    "Trying custom WhoScored fallback scraper. "
                    "This fallback is allowed for non top five leagues when soccerdata fails."
                ),
                stage="custom_fallback_start",
                soccerdata_backed=bool(soccerdata_backed),
            )
            return load_schedule_custom(
                league_key,
                str(season),
                headless,
                resolved_browser or None,
                status_callback=status_callback,
            )
        except Exception as custom_exc:
            _emit_schedule_status(
                status_callback,
                "error",
                f"Custom schedule fallback failed: {type(custom_exc).__name__}: {custom_exc}",
                stage="custom_fallback_failed",
            )
            raise RuntimeError(
                f"soccerdata failed for '{league_key}': {soccerdata_error} | "
                f"custom scraper failed: {custom_exc}"
            ) from custom_exc

    _emit_schedule_status(
        status_callback,
        "error",
        f"{league_key} could not be loaded by soccerdata and no custom fallback exists.",
        stage="no_custom_fallback",
    )
    raise ValueError(
        f"'{league_key}' could not be loaded by soccerdata and no custom fallback exists. "
        f"soccerdata error: {soccerdata_error}"
    )


def stream_load_schedule(
    league: str,
    season: str | int,
    headless: bool,
    browserpath: str | None = None,
) -> Iterator[dict[str, Any]]:
    queue: Queue[dict[str, Any] | None] = Queue()

    def emit(payload: dict[str, Any]) -> None:
        queue.put(payload)

    class ScheduleLogHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                emit({
                    "kind": "log",
                    "stage": "backend_log",
                    "message": self.format(record),
                })
            except Exception:
                pass

    def worker() -> None:
        log_handler = ScheduleLogHandler()
        log_handler.setLevel(logging.INFO)
        log_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)
        try:
            df = load_schedule(
                league=league,
                season=season,
                headless=headless,
                browserpath=browserpath,
                status_callback=emit,
            )
            emit(_schedule_table_payload(df, message=f"Loaded {len(df)} schedule rows. Review them, then save the schedule."))
        except ValueError as exc:
            emit({
                "kind": "error",
                "stage": "failed",
                "message": str(exc),
            })
        except Exception as exc:
            emit({
                "kind": "error",
                "stage": "failed",
                "message": f"Schedule scrape failed: {type(exc).__name__}: {exc}",
            })
        finally:
            root_logger.removeHandler(log_handler)
            queue.put(None)

    thread = Thread(target=worker, daemon=True)
    thread.start()

    heartbeat_count = 0
    while True:
        try:
            item = queue.get(timeout=1.0)
        except Empty:
            heartbeat_count += 1
            if heartbeat_count % 12 == 0:
                yield {
                    "kind": "heartbeat",
                    "stage": "running",
                    "message": "Schedule scrape is still running. Keep this page open.",
                }
            continue

        if item is None:
            break

        yield item

def _append_event_rows_csv(out_path: Path, rows: pd.DataFrame, match_id: int) -> int:
    if rows is None or rows.empty:
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    incoming = rows.copy()

    if out_path.exists() and out_path.stat().st_size > 0:
        try:
            existing = _read_csv_resilient(out_path)
        except Exception:
            existing = pd.DataFrame()

        if not existing.empty:
            existing_match_col = _get_match_id_col(existing)
            if existing_match_col:
                existing_ids = pd.to_numeric(existing[existing_match_col], errors="coerce")
                existing = existing.loc[~existing_ids.eq(int(match_id))].copy()

        all_cols = list(dict.fromkeys(list(existing.columns) + list(incoming.columns)))
        existing = existing.reindex(columns=all_cols) if not existing.empty else pd.DataFrame(columns=all_cols)
        incoming = incoming.reindex(columns=all_cols)
        combined = pd.concat([existing, incoming], ignore_index=True, sort=False)
    else:
        combined = incoming

    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    combined.to_csv(tmp_path, index=False)
    tmp_path.replace(out_path)
    return int(len(incoming))


def stream_fetch_events(
    basedir: Path,
    league: str,
    season: str,
    headless: bool = True,
    browserpath: str | None = None,
    nation: str = "",
    tier: str = "",
    only_finished: bool = True,
    overwrite: bool = False,
    retry_failed: bool = False,
    fail_fast: bool = True,
    scrape_positions: bool = True,
) -> Iterator[dict[str, Any]]:
    league = _league_key(league)
    nation, tier, auto_resolved_folder = resolve_league_folder(league, nation, tier)
    if not nation or not tier:
        yield {
            "kind": "error",
            "message": "Could not resolve a schedule folder. Select a known league or provide nation and tier.",
            "league": league,
        }
        return

    folder_name = f"{nation} {tier}".strip()
    yield {
        "kind": "status",
        "stage": "folder_resolved",
        "league": league,
        "nation": nation,
        "tier": tier,
        "folder": folder_name,
        "auto_resolved_folder": auto_resolved_folder,
        "message": f"Using folder {folder_name} for {league}.",
    }

    schedule_path = _schedule_root(basedir) / folder_name / f"{season}.csv"
    if not schedule_path.exists():
        yield {"kind": "error", "message": f"Schedule CSV not found: {schedule_path}"}
        return

    try:
        sch_df = _read_schedule_csv(schedule_path)
    except Exception as exc:
        yield {"kind": "error", "message": f"Failed to read schedule CSV: {exc}"}
        return

    if sch_df.empty:
        yield {"kind": "error", "message": "Schedule CSV is empty."}
        return

    match_id_col = _get_match_id_col(sch_df)
    home_col, away_col = _get_home_away_cols(sch_df)
    home_id_col, away_id_col = _get_home_away_id_cols(sch_df)

    if not match_id_col or not home_col or not away_col:
        yield {
            "kind": "error",
            "message": "Schedule CSV must contain match id, home team and away team columns.",
        }
        return

    teams = sorted(
        set(
            sch_df[home_col].dropna().astype(str).tolist() +
            sch_df[away_col].dropna().astype(str).tolist()
        )
    )

    total_matches = len(sch_df)
    completed_matches = int(_schedule_finished_mask(sch_df).sum())
    all_teams = len(teams)

    out_root = _events_scope_root(basedir, nation, tier)
    out_root.mkdir(parents=True, exist_ok=True)

    failed_csv = _failed_csv_path(basedir, nation, tier, season)
    positions_csv = _positions_csv_path(basedir, nation, tier, season)

    previous_failure_details = _load_failed_records(failed_csv)
    prev_failed = set(previous_failure_details.keys())
    failure_details: dict[int, dict[str, Any]] = dict(previous_failure_details)

    allow_overwrite = overwrite and not retry_failed

    backup_root = (
        _events_root(basedir)
        / "_backups"
        / _safe_slug(nation)
        / _safe_slug(tier or 'T1')
        / _safe_slug(season)
        / datetime.now().strftime("%Y%m%d_%H%M%S")
    )

    if allow_overwrite:
        backup_root.mkdir(parents=True, exist_ok=True)

        for team in teams:
            team_dir = out_root / _safe_slug(team)
            season_file = team_dir / f"{_safe_slug(season)}.csv"
            if season_file.exists():
                backup_team_dir = backup_root / _safe_slug(team)
                backup_team_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(season_file, backup_team_dir / season_file.name)
                season_file.unlink()

        if failed_csv.exists():
            backup_failed_dir = backup_root / "_failed"
            backup_failed_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(failed_csv, backup_failed_dir / failed_csv.name)
            failed_csv.unlink()

        if positions_csv.exists():
            backup_pos_dir = backup_root / "_positions"
            backup_pos_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(positions_csv, backup_pos_dir / positions_csv.name)
            positions_csv.unlink()

        yield {"kind": "warning", "message": f"Backup created at: {backup_root}"}

    keep_cols = [c for c in [match_id_col, home_col, away_col, home_id_col, away_id_col] if c in sch_df.columns]
    meta = sch_df[keep_cols].dropna(subset=[match_id_col]).copy()
    meta[match_id_col] = pd.to_numeric(meta[match_id_col], errors="coerce")
    meta = meta.dropna(subset=[match_id_col])
    meta[match_id_col] = meta[match_id_col].astype(int)

    if home_id_col:
        meta[home_id_col] = pd.to_numeric(meta[home_id_col], errors="coerce")
    if away_id_col:
        meta[away_id_col] = pd.to_numeric(meta[away_id_col], errors="coerce")

    meta["_finished"] = _schedule_finished_mask(sch_df).values
    meta["_sort_dt"] = _schedule_sort_datetime(sch_df).values

    home_by_mid = dict(zip(meta[match_id_col].astype(int), meta[home_col].astype(str)))
    away_by_mid = dict(zip(meta[match_id_col].astype(int), meta[away_col].astype(str)))
    home_id_by_mid = dict(zip(meta[match_id_col].astype(int), meta[home_id_col])) if home_id_col else {}
    away_id_by_mid = dict(zip(meta[match_id_col].astype(int), meta[away_id_col])) if away_id_col else {}

    team_paths: dict[str, Path] = {}
    existing_by_team: dict[str, set[int]] = {}
    for t in teams:
        team_dir = out_root / _safe_slug(t)
        team_dir.mkdir(parents=True, exist_ok=True)
        p = team_dir / f"{_safe_slug(season)}.csv"
        team_paths[t] = p
        existing_by_team[t] = _read_existing_match_ids(p)

    _merge_existing_flat_season_ids(existing_by_team, out_root, season)

    finished_ids = set(meta.loc[meta["_finished"].fillna(False), match_id_col].astype(int).tolist())

    if retry_failed:
        candidate_ids = set(prev_failed)
        if only_finished:
            candidate_ids = candidate_ids & finished_ids
    else:
        if only_finished:
            candidate_ids = finished_ids
        else:
            candidate_ids = set(meta[match_id_col].astype(int).tolist())

    fetch_meta = meta[meta[match_id_col].isin(candidate_ids)].copy()
    fetch_meta = fetch_meta.sort_values(
        by=["_finished", "_sort_dt", match_id_col],
        ascending=[False, True, True],
        na_position="last",
    )

    to_fetch: list[int] = []
    seen_to_fetch: set[int] = set()
    already_complete = 0
    skipped_not_in_schedule = 0

    for row in fetch_meta.itertuples(index=False):
        mid = int(getattr(row, match_id_col))

        if mid in seen_to_fetch:
            continue

        h = home_by_mid.get(mid)
        a = away_by_mid.get(mid)

        if not h or not a:
            skipped_not_in_schedule += 1
            continue

        h_done = mid in existing_by_team.get(h, set())
        a_done = mid in existing_by_team.get(a, set())

        if h_done and a_done:
            already_complete += 1
            seen_to_fetch.add(mid)
            continue

        to_fetch.append(mid)
        seen_to_fetch.add(mid)

    preview_df = fetch_meta[fetch_meta[match_id_col].isin(to_fetch)].copy().head(5)
    preview_cols = [c for c in [match_id_col, home_col, away_col, "_finished", "_sort_dt"] if c in preview_df.columns]

    yield {
        "kind": "summary",
        "schedule_path": str(schedule_path),
        "events_root": str(out_root),
        "candidate_matches": len(candidate_ids),
        "already_complete": already_complete,
        "to_fetch_now": len(to_fetch),
        "matches_in_schedule": total_matches,
        "matches_completed": completed_matches,
        "not_completed": total_matches - completed_matches,
        "unique_teams": all_teams,
        "only_finished": only_finished,
        "retry_failed": retry_failed,
        "overwrite": overwrite,
        "fail_fast": fail_fast,
        "scrape_positions": scrape_positions,
        "preview_rows": preview_df[preview_cols].fillna("").to_dict(orient="records"),
        "preview_columns": preview_cols,
        "skipped_not_in_schedule": skipped_not_in_schedule,
    }

    if len(to_fetch) == 0:
        yield {"kind": "complete", "message": "Nothing to fetch. Your data looks complete for the selected mode.", "written_rows": 0, "failed_count": len(prev_failed), "failed_ids": sorted(prev_failed), "saved_under": str(out_root)}
        return

    first_mid = int(to_fetch[0])

    soccerdata_season = _soccerdata_season_value(league, season)
    resolved_browser = str(browserpath).strip() if browserpath else guess_browser_path()

    yield {
        "kind": "status",
        "stage": "browser_resolved",
        "message": (
            f"Using browser at {resolved_browser}"
            if resolved_browser
            else "No Chrome browser path was detected for event fetching."
        ),
        "browser_path": resolved_browser,
    }

    if not resolved_browser:
        yield {
            "kind": "error",
            "stage": "browser_missing",
            "message": (
                "Chrome was not found. Install Google Chrome, or paste the full chrome.exe path "
                "into the Browser path field before fetching events. Common Windows path: "
                r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
            ),
        }
        return

    kwargs: dict[str, Any] = {
        "leagues": league,
        "seasons": soccerdata_season,
        "headless": headless,
        "path_to_browser": str(resolved_browser),
    }

    new_failed: set[int] = set()
    successful_fetch: set[int] = set()
    written_rows = 0

    with seleniumbase_local_cwd():
        try:
            ws = _make_cached_ws(**kwargs)
        except Exception as exc:
            yield {
                "kind": "error",
                "stage": "soccerdata_event_browser_failed",
                "message": (
                    f"Could not start soccerdata WhoScored event browser: {type(exc).__name__}: {exc}. "
                    "Check that Google Chrome is installed and that the Browser path points to chrome.exe."
                ),
                "browser_path": resolved_browser,
            }
            return

        for i, mid in enumerate(to_fetch, start=1):
            mid = int(mid)
            rows_written_this_match = 0
            fail_reason = None
            df_ev = pd.DataFrame()
            pos_df_file = pd.DataFrame()
            scraped_teams: list[str] = []
            group_key = ""
            team_col = None
            event_team_id_col = None
            h = home_by_mid.get(mid)
            a = away_by_mid.get(mid)
            match_url_slug = _build_match_url_slug(league, season, h or "", a or "")
            hid = home_id_by_mid.get(mid)
            aid = away_id_by_mid.get(mid)
            already_present_home = bool(h) and mid in existing_by_team.get(h, set())
            already_present_away = bool(a) and mid in existing_by_team.get(a, set())
            already_present_both = already_present_home and already_present_away

            yield {"kind": "progress", "completed": i, "total": len(to_fetch), "match_id": mid, "message": f"Fetching match {i} of {len(to_fetch)} id {mid}"}

            try:
                df_ev, pos_df_file = _scrape_match_bundle(
                    ws_obj=ws,
                    match_id=mid,
                    require_positions=scrape_positions,
                )
            except Exception as exc:
                first_error = f"{type(exc).__name__}: {exc}"
                yield {
                    "kind": "warning",
                    "match_id": mid,
                    "stage": "soccerdata_event_failed",
                    "message": (
                        f"soccerdata failed for match {mid}. "
                        f"Trying the direct WhoScored match page fallback. Reason: {first_error}"
                    ),
                }

                try:
                    if headless:
                        retry_kwargs: dict[str, Any] = {
                            "leagues": league,
                            "seasons": soccerdata_season,
                            "headless": False,
                            "path_to_browser": str(resolved_browser),
                        }
                        retry_ws = _make_cached_ws(**retry_kwargs)
                        df_ev, pos_df_file = _scrape_match_bundle(
                            ws_obj=retry_ws,
                            match_id=mid,
                            require_positions=scrape_positions,
                        )
                    else:
                        raise RuntimeError(first_error)
                except Exception as retry_exc:
                    try:
                        df_ev, pos_df_file = _scrape_match_bundle_from_match_page(
                            match_id=mid,
                            browserpath=str(resolved_browser),
                            headless=False,
                            require_positions=scrape_positions,
                            league=league,
                            season=season,
                            home_team=h or "",
                            away_team=a or "",
                        )
                        yield {
                            "kind": "status",
                            "match_id": mid,
                            "stage": "custom_event_fallback_success",
                            "message": f"Direct WhoScored match page fallback returned {len(df_ev)} event rows for match {mid}.",
                        }
                    except Exception as fallback_exc:
                        fail_reason = (
                            f"scrape failed. soccerdata={first_error}; "
                            f"visible_retry={type(retry_exc).__name__}: {retry_exc}; "
                            f"match_page_fallback={type(fallback_exc).__name__}: {fallback_exc}"
                        )

            if fail_reason is None:
                df_ev = df_ev.copy()
                if "player_position" not in df_ev.columns:
                    df_ev["player_position"] = "Unknown"
                if "position" not in df_ev.columns:
                    df_ev["position"] = "Unknown"
                if "position_group" not in df_ev.columns:
                    df_ev["position_group"] = ""

                if scrape_positions and isinstance(pos_df_file, pd.DataFrame) and not pos_df_file.empty:
                    try:
                        df_ev = _append_positions_to_events(df_ev, pos_df_file)
                        _append_positions_csv(positions_csv, pos_df_file)
                    except Exception as exc:
                        yield {"kind": "warning", "message": f"Position file scrape skipped for match {mid}: {exc}"}

            if fail_reason is None:
                team_col = _get_event_team_col(df_ev)
                if not team_col:
                    fail_reason = "No team column was found in the scraped events DataFrame."

            if fail_reason is None:
                allowed_names = [x for x in [h, a] if x]
                allowed_map = {_norm_team_name(x): x for x in allowed_names}

                id_to_name: dict[int, str] = {}
                if pd.notna(hid):
                    id_to_name[int(hid)] = h
                if pd.notna(aid):
                    id_to_name[int(aid)] = a

                df_ev = df_ev.copy()
                if "match_id" in df_ev.columns:
                    df_ev["match_id"] = mid
                else:
                    df_ev.insert(0, "match_id", mid)

                event_team_id_col = _get_event_team_id_col(df_ev)

                if event_team_id_col:
                    df_ev[event_team_id_col] = pd.to_numeric(df_ev[event_team_id_col], errors="coerce")

                if event_team_id_col and df_ev[event_team_id_col].notna().any():
                    group_key = event_team_id_col
                else:
                    group_key = str(team_col)

                for group_value, g in df_ev.groupby(group_key):
                    canonical_team_name = None

                    if group_key == event_team_id_col and pd.notna(group_value):
                        canonical_team_name = id_to_name.get(int(group_value))
                        scraped_teams.append(f"id:{int(group_value)}")
                    else:
                        team_name_raw = str(group_value)
                        scraped_teams.append(team_name_raw)
                        canonical_team_name = allowed_map.get(_norm_team_name(team_name_raw))

                        if canonical_team_name is None:
                            alias_map = {
                                "bayern": "Bayern Munich",
                                "rbl": "RB Leipzig",
                            }
                            canonical_team_name = alias_map.get(_norm_team_name(team_name_raw))

                    if canonical_team_name is None:
                        continue

                    if mid in existing_by_team.get(canonical_team_name, set()):
                        continue

                    out_path = team_paths.get(canonical_team_name)
                    if out_path is None:
                        team_dir = out_root / _safe_slug(canonical_team_name)
                        team_dir.mkdir(parents=True, exist_ok=True)
                        out_path = team_dir / f"{_safe_slug(season)}.csv"
                        team_paths[canonical_team_name] = out_path
                        existing_by_team[canonical_team_name] = _read_existing_match_ids(out_path)

                    rows_appended = _append_event_rows_csv(out_path, g, mid)

                    rows_written_this_match += rows_appended
                    written_rows += rows_appended
                    existing_by_team[canonical_team_name].add(mid)

                    yield {
                        "kind": "write",
                        "match_id": mid,
                        "team": canonical_team_name,
                        "rows_written": int(rows_appended),
                        "path": str(out_path),
                        "message": f"Wrote {rows_appended} rows for match {mid} to {out_path}",
                    }

            if rows_written_this_match > 0:
                successful_fetch.add(mid)
                if i % 10 == 0:
                    time.sleep(random.uniform(3.5, 6.0))
                else:
                    time.sleep(random.uniform(0.7, 1.3))
                continue

            if fail_reason is None and already_present_both:
                successful_fetch.add(mid)
                yield {"kind": "warning", "match_id": mid, "message": f"Match {mid} already existed in both team season files. Marked as success."}
                if i % 10 == 0:
                    time.sleep(random.uniform(3.5, 6.0))
                else:
                    time.sleep(random.uniform(0.7, 1.3))
                continue

            if fail_reason is None:
                fail_reason = (
                    f"Match fetched but no rows were written. "
                    f"Schedule teams: {[h, a]}. "
                    f"Already present home={already_present_home}, away={already_present_away}. "
                    f"Scraped team values: {sorted(set(scraped_teams))[:10]}"
                )

            new_failed.add(mid)
            failure_details[mid] = {
                "match_id": mid,
                "home_team": h or "",
                "away_team": a or "",
                "reason": fail_reason or "",
                "failed_at": datetime.now().isoformat(timespec="seconds"),
                "league": league,
                "season": season,
                "group_key": group_key,
                "team_col": team_col or "",
                "event_team_id_col": event_team_id_col or "",
                "scraped_teams": ", ".join(sorted(set(scraped_teams))[:10]),
                "match_slug": match_url_slug,
            }

            yield {
                "kind": "error",
                "match_id": mid,
                "home_team": h or "",
                "away_team": a or "",
                "message": (
                    f"FAIL {mid} | schedule teams={[h, a]} | "
                    f"group_key={group_key} | "
                    f"team_col={team_col} | "
                    f"event_team_id_col={event_team_id_col} | "
                    f"scraped_teams={sorted(set(scraped_teams))[:10]} | "
                    f"match_slug={match_url_slug} | "
                    f"df_ev_cols={list(df_ev.columns)[:30] if isinstance(df_ev, pd.DataFrame) else []} | "
                    f"reason={fail_reason}"
                ),
                "reason": fail_reason,
            }

            if fail_fast and mid == first_mid:
                combined_failed = (prev_failed - successful_fetch) | new_failed
                _save_failed_ids(failed_csv, combined_failed, failure_details=failure_details)
                yield {
                    "kind": "stopped",
                    "message": f"Stopped early. First match id {mid} did not scrape and save correctly. Reason: {fail_reason}",
                    "reason": fail_reason,
                    "failed_csv": str(failed_csv),
                    "failed_ids": sorted(combined_failed),
                }
                return

            if i % 10 == 0:
                time.sleep(random.uniform(3.5, 6.0))
            else:
                time.sleep(random.uniform(0.7, 1.3))

    combined_failed = (prev_failed - successful_fetch) | new_failed
    if combined_failed:
        _save_failed_ids(failed_csv, combined_failed, failure_details=failure_details)
    else:
        if failed_csv.exists():
            failed_csv.unlink()

    yield {
        "kind": "complete",
        "message": "Events export finished.",
        "saved_under": str(out_root),
        "written_rows": written_rows,
        "failed_count": len(combined_failed),
        "failed_ids": sorted(combined_failed),
        "failed_csv": str(failed_csv) if combined_failed else "",
    }