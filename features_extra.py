"""
features_extra.py
Extra team metrics built from game-level data, at the same two checkpoints:
  - net EPA/play (offense minus defense)
  - opponent-adjusted offense/defense/net EPA (strength of schedule)
  - Vegas market rating (points better than an average team, from point spreads)
  - recent form: point diff and net EPA over the last 6 games
  - pedigree: playoff wins over the previous 3 seasons, last season's point diff
Writes data/team_extra.csv
"""

from pathlib import Path
import numpy as np
import polars as pl
import nflreadpy as nfl
from features import RELOCATED, WEEK10

DATA = Path(__file__).parent / "data"
GAMES_FILE = DATA / "team_games.csv"     # cached game-level EPA (slow to rebuild)
OUT_FILE = DATA / "team_extra.csv"
FIRST = 2002
RECENT = 6                                # games in the "recent form" window


def norm(col):
    """Old team codes -> current (OAK->LV, SD->LAC, STL->LA)."""
    return pl.col(col).replace(RELOCATED)


def game_epa(seasons, rebuild=False) -> pl.DataFrame:
    """One row per team per game: offensive EPA/play and defensive EPA/play allowed."""
    if GAMES_FILE.exists() and not rebuild:
        cached = pl.read_csv(GAMES_FILE)
        missing = [s for s in seasons if s not in cached["season"].unique().to_list()]
        # Always refresh the latest season (it changes weekly)
        todo = sorted(set(missing) | {max(seasons)})
        cached = cached.filter(~pl.col("season").is_in(todo))
    else:
        cached, todo = None, list(seasons)

    frames = []
    for s in todo:
        p = nfl.load_pbp([s]).filter(
            (pl.col("season_type") == "REG") & ((pl.col("pass") == 1) | (pl.col("rush") == 1))
            & pl.col("epa").is_not_null() & pl.col("posteam").is_not_null())
        # EPA per play for each offense in each game
        g = p.group_by(["season", "week", "game_id", "posteam", "defteam"]).agg(
            pl.col("epa").mean().alias("off_epa"))
        # Pair each team's offense with its opponent's offense (= its defense allowed)
        g = g.join(g.select("game_id", pl.col("posteam").alias("defteam"),
                            pl.col("off_epa").alias("def_epa")), on=["game_id", "defteam"])
        frames.append(g.rename({"posteam": "team", "defteam": "opp"}))
    new = pl.concat(frames)
    out = new if cached is None else pl.concat([cached, new], how="diagonal_relaxed")
    out.write_csv(GAMES_FILE)
    return out


def opp_adjust(g: pl.DataFrame, iters: int = 5) -> pl.DataFrame:
    """Opponent-adjusted EPA: credit for good defenses faced, penalty for weak ones."""
    adj_off = g.group_by("team").agg(pl.col("off_epa").mean())
    adj_def = g.group_by("team").agg(pl.col("def_epa").mean())
    lg = g["off_epa"].mean()                                   # league-average EPA/play
    for _ in range(iters):                                     # a few passes to converge
        # Offense: actual EPA minus how much the opponents' defenses usually allow vs league
        o = g.join(adj_def.rename({"team": "opp", "def_epa": "opp_def"}), on="opp") \
             .group_by("team").agg((pl.col("off_epa") - (pl.col("opp_def") - lg)).mean().alias("off_epa"))
        d = g.join(adj_off.rename({"team": "opp", "off_epa": "opp_off"}), on="opp") \
             .group_by("team").agg((pl.col("def_epa") - (pl.col("opp_off") - lg)).mean().alias("def_epa"))
        adj_off, adj_def = o, d
    return adj_off.rename({"off_epa": "adj_off_epa"}).join(
        adj_def.rename({"def_epa": "adj_def_epa"}), on="team")


def market_rating(sched: pl.DataFrame) -> pl.DataFrame:
    """Least squares on point spreads: spread = home rating - away rating + home edge.
    Recent games count double, since lines react to injuries and form."""
    teams = sorted(set(sched["home_team"]) | set(sched["away_team"]))
    idx = {t: i for i, t in enumerate(teams)}
    n = len(sched)
    X = np.zeros((n + 1, len(teams) + 1))                      # last column = home-field edge
    y = np.zeros(n + 1)
    w = np.ones(n + 1)
    last_wk = sched["week"].max()
    for k, r in enumerate(sched.iter_rows(named=True)):
        X[k, idx[r["home_team"]]] = 1
        X[k, idx[r["away_team"]]] = -1
        X[k, -1] = 1
        y[k] = r["spread_line"]
        w[k] = 2.0 if r["week"] > last_wk - 4 else 1.0         # last 4 weeks weigh double
    X[n, :-1] = 1                                              # constraint: ratings average 0
    w = np.sqrt(w)
    coef = np.linalg.lstsq(X * w[:, None], y * w, rcond=None)[0]
    return pl.DataFrame({"team": teams, "market_rating": coef[:-1]})


def main():
    sched = nfl.load_schedules().with_columns(norm("home_team"), norm("away_team"))
    current = nfl.get_current_season()
    seasons = list(range(FIRST, current + 1))
    games = game_epa(seasons)

    # Playoff wins per team-season (for pedigree)
    post = sched.filter((pl.col("game_type") != "REG") & pl.col("result").is_not_null())
    pw = pl.concat([
        post.filter(pl.col("result") > 0).select("season", pl.col("home_team").alias("team")),
        post.filter(pl.col("result") < 0).select("season", pl.col("away_team").alias("team")),
    ]).group_by(["season", "team"]).agg(pl.len().alias("po_wins"))

    reg = sched.filter((pl.col("game_type") == "REG") & pl.col("result").is_not_null())
    # Margin per team per game (home and away views stacked)
    margins = pl.concat([
        reg.select("season", "week", pl.col("home_team").alias("team"), pl.col("result").alias("margin")),
        reg.select("season", "week", pl.col("away_team").alias("team"), (-pl.col("result")).alias("margin")),
    ])
    full_pd = margins.group_by(["season", "team"]).agg(pl.col("margin").mean().alias("pd"))

    rows = []
    for s in seasons:
        for cp, max_wk in [("wk10", WEEK10), ("full", 99)]:
            g = games.filter((pl.col("season") == s) & (pl.col("week") <= max_wk))
            if g.height == 0:
                continue
            m = margins.filter((pl.col("season") == s) & (pl.col("week") <= max_wk))
            sc = reg.filter((pl.col("season") == s) & (pl.col("week") <= max_wk))

            base = g.group_by("team").agg(
                (pl.col("off_epa") - pl.col("def_epa")).mean().alias("net_epa"))
            adj = opp_adjust(g).with_columns(
                (pl.col("adj_off_epa") - pl.col("adj_def_epa")).alias("adj_net_epa"))
            mkt = market_rating(sc)

            # Recent form: each team's last RECENT games before the checkpoint
            rec_g = g.sort("week").group_by("team").tail(RECENT).group_by("team").agg(
                (pl.col("off_epa") - pl.col("def_epa")).mean().alias("recent_net_epa"))
            rec_m = m.sort("week").group_by("team").tail(RECENT).group_by("team").agg(
                pl.col("margin").mean().alias("recent_pd"))

            # Pedigree: playoff wins over the 3 previous seasons + last season's point diff
            ped = pw.filter(pl.col("season").is_between(s - 3, s - 1)).group_by("team") \
                    .agg(pl.col("po_wins").sum().alias("po_wins_3yr"))
            prev = full_pd.filter(pl.col("season") == s - 1).select("team", pl.col("pd").alias("prev_pd"))

            df = base
            for other in [adj, mkt, rec_g, rec_m, ped, prev]:
                df = df.join(other, on="team", how="left")
            rows.append(df.with_columns(pl.lit(s).alias("season"), pl.lit(cp).alias("checkpoint"),
                                        pl.col("po_wins_3yr").fill_null(0)))
    out = pl.concat(rows, how="diagonal_relaxed")
    out.write_csv(OUT_FILE)
    print(f"Saved {out.height} rows -> {OUT_FILE}")


if __name__ == "__main__":
    main()
