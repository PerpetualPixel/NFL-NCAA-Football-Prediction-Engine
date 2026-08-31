"""Margin model: walk-forward ridge blend over the as-of-kickoff features.

The power-rating difference does most of the work; the blend learns how much
extra signal the unit matchups, rest, and home field carry on top of it —
trained only on games that predate the game being predicted.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.linear_model import Ridge

from .config import LeagueConfig

FEATURE_COLS = ["rating_diff", "hfa_ind", "rest_diff", "net_pass_epa", "net_rush_epa"]
MIN_TRAIN_ROWS = 200


def design_matrix(feats: pd.DataFrame) -> np.ndarray:
    df = feats.copy()
    df["hfa_ind"] = 1 - df["neutral"]
    for col in FEATURE_COLS:
        if col not in df.columns:
            df[col] = 0.0
    return df[FEATURE_COLS].fillna(0.0).to_numpy(float)


def fit_margin_model(train: pd.DataFrame) -> Ridge:
    model = Ridge(alpha=1.0, fit_intercept=False)
    model.fit(design_matrix(train), train["margin"].to_numpy(float))
    return model


def walk_forward_predict(feats: pd.DataFrame, cfg: LeagueConfig) -> pd.DataFrame:
    """Predict every completed game using a model trained on prior games only."""
    done = feats[feats["completed"]].sort_values(["season", "week"]).reset_index(drop=True)
    preds = np.full(len(done), np.nan)
    for (season, week), idx in done.groupby(["season", "week"]).groups.items():
        train = done[
            (done["season"] < season)
            | ((done["season"] == season) & (done["week"] < week))
        ]
        if len(train) < MIN_TRAIN_ROWS or week <= cfg.min_weeks_before_eval:
            continue
        model = fit_margin_model(train)
        preds[np.asarray(idx)] = model.predict(design_matrix(done.loc[idx]))
    out = done.assign(pred_margin=preds).dropna(subset=["pred_margin"])
    out["home_win_prob"] = norm.cdf(out["pred_margin"] / cfg.margin_sigma)
    return out


def predict_week(feats: pd.DataFrame, cfg: LeagueConfig, season: int, week: int) -> pd.DataFrame:
    """Fit on everything before (season, week) and predict that week's games."""
    target = feats[(feats["season"] == season) & (feats["week"] == week)]
    train = feats[
        feats["completed"]
        & ((feats["season"] < season)
           | ((feats["season"] == season) & (feats["week"] < week)))
    ]
    if target.empty or len(train) < MIN_TRAIN_ROWS:
        return target.iloc[0:0]
    fitted = fit_margin_model(train)
    out = target.copy()
    out["pred_margin"] = fitted.predict(design_matrix(out))
    out["home_win_prob"] = norm.cdf(out["pred_margin"] / cfg.margin_sigma)
    return out


def evaluate(preds: pd.DataFrame) -> dict:
    err = preds["pred_margin"] - preds["margin"]
    home_won = (preds["margin"] > 0).astype(float)
    metrics = {
        "n_games": int(len(preds)),
        "mae": float(err.abs().mean()),
        "rmse": float(np.sqrt((err ** 2).mean())),
        "su_accuracy": float(((preds["pred_margin"] > 0) == (preds["margin"] > 0)).mean()),
        "brier": float(((preds["home_win_prob"] - home_won) ** 2).mean()),
    }
    lined = preds.dropna(subset=["spread_line"])
    lined = lined[lined["margin"] != lined["spread_line"]]  # drop pushes
    if len(lined):
        pick_home = lined["pred_margin"] > lined["spread_line"]
        home_covered = lined["margin"] > lined["spread_line"]
        metrics["ats_record"] = float((pick_home == home_covered).mean())
        metrics["ats_n"] = int(len(lined))
        metrics["market_mae"] = float((lined["spread_line"] - lined["margin"]).abs().mean())
    return metrics
