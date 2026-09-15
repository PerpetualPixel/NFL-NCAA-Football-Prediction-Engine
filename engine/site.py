"""Static site generator for GitHub Pages.

Renders the upcoming week's picks for each league (plus a graded look at the
most recent completed week) into a small static site in site/.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from . import analysis, calibrate, feed, grades, locks, model as model_mod
from . import odds, pipeline, pixel, teams, tracking, weather
from .config import LEAGUES
from .data import espn_odds

SITE_DIR = Path(__file__).resolve().parents[1] / "site"

CSS = """
:root {
  --bg: #f3f5f8; --card: #ffffff; --sunken: #f6f8fb; --ink: #131820;
  --muted: #5f6b7a; --line: #e2e7ee; --accent: #1f4fd8; --accent-ink: #ffffff;
  --accent-soft: rgba(31, 79, 216, 0.10);
  --good: #0f7a57; --good-soft: rgba(15, 122, 87, 0.12);
  --warn: #b25c07; --warn-soft: rgba(178, 92, 7, 0.12);
  --bad: #b8262c; --bad-soft: rgba(184, 38, 44, 0.12);
  --shadow: 0 1px 2px rgba(16, 24, 40, 0.06), 0 4px 14px rgba(16, 24, 40, 0.05);
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0b0e12; --card: #151a21; --sunken: #10141a; --ink: #e8ebef;
    --muted: #97a1ae; --line: #252c36; --accent: #7fa6ff; --accent-ink: #0b0e12;
    --accent-soft: rgba(127, 166, 255, 0.14);
    --good: #3ddc9b; --good-soft: rgba(61, 220, 155, 0.14);
    --warn: #fbbf24; --warn-soft: rgba(251, 191, 36, 0.14);
    --bad: #ff7b7b; --bad-soft: rgba(255, 123, 123, 0.14);
    --shadow: 0 1px 2px rgba(0, 0, 0, 0.4), 0 6px 18px rgba(0, 0, 0, 0.35);
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 940px; margin: 0 auto; padding: 20px 16px 72px; }
header.site {
  display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
  margin-bottom: 6px; padding-bottom: 12px; border-bottom: 1px solid var(--line);
}
header.site h1 {
  font-size: 1.3rem; margin: 0; letter-spacing: -0.02em; display: flex; align-items: center; gap: 9px;
}
header.site h1 a { color: inherit; text-decoration: none; }
.mark {
  width: 22px; height: 22px; border-radius: 6px; flex-shrink: 0;
  background: linear-gradient(135deg, var(--accent), #8b5cf6);
  box-shadow: inset 0 0 0 2px rgba(255,255,255,0.35);
}
header.site nav { display: flex; gap: 2px; flex-wrap: wrap; margin-left: auto; }
header.site nav a {
  color: var(--muted); text-decoration: none; font-weight: 600; font-size: 0.86rem;
  padding: 6px 11px; border-radius: 999px;
}
header.site nav a:hover { color: var(--accent); background: var(--accent-soft); }
header.site nav a.active { color: var(--accent-ink); background: var(--accent); }
.stamp {
  color: var(--muted); font-size: 0.78rem; margin: 10px 0 22px; line-height: 1.5;
}
.stamp strong { color: var(--ink); }
h2 { font-size: 1.12rem; margin: 30px 0 12px; letter-spacing: -0.01em; }
h3 { font-size: 1rem; }
a { color: var(--accent); }
.card {
  background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 16px 18px; margin-bottom: 14px; box-shadow: var(--shadow);
}
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
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
  border-radius: 10px; padding: 10px 12px;
}
.bet:first-child { border-color: var(--accent-soft); box-shadow: inset 3px 0 0 var(--accent); }
.betlabel {
  font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--muted); font-weight: 700;
}
.betvalue { font-size: 0.95rem; font-weight: 700; margin-top: 2px; }
.betvalue .price { color: var(--muted); font-weight: 600; font-size: 0.88rem; }
.betnote { font-size: 0.76rem; margin-top: 1px; }
.outcome {
  display: inline-block; margin-top: 5px; font-size: 0.7rem; font-weight: 800;
  letter-spacing: 0.05em; padding: 1px 6px; border-radius: 4px;
  border: 1px solid currentColor;
}
.usageteam { margin-bottom: 12px; }
.usageteam h5 {
  margin: 8px 0 4px; font-size: 0.82rem; text-transform: uppercase;
  letter-spacing: 0.04em; color: var(--accent);
}
.factor { margin-bottom: 11px; list-style: none; }
ul.factors { padding-left: 0; }
.fhead { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.fname { font-weight: 700; font-size: 0.88rem; }
.fverdict {
  font-size: 0.7rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.04em;
}
.ftext { font-size: 0.86rem; color: var(--ink); margin-top: 1px; }
.provenance {
  font-size: 0.74rem; color: var(--muted); margin-top: 8px;
  border-top: 1px dashed var(--line); padding-top: 6px;
}
.stale { color: var(--warn, #b45309); font-weight: 700; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-bottom: 16px; }
.tile { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }
.tiletitle { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); font-weight: 700; }
.tilebig { font-size: 1.7rem; font-weight: 800; line-height: 1.15; margin: 4px 0 2px; }
.tilesub { font-size: 0.78rem; }
.tablewrap { overflow-x: auto; }
.crest { width: 18px; height: 18px; vertical-align: -4px; margin-right: 5px; }
.sched { margin: 0 0 18px; }
.schedhead {
  display: flex; justify-content: space-between; align-items: baseline; gap: 10px;
  padding: 8px 12px; background: var(--card); border: 1px solid var(--line);
  border-radius: 8px 8px 0 0; font-size: 0.78rem; text-transform: uppercase;
  letter-spacing: 0.05em; font-weight: 800; color: var(--muted);
}
.schedgrid {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  border: 1px solid var(--line); border-top: none; border-radius: 0 0 8px 8px;
  overflow: hidden; background: var(--card);
}
.schedgrid > .srow:nth-child(odd) { border-right: 1px solid var(--line); }
@media (max-width: 720px) {
  .schedgrid { grid-template-columns: minmax(0, 1fr); }
  .schedgrid > .srow:nth-child(odd) { border-right: none; }
}
.srow {
  display: flex; justify-content: space-between; align-items: center; gap: 10px;
  padding: 9px 12px; border-bottom: 1px solid var(--line);
  color: var(--ink); text-decoration: none;
}
.srow:hover { background: var(--accent-soft); }
.steams { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.steam {
  display: flex; align-items: center; gap: 7px; font-size: 0.9rem; font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.steam .crest { width: 20px; height: 20px; margin: 0; vertical-align: middle; }
.steam .sscore { margin-left: auto; padding-left: 12px; font-variant-numeric: tabular-nums; }
.steam.lost { color: var(--muted); font-weight: 500; }
.swhen {
  text-align: right; font-size: 0.78rem; color: var(--muted); line-height: 1.35;
  white-space: nowrap; flex-shrink: 0;
}
.swhen strong { display: block; color: var(--ink); font-weight: 700; }
#loadmore { margin-top: 10px; }
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
.tag-lock { color: var(--accent-ink); background: var(--good); border-color: var(--good); font-weight: 800; }
.tag-pick { color: var(--good); background: var(--good-soft); border-color: transparent; font-weight: 800; }
.tag-lean { color: var(--muted); background: var(--sunken); border-color: transparent; }
.tag-pass { color: var(--muted); border-style: dashed; }
.tag-value { color: var(--accent); border-color: var(--accent); }
.tag-upset { color: var(--bad); border-color: var(--bad); }
.tag-key { color: var(--warn); border-color: var(--warn); }
/* Every tag explains itself on hover or focus. A reader should never have to
   go looking for a legend to find out what "near 3" means. */
.tag[data-tip] { position: relative; cursor: help; }
.tag[data-tip]:hover::after, .tag[data-tip]:focus-visible::after {
  content: attr(data-tip); position: absolute; left: 50%; bottom: calc(100% + 7px);
  transform: translateX(-50%); z-index: 20; width: max-content; max-width: 230px;
  padding: 7px 9px; border-radius: 7px; border: 1px solid var(--line);
  background: var(--card); color: var(--ink); box-shadow: var(--shadow);
  font-size: 0.72rem; font-weight: 500; line-height: 1.35; letter-spacing: 0;
  text-transform: none; white-space: normal; text-align: left; pointer-events: none;
}
.tag[data-tip]:hover::before, .tag[data-tip]:focus-visible::before {
  content: ""; position: absolute; left: 50%; bottom: calc(100% + 2px);
  transform: translateX(-50%); z-index: 21; border: 5px solid transparent;
  border-top-color: var(--line); pointer-events: none;
}

/* The pick itself. Everything else on the card is context for this line, so
   it is the only thing at this size and it carries the tier's colour. */
.pickhero {
  display: flex; justify-content: space-between; align-items: center; gap: 14px;
  flex-wrap: wrap; margin: 12px 0 8px; padding: 12px 14px; border-radius: 10px;
  background: var(--sunken); border: 1px solid var(--line); border-left: 5px solid var(--muted);
}
.pickhero.t-lock { border-left-color: var(--good); background: var(--good-soft); }
.pickhero.t-pick { border-left-color: var(--good); }
.pickhero.t-lean { border-left-color: var(--warn); }
.pickhero.t-pass { border-left-color: var(--line); border-left-style: dashed; }
.pickside { min-width: 0; }
.pickkicker {
  display: flex; align-items: center; gap: 7px; flex-wrap: wrap;
  font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.09em;
  font-weight: 800; color: var(--muted);
}
.pickteam {
  font-size: 1.5rem; font-weight: 800; line-height: 1.15; margin-top: 2px;
  overflow-wrap: anywhere;
}
.pickhero.t-pass .pickteam { color: var(--muted); font-weight: 700; }
.picknote { font-size: 0.76rem; color: var(--muted); margin-top: 3px; }
.pickodds { text-align: right; flex-shrink: 0; }
.pickprice { font-size: 1.28rem; font-weight: 800; white-space: nowrap; }
.pickprob { font-size: 0.76rem; color: var(--muted); }
@media (max-width: 430px) {
  .pickteam { font-size: 1.24rem; }
  .pickodds { text-align: left; }
}
.oddssrc { color: var(--muted); font-size: 0.78rem; }
.form-w { color: var(--good); font-weight: 800; }
.form-l { color: var(--bad); font-weight: 800; }
.form-t { color: var(--muted); font-weight: 800; }
.lgtag.live { color: var(--good); border-color: var(--good); }
.emptynote { color: var(--muted); font-size: 0.9rem; padding: 8px 2px; }
.scopebar { position: sticky; top: 0; z-index: 5; background: var(--bg); padding: 8px 0; }
.hero.card {
  display: block; text-decoration: none; color: inherit; position: relative;
  border: 1px solid var(--accent); padding: 20px 22px; overflow: hidden;
  background: linear-gradient(135deg, var(--accent-soft), var(--card) 60%);
}
.hero.card:hover { transform: translateY(-1px); }
.ladder { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 12px 0 18px; }
.rung { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; box-shadow: var(--shadow); }
.rung .tiletitle { display: flex; justify-content: space-between; gap: 6px; }
.rung .tilebig { font-size: 1.3rem; }
.rung .tilesub { line-height: 1.35; }
.wknav {
  position: sticky; top: 0; z-index: 6; display: flex; align-items: center; gap: 10px;
  flex-wrap: wrap; padding: 8px 0; margin-bottom: 8px;
  background: var(--bg); border-bottom: 1px solid var(--line);
}
.wknav-btn {
  color: var(--accent); text-decoration: none; font-weight: 700; font-size: 0.86rem;
  padding: 6px 10px; border-radius: 8px; background: var(--accent-soft);
}
.wknav-btn.disabled { color: var(--muted); background: var(--sunken); }
.wksel { color: var(--muted); font-size: 0.84rem; }
.wksel select {
  font: inherit; font-size: 0.86rem; margin-left: 6px; padding: 5px 8px;
  border-radius: 8px; border: 1px solid var(--line); background: var(--card); color: var(--ink);
}
.weekhead { font-size: 0.95rem; color: var(--muted); margin: -4px 0 14px; }
footer { color: var(--muted); font-size: 0.78rem; margin-top: 40px; padding-top: 14px; border-top: 1px solid var(--line); }
.weekgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
.weektile {
  display: block; text-decoration: none; color: inherit; background: var(--card);
  border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px;
}
.weektile:hover { border-color: var(--accent); }
.wt-label { font-weight: 700; font-size: 0.9rem; }
.wt-detail { font-size: 0.78rem; margin-top: 2px; }
.upcoming { color: var(--accent); font-weight: 700; }
.leagues { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
.leagues .card { text-decoration: none; color: inherit; }
.leagues .card:hover { border-color: var(--accent); }
.herolabel {
  font-size: 0.7rem; font-weight: 800; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--accent);
}
.heroweek { font-size: 1.5rem; font-weight: 800; margin: 2px 0 4px; letter-spacing: -0.02em; }
.herogo { margin-top: 8px; font-weight: 700; color: var(--accent); font-size: 0.9rem; }
.card.pending { border-style: dashed; }
.stage {
  display: inline-block; font-size: 0.66rem; font-weight: 800; letter-spacing: 0.05em;
  text-transform: uppercase; padding: 2px 7px; border-radius: 4px; margin-right: 8px;
}
.stage.locked { background: var(--good); color: var(--accent-ink); }
.stage.leanstage { background: var(--warn-soft); color: var(--warn); }
.stage.earlystage { background: var(--sunken); color: var(--muted); }
.stage.started { background: var(--sunken); color: var(--muted); border: 1px solid var(--line); }
.stage.noresult { background: var(--bad-soft); color: var(--bad); }
.pixel.card { border: 2px solid var(--accent); }
.pxhead { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.pxbadge {
  background: var(--accent); color: #fff; font-weight: 800; font-size: 0.72rem;
  letter-spacing: 0.05em; text-transform: uppercase; padding: 3px 9px; border-radius: 5px;
}
.pxprice { font-size: 1.25rem; font-weight: 800; }
.pxlegs { list-style: none; padding: 0; margin: 10px 0 4px; }
.boardcard { border-left: 3px solid var(--accent); }
.boardnum {
  font-weight: 800; font-size: 0.95rem; color: var(--accent);
  background: var(--sunken); border-radius: 5px; padding: 2px 8px;
}
.slothead {
  font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--muted); font-weight: 800; margin: 18px 0 8px;
}
.pxleg { padding: 6px 0; border-bottom: 1px solid var(--line); }
.pxdetail { font-weight: 700; }
.tag-pixel { color: #fff; background: var(--accent); border-color: var(--accent); }
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
    stamp = _fmt_et(pd.Timestamp.now(tz="UTC"))
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{CSS}</style></head>
<body><div class="wrap">
<header class="site"><h1><span class="mark"></span><a href="index.html">Gridiron Engine</a></h1>
<nav><a href="index.html">Home</a><a href="nfl.html">NFL</a><a href="ncaa.html">NCAAF</a>
<a href="tracking-nfl.html">Tracking (NFL)</a>
<a href="tracking-ncaa.html">Tracking (NCAAF)</a></nav></header>
<script>(function(){{var p=(location.pathname.split('/').pop()||'index.html');
document.querySelectorAll('header.site nav a').forEach(function(a){{var h=a.getAttribute('href');
if(h===p||(h==='nfl.html'&&/^nfl-/.test(p))||(h==='ncaa.html'&&/^ncaa-/.test(p)))a.classList.add('active');}});}})();</script>
<div class="stamp">Odds, injuries and forecasts as of <strong>{stamp}</strong>
&middot; rebuilt twice an hour (GitHub may delay a run) &middot; free public data
(nflverse / sportsdataverse / ESPN / Open-Meteo) &middot; walk-forward model, no leakage</div>
{body}
<footer>Model picks are analytical output, not betting advice. Ratings are opponent-adjusted
ridge estimates with recency decay; win probabilities from a normal margin model.</footer>
</div></body></html>"""


def _usage_html(row: pd.Series, usage, roster=None) -> str:
    """Target hierarchy and backfield split for both sides."""
    reports = analysis.usage_report(row, usage, roster)
    if not reports:
        return ""
    blocks = "".join(
        f'<div class="usageteam"><h5>{r["team"]}</h5>'
        + "".join(f'<p class="ftext">{p}</p>' for p in r["paragraphs"])
        + "</div>"
        for r in reports
    )
    # every name above is only as good as the roster it was checked against,
    # so the page says which roster that was and when it was published
    note = reports[0].get("note") if reports else ""
    source = f'<p class="provenance">{note}</p>' if note else ""
    return f'<h4>Who gets the ball</h4>{blocks}{source}'


def _roster_banner(league: str, roster) -> str:
    """The page's standing claim about how current its player data is.

    Put where a reader sees it before any name: what was checked, when, and
    — if the feed could not be read this build — that the names below are
    not vouched for.
    """
    if roster is None or getattr(roster, "empty", True):
        return ('<div class="weekhead"><span class="stale">Rosters could not be '
                'verified for this build</span> &mdash; player names and positions '
                'below come from past production and may be out of date. Picks stay '
                'provisional until a roster is read.</div>')
    where = "depth charts and injury reports" if league == "nfl" else "team rosters"
    if not roster.fresh:
        return (f'<div class="weekhead"><span class="stale">Roster data is stale</span> '
                f'&mdash; the newest {where} this build could read are from '
                f'{roster.as_of_text()}. Names below are as of then.</div>')
    return (f'<div class="weekhead">Rosters, {where} read '
            f'{roster.as_of_text()}. Every player named below was on that roster; '
            f'anyone ruled out is marked.</div>')


def _movement_html(row: pd.Series) -> str:
    text = analysis.line_movement(row)
    return f'<h4>Line movement</h4><p class="ftext">{text}</p>' if text else ""


# What every tag on a card means, in the fewest words that are still true.
# These are the hover bubbles; the wording matches the tier definitions the
# tracking pages report against, so a reader is never told two different
# things about the same word.
TAG_HELP = {
    "pixel": "The week&rsquo;s headline play, priced &minus;175 or better",
    "lock": "85%+ calibrated win chance &mdash; wins about 93% of the time, but pays very little",
    "pick": "70&ndash;85% calibrated win chance &mdash; the everyday play",
    "lean": "55&ndash;70% &mdash; a tilt, not a bet",
    "pass": "Under 55% &mdash; a coin flip, shown but never counted as a pick",
    "pickem": "The model has this within a coin flip of even",
    "value": "The model disagrees with the market by 2.5 points or more",
    "upset": "The model wants the side the market has as the underdog",
}

# Why a key number is worth flagging at all.
KEY_NUMBER_MEANING = {3: "a field goal", 7: "a touchdown",
                      10: "a touchdown and a field goal", 14: "two touchdowns"}


def _tag(tag: str, text: str, tip: str) -> str:
    """One chip that explains itself. tabindex makes the bubble reachable by
    keyboard and by tap, not just by mouse."""
    return (f'<span class="tag tag-{tag}" data-tip="{tip}" tabindex="0">{text}</span>')


def _key_number_tip(key: int) -> str:
    meaning = KEY_NUMBER_MEANING.get(key)
    tail = f" &mdash; {meaning}" if meaning else ""
    return (f"The projected margin is within a point of {key}{tail}. "
            "Football scores land on this number far more often than its neighbours, "
            "so a small line move matters here.")


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
    n = analysis._team_count(row)
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


def _kickoff(row: pd.Series, league: str = "nfl") -> tuple[str, float]:
    """Human kickoff label in Eastern time and a sortable epoch value."""
    kick = _kickoff_time(row, league)
    if pd.isna(kick):
        return "", 0.0
    local = _local(kick)
    label = f"{local.strftime('%a, %b %-d')} &middot; {local.strftime('%-I:%M %p')} ET"
    return label, kick.timestamp()


def _by_kick(preds: pd.DataFrame, league: str) -> pd.DataFrame:
    """Rows in true kickoff order (the feed's date column alone ties every
    game on the same day)."""
    if preds.empty:
        return preds
    kicks = [_kickoff_time(r, league) for _, r in preds.iterrows()]
    order = pd.Series([k.timestamp() if pd.notna(k) else float("inf") for k in kicks],
                      index=preds.index)
    return preds.loc[order.sort_values(kind="stable").index]


def _game_card(row: pd.Series, graded: bool, league: str,
               players: pd.DataFrame | None = None, is_pixel: bool = False,
               usage: pd.DataFrame | None = None, ctx: dict | None = None) -> str:
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
        source = row.get("odds_source")
        source = (f' <span class="oddssrc">({source})</span>'
                  if isinstance(source, str) and source else "")
        verdict = (f'<span class="{cls}">model {abs(edge):.1f} toward {side}</span>'
                   if abs(edge) >= 0.05 else '<span class="meta">model agrees with the market</span>')
        market = (f'<div class="meta">Market line: '
                  f'{odds.format_market(row.home_team, row.away_team, row.spread_line)}'
                  f'{source} &middot; {verdict}</div>')

    stage = row.get("release_stage") or "locked"
    stage_badge = _stage_badge(row, stage) if not graded else ""
    spread_line = row.get("spread_line")
    tags = odds.classify(row.home_win_prob, edge,
                         float(spread_line) if pd.notna(spread_line) else None)
    tier = row.get("ml_tier") or "lean"
    tags = [tier] + tags
    if is_pixel:
        tags = ["pixel"] + tags
    key = odds.near_key_number(row.pred_margin, league)
    label = {"pixel": "Pixel&rsquo;s Pick", "lock": "lock", "pick": "pick", "lean": "lean",
             "pass": "pass", "pickem": "pick'em", "value": "value", "upset": "upset"}
    chips = "".join(_tag(t, label.get(t, t), TAG_HELP.get(t, ""))
                    for t in tags if t not in tracking.TIER_LABELS)
    if key:
        chips += _tag("key", f"near {key}", _key_number_tip(key))

    result = ""
    if graded and pd.notna(row.get("margin")):
        hs = int(row.home_score) if pd.notna(row.get("home_score")) else "?"
        as_ = int(row.away_score) if pd.notna(row.get("away_score")) else "?"
        if row.margin == 0:
            verdict = '<span class="t-mid">TIE</span>'
        else:
            actual_winner = row.home_team if row.margin > 0 else row.away_team
            hit = actual_winner == pick
            verdict = (f'<span class="{"result-hit" if hit else "result-miss"}">'
                       f'{"HIT" if hit else "MISS"}</span>')
        result = (f'<div class="meta">Final: {row.away_team} {as_} &ndash; '
                  f'{row.home_team} {hs} &middot; {verdict}</div>')

    kick_label, kick_sort = _kickoff(row, league)
    neutral = " (neutral site)" if row.neutral else ""
    return f"""<div class="card game" id="g-{row.game_id}" data-kick="{kick_sort:.0f}" data-prob="{prob:.4f}" \
data-margin="{abs(row.pred_margin):.3f}" data-edge="{abs(edge) if edge is not None else 0:.3f}" \
data-tags="{' '.join(tags)}">
<div class="cardhead"><div class="teams">{teams.logo_img(row.get("away_key", ""), league)}{row.away_team}
&nbsp;@&nbsp;{teams.logo_img(row.get("home_key", ""), league)}{row.home_team}{neutral}</div>
<div class="kick">{stage_badge}{kick_label}</div></div>
{_pick_row(row, line, prob, ml)}
<div class="chips">{chips}</div>
{market}{result}{_analysis_panel(row, league, players, usage, ctx)}
</div>"""


def _pick_row(row: pd.Series, line: str, prob: float, ml: str) -> str:
    """The card's answer, then its supporting numbers.

    The old layout put the moneyline pick, the projected line and the spread
    lean in three boxes of equal weight, which left a reader working out
    which one was the pick. There is only one pick; it gets the size and the
    tier's colour, and everything else drops to a supporting row.
    """
    def outcome(kind: str) -> str:
        res = row.get(f"{kind}_result")
        if not res or (isinstance(res, float) and pd.isna(res)):
            return ""
        label = {"win": "WIN", "loss": "LOSS", "push": "PUSH"}[res]
        return f'<span class="outcome t-{grades.result_tone(res)}">{label}</span>'

    tier = row.get("ml_tier") or "lean"
    tier_label = tracking.TIER_LABELS.get(tier, "Lean")
    cal = row.get("ml_cal")
    shown_prob = float(cal) if pd.notna(cal) else prob
    ml_price = row.get("ml_price")
    priced = pd.notna(ml_price) and abs(ml_price) >= 100

    if priced:
        price_html = f'<div class="pickprice">{pixel.format_american(float(ml_price))}</div>'
        prob_html = f'<div class="pickprob">{shown_prob:.0%} to win &middot; fair {ml}</div>'
    else:
        price_html = '<div class="pickprice">no line</div>'
        prob_html = (f'<div class="pickprob">{shown_prob:.0%} to win &middot; fair {ml} '
                     f'&middot; not graded</div>')

    # The tier badge sits inside the hero rather than in the chip row below,
    # so "what kind of pick is this" is answered in the same glance as "who".
    # A Pass is still shown in full, but must never read as a recommendation.
    kicker = {"lock": "Moneyline pick", "pick": "Moneyline pick",
              "lean": "Moneyline lean",
              "pass": "Moneyline &mdash; no play"}.get(tier, "Moneyline lean")
    note = {"lock": "Heavy favourite: wins almost always, pays very little",
            "pick": "The everyday play",
            "lean": "A tilt, not a bet",
            "pass": "Too close to call &mdash; shown, but not counted as a pick",
            }.get(tier, "")
    badge = _tag(tier, tier_label, TAG_HELP.get(tier, ""))

    hero = f'''<div class="pickhero t-{tier}">
<div class="pickside"><div class="pickkicker">{badge} {kicker}</div>
<div class="pickteam">{row.get('ml_pick', '')}</div>
<div class="picknote">{note}</div></div>
<div class="pickodds">{price_html}{prob_html}{outcome('ml')}</div></div>'''

    ats = ""
    if pd.notna(row.get("ats_pick")) and row.get("ats_pick"):
        ats_line = row.get("ats_line")
        shown = (f"{'+' if ats_line > 0 else '-' if ats_line < 0 else ''}"
                 f"{odds.format_spread(ats_line)}" if pd.notna(ats_line) else "")
        assumed = " (&minus;110 assumed)" if row.get("ats_price_assumed") else ""
        ats = (f'<div class="bet"><div class="betlabel">Spread lean</div>'
               f'<div class="betvalue">{row.ats_pick} {shown}</div>'
               f'<div class="betnote meta">{abs(row.get("ats_edge", 0)):.1f} pt edge vs market'
               f'{assumed}</div>{outcome("ats")}</div>')

    return f"""{hero}<div class="bets">
<div class="bet"><div class="betlabel">Projected line</div>
<div class="betvalue">{line}</div>
<div class="betnote meta">model projection</div></div>
{ats}</div>"""


def _analysis_panel(row: pd.Series, league: str, players: pd.DataFrame | None,
                    usage: pd.DataFrame | None = None, ctx: dict | None = None) -> str:
    """Collapsed 'Full breakdown' section: the case for the tier, the game
    script, who is out, the forecast, recent form, players, matchups, units."""
    ctx = ctx or {}
    script = analysis.game_script(row, league)
    score = analysis.projected_score(row)
    if score:
        script = script[:1] + [score] + script[1:]
    paras = "".join(f"<p>{p}</p>" for p in script)

    case = analysis.confidence_case(row, ctx.get("tier_rates"), league)
    case_html = ("<h4>Why this is a " + tracking.TIER_LABELS.get(row.get("ml_tier") or "lean", "Lean")
                 + "</h4>" + "".join(f'<p class="ftext">{p}</p>' for p in case)) if case else ""

    roster = ctx.get("roster")
    injuries = analysis.injury_report(row, ctx.get("reports"), league, roster)
    inj_html = ""
    if injuries:
        items = "".join(f"<li><strong>{team}:</strong> {text}</li>" for team, text in injuries)
        inj_html = f'<h4>Who is out</h4><ul class="factors">{items}</ul>'

    form = analysis.recent_form(row, ctx.get("games"), league)
    form_html = ""
    if form:
        items = "".join(f"<li><strong>{team}:</strong> {text}</li>" for team, text in form)
        form_html = f'<h4>Recent form</h4><ul class="factors">{items}</ul>'

    cond = analysis.conditions_note(row)
    cond_html = f'<h4>Conditions</h4><p class="ftext">{cond}</p>' if cond else ""

    player_html = ""
    people = analysis.key_players(row, players, roster)
    if people:
        items = "".join(f"<li><strong>{team}:</strong> {text}</li>" for team, text in people)
        player_html = f'<h4>Players to watch</h4><ul class="factors">{items}</ul>'

    avail = analysis.availability_note(row, roster)
    avail_html = (f'<h4>Quarterbacks &amp; availability</h4><p class="ftext">{avail}</p>'
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
<summary>Full breakdown</summary>
<div class="analysis">
{case_html}
<h4>How the model sees it playing out</h4>{paras}
{inj_html}{avail_html}{cond_html}
{form_html}
{player_html}{factor_html}
{_usage_html(row, usage, roster)}
{_movement_html(row)}
<h4>Unit ratings</h4>{_unit_table(row)}
</div></details>"""


CONTROLS = """<div class="controls">
  <div class="ctl-group" role="group" aria-label="Filter games">
    <button class="chip active" data-filter="all">All</button>
    <button class="chip" data-filter="pixel">Pixel&rsquo;s Pick</button>
    <button class="chip" data-filter="lock">Locks</button>
    <button class="chip" data-filter="pick">Picks</button>
    <button class="chip" data-filter="lean">Leans</button>
    <button class="chip" data-filter="pass">Pass</button>
    <button class="chip" data-filter="pickem">Pick'ems</button>
    <button class="chip" data-filter="value">Value vs market</button>
    <button class="chip" data-filter="upset">Upsets</button>
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
             "away_spread_odds", "open_spread_line", "total_line", "odds_source"]
# live-conditions columns carried from the schedule into the prediction frame
CONDITION_COLS = ["stadium", "roof", "fc_temp", "fc_wind", "fc_gust", "fc_precip_prob",
                  "fc_precip", "forecast_at", "indoors_venue"]

# A pick made in August for a game in December is worthless: it cannot know
# who is hurt, who is starting, or what the weather will be. Picks arrive in
# two stages instead of one.
#
#   * A LEAN goes out a few days ahead. The parlays are built and the
#     reasoning is published, but availability can still change and the
#     number may move, so it is explicitly provisional.
#   * The pick LOCKS inside two hours of kickoff, once inactives, starting
#     lineups and the closing number are known. That is the version worth
#     acting on, and it carries a badge saying so.
LEAN_LEAD_HOURS = locks.LEAN_LEAD_HOURS
LOCK_LEAD_HOURS = locks.LOCK_LEAD_HOURS
EASTERN = "US/Eastern"


def _local(ts: pd.Timestamp) -> pd.Timestamp:
    """Kickoffs read best in US Eastern; fall back to UTC where the system
    has no timezone database."""
    try:
        return ts.tz_convert(EASTERN)
    except Exception:
        return ts


def _kickoff_time(row, league: str = "nfl") -> pd.Timestamp:
    """Kickoff as a real UTC instant (the NFL feed's date + Eastern time
    columns combined; the college feed's timestamp as is)."""
    kick = row.get("kickoff") if hasattr(row, "get") else None
    if kick is not None and not (isinstance(kick, float) and pd.isna(kick)) and pd.notna(kick):
        return pd.Timestamp(kick)
    return weather.kickoff_utc(row, league)


def _fmt_et(ts) -> str:
    if ts is None or pd.isna(ts):
        return "soon"
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return _local(ts).strftime("%a %b %-d, %-I:%M %p") + " ET"


def _stage_badge(row: pd.Series, stage: str) -> str:
    """The release-state chip on an unsettled card."""
    locked_at = row.get("locked_at")
    when = f" &middot; {_fmt_et(pd.to_datetime(locked_at, utc=True))}" if locked_at else ""
    hours = row.get("hours_out")
    if pd.notna(hours) and hours < -12 and not bool(row.get("completed")):
        return ('<span class="stage noresult">No result recorded &middot; '
                'cancelled or unscored</span>')
    if stage == "locked":
        reason = row.get("lock_reason") or ""
        return f'<span class="stage locked" title="{reason}">Final &middot; locked{when}</span>'
    if stage == "started":
        if row.get("post_kick"):
            return ('<span class="stage started">Graded after the fact &middot; '
                    'not a live pick</span>')
        return f'<span class="stage started">Kicked off &middot; pick as of{when}</span>'
    waiting = row.get("waiting_on")
    hint = f" &middot; locks on {waiting}" if isinstance(waiting, str) and waiting else ""
    if stage == "pending":
        # more than a week out: the lean is published, but nobody knows who is
        # hurt yet, so the card says how much weight to put on it
        return ('<span class="stage earlystage">Early lean &middot; before the '
                'injury report</span>')
    return f'<span class="stage leanstage">Lean &middot; not final{hint}</span>'


def _sched_row(row: pd.Series, league: str) -> str:
    """One ESPN-style line in the week's schedule index: the two teams stacked,
    kickoff (or the final score) on the right, linking down to the breakdown."""
    final = bool(row.get("completed")) and pd.notna(row.get("margin"))

    def team(side: str) -> str:
        key = row.get(f"{side}_key", "")
        name = teams.short_name(key, league) if key else row.get(f"{side}_team", "")
        score = ""
        cls = "steam"
        if final:
            pts = row.get(f"{side}_score")
            score = f'<span class="sscore">{int(pts)}</span>' if pd.notna(pts) else ""
            margin = row.margin if side == "home" else -row.margin
            if margin < 0:
                cls += " lost"
        return f'<div class="{cls}">{teams.logo_img(key, league)}{name}{score}</div>'

    hours = row.get("hours_out")
    if final:
        when = "<strong>Final</strong>"
    elif pd.notna(hours) and hours < -12 and not bool(row.get("completed")):
        when = "<strong>No result</strong>"
    else:
        # _kickoff already handles the feed's quirk of storing the date and the
        # time in separate columns, so reuse it and split the two lines apart
        label, _ = _kickoff(row, league)
        if not label:
            when = "<strong>TBD</strong>"
        else:
            day, _, time = label.partition(" &middot; ")
            when = f"<strong>{day}</strong>{time}"
    return (f'<a class="srow" href="#g-{row.game_id}">'
            f'<div class="steams">{team("away")}{team("home")}</div>'
            f'<div class="swhen">{when}</div></a>')


def _ladder(week: dict, league: str) -> str:
    """The risk ladder: what each kind of play on this page has actually
    done, so a reader can pick the rung that matches their appetite."""
    rates = week.get("tier_rates") or {}
    preds = week["preds"]
    counts = preds["ml_tier"].value_counts().to_dict() if "ml_tier" in preds else {}
    rungs = []
    blurbs = {
        "lock": "Heavy favourites the model backs at 85%+. Win almost always, pay little.",
        "pick": "Confident sides at 70&ndash;85%. The everyday play.",
        "lean": "A tilt, not a bet. Shown with full reasoning.",
        "pass": "Coin flips. No play.",
    }
    plural = {"lock": "Locks", "pick": "Picks", "lean": "Leans", "pass": "Pass"}
    for tier in tracking.TIER_ORDER:
        rec = rates.get(tier)
        n = counts.get(tier, 0)
        if rec and rec.get("decided", 0) >= 20:
            big = f'{rec["hit_rate"]:.0%}'
            tone = grades.hit_tone(rec["hit_rate"], 0.50) if tier != "pass" else "mid"
            sub = (f'{rec["wins"]}-{rec["losses"]} since 2023 &middot; {rec["roi"]:+.1%} ROI')
        else:
            big, tone, sub = "&mdash;", "mid", "no history yet"
        rungs.append(
            f'<div class="rung"><div class="tiletitle"><span>{plural[tier]}</span>'
            f'<span>{n} this week</span></div>'
            f'<div class="tilebig t-{tone}">{big}</div>'
            f'<div class="tilesub meta">{sub}<br>{blurbs[tier]}</div></div>')
    return (f'<div class="ladder">{"".join(rungs)}</div>' if rungs else "")


def _schedule_grid(league: str, week: dict, weeks: list[dict]) -> str:
    """The whole slate at a glance, above the breakdowns."""
    preds = week["preds"]
    if preds.empty:
        return ""
    total = week.get("season_weeks")
    heading = (f'{week["label"]} of {total}' if week["label"].startswith("Week") and total
               else week["label"])
    rows = "".join(_sched_row(row, league)
                   for _, row in _by_kick(preds, league).iterrows())
    return (f'<section class="sched"><div class="schedhead"><span>{heading}</span>'
            f'<span>{len(preds)} games</span></div>'
            f'<div class="schedgrid">{rows}</div></section>')


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
    archive covers, rather than repeating the work per season.

    Upcoming games also get a kickoff forecast and the price the book is
    showing right now, so the model rates the conditions the game will be
    played in and the picks are graded at a real, current number."""
    cfg = LEAGUES[league]
    # the archive needs its own seasons plus enough earlier ones to rate the
    # first archived week from
    games, unit_stats, players, availability = pipeline.load_league_inputs(
        league, refresh=refresh, recent_only=True, with_players=True,
        history_seasons=ARCHIVE_SEASONS + cfg.rating_window_seasons,
    )
    games = weather.attach_forecasts(games, league, refresh=refresh)
    games = _overlay_live_odds(games, league, refresh)
    feats = pipeline.build_walk_forward_features(
        league, games, unit_stats, start_season=first_season - 1,
        availability=availability,
    )
    score_cols = ["game_id", "home_score", "away_score", "gameday", "game_type"]
    if "gametime" in games.columns:
        score_cols.append("gametime")
    score_cols += [c for c in ODDS_COLS + CONDITION_COLS
                   if c in games.columns and c not in score_cols]
    feats = feats.merge(games[score_cols], on="game_id", how="left")
    availability = availability or {}
    return games, feats, players, cfg, availability.get("usage"), availability


def _overlay_live_odds(games: pd.DataFrame, league: str, refresh: bool) -> pd.DataFrame:
    """Lay ESPN's current prices over the archived ones for upcoming games."""
    if league == "nfl":
        if "espn" not in games.columns:
            return games
        ids = pd.to_numeric(games["espn"], errors="coerce").astype("Int64").astype(str)
        games = games.assign(espn_id=ids.where(games["espn"].notna(), None))
        id_col = "espn_id"
    else:
        id_col = "game_id"
    upcoming = games[~games["completed"].astype(bool)]
    if upcoming.empty:
        return games
    kicks = pd.Series([weather.kickoff_utc(r, league) for _, r in upcoming.iterrows()],
                      index=upcoming[id_col].astype(str))
    try:
        live = espn_odds.fetch_live_odds(league, kicks, refresh=refresh)
    except Exception:  # never let a pricing feed break the build
        live = None
    return espn_odds.overlay(games, live, id_col=id_col)


def build_league_weeks(
    league: str, refresh: bool, season: int | None = None,
    write_pages: bool = True, seed_history: pd.DataFrame | None = None,
    prepared: tuple | None = None, state: dict | None = None,
    now: pd.Timestamp | None = None, live: bool = False,
) -> tuple[list[dict], str, int]:
    """Build every week of a season (default: the current one).
    Returns (weeks, season_summary, season).

    `live` marks the season being played: its games move through the
    release stages and, once locked, are frozen in `state`."""
    if prepared is None:
        season_guess = season or 2100
        prepared = prepare_league(league, refresh, season_guess)
    games, feats, players, cfg, usage, availability = prepared
    state = state if state is not None else {"games": {}, "tickets": {}}
    now = now or pd.Timestamp.now(tz="UTC")
    reports = (availability or {}).get("injury_reports")
    roster = (availability or {}).get("roster")
    season = int(games["season"].max()) if season is None else season
    # predict_week trains on everything before the target week, so keep the
    # full feature history and iterate only the current season's weeks
    season_weeks = sorted(feats.loc[feats["season"] == season, "week"].unique())
    regular = games[(games["season"] == season)
                    & games["game_type"].astype(str).str.lower().isin(["reg", "regular"])]
    season_regular_weeks = int(regular["week"].max()) if len(regular) else None

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
        # release stage per game; frozen games take their values of record
        preds = locks.stage_games(preds, league, state, now, reports, live, roster)
        preds = tracking.grade(preds, margin_sigma=cfg.margin_sigma)
        # calibrate against everything already settled this build
        history = (pd.concat(settled, ignore_index=True) if settled
                   else preds.iloc[0:0])
        for kind in ("ml", "ats"):
            preds[f"{kind}_cal"] = calibrate.fit_and_apply(history, preds, kind)
            preds[f"{kind}_ev"] = calibrate.expected_value(
                preds[f"{kind}_cal"], preds[f"{kind}_price"])
        preds = tracking.assign_tiers(preds)
        preds = locks.freeze_outputs(preds, state, live)
        tier_rates = tracking.tier_hit_rates(history) if len(history) else {}
        done_rows = preds[preds["ml_result"].isin(["win", "loss"])]
        if len(done_rows):
            settled.append(done_rows)
        preds = _add_display_names(preds, league)
        types = set(preds["game_type"].dropna().astype(str))
        label = week_label(league, week, types)
        graded = preds[preds["completed"].astype(bool) & preds["margin"].notna()]
        # every settled game counts; the ledger's `live` flag separates picks
        # that were frozen before kickoff from ones graded after the fact
        countable = graded
        upcoming = preds[~preds["completed"].astype(bool)]
        stages = preds["release_stage"].value_counts().to_dict()
        # a lean is published for every game, so "released" is all of them;
        # what still varies is how many have locked
        released = int(len(preds))
        locked = int(stages.get("locked", 0) + stages.get("started", 0))
        early = int(stages.get("pending", 0))

        ml = tracking.record(countable, "ml")
        ats = tracking.record(countable, "ats")
        if len(countable):
            status_short = f' — {tracking.format_record(ml)}'
            headline = f'{tracking.format_record(ml)} on the moneyline'
            if ats["decided"]:
                headline += f', {tracking.format_record(ats)} against the spread'
            if len(upcoming):
                headline += f' &middot; {len(upcoming)} still to play'
        else:
            status_short = " — leans out"
            if locked == len(preds):
                final = "all final"
            else:
                bits = ([f"{locked} final"] if locked else []) + [f"{released - locked} lean"]
                if early:
                    bits[-1] = f"{released - locked - early} lean"
                    bits.append(f"{early} early")
                final = ", ".join(b for b in bits if not b.startswith("0 "))
            headline = f"All {len(preds)} leans out ({final})"

        public = preds[preds["release_stage"] != "pending"]
        pick = pixel.select(public, cfg.margin_sigma) if len(public) else None
        pick = locks.freeze_ticket(state, locks.ticket_key(season, week, "pixel"),
                                   pick, preds, now, live)
        graded_pick = pixel.grade(pick, graded) if pick is not None else None
        pixel_ids = {leg["game_id"] for leg in pick["legs"]} if pick else set()
        # the board picks up where the headline pick leaves off
        board = (pixel.build_board(public, cfg.margin_sigma, league,
                                   exclude_game_ids=pixel_ids) if len(public) else [])
        board = _freeze_board(board, state, season, week, preds, now, live)
        for slot in board:
            for parlay in slot["parlays"]:
                parlay["graded"] = pixel.grade(parlay, graded)

        ledger = tracking.game_ledger(countable, league, season, week, label)
        ledger += tracking.wager_ledger(graded_pick, league, season, week, label,
                                        "Pixel")
        for slot in board:
            for parlay in slot["parlays"]:
                ledger += tracking.wager_ledger(parlay.get("graded"), league, season,
                                                week, label, "Parlay")

        weeks.append({
            "week": week, "label": label, "season": season, "preds": preds,
            "ledger": ledger, "released": released, "locked": locked,
            "countable": countable, "tier_rates": tier_rates, "reports": reports,
            "games": games, "season_weeks": season_regular_weeks,
            "pixel": pick, "pixel_graded": graded_pick, "pixel_ids": pixel_ids,
            "board": board,
            "graded": graded, "status_short": status_short, "headline": headline,
            "ml": ml, "ats": ats,
            "complete": len(graded) > 0 and len(upcoming) == 0,
        })

    all_graded = (pd.concat([w["countable"] for w in weeks], ignore_index=True)
                  if weeks else pd.DataFrame())
    season_ml = tracking.record(all_graded, "ml") if len(all_graded) else None
    season_ats = tracking.record(all_graded, "ats") if len(all_graded) else None
    if season_ml and season_ml["n"]:
        season_summary = (
            f'{season} season to date: {tracking.format_record(season_ml)} moneyline '
            f'({season_ml["hit_rate"]:.0%}, {season_ml["roi"]:+.1%} ROI)'
        )
        if season_ats and season_ats["decided"]:
            season_summary += (f' &middot; {tracking.format_record(season_ats)} spread '
                               f'({season_ats["hit_rate"]:.0%}, {season_ats["roi"]:+.1%} ROI)')
    else:
        season_summary = f"{season} season &mdash; no completed games yet"

    for w in weeks:
        w["players"] = players
        w["usage"] = usage
        w["roster"] = roster
    if not write_pages:
        return weeks, season_summary, season

    cur_season = int(games["season"].max())
    write_week_pages(league, weeks, season, cur_season, [(season, weeks)])
    return weeks, season_summary, season


def _freeze_board(board: list[dict], state: dict, season: int, week: int,
                  preds: pd.DataFrame, now: pd.Timestamp, live: bool) -> list[dict]:
    """Each board ticket freezes once every leg is locked; a frozen ticket
    replaces whatever this build would have rebuilt in its slot."""
    if not live:
        return board
    by_slot = {slot["slot"]: list(slot["parlays"]) for slot in board}
    slots = pixel.NFL_SLOTS if any(n == "Sunday" for n in by_slot) or not by_slot else pixel.NCAA_SLOTS
    slot_names = [name for name, _, _ in slots]
    for name in set(slot_names) | set(by_slot):
        fresh = by_slot.get(name, [])
        frozen_n = sum(1 for k, v in state.get("tickets", {}).items()
                       if k.startswith(f"{season}-{week}-board-{name}-") and v.get("frozen"))
        out = []
        for i in range(max(len(fresh), frozen_n)):
            ticket = locks.freeze_ticket(state, locks.ticket_key(season, week, "board", name, i),
                                         fresh[i] if i < len(fresh) else None, preds, now, live)
            if ticket:
                out.append(ticket)
        by_slot[name] = out
    ordered = slot_names + [n for n in by_slot if n not in slot_names]
    return [{"slot": name, "parlays": by_slot[name]} for name in ordered if by_slot.get(name)]


def write_week_pages(league: str, weeks: list[dict], season: int, cur_season: int,
                     season_index: list[tuple[int, list[dict]]]) -> None:
    """Render one page per week, with week and season navigation."""
    for w in weeks:
        players = w.get("players")
        usage = w.get("usage")
        ctx = {"tier_rates": w.get("tier_rates"), "reports": w.get("reports"),
               "games": w.get("games"), "roster": w.get("roster")}
        body = [
            _week_nav(league, weeks, w["week"], cur_season,
                      seasons=season_index, season=season),
            f'<h2>{w["label"]} &mdash; {season}</h2>',
            f'<div class="weekhead">{w["headline"]}</div>',
            _roster_banner(league, w.get("roster")),
            _ladder(w, league),
            _schedule_grid(league, w, weeks),
            _pixel_section(w, league, players, w.get("roster")),
            _board_section(w),
            CONTROLS,
            '<div id="games">',
        ]
        for _, row in _by_kick(w["preds"], league).iterrows():
            graded_row = bool(row["completed"]) and pd.notna(row["margin"])
            # Every game on the page carries a lean, including ones more than
            # a week out. What the stage still decides is whether that lean is
            # final: an early one is labelled as provisional and keeps moving
            # until it locks, and only a locked pick is frozen as the pick of
            # record. Nothing about grading changes.
            body.append(_game_card(row, graded=graded_row, league=league,
                                   players=players, usage=usage, ctx=ctx,
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
            if w.get("released"):
                final = f' &middot; {w["locked"]} final' if w.get("locked") else ""
                detail = (f'<span class="upcoming">{w["released"]} of {len(w["preds"])} out'
                          f'{final}</span>')
            else:
                detail = '<span class="meta">Leans release a week out</span>'
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


def _pixel_section(week: dict, league: str, players, roster=None) -> str:
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
        for p in analysis.pixel_rationale(pick, week["preds"], league, roster)
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


def _board_section(week: dict) -> str:
    """The week's confidence parlays, grouped by the day they play."""
    board = week.get("board") or []
    if not board:
        return ""
    groups = []
    for slot in board:
        cards = []
        for i, parlay in enumerate(slot["parlays"], 1):
            graded = parlay.get("graded")
            outcome = ""
            if graded and graded.get("result"):
                tone = {"win": "strong", "loss": "bad", "push": "mid"}[graded["result"]]
                outcome = (f'<span class="outcome t-{tone}">{graded["result"].upper()}'
                           f' &middot; {graded["profit"]:+.2f}u</span>')
            legs = "".join(
                f'<li class="pxleg"><span class="pxdetail">{leg["pick"]}</span> '
                f'<span class="meta">{leg["matchup"]} &middot; '
                f'{pixel.format_american(leg["price"])} &middot; model '
                f'{leg["prob"]:.0%}</span></li>'
                for leg in parlay["legs"]
            )
            cards.append(f"""<div class="card boardcard">
<div class="pxhead"><span class="boardnum">#{i}</span>
<span class="pxprice">{pixel.format_american(parlay["american"])}</span>
<span class="meta">{len(parlay["legs"])} legs &middot; model
{parlay["prob"]:.0%} to hit</span>{outcome}</div>
<ul class="pxlegs">{legs}</ul></div>""")
        groups.append(f'<h3 class="slothead">{slot["slot"]}</h3>{"".join(cards)}')

    return f"""<h2>Parlay board</h2>
<details class="more explainbox"><summary>How the board is built</summary>
<span class="meta">These are the most confident moneylines left after Pixel&rsquo;s Pick
takes its legs, stacked in confidence order until the combined price reaches
<strong>+100 or better</strong>. No game appears twice, so each ticket is genuinely
different rather than a reshuffle of the same games. Short favourites are worth little
on their own; combining them is what turns them into a payout worth the risk. It does
not turn them into an edge &mdash; every leg still carries its own vig, and the
tracker records how these actually land.</span></details>
{"".join(groups)}"""


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


LEAGUE_TITLE = {"nfl": "NFL", "ncaa": "NCAAF"}


def _tracking_page(league: str, weeks_by_season: dict[int, list[dict]]) -> str:
    """One league's results: headline numbers, then every wager, filterable."""
    seasons = sorted(weeks_by_season, reverse=True)
    title = LEAGUE_TITLE[league]

    tiles = []
    for season in seasons:
        weeks = weeks_by_season[season]
        graded = [w["graded"] for w in weeks if len(w["graded"])]
        if not graded:
            continue
        allg = pd.concat(graded, ignore_index=True)
        for kind, name, breakeven in (("ml", "Moneyline", 0.50),
                                      ("ats", "Spread", 0.524)):
            rec = tracking.record(allg, kind)
            if not rec["decided"]:
                continue
            tiles.append(_stat_tile(
                name, f'{rec["wins"]}-{rec["losses"]}',
                grades.hit_tone(rec["hit_rate"], breakeven),
                f'{rec["hit_rate"]:.0%} &middot; {rec["profit"]:+.1f} units '
                f'({rec["roi"]:+.1%} ROI)', league=league, season=season))
        clv = tracking.clv_summary(allg)
        if clv.get("n"):
            rate = clv["beat_rate"]
            tiles.append(_stat_tile(
                "Closing line value", f"{rate:.0%}",
                "strong" if rate >= 0.55 else "good" if rate > 0.5 else "bad",
                f'market moved toward the pick on {clv["n"]} spread picks',
                league=league, season=season))
        for label, kind in (("Pixel&rsquo;s Picks", "pixel"), ("Parlay board", "board")):
            settled = []
            for w in weeks:
                if kind == "pixel" and w.get("pixel_graded"):
                    settled.append(w["pixel_graded"])
                elif kind == "board":
                    for slot in w.get("board") or []:
                        settled += [p.get("graded") for p in slot["parlays"]
                                    if p.get("graded")]
            rec = pixel.record([p for p in settled if p])
            if rec["decided"]:
                tiles.append(_stat_tile(
                    label, f'{rec["wins"]}-{rec["losses"]}',
                    grades.roi_tone(rec["roi"]),
                    f'{rec["hit_rate"]:.0%} &middot; {rec["profit"]:+.1f} units '
                    f'({rec["roi"]:+.1%} ROI)', league=league, season=season))

    settled_seasons = [y for y in seasons
                       if any(len(w.get("countable", w["graded"])) for w in weeks_by_season[y])]
    default = settled_seasons[0] if settled_seasons else (seasons[0] if seasons else None)
    season_opts = "".join(
        f'<option value="{y}"{" selected" if y == default else ""}>{y} season</option>'
        for y in seasons)
    tiles = [t.replace('class="tile scoped"', 'class="tile scoped" hidden')
             if f'data-season="{default}"' not in t else t for t in tiles]
    labels: dict[int, str] = {}
    for yr in seasons:
        for w in weeks_by_season[yr]:
            labels.setdefault(w["week"], w["label"])
    week_opts = "".join(f'<option value="{wk}">{labels[wk]}</option>' for wk in sorted(labels))

    return f"""<h2>{title} tracking</h2>
<div class="controls scopebar">
  <label class="ctl-sort">Season:<select id="scopeseason">{season_opts}</select></label>
</div>
<div class="tiles">{''.join(tiles)}</div>

<h2>Every wager</h2>
<details class="more explainbox"><summary>What is in this table</summary>
<span class="meta">One row per settled bet: the moneyline and spread on every game,
plus each Pixel&rsquo;s Pick and parlay. Filter by season, week, bet type or result,
and sort by any column. Units are profit on a one-unit stake at the price the book
posted, so a winning underdog returns more than a winning favourite.</span></details>
<div class="controls">
  <div class="ctl-group" id="typefilter">
    <button class="chip active" data-f="all">All bets</button>
    <button class="chip" data-f="Pixel">Pixel&rsquo;s Picks</button>
    <button class="chip" data-f="Parlay">Parlays</button>
    <button class="chip" data-f="Moneyline">Moneyline</button>
    <button class="chip" data-f="Spread">Spread</button>
  </div>
  <div class="ctl-group" id="resultfilter">
    <button class="chip active" data-r="all">Any result</button>
    <button class="chip" data-r="win">Wins</button>
    <button class="chip" data-r="loss">Losses</button>
  </div>
</div>
<div class="controls">
  <div class="ctl-group" id="tierfilter">
    <button class="chip active" data-t="all">All tiers</button>
    <button class="chip" data-t="lock">Locks</button>
    <button class="chip" data-t="pick">Picks</button>
    <button class="chip" data-t="lean">Leans</button>
    <button class="chip" data-t="pass">Pass</button>
  </div>
  <div class="ctl-group" id="livefilter">
    <button class="chip active" data-l="all">Backtest + live</button>
    <button class="chip" data-l="live">Live only (published before kickoff)</button>
  </div>
</div>
<div class="controls">
  <label class="ctl-sort">Week:<select id="weekfilter">
    <option value="all">All weeks</option>{week_opts}</select></label>
  <label class="ctl-sort">Sort:<select id="ledgersort">
    <option value="week">Most recent first</option>
    <option value="profit">Biggest win</option>
    <option value="profit_asc">Biggest loss</option>
    <option value="price">Longest price</option>
    <option value="prob">Most confident</option>
  </select></label>
  <span class="meta" id="ledgercount"></span>
</div>
<div class="tablewrap"><table class="track" id="ledger">
<thead><tr><th>Week</th><th>Bet</th><th>Selection</th>
<th class="num">Price</th><th class="num">Model</th><th class="num">Result</th>
<th class="num">Units</th></tr></thead>
<tbody></tbody></table></div>
<div class="emptynote" hidden>No bets match these filters.</div>
<button class="chip" id="loadmore" hidden>Show more</button>
{LEDGER_SCRIPT.replace('__LEAGUE__', league)}"""


LEDGER_SCRIPT = """<script>
(function () {
  var rows = [], view = [], shown = 0, PAGE = 100;
  var state = {season: null, week: 'all', type: 'all', result: 'all', tier: 'all',
               live: 'all', sort: 'week'};
  var tbody = document.querySelector('#ledger tbody');
  var note = document.querySelector('.emptynote');
  var more = document.getElementById('loadmore');
  var count = document.getElementById('ledgercount');

  var LABEL = {'Pixel': "Pixel's Pick", 'Parlay': 'Parlay board'};
  function fmtPrice(p) { return p > 0 ? '+' + p : '' + p; }

  function render(reset) {
    if (reset) { tbody.innerHTML = ''; shown = 0; }
    var slice = view.slice(shown, shown + PAGE);
    var html = slice.map(function (r) {
      var tone = r.result === 'win' ? 't-strong' : (r.result === 'loss' ? 't-bad' : 't-mid');
      var prob = r.prob == null ? '' : Math.round(r.prob * 100) + '%';
      var tier = r.tier ? ' <span class="lgtag">' + r.tier + '</span>' : '';
      var live = r.live ? ' <span class="lgtag live">live</span>' : '';
      return '<tr><td>' + r.week_label + ' <span class="meta">' + r.season + '</span></td>' +
        '<td>' + (LABEL[r.type] || r.type) + tier + live + '</td>' +
        '<td>' + r.pick + ' <span class="meta">' + r.matchup + '</span></td>' +
        '<td class="num">' + fmtPrice(r.price) + '</td>' +
        '<td class="num meta">' + prob + '</td>' +
        '<td class="num ' + tone + '">' + r.result.toUpperCase() + '</td>' +
        '<td class="num ' + (r.profit > 0 ? 't-strong' : (r.profit < 0 ? 't-bad' : 't-mid')) +
        '">' + (r.profit > 0 ? '+' : '') + r.profit.toFixed(2) + 'u</td></tr>';
    }).join('');
    tbody.insertAdjacentHTML('beforeend', html);
    shown += slice.length;
    more.hidden = shown >= view.length;
    note.hidden = view.length !== 0;
    var units = view.reduce(function (a, r) { return a + r.profit; }, 0);
    var w = view.filter(function (r) { return r.result === 'win'; }).length;
    var l = view.filter(function (r) { return r.result === 'loss'; }).length;
    count.innerHTML = view.length + ' bets &middot; ' + w + '-' + l + ' &middot; ' +
      (units > 0 ? '+' : '') + units.toFixed(1) + ' units';
  }

  function apply() {
    view = rows.filter(function (r) {
      return (state.season == null || r.season === state.season)
        && (state.week === 'all' || String(r.week) === state.week)
        && (state.type === 'all' || r.type === state.type)
        && (state.result === 'all' || r.result === state.result)
        && (state.tier === 'all' || r.tier === state.tier)
        && (state.live === 'all' || r.live);
    });
    var s = state.sort;
    view.sort(function (a, b) {
      if (s === 'week') return (b.season - a.season) || (b.week - a.week);
      if (s === 'profit') return b.profit - a.profit;
      if (s === 'profit_asc') return a.profit - b.profit;
      if (s === 'price') return b.price - a.price;
      return (b.prob || 0) - (a.prob || 0);
    });
    render(true);
  }

  function bindChips(id, key) {
    document.querySelectorAll('#' + id + ' .chip').forEach(function (btn) {
      btn.addEventListener('click', function () {
        document.querySelectorAll('#' + id + ' .chip').forEach(function (b) {
          b.classList.remove('active');
        });
        btn.classList.add('active');
        state[key] = btn.dataset.f || btn.dataset.r || btn.dataset.t || btn.dataset.l;
        apply();
      });
    });
  }

  fetch('tracking-__LEAGUE__.json').then(function (r) { return r.json(); })
    .then(function (data) {
      rows = data;
      var sel = document.getElementById('scopeseason');
      state.season = sel ? parseInt(sel.value, 10) : null;
      rebuildWeeks();
      apply();
    })
    .catch(function () { if (count) count.textContent = 'results unavailable'; });

  bindChips('typefilter', 'type');
  bindChips('resultfilter', 'result');
  bindChips('tierfilter', 'tier');
  bindChips('livefilter', 'live');
  function rebuildWeeks() {
    var sel = document.getElementById('weekfilter');
    var seen = {}, opts = [];
    rows.forEach(function (r) {
      if (state.season != null && r.season !== state.season) return;
      if (!seen[r.week]) { seen[r.week] = true; opts.push([r.week, r.week_label]); }
    });
    opts.sort(function (a, b) { return a[0] - b[0]; });
    sel.innerHTML = '<option value="all">All weeks</option>' + opts.map(function (o) {
      return '<option value="' + o[0] + '">' + o[1] + '</option>';
    }).join('');
    state.week = 'all';
  }
  document.getElementById('weekfilter').addEventListener('change', function (e) {
    state.week = e.target.value; apply();
  });
  document.getElementById('ledgersort').addEventListener('change', function (e) {
    state.sort = e.target.value; apply();
  });
  more.addEventListener('click', function () { render(false); });

  var season = document.getElementById('scopeseason');
  if (season) season.addEventListener('change', function (e) {
    state.season = parseInt(e.target.value, 10);
    document.querySelectorAll('.scoped').forEach(function (el) {
      el.hidden = el.dataset.season && el.dataset.season !== e.target.value;
    });
    rebuildWeeks();
    apply();
  });
})();
</script>"""


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
        for tier, label in (("lock", "Locks (85%+ calibrated)"), ("pick", "Picks (70&ndash;85%)"),
                            ("lean", "Leans (55&ndash;70%)"), ("pass", "Pass (under 55%)")):
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
    return f"""<h2>Record by confidence tier</h2>
<details class="more explainbox"><summary>Locks, Picks, Leans and Pass explained</summary>
<span class="meta">Every moneyline is tiered by its <strong>calibrated</strong> chance of
winning &mdash; the model's number after it has been corrected against three seasons of
results and the market's price. <strong>Locks</strong> are 85% or better,
<strong>Picks</strong> 70&ndash;85%, <strong>Leans</strong> 55&ndash;70%, and
<strong>Pass</strong> is anything the model cannot separate from a coin flip. A tier says
how often plays like it have won; it does not say the price is good &mdash; heavy
favourites are priced as such, so Locks win often and pay little. Spread sides are always
leans: they have covered about half the time. <strong>Pixel&rsquo;s Pick</strong> is the
week's headline play at &minus;175 or better.</span></details>
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
<strong>Locks</strong> carry an 85%+ calibrated win chance, <strong>Value</strong>
means the model disagrees with the market by 2.5+ points, <strong>Pick'ems</strong> are near
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
    # (league, week, page) for picks.json — see engine/feed.py
    feed_entries: list[tuple[str, dict, str]] = []
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
        # what has already been published this season, so locked picks stay
        # exactly as they were shown
        state = locks.load_state(out_dir, league, fetch=refresh)
        now = pd.Timestamp.now(tz="UTC")
        for yr in range(first, season + 1):
            seed = (pd.concat(seed_frames, ignore_index=True) if seed_frames else None)
            yr_weeks, yr_summary, _ = build_league_weeks(
                league, refresh=False, season=yr, write_pages=False,
                seed_history=seed, prepared=prepared,
                state=state, now=now, live=(yr == season),
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
        locks.save_state(state, out_dir, league)
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

        # picks.json carries the current week and the one after it. A site
        # reading this feed is looking at a live odds board, and books post
        # next week's games days before this week's are all played — one week
        # alone would leave those games unmatched every Sunday night.
        if current is not None:
            idx = weeks.index(current)
            feed_entries += [
                (league, w, week_slug(league, w["week"], season, season))
                for w in weeks[idx:idx + 2]
            ]

    import json
    for league in ("nfl", "ncaa"):
        by_season = {season: weeks for (lg, season), weeks in league_weeks.items()
                     if lg == league}
        if not by_season:
            continue
        ledger = [row for weeks in by_season.values() for w in weeks
                  for row in w.get("ledger", [])]
        (out_dir / f"tracking-{league}.json").write_text(json.dumps(ledger))
        (out_dir / f"tracking-{league}.html").write_text(
            _page(f"{LEAGUE_TITLE[league]} tracking — Gridiron Engine",
                  _tracking_page(league, by_season))
        )

    # The same week, written for a program rather than a reader: one entry per
    # published game with its pick, tier, calibrated probability and every
    # breakdown paragraph as plain text. perpetualpicks.com reads this to lay
    # the engine's read over its own odds board.
    (out_dir / "picks.json").write_text(
        json.dumps(feed.build(feed_entries), ensure_ascii=False))

    cards = "\n".join(
        f'<a class="card" href="{week_slug(league, cur["week"]) if cur else f"{league}.html"}">'
        f'<div class="teams">{league.upper()}</div>'
        f'<div class="meta">{(cur["label"] + " &middot; " + cur["headline"]) if cur else "no games yet"}</div>'
        f'<div class="meta">{summary}</div></a>'
        for league, summary, cur in summaries
    )
    index_body = f"""
<h2>This week</h2>
<div class="leagues">{cards}</div>
<h2>How to read the picks</h2>
<div class="card"><div class="meta">
Every moneyline is tiered by its <strong>calibrated</strong> chance of winning:
<strong>Locks</strong> (85%+, win about 93% of the time and pay little),
<strong>Picks</strong> (70&ndash;85%), <strong>Leans</strong> (55&ndash;70%) and
<strong>Pass</strong> (coin flips, no play). <strong>Pixel&rsquo;s Pick</strong> is the
week's headline play at &minus;175 or better, and the <strong>parlay board</strong>
stacks the most confident moneylines to plus money. Every game on a week's page
carries a lean, however far out it is; a lean more than a week from kickoff is marked
<strong>early</strong>, because no injury report exists for it yet. A pick locks once
the final injury report and the kickoff forecast are in, no later than two hours before
the game, and never changes after that. The tracking pages grade every
one of them at the price the book actually posted.
</div></div>
<h2>How it works</h2>
<div class="card"><div class="meta">
Team power ratings and pass/rush unit ratings are solved by weighted ridge regression
over recent seasons with recency decay, using only games completed before kickoff.
A walk-forward blend converts rating differentials, unit matchups, quarterback status,
injuries, weather, rest and home field into a projected margin and win probability;
a second, walk-forward calibration corrects that probability against three seasons of
results and the market's price. Data: nflverse, sportsdataverse, ESPN's scoreboard for
live odds, Open-Meteo for forecasts &mdash; all free and keyless.
</div></div>"""
    (out_dir / "index.html").write_text(_page("Gridiron Engine", index_body))
    (out_dir / ".nojekyll").write_text("")
    return out_dir
