"""Pre-game breakdown reports.

For a given week, writes a markdown report with each game's pick, projected
margin, win probability, power ratings, and (NFL) the unit-vs-unit matchup
table that explains where the edge comes from.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports"


def _matchup_lines(row: pd.Series) -> list[str]:
    lines = []
    if "home_off_pass_epa" not in row.index or pd.isna(row.get("home_off_pass_epa")):
        return lines
    fmt = lambda v: f"{v:+.3f}"
    lines.append("| Unit matchup (EPA/play vs avg) | " f"{row.home_team} | {row.away_team} |")
    lines.append("|---|---|---|")
    lines.append(f"| Pass offense | {fmt(row.home_off_pass_epa)} | {fmt(row.away_off_pass_epa)} |")
    lines.append(f"| Pass defense | {fmt(row.home_def_pass_epa)} | {fmt(row.away_def_pass_epa)} |")
    lines.append(f"| Rush offense | {fmt(row.home_off_rush_epa)} | {fmt(row.away_off_rush_epa)} |")
    lines.append(f"| Rush defense | {fmt(row.home_def_rush_epa)} | {fmt(row.away_def_rush_epa)} |")
    edges = []
    if row.get("net_pass_epa", 0) and abs(row.net_pass_epa) > 0.05:
        better = row.home_team if row.net_pass_epa > 0 else row.away_team
        edges.append(f"**{better}** holds the passing-game edge ({row.net_pass_epa:+.3f} net EPA/play)")
    if row.get("net_rush_epa", 0) and abs(row.net_rush_epa) > 0.05:
        better = row.home_team if row.net_rush_epa > 0 else row.away_team
        edges.append(f"**{better}** holds the ground-game edge ({row.net_rush_epa:+.3f} net EPA/play)")
    if edges:
        lines.append("")
        lines.append("; ".join(edges) + ".")
    return lines


def render_week(preds: pd.DataFrame, league: str, season: int, week: int) -> str:
    lines = [f"# {league.upper()} — Season {season}, Week {week}", ""]
    for _, row in preds.sort_values("pred_margin", key=abs, ascending=False).iterrows():
        pick = row.home_team if row.pred_margin > 0 else row.away_team
        by = abs(row.pred_margin)
        prob = row.home_win_prob if row.pred_margin > 0 else 1 - row.home_win_prob
        lines.append(f"## {row.away_team} @ {row.home_team}")
        lines.append("")
        lines.append(
            f"**Pick: {pick} by {by:.1f}** — win probability {prob:.0%}. "
            f"Power ratings: {row.home_team} {row.home_rating:+.1f}, "
            f"{row.away_team} {row.away_rating:+.1f}"
            + (" (neutral site)." if row.neutral else ".")
        )
        if pd.notna(row.get("spread_line")):
            edge = row.pred_margin - row.spread_line
            side = row.home_team if edge > 0 else row.away_team
            lines.append(
                f"Market: {row.home_team} {-row.spread_line:+.1f}. "
                f"Model vs market: {edge:+.1f} pts toward **{side}**."
            )
        lines.append("")
        lines.extend(_matchup_lines(row))
        lines.append("")
    return "\n".join(lines)


def write_report(text: str, league: str, season: int, week: int) -> Path:
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"{league}_s{season}_w{week:02d}.md"
    path.write_text(text)
    return path
