"""League configurations.

One shared pipeline, two leagues. Everything league-specific (data sources,
rating decay, home-field priors, margin variance) lives here so the NFL and
NCAA engines stay in lockstep structurally while being tuned independently.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LeagueConfig:
    name: str
    # seasons available from the free, no-key data sources
    first_season: int
    # exponential recency decay applied to past games when fitting ratings
    # (weight = decay ** games_of_staleness measured in weeks)
    weekly_decay: float
    # weight multiplier applied to games from previous seasons
    prior_season_weight: float
    # how many past seasons of games feed the rating fit
    rating_window_seasons: int
    # ridge regularization strength for the rating solve; pulls teams toward
    # average when the sample is thin (early season). NCAA needs more because
    # 130+ FBS teams play ~12 games each against uneven schedules.
    rating_alpha: float
    # standard deviation of final-margin residuals, used for win probability
    margin_sigma: float
    # minimum completed weeks in a season before the backtest scores picks
    min_weeks_before_eval: int
    extra: dict = field(default_factory=dict)


NFL = LeagueConfig(
    name="nfl",
    first_season=2016,
    weekly_decay=0.985,
    prior_season_weight=0.45,
    rating_window_seasons=3,
    rating_alpha=6.0,
    margin_sigma=13.2,
    min_weeks_before_eval=4,
)

NCAA = LeagueConfig(
    name="ncaa",
    first_season=2016,
    weekly_decay=0.99,
    prior_season_weight=0.55,
    rating_window_seasons=3,
    rating_alpha=8.0,
    margin_sigma=16.5,
    min_weeks_before_eval=4,
)

LEAGUES = {"nfl": NFL, "ncaa": NCAA}
