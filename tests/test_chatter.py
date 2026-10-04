"""The chatter filter is keyword rules, so the tests are real posts and headlines it must get right."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
from datetime import date, datetime, timezone

import pytest

from pipeline import chatter as ch

PLACES = ["North Park"]
HOODS = [{"beat": 813, "name": "North Park"}, {"beat": 122, "name": "Pacific Beach"}, {"beat": 113, "name": "Bay Ho"},
         {"beat": 116, "name": "Bay Park"}, {"beat": 714, "name": "Border"}, {"beat": 821, "name": "Rolando"},
         {"beat": 841, "name": "Rolando Park"}]


@pytest.fixture
def places(monkeypatch):
    """The neighborhoods above, with settings of the test's own instead of the ones in config.py."""
    monkeypatch.setattr(ch.config, "CHATTER_ALIASES", {"Clairemont": ["Bay Ho", "Bay Park"], "PB": ["Pacific Beach"]})
    monkeypatch.setattr(ch.config, "CHATTER_SKIP", ["Border"])
    monkeypatch.setattr(ch.config, "CHATTER_IGNORE", ["North Park Produce"])
    monkeypatch.setattr(ch.config, "CHATTER_SKIP_OUTLETS", ["Hoodline"])
    monkeypatch.setattr(ch.config, "CHATTER_SUBREDDITS",
                        {"sandiego": None, "northpark": "North Park", "clairemont": "Clairemont"})
    return ch.Neighborhoods(HOODS)

# (title, text, tag it should lead with)
REPORTS = [
    ("San Diego police: 2 men shot after leaving North Park bar", "", "violence"),
    ("Stolen Bike", "My bike was stolen out of my building's garage in North Park", "theft"),
    ("Truck broken into in north park", "Just got back from a show and my truck was broken into.", "vehicle"),
    ("Truck break in 4400 block of Utah street in north park.", "", "vehicle"),
    ("Police helicopter circling North Park?", "Helo is circling above the intersection of Park and University", "police"),
    ("What's going on?", "I'm in north Park and the ambulance sirens were going nuts, the police cars were honking", "police"),
    ("Did anyone else hear gun shots last night near University Heights/North Park?", "", "violence"),
    ("Heckler assaults comic on stage at Queen Bee in North Park", "", "violence"),
    ("Man accused of groping, flashing women in North Park, PB charged", "", "harassment"),
    ("70-year-old pedestrian dies after being hit by vehicle in North Park", "", "traffic"),
    ("$2,000 Reward Offered in Pellet-Gun Shootings in Hillcrest, North Park", "", "violence"),
]

NOT_REPORTS = [
    ("Soccer/Open goals at parks?", "Any public parks with big nets for shooting around on? I'm in north park"),
    ("(4) Tickets for Citizen @ The Observatory North Park 10/25", "I have 4 tickets I'm willing to sell at cost."),
    ("San Diego Pride office in North Park currently taking walk-in vaccinations!", "Get your shot today"),
    ("North Park cat disappears; owner believes hawk attacked him", ""),
    ("Guitarist looking for people to jam with", "North Park based. Give it a shot if you play drums."),
    ("Two military helicopters circling low around City Heights / North Park?", ""),
    ("Co-Working Space or Quiet Coffee Shops to work at", "Options to work remote in the North Park area"),
    ("Daylighting curbs still not painted and meter maids giving tickets every day", "In north park lately"),
    ("Mark Zuckerberg's Meta Company Shooting Commercial In San Diego's North Park", ""),
]


@pytest.mark.parametrize("title,text,lead", REPORTS)
def test_reports_are_kept(title, text, lead):
    points, tags = ch.score(title, text)
    assert points >= ch.MIN_SCORE
    assert tags[0] == lead


@pytest.mark.parametrize("title,text", NOT_REPORTS)
def test_lookalikes_are_dropped(title, text):
    points, _ = ch.score(title, text)
    assert points < ch.MIN_SCORE


def test_a_traffic_death_is_not_tagged_as_violence():
    _, tags = ch.score("Man Killed in Pedestrian Accident on El Cajon Boulevard in North Park")
    assert "traffic" in tags and "violence" not in tags
    _, tags = ch.score("Woman arrested in North Park hit-and-run after shooting at driver")
    assert "traffic" in tags and "violence" in tags


def item(source, title, text="", outlet=None, published="2026-08-13T15:00:00+00:00"):
    return {"id": f"{source}:{outlet}:{title}:{published}", "source": source, "outlet": outlet or source,
            "title": title, "url": "https://example.test/x", "published": published, "text": text}


def test_where_the_neighborhood_has_to_be_named(places):
    # news: only the headline is known, so it must name the place
    assert places.of(item("news", "Two men injured in early morning North Park shooting")) == {813: "North Park"}
    assert not places.of(item("news", "Two men injured in early morning shooting"))
    # a citywide subreddit: the place can be in the body, and it decides where the post is listed
    assert places.of(item("reddit", "Stolen Bike", "taken from my garage in north park", outlet="r/sandiego")) == {813: "North Park"}
    assert places.of(item("reddit", "Stolen Bike", "taken from my garage in Pacific Beach", outlet="r/sandiego")) == {122: "Pacific Beach"}
    assert not places.of(item("reddit", "Stolen Bike", "taken from my garage", outlet="r/sandiego"))
    # a neighborhood's own subreddit: every post is about it
    assert places.of(item("reddit", "Stolen Bike", "taken from my garage", outlet="r/northpark")) == {813: ""}
    # a business that carries the name is not the place
    assert not places.of(item("news", "Car crashes into North Park Produce grocery store in Poway"))


def test_the_names_a_neighborhood_goes_by(places):
    # a name people use for a wider area covers each of SDPD's neighborhoods in it
    assert places.named("Car stolen in Clairemont overnight") == {113: "Clairemont", 116: "Clairemont"}
    assert places.called(113) == ["Bay Ho", "Clairemont"]
    # SDPD's own name is the one recorded when the text gives both
    assert places.named("Clairemont: break-ins near Bay Park") == {113: "Clairemont", 116: "Bay Park"}
    # of two names that start alike, the longer is the one meant
    assert set(places.named("Shots fired in Rolando Park")) == {841}
    assert set(places.named("Shots fired in Rolando")) == {821}
    # a name that is an ordinary word is not looked for
    assert not places.named("Two arrested at the border")
    # a subreddit can be about a wider area, and Reddit's spelling of its name does not matter
    assert places.of(item("reddit", "Stolen bike", outlet="r/Clairemont")) == {113: "", 116: ""}
    assert not places.unknown


def test_settings_that_name_no_known_neighborhood_are_reported(monkeypatch, places):
    monkeypatch.setattr(ch.config, "CHATTER_ALIASES", {"Clairemont": ["Bay Ho", "Bay Hoe"], "Uptown": ["Hillcrest"]})
    monkeypatch.setattr(ch.config, "CHATTER_SUBREDDITS", {"OBcean": "Ocean Beach"})
    places = ch.Neighborhoods(HOODS)
    assert places.unknown == ["Bay Hoe", "Hillcrest", "Ocean Beach"]
    assert places.named("Clairemont car theft") == {113: "Clairemont"} and "Uptown" not in places.names


def test_usernames_are_stripped():
    raw = ('&lt;div class="md"&gt;&lt;p&gt;My bike was stolen &amp;amp; I&#39;m sad&lt;/p&gt;&lt;/div&gt; &#32; submitted by &#32; '
           '&lt;a href="https://www.reddit.com/user/someone"&gt; /u/someone &lt;/a&gt; &lt;span&gt;[link]&lt;/span&gt; [comments]')
    assert ch.clean_text(raw) == "My bike was stolen & I'm sad"


REDDIT_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <author><name>/u/someone</name></author>
    <category term="sandiego" label="r/sandiego"/>
    <content type="html">&lt;p&gt;Truck was broken into on Ohio St.&lt;/p&gt; submitted by &lt;a&gt; /u/someone &lt;/a&gt;</content>
    <id>t3_abc123</id>
    <link href="https://www.reddit.com/r/sandiego/comments/abc123/truck/"/>
    <published>2025-03-08T07:12:00+00:00</published>
    <title>Truck broken into in north park</title>
  </entry>
</feed>"""

NEWS_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>2 Men Shot After Leaving North Park Bar - Patch</title>
    <link>https://news.google.com/rss/articles/xyz</link>
    <guid isPermaLink="false">xyz</guid>
    <pubDate>Thu, 13 Aug 2026 15:04:05 GMT</pubDate>
    <source url="https://patch.com">Patch</source>
  </item>
</channel></rss>"""


def test_feed_parsing():
    (post,) = ch.parse_reddit(REDDIT_FEED)
    assert post["id"] == "reddit:t3_abc123" and post["outlet"] == "r/sandiego"
    assert post["text"] == "Truck was broken into on Ohio St."
    assert "someone" not in json.dumps(post)
    (story,) = ch.parse_news(NEWS_FEED)
    assert story["title"] == "2 Men Shot After Leaving North Park Bar"      # outlet suffix removed
    assert story["outlet"] == "Patch" and story["published"].startswith("2026-08-13T15:04:05")


OUTLET_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:media="http://search.yahoo.com/mrss/">
<channel>
  <title>Local</title>
  <item>
    <title>Man stabbed during fight outside bar</title>
    <link>https://example.test/stabbing</link>
    <guid>4080707</guid>
    <media:content url="https://example.test/photo.jpg"/>
    <description>&lt;p&gt;Police say the stabbing happened in &lt;b&gt;North Park&lt;/b&gt; early Sunday.&lt;/p&gt;</description>
    <pubDate>Thu, Oct 01 2026 11:49:34 PM</pubDate>
  </item>
  <item>
    <title>Council approves budget</title>
    <link>https://example.test/budget</link>
    <description></description>
    <content:encoded>&lt;p&gt;The vote was 7-2.&lt;/p&gt;</content:encoded>
    <pubDate>Sat, 03 Oct 2026 19:05:25 GMT</pubDate>
  </item>
  <item>
    <title>No date, so it is skipped</title>
    <link>https://example.test/undated</link>
  </item>
</channel></rss>"""

ATOM_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Car stolen from driveway</title>
    <link href="https://example.test/car"/>
    <id>tag:example.test,2026:car</id>
    <updated>2026-10-01T15:49:34-07:00</updated>
    <summary>It happened in North Park.</summary>
  </entry>
</feed>"""


def test_outlet_feed_parsing():
    stabbing, budget = ch.parse_feed(OUTLET_FEED, "NBC 7 San Diego")
    assert stabbing["id"] == "feed:NBC 7 San Diego:4080707" and stabbing["source"] == "news"
    assert stabbing["outlet"] == "NBC 7 San Diego" and stabbing["url"] == "https://example.test/stabbing"
    assert stabbing["text"] == "Police say the stabbing happened in North Park early Sunday."
    # NBC writes local time with AM/PM and no zone: 11:49 pm on Oct 1 in San Diego is Oct 2 in UTC
    assert stabbing["published"] == "2026-10-02T06:49:34+00:00"
    assert ch.pacific_date(datetime.fromisoformat(stabbing["published"])) == date(2026, 10, 1)
    # no guid: the link identifies the article; no description: the article body stands in
    assert budget["id"] == "feed:NBC 7 San Diego:https://example.test/budget"
    assert budget["text"] == "The vote was 7-2." and budget["published"] == "2026-10-03T19:05:25+00:00"
    (car,) = ch.parse_feed(ATOM_FEED, "Example")
    assert car["url"] == "https://example.test/car" and car["published"] == "2026-10-01T22:49:34+00:00"
    assert car["text"] == "It happened in North Park."


def test_an_outlets_summary_can_name_the_neighborhood(places):
    title = "Man stabbed during fight outside bar"
    stabbing = item("news", title, "Police say the stabbing happened in North Park early Sunday.")
    assert ch.reads_as_crime(stabbing) and set(places.of(stabbing)) == {813}
    assert set(places.of(item("news", title, "Police say the stabbing happened in Pacific Beach."))) == {122}
    # the crime still has to be in the headline: a passing mention deep in an article does not count
    assert not ch.reads_as_crime(item("news", "Council approves North Park bike lanes", "One speaker said her bike was stolen."))
    # a resident's post can say it anywhere
    assert ch.reads_as_crime(item("reddit", "What happened last night?", "My truck was broken into on Utah St."))


def test_a_passing_mention_far_into_an_article_does_not_place_it(places):
    # some feeds carry the whole article, with links to other stories at the end
    opening = "A man was sentenced Friday for a card skimming scheme, prosecutors said. " * 6
    story = item("news", "Man sentenced for stealing benefits", opening + "Related: Pacific Beach market reopens.")
    assert len(opening) > ch.NEWS_LEAD and not places.of(story)
    # a resident's post can name the place anywhere
    assert set(places.of(item("reddit", "Stolen bike", opening + "This was in Pacific Beach.", outlet="r/sandiego"))) == {122}


CAFE_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>New cafe opens</title>
    <link>https://example.test/cafe</link>
    <description>It is in North Park.</description>
    <pubDate>Sat, 03 Oct 2026 19:05:25 GMT</pubDate>
  </item>
</channel></rss>"""


def test_fetch_feeds_keeps_crime_headlines_that_name_a_neighborhood_and_survives_a_dead_feed(monkeypatch, places):
    def fake_get(url):
        if "dead" in url:
            raise urllib.error.URLError("no route")
        return (CAFE_FEED if "eater" in url else OUTLET_FEED), {}
    monkeypatch.setattr(ch, "_get", fake_get)
    log = []
    feeds = [("NBC 7 San Diego", "https://example.test/feed"), ("Gone", "https://dead.test/feed"), ("Eater", "https://eater.test/feed")]
    items = ch.fetch_feeds(feeds, places, log=log.append)
    assert [i["title"] for i in items] == ["Man stabbed during fight outside bar"]
    assert any("Gone: skipped" in line for line in log)
    assert any("Eater: 0 of 1 articles" in line for line in log)        # names North Park, but is not about a crime


def test_news_is_searched_by_name_and_a_run_google_cuts_short_keeps_what_it_has(monkeypatch, places):
    asked = []

    def fake_search(query):
        asked.append(query)
        if len(asked) > 2:
            raise urllib.error.HTTPError("https://example.test", 429, "Too Many Requests", {}, None)
        return [item("news", "Man stabbed in North Park"), item("news", "Padres win in extra innings")]
    monkeypatch.setattr(ch, "_news_search", fake_search)
    log = []
    found = ch.fetch_news(places, log=log.append)
    assert [i["title"] for i in found] == ["Man stabbed in North Park"]       # the other headline names no neighborhood
    assert len(asked) == 5 and all(q.startswith('intitle:"') and '"San Diego"' in q for q in asked)
    assert any(f"stopped after three failed searches in a row, {len(places.names) - 5} names not reached" in line for line in log)


def test_names_that_are_ordinary_words_are_not_searched_for(monkeypatch, places):
    asked = []
    monkeypatch.setattr(ch, "_news_search", lambda query: asked.append(query) or [])
    assert ch.fetch_news(places, log=lambda line: None) == []
    assert sorted(q.split('"')[1] for q in asked) == ["Bay Ho", "Bay Park", "Clairemont", "North Park", "PB", "Pacific Beach",
                                                      "Rolando", "Rolando Park"]


def test_backfill_digs_only_where_the_first_search_came_back_full(monkeypatch, places):
    asked = []

    def fake_search(query):
        asked.append(query)
        full = 'intitle:"Pacific Beach"' in query and "after:" not in query
        return [item("news", f"Pacific Beach stabbing, number {n}") for n in range(ch.NEWS_FULL)] if full else []
    monkeypatch.setattr(ch, "_news_search", fake_search)
    found = ch.fetch_news(places, backfill_from=date.today().year, log=lambda line: None)
    by_half_year = [q for q in asked if "after:" in q]
    assert by_half_year and all('intitle:"Pacific Beach"' in q for q in by_half_year)
    assert len(asked) == len(places.names) + len(by_half_year) and len(found) == ch.NEWS_FULL


def test_name_queries_stay_within_the_room_given():
    assert ch.name_queries(["Bay Ho", "Bay Park", "North Park", "Pacific Beach"], room=25) == [
        '"Bay Ho" OR "Bay Park"', '"North Park"', '"Pacific Beach"']
    assert ch.name_queries([], room=25) == []


def reddit_searches(monkeypatch, places, **options):
    """Run fetch_reddit against a canned feed. Returns [(subreddits, query)] as asked, and the posts kept."""
    asked = []

    def fake_get(url):
        address = urllib.parse.urlsplit(url)
        asked.append((address.path.split("/")[2], urllib.parse.parse_qs(address.query)["q"][0]))
        return REDDIT_FEED, {"x-ratelimit-remaining": "5"}
    monkeypatch.setattr(ch, "_get", fake_get)
    return asked, ch.fetch_reddit(places, log=lambda line: None, **options)


def test_reddit_is_searched_once_per_citywide_subreddit_and_once_for_all_the_neighborhood_ones(monkeypatch, places):
    asked, found = reddit_searches(monkeypatch, places)
    assert [subreddits for subreddits, _ in asked] == ["sandiego", "northpark+clairemont"]
    assert all(query == f"({ch.REDDIT_TERMS})" for _, query in asked)
    assert [i["title"] for i in found] == ["Truck broken into in north park"]      # returned by both searches, kept once


def test_a_citywide_post_is_kept_only_if_it_names_a_neighborhood(monkeypatch, places):
    monkeypatch.setattr(ch.config, "CHATTER_SKIP", ["Border", "North Park"])
    asked, found = reddit_searches(monkeypatch, ch.Neighborhoods(HOODS))
    assert len(asked) == 2 and found == []


def reddit_feed(count, start=0):
    entries = "".join(
        f'<entry><category term="sandiego"/><content type="html">It was in North Park.</content><id>t3_{n}</id>'
        f'<link href="https://example.test/{n}"/><published>2025-03-08T07:12:00+00:00</published>'
        f"<title>Stolen bike {n}</title></entry>" for n in range(start, start + count))
    return f'<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'.encode()


def test_a_deep_reddit_search_is_followed_to_its_end_and_an_ordinary_one_reads_one_page(monkeypatch, places):
    monkeypatch.setattr(ch.config, "CHATTER_SUBREDDITS", {"sandiego": None})
    monkeypatch.setattr(ch, "name_queries", lambda names, room: [])        # leave only the search every run makes
    afters = []

    def fake_get(url):
        afters.append(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("after"))
        return (reddit_feed(30, start=100) if afters[-1] else reddit_feed(100)), {"x-ratelimit-remaining": "5"}
    monkeypatch.setattr(ch, "_get", fake_get)
    assert len(ch.fetch_reddit(places, deep=True, log=lambda line: None)) == 130
    assert afters == [None, ["t3_99"]]                                     # the second page starts after the last post of the first
    assert len(ch.fetch_reddit(places, log=lambda line: None)) == 100 and afters[2:] == [None]


def test_a_subreddit_reddit_turns_away_is_skipped(monkeypatch, places):
    waits = []

    def fake_get(url):
        if "/r/sandiego/" in url:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {"x-ratelimit-reset": "12"}, None)
        return REDDIT_FEED, {"x-ratelimit-remaining": "0.0", "x-ratelimit-reset": "31"}
    monkeypatch.setattr(ch, "_get", fake_get)
    monkeypatch.setattr(ch.time, "sleep", waits.append)
    log = []
    found = ch.fetch_reddit(places, log=log.append)
    assert [i["title"] for i in found] == ["Truck broken into in north park"]
    assert waits == [13.0] and any("r/sandiego: skipped (HTTP 403)" in line for line in log)


def test_a_deep_reddit_search_also_asks_for_the_names_in_searches_reddit_accepts(monkeypatch, places):
    monkeypatch.setattr(ch, "REDDIT_QUERY", len(ch.REDDIT_TERMS) + 45)
    asked, _ = reddit_searches(monkeypatch, places, deep=True)
    by_name = [query for _, query in asked[2:]]
    assert {subreddits for subreddits, _ in asked[2:]} == {"sandiego"} and len(by_name) > 1
    assert all(len(query) <= ch.REDDIT_QUERY and query.endswith(f"({ch.REDDIT_TERMS})") for query in by_name)
    assert all(f'"{name}"' in " ".join(by_name) for name in places.names)


def test_a_search_by_name_reads_one_page_even_when_there_is_more(monkeypatch, places):
    monkeypatch.setattr(ch.config, "CHATTER_SUBREDDITS", {"sandiego": None})
    asked = []

    def fake_get(url):
        asked.append(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query))
        return reddit_feed(100, start=100 * len(asked)), {"x-ratelimit-remaining": "5"}
    monkeypatch.setattr(ch, "_get", fake_get)
    ch.fetch_reddit(places, deep=True, log=lambda line: None)
    plain = [q for q in asked if q["q"] == [f"({ch.REDDIT_TERMS})"]]
    assert len(plain) == ch.REDDIT_PAGES and len(asked) == ch.REDDIT_PAGES + 1 and "after" not in asked[-1]


GAZETTEER = ch.Gazetteer([
    ["3000 block University Ave", -11713000, 3274850], ["3100 block University Ave", -11712800, 3274850],
    ["3900 block 30th St", -11713010, 3274900], ["4000 block 30th St", -11713010, 3275050],
    ["4300 block Utah St", -11713300, 3275550], ["4400 block Utah St", -11713300, 3275700],
    ["3200 block North Park Way", -11712600, 3274700], ["3300 block North Park Way", -11712400, 3274700],
    ["3800 block 32nd St", -11712500, 3274650], ["3900 block 32nd St", -11712500, 3274800],
    ["100 block Main St", None, None],
], reserved=PLACES)


def test_locating_an_intersection():
    where = GAZETTEER.locate("Several police cars are blocking the area around 30th and University")
    assert where == {"label": "30th St & University Ave", "precision": "intersection", "x": -11713005, "y": 3274875}
    assert GAZETTEER.locate("Big police presence on University/30th right now")["precision"] == "intersection"


def test_locating_a_block():
    where = GAZETTEER.locate("Truck break in 4400 block of Utah street in north park.")
    assert where == {"label": "4400 block Utah St", "precision": "block", "x": -11713300, "y": 3275700}


def test_the_neighborhood_name_is_not_a_street():
    assert GAZETTEER.locate("Lots of cops in North Park tonight near the park") is None
    where = GAZETTEER.locate("ICE operation gathering at North Park Way and 32nd in North Park")
    assert where["label"] == "North Park Way & 32nd St"


def test_streets_that_do_not_meet_are_not_an_intersection():
    assert GAZETTEER.locate("somewhere between Utah and 32nd I think") is None


def test_street_key():
    assert ch.street_key("University Ave") == "university"
    assert ch.street_key("30th St") == "30th"
    assert ch.street_key("El Cajon Blvd") == "el cajon"
    assert ch.street_key("05TH AVE") == "5th" and ch.street_key("10th Ave") == "10th"      # the dispatch log pads with a zero
    assert ch.street_key("PALM (SB) AVE") == "palm" and ch.street_key("27th (sb) St") == "27th"


def test_a_single_letter_street_can_be_looked_up_but_is_not_read_out_of_a_sentence():
    downtown = ch.Gazetteer([["400 block C St", -11716050, 3271700], ["500 block C St", -11715950, 3271700],
                             ["1100 block 5th Ave", -11716000, 3271690], ["1200 block 5th Ave", -11716000, 3271800]])
    assert downtown.intersection("C ST", "05TH AVE")["label"] == "C St & 5th Ave"
    assert downtown.block(1150, "05TH AVE")["label"] == "1100 block 5th Ave"
    assert downtown.block(400, "C ST")["label"] == "400 block C St"
    assert downtown.locate("We went with plan C and 5th graders loved it") is None


def dated(it):
    it["_date"] = ch.pacific_date(datetime.fromisoformat(it["published"]))
    return it


def test_grouping_stories():
    items = [dated(i) for i in [
        # one post shared to three subreddits
        item("reddit", "Stolen Bike a few weeks ago", outlet="r/sandiego", published="2026-07-07T18:00:00+00:00"),
        item("reddit", "Stolen bike a few weeks ago", outlet="r/SanDiegan", published="2026-07-07T18:05:00+00:00"),
        item("reddit", "Stolen Bike a few weeks ago", outlet="r/northpark", published="2026-07-07T18:06:00+00:00"),
        # one shooting, worded differently by two outlets, and reposted to Reddit under the first headline
        item("news", "2 Men Shot After Leaving North Park Bar", outlet="Patch", published="2026-08-13T15:00:00+00:00"),
        item("news", "Two men injured in early morning North Park shooting", outlet="fox5", published="2026-08-13T16:00:00+00:00"),
        item("reddit", "2 Men Shot After Leaving North Park Bar", outlet="r/sandiego", published="2026-08-13T14:00:00+00:00"),
        # two different events three days apart
        item("news", "70-year-old pedestrian dies after being hit by vehicle in North Park", outlet="UT", published="2026-09-28T20:00:00+00:00"),
        item("news", "Appeals panel overturns felony murder conviction in case of man killed in North Park dispute over hair",
             outlet="Times", published="2026-10-01T20:00:00+00:00"),
        # two residents, two thefts
        item("reddit", "Stolen Bike", outlet="r/sandiego", published="2025-10-08T18:00:00+00:00"),
        item("reddit", "Stolen Cannondale Topstone Bike", outlet="r/sandiego", published="2025-10-09T18:00:00+00:00"),
    ]]
    stories = ch.group_stories(items)
    sizes = sorted(len(s) for s in stories)
    assert sizes == [1, 1, 1, 1, 3, 3]
    shooting = next(s for s in stories if any("Shot" in i["title"] for i in s))
    assert {i["outlet"] for i in shooting} == {"Patch", "fox5", "r/sandiego"}
    assert shooting[0]["source"] == "news"            # a headline leads even though the repost came first


def test_an_outlets_own_copy_leads_its_story():
    title = "2 Men Shot After Leaving North Park Bar"
    google = dated(item("news", title, outlet="NBC 7 San Diego", published="2026-08-13T15:00:00+00:00"))
    own = dated(item("news", title, "Two men were shot early Thursday.", outlet="NBC 7 San Diego", published="2026-08-13T15:05:00+00:00"))
    (story,) = ch.group_stories([google, own])
    assert story[0] is own                            # it has the summary and the direct link


def test_pacific_date():
    assert ch.pacific_date(datetime(2026, 7, 1, 6, 30, tzinfo=timezone.utc)) == date(2026, 6, 30)     # PDT, UTC-7
    assert ch.pacific_date(datetime(2026, 1, 1, 7, 30, tzinfo=timezone.utc)) == date(2025, 12, 31)    # PST, UTC-8
    assert ch.pacific_date(datetime(2026, 7, 1, 7, 30, tzinfo=timezone.utc)) == date(2026, 7, 1)


def test_a_neighborhoods_own_name_does_not_make_two_events_one():
    # the same day and the same kind of crime, sharing no words but the neighborhood's
    items = [dated(i) for i in [
        item("news", "Man stabbed outside Pacific Beach bar", outlet="Patch", published="2026-08-13T15:00:00+00:00"),
        item("news", "Pacific Beach robbery suspect sought", outlet="fox5", published="2026-08-13T18:00:00+00:00"),
    ]]
    assert len(ch.group_stories(items, common={"pacific", "beach"})) == 2
    assert len(ch.group_stories(items)) == 1          # what would happen if those words counted


def test_export_writes_a_clean_feed_for_each_neighborhood(tmp_path, places):
    store = tmp_path / "items.jsonl"
    long_text = "My truck was broken into at 30th and University in North Park. " + "More details here. " * 30
    ch.save_store(store, {i["id"]: i for i in [
        item("reddit", "Truck broken into", long_text, outlet="r/sandiego", published="2025-03-08T07:12:00+00:00"),
        item("reddit", "Best tacos in North Park?", "Looking for recommendations", outlet="r/sandiego"),
        item("news", "2 Men Shot After Leaving North Park Bar", outlet="Patch"),
        item("news", "Two men injured in early morning North Park shooting", outlet="fox5"),
        item("news", "Clairemont break-in suspect arrested", outlet="Patch"),
        item("news", "Man stabbed outside bar", outlet="Patch"),
        item("news", "Pacific Beach Shooting Sends Man to Hospital", outlet="hoodline"),
        item("news", "Stabbing outside PB bar", outlet="Patch"),
    ]})
    out = tmp_path / "site-data"
    (out / "hood").mkdir(parents=True)
    (out / "hood" / "813.json").write_text(json.dumps({"places": [
        ["3000 block University Ave", -11713000, 3274850], ["3100 block University Ave", -11712800, 3274850],
        ["3900 block 30th St", -11713010, 3274900], ["4000 block 30th St", -11713010, 3275050],
    ]}), encoding="utf-8")

    result = ch.export(store, out, HOODS)
    written = {h["beat"]: json.loads((out / "chatter" / f"{h['beat']}.json").read_text(encoding="utf-8")) for h in HOODS}
    assert all(written[beat] == {"beat": beat, "stories": result[beat]} for beat in written)
    stories = written[813]["stories"]
    assert [s["title"] for s in stories] == ["2 Men Shot After Leaving North Park Bar", "Truck broken into"]
    shooting, truck = stories
    assert shooting["more"] == [{"outlet": "fox5", "url": "https://example.test/x"}]
    assert shooting["date"] == "2026-08-13" and shooting["day"] == (date(2026, 8, 13) - date(2020, 1, 1)).days
    assert truck["where"]["label"] == "30th St & University Ave"
    assert truck["tags"][0] == "vehicle"
    assert len(truck["excerpt"]) <= ch.EXCERPT + 1 and truck["excerpt"].endswith("…")
    assert shooting["area"] is None and truck["area"] is None
    # a story about a wider area is listed under each neighborhood in it, and says which area
    (break_in,) = written[113]["stories"]
    assert break_in["title"] == "Clairemont break-in suspect arrested" and break_in["area"] == "Clairemont"
    assert written[116]["stories"] == written[113]["stories"] and break_in["where"] is None
    # another name for the one neighborhood is not a wider area, and an outlet that is left out is left out
    (stabbing,) = written[122]["stories"]
    assert stabbing["title"] == "Stabbing outside PB bar" and stabbing["area"] is None
    # every neighborhood gets a file, and a story that names none is in no file
    assert written[714]["stories"] == []
    assert not any(key.startswith("_") for feed in written.values() for s in feed["stories"] for key in s)
