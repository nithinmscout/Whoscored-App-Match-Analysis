from __future__ import annotations

from typing import Any

from pydantic import BaseModel, RootModel


JsonScalar = str | int | float | bool | None


class TableRowModel(RootModel[dict[str, JsonScalar]]):
    pass


class MasterRequest(BaseModel):
    season: str | None = None


class PlayerProfileRequest(BaseModel):
    playerid: int
    season: str | None = None


class MasterResponseModel(BaseModel):
    rebuilt: bool
    masterpath: str
    meta: dict[str, Any]
    count: int
    columns: list[str]
    rows: list[dict[str, JsonScalar]]
    preview_rows: list[dict[str, JsonScalar]] = []


class AdvancedMasterStatusResponseModel(BaseModel):
    season: str
    has_master: bool
    up_to_date: bool
    event_csv_count: int
    db_source_max_mtime: float | None = None
    current_source_max_mtime: float | None = None
    newer_event_files: list[str]
    message: str


class PlayerEventsResponseModel(BaseModel):
    count: int
    rows: list[dict[str, JsonScalar]]