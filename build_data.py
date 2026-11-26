"""
build_data.py
Downloads play-by-play and saves team stats to data/team_stats.csv.

  python build_data.py            -> rebuilds ONLY the current season (fast; used weekly)
  python build_data.py --all      -> rebuilds every season from 2002 on (slow; run once)
"""

import sys
from pathlib import Path
import polars as pl
import nflreadpy as nfl
from features import build_season, champions

FIRST_SEASON = 2002                       # 32-team era begins (Houston joins)
DATA = Path(__file__).parent / "data"     # folder that holds the cached CSVs
STATS_FILE = DATA / "team_stats.csv"
CHAMPS_FILE = DATA / "champions.csv"


def main():
    DATA.mkdir(exist_ok=True)                         # make data/ if missing
    sched = nfl.load_schedules()                      # every game since 1999
    current = nfl.get_current_season()                # e.g. 2026

    rebuild_all = "--all" in sys.argv or not STATS_FILE.exists()
    seasons = range(FIRST_SEASON, current + 1) if rebuild_all else [current]

    frames = []
    for season in seasons:
        print(f"Building {season}...")
        frames.append(build_season(season, sched))   # wk10 + full rows for that season
    new = pl.concat(frames, how="diagonal")

    if not rebuild_all:
        # Keep cached history, swap in the freshly built current season
        old = pl.read_csv(STATS_FILE).filter(pl.col("season") != current)
        new = pl.concat([old, new], how="diagonal_relaxed")

    new.sort(["checkpoint", "season", "team"]).write_csv(STATS_FILE)
    champions(sched).write_csv(CHAMPS_FILE)          # list of past Super Bowl winners
    print(f"Saved {new.height} rows -> {STATS_FILE}")


if __name__ == "__main__":
    main()
