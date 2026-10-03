from datetime import date

from pipeline import fetch


def test_sources_cover_every_year_and_the_boundaries():
    names = [name for name, _ in fetch.sources([2024, 2025], today=date(2026, 10, 3))]
    assert names == ["pd_nibrs_2024_datasd.csv", "pd_nibrs_2025_datasd.csv",
                     "pd_beats_datasd.geojson", "pd_beat_codes_list_datasd.csv",
                     "pd_calls_for_service_2026_datasd.csv"]
    assert fetch.sources()[0][0] == "pd_nibrs_2020_datasd.csv"


def test_the_dispatch_log_reaches_into_last_year_in_january():
    # the dashboard lists the last 30 days of calls, so early in a year it needs two files
    names = [name for name, _ in fetch.sources(today=date(2027, 1, 20))]
    assert names[-2:] == ["pd_calls_for_service_2026_datasd.csv", "pd_calls_for_service_2027_datasd.csv"]
    names = [name for name, _ in fetch.sources(today=date(2027, 3, 1))]
    assert [n for n in names if "calls" in n] == ["pd_calls_for_service_2027_datasd.csv"]


def test_is_current():
    remote = {"etag": '"abc"', "bytes": 1000}
    # same ETag and size as the last download: nothing to do
    assert fetch.is_current(1000, {"etag": '"abc"', "bytes": 1000}, remote)
    # the server has a new version
    assert not fetch.is_current(1000, {"etag": '"old"', "bytes": 1000}, remote)
    # the local file was truncated since it was downloaded
    assert not fetch.is_current(400, {"etag": '"abc"', "bytes": 1000}, remote)
    # a file saved by hand (no record of it): trust it if the size matches
    assert fetch.is_current(1000, {}, remote)
    assert not fetch.is_current(999, {}, remote)
    # missing or empty files are never current
    assert not fetch.is_current(None, {}, remote)
    assert not fetch.is_current(0, {"etag": '"abc"', "bytes": 0}, {"etag": '"abc"', "bytes": 0})
