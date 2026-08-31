"""Turning model confidence into a probability worth betting on.

The model's raw probabilities are well calibrated on average, but that is not
the same as being useful at a price. Measured across 2021-2025, the further
the model's opinion strays from the market's, the worse it does: on NFL
moneylines, agreeing with the price returned about breakeven while the
biggest disagreements lost 40-65% of stake. Confident disagreement is the
model's worst habit, not its edge.

So the probability used for staking decisions is not the model's own. A
logistic model is fit, walk-forward, on two inputs — the model's log-odds and
the price's implied log-odds — against what actually happened. It learns how
much the model's opinion is worth once the price is known, which is usually
"a little", and it learns any systematic tilt in the prices themselves.

Fitting only on games that finished before the week being predicted keeps
this as honest as the rest of the pipeline.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

MIN_TRAIN = 150


def logit(p) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def implied_probability(american) -> np.ndarray:
    odds = np.asarray(american, dtype=float)
    return np.where(odds > 0, 100.0 / (odds + 100.0), np.abs(odds) / (np.abs(odds) + 100.0))


def _design(model_prob, price) -> np.ndarray:
    return np.column_stack([logit(model_prob), logit(implied_probability(price))])


def fit_and_apply(history: pd.DataFrame, target: pd.DataFrame, kind: str) -> pd.Series:
    """Calibrated win probability for `target`, fit on `history` alone.

    Returns the model's own probability unchanged when there is not yet
    enough settled history to fit on.
    """
    prob_col, price_col, result_col = f"{kind}_prob", f"{kind}_price", f"{kind}_result"
    if target.empty:
        return pd.Series(dtype=float)
    fallback = target[prob_col] if prob_col in target.columns else pd.Series(
        0.5, index=target.index)

    usable = history.dropna(subset=[prob_col, price_col]) if len(history) else history
    if len(usable) < MIN_TRAIN:
        return fallback
    usable = usable[usable[result_col].isin(["win", "loss"])]
    if len(usable) < MIN_TRAIN or usable[result_col].nunique() < 2:
        return fallback

    model = LogisticRegression(max_iter=1000)
    model.fit(_design(usable[prob_col], usable[price_col]),
              (usable[result_col] == "win").astype(int))

    ok = target[prob_col].notna() & target[price_col].notna()
    out = fallback.copy().astype(float)
    if ok.any():
        probs = model.predict_proba(_design(target.loc[ok, prob_col],
                                            target.loc[ok, price_col]))[:, 1]
        out.loc[ok] = probs
    return out


def expected_value(prob, american) -> np.ndarray:
    """Profit per unit staked at these odds, under this probability."""
    odds = np.asarray(american, dtype=float)
    payout = np.where(odds > 0, odds / 100.0, 100.0 / np.abs(odds))
    p = np.asarray(prob, dtype=float)
    return p * payout - (1 - p)
