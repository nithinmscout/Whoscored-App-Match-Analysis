from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.metrics.expected_goals import score_shots_with_models
from app.metrics.expected_threat import build_xt_model, value_actions
from app.services.event_data_service import load_schedule_frame, load_season_events
from app.services.viewer_service import _event_masks, _home_away_cols, _match_id_col, _norm_team_name

LEAGUE_ANALYSIS_CACHE_VERSION = "league_style_analysis_v1"
MAX_LEAGUE_ANALYSIS_CACHE_ITEMS = 12

LeagueAnalysisCacheKey = tuple[str, str, str, str, str, int, tuple[tuple[str, float, int, int], ...]]
_LEAGUE_ANALYSIS_CACHE: dict[LeagueAnalysisCacheKey, dict[str, Any]] = {}

ALLOWED_CORRELATION_METHODS = {"pearson", "spearman", "kendall"}

METRIC_DEFINITIONS: list[dict[str, Any]] = [
    {
        "key": "possession_proxy",
        "label": "Possession proxy",
        "phase": "Control",
        "description": "Share of event volume within covered matches. This is a proxy, not official possession.",
    },
    {"key": "events_per_match", "label": "Tempo", "phase": "Control", "description": "Total event involvement per covered match."},
    {"key": "passes_per_match", "label": "Pass volume", "phase": "Build up", "description": "Pass actions per covered match."},
    {"key": "pass_success_pct", "label": "Pass security", "phase": "Build up", "description": "Successful pass share."},
    {"key": "progressive_actions_per_match", "label": "Progression", "phase": "Progression", "description": "Final third entries and box entries per match."},
    {"key": "final_third_entries_per_match", "label": "Final third access", "phase": "Progression", "description": "Successful entries into the final third per match."},
    {"key": "box_entries_per_match", "label": "Box access", "phase": "Chance creation", "description": "Successful entries into the penalty area per match."},
    {"key": "directness", "label": "Directness", "phase": "Progression", "description": "Average forward distance gained on successful passes and carries."},
    {"key": "shots_per_match", "label": "Shot volume", "phase": "Chance creation", "description": "Shot events per match."},
    {"key": "xg_per_match", "label": "xG volume", "phase": "Chance creation", "description": "Modelled expected goals per match from saved event rows."},
    {"key": "xg_per_shot", "label": "Shot quality", "phase": "Chance creation", "description": "Expected goals divided by shots."},
    {"key": "xt_per_match", "label": "xT progression", "phase": "Progression", "description": "Positive expected threat added per match."},
    {"key": "defensive_actions_per_match", "label": "Defensive activity", "phase": "Out of possession", "description": "Defensive actions per match."},
    {"key": "high_regains_per_match", "label": "High regains", "phase": "Pressing", "description": "Defensive actions in advanced territory per match."},
    {"key": "defensive_height", "label": "Defensive height", "phase": "Pressing", "description": "Average x location of defensive actions."},
    {"key": "wide_action_share", "label": "Wide usage", "phase": "Attacking width", "description": "Share of movement actions in wide lanes."},
    {"key": "crosses_per_match", "label": "Crossing volume", "phase": "Attacking width", "description": "Crosses per match."},
    {"key": "set_piece_share", "label": "Set piece reliance", "phase": "Set pieces", "description": "Share of actions coming from set pieces."},
    {"key": "set_piece_shots_per_match", "label": "Set piece threat", "phase": "Set pieces", "description": "Set piece shots per match."},
]

STYLE_DIMENSIONS: list[dict[str, Any]] = [
    {
        "key": "control_score",
        "label": "Control and tempo",
        "metrics": ["possession_proxy", "passes_per_match", "pass_success_pct", "events_per_match"],
    },
    {
        "key": "progression_score",
        "label": "Progression and territory",
        "metrics": ["final_third_entries_per_match", "box_entries_per_match", "xt_per_match", "progressive_actions_per_match"],
    },
    {
        "key": "chance_creation_score",
        "label": "Chance creation",
        "metrics": ["shots_per_match", "xg_per_match", "xg_per_shot", "box_entries_per_match"],
    },
    {
        "key": "pressing_score",
        "label": "Pressing and regain height",
        "metrics": ["high_regains_per_match", "defensive_height", "defensive_actions_per_match"],
    },
    {
        "key": "width_score",
        "label": "Width and crossing",
        "metrics": ["wide_action_share", "crosses_per_match"],
    },
    {
        "key": "set_piece_score",
        "label": "Set piece influence",
        "metrics": ["set_piece_share", "set_piece_shots_per_match"],
    },
]


def _safe_slug(value: str) -> str:
    import unicodedata

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


def _event_scope_roots(events_root: Path, nation: str, tier: str) -> list[Path]:
    roots: list[Path] = []
    scoped_root = events_root / _safe_slug(nation)
    if str(tier or "").strip():
        scoped_root = scoped_root / _safe_slug(tier)
    roots.append(scoped_root)

    legacy_root = events_root / _safe_slug(nation)
    if legacy_root != scoped_root:
        roots.append(legacy_root)

    flat_root = events_root / f"{_safe_slug(nation)} {_safe_slug(tier)}".strip()
    if flat_root not in roots:
        roots.append(flat_root)

    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def _source_paths(basedir: Path, nation: str, tier: str, season: str) -> list[Path]:
    events_root = _events_root(basedir)
    season_file = f"{_safe_slug(season)}.csv"
    paths: list[Path] = []
    seen: set[str] = set()
    for root in _event_scope_roots(events_root, nation, tier):
        if not root.exists():
            continue
        direct = root / season_file
        if direct.exists() and direct.is_file():
            key = str(direct.resolve())
            if key not in seen:
                seen.add(key)
                paths.append(direct)
        for team_dir in sorted(item for item in root.iterdir() if item.is_dir()):
            if team_dir.name.startswith("_") or team_dir.name.upper() in {"T1", "T2", "T3", "T4", "T5"}:
                continue
            candidate = team_dir / season_file
            if candidate.exists() and candidate.is_file():
                key = str(candidate.resolve())
                if key not in seen:
                    seen.add(key)
                    paths.append(candidate)
    schedule = basedir / "data" / "Schedule" / f"{nation} {tier}".strip() / season_file
    if schedule.exists() and schedule.is_file():
        paths.append(schedule)
    return paths


def _source_signature(paths: list[Path]) -> tuple[tuple[str, float, int, int], ...]:
    rows: list[tuple[str, float, int, int]] = []
    for path in sorted(paths, key=lambda item: str(item).lower()):
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append((str(path.resolve()), float(stat.st_mtime), int(stat.st_mtime_ns), int(stat.st_size)))
    return tuple(rows)


def _cache_key(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    method: str,
    min_matches: int,
) -> LeagueAnalysisCacheKey:
    return (
        LEAGUE_ANALYSIS_CACHE_VERSION,
        str(nation),
        str(tier),
        str(season),
        str(method),
        int(min_matches),
        _source_signature(_source_paths(basedir, nation, tier, season)),
    )


def _remember(cache_key: LeagueAnalysisCacheKey, payload: dict[str, Any]) -> None:
    import copy

    if cache_key in _LEAGUE_ANALYSIS_CACHE:
        _LEAGUE_ANALYSIS_CACHE.pop(cache_key, None)
    _LEAGUE_ANALYSIS_CACHE[cache_key] = copy.deepcopy(payload)
    while len(_LEAGUE_ANALYSIS_CACHE) > MAX_LEAGUE_ANALYSIS_CACHE_ITEMS:
        oldest = next(iter(_LEAGUE_ANALYSIS_CACHE))
        _LEAGUE_ANALYSIS_CACHE.pop(oldest, None)


def _cached(cache_key: LeagueAnalysisCacheKey) -> dict[str, Any] | None:
    import copy

    item = _LEAGUE_ANALYSIS_CACHE.get(cache_key)
    return copy.deepcopy(item) if item is not None else None


def _safe_float(value: object, fallback: float = 0.0) -> float:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except Exception:
        return fallback
    return numeric if math.isfinite(numeric) else fallback


def _round(value: object, digits: int = 3) -> float:
    return round(_safe_float(value), digits)


def _pct(value: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round((float(value) / float(denominator)) * 100.0, 2)


def _available_metric_keys(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    keys: list[str] = []
    for item in METRIC_DEFINITIONS:
        key = str(item["key"])
        if key not in frame.columns:
            continue
        values = pd.to_numeric(frame[key], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if len(values) >= 3 and values.nunique() >= 2:
            keys.append(key)
    return keys


def _z_scores(frame: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    z = pd.DataFrame(index=frame.index)
    for key in keys:
        values = pd.to_numeric(frame[key], errors="coerce").replace([np.inf, -np.inf], np.nan)
        mean = values.mean()
        std = values.std(ddof=0)
        if pd.isna(std) or std <= 0:
            z[key] = 0.0
        else:
            z[key] = (values - mean) / std
    return z.fillna(0.0)


def _style_dimension_scores(frame: pd.DataFrame, keys: list[str]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    out = frame.copy()
    z = _z_scores(out, keys)
    dimensions: list[dict[str, Any]] = []
    for item in STYLE_DIMENSIONS:
        metric_keys = [key for key in item["metrics"] if key in z.columns]
        if not metric_keys:
            out[item["key"]] = 50.0
            dimensions.append({**item, "metrics": [], "league_average": 50.0})
            continue
        raw = z[metric_keys].mean(axis=1)
        score = (50.0 + (raw * 16.0)).clip(0.0, 100.0)
        out[item["key"]] = score.round(2)
        dimensions.append(
            {
                "key": item["key"],
                "label": item["label"],
                "metrics": metric_keys,
                "league_average": round(float(score.mean()), 2) if len(score) else 50.0,
                "top_teams": [
                    {"team": str(row["team"]), "score": _round(row[item["key"]], 2)}
                    for _, row in out[["team", item["key"]]].sort_values(item["key"], ascending=False).head(5).iterrows()
                ],
            }
        )
    return out, dimensions


def _team_tag(row: pd.Series, league_means: pd.Series, league_stds: pd.Series) -> list[str]:
    tags: list[str] = []

    def high(key: str, multiplier: float = 0.6) -> bool:
        std = _safe_float(league_stds.get(key), 0.0)
        if std <= 0:
            return False
        return _safe_float(row.get(key)) >= _safe_float(league_means.get(key)) + (std * multiplier)

    if high("possession_proxy") and high("passes_per_match", 0.35):
        tags.append("possession and circulation heavy")
    if high("directness") or high("box_entries_per_match"):
        tags.append("vertical territory focused")
    if high("high_regains_per_match") and high("defensive_height", 0.35):
        tags.append("high press and regain focused")
    if high("wide_action_share") or high("crosses_per_match"):
        tags.append("wide and crossing orientated")
    if high("set_piece_share") or high("set_piece_shots_per_match"):
        tags.append("set piece leaning")
    if high("shots_per_match") and high("xg_per_match", 0.35):
        tags.append("high shot and chance volume")
    return tags[:4] if tags else ["balanced profile"]


def _style_findings(frame: pd.DataFrame, dimensions: list[dict[str, Any]]) -> list[str]:
    findings: list[str] = []
    if frame.empty:
        return findings

    for dimension in dimensions:
        key = str(dimension.get("key"))
        label = str(dimension.get("label"))
        if key not in frame.columns:
            continue
        leaders = frame[["team", key]].sort_values(key, ascending=False).head(3)
        if leaders.empty:
            continue
        leader_text = ", ".join(f"{row.team} ({_round(getattr(row, key), 1)})" for row in leaders.itertuples(index=False))
        findings.append(f"{label}: strongest teams are {leader_text}.")

    numeric = frame.select_dtypes(include=["number"])
    if "directness" in numeric.columns and "possession_proxy" in numeric.columns and len(frame) >= 4:
        corr = numeric[["directness", "possession_proxy"]].corr().iloc[0, 1]
        if math.isfinite(float(corr)):
            if corr <= -0.35:
                findings.append("The league shows a possession versus directness trade off: teams with more event control are generally less direct.")
            elif corr >= 0.35:
                findings.append("The league allows controlled possession sides to progress directly, rather than forcing a possession versus directness split.")

    return findings[:8]


def _score_models(df: pd.DataFrame) -> tuple[dict[str, float], dict[str, float], dict[str, Any]]:
    xg_by_team: dict[str, float] = {}
    xt_by_team: dict[str, float] = {}
    quality: dict[str, Any] = {
        "xg": {"status": "not_run", "note": "xG model was not run."},
        "xt": {"status": "not_run", "note": "xT model was not run."},
    }

    try:
        scored_shots, model = score_shots_with_models(df)
        if not scored_shots.empty and "team" in scored_shots.columns and "xg" in scored_shots.columns:
            grouped = scored_shots.groupby(scored_shots["team"].astype(str))["xg"].sum()
            xg_by_team = {str(team): round(float(value), 4) for team, value in grouped.items() if math.isfinite(float(value))}
        quality["xg"] = {
            "status": "trained" if getattr(model, "shots_used", 0) else "limited",
            "shots_used": int(getattr(model, "shots_used", 0)),
            "goals_seen": int(getattr(model, "goals_seen", 0)),
            "note": "xG scored from saved local event rows.",
        }
    except Exception as exc:
        quality["xg"] = {"status": "fallback", "note": f"xG scoring failed safely: {type(exc).__name__}: {exc}"}

    try:
        model = build_xt_model(df, include_set_pieces=False)
        valued = value_actions(df, model, include_set_pieces=True)
        if not valued.empty and "team" in valued.columns and "xt_added" in valued.columns:
            valued = valued.copy()
            valued["positive_xt"] = pd.to_numeric(valued["xt_added"], errors="coerce").fillna(0.0).clip(lower=0.0)
            grouped = valued.groupby(valued["team"].astype(str))["positive_xt"].sum()
            xt_by_team = {str(team): round(float(value), 4) for team, value in grouped.items() if math.isfinite(float(value))}
        quality["xt"] = {"status": "trained", "note": "xT scored from saved local event rows."}
    except Exception as exc:
        quality["xt"] = {"status": "fallback", "note": f"xT scoring failed safely: {type(exc).__name__}: {exc}"}

    return xg_by_team, xt_by_team, quality


def _schedule_overview(basedir: Path, nation: str, tier: str, season: str) -> dict[str, Any]:
    try:
        schedule = load_schedule_frame(basedir, nation, tier, season)
    except Exception as exc:
        return {"available": False, "matches": 0, "teams": [], "note": f"Schedule unavailable: {type(exc).__name__}: {exc}"}

    home_col, away_col = _home_away_cols(schedule)
    match_col = _match_id_col(schedule)
    teams: set[str] = set()
    if home_col:
        teams.update(str(item).strip() for item in schedule[home_col].dropna().tolist() if str(item).strip())
    if away_col:
        teams.update(str(item).strip() for item in schedule[away_col].dropna().tolist() if str(item).strip())

    match_count = int(pd.to_numeric(schedule[match_col], errors="coerce").nunique()) if match_col else int(len(schedule))
    return {
        "available": True,
        "matches": match_count,
        "teams": sorted(teams),
        "columns": schedule.columns.tolist(),
        "note": "Schedule loaded for league context.",
    }


def _cluster_label(centroid: pd.Series) -> str:
    checks = [
        ("pressing_score", "High press league profile"),
        ("control_score", "Control and circulation profile"),
        ("progression_score", "Territory progression profile"),
        ("chance_creation_score", "Chance creation profile"),
        ("width_score", "Wide attack profile"),
        ("set_piece_score", "Set piece leaning profile"),
    ]
    key, label = max(checks, key=lambda item: _safe_float(centroid.get(item[0])))
    return label if _safe_float(centroid.get(key)) >= 52.0 else "Balanced profile"


def _build_pca_and_clusters(frame: pd.DataFrame, keys: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    pca_payload: dict[str, Any] = {"available": False, "note": "PCA needs at least three teams and three variable metrics."}
    cluster_payload: dict[str, Any] = {"available": False, "note": "Clustering needs at least four teams and three variable metrics."}

    if len(frame) < 3 or len(keys) < 3:
        return pca_payload, cluster_payload

    values = frame[keys].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    values = values.fillna(values.median(numeric_only=True)).fillna(0.0)

    try:
        from sklearn.decomposition import PCA
        from sklearn.preprocessing import StandardScaler

        scaled = StandardScaler().fit_transform(values)
        pca = PCA(n_components=2, random_state=7)
        scores = pca.fit_transform(scaled)
        pca_payload = {
            "available": True,
            "explained_variance_pct": [round(float(item) * 100.0, 2) for item in pca.explained_variance_ratio_.tolist()],
            "team_scores": [
                {
                    "team": str(team),
                    "pc1": round(float(scores[index, 0]), 4),
                    "pc2": round(float(scores[index, 1]), 4),
                }
                for index, team in enumerate(frame["team"].astype(str).tolist())
            ],
            "loadings": [
                {
                    "metric": key,
                    "label": next((str(item["label"]) for item in METRIC_DEFINITIONS if item["key"] == key), key),
                    "pc1": round(float(pca.components_[0, col_index]), 4),
                    "pc2": round(float(pca.components_[1, col_index]), 4),
                }
                for col_index, key in enumerate(keys)
            ],
            "note": "PCA reduces the league style variables into two axes so outlier teams are easier to spot.",
        }
    except Exception as exc:
        pca_payload = {"available": False, "note": f"PCA failed safely: {type(exc).__name__}: {exc}"}

    if len(frame) < 4:
        return pca_payload, cluster_payload

    try:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler

        cluster_keys = [key for key in ["control_score", "progression_score", "chance_creation_score", "pressing_score", "width_score", "set_piece_score"] if key in frame.columns]
        if len(cluster_keys) < 3:
            cluster_keys = keys[: min(len(keys), 8)]
        cluster_values = frame[cluster_keys].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
        scaled = StandardScaler().fit_transform(cluster_values)
        cluster_count = min(4, max(2, len(frame) // 3))
        labels = KMeans(n_clusters=cluster_count, n_init=12, random_state=7).fit_predict(scaled)
        work = frame[["team"] + cluster_keys].copy()
        work["cluster"] = labels

        clusters: list[dict[str, Any]] = []
        for cluster_id, group in work.groupby("cluster"):
            centroid = group[cluster_keys].mean(numeric_only=True)
            clusters.append(
                {
                    "cluster": int(cluster_id),
                    "label": _cluster_label(centroid),
                    "teams": sorted(group["team"].astype(str).tolist()),
                    "centroid": {key: _round(centroid.get(key), 2) for key in cluster_keys},
                }
            )
        cluster_payload = {
            "available": True,
            "method": "kmeans",
            "cluster_count": int(cluster_count),
            "metrics": cluster_keys,
            "clusters": sorted(clusters, key=lambda item: item["cluster"]),
            "note": "Clusters group teams by statistical similarity across the selected style variables.",
        }
    except Exception as exc:
        cluster_payload = {"available": False, "note": f"Clustering failed safely: {type(exc).__name__}: {exc}"}

    return pca_payload, cluster_payload


def _build_correlation(frame: pd.DataFrame, keys: list[str], method: str) -> dict[str, Any]:
    if len(frame) < 3 or len(keys) < 2:
        return {"available": False, "method": method, "note": "Correlation needs at least three teams and two variable metrics."}

    corr = frame[keys].apply(pd.to_numeric, errors="coerce").corr(method=method).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    label_lookup = {str(item["key"]): str(item["label"]) for item in METRIC_DEFINITIONS}
    matrix: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []

    for x_key in keys:
        for y_key in keys:
            value = round(float(corr.loc[y_key, x_key]), 4)
            matrix.append(
                {
                    "x": x_key,
                    "y": y_key,
                    "x_label": label_lookup.get(x_key, x_key),
                    "y_label": label_lookup.get(y_key, y_key),
                    "value": value,
                }
            )

    for i, x_key in enumerate(keys):
        for y_key in keys[i + 1:]:
            value = float(corr.loc[y_key, x_key])
            if not math.isfinite(value):
                continue
            pairs.append(
                {
                    "x": x_key,
                    "y": y_key,
                    "x_label": label_lookup.get(x_key, x_key),
                    "y_label": label_lookup.get(y_key, y_key),
                    "value": round(value, 4),
                    "strength": "strong" if abs(value) >= 0.7 else "moderate" if abs(value) >= 0.45 else "weak",
                }
            )

    strongest_positive = sorted([item for item in pairs if item["value"] > 0], key=lambda item: item["value"], reverse=True)[:8]
    strongest_negative = sorted([item for item in pairs if item["value"] < 0], key=lambda item: item["value"])[:8]

    return {
        "available": True,
        "method": method,
        "metrics": [{**item, "enabled": item["key"] in keys} for item in METRIC_DEFINITIONS],
        "matrix": matrix,
        "strongest_positive": strongest_positive,
        "strongest_negative": strongest_negative,
        "note": "Correlations are computed across team season profiles, so they describe league style relationships rather than individual match causation.",
    }


def _build_outliers(frame: pd.DataFrame, keys: list[str]) -> list[dict[str, Any]]:
    if frame.empty or not keys:
        return []
    z = _z_scores(frame, keys)
    rows: list[dict[str, Any]] = []
    label_lookup = {str(item["key"]): str(item["label"]) for item in METRIC_DEFINITIONS}
    for row_index, team in enumerate(frame["team"].astype(str).tolist()):
        for key in keys:
            value = float(z.iloc[row_index][key])
            if abs(value) < 1.8:
                continue
            metric_values = pd.to_numeric(frame[key], errors="coerce").replace([np.inf, -np.inf], np.nan)
            rows.append(
                {
                    "team": team,
                    "metric": key,
                    "label": label_lookup.get(key, key),
                    "value": _round(frame.iloc[row_index][key], 3),
                    "z_score": round(value, 2),
                    "league_average": _round(metric_values.mean(), 3),
                    "direction": "above league norm" if value > 0 else "below league norm",
                }
            )
    return sorted(rows, key=lambda item: abs(float(item["z_score"])), reverse=True)[:30]


def _build_team_rows(df: pd.DataFrame, min_matches: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if df.empty:
        return [], {"xg": {"status": "limited"}, "xt": {"status": "limited"}}

    df = df.copy()
    df["team"] = df.get("team", pd.Series([""] * len(df), index=df.index)).astype(str).str.strip()
    df = df.loc[df["team"].ne("")].copy()
    if df.empty:
        return [], {"xg": {"status": "limited", "note": "No team names were available in event rows."}}

    masks = _event_masks(df)
    match_col = _match_id_col(df)
    xg_by_team, xt_by_team, quality = _score_models(df)

    if match_col:
        all_match_ids = pd.to_numeric(df[match_col], errors="coerce")
    else:
        all_match_ids = pd.Series([1] * len(df), index=df.index)

    rows: list[dict[str, Any]] = []
    for team, team_df in df.groupby("team", dropna=False):
        team_name = str(team).strip()
        if not team_name:
            continue
        idx = team_df.index
        team_match_ids = pd.to_numeric(team_df[match_col], errors="coerce").dropna().astype(int).unique().tolist() if match_col else [1]
        match_count = max(len(team_match_ids), 1)
        if match_count < int(min_matches):
            continue

        if match_col and team_match_ids:
            match_scope_mask = all_match_ids.isin(team_match_ids)
            match_scope_rows = int(match_scope_mask.sum())
        else:
            match_scope_rows = int(len(df))

        team_rows = int(len(team_df))
        is_pass = masks["is_pass"].loc[idx]
        is_carry = masks["is_carry"].loc[idx]
        is_move = masks["is_move"].loc[idx]
        successful = masks["successful"].loc[idx]
        is_shot = masks["is_shot"].loc[idx]
        is_goal = masks["is_goal"].loc[idx]
        is_defensive = masks["is_defensive"].loc[idx]
        is_set_piece = masks["is_set_piece"].loc[idx]
        is_cross = masks["is_cross"].loc[idx]
        final_third_entry = masks["final_third_entry"].loc[idx]
        box_entry = masks["box_entry"].loc[idx]
        high_regain = masks["high_regain"].loc[idx]
        wide_action = masks["wide_action"].loc[idx]
        central_action = masks["central_action"].loc[idx]
        x = masks["x"].loc[idx]
        end_x = masks["end_x"].loc[idx]

        move_success = is_move & successful
        forward_gain = (end_x - x).where(move_success, np.nan)
        defensive_x = x.where(is_defensive, np.nan)
        passes = int(is_pass.sum())
        shots = int(is_shot.sum())
        set_piece_shots = int((is_set_piece & is_shot).sum())
        progressive_actions = int((final_third_entry | box_entry).sum())
        xt_total = _safe_float(xt_by_team.get(team_name), 0.0)
        xg_total = _safe_float(xg_by_team.get(team_name), 0.0)

        rows.append(
            {
                "team": team_name,
                "matches": int(match_count),
                "rows": int(team_rows),
                "match_ids": team_match_ids[:80],
                "possession_proxy": _pct(team_rows, max(match_scope_rows, 1)),
                "events_per_match": _round(team_rows / match_count, 2),
                "passes_per_match": _round(passes / match_count, 2),
                "pass_success_pct": _pct(int((is_pass & successful).sum()), max(passes, 1)),
                "carries_per_match": _round(int(is_carry.sum()) / match_count, 2),
                "crosses_per_match": _round(int(is_cross.sum()) / match_count, 2),
                "shots_per_match": _round(shots / match_count, 2),
                "goals_per_match": _round(int(is_goal.sum()) / match_count, 2),
                "xg": _round(xg_total, 3),
                "xg_per_match": _round(xg_total / match_count, 3),
                "xg_per_shot": _round(xg_total / max(shots, 1), 3),
                "xt": _round(xt_total, 3),
                "xt_per_match": _round(xt_total / match_count, 3),
                "final_third_entries_per_match": _round(int(final_third_entry.sum()) / match_count, 2),
                "box_entries_per_match": _round(int(box_entry.sum()) / match_count, 2),
                "progressive_actions_per_match": _round(progressive_actions / match_count, 2),
                "directness": _round(forward_gain.mean(skipna=True), 3),
                "defensive_actions_per_match": _round(int(is_defensive.sum()) / match_count, 2),
                "high_regains_per_match": _round(int(high_regain.sum()) / match_count, 2),
                "defensive_height": _round(defensive_x.mean(skipna=True), 2),
                "wide_action_share": _pct(int(wide_action.sum()), max(int(is_move.sum()), 1)),
                "central_action_share": _pct(int(central_action.sum()), max(int(is_move.sum()), 1)),
                "set_piece_share": _pct(int(is_set_piece.sum()), max(team_rows, 1)),
                "set_piece_shots_per_match": _round(set_piece_shots / match_count, 2),
                "corners_per_match": _round(int(masks["is_corner"].loc[idx].sum()) / match_count, 2),
                "free_kicks_per_match": _round(int(masks["is_free_kick"].loc[idx].sum()) / match_count, 2),
                "throw_ins_per_match": _round(int(masks["is_throw_in"].loc[idx].sum()) / match_count, 2),
            }
        )

    return rows, quality


def get_league_analysis(
    basedir: Path,
    nation: str,
    tier: str,
    season: str,
    method: str = "pearson",
    min_matches: int = 1,
) -> dict[str, Any]:
    started = time.perf_counter()
    method = str(method or "pearson").lower().strip()
    if method not in ALLOWED_CORRELATION_METHODS:
        method = "pearson"
    min_matches = max(1, int(min_matches or 1))

    cache_key = _cache_key(basedir, nation, tier, season, method, min_matches)
    cached = _cached(cache_key)
    if cached is not None:
        render_meta = cached.get("render_meta")
        if isinstance(render_meta, dict):
            render_meta["cache_hit"] = True
        else:
            cached["render_meta"] = {"cache_hit": True}
        return cached

    schedule = _schedule_overview(basedir, nation, tier, season)
    season_df = load_season_events(basedir, nation, tier, season)
    rows, model_quality = _build_team_rows(season_df, min_matches=min_matches)
    if not rows:
        raise ValueError("No team style rows could be built from the selected league event files.")

    frame = pd.DataFrame(rows).sort_values("team").reset_index(drop=True)
    metric_keys = _available_metric_keys(rows)
    frame, dimensions = _style_dimension_scores(frame, metric_keys)

    league_means = frame.select_dtypes(include=["number"]).mean(numeric_only=True)
    league_stds = frame.select_dtypes(include=["number"]).std(numeric_only=True, ddof=0)
    frame["style_tags"] = [
        _team_tag(row, league_means, league_stds)
        for _, row in frame.iterrows()
    ]
    frame["overall_style_score"] = frame[[item["key"] for item in STYLE_DIMENSIONS if item["key"] in frame.columns]].mean(axis=1).round(2)

    correlation = _build_correlation(frame, metric_keys, method)
    pca, clusters = _build_pca_and_clusters(frame, metric_keys)
    outliers = _build_outliers(frame, metric_keys)
    findings = _style_findings(frame, dimensions)

    dimension_keys = [item["key"] for item in STYLE_DIMENSIONS if item["key"] in frame.columns]
    dimension_overview = []
    for item in dimensions:
        key = str(item["key"])
        if key not in frame.columns:
            continue
        dimension_overview.append(
            {
                "key": key,
                "label": str(item["label"]),
                "league_average": _round(frame[key].mean(), 2),
                "top_teams": item.get("top_teams", []),
            }
        )

    table_rows = frame.replace([np.inf, -np.inf], np.nan).fillna(0.0).to_dict(orient="records")
    completed_ms = (time.perf_counter() - started) * 1000.0

    payload: dict[str, Any] = {
        "nation": nation,
        "tier": tier,
        "season": season,
        "overview": {
            "teams_compared": int(len(frame)),
            "event_rows": int(len(season_df)),
            "schedule_matches": int(schedule.get("matches", 0)),
            "event_matches": int(pd.to_numeric(season_df[_match_id_col(season_df)], errors="coerce").nunique()) if _match_id_col(season_df) else 0,
            "correlation_method": method,
            "min_matches": int(min_matches),
            "dimension_scores": dimension_overview,
            "dominant_dimensions": sorted(dimension_overview, key=lambda item: item.get("league_average", 0), reverse=True)[:3],
        },
        "metric_catalog": [{**item, "enabled": item["key"] in metric_keys} for item in METRIC_DEFINITIONS],
        "style_dimensions": dimensions,
        "teams": table_rows,
        "correlations": correlation,
        "pca": pca,
        "clusters": clusters,
        "outliers": outliers,
        "findings": findings,
        "data_quality": {
            "schedule": schedule,
            "source_files": [str(path) for path in _source_paths(basedir, nation, tier, season)],
            "source_file_count": len(_source_paths(basedir, nation, tier, season)),
            "model_quality": model_quality,
            "notes": [
                "Possession is estimated from event volume and should be treated as a proxy.",
                "Correlations describe relationships between team profiles and should not be read as direct causation.",
                "Teams with limited saved event coverage are excluded when they fall below the minimum match filter.",
            ],
        },
        "render_meta": {
            "generated_at": time.time(),
            "duration_ms": round(completed_ms, 2),
            "cache_hit": False,
            "model_version": LEAGUE_ANALYSIS_CACHE_VERSION,
            "phases": [
                {"label": "Loading saved season event files"},
                {"label": "Building team style profiles"},
                {"label": "Scoring xG and xT context"},
                {"label": "Running correlation analysis"},
                {"label": "Running PCA and clustering"},
                {"label": "Preparing league style dashboard"},
            ],
            "data_source_counts": {
                "teams": int(len(frame)),
                "event_rows": int(len(season_df)),
                "metrics": int(len(metric_keys)),
            },
            "message": "League style analysis prepared from saved local event files.",
        },
    }

    _remember(cache_key, payload)
    return payload
