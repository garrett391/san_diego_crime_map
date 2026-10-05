"""Residents per neighborhood, for the per-resident rates.

The 2020 Census counted the people in every census block (roughly one city block). Those counts are
final, so they are kept in the repository (census/blocks_2020.csv, every block in San Diego County
with anyone living in it) and the build reads them from there. `python -m pipeline census` makes
the file again from the Census Bureau's redistricting file for California (an 80 MB download, no
key or account); that is only needed if the file is lost.

Two rules turn blocks into a neighborhood's residents:
  1. A block belongs to the neighborhood its centre falls in.
  2. People living in barracks or on ships, in jails, or in college dorms are left out. Crime there
     is mostly another agency's to record (the Navy, the Sheriff, campus police), so it is not in
     SDPD's reports, and counting those people would make the neighborhood around them look safer
     than it is. Nearly two thirds of the census count for Barrio Logan is sailors at the naval base.
"""
from __future__ import annotations

import csv
import io
import os
import urllib.request
import zipfile
from pathlib import Path

import duckdb

from . import config

ZIP_NAME = "ca2020.pl.zip"
GEO_FILE = "cageo2020.pl"            # one line per place of every size; blocks are summary level 750
QUARTERS_FILE = "ca000032020.pl"     # table P5: people in group quarters, by kind
COLUMNS = ["block", "lat", "lon", "people", "in_quarters"]

# Positions in the bureau's pipe-separated lines (2020 P.L. 94-171 technical documentation).
SUMLEV, LOGRECNO, GEOCODE, COUNTY, POP100, INTPTLAT, INTPTLON = 2, 7, 9, 14, 90, 92, 93
BLOCK_LEVEL = "750"
P5_LOGRECNO = 4
# Adult correctional, juvenile facilities, college housing, military quarters. Nursing homes,
# shelters and group homes stay in: SDPD is their police too.
P5_LEFT_OUT = (7, 8, 12, 13)


def _lines(archive: zipfile.ZipFile, name: str):
    with archive.open(name) as f:
        for line in io.TextIOWrapper(f, encoding="latin-1"):
            yield line.rstrip("\n").split("|")


def read_blocks(zip_path: Path, county: str = config.CENSUS_COUNTY) -> list[tuple]:
    """(block, lat, lon, people, in_quarters) for every block of the county with anyone in it."""
    with zipfile.ZipFile(zip_path) as archive:
        blocks = {p[LOGRECNO]: (p[GEOCODE], float(p[INTPTLAT]), float(p[INTPTLON]), int(p[POP100]))
                  for p in _lines(archive, GEO_FILE)
                  if p[SUMLEV] == BLOCK_LEVEL and p[COUNTY] == county and int(p[POP100]) > 0}
        quarters = {p[P5_LOGRECNO]: sum(int(p[i]) for i in P5_LEFT_OUT)
                    for p in _lines(archive, QUARTERS_FILE) if p[P5_LOGRECNO] in blocks}
    # the bureau's privacy noise can leave a block with more people in quarters than people
    return sorted((*b, min(quarters.get(rec, 0), b[3])) for rec, b in blocks.items())


def download(path: Path = config.CENSUS_BLOCKS, data_dir: Path = config.DATA_DIR) -> int:
    """Make census/blocks_2020.csv again. Returns the number of blocks."""
    data_dir.mkdir(parents=True, exist_ok=True)
    archive = data_dir / ZIP_NAME
    if not archive.exists():
        print(f"  downloading {config.CENSUS_URL} (80 MB)", flush=True)
        part = archive.with_name(archive.name + ".part")
        try:
            req = urllib.request.Request(config.CENSUS_URL, headers={"User-Agent": config.USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            os.replace(part, archive)
        finally:
            part.unlink(missing_ok=True)

    rows = read_blocks(archive)
    if not rows:
        raise SystemExit(f"No blocks for county {config.CENSUS_COUNTY} in {archive}")
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with open(part, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            writer.writerows(rows)
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    print(f"  wrote {path}: {len(rows):,} blocks, {sum(r[3] for r in rows):,} people in the county, "
          f"{sum(r[4] for r in rows):,} of them in barracks, jails or dorms")
    return len(rows)


def has_rate(residents: int, biggest_block: int) -> bool:
    """Whether a rate per resident means anything for a neighborhood with this many residents.

    Not when few people live there (config.MIN_RESIDENTS), and not when one block holds more than
    half of them: the count then hangs on which side of the line that one block's centre falls.
    Islenair's is 1,066 by this method and about 460 if its big block is split by area.
    """
    return residents >= config.MIN_RESIDENTS and biggest_block * 2 <= residents


def residents(con: duckdb.DuckDBPyConnection, path: Path | None) -> dict[int, dict] | None:
    """{beat: {"residents", "left_out", "biggest"}} for every beat in the `beats` table; None without
    a block file. "biggest" is the residents of the beat's most populous block.

    Only blocks whose centre is inside a beat count. Nothing is snapped to the nearest beat, because
    a block just over the city line belongs to La Mesa or National City, not to San Diego.
    """
    if path is None or not path.exists():
        return None
    literal = "'" + path.as_posix().replace("'", "''") + "'"
    rows = con.execute(f"""
        WITH blocks AS (
            SELECT row_number() OVER () AS id, people, in_quarters, ST_Point(lon, lat) AS pt
            FROM read_csv({literal}, header = true, columns = {{'block': 'VARCHAR', 'lat': 'DOUBLE',
                          'lon': 'DOUBLE', 'people': 'INTEGER', 'in_quarters': 'INTEGER'}})
        ),
        -- a centre exactly on a shared edge would match two beats; it goes to one of them
        placed AS (
            SELECT min(b.beat) AS beat, any_value(k.people) AS people, any_value(k.in_quarters) AS in_quarters
            FROM blocks k JOIN beats b ON ST_Contains(b.geom, k.pt)
            GROUP BY k.id
        )
        SELECT b.beat,
               CAST(coalesce(sum(p.people - p.in_quarters), 0) AS INTEGER),
               CAST(coalesce(sum(p.in_quarters), 0) AS INTEGER),
               CAST(coalesce(max(p.people - p.in_quarters), 0) AS INTEGER)
        FROM beats b LEFT JOIN placed p USING (beat)
        GROUP BY b.beat
    """).fetchall()
    return {beat: {"residents": n, "left_out": out, "biggest": biggest} for beat, n, out, biggest in rows}
