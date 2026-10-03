# San Diego Crime Map — Requirements

## Overview and goals

A dark-themed web dashboard that maps crime incidents in the City of San Diego. Refreshed as needed from official SDPD data and enriched with crime-related social media posts. It is a personal project first, and portfolio project 2nd, so clean architecture and visual polish matter as much as the features, with possibly a real scheduled data pipeline down the line.

- **Scope:** City of San Diego only (not the wider county). Default map view centers on North Park.
- **Audience:** Safetey data for myself and my wife first, recruiters and engineers reviewing the portfolio second.
- **Budget:** $0/month hosting, free tiers only.
- **Success looks like:** Either a local app we can spin up, a github pages app, or a public URL that loads in under 3 seconds, shows recent incidents from the last data load to several years back, and a README that explains the pipeline and design choices.

## Data sources

The primary source is SDPD's NIBRS crime offenses feature service, which the City says is updated daily; yearly CSVs back-fill history, and social media adds an unofficial "chatter" layer.

| Source | Use | Freshness | Access |
| --- | --- | --- | --- |
| [SDPD NIBRS Crime Offenses (ArcGIS FeatureServer)](https://webmaps.sandiego.gov/arcgis/rest/services/SDPD/SDPD_NIBRS_Crime_Offenses_Geo/FeatureServer) | Main incident layer: offense, category, date/time, hundred-block location | Daily, per the service description | Free ArcGIS REST query API, no key |
| [Police NIBRS Crime Offenses CSVs (Open Data Portal)](https://data.sandiego.gov/datasets/police-nibrs/) | One-time historical backfill, 2020 to present, one file per year | Quarterly | Free CSV download |
| [Police Calls for Service (Open Data Portal)](https://data.sandiego.gov/) | Optional layer: dispatched calls, not confirmed crimes | To verify | Free CSV download |
| [Police beats and neighborhoods (Open Data Portal)](https://data.sandiego.gov/departments/police/) | Boundaries for neighborhood filter and North Park outline | Static | GeoJSON |
| [Reddit Data API](https://creatorcrawl.com/blog/reddit-api-pricing-2026/) | Crime-related posts from San Diego subreddits | Hourly | Free for non-commercial use, 100 queries/min, approval required |


The police NIBRS data has been saved as csvs in `./data`


**Down the line:** Scraping Nextdoor, Twitter, facebook etc.

**Data caveats to surface in the UI:** NIBRS locations are hundred-block addresses, not exact points, and reflect reported offenses, not convictions.

## Reddit pipeline

Recommendation: a hybrid filter. Cheap keyword rules discard most posts, and only the survivors go to a small LLM for classification and location extraction. This keeps LLM volume tiny and makes a good portfolio story.

1. **Fetch:** pull new posts and top-level comments from r/sandiego and any active neighborhood subreddits (to verify) as needed via the official API.
2. **Keyword prefilter:** keep posts matching a crime vocabulary (stolen, break-in, shooting, robbed, police activity, catalytic converter, etc.). Tune for recall, not precision.
3. **LLM classify:** return strict JSON with `is_crime_report`, `category` (mapped to the NIBRS categories), `severity`, `location_text`, `time_reference` and `confidence`. Drop anything below a confidence threshold.
4. **Geocode:** resolve `location_text` with a free geocoder (e.g. Nominatim or the US Census geocoder), bounded to the city limits.
5. **Assign precision:** address, intersection, neighborhood or none. Posts with no usable location are kept for the stats panel but not mapped.
6. **Store:** post ID, permalink, short excerpt, classification and coordinates. No usernames.

**Map treatment:** Reddit items render as translucent circles sized by location precision, visually distinct from official points, and labeled "unverified".

**Classifier is pluggable:** an interface with two implementations, a hosted LLM (e.g. Claude Haiku) and a free local model via Ollama for manual runs. See the open question on LLM spend.

## Functional requirements

The app is a single full-screen map with a filter bar, a collapsible stats panel and an incident detail drawer.

**Map**

- Scatter plot of incidents, colored by category; point size or opacity encodes severity.
- Heatmap toggle that swaps the scatter for a density layer using the same filters.
- Layer toggles: Official incidents, Reddit chatter, and optionally Calls for service.
- City boundary and neighborhood outlines; North Park highlighted on first load.
- "Data updated X hours ago" indicator per source.

**Filters** (all reflected in the URL so a view can be shared)

- Date range: presets for 24 hours, 7 days, 30 days, 90 days and year to date, plus a custom range picker.
- Crime category (multi-select, NIBRS categories).
- Severity (High, Medium, Low; see the open question on mapping).
- Crime against: Persons, Property, Society.
- Neighborhood or police beat.
- Source: official, Reddit or both.

**Stats panel** (follows the filters and, optionally, the current map viewport)

- Total incidents and change versus the previous equal-length period.
- Trend line by day or week.
- Breakdown by category and by hour of day.
- Top neighborhoods by count.

**Incident details** (click a point)

- Official: offense description, category, code section, date and time, hundred-block address, beat and neighborhood.
- Reddit: post title, short excerpt, link to the post, classifier confidence and location precision.

## UI and design

Dark theme only, styled like an operations console: a near-black basemap with muted streets so colored points carry the attention.

- **Basemap:** a free dark vector style (e.g. CARTO Dark Matter or Protomaps) rendered with MapLibre GL. No paid map token.
- **Palette:** colorblind-safe categorical colors for crime categories; severity shown with intensity, not a red/green scale.
- **Layout:** map fills the screen; filter bar pinned top; stats panel slides in from the right; detail drawer from the bottom on mobile.
- **Responsive:** fully usable on a phone, since the author will check North Park on the go.
- **Disclaimer footer:** data sources, hundred-block precision note, and "Reddit items are unverified".

## Non-functional requirements

- **Cost:** $0/month for hosting, database and scheduling. Free-tier limits (storage, sleeping instances, job minutes) to be verified during setup.
- **Reliability:** each ingest job is idempotent (upsert on the source's unique offense ID), logs its run, and a failure never deletes existing data.
- **Code quality (portfolio):** typed code, unit tests for parsers and the classifier, a CI workflow, and a README with an architecture diagram and screenshots.
