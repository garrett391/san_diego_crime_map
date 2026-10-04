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

# News and Reddit posts are collected for every neighborhood. A story belongs to a neighborhood
# when it names it: by SDPD's own name for it, or by one of the names below.

# Names people use that SDPD does not, and the SDPD neighborhoods each one covers. A story that
# says "Clairemont" is listed under all five.
CHATTER_ALIASES = {
    "Bankers Hill": ["Park West"],
    "City Heights": ["Azalea/Hollywood Park", "Castle", "Cherokee Point", "Chollas Creek", "Colina del Sol",
                     "Corridor", "Fairmount Park", "Fairmount Village", "Fox Canyon", "Islenair", "Swan Canyon",
                     "Teralta East", "Teralta West"],
    "Clairemont": ["Bay Ho", "Bay Park", "Clairemont Mesa East", "Clairemont Mesa West", "North Clairemont"],
    "College Area": ["College East", "College West"],
    "Cortez Hill": ["Cortez"],
    "Downtown San Diego": ["Core-Columbia", "Cortez", "East Village", "Gaslamp", "Harborview", "Horton Plaza",
                           "Little Italy", "Marina", "Petco Park"],
    "Mission Bay": ["Mission Bay Park"],
    "Mission Valley": ["Mission Valley East", "Mission Valley West"],
    "Mount Hope": ["Mt. Hope"],
    "OB": ["Ocean Beach"],
    "PB": ["Pacific Beach"],
    "Point Loma": ["La Playa", "Loma Portal", "Point Loma Heights", "Roseville/Fleet Ridge", "Sunset Cliffs",
                   "Wooded Area"],
    "Rancho Peñasquitos": ["Rancho Penasquitos"],
}

# SDPD names that are not looked for, because the words usually mean something else: an ordinary
# word ("a wooded area", "the border"), a surname, a company, a high school in Vista, a city up
# north. These neighborhoods still get their police calls, and stories that use one of the names above.
CHATTER_SKIP = ["Alta Vista", "Border", "Castle", "Corridor", "Cortez", "Harborview", "Marina", "Midtown", "North City",
                "Qualcomm", "Stockton", "Wooded Area"]

# Phrases that contain a name but mean something else (a grocery chain with stores elsewhere).
CHATTER_IGNORE = ["North Park Produce"]

# Outlets whose headlines are left out: ones from other cities that have a place of the same name
# (San Francisco has an Ocean Beach, Sacramento an Oak Park), and law firms' advertising written
# up as news. Add an outlet here when its stories turn up under the wrong neighborhood.
CHATTER_SKIP_OUTLETS = [
    "Hoodline", "KCRA", "KQED", "KSBW", "Mexico News Daily", "Miramar News", "San Francisco Chronicle", "The Acorn",
    "Ventura County Star",
    "Arash Law", "californiainjuryaccidentlawyer.com", "Carrillo Law Firm, LLP", "J&Y Law", "Pacific Attorney Group",
]

# Subreddits to read. A neighborhood's own subreddit is given the name it is about (SDPD's, or one
# from CHATTER_ALIASES), and every post there counts for that neighborhood. None marks a city-wide
# subreddit, where a post has to name a neighborhood to be kept.
CHATTER_SUBREDDITS = {
    "sandiego": None,
    "SanDiegan": None,
    "northpark": "North Park",
    "PacificBeach": "Pacific Beach",
    "LaJolla": "La Jolla",
    "Missionvalley": "Mission Valley",
    "OceanBeach": "Ocean Beach",
    "normalheights": "Normal Heights",
}
# Checked and left out (October 2026): r/clairemont and r/SouthParkSD are private, r/BarrioLogan and
# r/BankersHill do not exist, r/pointloma is a high school's page, and r/hillcrest is a handful of
# off-topic posts.

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

# Police dispatch: how many days of SDPD's call log the dashboard lists for each neighborhood.
# Which kinds of call are listed is the CALLS table in pipeline/dispatch.py.
DISPATCH_DAYS = 30

DIVISIONS = {
    1: "Northern", 2: "Northeastern", 3: "Eastern", 4: "Southeastern", 5: "Central",
    6: "Western", 7: "Southern", 8: "Mid-City", 9: "Northwestern",
}
