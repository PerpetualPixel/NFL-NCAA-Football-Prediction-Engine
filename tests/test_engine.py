"""Offline checks for the parts of the engine that decide what gets published.

Runs with plain python (``python -m tests.test_engine``) or pytest. Nothing
here touches the network: fixtures stand in for the feeds.
"""
from __future__ import annotations

import pandas as pd

from engine import analysis, locks, odds, pipeline, rosters, site, tracking, weather
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


# ---------------------------------------------------------------------------
# Rosters: nobody is named who is not on the team, and nobody who is out is
# presented as the man to watch
# ---------------------------------------------------------------------------

def _roster_view(as_of=None) -> rosters.RosterView:
    """Two clubs, covering every case the prose has to handle."""
    view = rosters.RosterView(
        league="nfl", season=2026, week=1,
        as_of=as_of or pd.Timestamp.now(tz="UTC"), roster_week=1,
    )
    people = [
        # (id, name, team, position, availability, reason)
        ("p1", "Patrick Mahomes", "KC", "QB", rosters.AVAILABLE, None),
        ("p2", "Rashee Rice", "KC", "WR", rosters.AVAILABLE, None),
        ("p3", "Travis Kelce", "KC", "TE", rosters.OUT, "on injured reserve"),
        ("p4", "Kenneth Walker III", "KC", "RB", rosters.AVAILABLE, None),
        ("p5", "Kyler Murray", "MIN", "QB", rosters.AVAILABLE, None),
    ]
    for i in range(20):  # enough bodies that the club counts as covered
        people.append((f"f{i}", f"Filler {i}", "KC", "G", rosters.AVAILABLE, None))
        people.append((f"m{i}", f"Other {i}", "MIN", "G", rosters.AVAILABLE, None))
    for pid, name, team, pos, avail, reason in people:
        rosters._index(view, rosters.PlayerStatus(
            name=name, team=team, availability=avail, player_id=pid,
            position=pos, reason=reason))
    return view


def test_a_player_who_changed_teams_is_not_named_for_the_old_one():
    view = _roster_view()
    gone = view.lookup("ARI", "p5", "K.Murray")
    assert gone.availability == rosters.OFF_ROSTER or not view.covers("ARI")
    # ARI has no roster here, so nothing is claimed either way; the real case
    # is a covered team that no longer lists him
    moved = view.lookup("KC", "p5", "K.Murray")
    assert moved.availability == rosters.OFF_ROSTER
    assert moved.now_with == "MIN" and "now with MIN" in moved.label()


def test_an_unknown_player_on_a_covered_team_is_off_the_roster():
    view = _roster_view()
    assert view.lookup("KC", "nobody", "A.Ghost").availability == rosters.OFF_ROSTER


def test_nothing_is_claimed_about_a_team_with_no_roster():
    view = _roster_view()
    verdict = view.lookup("SEA", "p1", "P.Mahomes")
    assert verdict.availability == rosters.UNKNOWN and not verdict.verified


def test_the_playbyplay_name_form_resolves_to_the_roster_name():
    view = _roster_view()
    # no id, only the feed's "R.Rice" abbreviation
    found = view.lookup("KC", None, "R.Rice")
    assert found.name == "Rashee Rice" and found.availability == rosters.AVAILABLE
    assert rosters.normalize_name("Ken Walker III") == "ken walker"
    assert rosters.abbreviated_key("Rashee Rice") == "r rice"


def test_annotate_drops_the_departed_and_marks_the_unavailable():
    view = _roster_view()
    usage = pd.DataFrame([
        {"team": "KC", "player_id": "p2", "player": "R.Rice", "share": 0.3, "role": "REC"},
        {"team": "KC", "player_id": "p3", "player": "T.Kelce", "share": 0.25, "role": "REC"},
        {"team": "KC", "player_id": "p5", "player": "K.Murray", "share": 0.2, "role": "REC"},
    ])
    out = view.annotate(usage)
    assert list(out["player"]) == ["Rashee Rice", "Travis Kelce"]   # Murray dropped
    assert out.loc[out["player"] == "Travis Kelce", "availability"].iloc[0] == rosters.OUT
    assert out.loc[out["player"] == "Travis Kelce", "roster_note"].iloc[0] == "on injured reserve"
    assert list(out["position"]) == ["WR", "TE"]


def test_a_player_on_injured_reserve_is_not_the_man_to_watch():
    view = _roster_view()
    usage = view.annotate(pd.DataFrame([
        {"team": "KC", "player_id": "p3", "player": "T.Kelce", "share": 0.31,
         "role": "REC", "rank": 1, "targets_pg": 9.0, "rec_yards_pg": 80.0},
        {"team": "KC", "player_id": "p2", "player": "R.Rice", "share": 0.24,
         "role": "REC", "rank": 2, "targets_pg": 7.0, "rec_yards_pg": 66.0},
    ]))
    usage["stats_are_current"] = True
    usage["stat_season"] = 2026
    row = pd.Series({"home_team": "KC", "away_team": "MIN", "home_key": "KC",
                     "away_key": "MIN", "n_teams": 32})
    text = " ".join(p for r in analysis.usage_report(row, usage, view)
                    for p in r["paragraphs"])
    assert "Expect Rashee Rice to see the ball early and often." in text
    assert "Expect Travis Kelce" not in text
    assert "Not available: <strong>Travis Kelce</strong> (on injured reserve)" in text


def test_last_seasons_usage_is_written_in_the_past_tense():
    view = _roster_view()
    usage = view.annotate(pd.DataFrame([
        {"team": "KC", "player_id": "p2", "player": "R.Rice", "share": 0.24,
         "role": "REC", "rank": 1, "targets_pg": 7.0, "rec_yards_pg": 66.0},
    ]))
    usage["stats_are_current"] = False
    usage["stat_season"] = 2025
    row = pd.Series({"home_team": "KC", "away_team": "MIN", "home_key": "KC",
                     "away_key": "MIN", "n_teams": 32})
    report = analysis.usage_report(row, usage, view)[0]
    assert "commanded 24% of the targets" in report["paragraphs"][0]
    assert "Usage is 2025 production" in report["note"]
    assert "roster verified" in report["note"]


def test_the_page_says_when_the_roster_could_not_be_checked():
    usage = pd.DataFrame([
        {"team": "KC", "player_id": "p2", "player": "R.Rice", "share": 0.24,
         "role": "REC", "rank": 1, "targets_pg": 7.0, "rec_yards_pg": 66.0,
         "stats_are_current": False, "stat_season": 2025},
    ])
    row = pd.Series({"home_team": "KC", "away_team": "MIN", "home_key": "KC",
                     "away_key": "MIN", "n_teams": 32})
    note = analysis.usage_report(row, usage, None)[0]["note"]
    assert "roster could not be verified" in note


def test_the_reserve_list_is_reported_when_no_injury_report_exists_yet():
    view = _roster_view()
    row = pd.Series({"home_team": "KC", "away_team": "MIN", "home_key": "KC",
                     "away_key": "MIN", "season": 2026, "week": 1})
    lines = dict(analysis.injury_report(row, None, "nfl", view))
    assert "Travis Kelce" in lines["KC"] and "injured reserve" in lines["KC"]
    assert lines["MIN"] == "nobody ruled out and no game-status designations."


def test_a_stale_roster_keeps_a_pick_provisional():
    now = pd.Timestamp("2026-09-11T20:00:00Z")
    preds = _preds(41, now).assign(forecast_at="2026-09-11T18:00:00+00:00")
    reports = pd.DataFrame({"season": [2026] * 4, "week": [1] * 4,
                            "team": ["SEA", "NE", "KC", "DET"],
                            "report_status": ["Out", "Questionable", None, "Out"]})
    stale = _roster_view(as_of=pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=5))
    staged = locks.stage_games(preds, "nfl", {"games": {}, "tickets": {}}, now,
                               reports, live=True, roster=stale)
    assert staged["release_stage"].iloc[0] == "lean"
    assert staged["waiting_on"].iloc[0] == "a current roster"
    # with a roster that was read this build, the same game locks
    fresh = locks.stage_games(preds, "nfl", {"games": {}, "tickets": {}}, now,
                              reports, live=True, roster=_roster_view())
    assert fresh["release_stage"].iloc[0] == "locked"


def test_reserve_lists_count_toward_the_injury_burden():
    """A star on injured reserve never appears on a game-status report, so
    without the roster the model saw a fully healthy team."""
    weekly = pd.DataFrame([
        {"season": 2026, "week": 1, "team": "KC", "full_name": "Travis Kelce",
         "position": "TE", "depth_chart_position": "TE", "status": "RES"},
        {"season": 2026, "week": 1, "team": "KC", "full_name": "Spare Part",
         "position": "G", "depth_chart_position": "G", "status": "ACT"},
    ])
    empty_reports = pd.DataFrame(columns=["season", "week", "team", "full_name",
                                          "position", "report_status"])
    snaps = pd.DataFrame(columns=["season", "week", "team", "player",
                                  "offense_pct", "defense_pct"])
    burden = pipeline.nfl_injury_burden(empty_reports, snaps, weekly)
    assert len(burden) == 1 and burden["team"].iloc[0] == "KC"
    assert burden["inj_burden"].iloc[0] > 0
    # a game-day inactive is published ninety minutes before kickoff, long
    # after the pick, so it must not leak into the feature
    inactive = weekly.assign(status=["INA", "ACT"])
    assert pipeline.nfl_injury_burden(empty_reports, snaps, inactive).empty


def test_injury_burden_counts_a_doubly_listed_player_once():
    weekly = pd.DataFrame([
        {"season": 2026, "week": 1, "team": "KC", "full_name": "Travis Kelce",
         "position": "TE", "depth_chart_position": "TE", "status": "RES"},
    ])
    reports = pd.DataFrame([
        {"season": 2026, "week": 1, "team": "KC", "full_name": "Travis Kelce",
         "position": "TE", "report_status": "Out"},
    ])
    snaps = pd.DataFrame(columns=["season", "week", "team", "player",
                                  "offense_pct", "defense_pct"])
    both = pipeline.nfl_injury_burden(reports, snaps, weekly)
    only = pipeline.nfl_injury_burden(reports, snaps, None)
    assert both["inj_burden"].iloc[0] == only["inj_burden"].iloc[0]


def _season_games(season: int, rounds: int) -> pd.DataFrame:
    """A four-team season with `rounds` completed games each."""
    rows = []
    for r in range(rounds):
        rows += [{"season": season, "home_team": "A", "away_team": "B", "completed": True},
                 {"season": season, "home_team": "C", "away_team": "D", "completed": True}]
    return pd.DataFrame(rows)


def test_production_is_split_from_the_season_being_played():
    plays = pd.DataFrame({"season": [2025] * 6000 + [2026] * 6000})
    # one week in: plenty of plays, but nowhere near enough games to describe
    # how a team shares the ball, so the numbers stay labelled as last year's
    games = pd.concat([_season_games(2025, 17), _season_games(2026, 1)])
    assert pipeline._player_season(plays, games) == (2025, 2026)
    # four weeks in it describes its own players
    games = pd.concat([_season_games(2025, 17), _season_games(2026, 4)])
    assert pipeline._player_season(plays, games) == (2026, 2026)
    # and before a ball is kicked there is nothing but last season
    games = pd.concat([_season_games(2025, 17),
                       pd.DataFrame([{"season": 2026, "home_team": "A",
                                      "away_team": "B", "completed": False}])])
    assert pipeline._player_season(plays, games) == (2025, 2026)


# ---------------------------------------------------------------------------
# The card: one unmissable pick, a lean on every game, and tags that explain
# themselves
# ---------------------------------------------------------------------------

def _card(tier="lean", prob=0.62, price=-140.0, stage="lean", **over):
    row = pd.Series({
        "game_id": "x1", "home_team": "Kansas City Chiefs",
        "away_team": "Arizona Cardinals", "home_key": "KC", "away_key": "ARI",
        "season": 2026, "week": 1, "n_teams": 32, "pred_margin": 3.2,
        "home_win_prob": prob, "neutral": False, "hfa_fit": 1.8,
        "home_rating": 4.0, "away_rating": -2.0, "spread_line": 0.5,
        "completed": False, "margin": float("nan"), "ml_tier": tier,
        "ml_pick": "Kansas City Chiefs", "ml_cal": prob, "ml_price": price,
        "ats_pick": "Arizona Cardinals", "ats_line": -0.5, "ats_edge": 2.7,
        "release_stage": stage, "game_type": "REG",
        "gameday": pd.Timestamp("2026-09-13"), "gametime": "13:00", **over,
    })
    return site._game_card(row, graded=False, league="nfl", ctx={})


def test_the_pick_is_the_loudest_thing_on_the_card():
    html = _card(tier="pick", prob=0.76, price=-260.0)
    assert 'class="pickhero t-pick"' in html
    assert '<div class="pickteam">Kansas City Chiefs</div>' in html
    # the tier badge sits with the pick, not adrift in the chip row
    hero = html.split('class="pickhero')[1].split("</div></div>")[0]
    assert "Moneyline pick" in hero and 'tag-pick' in hero


def test_a_pass_never_reads_as_a_recommendation():
    html = _card(tier="pass", prob=0.52, price=110.0)
    assert 'class="pickhero t-pass"' in html
    assert "no play" in html
    assert "not counted as a pick" in html


def test_every_game_carries_a_lean_however_far_out():
    """A game more than a week from kickoff used to publish nothing at all."""
    html = _card(stage="pending")
    assert 'class="pickhero' in html and "Kansas City Chiefs" in html
    # ...and it says how much weight to put on a lean made before any
    # injury report exists
    assert "Early lean" in html


def test_the_stage_still_says_whether_a_pick_is_final():
    assert "Early lean" in _card(stage="pending")
    assert "not final" in _card(stage="lean")
    assert "locked" in _card(stage="locked", locked_at="2026-09-13T10:00:00+00:00")


def test_every_tag_explains_itself_on_hover():
    html = _card(tier="lock", prob=0.9, price=-450.0)
    chips = html.split('<div class="chips">')[1].split("</div>")[0]
    # no chip ships without a bubble
    for chip in chips.split("<span")[1:]:
        assert "data-tip=" in chip, chip
    # and the tier badge in the hero has one too
    assert 'class="tag tag-lock" data-tip=' in html


def test_near_three_says_what_it_means():
    tip = site._key_number_tip(3)
    assert "within a point of 3" in tip and "field goal" in tip
    assert "touchdown" in site._key_number_tip(7)
    html = _card()
    assert "near 3" in html and 'data-tip="The projected margin is within a point of 3' in html


def test_tier_filters_still_work_after_the_badge_moved():
    """The tier chip moved into the hero, but the card's data-tags — which is
    what the filter buttons read — must still carry it."""
    html = _card(tier="lock", prob=0.9, price=-450.0)
    tags = html.split('data-tags="')[1].split('"')[0].split()
    assert "lock" in tags


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
