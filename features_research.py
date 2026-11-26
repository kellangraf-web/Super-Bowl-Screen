"""
features_research.py
Stats that research has linked to Super Bowl champions, per team/season/checkpoint:
  - passer rating differential (own passer rating minus rating allowed)
  - ANY/A differential (adjusted net yards per pass attempt, offense minus defense)
  - yards per play differential
  - turnover margin per game
  - defensive sacks per game
  - points scored / allowed per game (for scoring-offense and scoring-defense ranks)
Writes data/team_research.csv
"""

from pathlib import Path
import polars as pl
import nflreadpy as nfl
from features import RELOCATED, WEEK10

DATA = Path(__file__).parent / "data"
OUT_FILE = DATA / "team_research.csv"
FIRST = 2002


def passer_rating(cmp, att, yds, td, ints):
    """Official NFL passer rating formula (each piece capped between 0 and 2.375)."""
    clip = lambda e: e.clip(0, 2.375)
    a = clip((cmp / att - 0.3) * 5)            # completion %
    b = clip((yds / att - 3) * 0.25)           # yards per attempt
    c = clip(td / att * 20)                    # TD rate
    d = clip(2.375 - ints / att * 25)          # INT rate
    return (a + b + c + d) / 6 * 100


def side_stats(p: pl.DataFrame, team_col: str, prefix: str) -> pl.DataFrame:
    """Passing + yardage + turnover totals, grouped by offense or defense team."""
    att = (pl.col("pass_attempt") == 1) & (pl.col("sack") == 0)      # true pass attempts
    snap = (pl.col("pass") == 1) | (pl.col("rush") == 1)            # scrimmage plays
    g = p.group_by(["season", team_col]).agg(
        att.sum().alias("att"),
        pl.col("complete_pass").filter(att).sum().alias("cmp"),
        pl.col("passing_yards").filter(att).sum().alias("pyds"),
        pl.col("pass_touchdown").filter(att).sum().alias("td"),
        pl.col("interception").sum().alias("ints"),
        (pl.col("sack") == 1).sum().alias("sacks"),
        pl.col("yards_gained").filter(pl.col("sack") == 1).sum().alias("sack_yds"),  # negative yards
        pl.col("yards_gained").filter(snap).sum().alias("yds"),
        snap.sum().alias("plays"),
        pl.col("fumble_lost").sum().alias("fum_lost"),
    )
    g = g.with_columns(
        passer_rating(pl.col("cmp"), pl.col("att"), pl.col("pyds"), pl.col("td"), pl.col("ints")).alias("pr"),
        # ANY/A = (pass yds + 20*TD - 45*INT - sack yds lost) / (attempts + sacks)
        ((pl.col("pyds") + 20 * pl.col("td") - 45 * pl.col("ints") + pl.col("sack_yds"))
         / (pl.col("att") + pl.col("sacks"))).alias("anya"),
        (pl.col("yds") / pl.col("plays")).alias("ypp"),               # yards per play
        (pl.col("ints") + pl.col("fum_lost")).alias("to"),            # turnovers
    )
    keep = ["pr", "anya", "ypp", "to", "sacks"]
    return g.select(["season", pl.col(team_col).alias("team")] +
                    [pl.col(c).alias(f"{prefix}_{c}") for c in keep])


def build(season: int, sched: pl.DataFrame) -> pl.DataFrame:
    pbp = nfl.load_pbp([season]).filter(
        (pl.col("season_type") == "REG") & pl.col("posteam").is_not_null()
        & (pl.col("two_point_attempt") == 0))
    reg = sched.filter((pl.col("season") == season) & (pl.col("game_type") == "REG")
                       & pl.col("result").is_not_null())
    out = []
    for cp, max_wk in [("wk10", WEEK10), ("full", 99)]:
        p = pbp.filter(pl.col("week") <= max_wk)
        if p.height == 0:
            continue
        off = side_stats(p, "posteam", "o")       # what the team's offense did
        dfn = side_stats(p, "defteam", "d")       # what opponents did against its defense
        s = reg.filter(pl.col("week") <= max_wk)
        pts = pl.concat([
            s.select(pl.col("home_team").alias("team"), pl.col("home_score").alias("pf"), pl.col("away_score").alias("pa")),
            s.select(pl.col("away_team").alias("team"), pl.col("away_score").alias("pf"), pl.col("home_score").alias("pa")),
        ]).group_by("team").agg(pl.col("pf").mean().alias("pf_pg"), pl.col("pa").mean().alias("pa_pg"),
                                pl.len().alias("g"))
        df = off.join(dfn, on=["season", "team"]).join(pts, on="team")
        df = df.with_columns(
            (pl.col("o_pr") - pl.col("d_pr")).alias("prd"),                 # passer rating differential
            (pl.col("o_anya") - pl.col("d_anya")).alias("anya_diff"),       # ANY/A differential
            (pl.col("o_ypp") - pl.col("d_ypp")).alias("ypp_diff"),          # yards/play differential
            ((pl.col("d_to") - pl.col("o_to")) / pl.col("g")).alias("to_margin_pg"),  # takeaways - giveaways
            (pl.col("d_sacks") / pl.col("g")).alias("sacks_pg"),            # defensive sacks per game
            pl.lit(cp).alias("checkpoint"),
        )
        out.append(df.select("season", "team", "checkpoint", "o_pr", "d_pr", "prd",
                             "anya_diff", "ypp_diff", "to_margin_pg", "sacks_pg", "pf_pg", "pa_pg"))
    return pl.concat(out)


def main():
    sched = nfl.load_schedules().with_columns(
        pl.col("home_team").replace(RELOCATED), pl.col("away_team").replace(RELOCATED))
    current = nfl.get_current_season()
    rebuild_all = not OUT_FILE.exists()
    seasons = range(FIRST, current + 1) if rebuild_all else [current]
    new = pl.concat([build(s, sched) for s in seasons])
    if not rebuild_all:
        new = pl.concat([pl.read_csv(OUT_FILE).filter(pl.col("season") != current), new],
                        how="diagonal_relaxed")
    new.sort(["checkpoint", "season", "team"]).write_csv(OUT_FILE)
    print(f"Saved {new.height} rows -> {OUT_FILE}")


if __name__ == "__main__":
    main()
