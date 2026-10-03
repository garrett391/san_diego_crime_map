"""Unofficial context: local news headlines and Reddit posts about the home neighborhood.

Sources (no API keys):
  * Google News RSS search
  * Reddit RSS search, one request per subreddit in config.CHATTER_SUBREDDITS

Everything fetched is appended to a small JSONL store (config.CHATTER_STORE) so items survive after
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


def mentions_place(text: str, places: list[str]) -> bool:
    for phrase in config.CHATTER_IGNORE:             # e.g. a business that carries the place's name
        text = re.sub(re.escape(phrase), " ", text, flags=re.IGNORECASE)
    return any(re.search(rf"\b{re.escape(p)}\b", text, re.IGNORECASE) for p in places)


def is_local_subreddit(name: str, places: list[str]) -> bool:
    """r/northpark is about North Park even when a post never says so."""
    squashed = re.sub(r"[^a-z0-9]", "", name.lower())
    return any(re.sub(r"[^a-z0-9]", "", p.lower()) in squashed for p in places)


def is_relevant(item: dict, places: list[str]) -> bool:
    points, _ = score(item["title"], item.get("text", ""))
    if points < MIN_SCORE:
        return False
    if item["source"] == "news":
        # Only the headline is available, so the neighborhood has to be named in it.
        return mentions_place(item["title"], places)
    if item.get("local"):
        return True
    return mentions_place(item["title"] + " " + item.get("text", ""), places)


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


def parse_reddit(xml: bytes, local: bool) -> list[dict]:
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
            "local": local,
        })
    return items


def _news_search(query: str) -> list[dict]:
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    body, _ = _get(url)
    time.sleep(1.5)
    return parse_news(body)


def fetch_news(places: list[str], backfill_from: int | None = None) -> list[dict]:
    """Recent headlines, plus (with backfill_from) one search per half-year since that year.

    Google returns at most 100 results a search and far fewer for old date ranges (about ten per
    half-year for one neighborhood), so the backfill recovers the notable events, not everything.
    """
    items = []
    for place in places:
        items += _news_search(f'"{place}" "San Diego" ({NEWS_TERMS})')
        items += _news_search(f'intitle:"{place}" "San Diego" ({NEWS_TERMS})')
        if backfill_from:
            start = date(backfill_from, 1, 1)
            while start <= date.today():
                end = date(start.year, 7, 1) if start.month == 1 else date(start.year + 1, 1, 1)
                items += _news_search(f'intitle:"{place}" "San Diego" ({NEWS_TERMS}) '
                                      f"after:{start.isoformat()} before:{end.isoformat()}")
                start = end
    return list({i["id"]: i for i in items}.values())


def fetch_reddit(subreddits: list[str], places: list[str], log=print) -> list[dict]:
    """One search per subreddit. Reddit allows unauthenticated readers very few requests a minute,
    so this waits out the limit it reports between calls."""
    items = []
    wait = 0.0
    for sub in subreddits:
        local = is_local_subreddit(sub, places)
        place_part = "" if local else "(" + " OR ".join(f'"{p}"' for p in places) + ") "
        url = f"https://www.reddit.com/r/{urllib.parse.quote(sub)}/search.rss?" + urllib.parse.urlencode(
            {"q": f"{place_part}({REDDIT_TERMS})", "restrict_sr": 1, "sort": "new", "limit": 100})
        if wait:
            log(f"    waiting {wait:.0f}s for Reddit's rate limit...")
            time.sleep(wait)
        try:
            body, headers = _get(url)
        except urllib.error.HTTPError as e:
            log(f"    r/{sub}: skipped (HTTP {e.code})")
            wait = min(float(e.headers.get("retry-after") or e.headers.get("x-ratelimit-reset") or 30), 90) + 1
            continue
        found = parse_reddit(body, local)
        log(f"    r/{sub}: {len(found)} posts" + (" (neighborhood subreddit)" if local else ""))
        items += found
        try:
            remaining = float(headers.get("x-ratelimit-remaining", 1))
            wait = min(float(headers.get("x-ratelimit-reset", 0)), 90) + 1 if remaining < 1 else 0.0
        except ValueError:
            wait = 5.0
    return items


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
    """'University Ave' -> 'university'; '30th St' -> '30th'."""
    words = name.lower().replace(".", "").split()
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
                if len(key) < 3:
                    continue
                self.blocks.setdefault(key, []).append((number, x, y))
                self.display.setdefault(key, name)
        # A street that shares its name with the neighborhood ("North Park Way") must be written
        # with its suffix to count as a street.
        self.reserved = {p.lower() for p in reserved}
        keys = sorted((k for k, pts in self.blocks.items() if len(pts) >= 2), key=len, reverse=True)
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

    def locate(self, text: str) -> dict | None:
        mentions = self._mentions(text)
        # "<street> and|&|at|/ <street>" where the two streets really do meet
        for (_, end_a, a), (start_b, _, b) in zip(mentions, mentions[1:]):
            if a != b and re.fullmatch(r"\s*(?:and|&|at|near|/|x)\s*", text[end_a:start_b], re.IGNORECASE):
                best = min(((abs(xa - xb) * M_PER_X + abs(ya - yb) * M_PER_Y, xa, ya, xb, yb)
                            for _, xa, ya in self.blocks[a] for _, xb, yb in self.blocks[b]), default=None)
                if best and best[0] <= 160:
                    _, xa, ya, xb, yb = best
                    return {"label": f"{self.display[a]} & {self.display[b]}", "precision": "intersection",
                            "x": round((xa + xb) / 2), "y": round((ya + yb) / 2)}
        # "<number> [block of] <street>"
        for start, _, key in mentions:
            if m := re.search(r"(\d{3,5})\s+(?:block\s+(?:of\s+)?)?$", text[:start], re.IGNORECASE):
                block = int(m.group(1)) // 100 * 100
                for number, x, y in self.blocks[key]:
                    if number == block:
                        return {"label": f"{block} block {self.display[key]}", "precision": "block", "x": x, "y": y}
        return None


# ----------------------------------------------------------------------------------------------
# export
# ----------------------------------------------------------------------------------------------

# Words too common in crime headlines to show that two of them are about the same event.
_STOP = {"north", "park", "san", "diego", "police", "sdpd", "after", "with", "from", "that", "this", "says",
         "said", "been", "being", "were", "was", "have", "about", "what", "know", "into", "over", "near",
         "their", "they", "your", "the", "and", "for", "who", "man", "men", "woman", "women", "people",
         "person", "dies", "died", "dead", "death", "killed", "year", "old"}


def pacific_date(when: datetime) -> date:
    """UTC -> calendar date in San Diego (US daylight time: second Sunday of March to first of November)."""
    utc = when.astimezone(timezone.utc)
    march = date(utc.year, 3, 8)
    start = march + timedelta(days=(6 - march.weekday()) % 7)
    november = date(utc.year, 11, 1)
    end = november + timedelta(days=(6 - november.weekday()) % 7)
    offset = 7 if start <= utc.date() < end else 8
    return (utc - timedelta(hours=offset)).date()


_SAME_WORD = {"shot": "shoot", "shots": "shoot", "struck": "hit", "one": "1", "two": "2", "three": "3", "four": "4"}


def _stems(title: str) -> set[str]:
    """The distinctive words of a headline, roughly normalized ('2 men shot' ~ 'two men ... shooting')."""
    words = re.findall(r"[a-z]{3,}|\d+", title.lower())
    return {_SAME_WORD.get(w, w)[:5] for w in words if w not in _STOP}


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


def group_stories(items: list[dict]) -> list[list[dict]]:
    """Group items that are plainly the same event: one incident covered by several outlets, or one
    post shared to several subreddits. Each item needs `_date`, set by export()."""
    stories: list[dict] = []
    for item in sorted(items, key=lambda i: i["published"]):
        _, tags = score(item["title"], item.get("text", ""))
        item["_kinds"] = set(tags) - {"police", "court"}
        item["_stems"] = _stems(item["title"])
        item["_norm"] = re.sub(r"[^a-z0-9]+", " ", item["title"].lower()).strip()
        home = next((s for s in reversed(stories)
                     if (item["_date"] - s["last"]).days <= STORY_GAP_DAYS
                     and any(_same_story(item, other) for other in s["items"])), None)
        if home:
            home["items"].append(item)
            home["last"] = item["_date"]
        else:
            stories.append({"items": [item], "last": item["_date"]})
    # A news headline leads its story when there is one; otherwise the earliest post.
    return [sorted(s["items"], key=lambda i: (i["source"] != "news", i["published"])) for s in stories]


def export(store_path: Path = config.CHATTER_STORE, out_dir: Path = config.OUT_DIR,
           places: list[str] | None = None, home_beat: int = config.HOME_BEAT, epoch: date = date(2020, 1, 1)) -> dict:
    places = places or config.CHATTER_PLACES
    store = load_store(store_path)
    relevant = [dict(i) for i in store.values() if is_relevant(i, places)]
    for item in relevant:
        item["_date"] = pacific_date(datetime.fromisoformat(item["published"]))

    gazetteer = None
    hood_file = out_dir / "hood" / f"{home_beat}.json"
    if hood_file.exists():
        gazetteer = Gazetteer(json.loads(hood_file.read_text(encoding="utf-8"))["places"], reserved=places)

    stories = []
    for group in group_stories(relevant):
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
        seen = {lead["outlet"]}
        more = []
        for item in group[1:]:
            if item["outlet"] not in seen:
                seen.add(item["outlet"])
                more.append({"outlet": item["outlet"], "url": item["url"]})
        text = lead.get("text", "")
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
        })
    stories.sort(key=lambda s: (s["date"], s["id"]), reverse=True)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "places": places,
        "stored": len(store),
        "stories": stories,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "chatter.json").write_text(json.dumps(result, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return result


def collect(offline: bool = False, backfill: bool = False,
            store_path: Path = config.CHATTER_STORE, out_dir: Path = config.OUT_DIR) -> dict:
    places = config.CHATTER_PLACES
    store = load_store(store_path)
    before = len(store)
    if not offline:
        fetched: list[dict] = []
        try:
            news = fetch_news(places, backfill_from=config.FIRST_YEAR if backfill else None)
            print(f"    Google News: {len(news)} headlines")
            fetched += news
        except (urllib.error.URLError, ET.ParseError, TimeoutError) as e:
            print(f"    Google News: skipped ({e})")
        try:
            fetched += fetch_reddit(config.CHATTER_SUBREDDITS, places)
        except (urllib.error.URLError, ET.ParseError, TimeoutError) as e:
            print(f"    Reddit: stopped early ({e})")
        today = datetime.now(timezone.utc).date().isoformat()
        for item in fetched:
            item["first_seen"] = store.get(item["id"], {}).get("first_seen", today)
            store[item["id"]] = item
        save_store(store_path, store)
    result = export(store_path, out_dir, places)
    located = sum(1 for s in result["stories"] if s["where"])
    print(f"  chatter: {len(store)} items stored ({len(store) - before} new), "
          f"{len(result['stories'])} relevant stories, {located} placed on the map")
    return result
