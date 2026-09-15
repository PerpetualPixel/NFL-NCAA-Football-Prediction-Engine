"""Machine-readable picks feed, published alongside the site.

The week pages are written for a person: HTML, with the reasoning laid out in
prose. `picks.json` is the same week's work written for a program — one entry
per game, carrying the pick, the tier, the calibrated probability, the market
numbers it was priced against, and every breakdown paragraph the card shows,
as plain text.

It exists because another site (perpetualpicks.com) grades a live odds board
of its own and wants this engine's read on the football games sitting on it.
Scraping the week page for that would mean parsing presentation markup that
changes whenever the card layout does; a feed is a contract instead.

WHAT A CONSUMER MUST KNOW
-------------------------
Two things travel with every build, in `disclosure` and in each game's own
numbers, because a downstream site that treats this as an edge over the
market will lose money with it:

  * The tier says how often plays like this have *won*, never that the price
    is good. Locks win about 93% of the time and pay accordingly.
  * Measured against three seasons of results, this model does not beat the
    closing line (optimal blend weight on it, given the price, is 0.00; CLV
    sits at 34-39%), and its biggest disagreements with the market have been
    its worst bets. `ml_ev` is the model's own expected value at the posted
    price and is published as-is — it is not a claim that the market is wrong.

So the honest use of this feed downstream is confirmation and research: which
side the model lands on, how confident it is after calibration, and the facts
behind it. `agreement` is included on every game for exactly that purpose —
how far the model's calibrated probability sits from the market's own implied
number, so a consumer can damp a pick the model only likes because it
disagrees with the price.

STABILITY
---------
`feed_version` is bumped when a field changes meaning or disappears; new
fields may appear at any time without a bump. Every game field except the
identifiers can be null: the college feed posts no injuries and often no
spread price, a game with no posted moneyline is not graded as a bet at all,
and a game rated Pass carries a side with `counted: false` rather than no side
at all.

Version 2 added early leans: games more than a week from kickoff, which
version 1 omitted. They arrive with `stage: "pending"` and are the model's
read before any injury report exists for the game. Filter them out on `stage`
to get version 1's set back.
"""
from __future__ import annotations

import html as html_mod
import re

import pandas as pd

from . import analysis, odds, pixel, tracking
from .config import LEAGUES

FEED_VERSION = 2

SITE_URL = "https://perpetualpixel.github.io/NFL-NCAA-Football-Prediction-Engine/"

# The Odds API's sport keys, so a consumer can match its own board to a league
# without hardcoding this engine's shorthand.
SPORT_KEYS = {"nfl": "americanfootball_nfl", "ncaa": "americanfootball_ncaaf"}

DISCLOSURE = (
    "Tiers report how often plays like this have won, not that the price is good: "
    "Locks win about 93% of the time at prices that make that roughly breakeven. "
    "Measured 2023-2025 this model does not beat the closing line (optimal blend "
    "weight on it given the price is 0.00, closing line value 34-39%), and its "
    "largest disagreements with the market have been its worst bets. Use it as "
    "confirmation and research, not as an edge over the price."
)

# A pick that has not been released yet is not a pick. Pending games carry a
# projection internally, but publishing one would put a number on a game whose
# injuries and weather are still a week out — exactly what the two-stage
# release exists to prevent.
# Every game the site lists carries a lean, so the feed carries them all too.
# `stage` separates them: "pending" is an early lean made before any injury
# report exists, and a consumer that only wants settled reads can filter on it.
PUBLISHED_STAGES = ("pending", "lean", "locked", "started")

_TAG = re.compile(r"<[^>]+>")


def plain(text) -> str:
    """One of the site's HTML fragments as plain text.

    The analysis module writes for the page — `<strong>` around the team that
    matters, `&mdash;` between clauses. A consumer wants the sentence.
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    stripped = _TAG.sub("", str(text))
    return re.sub(r"\s+", " ", html_mod.unescape(stripped)).strip()


def _num(value):
    """A JSON number, or None for anything pandas calls missing."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value, digits=4):
    n = _num(value)
    return None if n is None else round(n, digits)


def _str(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = plain(value)
    return text or None


def _iso(ts) -> str | None:
    if ts is None or pd.isna(ts):
        return None
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC").isoformat().replace("+00:00", "Z")


def projected_score(row) -> dict | None:
    """The projected scoreline, as numbers.

    The site renders this as a sentence (analysis.projected_score); the same
    arithmetic, split back out, is what a consumer can compare to a total on
    its own board. Needs a market total — without one there is no way to
    split a margin into two scores.
    """
    total = _num(row.get("total_line"))
    margin = _num(row.get("pred_margin"))
    if total is None or margin is None:
        return None
    return {
        "home": round((total + margin) / 2.0, 1),
        "away": round((total - margin) / 2.0, 1),
        "total": round(total, 1),
    }


def agreement(prob, price) -> dict | None:
    """How far the model's calibrated probability sits from the market's.

    `gap` is signed: positive means the model is more confident in this side
    than the price is. The engine's own measurements say a large positive gap
    is a warning, not an edge (see the module docstring), so this is published
    next to every pick rather than left for a consumer to reconstruct.
    """
    p = _num(prob)
    line = _num(price)
    if p is None or line is None or abs(line) < 100:
        return None
    implied = pixel.implied_probability(line)
    return {
        "model_prob": round(p, 4),
        "implied_prob": round(implied, 4),
        "gap": round(p - implied, 4),
    }


def _moneyline(row) -> dict | None:
    pick = _str(row.get("ml_pick"))
    if not pick:
        return None
    prob = _num(row.get("ml_cal"))
    if prob is None:
        prob = _num(row.get("ml_prob"))
    price = _num(row.get("ml_price"))
    return {
        "selection": pick,
        "tier": row.get("ml_tier") or "lean",
        "tier_label": tracking.TIER_LABELS.get(row.get("ml_tier") or "lean", "Lean"),
        # calibrated: the model's raw number corrected walk-forward against
        # settled results and the price. This is what the tier keys off.
        "prob": _round(prob),
        "model_prob": _round(row.get("ml_prob")),
        "price": None if price is None or abs(price) < 100 else price,
        "ev": _round(row.get("ml_ev")),
        "agreement": agreement(prob, price),
    }


def _spread(row) -> dict | None:
    pick = _str(row.get("ats_pick"))
    line = _num(row.get("ats_line"))
    if not pick or line is None:
        return None
    prob = _num(row.get("ats_cal"))
    if prob is None:
        prob = _num(row.get("ats_prob"))
    return {
        "selection": pick,
        # the number this side lays or takes, book-style: -3.5 / +3.5
        "point": round(line, 1),
        "tier": row.get("ats_tier") or "lean",
        "prob": _round(prob),
        "model_prob": _round(row.get("ats_prob")),
        "price": _num(row.get("ats_price")),
        # the college mirror posts no spread price; -110 is assumed there,
        # for settlement and for this feed alike
        "price_assumed": bool(row.get("ats_price_assumed")),
        "ev": _round(row.get("ats_ev")),
        # how many points the model's margin sits off the market's line
        "edge": _round(row.get("ats_edge"), 2),
    }


def _analysis(row, league: str, ctx: dict) -> dict:
    """Every breakdown the game card shows, as plain text.

    Same functions the page calls, so the feed cannot drift from what a reader
    sees; only the markup is dropped. A section with nothing to say is an
    empty list or null rather than filler — a thin entry means thin evidence.
    """
    factors = [
        {
            "title": plain(f["title"]),
            "verdict": plain(f["verdict"]),
            "tone": f["tone"],
            "winner": plain(f["winner"]),
            "text": plain(f["text"]),
        }
        for f in analysis.key_factors(row)
    ]
    return {
        "confidence": [plain(p) for p in analysis.confidence_case(
            row, ctx.get("tier_rates"), league)],
        "script": [plain(p) for p in analysis.game_script(row, league)],
        "injuries": [{"team": plain(t), "text": plain(x)}
                     for t, x in analysis.injury_report(row, ctx.get("reports"), league,
                                                        ctx.get("roster"))],
        "form": [{"team": plain(t), "text": plain(x)}
                 for t, x in analysis.recent_form(row, ctx.get("games"), league)],
        "players": [{"team": plain(t), "text": plain(x)}
                    for t, x in analysis.key_players(row, ctx.get("players"),
                                                      ctx.get("roster"))],
        "conditions": _str(analysis.conditions_note(row)),
        "availability": _str(analysis.availability_note(row, ctx.get("roster"))),
        "movement": _str(analysis.line_movement(row)),
        "factors": factors,
    }


def game_entry(row, league: str, week: dict, page: str, ctx: dict) -> dict:
    """One published game, pick and reasoning together."""
    game_id = str(row.get("game_id"))
    stage = row.get("release_stage") or "locked"
    margin = _num(row.get("pred_margin")) or 0.0
    favourite = row.get("home_team") if margin > 0 else row.get("away_team")
    return {
        "league": league,
        "sport_key": SPORT_KEYS[league],
        "game_id": game_id,
        "season": int(week["season"]),
        "week": int(week["week"]),
        "week_label": plain(week["label"]),
        # Display names with mascots on both sides ("Rutgers Scarlet Knights"),
        # which is how the odds market names college teams too; the data
        # source's own key travels alongside for anything that needs to match
        # back to nflverse/cfbfastR rather than to a book.
        "home": _str(row.get("home_team")),
        "away": _str(row.get("away_team")),
        "home_key": _str(row.get("home_key")),
        "away_key": _str(row.get("away_key")),
        "neutral": bool(row.get("neutral")),
        "kickoff": _iso(row.get("kickoff")),
        # 'lean' is provisional and moves with the market; 'locked' is final
        # and is what the tracker grades; 'started' kicked off already.
        "stage": stage,
        "locked": stage in ("locked", "started"),
        "locked_at": _iso(row.get("locked_at")) if row.get("locked_at") else None,
        "lock_reason": _str(row.get("lock_reason")),
        "waiting_on": _str(row.get("waiting_on")),
        "url": f"{SITE_URL}{page}#g-{game_id}",
        "model": {
            "margin": _round(margin, 2),
            # the projection as a book would post it: "Seattle Seahawks -4.5"
            "line": odds.format_line(plain(favourite), margin),
            "home_win_prob": _round(row.get("home_win_prob")),
            "projected_score": projected_score(row),
        },
        "market": {
            "spread_line": _num(row.get("spread_line")),
            "total_line": _num(row.get("total_line")),
            "home_moneyline": _num(row.get("home_moneyline")),
            "away_moneyline": _num(row.get("away_moneyline")),
            "source": _str(row.get("odds_source")),
        },
        "moneyline": _moneyline(row),
        "spread": _spread(row),
        "analysis": _analysis(row, league, ctx),
    }


def _ticket(pick: dict | None) -> dict | None:
    """A Pixel's Pick or board parlay flattened for the feed."""
    if not pick:
        return None
    return {
        "american": round(float(pick["american"])),
        "decimal": round(float(pick["decimal"]), 4),
        "prob": round(float(pick["prob"]), 4),
        "ev": round(float(pick["ev"]), 4),
        "is_parlay": bool(pick["is_parlay"]),
        # a fair-priced ticket carries no edge by the model's own numbers and
        # says so on the card; the flag travels so a consumer can say it too
        "fair_priced": bool(pick.get("fair_priced", False)),
        "legs": [
            {
                "game_id": str(leg["game_id"]),
                "kind": leg["kind"],
                "selection": plain(leg["pick"]),
                "price": float(leg["price"]),
                "prob": round(float(leg["prob"]), 4),
                "detail": plain(leg["detail"]),
                "matchup": plain(leg["matchup"]),
            }
            for leg in pick["legs"]
        ],
    }


def league_entry(league: str, week: dict, page: str, rationale: list[str]) -> dict:
    """The week-level header for one league: where it is, and its headline
    play."""
    pixel_ticket = _ticket(week.get("pixel"))
    if pixel_ticket is not None:
        pixel_ticket["rationale"] = [plain(p) for p in rationale]
    return {
        "sport_key": SPORT_KEYS[league],
        "season": int(week["season"]),
        "week": int(week["week"]),
        "week_label": plain(week["label"]),
        "headline": plain(week["headline"]),
        "url": f"{SITE_URL}{page}",
        "released": int(week["released"]),
        "locked": int(week["locked"]),
        "margin_sigma": LEAGUES[league].margin_sigma,
        # measured hit rate and ROI per tier, from every settled game this
        # build has graded — the numbers behind the tier a pick carries
        "tier_rates": {
            tier: {
                "wins": int(rec["wins"]),
                "losses": int(rec["losses"]),
                "decided": int(rec["decided"]),
                "hit_rate": round(float(rec["hit_rate"]), 4),
                "roi": round(float(rec["roi"]), 4),
            }
            for tier, rec in (week.get("tier_rates") or {}).items()
            if rec.get("decided")
        },
        "pixel": pixel_ticket,
    }


def build(entries: list[tuple[str, dict, str]], now: pd.Timestamp | None = None) -> dict:
    """The whole feed.

    `entries` is (league, week, page filename) for every week being published —
    the current week of each league, plus the one after it where the schedule
    has already rolled over, so a consumer looking at a Tuesday odds board
    finds the games on it.
    """
    now = now or pd.Timestamp.now(tz="UTC")
    games: list[dict] = []
    leagues: dict[str, dict] = {}

    for league, week, page in entries:
        preds = week["preds"]
        ctx = {
            "tier_rates": week.get("tier_rates"),
            "reports": week.get("reports"),
            "games": week.get("games"),
            "players": week.get("players"),
            # without this the feed loses the reserve lists and the named
            # starting quarterbacks that the pages carry
            "roster": week.get("roster"),
        }
        published = preds[preds["release_stage"].isin(PUBLISHED_STAGES)]
        # A game already played is history; the tracking pages carry those.
        published = published[~published["completed"].astype(bool)]
        for _, row in published.iterrows():
            games.append(game_entry(row, league, week, page, ctx))

        # The first week listed for a league is its current one — that is the
        # week whose headline play the feed advertises.
        if league not in leagues:
            pick = week.get("pixel")
            rationale = (analysis.pixel_rationale(pick, preds, league) if pick else [])
            leagues[league] = league_entry(league, week, page, rationale)

    games.sort(key=lambda g: (g["kickoff"] or "", g["league"], g["game_id"]))
    return {
        "feed_version": FEED_VERSION,
        "generated_at": _iso(now),
        "source": SITE_URL,
        "disclosure": DISCLOSURE,
        "leagues": leagues,
        "games": games,
    }
