from pydantic import BaseModel
from typing import Optional, Any

class ScheduleRequest(BaseModel):
    league: str = 'ENG-Premier League'
    season: str = '2024'
    headless: bool = True
    browserpath: Optional[str] = None

class SaveScheduleRequest(BaseModel):
    nation: str = ''
    tier: str = ''
    season: str
    rows: list[dict[str, Any]]
    league: Optional[str] = None

class LoadScheduleCsvRequest(BaseModel):
    nation: str
    tier: str
    season: str