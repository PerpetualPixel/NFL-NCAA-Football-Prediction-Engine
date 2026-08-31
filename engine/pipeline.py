"""League pipelines: normalized game tables and walk-forward features.

Every feature row for game G is computed using only games completed before
G's (season, week). That walk-forward discipline is what makes the backtest
honest — no rating ever sees the game it is predicting.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import LeagueConfig, LEAGUES
from .data import ingest
from .features.ratings import recency_weights, solve_ratings

FCS_BUCKET = "NON-FBS"


def load_league_inputs(league: str, refresh: bool = False, recent_only: bool = False):
    """Games table plus (NFL) unit stats; recent_only trims the play-by-play
    download to the rating window for fast CI site builds."""
    games = load_games(league, refresh=refresh)
    unit_stats = None
    if league == "nfl":
        seasons = sorted(games.loc[games["completed"], "season"].unique().tolist())
        if recent_only:
            seasons = seasons[-(LEAGUES[league].rating_window_seasons + 1):]
        pbp = ingest.load_nfl_pbp(seasons, refresh_latest=refresh)
        unit_stats = nfl_unit_game_stats(pbp)
    return games, unit_stats


# ---------------------------------------------------------------------------
# Normalized game tables
# ---------------------------------------------------------------------------

def load_games(league: str, refresh: bool = False) -> pd.DataFrame:
    """Return one row per game with a shared schema across leagues:

    season, week, home_team, away_team, home_score, away_score, margin
    (home - away), neutral, completed, spread_line (positive = home
    favored), plus league extras (rest days, QBs, coaches for NFL).
    """
    cfg = LEAGUES[league]
    if league == "nfl":
        df = ingest.load_nfl_schedules(refresh=refresh)
        df = df[df["season"] >= cfg.first_season].copy()
        df["neutral"] = (df["location"] == "Neutral").astype(int)
        df["completed"] = df["home_score"].notna() & df["away_score"].notna()
        df["margin"] = df["home_score"] - df["away_score"]
        keep = [
            "game_id", "season", "week", "gameday", "home_team", "away_team",
            "home_score", "away_score", "margin", "neutral", "completed",
            "spread_line", "total_line", "home_rest", "away_rest",
            "home_qb_name", "away_qb_name", "home_coach", "away_coach",
            "div_game", "roof", "temp", "wind",
        ]
        return df[keep].sort_values(["season", "week", "gameday"]).reset_index(drop=True)

    import datetime as _dt
    seasons = list(range(cfg.first_season, _dt.date.today().year + 1))
    df = ingest.load_ncaa_schedules(seasons, refresh_latest=refresh)
    # Collapse every non-FBS opponent into one bucket team so FCS blowouts
    # inform the fit without adding hundreds of one-game columns; drop games
    # where neither side is FBS.
    home_fbs = df["home_division"].str.lower().eq("fbs")
    away_fbs = df["away_division"].str.lower().eq("fbs")
    df = df[home_fbs | away_fbs].copy()
    df.loc[~home_fbs, "home_team"] = FCS_BUCKET
    df.loc[~away_fbs, "away_team"] = FCS_BUCKET
    # Postseason weeks restart at 1; push them past the regular season so
    # (season, week) ordering stays chronological.
    df["week"] = df["week"].astype(int)
    is_post = df["season_type"] == "postseason"
    max_reg_week = (
        df["week"].where(~is_post).groupby(df["season"]).transform("max").fillna(16)
    )
    df.loc[is_post, "week"] = df.loc[is_post, "week"] + max_reg_week[is_post].astype(int)
    df["completed"] = df["home_points"].notna() & df["away_points"].notna()
    df["margin"] = df["home_points"] - df["away_points"]
    df["neutral"] = df["neutral_site"].astype(bool).astype(int)
    out = df.rename(
        columns={"home_points": "home_score", "away_points": "away_score",
                 "start_date": "gameday"}
    )[
        ["game_id", "season", "week", "gameday", "home_team", "away_team",
         "home_score", "away_score", "margin", "neutral", "completed",
         "home_conference", "away_conference"]
    ]
    out["spread_line"] = np.nan  # attached from the betting file in backtest
    return out.sort_values(["season", "week", "gameday"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# NFL unit-level game stats from play-by-play
# ---------------------------------------------------------------------------

def nfl_unit_game_stats(pbp: pd.DataFrame) -> pd.DataFrame:
    """Per (game, offense team): pass/rush EPA per play and sack rate allowed."""
    plays = pbp[
        pbp["posteam"].notna()
        & pbp["epa"].notna()
        & pbp["play_type"].isin(["pass", "run"])
    ].copy()
    plays["is_pass"] = plays["play_type"].eq("pass")

    grp = plays.groupby(["game_id", "season", "week", "posteam", "defteam"])
    out = grp.apply(
        lambda g: pd.Series({
            "pass_epa": g.loc[g["is_pass"], "epa"].mean(),
            "rush_epa": g.loc[~g["is_pass"], "epa"].mean(),
            "pass_sr": g.loc[g["is_pass"], "success"].mean(),
            "rush_sr": g.loc[~g["is_pass"], "success"].mean(),
            "sack_rate": g.loc[g["qb_dropback"] == 1, "sack"].mean(),
        }),
        include_groups=False,
    ).reset_index()
    return out


# ---------------------------------------------------------------------------
# Walk-forward feature building
# ---------------------------------------------------------------------------

def _iter_weeks(games: pd.DataFrame):
    weeks = games[["season", "week"]].drop_duplicates().sort_values(["season", "week"])
    for season, week in weeks.itertuples(index=False):
        yield int(season), int(week)


def _rating_window(games: pd.DataFrame, cfg: LeagueConfig, season: int, week: int) -> pd.DataFrame:
    before = games[
        games["completed"]
        & (
            (games["season"] < season)
            | ((games["season"] == season) & (games["week"] < week))
        )
        & (games["season"] >= season - cfg.rating_window_seasons)
    ]
    return before


def build_walk_forward_features(
    league: str,
    games: pd.DataFrame,
    unit_stats: pd.DataFrame | None = None,
    start_season: int | None = None,
) -> pd.DataFrame:
    """For each game, attach as-of-kickoff ratings and derived features."""
    cfg = LEAGUES[league]
    rows = []
    for season, week in _iter_weeks(games):
        if start_season is not None and season < start_season:
            continue
        window = _rating_window(games, cfg, season, week)
        if len(window) < 100:
            continue
        w = recency_weights(
            window["season"], window["week"], season, week,
            cfg.weekly_decay, cfg.prior_season_weight,
        )
        power, hfa = solve_ratings(
            window["home_team"], window["away_team"],
            window["margin"].to_numpy(float), w, cfg.rating_alpha,
            hfa_indicator=(1 - window["neutral"]).to_numpy(float),
        )

        units = {}
        if unit_stats is not None:
            uwindow = unit_stats.merge(
                window[["game_id"]], on="game_id", how="inner"
            )
            uw = recency_weights(
                uwindow["season"], uwindow["week"], season, week,
                cfg.weekly_decay, cfg.prior_season_weight,
            )
            for metric in ["pass_epa", "rush_epa"]:
                valid = uwindow[metric].notna().to_numpy()
                # Offense and defense are separate entities in one joint
                # solve: EPA = off_strength(posteam) - def_strength(defteam).
                combined, _ = solve_ratings(
                    "O::" + uwindow.loc[valid, "posteam"],
                    "D::" + uwindow.loc[valid, "defteam"],
                    uwindow.loc[valid, metric].to_numpy(float),
                    uw[valid], alpha=cfg.rating_alpha / 4,
                )
                off = {k[3:]: v for k, v in combined.items() if k.startswith("O::")}
                deff = {k[3:]: v for k, v in combined.items() if k.startswith("D::")}
                # center each side on its own league average for display;
                # matchup differentials are invariant to this shift
                off_mean = sum(off.values()) / len(off)
                def_mean = sum(deff.values()) / len(deff)
                units[f"off_{metric}"] = {k: v - off_mean for k, v in off.items()}
                units[f"def_{metric}"] = {k: v - def_mean for k, v in deff.items()}

        current = games[(games["season"] == season) & (games["week"] == week)]
        for g in current.itertuples(index=False):
            home, away = g.home_team, g.away_team
            if home not in power or away not in power:
                continue
            row = {
                "game_id": g.game_id, "season": season, "week": week,
                "home_team": home, "away_team": away,
                "margin": g.margin, "completed": g.completed,
                "neutral": g.neutral, "spread_line": g.spread_line,
                "rating_diff": power[home] - power[away],
                "hfa_fit": hfa,
                "home_rating": power[home], "away_rating": power[away],
            }
            if hasattr(g, "home_rest") and pd.notna(g.home_rest):
                row["rest_diff"] = g.home_rest - g.away_rest
            else:
                row["rest_diff"] = 0.0
            for metric in ["pass_epa", "rush_epa"]:
                off_key, def_key = f"off_{metric}", f"def_{metric}"
                if off_key in units and all(
                    t in units[off_key] and t in units[def_key] for t in (home, away)
                ):
                    home_adv = units[off_key][home] - units[def_key][away]
                    away_adv = units[off_key][away] - units[def_key][home]
                    row[f"net_{metric}"] = home_adv - away_adv
                    row[f"home_{off_key}"] = units[off_key][home]
                    row[f"home_{def_key}"] = units[def_key][home]
                    row[f"away_{off_key}"] = units[off_key][away]
                    row[f"away_{def_key}"] = units[def_key][away]
            rows.append(row)
    return pd.DataFrame(rows)
