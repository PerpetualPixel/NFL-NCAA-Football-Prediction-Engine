"""Pixel's Picks — the highest-conviction plays on each week's slate.

The rules, in order:

* Every week produces at least one pick for the NFL and one for college.
* A pick may be a moneyline or a spread, but it has to carry value: at the
  price offered, the model's own probability has to beat the price's implied
  probability. Confidence alone is not enough — a -650 favorite is usually
  the right side and still a bad bet.
* The price has to be -175 or better. When the best play is a heavy favorite,
  it is parlayed with other high-conviction legs until the combined price
  clears that floor, and the pick names every leg it is combined with.
  Even money is the target; two -250 legs land near +96, which qualifies.
"""
from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd
from scipy.stats import norm

MIN_COMBINED_AMERICAN = -175.0
TARGET_DECIMAL = 2.0          # around even money
MAX_LEGS = 3
MIN_ML_PROB = 0.60
MIN_ATS_PROB = 0.55
MIN_EDGE_POINTS = 1.5         # a spread leg needs a real disagreement

# Backtested 2021-2025: the further the model's probability sits above the
# price's, the worse the bet did — NFL moneylines went from roughly breakeven
# when the model agreed with the price to -44% and -65% in the two most
# disagreeable buckets. Large disagreement is a symptom of the model missing
# something the market knows, so it is disqualifying rather than exciting.
MAX_DISAGREEMENT = 0.15


def american_to_decimal(odds: float) -> float:
    # guard impossible prices from consensus medians (see tracking module)
    if pd.isna(odds) or abs(odds) < 100:
        odds = -110.0
    return 1.0 + (odds / 100.0 if odds > 0 else 100.0 / abs(odds))


def decimal_to_american(dec: float) -> float:
    if dec <= 1.0:
        return -10000.0
    return (dec - 1.0) * 100.0 if dec >= 2.0 else -100.0 / (dec - 1.0)


def format_american(odds: float) -> str:
    return f"{odds:+.0f}" if odds > 0 else f"{odds:.0f}"


def implied_probability(odds: float) -> float:
    return 1.0 / american_to_decimal(odds)


def _leg(row, kind, pick, price, prob, detail):
    dec = american_to_decimal(price)
    return {
        "game_id": row.game_id, "kind": kind, "pick": pick, "price": float(price),
        "decimal": dec, "prob": float(prob), "detail": detail,
        # expected profit per unit staked, by the model's own probability
        "ev": prob * (dec - 1.0) - (1.0 - prob),
        "matchup": f"{row.away_team} @ {row.home_team}",
        "home_team": row.home_team, "away_team": row.away_team,
    }


def candidate_legs(preds: pd.DataFrame, margin_sigma: float,
                   relaxed: bool = False) -> list[dict]:
    """Every leg worth considering this week.

    Probabilities are the calibrated ones where available: the raw model
    numbers systematically overrate its own disagreements with the price,
    which is what makes confident-looking picks lose.

    The relaxed pool drops the confidence thresholds. It is never used to
    choose a pick on its own — only to find partners to parlay a short-priced
    favourite with, so the combined price can reach the floor.
    """
    min_ml = 0.0 if relaxed else MIN_ML_PROB
    min_ats = 0.0 if relaxed else MIN_ATS_PROB
    min_edge = 0.0 if relaxed else MIN_EDGE_POINTS
    max_gap = 1.0 if relaxed else MAX_DISAGREEMENT
    legs = []
    for row in preds.itertuples():
        cal_ml = getattr(row, "ml_cal", np.nan)
        cal_ats = getattr(row, "ats_cal", np.nan)
        # --- moneyline -------------------------------------------------
        home_favored = row.pred_margin > 0
        prob = row.home_win_prob if home_favored else 1 - row.home_win_prob
        price = getattr(row, "home_moneyline" if home_favored else "away_moneyline", np.nan)
        pick = row.home_team if home_favored else row.away_team
        if pd.notna(cal_ml):
            prob = float(cal_ml)
        if (pd.notna(price) and prob >= min_ml
                and prob - implied_probability(price) <= max_gap):
            legs.append(_leg(row, "ML", pick, price, prob,
                             f"{pick} to win outright"))

        # --- spread ----------------------------------------------------
        if pd.notna(getattr(row, "spread_line", np.nan)):
            edge = row.pred_margin - row.spread_line
            take_home = edge > 0
            side = row.home_team if take_home else row.away_team
            line = -row.spread_line if take_home else row.spread_line
            # probability the side covers, from the margin distribution
            cover = norm.cdf(abs(edge) / margin_sigma)
            price = getattr(row, "home_spread_odds" if take_home else "away_spread_odds",
                            np.nan)
            price = -110.0 if pd.isna(price) else price
            if pd.notna(cal_ats):
                cover = float(cal_ats)
            if (cover >= min_ats and abs(edge) >= min_edge
                    and cover - implied_probability(price) <= max_gap):
                legs.append(_leg(row, "ATS", side, price, cover,
                                 f"{side} {line:+.1f}"))
    return legs


def _combo_quality(combo: tuple[dict, ...]) -> tuple[float, float]:
    """Rank combinations: clear the odds floor, sit near even money, and
    carry the highest joint confidence."""
    dec = math.prod(leg["decimal"] for leg in combo)
    joint = math.prod(leg["prob"] for leg in combo)
    ev = joint * (dec - 1.0) - (1.0 - joint)
    return ev, -abs(dec - TARGET_DECIMAL)


def _clears_floor(combo) -> bool:
    dec = math.prod(leg["decimal"] for leg in combo)
    return decimal_to_american(dec) >= MIN_COMBINED_AMERICAN


def _score(combo) -> tuple:
    """Rank floor-clearing combinations: value first, then confidence, then
    proximity to even money."""
    dec = math.prod(leg["decimal"] for leg in combo)
    joint = math.prod(leg["prob"] for leg in combo)
    ev = joint * (dec - 1.0) - (1.0 - joint)
    return (ev > 0, ev, joint, -abs(dec - TARGET_DECIMAL))


def _best_combo(anchors: list[dict], partners: list[dict]) -> tuple | None:
    """Smallest combination that clears the price floor.

    At least one leg always comes from `anchors`, so a pick is never built
    entirely from legs that failed the confidence thresholds.
    """
    if not anchors:
        return None
    pool = anchors + [p for p in partners
                      if p["game_id"] not in {a["game_id"] for a in anchors}]
    for size in range(1, MAX_LEGS + 1):
        best = None
        for combo in itertools.combinations(pool, size):
            if len({leg["game_id"] for leg in combo}) != size:
                continue  # legs from one game are not independent
            if not any(leg in anchors for leg in combo):
                continue
            if not _clears_floor(combo):
                continue
            score = _score(combo)
            if best is None or score > best[0]:
                best = (score, combo)
        if best is not None:
            return best[1]
    return None


def select(preds: pd.DataFrame, margin_sigma: float) -> dict | None:
    """Choose this week's pick.

    The price floor is not negotiable: a short-priced favourite is only ever
    published parlayed with enough alongside it to reach -175 or better. If
    no combination can reach the floor, there is no pick — publishing one at
    -575 would break the promise the pick makes.
    """
    strict = candidate_legs(preds, margin_sigma)
    if not strict:
        return None

    # value legs are the preferred anchors; confident ones are the fallback
    valued = [leg for leg in strict if leg["ev"] > 0]
    partners = candidate_legs(preds, margin_sigma, relaxed=True)

    combo = _best_combo(sorted(valued, key=lambda l: -l["ev"])[:8], partners)
    if combo is None:
        combo = _best_combo(sorted(strict, key=lambda l: -l["prob"])[:8], partners)
    if combo is None:
        return None

    dec = math.prod(leg["decimal"] for leg in combo)
    joint = math.prod(leg["prob"] for leg in combo)
    return {
        "legs": list(combo),
        "decimal": dec,
        "american": decimal_to_american(dec),
        "prob": joint,
        "ev": joint * (dec - 1.0) - (1.0 - joint),
        "is_parlay": len(combo) > 1,
    }


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def grade(pick: dict, preds: pd.DataFrame) -> dict | None:
    """Settle a pick against results: every leg must land."""
    if not pick:
        return None
    by_id = preds.set_index("game_id")
    outcomes = []
    for leg in pick["legs"]:
        if leg["game_id"] not in by_id.index:
            return None
        row = by_id.loc[leg["game_id"]]
        result = row["ml_result"] if leg["kind"] == "ML" else row["ats_result"]
        if result is None or (isinstance(result, float) and pd.isna(result)):
            return None  # not played yet
        outcomes.append(result)

    if any(o == "loss" for o in outcomes):
        settled, profit = "loss", -1.0
    elif all(o == "push" for o in outcomes):
        settled, profit = "push", 0.0
    else:
        # a pushed leg drops out of the parlay, shortening the price
        live = [leg for leg, o in zip(pick["legs"], outcomes) if o != "push"]
        dec = math.prod(leg["decimal"] for leg in live) if live else 1.0
        settled, profit = "win", dec - 1.0
    return {**pick, "result": settled, "profit": profit, "leg_results": outcomes}


def record(graded_picks: list[dict]) -> dict:
    """Season record and ROI across settled picks."""
    settled = [p for p in graded_picks if p and p.get("result")]
    wins = sum(1 for p in settled if p["result"] == "win")
    losses = sum(1 for p in settled if p["result"] == "loss")
    pushes = sum(1 for p in settled if p["result"] == "push")
    profit = sum(p["profit"] for p in settled)
    decided = wins + losses
    return {
        "wins": wins, "losses": losses, "pushes": pushes, "decided": decided,
        "n": len(settled), "profit": profit,
        "hit_rate": wins / decided if decided else 0.0,
        "roi": profit / decided if decided else 0.0,
    }


# ---------------------------------------------------------------------------
# The parlay board: the week's most confident moneylines, combined to + money
# ---------------------------------------------------------------------------
#
# Pixel's Pick answers "what is the single best play". The board answers the
# next question: having taken that one, what do the rest of the confident
# moneylines give you? Short favourites are worth little alone, so they are
# stacked in confidence order until the combined price reaches even money or
# better. Legs are never reused between parlays, so each one is a genuinely
# different ticket rather than a reshuffle of the same games.

BOARD_MIN_DECIMAL = 2.0        # +100 or better
BOARD_MAX_LEGS = 4             # beyond this the hit rate collapses
BOARD_MIN_LEGS = 2
# The board wants more legs to work with than the headline pick does, so it
# takes a slightly lower confidence floor. Large disagreements with the price
# stay disqualified here too.
BOARD_MIN_PROB = 0.55

# A parlay needs several games, so single-game slots (Thursday, Monday) are
# combined into one primetime ticket rather than left unbuildable.
NFL_SLOTS = [
    ("Sunday", {6}, 3),
    # Thursday and Monday are one game each, and openers sometimes land
    # midweek, so the standalone nights share a single ticket
    ("Thursday, Monday &amp; standalone nights", {0, 1, 2, 3, 4, 5}, 1),
]
NCAA_SLOTS = [
    ("Saturday", {5}, 3),
    ("Weeknight", {0, 1, 2, 3, 4}, 1),
]


def _weekday(ts) -> int | None:
    """Kickoff weekday as the game is played locally, where Monday is 0.

    The two feeds differ: the NFL schedule gives a local game *date* stamped
    at midnight, while the college feed gives a real kickoff timestamp in
    UTC. Converting the former to Eastern would roll every game back a day —
    Sunday games would read as Saturday — so a midnight stamp is treated as
    an already-local date and left alone.
    """
    if pd.isna(ts):
        return None
    if ts.hour == 0 and ts.minute == 0:
        return ts.weekday()
    try:
        return ts.tz_convert("US/Eastern").weekday()
    except Exception:
        return ts.weekday()


def build_board(preds: pd.DataFrame, margin_sigma: float, league: str,
                exclude_game_ids: set | None = None) -> list[dict]:
    """Parlays grouped by the day they are played, priced at +100 or better."""
    if "gameday" not in preds.columns:
        return []
    legs = [
        leg for leg in candidate_legs(preds, margin_sigma, relaxed=True)
        if leg["kind"] == "ML"
        and leg["prob"] >= BOARD_MIN_PROB
        and leg["prob"] - implied_probability(leg["price"]) <= MAX_DISAGREEMENT
    ]
    if not legs:
        return []

    exclude = set(exclude_game_ids or ())
    kicks = dict(zip(preds["game_id"], pd.to_datetime(preds["gameday"], utc=True,
                                                      errors="coerce")))
    slots = NFL_SLOTS if league == "nfl" else NCAA_SLOTS
    board = []
    used = set(exclude)

    for label, weekdays, max_parlays in slots:
        pool = [leg for leg in legs
                if leg["game_id"] not in used
                and _weekday(kicks.get(leg["game_id"])) in weekdays]
        # most confident first: this is a confidence board, not a value board
        pool.sort(key=lambda l: -l["prob"])

        parlays = []
        current: list[dict] = []
        for leg in pool:
            if len(parlays) >= max_parlays:
                break
            if leg["game_id"] in used:
                continue
            current.append(leg)
            dec = math.prod(l["decimal"] for l in current)
            long_enough = len(current) >= BOARD_MIN_LEGS and dec >= BOARD_MIN_DECIMAL
            if long_enough or len(current) >= BOARD_MAX_LEGS:
                if dec >= BOARD_MIN_DECIMAL and len(current) >= BOARD_MIN_LEGS:
                    parlays.append(_finalise(current))
                    used.update(l["game_id"] for l in current)
                current = []
        if parlays:
            board.append({"slot": label, "parlays": parlays})
    return board


def _finalise(legs: list[dict]) -> dict:
    dec = math.prod(leg["decimal"] for leg in legs)
    joint = math.prod(leg["prob"] for leg in legs)
    return {
        "legs": list(legs),
        "decimal": dec,
        "american": decimal_to_american(dec),
        "prob": joint,
        "ev": joint * (dec - 1.0) - (1.0 - joint),
        "is_parlay": len(legs) > 1,
    }


def board_record(graded_parlays: list[dict]) -> dict:
    """Combined record across every settled board parlay."""
    return record([p for p in graded_parlays if p])
