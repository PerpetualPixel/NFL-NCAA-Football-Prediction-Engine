"""Full team names with mascots, for display.

The model keys teams by whatever the data source uses — NFL abbreviations
("DET") and college school names ("Ohio State"). Readers want "Detroit
Lions" and "Ohio State Buckeyes", so display names are resolved here once
and looked up everywhere the site renders a team.
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd
import requests

from .data.ingest import DATA_DIR, _cached_parquet, _download

NFL_TEAMS_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/teams.csv"
CFB_TEAM_INFO_URL = (
    "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/"
    "team_info/parquet/cfb_team_info_{year}.parquet"
)


@lru_cache(maxsize=1)
def nfl_names() -> dict[str, str]:
    dest = DATA_DIR / "nfl" / "teams.csv"
    try:
        if not dest.exists():
            _download(NFL_TEAMS_URL, dest)
        df = pd.read_csv(dest)
    except (requests.RequestException, OSError):
        return {}
    # keep the most recent naming for each abbreviation (franchises move)
    df = df.sort_values("season").drop_duplicates("team", keep="last")
    return dict(zip(df["team"], df["full"]))


@lru_cache(maxsize=1)
def ncaa_names() -> dict[str, str]:
    for year in (2024, 2023, 2022):
        dest = DATA_DIR / "ncaa" / f"team_info_{year}.parquet"
        try:
            df = _cached_parquet(CFB_TEAM_INFO_URL.format(year=year), dest)
        except (requests.RequestException, OSError):
            continue
        out = {}
        for school, mascot in zip(df["school"], df.get("mascot", [])):
            if not isinstance(school, str):
                continue
            out[school] = f"{school} {mascot}" if isinstance(mascot, str) and mascot else school
        return out
    return {}


@lru_cache(maxsize=1)
def ncaa_fbs_teams() -> frozenset[str]:
    """School names of the FBS teams, used to keep unit ratings and their
    ranks scoped to the level the site actually covers."""
    for year in (2024, 2023, 2022):
        dest = DATA_DIR / "ncaa" / f"team_info_{year}.parquet"
        try:
            df = _cached_parquet(CFB_TEAM_INFO_URL.format(year=year), dest)
        except (requests.RequestException, OSError):
            continue
        if "classification" not in df.columns:
            continue
        return frozenset(df.loc[df["classification"].eq("fbs"), "school"].dropna())
    return frozenset()


def display_name(team: str, league: str) -> str:
    """Full name with mascot, falling back to whatever the data provides."""
    if not isinstance(team, str):
        return ""
    table = nfl_names() if league == "nfl" else ncaa_names()
    return table.get(team, team)


def short_name(team: str, league: str) -> str:
    """Just the mascot/nickname, for compact spots like matchup lines."""
    full = display_name(team, league)
    if league == "nfl":
        return full.rsplit(" ", 1)[-1] if " " in full else full
    return team
