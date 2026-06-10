from __future__ import annotations

import json
import random
import re
import time
import unicodedata
from html import unescape
from typing import Any
from urllib.parse import quote

import pandas as pd

from app.services.scraper_service import setup_driver

# WhoScored league metadata used by both the custom schedule fallback and the app UI.
# region_id and tournament_id are taken from current WhoScored competition URLs.
# season_mode controls how the season dropdown is matched on WhoScored.
LEAGUE_PRESETS: dict[str, dict[str, Any]] = {
    # Top five first divisions
    "ENG-Premier League": {
        "nation": "England", "tier": "T1", "region_id": 252, "tournament_id": 2,
        "slug": "england-premier-league", "season_mode": "split", "group": "Top five leagues",
        "season_overrides": {
            "2025": {
                "season_id": 10743,
                "stage_id": 24533,
                "slugs": ["england-premier-league-2025-2026", "England-Premier-League-2025-2026"],
            },
        },
    },
    "ESP-La Liga": {
        "nation": "Spain", "tier": "T1", "region_id": 206, "tournament_id": 4,
        "slug": "spain-laliga", "season_mode": "split", "group": "Top five leagues",
        "season_overrides": {
            "2025": {
                "season_id": 10803,
                "stage_id": 24622,
                "slugs": ["spain-laliga-2025-2026", "Spain-LaLiga-2025-2026"],
            },
        },
    },
    "FRA-Ligue 1": {
        "nation": "France", "tier": "T1", "region_id": 74, "tournament_id": 22,
        "slug": "france-ligue-1", "season_mode": "split", "group": "Top five leagues",
        "season_overrides": {
            "2025": {
                "season_id": 10792,
                "stage_id": 24609,
                "slugs": ["france-ligue-1-2025-2026", "France-Ligue-1-2025-2026"],
            },
        },
    },
    "GER-Bundesliga": {
        "nation": "Germany", "tier": "T1", "region_id": 81, "tournament_id": 3,
        "slug": "germany-bundesliga", "season_mode": "split", "group": "Top five leagues",
        "season_overrides": {
            "2025": {
                "season_id": 10720,
                "stage_id": 24478,
                "slugs": ["germany-bundesliga-2025-2026", "Germany-Bundesliga-2025-2026"],
            },
        },
    },
    "ITA-Serie A": {
        "nation": "Italy", "tier": "T1", "region_id": 108, "tournament_id": 5,
        "slug": "italy-serie-a", "season_mode": "split", "group": "Top five leagues",
        "season_overrides": {
            "2025": {
                "season_id": 10732,
                "stage_id": 24500,
                "slugs": ["italy-serie-a-2025-2026", "Italy-Serie-A-2025-2026"],
            },
        },
    },

    # Strong European scouting leagues
    "ENG-Championship": {
        "nation": "England", "tier": "T2", "region_id": 252, "tournament_id": 7,
        "slug": "England-Championship", "season_mode": "split", "group": "England",
    },
    "ENG-League One": {
        "nation": "England", "tier": "T3", "region_id": 252, "tournament_id": 8,
        "slug": "England-League-One", "season_mode": "split", "group": "England",
    },
    "ENG-League Two": {
        "nation": "England", "tier": "T4", "region_id": 252, "tournament_id": 9,
        "slug": "England-League-Two", "season_mode": "split", "group": "England",
        "season_overrides": {
            "2025": {
                "season_id": 10786,
                "stage_id": 24582,
                "extra_stage_ids": [25407],
                "slugs": ["england-league-two-2025-2026", "England-League-Two-2025-2026"],
            },
        },
    },
    "ESP-Segunda Division": {
        "nation": "Spain", "tier": "T2", "region_id": 206, "tournament_id": 63,
        "slug": "Spain-Segunda-Division", "season_mode": "split", "group": "Spain",
    },
    "FRA-Ligue 2": {
        "nation": "France", "tier": "T2", "region_id": 74, "tournament_id": 37,
        "slug": "France-Ligue-2", "season_mode": "split", "group": "France",
    },
    "GER-2. Bundesliga": {
        "nation": "Germany", "tier": "T2", "region_id": 81, "tournament_id": 6,
        "slug": "Germany-2-Bundesliga", "season_mode": "split", "group": "Germany",
    },
    "GER-3. Liga": {
        "nation": "Germany", "tier": "T3", "region_id": 81, "tournament_id": 308,
        "slug": "Germany-3-Liga", "season_mode": "split", "group": "Germany",
    },
    "ITA-Serie B": {
        "nation": "Italy", "tier": "T2", "region_id": 108, "tournament_id": 19,
        "slug": "Italy-Serie-B", "season_mode": "split", "group": "Italy",
    },
    "NED-Eredivisie": {
        "nation": "Netherlands", "tier": "T1", "region_id": 155, "tournament_id": 13,
        "slug": "Netherlands-Eredivisie", "season_mode": "split", "group": "Netherlands",
    },
    "NED-Eerste Divisie": {
        "nation": "Netherlands", "tier": "T2", "region_id": 155, "tournament_id": 66,
        "slug": "Netherlands-Eerste-Divisie", "season_mode": "split", "group": "Netherlands",
    },
    "BEL-First Division A": {
        "nation": "Belgium", "tier": "T1", "region_id": 22, "tournament_id": 18,
        "slug": "Belgium-Jupiler-Pro-League", "season_mode": "split", "group": "Belgium",
        "season_overrides": {
            "2025": {
                "season_id": 10759,
                "stage_id": 24549,
                "extra_stage_ids": [25287, 25288, 25289, 25500],
                "slugs": ["belgium-jupiler-pro-league-2025-2026", "Belgium-Jupiler-Pro-League-2025-2026"],
            },
        },
    },
    "BEL-First Division B": {
        "nation": "Belgium", "tier": "T2", "region_id": 22, "tournament_id": 137,
        "slug": "Belgium-Second-Division", "season_mode": "split", "group": "Belgium",
    },
    "POR-Liga Portugal": {
        "nation": "Portugal", "tier": "T1", "region_id": 177, "tournament_id": 21,
        "slug": "Portugal-Liga-Portugal", "season_mode": "split", "group": "Portugal",
        "season_overrides": {
            "2025": {
                "season_id": 10774,
                "stage_id": 24568,
                "slugs": ["portugal-liga-2025-2026", "Portugal-Liga-2025-2026"],
            },
        },
    },
    "POR-Liga Portugal 2": {
        "nation": "Portugal", "tier": "T2", "region_id": 177, "tournament_id": 139,
        "slug": "Portugal-Liga-2", "season_mode": "split", "group": "Portugal",
        "season_overrides": {
            "2025": {
                "season_id": 10775,
                "stage_id": 24569,
                "slugs": ["portugal-liga-2-2025-2026", "Portugal-Liga-2-2025-2026"],
            },
        },
    },
    "SCO-Premiership": {
        "nation": "Scotland", "tier": "T1", "region_id": 253, "tournament_id": 20,
        "slug": "Scotland-Premiership", "season_mode": "split", "group": "Scotland",
    },
    "SCO-Championship": {
        "nation": "Scotland", "tier": "T2", "region_id": 253, "tournament_id": 71,
        "slug": "Scotland-Championship", "season_mode": "split", "group": "Scotland",
    },
    "TUR-Süper Lig": {
        "nation": "Turkey", "tier": "T1", "region_id": 225, "tournament_id": 17,
        "slug": "Turkey-Super-Lig", "season_mode": "split", "group": "Other Europe",
        "season_overrides": {
            "2025": {
                "season_id": 10807,
                "stage_id": 24627,
                "slugs": ["turkey-super-lig-2025-2026", "Turkey-Super-Lig-2025-2026"],
            },
        },
    },
    "SAU-Saudi Pro League": {
        "nation": "Saudi Arabia", "tier": "T1", "region_id": 197, "tournament_id": 955,
        "slug": "Saudi-Arabia-Pro-League", "season_mode": "split", "group": "Other markets",
    },

    # Calendar year leagues
    "BRA-Série A": {
        "nation": "Brazil", "tier": "T1", "region_id": 31, "tournament_id": 95,
        "slug": "Brazil-Brasileirão", "season_mode": "calendar", "group": "South America",
        "season_overrides": {
            "2026": {
                "season_id": 10980,
                "stage_id": 25039,
                "slugs": ["brazil-brasileir%C3%A3o-2026", "brazil-brasileirão-2026", "Brazil-Brasileirão-2026", "Brazil-Brasileirao-2026"],
            },
        },
    },
    "ARG-Liga Profesional": {
        "nation": "Argentina", "tier": "T1", "region_id": 11, "tournament_id": 68,
        "slug": "Argentina-Liga-Profesional", "season_mode": "calendar", "group": "South America",
    },
    "USA-MLS": {
        "nation": "USA", "tier": "T1", "region_id": 233, "tournament_id": 85,
        "slug": "USA-Major-League-Soccer", "season_mode": "calendar", "group": "Other markets",
    },
}

BUILTIN_SOCCERDATA_WS_LEAGUES = frozenset({
    "ENG-Premier League",
    "ESP-La Liga",
    "FRA-Ligue 1",
    "GER-Bundesliga",
    "ITA-Serie A",
    "INT-World Cup",
    "INT-European Championship",
    "INT-Women's World Cup",
})

SOCCERDATA_LEAGUE_DICT: dict[str, dict[str, str]] = {
    "NED-Eredivisie": {"WhoScored": "Netherlands - Eredivisie", "season_start": "Aug", "season_end": "May"},
    "NED-Eerste Divisie": {"WhoScored": "Netherlands - Eerste Divisie", "season_start": "Aug", "season_end": "May"},
    "BEL-First Division A": {"WhoScored": "Belgium - Jupiler Pro League", "season_start": "Jul", "season_end": "May"},
    "BEL-First Division B": {"WhoScored": "Belgium - First Division B", "season_start": "Aug", "season_end": "Apr"},
    "FRA-Ligue 2": {"WhoScored": "France - Ligue 2", "season_start": "Aug", "season_end": "May"},
    "ENG-Championship": {"WhoScored": "England - Championship", "season_start": "Aug", "season_end": "May"},
    "ENG-League One": {"WhoScored": "England - League One", "season_start": "Aug", "season_end": "May"},
    "ENG-League Two": {"WhoScored": "England - League Two", "season_start": "Aug", "season_end": "May"},
    "ESP-Segunda Division": {"WhoScored": "Spain - Segunda Division", "season_start": "Aug", "season_end": "Jun"},
    "ESP-Segunda División": {"WhoScored": "Spain - Segunda Division", "season_start": "Aug", "season_end": "Jun"},
    "GER-2. Bundesliga": {"WhoScored": "Germany - 2. Bundesliga", "season_start": "Aug", "season_end": "May"},
    "GER-3. Liga": {"WhoScored": "Germany - 3. Liga", "season_start": "Aug", "season_end": "May"},
    "ITA-Serie B": {"WhoScored": "Italy - Serie B", "season_start": "Aug", "season_end": "May"},
    "POR-Liga NOS": {"WhoScored": "Portugal - Liga NOS", "season_start": "Aug", "season_end": "May"},
    "POR-Liga Portugal": {"WhoScored": "Portugal - Liga Portugal", "season_start": "Aug", "season_end": "May"},
    "POR-Liga Portugal 2": {"WhoScored": "Portugal - Liga Portugal 2", "season_start": "Aug", "season_end": "May"},
    "SCO-Premiership": {"WhoScored": "Scotland - Premiership", "season_start": "Aug", "season_end": "May"},
    "SCO-Championship": {"WhoScored": "Scotland - Championship", "season_start": "Aug", "season_end": "May"},
    "TUR-Süper Lig": {"WhoScored": "Turkey - Super Lig", "season_start": "Aug", "season_end": "May"},
    "TUR-Super Lig": {"WhoScored": "Turkey - Super Lig", "season_start": "Aug", "season_end": "May"},
    "SAU-Saudi Pro League": {"WhoScored": "Saudi Arabia - Pro League", "season_start": "Aug", "season_end": "May"},
    "USA-MLS": {"WhoScored": "USA - Major League Soccer", "season_start": "Feb", "season_end": "Dec"},
    "BRA-Série A": {"WhoScored": "Brazil - Serie A", "season_start": "Apr", "season_end": "Dec"},
    "BRA-Serie A": {"WhoScored": "Brazil - Serie A", "season_start": "Apr", "season_end": "Dec"},
    "ARG-Primera División": {"WhoScored": "Argentina - Primera Division", "season_start": "Jan", "season_end": "Dec"},
    "ARG-Liga Profesional": {"WhoScored": "Argentina - Primera Division", "season_start": "Jan", "season_end": "Dec"},
}

SOCCERDATA_BACKED_LEAGUES = BUILTIN_SOCCERDATA_WS_LEAGUES | frozenset(SOCCERDATA_LEAGUE_DICT)

# Every league in LEAGUE_PRESETS can use the custom WhoScored fallback.
# soccerdata is still tried first for built in and configured leagues, but this
# rescue layer prevents a blocked or corrupt soccerdata response from stopping
# the whole schedule workflow.
CUSTOM_WS_LEAGUES: dict[str, tuple[int, int, str]] = {
    league: (int(meta["region_id"]), int(meta["tournament_id"]), str(meta["slug"]))
    for league, meta in LEAGUE_PRESETS.items()
}


def get_league_presets_payload() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for league, meta in LEAGUE_PRESETS.items():
        rows.append(
            {
                "league": league,
                "nation": str(meta.get("nation", "")),
                "tier": str(meta.get("tier", "")),
                "folder": f"{meta.get('nation', '')} {meta.get('tier', '')}".strip(),
                "group": str(meta.get("group", "Other")),
                "season_mode": str(meta.get("season_mode", "split")),
                "source": (
                    "soccerdata_primary_custom_rescue"
                    if league in BUILTIN_SOCCERDATA_WS_LEAGUES
                    else ("soccerdata_with_custom_fallback" if league in SOCCERDATA_BACKED_LEAGUES else "custom_who_scored")
                ),
                "has_custom_fallback": bool(league in CUSTOM_WS_LEAGUES),
            }
        )
    return rows


def resolve_league_folder(league: str, nation: str | None = None, tier: str | None = None) -> tuple[str, str, bool]:
    clean_nation = str(nation or "").strip()
    clean_tier = str(tier or "").strip()
    if clean_nation and clean_tier:
        return clean_nation, clean_tier, False

    league_key = str(league or "")
    meta = LEAGUE_PRESETS.get(league_key)
    if not meta and league_key in {"TUR-Super Lig", "BRA-Serie A", "ARG-Primera División", "POR-Liga NOS", "ESP-Segunda División"}:
        alias_map = {
            "TUR-Super Lig": "TUR-Süper Lig",
            "BRA-Serie A": "BRA-Série A",
            "ARG-Primera División": "ARG-Liga Profesional",
            "POR-Liga NOS": "POR-Liga Portugal",
            "ESP-Segunda División": "ESP-Segunda Division",
        }
        meta = LEAGUE_PRESETS.get(alias_map.get(league_key, league_key))
    if not meta:
        return clean_nation, clean_tier, False

    resolved_nation = clean_nation or str(meta.get("nation", "")).strip()
    resolved_tier = clean_tier or str(meta.get("tier", "")).strip()
    return resolved_nation, resolved_tier, True


def _season_label(season: str, season_mode: str = "split") -> str:
    text = str(season or "").strip()
    mode = str(season_mode or "split").strip().lower()

    if mode == "calendar":
        if re.fullmatch(r"\d{4}", text) and text.startswith("20"):
            return text
        if re.fullmatch(r"\d{4}", text):
            return f"20{text[2:]}"
        if re.fullmatch(r"\d{4}/\d{4}", text):
            return text.split("/")[-1]
        if re.fullmatch(r"\d{4}-\d{4}", text):
            return text.split("-")[-1]
        raise ValueError(f"Unsupported calendar season format for WhoScored custom scraper: {season}")

    if re.fullmatch(r"\d{4}/\d{4}", text):
        return text

    if re.fullmatch(r"\d{4}-\d{4}", text):
        return text.replace("-", "/")

    if re.fullmatch(r"\d{4}", text) and not text.startswith("20"):
        return f"20{text[:2]}/20{text[2:]}"

    if re.fullmatch(r"\d{4}", text):
        y = int(text)
        return f"{y}/{y + 1}"

    raise ValueError(f"Unsupported season format for WhoScored custom scraper: {season}")


def _emit_status(status_callback, kind: str, message: str, **extra) -> None:
    payload = {"kind": kind, "message": message}
    payload.update(extra)
    if status_callback is not None:
        try:
            status_callback(payload)
        except Exception:
            pass


def _slug_candidates(slug: str) -> list[str]:
    raw = str(slug or "").strip().strip("/")
    ascii_slug = raw.encode("ascii", "ignore").decode("ascii")
    encoded_raw = quote(raw, safe="%-")
    encoded_ascii = quote(ascii_slug, safe="%-")
    candidates = [raw, encoded_raw, ascii_slug, encoded_ascii]
    if "Brasileirao" in ascii_slug:
        candidates.append(ascii_slug.replace("Brasileirao", "Brasileir%C3%A3o"))
    out: list[str] = []
    for item in candidates:
        cleaned = str(item or "").strip().strip("/")
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def _season_override(meta: dict[str, Any], season: str, season_label: str) -> dict[str, Any] | None:
    overrides = meta.get("season_overrides")
    if not isinstance(overrides, dict):
        return None

    keys = []
    for value in [season, season_label]:
        text = str(value or "").strip()
        if text and text not in keys:
            keys.append(text)
    if "/" in season_label:
        start_year, last_year = season_label.split("/", 1)
        for extra_key in [start_year, last_year, season_label.replace("/", "-")]:
            if extra_key and extra_key not in keys:
                keys.append(extra_key)

    for key in keys:
        item = overrides.get(key)
        if isinstance(item, dict):
            return item
    return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _build_stage_fixtures_url(region_id: int, tournament_id: int, season_id: int, stage_id: int, slug: str) -> str:
    return (
        f"https://www.whoscored.com/Regions/{region_id}/Tournaments/{tournament_id}"
        f"/Seasons/{season_id}/Stages/{stage_id}/Fixtures/{slug}"
    )


def _build_season_url(region_id: int, tournament_id: int, season_id: int, slug: str) -> str:
    return f"https://www.whoscored.com/Regions/{region_id}/Tournaments/{tournament_id}/Seasons/{season_id}/{slug}"


def _build_season_fixtures_url(region_id: int, tournament_id: int, season_id: int, slug: str) -> str:
    return f"https://www.whoscored.com/Regions/{region_id}/Tournaments/{tournament_id}/Seasons/{season_id}/Fixtures/{slug}"


def _build_stage_url(region_id: int, tournament_id: int, season_id: int, stage_id: int) -> str:
    return (
        f"https://www.whoscored.com/Regions/{region_id}/Tournaments/{tournament_id}"
        f"/Seasons/{season_id}/Stages/{stage_id}"
    )


def _build_stage_show_url(region_id: int, tournament_id: int, season_id: int, stage_id: int, slug: str) -> str:
    return (
        f"https://www.whoscored.com/Regions/{region_id}/Tournaments/{tournament_id}"
        f"/Seasons/{season_id}/Stages/{stage_id}/Show/{slug}"
    )


def _clean_stage_label(value: Any) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—|,")
    return text


def _normalise_phase_key(value: Any) -> str:
    text = unescape(str(value or "")).lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text)


PLAYOFF_STAGE_KEYWORDS = frozenset(
    {
        "playoff",
        "playoffs",
        "promotion",
        "semifinal",
        "semifinals",
        "semifinal",
        "semifinals",
        "final",
        "finals",
        "relegation",
        "championshipround",
        "championshipgroup",
        "promotionround",
        "relegationround",
        "europaplayoff",
        "europaplayoffs",
        "conferenceleagueplayoff",
        "conferenceleagueplayoffs",
        "postseason",
        "postseason",
        "finalseries",
        "knockout",
    }
)


def _classify_stage_phase(stage_id: int, primary_stage_id: int | None, label: str) -> tuple[str, bool]:
    if primary_stage_id is not None and int(stage_id) == int(primary_stage_id):
        return "League phase", False

    key = _normalise_phase_key(label)
    if any(keyword in key for keyword in PLAYOFF_STAGE_KEYWORDS):
        return "Playoffs", True

    return "Additional stage", True


def _merge_stage_candidate(
    candidates: dict[int, dict[str, Any]],
    stage_id: int | None,
    primary_stage_id: int | None,
    label: str = "",
    source: str = "",
) -> None:
    if stage_id is None:
        return

    stage_id_int = int(stage_id)
    clean_label = _clean_stage_label(label)
    phase, is_playoff = _classify_stage_phase(stage_id_int, primary_stage_id, clean_label)

    existing = candidates.get(stage_id_int)
    if existing is None:
        candidates[stage_id_int] = {
            "stage_id": stage_id_int,
            "stage_name": clean_label or phase,
            "competition_phase": phase,
            "is_playoff": bool(is_playoff),
            "source": source,
        }
        return

    if clean_label and (
        not str(existing.get("stage_name", "")).strip()
        or str(existing.get("stage_name", "")).strip() in {"League phase", "Additional stage", "Playoffs"}
    ):
        existing["stage_name"] = clean_label

    existing["is_playoff"] = bool(existing.get("is_playoff")) or bool(is_playoff)
    if existing.get("competition_phase") in {"Additional stage", ""} and phase != "Additional stage":
        existing["competition_phase"] = phase
    if source and not existing.get("source"):
        existing["source"] = source


def _stage_ids_from_override(override: dict[str, Any] | None) -> list[int]:
    if not isinstance(override, dict):
        return []

    values: list[Any] = []
    for key in ["stage_ids", "extra_stage_ids", "playoff_stage_ids", "postseason_stage_ids"]:
        raw = override.get(key)
        if raw is None:
            continue
        if isinstance(raw, (list, tuple, set)):
            values.extend(raw)
        else:
            values.append(raw)

    out: list[int] = []
    for value in values:
        stage_id = _safe_int(value)
        if stage_id is not None and stage_id not in out:
            out.append(stage_id)
    return out


def _stage_candidates_from_source(
    source: str,
    region_id: int,
    tournament_id: int,
    season_id: int,
    primary_stage_id: int | None,
    source_name: str,
) -> dict[int, dict[str, Any]]:
    candidates: dict[int, dict[str, Any]] = {}
    if not source:
        return candidates

    escaped_region = re.escape(str(region_id))
    escaped_tournament = re.escape(str(tournament_id))
    escaped_season = re.escape(str(season_id))

    anchor_pattern = re.compile(
        rf"<a[^>]+href=[\"'][^\"']*/Regions/{escaped_region}/Tournaments/{escaped_tournament}"
        rf"/Seasons/{escaped_season}/Stages/(\d+)(?:/[^\"']*)?[\"'][^>]*>(.*?)</a>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in anchor_pattern.finditer(source):
        stage_id = _safe_int(match.group(1))
        label = _clean_stage_label(match.group(2))
        _merge_stage_candidate(candidates, stage_id, primary_stage_id, label, source_name)

    href_pattern = re.compile(
        rf"/Regions/{escaped_region}/Tournaments/{escaped_tournament}"
        rf"/Seasons/{escaped_season}/Stages/(\d+)(?:/[^\"'<>\s]*)?",
        re.IGNORECASE,
    )
    for match in href_pattern.finditer(source):
        stage_id = _safe_int(match.group(1))
        around = source[max(0, match.start() - 180): match.end() + 220]
        label = _clean_stage_label(around)
        _merge_stage_candidate(candidates, stage_id, primary_stage_id, label, source_name)

    option_pattern = re.compile(
        rf"<option[^>]+value=[\"']{escaped_season}[\"'][^>]+data-stage-id=[\"'](\d+)[\"'][^>]*>(.*?)</option>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in option_pattern.finditer(source):
        stage_id = _safe_int(match.group(1))
        label = _clean_stage_label(match.group(2))
        _merge_stage_candidate(candidates, stage_id, primary_stage_id, label, source_name)

    return candidates


def _discover_stage_candidates(
    sb,
    region_id: int,
    tournament_id: int,
    season_id: int,
    primary_stage_id: int | None,
    resolved_slug: str,
    season_label: str,
    override: dict[str, Any] | None = None,
    status_callback=None,
) -> list[dict[str, Any]]:
    candidates: dict[int, dict[str, Any]] = {}
    _merge_stage_candidate(candidates, primary_stage_id, primary_stage_id, "League phase", "primary_stage")

    for stage_id in _stage_ids_from_override(override):
        label = "League phase" if primary_stage_id is not None and int(stage_id) == int(primary_stage_id) else "Configured extra stage"
        _merge_stage_candidate(candidates, stage_id, primary_stage_id, label, "season_override")

    urls: list[tuple[str, str]] = []
    for slug_candidate in _slug_candidates(resolved_slug)[:3]:
        urls.extend(
            [
                ("season_page", _build_season_url(region_id, tournament_id, season_id, slug_candidate)),
                ("season_fixtures_page", _build_season_fixtures_url(region_id, tournament_id, season_id, slug_candidate)),
            ]
        )
        if primary_stage_id is not None:
            urls.extend(
                [
                    ("primary_stage_page", _build_stage_url(region_id, tournament_id, season_id, int(primary_stage_id))),
                    ("primary_stage_show_page", _build_stage_show_url(region_id, tournament_id, season_id, int(primary_stage_id), slug_candidate)),
                ]
            )

    seen_urls: set[str] = set()
    for source_name, url in urls:
        if url in seen_urls:
            continue
        seen_urls.add(url)

        try:
            _emit_status(
                status_callback,
                "status",
                "Checking WhoScored for additional season stages such as playoffs.",
                stage="custom_stage_discovery_open",
                url=url,
            )
            sb.open(url)
            time.sleep(random.uniform(2.2, 3.8))
            page_title = sb.get_title() or ""
            page_source = sb.get_page_source() or ""
        except Exception as exc:
            _emit_status(
                status_callback,
                "log",
                f"Could not open WhoScored stage discovery page: {type(exc).__name__}: {exc}",
                stage="custom_stage_discovery_failed",
                url=url,
            )
            continue

        if _is_challenge_source(page_title, page_source):
            _emit_status(
                status_callback,
                "warning",
                "WhoScored returned a challenge page while checking extra stages. Continuing with the stages already known.",
                stage="custom_stage_discovery_challenge",
                page_title=page_title,
                url=url,
            )
            continue

        found = _stage_candidates_from_source(
            page_source,
            region_id=region_id,
            tournament_id=tournament_id,
            season_id=season_id,
            primary_stage_id=primary_stage_id,
            source_name=source_name,
        )
        for stage in found.values():
            _merge_stage_candidate(
                candidates,
                _safe_int(stage.get("stage_id")),
                primary_stage_id,
                str(stage.get("stage_name", "")),
                str(stage.get("source", source_name)),
            )

    ordered = sorted(
        candidates.values(),
        key=lambda item: (
            0 if primary_stage_id is not None and int(item["stage_id"]) == int(primary_stage_id) else 1,
            0 if bool(item.get("is_playoff")) else 1,
            int(item["stage_id"]),
        ),
    )

    if len(ordered) > 1:
        _emit_status(
            status_callback,
            "status",
            f"Resolved {len(ordered)} WhoScored stages for {season_label}, including additional playoff or postseason stages where available.",
            stage="custom_stage_discovery_complete",
            stage_ids=", ".join(str(item["stage_id"]) for item in ordered),
        )
    else:
        _emit_status(
            status_callback,
            "status",
            f"Resolved one WhoScored stage for {season_label}. No additional playoff stage was detected.",
            stage="custom_stage_discovery_complete",
            stage_ids=", ".join(str(item["stage_id"]) for item in ordered),
        )

    return ordered


def _build_month_data_url(stage_id: int, year_month: str) -> str:
    return f"https://www.whoscored.com/tournaments/{stage_id}/data/?d={year_month}"


def _is_challenge_source(title: str, source: str) -> bool:
    haystack = f"{title or ''} {source[:5000] if source else ''}".lower()
    return "just a moment" in haystack or "challenges.cloudflare.com" in haystack or "incapsula incident" in haystack


def _extract_balanced_json(text: str, start_pos: int) -> str | None:
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


def _extract_assigned_json(source: str, names: list[str]) -> Any | None:
    if not source:
        return None

    for name in names:
        for match in re.finditer(rf"(?:var\s+)?{re.escape(name)}\s*=", source):
            raw_json = _extract_balanced_json(source, match.end())
            if not raw_json:
                continue
            try:
                return json.loads(raw_json)
            except Exception:
                continue
    return None


def _json_from_text(text: str) -> Any | None:
    text = unescape(str(text or "")).strip()
    if not text:
        return None
    start = min([pos for pos in [text.find("{"), text.find("[")] if pos >= 0], default=-1)
    if start > 0:
        text = text[start:]
    try:
        return json.loads(text)
    except Exception:
        return None



def _page_to_text(page: Any) -> str:
    for attr in ["text", "body", "html", "content"]:
        try:
            value = getattr(page, attr)
            value = value() if callable(value) else value
            if value:
                return str(value)
        except Exception:
            continue
    try:
        return str(page)
    except Exception:
        return ""


def _fetch_text_with_scrapling(url: str, status_callback=None, stage: str = "custom_scrapling_fetch") -> str | None:
    try:
        from scrapling.fetchers import Fetcher, StealthyFetcher
    except Exception as exc:
        _emit_status(
            status_callback,
            "log",
            "Scrapling is not installed. Skipping the Scrapling fetch layer.",
            stage="custom_scrapling_missing",
            detail=str(exc),
        )
        return None

    attempts = [
        ("Fetcher", Fetcher, "get"),
        ("StealthyFetcher", StealthyFetcher, "fetch"),
    ]

    for label, cls, method_name in attempts:
        _emit_status(
            status_callback,
            "status",
            f"Trying Scrapling {label} for WhoScored data.",
            stage=stage,
            url=url,
            fetcher=label,
        )
        try:
            try:
                fetcher = cls()
            except TypeError:
                fetcher = cls

            method = getattr(fetcher, method_name)
            try:
                if label == "Fetcher":
                    page = method(url, stealthy_headers=True)
                else:
                    page = method(url, headless=True, network_idle=True, solve_cloudflare=True)
            except TypeError:
                try:
                    page = method(url, headless=True, network_idle=True)
                except TypeError:
                    page = method(url)

            text = _page_to_text(page)
            if text and not _is_challenge_source("", text):
                return text

            if text:
                _emit_status(
                    status_callback,
                    "warning",
                    f"Scrapling {label} received a challenge page.",
                    stage="custom_scrapling_challenge",
                    url=url,
                    fetcher=label,
                )
        except Exception as exc:
            _emit_status(
                status_callback,
                "log",
                f"Scrapling {label} failed: {type(exc).__name__}: {exc}",
                stage="custom_scrapling_failed",
                url=url,
                fetcher=label,
            )

    return None


def _fetch_json_with_scrapling(url: str, status_callback=None) -> Any | None:
    text = _fetch_text_with_scrapling(url, status_callback=status_callback, stage="custom_scrapling_json_fetch")
    if not text:
        return None
    return _json_from_text(text)


def _fetch_json_from_browser(sb, url: str) -> Any | None:
    driver = getattr(sb, "driver", None) or getattr(sb, "_driver", None)
    if driver is None:
        return None

    script = """
        const url = arguments[0];
        const done = arguments[arguments.length - 1];
        fetch(url, {
            credentials: 'include',
            headers: {
                'accept': 'application/json, text/javascript, */*; q=0.01',
                'x-requested-with': 'XMLHttpRequest'
            }
        })
        .then(response => response.text().then(text => done({
            ok: response.ok,
            status: response.status,
            text: text
        })))
        .catch(error => done({
            ok: false,
            status: 0,
            text: '',
            error: String(error)
        }));
    """

    try:
        result = driver.execute_async_script(script, url)
    except Exception:
        return None

    if not isinstance(result, dict):
        return None

    text = str(result.get("text") or "")
    if not text:
        return None
    return _json_from_text(text)


def _json_from_page_source(source: str) -> Any | None:
    if not source:
        return None

    text = source.strip()
    pre_match = re.search(r"<pre[^>]*>(.*?)</pre>", text, re.IGNORECASE | re.DOTALL)
    if pre_match:
        text = pre_match.group(1)
    else:
        body_match = re.search(r"<body[^>]*>(.*?)</body>", text, re.IGNORECASE | re.DOTALL)
        if body_match:
            text = body_match.group(1)
        text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)

    text = unescape(text).strip()
    if not text:
        return None

    start = min([pos for pos in [text.find("{"), text.find("[")] if pos >= 0], default=-1)
    if start > 0:
        text = text[start:]

    try:
        return json.loads(text)
    except Exception:
        return None


def _calendar_months_from_mask(mask: Any) -> list[str]:
    if not isinstance(mask, dict):
        return []

    months: list[str] = []
    for year_key, month_values in mask.items():
        year_text = str(year_key).strip()
        if not re.fullmatch(r"\d{4}", year_text):
            continue
        if not isinstance(month_values, list):
            continue
        for raw_month in month_values:
            month_num = _safe_int(raw_month)
            if month_num is None:
                continue
            # WhoScored's wsCalendar mask stores months as zero based values.
            actual_month = month_num + 1
            if 1 <= actual_month <= 12:
                months.append(f"{year_text}{actual_month:02d}")

    return sorted(dict.fromkeys(months))


def _fallback_months_for_season(season_label: str, season_mode: str) -> list[str]:
    label = str(season_label or "").strip()
    mode = str(season_mode or "split").strip().lower()

    months: list[str] = []
    if mode == "calendar":
        year_match = re.search(r"20\d{2}", label)
        if not year_match:
            return []
        year = int(year_match.group(0))
        months = [f"{year}{month:02d}" for month in range(1, 13)]
    else:
        split_match = re.fullmatch(r"(20\d{2})/(20\d{2})", label)
        if not split_match:
            return []
        start_year = int(split_match.group(1))
        end_year = int(split_match.group(2))
        months = [f"{start_year}{month:02d}" for month in range(7, 13)]
        months.extend(f"{end_year}{month:02d}" for month in range(1, 7))

    return months


def _extract_calendar_months_from_page(sb, source: str, season_label: str, season_mode: str) -> list[str]:
    raw = None
    try:
        raw = sb.execute_script(
            "try { return window.wsCalendar || (typeof wsCalendar !== 'undefined' ? wsCalendar : null); }"
            "catch(e) { return null; }"
        )
    except Exception:
        raw = None

    if raw is None:
        raw = _extract_assigned_json(source, ["wsCalendar", "window.wsCalendar"])

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = None

    mask = raw.get("mask") if isinstance(raw, dict) else None
    months = _calendar_months_from_mask(mask)
    if months:
        return months
    return _fallback_months_for_season(season_label, season_mode)


def _matches_from_tournament_payload(data: Any) -> list[dict]:
    if not isinstance(data, dict):
        return []

    rows: list[dict] = []
    tournaments = data.get("tournaments")
    if isinstance(tournaments, list):
        for tournament in tournaments:
            if not isinstance(tournament, dict):
                continue
            matches = tournament.get("matches")
            if isinstance(matches, list):
                rows.extend([item for item in matches if isinstance(item, dict)])

    if rows:
        return rows

    found = _find_matches_in_object(data)
    return found if found else []


def _dedupe_matches(matches: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in matches:
        key = str(item.get("id") or item.get("matchId") or item.get("gameId") or "").strip()
        if not key:
            key = json.dumps(item, sort_keys=True, default=str)[:300]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _read_matches_from_month_feed(
    sb,
    region_id: int,
    tournament_id: int,
    season_id: int,
    stage_id: int | None,
    league: str,
    season_label: str,
    season_mode: str,
    status_callback=None,
) -> list[dict]:
    if stage_id is None:
        return []

    stage_url = _build_stage_url(region_id, tournament_id, season_id, int(stage_id))
    _emit_status(
        status_callback,
        "status",
        "Opening WhoScored stage page to read the fixture calendar mask.",
        stage="custom_open_calendar",
        url=stage_url,
    )
    sb.open(stage_url)
    time.sleep(random.uniform(3.0, 4.5))

    page_title = sb.get_title() or ""
    page_source = sb.get_page_source() or ""
    if _is_challenge_source(page_title, page_source):
        _emit_status(
            status_callback,
            "warning",
            "WhoScored returned a challenge page while opening the calendar page.",
            stage="cloudflare_challenge",
            page_title=page_title,
            url=stage_url,
        )
        raise RuntimeError(
            "WhoScored returned a challenge page while opening the calendar. "
            "Retry with Headless unticked, clear the browser challenge if shown, then run again."
        )

    months = _extract_calendar_months_from_page(sb, page_source, season_label, season_mode)
    if not months:
        _emit_status(
            status_callback,
            "warning",
            "Could not resolve a WhoScored calendar mask, and no fallback months were generated.",
            stage="custom_calendar_missing",
            url=stage_url,
        )
        return []

    _emit_status(
        status_callback,
        "status",
        f"Resolved {len(months)} fixture calendar months for {league} {season_label}.",
        stage="custom_calendar_resolved",
        count=len(months),
        months=", ".join(months),
    )

    all_matches: list[dict] = []
    for idx, year_month in enumerate(months, start=1):
        data_url = _build_month_data_url(int(stage_id), year_month)
        _emit_status(
            status_callback,
            "status",
            f"Reading WhoScored monthly fixture feed {idx} of {len(months)}.",
            stage="custom_month_feed_open",
            url=data_url,
            month=year_month,
        )
        data = _fetch_json_with_scrapling(data_url, status_callback=status_callback)
        if data is None:
            data = _fetch_json_from_browser(sb, data_url)
        if data is None:
            sb.open(data_url)
            time.sleep(random.uniform(1.2, 2.1))

            title = sb.get_title() or ""
            source = sb.get_page_source() or ""
            if _is_challenge_source(title, source):
                _emit_status(
                    status_callback,
                    "warning",
                    "WhoScored returned a challenge page while opening the monthly fixture feed.",
                    stage="cloudflare_challenge",
                    page_title=title,
                    url=data_url,
                )
                raise RuntimeError(
                    "WhoScored returned a challenge page while reading the monthly fixture feed. "
                    "Retry with Headless unticked."
                )

            data = _json_from_page_source(source)

        month_matches = _matches_from_tournament_payload(data)
        if month_matches:
            all_matches.extend(month_matches)
            _emit_status(
                status_callback,
                "status",
                f"Found {len(month_matches)} fixture rows for {year_month}.",
                stage="custom_month_feed_rows",
                count=len(month_matches),
                month=year_month,
            )
        else:
            _emit_status(
                status_callback,
                "log",
                f"No fixture rows found in monthly feed {year_month}.",
                stage="custom_month_feed_empty",
                month=year_month,
            )

    return _dedupe_matches(all_matches)


def _get_season_stage_ids(
    sb,
    region_id: int,
    tournament_id: int,
    slug: str,
    season_label: str,
    status_callback=None,
) -> tuple[int, int | None, str]:
    last_source = ""
    last_title = ""

    for slug_candidate in _slug_candidates(slug):
        url = f"https://www.whoscored.com/Regions/{region_id}/Tournaments/{tournament_id}/Show/{slug_candidate}"
        _emit_status(
            status_callback,
            "status",
            f"Opening WhoScored competition page for {slug_candidate}.",
            stage="custom_open_competition",
            url=url,
        )
        sb.open(url)
        time.sleep(random.uniform(3.5, 5.5))
        src = sb.get_page_source()
        page_title = sb.get_title()
        last_source = src
        last_title = page_title or ""

        print(f"[WS DEBUG] show_url={url}")
        print(f"[WS DEBUG] page_title={page_title}")
        print(src[:1500])

        _emit_status(
            status_callback,
            "status",
            f"WhoScored page title: {page_title or 'untitled'}.",
            stage="custom_page_loaded",
            page_title=page_title or "",
        )

        challenge_text = f"{page_title} {src[:3000]}".lower()
        if "just a moment" in challenge_text or "challenges.cloudflare.com" in challenge_text:
            _emit_status(
                status_callback,
                "warning",
                "WhoScored returned a Cloudflare challenge page. The scraper cannot read the season list until the challenge is cleared.",
                stage="cloudflare_challenge",
                page_title=page_title or "",
            )
            raise RuntimeError(
                "WhoScored returned a Cloudflare 'Just a moment' challenge page. "
                "Open the same page in a visible browser, clear the challenge, then retry with headless off if needed."
            )

        pat = re.compile(
            r'<option[^>]+value="(\d+)"[^>]+data-stage-id="(\d+)"[^>]*>\s*'
            + re.escape(season_label) + r'\s*</option>',
            re.IGNORECASE,
        )
        match = pat.search(src)
        if match:
            season_id = int(match.group(1))
            stage_id = int(match.group(2))
            _emit_status(
                status_callback,
                "status",
                f"Resolved WhoScored season {season_label} with season id {season_id} and stage id {stage_id}.",
                stage="custom_season_resolved",
                season_id=season_id,
                stage_id=stage_id,
            )
            return season_id, stage_id, slug_candidate

        for option_match in re.finditer(r'<option[^>]+value="(\d+)"[^>]+data-stage-id="(\d+)"[^>]*>([^<]+)<', src):
            label = option_match.group(3).strip()
            if season_label in label:
                season_id = int(option_match.group(1))
                stage_id = int(option_match.group(2))
                _emit_status(
                    status_callback,
                    "status",
                    f"Resolved season {season_label} from WhoScored fallback option scan.",
                    stage="custom_season_resolved",
                    season_id=season_id,
                    stage_id=stage_id,
                )
                return season_id, stage_id, slug_candidate

        for option_match in re.finditer(r'<option[^>]+value="(\d+)"[^>]*>([^<]+)<', src):
            label = option_match.group(2).strip()
            if season_label in label:
                season_id = int(option_match.group(1))
                _emit_status(
                    status_callback,
                    "status",
                    f"Resolved season {season_label} without a stage id. The scraper will use the season page directly.",
                    stage="custom_season_resolved_no_stage",
                    season_id=season_id,
                )
                return season_id, None, slug_candidate

        for link_match in re.finditer(r'/Regions/%s/Tournaments/%s/Seasons/(\d+)/([^"\'<>\s]+)' % (region_id, tournament_id), src):
            season_id = int(link_match.group(1))
            linked_slug = link_match.group(2).strip().strip("/") or slug_candidate
            around = src[max(0, link_match.start() - 160): link_match.end() + 160]
            if season_label in around:
                _emit_status(
                    status_callback,
                    "status",
                    f"Resolved season {season_label} from a WhoScored season link. The scraper will use the season page directly.",
                    stage="custom_season_link_resolved",
                    season_id=season_id,
                )
                return season_id, None, linked_slug

    available = []
    for option_match in re.finditer(r'<option[^>]+(?:data-stage-id="\d+"[^>]*)?[^>]*>([^<]+)<', last_source):
        label = option_match.group(1).strip()
        if label:
            available.append(label)

    _emit_status(
        status_callback,
        "warning",
        f"Season {season_label} was not found on the WhoScored page. If soccerdata already returned schedule rows, the app will keep those rows and continue without custom stage enrichment.",
        stage="custom_season_missing",
        page_title=last_title,
        available_seasons=", ".join(available[:12]),
    )
    raise ValueError(f"Season '{season_label}' not found on WhoScored for {slug}. Check the season year.")


def _find_matches_in_object(value: Any) -> list[dict]:
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            if any("home" in item or "homeTeam" in item or "matchId" in item or "id" in item for item in value):
                return value
        for item in value:
            found = _find_matches_in_object(item)
            if found:
                return found
        return []

    if isinstance(value, dict):
        for key in ["matches", "matchList", "fixtures", "events"]:
            item = value.get(key)
            if isinstance(item, list):
                found = _find_matches_in_object(item)
                if found:
                    return found
        for item in value.values():
            found = _find_matches_in_object(item)
            if found:
                return found
    return []


def _parse_schedule_from_source(src: str) -> list[dict]:
    patterns = [
        r'var\s+matchdayData\s*=\s*(\{.*?\});\s*\n',
        r'matchdayData\s*=\s*(\{.*?\})\s*;',
        r'matchCentreData\s*=\s*(\{.*?\})\s*;',
        r'fixtureData\s*=\s*(\{.*?\})\s*;',
    ]
    for pattern in patterns:
        match = re.search(pattern, src, re.DOTALL)
        if not match:
            continue
        try:
            data = json.loads(match.group(1))
            found = _find_matches_in_object(data)
            if found:
                return found
        except Exception:
            continue

    next_match = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', src, re.DOTALL)
    if next_match:
        try:
            data = json.loads(next_match.group(1))
            return _find_matches_in_object(data)
        except Exception:
            return []
    return []


def _read_matches_from_page(sb) -> list[dict]:
    raw = sb.execute_script(
        "try { return window.matchdayData || window.matchList || window.fixtureData || window.__NEXT_DATA__ || null; }"
        "catch(e) { return null; }"
    )
    matches = _find_matches_in_object(raw)
    if matches:
        return matches
    return _parse_schedule_from_source(sb.get_page_source())


def _custom_value_is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "nan", "none", "<na>", "nat", "null"}
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    try:
        import numpy as _np

        if isinstance(value, _np.ndarray):
            return value.size == 0
        if isinstance(value, _np.generic):
            value = value.item()
    except Exception:
        pass
    try:
        missing = pd.isna(value)
    except Exception:
        return False
    return bool(missing) if isinstance(missing, bool) else False


def _first_present(*values: Any) -> Any:
    for value in values:
        if not _custom_value_is_empty(value):
            return value
    return ""


def _team_name(team_obj: Any) -> str:
    if isinstance(team_obj, dict):
        return str(_first_present(team_obj.get("name"), team_obj.get("teamName"), team_obj.get("shortName"), team_obj.get("field"), ""))
    return ""


def _team_id(team_obj: Any) -> Any:
    if isinstance(team_obj, dict):
        return _first_present(team_obj.get("teamId"), team_obj.get("id"), team_obj.get("team_id"), None)
    return None


def _build_dataframe(matches: list[dict]) -> pd.DataFrame:
    rows = []
    for item in matches:
        home = item.get("home") or item.get("homeTeam") or {}
        away = item.get("away") or item.get("awayTeam") or {}
        score = item.get("score") or {}
        rows.append(
            {
                "game_id": _first_present(item.get("id"), item.get("matchId"), item.get("gameId")),
                "date": _first_present(item.get("startTime"), item.get("startDate"), item.get("date"), item.get("time")),
                "home_team": _first_present(_team_name(home), item.get("homeTeamName"), item.get("homeName"), ""),
                "away_team": _first_present(_team_name(away), item.get("awayTeamName"), item.get("awayName"), ""),
                "home_team_id": _first_present(_team_id(home), item.get("homeTeamId")),
                "away_team_id": _first_present(_team_id(away), item.get("awayTeamId")),
                "home_score": score.get("home") if isinstance(score, dict) else item.get("homeScore"),
                "away_score": score.get("away") if isinstance(score, dict) else item.get("awayScore"),
                "status": _first_present(item.get("statusCode"), item.get("status")),
                "elapsed": _first_present(item.get("minuteOfEntry"), item.get("timeElapsed")),
                "stage_id": _first_present(item.get("__ws_stage_id"), item.get("stage_id"), item.get("stageId")),
                "stage_name": _first_present(item.get("__ws_stage_name"), item.get("stage_name"), item.get("stageName"), ""),
                "competition_phase": _first_present(item.get("__competition_phase"), item.get("competition_phase"), ""),
                "is_playoff": bool(_first_present(item.get("__is_playoff"), item.get("is_playoff"), False)),
            }
        )
    return pd.DataFrame(rows)


def _annotate_stage_matches(matches: list[dict], stage: dict[str, Any]) -> list[dict]:
    stage_id = _safe_int(stage.get("stage_id"))
    out: list[dict] = []
    for item in matches:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["__ws_stage_id"] = stage_id
        row["__ws_stage_name"] = str(stage.get("stage_name", "") or "")
        row["__competition_phase"] = str(stage.get("competition_phase", "") or "")
        row["__is_playoff"] = bool(stage.get("is_playoff"))
        out.append(row)
    return out


def _fixture_urls_for_stage(
    region_id: int,
    tournament_id: int,
    season_id: int,
    stage_id: int | None,
    resolved_slug: str,
) -> list[str]:
    urls: list[str] = []
    for slug_candidate in _slug_candidates(resolved_slug):
        if stage_id is not None:
            urls.append(_build_stage_fixtures_url(region_id, tournament_id, season_id, int(stage_id), slug_candidate))
            urls.append(_build_stage_show_url(region_id, tournament_id, season_id, int(stage_id), slug_candidate))
        urls.append(_build_season_url(region_id, tournament_id, season_id, slug_candidate))
        urls.append(_build_season_fixtures_url(region_id, tournament_id, season_id, slug_candidate))
    return list(dict.fromkeys(urls))


def load_schedule_custom(
    league: str,
    season: str,
    headless: bool,
    browserpath: str | None = None,
    status_callback=None,
) -> pd.DataFrame:
    alias_map = {
        "TUR-Super Lig": "TUR-Süper Lig",
        "BRA-Serie A": "BRA-Série A",
        "ARG-Primera División": "ARG-Liga Profesional",
        "POR-Liga NOS": "POR-Liga Portugal",
        "ESP-Segunda División": "ESP-Segunda Division",
    }
    league = alias_map.get(str(league or ""), str(league or ""))

    if league not in LEAGUE_PRESETS:
        raise ValueError(f"'{league}' not in custom league map.")

    meta = LEAGUE_PRESETS[league]
    region_id = int(meta["region_id"])
    tournament_id = int(meta["tournament_id"])
    slug = str(meta["slug"])
    season_label = _season_label(season, str(meta.get("season_mode", "split")))
    _emit_status(
        status_callback,
        "status",
        f"Using custom WhoScored fallback for {league} {season_label}.",
        stage="custom_start",
        league=league,
        season_label=season_label,
    )

    with setup_driver(headless=headless, browserpath=browserpath) as sb:
        override = _season_override(meta, str(season), season_label)
        override_slugs = override.get("slugs", []) if isinstance(override, dict) else []
        if isinstance(override_slugs, str):
            override_slugs = [override_slugs]

        override_season_id = _safe_int(override.get("season_id")) if isinstance(override, dict) else None
        override_stage_id = _safe_int(override.get("stage_id")) if isinstance(override, dict) else None

        if override_season_id is not None:
            resolved_slug = _slug_candidates(str(override_slugs[0] if override_slugs else slug))[0]
            season_id = override_season_id
            stage_id = override_stage_id
            _emit_status(
                status_callback,
                "status",
                f"Using fixed WhoScored season id {season_id} for {league} {season_label}.",
                stage="custom_season_override",
                season_id=season_id,
                stage_id=stage_id,
            )
        else:
            season_id, stage_id, resolved_slug = _get_season_stage_ids(
                sb,
                region_id,
                tournament_id,
                slug,
                season_label,
                status_callback=status_callback,
            )

        stage_candidates = _discover_stage_candidates(
            sb,
            region_id=region_id,
            tournament_id=tournament_id,
            season_id=season_id,
            primary_stage_id=stage_id,
            resolved_slug=resolved_slug,
            season_label=season_label,
            override=override,
            status_callback=status_callback,
        )
        if not stage_candidates:
            stage_candidates = [
                {
                    "stage_id": None,
                    "stage_name": "Season fixtures",
                    "competition_phase": "League phase",
                    "is_playoff": False,
                    "source": "season_page",
                }
            ]

        all_matches: list[dict] = []
        empty_stages: list[dict[str, Any]] = []
        fixture_urls: list[str] = []

        for stage in stage_candidates:
            candidate_stage_id = _safe_int(stage.get("stage_id"))
            if candidate_stage_id is None:
                continue

            stage_urls = _fixture_urls_for_stage(
                region_id=region_id,
                tournament_id=tournament_id,
                season_id=season_id,
                stage_id=candidate_stage_id,
                resolved_slug=resolved_slug,
            )
            fixture_urls.extend(stage_urls)

            stage_label = str(stage.get("stage_name") or stage.get("competition_phase") or candidate_stage_id)
            _emit_status(
                status_callback,
                "status",
                f"Reading WhoScored fixtures for stage {candidate_stage_id} ({stage_label}).",
                stage="custom_stage_read_start",
                stage_id=candidate_stage_id,
                stage_name=stage_label,
                competition_phase=str(stage.get("competition_phase", "")),
                is_playoff=bool(stage.get("is_playoff")),
            )

            stage_matches = _read_matches_from_month_feed(
                sb,
                region_id=region_id,
                tournament_id=tournament_id,
                season_id=season_id,
                stage_id=candidate_stage_id,
                league=league,
                season_label=season_label,
                season_mode=str(meta.get("season_mode", "split")),
                status_callback=status_callback,
            )

            if stage_matches:
                annotated = _annotate_stage_matches(stage_matches, stage)
                all_matches.extend(annotated)
                _emit_status(
                    status_callback,
                    "status",
                    f"WhoScored monthly feed returned {len(stage_matches)} fixture rows for stage {candidate_stage_id}.",
                    stage="custom_stage_month_feed_complete",
                    count=len(stage_matches),
                    stage_id=candidate_stage_id,
                    competition_phase=str(stage.get("competition_phase", "")),
                )
            else:
                empty_stages.append(stage)

        fixture_urls = list(dict.fromkeys(fixture_urls))

        print(f"[WS DEBUG] league={league}")
        print(f"[WS DEBUG] region_id={region_id}, tournament_id={tournament_id}, slug={resolved_slug}")
        print(f"[WS DEBUG] season_label={season_label}")
        print(f"[WS DEBUG] stages={[item.get('stage_id') for item in stage_candidates]}")
        print(f"[WS DEBUG] fixture_urls={fixture_urls}")

        tried_urls: list[str] = []
        fallback_stages = empty_stages if empty_stages else ([] if all_matches else stage_candidates)

        if fallback_stages:
            _emit_status(
                status_callback,
                "warning" if all_matches else "status",
                "Trying rendered fixture pages for stages that were not fully covered by the monthly feed.",
                stage="custom_rendered_fixture_fallback_start",
                stage_ids=", ".join(str(item.get("stage_id")) for item in fallback_stages),
            )

        for stage in fallback_stages:
            candidate_stage_id = _safe_int(stage.get("stage_id"))
            for fixtures_url in _fixture_urls_for_stage(
                region_id=region_id,
                tournament_id=tournament_id,
                season_id=season_id,
                stage_id=candidate_stage_id,
                resolved_slug=resolved_slug,
            ):
                if fixtures_url in tried_urls:
                    continue

                tried_urls.append(fixtures_url)
                _emit_status(
                    status_callback,
                    "status",
                    f"Opening fixtures page for {league} {season_label}.",
                    stage="custom_open_fixtures",
                    url=fixtures_url,
                    stage_id=candidate_stage_id,
                )
                sb.open(fixtures_url)
                time.sleep(random.uniform(4, 6))

                page_title = sb.get_title() or ""
                page_source = sb.get_page_source() or ""
                if _is_challenge_source(page_title, page_source):
                    _emit_status(
                        status_callback,
                        "warning",
                        "WhoScored returned a Cloudflare challenge page while opening fixtures.",
                        stage="cloudflare_challenge",
                        page_title=page_title,
                        url=fixtures_url,
                    )
                    raise RuntimeError(
                        "WhoScored returned a Cloudflare 'Just a moment' challenge page. "
                        "Open the same page in a visible browser, clear the challenge, then retry with headless off if needed."
                    )

                _emit_status(
                    status_callback,
                    "status",
                    "Reading embedded fixture data from the WhoScored page.",
                    stage="custom_read_fixtures",
                    url=fixtures_url,
                    stage_id=candidate_stage_id,
                )
                fallback_matches = _read_matches_from_page(sb)
                if fallback_matches:
                    all_matches.extend(_annotate_stage_matches(fallback_matches, stage))
                    _emit_status(
                        status_callback,
                        "status",
                        f"Fixture data found using {fixtures_url}.",
                        stage="custom_fixture_url_resolved",
                        url=fixtures_url,
                        count=len(fallback_matches),
                        stage_id=candidate_stage_id,
                    )
                    break

        matches = _dedupe_matches(all_matches)

        if not matches:
            _emit_status(
                status_callback,
                "error",
                f"No fixtures were extracted from WhoScored for {league} {season_label}.",
                stage="custom_no_fixtures",
                tried_urls=" | ".join(tried_urls[:8]),
            )
            raise RuntimeError(f"No fixtures extracted from WhoScored page for {league} {season_label}")

        df = _build_dataframe(matches)
        df["league"] = league
        df["folder_nation"] = str(meta.get("nation", ""))
        df["folder_tier"] = str(meta.get("tier", ""))

        if "stage_id" in df.columns:
            df["stage_id"] = pd.to_numeric(df["stage_id"], errors="coerce")
        if "is_playoff" in df.columns:
            df["is_playoff"] = df["is_playoff"].fillna(False).astype(bool)

        playoff_count = int(df["is_playoff"].sum()) if "is_playoff" in df.columns else 0
        _emit_status(
            status_callback,
            "status",
            f"Custom scraper extracted {len(df)} fixture rows across {len(stage_candidates)} WhoScored stage(s).",
            stage="custom_complete",
            count=int(len(df)),
            playoff_count=playoff_count,
            stage_ids=", ".join(str(item.get("stage_id")) for item in stage_candidates),
        )
        return df