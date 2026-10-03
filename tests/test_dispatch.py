"""The dispatch layer on a hand-made call log: each row is there to exercise one rule."""
from __future__ import annotations

import csv
import json

import pytest

from pipeline import dispatch as d

COLUMNS = ["INCIDENT_NUM", "DATE_TIME", "DAY_OF_WEEK", "ADDRESS_NUMBER_PRIMARY", "ADDRESS_DIR_PRIMARY",
           "ADDRESS_ROAD_PRIMARY", "ADDRESS_SFX_PRIMARY", "ADDRESS_DIR_INTERSECTING", "ADDRESS_ROAD_INTERSECTING",
           "ADDRESS_SFX_INTERSECTING", "CALL_TYPE", "DISPOSITION", "BEAT", "PRIORITY"]


def call(incident, when, call_type, disposition="K", beat="813", number="3000", road="UNIVERSITY", sfx="AVE", cross=""):
    return [incident, when, "5", number, "", road, sfx, "", cross, "", call_type, disposition, beat, "1"]


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    out = tmp_path_factory.mktemp("site") / "data"
    rows = [
        # the newest row in the whole log, in another beat: it sets the day the log runs through
        call("other-beat", "2026-10-01 23:50:00.000", "459", beat="521"),
        call("burglary", "2026-10-01 17:12:58.000", "459HP", "R"),
        # an intersection, logged with the street type already in the road name
        call("fight", "2026-09-30 01:05:00.000", "415V", "A", number="0", road="30TH ST", sfx="ST", cross="UNIVERSITY AVE"),
        # SDPD's suffix for a report taken afterwards, and its own spelling of "Blvd"
        call("stolen-car", "2026-09-20 09:00:00.000", "10851R", "O", number="2900", road="EL CAJON", sfx="BLV"),
        # the same incident repeated in the file counts once
        call("stolen-car", "2026-09-20 09:00:00.000", "10851R", "O", number="2900", road="EL CAJON", sfx="BLV"),
        # not about a possible crime: noise, a burglar alarm
        call("noise", "2026-09-29 22:00:00.000", "415N"),
        call("alarm", "2026-09-29 03:00:00.000", "459A"),
        # cancelled, and unfounded
        call("cancelled", "2026-09-28 12:00:00.000", "211", "CAN"),
        call("unfounded", "2026-09-28 13:00:00.000", "11-6", "U"),
        # the first day inside a 30-day window ending 2026-10-01, and the day before it
        call("first-day", "2026-09-02 08:00:00.000", "594"),
        call("too-old", "2026-09-01 08:00:00.000", "594"),
        call("bad-date", "sometime", "594"),
    ]
    with open(data / "pd_calls_for_service_2026_datasd.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    (out / "hood").mkdir(parents=True)
    (out / "hood" / "813.json").write_text(json.dumps({"places": [
        ["3000 block University Ave", -11713000, 3274850], ["3100 block University Ave", -11712800, 3274850],
        ["3900 block 30th St", -11713010, 3274900], ["4000 block 30th St", -11713010, 3275050],
    ]}), encoding="utf-8")
    result = d.export(data, out, home_beat=813, days=30)
    assert json.loads((out / "dispatch.json").read_text(encoding="utf-8")) == result
    return result


def test_window_ends_with_the_log_not_the_neighborhood(exported):
    assert exported["through"] == "2026-10-01" and exported["first"] == "2026-09-02"
    assert exported["beat"] == 813 and exported["days"] == 30


def test_only_possible_crimes_that_were_not_called_off_are_listed(exported):
    assert [c["id"] for c in exported["calls"]] == ["burglary", "fight", "stolen-car", "first-day"]     # newest first
    assert exported["logged"] == 8          # everything logged for the beat in the window, for scale


def test_wording_tag_and_outcome(exported):
    burglary, fight, car, vandalism = exported["calls"]
    assert (burglary["what"], burglary["tag"], burglary["outcome"]) == ("Burglary with someone home", "burglary", "report taken")
    assert (burglary["date"], burglary["time"]) == ("2026-10-01", "17:12")
    assert burglary["day"] == 2465          # days since 2020-01-01, the dashboard's day numbers
    assert (fight["what"], fight["outcome"]) == ("Fight or violent disturbance", "arrest made")
    assert (car["what"], car["tag"], car["outcome"]) == ("Car theft, reported afterwards", "vehicle", None)
    assert vandalism["outcome"] == "no report taken"


def test_calls_are_placed_where_the_offense_records_know_the_spot(exported):
    burglary, fight, car, _ = exported["calls"]
    assert (burglary["place"], burglary["x"], burglary["y"]) == ("3000 block University Ave", -11713000, 3274850)
    assert (fight["place"], fight["x"], fight["y"]) == ("30th St & University Ave", -11713005, 3274875)
    # a block with no offense on record has no point, but is still named
    assert (car["place"], car["x"], car["y"]) == ("2900 block El Cajon Blvd", None, None)


def test_describe():
    assert d.describe("459") == ("Burglary", "burglary")
    assert d.describe("459R") == ("Burglary, reported afterwards", "burglary")
    assert d.describe("211-SUSP") == ("Robbery, suspect still there", "violence")
    assert d.describe("10851RR") == ("Stolen car recovered", "vehicle")       # its own entry, not a double suffix
    assert d.describe("TAGGER") == ("Tagging in progress", "vandalism")       # ends in R without being a report
    assert d.describe("586") is None and d.describe("5150") is None and d.describe("") is None


def test_street():
    assert d.street("UNIVERSITY", "AVE") == "UNIVERSITY AVE"
    assert d.street("EL CAJON", "BLV") == "EL CAJON BLVD"
    assert d.street("EL CAJON BLVD", "BLV") == "EL CAJON BLVD"       # intersections repeat the type
    assert d.street("30TH ST", "ST") == "30TH ST"
    assert d.street("UNIVERSITY", "") == "UNIVERSITY"
    assert d.street("", "") == ""


def test_no_log_means_no_file(tmp_path):
    assert d.export(tmp_path, tmp_path / "out", home_beat=813) is None
    assert not (tmp_path / "out" / "dispatch.json").exists()


def test_a_log_with_unexpected_columns_is_skipped(tmp_path, capsys):
    (tmp_path / "pd_calls_for_service_2026_datasd.csv").write_text('"id","when"\n"1","2026-10-01"\n', encoding="utf-8")
    assert d.export(tmp_path, tmp_path / "out", home_beat=813) is None
    assert "skipped" in capsys.readouterr().out
