"""Checks for picks.json — the machine-readable feed other sites read.

The week pages can be redesigned freely; this feed is a contract, so what it
promises (plain text, no markup; only released games; the market numbers a
pick was priced against) is asserted here rather than left to inspection.
"""
from __future__ import annotations

import pandas as pd

from engine import feed


def _row(**over) -> pd.Series:
    """One prediction row with every column the analysis writers read."""
    base = {
        "game_id": "2026_01_NE_SEA",
        "home_team": "Seattle Seahawks", "away_team": "New England Patriots",
        "home_key": "SEA", "away_key": "NE",
        "season": 2026, "week": 1, "game_type": "REG",
        "neutral": False, "completed": False, "margin": float("nan"),
        "kickoff": pd.Timestamp("2026-09-07T20:05:00Z"),
        "release_stage": "lean", "waiting_on": "final injury report",
        "locked_at": None, "lock_reason": None, "post_kick": False,
        "pred_margin": 4.4, "home_win_prob": 0.63,
        "home_rating": 3.2, "away_rating": -1.1, "hfa_fit": 1.8,
        "spread_line": 3.5, "total_line": 44.5,
        "home_moneyline": -185.0, "away_moneyline": 154.0,
        "home_spread_odds": -105.0, "away_spread_odds": -115.0,
        "odds_source": "ESPN",
        "ml_pick": "Seattle Seahawks", "ml_prob": 0.63, "ml_cal": 0.6057,
        "ml_price": -185.0, "ml_tier": "lean", "ml_ev": -0.0669,
        "ats_pick": "Seattle Seahawks", "ats_line": -3.5, "ats_edge": 0.9,
        "ats_prob": 0.5271, "ats_cal": 0.476, "ats_price": -105.0,
        "ats_price_assumed": False, "ats_tier": "lean", "ats_ev": -0.0707,
        "wind": float("nan"), "indoors": False,
    }
    base.update(over)
    return pd.Series(base)


def _week(rows: list[pd.Series], **over) -> dict:
    week = {
        "week": 1, "label": "Week 1", "season": 2026,
        "preds": pd.DataFrame([dict(r) for r in rows]),
        "headline": "13 of 16 leans out; the rest release one week before kickoff",
        "released": 13, "locked": 0,
        "tier_rates": {"lock": {"wins": 940, "losses": 60, "decided": 1000,
                                "hit_rate": 0.94, "roi": -0.004}},
        "reports": None, "games": None, "players": None, "pixel": None,
    }
    week.update(over)
    return week


def _build(rows, **week_over) -> dict:
    return feed.build([("nfl", _week(rows, **week_over), "nfl-w01.html")],
                      now=pd.Timestamp("2026-09-06T18:00:00Z"))


def test_plain_strips_markup_and_entities():
    assert feed.plain("<strong>Seattle</strong> by 4 &mdash; a lean") == "Seattle by 4 — a lean"
    assert feed.plain(None) == ""
    assert feed.plain(float("nan")) == ""


def test_a_published_game_carries_both_picks_and_the_market_it_was_priced_against():
    game = _build([_row()])["games"][0]

    assert game["home"] == "Seattle Seahawks"
    assert game["away_key"] == "NE"
    assert game["sport_key"] == "americanfootball_nfl"
    assert game["kickoff"] == "2026-09-07T20:05:00Z"
    assert game["url"].endswith("nfl-w01.html#g-2026_01_NE_SEA")

    assert game["moneyline"]["selection"] == "Seattle Seahawks"
    assert game["moneyline"]["tier"] == "lean"
    assert game["moneyline"]["tier_label"] == "Lean"
    # the tier keys off the calibrated number, so that is what `prob` is
    assert game["moneyline"]["prob"] == 0.6057
    assert game["moneyline"]["model_prob"] == 0.63
    assert game["moneyline"]["price"] == -185.0

    assert game["spread"]["point"] == -3.5
    assert game["spread"]["price_assumed"] is False
    assert game["market"]["spread_line"] == 3.5
    assert game["market"]["source"] == "ESPN"


def test_agreement_says_how_far_the_model_sits_from_the_price():
    game = _build([_row()])["games"][0]
    gap = game["moneyline"]["agreement"]
    # -185 implies about 65%; the model's calibrated 60.6% is below it
    assert gap["implied_prob"] > gap["model_prob"]
    assert gap["gap"] < 0
    assert round(gap["model_prob"] - gap["implied_prob"], 4) == gap["gap"]


def test_a_moneyline_the_book_never_posted_is_not_published_as_a_price():
    game = _build([_row(ml_price=float("nan"))])["games"][0]
    assert game["moneyline"]["price"] is None
    assert game["moneyline"]["agreement"] is None
    # the pick itself still stands; it just is not graded as a bet
    assert game["moneyline"]["selection"] == "Seattle Seahawks"


def test_projected_score_splits_the_margin_across_the_market_total():
    game = _build([_row()])["games"][0]
    score = game["model"]["projected_score"]
    assert score == {"home": 24.4, "away": 20.1, "total": 44.5}
    # the two scores are the market's total, split by the projected margin
    assert round(score["home"] + score["away"], 1) == 44.5
    assert round(score["home"] - score["away"], 1) == 4.3   # 4.4, each side rounded

    # no market total, no scoreline — the margin alone cannot make one
    bare = _build([_row(total_line=float("nan"))])["games"][0]
    assert bare["model"]["projected_score"] is None


def test_only_released_games_still_to_play_are_published():
    rows = [
        _row(game_id="released"),
        _row(game_id="pending", release_stage="pending"),
        _row(game_id="played", completed=True, margin=7.0),
    ]
    ids = [g["game_id"] for g in _build(rows)["games"]]
    assert ids == ["released"]


def test_a_locked_pick_says_so_and_carries_its_reason():
    game = _build([_row(release_stage="locked", lock_reason="final injury report in",
                        locked_at=pd.Timestamp("2026-09-07T14:00:00Z"))])["games"][0]
    assert game["stage"] == "locked"
    assert game["locked"] is True
    assert game["lock_reason"] == "final injury report in"
    assert game["locked_at"] == "2026-09-07T14:00:00Z"


def test_analysis_is_plain_text_throughout():
    game = _build([_row()])["games"][0]
    blocks = game["analysis"]
    assert blocks["script"], "the game script always has something to say"
    text = " ".join(blocks["script"] + blocks["confidence"])
    assert "<" not in text and "&mdash;" not in text
    # this fixture passes no player usage, and a section with nothing to say
    # stays empty rather than being filled with placeholder prose
    assert blocks["players"] == []
    # an availability section with no feed behind it says exactly that, on the
    # card and in the feed alike — a silent empty list would read as "nobody
    # is hurt", which is a different claim
    assert [i["text"] for i in blocks["injuries"]] == [
        "no availability data published for this team."] * 2


def test_the_league_header_names_the_week_and_its_measured_tier_rates():
    built = _build([_row()])
    nfl = built["leagues"]["nfl"]
    assert nfl["sport_key"] == "americanfootball_nfl"
    assert nfl["season"] == 2026 and nfl["week"] == 1
    assert nfl["url"].endswith("nfl-w01.html")
    assert nfl["tier_rates"]["lock"]["hit_rate"] == 0.94
    assert nfl["pixel"] is None      # no headline play in this fixture


def test_the_headline_play_travels_with_its_legs_and_its_case():
    pick = {
        "legs": [{"game_id": "2026_01_NE_SEA", "kind": "ml", "pick": "Seattle Seahawks",
                  "price": -185.0, "decimal": 1.5405, "prob": 0.63,
                  "detail": "<strong>Seattle</strong> to win",
                  "matchup": "New England Patriots @ Seattle Seahawks",
                  "home_team": "Seattle Seahawks", "away_team": "New England Patriots",
                  "ev": -0.03}],
        "decimal": 1.5405, "american": -185.0, "prob": 0.63, "ev": -0.0296,
        "is_parlay": False, "fair_priced": False,
    }
    nfl = _build([_row()], pixel=pick)["leagues"]["nfl"]
    assert nfl["pixel"]["american"] == -185
    assert nfl["pixel"]["is_parlay"] is False
    assert nfl["pixel"]["legs"][0]["selection"] == "Seattle Seahawks"
    assert nfl["pixel"]["legs"][0]["detail"] == "Seattle to win"
    assert isinstance(nfl["pixel"]["rationale"], list)


def test_the_feed_is_ordered_by_kickoff_and_stamped():
    rows = [
        _row(game_id="sunday", kickoff=pd.Timestamp("2026-09-13T17:00:00Z")),
        _row(game_id="thursday", kickoff=pd.Timestamp("2026-09-10T00:20:00Z")),
    ]
    built = _build(rows)
    assert [g["game_id"] for g in built["games"]] == ["thursday", "sunday"]
    assert built["generated_at"] == "2026-09-06T18:00:00Z"
    assert built["feed_version"] == feed.FEED_VERSION
    # the honesty note ships with every build, not just the README
    assert "does not beat the closing line" in built["disclosure"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
