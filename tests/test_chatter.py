"""The chatter filter is keyword rules, so the tests are real posts and headlines it must get right."""
from __future__ import annotations

import json
import urllib.error
from datetime import date, datetime, timezone

import pytest

from pipeline import chatter as ch

PLACES = ["North Park"]

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


def item(source, title, text="", outlet=None, published="2026-08-13T15:00:00+00:00", local=False):
    return {"id": f"{source}:{outlet}:{title}:{published}", "source": source, "outlet": outlet or source,
            "title": title, "url": "https://example.test/x", "published": published, "text": text, "local": local}


def test_where_the_neighborhood_has_to_be_named():
    # news: only the headline is known, so it must name the place
    assert ch.is_relevant(item("news", "Two men injured in early morning North Park shooting"), PLACES)
    assert not ch.is_relevant(item("news", "Two men injured in early morning shooting"), PLACES)
    # a citywide subreddit: the place can be in the body
    assert ch.is_relevant(item("reddit", "Stolen Bike", "taken from my garage in North Park"), PLACES)
    assert not ch.is_relevant(item("reddit", "Stolen Bike", "taken from my garage in Pacific Beach"), PLACES)
    # a subreddit named after the neighborhood: every post is about it
    assert ch.is_relevant(item("reddit", "Stolen Bike", "taken from my garage", local=True), PLACES)
    # a business that carries the name is not the place
    assert not ch.is_relevant(item("news", "Car crashes into North Park Produce grocery store in Poway"), PLACES)


def test_local_subreddit_is_recognized_by_name():
    assert ch.is_local_subreddit("northpark", PLACES)
    assert ch.is_local_subreddit("NorthParkSD", PLACES)
    assert not ch.is_local_subreddit("sandiego", PLACES)


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
    (post,) = ch.parse_reddit(REDDIT_FEED, local=False)
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


def test_an_outlets_summary_can_name_the_neighborhood():
    title = "Man stabbed during fight outside bar"
    assert ch.is_relevant(item("news", title, "Police say the stabbing happened in North Park early Sunday."), PLACES)
    assert not ch.is_relevant(item("news", title, "Police say the stabbing happened in Pacific Beach."), PLACES)
    # the crime still has to be in the headline: a passing mention deep in an article does not count
    assert not ch.is_relevant(item("news", "Council approves North Park bike lanes", "One speaker said her bike was stolen."), PLACES)


def test_fetch_feeds_keeps_what_names_the_neighborhood_and_survives_a_dead_feed(monkeypatch):
    def fake_get(url):
        if "dead" in url:
            raise urllib.error.URLError("no route")
        return OUTLET_FEED, {}
    monkeypatch.setattr(ch, "_get", fake_get)
    log = []
    items = ch.fetch_feeds([("NBC 7 San Diego", "https://example.test/feed"), ("Gone", "https://dead.test/feed")],
                           PLACES, log=log.append)
    assert [i["title"] for i in items] == ["Man stabbed during fight outside bar"]
    assert any("Gone: skipped" in line for line in log)


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


def test_export_writes_a_clean_feed(tmp_path):
    store = tmp_path / "items.jsonl"
    long_text = "My truck was broken into at 30th and University in North Park. " + "More details here. " * 30
    ch.save_store(store, {i["id"]: i for i in [
        item("reddit", "Truck broken into", long_text, outlet="r/sandiego", published="2025-03-08T07:12:00+00:00"),
        item("reddit", "Best tacos in North Park?", "Looking for recommendations", outlet="r/sandiego"),
        item("news", "2 Men Shot After Leaving North Park Bar", outlet="Patch"),
        item("news", "Two men injured in early morning North Park shooting", outlet="fox5"),
    ]})
    out = tmp_path / "site-data"
    (out / "hood").mkdir(parents=True)
    (out / "hood" / "813.json").write_text(json.dumps({"places": [
        ["3000 block University Ave", -11713000, 3274850], ["3100 block University Ave", -11712800, 3274850],
        ["3900 block 30th St", -11713010, 3274900], ["4000 block 30th St", -11713010, 3275050],
    ]}), encoding="utf-8")

    result = ch.export(store, out, places=PLACES, home_beat=813)
    written = json.loads((out / "chatter.json").read_text(encoding="utf-8"))
    assert written["stories"] == result["stories"] and written["stored"] == 4
    assert [s["title"] for s in written["stories"]] == ["2 Men Shot After Leaving North Park Bar", "Truck broken into"]
    shooting, truck = written["stories"]
    assert shooting["more"] == [{"outlet": "fox5", "url": "https://example.test/x"}]
    assert shooting["date"] == "2026-08-13" and shooting["day"] == (date(2026, 8, 13) - date(2020, 1, 1)).days
    assert truck["where"]["label"] == "30th St & University Ave"
    assert truck["tags"][0] == "vehicle"
    assert len(truck["excerpt"]) <= ch.EXCERPT + 1 and truck["excerpt"].endswith("…")
    assert not any(key.startswith("_") for s in written["stories"] for key in s)
