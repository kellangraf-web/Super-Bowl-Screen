# Super Bowl Contender Screen

A static cutoff screen built on statements like "every Super Bowl champion
since 2002 ranked top X in this stat." Each cutoff sits at least 2 spots looser
than the worst past champion, as a buffer. A team must pass **every** filter.
The cutoffs are fixed and don't recalculate. It runs **after Week 10** and
**after the regular season**.

**Latest results → [output/RESULTS.md](output/RESULTS.md)** (auto-updates every Tuesday)

## The filters

**After Week 10**

| Filter | Must be | Worst champ | Buffer |
|---|---|---|---|
| Win % | top 12 | #10 (2019 KC) | 2 |
| Net EPA/play | top 16 (top half) | #13 (2011 NYG) | 3 |
| Last season's point diff | top 20 | #16 (2007 NYG) | 4 |
| Yards per play differential | top 20 | #16 (2014 NE) | 4 |
| Net EPA/play, last 6 games | top 18 | #16 (2019 KC) | 2 |

**After the regular season**

| Filter | Must be | Worst champ | Buffer |
|---|---|---|---|
| Win % | top 12 | #10 (2011 NYG) | 2 |
| Vegas rating (point spreads) | top 16 (top half) | #13 (2011 NYG) | 3 |
| Last season's point diff | top 20 | #16 (2007 NYG) | 4 |
| Yards per play differential | top 20 | #17 (2007 NYG) | 3 |
| Net EPA/play, opponent-adjusted | top 18 | #15 (2011 NYG) | 3 |

## Backtest (2002–2025)

All 24 champs pass at both checkpoints: about 8.4 contenders per season
after Week 10 (range 4–12) and 8.9 after the regular season (range 6–11). The buffers trade a slightly bigger list for protection against
a future champ who is a bit weaker than any past one in one area.

## How filters were chosen

Every stat was ranked within each season and checked for the worst rank any
champion had. The core four had the tightest worst-champ ranks and did not overlap
much. A fifth filter was added to each checkpoint: recent form (net EPA over the
last 6 games) after Week 10, and opponent-adjusted net EPA after the regular
season. Each was the best of all remaining stats, but trims less than one team
per season. Further filters add almost nothing.

## Researched markers that didn't make the cut

The well-known indicators all have one or two champion exceptions, which force
the cutoff so loose it barely filters anything:

| Marker | Champ exception | Teams that would pass |
|---|---|---|
| Passer rating differential | 2007 NYG (−10.3) | 74% |
| ANY/A differential | 2007 NYG | 66% |
| Turnover margin ≥ 0 | 2007 NYG, 2015 DEN, 2023 KC | 85% |
| 30+ sacks | 2006 IND | 95% |
| Top-10 scoring offense and defense | only 12 of 24 champs | — |

Yards differential, another commonly cited marker, did make the cut as
yards-per-play differential.

## Files

| File | What it does |
|---|---|
| `features.py` | Play-by-play → EPA, success, 3rd down, pressure, point diff |
| `features_extra.py` | Vegas rating, opponent-adjusted EPA, recent form, playoff pedigree |
| `features_research.py` | Passer rating diff, ANY/A diff, yards/play diff, turnovers, sacks, scoring |
| `build_data.py` | Downloads and caches `data/team_stats.csv` (`--all` for full history) |
| `screen.py` | Sets cutoffs from past champs, runs the screen, writes `output/` |
| `.github/workflows/update.yml` | Re-runs everything every Tuesday and commits results |

## Run it locally

```bash
pip install -r requirements.txt
python build_data.py --all
python features_extra.py
python features_research.py
python screen.py
```

Edit `FILTERS` at the top of `screen.py` to change a stat or its "top X" cutoff.

## Sources for the researched markers

- [SI: Passer Rating Differential](https://www.si.com/more-sports/2011/06/23/most-importantstatpasserratingdifferential)
- [Football Perspective: Super Bowl metrics](https://www.footballperspective.com/super-bowl-metrics/)
- [AJC: Super Bowl champions' statistical indicators](https://ajc.com/sports/football/super-bowl-champions-have-certain-statistical-indicators/cLOjXXg1anpPighqSL5IcP)
