"""Narrative game analysis generated from the model's own components.

Everything here is derived from numbers the engine already computed —
power ratings, unit ratings, the market line, rest and home field. The
prose states what those components imply and how they add up to the pick,
so a reader can follow the reasoning rather than trust a bare number.
"""
from __future__ import annotations

import pandas as pd

from . import grades, odds

# Unit rating thresholds in EPA/play, roughly: 0.05 is a noticeable edge,
# 0.10 is a strong unit, 0.15+ is elite (or, negative, a real liability).
NOTABLE = 0.05
STRONG = 0.10
ELITE = 0.15


def _grade(value: float) -> str:
    if value >= ELITE:
        return "elite"
    if value >= STRONG:
        return "strong"
    if value >= NOTABLE:
        return "above average"
    if value <= -ELITE:
        return "a serious liability"
    if value <= -STRONG:
        return "well below average"
    if value <= -NOTABLE:
        return "below average"
    return "roughly league average"


def _has_units(row: pd.Series) -> bool:
    return "home_off_pass_epa" in row.index and pd.notna(row.get("home_off_pass_epa"))


def game_script(row: pd.Series, league: str) -> list[str]:
    """Two-or-so paragraphs on how the model expects the game to be played."""
    home, away = row.home_team, row.away_team
    home_favored = row.pred_margin > 0
    fav, dog = (home, away) if home_favored else (away, home)
    prob = row.home_win_prob if home_favored else 1 - row.home_win_prob
    spread = odds.format_spread(row.pred_margin)
    paras = []

    # --- paragraph 1: the shape of the matchup -------------------------
    n = int(row.get("n_teams", 32) or 32)
    ranks = ""
    if pd.notna(row.get("home_rating_rank")):
        h_pct = grades.percentile(int(row.home_rating_rank), n)
        a_pct = grades.percentile(int(row.away_rating_rank), n)
        ranks = (f" Overall, {home} is the {grades.ordinal(int(row.home_rating_rank))}-rated "
                 f"team in the league ({grades.tier(h_pct)}) and {away} is "
                 f"{grades.ordinal(int(row.away_rating_rank))} ({grades.tier(a_pct)}).")

    rating_gap = row.home_rating - row.away_rating
    stronger = home if rating_gap > 0 else away
    gap = abs(rating_gap)
    if gap >= 7:
        shape = (f"a clear talent gap &mdash; {stronger} rates {gap:.1f} points stronger "
                 "before any situational adjustment")
    elif gap >= 3:
        shape = (f"a meaningful but not overwhelming edge for {stronger}, {gap:.1f} points "
                 "of separation before adjustments")
    else:
        shape = (f"two closely matched teams, with only {gap:.1f} points between them "
                 "on raw strength")
    venue = ("Neither side banks a home-field bump at this neutral site."
             if row.neutral else
             f"{home} then adds the home-field bump the model fits at "
             f"{abs(row.hfa_fit):.1f} points.")
    paras.append(
        f"The power ratings see {shape}.{ranks} {venue} Rolling that together, the engine "
        f"projects <strong>{fav} by {spread}</strong> and gives them a {prob:.0%} chance "
        "to win outright."
    )

    # --- paragraph 2: how it plays out, from the unit ratings ----------
    if _has_units(row):
        h_pass_edge = row.home_off_pass_epa - row.away_def_pass_epa
        a_pass_edge = row.away_off_pass_epa - row.home_def_pass_epa
        h_rush_edge = row.home_off_rush_epa - row.away_def_rush_epa
        a_rush_edge = row.away_off_rush_epa - row.home_def_rush_epa

        bits = []
        if abs(h_pass_edge - a_pass_edge) >= NOTABLE:
            better = home if h_pass_edge > a_pass_edge else away
            size, _ = grades.edge_word(h_pass_edge - a_pass_edge)
            bits.append(f"the passing game tilts toward <strong>{better}</strong> "
                        f"&mdash; {size} through the air")
        if abs(h_rush_edge - a_rush_edge) >= NOTABLE:
            better = home if h_rush_edge > a_rush_edge else away
            val = max(h_rush_edge, a_rush_edge)
            size, _ = grades.edge_word(h_rush_edge - a_rush_edge)
            bits.append(
                f"<strong>{better}</strong> has the better ground matchup, {size} on "
                "the run" if val > 0 else
                f"neither run game projects well, though <strong>{better}</strong> is "
                "the less inefficient of the two"
            )
        if not bits:
            bits.append(
                "neither side owns a clear schematic edge &mdash; the unit ratings are "
                "close enough that field position and turnovers likely decide it"
            )

        pace = ("Expect the favorite to be able to play from ahead and lean on the run "
                "late" if abs(row.pred_margin) >= 7 else
                "Expect a game that stays within one score into the fourth quarter, "
                "where late possessions carry outsized weight")
        paras.append(f"On how it plays out: {'; and '.join(bits)}. {pace}.")

    # --- paragraph 3: market context ------------------------------------
    if pd.notna(row.get("spread_line")):
        edge = float(row.pred_margin - row.spread_line)
        side = home if edge > 0 else away
        market_line = odds.format_line(
            home if row.spread_line > 0 else away, row.spread_line
        )
        if abs(edge) >= odds.VALUE_EDGE_PTS:
            verdict = (f"That is a real disagreement: the model wants {abs(edge):.1f} more "
                       f"points on {side} than the market is pricing, which is where the "
                       "value on this game sits")
        elif abs(edge) >= 1.0:
            verdict = (f"The model leans {abs(edge):.1f} points toward {side}, a mild "
                       "difference rather than a strong disagreement")
        else:
            verdict = ("The model and the market are essentially in agreement here, which "
                       "usually means no edge worth chasing")
        paras.append(f"The market has this at {market_line}. {verdict}.")

    key = odds.near_key_number(row.pred_margin, league)
    if key:
        paras.append(
            f"One caution: the projection sits near {key}, a key number in football "
            "margins. Games cluster on it, so small errors in the estimate flip outcomes "
            "against the spread more often than the point difference suggests."
        )
    return paras


ROLE_LABEL = {"QB": "quarterback", "RB": "rushing", "REC": "receiving"}


def key_players(row: pd.Series, players: pd.DataFrame | None) -> list[tuple[str, str]]:
    """(team, sentence) pairs naming the players driving each side's offense,
    with the production that earns them the mention."""
    if players is None or players.empty:
        return []
    out = []
    # players are keyed by the data source's team code, while display uses
    # the full name, so look up by key and label by name
    pairs = [(row.get("home_key", row.home_team), row.home_team),
             (row.get("away_key", row.away_team), row.away_team)]
    for key, team in pairs:
        side = players[players["team"] == key]
        if side.empty:
            continue
        bits = []
        qb = side[side["role"] == "QB"].nlargest(1, "total_epa")
        if not qb.empty:
            q = qb.iloc[0]
            verdict = ("carrying the offense" if q.epa_per_play >= 0.15
                       else "playing well" if q.epa_per_play >= 0.08
                       else "steady but not a difference-maker" if q.epa_per_play >= 0.0
                       else "a drag on the offense")
            bits.append(
                f"QB <strong>{q.player}</strong> has been {verdict}, adding roughly "
                f"{q.epa_per_play * 35:+.1f} points a game over an average quarterback"
            )
        top_rec = side[side["role"] == "REC"].nlargest(2, "total_epa")
        if not top_rec.empty:
            names = " and ".join(f"<strong>{r.player}</strong>" for r in top_rec.itertuples())
            bits.append(f"the passing game runs through {names}")
        # rusher_player_name includes QB scrambles, so drop anyone who is
        # this team's passer before naming a running back
        qbs = set(side.loc[side["role"] == "QB", "player"])
        rb = side[(side["role"] == "RB") & ~side["player"].isin(qbs)].nlargest(1, "total_epa")
        if not rb.empty:
            r = rb.iloc[0]
            quality = ("been efficient" if r.epa_per_play >= 0.02
                       else "held his own" if r.epa_per_play >= -0.05
                       else "struggled to move the ball")
            bits.append(f"lead back <strong>{r.player}</strong> has {quality}")
        if bits:
            out.append((team, "; ".join(bits) + "."))
    return out


def key_factors(row: pd.Series) -> list[dict]:
    """Matchup breakdowns in plain language: who has the edge, how big, and
    how each unit ranks in its league."""
    if not _has_units(row):
        return []
    home, away = row.home_team, row.away_team
    n = int(row.get("unit_n") or row.get("n_teams", 32) or 32)
    out = []
    specs = [
        (home, away, "home_off_pass_epa", "away_def_pass_epa", "passing", "pass defense"),
        (away, home, "away_off_pass_epa", "home_def_pass_epa", "passing", "pass defense"),
        (home, away, "home_off_rush_epa", "away_def_rush_epa", "running", "run defense"),
        (away, home, "away_off_rush_epa", "home_def_rush_epa", "running", "run defense"),
    ]
    for off_team, def_team, off_key, def_key, phase, def_name in specs:
        off_val, def_val = row[off_key], row[def_key]
        net = off_val - def_val
        off_pct = grades.percentile(int(row.get(f"{off_key}_rank", 1)), n)
        def_pct = grades.percentile(int(row.get(f"{def_key}_rank", 1)), n)
        desc, tone = grades.edge_word(net)
        winner = off_team if net > 0 else def_team
        out.append({
            "title": f"{off_team} {phase} vs {def_team} {def_name}",
            "verdict": desc,
            "tone": tone if net > 0 else ("bad" if abs(net) >= 0.09 else "mid"),
            "winner": winner,
            "magnitude": abs(net),
            "text": (
                f"{off_team}'s {phase} attack grades <strong>{grades.grade(off_pct)}</strong> "
                f"({grades.rank_label(int(row[f'{off_key}_rank']), n)}); "
                f"{def_team}'s {def_name} grades <strong>{grades.grade(def_pct)}</strong> "
                f"({grades.rank_label(int(row[f'{def_key}_rank']), n)}). "
                f"That is {desc} for <strong>{winner}</strong>."
            ),
        })
    out.sort(key=lambda f: -f["magnitude"])

    rest = row.get("rest_diff", 0)
    if pd.notna(rest) and abs(rest) >= 3:
        team = home if rest > 0 else away
        out.append({
            "title": "Rest advantage", "verdict": "situational", "tone": "mid",
            "winner": team, "magnitude": 0.0,
            "text": (f"<strong>{team}</strong> comes in with {abs(rest):.0f} more days of "
                     "rest &mdash; worth a fraction of a point, but real for recovery "
                     "and preparation."),
        })
    return out
