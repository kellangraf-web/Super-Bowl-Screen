"""
features_injuries.py
Health data per team / season / checkpoint. Data only, not used as a screen filter.

  ir_count          Players on a reserve list (IR, PUP, NFI, etc.) at the checkpoint:
                    Week 10 for "wk10", the last regular-season week for "full".
                    2016 on only: earlier weekly rosters were backfilled and
                    their reserve counts aren't reliable.
  po1_missing       Starters missing from the team's first playoff game
                    ("full" checkpoint, playoff teams only, 2013 on).
                    Starter = 50%+ of snaps in at least 5 of the team's last 8 regular-season games.
                    Missing = under 25% of snaps in that playoff game (inactive counts as 0%).
  po1_missing_names Who was missing, for reference (CSV only).

Writes data/team_injuries.csv
"""

from pathlib import Path
import polars as pl
import nflreadpy as nfl
from features import RELOCATED, WEEK10

DATA = Path(__file__).parent / "data"
OUT_FILE = DATA / "team_injuries.csv"
FIRST_IR = 2016          # first season with trustworthy weekly reserve lists
FIRST_SNAPS = 2013       # first season with snap counts


def ir_counts(season: int) -> pl.DataFrame:
    """Reserve-list count per team at Week 10 and at the end of the regular season."""
    r = nfl.load_rosters_weekly([season]).filter(pl.col("game_type") == "REG") \
           .with_columns(pl.col("team").replace(RELOCATED))
    last = r["week"].max()                                  # latest regular-season week so far
    out = []
    for cp, wk in [("wk10", min(WEEK10, last)), ("full", last)]:
        n = r.filter((pl.col("week") == wk) & (pl.col("status") == "RES")) \
             .group_by("team").agg(pl.len().alias("ir_count"))
        teams = r.filter(pl.col("week") == wk).select("team").unique()   # teams with 0 on IR still appear
        out.append(teams.join(n, on="team", how="left")
                        .with_columns(pl.col("ir_count").fill_null(0), pl.lit(cp).alias("checkpoint")))
    return pl.concat(out).with_columns(pl.lit(season).alias("season"))


def playoff_missing(season: int) -> pl.DataFrame:
    """Starters missing from each playoff team's first playoff game."""
    sc = nfl.load_snap_counts([season]).with_columns(pl.col("team").replace(RELOCATED))
    if sc.height == 0 or sc.filter(pl.col("game_type") != "REG").height == 0:
        return pl.DataFrame()                               # no playoff games yet
    # A player's share of snaps = the larger of his offense or defense share
    sc = sc.with_columns(pl.max_horizontal(pl.col("offense_pct").fill_null(0),
                                           pl.col("defense_pct").fill_null(0)).alias("pct"))
    reg = sc.filter(pl.col("game_type") == "REG")

    # Starters: 50%+ snaps in 5+ of the team's last 8 regular-season games
    last8 = reg.select("team", "week").unique().sort("week").group_by("team").tail(8)
    starters = reg.join(last8, on=["team", "week"]) \
                  .group_by(["team", "pfr_player_id", "player", "position"]) \
                  .agg((pl.col("pct") >= 0.5).sum().alias("n50")) \
                  .filter(pl.col("n50") >= 5)

    # Each team's first playoff game (Wild Card, or Divisional for bye teams)
    post = sc.filter(pl.col("game_type") != "REG")
    first = post.group_by("team").agg(pl.col("week").min().alias("week"))
    g1 = post.join(first, on=["team", "week"]).select("team", "pfr_player_id", "pct")

    # Starters who didn't appear (null -> 0%) or barely played
    miss = starters.join(first.select("team"), on="team") \
                   .join(g1, on=["team", "pfr_player_id"], how="left") \
                   .with_columns(pl.col("pct").fill_null(0)) \
                   .filter(pl.col("pct") < 0.25)
    names = miss.group_by("team").agg(
        pl.len().alias("po1_missing"),
        pl.concat_str([pl.col("player"), pl.lit(" ("), pl.col("position"), pl.lit(")")])
          .str.join(", ").alias("po1_missing_names"))
    return first.select("team").join(names, on="team", how="left") \
                .with_columns(pl.col("po1_missing").fill_null(0),
                              pl.col("po1_missing_names").fill_null(""),
                              pl.lit(season).alias("season"), pl.lit("full").alias("checkpoint"))


def build(season: int) -> pl.DataFrame:
    parts = []
    if season >= FIRST_IR:
        parts.append(ir_counts(season))
    if season >= FIRST_SNAPS:
        pm = playoff_missing(season)
        if pm.height:
            parts.append(pm)
    if not parts:
        return pl.DataFrame()
    df = parts[0]
    for p in parts[1:]:
        df = df.join(p, on=["season", "team", "checkpoint"], how="full", coalesce=True)
    return df


def main():
    current = nfl.get_current_season()
    rebuild_all = not OUT_FILE.exists()
    seasons = range(FIRST_SNAPS, current + 1) if rebuild_all else [current - 1, current]  # last season's playoffs may finish in Feb
    frames = [f for f in (build(s) for s in seasons) if f.height]
    new = pl.concat(frames, how="diagonal_relaxed")
    if not rebuild_all:
        old = pl.read_csv(OUT_FILE, infer_schema_length=None).filter(~pl.col("season").is_in(list(seasons)))
        new = pl.concat([old, new], how="diagonal_relaxed")
    cols = ["season", "team", "checkpoint", "ir_count", "po1_missing", "po1_missing_names"]
    new = new.with_columns([pl.lit(None).alias(c) for c in cols if c not in new.columns])
    new.select(cols).sort(["checkpoint", "season", "team"]).write_csv(OUT_FILE)
    print(f"Saved {new.height} rows -> {OUT_FILE}")


if __name__ == "__main__":
    main()
