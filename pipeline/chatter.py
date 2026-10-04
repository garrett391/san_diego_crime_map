"""Unofficial context: local news headlines and Reddit posts about each neighborhood.

Sources (no API keys):
  * Google News RSS search, one search per neighborhood name
  * local outlets' own RSS feeds, config.CHATTER_FEEDS
  * Reddit RSS search over the subreddits in config.CHATTER_SUBREDDITS

Police dispatch calls, the third kind of chatter on the dashboard, come from the city's files
rather than a feed; see dispatch.py.

What is fetched is kept when it names a neighborhood (or was posted in a neighborhood's own
subreddit) and appended to a small JSONL store (config.CHATTER_STORE), so items survive after
they drop out of the feeds. Scoring, grouping and locating happen at export time from the stored
text, so changing the rules below re-scores the whole history: `python -m pipeline chatter --offline`.

This layer is a feed, not a statistic. The feeds return whatever is recent and popular, so the
number of items per month says nothing about the amount of crime. No usernames are stored.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import config

ATOM = {"a": "http://www.w3.org/2005/Atom"}
STORED_TEXT = 1500      # characters of post text kept for scoring
EXCERPT = 240           # characters shown on the dashboard
MIN_SCORE = 3
STORY_GAP_DAYS = 4      # headlines this close in time can be the same story
NEWS_LEAD = 400         # characters of a news summary read for the neighborhood's name
NEWS_FULL = 100         # the most headlines Google News returns for one search
REDDIT_PAGE = 100       # the most posts Reddit returns for one request
REDDIT_PAGES = 3        # a Reddit search runs out after about 250 posts
REDDIT_QUERY = 500      # Reddit answers a longer search with nothing at all

NEWS_TERMS = "police OR shooting OR stabbing OR robbery OR burglary OR theft OR stolen OR assault OR arrested OR killed"
REDDIT_TERMS = ('stolen OR theft OR thief OR robbed OR robbery OR shooting OR shot OR stabbed OR police OR cops '
                'OR helicopter OR burglary OR assault OR vandalized OR "broken into" OR "break in"')


# ----------------------------------------------------------------------------------------------
# relevance
# ----------------------------------------------------------------------------------------------

def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


_VEHICLE = r"(?:car|truck|vehicle|van|suv|jeep|tacoma|prius|kia|hyundai|honda|motorcycle|moped)"

# tag -> [(pattern, points)]. A hit in the title counts double, and a post needs MIN_SCORE points.
# Unambiguous words score 3. Words that are often innocent ("shot", "attacked", "killed") score 2,
# so on their own they only count when they are in the title.
SIGNALS: dict[str, list[tuple[re.Pattern, int]]] = {
    "violence": [
        (_rx(r"\b(?:shootings?|shots fired|gun ?fire|gun ?shots?|gunman|stabb(?:ed|ing)s?|homicide|murder\w*|"
             r"assault\w*|robb(?:ed|ery|eries|ing)|mugg(?:ed|ing)|carjack\w*|gunpoint|knifepoint|"
             r"kidnapp\w*|rape[ds]?|pepper[- ]spray(?:ed)?)\b"), 3),
        (_rx(r"\b(?:shot|shoots?|killed|kills?|attacked|armed|threat(?:s|ened)|gun|knife|machete)\b"), 2),
    ],
    "vehicle": [
        (_rx(rf"\b{_VEHICLE} (?:was |got |were |been )?(?:stolen|broken into|burglarized|vandalized)\b"), 3),
        (_rx(rf"\bstolen {_VEHICLE}\b"), 3),
        (_rx(rf"\b(?:broke|broken|breaking) into (?:my |our |a |the |his |her )?{_VEHICLE}s?\b"), 3),
        (_rx(rf"\b{_VEHICLE} (?:break[- ]?ins?|prowl\w*|thie\w+|thefts?)\b"), 3),
        (_rx(r"\bcatalytic converters?\b"), 3),
        (_rx(r"\b(?:smashed|shattered|busted) (?:car |my |the )?windows?\b"), 2),
    ],
    "burglary": [
        (_rx(r"\b(?:burglar\w*|home invasion|intruder|prowler|smash[- ]and[- ]grab)\b"), 3),
        (_rx(r"\b(?:break[- ]?ins?|broke into|broken into|breaking into)\b"), 2),
    ],
    "theft": [
        (_rx(r"\b(?:stolen|stole|steals?|stealing|thefts?|thief|thieves|shoplift\w*|porch pirates?|pickpocket\w*)\b"), 3),
    ],
    "harassment": [
        (_rx(r"\b(?:harass\w+|indecent exposure|expos(?:ed|ing) (?:himself|themselves)|masturbat\w+|jerking off|"
             r"flasher|flashing (?:women|people|kids)|grop\w+|stalk(?:ed|er|ing)|follow(?:ed|ing) (?:me|her|us)|"
             r"peeping|road rage)\b"), 3),
    ],
    "police": [
        (_rx(r"\b(?:police|cops?|sdpd|officers?) (?:activity|presence|chase|pursuit|helicopter|standoff|search\w*|"
             r"investigat\w+|out in force|tape|cars?|cruisers?|block\w*|respond\w*)\b"), 3),
        (_rx(r"\b(?:swat|standoff|crime scene|manhunt|officer-involved|police shooting|caution tape)\b"), 3),
        (_rx(r"\b(?:\d+\+?|bunch of|lots? of|tons of|dozens? of|so many) (?:cops|police)\b"), 3),
        (re.compile(r"\bICE (?i:agents?|operation|raid|officers?)\b"), 3),
        (_rx(r"\bhelicopters?\b"), 2),
        (_rx(r"\b(?:arrest(?:ed|s)?|suspects?)\b"), 2),
        (_rx(r"\b(?:police|cops|sdpd|sirens)\b"), 1),
    ],
    "vandalism": [
        (_rx(r"\b(?:vandal\w*|graffiti|arson\w*|slashed tires?|incendiary|molotov|set (?:on )?fire|sets? fire)\b"), 3),
    ],
    "traffic": [
        (_rx(r"\bhit[- ]and[- ]run\b"), 3),
        (_rx(r"\b(?:struck|hit) by (?:a |an )?(?:car|vehicle|driver|truck|suv|\w+ driver)\b"), 3),
        (_rx(r"\b(?:dui|drunk driv\w+|street racing)\b"), 3),
        (_rx(r"\b(?:pedestrian|collision|crash(?:es|ed)?)\b"), 2),
    ],
    "court": [
        (_rx(r"\b(?:pleads? (?:not )?guilty|convict\w*|sentenc\w+|charged|stand trial|arraign\w+|prison|verdict)\b"), 2),
    ],
}

# Things that look like crime words but are not, or posts that are about something else.
NOISE = _rx(
    r"\b(?:tickets?|for sale|selling|giveaway|screening|concert|recommend\w*|photo ?shoot|musicians?|jam with|"
    r"shoot(?:ing)? (?:around|hoops|photos?|video|commercial|a (?:film|movie|video|commercial))|"
    r"sto(?:le|len) my heart|shot of (?:espresso|tequila|whiskey)|flu shot|long shot|vaccin\w+|booster|"
    r"parking (?:ticket|enforcement)|meter maids?|military helicopters?|hawk|coyotes?|lost/stolen|"
    r"(?:lost|missing|found) (?:my |our |a )?(?:cat|dog|kitten|puppy|wallet|keys?|glasses|phone))\b")


def score(title: str, text: str = "") -> tuple[int, list[str]]:
    """How strongly a post reads as a crime or police-activity report, and which kinds."""
    points: dict[str, int] = {}
    for tag, patterns in SIGNALS.items():
        for pattern, weight in patterns:
            if pattern.search(title):
                points[tag] = max(points.get(tag, 0), 2 * weight)
            elif pattern.search(text):
                points[tag] = max(points.get(tag, 0), weight)
    # "Pedestrian killed by car" is a traffic story; only call it violence on stronger words.
    if "traffic" in points and "violence" in points and not SIGNALS["violence"][0][0].search(title + " " + text):
        del points["violence"]
    total = sum(points.values())
    if NOISE.search(title):
        total -= 6
    elif NOISE.search(text):
        total -= 2
    # "police" and "court" describe the response rather than the event; other tags lead when present.
    tags = sorted(points, key=lambda t: (t in ("police", "court"), -points[t]))
    return total, tags


def reads_as_crime(item: dict) -> bool:
    if item["source"] == "news":
        return score(item["title"])[0] >= MIN_SCORE         # a crime story says so in its headline
    return score(item["title"], item.get("text", ""))[0] >= MIN_SCORE


# ----------------------------------------------------------------------------------------------
# neighborhoods: which ones an item is about
# ----------------------------------------------------------------------------------------------

class Neighborhoods:
    """The names each neighborhood goes by, and which neighborhoods a piece of text names.

    A neighborhood answers to SDPD's name for it, unless config.CHATTER_SKIP says those words
    usually mean something else, and to the names people use instead (config.CHATTER_ALIASES).
    One of those can cover several neighborhoods: "Clairemont" is five of SDPD's."""

    def __init__(self, hoods: list[dict]):               # [{"beat": 813, "name": "North Park"}, ...]
        self.own = {h["beat"]: h["name"] for h in hoods}
        official = {name: {beat} for beat, name in self.own.items()}
        skipped = {name.lower() for name in config.CHATTER_SKIP}
        self.names = {name: set(beats) for name, beats in official.items() if name.lower() not in skipped}
        self.unknown: list[str] = []                     # names in config.py that SDPD's list does not have
        for alias, covers in config.CHATTER_ALIASES.items():
            self.unknown += [name for name in covers if name not in official]
            if beats := set().union(*(official.get(name, ()) for name in covers)):
                self.names.setdefault(alias, set()).update(beats)
        self.subreddits: dict[str, set[int]] = {}        # "r/pacificbeach" -> the neighborhoods it is about
        for sub, name in config.CHATTER_SUBREDDITS.items():
            beats = official.get(name) or self.names.get(name) or set()
            if name and not beats:
                self.unknown.append(name)
            self.subreddits["r/" + sub.lower()] = beats
        self._spelling = {name.lower(): name for name in self.names}
        longest_first = sorted(self.names, key=len, reverse=True)       # "Rolando Park" before "Rolando"
        self._rx = _rx(r"\b(" + "|".join(map(re.escape, longest_first)) + r")\b") if longest_first else None

    def named(self, text: str) -> dict[int, str]:
        """The neighborhoods a text names, each with the name it was given there."""
        for phrase in config.CHATTER_IGNORE:             # e.g. a business that carries a neighborhood's name
            text = re.sub(re.escape(phrase), " ", text, flags=re.IGNORECASE)
        found: dict[int, str] = {}
        for m in self._rx.finditer(text) if self._rx else ():
            name = self._spelling.get(m.group(1).lower())
            for beat in self.names.get(name, ()):
                if beat not in found or name == self.own[beat]:
                    found[beat] = name
        return found

    def of(self, item: dict) -> dict[int, str]:
        """The neighborhoods an item is about, each with the name the item used for it.

        A resident's post is about the neighborhoods it names anywhere, and about the one whose
        subreddit it was posted in ("" for the name if the post never says it). A news story is
        about the ones in its headline or the opening of its summary, which an outlet's own feed
        carries and Google News does not. Some feeds carry the whole article, and a name further
        down is a passing mention or a link to another story."""
        text = item.get("text", "")
        found = self.named(item["title"] + " " + (text[:NEWS_LEAD] if item["source"] == "news" else text))
        for beat in self.subreddits.get(item["outlet"].lower(), ()):
            found.setdefault(beat, "")
        return found

    def called(self, beat: int) -> list[str]:
        """Every name that is looked for and covers this neighborhood."""
        return [name for name, beats in self.names.items() if beat in beats]


def built_hoods(out_dir: Path) -> list[dict]:
    """The neighborhoods on the dashboard, as the last build wrote them."""
    try:
        return json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))["hoods"]
    except FileNotFoundError:
        raise SystemExit("The neighborhoods are not known until the dashboard has been built once. "
                         "Run:  python -m pipeline build") from None


# ----------------------------------------------------------------------------------------------
# fetch
# ----------------------------------------------------------------------------------------------

def _get(url: str) -> tuple[bytes, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read(), {k.lower(): v for k, v in r.headers.items()}


def clean_text(fragment: str) -> str:
    """Feed HTML to plain text, with Reddit's 'submitted by /u/name' footer removed."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(fragment or "")))
    text = re.sub(r"submitted by\s+/u/\S+", " ", text)
    text = re.sub(r"\[link\]|\[comments\]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_news(xml: bytes) -> list[dict]:
    items = []
    for node in ET.fromstring(xml).findall("./channel/item"):
        outlet = (node.findtext("source") or "").strip()
        title = (node.findtext("title") or "").strip()
        if outlet and title.endswith(" - " + outlet):
            title = title[: -len(outlet) - 3]
        guid = node.findtext("guid") or node.findtext("link") or title
        try:
            published = parsedate_to_datetime(node.findtext("pubDate")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        items.append({"id": "news:" + guid, "source": "news", "outlet": outlet, "title": title,
                      "url": node.findtext("link") or "", "published": published.isoformat(), "text": ""})
    return items


def _when(stamp: str | None) -> datetime | None:
    """A feed's timestamp, in UTC. Feeds use RFC 822 or ISO 8601; NBC stations write local time
    with AM/PM and no zone ('Thu, Oct 01 2026 03:49:34 PM'), which has to be tried first because
    the RFC 822 parser accepts it and quietly drops the PM."""
    stamp = (stamp or "").strip()
    try:
        local = datetime.strptime(stamp, "%a, %b %d %Y %I:%M:%S %p")
        return (local + _pacific_offset(local.date())).replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for parse in (parsedate_to_datetime, datetime.fromisoformat):
        try:
            when = parse(stamp)
        except (TypeError, ValueError):
            continue
        return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    return None


def parse_feed(xml: bytes, outlet: str) -> list[dict]:
    """An outlet's own RSS or Atom feed. Unlike Google News, these carry a summary of the article."""
    items = []
    for node in ET.fromstring(xml).iter():
        if node.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        field: dict[str, str] = {}
        for child in node:                           # first non-empty value per tag, namespaces ignored
            value = (child.text or "").strip() or child.get("href") or ""
            if value:
                field.setdefault(child.tag.rsplit("}", 1)[-1], value)
        title, link = clean_text(field.get("title", "")), field.get("link", "")
        published = _when(field.get("pubDate") or field.get("published") or field.get("updated") or field.get("date"))
        if not (title and link and published):
            continue
        summary = field.get("description") or field.get("summary") or field.get("encoded") or field.get("content") or ""
        items.append({"id": f"feed:{outlet}:{field.get('guid') or field.get('id') or link}", "source": "news",
                      "outlet": outlet, "title": title, "url": link, "published": published.isoformat(),
                      "text": clean_text(summary)[:STORED_TEXT]})
    return items


def parse_reddit(xml: bytes) -> list[dict]:
    items = []
    for node in ET.fromstring(xml).findall("a:entry", ATOM):
        link = node.find("a:link", ATOM)
        category = node.find("a:category", ATOM)
        try:
            published = datetime.fromisoformat(node.findtext("a:published", "", ATOM)).astimezone(timezone.utc)
        except ValueError:
            continue
        items.append({
            "id": "reddit:" + node.findtext("a:id", "", ATOM),
            "source": "reddit",
            "outlet": "r/" + (category.get("term") if category is not None else "reddit"),
            "title": (node.findtext("a:title", "", ATOM) or "").strip(),
            "url": link.get("href") if link is not None else "",
            "published": published.isoformat(),
            "text": clean_text(node.findtext("a:content", "", ATOM))[:STORED_TEXT],
        })
    return items


def _news_search(query: str) -> list[dict]:
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    try:
        body, _ = _get(url)
    finally:
        time.sleep(1.5)
    return parse_news(body)


def fetch_news(hoods: Neighborhoods, backfill_from: int | None = None, log=print) -> list[dict]:
    """One search per neighborhood name, for crime headlines that carry it. With backfill_from, a
    name whose search came back full is also searched one half-year at a time since that year.

    Google returns at most 100 results a search and far fewer for old date ranges (about ten per
    half-year for North Park), so the backfill recovers the notable events, not everything.

    Three failed searches in a row end the run with what it has. Each day's run starts from a
    different name, so the names a short run never reached are not the same ones every time."""
    names = sorted(hoods.names)
    turn = date.today().toordinal() % len(names) if names else 0
    items: list[dict] = []
    failed = 0

    def search(query: str) -> list[dict]:
        nonlocal failed
        try:
            found = _news_search(query)
        except (urllib.error.URLError, ET.ParseError, TimeoutError) as e:
            failed += 1
            log(f"    Google News: a search failed ({e})")
            return []
        failed = 0
        return found

    for done, name in enumerate(names[turn:] + names[:turn], 1):
        found = search(f'intitle:"{name}" "San Diego" ({NEWS_TERMS})')
        items += found
        if backfill_from and len(found) >= NEWS_FULL:
            start = date(backfill_from, 1, 1)
            while start <= date.today() and failed < 3:
                end = date(start.year, 7, 1) if start.month == 1 else date(start.year + 1, 1, 1)
                items += search(f'intitle:"{name}" "San Diego" ({NEWS_TERMS}) '
                                f"after:{start.isoformat()} before:{end.isoformat()}")
                start = end
        if failed >= 3:
            log(f"    Google News: stopped after three failed searches in a row, {len(names) - done} names not reached")
            break
        if done % 25 == 0:
            log(f"    Google News: {done} of {len(names)} names searched")
    # A search also returns headlines that do not carry the name after all; those cannot be placed.
    return [i for i in {i["id"]: i for i in items}.values() if hoods.of(i)]


def fetch_feeds(feeds: list[tuple[str, str]], hoods: Neighborhoods, log=print) -> list[dict]:
    """Each outlet's own feed. These list every recent article, so only the ones that name a
    neighborhood and have a crime word in the headline are kept (the searches ask for both up
    front). A feed that is down or malformed is skipped."""
    items = []
    for outlet, url in feeds:
        try:
            found = parse_feed(_get(url)[0], outlet)
        except (urllib.error.URLError, ET.ParseError, TimeoutError) as e:
            log(f"    {outlet}: skipped ({e})")
            continue
        kept = [i for i in found if hoods.of(i) and score(i["title"])[1]]
        log(f"    {outlet}: {len(kept)} of {len(found)} articles are about a neighborhood")
        items += kept
    return list({i["id"]: i for i in items}.values())


def name_queries(names: list[str], room: int) -> list[str]:
    """'"A" OR "B" OR ...' search phrases that between them cover every name, each at most `room` characters."""
    queries, batch = [], ""
    for name in names:
        longer = f'{batch} OR "{name}"' if batch else f'"{name}"'
        if batch and len(longer) > room:
            queries.append(batch)
            longer = f'"{name}"'
        batch = longer
    return queries + [batch] if batch else queries


def fetch_reddit(hoods: Neighborhoods, deep: bool = False, log=print) -> list[dict]:
    """Posts with a crime word in them, the 100 newest a search (about a month of r/sandiego): one
    search per city-wide subreddit, and one across all the neighborhoods' own subreddits.

    With `deep`, those searches are followed as far back as Reddit goes (about 250 posts), and
    each city-wide subreddit is also searched for the neighborhood names themselves, a few names
    at a time. That is another 100 posts for each handful of names, reaching back a year or two.

    Only posts that name a neighborhood, or come from a neighborhood's own subreddit, are kept.
    Reddit allows unauthenticated readers about one request a minute, so this waits out the limit
    it reports between calls."""
    citywide = [sub for sub, about in config.CHATTER_SUBREDDITS.items() if not about]
    local = [sub for sub, about in config.CHATTER_SUBREDDITS.items() if about]
    pages = REDDIT_PAGES if deep else 1
    searches = [(sub, "", f"r/{sub}", pages) for sub in citywide]       # (subreddits, names to ask for, label, pages)
    if local:
        # Subreddits joined with "+" are searched as one; any that have gone private are left out.
        searches.append(("+".join(local), "", "neighborhood subreddits", pages))
    if deep:
        by_name = name_queries(sorted(hoods.names), REDDIT_QUERY - len(REDDIT_TERMS) - 5)
        searches += [(sub, f"({query}) ", f"r/{sub}, {n} of {len(by_name)} searches by name", 1)
                     for sub in citywide for n, query in enumerate(by_name, 1)]
    wait = 0.0

    def ask(url: str, label: str) -> list[dict] | None:
        nonlocal wait
        if wait:
            log(f"    waiting {wait:.0f}s for Reddit's rate limit...")
            time.sleep(wait)
        try:
            body, headers = _get(url)
            found = parse_reddit(body)
        except urllib.error.HTTPError as e:
            log(f"    {label}: skipped (HTTP {e.code})")
            wait = min(float(e.headers.get("retry-after") or e.headers.get("x-ratelimit-reset") or 30), 90) + 1
            return None
        except (urllib.error.URLError, ET.ParseError, TimeoutError) as e:
            log(f"    {label}: skipped ({e})")
            wait = 5.0
            return None
        try:
            remaining = float(headers.get("x-ratelimit-remaining", 1))
            wait = min(float(headers.get("x-ratelimit-reset", 0)), 90) + 1 if remaining < 1 else 0.0
        except ValueError:
            wait = 5.0
        return found

    items = []
    for subreddits, names, label, pages in searches:
        address = f"https://www.reddit.com/r/{urllib.parse.quote(subreddits, safe='+')}/search.rss?"
        query = {"q": f"{names}({REDDIT_TERMS})", "restrict_sr": 1, "sort": "new", "limit": REDDIT_PAGE}
        found: list[dict] = []
        for _ in range(pages):
            page = ask(address + urllib.parse.urlencode(query), label)
            found += page or []
            if not page or len(page) < REDDIT_PAGE:
                break
            query["after"] = page[-1]["id"].removeprefix("reddit:")     # the next page starts after this post
        if found or page is not None:
            kept = [i for i in found if hoods.of(i)]
            log(f"    {label}: {len(kept)} of {len(found)} posts are about a neighborhood")
            items += kept
    return list({i["id"]: i for i in items}.values())


# ----------------------------------------------------------------------------------------------
# store
# ----------------------------------------------------------------------------------------------

def load_store(path: Path) -> dict[str, dict]:
    items: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                items[item["id"]] = item
    return items


def save_store(path: Path, items: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(items.values(), key=lambda i: (i["published"], i["id"]))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps(i, ensure_ascii=False, sort_keys=True) + "\n" for i in ordered), encoding="utf-8")
    tmp.replace(path)


# ----------------------------------------------------------------------------------------------
# locate: turn "30th and University" into a point, using the neighborhood's own block addresses
# ----------------------------------------------------------------------------------------------

_SUFFIXES = {"st": "st", "street": "st", "ave": "ave", "av": "ave", "avenue": "ave", "blvd": "blvd",
             "bl": "blvd", "boulevard": "blvd", "dr": "dr", "drive": "dr", "pl": "pl", "place": "pl",
             "rd": "rd", "road": "rd", "way": "way", "ct": "ct", "court": "ct", "ln": "ln", "lane": "ln",
             "ter": "ter", "terrace": "ter", "pkwy": "pkwy", "parkway": "pkwy"}
_SUFFIX_RX = "(?:" + "|".join(sorted(_SUFFIXES, key=len, reverse=True)) + r")\b\.?"
M_PER_X, M_PER_Y = 0.934, 1.109     # metres per 1e-5 degree of longitude / latitude at San Diego


def street_key(name: str) -> str:
    """'University Ave' -> 'university'; '30th St' -> '30th'. SDPD's files do not agree on two
    things, which are evened out here: the dispatch log pads numbered streets ('05TH AVE' -> '5th'),
    and either file may mark a street as the South Bay one of that name ('PALM (SB) AVE' -> 'palm')."""
    name = re.sub(r"\([a-z]+\)", " ", name.lower().replace(".", ""))
    words = [re.sub(r"^0+(?=\d+(?:st|nd|rd|th)$)", "", word) for word in name.split()]
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)


class Gazetteer:
    """Street names and block points for one neighborhood, from its hood/<beat>.json places."""

    def __init__(self, places: list[list], reserved: list[str] = ()):  # [[label, x, y], ...]
        self.blocks: dict[str, list[tuple[int | None, int, int]]] = {}
        self.display: dict[str, str] = {}
        for label, x, y in places:
            if x is None or not label:
                continue
            if " & " in label:
                names, number = label.split(" & "), None
            elif m := re.match(r"^(\d+) block (.+)$", label):
                names, number = [m.group(2)], int(m.group(1))
            else:
                continue
            for name in names:
                key = street_key(name)
                if not key:
                    continue
                self.blocks.setdefault(key, []).append((number, x, y))
                self.display.setdefault(key, name)
        # A street that shares its name with the neighborhood ("North Park Way") must be written
        # with its suffix to count as a street.
        self.reserved = {p.lower() for p in reserved}
        # Streets looked for in a post's wording. "A St" and "C St" can be looked up by name, as
        # the dispatch log does, but a single letter in a sentence is not a street.
        keys = sorted((k for k, pts in self.blocks.items() if len(pts) >= 2 and len(k) >= 3), key=len, reverse=True)
        self._street = re.compile(
            r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b(?:\s+(" + _SUFFIX_RX + "))?", re.IGNORECASE
        ) if keys else None

    def _mentions(self, text: str) -> list[tuple[int, int, str]]:
        found = []
        for m in self._street.finditer(text) if self._street else ():
            key = m.group(1).lower()
            if key in self.reserved and not m.group(2):
                continue
            found.append((m.start(), m.end(), key))
        return found

    def intersection(self, a: str, b: str) -> dict | None:
        """Where two streets meet, if they really do."""
        a, b = street_key(a), street_key(b)
        best = min(((abs(xa - xb) * M_PER_X + abs(ya - yb) * M_PER_Y, xa, ya, xb, yb)
                    for _, xa, ya in self.blocks.get(a, ()) for _, xb, yb in self.blocks.get(b, ())), default=None)
        if a == b or not best or best[0] > 160:
            return None
        _, xa, ya, xb, yb = best
        return {"label": f"{self.display[a]} & {self.display[b]}", "precision": "intersection",
                "x": round((xa + xb) / 2), "y": round((ya + yb) / 2)}

    def block(self, number: int, street: str) -> dict | None:
        """The hundred-block of a street that an address number falls in."""
        key, block = street_key(street), number // 100 * 100
        for known, x, y in self.blocks.get(key, ()):
            if known == block:
                return {"label": f"{block} block {self.display[key]}", "precision": "block", "x": x, "y": y}
        return None

    def locate(self, text: str) -> dict | None:
        mentions = self._mentions(text)
        # "<street> and|&|at|/ <street>"
        for (_, end_a, a), (start_b, _, b) in zip(mentions, mentions[1:]):
            if re.fullmatch(r"\s*(?:and|&|at|near|/|x)\s*", text[end_a:start_b], re.IGNORECASE):
                if where := self.intersection(a, b):
                    return where
        # "<number> [block of] <street>"
        for start, _, key in mentions:
            if m := re.search(r"(\d{3,5})\s+(?:block\s+(?:of\s+)?)?$", text[:start], re.IGNORECASE):
                if where := self.block(int(m.group(1)), key):
                    return where
        return None


# ----------------------------------------------------------------------------------------------
# export
# ----------------------------------------------------------------------------------------------

# Words too common in crime headlines to show that two of them are about the same event.
_STOP = {"north", "park", "san", "diego", "police", "sdpd", "after", "with", "from", "that", "this", "says",
         "said", "been", "being", "were", "was", "have", "about", "what", "know", "into", "over", "near",
         "their", "they", "your", "the", "and", "for", "who", "man", "men", "woman", "women", "people",
         "person", "dies", "died", "dead", "death", "killed", "year", "old"}


def _pacific_offset(day: date) -> timedelta:
    """How far San Diego is behind UTC on a date (US daylight time: second Sunday of March to first of November)."""
    march = date(day.year, 3, 8)
    start = march + timedelta(days=(6 - march.weekday()) % 7)
    november = date(day.year, 11, 1)
    end = november + timedelta(days=(6 - november.weekday()) % 7)
    return timedelta(hours=7 if start <= day < end else 8)


def pacific_date(when: datetime) -> date:
    """UTC -> calendar date in San Diego."""
    utc = when.astimezone(timezone.utc)
    return (utc - _pacific_offset(utc.date())).date()


_SAME_WORD = {"shot": "shoot", "shots": "shoot", "struck": "hit", "one": "1", "two": "2", "three": "3", "four": "4"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]{3,}|\d+", text.lower())


def _stems(title: str, common: set[str]) -> set[str]:
    """The distinctive words of a headline, roughly normalized ('2 men shot' ~ 'two men ... shooting')."""
    return {_SAME_WORD.get(w, w)[:5] for w in _words(title) if w not in _STOP and w not in common}


def _same_story(a: dict, b: dict) -> bool:
    if a["_norm"] == b["_norm"]:                     # cross-posts and reposted headlines
        return True
    if a["source"] != "news" or b["source"] != "news":
        return False                                 # two residents' posts are two reports
    # Two headlines about the same kind of event that share distinctive words: one is enough when
    # they ran within a day of each other, two when they are further apart.
    same_kind = bool(a["_kinds"] & b["_kinds"]) or not (a["_kinds"] or b["_kinds"])
    needed = 1 if abs((a["_date"] - b["_date"]).days) <= 1 else 2
    return same_kind and len(a["_stems"] & b["_stems"]) >= needed


def group_stories(items: list[dict], common: set[str] = frozenset()) -> list[list[dict]]:
    """Group one neighborhood's items that are plainly the same event: one incident covered by
    several outlets, or one post shared to several subreddits. Each item needs `_date`, set by
    export(). `common` is the words of the neighborhood's own names, which every headline about
    it shares."""
    stories: list[dict] = []
    for item in sorted(items, key=lambda i: i["published"]):
        _, tags = score(item["title"], item.get("text", ""))
        item["_kinds"] = set(tags) - {"police", "court"}
        item["_stems"] = _stems(item["title"], common)
        item["_norm"] = re.sub(r"[^a-z0-9]+", " ", item["title"].lower()).strip()
        home = next((s for s in reversed(stories)
                     if (item["_date"] - s["last"]).days <= STORY_GAP_DAYS
                     and any(_same_story(item, other) for other in s["items"])), None)
        if home:
            home["items"].append(item)
            home["last"] = item["_date"]
        else:
            stories.append({"items": [item], "last": item["_date"]})
    # A news headline leads its story when there is one, the outlet's own copy (summary, direct
    # link) ahead of the Google News one; otherwise the earliest post.
    return [sorted(s["items"], key=lambda i: (i["source"] != "news", i["source"] == "news" and not i.get("text"),
                                              i["published"])) for s in stories]


def export(store_path: Path = config.CHATTER_STORE, out_dir: Path = config.OUT_DIR,
           hoods: list[dict] | None = None, epoch: date = date(2020, 1, 1)) -> dict[int, list[dict]]:
    """Write chatter/<beat>.json for every neighborhood: the stored items about it that read like
    a crime report, grouped into stories, newest first. Returns the stories, by beat."""
    places = Neighborhoods(built_hoods(out_dir) if hoods is None else hoods)
    if places.unknown:
        print(f"  chatter: config.py names neighborhoods SDPD's list does not have: {', '.join(sorted(set(places.unknown)))}")
    about: dict[int, list[dict]] = {beat: [] for beat in places.own}
    left_out = {outlet.lower() for outlet in config.CHATTER_SKIP_OUTLETS}
    for stored in load_store(store_path).values():
        if not reads_as_crime(stored) or stored["outlet"].lower() in left_out:
            continue
        day = pacific_date(datetime.fromisoformat(stored["published"]))
        for beat, name in places.of(stored).items():
            about[beat].append({**stored, "_date": day, "_name": name})

    written = {}
    for beat, items in about.items():
        names = places.called(beat)
        gazetteer = None
        hood_file = out_dir / "hood" / f"{beat}.json"
        if items and hood_file.exists():
            gazetteer = Gazetteer(json.loads(hood_file.read_text(encoding="utf-8"))["places"], reserved=names)

        stories = []
        for group in group_stories(items, common={word for name in names for word in _words(name)}):
            lead = group[0]
            points: dict[str, int] = {}
            for item in group:
                total, tags = score(item["title"], item.get("text", ""))
                for rank, tag in enumerate(tags):
                    points[tag] = points.get(tag, 0) + total - rank
            where = None
            if gazetteer:
                for item in group:
                    where = gazetteer.locate(item["title"] + ". " + item.get("text", ""))
                    if where:
                        break
            seen = {lead["outlet"].lower()}              # Google News spells "10News.com" both ways
            more = []
            for item in group[1:]:
                if item["outlet"].lower() not in seen:
                    seen.add(item["outlet"].lower())
                    more.append({"outlet": item["outlet"], "url": item["url"]})
            text = lead.get("text", "")
            said = [item["_name"] for item in group if item["_name"]]
            stories.append({
                "id": lead["id"],
                "kind": lead["source"],
                "date": lead["_date"].isoformat(),
                "day": (lead["_date"] - epoch).days,
                "title": lead["title"],
                "url": lead["url"],
                "outlet": lead["outlet"],
                "more": more[:8],
                "tags": sorted(points, key=lambda t: (t in ("police", "court"), -points[t]))[:3],
                "excerpt": (text[:EXCERPT].rsplit(" ", 1)[0] + "…") if len(text) > EXCERPT else text,
                "where": where,
                # the wider area the story names ("Clairemont"), when it has no name for this neighborhood alone
                "area": None if any(len(places.names[name]) == 1 for name in said) else next(iter(said), None),
            })
        stories.sort(key=lambda s: (s["date"], s["id"]), reverse=True)
        written[beat] = stories
        path = out_dir / "chatter" / f"{beat}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"beat": beat, "stories": stories}, separators=(",", ":"), ensure_ascii=False),
                        encoding="utf-8")
    return written


def collect(offline: bool = False, backfill: bool = False,
            store_path: Path = config.CHATTER_STORE, out_dir: Path = config.OUT_DIR) -> dict[int, list[dict]]:
    hoods = built_hoods(out_dir)
    store = load_store(store_path)
    before = len(store)
    if not offline:
        places = Neighborhoods(hoods)
        fetched = fetch_feeds(config.CHATTER_FEEDS, places)
        fetched += fetch_reddit(places, deep=backfill)
        news = fetch_news(places, backfill_from=config.FIRST_YEAR if backfill else None)
        print(f"    Google News: {len(news)} headlines that name a neighborhood")
        today = datetime.now(timezone.utc).date().isoformat()
        for item in fetched + news:
            item["first_seen"] = store.get(item["id"], {}).get("first_seen", today)
            store[item["id"]] = item
        save_store(store_path, store)
    written = export(store_path, out_dir, hoods)
    stories = [s for found in written.values() for s in found]         # one under several neighborhoods counts once
    print(f"  chatter: {len(store)} items stored ({len(store) - before} new), {len({s['id'] for s in stories})} relevant stories "
          f"in {sum(1 for found in written.values() if found)} of {len(written)} neighborhoods, "
          f"{len({s['id'] for s in stories if s['where']})} placed on the map")
    return written
