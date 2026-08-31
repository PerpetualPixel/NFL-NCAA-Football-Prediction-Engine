"""Static site generator for GitHub Pages.

Renders the upcoming week's picks for each league (plus a graded look at the
most recent completed week) into a small static site in site/.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from . import analysis, model as model_mod
from . import odds, pipeline
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
.cardhead { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.kick { color: var(--muted); font-size: 0.82rem; white-space: nowrap; }
.controls { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin: 12px 0 16px; }
.ctl-group { display: flex; gap: 6px; flex-wrap: wrap; }
.chip {
  font: inherit; font-size: 0.82rem; padding: 5px 11px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--line); background: var(--card); color: var(--ink);
}
.chip:hover { border-color: var(--accent); }
.chip.active { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
.ctl-sort { color: var(--muted); font-size: 0.82rem; }
.ctl-sort select {
  font: inherit; font-size: 0.82rem; margin-left: 6px; padding: 4px 8px;
  border-radius: 6px; border: 1px solid var(--line); background: var(--card); color: var(--ink);
}
.chips { display: flex; gap: 5px; flex-wrap: wrap; margin: 6px 0 2px; }
.tag {
  font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.04em; font-weight: 700;
  padding: 2px 7px; border-radius: 4px; border: 1px solid var(--line); color: var(--muted);
}
.tag-lock { color: var(--good); border-color: var(--good); }
.tag-value { color: var(--accent); border-color: var(--accent); }
.tag-upset { color: var(--bad); border-color: var(--bad); }
.emptynote { color: var(--muted); font-size: 0.9rem; padding: 8px 2px; }
details.more { margin-top: 10px; border-top: 1px solid var(--line); padding-top: 8px; }
details.more summary {
  cursor: pointer; font-size: 0.85rem; font-weight: 600; color: var(--accent);
  list-style: none; padding: 2px 0;
}
details.more summary::-webkit-details-marker { display: none; }
details.more summary::before { content: "\\25B8 "; display: inline-block; transition: transform 0.15s; }
details.more[open] summary::before { transform: rotate(90deg); }
.analysis { padding-top: 6px; }
.analysis h4 {
  font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--muted); margin: 14px 0 6px;
}
.analysis p { margin: 0 0 10px; font-size: 0.9rem; }
ul.factors { margin: 0; padding-left: 18px; font-size: 0.88rem; }
ul.factors li { margin-bottom: 8px; }
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


def _kickoff(row: pd.Series) -> tuple[str, float]:
    """Human kickoff label and a sortable epoch value."""
    ts = pd.to_datetime(row.get("gameday"), utc=True, errors="coerce")
    if pd.isna(ts):
        return "", 0.0
    label = ts.strftime("%a, %b %-d")
    gametime = row.get("gametime")
    if isinstance(gametime, str) and ":" in gametime:
        hour, minute = (int(p) for p in gametime.split(":")[:2])
        suffix = "AM" if hour < 12 else "PM"
        label += f" &middot; {((hour - 1) % 12) + 1}:{minute:02d} {suffix} ET"
    return label, ts.timestamp()


def _game_card(row: pd.Series, graded: bool, league: str,
               players: pd.DataFrame | None = None) -> str:
    home_favored = row.pred_margin > 0
    pick = row.home_team if home_favored else row.away_team
    prob = row.home_win_prob if home_favored else 1 - row.home_win_prob
    line = odds.format_line(pick, row.pred_margin)
    ml = odds.format_moneyline(prob)

    edge = None
    market = ""
    if pd.notna(row.get("spread_line")):
        edge = float(row.pred_margin - row.spread_line)
        side = row.home_team if edge > 0 else row.away_team
        cls = "edge-pos" if abs(edge) >= odds.VALUE_EDGE_PTS else "meta"
        market = (f'<div class="meta">Market: {odds.format_line(row.home_team, row.spread_line)} '
                  f'&middot; <span class="{cls}">model {edge:+.1f} toward {side}</span></div>')

    tags = odds.classify(prob, edge)
    key = odds.near_key_number(row.pred_margin, league)
    chips = "".join(f'<span class="tag tag-{t}">{t}</span>' for t in tags)
    if key:
        chips += f'<span class="tag tag-key">near {key}</span>'

    result = ""
    if graded and pd.notna(row.get("margin")):
        actual_winner = row.home_team if row.margin > 0 else row.away_team
        hit = actual_winner == pick
        cls = "result-hit" if hit else "result-miss"
        hs = int(row.home_score) if pd.notna(row.get("home_score")) else "?"
        as_ = int(row.away_score) if pd.notna(row.get("away_score")) else "?"
        result = (f'<div class="meta">Final: {row.home_team} {hs}&ndash;{as_} '
                  f'&middot; <span class="{cls}">{"HIT" if hit else "MISS"}</span></div>')

    kick_label, kick_sort = _kickoff(row)
    neutral = " (neutral site)" if row.neutral else ""
    return f"""<div class="card game" data-kick="{kick_sort:.0f}" data-prob="{prob:.4f}" \
data-margin="{abs(row.pred_margin):.3f}" data-edge="{abs(edge) if edge is not None else 0:.3f}" \
data-tags="{' '.join(tags)}">
<div class="cardhead"><div class="teams">{row.away_team} @ {row.home_team}{neutral}</div>
<div class="kick">{kick_label}</div></div>
<div class="pick">Pick: <strong>{line}</strong> &middot; {prob:.0%} win probability &middot; <span class="meta">ML {ml}</span></div>
<div class="chips">{chips}</div>
<div class="meta">Power ratings: {row.home_team} {row.home_rating:+.1f}, {row.away_team} {row.away_rating:+.1f}
&middot; raw projection {abs(row.pred_margin):.1f}</div>
{market}{result}{_analysis_panel(row, league, players)}
</div>"""


def _analysis_panel(row: pd.Series, league: str, players: pd.DataFrame | None) -> str:
    """Collapsed 'More info' section: game script, key players, matchups, units."""
    paras = "".join(f"<p>{p}</p>" for p in analysis.game_script(row, league))

    player_html = ""
    people = analysis.key_players(row, players)
    if people:
        items = "".join(f"<li><strong>{team}:</strong> {text}</li>" for team, text in people)
        player_html = f'<h4>Players to watch</h4><ul class="factors">{items}</ul>'

    factor_html = ""
    factors = analysis.key_factors(row)
    if factors:
        items = "".join(
            f"<li><strong>{label}.</strong> {text}</li>" for label, text in factors
        )
        factor_html = f'<h4>Key matchups</h4><ul class="factors">{items}</ul>'

    return f"""<details class="more">
<summary>More info &mdash; full analysis</summary>
<div class="analysis">
<h4>How the model sees it playing out</h4>{paras}
{player_html}{factor_html}
<h4>Unit ratings</h4>{_unit_table(row)}
</div></details>"""


CONTROLS = """<div class="controls">
  <div class="ctl-group" role="group" aria-label="Filter games">
    <button class="chip active" data-filter="all">All</button>
    <button class="chip" data-filter="lock">Locks</button>
    <button class="chip" data-filter="pickem">Pick'ems</button>
    <button class="chip" data-filter="value">Value vs market</button>
    <button class="chip" data-filter="upset">Upset picks</button>
  </div>
  <label class="ctl-sort">Sort:
    <select id="sortby">
      <option value="kick">Kickoff (chronological)</option>
      <option value="prob">Win probability</option>
      <option value="margin">Projected margin</option>
      <option value="edge">Edge vs market</option>
    </select>
  </label>
</div>
<div class="emptynote" hidden>No games match this filter.</div>"""

SCRIPT = """<script>
(function () {
  var list = document.getElementById('games');
  if (!list) return;
  var note = document.querySelector('.emptynote');
  var filter = 'all';

  function apply() {
    var key = document.getElementById('sortby').value;
    var cards = Array.prototype.slice.call(list.querySelectorAll('.game'));
    cards.sort(function (a, b) {
      var av = parseFloat(a.dataset[key]), bv = parseFloat(b.dataset[key]);
      return key === 'kick' ? av - bv : bv - av;   // time ascending, strength descending
    });
    cards.forEach(function (c) {
      list.appendChild(c);
      var show = filter === 'all' || c.dataset.tags.split(' ').indexOf(filter) !== -1;
      c.hidden = !show;
    });
    var visible = cards.filter(function (c) { return !c.hidden; }).length;
    if (note) note.hidden = visible !== 0;
  }

  document.querySelectorAll('.chip').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('.chip').forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      filter = btn.dataset.filter;
      apply();
    });
  });
  document.getElementById('sortby').addEventListener('change', apply);
  apply();
})();
</script>"""


def _week_key(df: pd.DataFrame) -> list[tuple[int, int]]:
    return sorted(df[["season", "week"]].drop_duplicates().itertuples(index=False, name=None))


def build_league_page(league: str, refresh: bool) -> tuple[str, str]:
    """Returns (html_body, summary_line)."""
    cfg = LEAGUES[league]
    games, unit_stats, players = pipeline.load_league_inputs(
        league, refresh=refresh, recent_only=True, with_players=True
    )
    latest_season = int(games.loc[games["completed"], "season"].max())
    feats = pipeline.build_walk_forward_features(
        league, games, unit_stats, start_season=latest_season - 1
    )
    # attach scores (for grading) and dates (to pick the true upcoming week)
    score_cols = ["game_id", "home_score", "away_score", "gameday"]
    if "gametime" in games.columns:
        score_cols.append("gametime")
    feats = feats.merge(games[score_cols], on="game_id", how="left")

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
            body.append(CONTROLS)
            body.append('<div id="games">')
            # chronological by default; the sort control reorders client-side
            for _, row in preds.sort_values("gameday").iterrows():
                body.append(_game_card(row, graded=False, league=league, players=players))
            body.append("</div>")
            best = preds.loc[preds["pred_margin"].abs().idxmax()]
            best_pick = best.home_team if best.pred_margin > 0 else best.away_team
            summary = (f"{league.upper()}: week {week} &mdash; {len(preds)} games, "
                       f"top pick {odds.format_line(best_pick, best.pred_margin)}")

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
            for _, row in graded.sort_values("gameday").iterrows():
                body.append(_game_card(row, graded=True, league=league, players=players))

    body.append(SCRIPT)
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
