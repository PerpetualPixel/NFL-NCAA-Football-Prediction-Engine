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


def candidate_legs(preds: pd.DataFrame, margin_sigma: float) -> list[dict]:
    """Every leg worth considering this week.

    Probabilities are the calibrated ones where available: the raw model
    numbers systematically overrate its own disagreements with the price,
    which is what makes confident-looking picks lose.
    """
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
        if (pd.notna(price) and prob >= MIN_ML_PROB
                and prob - implied_probability(price) <= MAX_DISAGREEMENT):
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
            if (cover >= MIN_ATS_PROB and abs(edge) >= MIN_EDGE_POINTS
                    and cover - implied_probability(price) <= MAX_DISAGREEMENT):
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


def select(preds: pd.DataFrame, margin_sigma: float) -> dict | None:
    """Choose this week's pick: a single leg when the price allows, otherwise
    the smallest parlay of high-conviction legs that clears the odds floor."""
    legs = candidate_legs(preds, margin_sigma)
    if not legs:
        return None

    # value is the point: a leg only qualifies if the calibrated probability
    # beats the price. Falling back to "most confident" would reintroduce
    # exactly the heavy favorites that lose money.
    valued = [leg for leg in legs if leg["ev"] > 0]
    pool = sorted(valued or legs, key=lambda l: -l["ev"])[:8]

    best = None
    for size in range(1, MAX_LEGS + 1):
        for combo in itertools.combinations(pool, size):
            # never two legs from the same game: they are not independent
            if len({leg["game_id"] for leg in combo}) != size:
                continue
            dec = math.prod(leg["decimal"] for leg in combo)
            if decimal_to_american(dec) < MIN_COMBINED_AMERICAN:
                continue  # still too short a price even combined
            quality = _combo_quality(combo)
            if best is None or quality > best[0]:
                best = (quality, combo)
        if best is not None:
            break  # prefer the fewest legs that clear the floor

    if best is None:
        # nothing cleared the floor even parlayed; take the most confident
        # pair so the week still has a pick, and let the price speak
        combo = tuple(sorted(pool, key=lambda l: -l["prob"])[:2])
        if not combo:
            return None
        best = (_combo_quality(combo), combo)

    combo = best[1]
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
