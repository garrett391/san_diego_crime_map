"""Turn the raw SDPD files in ./data into the compact JSON the dashboard reads.

The whole thing is rebuilt from scratch on every run (the city rewrites its files daily and old
records change), and the output is written to a temporary folder that replaces the previous one
only if the build succeeds.

Steps, each a function below:
  load_offenses   read every yearly CSV, fix types, keep one row per NIBRS offense id
  classify        attach the dashboard's category and severity to each offense
  load_beats      SDPD beat polygons (one beat = one neighborhood)
  assign_beats    decide which neighborhood each offense belongs to
  export          write meta.json, city.json, beats.geojson and one file per neighborhood

The unofficial layers are added last: chatter.json (chatter.py) and dispatch.json (dispatch.py).
"""
from __future__ import annotations

import json
import re
import shutil
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from . import categories, config

EPOCH = date(2020, 1, 1)        # "day" numbers in the output count from here
SIMPLIFY_DEGREES = 0.00003      # about 3 m; shrinks the boundary file from 45 MB to ~0.2 MB

# Year-over-year comparisons. Reports keep arriving for weeks after an offense (about a quarter of
# a month's reports are still missing when it ends), so two rules keep a comparison honest:
#   1. the window ends SETTLE_DAYS before the newest data, and
#   2. the year-earlier window counts only reports that had been approved by the same point then.
# Replayed on monthly snapshots since 2022 (python -m analysis.backtest), North Park's 12-month
# figure lands within 2 points of the eventual number (0.7 on average) and its 3-month figure
# within about 2 on average, 7 at worst, including through the 2026 approval backlog. Raw counts
# run 2-30 points too low depending on the window. No correction held up for the newest 30 days
# (approval speed shifts too much), so those are never compared.
# site/js/stats.js applies the same rule in the browser; tests check the two agree.
SETTLE_DAYS = 30
WINDOWS = (90, 365)             # window lengths, in days
YEAR = 365                      # how far back the comparison period sits

NIBRS_GLOB = "pd_nibrs_*_datasd.csv"
BEATS_FILE = "pd_beats_datasd.geojson"
BEAT_CODES_FILE = "pd_beat_codes_list_datasd.csv"

REQUIRED_COLUMNS = {
    "nibrs_uniq", "case_number", "occured_on", "approved_on", "code_section", "ibr_offense",
    "ibr_offense_description", "beat", "block_addr", "geocode_status", "geocode_score",
    "latitude", "longitude",
}

CELLS = [(c, s) for c in categories.CATEGORY_IDS for s in categories.SEVERITY_IDS]


def _q(text: str) -> str:
    """A Python string as a SQL literal."""
    return "'" + text.replace("'", "''") + "'"


# ----------------------------------------------------------------------------------------------
# load
# ----------------------------------------------------------------------------------------------

def load_offenses(con: duckdb.DuckDBPyConnection, data_dir: Path) -> dict:
    files = sorted(data_dir.glob(NIBRS_GLOB))
    if not files:
        raise SystemExit(f"No {NIBRS_GLOB} files in {data_dir}. Run:  python -m pipeline fetch")

    con.execute(f"""
        CREATE OR REPLACE TABLE raw AS
        SELECT * FROM read_csv({_q((data_dir / NIBRS_GLOB).as_posix())},
                               all_varchar = true, union_by_name = true, filename = true)
    """)
    have = {row[0] for row in con.execute("DESCRIBE raw").fetchall()}
    if missing := REQUIRED_COLUMNS - have:
        raise SystemExit(f"The NIBRS files are missing expected columns: {sorted(missing)}")

    # nibrs_uniq is the city's id for one countable offense. If the same id shows up in two files
    # (downloads taken on different days), the most recently approved version wins.
    con.execute(rf"""
        CREATE OR REPLACE TABLE offenses AS
        WITH typed AS (
            SELECT
                trim(nibrs_uniq)                                   AS id,
                coalesce(try_cast(case_number AS BIGINT), 0)       AS case_no,
                try_cast(occured_on AS DATE)                       AS occurred,
                try_cast(approved_on AS TIMESTAMP)                 AS approved,
                upper(trim(ibr_offense))                           AS code,
                trim(ibr_offense_description)                      AS code_label,
                list_transform(
                    list_filter(string_split(coalesce(code_section, ''), '||'), lambda s: trim(s) <> ''),
                    lambda s: regexp_replace(trim(s), '\s+', ' ', 'g'))  AS sections,
                try_cast(beat AS INTEGER)                          AS label_beat,
                nullif(regexp_replace(trim(coalesce(block_addr, '')), '\s+', ' ', 'g'), '') AS address,
                try_cast(latitude AS DOUBLE)                       AS lat,
                try_cast(longitude AS DOUBLE)                      AS lon,
                coalesce(geocode_status = 'M'
                         AND try_cast(geocode_score AS DOUBLE) >= {config.MIN_GEOCODE_SCORE}, false) AS confident,
                filename
            FROM raw
            WHERE nibrs_uniq IS NOT NULL
        )
        SELECT * EXCLUDE (filename)
        FROM typed
        WHERE occurred IS NOT NULL AND approved IS NOT NULL AND occurred >= DATE '{EPOCH.isoformat()}'
        QUALIFY row_number() OVER (PARTITION BY id ORDER BY approved DESC, filename DESC) = 1
    """)
    rows_raw = con.execute("SELECT count(*) FROM raw").fetchone()[0]
    rows = con.execute("SELECT count(*) FROM offenses").fetchone()[0]
    con.execute("DROP TABLE raw")
    return {"files": len(files), "rows_read": rows_raw, "rows_kept": rows, "rows_dropped": rows_raw - rows}


def classify(con: duckdb.DuckDBPyConnection) -> dict:
    con.execute("CREATE OR REPLACE TABLE offense_types (code VARCHAR, label VARCHAR, category VARCHAR, severity VARCHAR)")
    con.executemany("INSERT INTO offense_types VALUES (?, ?, ?, ?)",
                    [(code, *info) for code, info in categories.OFFENSES.items()])
    # A 90Z row is administrative when none of its code sections is anything but paperwork.
    con.execute(f"""
        CREATE OR REPLACE TABLE classified AS
        SELECT o.*,
               coalesce(t.label, o.code_label, o.code) AS label,
               CASE WHEN o.code = '90Z' AND len(o.sections) > 0
                         AND len(list_filter(o.sections, lambda s:
                                 NOT regexp_matches(upper(s), {_q(categories.ADMIN_SECTION)}))) = 0
                    THEN {_q(categories.ADMIN)}
                    ELSE coalesce(t.category, 'order') END AS category,
               coalesce(t.severity, 'low') AS severity
        FROM offenses o LEFT JOIN offense_types t USING (code)
    """)
    unknown = con.execute("""
        SELECT code, count(*) FROM classified WHERE code NOT IN (SELECT code FROM offense_types) GROUP BY 1
    """).fetchall()
    admin = con.execute(f"SELECT count(*) FROM classified WHERE category = {_q(categories.ADMIN)}").fetchone()[0]
    return {"administrative_excluded": admin, "unknown_codes": dict(unknown)}


def beat_name(code_name: str | None, layer_name: str | None, beat: int) -> str:
    """Prefer the city's beat-code list (proper case, correct spelling) over the map layer."""
    if code_name and code_name.strip():
        return code_name.strip()
    if layer_name and layer_name.strip():
        return layer_name.strip().title()
    return f"Beat {beat}"


def load_beats(con: duckdb.DuckDBPyConnection, data_dir: Path) -> int:
    geojson = data_dir / BEATS_FILE
    if not geojson.exists():
        raise SystemExit(f"Missing {geojson}. Run:  python -m pipeline fetch")
    # The layer covers the whole county; SDPD's own beats are the ones with a division number.
    # A beat can be several polygons (Mission Bay has four), so they are merged.
    con.execute(f"""
        CREATE OR REPLACE TABLE beats AS
        SELECT try_cast(beat AS INTEGER) AS beat,
               any_value(try_cast(div AS INTEGER)) AS div,
               max(trim("name")) AS layer_name,
               ST_Union_Agg(geom) AS geom
        FROM ST_Read({_q(geojson.as_posix())})
        WHERE try_cast(div AS INTEGER) > 0 AND try_cast(beat AS INTEGER) IS NOT NULL
        GROUP BY 1
    """)
    code_names: dict[int, str] = {}
    codes = data_dir / BEAT_CODES_FILE
    if codes.exists() and codes.stat().st_size > 0:
        code_names = dict(con.execute(f"""
            SELECT try_cast(beat AS INTEGER), neighborhood
            FROM read_csv({_q(codes.as_posix())}, all_varchar = true, header = true)
            WHERE try_cast(beat AS INTEGER) IS NOT NULL
        """).fetchall())
    names = [(b, beat_name(code_names.get(b), layer, b))
             for b, layer in con.execute("SELECT beat, layer_name FROM beats").fetchall()]
    con.execute("CREATE OR REPLACE TABLE beat_names (beat INTEGER, name VARCHAR)")
    con.executemany("INSERT INTO beat_names VALUES (?, ?)", names)
    return len(names)


def assign_beats(con: duckdb.DuckDBPyConnection) -> dict:
    """Decide which neighborhood each offense belongs to.

    SDPD labels every record with a beat, but about one label in ten disagrees with where the
    block address actually geocodes. A map needs the two to match, so:
      1. a confident geocode decides (the polygon the point falls in);
      2. otherwise SDPD's label is used;
      3. otherwise a low-confidence geocode, if that is all there is.
    A confident geocode outside every SDPD beat means the address is outside the city, and the
    record is left unassigned. It still counts toward the citywide totals.
    """
    con.execute("""
        CREATE OR REPLACE TABLE pts AS
        SELECT id, label_beat, ST_Point(lon, lat) AS pt
        FROM classified WHERE lat IS NOT NULL AND lon IS NOT NULL
    """)
    # A point exactly on a shared edge can match two polygons; keep SDPD's label if it is one of them.
    con.execute("""
        CREATE OR REPLACE TABLE geo AS
        SELECT p.id,
               CASE WHEN bool_or(b.beat = p.label_beat) THEN any_value(p.label_beat) ELSE min(b.beat) END AS beat
        FROM pts p JOIN beats b ON ST_Contains(b.geom, p.pt)
        GROUP BY p.id
    """)
    con.execute(f"""
        INSERT INTO geo
        SELECT p.id, arg_min(b.beat, ST_Distance(b.geom, p.pt))
        FROM pts p JOIN beats b ON ST_DWithin(b.geom, p.pt, {config.SNAP_DEGREES})
        WHERE p.id NOT IN (SELECT id FROM geo)
        GROUP BY p.id
    """)
    con.execute("""
        CREATE OR REPLACE TABLE located AS
        WITH j AS (
            SELECT c.*, g.beat AS geo_beat,
                   c.label_beat IN (SELECT beat FROM beats) AS label_ok
            FROM classified c LEFT JOIN geo g USING (id)
        )
        SELECT * EXCLUDE (label_ok),
               CASE WHEN confident THEN geo_beat
                    WHEN label_ok THEN label_beat
                    ELSE geo_beat END AS beat,
               CASE WHEN confident AND geo_beat IS NOT NULL THEN 'address'
                    WHEN confident THEN 'outside'
                    WHEN label_ok THEN 'label'
                    WHEN geo_beat IS NOT NULL THEN 'address_low'
                    ELSE 'unknown' END AS how
        FROM j
    """)
    con.execute("DROP TABLE pts")
    how = dict(con.execute("SELECT how, count(*) FROM located GROUP BY 1").fetchall())
    agree, both = con.execute("""
        SELECT count(*) FILTER (label_beat = geo_beat), count(*)
        FROM located WHERE how = 'address' AND label_beat IN (SELECT beat FROM beats)
    """).fetchone()
    return {"assigned_by": how, "label_matches_address_pct": round(100 * agree / both, 1) if both else None}


def finalize(con: duckdb.DuckDBPyConnection) -> date:
    """One tidy table of countable offenses, and the date the data runs through."""
    con.execute(f"""
        CREATE OR REPLACE TABLE final AS
        SELECT id, case_no, occurred,
               CAST(approved AS DATE) AS approved_day,
               date_diff('day', DATE '{EPOCH.isoformat()}', occurred) AS day,
               greatest(date_diff('day', occurred, CAST(approved AS DATE)), 0) AS lag,
               code, label, category, severity,
               array_to_string(sections, '; ') AS sections,
               address, beat, how,
               -- a dot is drawn only where the point lies in the neighborhood the record is counted in
               CASE WHEN geo_beat IS NOT NULL AND geo_beat = beat THEN CAST(round(lon * 1e5) AS INTEGER) END AS x,
               CASE WHEN geo_beat IS NOT NULL AND geo_beat = beat THEN CAST(round(lat * 1e5) AS INTEGER) END AS y
        FROM located
        WHERE category <> {_q(categories.ADMIN)}
    """)
    through = con.execute("SELECT max(approved_day) FROM (SELECT CAST(approved AS DATE) AS approved_day FROM located)").fetchone()[0]
    con.execute("CREATE OR REPLACE TABLE cells (category VARCHAR, severity VARCHAR, cell INTEGER)")
    con.executemany("INSERT INTO cells VALUES (?, ?, ?)", [(c, s, i) for i, (c, s) in enumerate(CELLS)])
    return through


# ----------------------------------------------------------------------------------------------
# export
# ----------------------------------------------------------------------------------------------

_ORDINAL = re.compile(r"^0*(\d+)(ST|ND|RD|TH)$", re.IGNORECASE)
_BLOCK = re.compile(r"^(\d+) (.+)$")


def pretty_address(raw: str) -> str:
    """'3000 UNIVERSITY AVE' -> '3000 block University Ave'; intersections keep their '&'."""
    words = []
    for word in raw.split():
        if m := _ORDINAL.match(word):
            words.append(m.group(1) + m.group(2).lower())
        elif any(ch.isdigit() for ch in word):
            words.append(word.upper())
        else:
            words.append(word.capitalize())
    text = " ".join(words)
    if "&" not in text and (m := _BLOCK.match(text)):
        return f"{m.group(1)} block {m.group(2)}"
    return text


def _write(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def comparison_table(con: duckdb.DuckDBPyConnection, through: date) -> dict:
    """Counts for each window length and for the same window a year earlier (see SETTLE_DAYS)."""
    t = f"DATE '{through}'"
    end = f"({t} - {SETTLE_DAYS})"
    selects = []
    for w in WINDOWS:
        selects.append(f"count(*) FILTER (occurred > {end} - {w} AND occurred <= {end}) AS cur{w}")
        selects.append(f"count(*) FILTER (occurred > {end} - {YEAR + w} AND occurred <= {end} - {YEAR} "
                       f"AND approved_day <= {t} - {YEAR}) AS prior{w}")
    cols = ", ".join(selects)

    def pack(rows) -> dict:
        out: dict = {}
        for scope, cell, *counts in rows:
            entry = out.setdefault(str(scope), {str(w): {"cur": [0] * len(CELLS), "prior": [0] * len(CELLS)}
                                                for w in WINDOWS})
            for i, w in enumerate(WINDOWS):
                entry[str(w)]["cur"][cell] = counts[2 * i]
                entry[str(w)]["prior"][cell] = counts[2 * i + 1]
        return out

    table = pack(con.execute(f"""
        SELECT beat, cell, {cols} FROM final JOIN cells USING (category, severity)
        WHERE beat IS NOT NULL GROUP BY 1, 2
    """).fetchall())
    table.update(pack(con.execute(f"""
        SELECT 'city', cell, {cols} FROM final JOIN cells USING (category, severity) GROUP BY 1, 2
    """).fetchall()))
    return table


def city_monthly(con: duckdb.DuckDBPyConnection, through: date) -> dict:
    n_months = (through.year - EPOCH.year) * 12 + through.month - EPOCH.month + 1
    months = [f"{EPOCH.year + (EPOCH.month - 1 + i) // 12}-{(EPOCH.month - 1 + i) % 12 + 1:02d}" for i in range(n_months)]
    series = [[0] * n_months for _ in CELLS]
    for month, cell, n in con.execute(f"""
        SELECT date_diff('month', DATE '{EPOCH.isoformat()}', occurred), cell, count(*)
        FROM final JOIN cells USING (category, severity) GROUP BY 1, 2
    """).fetchall():
        if 0 <= month < n_months:
            series[cell][month] = n
    return {"months": months, "monthly": series}


def export_beats(con: duckdb.DuckDBPyConnection, out_dir: Path) -> list[dict]:
    """Simplified boundaries for the map, plus the facts about each neighborhood for meta.json."""
    rows = con.execute(f"""
        WITH g AS (
            SELECT b.beat, n.name, b.div, b.geom,
                   CASE WHEN ST_Contains(b.geom, ST_Centroid(b.geom)) THEN ST_Centroid(b.geom)
                        ELSE ST_PointOnSurface(b.geom) END AS c
            FROM beats b JOIN beat_names n USING (beat)
        )
        SELECT beat, name, div,
               ST_AsGeoJSON(ST_ReducePrecision(ST_SimplifyPreserveTopology(geom, {SIMPLIFY_DEGREES}), 0.00001)) AS geojson,
               ST_X(c), ST_Y(c), ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom),
               -- planar area in square degrees, scaled to km2 at the beat's own latitude
               ST_Area(geom) * 110.95 * 111.32 * cos(radians(ST_Y(c))) AS km2
        FROM g ORDER BY beat
    """).fetchall()
    neighbors = dict(con.execute("""
        SELECT a.beat, list(b.beat ORDER BY b.beat)
        FROM beats a JOIN beats b ON a.beat <> b.beat AND ST_DWithin(a.geom, b.geom, 0.0002)
        GROUP BY a.beat
    """).fetchall())
    totals = dict(con.execute("SELECT beat, count(*) FROM final WHERE beat IS NOT NULL GROUP BY 1").fetchall())

    features, hoods = [], []
    for beat, name, div, geojson, cx, cy, x0, y0, x1, y1, km2 in rows:
        features.append({"type": "Feature", "id": beat, "properties": {"beat": beat, "name": name},
                         "geometry": json.loads(geojson)})
        hoods.append({
            "beat": beat, "name": name, "division": config.DIVISIONS.get(div, ""),
            "sq_mi": round(km2 / 2.58999, 2),
            "center": [round(cx, 5), round(cy, 5)],
            "bbox": [round(x0, 5), round(y0, 5), round(x1, 5), round(y1, 5)],
            "neighbors": neighbors.get(beat, []),
            "n": totals.get(beat, 0),
        })
    _write(out_dir / "beats.geojson", {"type": "FeatureCollection", "features": features})
    return hoods


def export_hoods(con: duckdb.DuckDBPyConnection, out_dir: Path, beats: list[int], offense_index: dict[str, int]) -> int:
    """One columnar file per neighborhood, every countable offense in it, oldest first."""
    cur = con.execute("""
        SELECT beat, day, lag, code, case_no, sections, address, x, y
        FROM final WHERE beat IS NOT NULL ORDER BY beat, day, case_no, id
    """)
    by_beat: dict[int, list] = {b: [] for b in beats}
    while chunk := cur.fetchmany(50_000):
        for row in chunk:
            by_beat.setdefault(row[0], []).append(row[1:])

    total_bytes = 0
    for beat, rows in by_beat.items():
        place_index: dict = {}
        place_labels: list[Counter] = []
        place_xy: list[tuple] = []
        section_index: dict[str, int] = {}
        cols = {k: [] for k in ("day", "lag", "off", "case", "sec", "place")}
        for day, lag, code, case_no, sections, address, x, y in rows:
            # A place is one hundred-block: a point on the map when it geocoded, else just its text.
            key = (x, y) if x is not None else (("text", address.upper()) if address else None)
            if key is None:
                p = -1
            else:
                p = place_index.get(key)
                if p is None:
                    p = place_index[key] = len(place_labels)
                    place_labels.append(Counter())
                    place_xy.append((x, y))
                if address:
                    place_labels[p][pretty_address(address)] += 1
            cols["day"].append(day)
            cols["lag"].append(lag)
            cols["off"].append(offense_index[code])
            cols["case"].append(case_no)
            cols["sec"].append(section_index.setdefault(sections, len(section_index)))
            cols["place"].append(p)
        places = [[labels.most_common(1)[0][0] if labels else "", x, y]
                  for labels, (x, y) in zip(place_labels, place_xy)]
        total_bytes += _write(out_dir / "hood" / f"{beat}.json",
                              {"beat": beat, "places": places, "sections": list(section_index), **cols})
    return total_bytes


def build(data_dir: Path = config.DATA_DIR, out_dir: Path = config.OUT_DIR,
          home_beat: int = config.HOME_BEAT, chatter_store: Path | None = config.CHATTER_STORE) -> dict:
    started = time.monotonic()
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")

    qa: dict = {}
    qa.update(load_offenses(con, data_dir))
    qa.update(classify(con))
    qa["beats"] = load_beats(con, data_dir)
    qa.update(assign_beats(con))
    through = finalize(con)
    qa["countable_offenses"] = con.execute("SELECT count(*) FROM final").fetchone()[0]
    qa["mapped_pct"] = round(100 * con.execute("SELECT avg((x IS NOT NULL)::INT) FROM final").fetchone()[0], 1)

    tmp = out_dir.with_name(out_dir.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    hoods = export_beats(con, tmp)
    if home_beat not in {h["beat"] for h in hoods}:
        raise SystemExit(f"HOME_BEAT {home_beat} is not an SDPD beat in {BEATS_FILE}")

    # Only offense codes that actually occur are shipped; hood files refer to them by position.
    present = {r[0]: r[1] for r in con.execute("SELECT code, any_value(label) FROM final GROUP BY 1").fetchall()}
    offenses, offense_index = [], {}
    for code in sorted(present):
        _, cat, sev = categories.OFFENSES.get(code, (present[code], "order", "low"))
        offense_index[code] = len(offenses)
        offenses.append({"code": code, "label": present[code],
                         "cat": categories.CATEGORY_IDS.index(cat), "sev": categories.SEVERITY_IDS.index(sev)})

    hood_bytes = export_hoods(con, tmp, [h["beat"] for h in hoods], offense_index)
    _write(tmp / "city.json", {**city_monthly(con, through), "compare": comparison_table(con, through)})

    first_day = con.execute("SELECT min(occurred) FROM final").fetchone()[0]
    fetch_state = {}
    try:
        fetch_state = json.loads((data_dir / ".fetch_state.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    fetched = sorted(v["fetched_at"] for k, v in fetch_state.items() if k.startswith("pd_nibrs") and v.get("fetched_at"))

    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fetched_at": fetched[-1] if fetched else None,
        "data_through": through.isoformat(),
        "first_date": first_day.isoformat(),
        "epoch": EPOCH.isoformat(),
        "home_beat": home_beat,
        "windows": list(WINDOWS),
        "settle_days": SETTLE_DAYS,
        "year_days": YEAR,
        "categories": [{"id": c, "label": label} for c, label in categories.CATEGORIES],
        "severities": [{"id": s, "label": label, "about": about} for s, label, about in categories.SEVERITIES],
        "offenses": offenses,
        "hoods": hoods,
        "qa": qa,
    }
    _write(tmp / "meta.json", meta)

    if chatter_store is not None:
        from .chatter import export as export_chatter
        export_chatter(chatter_store, tmp)
    from .dispatch import export as export_dispatch
    dispatch = export_dispatch(data_dir, tmp, home_beat)

    shutil.rmtree(out_dir, ignore_errors=True)
    tmp.rename(out_dir)

    home = next(h for h in hoods if h["beat"] == home_beat)
    print(f"  {qa['rows_kept']:,} offenses read from {qa['files']} files, data through {through}")
    print(f"  {qa['administrative_excluded']:,} administrative records left out "
          f"(mental-health holds, warrants, parole/probation)")
    print(f"  {qa['countable_offenses']:,} countable offenses, {qa['mapped_pct']}% placed on the map; "
          f"neighborhood from: {qa['assigned_by']}")
    if qa["unknown_codes"]:
        print(f"  note: offense codes not in categories.py (treated as low / public order): {qa['unknown_codes']}")
    print(f"  {home['name']}: {home['n']:,} offenses since {first_day}")
    if dispatch:
        print(f"  dispatch: {len(dispatch['calls']):,} of {dispatch['logged']:,} police calls there in the {dispatch['days']} days "
              f"to {dispatch['through']} were about a possible crime; "
              f"{sum(c['x'] is not None for c in dispatch['calls']):,} placed on the map")
    print(f"  wrote {out_dir} ({hood_bytes / 1e6:.1f} MB of neighborhood files) in {time.monotonic() - started:.1f}s")
    return meta
