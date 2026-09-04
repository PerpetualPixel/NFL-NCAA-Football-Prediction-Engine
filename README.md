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

**Picks.** Every game gets a projected margin, a win probability, a moneyline
pick and a spread pick. Sides where the calibrated probability beats the price
are labelled **Picks**; everything else is a **Lean** — an opinion with all its
reasoning shown, but not put forward as a bet.

**Pixel's Pick.** One headline play per league per week, priced −175 or better.
A short-priced favourite is only ever published parlayed up to that floor, with
every leg named and a written case for it.

**Parlay board.** The most confident moneylines left over, stacked in
confidence order until the combined price reaches +100 or better, grouped by
the day they play.

**Breakdowns.** Per game: unit grades against the rest of the league, the
target hierarchy and backfield split with per-game volume and average depth of
target, quarterback status, injury burden, weather, line movement, and a
written game script.

**Tracking.** Records and ROI at the prices the books actually posted, for
moneylines and spreads separately, by pick category, and — the number that
matters most — closing line value.

## When picks appear

| Stage | Timing | Meaning |
|---|---|---|
| Scheduled | more than 3 days out | Nothing published; a pick made now could not know who is hurt |
| **Lean** | 3 days out | Parlays built, reasoning written, explicitly provisional |
| **Locked** | inside 2 hours of kickoff | Final, with inactives and the closing number known |

The build runs hourly so the locked version lands inside that window.
GitHub throttles scheduled workflows on shared runners, so timing is
best-effort; a card only ever reads "locked" if a build actually ran inside
the window.

## Architecture

One shared pipeline, two leagues (`engine/config.py` holds the per-league tuning):

| Module | Does |
|---|---|
| `data/ingest.py` | Cached parquet downloads: play-by-play, schedules, betting lines, injuries, snap counts, rosters |
| `features/ratings.py` | Weighted ridge solve: `margin = strength(home) − strength(away) + HFA`, recency-decayed |
| `pipeline.py` | Walk-forward features, unit ratings, quarterback values, injury burden, player usage |
| `calibrate.py` | Logistic fit on the model's log-odds *and* the price's, so staking uses a probability that accounts for the market |
| `model.py` | Ridge margin model and win probabilities |
| `pixel.py` | Pixel's Pick and the parlay board, with the price floor as a hard constraint |
| `tracking.py` | Grading, records, ROI, closing line value |
| `site.py` | Static site generation |

Every feature for a game is computed using only games that finished before it.
No rating ever sees the game it is predicting.

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

**It does not beat the market, and the tracking page says so.** Three
independent measurements agree:

- Given the closing line, the model's own prediction adds nothing — the
  optimal blend weight on it is 0.00.
- The further the model's probability strays above the price's, the worse the
  bet does: NFL moneylines go from roughly breakeven when the model agrees
  with the price to −44% and −65% in the two most disagreeable buckets.
- Closing line value sits at 34–39% across three seasons and ~2,000 college
  picks, meaning the market moves *against* these picks about two-thirds of
  the time.

The engine predicts football well. It has not been shown to predict it better
than the people setting the prices, which is a different and much harder bar.
Picks are published with their record attached so that stays visible rather
than implied.

## Known limits

- **Coverage matchups are not in free data.** The breakdown reports who
  commands targets and how the opposing unit grades; it does not claim to know
  which corner shadows whom.
- **Line movement is college-only.** The NFL feed publishes one number, not an
  opening price, so that section is absent rather than invented.
- **The college feed records completions, not attempts**, so its receiving
  shares read as receptions and no target counts are claimed.
- Picks are analytical output, not betting advice.
