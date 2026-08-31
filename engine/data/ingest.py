"""Data ingestion from free, no-API-key sources.

NFL  : nflverse-data GitHub releases (play-by-play, schedules with betting
       lines, rest days, starting QBs, coaches, and stadium weather).
NCAA : sportsdataverse/cfbfastR-data GitHub main branch (schedules with
       scores and neutral-site flags, team info, betting lines).

Everything is cached locally under data/raw/ as parquet; re-running ingest
only re-downloads the current season.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"

NFL_PBP_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/pbp/"
    "play_by_play_{year}.parquet"
)
NFL_SCHED_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
)
CFB_SCHED_URL = (
    "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/"
    "schedules/parquet/schedules_{year}.parquet"
)
CFB_LINES_URL = (
    "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/"
    "betting/parquet/cfb_line_odds.parquet"
)
# ESPN schedule mirror (also keyless); shares ESPN game ids with the CFBD
# schedules above, and is used to backfill scores for seasons where the
# primary mirror's results are stale (e.g. 2024).
CFB_ESPN_SCHED_URL = (
    "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
    "espn_cfb_schedules/cfb_schedule_{year}.parquet"
)

# Only the play-by-play columns the feature pipeline needs; the full nflverse
# pbp file has 370+ columns and reading them all wastes memory and disk.
NFL_PBP_COLUMNS = [
    "game_id", "season", "week", "home_team", "away_team",
    "posteam", "defteam", "play_type", "pass", "rush",
    "epa", "success", "qb_dropback", "sack", "yards_gained",
    # player attribution, for the key-players breakdown
    "passer_player_name", "rusher_player_name", "receiver_player_name",
]


def _download(url: str, dest: Path, retries: int = 3) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=180)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return dest
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** (attempt + 1))
    return dest


def _cached_parquet(url: str, dest: Path, refresh: bool = False) -> pd.DataFrame:
    if refresh or not dest.exists():
        _download(url, dest)
    return pd.read_parquet(dest)


def load_nfl_schedules(refresh: bool = False) -> pd.DataFrame:
    dest = DATA_DIR / "nfl" / "games.csv"
    if refresh or not dest.exists():
        _download(NFL_SCHED_URL, dest)
    df = pd.read_csv(dest, low_memory=False)
    df["gameday"] = pd.to_datetime(df["gameday"])
    return df


def load_nfl_pbp(seasons: list[int], refresh_latest: bool = False) -> pd.DataFrame:
    frames = []
    for year in seasons:
        dest = DATA_DIR / "nfl" / f"pbp_{year}.parquet"
        if not dest.exists() or (refresh_latest and year == max(seasons)):
            _download(NFL_PBP_URL.format(year=year), dest)
        df = pd.read_parquet(dest, columns=NFL_PBP_COLUMNS)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def load_ncaa_schedules(seasons: list[int], refresh_latest: bool = False) -> pd.DataFrame:
    frames = []
    for year in seasons:
        dest = DATA_DIR / "ncaa" / f"schedules_{year}.parquet"
        refresh = refresh_latest and year == max(seasons)
        try:
            df = _cached_parquet(CFB_SCHED_URL.format(year=year), dest, refresh)
        except requests.HTTPError:
            # primary mirror hasn't published this season; fall back to the
            # live-updating ESPN mirror, inferring FBS membership from the
            # most recent published season
            df = _espn_fallback_schedule(year, frames, refresh)
            if df is None:
                continue
        else:
            df = _patch_ncaa_scores(df, year, refresh)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out["start_date"] = pd.to_datetime(out["start_date"], format="mixed", utc=True)
    return out


def _espn_fallback_schedule(
    year: int, prior_frames: list[pd.DataFrame], refresh: bool
) -> pd.DataFrame | None:
    dest = DATA_DIR / "ncaa" / f"espn_schedule_{year}.parquet"
    try:
        espn = _cached_parquet(CFB_ESPN_SCHED_URL.format(year=year), dest, refresh)
    except requests.HTTPError:
        return None
    if espn.empty or not prior_frames:
        return None
    # ESPN uses display names ("USC Trojans") but shares numeric team ids
    # with the primary mirror, so map name and division by id
    name_by_id, div_by_id = {}, {}
    for prev in prior_frames[-2:]:
        for side in ("home", "away"):
            ids = prev[f"{side}_id"].astype("Int64")
            for tid, name, div in zip(ids, prev[f"{side}_team"], prev[f"{side}_division"]):
                if pd.notna(tid):
                    name_by_id[int(tid)] = name
                    div_by_id[int(tid)] = str(div).lower()
    home_id = espn["home_id"].astype("int64")
    away_id = espn["away_id"].astype("int64")
    final = espn["status"] == "STATUS_FINAL"
    df = pd.DataFrame({
        "game_id": espn["game_id"].astype("int64"),
        "season": year,
        "week": espn["week"].astype(int),
        # ESPN season_type 3 = postseason
        "season_type": espn["season_type"].map({2: "regular", 3: "postseason"}).fillna("regular"),
        "start_date": espn["game_date"],
        "neutral_site": espn["neutral_site"].astype(bool),
        "home_team": home_id.map(name_by_id).fillna(espn["home_team"]),
        "away_team": away_id.map(name_by_id).fillna(espn["away_team"]),
        "home_points": espn["home_score"].where(final),
        "away_points": espn["away_score"].where(final),
        "home_division": home_id.map(div_by_id).fillna("other"),
        "away_division": away_id.map(div_by_id).fillna("other"),
        "home_conference": None,
        "away_conference": None,
    })
    return df


def _patch_ncaa_scores(df: pd.DataFrame, year: int, refresh: bool) -> pd.DataFrame:
    """Backfill missing scores from the ESPN schedule mirror (same game ids)."""
    if df["home_points"].notna().mean() > 0.5:
        return df
    dest = DATA_DIR / "ncaa" / f"espn_schedule_{year}.parquet"
    try:
        espn = _cached_parquet(CFB_ESPN_SCHED_URL.format(year=year), dest, refresh)
    except requests.HTTPError:
        return df
    espn = espn[espn["status"] == "STATUS_FINAL"][["game_id", "home_score", "away_score"]]
    espn["game_id"] = espn["game_id"].astype("int64")
    df = df.merge(espn, on="game_id", how="left")
    for side in ("home", "away"):
        df[f"{side}_points"] = df[f"{side}_points"].fillna(df[f"{side}_score"])
    return df.drop(columns=["home_score", "away_score"])


def load_ncaa_lines(refresh: bool = False) -> pd.DataFrame:
    dest = DATA_DIR / "ncaa" / "cfb_line_odds.parquet"
    return _cached_parquet(CFB_LINES_URL, dest, refresh)
