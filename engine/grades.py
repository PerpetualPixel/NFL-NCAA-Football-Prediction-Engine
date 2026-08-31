"""Turning model numbers into things a reader can actually judge.

An EPA rating of +0.074 tells you nothing unless you already know the scale.
A rank ("4th of 32") and a letter grade do, and a color says good or bad at a
glance. Everything user-facing goes through here so the whole site speaks one
language.
"""
from __future__ import annotations

# Grade bands by percentile of the league (1.0 = best team, 0.0 = worst).
GRADE_BANDS = [
    (0.93, "A+"), (0.85, "A"), (0.75, "A-"),
    (0.66, "B+"), (0.58, "B"), (0.50, "B-"),
    (0.42, "C+"), (0.34, "C"), (0.25, "C-"),
    (0.16, "D+"), (0.08, "D"), (0.0, "F"),
]

# Plain-English tiers, used in prose instead of raw numbers.
TIERS = [
    (0.90, "elite"), (0.75, "very good"), (0.60, "solid"),
    (0.40, "average"), (0.25, "weak"), (0.0, "a real weakness"),
]


def percentile(rank: int, n_teams: int) -> float:
    """Convert 1-is-best rank to a 0-1 percentile where 1.0 is best."""
    if not n_teams or n_teams < 2:
        return 0.5
    return 1.0 - (rank - 1) / (n_teams - 1)


def grade(pct: float) -> str:
    for cutoff, letter in GRADE_BANDS:
        if pct >= cutoff:
            return letter
    return "F"


def tier(pct: float) -> str:
    for cutoff, word in TIERS:
        if pct >= cutoff:
            return word
    return "a real weakness"


def tone(pct: float) -> str:
    """CSS class for color coding: strong / good / mid / poor / bad."""
    if pct >= 0.80:
        return "strong"
    if pct >= 0.60:
        return "good"
    if pct >= 0.40:
        return "mid"
    if pct >= 0.20:
        return "poor"
    return "bad"


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def rank_label(rank: int, n_teams: int) -> str:
    return f"{ordinal(rank)} of {n_teams}"


# --- edge sizing ---------------------------------------------------------
# Net EPA per play differences, translated into words a reader can weigh.

def edge_word(net_epa: float) -> tuple[str, str]:
    """(description, tone) for a unit-vs-unit net EPA difference."""
    mag = abs(net_epa)
    if mag >= 0.15:
        return "a major mismatch", "strong"
    if mag >= 0.09:
        return "a clear edge", "good"
    if mag >= 0.05:
        return "a slight edge", "mid"
    return "essentially even", "mid"


def result_tone(result: str | None) -> str:
    return {"win": "strong", "loss": "bad", "push": "mid"}.get(result or "", "mid")


def roi_tone(roi: float) -> str:
    if roi >= 0.05:
        return "strong"
    if roi > 0:
        return "good"
    if roi > -0.05:
        return "mid"
    return "bad"


def hit_tone(rate: float, breakeven: float = 0.524) -> str:
    """Colour a hit rate against the rate that actually breaks even."""
    if rate >= breakeven + 0.03:
        return "strong"
    if rate >= breakeven:
        return "good"
    if rate >= breakeven - 0.04:
        return "mid"
    return "bad"
