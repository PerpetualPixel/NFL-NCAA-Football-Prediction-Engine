# NFL / NCAA Football Prediction Engine

A layered, walk-forward-honest prediction engine for NFL and NCAA football.
Every data source is **free and requires no API key** — everything comes from
public GitHub-hosted mirrors (nflverse and sportsdataverse).

## What it does

For every game it produces a pre-game breakdown: projected margin, win
probability, a pick, opponent-adjusted power ratings, and (NFL) the
unit-vs-unit matchup table — pass offense vs pass defense, rush offense vs
rush defense in EPA/play — that explains where the edge comes from. Where
market lines exist it shows the model-vs-market disagreement.

## Architecture

One shared pipeline, two leagues (`engine/config.py` holds the per-league
tuning):

1. **Ingestion** (`engine/data/ingest.py`) — cached parquet downloads:
   - NFL: nflverse play-by-play, schedules (with spread lines, rest days,
     starting QBs, coaches, stadium weather)
   - NCAA: sportsdataverse schedules 2016–present (ESPN schedule mirror
     backfills seasons whose scores are stale in the primary mirror)
2. **Ratings** (`engine/features/ratings.py`) — weighted ridge regression
   solves `margin = strength(home) − strength(away) + HFA`, with exponential
   recency decay and reduced prior-season weight. The same solver produces
   NFL unit ratings from play-by-play EPA with offense and defense as
   separate entities.
3. **Features** (`engine/pipeline.py`) — for each game, ratings are computed
   **as of kickoff** using only earlier games. No leakage, ever.
4. **Margin model** (`engine/model.py`) — a ridge blend over rating
   differential, unit matchup nets, rest differential, and home field,
   trained walk-forward. Win probability via a normal margin distribution.
5. **Reports** (`engine/report.py`) — markdown per-week breakdowns in
   `reports/`.

## Usage

```bash
pip install -r requirements.txt

python -m engine.cli ingest   --league nfl          # download + cache data
python -m engine.cli backtest --league nfl --start-season 2021
python -m engine.cli predict  --league nfl --season 2026 --week 1
python -m engine.cli predict  --league ncaa --season 2025 --week 10
```

## Honest baseline results (walk-forward, 2021–2025, from week 5)

| League | MAE (pts) | Straight-up | Notes |
|---|---|---|---|
| NFL  | 10.15 | 63.1% | closing line MAE on same games: 9.72 |
| NCAA | 12.78 | 70.0% | |

The NFL model sits ~0.4 pts behind the closing Vegas line — the expected
result for a ratings-only model evaluated honestly. The gap is the roadmap:
the market's remaining edge is mostly injuries/QB status and late news.

## Roadmap

- [ ] Injury adjustment: player-value layer (nflverse injuries + depth
      charts are already free/keyless), QB-out adjustments first
- [ ] NCAA betting lines joined from the bundled `cfb_line_odds` file for
      ATS evaluation
- [ ] Weather forecasts for upcoming games (Open-Meteo, free, no key)
- [ ] NCAA play-by-play efficiency ratings (free CFBD API key unlocks
      2023+ pbp; mirrors cover 2014–2022)
- [ ] Quantile/GBM margin model once features go nonlinear
