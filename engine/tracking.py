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

FLAT_STAKE = 1.0
DEFAULT_ODDS = -110.0


def american_profit(odds: float, stake: float = FLAT_STAKE) -> float:
    """Profit on a winning bet at American odds (loss is simply -stake)."""
    if pd.isna(odds):
        odds = DEFAULT_ODDS
    return stake * (odds / 100.0 if odds > 0 else 100.0 / abs(odds))


def grade(preds: pd.DataFrame) -> pd.DataFrame:
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
    df["ml_result"] = np.where(
        ~done, None, np.where(df["margin"] == 0, "push", np.where(ml_won, "win", "loss"))
    )
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
    if "home_spread_odds" in df.columns:
        df["ats_price"] = np.where(take_home, df["home_spread_odds"], df["away_spread_odds"])
    else:
        df["ats_price"] = DEFAULT_ODDS
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
    return df


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


def format_record(rec: dict) -> str:
    base = f'{rec["wins"]}-{rec["losses"]}'
    if rec["pushes"]:
        base += f'-{rec["pushes"]}'
    return base
