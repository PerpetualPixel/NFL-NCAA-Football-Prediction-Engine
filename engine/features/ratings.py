"""Opponent-adjusted ratings via weighted ridge regression.

The core solve, used for every rating in the system:

    observed value  =  strength(entity_plus) - strength(entity_minus)
                       + hfa * home_indicator

For team power ratings the observation is home margin (entity_plus = home
team, entity_minus = away team). For NFL unit ratings the observation is a
team's pass/rush EPA per play in one game (entity_plus = that offense,
entity_minus = the defense it faced). Ridge regularization pulls thin
samples toward league average, and exponential recency weights make the
ratings track current form instead of season averages.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.linear_model import Ridge


def solve_ratings(
    plus: pd.Series,
    minus: pd.Series,
    y: np.ndarray,
    weights: np.ndarray,
    alpha: float,
    hfa_indicator: np.ndarray | None = None,
) -> tuple[dict[str, float], float]:
    """Solve for entity strengths. Returns (ratings, hfa).

    ratings are centered so the league average is 0; hfa is the fitted
    home-field term (0.0 when hfa_indicator is None).
    """
    entities = sorted(set(plus) | set(minus))
    idx = {t: i for i, t in enumerate(entities)}
    n_rows, n_ent = len(y), len(entities)

    rows = np.arange(n_rows)
    data_cols = [
        sparse.csr_matrix(
            (np.ones(n_rows), (rows, plus.map(idx).to_numpy())),
            shape=(n_rows, n_ent),
        )
        - sparse.csr_matrix(
            (np.ones(n_rows), (rows, minus.map(idx).to_numpy())),
            shape=(n_rows, n_ent),
        )
    ]
    if hfa_indicator is not None:
        data_cols.append(sparse.csr_matrix(hfa_indicator.reshape(-1, 1)))
    X = sparse.hstack(data_cols, format="csr")

    model = Ridge(alpha=alpha, fit_intercept=False, solver="sparse_cg")
    model.fit(X, y, sample_weight=weights)

    coefs = model.coef_
    strengths = coefs[:n_ent]
    strengths = strengths - strengths.mean()
    hfa = float(coefs[n_ent]) if hfa_indicator is not None else 0.0
    return {t: float(strengths[idx[t]]) for t in entities}, hfa


def recency_weights(
    season: pd.Series,
    week: pd.Series,
    asof_season: int,
    asof_week: int,
    weekly_decay: float,
    prior_season_weight: float,
    weeks_per_season: int = 22,
) -> np.ndarray:
    """Exponential decay by staleness, with a flat multiplier per season back."""
    season_diff = (asof_season - season).to_numpy()
    week_diff = np.where(
        season_diff == 0,
        asof_week - week.to_numpy(),
        # approximate cross-season staleness in weeks
        season_diff * weeks_per_season + (asof_week - week.to_numpy()),
    )
    week_diff = np.clip(week_diff, 0, None)
    return (weekly_decay ** week_diff) * (prior_season_weight ** season_diff)
