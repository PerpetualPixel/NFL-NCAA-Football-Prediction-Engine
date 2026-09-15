"""League pipelines: normalized game tables and walk-forward features.

Every feature row for game G is computed using only games completed before
G's (season, week). That walk-forward discipline is what makes the backtest
honest — no rating ever sees the game it is predicting.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import rosters
from .config import LeagueConfig, LEAGUES
from .data import ingest
from .features.ratings import recency_weights, solve_ratings

FCS_BUCKET = "NON-FBS"

# How many games a team must have played before its usage table means
# anything — and, equivalently, before a new season can describe its own
# players rather than deferring to the last one.
USAGE_MIN_GAMES = 3


def load_league_inputs(
    league: str, refresh: bool = False, recent_only: bool = False,
    with_players: bool = False, history_seasons: int | None = None,
):
    """Games table, (NFL) unit stats, and the availability context.

    recent_only trims the play-by-play download to the rating window for fast
    CI site builds; with_players also returns per-player production for the
    key-players breakdown."""
    games = load_games(league, refresh=refresh)
    unit_stats = players = None
    seasons = sorted(games.loc[games["completed"], "season"].unique().tolist())
    if recent_only:
        span = history_seasons or (LEAGUES[league].rating_window_seasons + 1)
        seasons = seasons[-span:]
    availability = None
    if league == "nfl":
        pbp = ingest.load_nfl_pbp(seasons, refresh_latest=refresh)
        unit_stats = nfl_unit_game_stats(pbp)
        injuries = ingest.load_nfl_injuries(seasons, refresh_latest=refresh)
        weekly_rosters = ingest.load_nfl_weekly_roster_history(
            seasons, refresh_latest=refresh)
        availability = {
            "qb_values": nfl_qb_game_value(pbp),
            "injury": nfl_injury_burden(
                injuries, ingest.load_nfl_snaps(seasons, refresh_latest=refresh),
                weekly_rosters,
            ),
            # the raw reports, so the site can tell when a week's final
            # (game-status) report has been filed
            "injury_reports": injuries,
        }
        if with_players:
            stat_season, current = _player_season(pbp, games)
            roster = rosters.build("nfl", current, _current_week(games, current))
            availability["roster"] = roster
            players = _current_players(
                nfl_player_production(pbp, stat_season), roster, stat_season, current)
            availability["usage"] = _current_players(
                nfl_player_usage(pbp, stat_season), roster, stat_season, current)
    else:
        plays = ingest.load_ncaa_plays(seasons, refresh_latest=refresh)
        if not plays.empty:
            # rate and rank units among FBS teams only, so a grade means
            # "against the level this site covers"
            from . import teams as _teams
            fbs = _teams.ncaa_fbs_teams()
            if fbs:
                plays = plays[plays["team"].isin(fbs) & plays["opponent"].isin(fbs)]
            unit_stats = ncaa_unit_game_stats(plays)
            if with_players:
                stat_season, current = _player_season(plays, games)
                roster = rosters.build("ncaa", current, _current_week(games, current))
                players = _current_players(
                    ncaa_player_production(plays, stat_season), roster,
                    stat_season, current)
                availability = {
                    "roster": roster,
                    "usage": _current_players(
                        ncaa_player_usage(plays, stat_season), roster,
                        stat_season, current),
                }
    if with_players:
        return games, unit_stats, players, availability
    return games, unit_stats, availability


def ncaa_normalize_plays(plays: pd.DataFrame) -> pd.DataFrame:
    """Reshape college play rows into the same per-play frame the NFL side
    produces: one row per play with play_type, yards, and a success flag.

    College play-by-play is not published with EPA, so efficiency is measured
    by success rate — the standard down-based definition of a successful
    play — which is stable and opponent-adjustable in the same way.
    """
    if plays.empty:
        return plays
    df = plays.copy()
    is_pass = df["completion_yds"].notna() | df["incompletion_player"].notna()
    is_rush = df["rush_yds"].notna() & ~is_pass
    df = df[is_pass | is_rush].copy()
    df["play_type"] = np.where(is_pass[is_pass | is_rush], "pass", "run")
    df["yards_gained"] = (
        df["completion_yds"].fillna(0) + df["rush_yds"].where(df["play_type"] == "run", 0).fillna(0)
    )
    # success: 50% of needed yards on 1st down, 70% on 2nd, 100% on 3rd/4th
    need = df["distance"].astype(float)
    frac = df["down"].map({1: 0.5, 2: 0.7, 3: 1.0, 4: 1.0}).fillna(1.0)
    df["success"] = (df["yards_gained"] >= need * frac).astype(float)
    return df


def ncaa_unit_game_stats(plays: pd.DataFrame) -> pd.DataFrame:
    """Per (game, offense): pass/rush efficiency, matching the NFL columns so
    the rating solve and the site render identically for both leagues."""
    df = ncaa_normalize_plays(plays)
    if df.empty:
        return pd.DataFrame()
    df["is_pass"] = df["play_type"].eq("pass")
    grp = df.groupby(["game_id", "season", "week", "team", "opponent"])
    out = grp.apply(
        lambda g: pd.Series({
            "pass_epa": g.loc[g["is_pass"], "success"].mean(),
            "rush_epa": g.loc[~g["is_pass"], "success"].mean(),
        }),
        include_groups=False,
    ).reset_index().rename(columns={"team": "posteam", "opponent": "defteam"})
    # drop units with too few plays to mean anything
    counts = grp.size().reset_index(name="plays").rename(
        columns={"team": "posteam", "opponent": "defteam"})
    out = out.merge(counts, on=["game_id", "season", "week", "posteam", "defteam"])
    return out[out["plays"] >= 20].drop(columns=["plays"])


def ncaa_player_production(plays: pd.DataFrame, season: int, min_plays: int = 25) -> pd.DataFrame:
    """Per-player production for the key-players breakdown, in the same shape
    as the NFL version (team, player, role, plays, epa_per_play, total_epa)."""
    df = ncaa_normalize_plays(plays)
    if df.empty:
        return pd.DataFrame()
    df = df[df["season"] == season]
    roles = [("completion_player", "QB"), ("rush_player", "RB"), ("reception_player", "REC")]
    frames = []
    for col, role in roles:
        # group on the athlete id rather than the name: the id is what the
        # current roster can be checked against, and the roster's spelling of
        # the name is the better one anyway
        id_col = f"{col}_id"
        sub = df[df[col].notna()]
        if sub.empty or id_col not in sub.columns:
            continue
        sub = sub[sub[id_col].notna()]
        if sub.empty:
            continue
        agg = (
            sub.assign(**{id_col: sub[id_col].astype("Int64").astype(str)})
            .groupby(["team", id_col])
            .agg(player=(col, "first"), plays=("success", "size"),
                 epa_per_play=("success", "mean"), total_epa=("yards_gained", "sum"))
            .reset_index()
            .rename(columns={id_col: "player_id"})
        )
        agg["role"] = role
        frames.append(agg[agg["plays"] >= min_plays])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def nfl_player_production(pbp: pd.DataFrame, season: int, min_plays: int = 25) -> pd.DataFrame:
    """Per-player EPA production for the latest season, by role.

    Returns columns: team, player, role, plays, epa_per_play, total_epa.
    Used to name the handful of players actually driving each unit rating.
    """
    recent = pbp[(pbp["season"] == season) & pbp["epa"].notna()]
    roles = [
        ("passer_player", "pass", "QB"),
        ("rusher_player", "run", "RB"),
        ("receiver_player", "pass", "REC"),
    ]
    frames = []
    for stem, play_type, role in roles:
        col, id_col = f"{stem}_name", f"{stem}_id"
        sub = recent[recent[col].notna() & recent["play_type"].eq(play_type)]
        if sub.empty or id_col not in sub.columns:
            continue
        # keyed by gsis id, so the player can be looked up on today's roster
        sub = sub[sub[id_col].notna()]
        if sub.empty:
            continue
        agg = (
            sub.groupby(["posteam", id_col])
            .agg(player=(col, "first"), plays=("epa", "size"),
                 total_epa=("epa", "sum"), epa_per_play=("epa", "mean"),
                 success=("success", "mean"))
            .reset_index()
            .rename(columns={"posteam": "team", id_col: "player_id"})
        )
        agg["role"] = role
        frames.append(agg[agg["plays"] >= min_plays])
    if not frames:
        return pd.DataFrame(columns=["team", "player", "player_id", "role", "plays",
                                     "epa_per_play", "total_epa", "success"])
    return pd.concat(frames, ignore_index=True)


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
            "game_id", "season", "week", "gameday", "gametime", "weekday",
            "home_team", "away_team",
            "home_score", "away_score", "margin", "neutral", "completed",
            "game_type", "spread_line", "total_line", "home_rest", "away_rest",
            "home_moneyline", "away_moneyline", "home_spread_odds", "away_spread_odds",
            "home_qb_name", "away_qb_name", "home_qb_id", "away_qb_id",
            "home_coach", "away_coach",
            "div_game", "roof", "temp", "wind", "stadium", "espn",
        ]
        keep = [c for c in keep if c in df.columns]
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
         "season_type", "home_conference", "away_conference"]
    ].rename(columns={"season_type": "game_type"})
    odds_df = ingest.ncaa_game_odds(out, refresh=refresh)
    out = out.merge(odds_df, on="game_id", how="left")
    for col in ["spread_line", "open_spread_line", "total_line",
                "home_moneyline", "away_moneyline",
                "home_spread_odds", "away_spread_odds"]:
        if col not in out.columns:
            out[col] = np.nan
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

def _player_season(plays: pd.DataFrame, games: pd.DataFrame | None = None,
                   min_team_games: int = USAGE_MIN_GAMES) -> tuple[int, int]:
    """(season the production numbers come from, season being played).

    A season three games old cannot describe how a team distributes the ball
    — one blowout and one weather game is not a target share — so until every
    club has played a few, production falls back to the last full season.
    The two seasons are returned separately because they are different
    claims: how a player has produced is a fact about last autumn, whereas
    who he plays for is a fact about today, and the site has to be able to
    say which is which rather than run them together.

    Both leagues use the same rule. Measuring it in games rather than raw
    plays matters: a single college Saturday clears any play count, and
    would otherwise have the college pages describing target shares off one
    afternoon while the NFL pages still used last season.
    """
    counts = plays.groupby("season").size()
    seasons = sorted(int(x) for x in counts.index) if len(counts) else []
    played = seasons[-1] if seasons else 0
    current = played
    if games is not None and len(games) and "season" in games.columns:
        current = max(current, int(games["season"].max()))
    stat_season = played
    if _too_early(games, current, min_team_games):
        earlier = [x for x in seasons if x < current]
        if earlier:
            stat_season = earlier[-1]
    return stat_season, current


def _too_early(games: pd.DataFrame | None, season: int, min_team_games: int) -> bool:
    """Whether the season is too young to describe players by."""
    if games is None or not len(games) or "season" not in games.columns:
        return False
    done = games[(games["season"] == season) & games["completed"].astype(bool)]
    if done.empty:
        return True
    played = pd.concat([done["home_team"], done["away_team"]]).value_counts()
    return bool(played.median() < min_team_games)


def _current_week(games: pd.DataFrame | None, season: int) -> int | None:
    """The week of `season` being played now: the earliest one still holding
    a game that has not finished, or the last one if the season is over."""
    if games is None or not len(games) or "season" not in games.columns:
        return None
    this = games[games["season"] == season]
    if this.empty or "week" not in this.columns:
        return None
    pending = this[~this["completed"].astype(bool)] if "completed" in this else this
    weeks = (pending if not pending.empty else this)["week"].dropna()
    if weeks.empty:
        return None
    return int(weeks.min() if not pending.empty else weeks.max())


def _current_players(frame: pd.DataFrame | None, roster, stat_season: int,
                     current_season: int) -> pd.DataFrame | None:
    """Put a table of players through the current roster before anyone sees it.

    Players who have moved on are removed, the rest carry today's status and
    the roster's spelling of their name and position, and every row records
    which season its numbers came from so the prose can say so rather than
    implying they are this week's.
    """
    if frame is None or frame.empty:
        return frame
    df = frame.copy()
    df["stat_season"] = stat_season
    df["stats_are_current"] = stat_season == current_season
    if roster is None or getattr(roster, "empty", True):
        # nothing to check against: say so rather than vouching for the names
        df["availability"] = rosters.UNKNOWN
        df["roster_note"] = "roster not verified"
        df["roster_verified"] = False
        df["depth_rank"] = None
        return df
    return roster.annotate(df)


def _qb_state(qb_values, window, cfg, season, week):
    """As-of quarterback ratings and each team's usual starter.

    Returns (value by quarterback id, usual starter id by team). Values are
    recency-weighted EPA per dropback, shrunk toward league average so a
    quarterback with two good games is not rated above a proven starter.
    """
    if qb_values is None or qb_values.empty:
        return {}, {}
    prior = qb_values.merge(window[["game_id"]], on="game_id", how="inner")
    if prior.empty:
        return {}, {}
    w = recency_weights(prior["season"], prior["week"], season, week,
                        cfg.weekly_decay, cfg.prior_season_weight)
    prior = prior.assign(_w=w * prior["dropbacks"])
    grp = prior.groupby("passer_player_id")
    weighted = grp.apply(lambda g: (g["epa"] * g["_w"]).sum(), include_groups=False)
    mass = grp["_w"].sum()
    # shrink toward zero (league average EPA) by effective dropbacks
    values = (weighted / (mass + QB_PRIOR_DROPBACKS)).to_dict()

    recent = prior[prior["season"] >= season - 1]
    starters = {}
    if not recent.empty:
        idx = recent.groupby("team")["_w"].idxmax()
        starters = dict(zip(recent.loc[idx, "team"], recent.loc[idx, "passer_player_id"]))
    return values, starters


def _availability_features(g, qb_now: dict, starters: dict, injury_now: dict) -> dict:
    """Who is actually playing: quarterback quality, and whether this is the
    team's usual starter or a replacement."""
    out = {"qb_gap": 0.0, "qb_change": 0.0, "inj_diff": 0.0}
    if qb_now:
        sides = {}
        for side, team in (("home", g.home_team), ("away", g.away_team)):
            qb_id = getattr(g, f"{side}_qb_id", None)
            value = qb_now.get(qb_id) if isinstance(qb_id, str) else None
            usual = qb_now.get(starters.get(team))
            sides[side] = (
                value if value is not None else (usual if usual is not None else 0.0),
                # negative when a team is starting someone worse than usual
                (value - usual) if (value is not None and usual is not None) else 0.0,
            )
        out["qb_gap"] = sides["home"][0] - sides["away"][0]
        out["qb_change"] = sides["home"][1] - sides["away"][1]
    if injury_now:
        # positive favors the home team: the away side is missing more
        out["inj_diff"] = (injury_now.get(g.away_team, 0.0)
                           - injury_now.get(g.home_team, 0.0))
    return out


def _weather_features(g) -> dict:
    """Conditions only matter through how they suppress the passing game, so
    weather enters as an interaction rather than a direct push either way."""
    roof = str(getattr(g, "roof", "") or "").lower()
    indoors = roof in {"dome", "closed"}
    wind = 0.0 if indoors else float(getattr(g, "wind", 0.0) or 0.0)
    temp = 70.0 if indoors else float(getattr(g, "temp", 60.0) or 60.0)
    return {"wind": wind, "cold": max(0.0, 50.0 - temp) / 10.0, "indoors": float(indoors)}


def _rank_of(team: str, ratings: dict[str, float]) -> int:
    """1 = best in the league on this rating (higher is better everywhere,
    including defense, where the solve measures EPA suppressed)."""
    return 1 + sum(1 for v in ratings.values() if v > ratings[team])


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
    availability: dict | None = None,
) -> pd.DataFrame:
    """For each game, attach as-of-kickoff ratings and derived features.

    availability carries the who-is-playing context (quarterback values and
    injury burden); it is optional so the pipeline still runs without it.
    """
    cfg = LEAGUES[league]
    qb_values = (availability or {}).get("qb_values")
    injury = (availability or {}).get("injury")
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

        qb_now, starters = _qb_state(qb_values, window, cfg, season, week)
        injury_now = {}
        if injury is not None and not injury.empty:
            wk = injury[(injury["season"] == season) & (injury["week"] == week)]
            injury_now = dict(zip(wk["team"], wk["inj_burden"]))

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
                "home_rating_rank": _rank_of(home, power),
                "away_rating_rank": _rank_of(away, power),
                "n_teams": len(power),
            }
            if hasattr(g, "home_rest") and pd.notna(g.home_rest):
                row["rest_diff"] = g.home_rest - g.away_rest
            else:
                row["rest_diff"] = 0.0
            row.update(_availability_features(g, qb_now, starters, injury_now))
            row.update(_weather_features(g))
            for metric in ["pass_epa", "rush_epa"]:
                off_key, def_key = f"off_{metric}", f"def_{metric}"
                if off_key in units and all(
                    t in units[off_key] and t in units[def_key] for t in (home, away)
                ):
                    home_adv = units[off_key][home] - units[def_key][away]
                    away_adv = units[off_key][away] - units[def_key][home]
                    row[f"net_{metric}"] = home_adv - away_adv
                    for side, team in (("home", home), ("away", away)):
                        for key in (off_key, def_key):
                            row[f"{side}_{key}"] = units[key][team]
                            # rank makes the rating legible: 1 = best in league
                            row[f"{side}_{key}_rank"] = _rank_of(team, units[key])
                    # unit ratings cover a different set of teams than the
                    # power ratings, so ranks need their own denominator
                    row["unit_n"] = len(units[off_key])
            # wind only matters through the passing matchup, so the
            # interaction is formed once that matchup is known
            row["wind_pass"] = row["wind"] / 10.0 * row.get("net_pass_epa", 0.0)
            rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Availability context: who is actually playing, and in what conditions
# ---------------------------------------------------------------------------

# A replacement-level quarterback is worth several points a game. The model
# was blind to this: a team starting its backup carried the same rating as
# one starting a healthy franchise passer.
QB_MIN_DROPBACKS = 40
QB_PRIOR_DROPBACKS = 120.0   # shrink thin samples toward league average

# How much a missing player matters, by position group. Weighted by the
# snap share they had been playing, so a starting corner counts and a
# third-string one barely does.
POSITION_WEIGHT = {
    "QB": 4.0, "LT": 1.6, "T": 1.4, "WR": 1.2, "CB": 1.2, "EDGE": 1.3,
    "DE": 1.3, "OLB": 1.1, "S": 1.0, "G": 1.0, "C": 1.0, "TE": 0.9,
    "RB": 0.9, "DT": 1.0, "LB": 0.9, "ILB": 0.8, "FS": 1.0, "SS": 1.0,
}
OUT_WEIGHT = {"Out": 1.0, "Doubtful": 0.75, "Questionable": 0.25}


def nfl_qb_game_value(pbp: pd.DataFrame) -> pd.DataFrame:
    """Per (game, quarterback): dropbacks and EPA per dropback."""
    plays = pbp[pbp["passer_player_id"].notna() & pbp["epa"].notna()]
    if plays.empty:
        return pd.DataFrame()
    return (
        plays.groupby(["game_id", "season", "week", "posteam", "passer_player_id"])
        .agg(dropbacks=("epa", "size"), epa=("epa", "mean"))
        .reset_index()
        .rename(columns={"posteam": "team"})
    )


def nfl_injury_burden(injuries: pd.DataFrame, snaps: pd.DataFrame,
                      weekly_rosters: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per (season, week, team): a weighted count of who is missing.

    Availability is reported before kickoff, so this is legitimately known
    at prediction time. Each absence is weighted by the player's recent snap
    share and by position, then summed.

    Two things count as missing, and the second used to be invisible:

    * a game-status designation on the week's injury report (Out, Doubtful,
      Questionable), weighted by how firm it is; and
    * being on a reserve list that week — injured reserve, PUP, the exempt
      list. A player put on IR in August never appears on a game-status
      report again, because he was never in that week's game plan, so a
      team missing its best receiver for the season registered no injury
      burden at all. In week one, when no report has been filed, this is
      the *only* signal there is.

    Reserve lists are filed days ahead, so counting them keeps the feature
    walk-forward honest. Game-day inactives are deliberately not counted:
    they are published ninety minutes before kickoff, long after a pick is.
    """
    frames = []
    if injuries is not None and not injuries.empty:
        inj = injuries[injuries["report_status"].isin(OUT_WEIGHT)].copy()
        if not inj.empty:
            inj["weight"] = inj["report_status"].map(OUT_WEIGHT).fillna(0.0)
            frames.append(inj[["season", "week", "team", "full_name",
                               "position", "weight"]])
    reserve = _nfl_reserve_absences(weekly_rosters)
    if reserve is not None and not reserve.empty:
        frames.append(reserve)
    if not frames:
        return pd.DataFrame(columns=["season", "week", "team", "inj_burden"])
    out = pd.concat(frames, ignore_index=True)
    # a player on injured reserve who is also listed Out is one absence
    out = out.sort_values("weight", ascending=False).drop_duplicates(
        subset=["season", "week", "team", "full_name"], keep="first")

    share = pd.Series(0.55, index=out.index)  # default for an unmatched name
    if snaps is not None and not snaps.empty:
        snaps = snaps.copy()
        snaps["snap_pct"] = snaps[["offense_pct", "defense_pct"]].max(axis=1)
        # a player's typical role, averaged over the season to date
        role = (snaps.groupby(["season", "team", "player"])["snap_pct"]
                .mean().rename("snap_share").reset_index())
        out = out.merge(
            role, left_on=["season", "team", "full_name"],
            right_on=["season", "team", "player"], how="left",
        )
        share = out["snap_share"].fillna(0.55).clip(0, 1)

    pos_w = out["position"].map(POSITION_WEIGHT).fillna(0.7)
    out["burden"] = share * pos_w * out["weight"]
    return (out.groupby(["season", "week", "team"])["burden"].sum()
            .rename("inj_burden").reset_index())


def _nfl_reserve_absences(weekly_rosters: pd.DataFrame | None) -> pd.DataFrame | None:
    """Players a club had on a reserve list in a given week, as absences.

    A reserve listing is a full absence, so it carries the same weight an
    "Out" designation does.
    """
    if weekly_rosters is None or weekly_rosters.empty:
        return None
    if not {"status", "week", "team", "season"} <= set(weekly_rosters.columns):
        return None
    df = weekly_rosters[
        weekly_rosters["status"].astype(str).str.upper().isin(rosters.RESERVE_STATUSES)
    ].copy()
    if df.empty:
        return None
    # the depth-chart position is the more specific of the two ("LT", not "T")
    pos = pd.Series(None, index=df.index, dtype=object)
    if "depth_chart_position" in df.columns:
        pos = df["depth_chart_position"]
    if "position" in df.columns:
        pos = pos.fillna(df["position"])
    df["position"] = pos
    df["weight"] = OUT_WEIGHT["Out"]
    return df[["season", "week", "team", "full_name", "position", "weight"]]


# ---------------------------------------------------------------------------
# Usage: who actually gets the ball
# ---------------------------------------------------------------------------
# Ratings say which unit is better. They do not say who to watch. Usage
# answers that: the share of targets and carries each player commands, how
# far downfield they work, and what they do with it — the difference between
# "their passing game is good" and "their number one runs 27% of the routes
# targeted and averages 9 yards a catch".



def nfl_player_usage(pbp: pd.DataFrame, season: int) -> pd.DataFrame:
    """Per team and player: role, volume per game, share, and efficiency."""
    df = pbp[(pbp["season"] == season) & pbp["posteam"].notna()]
    if df.empty:
        return pd.DataFrame()

    games = df.groupby("posteam")["game_id"].nunique().rename("team_games")

    # --- receiving: a named receiver on a pass play is a target ----------
    tgt = df[df["receiver_player_id"].notna() & df["pass_attempt"].eq(1)]
    rec = (tgt.groupby(["posteam", "receiver_player_id"])
           .agg(player=("receiver_player_name", "first"),
                targets=("pass_attempt", "sum"),
                catches=("complete_pass", "sum"),
                rec_yards=("yards_gained", "sum"),
                air=("air_yards", "mean"),
                yac=("yards_after_catch", "mean"),
                epa=("epa", "mean"))
           .reset_index().rename(columns={"posteam": "team",
                                          "receiver_player_id": "player_id"}))
    rec["role"] = "REC"
    team_targets = rec.groupby("team")["targets"].transform("sum")
    rec["share"] = rec["targets"] / team_targets

    # --- rushing ---------------------------------------------------------
    car = df[df["rusher_player_id"].notna() & df["rush_attempt"].eq(1)]
    run = (car.groupby(["posteam", "rusher_player_id"])
           .agg(player=("rusher_player_name", "first"),
                carries=("rush_attempt", "sum"),
                rush_yards=("yards_gained", "sum"),
                epa=("epa", "mean"))
           .reset_index().rename(columns={"posteam": "team",
                                          "rusher_player_id": "player_id"}))
    run["role"] = "RUSH"
    team_carries = run.groupby("team")["carries"].transform("sum")
    run["share"] = run["carries"] / team_carries

    out = pd.concat([rec, run], ignore_index=True).merge(games, left_on="team",
                                                         right_index=True, how="left")
    out = out[out["team_games"] >= USAGE_MIN_GAMES]
    for col, per in (("targets", "targets_pg"), ("catches", "catches_pg"),
                     ("rec_yards", "rec_yards_pg"), ("carries", "carries_pg"),
                     ("rush_yards", "rush_yards_pg")):
        if col in out.columns:
            out[per] = out[col] / out["team_games"]
    # depth-chart position within each role, by share
    out["rank"] = out.groupby(["team", "role"])["share"].rank(ascending=False,
                                                              method="first")
    return out


def ncaa_player_usage(plays: pd.DataFrame, season: int) -> pd.DataFrame:
    """The college equivalent, from the play-level feed."""
    df = ncaa_normalize_plays(plays)
    if df.empty:
        return pd.DataFrame()
    df = df[df["season"] == season]
    if df.empty:
        return pd.DataFrame()
    games = df.groupby("team")["game_id"].nunique().rename("team_games")

    tgt = df[df["reception_player_id"].notna()]
    tgt = tgt.assign(reception_player_id=tgt["reception_player_id"].astype("Int64").astype(str))
    rec = (tgt.groupby(["team", "reception_player_id"])
           .agg(player=("reception_player", "first"), catches=("success", "size"),
                rec_yards=("reception_yds", "sum"), epa=("success", "mean"))
           .reset_index().rename(columns={"reception_player_id": "player_id"}))
    rec["targets"] = rec["catches"]      # the feed records completions only
    rec["role"] = "REC"
    rec["share"] = rec["catches"] / rec.groupby("team")["catches"].transform("sum")

    car = df[df["rush_player_id"].notna()]
    car = car.assign(rush_player_id=car["rush_player_id"].astype("Int64").astype(str))
    run = (car.groupby(["team", "rush_player_id"])
           .agg(player=("rush_player", "first"), carries=("success", "size"),
                rush_yards=("rush_yds", "sum"), epa=("success", "mean"))
           .reset_index().rename(columns={"rush_player_id": "player_id"}))
    run["role"] = "RUSH"
    run["share"] = run["carries"] / run.groupby("team")["carries"].transform("sum")

    out = pd.concat([rec, run], ignore_index=True).merge(games, left_on="team",
                                                         right_index=True, how="left")
    out = out[out["team_games"] >= USAGE_MIN_GAMES]
    for col, per in (("catches", "catches_pg"), ("rec_yards", "rec_yards_pg"),
                     ("carries", "carries_pg"), ("rush_yards", "rush_yards_pg")):
        if col in out.columns:
            out[per] = out[col] / out["team_games"]
    # the college feed records completions, not attempts, so these are
    # receptions rather than true targets and the prose must not claim otherwise
    out["targets_pg"] = None
    out["completions_only"] = True
    out["rank"] = out.groupby(["team", "role"])["share"].rank(ascending=False,
                                                              method="first")
    return out
