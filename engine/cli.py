"""Command-line entry points.

    python -m engine.cli ingest   --league nfl|ncaa
    python -m engine.cli backtest --league nfl|ncaa [--start-season YYYY]
    python -m engine.cli predict  --league nfl|ncaa --season YYYY --week N
"""
from __future__ import annotations

import argparse
import json

import pandas as pd

from . import model as model_mod
from . import pipeline, report
from .config import LEAGUES
from .data import ingest


def _load_league_inputs(league: str, refresh: bool = False):
    games = pipeline.load_games(league, refresh=refresh)
    unit_stats = None
    if league == "nfl":
        seasons = sorted(games.loc[games["completed"], "season"].unique().tolist())
        pbp = ingest.load_nfl_pbp(seasons, refresh_latest=refresh)
        unit_stats = pipeline.nfl_unit_game_stats(pbp)
    return games, unit_stats


def cmd_ingest(args):
    games, unit_stats = _load_league_inputs(args.league, refresh=True)
    print(f"{args.league}: {len(games)} games, seasons "
          f"{games['season'].min()}–{games['season'].max()}")
    if unit_stats is not None:
        print(f"unit stats: {len(unit_stats)} team-game rows")


def cmd_backtest(args):
    cfg = LEAGUES[args.league]
    games, unit_stats = _load_league_inputs(args.league)
    feats = pipeline.build_walk_forward_features(
        args.league, games, unit_stats, start_season=args.start_season
    )
    preds = model_mod.walk_forward_predict(feats, cfg)
    metrics = model_mod.evaluate(preds)
    print(json.dumps(metrics, indent=2))
    by_season = preds.groupby("season").apply(
        lambda g: pd.Series(model_mod.evaluate(g)), include_groups=False
    )
    print(by_season.to_string())


def cmd_predict(args):
    cfg = LEAGUES[args.league]
    games, unit_stats = _load_league_inputs(args.league, refresh=args.refresh)
    feats = pipeline.build_walk_forward_features(args.league, games, unit_stats)
    target = feats[(feats["season"] == args.season) & (feats["week"] == args.week)]
    if target.empty:
        raise SystemExit(f"no games found for {args.league} {args.season} week {args.week}")
    train = feats[
        feats["completed"]
        & ((feats["season"] < args.season)
           | ((feats["season"] == args.season) & (feats["week"] < args.week)))
    ]
    fitted = model_mod.fit_margin_model(train)
    target = target.copy()
    target["pred_margin"] = fitted.predict(model_mod.design_matrix(target))
    from scipy.stats import norm
    target["home_win_prob"] = norm.cdf(target["pred_margin"] / cfg.margin_sigma)
    text = report.render_week(target, args.league, args.season, args.week)
    path = report.write_report(text, args.league, args.season, args.week)
    print(text)
    print(f"\nsaved: {path}")


def main():
    p = argparse.ArgumentParser(prog="engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, fn in [("ingest", cmd_ingest), ("backtest", cmd_backtest), ("predict", cmd_predict)]:
        sp = sub.add_parser(name)
        sp.add_argument("--league", choices=list(LEAGUES), required=True)
        sp.set_defaults(fn=fn)
        if name == "backtest":
            sp.add_argument("--start-season", type=int, default=None)
        if name == "predict":
            sp.add_argument("--season", type=int, required=True)
            sp.add_argument("--week", type=int, required=True)
            sp.add_argument("--refresh", action="store_true")

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
