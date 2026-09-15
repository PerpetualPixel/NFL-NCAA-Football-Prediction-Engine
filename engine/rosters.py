"""Who is on the team today, and who is ruled out.

Ratings can be fitted from last season. Names cannot. Measured against the
current rosters, of the players an NFL team leaned on last season 21% are
now under contract somewhere else and 7% are out of the league; in college
it is worse — 23% have transferred and 31% are on no roster at all, so a
breakdown built from last season's production names the wrong player more
often than the right one. And a player who *is* still on the roster may be
on injured reserve, which last season's numbers say nothing about either.

This module is the answer to "is that still true today". It reads the
current roster for every team, the current NFL depth chart, and the week's
injury report, and turns them into one verdict per player:

    ``available``   on the active roster with nothing against their name
    ``limited``     questionable for this game
    ``out``         ruled out: Out or Doubtful on the report, or on a
                    reserve list (injured reserve, PUP, NFI, suspended)
    ``reserve``     on the practice squad — not part of the game-day plan
    ``off_roster``  not on this team any more
    ``unknown``     no current roster for this team, so nothing is claimed

Everything that names a player goes through this. Matching is by player id
— gsis ids for the NFL, ESPN athlete ids for college — because the play
feed writes "C.Sutton" and no name-matching rule is trustworthy enough to
decide whether somebody is on a roster. On a team whose roster was read, a
player the roster does not list has left, and is not named; where no roster
covers the team nothing is claimed either way and the page says as much.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace

import pandas as pd

from .data import ingest

AVAILABLE = "available"
LIMITED = "limited"
OUT = "out"
RESERVE = "reserve"
OFF_ROSTER = "off_roster"
UNKNOWN = "unknown"

#: verdicts that mean the player can be expected to take the field
PLAYABLE = frozenset({AVAILABLE, LIMITED, UNKNOWN})
#: verdicts that mean it would be wrong to present them as a current player
GONE = frozenset({OFF_ROSTER})

# How old the roster snapshot may be before the site stops treating it as a
# statement about right now. Rosters move on Tuesdays and Wednesdays and the
# feeds republish several times a day, so anything beyond a couple of days is
# not "current" for a game this week.
FRESH_HOURS = 48.0

# The club-filed roster status, from the NFL's own transaction feed.
_NFL_STATUS = {
    "ACT": (AVAILABLE, None),
    "DEV": (RESERVE, "on the practice squad"),
    "RES": (OUT, "on a reserve list"),
    "PUP": (OUT, "on the physically-unable-to-perform list"),
    "INA": (OUT, "inactive"),
    "EXE": (OUT, "on the exempt list"),
    "E01": (OUT, "on the exempt list"),
    "E14": (OUT, "on the exempt list"),
    "CUT": (OFF_ROSTER, "released"),
    "RET": (OFF_ROSTER, "retired"),
    "TRD": (OFF_ROSTER, "traded"),
    "TRC": (OFF_ROSTER, "traded"),
    "TRT": (OFF_ROSTER, "traded"),
}

#: statuses that mean a club has ruled the player out of this week's game for
#: a reason filed days in advance — as opposed to a game-day inactive, which
#: is not known when a pick is published as a lean
RESERVE_STATUSES = frozenset({"RES", "PUP", "EXE", "E01", "E14"})

# Only the reserve codes whose meaning is unambiguous are spelled out; any
# other reserve code keeps the generic "on a reserve list", which is true
# without claiming to know which one.
_NFL_RESERVE_DETAIL = {
    "R01": "on injured reserve",
    "R48": "on injured reserve, designated to return",
    "R02": "on the physically-unable-to-perform list",
    "R40": "on the non-football-injury list",
    "W03": "waived",
}

# The game-status designations on the final injury report.
_DESIGNATION = {
    "Out": (OUT, "out"),
    "Doubtful": (OUT, "doubtful"),
    "Questionable": (LIMITED, "questionable"),
}

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: object) -> str:
    """A comparable form of a name: no accents, punctuation or suffix.

    Used only as a fallback when no id is available, and never on its own to
    decide that somebody has left a team.
    """
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z ]", " ", text.lower())
    parts = [p for p in text.split() if p and p not in _SUFFIXES]
    return " ".join(parts)


def abbreviated_key(name: object) -> str:
    """The play-by-play's "C.Sutton" form, reduced to "c sutton"."""
    norm = normalize_name(name)
    parts = norm.split()
    if len(parts) < 2:
        return norm
    return f"{parts[0][0]} {' '.join(parts[1:])}"


@dataclass(frozen=True)
class PlayerStatus:
    """One player's standing with one team, as of the roster snapshot."""

    name: str
    team: str
    availability: str
    player_id: str | None = None
    position: str | None = None
    reason: str | None = None
    depth_rank: int | None = None
    now_with: str | None = None

    @property
    def playable(self) -> bool:
        return self.availability in PLAYABLE

    @property
    def gone(self) -> bool:
        return self.availability in GONE

    @property
    def verified(self) -> bool:
        return self.availability != UNKNOWN

    def label(self) -> str:
        """A short parenthetical for the prose, or "" when there is nothing
        worth saying (an available player needs no annotation)."""
        if self.availability == AVAILABLE:
            return ""
        if self.availability == UNKNOWN:
            # no roster covers this team, so there is nothing to say about
            # this player in particular; the page says so once, at the top
            return ""
        if self.now_with:
            return f"now with {self.now_with}"
        return self.reason or self.availability.replace("_", " ")


@dataclass
class RosterView:
    """Every team's current roster, indexed for lookup by id or by name."""

    league: str
    season: int
    week: int | None = None
    as_of: pd.Timestamp | None = None
    roster_week: int | None = None
    sources: tuple[str, ...] = ()
    by_team: dict[str, dict[str, PlayerStatus]] = field(default_factory=dict)
    team_of_id: dict[str, str] = field(default_factory=dict)
    names_by_team: dict[str, dict[str, str]] = field(default_factory=dict)
    depth: dict[tuple[str, str], list[PlayerStatus]] = field(default_factory=dict)

    # -- state -----------------------------------------------------------
    @property
    def empty(self) -> bool:
        return not self.by_team

    @property
    def fresh(self) -> bool:
        """Whether this describes the present rather than a stale download."""
        if self.empty or self.as_of is None:
            return False
        age = (pd.Timestamp.now(tz="UTC") - self.as_of).total_seconds() / 3600.0
        return age <= FRESH_HOURS

    def covers(self, team: object) -> bool:
        """Whether a verdict about this team's players can be trusted.

        A team with a handful of listed players is a broken download, not a
        roster, and is treated as no coverage at all.
        """
        return len(self.by_team.get(str(team), {})) >= 20

    def as_of_text(self) -> str:
        if self.as_of is None:
            return "unavailable"
        return self.as_of.tz_convert("UTC").strftime("%d %b %Y %H:%M UTC")

    # -- lookup ----------------------------------------------------------
    def lookup(self, team: object, player_id: object = None,
               name: object = None) -> PlayerStatus:
        """This player's standing with this team.

        Resolution order is id, then exact name, then the abbreviated name
        the play feed uses. An id that resolves to a *different* team is a
        transfer and says so; an id that resolves nowhere on a team whose
        roster is published means the player has left the league or the
        level. Anything on an uncovered team is ``unknown``.
        """
        team = str(team)
        squad = self.by_team.get(team)
        if not self.covers(team):
            return PlayerStatus(name=str(name or player_id or ""), team=team,
                                availability=UNKNOWN, player_id=_id(player_id))
        pid = _id(player_id)
        if pid and pid in squad:
            return squad[pid]
        if pid and pid in self.team_of_id:
            return PlayerStatus(name=str(name or ""), team=team,
                                availability=OFF_ROSTER, player_id=pid,
                                now_with=self.team_of_id[pid])
        found = self._by_name(team, name)
        if found is not None:
            return found
        if pid or name:
            return PlayerStatus(name=str(name or pid), team=team,
                                availability=OFF_ROSTER, player_id=pid,
                                reason="not on the roster")
        return PlayerStatus(name="", team=team, availability=UNKNOWN)

    def _by_name(self, team: str, name: object) -> PlayerStatus | None:
        if not name:
            return None
        index = self.names_by_team.get(team, {})
        squad = self.by_team.get(team, {})
        for key in (normalize_name(name), abbreviated_key(name)):
            pid = index.get(key)
            if pid and pid in squad:
                return squad[pid]
        return None

    def starters(self, team: object, position: str, limit: int = 3) -> list[PlayerStatus]:
        """The published depth chart at one position, best first, with anyone
        ruled out dropped. Empty when no depth chart was published."""
        listed = self.depth.get((str(team), position.upper()), [])
        out = [p for p in listed if p.availability in (AVAILABLE, LIMITED, UNKNOWN)]
        out.sort(key=lambda p: (p.depth_rank is None, p.depth_rank or 0))
        return out[:limit]

    def unavailable(self, team: object) -> list[PlayerStatus]:
        """Everyone the club has ruled out, most important position first.

        This is the half of availability the weekly injury report misses: a
        player placed on injured reserve in August never appears on a game
        status report, because he was never in the game plan to begin with.
        """
        squad = self.by_team.get(str(team), {})
        out = [p for p in squad.values() if p.availability == OUT]
        return sorted(out, key=lambda p: (_position_rank(p.position), p.name))

    # -- bulk use --------------------------------------------------------
    def annotate(self, frame: pd.DataFrame, team_col: str = "team",
                 id_col: str = "player_id", name_col: str = "player") -> pd.DataFrame:
        """Attach the current verdict to a table of players.

        Adds ``availability``, ``roster_note``, ``roster_verified`` and, where
        the roster knows better than the source feed, corrected ``player`` and
        ``position`` columns. Players who have left the team are dropped: a
        breakdown that names them is wrong, not merely dated.
        """
        if frame is None or frame.empty:
            return frame
        df = frame.copy()
        ids = df[id_col] if id_col in df.columns else pd.Series(None, index=df.index)
        names = df[name_col] if name_col in df.columns else pd.Series(None, index=df.index)
        found = [self.lookup(t, i, n) for t, i, n in zip(df[team_col], ids, names)]
        df["availability"] = [p.availability for p in found]
        df["roster_note"] = [p.label() for p in found]
        df["roster_verified"] = [p.verified for p in found]
        df["depth_rank"] = [p.depth_rank for p in found]
        # the roster spells names properly and knows what a player is listed
        # at now; the play feed abbreviates and the archive is a year old
        df[name_col] = [p.name if p.verified and p.name else n
                        for p, n in zip(found, names)]
        if "position" in df.columns:
            df["position"] = [p.position or old
                              for p, old in zip(found, df["position"])]
        else:
            df["position"] = [p.position for p in found]
        return df[~df["availability"].isin(GONE)].reset_index(drop=True)


def _id(value: object) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return None
    # ids arrive as floats out of parquet often enough to be worth handling
    if text.endswith(".0"):
        text = text[:-2]
    return text


_POSITION_ORDER = {"QB": 0, "RB": 1, "WR": 1, "TE": 2, "LT": 2, "T": 3, "OT": 3,
                   "CB": 3, "EDGE": 3, "DE": 3, "OLB": 4, "G": 4, "C": 4, "S": 4,
                   "DT": 4, "LB": 4, "FS": 5, "SS": 5, "K": 6, "P": 7, "LS": 8}


def _position_rank(position: object) -> int:
    return _POSITION_ORDER.get(str(position or "").upper(), 5)


# ---------------------------------------------------------------------------
# Building the view
# ---------------------------------------------------------------------------

def build(league: str, season: int, week: int | None = None) -> RosterView:
    """The current roster picture for a league, or an empty view.

    Never raises: a feed that cannot be read produces a view whose verdicts
    are all ``unknown``, and the site says the roster could not be verified
    rather than printing last season's names as though they were this
    week's.
    """
    try:
        if league == "nfl":
            return _build_nfl(season, week)
        return _build_ncaa(season, week)
    except Exception:  # noqa: BLE001 - a roster feed must never break a build
        return RosterView(league=league, season=season, week=week)


def _build_nfl(season: int, week: int | None) -> RosterView:
    roster, as_of = ingest.load_nfl_weekly_rosters(season)
    view = RosterView(league="nfl", season=season, week=week, as_of=as_of)
    if roster.empty:
        return view
    view.sources = ("nflverse weekly rosters",)
    roster = roster[roster["season"] == season] if "season" in roster else roster
    if roster.empty:
        return view
    # the roster published for the target week, or the latest one published:
    # a week that has not been filed yet is still described by the newest
    # roster on file, which is today's truth about who is on the team
    weeks = sorted(int(w) for w in roster["week"].dropna().unique())
    if weeks:
        usable = [w for w in weeks if week is None or w <= int(week)] or weeks
        view.roster_week = usable[-1]
        roster = roster[roster["week"] == view.roster_week]

    for row in roster.itertuples():
        pid = _id(getattr(row, "gsis_id", None))
        if pid is None:
            continue
        team = str(row.team)
        code = str(getattr(row, "status", "") or "").upper()
        availability, reason = _NFL_STATUS.get(code, (UNKNOWN, None))
        detail = _NFL_RESERVE_DETAIL.get(
            str(getattr(row, "status_description_abbr", "") or "").upper())
        if detail and availability in (OUT, OFF_ROSTER):
            reason = detail
        if availability in GONE:
            # released or retired: they are not on this team, and they are
            # not on another one either, so they are simply left out
            continue
        status = PlayerStatus(
            name=str(getattr(row, "full_name", "") or ""),
            team=team,
            availability=availability,
            player_id=pid,
            position=_clean_text(getattr(row, "depth_chart_position", None)
                                 or getattr(row, "position", None)),
            reason=reason,
        )
        _index(view, status)

    _apply_depth_chart(view, season)
    _apply_injury_report(view, season, week)
    return view


def _build_ncaa(season: int, week: int | None) -> RosterView:
    roster, as_of = ingest.load_ncaa_rosters(season)
    view = RosterView(league="ncaa", season=season, week=week, as_of=as_of)
    if roster.empty:
        return view
    view.sources = ("cfbfastR rosters",)
    if "season" in roster.columns:
        roster = roster[roster["season"] == season]
    if roster.empty:
        return view
    for row in roster.itertuples():
        pid = _id(getattr(row, "athlete_id", None))
        if pid is None:
            continue
        first = _clean_text(getattr(row, "first_name", None)) or ""
        last = _clean_text(getattr(row, "last_name", None)) or ""
        name = f"{first} {last}".strip()
        if not name:
            continue
        _index(view, PlayerStatus(
            name=name,
            team=str(row.team),
            # college publishes no availability at all; being on the roster
            # is the only claim the data supports
            availability=AVAILABLE,
            player_id=pid,
            position=_clean_text(getattr(row, "position", None)),
        ))
    return view


def _index(view: RosterView, status: PlayerStatus) -> None:
    squad = view.by_team.setdefault(status.team, {})
    squad[status.player_id] = status
    view.team_of_id[status.player_id] = status.team
    names = view.names_by_team.setdefault(status.team, {})
    for key in (normalize_name(status.name), abbreviated_key(status.name)):
        if key:
            names.setdefault(key, status.player_id)


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _apply_depth_chart(view: RosterView, season: int) -> None:
    """Order each position group by the club's own published depth chart.

    This is what lets a week-one breakdown name the starter. Where the depth
    chart is newer than the roster file, its timestamp becomes the view's:
    it is the more recent statement about who plays.
    """
    chart, chart_as_of = ingest.load_nfl_depth_charts(season)
    if chart.empty:
        return
    view.sources = view.sources + ("nflverse depth charts",)
    if chart_as_of is not None and (view.as_of is None or chart_as_of > view.as_of):
        view.as_of = chart_as_of
    ranked = chart.sort_values(["team", "pos_abb", "pos_rank"], kind="stable")
    for row in ranked.itertuples():
        pid = _id(getattr(row, "gsis_id", None))
        team = str(row.team)
        position = str(getattr(row, "pos_abb", "") or "").upper()
        if not pid or not position:
            continue
        squad = view.by_team.get(team, {})
        known = squad.get(pid)
        rank = getattr(row, "pos_rank", None)
        rank = int(rank) if rank is not None and pd.notna(rank) else None
        if known is None:
            # on the depth chart but not on the roster file: trust the depth
            # chart's name, and say the roster could not confirm it
            known = PlayerStatus(
                name=str(getattr(row, "player_name", "") or ""), team=team,
                availability=UNKNOWN, player_id=pid, position=position,
            )
            _index(view, known)
        best = known.depth_rank
        if rank is not None:
            best = rank if best is None else min(best, rank)
        updated = replace(known, depth_rank=best)
        view.by_team.setdefault(team, {})[pid] = updated
        # the depth list carries this position's own rank: a guard who is the
        # second-choice left guard and the third-choice right guard is second
        # on one list and third on the other
        view.depth.setdefault((team, position), []).append(
            replace(updated, depth_rank=rank))


def _apply_injury_report(view: RosterView, season: int, week: int | None) -> None:
    """Lay this week's game-status designations over the roster statuses.

    The designations are the sharper signal — a club puts "Out" against a
    player who is otherwise perfectly rostered — so they win where they
    exist, but they never resurrect somebody the roster has on a reserve
    list.
    """
    if week is None:
        return
    reports = ingest.load_nfl_injuries([season], refresh_latest=True)
    if reports is None or reports.empty or "report_status" not in reports.columns:
        return
    wk = reports[(reports["season"] == season) & (reports["week"] == int(week))]
    wk = wk[wk["report_status"].notna()]
    if wk.empty:
        return
    view.sources = view.sources + ("nflverse injury reports",)
    for row in wk.itertuples():
        pid = _id(getattr(row, "gsis_id", None))
        team = str(row.team)
        squad = view.by_team.get(team)
        if not squad or pid not in squad:
            continue
        current = squad[pid]
        if current.availability in (OUT, OFF_ROSTER):
            continue  # already ruled out for a longer-running reason
        availability, word = _DESIGNATION.get(str(row.report_status), (None, None))
        if availability is None:
            continue
        injury = _clean_text(getattr(row, "report_primary_injury", None))
        reason = f"{word} — {injury.lower()}" if injury else word
        squad[pid] = replace(current, availability=availability, reason=reason)
