# NFL / NCAA Football Prediction Engine

### 🏈 **[perpetualpixel.github.io/NFL-NCAA-Football-Prediction-Engine](https://perpetualpixel.github.io/NFL-NCAA-Football-Prediction-Engine/)**

Weekly picks, full game breakdowns and a running record for both leagues.
The site rebuilds itself hourly from public data — no API keys, no paid feeds,
nothing to run yourself.

---

A layered, walk-forward-honest prediction engine for NFL and college football.
Every source is free and keyless: public GitHub-hosted mirrors maintained by
[nflverse](https://github.com/nflverse/nflverse-data) and
[sportsdataverse](https://github.com/sportsdataverse).

## What the site shows

**Tiers.** Every moneyline is tiered by its *calibrated* chance of winning —
the model's number after a walk-forward correction against three seasons of
results and the market's price:

| Tier | Calibrated win chance | Measured 2023–2025 (NFL / college) |
|---|---|---|
| **Lock** | 85%+ | 94% / 93% — ROI about breakeven |
| **Pick** | 70–85% | ~73% / ~76% |
| **Lean** | 55–70% | ~62% / ~64% — a tilt, not a bet |
| **Pass** | under 55% | coin flips; shown, never counted as a pick |

A tier says how often plays like it have won. It does not say the price is
good: heavy favourites are priced as such, so Locks win almost always and
pay very little. Spread sides are always leans — they cover about half the
time.

**Pixel's Pick.** One headline play per league per week, priced −175 or better.
A short-priced favourite is only ever published parlayed up to that floor, with
every leg named and a written case for it. Because −175 implies about 64%,
this rung cannot hit like a Lock does; it is the best play *at that price*.

**Parlay board.** The most confident moneylines left over (65%+ calibrated),
stacked in confidence order until the combined price reaches +100 or better,
grouped by the day they play.

**Breakdowns.** Per game: why the side carries its tier, in the tracker's own
numbers; the projected score against the market total; the game script; the
final injury report with names and designations; the kickoff forecast; recent
form; unit grades against the rest of the league; the target hierarchy and
backfield split; quarterback status; line movement.

**Tracking.** One page per league. Every settled wager, filterable by season,
week, bet type, tier, result, and whether it was published live before
kickoff or produced by the backtest; records and ROI at the prices the books
posted; closing line value.

## When picks appear

| Stage | When | Meaning |
|---|---|---|
| Scheduled | more than a week out | Nothing published |
| **Lean** | one week out | The model's current read, refreshed on every build as prices, injuries and forecasts change |
| **Locked** | final injury report + kickoff forecast in, or two hours before kickoff, whichever is first | Final. Frozen and never changed; this is what the tracker grades |

Locked picks are written to a state file that ships with the site and is read
back on every build, so a later run cannot quietly move a number that was
already published. A game that reaches kickoff without a locked build keeps
its last lean as the pick of record, clearly labelled; a game first published
after kickoff is shown but never counted.

The build runs twice an hour off the top of the hour (GitHub delays runs at
:00, and in practice an hourly schedule fired every four to five hours).
Most locks come from the injury report and forecast rather than the clock,
so they do not depend on a build landing inside the two-hour window.

**Odds.** Archived lines come from the nflverse and cfbfastR mirrors; for the
games about to be played, ESPN's public scoreboard supplies the current
spread and moneylines and the card names the book and the time. The college
mirror posts no spread price, so −110 is assumed there and the card says so.
A moneyline the book never posted is not graded at all.

## Architecture

One shared pipeline, two leagues (`engine/config.py` holds the per-league tuning):

| Module | Does |
|---|---|
| `data/ingest.py` | Cached parquet downloads: play-by-play, schedules, betting lines, injuries, snap counts, rosters |
| `data/espn_odds.py` | Current spread and moneylines from ESPN's keyless scoreboard, laid over the archived lines |
| `weather.py` | Real kickoff instants, venue coordinates, Open-Meteo kickoff forecasts |
| `features/ratings.py` | Weighted ridge solve: `margin = strength(home) − strength(away) + HFA`, recency-decayed |
| `pipeline.py` | Walk-forward features, unit ratings, quarterback values, injury burden, player usage |
| `calibrate.py` | Logistic fit on the model's log-odds *and* the price's, so staking uses a probability that accounts for the market |
| `model.py` | Ridge margin model and win probabilities |
| `locks.py` | Release stages (pending → lean → locked) and freezing published picks |
| `pixel.py` | Pixel's Pick and the parlay board, with the price floor as a hard constraint |
| `tracking.py` | Grading, tiers, records, ROI, closing line value, the wager ledger |
| `analysis.py` | The written breakdowns |
| `site.py` | Static site generation |
| `feed.py` | `picks.json` — the same week written for a program |

Every feature for a game is computed using only games that finished before it.
No rating ever sees the game it is predicting.

## The picks feed

Everything the week page shows is also published as JSON, so another site can
read the engine's picks rather than scrape its markup:

**[`picks.json`](https://perpetualpixel.github.io/NFL-NCAA-Football-Prediction-Engine/picks.json)**
— rebuilt on every run, alongside the pages.

It carries the current week of each league and the one after it (books post
next week's games before this week's are all played), one entry per released
game: the moneyline and spread picks, the tier, the calibrated probability, the
market numbers the pick was priced against, the release stage, and every
breakdown paragraph from the card as plain text. Pending games are not in it —
an unreleased pick is not a pick.

Each pick also carries `agreement`: the model's calibrated probability, the
price's implied probability, and the signed gap between them. That field exists
because of the measurements in *How it actually performs* below — the model's
largest disagreements with the market have been its worst bets, so a consumer
that treats a big positive gap as an edge is reading the feed backwards. The
same warning ships inside every build as the feed's `disclosure` string.

`feed_version` is bumped whenever a field changes meaning or goes away; new
fields can appear without one. Every field but the identifiers can be null.

[perpetualpicks.com](https://perpetualpicks.com) reads this feed to lay the
engine's read over its own live odds board — see `docs/gridiron.js` in
[PerpetualCode](https://github.com/PerpetualPixel/PerpetualCode).

## Usage

```bash
pip install -r requirements.txt

python -m engine.cli ingest   --league nfl          # download and cache
python -m engine.cli backtest --league nfl --start-season 2021
python -m engine.cli predict  --league ncaa --season 2025 --week 10
python -m engine.cli site                           # build the full site
```

## How it actually performs

Walk-forward, 2021–2025, from week 5:

| League | Margin MAE | Straight-up | Benchmark |
|---|---|---|---|
| NFL | 9.97 pts | 64.9% | closing line: 9.72 MAE on the same games |
| NCAA | 12.78 pts | 70.0% | — |

**Low risk and high return are different things, and only one of them is on
offer.** The calibrated tiers deliver the win rates in the table above — Locks
won 93–94% of the time across 3,400 graded games — but at the prices heavy
favourites carry, that is roughly breakeven. Three independent measurements
say the model does not beat the market:

- Given the closing line, the model's own prediction adds nothing — the
  optimal blend weight on it is 0.00.
- The further the model's probability strays above the price's, the worse the
  bet does: NFL moneylines go from roughly breakeven when the model agrees
  with the price to −44% and −65% in the two most disagreeable buckets. The
  old "Pick" tier, which promoted exactly those disagreements, hit 41%.
- Closing line value sits at 34–39% across three seasons and ~2,000 college
  picks, meaning the market moves *against* these picks about two-thirds of
  the time.

Plus-money parlays cannot hit much above 50% by construction, whatever the
legs; the board is published because it was asked for, with its record next
to it. The engine predicts football well. It has not been shown to predict it
better than the people setting the prices, which is a different and much
harder bar. Picks are published with their record attached so that stays
visible rather than implied.

## Known limits

- **Coverage matchups are not in free data.** The breakdown reports who
  commands targets and how the opposing unit grades; it does not claim to know
  which corner shadows whom.
- **College has no injury feed**, so college picks lock on the day-before
  forecast (or the two-hour clock) and the injury section is NFL-only.
- **The college mirror publishes no spread prices** and, as of September 2026,
  no 2026 lines at all; live prices come from ESPN's scoreboard, which is
  best-effort.
- **The college feed records completions, not attempts**, so its receiving
  shares read as receptions and no target counts are claimed.
- Picks are analytical output, not betting advice.
