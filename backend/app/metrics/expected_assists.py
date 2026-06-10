from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.metrics.expected_goals import IDENTITY_COLS, score_shots_with_models, _sorted_actions


def link_shots_to_assists(
    events_df: pd.DataFrame,
    scored_shots: pd.DataFrame | None = None,
) -> pd.DataFrame:
    actions = _sorted_actions(events_df)

    if scored_shots is None:
        scored_shots, _ = score_shots_with_models(actions)

    if actions.empty or scored_shots.empty:
        return pd.DataFrame()

    shot_map = scored_shots.set_index("event_index").to_dict(orient="index")

    records: list[dict[str, Any]] = []

    group_cols = ["match_id", "possession_id", "team"]
    for _, group in actions.groupby(group_cols, dropna=False):
        group = group.sort_values("event_index", kind="stable").reset_index(drop=True)
        if group.empty:
            continue

        pass_mask = group["successful"].astype(bool) & group["is_pass_like"].astype(bool)
        carry_mask = group["successful"].astype(bool) & group["is_carry"].astype(bool)
        shot_mask = group["is_shot_event"].astype(bool)

        if not shot_mask.any():
            continue

        for shot_row in group.loc[shot_mask].itertuples(index=False):
            shot_info = shot_map.get(int(shot_row.event_index))
            if not shot_info:
                continue

            prev_rows = group.loc[group["event_index"].lt(int(shot_row.event_index))].copy()
            if prev_rows.empty:
                continue

            prev_passes = prev_rows.loc[pass_mask.loc[prev_rows.index]].copy()
            prev_carries = prev_rows.loc[carry_mask.loc[prev_rows.index]].copy()

            final_pass = prev_passes.iloc[-1] if not prev_passes.empty else None
            second_pass = prev_passes.iloc[-2] if len(prev_passes) >= 2 else None
            final_carry = prev_carries.iloc[-1] if not prev_carries.empty else None

            shot_xg = float(shot_info.get("xg", 0.0))
            is_set_piece_shot = bool(shot_info.get("is_set_piece_action", False))
            is_open_play_shot = not is_set_piece_shot

            if final_pass is not None:
                rec = {col: final_pass.get(col, "") for col in IDENTITY_COLS}
                rec.update(
                    {
                        "xa_raw": shot_xg,
                        "open_play_xa_raw": shot_xg if is_open_play_shot else 0.0,
                        "set_piece_xa_raw": shot_xg if is_set_piece_shot else 0.0,
                        "shot_assists_linked_raw": 1.0,
                        "assisted_shot_xg_raw": shot_xg,
                    }
                )
                records.append(rec)

            if second_pass is not None:
                rec = {col: second_pass.get(col, "") for col in IDENTITY_COLS}
                rec.update(
                    {
                        "secondary_xa_raw": shot_xg,
                    }
                )
                records.append(rec)

            if final_carry is not None:
                rec = {col: final_carry.get(col, "") for col in IDENTITY_COLS}
                rec.update(
                    {
                        "carry_to_shot_xg_raw": shot_xg,
                    }
                )
                records.append(rec)

    if not records:
        return pd.DataFrame()

    return pd.DataFrame(records)


def aggregate_player_xa(linked_events: pd.DataFrame) -> pd.DataFrame:
    if linked_events.empty:
        return pd.DataFrame(columns=IDENTITY_COLS + ["xa_raw", "shot_assists_linked_raw"])

    work = linked_events.copy()
    for col in [
        "xa_raw",
        "open_play_xa_raw",
        "set_piece_xa_raw",
        "secondary_xa_raw",
        "carry_to_shot_xg_raw",
        "shot_assists_linked_raw",
        "assisted_shot_xg_raw",
    ]:
        if col not in work.columns:
            work[col] = 0.0
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0.0)

    agg = (
        work.groupby(IDENTITY_COLS, dropna=False)
        .agg(
            xa_raw=("xa_raw", "sum"),
            open_play_xa_raw=("open_play_xa_raw", "sum"),
            set_piece_xa_raw=("set_piece_xa_raw", "sum"),
            secondary_xa_raw=("secondary_xa_raw", "sum"),
            carry_to_shot_xg_raw=("carry_to_shot_xg_raw", "sum"),
            shot_assists_linked_raw=("shot_assists_linked_raw", "sum"),
            assisted_shot_xg_raw=("assisted_shot_xg_raw", "sum"),
        )
        .reset_index()
    )

    agg["xa_per_shot_assist"] = np.where(
        agg["shot_assists_linked_raw"] > 0,
        agg["xa_raw"] / agg["shot_assists_linked_raw"],
        np.nan,
    )
    agg["expected_assists_raw"] = agg["xa_raw"]
    return agg