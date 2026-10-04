"""End-to-end test of the build on a tiny hand-made city: two square beats and a dozen offenses,
each row chosen to exercise one rule."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from pipeline import build as b

COLUMNS = [
    "objectid", "nibrs_uniq", "case_number", "occured_on", "approved_on", "code_section", "ibr_offense",
    "ibr_offense_description", "beat", "block_addr", "geocode_status", "geocode_score", "latitude", "longitude",
]

# Beat 101 is the square lon -117.20..-117.10, beat 102 the square next to it, -117.10..-117.00.
IN_101 = ("32.75", "-117.15")
IN_102 = ("32.75", "-117.05")
NOWHERE = ("33.50", "-116.00")


def square(beat: int, div: int, name: str, west: float) -> dict:
    ring = [[west, 32.70], [west + 0.10, 32.70], [west + 0.10, 32.80], [west, 32.80], [west, 32.70]]
    return {"type": "Feature",
            "properties": {"objectid": str(beat), "beat": str(beat), "div": str(div), "serv": "0", "name": name},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def offense(uid, occurred, approved, code="220", sections="459 PC BURGLARY (RESIDENTIAL) (F) || ", beat="101",
            where=IN_101, status="M", score="100", addr="3000 UNIVERSITY AVE", case="26000001"):
    lat, lon = where if where else ("", "")
    return [uid, uid, case, occurred, approved, sections, code, "desc", beat, addr, status, score, lat, lon]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    out = tmp_path_factory.mktemp("site") / "data"
    (data / "pd_beats_datasd.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        square(101, 8, "ALPHA", -117.20),
        square(102, 8, "BETA", -117.10),
        square(101, 0, "SOME OTHER CITY", -116.50),      # a county beat reusing an SDPD number
    ]}), encoding="utf-8")
    (data / "pd_beat_codes_list_datasd.csv").write_text("beat,neighborhood\n101,Alpha Heights\n", encoding="utf-8")

    rows_2026 = [
        # newest approval in the data: sets "data through" to 2026-06-30
        offense("newest", "2026-06-30", "2026-06-30 10:00:00"),
        # inside the settled 90-day window (2026-03-03 .. 2026-05-31)
        offense("cur-1", "2026-05-01", "2026-05-02 08:00:00"),
        offense("cur-2", "2026-04-10", "2026-04-10 08:00:00", code="13A", sections="245 (A)(1) PC ADW (F) || "),
        # SDPD says beat 102, but the address is confidently inside 101: the address decides
        offense("mislabeled", "2026-05-05", "2026-05-06 08:00:00", code="23H", sections="484 PC THEFT (M) || ", beat="102"),
        # a shaky geocode pointing into 102: SDPD's label (101) is used, and no dot is drawn
        offense("shaky", "2026-05-07", "2026-05-08 08:00:00", where=IN_102, score="62", addr="100 MAIN ST"),
        # no coordinates at all: the label is all there is
        offense("no-coords", "2026-05-09", "2026-05-09 08:00:00", beat="102", where=None, status="U", score="0", addr="55 ELM ST"),
        # confident address outside every SDPD beat: counted for the city only
        offense("outside", "2026-05-11", "2026-05-11 08:00:00", where=NOWHERE),
        # paperwork: a mental-health hold, and a warrant
        offense("hold", "2026-05-12", "2026-05-12 08:00:00", code="90Z", sections="5150 WI MENTAL DISORDER 72 HR OBSERVATION || "),
        offense("warrant", "2026-05-12", "2026-05-12 09:00:00", code="90Z", sections="BW-F ZZ FELONY BENCH WARRANT (OUR AGENCY) || "),
        # a warrant plus a real charge stays in the counts
        offense("warrant+", "2026-05-13", "2026-05-13 08:00:00", code="90Z",
                sections="BW-M ZZ MISDEMEANOR BENCH WARRANT (OUR AGENCY) || 148 (A)(1) PC OBSTRUCT/RESIST PEACE OFCR (M) || "),
        # the same offense id also appears in the 2025 file with an older approval: this one wins
        offense("dup", "2026-04-20", "2026-04-25 08:00:00", addr="NEW VERSION ST", where=("32.76", "-117.16")),
    ]
    rows_2025 = [
        # a year before the window, approved promptly: counts toward the comparison
        offense("prior-ontime", "2025-05-01", "2025-05-03 08:00:00"),
        # a year before the window, but not approved until after last year's cut-off (2025-06-30)
        offense("prior-late", "2025-05-10", "2025-08-01 08:00:00"),
        offense("dup", "2026-04-20", "2026-04-21 08:00:00", addr="OLD VERSION ST", where=("32.76", "-117.16")),
        # unreadable date: dropped
        offense("bad-date", "not a date", "2025-05-03 08:00:00"),
    ]
    for year, rows in ((2026, rows_2026), (2025, rows_2025)):
        with open(data / f"pd_nibrs_{year}_datasd.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(COLUMNS)
            writer.writerows(rows)

    # The unofficial layers: a dispatch log with a burglary call in 101 and a noise complaint in 102,
    # and a chatter store with one headline that names 101 and one that names nowhere.
    with open(data / "pd_calls_for_service_2026_datasd.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(["incident_num", "date_time", "address_number_primary", "address_dir_primary", "address_road_primary",
                         "address_sfx_primary", "address_road_intersecting", "address_sfx_intersecting", "call_type",
                         "disposition", "beat"])
        writer.writerow(["call-1", "2026-06-29 21:15:00.000", "3000", "", "UNIVERSITY", "AVE", "", "", "459", "R", "101"])
        writer.writerow(["call-2", "2026-06-28 10:00:00.000", "55", "", "ELM", "ST", "", "", "415N", "K", "102"])
    store = data / "items.jsonl"
    headline = {"source": "news", "outlet": "Patch", "url": "https://example.test/x", "published": "2026-06-20T15:00:00+00:00", "text": ""}
    store.write_text("".join(json.dumps({**headline, "id": title, "title": title}) + "\n" for title in
                             ("Man stabbed in Alpha Heights", "Man stabbed outside bar")), encoding="utf-8")

    with pytest.MonkeyPatch.context() as settings:       # the tiny city has none of San Diego's names
        settings.setattr(b.config, "CHATTER_ALIASES", {})
        settings.setattr(b.config, "CHATTER_SKIP", [])
        settings.setattr(b.config, "CHATTER_SUBREDDITS", {})
        meta = b.build(data_dir=data, out_dir=out, home_beat=101, chatter_store=store)
    load = lambda name: json.loads((out / name).read_text(encoding="utf-8"))  # noqa: E731
    return {"meta": meta, "city": load("city.json"), "beats": load("beats.geojson"),
            "h101": load("hood/101.json"), "h102": load("hood/102.json"), "out": out, "load": load}


def test_basics(built):
    meta = built["meta"]
    assert meta["data_through"] == "2026-06-30"
    assert meta["settle_days"] == b.SETTLE_DAYS and meta["windows"] == list(b.WINDOWS)
    assert meta["qa"]["rows_read"] == 15
    assert meta["qa"]["rows_kept"] == 13          # one unreadable date, one duplicate id
    assert meta["qa"]["administrative_excluded"] == 2
    assert meta["qa"]["countable_offenses"] == 11


def test_only_sdpd_beats_and_names(built):
    hoods = {h["beat"]: h for h in built["meta"]["hoods"]}
    assert set(hoods) == {101, 102}
    assert hoods[101]["name"] == "Alpha Heights"      # from the beat-code list
    assert hoods[102]["name"] == "Beta"               # falls back to the map layer, title-cased
    assert hoods[101]["division"] == "Mid-City"
    assert hoods[101]["neighbors"] == [102] and hoods[102]["neighbors"] == [101]
    # a 0.1 x 0.1 degree square at 32.75N is about 6.9 x 5.8 miles
    assert 39.5 < hoods[101]["sq_mi"] < 40.7
    assert {f["properties"]["beat"] for f in built["beats"]["features"]} == {101, 102}
    # the county polygon that shares number 101 must not have been merged in
    assert hoods[101]["bbox"][2] < -117.0


def test_neighborhood_assignment(built):
    h101, h102, meta = built["h101"], built["h102"], built["meta"]
    assert len(h101["day"]) == 9 and len(h102["day"]) == 1
    labels = [p[0] for p in h101["places"]]
    assert "3000 block University Ave" in labels
    assert "New Version St" in labels and "Old Version St" not in labels        # newest duplicate won
    # the shaky geocode is counted in 101 but has no point on the map
    shaky = next(p for p in h101["places"] if p[0] == "100 block Main St")
    assert shaky[1] is None and shaky[2] is None
    confident = next(p for p in h101["places"] if p[0] == "3000 block University Ave")
    assert confident[1:] == [-11715000, 3275000]
    assert h102["places"] == [["55 block Elm St", None, None]]
    assert meta["qa"]["assigned_by"] == {"address": 10, "label": 2, "outside": 1}
    codes = [meta["offenses"][i]["code"] for i in h101["off"]]
    assert "23H" in codes                                                       # the mislabeled theft landed in 101


def test_administrative_records_are_left_out(built):
    meta, h101 = built["meta"], built["h101"]
    sections = set(h101["sections"])
    assert not any("5150" in s for s in sections)
    assert not any(s.startswith("BW-F ZZ FELONY BENCH WARRANT") for s in sections)
    kept = next(s for s in sections if "OBSTRUCT/RESIST" in s)
    assert kept.startswith("BW-M ZZ MISDEMEANOR BENCH WARRANT (OUR AGENCY); 148")
    other = next(o for o in meta["offenses"] if o["code"] == "90Z")
    assert meta["categories"][other["cat"]]["id"] == "order" and meta["severities"][other["sev"]]["id"] == "low"


def test_records_are_sorted_and_lag_is_recorded(built):
    h101 = built["h101"]
    assert h101["day"] == sorted(h101["day"])
    first = h101["day"].index(min(h101["day"]))
    assert h101["lag"][first] == 2                    # prior-ontime: 2025-05-01 approved 2025-05-03
    assert max(h101["lag"]) == 83                     # prior-late: 2025-05-10 approved 2025-08-01


def test_year_over_year_counts_only_what_was_known_at_the_time(built):
    compare = built["city"]["compare"]
    total = lambda scope, window, key: sum(compare[scope][str(window)][key])  # noqa: E731
    # 101, settled 90-day window: cur-1, cur-2, mislabeled, shaky, warrant+, dup (not "newest": too recent)
    assert total("101", 90, "cur") == 6
    # a year earlier only the promptly approved report existed; the late one is not counted
    assert total("101", 90, "prior") == 1
    assert total("102", 90, "cur") == 1
    # the city also includes the offense with an address outside every beat
    assert total("city", 90, "cur") == 8
    assert total("city", 365, "cur") == 8
    assert total("city", 365, "prior") == 1           # again only the report that had arrived by then


def test_city_monthly_matches_offense_count(built):
    city = built["city"]
    assert city["months"][0] == "2020-01" and city["months"][-1] == "2026-06"
    assert sum(sum(row) for row in city["monthly"]) == built["meta"]["qa"]["countable_offenses"]


def test_the_unofficial_layers_get_a_file_per_neighborhood(built):
    load = built["load"]
    assert [s["title"] for s in load("chatter/101.json")["stories"]] == ["Man stabbed in Alpha Heights"]
    assert load("chatter/102.json") == {"beat": 102, "stories": []}
    # the call is placed on the block the neighborhood's offense records already know
    (burglary,) = load("dispatch/101.json")["calls"]
    assert (burglary["what"], burglary["place"], burglary["x"], burglary["y"]) == ("Burglary", "3000 block University Ave", -11715000, 3275000)
    # a noise complaint is logged but not listed
    assert load("dispatch/102.json")["calls"] == [] and load("dispatch/102.json")["logged"] == 1


def test_pretty_address():
    assert b.pretty_address("3000 UNIVERSITY AVE") == "3000 block University Ave"
    assert b.pretty_address("700 05TH AVE") == "700 block 5th Ave"
    assert b.pretty_address("Edna PL & 39th ST") == "Edna Pl & 39th St"
    assert b.pretty_address("EL CAJON BLVD") == "El Cajon Blvd"


def test_beat_name_prefers_the_code_list():
    assert b.beat_name("Kearny Mesa", "KEARNEY MESA", 313) == "Kearny Mesa"
    assert b.beat_name(None, "NORTH PARK", 813) == "North Park"
    assert b.beat_name(None, " ", 511) == "Beat 511"


def test_missing_data_is_a_clear_error(tmp_path: Path):
    with pytest.raises(SystemExit, match="python -m pipeline fetch"):
        b.build(data_dir=tmp_path, out_dir=tmp_path / "out", chatter_store=None)
