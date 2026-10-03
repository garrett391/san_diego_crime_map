# San Diego neighborhood crime dashboard

A dashboard that answers one question about one neighborhood: **how much crime is reported here, how serious is it, and is it getting better or worse?** It opens on North Park and works for any of San Diego's 125 police beats.

![The dashboard, opened on North Park](docs/dashboard.png)

It is built from the San Diego Police Department's public offense records (January 2020 to yesterday), with an unofficial layer of local news headlines and Reddit posts on top. There is no server and no database. A Python script turns the city's CSV files into small JSON files, and a plain HTML and JavaScript page reads them.

## Run it

You need Python 3.11 or newer. Node is only needed for the JavaScript tests.

```
python -m venv .venv
.venv\Scripts\activate              # macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt

python -m pipeline refresh          # download, collect chatter, build (a few minutes the first time)
python -m pipeline serve            # opens http://localhost:8000
```

Run `refresh` again whenever you want newer data. The city rewrites its files every morning.

| Command | What it does |
| --- | --- |
| `python -m pipeline fetch` | Downloads the SDPD files into `data/` (about 250 MB; files that have not changed are skipped) |
| `python -m pipeline chatter` | Collects news and Reddit posts into `chatter/items.jsonl`. Add `--backfill` once to search older news, or `--offline` to re-score what is stored without fetching |
| `python -m pipeline build` | Turns `data/` into `site/data/` (about 15 seconds) |
| `python -m pipeline serve` | Serves `site/` at http://localhost:8000 |
| `python -m pipeline refresh` | `fetch`, then `chatter`, then `build` |

Any neighborhood can be opened from the menu or by clicking it on the map. To change which one the page opens on, and which one news and Reddit posts are collected for, edit `HOME_BEAT` and `CHATTER_PLACES` in [pipeline/config.py](pipeline/config.py).

## What is on the page

- **The headline.** Offenses reported in the past 12 months, the change from the 12 months before, and whether that change is more than chance. Four smaller figures sit beside it: high-severity offenses, the latest 3 months, the last 30 days so far, and the city as a whole.
- **Monthly trend since 2020**, stacked by severity, with a 12-month average line and a rough outlook for the next three months.
- **The neighborhood against the city**, both as running 12-month totals indexed to 2020.
- **Map.** One circle per block, sized by the number of reports and colored by the most serious one. Click a circle for its reports. A density view is one click away.
- **By type, busiest blocks, latest reports**, all for the period you pick (30 days, 3 months, 12 months, or everything since 2020).
- **Next-door neighborhoods**, compared on the same footing.
- **What people are saying.** News and Reddit posts that mention the neighborhood, grouped so one incident covered by eight outlets shows once. A post that names an intersection or block is pinned on the map.

Severity and type filters at the top apply to everything. The filters, period and neighborhood are kept in the URL, so a view can be bookmarked or shared.

## Four decisions behind the numbers

**1. Paperwork is not crime.** The largest single code in SDPD's data is "All Other Offenses," and most of it is not crime at all: 72-hour mental-health holds, warrant arrests, parole and probation violations, missing-person reports. That is 15% of all records. They are left out of every count and off the map. The rule is in [pipeline/categories.py](pipeline/categories.py).

**2. A neighborhood is where the address is.** SDPD labels each record with a beat, but about one label in twelve disagrees with where the block address actually falls. Hundreds of reports at plainly North Park addresses carry another neighborhood's label, and some labeled North Park geocode miles away. A map needs the two to agree, so a record is placed by its address when the city's geocoding is confident and by SDPD's label otherwise. Totals therefore differ slightly from SDPD's own dashboard.

**3. Late reports must not flatter the present.** Reports reach the data days to months after the offense. About a quarter of a month's reports are still missing when the month ends, and burglary and fraud run later still. Comparing a fresh period with a fully reported old one always makes the present look safer than it is. So every comparison here:

- stops 30 days before the newest data, and
- counts the year-earlier period only as it looked at the same point last year.

`python -m analysis.backtest` replays this on the first of every month since 2022: pretend the data ended that day, estimate the year-over-year change, and compare it with what the change turned out to be. Errors, in percentage points:

| North Park | Raw counts | This method |
| --- | --- | --- |
| 12 months, ending 30 days back | 2.0 too low on average | 0.7 on average, 1.8 at worst |
| 3 months, ending 30 days back | 5.1 too low | 2.3 on average, 7.0 at worst |
| 3 months, ending today | 13.8 too low | 4.3 on average, 15.4 at worst |
| 30 days, ending today | 29.6 too low | 10.9 on average, 37.3 at worst |

The last two rows are why the newest 30 days are shown but never compared. Approval speed changes too much from month to month (there was a backlog in early 2026 and a speed-up that September) for any correction to hold there.

A change is called "lower" or "higher" only when it is at least 5% and at least twice the ordinary chance variation for counts that size. Otherwise the page says "no clear change."

**4. Severity is three plain levels.** High is serious violence, medium is a direct threat to a person, home or car, and low is property loss and nuisance. The full mapping from FBI offense codes is one table in [pipeline/categories.py](pipeline/categories.py). It is this project's judgment, not an official scale, and that file is the one place to change it.

## How it is put together

```
City open data portal ──fetch──▶ data/*.csv, *.geojson ──build──▶ site/data/*.json ──▶ site/ (static page)
Google News, Reddit RSS ─chatter─▶ chatter/items.jsonl ───────────┘
```

| Path | What is in it |
| --- | --- |
| `pipeline/fetch.py` | Downloads the yearly SDPD files; a failed download never replaces a good file |
| `pipeline/build.py` | DuckDB: load, classify, place each offense in a neighborhood, write JSON |
| `pipeline/categories.py` | Offense code to category and severity; what counts as paperwork |
| `pipeline/chatter.py` | News and Reddit: fetch, score for relevance, group into stories, locate intersections |
| `pipeline/config.py` | Home neighborhood, data URLs, chatter settings |
| `site/js/stats.js` | Every number on the page, as pure functions |
| `site/js/charts.js`, `map.js`, `app.js` | Hand-drawn SVG charts, the MapLibre map, and the page logic |
| `analysis/backtest.py` | The replay behind decision 3 |
| `tests/` | Python tests, including a full build of a tiny made-up city, and JavaScript tests for the statistics |

The build is rerun from scratch every time, because the city rewrites past years as investigations proceed. It writes to a temporary folder and swaps it in only on success. The page loads about 500 KB of its own files up front, then one file per neighborhood (North Park's is 400 KB) on demand.

The only Python dependency is DuckDB. The page uses MapLibre GL from a CDN and CARTO's free Dark Matter basemap, with no build step and no API keys.

## Tests

```
python -m pytest
node --test tests/js/stats.test.mjs
```

The JavaScript suite includes a check that the browser's year-over-year counts match the pipeline's for the real data, when a build is present.

## Publishing on GitHub Pages

[.github/workflows/publish.yml](.github/workflows/publish.yml) runs the tests, downloads the data, builds, and publishes `site/` every morning and on every push. To turn it on, push the repository to GitHub and set Settings → Pages → Source to "GitHub Actions". Pages is free for public repositories.

This workflow has not been run yet; it was written without a GitHub repository to try it on.

The workflow does not collect news or Reddit posts (Reddit turns away requests from cloud servers). It publishes whatever is in `chatter/items.jsonl`, so run `python -m pipeline chatter` locally and commit that file when you want the feed updated.

## Limits

- These are **reports**, not convictions, and crimes nobody reported are not here.
- Addresses are rounded to the hundred-block. A circle marks a block, never a building.
- There are no population figures, so neighborhoods are compared by count, by area and by trend, not per resident. A busy commercial strip draws more reports than the number of people who live there would suggest.
- The public CSV files carry the date of an offense but not the time of day.
- The chatter filter is keyword rules. It misses some relevant posts and keeps a few irrelevant ones, and the number of posts says nothing about the amount of crime. History is thin: Google News returns about ten headlines per half-year for one neighborhood, and Reddit's search returns the 100 newest matches per subreddit.
- The outlook is the past year's level adjusted for season. It describes what "no change" would look like and does not predict events.

## Ideas for later

- **Time of day.** The city's live map service has the hour of each offense; joining it in would show when things happen.
- **Calls for service.** Dispatch records (already downloadable from the same portal) would add police activity that never became a report.
- **A better chatter filter.** A small language model could replace the keyword rules for relevance and location.
- **"Near my block."** Counts within a chosen distance of a point, across neighborhood lines.
- **Per-resident rates**, by joining census block populations to the beats.

## Data and credits

- [Police NIBRS Crime Offenses](https://data.sandiego.gov/datasets/police-nibrs/) and [Police Beats](https://data.sandiego.gov/datasets/police-beats/), City of San Diego open data portal.
- Basemap © [CARTO](https://carto.com/attributions), map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, drawn with [MapLibre GL JS](https://maplibre.org/).
- Headlines from Google News RSS and posts from Reddit's public RSS feeds, shown as title, source and link. No usernames are stored.
