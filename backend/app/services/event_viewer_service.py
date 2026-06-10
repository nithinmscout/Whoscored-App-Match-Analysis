from pathlib import Path

import soccerdata as sd


def make_ws(
    league: str,
    season: str | int,
    headless: bool,
    browser_path: str | None,
) -> sd.WhoScored:
    kwargs = {"leagues": league, "seasons": season, "headless": headless}
    if browser_path:
        kwargs["path_to_browser"] = str(Path(browser_path))
    return sd.WhoScored(**kwargs)


def fetch_missing_players(
    league: str,
    season: str | int,
    headless: bool,
    browser_path: str | None,
    game_id: int,
):
    ws = make_ws(league, season, headless, browser_path)
    return ws.read_missing_players(game_id=game_id)


def fetch_events(
    league: str,
    season: str | int,
    headless: bool,
    browser_path: str | None,
    match_id: int,
    output_fmt: str,
):
    ws = make_ws(league, season, headless, browser_path)
    try:
        return ws.read_events(
            match_id=match_id,
            output_fmt=output_fmt,
            force_cache=True,
            retry_missing=True,
            on_error="raise",
        )
    except TypeError:
        try:
            return ws.read_events(
                match_id=match_id,
                output_fmt=output_fmt,
                force_cache=True,
                retry_missing=True,
            )
        except TypeError:
            return ws.read_events(
                match_id=match_id,
                output_fmt=output_fmt,
                force_cache=True,
            )
