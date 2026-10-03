"""Settings you are most likely to change live here."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"          # raw downloads (git-ignored)
SITE_DIR = ROOT / "site"          # the static dashboard
OUT_DIR = SITE_DIR / "data"       # generated JSON the dashboard reads (git-ignored)
CHATTER_STORE = ROOT / "chatter" / "items.jsonl"   # accumulates across runs; safe to commit

# The neighborhood the dashboard opens on. SDPD beat 813 = North Park.
HOME_BEAT = 813

# --- official data -----------------------------------------------------------------------

PORTAL = "https://seshat.datasd.org"
FIRST_YEAR = 2020                 # the NIBRS dataset starts in January 2020
NIBRS_URL = PORTAL + "/police_nibrs/pd_nibrs_{year}_datasd.csv"
CALLS_URL = PORTAL + "/police_calls_for_service/pd_calls_for_service_{year}_datasd.csv"   # dispatch log
BEATS_GEOJSON_URL = PORTAL + "/gis_police_beats/pd_beats_datasd.geojson"
BEAT_CODES_URL = PORTAL + "/gis_police_beats/pd_beat_codes_list_datasd.csv"

USER_AGENT = "sd-neighborhood-crime-dashboard/0.1 (personal, non-commercial)"

# A geocode is trusted to place an offense in a neighborhood when it is a single match ("M")
# with at least this score. Below that, SDPD's own beat label is used instead.
MIN_GEOCODE_SCORE = 90.0

# Points this close to a beat boundary (in degrees, roughly 100 m) are snapped to that beat.
# Catches addresses on the waterfront and in slivers between polygons.
SNAP_DEGREES = 0.001

# --- chatter (news, Reddit and police dispatch) --------------------------------------------

# Names people use for the home neighborhood. A post has to mention one of these to be kept,
# unless it comes from a subreddit named after the place (r/northpark), where every post counts.
CHATTER_PLACES = ["North Park"]
# Phrases that contain the name but mean something else (a grocery chain with stores elsewhere).
CHATTER_IGNORE = ["North Park Produce"]
CHATTER_SUBREDDITS = ["sandiego", "SanDiegan", "northpark"]

# Local outlets' own feeds, read on top of the Google News search: (name, address). Any RSS or
# Atom feed can be added. The name is what the dashboard shows; spell it the way Google News does
# (the "outlet" values in chatter/items.jsonl) so one outlet is not listed twice on a story.
CHATTER_FEEDS = [
    ("NBC 7 San Diego", "https://www.nbcsandiego.com/news/local/?rss=y"),
    ("fox5sandiego.com", "https://fox5sandiego.com/news/local-news/feed/"),
    ("10News.com", "https://www.10news.com/news/local-news.rss"),
    ("cbs8.com", "https://www.cbs8.com/feeds/syndication/rss/news/local"),
    ("cbs8.com", "https://www.cbs8.com/feeds/syndication/rss/news/crime"),
    ("Times of San Diego", "https://timesofsandiego.com/crime/feed/"),
    ("KPBS", "https://www.kpbs.org/news/public-safety.rss"),
    ("Patch", "https://patch.com/feeds/aol/california/san-diego"),
]

# Police dispatch: how many days of SDPD's call log the dashboard lists for the home neighborhood.
# Which kinds of call are listed is the CALLS table in pipeline/dispatch.py.
DISPATCH_DAYS = 30

DIVISIONS = {
    1: "Northern", 2: "Northeastern", 3: "Eastern", 4: "Southeastern", 5: "Central",
    6: "Western", 7: "Southern", 8: "Mid-City", 9: "Northwestern",
}
