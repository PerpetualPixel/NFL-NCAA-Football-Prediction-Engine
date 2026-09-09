"""Narrative game analysis generated from the model's own components.

Everything here is derived from numbers the engine already computed —
power ratings, unit ratings, the market line, rest and home field. The
prose states what those components imply and how they add up to the pick,
so a reader can follow the reasoning rather than trust a bare number.
"""
from __future__ import annotations

import pandas as pd

from . import grades, odds, rosters, teams, weather

# Unit rating thresholds in EPA/play, roughly: 0.05 is a noticeable edge,
# 0.10 is a strong unit, 0.15+ is elite (or, negative, a real liability).
NOTABLE = 0.05
STRONG = 0.10
ELITE = 0.15


# Whether a player is expected to take the field, and how the numbers beside
# their name should be described, are separate questions and are answered
# separately. A player is only ever presented as current after the roster has
# confirmed them; production from a previous season is always labelled as
# such rather than written in the present tense.

# Below this share of a team's targets or carries a player is not part of the
# distribution, they are a rounding error — a quarterback's one reception, a
# fourth receiver's two targets — and naming them makes the hierarchy harder
# to read rather than more complete.
MINOR_SHARE = 0.04


def _availability(row) -> str:
    value = getattr(row, "availability", None)
    return value if isinstance(value, str) else rosters.UNKNOWN


def _availability_suffix(row) -> str:
    """The parenthetical that keeps a name honest: on injured reserve, out
    with an ankle, unconfirmed by the roster. Empty for an available player,
    who needs no qualifier."""
    note = getattr(row, "roster_note", None)
    if _availability(row) == rosters.AVAILABLE or not isinstance(note, str) or not note:
        return ""
    return f" &mdash; <em>{note}</em>"


def _is_out(row) -> bool:
    return _availability(row) in (rosters.OUT, rosters.RESERVE)


def _plays_this_week(frame: pd.DataFrame) -> pd.DataFrame:
    """The rows for players who are expected to play."""
    if frame is None or frame.empty or "availability" not in frame.columns:
        return frame
    return frame[~frame["availability"].isin([rosters.OUT, rosters.RESERVE])]


def _ruled_out(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty or "availability" not in frame.columns:
        return frame.iloc[0:0] if frame is not None else frame
    return frame[frame["availability"].isin([rosters.OUT, rosters.RESERVE])]


def _stats_are_current(frame: pd.DataFrame) -> bool:
    if frame is None or frame.empty or "stats_are_current" not in frame.columns:
        return True
    return bool(frame["stats_are_current"].iloc[0])


def _stat_season(frame: pd.DataFrame) -> int | None:
    if frame is None or frame.empty or "stat_season" not in frame.columns:
        return None
    value = frame["stat_season"].iloc[0]
    return int(value) if pd.notna(value) else None


def _provenance(frame: pd.DataFrame, roster=None) -> str:
    """One line saying where these names and numbers come from and when.

    A reader deciding whether to trust a target share needs to know it is
    last season's, and that the man it is attached to is on this week's
    roster. Both facts go on the page.
    """
    bits = []
    if not _stats_are_current(frame):
        season = _stat_season(frame)
        bits.append(f"Usage is {season} production" if season
                    else "Usage is last season's production")
    else:
        bits.append("Usage is this season to date")
    if roster is not None and not getattr(roster, "empty", True):
        state = "verified" if roster.fresh else "last verified"
        bits.append(f"roster {state} {roster.as_of_text()}")
    else:
        bits.append("roster could not be verified for this build")
    return "; ".join(bits) + "."


def _out_sentence(frame: pd.DataFrame, limit: int = 3) -> str:
    """Who among these players will not be on the field."""
    gone = _ruled_out(frame)
    if gone is None or gone.empty:
        return ""
    if "share" in gone.columns:
        gone = gone[gone["share"] >= MINOR_SHARE].nlargest(limit, "share")
    elif "plays" in gone.columns and "plays" in frame.columns and len(frame):
        # the production table has no share; a fifth of the busiest player's
        # volume is the same idea — enough of a role for the absence to matter
        floor = float(frame["plays"].max()) * MINOR_SHARE * 5
        gone = gone[gone["plays"] >= floor].nlargest(limit, "plays")
    else:
        gone = gone.head(limit)
    if gone.empty:
        return ""
    named = []
    for r in gone.itertuples():
        note = getattr(r, "roster_note", None)
        tail = f" ({note})" if isinstance(note, str) and note else ""
        named.append(f"<strong>{r.player}</strong>{tail}")
    if not named:
        return ""
    return f" Not available: {', '.join(named)} &mdash; that work is going elsewhere."


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
        market_line = odds.format_market(home, away, row.spread_line)
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


def key_players(row: pd.Series, players: pd.DataFrame | None,
                roster=None) -> list[tuple[str, str]]:
    """(team, sentence) pairs naming the players driving each side's offense,
    with the production that earns them the mention.

    Only players the current roster still places on the team are named, and
    where the production is a previous season's the sentence says so instead
    of writing last year in the present tense.
    """
    if players is None or players.empty:
        return []
    current = _stats_are_current(players)
    season = _stat_season(players)
    when = "" if current else (f" in {season}" if season else " last season")
    out = []
    # players are keyed by the data source's team code, while display uses
    # the full name, so look up by key and label by name
    pairs = [(row.get("home_key", row.home_team), row.home_team),
             (row.get("away_key", row.away_team), row.away_team)]
    for key, team in pairs:
        side = players[players["team"] == key]
        if side.empty:
            continue
        playing = _plays_this_week(side)
        bits = []
        qb = playing[playing["role"] == "QB"].nlargest(1, "total_epa")
        if not qb.empty:
            q = qb.iloc[0]
            verdict = ("carrying the offense" if q.epa_per_play >= 0.15
                       else "playing well" if q.epa_per_play >= 0.08
                       else "steady but not a difference-maker" if q.epa_per_play >= 0.0
                       else "a drag on the offense")
            bits.append(
                f"QB <strong>{q.player}</strong> was {verdict}{when}, worth roughly "
                f"{q.epa_per_play * 35:+.1f} points a game over an average quarterback"
                if when else
                f"QB <strong>{q.player}</strong> has been {verdict}, adding roughly "
                f"{q.epa_per_play * 35:+.1f} points a game over an average quarterback"
            )
        top_rec = playing[playing["role"] == "REC"].nlargest(2, "total_epa")
        if not top_rec.empty:
            names = " and ".join(f"<strong>{r.player}</strong>" for r in top_rec.itertuples())
            bits.append(f"the passing game ran through {names}{when}" if when
                        else f"the passing game runs through {names}")
        # rusher_player_name includes QB scrambles, so drop anyone who is
        # this team's passer before naming a running back
        qbs = set(side.loc[side["role"] == "QB", "player"])
        rb = playing[(playing["role"] == "RB")
                     & ~playing["player"].isin(qbs)].nlargest(1, "total_epa")
        if not rb.empty:
            r = rb.iloc[0]
            quality = ("was efficient" if r.epa_per_play >= 0.02
                       else "held his own" if r.epa_per_play >= -0.05
                       else "struggled to move the ball") if when else (
                       "been efficient" if r.epa_per_play >= 0.02
                       else "held his own" if r.epa_per_play >= -0.05
                       else "struggled to move the ball")
            lead_in = "lead back <strong>%s</strong>" % r.player
            bits.append(f"{lead_in} {quality}{when}" if when
                        else f"{lead_in} has {quality}")
        missing = _out_sentence(side, limit=2).strip()
        if bits:
            out.append((team, "; ".join(bits) + "." + (f" {missing}" if missing else "")))
        elif missing:
            out.append((team, missing))
    return out


def availability_note(row: pd.Series, roster=None) -> str:
    """Plain-language read on quarterbacks and injuries for this game."""
    bits = []
    home_key = row.get("home_key", row.home_team)
    away_key = row.get("away_key", row.away_team)
    starters = _starting_quarterbacks(roster, home_key, away_key,
                                      row.home_team, row.away_team)
    if starters:
        bits.append(starters)
    qb_change = row.get("qb_change", 0.0)
    if pd.notna(qb_change) and abs(qb_change) >= 0.04:
        side = row.home_team if qb_change < 0 else row.away_team
        bits.append(
            f"<strong>{side}</strong> is not starting its usual quarterback, or is "
            "starting one playing below that level &mdash; the model marks them down "
            "accordingly"
        )
    qb_gap = row.get("qb_gap", 0.0)
    if pd.notna(qb_gap) and abs(qb_gap) >= 0.08:
        better = row.home_team if qb_gap > 0 else row.away_team
        bits.append(f"<strong>{better}</strong> has the clear edge at quarterback")
    inj = row.get("inj_diff", 0.0)
    if pd.notna(inj) and abs(inj) >= 1.5:
        healthier = row.home_team if inj > 0 else row.away_team
        bits.append(
            f"<strong>{healthier}</strong> is the healthier side this week by the "
            "injury report and the reserve lists, weighted by who is actually missing"
        )
    if not bits:
        return ("Both sides are starting their usual quarterbacks with no notable "
                "injury gap.")
    return _sentence_case("; ".join(bits)) + "."


def _starting_quarterbacks(roster, home_key, away_key, home, away) -> str:
    """Name the quarterbacks the clubs themselves list first this week.

    Whose name is on top of the depth chart is a fact about today. Who threw
    the most passes last season is not the same fact, and in September they
    are routinely different people.
    """
    if roster is None or getattr(roster, "empty", True) or roster.league != "nfl":
        return ""
    named = []
    for key, team in ((home_key, home), (away_key, away)):
        listed = roster.starters(key, "QB", limit=1)
        if listed:
            named.append(f"{team} list <strong>{listed[0].name}</strong> at quarterback")
    if not named:
        return ""
    return " and ".join(named)


def _sentence_case(text: str) -> str:
    return text[0].upper() + text[1:] if text else text


def key_factors(row: pd.Series) -> list[dict]:
    """Matchup breakdowns in plain language: who has the edge, how big, and
    how each unit ranks in its league."""
    if not _has_units(row):
        return []
    home, away = row.home_team, row.away_team
    n = _team_count(row)
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


def pixel_rationale(pick: dict, preds: pd.DataFrame, league: str,
                    roster=None) -> list[str]:
    """The case for a Pixel's Pick: why these legs, why this price.

    A high-confidence tag is only worth something if the reasoning behind it
    is visible, so this spells out the model's probability against the price,
    what the matchup says, and — for a parlay — why the legs were combined.
    """
    from . import pixel

    by_id = preds.set_index("game_id")
    paras = []

    price = pixel.format_american(pick["american"])
    implied = pixel.implied_probability(pick["american"])
    if pick["is_parlay"]:
        names = " + ".join(leg["detail"] for leg in pick["legs"])
        paras.append(
            f"<strong>{len(pick['legs'])}-leg parlay at {price}:</strong> {names}. "
            f"Each leg on its own is priced too short to be worth backing, so they are "
            f"combined to get the price to {price}. The model puts the joint chance of "
            f"all legs landing at {pick['prob']:.0%}, against {implied:.0%} implied by "
            f"the price &mdash; that gap is the reason this qualifies."
        )
    else:
        leg = pick["legs"][0]
        paras.append(
            f"<strong>{leg['detail']} at {price}.</strong> The model gives this "
            f"{leg['prob']:.0%}, while the price implies {implied:.0%}. Backing it is "
            "worth doing only because of that difference, not because the side is "
            "likely to win."
        )

    for leg in pick["legs"]:
        if leg["game_id"] not in by_id.index:
            continue
        row = by_id.loc[leg["game_id"]]
        bits = []
        gap = row.get("home_rating", 0) - row.get("away_rating", 0)
        stronger = row["home_team"] if gap > 0 else row["away_team"]
        bits.append(f"{stronger} rates {abs(gap):.1f} points stronger on power ratings")
        factors = key_factors(row)
        if factors:
            top = factors[0]
            bits.append(f"the biggest matchup edge is {top['title'].lower()} "
                        f"({top['verdict']})")
        if pd.notna(row.get("spread_line")):
            edge = row["pred_margin"] - row["spread_line"]
            if abs(edge) >= 1:
                side = row["home_team"] if edge > 0 else row["away_team"]
                bits.append(f"the model wants {abs(edge):.1f} more points on {side} "
                            "than the market prices")
        note = availability_note(row, roster)
        paras.append(
            f"<strong>{leg['matchup']} &mdash; {leg['detail']}.</strong> "
            + _sentence_case("; ".join(bits)) + ". " + note
        )

    if pick["ev"] > 0:
        paras.append(
            f"By the model's own numbers this returns {pick['ev']:+.2f} units per unit "
            "staked over the long run. That is the model's estimate, not a guarantee "
            "&mdash; the tracker records what actually happened."
        )
    return paras


def _num(row, field):
    """Read a field from a namedtuple row, treating missing or NaN as absent."""
    value = getattr(row, field, None)
    return value if value is not None and pd.notna(value) else None


def _fmt_player(row, kind: str, current: bool = True) -> str:
    """One player's line, in the shape a broadcast would read it.

    `current` says whether the numbers are from the season being played. If
    they are not, the sentence goes into the past tense, because "commands
    27% of the targets" is a claim about now and last year's file cannot
    support it.
    """
    commands = "commands" if current else "commanded"
    takes = "takes" if current else "took"
    if kind == "REC":
        catches_only = bool(getattr(row, "completions_only", False))
        bits = []
        for field, label in (("targets_pg", "targets"), ("catches_pg", "catches")):
            value = _num(row, field)
            if value is not None:
                bits.append(f"{value:.1f} {label}")
        yards = _num(row, "rec_yards_pg")
        if yards is not None:
            bits.append(f"{yards:.0f} yards")
        detail = ", ".join(bits)
        air = _num(row, "air")
        depth = f", working {air:.1f} yards downfield on average" if air else ""
        noun = "receptions" if catches_only else "targets"
        position = getattr(row, "position", None)
        label = f" ({position})" if isinstance(position, str) and position else ""
        return (f"<strong>{row.player}</strong>{label} {commands} {row.share:.0%} of the "
                f"{noun} ({detail} a game){depth}{_availability_suffix(row)}")
    bits = []
    carries, yards = _num(row, "carries_pg"), _num(row, "rush_yards_pg")
    if carries is not None:
        bits.append(f"{carries:.1f} carries")
    if yards is not None:
        bits.append(f"{yards:.0f} yards")
    detail = ", ".join(bits)
    return (f"<strong>{row.player}</strong> {takes} {row.share:.0%} of the carries "
            f"({detail} a game){_availability_suffix(row)}")


def _team_count(row: pd.Series) -> int:
    """How many teams the unit ranks are drawn from, guarding missing values."""
    for key, default in (("unit_n", None), ("n_teams", 32)):
        value = row.get(key, default)
        if value is not None and pd.notna(value):
            return int(value)
    return 32


def usage_report(row: pd.Series, usage: pd.DataFrame | None,
                 roster=None) -> list[dict]:
    """Who gets the ball for each side, and what the other side does about it.

    This is the part a reader cannot get from a rating: the target hierarchy,
    the backfield split, how far downfield the offense works, and whether the
    defence across from them is equipped to handle it.

    Players who have left the team have already been removed upstream; the
    ones ruled out for this game are named separately rather than presented
    as the people to watch, and where the depth chart disagrees with last
    season's usage the depth chart decides who is called the starter.
    """
    if usage is None or usage.empty:
        return []
    n = _team_count(row)
    current = _stats_are_current(usage)
    note = _provenance(usage, roster)
    out = []
    sides = [
        (row.get("home_key", row.home_team), row.home_team, row.away_team,
         "away_def_pass_epa", "away_def_rush_epa"),
        (row.get("away_key", row.away_team), row.away_team, row.home_team,
         "home_def_pass_epa", "home_def_rush_epa"),
    ]
    for key, team, opponent, pass_def_key, rush_def_key in sides:
        side = usage[usage["team"] == key]
        if side.empty:
            continue
        paras = []

        rec_all = _receivers(side)
        recs = _order(_plays_this_week(rec_all), 3)
        if not recs.empty:
            lead = recs.iloc[0]
            lines = [_fmt_player(r, "REC", current) for r in recs.itertuples()]
            defence = ""
            if pd.notna(row.get(f"{pass_def_key}_rank")):
                rank = int(row[f"{pass_def_key}_rank"])
                pct = grades.percentile(rank, n)
                verdict = ("should be tested repeatedly" if pct < 0.4
                           else "will make them work for it" if pct > 0.7
                           else "is roughly a neutral matchup")
                defence = (f" The {opponent} pass defence grades "
                           f"<strong>{grades.grade(pct)}</strong> "
                           f"({grades.rank_label(rank, n)}), so this group {verdict}.")
            expect = (f" Expect {lead.player} to see the ball early and often."
                      if _availability(lead) == rosters.AVAILABLE else "")
            paras.append(
                f"<strong>Through the air:</strong> {lines[0]}"
                + (f". Behind him, {'; '.join(lines[1:])}" if len(lines) > 1 else "")
                + "." + expect + _out_sentence(rec_all) + defence
            )
        elif not rec_all.empty:
            paras.append("<strong>Through the air:</strong> everyone this offence "
                         "leaned on is unavailable." + _out_sentence(rec_all))

        run_all = _material(side[side["role"] == "RUSH"])
        runs = _order(_plays_this_week(run_all), 2)
        if not runs.empty:
            lines = [_fmt_player(r, "RUSH", current) for r in runs.itertuples()]
            split = (f" {lines[1]}, so this is a committee rather than a bell cow."
                     if len(lines) > 1 and runs.iloc[1].share >= 0.25
                     else (f" {lines[1]} in a change-of-pace role."
                           if len(lines) > 1 else ""))
            defence = ""
            if pd.notna(row.get(f"{rush_def_key}_rank")):
                rank = int(row[f"{rush_def_key}_rank"])
                pct = grades.percentile(rank, n)
                verdict = ("a defence that has been run on all year" if pct < 0.4
                           else "a front that holds up well" if pct > 0.7
                           else "an average front")
                defence = (f" They run into {verdict} &mdash; {opponent} grades "
                           f"<strong>{grades.grade(pct)}</strong> against the run "
                           f"({grades.rank_label(rank, n)}).")
            paras.append(f"<strong>On the ground:</strong> {lines[0]}.{split}"
                         + _out_sentence(run_all) + defence)
        elif not run_all.empty:
            paras.append("<strong>On the ground:</strong> the backs who carried this "
                         "offence are unavailable." + _out_sentence(run_all))

        depth_line = _depth_chart_line(roster, key)
        if depth_line:
            # a player who joined this summer has no usage with this team at
            # all, so on last season's numbers the depth chart is the only
            # place his name can come from — it goes first
            if current:
                paras.append(depth_line)
            else:
                paras.insert(0, depth_line)
        if paras:
            out.append({"team": team, "paragraphs": paras, "note": note})
    return out


def _material(frame: pd.DataFrame) -> pd.DataFrame:
    """Players with enough of the work to be part of the hierarchy."""
    if frame is None or frame.empty or "share" not in frame.columns:
        return frame if frame is not None else pd.DataFrame()
    return frame[frame["share"] >= MINOR_SHARE]


def _receivers(side: pd.DataFrame) -> pd.DataFrame:
    """The pass catchers, which does not include the man throwing to them."""
    recs = _material(side[side["role"] == "REC"])
    if recs is None or recs.empty or "position" not in recs.columns:
        return recs
    return recs[recs["position"].astype(str).str.upper() != "QB"]


def _order(frame: pd.DataFrame, limit: int) -> pd.DataFrame:
    """The top few by share of the work, the depth chart breaking ties."""
    if frame is None or frame.empty:
        return frame if frame is not None else pd.DataFrame()
    df = frame.copy()
    df["_depth"] = df["depth_rank"].fillna(99) if "depth_rank" in df.columns else 99
    return df.sort_values(["share", "_depth"], ascending=[False, True]).head(limit)


def _depth_chart_line(roster, team) -> str:
    """The starters as the club itself lists them, which in the first weeks
    of a season is the only current statement about who plays."""
    if roster is None or getattr(roster, "empty", True) or roster.league != "nfl":
        return ""
    bits = []
    for label, position in (("QB", "QB"), ("RB", "RB"), ("TE", "TE")):
        named = roster.starters(team, position, limit=1)
        if named:
            bits.append(f"{label} <strong>{named[0].name}</strong>")
    wrs = roster.starters(team, "WR", limit=3)
    if wrs:
        bits.append("WR " + ", ".join(f"<strong>{w.name}</strong>" for w in wrs))
    if not bits:
        return ""
    return ("<strong>On the depth chart:</strong> the club lists "
            + "; ".join(bits) + ".")


def line_movement(row: pd.Series) -> str:
    """How the number has moved since it opened, where the feed publishes it."""
    opened = row.get("open_spread_line")
    current = row.get("spread_line")
    if pd.isna(opened) or pd.isna(current):
        return ""
    opened, current = odds.to_spread(opened), odds.to_spread(current)
    move = current - opened
    home, away = row.home_team, row.away_team
    if abs(move) < 0.5:
        return (f"The line has not moved off its open of "
                f"{odds.format_market(home, away, opened)} &mdash; the market is settled "
                "on this number.")
    toward = home if move > 0 else away
    return (f"The line opened at {odds.format_market(home, away, opened)} and now sits at "
            f"{odds.format_market(home, away, current)}, {abs(move):.1f} points toward "
            f"<strong>{toward}</strong>. Money has been coming in on that side.")


# ---------------------------------------------------------------------------
# The rest of the picture: score, conditions, who is out, recent form, and
# the honest case for the confidence tier
# ---------------------------------------------------------------------------

def projected_score(row: pd.Series) -> str:
    """A scoreline, from the projected margin and the market total."""
    total = row.get("total_line")
    if pd.isna(total) or total is None:
        return ""
    margin = float(row.pred_margin)
    home = (float(total) + margin) / 2.0
    away = (float(total) - margin) / 2.0
    return (f"Projected score: <strong>{row.home_team} {home:.0f}, {row.away_team} {away:.0f}"
            f"</strong> against a market total of {float(total):g}.")


def conditions_note(row: pd.Series) -> str:
    """Forecast for kickoff where one was fetched; otherwise the venue."""
    text = weather.describe(row)
    if text:
        stamp = row.get("forecast_at")
        if isinstance(stamp, str) and stamp and not bool(row.get("indoors_venue")):
            text += f' <span class="meta">(forecast as of {stamp[:16].replace("T", " ")} UTC)</span>'
        return text
    if row.get("indoors"):
        return "Played indoors, so weather is not a factor."
    wind = row.get("wind")
    if pd.notna(wind) and wind >= 15:
        return (f"Wind around {wind:.0f} mph at kickoff, enough to lean on the run "
                "and shorten the passing game.")
    return ""


_STATUS_ORDER = {"Out": 0, "Doubtful": 1, "Questionable": 2}
_POSITION_PRIORITY = {"QB": 0, "WR": 1, "RB": 1, "TE": 2, "T": 2, "G": 3, "C": 3,
                      "CB": 2, "EDGE": 2, "DE": 2, "OLB": 2, "S": 3, "LB": 3, "DT": 3,
                      "K": 4, "P": 5, "LS": 6}


def injury_report(row: pd.Series, reports: pd.DataFrame | None, league: str,
                  roster=None) -> list[tuple[str, str]]:
    """(team, sentence) for each side: who is ruled out and who is a question
    mark, named, most important positions first.

    Two sources, because the weekly report alone is not the answer. The
    game-status designations cover players in this week's plan; the roster's
    reserve lists cover the ones who have been out for weeks and therefore
    never appear on a report again. Before the first report of a week is
    filed — which is the entire situation in week one — the reserve lists
    are all there is, and a page that showed nothing was not saying "nobody
    is hurt", it was failing to say anything.
    """
    # College availability is not published in any free feed, so there is
    # nothing to report and the section stays off rather than implying a
    # clean bill of health nobody has given.
    if league != "nfl":
        return []
    out = []
    for key, team in ((row.get("home_key", row.home_team), row.home_team),
                      (row.get("away_key", row.away_team), row.away_team)):
        named = _designations(reports, row, key, league)
        sidelined = _reserve_list(roster, key, exclude=named["ids"])
        groups = named["groups"] + sidelined
        if groups:
            out.append((team, "; ".join(groups) + "."))
        elif named["filed"] or (roster is not None and roster.covers(key)):
            out.append((team, "nobody ruled out and no game-status designations."))
        else:
            out.append((team, "no availability data published for this team."))
    return out


def _designations(reports: pd.DataFrame | None, row: pd.Series, key: object,
                  league: str) -> dict:
    """This week's game-status designations for one team."""
    blank = {"groups": [], "ids": set(), "filed": False}
    if league != "nfl" or reports is None or reports.empty:
        return blank
    if "report_status" not in reports.columns:
        return blank
    wk = reports[(reports["season"] == row.get("season"))
                 & (reports["week"] == row.get("week"))]
    if wk.empty:
        return blank
    side = wk[(wk["team"] == key) & wk["report_status"].notna()].copy()
    if side.empty:
        return {"groups": [], "ids": set(), "filed": True}
    side["_s"] = side["report_status"].map(_STATUS_ORDER).fillna(3)
    side["_p"] = side["position"].map(_POSITION_PRIORITY).fillna(4)
    side = side.sort_values(["_s", "_p"])
    groups, ids = [], set()
    for status in ("Out", "Doubtful", "Questionable"):
        names = []
        for r in side[side["report_status"] == status].head(6).itertuples():
            inj = getattr(r, "report_primary_injury", None)
            inj = f", {str(inj).lower()}" if isinstance(inj, str) and inj else ""
            names.append(f"<strong>{r.full_name}</strong> ({r.position}{inj})")
            gsis = getattr(r, "gsis_id", None)
            if isinstance(gsis, str):
                ids.add(gsis)
        extra = (side["report_status"] == status).sum() - len(names)
        if names:
            groups.append(f"{status}: " + ", ".join(names)
                          + (f" and {extra} more" if extra > 0 else ""))
    return {"groups": groups, "ids": ids, "filed": True}


def _reserve_list(roster, key: object, exclude: set, limit: int = 6) -> list[str]:
    """The players a club has parked on a reserve list, which is the half of
    "who is out" the weekly report never shows."""
    if roster is None or getattr(roster, "empty", True) or not roster.covers(key):
        return []
    sidelined = [p for p in roster.unavailable(key) if p.player_id not in exclude]
    if not sidelined:
        return []
    names = []
    for player in sidelined[:limit]:
        reason = f", {player.reason}" if player.reason else ""
        names.append(f"<strong>{player.name}</strong> ({player.position or '—'}{reason})")
    extra = len(sidelined) - len(names)
    return ["Ruled out: " + ", ".join(names)
            + (f" and {extra} more" if extra > 0 else "")]


def recent_form(row: pd.Series, games: pd.DataFrame | None, league: str,
                last: int = 4) -> list[tuple[str, str]]:
    """(team, sentence) with each side's last few results, most recent first."""
    if games is None or games.empty:
        return []
    season, week = row.get("season"), row.get("week")
    done = games[games["completed"].astype(bool) & games["margin"].notna()]
    before = done[(done["season"] < season) | ((done["season"] == season) & (done["week"] < week))]
    out = []
    for key, team in ((row.get("home_key", row.home_team), row.home_team),
                      (row.get("away_key", row.away_team), row.away_team)):
        mine = before[(before["home_team"] == key) | (before["away_team"] == key)]
        mine = mine.sort_values(["season", "week"]).tail(last)
        if mine.empty:
            continue
        bits, wins = [], 0
        for g in mine.itertuples():
            at_home = g.home_team == key
            opp = g.away_team if at_home else g.home_team
            us = g.home_score if at_home else g.away_score
            them = g.away_score if at_home else g.home_score
            if pd.isna(us) or pd.isna(them):
                continue
            won = us > them
            wins += int(won)
            tag = "W" if won else ("T" if us == them else "L")
            opp_name = teams.short_name(opp, league) if isinstance(opp, str) else str(opp)
            where = "vs" if at_home else "@"
            stale = f" ({int(g.season)})" if g.season != season else ""
            bits.append(f"<span class=\"form-{tag.lower()}\">{tag}</span> {int(us)}&ndash;{int(them)} "
                        f"{where} {opp_name}{stale}")
        if not bits:
            continue
        n = len(bits)
        margin = (mine.apply(lambda g: (g.home_score - g.away_score) if g.home_team == key
                             else (g.away_score - g.home_score), axis=1)).mean()
        out.append((team, f"{wins}-{n - wins} in the last {n}, average margin {margin:+.1f}: "
                          + ", ".join(reversed(bits)) + "."))
    return out


def confidence_case(row: pd.Series, tier_rates: dict | None, league: str) -> list[str]:
    """Why the moneyline carries the tier it does, in the tracker's own
    numbers rather than adjectives."""
    from . import pixel, tracking

    tier = row.get("ml_tier") or "lean"
    cal = row.get("ml_cal")
    prob = float(cal) if pd.notna(cal) else float(row.get("ml_prob", 0.5))
    price = row.get("ml_price")
    pick = row.get("ml_pick", "")
    paras = []

    if pd.notna(price) and abs(price) >= 100:
        implied = pixel.implied_probability(float(price))
        gap = prob - implied
        if gap >= 0.02:
            price_txt = (f"The price ({pixel.format_american(float(price))}) implies {implied:.0%}, "
                         f"so the model sees {gap:+.0%} of value on top of the confidence.")
        elif gap <= -0.03:
            price_txt = (f"The price ({pixel.format_american(float(price))}) implies {implied:.0%}, "
                         f"more than the model gives &mdash; the confidence is real but the "
                         "book is charging for it.")
        else:
            price_txt = (f"The price ({pixel.format_american(float(price))}) implies {implied:.0%}, "
                         "essentially the model's number: a fair price, no edge either way.")
    else:
        price_txt = "No moneyline is posted yet, so this is not graded as a bet."

    label = tracking.TIER_LABELS.get(tier, tier.title())
    paras.append(f"<strong>{pick}</strong> is a <strong>{label}</strong>: the calibrated "
                 f"chance of winning is <strong>{prob:.0%}</strong>. {price_txt}")

    rec = (tier_rates or {}).get(tier)
    if rec and rec.get("decided", 0) >= 20:
        verdict = ("a low-risk play that pays little" if tier == "lock"
                   else "a solid play at a price that already knows it" if tier == "pick"
                   else "a coin flip with a small tilt" if tier == "lean"
                   else "no play")
        paras.append(
            f"Since 2023, {league.upper() if league == 'nfl' else 'college'} moneylines the model "
            f"rated {'as a Pass' if tier == 'pass' else 'as ' + label + 's'} have gone "
            f"<strong>{rec['wins']}-{rec['losses']}</strong> "
            f"({rec['hit_rate']:.0%}), returning {rec['roi']:+.1%} per unit staked &mdash; "
            f"{verdict}.")
    if tier == "pass":
        paras.append("The model cannot separate these two: it still shows a side so the "
                     "reasoning is visible, but this is not put forward as a pick.")

    waiting = row.get("waiting_on")
    stage = row.get("release_stage")
    if stage == "lean" and isinstance(waiting, str) and waiting:
        paras.append(f"This is a lean. It locks once the {waiting} is in, and no later than "
                     "two hours before kickoff; the number can still move until then.")
    elif stage == "locked":
        reason = row.get("lock_reason") or ""
        paras.append(f"Locked ({reason}). This is the pick of record: it will not change "
                     "and it is what the tracker grades.")
    return paras
