"""The census step: reading the bureau's redistricting file. Adding blocks up into neighborhoods is
covered by the tiny city in test_build.py."""
from __future__ import annotations

import zipfile

from pipeline import census


def geo(logrecno: str, block: str, people: int, level: str = "750", county: str = "073") -> str:
    fields = [""] * 97
    fields[census.SUMLEV], fields[census.LOGRECNO], fields[census.GEOCODE] = level, logrecno, block
    fields[census.COUNTY], fields[census.POP100] = county, str(people)
    fields[census.INTPTLAT], fields[census.INTPTLON] = "+32.7500000", "-117.1500000"
    return "|".join(fields)


def quarters(logrecno: str, jail=0, juvenile=0, nursing=0, dorm=0, military=0, shelter=0) -> str:
    """One line of table P5, in the bureau's order (totals first, which the reader ignores)."""
    counts = [0, 0, jail, juvenile, nursing, 0, 0, dorm, military, shelter]
    return "|".join(["PLST", "CA", "000", "03", logrecno, *map(str, counts)])


def test_read_blocks_keeps_the_countys_lived_in_blocks_and_who_is_left_out(tmp_path):
    archive = tmp_path / "ca2020.pl.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(census.GEO_FILE, "\n".join([
            geo("0000001", "06073", 3_000_000, level="050"),      # the county itself: not a block
            geo("0000002", "060730001001000", 100),
            geo("0000003", "060730001001001", 0),                 # nobody lives here
            geo("0000004", "060370001001000", 50, county="037"),  # Los Angeles County
            geo("0000005", "060730001001002", 5),
            geo("0000006", "060730001001003", 2000),
        ]) + "\n")
        z.writestr(census.QUARTERS_FILE, "\n".join([
            quarters("0000002", dorm=30, nursing=20, shelter=10),  # only the dorm is left out
            quarters("0000004", jail=50),
            quarters("0000005", military=9),                       # privacy noise: more than live there
            quarters("0000006", jail=700, juvenile=50, military=1000),
        ]) + "\n")

    assert census.read_blocks(archive) == [
        ("060730001001000", 32.75, -117.15, 100, 30),
        ("060730001001002", 32.75, -117.15, 5, 5),
        ("060730001001003", 32.75, -117.15, 2000, 1750),
    ]


def test_no_block_file_means_no_residents():
    assert census.residents(None, None) is None


def test_has_rate():
    assert census.has_rate(38_572, 1_200)
    assert not census.has_rate(256, 41)           # Balboa Park: nearly everyone there is a visitor
    assert not census.has_rate(1_066, 873)        # Islenair: one block is most of it
    assert census.has_rate(1_000, 500)            # exactly at both limits
    assert not census.has_rate(0, 0)
