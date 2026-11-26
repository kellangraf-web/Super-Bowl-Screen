"""
export_dashboard.py
Writes docs/data.json for the interactive dashboard (docs/index.html).

The JSON is compact: a list of metric definitions, then one array per
team/season/checkpoint holding raw values in metric order. The page computes
ranks in the browser, so any metric can become a filter on the fly.

Run after screen.py:  python export_dashboard.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path
import polars as pl
from screen import FILTERS

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"

# key -> (label, higher_is_better, display format, group)
#   format: "pct3" = .650   "n1"/"n2"/"n3" = decimals   "int" = whole number
METRICS = {
    "win_pct":                ("Win %",                              True,  "pct3", "Results"),
    "point_diff_pg":          ("Point diff / game",                  True,  "n1",   "Results"),
    "pf_pg":                  ("Points scored / game",               True,  "n1",   "Results"),
    "pa_pg":                  ("Points allowed / game",              False, "n1",   "Results"),
    "prev_pd":                ("Last season's point diff / game",    True,  "n1",   "Pedigree"),
    "po_wins_3yr":            ("Playoff wins, prior 3 seasons",      True,  "int",  "Pedigree"),
    "market_rating":          ("Vegas rating (pts vs avg team)",     True,  "n1",   "Market"),
    "net_epa":                ("Net EPA / play",                     True,  "n3",   "Efficiency"),
    "adj_net_epa":            ("Net EPA / play, opp-adjusted",       True,  "n3",   "Efficiency"),
    "recent_net_epa":         ("Net EPA / play, last 6 games",       True,  "n3",   "Form"),
    "recent_pd":              ("Point diff / game, last 6",          True,  "n1",   "Form"),
    "ypp_diff":               ("Yards / play differential",          True,  "n2",   "Efficiency"),
    "off_epa_play":           ("Offense EPA / play",                 True,  "n3",   "Offense"),
    "adj_off_epa":            ("Offense EPA / play, opp-adjusted",   True,  "n3",   "Offense"),
    "off_pass_epa":           ("Offense EPA / dropback",             True,  "n3",   "Offense"),
    "qb_composite":           ("QB composite (EPA + CPOE z-score)",  True,  "n2",   "Offense"),
    "off_early_success":      ("Offense early-down success",         True,  "pct3", "Offense"),
    "off_3rd_conv":           ("Offense 3rd-down conv %",            True,  "pct3", "Offense"),
    "off_explosive":          ("Offense explosive play rate",        True,  "pct3", "Offense"),
    "oline_pressure_allowed": ("O-line sack+hit rate allowed",       False, "pct3", "Offense"),
    "def_epa_play":           ("Defense EPA / play allowed",         False, "n3",   "Defense"),
    "adj_def_epa":            ("Defense EPA / play, opp-adjusted",   False, "n3",   "Defense"),
    "def_pass_epa":           ("Defense EPA / dropback allowed",     False, "n3",   "Defense"),
    "def_early_success":      ("Defense early-down success allowed", False, "pct3", "Defense"),
    "def_3rd_conv":           ("Defense 3rd-down conv % allowed",    False, "pct3", "Defense"),
    "def_explosive":          ("Defense explosive rate allowed",     False, "pct3", "Defense"),
    "def_pressure_generated": ("Pass rush sack+hit rate",            True,  "pct3", "Defense"),
    "sacks_pg":               ("Sacks / game",                       True,  "n2",   "Defense"),
    "prd":                    ("Passer rating differential",         True,  "n1",   "Passing"),
    "anya_diff":              ("ANY/A differential",                 True,  "n2",   "Passing"),
    "to_margin_pg":           ("Turnover margin / game",             True,  "n2",   "Ball security"),
}


def load() -> pl.DataFrame:
    """Join the three stat files and build the QB composite."""
    df = pl.read_csv(DATA / "team_stats.csv", infer_schema_length=None)
    for f in ["team_extra.csv", "team_research.csv"]:
        df = df.join(pl.read_csv(DATA / f, infer_schema_length=None),
                     on=["season", "team", "checkpoint"], how="left")
    by = ["checkpoint", "season"]
    # z-score within each season, so eras compare fairly
    z = lambda c: (pl.col(c) - pl.col(c).mean().over(by)) / pl.col(c).std().over(by)
    return df.with_columns(
        # QB composite = avg of EPA/dropback z and CPOE z (CPOE starts 2006; EPA only before)
        pl.when(pl.col("off_cpoe").is_null()).then(z("off_pass_epa"))
          .otherwise((z("off_pass_epa") + z("off_cpoe")) / 2).alias("qb_composite"),
    )
    # Missing values stay null; the page ranks them last (e.g. no prior season)


def main():
    DOCS.mkdir(exist_ok=True)
    df = load().sort(["season", "checkpoint", "team"])
    keys = list(METRICS)
    cp_index = {"wk10": 0, "full": 1}

    # One compact array per row: [season, checkpoint index, team, games, *metric values]
    rows = []
    for r in df.select(["season", "checkpoint", "team", "games"] + keys).iter_rows():
        vals = [None if v is None else round(float(v), 4) for v in r[4:]]
        rows.append([r[0], cp_index[r[1]], r[2], r[3]] + vals)

    champs = pl.read_csv(DATA / "champions.csv").filter(pl.col("season") >= df["season"].min())

    out = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "checkpoints": ["wk10", "full"],
        "metrics": [{"key": k, "label": v[0], "hib": v[1], "fmt": v[2], "group": v[3]}
                    for k, v in METRICS.items()],
        "champions": {str(s): t for s, t in champs.iter_rows()},
        # The screen's official filters, used as the dashboard's starting point
        "defaults": {cp: [{"key": c, "top": top} for c, _, _, top in fl] for cp, fl in FILTERS.items()},
        "rows": rows,
    }
    path = DOCS / "data.json"
    path.write_text(json.dumps(out, separators=(",", ":")))
    print(f"Saved {len(rows)} rows, {len(keys)} metrics -> {path} ({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
