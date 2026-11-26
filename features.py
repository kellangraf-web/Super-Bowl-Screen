"""
features.py
Builds one row of stats per team per season, at two checkpoints:
  - "wk10": regular-season games through Week 10
  - "full": the entire regular season
All stats come from nflverse play-by-play (via nflreadpy), which has EPA,
success rate, and CPOE already calculated for every play.
"""

import polars as pl          # fast DataFrame library (nflreadpy returns polars frames)
import nflreadpy as nfl      # official Python loader for nflverse NFL data

# Week cutoff for the midseason checkpoint
WEEK10 = 10

# Play-by-play uses today's abbreviations for relocated teams, but the schedule
# uses the old ones. Map old -> new so the two always join.
RELOCATED = {"OAK": "LV", "SD": "LAC", "STL": "LA"}


def _norm(col: str) -> pl.Expr:
    """Replace old team abbreviations with current ones."""
    return pl.col(col).replace(RELOCATED)


def _team_stats(pbp: pl.DataFrame) -> pl.DataFrame:
    """Turn a slice of play-by-play into one row of stats per team."""

    # Keep only real offensive snaps: a pass or a run with an EPA value.
    # (This drops kickoffs, punts, kneels, spikes and penalties with no play.)
    plays = pbp.filter(
        ((pl.col("pass") == 1) | (pl.col("rush") == 1))
        & pl.col("epa").is_not_null()
        & pl.col("posteam").is_not_null()
    )

    # Flag "explosive" plays: 20+ yard passes or 10+ yard runs (common definition)
    plays = plays.with_columns(
        (
            ((pl.col("pass") == 1) & (pl.col("yards_gained") >= 20))
            | ((pl.col("rush") == 1) & (pl.col("yards_gained") >= 10))
        ).cast(pl.Int8).alias("explosive"),
        # Pressure proxy: a sack OR a QB hit on a dropback (true pressure data
        # isn't in play-by-play before 2018, so this keeps history comparable)
        (
            (pl.col("qb_dropback") == 1)
            & ((pl.col("sack") == 1) | (pl.col("qb_hit") == 1))
        ).cast(pl.Int8).alias("pressured"),
        # Early downs = 1st and 2nd down
        (pl.col("down") <= 2).alias("early_down"),
        # 3rd-down attempts (4th downs excluded so "go for it" choices don't skew it)
        (pl.col("down") == 3).alias("third_down"),
    )

    # Aggregation recipe shared by offense (group by posteam) and defense (by defteam)
    def agg_exprs(prefix: str):
        return [
            pl.col("epa").mean().alias(f"{prefix}_epa_play"),                       # EPA per play
            pl.col("epa").filter(pl.col("qb_dropback") == 1).mean()
              .alias(f"{prefix}_pass_epa"),                                          # EPA per dropback
            pl.col("success").filter(pl.col("early_down")).mean()
              .alias(f"{prefix}_early_success"),                                     # 1st/2nd down success rate
            pl.col("third_down_converted").filter(pl.col("third_down")).mean()
              .alias(f"{prefix}_3rd_conv"),                                           # 3rd-down conversion %
            pl.col("explosive").mean().alias(f"{prefix}_explosive"),                 # explosive play rate
            pl.col("pressured").filter(pl.col("qb_dropback") == 1).mean()
              .alias(f"{prefix}_pressure"),                                           # sack+hit rate per dropback
            pl.col("cpoe").mean().alias(f"{prefix}_cpoe"),                           # completion % over expected
        ]

    # Offense: stats for plays where the team had the ball
    off = plays.group_by(["season", "posteam"]).agg(agg_exprs("off")).rename({"posteam": "team"})
    # Defense: same stats, but for plays where the team was on defense
    dfn = plays.group_by(["season", "defteam"]).agg(agg_exprs("def")).rename({"defteam": "team"})

    # Combine offense + defense; drop columns that don't make sense on one side
    stats = off.join(dfn, on=["season", "team"]).drop(["def_cpoe"])
    # Rename for clarity: off_pressure is pressure ALLOWED (o-line), def_pressure is GENERATED
    return stats.rename({"off_pressure": "oline_pressure_allowed",
                         "def_pressure": "def_pressure_generated"})


def _point_diff(sched: pl.DataFrame) -> pl.DataFrame:
    """Point differential per game, win %, from the schedule of completed games."""
    done = sched.filter(pl.col("home_score").is_not_null()).with_columns(
        _norm("home_team"), _norm("away_team"))                             # fix relocated-team codes
    # Stack home and away perspectives so every game appears once per team
    home = done.select("season", pl.col("home_team").alias("team"),
                       (pl.col("home_score") - pl.col("away_score")).alias("margin"))
    away = done.select("season", pl.col("away_team").alias("team"),
                       (pl.col("away_score") - pl.col("home_score")).alias("margin"))
    games = pl.concat([home, away])
    return games.group_by(["season", "team"]).agg(
        pl.col("margin").mean().alias("point_diff_pg"),                      # avg margin per game
        ((pl.col("margin") > 0).sum() + 0.5 * (pl.col("margin") == 0).sum())
            .truediv(pl.len()).alias("win_pct"),                             # ties count as half
        pl.len().alias("games"),                                             # games played
    )


def build_season(season: int, sched: pl.DataFrame) -> pl.DataFrame:
    """Return both checkpoints ('wk10' and 'full') for one season."""
    # Load this season's play-by-play, regular season only
    pbp = nfl.load_pbp([season]).filter(pl.col("season_type") == "REG")
    reg = sched.filter((pl.col("season") == season) & (pl.col("game_type") == "REG"))

    out = []
    for checkpoint, max_week in [("wk10", WEEK10), ("full", 99)]:
        # Slice play-by-play and schedule to the checkpoint
        p = pbp.filter(pl.col("week") <= max_week)
        s = reg.filter(pl.col("week") <= max_week)
        if p.height == 0:          # season hasn't reached this point yet
            continue
        df = _team_stats(p).join(_point_diff(s), on=["season", "team"], how="left")
        out.append(df.with_columns(pl.lit(checkpoint).alias("checkpoint")))
    return pl.concat(out)


def champions(sched: pl.DataFrame) -> pl.DataFrame:
    """One row per season with the Super Bowl winner."""
    sb = sched.filter((pl.col("game_type") == "SB") & pl.col("home_score").is_not_null()) \
              .with_columns(_norm("home_team"), _norm("away_team"))         # fix relocated-team codes
    return sb.select(
        "season",
        # Winner = home team if they scored more, otherwise away team
        pl.when(pl.col("home_score") > pl.col("away_score"))
          .then(pl.col("home_team")).otherwise(pl.col("away_team")).alias("champion"),
    )
