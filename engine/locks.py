"""Release stages, and freezing a pick once it is final.

A pick goes through three public states:

* **pending** — more than a week out. Nothing is published; a pick made now
  could not know who is hurt or what the weather will be.
* **lean** — inside a week. The model's current read, explicitly provisional,
  refreshed on every build as prices, injuries and forecasts change.
* **locked** — final. Reached as soon as the things a pick waits for are in
  (the final injury report and a kickoff forecast) or, failing that, two
  hours before kickoff. From this point the pick never changes.

"Never changes" has to be enforced, not just labelled. Every build re-runs
the model on fresh data, so without a record of what was published a
"locked" number could quietly drift between builds and the tracker would
grade a pick nobody ever saw. So the first locked version of each game is
written to a state file that ships with the site, every later build reads
it back — from the live site, so a fresh CI checkout still remembers — and
the frozen values override the model's for that game.

Games that reach kickoff without ever being locked (the scheduler skipped
the window) keep their last published lean as the pick of record, clearly
marked. A game first published after it kicked off is shown but never
counted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import requests

from .weather import kickoff_utc

LEAN_LEAD_HOURS = 24 * 7      # leans appear one week out
LOCK_LEAD_HOURS = 2           # final no later than two hours before kickoff
# the injury report is final about two days before a Sunday game; a lock
# on news is allowed from this far out
NEWS_LOCK_MAX_HOURS = 60
# college has no injury feed, so its news lock is the day-before forecast
NCAA_NEWS_LOCK_MAX_HOURS = 30
# share of a week's teams that must have filed game-status designations
# before the week's final reports count as in
WEEK_REPORT_SHARE = 0.6

SITE_URL = os.environ.get(
    "SITE_URL", "https://perpetualpixel.github.io/NFL-NCAA-Football-Prediction-Engine/")

FROZEN_INPUTS = ["pred_margin", "home_win_prob", "spread_line",
                 "home_moneyline", "away_moneyline", "home_spread_odds",
                 "away_spread_odds", "open_spread_line", "odds_source"]
FROZEN_OUTPUTS = ["ml_cal", "ats_cal", "ml_ev", "ats_ev", "ml_tier", "ats_tier"]
STAGE_ORDER = {"pending": 0, "lean": 1, "locked": 2, "started": 2}


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

def state_path(site_dir: Path, league: str) -> Path:
    return site_dir / f"locks-{league}.json"


def load_state(site_dir: Path, league: str, fetch: bool = True) -> dict:
    """The record of everything published so far, newest source first: the
    live site (which a fresh checkout does not have), then the local copy."""
    candidates = []
    if fetch:
        try:
            resp = requests.get(SITE_URL.rstrip("/") + f"/locks-{league}.json", timeout=15)
            if resp.ok:
                candidates.append(resp.json())
        except (requests.RequestException, ValueError):
            pass
    local = state_path(site_dir, league)
    if local.exists():
        try:
            candidates.append(json.loads(local.read_text()))
        except (OSError, ValueError):
            pass
    state = {"games": {}, "tickets": {}}
    # merge so a frozen game in either copy stays frozen
    for cand in reversed(candidates):
        for key in ("games", "tickets"):
            for k, v in (cand.get(key) or {}).items():
                cur = state[key].get(k)
                if cur is None or STAGE_ORDER.get(v.get("stage"), 0) >= STAGE_ORDER.get(cur.get("stage"), 0):
                    state[key][k] = v
    return state


def save_state(state: dict, site_dir: Path, league: str) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    state_path(site_dir, league).write_text(json.dumps(state, default=_json_default))


def _json_default(value):
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _clean(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


# ---------------------------------------------------------------------------
# What "the news is in" means
# ---------------------------------------------------------------------------

def nfl_final_reports(reports: pd.DataFrame | None, season: int, week: int) -> tuple[set, bool]:
    """Teams whose final injury report (with game-status designations) has
    been filed for this week, and whether the week's reports are in for
    everyone. Wednesday and Thursday reports list practice participation
    only; the game status column is populated on the final report."""
    if reports is None or reports.empty:
        return set(), False
    wk = reports[(reports["season"] == season) & (reports["week"] == week)]
    if wk.empty:
        return set(), False
    designated = set(wk.loc[wk["report_status"].notna(), "team"].dropna())
    all_teams = set(wk["team"].dropna())
    week_in = bool(all_teams) and len(designated) / len(all_teams) >= WEEK_REPORT_SHARE
    return designated, week_in


def news_in(row, league: str, hours: float, designated: set, week_in: bool) -> tuple[bool, str]:
    """(is the news in, what is still missing) for one game."""
    weather_known = bool(_clean(row.get("indoors_venue")) or _clean(row.get("forecast_at")))
    if league == "nfl":
        both = row["home_team"] in designated and row["away_team"] in designated
        injuries_known = both or (week_in and hours <= NEWS_LOCK_MAX_HOURS)
        if hours > NEWS_LOCK_MAX_HOURS:
            return False, "final injury report"
        if not injuries_known:
            return False, "final injury report"
        if not weather_known and hours > 24:
            return False, "kickoff forecast"
        return True, ""
    if hours > NCAA_NEWS_LOCK_MAX_HOURS:
        return False, "day-before forecast"
    if not weather_known and hours > 6:
        return False, "kickoff forecast"
    return True, ""


# ---------------------------------------------------------------------------
# Applying stages and freezing
# ---------------------------------------------------------------------------

def stage_games(preds: pd.DataFrame, league: str, state: dict, now: pd.Timestamp,
                reports: pd.DataFrame | None, live: bool) -> pd.DataFrame:
    """Decide each game's stage and freeze what needs freezing.

    Adds: release_stage, locked_at, lock_reason, waiting_on, kickoff, hours_out,
    frozen (bool), post_kick (bool). For frozen games the FROZEN_INPUTS
    columns are overwritten with the values of record. Mutates `state`.
    """
    df = preds.copy()
    df["kickoff"] = [kickoff_utc(r, league) for _, r in df.iterrows()]
    df["hours_out"] = [((k - now).total_seconds() / 3600.0) if pd.notna(k) else float("nan")
                       for k in df["kickoff"]]
    df["release_stage"] = "locked"
    df["locked_at"] = None
    df["lock_reason"] = None
    df["waiting_on"] = None
    df["frozen"] = False
    df["post_kick"] = False
    if df.empty:
        return df

    if not live:
        # archive seasons: everything is settled, nothing to freeze
        return df

    season, week = int(df["season"].iloc[0]), int(df["week"].iloc[0])
    designated, week_in = nfl_final_reports(reports, season, week) if league == "nfl" else (set(), False)
    games = state.setdefault("games", {})

    for i, row in df.iterrows():
        gid = str(row["game_id"])
        prev = games.get(gid)
        hours = row["hours_out"]
        done = bool(row.get("completed")) and pd.notna(row.get("margin"))

        if prev and prev.get("stage") in ("locked", "started"):
            _apply_frozen(df, i, prev)
            df.at[i, "release_stage"] = prev["stage"]
            df.at[i, "locked_at"] = prev.get("as_of")
            df.at[i, "lock_reason"] = prev.get("reason")
            df.at[i, "frozen"] = True
            df.at[i, "post_kick"] = bool(prev.get("post_kick", False))
            continue

        if done or (pd.notna(hours) and hours < 0):
            # kicked off without ever being locked
            if prev and prev.get("stage") == "lean":
                _apply_frozen(df, i, prev)
                record = {**prev, "stage": "started", "reason": "last lean before kickoff",
                          "post_kick": False}
            else:
                record = _snapshot(row, now, "started", "first published after kickoff",
                                   post_kick=True)
            games[gid] = record
            df.at[i, "release_stage"] = "started"
            df.at[i, "locked_at"] = record.get("as_of")
            df.at[i, "lock_reason"] = record.get("reason")
            df.at[i, "frozen"] = True
            df.at[i, "post_kick"] = bool(record.get("post_kick", False))
            continue

        if pd.isna(hours):
            df.at[i, "release_stage"] = "lean"
            continue

        ready, missing = news_in(row, league, hours, designated, week_in)
        if hours <= LOCK_LEAD_HOURS or ready:
            reason = ("inside two hours of kickoff" if hours <= LOCK_LEAD_HOURS
                      else ("final injury report and kickoff forecast in" if league == "nfl"
                            else "day-before forecast in"))
            record = _snapshot(row, now, "locked", reason)
            games[gid] = record
            df.at[i, "release_stage"] = "locked"
            df.at[i, "locked_at"] = record["as_of"]
            df.at[i, "lock_reason"] = reason
            df.at[i, "frozen"] = True
        elif hours <= LEAN_LEAD_HOURS:
            games[gid] = _snapshot(row, now, "lean", "provisional")
            df.at[i, "release_stage"] = "lean"
            df.at[i, "waiting_on"] = missing
        else:
            df.at[i, "release_stage"] = "pending"
    return df


def freeze_outputs(preds: pd.DataFrame, state: dict, live: bool) -> pd.DataFrame:
    """After calibration and tiering: overwrite the calibrated numbers for
    frozen games with the values of record, and record them for games that
    have just been frozen."""
    df = preds.copy()
    if not live or df.empty:
        return df
    games = state.setdefault("games", {})
    for i, row in df.iterrows():
        gid = str(row["game_id"])
        rec = games.get(gid)
        if not rec:
            continue
        if rec.get("stage") in ("locked", "started") and rec.get("outputs"):
            for col, val in rec["outputs"].items():
                if col in df.columns:
                    df.at[i, col] = val
        else:
            rec["outputs"] = {c: _clean(row.get(c)) for c in FROZEN_OUTPUTS if c in df.columns}
    return df


def _snapshot(row, now: pd.Timestamp, stage: str, reason: str, post_kick: bool = False) -> dict:
    return {
        "stage": stage, "as_of": now.isoformat(), "reason": reason, "post_kick": post_kick,
        "inputs": {c: _clean(row.get(c)) for c in FROZEN_INPUTS if c in row.index},
    }


def _apply_frozen(df: pd.DataFrame, i, record: dict) -> None:
    for col, val in (record.get("inputs") or {}).items():
        if col in df.columns and val is not None:
            df.at[i, col] = val


# ---------------------------------------------------------------------------
# Tickets (Pixel's Pick and the parlay board)
# ---------------------------------------------------------------------------

def ticket_key(season: int, week: int, kind: str, slot: str = "", index: int = 0) -> str:
    return f"{season}-{week}-{kind}-{slot}-{index}"


def freeze_ticket(state: dict, key: str, ticket: dict | None, preds: pd.DataFrame,
                  now: pd.Timestamp, live: bool) -> dict | None:
    """Return the ticket of record for this slot.

    A ticket is frozen once every leg's game is locked (or has started), so
    each leg carries a frozen price. Before that it is rebuilt each build
    from the current legs; afterwards the frozen version is used whatever
    the model now thinks."""
    if not live:
        return ticket
    tickets = state.setdefault("tickets", {})
    prev = tickets.get(key)
    if prev and prev.get("frozen"):
        return prev.get("ticket")
    if ticket is None:
        return None
    stages = dict(zip(preds["game_id"].astype(str), preds["release_stage"]))
    legs_final = all(stages.get(str(leg["game_id"])) in ("locked", "started")
                     for leg in ticket["legs"])
    if legs_final:
        tickets[key] = {"frozen": True, "as_of": now.isoformat(), "ticket": _plain(ticket)}
        ticket = {**ticket, "locked_at": now.isoformat()}
    return ticket


def _plain(ticket: dict) -> dict:
    return json.loads(json.dumps(ticket, default=_json_default))
