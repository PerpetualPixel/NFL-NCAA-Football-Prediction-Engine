"""Bet-level tracking: the model makes a moneyline pick and a spread pick on
every game, and the two are graded and reported separately.

They are genuinely different bets and can disagree. The moneyline pick is
simply the projected winner. The spread pick is whichever side the model
thinks the market has mispriced — so the model can like a favorite to win
outright while liking the underdog to cover.

Results are reported as win-loss records and as return on investment at the
prices the book actually posted, which is the number that decides whether a
model is worth anything.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

from . import odds

FLAT_STAKE = 1.0
DEFAULT_ODDS = -110.0


def american_profit(odds: float, stake: float = FLAT_STAKE) -> float:
    """Profit on a winning bet at American odds (loss is simply -stake).

    Odds strictly between -100 and +100 do not exist; a consensus median can
    still produce one, and it is treated as a missing price rather than a
    windfall."""
    if pd.isna(odds) or abs(odds) < 100:
        odds = DEFAULT_ODDS
    return stake * (odds / 100.0 if odds > 0 else 100.0 / abs(odds))


def grade(preds: pd.DataFrame, margin_sigma: float = 13.5) -> pd.DataFrame:
    """Add moneyline and spread pick columns plus their outcomes.

    Only completed games are graded; upcoming games keep NaN results so the
    same frame can render both picks and history.
    """
    df = preds.copy()
    home_favored = df["pred_margin"] > 0
    done = df["completed"].astype(bool) & df["margin"].notna()

    # --- moneyline: back the projected winner -------------------------
    df["ml_pick"] = np.where(home_favored, df["home_team"], df["away_team"])
    df["ml_prob"] = np.where(home_favored, df["home_win_prob"], 1 - df["home_win_prob"])
    if "home_moneyline" in df.columns:
        df["ml_price"] = np.where(home_favored, df["home_moneyline"], df["away_moneyline"])
    else:
        df["ml_price"] = np.nan
    ml_won = np.where(home_favored, df["margin"] > 0, df["margin"] < 0)
    # a moneyline the book never posted cannot be settled at any price, so
    # the bet is simply not made rather than graded at an invented number
    priced = df["ml_price"].notna() & (df["ml_price"].abs() >= 100)
    df["ml_result"] = np.where(
        ~(done & priced), None,
        np.where(df["margin"] == 0, "push", np.where(ml_won, "win", "loss")))
    df["ml_profit"] = [
        0.0 if r in (None, "push") else
        (american_profit(p) if r == "win" else -FLAT_STAKE)
        for r, p in zip(df["ml_result"], df["ml_price"])
    ]

    # --- spread: back the side the market misprices ---------------------
    has_line = df["spread_line"].notna()
    take_home = df["pred_margin"] > df["spread_line"]
    df["ats_pick"] = np.where(has_line, np.where(take_home, df["home_team"], df["away_team"]), None)
    df["ats_line"] = np.where(has_line, np.where(take_home, -df["spread_line"], df["spread_line"]), np.nan)
    df["ats_edge"] = np.where(has_line, (df["pred_margin"] - df["spread_line"]).abs(), np.nan)
    # the model's own chance that its spread side covers
    df["ats_prob"] = np.where(has_line, norm.cdf(df["ats_edge"] / margin_sigma), np.nan)
    if "home_spread_odds" in df.columns:
        df["ats_price"] = np.where(take_home, df["home_spread_odds"], df["away_spread_odds"])
    else:
        df["ats_price"] = DEFAULT_ODDS
    # the college feed posts no spread price; -110 is assumed for settlement,
    # so it is assumed for the calibration and tiering decisions as well
    df["ats_price_assumed"] = pd.isna(df["ats_price"]) & has_line
    df["ats_price"] = pd.to_numeric(df["ats_price"], errors="coerce").fillna(DEFAULT_ODDS)
    covered = np.where(take_home, df["margin"] > df["spread_line"], df["margin"] < df["spread_line"])
    push = df["margin"] == df["spread_line"]
    df["ats_result"] = np.where(
        ~(done & has_line), None,
        np.where(push, "push", np.where(covered, "win", "loss")),
    )
    df["ats_profit"] = [
        0.0 if r in (None, "push") else
        (american_profit(p) if r == "win" else -FLAT_STAKE)
        for r, p in zip(df["ats_result"], df["ats_price"])
    ]

    # --- closing line value ---------------------------------------------
    # Did the market move toward our side after we took it? Over a season
    # this is a far better test of edge than win rate, because it is not
    # drowned in the variance of individual results. It needs an opening
    # price to compare against, which only the college feed publishes.
    if "open_spread_line" in df.columns:
        opened = df["open_spread_line"]
        moved = df["spread_line"] - opened          # positive = toward home
        df["clv_points"] = np.where(has_line & opened.notna(),
                                    np.where(take_home, moved, -moved), np.nan)
    else:
        df["clv_points"] = np.nan

    # pick categories (lock / pickem / value / upset) travel with the graded
    # frame so the same buckets shown on a game card can be scored later
    edges = df["pred_margin"] - df["spread_line"]
    df["tags"] = [
        odds.classify(p, e if pd.notna(e) else None, s if pd.notna(s) else None)
        for p, e, s in zip(df["home_win_prob"], edges, df["spread_line"])
    ]
    return df


# Tiers are set by the calibrated chance of winning, because that is what
# tracks reality. Measured on 2023-2025 (3,400 games), calibrated 85%+
# moneylines won 93% of the time in both leagues, 70-85% won about 74%,
# 55-70% about 62%, and anything the model could not separate from a coin
# flip won 45%. The old tier, keyed on expected value, did the opposite: it
# promoted the model's biggest disagreements with the price, which were its
# worst bets (41% in the NFL).
#
# A tier says how often a play like this has won, not that it is a good
# price: an 85% favourite is priced accordingly, so the return on Locks is
# about breakeven. Low risk and high return are different things, and the
# tracker reports both.
LOCK_PROB = 0.85
PICK_PROB = 0.70
LEAN_PROB = 0.55
TIER_LABELS = {"lock": "Lock", "pick": "Pick", "lean": "Lean", "pass": "Pass"}
TIER_ORDER = ["lock", "pick", "lean", "pass"]


def tier_for(prob: float) -> str:
    if pd.isna(prob):
        return "lean"
    if prob >= LOCK_PROB:
        return "lock"
    if prob >= PICK_PROB:
        return "pick"
    if prob >= LEAN_PROB:
        return "lean"
    return "pass"


def assign_tiers(graded: pd.DataFrame) -> pd.DataFrame:
    """Label each moneyline side by calibrated win chance. Spread sides are
    never more than a lean: measured across 3,000 spread picks they cover
    49-50% of the time, which is a coin flip at -110."""
    df = graded.copy()
    cal = df["ml_cal"] if "ml_cal" in df.columns else df["ml_prob"]
    cal = cal.where(cal.notna(), df["ml_prob"])
    df["ml_tier"] = [tier_for(p) for p in cal]
    df["ats_tier"] = "lean"
    return df


def tier_record(graded: pd.DataFrame, kind: str, tier: str) -> dict:
    col = f"{kind}_tier"
    if col not in graded.columns:
        return record(graded.iloc[0:0], kind)
    return record(graded[graded[col] == tier], kind)


CATEGORY_LABELS = {
    "lock": "Locks",
    "value": "Value vs market",
    "pickem": "Pick'ems",
    "upset": "Upset picks",
}


def category_records(graded: pd.DataFrame) -> list[dict]:
    """Record and ROI for each pick category, moneyline and spread separately."""
    if graded.empty or "tags" not in graded.columns:
        return []
    out = []
    for tag, label in CATEGORY_LABELS.items():
        subset = graded[[tag in t for t in graded["tags"]]]
        if subset.empty:
            continue
        row = {"tag": tag, "label": label}
        for kind in ("ml", "ats"):
            row[kind] = record(subset, kind)
        if row["ml"]["decided"] or row["ats"]["decided"]:
            out.append(row)
    return out


def record(graded: pd.DataFrame, kind: str) -> dict:
    """Win-loss-push, hit rate, profit and ROI for one bet type."""
    col = f"{kind}_result"
    played = graded[graded[col].notna()]
    wins = int((played[col] == "win").sum())
    losses = int((played[col] == "loss").sum())
    pushes = int((played[col] == "push").sum())
    decided = wins + losses
    staked = decided * FLAT_STAKE
    profit = float(played[f"{kind}_profit"].sum())
    return {
        "wins": wins, "losses": losses, "pushes": pushes,
        "n": len(played), "decided": decided,
        "hit_rate": wins / decided if decided else 0.0,
        "profit": profit,
        "roi": profit / staked if staked else 0.0,
    }


def clv_summary(graded: pd.DataFrame) -> dict:
    """Share of spread picks the market moved toward, and by how much."""
    if "clv_points" not in graded.columns:
        return {"n": 0}
    clv = graded["clv_points"].dropna()
    clv = clv[clv != 0]  # an unmoved line is neither for nor against
    if clv.empty:
        return {"n": 0}
    return {"n": int(len(clv)), "beat_rate": float((clv > 0).mean()),
            "avg_points": float(clv.mean())}


def format_record(rec: dict) -> str:
    base = f'{rec["wins"]}-{rec["losses"]}'
    if rec["pushes"]:
        base += f'-{rec["pushes"]}'
    return base


# ---------------------------------------------------------------------------
# The ledger: one row per wager
# ---------------------------------------------------------------------------
# Week records answer "how did week 5 go". They cannot answer "show me every
# losing Pixel's Pick in 2025", because by then the individual bets have been
# summed away. The ledger keeps them, so the tracking page can filter and sort
# on anything a reader might reasonably ask for.

def game_ledger(graded: pd.DataFrame, league: str, season: int,
                week: int, week_label: str) -> list[dict]:
    """Every settled moneyline and spread bet from one week."""
    rows = []
    for row in graded.itertuples():
        for kind, label in (("ml", "Moneyline"), ("ats", "Spread")):
            result = getattr(row, f"{kind}_result", None)
            if result not in ("win", "loss", "push"):
                continue
            pick = getattr(row, f"{kind}_pick", None)
            if kind == "ats" and pd.notna(getattr(row, "ats_line", None)):
                pick = f"{pick} {row.ats_line:+.1f}"
            rows.append({
                "league": league, "season": season, "week": week,
                "week_label": week_label, "type": label,
                "tier": getattr(row, f"{kind}_tier", "lean"),
                # a wager published before kickoff and frozen, as opposed to
                # a backtested one the model produced after the fact
                "live": bool(getattr(row, "frozen", False))
                        and not bool(getattr(row, "post_kick", False)),
                "matchup": f"{row.away_team} @ {row.home_team}",
                "pick": pick,
                "price": _clean_price(getattr(row, f"{kind}_price", None)),
                "prob": _clean_float(getattr(row, f"{kind}_prob", None)),
                "result": result,
                "profit": round(float(getattr(row, f"{kind}_profit", 0.0) or 0.0), 3),
            })
    return rows


def wager_ledger(pick: dict | None, league: str, season: int, week: int,
                 week_label: str, kind: str) -> list[dict]:
    """A settled Pixel's Pick or board parlay, as one ledger row."""
    if not pick or not pick.get("result"):
        return []
    legs = " + ".join(leg["detail"] for leg in pick["legs"])
    return [{
        "league": league, "season": season, "week": week,
        "week_label": week_label, "type": kind,
        "tier": "pick",
        "live": bool(pick.get("locked_at")),
        "matchup": f'{len(pick["legs"])} leg' + ("s" if len(pick["legs"]) > 1 else ""),
        "pick": legs,
        "price": round(float(pick["american"]), 0),
        "prob": round(float(pick["prob"]), 4),
        "result": pick["result"],
        "profit": round(float(pick["profit"]), 3),
    }]


def _clean_price(value):
    if value is None or pd.isna(value) or abs(value) < 100:
        return DEFAULT_ODDS
    return round(float(value), 0)


def tier_hit_rates(graded: pd.DataFrame) -> dict:
    """Realised win rate and ROI per moneyline tier, for the breakdowns."""
    out = {}
    if graded is None or graded.empty or "ml_tier" not in graded.columns:
        return out
    for tier in TIER_ORDER:
        rec = record(graded[graded["ml_tier"] == tier], "ml")
        if rec["decided"]:
            out[tier] = rec
    return out


def _clean_float(value):
    return None if value is None or pd.isna(value) else round(float(value), 4)
