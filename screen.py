"""
screen.py
Super Bowl contender screen — static rank cutoffs with a buffer.

Rule of thumb behind each filter: "every Super Bowl champion since 2002 ranked
top X in this stat." Each cutoff is set at least 2 spots looser than the worst
champion, so a slightly unusual future champ still gets through.
A contender must pass EVERY filter. The cutoffs never change on their own;
edit FILTERS to change them.

Run:  python screen.py
"""

from pathlib import Path
import polars as pl

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = ROOT / "output"

# ---------------- the static filters ----------------
# (column, label, higher_is_better, must rank top X)
FILTERS = {
    "wk10": [
        ("win_pct",  "Win %",                            True, 12),
        ("net_epa",  "Net EPA/play (offense − defense)", True, 16),   # top half
        ("prev_pd",  "Last season's point diff / game",  True, 20),
        ("ypp_diff", "Yards per play differential",      True, 20),
        ("recent_net_epa", "Net EPA/play, last 6 games", True, 18),   # current form
    ],
    "full": [
        ("win_pct",       "Win %",                             True, 12),
        ("market_rating", "Vegas rating (from point spreads)", True, 16),   # top half
        ("prev_pd",       "Last season's point diff / game",   True, 20),
        ("ypp_diff",      "Yards per play differential",       True, 20),
        ("adj_net_epa",   "Net EPA/play, opponent-adjusted",   True, 18),  # strength of schedule
    ],
}
# -----------------------------------------------------


def load() -> pl.DataFrame:
    """All stats joined (one row per team/season/checkpoint) with ranks for each filter."""
    df = pl.read_csv(DATA / "team_stats.csv", infer_schema_length=None)
    for f in ["team_extra.csv", "team_research.csv"]:
        df = df.join(pl.read_csv(DATA / f, infer_schema_length=None),
                     on=["season", "team", "checkpoint"], how="left")
    # A team with no prior season on record ranks last on last season's point diff
    df = df.with_columns(pl.col("prev_pd").fill_null(-99.0))
    by = ["checkpoint", "season"]
    cols = {c: h for fl in FILTERS.values() for c, _, h, _ in fl}
    # rank 1 = best within each season and checkpoint
    return df.with_columns([
        pl.col(c).rank("min", descending=h).over(by).cast(pl.Int32).alias(f"rk_{c}")
        for c, h in cols.items()
    ])


def screen(cp_df: pl.DataFrame, cp: str) -> pl.DataFrame:
    """Pass/fail per filter, number failed, and overall contender flag."""
    oks = [(pl.col(f"rk_{c}") <= top).alias(f"ok_{c}") for c, _, _, top in FILTERS[cp]]
    names = [f"ok_{c}" for c, *_ in FILTERS[cp]]
    return cp_df.with_columns(oks).with_columns(
        pl.sum_horizontal([(~pl.col(n)).cast(pl.Int32) for n in names]).alias("fails"),
        pl.all_horizontal(names).alias("contender"),
    )


def main():
    OUT.mkdir(exist_ok=True)
    df = load()
    current = df["season"].max()
    champs = pl.read_csv(DATA / "champions.csv").filter(pl.col("season").is_between(df["season"].min(), current))
    live = current not in champs["season"].to_list()

    md = [f"# Super Bowl Contender Screen — {current} season\n",
          "Static cutoffs. A contender must pass **every** filter. "
          "Buffer = how many spots looser the cutoff is than the worst past champion.\n"]

    for cp, title in [("wk10", "After Week 10"), ("full", "After the regular season")]:
        d = screen(df.filter(pl.col("checkpoint") == cp), cp)
        ch = d.join(champs, left_on=["season", "team"], right_on=["season", "champion"])

        md.append(f"\n## {title}\n")
        md.append("| Filter | Must be | Worst champ | Buffer |\n|---|---|---|---|")
        for c, label, _, top in FILTERS[cp]:
            worst = ch.sort(f"rk_{c}", descending=True).row(0, named=True)     # lowest-ranked champ
            md.append(f"| {label} | top {top} | #{worst[f'rk_{c}']} ({worst['season']} {worst['team']}) "
                      f"| {top - worst[f'rk_{c}']} |")

        per = d.filter(pl.col("season").is_in(champs["season"].to_list())) \
               .group_by("season").agg(pl.col("contender").sum().alias("n")).sort("season")
        per.join(champs, on="season").write_csv(OUT / f"backtest_{cp}.csv")
        md.append(f"\n**Backtest 2002–{champs['season'].max()}:** {ch['contender'].sum()}/{ch.height} champs pass. "
                  f"Contenders per season: avg {per['n'].mean():.1f}, range {per['n'].min()}–{per['n'].max()}.\n")

        if live:
            now = d.filter(pl.col("season") == current).sort(["fails", "rk_win_pct"])
            now.write_csv(OUT / f"screen_{current}_{cp}.csv")
            games = int(now["games"].max())
            note = " — season still in progress" if cp == "full" and games < 17 else ""
            ins = now.filter(pl.col("contender"))["team"].to_list()
            md.append(f"**{current} contenders** (through {games} games{note}): **{', '.join(ins) or 'none'}**\n")
            bub = []
            for r in now.filter(pl.col("fails") == 1).iter_rows(named=True):
                miss = next(l for c, l, *_ in FILTERS[cp] if not r[f"ok_{c}"])
                bub.append(f"{r['team']} ({miss.lower()})")
            if bub:
                md.append("On the bubble (miss one filter): " + ", ".join(bub) + "\n")

    (OUT / "RESULTS.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
