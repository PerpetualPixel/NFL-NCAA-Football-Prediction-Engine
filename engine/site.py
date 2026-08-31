"""Static site generator for GitHub Pages.

Renders the upcoming week's picks for each league (plus a graded look at the
most recent completed week) into a small static site in site/.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from . import model as model_mod
from . import pipeline
from .config import LEAGUES

SITE_DIR = Path(__file__).resolve().parents[1] / "site"

CSS = """
:root {
  --bg: #f6f7f9; --card: #ffffff; --ink: #1a1d21; --muted: #6b7280;
  --line: #e5e7eb; --accent: #1d4ed8; --good: #047857; --bad: #b91c1c;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #101317; --card: #1a1f26; --ink: #e7eaee; --muted: #9aa3af;
    --line: #2a3138; --accent: #7aa2ff; --good: #34d399; --bad: #f87171;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 880px; margin: 0 auto; padding: 24px 16px 64px; }
header.site { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; margin-bottom: 8px; }
header.site h1 { font-size: 1.5rem; margin: 0; }
header.site nav a { margin-right: 12px; color: var(--accent); text-decoration: none; font-weight: 600; }
.stamp { color: var(--muted); font-size: 0.85rem; margin-bottom: 24px; }
h2 { font-size: 1.15rem; margin: 32px 0 12px; }
.card {
  background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 16px 18px; margin-bottom: 14px;
}
.card .teams { font-size: 1.05rem; font-weight: 700; }
.card .pick { margin: 6px 0 2px; }
.card .pick strong { color: var(--accent); }
.meta { color: var(--muted); font-size: 0.85rem; }
.edge-pos { color: var(--good); font-weight: 600; }
.edge-neg { color: var(--bad); font-weight: 600; }
table.units { border-collapse: collapse; margin-top: 10px; font-size: 0.85rem; width: 100%; max-width: 420px; }
table.units th, table.units td { text-align: right; padding: 3px 10px; border-bottom: 1px solid var(--line); }
table.units th:first-child, table.units td:first-child { text-align: left; }
.result-hit { color: var(--good); font-weight: 700; }
.result-miss { color: var(--bad); font-weight: 700; }
.leagues { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }
.leagues a.card { display: block; text-decoration: none; color: inherit; }
footer { margin-top: 48px; color: var(--muted); font-size: 0.8rem; }
"""


def _page(title: str, body: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{CSS}</style></head>
<body><div class="wrap">
<header class="site"><h1>Gridiron Engine</h1>
<nav><a href="index.html">Home</a><a href="nfl.html">NFL</a><a href="ncaa.html">NCAA</a></nav></header>
<div class="stamp">Updated {stamp} &middot; free public data (nflverse / sportsdataverse) &middot; walk-forward model, no leakage</div>
{body}
<footer>Model picks are analytical output, not betting advice. Ratings are opponent-adjusted
ridge estimates with recency decay; win probabilities from a normal margin model.</footer>
</div></body></html>"""


def _unit_table(row: pd.Series) -> str:
    if "home_off_pass_epa" not in row.index or pd.isna(row.get("home_off_pass_epa")):
        return ""
    f = lambda v: f"{v:+.3f}"
    return f"""<table class="units">
<tr><th>EPA/play vs avg</th><th>{row.home_team}</th><th>{row.away_team}</th></tr>
<tr><td>Pass offense</td><td>{f(row.home_off_pass_epa)}</td><td>{f(row.away_off_pass_epa)}</td></tr>
<tr><td>Pass defense</td><td>{f(row.home_def_pass_epa)}</td><td>{f(row.away_def_pass_epa)}</td></tr>
<tr><td>Rush offense</td><td>{f(row.home_off_rush_epa)}</td><td>{f(row.away_off_rush_epa)}</td></tr>
<tr><td>Rush defense</td><td>{f(row.home_def_rush_epa)}</td><td>{f(row.away_def_rush_epa)}</td></tr>
</table>"""


def _game_card(row: pd.Series, graded: bool) -> str:
    pick = row.home_team if row.pred_margin > 0 else row.away_team
    by = abs(row.pred_margin)
    prob = row.home_win_prob if row.pred_margin > 0 else 1 - row.home_win_prob
    market = ""
    if pd.notna(row.get("spread_line")):
        edge = row.pred_margin - row.spread_line
        side = row.home_team if edge > 0 else row.away_team
        cls = "edge-pos" if abs(edge) >= 2 else "meta"
        market = (f'<div class="meta">Market: {row.home_team} {-row.spread_line:+.1f} '
                  f'&middot; <span class="{cls}">model {edge:+.1f} toward {side}</span></div>')
    result = ""
    if graded and pd.notna(row.get("margin")):
        actual_winner = row.home_team if row.margin > 0 else row.away_team
        hit = actual_winner == pick
        cls = "result-hit" if hit else "result-miss"
        result = (f'<div class="meta">Final: {row.home_team} {int(row.home_score) if pd.notna(row.get("home_score")) else "?"}'
                  f'&ndash;{int(row.away_score) if pd.notna(row.get("away_score")) else "?"} '
                  f'&middot; <span class="{cls}">{"HIT" if hit else "MISS"}</span></div>')
    neutral = " (neutral site)" if row.neutral else ""
    return f"""<div class="card">
<div class="teams">{row.away_team} @ {row.home_team}{neutral}</div>
<div class="pick">Pick: <strong>{pick} by {by:.1f}</strong> &middot; {prob:.0%} win probability</div>
<div class="meta">Power ratings: {row.home_team} {row.home_rating:+.1f}, {row.away_team} {row.away_rating:+.1f}</div>
{market}{result}{_unit_table(row)}
</div>"""


def _week_key(df: pd.DataFrame) -> list[tuple[int, int]]:
    return sorted(df[["season", "week"]].drop_duplicates().itertuples(index=False, name=None))


def build_league_page(league: str, refresh: bool) -> tuple[str, str]:
    """Returns (html_body, summary_line)."""
    cfg = LEAGUES[league]
    games, unit_stats = pipeline.load_league_inputs(league, refresh=refresh, recent_only=True)
    latest_season = int(games.loc[games["completed"], "season"].max())
    feats = pipeline.build_walk_forward_features(
        league, games, unit_stats, start_season=latest_season - 1
    )
    # attach scores (for grading) and dates (to pick the true upcoming week)
    feats = feats.merge(
        games[["game_id", "home_score", "away_score", "gameday"]],
        on="game_id", how="left",
    )

    body, summary = [], f"{league.upper()}: no upcoming games found"
    gameday = pd.to_datetime(feats["gameday"], utc=True, errors="coerce")
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=1)
    # date filter keeps canceled/no-score relics from masquerading as upcoming
    upcoming = feats[~feats["completed"].astype(bool) & (gameday >= cutoff)]
    if not upcoming.empty:
        season, week = _week_key(upcoming)[0]
        preds = model_mod.predict_week(feats, cfg, season, week)
        preds = preds[~preds["completed"].astype(bool)]
        if not preds.empty:
            body.append(f"<h2>Upcoming picks &mdash; season {season}, week {week}</h2>")
            for _, row in preds.sort_values("pred_margin", key=abs, ascending=False).iterrows():
                body.append(_game_card(row, graded=False))
            summary = (f"{league.upper()}: week {week} &mdash; {len(preds)} games, "
                       f"top pick {preds.loc[preds['pred_margin'].abs().idxmax(), 'home_team']}")

    if not body:
        body.append('<h2>Upcoming picks</h2><div class="card"><div class="meta">'
                    "No upcoming games in the schedule mirror yet &mdash; picks appear "
                    "here automatically once next week's slate is published.</div></div>")

    done = feats[feats["completed"].astype(bool)]
    if not done.empty:
        season, week = _week_key(done)[-1]
        graded = model_mod.predict_week(feats, cfg, season, week)
        graded = graded[graded["completed"].astype(bool)]
        if not graded.empty:
            hits = ((graded["pred_margin"] > 0) == (graded["margin"] > 0)).sum()
            body.append(f"<h2>Last completed week graded &mdash; season {season}, week {week} "
                        f"({hits}/{len(graded)} straight-up)</h2>")
            for _, row in graded.sort_values("pred_margin", key=abs, ascending=False).iterrows():
                body.append(_game_card(row, graded=True))

    return "\n".join(body), summary


def build_site(out_dir: Path = SITE_DIR, refresh: bool = True) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for league in ("nfl", "ncaa"):
        body, summary = build_league_page(league, refresh)
        (out_dir / f"{league}.html").write_text(
            _page(f"{league.upper()} picks — Gridiron Engine", body)
        )
        summaries.append((league, summary))

    cards = "\n".join(
        f'<a class="card" href="{league}.html"><div class="teams">{league.upper()}</div>'
        f'<div class="meta">{summary}</div></a>'
        for league, summary in summaries
    )
    index_body = f"""
<h2>Weekly picks, powered by opponent-adjusted ratings</h2>
<div class="leagues">{cards}</div>
<h2>How it works</h2>
<div class="card"><div class="meta">
Team power ratings and (NFL) pass/rush unit ratings are solved by weighted ridge
regression over recent seasons with recency decay, using only games completed
before kickoff. A walk-forward ridge blend converts rating differentials, unit
matchups, rest, and home field into a projected margin and win probability.
Honest backtest (2021&ndash;2025, from week 5): NFL 10.15 MAE / 63% straight-up
(closing line: 9.72 MAE); NCAA 12.78 MAE / 70% straight-up.
</div></div>"""
    (out_dir / "index.html").write_text(_page("Gridiron Engine", index_body))
    (out_dir / ".nojekyll").write_text("")
    return out_dir
