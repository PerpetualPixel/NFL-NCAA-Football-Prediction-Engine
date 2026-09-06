"""Offline checks for the parts of the engine that decide what gets published.

Runs with plain python (``python -m tests.test_engine``) or pytest. Nothing
here touches the network: fixtures stand in for the feeds.
"""
from __future__ import annotations

import pandas as pd

from engine import locks, odds, tracking, weather
from engine.data import espn_odds


def test_nfl_kickoff_combines_date_and_eastern_time():
    row = {"gameday": pd.Timestamp("2026-09-13"), "gametime": "13:00"}
    assert weather.kickoff_utc(row, "nfl") == pd.Timestamp("2026-09-13T17:00:00Z")
    # a bare date is not midnight UTC (which would be 8 PM the evening before)
    assert weather.kickoff_utc({"gameday": pd.Timestamp("2026-09-13"), "gametime": None},
                               "nfl").hour == 17


def test_ncaa_kickoff_is_the_feed_timestamp():
    assert weather.kickoff_utc({"gameday": "2026-09-12T19:30:00.000Z"}, "ncaa") == \
        pd.Timestamp("2026-09-12T19:30:00Z")


def test_forecast_parse_averages_the_game_window():
    times = [f"2026-09-13T{h:02d}:00" for h in range(24)]
    payload = {"hourly": {"time": times, "temperature_2m": list(range(24)),
                          "wind_speed_10m": list(range(24)),
                          "precipitation_probability": [h * 4 for h in range(24)],
                          "precipitation": [0.0] * 24}}
    parsed = weather.parse_forecast(payload, pd.Timestamp("2026-09-13T17:00:00Z"))
    assert parsed["temp"] == 18.0            # hours 17, 18, 19
    assert parsed["precip_prob"] == 76.0     # max over the window


def test_espn_odds_sign_convention():
    event = {"id": "1", "competitions": [{
        "competitors": [{"homeAway": "home", "team": {"abbreviation": "SEA"}},
                        {"homeAway": "away", "team": {"abbreviation": "NE"}}],
        "odds": [{"provider": {"name": "ESPN BET", "priority": 1}, "details": "SEA -3.5",
                  "overUnder": 44.5, "spread": -3.5,
                  "homeTeamOdds": {"moneyLine": -180}, "awayTeamOdds": {"moneyLine": 150}}]}]}
    row = espn_odds.parse_event(event)
    assert row["spread_line"] == 3.5 and row["home_moneyline"] == -180
    away_fav = {"id": "2", "competitions": [{
        "competitors": [{"homeAway": "home", "team": {"abbreviation": "CAR"}},
                        {"homeAway": "away", "team": {"abbreviation": "CHI"}}],
        "odds": [{"details": "CHI -2.5", "spread": 2.5}]}]}
    assert espn_odds.parse_event(away_fav)["spread_line"] == -2.5


def test_overlay_only_replaces_what_the_live_feed_has():
    live = espn_odds._frame([{"game_id": "9", "spread_line": -2.5, "home_moneyline": 118.0,
                              "away_moneyline": None, "home_spread_odds": None,
                              "away_spread_odds": None, "total_line": None,
                              "open_spread_line": None, "odds_provider": "X"}], "2026-09-06T16:00")
    games = pd.DataFrame({"game_id": ["9", "z"], "spread_line": [1.0, 4.0],
                          "home_moneyline": [-150.0, -200.0], "away_moneyline": [130.0, 170.0]})
    out = espn_odds.overlay(games, live)
    assert out.loc[0, "spread_line"] == -2.5 and out.loc[0, "home_moneyline"] == 118.0
    assert out.loc[0, "away_moneyline"] == 130.0          # kept: live had none
    assert out.loc[1, "spread_line"] == 4.0 and out.loc[1, "odds_source"] is None


def _preds(hours_out: float, now: pd.Timestamp) -> pd.DataFrame:
    kick = now + pd.Timedelta(hours=hours_out)
    return pd.DataFrame([{
        "game_id": "g1", "season": 2026, "week": 1, "home_team": "SEA", "away_team": "NE",
        "gameday": kick.tz_convert("US/Eastern").strftime("%Y-%m-%d"),
        "gametime": kick.tz_convert("US/Eastern").strftime("%H:%M"),
        "completed": False, "margin": float("nan"), "pred_margin": 3.0, "home_win_prob": 0.6,
        "spread_line": 2.5, "home_moneyline": -150.0, "away_moneyline": 130.0,
        "home_spread_odds": -110.0, "away_spread_odds": -110.0, "open_spread_line": 2.0,
        "odds_source": None, "indoors_venue": False, "forecast_at": None,
    }])


def test_release_stages_and_freezing():
    now = pd.Timestamp("2026-09-06T16:00:00Z")
    state = {"games": {}, "tickets": {}}
    pending = locks.stage_games(_preds(24 * 9, now), "nfl", state, now, None, live=True)
    assert pending["release_stage"].iloc[0] == "pending"

    lean = locks.stage_games(_preds(24 * 3, now), "nfl", state, now, None, live=True)
    assert lean["release_stage"].iloc[0] == "lean" and not lean["frozen"].iloc[0]
    assert state["games"]["g1"]["stage"] == "lean"

    locked = locks.stage_games(_preds(1.5, now), "nfl", state, now, None, live=True)
    assert locked["release_stage"].iloc[0] == "locked" and locked["frozen"].iloc[0]
    assert state["games"]["g1"]["inputs"]["pred_margin"] == 3.0

    # a later build with a different model number keeps the frozen one
    later = _preds(1.0, now).assign(pred_margin=-7.0)
    again = locks.stage_games(later, "nfl", state, now, None, live=True)
    assert again["pred_margin"].iloc[0] == 3.0 and again["release_stage"].iloc[0] == "locked"


def test_news_lock_needs_the_final_report_and_a_forecast():
    now = pd.Timestamp("2026-09-11T20:00:00Z")   # Friday evening
    preds = _preds(41, now).assign(forecast_at="2026-09-11T18:00:00+00:00")
    reports = pd.DataFrame({"season": [2026] * 4, "week": [1] * 4,
                            "team": ["SEA", "NE", "KC", "DET"],
                            "report_status": ["Out", "Questionable", None, "Out"]})
    state = {"games": {}, "tickets": {}}
    staged = locks.stage_games(preds, "nfl", state, now, reports, live=True)
    assert staged["release_stage"].iloc[0] == "locked"
    assert "injury report" in staged["lock_reason"].iloc[0]
    # without the reports it stays a lean, waiting on them
    staged2 = locks.stage_games(preds, "nfl", {"games": {}, "tickets": {}}, now, None, live=True)
    assert staged2["release_stage"].iloc[0] == "lean"
    assert staged2["waiting_on"].iloc[0] == "final injury report"


def test_post_kickoff_first_publication_is_not_counted():
    now = pd.Timestamp("2026-09-06T16:00:00Z")
    state = {"games": {}, "tickets": {}}
    started = locks.stage_games(_preds(-2, now), "nfl", state, now, None, live=True)
    assert started["release_stage"].iloc[0] == "started" and started["post_kick"].iloc[0]


def test_tiers_follow_calibrated_probability():
    assert tracking.tier_for(0.9) == "lock"
    assert tracking.tier_for(0.72) == "pick"
    assert tracking.tier_for(0.6) == "lean"
    assert tracking.tier_for(0.5) == "pass"


def test_unpriced_moneyline_is_not_graded():
    preds = pd.DataFrame([{
        "home_team": "A", "away_team": "B", "pred_margin": 5.0, "home_win_prob": 0.65,
        "completed": True, "margin": 7.0, "spread_line": 3.0,
        "home_moneyline": float("nan"), "away_moneyline": float("nan"),
        "home_spread_odds": float("nan"), "away_spread_odds": float("nan"),
    }])
    graded = tracking.grade(preds, margin_sigma=13.0)
    assert graded["ml_result"].iloc[0] is None
    assert graded["ats_result"].iloc[0] == "win" and graded["ats_price_assumed"].iloc[0]


def test_market_line_names_the_market_favourite():
    assert odds.format_market("Home", "Away", -5.5) == "Away -5.5"
    assert odds.format_market("Home", "Away", 3.0) == "Home -3"
    # upset = the model wants points on the market underdog
    assert "upset" in odds.classify(0.55, 3.0, spread_line=-4.0)
    assert "upset" not in odds.classify(0.55, 3.0, spread_line=4.0)


if __name__ == "__main__":
    import sys
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print("FAIL", name, "->", repr(exc))
    sys.exit(1 if failures else 0)
