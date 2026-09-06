"""Betting-market presentation helpers.

Sportsbooks post spreads in half-point increments (-7, -7.5, -8), never
-8.9. The model's continuous margin estimate is the honest internal number,
but a pick shown to a reader should read like a line: round it to the
nearest half point. The raw projection stays available for sorting and for
the model-vs-market edge, which is where the decimals actually matter.
"""
from __future__ import annotations

import math

# Key numbers in football margins — games land on these far more often than
# on neighboring values (3 = field goal, 7 = touchdown), so a projection
# sitting just off one is worth flagging.
NFL_KEY_NUMBERS = (3, 7, 10, 14)
NCAA_KEY_NUMBERS = (3, 7, 10, 14)


def to_spread(margin: float) -> float:
    """Round a projected margin to the nearest half point, book style."""
    return round(margin * 2) / 2


def format_spread(margin: float) -> str:
    """Render a spread the way a book posts it: -7, -7.5, PK."""
    spread = to_spread(abs(margin))
    if spread == 0:
        return "PK"
    return f"{spread:.0f}" if float(spread).is_integer() else f"{spread:.1f}"


def format_line(team: str, margin: float) -> str:
    """'DET -7.5' style line for the favored team."""
    spread = format_spread(margin)
    return f"{team} PK" if spread == "PK" else f"{team} -{spread}"


def format_market(home: str, away: str, spread_line: float) -> str:
    """The market line named for whichever side the market favours
    (spread_line is positive when the home team is favoured)."""
    fav = home if spread_line >= 0 else away
    return format_line(fav, spread_line)


def format_moneyline(win_prob: float, vig: float = 0.045) -> str:
    """Convert a win probability to an American moneyline, with a little
    vig applied so the number reads like a real book price."""
    p = min(max(win_prob, 0.01), 0.99)
    p_vig = min(p + vig / 2, 0.985)
    if p_vig >= 0.5:
        ml = -round(100 * p_vig / (1 - p_vig) / 5) * 5
    else:
        ml = round(100 * (1 - p_vig) / p_vig / 5) * 5
    return f"{ml:+.0f}"


def near_key_number(margin: float, league: str) -> int | None:
    """Return the nearest key number the projection sits within a point of."""
    keys = NFL_KEY_NUMBERS if league == "nfl" else NCAA_KEY_NUMBERS
    close = [k for k in keys if abs(abs(margin) - k) <= 1.0]
    return min(close, key=lambda k: abs(abs(margin) - k)) if close else None


# --- pick classification -------------------------------------------------
# Buckets are defined on the model's own confidence and on its disagreement
# with the market, so a "lock" means the model is confident, and "value"
# means the model and the market disagree enough to be interesting.

LOCK_WIN_PROB = 0.70
PICKEM_WIN_PROB = 0.58
VALUE_EDGE_PTS = 2.5


def classify(win_prob: float, edge: float | None,
             spread_line: float | None = None) -> list[str]:
    """Tags for one game: any of pickem / value / upset.

    `win_prob` is the home side's probability. An upset is a side the
    *market* has as the underdog, judged from the sign of the market line
    rather than from the model's own favourite. The "lock" tag comes from
    the calibrated tier (tracking.tier_for), not from here."""
    tags = []
    confidence = max(win_prob, 1 - win_prob)
    if confidence <= PICKEM_WIN_PROB:
        tags.append("pickem")
    if edge is not None and abs(edge) >= VALUE_EDGE_PTS:
        tags.append("value")
        market_home_fav = (spread_line > 0) if spread_line is not None else (win_prob > 0.5)
        # model wants points on the side the market has as the underdog
        if (edge > 0) != market_home_fav:
            tags.append("upset")
    return tags
