"""Kickoff weather forecasts, from Open-Meteo's free, keyless API.

The schedule feeds only record temperature and wind after a game has been
played, so for an upcoming game the model was assuming a calm 60°F day. A
forecast replaces that guess with the conditions the game will actually be
played in, and — because a pick is not final until the weather is known —
the moment a forecast lands is one of the things that lets a pick lock.

Everything here degrades gracefully: no network, a dead API, or a venue
without coordinates simply means no forecast, never a failed build.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import requests

from .data.ingest import DATA_DIR

TEAM_INFO_YEARS = (2026, 2025, 2024, 2023)

API = "https://api.open-meteo.com/v1/forecast"
HORIZON_DAYS = 14          # Open-Meteo forecasts 16 days out; the last two are noise
REFRESH_HOURS = 6          # how stale a cached forecast may be before it is refetched
TIMEOUT = 12
MAX_FAILURES = 4           # stop calling the API for this build after this many misses
CACHE_DIR = DATA_DIR / "weather"

EASTERN = "US/Eastern"

# Home venues by the schedule feed's team code: latitude, longitude, roof.
# The feed's own roof column is used when present; this fills its gaps
# (it is blank for several 2026 games) and covers the international venues,
# which are keyed by stadium name below.
NFL_VENUES: dict[str, tuple[float, float, str]] = {
    "ARI": (33.5276, -112.2626, "retractable"),
    "ATL": (33.7554, -84.4010, "retractable"),
    "BAL": (39.2780, -76.6227, "outdoors"),
    "BUF": (42.7738, -78.7870, "outdoors"),
    "CAR": (35.2258, -80.8528, "outdoors"),
    "CHI": (41.8623, -87.6167, "outdoors"),
    "CIN": (39.0954, -84.5160, "outdoors"),
    "CLE": (41.5061, -81.6995, "outdoors"),
    "DAL": (32.7473, -97.0945, "retractable"),
    "DEN": (39.7439, -105.0201, "outdoors"),
    "DET": (42.3400, -83.0456, "dome"),
    "GB": (44.5013, -88.0622, "outdoors"),
    "HOU": (29.6847, -95.4107, "retractable"),
    "IND": (39.7601, -86.1639, "retractable"),
    "JAX": (30.3239, -81.6373, "outdoors"),
    "KC": (39.0489, -94.4839, "outdoors"),
    "LA": (33.9535, -118.3392, "dome"),
    "LAC": (33.9535, -118.3392, "dome"),
    "LV": (36.0909, -115.1833, "dome"),
    "MIA": (25.9580, -80.2389, "outdoors"),
    "MIN": (44.9736, -93.2575, "dome"),
    "NE": (42.0909, -71.2643, "outdoors"),
    "NO": (29.9511, -90.0812, "dome"),
    "NYG": (40.8135, -74.0745, "outdoors"),
    "NYJ": (40.8135, -74.0745, "outdoors"),
    "PHI": (39.9008, -75.1675, "outdoors"),
    "PIT": (40.4468, -80.0158, "outdoors"),
    "SEA": (47.5952, -122.3316, "outdoors"),
    "SF": (37.4033, -121.9694, "outdoors"),
    "TB": (27.9759, -82.5033, "outdoors"),
    "TEN": (36.1665, -86.7713, "outdoors"),
    "WAS": (38.9076, -76.8645, "outdoors"),
}

INTERNATIONAL_VENUES: dict[str, tuple[float, float, str]] = {
    "Tottenham Hotspur Stadium": (51.6043, -0.0664, "outdoors"),
    "Wembley Stadium": (51.5560, -0.2795, "outdoors"),
    "FC Bayern Munich Stadium": (48.2188, 11.6247, "outdoors"),
    "Allianz Arena": (48.2188, 11.6247, "outdoors"),
    "Deutsche Bank Park": (50.0686, 8.6455, "outdoors"),
    "Olympiastadion Berlin": (52.5147, 13.2395, "outdoors"),
    "Estadio Banorte": (19.3029, -99.1505, "outdoors"),
    "Estadio Azteca": (19.3029, -99.1505, "outdoors"),
    "Bernabeu": (40.4531, -3.6883, "retractable"),
    "Santiago Bernabeu": (40.4531, -3.6883, "retractable"),
    "Maracana Stadium": (-22.9121, -43.2302, "outdoors"),
    "Arena Corinthians": (-23.5453, -46.4742, "outdoors"),
    "Melbourne Cricket Ground": (-37.8200, 144.9834, "outdoors"),
    "Stade de France": (48.9244, 2.3601, "outdoors"),
    "Croke Park": (53.3607, -6.2512, "outdoors"),
    "Aviva Stadium": (53.3352, -6.2285, "outdoors"),
}

INDOOR_ROOFS = {"dome", "closed"}


def kickoff_utc(row, league: str) -> pd.Timestamp:
    """The real kickoff instant.

    The NFL feed stores the local game date at midnight and the Eastern
    kickoff time in a separate column; the college feed stores one UTC
    timestamp. Reading the NFL date on its own puts kickoff at 8 PM Eastern
    the evening *before* the game, which is 17 hours early for a 1 PM slot.
    """
    day = pd.to_datetime(_get(row, "gameday"), errors="coerce")
    if pd.isna(day):
        return pd.NaT
    if league != "nfl":
        return day if day.tzinfo else day.tz_localize("UTC")
    gametime = _get(row, "gametime")
    hour, minute = 13, 0
    if isinstance(gametime, str) and ":" in gametime:
        try:
            hour, minute = (int(p) for p in gametime.split(":")[:2])
        except ValueError:
            pass
    local = pd.Timestamp(day.year, day.month, day.day, hour, minute)
    try:
        return local.tz_localize(EASTERN).tz_convert("UTC")
    except Exception:
        # no timezone database: treat Eastern as UTC-4, close enough for
        # release timing
        return (local + pd.Timedelta(hours=4)).tz_localize("UTC")


def _get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    if hasattr(row, "get"):
        return row.get(key, default)
    return getattr(row, key, default)


def venue_for(row, league: str, ncaa_venues: dict | None = None) -> tuple[float, float, str] | None:
    """(lat, lon, roof) for where this game is played, or None if unknown."""
    if league == "nfl":
        stadium = _get(row, "stadium")
        if isinstance(stadium, str) and stadium in INTERNATIONAL_VENUES:
            return INTERNATIONAL_VENUES[stadium]
        home = _get(row, "home_team")
        venue = NFL_VENUES.get(home) if isinstance(home, str) else None
        if venue is None:
            return None
        roof = _get(row, "roof")
        if isinstance(roof, str) and roof.strip():
            roof = roof.strip().lower()
            roof = "retractable" if roof in {"open", "closed"} and venue[2] == "retractable" else roof
            return (venue[0], venue[1], roof)
        return venue
    if not ncaa_venues:
        return None
    if int(_get(row, "neutral", 0) or 0):
        return None
    return ncaa_venues.get(_get(row, "home_team"))


def ncaa_venue_table() -> dict[str, tuple[float, float, str]]:
    """Home venue coordinates for every FBS school, from the team file."""
    try:
        from .teams import CFB_TEAM_INFO_URL, _cached_parquet
    except ImportError:  # pragma: no cover
        return {}
    for year in TEAM_INFO_YEARS:
        dest = DATA_DIR / "ncaa" / f"team_info_{year}.parquet"
        try:
            df = _cached_parquet(CFB_TEAM_INFO_URL.format(year=year), dest)
        except Exception:
            continue
        if "latitude" not in df.columns:
            continue
        out = {}
        for r in df.itertuples():
            if pd.isna(r.latitude) or pd.isna(r.longitude):
                continue
            dome = bool(getattr(r, "dome", False)) if pd.notna(getattr(r, "dome", None)) else False
            out[r.school] = (float(r.latitude), float(r.longitude),
                             "dome" if dome else "outdoors")
        return out
    return {}


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _cache_path(league: str, game_id) -> Path:
    return CACHE_DIR / f"{league}_{game_id}.json"


def _read_cache(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _fresh(entry: dict | None, now: pd.Timestamp) -> bool:
    if not entry or "fetched_at" not in entry:
        return False
    fetched = pd.to_datetime(entry["fetched_at"], utc=True, errors="coerce")
    return pd.notna(fetched) and (now - fetched) <= pd.Timedelta(hours=REFRESH_HOURS)


def parse_forecast(payload: dict, kick: pd.Timestamp) -> dict | None:
    """Pull the kickoff hour (and the two after it) out of an hourly payload.

    A game lasts about three hours, so conditions are averaged across the
    kickoff hour and the next two rather than read off a single instant."""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return None
    stamps = pd.to_datetime(pd.Series(times), utc=True, errors="coerce")
    diffs = (stamps - kick).dt.total_seconds()
    # first hour at or before kickoff, then the two that follow
    at_or_before = diffs[diffs <= 0]
    start = int(at_or_before.index[-1]) if len(at_or_before) else int(diffs.abs().idxmin())
    idx = [i for i in range(start, start + 3) if i < len(times)]
    if not idx:
        return None

    def take(key, how="mean"):
        vals = hourly.get(key)
        if not vals:
            return None
        picked = [vals[i] for i in idx if i < len(vals) and vals[i] is not None]
        if not picked:
            return None
        return float(max(picked) if how == "max" else sum(picked) / len(picked))

    return {
        "temp": take("temperature_2m"),
        "wind": take("wind_speed_10m"),
        "gust": take("wind_gusts_10m", "max"),
        "precip_prob": take("precipitation_probability", "max"),
        "precip": take("precipitation"),
    }


class _Fetcher:
    """One session per build, with a circuit breaker so a dead API costs a
    few seconds rather than a timeout per game."""

    def __init__(self):
        self.session = requests.Session()
        self.failures = 0

    def fetch(self, lat: float, lon: float, kick: pd.Timestamp) -> dict | None:
        if self.failures >= MAX_FAILURES:
            return None
        day = kick.strftime("%Y-%m-%d")
        end = (kick + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        params = {
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
            "hourly": "temperature_2m,wind_speed_10m,wind_gusts_10m,"
                      "precipitation_probability,precipitation",
            "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
            "precipitation_unit": "inch", "timezone": "UTC",
            "start_date": day, "end_date": end,
        }
        try:
            resp = self.session.get(API, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError):
            self.failures += 1
            return None
        parsed = parse_forecast(payload, kick)
        if parsed is None:
            self.failures += 1
        return parsed


FORECAST_COLS = ["fc_temp", "fc_wind", "fc_gust", "fc_precip_prob", "fc_precip",
                 "forecast_at", "indoors_venue"]


def attach_forecasts(games: pd.DataFrame, league: str, refresh: bool = True,
                     now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Add forecast columns for upcoming games inside the horizon.

    Completed games keep the feed's recorded conditions. Indoor venues are
    marked rather than fetched. Anything without a venue, outside the
    horizon, or unreachable simply stays empty."""
    df = games.copy()
    for col in FORECAST_COLS:
        if col == "forecast_at":
            df[col] = pd.Series([None] * len(df), index=df.index, dtype=object)
        elif col == "indoors_venue":
            df[col] = False
        else:
            df[col] = float("nan")
    if df.empty:
        return df
    now = now or pd.Timestamp.now(tz="UTC")
    ncaa_venues = ncaa_venue_table() if league != "nfl" else None
    fetcher = _Fetcher() if refresh else None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    upcoming = df[~df["completed"].astype(bool)]
    for i, row in upcoming.iterrows():
        kick = kickoff_utc(row, league)
        if pd.isna(kick):
            continue
        hours_out = (kick - now).total_seconds() / 3600.0
        if hours_out < -4 or hours_out > HORIZON_DAYS * 24:
            continue
        venue = venue_for(row, league, ncaa_venues)
        if venue is None:
            continue
        lat, lon, roof = venue
        if roof in INDOOR_ROOFS:
            df.at[i, "indoors_venue"] = True
            df.at[i, "forecast_at"] = now.isoformat()
            continue
        path = _cache_path(league, row["game_id"])
        entry = _read_cache(path)
        if not _fresh(entry, now) and fetcher is not None:
            parsed = fetcher.fetch(lat, lon, kick)
            if parsed is not None:
                entry = {**parsed, "fetched_at": now.isoformat(), "kick": kick.isoformat()}
                try:
                    path.write_text(json.dumps(entry))
                except OSError:
                    pass
        if not entry:
            continue
        df.at[i, "fc_temp"] = entry.get("temp")
        df.at[i, "fc_wind"] = entry.get("wind")
        df.at[i, "fc_gust"] = entry.get("gust")
        df.at[i, "fc_precip_prob"] = entry.get("precip_prob")
        df.at[i, "fc_precip"] = entry.get("precip")
        df.at[i, "forecast_at"] = entry.get("fetched_at")

    # the model's weather features read temp/wind; give upcoming games the
    # forecast where the feed has nothing yet
    for col in ("fc_temp", "fc_wind", "fc_gust", "fc_precip_prob", "fc_precip"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    for src, dst in (("fc_temp", "temp"), ("fc_wind", "wind")):
        if dst not in df.columns:
            df[dst] = float("nan")
        df[dst] = pd.to_numeric(df[dst], errors="coerce").astype(float)
        df[dst] = df[dst].where(df[dst].notna(), df[src])
    return df


def describe(row) -> str:
    """One plain sentence on the forecast, for the breakdown."""
    if bool(_get(row, "indoors_venue", False)):
        return "Played indoors, so weather is not a factor."
    temp, wind = _get(row, "fc_temp"), _get(row, "fc_wind")
    if temp is None or pd.isna(temp):
        return ""
    bits = [f"forecast around {temp:.0f}°F at kickoff"]
    if wind is not None and pd.notna(wind):
        gust = _get(row, "fc_gust")
        gust_txt = f" (gusts to {gust:.0f})" if gust is not None and pd.notna(gust) and gust >= wind + 5 else ""
        bits.append(f"wind {wind:.0f} mph{gust_txt}")
    pp = _get(row, "fc_precip_prob")
    if pp is not None and pd.notna(pp) and pp >= 30:
        bits.append(f"{pp:.0f}% chance of precipitation")
    text = ", ".join(bits)
    if wind is not None and pd.notna(wind) and wind >= 15:
        text += " — enough wind to lean on the run and shorten the passing game"
    elif temp is not None and temp <= 32:
        text += " — freezing conditions favour the ground game and the kicking edge"
    elif pp is not None and pd.notna(pp) and pp >= 60:
        text += " — a wet ball favours the side that can run and protect it"
    return text[0].upper() + text[1:] + "."
