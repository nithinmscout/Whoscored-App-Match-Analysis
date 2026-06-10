from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

IDENTITY_COLS = [
    "nation",
    "team_folder",
    "season",
    "team_id",
    "team",
    "player_id",
    "player",
]

GOAL_CENTRE_Y_68 = 34.0
GOAL_LEFT_POST_Y_68 = GOAL_CENTRE_Y_68 - 3.66
GOAL_RIGHT_POST_Y_68 = GOAL_CENTRE_Y_68 + 3.66

FEATURE_COLUMNS = [
    "x_105",
    "y_68",
    "distance_to_goal_m",
    "distance_sq",
    "angle_to_goal_rad",
    "inside_box",
    "central_lane",
    "big_chance",
    "header",
    "first_time",
    "rebound",
    "fast_break",
    "through_ball_assist",
    "cross_assist",
    "cutback_assist",
    "carry_before_shot",
    "prev_pass",
    "prev_carry",
    "prev_cross",
    "prev_action_seconds",
    "possession_action_index",
]


@dataclass
class ShotFamilyModel:
    name: str
    logistic_model: Pipeline | None
    tree_model: Pipeline | None
    feature_columns: list[str]
    sample_size: int
    goal_rate: float

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        if frame.empty:
            return np.array([], dtype=float)

        if self.logistic_model is None and self.tree_model is None:
            return np.full(len(frame), self.goal_rate, dtype=float)

        x = frame[self.feature_columns].copy()

        probs: list[np.ndarray] = []
        if self.logistic_model is not None:
            probs.append(self.logistic_model.predict_proba(x)[:, 1])
        if self.tree_model is not None:
            probs.append(self.tree_model.predict_proba(x)[:, 1])

        if not probs:
            return np.full(len(frame), self.goal_rate, dtype=float)

        blended = np.mean(np.vstack(probs), axis=0)

        shrink = float(np.clip(self.sample_size / 1500.0, 0.15, 1.0))
        shrunk = (shrink * blended) + ((1.0 - shrink) * self.goal_rate)
        return np.clip(shrunk, 0.001, 0.999)


@dataclass
class XGModelBundle:
    global_model: ShotFamilyModel | None
    family_models: dict[str, ShotFamilyModel]
    penalty_rate: float
    shots_used: int
    goals_seen: int


def _coerce_tag_set(value: object) -> set[str]:
    if isinstance(value, set):
        return {str(x).strip().lower().replace(" ", "").replace("-", "") for x in value if str(x).strip()}
    if isinstance(value, list):
        return {str(x).strip().lower().replace(" ", "").replace("-", "") for x in value if str(x).strip()}
    if value is None:
        return set()
    text = str(value).strip()
    if not text:
        return set()
    text = text.strip("[]")
    parts = [p.strip(" '\"") for p in text.split(",")]
    return {p.lower().replace(" ", "").replace("-", "") for p in parts if p}


def _ensure_numeric(df: pd.DataFrame, col: str, default: float = np.nan) -> pd.Series:
    if col not in df.columns:
        return pd.Series(default, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")


def _ensure_bool(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    series = df[col]
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).astype(bool)
    return (
        series.astype(str)
        .str.strip()
        .str.lower()
        .isin(["1", "true", "yes", "y"])
        .fillna(False)
    )


def _shifted_bool_with_default(df: pd.DataFrame, group_cols: list[str], col: str) -> pd.Series:
    shifted = df.groupby(group_cols, dropna=False)[col].shift(1)

    def _to_bool(value: object) -> bool:
        if value is None:
            return False
        try:
            if pd.isna(value):
                return False
        except Exception:
            pass
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y"}
        return bool(value)

    return shifted.map(_to_bool).astype(bool)

def _goal_distance_and_angle(x_105: pd.Series, y_68: pd.Series) -> tuple[pd.Series, pd.Series]:
    dx = (105.0 - x_105).clip(lower=0.0)
    dy_c = y_68 - GOAL_CENTRE_Y_68

    distance = np.sqrt((dx ** 2) + (dy_c ** 2))

    left = np.sqrt((dx ** 2) + ((y_68 - GOAL_LEFT_POST_Y_68) ** 2))
    right = np.sqrt((dx ** 2) + ((y_68 - GOAL_RIGHT_POST_Y_68) ** 2))

    numerator = (left ** 2) + (right ** 2) - (7.32 ** 2)
    denominator = 2.0 * left * right
    denominator = denominator.replace(0, np.nan)
    cosine = (numerator / denominator).clip(-1.0, 1.0)
    angle = np.arccos(cosine).fillna(0.0)

    return distance, angle


def _is_set_piece_tags(tags: set[str]) -> bool:
    probes = {
        "corner",
        "freekick",
        "freekick",
        "throwin",
        "throw",
    }
    return any(p in tags for p in probes)


def _is_penalty_tags(tags: set[str]) -> bool:
    return any(p in tags for p in {"penalty", "pen"})


def _is_direct_fk_tags(tags: set[str]) -> bool:
    return any(p in tags for p in {"freekick", "freekick"}) and not any(
        p in tags for p in {"cross", "cornercrossed", "freekickcrossed", "indirect"}
    )


def _sorted_actions(events_df: pd.DataFrame) -> pd.DataFrame:
    df = events_df.copy()

    if "event_index" not in df.columns:
        df = df.reset_index(drop=False).rename(columns={"index": "event_index"})

    if "match_id" not in df.columns:
        for alias in ["matchid", "game_id", "id"]:
            if alias in df.columns:
                df["match_id"] = pd.to_numeric(df[alias], errors="coerce")
                break

    df["match_id"] = _ensure_numeric(df, "match_id")
    df["period"] = _ensure_numeric(df, "period", 1).fillna(1)
    df["expanded_minute"] = _ensure_numeric(df, "expanded_minute")
    if df["expanded_minute"].isna().all():
        minute = _ensure_numeric(df, "minute", 0).fillna(0.0)
        second = _ensure_numeric(df, "second", 0).fillna(0.0)
        df["expanded_minute"] = minute + (second / 60.0)

    df["team"] = df.get("team", pd.Series([""] * len(df), index=df.index)).astype(str)
    df["player"] = df.get("player", pd.Series([""] * len(df), index=df.index)).astype(str)
    df["type_l"] = df.get("type_l", df.get("type", pd.Series([""] * len(df), index=df.index)).astype(str).str.lower().str.strip())
    df["outcome_l"] = df.get("outcome_l", df.get("outcome_type", pd.Series([""] * len(df), index=df.index)).astype(str).str.lower().str.strip())

    df["x_120"] = _ensure_numeric(df, "x_120")
    df["y_80"] = _ensure_numeric(df, "y_80")
    if df["x_120"].isna().all() and "x" in df.columns:
        df["x_120"] = _ensure_numeric(df, "x") * (120.0 / 100.0)
    if df["y_80"].isna().all() and "y" in df.columns:
        df["y_80"] = _ensure_numeric(df, "y") * (80.0 / 100.0)

    df["end_x_120"] = _ensure_numeric(df, "end_x_120")
    df["end_y_80"] = _ensure_numeric(df, "end_y_80")
    if df["end_x_120"].isna().all() and "end_x" in df.columns:
        df["end_x_120"] = _ensure_numeric(df, "end_x") * (120.0 / 100.0)
    if df["end_y_80"].isna().all() and "end_y" in df.columns:
        df["end_y_80"] = _ensure_numeric(df, "end_y") * (80.0 / 100.0)

    df["successful"] = df.get(
        "successful",
        df["outcome_l"].isin({"successful", "success", "won", "complete", "completed", "accurate"})
    )
    df["successful"] = _ensure_bool(df, "successful") | df["outcome_l"].isin(
        {"successful", "success", "won", "complete", "completed", "accurate"}
    )

    df["is_goal"] = _ensure_bool(df, "is_goal") | df["type_l"].eq("goal")
    df["is_shot_event"] = _ensure_bool(df, "is_shot_event") | _ensure_bool(df, "is_shot") | df["type_l"].str.contains("shot", na=False) | df["type_l"].eq("goal")
    df["is_pass_like"] = _ensure_bool(df, "is_pass_like") | df["type_l"].str.contains("pass", na=False) | df["type_l"].eq("cross")
    df["is_carry"] = _ensure_bool(df, "is_carry") | df["type_l"].isin(["carry", "dribble", "take on", "takeon", "run"])

    tag_series = df.get("qual_tags", pd.Series([[] for _ in range(len(df))], index=df.index))
    df["tag_set"] = tag_series.apply(_coerce_tag_set)

    df["is_cross"] = df["tag_set"].apply(lambda s: "cross" in s) | df["type_l"].eq("cross")
    df["is_big_chance"] = df["tag_set"].apply(lambda s: "bigchance" in s)
    df["is_header"] = df["tag_set"].apply(lambda s: any(p in s for p in {"header", "headed", "head"}))
    df["is_first_time"] = df["tag_set"].apply(lambda s: any(p in s for p in {"firsttime", "firsttimefinish"}))
    df["is_rebound"] = df["tag_set"].apply(lambda s: "rebound" in s)
    df["is_fast_break"] = df["tag_set"].apply(lambda s: any(p in s for p in {"fastbreak", "counterattack"}))
    df["is_through_ball"] = df["tag_set"].apply(lambda s: any(p in s for p in {"throughball", "through"}))
    df["is_cutback"] = df["tag_set"].apply(lambda s: any(p in s for p in {"cutback", "lowcentre", "boxcentre"}))
    df["is_penalty"] = df["tag_set"].apply(_is_penalty_tags)
    df["is_direct_free_kick"] = df["tag_set"].apply(_is_direct_fk_tags)
    df["is_set_piece_action"] = df["tag_set"].apply(_is_set_piece_tags) | df["is_penalty"] | df["is_direct_free_kick"]

    df = df.sort_values(
        ["match_id", "period", "expanded_minute", "event_index"],
        kind="stable",
        na_position="last",
    ).reset_index(drop=True)

    new_possession = (
        df["match_id"].ne(df["match_id"].shift(1))
        | df["period"].ne(df["period"].shift(1))
        | df["team"].ne(df["team"].shift(1))
    )
    df["possession_id"] = new_possession.cumsum()

    grp = ["match_id", "possession_id", "team"]
    df["possession_action_index"] = df.groupby(grp, dropna=False).cumcount() + 1
    df["prev_expanded_minute"] = df.groupby(grp, dropna=False)["expanded_minute"].shift(1)
    df["prev_player"] = df.groupby(grp, dropna=False)["player"].shift(1)
    df["prev_is_pass_like"] = _shifted_bool_with_default(df, grp, "is_pass_like")
    df["prev_is_carry"] = _shifted_bool_with_default(df, grp, "is_carry")
    df["prev_is_cross"] = _shifted_bool_with_default(df, grp, "is_cross")
    df["prev_is_through_ball"] = _shifted_bool_with_default(df, grp, "is_through_ball")
    df["prev_is_cutback"] = _shifted_bool_with_default(df, grp, "is_cutback")

    return df


def build_shot_feature_frame(events_df: pd.DataFrame) -> pd.DataFrame:
    df = _sorted_actions(events_df)
    shots = df.loc[df["is_shot_event"]].copy()
    if shots.empty:
        return shots

    shots["x_105"] = shots["x_120"] * (105.0 / 120.0)
    shots["y_68"] = shots["y_80"] * (68.0 / 80.0)
    shots["y_abs_from_centre"] = (shots["y_68"] - GOAL_CENTRE_Y_68).abs()

    distance, angle = _goal_distance_and_angle(shots["x_105"], shots["y_68"])
    shots["distance_to_goal_m"] = distance
    shots["distance_sq"] = distance ** 2
    shots["angle_to_goal_rad"] = angle

    shots["inside_box"] = (
        shots["x_105"].ge(88.0) & shots["y_68"].between(13.84, 54.16, inclusive="both")
    ).astype(int)
    shots["central_lane"] = shots["y_68"].between(24.0, 44.0, inclusive="both").astype(int)

    shots["big_chance"] = shots["is_big_chance"].astype(int)
    shots["header"] = shots["is_header"].astype(int)
    shots["first_time"] = shots["is_first_time"].astype(int)
    shots["rebound"] = shots["is_rebound"].astype(int)
    shots["fast_break"] = shots["is_fast_break"].astype(int)
    shots["through_ball_assist"] = shots["prev_is_through_ball"].astype(int)
    shots["cross_assist"] = shots["prev_is_cross"].astype(int)
    shots["cutback_assist"] = shots["prev_is_cutback"].astype(int)
    shots["carry_before_shot"] = (
        shots["prev_is_carry"] & shots["prev_player"].fillna("").eq(shots["player"].fillna(""))
    ).astype(int)
    shots["prev_pass"] = shots["prev_is_pass_like"].astype(int)
    shots["prev_carry"] = shots["prev_is_carry"].astype(int)
    shots["prev_cross"] = shots["prev_is_cross"].astype(int)

    delta_seconds = (shots["expanded_minute"] - shots["prev_expanded_minute"]).fillna(99.0) * 60.0
    shots["prev_action_seconds"] = delta_seconds.clip(lower=0.0, upper=99.0)

    shots["goal"] = shots["is_goal"].astype(int)
    shots["shot_family"] = np.select(
        [
            shots["is_penalty"],
            shots["is_direct_free_kick"],
            shots["is_header"],
            shots["is_set_piece_action"],
        ],
        [
            "penalty",
            "direct_free_kick",
            "header",
            "set_piece",
        ],
        default="open_play",
    )

    for col in FEATURE_COLUMNS:
        shots[col] = pd.to_numeric(shots[col], errors="coerce").fillna(0.0)

    return shots


def _build_logistic_model() -> Pipeline:
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "clf",
                LogisticRegression(
                    max_iter=3000,
                    solver="lbfgs",
                    random_state=7,
                ),
            ),
        ]
    )


def _build_tree_model() -> Pipeline:
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            (
                "clf",
                HistGradientBoostingClassifier(
                    max_depth=5,
                    max_iter=260,
                    learning_rate=0.05,
                    min_samples_leaf=24,
                    l2_regularization=0.05,
                    random_state=7,
                ),
            ),
        ]
    )


def _fit_family_model(name: str, shot_frame: pd.DataFrame) -> ShotFamilyModel | None:
    if shot_frame.empty:
        return None

    y = pd.to_numeric(shot_frame["goal"], errors="coerce").fillna(0).astype(int)
    if len(shot_frame) < 120 or int(y.sum()) < 12:
        return ShotFamilyModel(
            name=name,
            logistic_model=None,
            tree_model=None,
            feature_columns=FEATURE_COLUMNS,
            sample_size=int(len(shot_frame)),
            goal_rate=float(y.mean()) if len(y) else 0.1,
        )

    x = shot_frame[FEATURE_COLUMNS].copy()

    logistic = _build_logistic_model()
    tree = _build_tree_model()

    logistic.fit(x, y)
    tree.fit(x, y)

    return ShotFamilyModel(
        name=name,
        logistic_model=logistic,
        tree_model=tree,
        feature_columns=FEATURE_COLUMNS,
        sample_size=int(len(shot_frame)),
        goal_rate=float(y.mean()),
    )


def fit_xg_models(events_df: pd.DataFrame) -> XGModelBundle:
    shots = build_shot_feature_frame(events_df)
    if shots.empty:
        return XGModelBundle(
            global_model=None,
            family_models={},
            penalty_rate=0.76,
            shots_used=0,
            goals_seen=0,
        )

    non_pen = shots.loc[shots["shot_family"].ne("penalty")].copy()
    global_model = _fit_family_model("global_non_penalty", non_pen)

    family_models: dict[str, ShotFamilyModel] = {}
    for family_name in ["open_play", "header", "set_piece", "direct_free_kick"]:
        family_frame = shots.loc[shots["shot_family"].eq(family_name)].copy()
        family_model = _fit_family_model(family_name, family_frame)
        if family_model is not None:
            family_models[family_name] = family_model

    pens = shots.loc[shots["shot_family"].eq("penalty")].copy()
    if len(pens) >= 10:
        penalty_rate = float(pens["goal"].mean())
    else:
        penalty_rate = 0.76

    return XGModelBundle(
        global_model=global_model,
        family_models=family_models,
        penalty_rate=penalty_rate,
        shots_used=int(len(shots)),
        goals_seen=int(shots["goal"].sum()),
    )


def score_shots_with_models(
    events_df: pd.DataFrame,
    model_bundle: XGModelBundle | None = None,
) -> tuple[pd.DataFrame, XGModelBundle]:
    shots = build_shot_feature_frame(events_df)
    if model_bundle is None:
        model_bundle = fit_xg_models(events_df)

    if shots.empty:
        shots["xg"] = pd.Series(dtype=float)
        return shots, model_bundle

    xg = np.full(len(shots), np.nan, dtype=float)

    penalty_mask = shots["shot_family"].eq("penalty").to_numpy()
    xg[penalty_mask] = model_bundle.penalty_rate

    global_probs = None
    if model_bundle.global_model is not None:
        global_probs = model_bundle.global_model.predict(shots)

    if global_probs is not None:
        xg[~penalty_mask] = global_probs[~penalty_mask]

    for family_name, family_model in model_bundle.family_models.items():
        if family_name == "penalty":
            continue
        fam_mask = shots["shot_family"].eq(family_name).to_numpy()
        if not fam_mask.any():
            continue
        fam_probs = family_model.predict(shots.loc[fam_mask])
        if global_probs is not None:
            xg[fam_mask] = (0.50 * global_probs[fam_mask]) + (0.50 * fam_probs)
        else:
            xg[fam_mask] = fam_probs

    shots = shots.copy()
    shots["xg"] = np.clip(xg, 0.001, 0.999)
    return shots, model_bundle


def aggregate_player_xg(scored_shots: pd.DataFrame) -> pd.DataFrame:
    if scored_shots.empty:
        return pd.DataFrame(columns=IDENTITY_COLS + ["xg_raw", "np_xg_raw", "xg_shots_raw"])

    shots = scored_shots.copy()

    for col in IDENTITY_COLS:
        if col not in shots.columns:
            shots[col] = ""

    shots["goal_value"] = shots["goal"].astype(float)
    shots["np_goal_value"] = np.where(shots["shot_family"].eq("penalty"), 0.0, shots["goal"].astype(float))
    shots["xg_value"] = shots["xg"].astype(float)
    shots["np_xg_value"] = np.where(shots["shot_family"].eq("penalty"), 0.0, shots["xg_value"])
    shots["header_xg_value"] = np.where(shots["shot_family"].eq("header"), shots["xg_value"], 0.0)
    shots["set_piece_xg_value"] = np.where(shots["shot_family"].eq("set_piece"), shots["xg_value"], 0.0)
    shots["direct_fk_xg_value"] = np.where(shots["shot_family"].eq("direct_free_kick"), shots["xg_value"], 0.0)
    shots["penalty_xg_value"] = np.where(shots["shot_family"].eq("penalty"), shots["xg_value"], 0.0)

    shots["xg_shots_raw"] = 1.0
    shots["np_xg_shots_raw"] = np.where(shots["shot_family"].eq("penalty"), 0.0, 1.0)
    shots["header_shots_raw"] = np.where(shots["shot_family"].eq("header"), 1.0, 0.0)
    shots["set_piece_shots_raw"] = np.where(shots["shot_family"].eq("set_piece"), 1.0, 0.0)
    shots["direct_fk_shots_raw"] = np.where(shots["shot_family"].eq("direct_free_kick"), 1.0, 0.0)
    shots["penalty_shots_raw"] = np.where(shots["shot_family"].eq("penalty"), 1.0, 0.0)

    agg = (
        shots.groupby(IDENTITY_COLS, dropna=False)
        .agg(
            xg_raw=("xg_value", "sum"),
            np_xg_raw=("np_xg_value", "sum"),
            header_xg_raw=("header_xg_value", "sum"),
            set_piece_xg_raw=("set_piece_xg_value", "sum"),
            direct_fk_xg_raw=("direct_fk_xg_value", "sum"),
            penalty_xg_raw=("penalty_xg_value", "sum"),
            xg_goals_raw=("goal_value", "sum"),
            np_goals_raw=("np_goal_value", "sum"),
            xg_shots_raw=("xg_shots_raw", "sum"),
            np_xg_shots_raw=("np_xg_shots_raw", "sum"),
            header_shots_raw=("header_shots_raw", "sum"),
            set_piece_shots_raw=("set_piece_shots_raw", "sum"),
            direct_fk_shots_raw=("direct_fk_shots_raw", "sum"),
            penalty_shots_raw=("penalty_shots_raw", "sum"),
        )
        .reset_index()
    )

    agg["goals_minus_xg_raw"] = agg["xg_goals_raw"] - agg["xg_raw"]
    agg["goals_minus_np_xg_raw"] = agg["np_goals_raw"] - agg["np_xg_raw"]
    agg["xg_per_shot"] = np.where(agg["xg_shots_raw"] > 0, agg["xg_raw"] / agg["xg_shots_raw"], np.nan)
    agg["np_xg_per_shot"] = np.where(agg["np_xg_shots_raw"] > 0, agg["np_xg_raw"] / agg["np_xg_shots_raw"], np.nan)

    agg["expected_goals_raw"] = agg["xg_raw"]
    return agg