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
CFB_PLAYER_STATS_URL = (
    "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/"
    "player_stats/parquet/player_stats_{year}.parquet"
)
# one row per play, with player attribution, down/distance and yardage —
# enough to build the same unit ratings and player breakdowns as the NFL side
CFB_PLAY_COLUMNS = [
    "game_id", "season", "week", "team", "opponent", "down", "distance",
    "yards_to_goal", "completion_player", "completion_yds", "rush_player",
    "rush_yds", "incompletion_player", "reception_player", "reception_yds",
]

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
    # player attribution, for the key-players breakdown and QB ratings
    "passer_player_name", "rusher_player_name", "receiver_player_name",
    "passer_player_id",
    # usage and role: who gets the targets and carries, how far downfield,
    # and how much of it they convert
    "complete_pass", "air_yards", "yards_after_catch",
    "pass_attempt", "rush_attempt",
]

NFL_INJURIES_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/injuries/"
    "injuries_{year}.parquet"
)
NFL_ROSTER_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/rosters/"
    "roster_{year}.parquet"
)
ROSTER_COLUMNS = ["season", "team", "full_name", "position", "depth_chart_position"]

NFL_SNAPS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/"
    "snap_counts_{year}.parquet"
)
INJURY_COLUMNS = ["season", "week", "team", "gsis_id", "position",
                  "full_name", "report_status"]
SNAP_COLUMNS = ["season", "week", "team", "player", "pfr_player_id",
                "position", "offense_pct", "defense_pct"]


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


def _load_nfl_yearly(url: str, name: str, columns: list[str],
                      seasons: list[int], refresh_latest: bool) -> pd.DataFrame:
    frames = []
    for year in seasons:
        dest = DATA_DIR / "nfl" / f"{name}_{year}.parquet"
        if not dest.exists() or (refresh_latest and year == max(seasons)):
            try:
                _download(url.format(year=year), dest)
            except requests.HTTPError:
                continue  # season not published yet
        try:
            df = pd.read_parquet(dest)
        except (OSError, ValueError):
            continue
        frames.append(df[[c for c in columns if c in df.columns]])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_nfl_injuries(seasons: list[int], refresh_latest: bool = False) -> pd.DataFrame:
    """Weekly injury reports (Out / Doubtful / Questionable per player)."""
    return _load_nfl_yearly(NFL_INJURIES_URL, "injuries", INJURY_COLUMNS,
                            seasons, refresh_latest)


def load_nfl_snaps(seasons: list[int], refresh_latest: bool = False) -> pd.DataFrame:
    """Per-game snap shares, used to weight how much a missing player matters."""
    return _load_nfl_yearly(NFL_SNAPS_URL, "snap_counts", SNAP_COLUMNS,
                            seasons, refresh_latest)


def load_nfl_rosters(seasons: list[int], refresh_latest: bool = False) -> pd.DataFrame:
    """Rosters, used to expand the play-by-play's abbreviated player names."""
    return _load_nfl_yearly(NFL_ROSTER_URL, "roster", ROSTER_COLUMNS,
                            seasons, refresh_latest)


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


def load_ncaa_plays(seasons: list[int], refresh_latest: bool = False) -> pd.DataFrame:
    """Play-level data for the seasons available from the mirror."""
    frames = []
    for year in seasons:
        dest = DATA_DIR / "ncaa" / f"plays_{year}.parquet"
        refresh = refresh_latest and year == max(seasons)
        try:
            df = _cached_parquet(CFB_PLAYER_STATS_URL.format(year=year), dest, refresh)
        except requests.HTTPError:
            continue  # season not published yet
        cols = [c for c in CFB_PLAY_COLUMNS if c in df.columns]
        frames.append(df[cols])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def ncaa_game_odds(schedule: pd.DataFrame, refresh: bool = False) -> pd.DataFrame:
    """Consensus spread, total, and moneyline per game, in the same shape and
    sign convention as the NFL schedule feed.

    The lines file carries one row per book per side; team names match the
    schedule exactly for every FBS team, so the home row is identified by
    name. Consensus is the median across books, which is more robust than
    trusting any single one.
    """
    lines = load_ncaa_lines(refresh=refresh)
    lines = lines.dropna(subset=["game_id"]).copy()
    lines["game_id"] = lines["game_id"].astype("int64")

    sched = schedule[["game_id", "home_team", "away_team"]].copy()
    sched["game_id"] = sched["game_id"].astype("int64")
    df = lines.merge(sched, on="game_id", how="inner")
    is_home = df["abbr"] == df["home_team"]
    is_away = df["abbr"] == df["away_team"]

    def consensus(mask: pd.Series, value_col: str, name: str) -> pd.Series:
        return df.loc[mask].groupby("game_id")[value_col].median().rename(name)

    spread = df["market_type"] == "spread"
    money = df["market_type"] == "money_line"
    total = (df["market_type"] == "total") & (df["abbr"] == "over")

    out = pd.concat([
        # book posts the home side as e.g. -2.5; the engine's convention is
        # positive when the home team is favored
        (-consensus(spread & is_home, "lines", "spread_line")),
        # the number the market opened at, for closing-line-value tracking
        (-consensus(spread & is_home, "opening_lines", "open_spread_line")),
        consensus(total, "lines", "total_line"),
        consensus(money & is_home, "odds", "home_moneyline"),
        consensus(money & is_away, "odds", "away_moneyline"),
        consensus(spread & is_home, "odds", "home_spread_odds"),
        consensus(spread & is_away, "odds", "away_spread_odds"),
    ], axis=1).reset_index()
    # American odds are undefined between -100 and +100; consensus medians
    # across books occasionally produce 0 or other impossible values, which
    # would otherwise be treated as a real price
    for col in ("home_moneyline", "away_moneyline", "home_spread_odds", "away_spread_odds"):
        if col in out.columns:
            out[col] = out[col].where(out[col].abs() >= 100)
    return out
