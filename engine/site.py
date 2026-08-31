"""Static site generator for GitHub Pages.

Renders the upcoming week's picks for each league (plus a graded look at the
most recent completed week) into a small static site in site/.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from . import analysis, calibrate, grades, model as model_mod
from . import odds, pipeline, pixel, teams, tracking
from .config import LEAGUES

SITE_DIR = Path(__file__).resolve().parents[1] / "site"

CSS = """
:root {
  --bg: #f4f6f8; --card: #ffffff; --sunken: #f8fafc; --ink: #14181d;
  --muted: #64707e; --line: #e3e8ee; --accent: #1d4ed8;
  --good: #047857; --warn: #b45309; --bad: #b91c1c;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1014; --card: #171c23; --sunken: #12161c; --ink: #e7eaee;
    --muted: #98a2b0; --line: #262d36; --accent: #7aa2ff;
    --good: #34d399; --warn: #fbbf24; --bad: #f87171;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 880px; margin: 0 auto; padding: 24px 16px 64px; }
header.site {
  display: flex; align-items: baseline; gap: 18px; flex-wrap: wrap;
  margin-bottom: 8px; padding-bottom: 12px; border-bottom: 2px solid var(--line);
}
header.site h1 { font-size: 1.45rem; margin: 0; letter-spacing: -0.02em; }
header.site nav { display: flex; gap: 4px; flex-wrap: wrap; }
header.site nav a {
  color: var(--muted); text-decoration: none; font-weight: 600; font-size: 0.9rem;
  padding: 4px 10px; border-radius: 6px;
}
header.site nav a:hover { color: var(--accent); background: var(--sunken); }
.stamp { color: var(--muted); font-size: 0.82rem; margin: 12px 0 22px; }
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
table.units { border-collapse: collapse; margin-top: 10px; font-size: 0.85rem; width: 100%; max-width: 480px; }
table.units th, table.units td { text-align: center; padding: 6px 10px; border-bottom: 1px solid var(--line); }
table.units th:first-child, table.units td.ulabel { text-align: left; }
table.units th { color: var(--muted); font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.04em; }
.ulabel { color: var(--ink); }
.gwrap { display: flex; align-items: baseline; justify-content: center; gap: 6px; }
.gletter { font-weight: 800; font-size: 0.98rem; }
.grank { color: var(--muted); font-size: 0.74rem; }
.gbar { height: 4px; border-radius: 3px; background: var(--line); margin-top: 4px; overflow: hidden; }
.gbar i { display: block; height: 100%; border-radius: 3px; }
.gnote { margin-top: 8px; font-size: 0.78rem; }
/* one colour language across the whole site */
.t-strong { color: var(--good); }
.t-good { color: var(--good); opacity: 0.85; }
.t-mid { color: var(--muted); }
.t-poor { color: var(--warn); }
.t-bad { color: var(--bad); }
.gbar i.t-strong { background: var(--good); }
.gbar i.t-good { background: var(--good); opacity: 0.7; }
.gbar i.t-mid { background: var(--muted); }
.gbar i.t-poor { background: var(--warn); }
.gbar i.t-bad { background: var(--bad); }
.bets { display: flex; gap: 10px; flex-wrap: wrap; margin: 10px 0 4px; }
.bet {
  flex: 1 1 150px; background: var(--sunken); border: 1px solid var(--line);
  border-radius: 8px; padding: 9px 11px;
}
.betlabel {
  font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--muted); font-weight: 700;
}
.betvalue { font-size: 1.02rem; font-weight: 700; margin-top: 2px; }
.betvalue .price { color: var(--muted); font-weight: 600; font-size: 0.88rem; }
.betnote { font-size: 0.76rem; margin-top: 1px; }
.outcome {
  display: inline-block; margin-top: 5px; font-size: 0.7rem; font-weight: 800;
  letter-spacing: 0.05em; padding: 1px 6px; border-radius: 4px;
  border: 1px solid currentColor;
}
.factor { margin-bottom: 11px; list-style: none; }
ul.factors { padding-left: 0; }
.fhead { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.fname { font-weight: 700; font-size: 0.88rem; }
.fverdict {
  font-size: 0.7rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.04em;
}
.ftext { font-size: 0.86rem; color: var(--ink); margin-top: 1px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-bottom: 16px; }
.tile { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }
.tiletitle { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); font-weight: 700; }
.tilebig { font-size: 1.7rem; font-weight: 800; line-height: 1.15; margin: 4px 0 2px; }
.tilesub { font-size: 0.78rem; }
.explain { font-size: 0.88rem; }
.explainbox { margin: 0 0 12px; border-top: none; padding-top: 0; }
.explainbox summary { color: var(--muted); font-weight: 600; }
table.track { border-collapse: collapse; width: 100%; font-size: 0.88rem; margin-top: 4px; }
table.track th, table.track td { padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: left; }
table.track th { color: var(--muted); font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.04em; }
table.track td.num, table.track th.num { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
table.track a { color: var(--ink); text-decoration: none; }
table.track a:hover { color: var(--accent); }
.lgtag {
  font-size: 0.65rem; font-weight: 800; letter-spacing: 0.04em; padding: 1px 5px;
  border-radius: 3px; border: 1px solid var(--line); color: var(--muted); margin-right: 6px;
}
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
.tag-pick { color: var(--good); border-color: var(--good); font-weight: 800; }
.tag-lean { color: var(--muted); }
.emptynote { color: var(--muted); font-size: 0.9rem; padding: 8px 2px; }
.scopebar { position: sticky; top: 0; z-index: 5; background: var(--bg); padding: 8px 0; }
.hero.card {
  display: block; text-decoration: none; color: inherit;
  border: 2px solid var(--accent); padding: 18px 20px;
}
.herolabel {
  font-size: 0.7rem; font-weight: 800; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--accent);
}
.heroweek { font-size: 1.5rem; font-weight: 800; margin: 2px 0 4px; letter-spacing: -0.02em; }
.herogo { margin-top: 8px; font-weight: 700; color: var(--accent); font-size: 0.9rem; }
.card.pending { border-style: dashed; }
.pixel.card { border: 2px solid var(--accent); }
.pxhead { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.pxbadge {
  background: var(--accent); color: #fff; font-weight: 800; font-size: 0.72rem;
  letter-spacing: 0.05em; text-transform: uppercase; padding: 3px 9px; border-radius: 5px;
}
.pxprice { font-size: 1.25rem; font-weight: 800; }
.pxlegs { list-style: none; padding: 0; margin: 10px 0 4px; }
.pxleg { padding: 6px 0; border-bottom: 1px solid var(--line); }
.pxdetail { font-weight: 700; }
.tag-pixel { color: #fff; background: var(--accent); border-color: var(--accent); }
.pendingnote { margin-top: 6px; font-size: 0.9rem; }
.pendingnote strong { color: var(--accent); }
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
.wknav {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  flex-wrap: wrap; background: var(--card); border: 1px solid var(--line);
  border-radius: 10px; padding: 10px 12px; margin-bottom: 6px;
}
.wknav-btn {
  color: var(--accent); text-decoration: none; font-size: 0.85rem; font-weight: 600;
  white-space: nowrap;
}
.wknav-btn.disabled { color: var(--muted); opacity: 0.5; }
.wksel { font-size: 0.85rem; color: var(--muted); }
.wksel select {
  font: inherit; font-size: 0.85rem; margin-left: 6px; padding: 4px 8px; max-width: 240px;
  border-radius: 6px; border: 1px solid var(--line); background: var(--bg); color: var(--ink);
}
.weekhead { color: var(--muted); font-size: 0.9rem; margin: -6px 0 10px; }
.weekgrid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px;
}
.weektile {
  display: block; text-decoration: none; color: inherit; background: var(--card);
  border: 1px solid var(--line); border-radius: 9px; padding: 11px 13px;
}
.weektile:hover { border-color: var(--accent); }
.wt-label { font-weight: 700; font-size: 0.92rem; }
.wt-detail { margin-top: 3px; }
.wt-detail .good { color: var(--good); font-weight: 600; }
.wt-detail .bad { color: var(--bad); font-weight: 600; }
.wt-detail .upcoming { color: var(--accent); font-weight: 600; }
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
<nav><a href="index.html">Home</a><a href="nfl.html">NFL</a><a href="ncaa.html">NCAA</a>
<a href="tracking.html">Tracking</a></nav></header>
<div class="stamp">Updated {stamp} &middot; free public data (nflverse / sportsdataverse) &middot; walk-forward model, no leakage</div>
{body}
<footer>Model picks are analytical output, not betting advice. Ratings are opponent-adjusted
ridge estimates with recency decay; win probabilities from a normal margin model.</footer>
</div></body></html>"""


def _grade_cell(row: pd.Series, key: str, n: int) -> str:
    """One team's unit: letter grade, rank, and a filled bar, colour-coded."""
    rank_key = f"{key}_rank"
    if rank_key not in row.index or pd.isna(row.get(rank_key)):
        return '<td class="gcell">&mdash;</td>'
    rank = int(row[rank_key])
    pct = grades.percentile(rank, n)
    return (f'<td class="gcell"><div class="gwrap">'
            f'<span class="gletter t-{grades.tone(pct)}">{grades.grade(pct)}</span>'
            f'<span class="grank">{grades.ordinal(rank)}</span></div>'
            f'<div class="gbar"><i class="t-{grades.tone(pct)}" '
            f'style="width:{pct * 100:.0f}%"></i></div></td>')


def _unit_table(row: pd.Series) -> str:
    if "home_off_pass_epa" not in row.index or pd.isna(row.get("home_off_pass_epa")):
        return ""
    n = int(row.get("unit_n") or row.get("n_teams", 32) or 32)
    rows = [
        ("Passing offense", "off_pass_epa"),
        ("Pass defense", "def_pass_epa"),
        ("Rushing offense", "off_rush_epa"),
        ("Run defense", "def_rush_epa"),
    ]
    body = "".join(
        f'<tr><td class="ulabel">{label}</td>'
        f'{_grade_cell(row, f"home_{key}", n)}{_grade_cell(row, f"away_{key}", n)}</tr>'
        for label, key in rows
    )
    return f"""<table class="units">
<tr><th>Unit grades</th><th>{row.home_team}</th><th>{row.away_team}</th></tr>
{body}</table>
<div class="meta gnote">Grades compare each unit to the rest of the league
(A+ = top of the league, F = bottom), based on opponent-adjusted efficiency.</div>"""


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
               players: pd.DataFrame | None = None, is_pixel: bool = False) -> str:
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
        market = (f'<div class="meta">Market line: {odds.format_line(row.home_team, row.spread_line)} '
                  f'&middot; <span class="{cls}">model {edge:+.1f} toward {side}</span></div>')

    tags = odds.classify(prob, edge)
    tier = "lean"
    if row.get("ml_tier") == "pick" or row.get("ats_tier") == "pick":
        tier = "pick"
    tags = [tier] + tags
    if is_pixel:
        tags = ["pixel"] + tags
    key = odds.near_key_number(row.pred_margin, league)
    label = {"pixel": "Pixel&rsquo;s Pick", "pick": "pick", "lean": "lean",
             "lock": "lock", "pickem": "pick'em", "value": "value", "upset": "upset"}
    chips = "".join(f'<span class="tag tag-{t}">{label.get(t, t)}</span>' for t in tags)
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
{_pick_row(row, line, prob, ml)}
<div class="chips">{chips}</div>
{market}{result}{_analysis_panel(row, league, players)}
</div>"""


def _pick_row(row: pd.Series, line: str, prob: float, ml: str) -> str:
    """The two bets side by side: moneyline (who wins) and spread (who covers),
    each with its own result once the game is final."""
    def outcome(kind: str) -> str:
        res = row.get(f"{kind}_result")
        if not res or (isinstance(res, float) and pd.isna(res)):
            return ""
        label = {"win": "WIN", "loss": "LOSS", "push": "PUSH"}[res]
        return f'<span class="outcome t-{grades.result_tone(res)}">{label}</span>'

    ats = ""
    if pd.notna(row.get("ats_pick")) and row.get("ats_pick"):
        ats_line = row.get("ats_line")
        shown = f"{ats_line:+.1f}" if pd.notna(ats_line) else ""
        ats = (f'<div class="bet"><div class="betlabel">Spread pick</div>'
               f'<div class="betvalue">{row.ats_pick} {shown}</div>'
               f'<div class="betnote meta">{abs(row.get("ats_edge", 0)):.1f} pt edge vs market'
               f'</div>{outcome("ats")}</div>')

    return f"""<div class="bets">
<div class="bet"><div class="betlabel">Moneyline pick</div>
<div class="betvalue">{row.get('ml_pick', '')} <span class="price">{ml}</span></div>
<div class="betnote meta">{prob:.0%} to win outright</div>{outcome('ml')}</div>
<div class="bet"><div class="betlabel">Projected line</div>
<div class="betvalue">{line}</div>
<div class="betnote meta">model projection</div></div>
{ats}</div>"""


def _analysis_panel(row: pd.Series, league: str, players: pd.DataFrame | None) -> str:
    """Collapsed 'More info' section: game script, key players, matchups, units."""
    paras = "".join(f"<p>{p}</p>" for p in analysis.game_script(row, league))

    player_html = ""
    people = analysis.key_players(row, players)
    if people:
        items = "".join(f"<li><strong>{team}:</strong> {text}</li>" for team, text in people)
        player_html = f'<h4>Players to watch</h4><ul class="factors">{items}</ul>'

    avail = analysis.availability_note(row)
    avail_html = (f'<h4>Availability &amp; conditions</h4><p class="ftext">{avail}</p>'
                  if avail else "")
    factor_html = ""
    factors = analysis.key_factors(row)
    if factors:
        items = "".join(
            f'<li class="factor"><div class="fhead">'
            f'<span class="fname">{f["title"]}</span>'
            f'<span class="fverdict t-{f["tone"]}">{f["verdict"]}</span></div>'
            f'<div class="ftext">{f["text"]}</div></li>'
            for f in factors
        )
        factor_html = f'<h4>Key matchups</h4><ul class="factors">{items}</ul>'

    return f"""<details class="more">
<summary>More info &mdash; full analysis</summary>
<div class="analysis">
<h4>How the model sees it playing out</h4>{paras}
{player_html}{factor_html}
{avail_html}
<h4>Unit ratings</h4>{_unit_table(row)}
</div></details>"""


CONTROLS = """<div class="controls">
  <div class="ctl-group" role="group" aria-label="Filter games">
    <button class="chip active" data-filter="all">All</button>
    <button class="chip" data-filter="pixel">Pixel&rsquo;s Picks</button>
    <button class="chip" data-filter="pick">Picks (value)</button>
    <button class="chip" data-filter="lean">Leans</button>
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

  var wk = document.getElementById('weekpick');
  if (wk) wk.addEventListener('change', function () { window.location.href = wk.value; });
  var sn = document.getElementById('seasonpick');
  if (sn) sn.addEventListener('change', function () { window.location.href = sn.value; });
})();
</script>"""


def _week_key(df: pd.DataFrame) -> list[tuple[int, int]]:
    return sorted(df[["season", "week"]].drop_duplicates().itertuples(index=False, name=None))


# NFL playoff rounds carry a game_type; NCAA postseason weeks are shifted
# past the regular season by the pipeline and labelled as bowls.
NFL_ROUND_LABELS = {
    "WC": "Wild Card Round", "DIV": "Divisional Round",
    "CON": "Conference Championships", "SB": "Super Bowl",
}


def week_label(league: str, week: int, game_types: set[str]) -> str:
    if league == "nfl":
        for code, label in NFL_ROUND_LABELS.items():
            if code in game_types:
                return label
        return f"Week {week}"
    if game_types & {"postseason"}:
        return "Bowls &amp; Playoff"
    return f"Week {week}"


def week_slug(league: str, week: int, season: int | None = None, current: int | None = None) -> str:
    """Current season keeps clean names; past seasons are namespaced."""
    if season is not None and current is not None and season != current:
        return f"{league}-{season}-w{week:02d}.html"
    return f"{league}-w{week:02d}.html"


ODDS_COLS = ["home_moneyline", "away_moneyline", "home_spread_odds",
             "away_spread_odds", "open_spread_line"]

# A pick made in August for a game in December is worthless: it cannot know
# who is hurt, who is starting, or what the weather will be. Picks are held
# until the day before kickoff, then refreshed on game day by the scheduled
# rebuild so late injury and lineup news is reflected.
RELEASE_LEAD_HOURS = 24


EASTERN = "US/Eastern"


def _local(ts: pd.Timestamp) -> pd.Timestamp:
    """Kickoffs read best in US Eastern; fall back to UTC where the system
    has no timezone database."""
    try:
        return ts.tz_convert(EASTERN)
    except Exception:
        return ts


def _kickoff_time(row) -> pd.Timestamp:
    """Kickoff as a UTC timestamp, for release-timing decisions."""
    return pd.to_datetime(row.get("gameday"), utc=True, errors="coerce")


def _is_released(kick: pd.Timestamp, now: pd.Timestamp) -> bool:
    if pd.isna(kick):
        return True  # no kickoff time known: nothing to hold back for
    return now >= kick - pd.Timedelta(hours=RELEASE_LEAD_HOURS)


def _release_label(kick: pd.Timestamp) -> str:
    if pd.isna(kick):
        return "soon"
    release = _local(kick - pd.Timedelta(hours=RELEASE_LEAD_HOURS))
    return release.strftime("%a %b %-d, %-I:%M %p")


def _pending_card(row, kick: pd.Timestamp) -> str:
    """A scheduled game whose pick is not out yet."""
    when = "" if pd.isna(kick) else _local(kick).strftime("%a %b %-d, %-I:%M %p")
    return f"""<div class="card game pending" data-kick="{0 if pd.isna(kick) else int(kick.timestamp())}"
 data-prob="0" data-margin="0" data-edge="0" data-tags="pending">
<div class="cardhead"><div class="teams">{row.away_team} @ {row.home_team}</div>
<div class="kick">{when}</div></div>
<div class="pendingnote">Pick releases <strong>{_release_label(kick)}</strong>
<span class="meta">&mdash; held until injury reports, starting lineups and the
forecast are known, then refreshed on game day.</span></div>
</div>"""


def _add_display_names(preds: pd.DataFrame, league: str) -> pd.DataFrame:
    """Swap data-source team keys for full names with mascots.

    Rendering reads home_team/away_team, so the display names replace them
    outright and the original keys are kept for lookups that need them.
    """
    df = preds.copy()
    name = lambda s: s.map(lambda t: teams.display_name(t, league))
    df["home_key"] = df["home_team"]
    df["away_key"] = df["away_team"]
    df["home_team"] = name(df["home_team"])
    df["away_team"] = name(df["away_team"])
    for col in ("ml_pick", "ats_pick"):
        if col in df.columns:
            df[col] = df[col].map(
                lambda t: teams.display_name(t, league) if isinstance(t, str) else t
            )
    return df


def _season_select(league: str, season: int, seasons: list[tuple[int, list[dict]]],
                   cur_season: int) -> str:
    """Season dropdown, so a past season is one click away."""
    if len(seasons) < 2:
        return ""
    opts = []
    for yr, yr_weeks in seasons:
        if not yr_weeks:
            continue
        target = week_slug(league, yr_weeks[0]["week"], yr, cur_season)
        sel = " selected" if yr == season else ""
        opts.append(f'<option value="{target}"{sel}>{yr} season</option>')
    if len(opts) < 2:
        return ""
    return (f'<label class="wksel">Season:<select id="seasonpick">{"".join(opts)}'
            "</select></label>")


def _week_nav(league: str, weeks: list[dict], current: int, cur_season: int,
              seasons: list[tuple[int, list[dict]]] | None = None,
              season: int | None = None) -> str:
    """Dropdown + prev/next links across every week of the season."""
    slug = lambda w: week_slug(league, w["week"], w["season"], cur_season)
    options = []
    for w in weeks:
        sel = " selected" if w["week"] == current else ""
        options.append(
            f'<option value="{slug(w)}"{sel}>{w["label"]}{w["status_short"]}</option>'
        )
    idx = next((i for i, w in enumerate(weeks) if w["week"] == current), 0)
    prev_link = (f'<a class="wknav-btn" href="{slug(weeks[idx-1])}">&larr; '
                 f'{weeks[idx-1]["label"]}</a>' if idx > 0 else
                 '<span class="wknav-btn disabled">&larr; Prev</span>')
    next_link = (f'<a class="wknav-btn" href="{slug(weeks[idx+1])}">'
                 f'{weeks[idx+1]["label"]} &rarr;</a>' if idx < len(weeks) - 1 else
                 '<span class="wknav-btn disabled">Next &rarr;</span>')
    season_sel = (_season_select(league, season, seasons, cur_season)
                  if seasons and season is not None else "")
    return f"""<div class="wknav">
{prev_link}
{season_sel}
<label class="wksel">Week:
  <select id="weekpick">{''.join(options)}</select>
</label>
{next_link}
</div>"""


# How many seasons of archive to publish. More seasons means more history
# for the calibrator to learn the model-versus-price relationship from, and
# more of a record for a reader to judge the picks by.
ARCHIVE_SEASONS = 4


def prepare_league(league: str, refresh: bool, first_season: int):
    """Load data and build walk-forward features once for every season the
    archive covers, rather than repeating the work per season."""
    cfg = LEAGUES[league]
    # the archive needs its own seasons plus enough earlier ones to rate the
    # first archived week from
    games, unit_stats, players, availability = pipeline.load_league_inputs(
        league, refresh=refresh, recent_only=True, with_players=True,
        history_seasons=ARCHIVE_SEASONS + cfg.rating_window_seasons,
    )
    feats = pipeline.build_walk_forward_features(
        league, games, unit_stats, start_season=first_season - 1,
        availability=availability,
    )
    score_cols = ["game_id", "home_score", "away_score", "gameday", "game_type"]
    if "gametime" in games.columns:
        score_cols.append("gametime")
    score_cols += [c for c in ODDS_COLS if c in games.columns]
    feats = feats.merge(games[score_cols], on="game_id", how="left")
    return games, feats, players, cfg


def build_league_weeks(
    league: str, refresh: bool, season: int | None = None,
    write_pages: bool = True, seed_history: pd.DataFrame | None = None,
    prepared: tuple | None = None,
) -> tuple[list[dict], str, int]:
    """Build every week of a season (default: the current one).
    Returns (weeks, season_summary, season)."""
    if prepared is None:
        season_guess = season or 2100
        prepared = prepare_league(league, refresh, season_guess)
    games, feats, players, cfg = prepared
    season = int(games["season"].max()) if season is None else season
    # predict_week trains on everything before the target week, so keep the
    # full feature history and iterate only the current season's weeks
    season_weeks = sorted(feats.loc[feats["season"] == season, "week"].unique())

    weeks = []
    # calibration starts from earlier seasons' settled games, so week 1 is not
    # flying blind while this season accumulates results
    settled: list[pd.DataFrame] = ([seed_history] if seed_history is not None
                                   and len(seed_history) else [])
    for week in season_weeks:
        week = int(week)
        preds = model_mod.predict_week(feats, cfg, season, week)
        # games against non-FBS opponents still inform the ratings, but the
        # site lists FBS matchups only
        preds = preds[~(preds["home_team"].eq(pipeline.FCS_BUCKET)
                        | preds["away_team"].eq(pipeline.FCS_BUCKET))]
        if preds.empty:
            continue
        preds = tracking.grade(preds, margin_sigma=cfg.margin_sigma)
        # calibrate against everything already settled this build
        history = (pd.concat(settled, ignore_index=True) if settled
                   else preds.iloc[0:0])
        for kind in ("ml", "ats"):
            preds[f"{kind}_cal"] = calibrate.fit_and_apply(history, preds, kind)
            preds[f"{kind}_ev"] = calibrate.expected_value(
                preds[f"{kind}_cal"], preds[f"{kind}_price"])
        preds = tracking.assign_tiers(preds)
        done_rows = preds[preds["ml_result"].isin(["win", "loss"])]
        if len(done_rows):
            settled.append(done_rows)
        preds = _add_display_names(preds, league)
        types = set(preds["game_type"].dropna().astype(str))
        label = week_label(league, week, types)
        graded = preds[preds["completed"].astype(bool) & preds["margin"].notna()]
        upcoming = preds[~preds["completed"].astype(bool)]

        ml = tracking.record(graded, "ml")
        ats = tracking.record(graded, "ats")
        if len(graded):
            status_short = f' — {tracking.format_record(ml)}'
            headline = (f'{tracking.format_record(ml)} on the moneyline, '
                        f'{tracking.format_record(ats)} against the spread')
        else:
            now = pd.Timestamp.now(tz="UTC")
            kicks = pd.to_datetime(preds["gameday"], utc=True, errors="coerce")
            released = sum(_is_released(k, now) for k in kicks)
            if released:
                status_short = f" — {released} of {len(preds)} picks out"
                headline = (f"{released} of {len(preds)} picks released; the rest come out "
                            "the day before kickoff")
            else:
                first = kicks.min()
                status_short = " — upcoming"
                headline = (f"{len(preds)} games &mdash; picks release from "
                            f"{_release_label(first)}, one day before each kickoff")

        pick = pixel.select(preds, cfg.margin_sigma)
        graded_pick = pixel.grade(pick, graded) if pick is not None else None
        pixel_ids = {leg["game_id"] for leg in pick["legs"]} if pick else set()

        weeks.append({
            "week": week, "label": label, "season": season, "preds": preds,
            "pixel": pick, "pixel_graded": graded_pick, "pixel_ids": pixel_ids,
            "graded": graded, "status_short": status_short, "headline": headline,
            "ml": ml, "ats": ats,
            "complete": len(graded) > 0 and len(upcoming) == 0,
        })

    all_graded = (pd.concat([w["graded"] for w in weeks], ignore_index=True)
                  if weeks else pd.DataFrame())
    season_ml = tracking.record(all_graded, "ml") if len(all_graded) else None
    season_ats = tracking.record(all_graded, "ats") if len(all_graded) else None
    if season_ml and season_ml["n"]:
        season_summary = (
            f'{season} season to date: {tracking.format_record(season_ml)} moneyline '
            f'({season_ml["hit_rate"]:.0%}, {season_ml["roi"]:+.1%} ROI) &middot; '
            f'{tracking.format_record(season_ats)} spread '
            f'({season_ats["hit_rate"]:.0%}, {season_ats["roi"]:+.1%} ROI)'
        )
    else:
        season_summary = f"{season} season &mdash; no completed games yet"

    for w in weeks:
        w["players"] = players
    if not write_pages:
        return weeks, season_summary, season

    cur_season = int(games["season"].max())
    write_week_pages(league, weeks, season, cur_season, [(season, weeks)])
    return weeks, season_summary, season


def write_week_pages(league: str, weeks: list[dict], season: int, cur_season: int,
                     season_index: list[tuple[int, list[dict]]]) -> None:
    """Render one page per week, with week and season navigation."""
    for w in weeks:
        players = w.get("players")
        body = [
            _week_nav(league, weeks, w["week"], cur_season,
                      seasons=season_index, season=season),
            f'<h2>{w["label"]} &mdash; {season}</h2>',
            f'<div class="weekhead">{w["headline"]}</div>',
            _pixel_section(w, league, players),
            CONTROLS,
            '<div id="games">',
        ]
        now = pd.Timestamp.now(tz="UTC")
        for _, row in w["preds"].sort_values("gameday").iterrows():
            graded_row = bool(row["completed"]) and pd.notna(row["margin"])
            kick = _kickoff_time(row)
            if not graded_row and not _is_released(kick, now):
                body.append(_pending_card(row, kick))
                continue
            body.append(_game_card(row, graded=graded_row, league=league,
                                   players=players,
                                   is_pixel=row["game_id"] in w["pixel_ids"]))
        body += ["</div>", SCRIPT]
        (SITE_DIR / week_slug(league, w["week"], season, cur_season)).write_text(
            _page(f'{league.upper()} {w["label"]} {season} — Gridiron Engine',
                  "\n".join(body))
        )


def _week_grid(league: str, weeks: list[dict], season: int, cur_season: int) -> str:
    tiles = []
    for w in weeks:
        ml, ats = w["ml"], w["ats"]
        if ml["n"]:
            detail = (f'<span class="t-{grades.hit_tone(ml["hit_rate"], 0.50)}">'
                      f'{tracking.format_record(ml)} ML</span>')
            if ats["decided"]:
                detail += (f' &middot; <span class="t-{grades.hit_tone(ats["hit_rate"])}">'
                           f'{tracking.format_record(ats)} ATS</span>')
        else:
            detail = ('<span class="upcoming">Picks out</span>'
                      if "picks out" in w["status_short"]
                      else '<span class="meta">Picks release day before</span>')
        tiles.append(
            f'<a class="weektile" href="{week_slug(league, w["week"], season, cur_season)}">'
            f'<div class="wt-label">{w["label"]}</div>'
            f'<div class="wt-detail meta">{detail}</div></a>'
        )
    return f'<div class="weekgrid">{"".join(tiles)}</div>' if tiles else (
        '<div class="card"><div class="meta">No games published for this season yet.'
        "</div></div>"
    )


def _league_hub(league: str, by_season: list[tuple[int, list[dict]]],
                summaries: dict[int, str], season: int) -> str:
    """League landing page: the current week first, then one season at a time."""
    newest = list(reversed(by_season))
    current_weeks = dict(by_season).get(season, [])
    # the week a visitor actually wants: the next one still to be played
    current = next((w for w in current_weeks if not w["complete"]),
                   current_weeks[-1] if current_weeks else None)
    hero = ""
    if current:
        hero = (
            f'<a class="hero card" href="{week_slug(league, current["week"], season, season)}">'
            f'<div class="herolabel">Now &middot; {season}</div>'
            f'<div class="heroweek">{current["label"]}</div>'
            f'<div class="meta">{current["headline"]}</div>'
            f'<div class="herogo">View picks and breakdowns &rarr;</div></a>'
        )

    opts = "".join(f'<option value="{yr}">{yr} season</option>' for yr, _ in newest)
    blocks = "".join(
        f'<section class="scoped" data-season="{yr}">'
        f'<div class="card"><strong>{summaries.get(yr, "")}</strong></div>'
        f'{_week_grid(league, yr_weeks, yr, season)}</section>'
        for yr, yr_weeks in newest
    )
    return f"""{hero}
<h2>Season archive</h2>
<div class="controls scopebar">
  <label class="ctl-sort">Season:<select id="scopeseason">{opts}</select></label>
</div>
{blocks}
{TRACK_SCRIPT}"""


def _pixel_section(week: dict, league: str, players) -> str:
    """The week's headline pick, with the full case for it."""
    pick = week.get("pixel")
    if not pick:
        return ""
    graded = week.get("pixel_graded")
    price = pixel.format_american(pick["american"])
    legs = "".join(
        f'<li class="pxleg"><span class="pxdetail">{leg["detail"]}</span> '
        f'<span class="meta">{leg["matchup"]} &middot; '
        f'{pixel.format_american(leg["price"])} &middot; model {leg["prob"]:.0%}</span></li>'
        for leg in pick["legs"]
    )
    kind = (f'{len(pick["legs"])}-leg parlay' if pick["is_parlay"] else "single")
    outcome = ""
    if graded and graded.get("result"):
        tone = {"win": "strong", "loss": "bad", "push": "mid"}[graded["result"]]
        outcome = (f'<span class="outcome t-{tone}">{graded["result"].upper()}'
                   f' &middot; {graded["profit"]:+.2f}u</span>')
    rationale = "".join(
        f"<p class='ftext'>{p}</p>"
        for p in analysis.pixel_rationale(pick, week["preds"], league)
    )
    return f"""<div class="pixel card">
<div class="pxhead"><span class="pxbadge">Pixel&rsquo;s Pick</span>
<span class="pxprice">{price}</span>
<span class="meta">{kind} &middot; model {pick["prob"]:.0%} vs
{pixel.implied_probability(pick["american"]):.0%} implied</span>{outcome}</div>
<ul class="pxlegs">{legs}</ul>
<details class="more"><summary>Why this is the pick</summary>
<div class="analysis">{rationale}</div></details>
</div>"""


def _stat_tile(title: str, big: str, tone: str, sub: str,
               league: str = "", season: int | None = None) -> str:
    attrs = (f' data-league="{league}"' if league else "") + (
        f' data-season="{season}"' if season is not None else "")
    return (f'<div class="tile scoped"{attrs}><div class="tiletitle">{title}</div>'
            f'<div class="tilebig t-{tone}">{big}</div>'
            f'<div class="tilesub meta">{sub}</div></div>')


def _totals(weeks: list[dict], kind: str) -> dict:
    graded = [w for w in weeks if w[kind]["decided"]]
    wins = sum(w[kind]["wins"] for w in graded)
    losses = sum(w[kind]["losses"] for w in graded)
    profit = sum(w[kind]["profit"] for w in graded)
    decided = wins + losses
    return {"wins": wins, "losses": losses, "decided": decided, "profit": profit,
            "hit_rate": wins / decided if decided else 0.0,
            "roi": profit / decided if decided else 0.0}


def _tracking_page(league_weeks: dict[tuple[str, int], list[dict]], cur_season: dict) -> str:
    """Results page: how the model's moneyline and spread picks are doing."""
    tiles, rows = [], []
    for (league, season), weeks in league_weeks.items():
        for kind, name, breakeven in (("ml", "moneyline", 0.50), ("ats", "spread", 0.524)):
            t = _totals(weeks, kind)
            title = name.capitalize()
            if not t["decided"]:
                tiles.append(_stat_tile(title, "—", "mid", "no graded picks yet",
                                        league=league, season=season))
                continue
            verb = "won" if kind == "ml" else "covered"
            tiles.append(_stat_tile(
                title, f'{t["wins"]}-{t["losses"]}',
                grades.hit_tone(t["hit_rate"], breakeven),
                f'{t["hit_rate"]:.0%} of picks {verb} &middot; '
                f'{t["profit"]:+.1f} units ({t["roi"]:+.1%} ROI)',
                league=league, season=season))

        for w in weeks:
            if not w["ml"]["decided"]:
                continue
            ml, ats = w["ml"], w["ats"]
            rows.append({
                "league": league, "season": season, "week": w["week"], "label": w["label"],
                "slug": week_slug(league, w["week"], season, cur_season.get(league)),
                "ml": ml, "ats": ats,
            })

    cat_table = (_clv_section(league_weeks) + _tier_table(league_weeks)
                 + _category_table(league_weeks))

    if not rows:
        table = ('<div class="card"><div class="meta">Once games are played, every week\'s '
                 "moneyline and spread results land here.</div></div>")
    else:
        def cell(rec: dict, breakeven: float) -> str:
            if not rec["decided"]:
                return '<td class="num meta">&mdash;</td><td class="num meta">&mdash;</td>'
            return (f'<td class="num"><span class="t-{grades.hit_tone(rec["hit_rate"], breakeven)}">'
                    f'{rec["wins"]}-{rec["losses"]}</span></td>'
                    f'<td class="num t-{grades.roi_tone(rec["roi"])}">{rec["profit"]:+.1f}u</td>')

        body = "".join(
            f'<tr class="trow scoped" data-league="{r["league"]}" '
            f'data-season="{r["season"]}" '
            f'data-week="{r["season"] * 100 + r["week"]}" '
            f'data-mlrate="{r["ml"]["hit_rate"]:.4f}" data-atrate="{r["ats"]["hit_rate"]:.4f}" '
            f'data-mlprofit="{r["ml"]["profit"]:.3f}" data-atprofit="{r["ats"]["profit"]:.3f}">'
            f'<td><a href="{r["slug"]}">{r["label"]}</a></td>'
            f'{cell(r["ml"], 0.50)}{cell(r["ats"], 0.524)}</tr>'
            for r in rows
        )
        table = f"""<table class="track" id="tracktable">
<thead><tr><th>Week</th><th class="num">Moneyline</th><th class="num">ML units</th>
<th class="num">Spread</th><th class="num">Spread units</th></tr></thead>
<tbody>{body}</tbody></table>"""

    seasons = sorted({season for _, season in league_weeks}, reverse=True)
    season_opts = "".join(f'<option value="{y}">{y} season</option>' for y in seasons)
    scope_bar = f"""<div class="controls scopebar">
  <div class="ctl-group">
    <button class="chip active" data-lgfilter="all">Both leagues</button>
    <button class="chip" data-lgfilter="nfl">NFL</button>
    <button class="chip" data-lgfilter="ncaa">NCAA</button>
  </div>
  <label class="ctl-sort">Season:<select id="scopeseason">{season_opts}</select></label>
</div>"""

    return f"""<h2>Tracking &mdash; how the picks are doing</h2>
{scope_bar}
<div class="tiles">{''.join(tiles)}</div>
<details class="more explainbox"><summary>How to read this</summary>
<span class="meta">Every game gets two separate picks: a <em>moneyline</em> pick (who wins the
game outright) and a <em>spread</em> pick (which side beats the betting line). They often
disagree, so they are tracked separately. "Units" is profit from betting one unit on every
pick at the prices the sportsbook actually posted &mdash; positive means the picks made
money, negative means they lost. Spread picks need about 52.4% to break even;
<span class="t-strong">green</span> is beating that, <span class="t-bad">red</span> is
losing money.</span></details>
<div class="controls">
  <label class="ctl-sort">Sort:
    <select id="tracksort">
      <option value="week">Most recent week first</option>
      <option value="mlrate">Best moneyline record</option>
      <option value="atrate">Best spread record</option>
      <option value="mlprofit">Most moneyline profit</option>
      <option value="atprofit">Most spread profit</option>
    </select>
  </label>
</div>
{table}
{cat_table}
{TRACK_SCRIPT}"""


def _clv_section(league_weeks: dict[tuple[str, int], list[dict]]) -> str:
    """Closing line value: the leading indicator of whether an edge is real."""
    tiles = []
    for (league, season), weeks in league_weeks.items():
        graded = [w["graded"] for w in weeks if len(w["graded"])]
        if not graded:
            continue
        summary = tracking.clv_summary(pd.concat(graded, ignore_index=True))
        if not summary.get("n"):
            continue
        rate = summary["beat_rate"]
        tone = "strong" if rate >= 0.55 else "good" if rate > 0.50 else "bad"
        tiles.append(_stat_tile(
            "Closing line value", f"{rate:.0%}", tone,
            f'market moved toward the pick on {summary["n"]} spread picks '
            f'&middot; {summary["avg_points"]:+.2f} pts on average',
            league=league, season=season))
    if not tiles:
        return ""
    return f"""<h2>Closing line value</h2>
<details class="more explainbox"><summary>What closing line value means</summary>
<span class="meta">The most reliable test of whether picks
carry an edge. If a pick is genuinely good, the market tends to move toward that side
before kickoff &mdash; the number taken beats the number at close. Above 50% means the
picks are on the right side of where money goes; below 50% means the opposite, and no
run of wins changes that verdict. Win rate over one season is mostly noise; this is not.
Opening prices are published for college games only, so the NFL cannot be measured this
way from free data.</span></details>
<div class="tiles">{''.join(tiles)}</div>"""


def _tier_table(league_weeks: dict[tuple[str, int], list[dict]]) -> str:
    """Picks (the model saw value at the price) against Leans (it had an
    opinion but the price did not justify a bet), plus Pixel's Picks."""
    body = []
    for (league, season), weeks in league_weeks.items():
        graded = [w["graded"] for w in weeks if len(w["graded"])]
        if not graded:
            continue
        allg = pd.concat(graded, ignore_index=True)
        for tier, label in (("pick", "Picks (value)"), ("lean", "Leans")):
            cells = []
            for kind, breakeven in (("ml", 0.50), ("ats", 0.524)):
                rec = tracking.tier_record(allg, kind, tier)
                if not rec["decided"]:
                    cells.append('<td class="num meta">&mdash;</td>'
                                 '<td class="num meta">&mdash;</td>')
                    continue
                cells.append(
                    f'<td class="num"><span class="t-{grades.hit_tone(rec["hit_rate"], breakeven)}">'
                    f'{rec["wins"]}-{rec["losses"]}</span> '
                    f'<span class="meta">{rec["hit_rate"]:.0%}</span></td>'
                    f'<td class="num t-{grades.roi_tone(rec["roi"])}">{rec["profit"]:+.1f}u '
                    f'<span class="meta">{rec["roi"]:+.0%}</span></td>')
            body.append(f'<tr class="scoped" data-league="{league}" data-season="{season}">'
                        f'<td>{label}</td>{"".join(cells)}</tr>')

        # Pixel's Picks are single wagers (sometimes parlays), so one column pair
        settled = [w["pixel_graded"] for w in weeks if w.get("pixel_graded")]
        if settled:
            rec = pixel.record(settled)
            if rec["decided"]:
                body.append(
                    f'<tr class="scoped" data-league="{league}" data-season="{season}">'
                    f'<td><strong>Pixel&rsquo;s Picks</strong></td>'
                    f'<td class="num"><span class="t-{grades.hit_tone(rec["hit_rate"], 0.50)}">'
                    f'{rec["wins"]}-{rec["losses"]}</span> '
                    f'<span class="meta">{rec["hit_rate"]:.0%}</span></td>'
                    f'<td class="num t-{grades.roi_tone(rec["roi"])}">{rec["profit"]:+.1f}u '
                    f'<span class="meta">{rec["roi"]:+.0%}</span></td>'
                    f'<td class="num meta">&mdash;</td><td class="num meta">&mdash;</td></tr>')
    if not body:
        return ""
    return f"""<h2>Picks vs Leans</h2>
<details class="more explainbox"><summary>Picks, Leans and Pixel&rsquo;s Picks explained</summary>
<span class="meta">A <strong>Pick</strong> is a side where the
calibrated probability beats the price &mdash; the model thinks the bet is worth making.
A <strong>Lean</strong> is everything else: the model has an opinion and shows all its
reasoning, but the price does not justify backing it. Leans are information, not bets.
<strong>Pixel&rsquo;s Picks</strong> are the single highest-value play each week.</span></details>
<table class="track">
<thead><tr><th>Group</th><th class="num">Moneyline</th><th class="num">ML units</th>
<th class="num">Spread</th><th class="num">Spread units</th></tr></thead>
<tbody>{''.join(body)}</tbody></table>"""


def _category_table(league_weeks: dict[tuple[str, int], list[dict]]) -> str:
    """How each pick bucket (Locks, Value, Pick'ems, Upsets) has actually done."""
    body = []
    for (league, season), weeks in league_weeks.items():
        graded = [w["graded"] for w in weeks if len(w["graded"])]
        if not graded:
            continue
        for cat in tracking.category_records(pd.concat(graded, ignore_index=True)):
            cells = []
            for kind, breakeven in (("ml", 0.50), ("ats", 0.524)):
                rec = cat[kind]
                if not rec["decided"]:
                    cells.append('<td class="num meta">&mdash;</td>'
                                 '<td class="num meta">&mdash;</td>')
                    continue
                cells.append(
                    f'<td class="num"><span class="t-{grades.hit_tone(rec["hit_rate"], breakeven)}">'
                    f'{rec["wins"]}-{rec["losses"]}</span> '
                    f'<span class="meta">{rec["hit_rate"]:.0%}</span></td>'
                    f'<td class="num t-{grades.roi_tone(rec["roi"])}">{rec["profit"]:+.1f}u '
                    f'<span class="meta">{rec["roi"]:+.0%}</span></td>')
            body.append(
                f'<tr class="scoped" data-league="{league}" data-season="{season}">'
                f'<td>{cat["label"]}</td>{"".join(cells)}</tr>'
            )
    if not body:
        return ""
    return f"""<h2>By pick category</h2>
<details class="more explainbox"><summary>What the buckets mean</summary>
<span class="meta">Every pick is also filed into a bucket:
<strong>Locks</strong> are games the model is at least 70% sure of, <strong>Value</strong>
means it disagrees with the market by 2.5+ points, <strong>Pick'ems</strong> are near
coin-flips, and <strong>Upsets</strong> are value picks on the market's underdog. This is
how each bucket has actually paid.</span></details>
<table class="track">
<thead><tr><th>Category</th><th class="num">Moneyline</th><th class="num">ML units</th>
<th class="num">Spread</th><th class="num">Spread units</th></tr></thead>
<tbody>{''.join(body)}</tbody></table>"""


TRACK_SCRIPT = """<script>
(function () {
  // one scope for the whole page: a season, and one or both leagues
  var seasonSel = document.getElementById('scopeseason');
  var lg = 'all';
  function scope() {
    var yr = seasonSel ? seasonSel.value : null;
    document.querySelectorAll('.scoped').forEach(function (el) {
      var okSeason = !yr || !el.dataset.season || el.dataset.season === yr;
      var okLeague = lg === 'all' || !el.dataset.league || el.dataset.league === lg;
      el.hidden = !(okSeason && okLeague);
    });
    document.querySelectorAll('table.track').forEach(function (t) {
      var live = t.querySelectorAll('tbody tr.scoped:not([hidden])').length;
      var wrap = t.closest('.tablewrap') || t;
      wrap.hidden = live === 0;
    });
  }
  document.querySelectorAll('[data-lgfilter]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('[data-lgfilter]').forEach(function (b) {
        b.classList.remove('active');
      });
      btn.classList.add('active');
      lg = btn.dataset.lgfilter;
      scope(); if (window.__trackApply) window.__trackApply();
    });
  });
  if (seasonSel) seasonSel.addEventListener('change', function () {
    scope(); if (window.__trackApply) window.__trackApply();
  });
  scope();
})();
(function () {
  var tbody = document.querySelector('#tracktable tbody');
  if (!tbody) return;
  var lg = 'all';
  function apply() {
    var key = document.getElementById('tracksort').value;
    var rows = Array.prototype.slice.call(tbody.querySelectorAll('.trow'));
    rows.sort(function (a, b) {
      return parseFloat(b.dataset[key]) - parseFloat(a.dataset[key]);
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
  }
  document.getElementById('tracksort').addEventListener('change', apply);
  window.__trackApply = apply;
  apply();
})();
</script>"""


def build_site(out_dir: Path = SITE_DIR, refresh: bool = True) -> Path:
    global SITE_DIR
    SITE_DIR = out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    league_weeks: dict[tuple[str, int], list[dict]] = {}
    cur_season: dict[str, int] = {}
    for league in ("nfl", "ncaa"):
        # Seasons are built oldest first so each one's calibration is seeded
        # with every settled game that came before it, the same way the live
        # season will be seeded when it starts.
        probe_games, _, _, _ = pipeline.load_league_inputs(
            league, refresh=refresh, recent_only=True, with_players=True)
        season = int(probe_games["season"].max())
        cur_season[league] = season
        first = season - ARCHIVE_SEASONS + 1
        prepared = prepare_league(league, refresh=False, first_season=first)

        by_season: list[tuple[int, list[dict]]] = []
        summaries_by_season: dict[int, str] = {}
        seed_frames: list[pd.DataFrame] = []
        for yr in range(first, season + 1):
            seed = (pd.concat(seed_frames, ignore_index=True) if seed_frames else None)
            yr_weeks, yr_summary, _ = build_league_weeks(
                league, refresh=False, season=yr, write_pages=False,
                seed_history=seed, prepared=prepared,
            )
            if not yr_weeks:
                continue
            by_season.append((yr, yr_weeks))
            summaries_by_season[yr] = yr_summary
            league_weeks[(league, yr)] = yr_weeks
            graded_rows = [w["graded"] for w in yr_weeks if len(w["graded"])]
            if graded_rows:
                seed_frames.append(pd.concat(graded_rows, ignore_index=True))

        if not by_season:
            continue
        index = list(reversed(by_season))  # newest first in the dropdown
        for yr, yr_weeks in by_season:
            write_week_pages(league, yr_weeks, yr, season, index)

        weeks = dict(by_season).get(season, [])
        season_summary = summaries_by_season.get(season, "")

        (out_dir / f"{league}.html").write_text(
            _page(f"{league.upper()} archive — Gridiron Engine",
                  _league_hub(league, by_season, summaries_by_season, season))
        )
        # current week = first with games still to play, else the last graded
        current = next((w for w in weeks if not w["complete"]), weeks[-1] if weeks else None)
        summaries.append((league, season_summary, current))

    (out_dir / "tracking.html").write_text(
        _page("Tracking — Gridiron Engine", _tracking_page(league_weeks, cur_season))
    )

    cards = "\n".join(
        f'<a class="card" href="{week_slug(league, cur["week"]) if cur else f"{league}.html"}">'
        f'<div class="teams">{league.upper()}</div>'
        f'<div class="meta">{(cur["label"] + " &middot; " + cur["headline"]) if cur else "no games yet"}</div>'
        f'<div class="meta">{summary}</div></a>'
        for league, summary, cur in summaries
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
