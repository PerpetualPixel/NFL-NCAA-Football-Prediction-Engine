"""Live odds from ESPN's public scoreboard, which needs no key.

The archived line files are exactly that — archives. The college one had
not published a single 2026 price by the second week of the season, and
the NFL schedule feed carries one number per game that moves only when its
maintainers rerun their scraper. A pick that claims to be final needs the
price the book is actually showing, so this pulls the current spread and
moneylines from ESPN's scoreboard for the games about to be played and
lays them over the archived numbers.

The scoreboard also reports the opening line where ESPN has one, which is
what makes an honest "the line moved" sentence possible for the NFL.

Everything here is best-effort: if ESPN is unreachable or its shape
changes, the build carries on with the archived prices and says so.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

from .ingest import DATA_DIR

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/{sport}/scoreboard"
SPORT = {"nfl": "nfl", "ncaa": "college-football"}
TIMEOUT = 20
CACHE_DIR = DATA_DIR / "espn_odds"
CACHE_HOURS = 1.0     # scoreboard odds are refetched at most this often

ODDS_COLS = ["game_id", "spread_line", "home_moneyline", "away_moneyline",
             "home_spread_odds", "away_spread_odds", "total_line",
             "open_spread_line", "odds_provider", "odds_as_of"]


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN guard


def parse_event(event: dict) -> dict | None:
    """One scoreboard event -> odds row in the schedule feeds' convention
    (spread_line positive when the home team is favoured)."""
    comps = event.get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    odds_list = comp.get("odds") or []
    if not odds_list:
        return None
    # ESPN lists its own book first; prefer the entry with the lowest priority
    odds = sorted(odds_list, key=lambda o: (o.get("provider") or {}).get("priority", 99))[0]

    teams = {}
    for side in comp.get("competitors") or []:
        teams[side.get("homeAway")] = side
    home, away = teams.get("home") or {}, teams.get("away") or {}
    home_abbr = ((home.get("team") or {}).get("abbreviation") or "").upper()
    away_abbr = ((away.get("team") or {}).get("abbreviation") or "").upper()

    spread_line = None
    # "KC -3.5" names the favourite; that is the most robust reading
    details = odds.get("details")
    if isinstance(details, str) and details.strip():
        parts = details.strip().split()
        if len(parts) >= 2:
            fav = parts[0].upper()
            pts = _num(parts[-1])
            if pts is not None:
                pts = abs(pts)
                if fav == home_abbr:
                    spread_line = pts
                elif fav == away_abbr:
                    spread_line = -pts
        elif details.strip().upper() in {"EVEN", "PK", "PICK"}:
            spread_line = 0.0
    if spread_line is None:
        raw = _num(odds.get("spread"))
        if raw is not None:
            spread_line = -raw  # ESPN's spread is from the home side, negative = favoured

    home_odds = odds.get("homeTeamOdds") or {}
    away_odds = odds.get("awayTeamOdds") or {}

    def price(block, key):
        value = _num(block.get(key))
        if value is None:
            current = block.get("current") or {}
            inner = current.get(key) if isinstance(current, dict) else None
            if isinstance(inner, dict):
                value = _num(inner.get("american") or inner.get("value"))
            else:
                value = _num(inner)
        return value if value is not None and abs(value) >= 100 else None

    open_spread = None
    opened = odds.get("open") or {}
    if isinstance(opened, dict):
        pl = (opened.get("pointSpread") or {})
        if isinstance(pl, dict):
            # ESPN's open block quotes the line for each side; the away line
            # is the home spread negated
            val = _num(((pl.get("home") or {}).get("open") or {}).get("value")
                       if isinstance(pl.get("home"), dict) else None)
            if val is None:
                val = _num(pl.get("home") if not isinstance(pl.get("home"), dict) else None)
            if val is not None:
                open_spread = -val
        if open_spread is None:
            raw_open = _num((opened.get("spread") if not isinstance(opened.get("spread"), dict)
                             else None))
            if raw_open is not None:
                open_spread = -raw_open

    return {
        "game_id": str(event.get("id")),
        "spread_line": spread_line,
        "home_moneyline": price(home_odds, "moneyLine"),
        "away_moneyline": price(away_odds, "moneyLine"),
        "home_spread_odds": price(home_odds, "spreadOdds"),
        "away_spread_odds": price(away_odds, "spreadOdds"),
        "total_line": _num(odds.get("overUnder")),
        "open_spread_line": open_spread,
        "odds_provider": (odds.get("provider") or {}).get("name"),
    }


def parse_scoreboard(payload: dict) -> list[dict]:
    rows = []
    for event in payload.get("events") or []:
        row = parse_event(event)
        if row and (row["spread_line"] is not None or row["home_moneyline"] is not None):
            rows.append(row)
    return rows


def _fetch(sport: str, params: dict) -> dict | None:
    try:
        resp = requests.get(SCOREBOARD.format(sport=sport), params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError):
        return None


def _cache_file(league: str) -> Path:
    return CACHE_DIR / f"{league}.json"


def fetch_live_odds(league: str, kickoffs: pd.Series, now: pd.Timestamp | None = None,
                    refresh: bool = True) -> pd.DataFrame:
    """Current odds for every game kicking off in the next eight days.

    `kickoffs` is a Series of UTC kickoff timestamps indexed by game id in
    the schedule feed's id space (which for both leagues is ESPN's event
    id — nflverse carries it in the `espn` column, cfbfastR uses it
    outright). Results are cached for an hour so repeated builds do not
    hammer the endpoint."""
    now = now or pd.Timestamp.now(tz="UTC")
    cache = _cache_file(league)
    if cache.exists():
        try:
            saved = json.loads(cache.read_text())
            age = (now - pd.to_datetime(saved["as_of"], utc=True)).total_seconds() / 3600
            if not refresh or age <= CACHE_HOURS:
                return _frame(saved["rows"], saved["as_of"])
        except (OSError, ValueError, KeyError):
            pass
    if not refresh:
        return _frame([], None)

    kicks = pd.to_datetime(kickoffs, utc=True, errors="coerce").dropna()
    window = kicks[(kicks >= now - pd.Timedelta(hours=6)) & (kicks <= now + pd.Timedelta(days=8))]
    if window.empty:
        return _frame([], None)
    # one scoreboard call per calendar day the slate touches (ESPN keys the
    # scoreboard by date); college needs groups=80 for all of FBS
    days = sorted({k.strftime("%Y%m%d") for k in window})
    rows: list[dict] = []
    for day in days:
        params = {"dates": day, "limit": 500}
        if league == "ncaa":
            params["groups"] = 80
        payload = _fetch(SPORT[league], params)
        if payload is None:
            continue
        rows.extend(parse_scoreboard(payload))
        time.sleep(0.3)
    as_of = now.isoformat()
    if rows:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"as_of": as_of, "rows": rows}))
        except OSError:
            pass
    return _frame(rows, as_of if rows else None)


def _frame(rows: list[dict], as_of: str | None) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=[c for c in ODDS_COLS if c != "odds_as_of"])
    df["odds_as_of"] = as_of
    if not df.empty:
        df = df.drop_duplicates("game_id", keep="last")
    return df


def overlay(games: pd.DataFrame, live: pd.DataFrame, id_col: str = "game_id") -> pd.DataFrame:
    """Lay live prices over the archived ones, game by game.

    Only fields the live feed actually has replace the archive, so a game
    ESPN prices without a moneyline keeps its archived moneyline. Rows that
    received anything carry `odds_source` naming the book and the time."""
    df = games.copy()
    if "odds_source" not in df.columns:
        df["odds_source"] = None
    if live is None or live.empty or id_col not in df.columns:
        return df
    key = df[id_col].astype(str)
    live = live.set_index(live["game_id"].astype(str))
    hit = key.isin(live.index)
    if not hit.any():
        return df
    for col in ("spread_line", "home_moneyline", "away_moneyline",
                "home_spread_odds", "away_spread_odds", "total_line", "open_spread_line"):
        if col not in live.columns:
            continue
        if col not in df.columns:
            df[col] = pd.NA
        incoming = key.map(live[col])
        take = hit & incoming.notna()
        df.loc[take, col] = incoming[take].astype(float)
    provider = key.map(live["odds_provider"]).fillna("ESPN")
    stamp = key.map(live["odds_as_of"])
    df.loc[hit, "odds_source"] = provider[hit] + " · " + stamp[hit].astype(str).str.slice(0, 16)
    return df
