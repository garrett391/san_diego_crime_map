"""Police dispatch: what SDPD was called to in each neighborhood over the last few weeks.

The city publishes every call for service (911 and non-emergency calls, plus stops officers start
themselves) as one CSV per year, rewritten daily and about two days behind. `fetch` downloads it
and `build` turns each neighborhood's recent calls into dispatch/<beat>.json.

A call is what someone told a dispatcher, not a confirmed crime, and most end without a report.
So calls are shown with the news and Reddit posts as unverified context and are never added to
the counts. Their use is the newest few weeks, which offense reports have not caught up with yet.

Only call types that describe a possible crime are kept (CALLS below). Most of the log is
something else: noise, parking, welfare checks, traffic stops, alarms.

The log has no coordinates. A call belongs to the neighborhood SDPD labeled it with (offense
reports can be checked against their geocode; these cannot), and it gets a map point only when
its block or intersection is one the neighborhood's offense records already know.
"""
from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config
from .build import EPOCH, pretty_address
from .chatter import Gazetteer

CALLS_GLOB = "pd_calls_for_service_*_datasd.csv"
COLUMNS = {"incident_num", "date_time", "address_number_primary", "address_dir_primary", "address_road_primary",
           "address_sfx_primary", "address_road_intersecting", "address_sfx_intersecting", "call_type",
           "disposition", "beat"}

# SDPD call type -> (plain wording, tag). The tags are the ones chatter.py gives news and Reddit
# posts. SDPD adds R to a code for a report taken once the crime was discovered ("459R") and -SUSP
# when the suspect is still there; describe() handles both, so only the base codes are listed.
# A call type that is not here is left off the dashboard.
CALLS: dict[str, tuple[str, str]] = {
    "187": ("Homicide", "violence"),
    "207": ("Kidnapping", "violence"),
    "211": ("Robbery", "violence"),
    "215": ("Carjacking", "violence"),
    "242": ("Battery", "violence"),
    "245": ("Assault with a deadly weapon", "violence"),
    "245DV": ("Assault with a deadly weapon, domestic", "violence"),
    "246": ("Shots fired at an occupied home or car", "violence"),
    "247": ("Shots fired at an empty home or car", "violence"),
    "417": ("Threat with a weapon", "violence"),
    "422": ("Criminal threats", "violence"),
    "11-6": ("Gunshots reported", "violence"),
    "11-6SPT": ("Gunshots picked up by a sensor", "violence"),
    "415V": ("Fight or violent disturbance", "violence"),
    "415W": ("Disturbance with a weapon", "violence"),
    "415DV": ("Domestic violence", "violence"),
    "459": ("Burglary", "burglary"),
    "459HP": ("Burglary with someone home", "burglary"),
    "459C": ("Someone casing a building", "burglary"),
    "11-7": ("Prowler", "burglary"),
    "10851": ("Car theft", "vehicle"),
    "10851RR": ("Stolen car recovered", "vehicle"),
    "10852": ("Car break-in or tampering", "vehicle"),
    "10852C": ("Someone casing cars", "vehicle"),
    "487": ("Grand theft", "theft"),
    "488": ("Petty theft", "theft"),
    "314": ("Indecent exposure", "harassment"),
    "646": ("Stalking", "harassment"),
    "594": ("Vandalism", "vandalism"),
    "594GR": ("Graffiti", "vandalism"),
    "TAGGER": ("Tagging in progress", "vandalism"),
    "451": ("Arson", "vandalism"),
    "20001": ("Hit-and-run with injury", "traffic"),
    "20002": ("Hit-and-run", "traffic"),
    "23152": ("Drunk driver", "traffic"),
    "1180": ("Crash with serious injury", "traffic"),
    "DRAGNET": ("Street racing", "traffic"),
}
VARIANTS = (("R", ", reported afterwards"), ("-SUSP", ", suspect still there"))

# How a call ended (SDPD's disposition code). Calls that were cancelled, repeated another call,
# never had an officer sent, or turned out to be nothing are dropped.
DROPPED = {"CAN", "DUP", "V", "W", "X", "U"}
OUTCOMES = {"A": "arrest made", "R": "report taken", "K": "no report taken"}     # by first letter

# The call log's street-type abbreviations that differ from the ones offense reports use.
SUFFIXES = {"BLV": "BLVD", "PKW": "PKWY", "PKY": "PKWY", "WY": "WAY", "AV": "AVE"}


def describe(call_type: str) -> tuple[str, str] | None:
    """Plain wording and tag for an SDPD call type; None when it is not about a possible crime."""
    if call_type in CALLS:
        return CALLS[call_type]
    for ending, wording in VARIANTS:
        base = call_type.removesuffix(ending)
        if base != call_type and base in CALLS:
            return CALLS[base][0] + wording, CALLS[base][1]
    return None


def street(road: str, suffix: str) -> str:
    """'UNIVERSITY' + 'AVE' -> 'UNIVERSITY AVE'. Intersections arrive with the type already in the road."""
    road, suffix = " ".join(road.upper().split()), suffix.strip().upper()
    full = SUFFIXES.get(suffix, suffix)
    if road and full and not road.endswith((" " + full, " " + suffix)):
        road = f"{road} {full}"
    return road


def place(row: dict, gazetteer: Gazetteer | None) -> dict:
    """Where a call was. The log gives a hundred-block or an intersection but no coordinates, so a
    call gets a map point only where the neighborhood's offense records already know that spot."""
    road = street(row["address_road_primary"], row["address_sfx_primary"])
    cross = street(row["address_road_intersecting"], row["address_sfx_intersecting"])
    number = row["address_number_primary"].strip()
    block = int(number) // 100 * 100 if number.isdigit() else 0
    direction = row["address_dir_primary"].strip().upper()
    if direction:                                   # 'W WASHINGTON ST', but '805 SB'
        road = f"{direction} {road}" if len(direction) == 1 else f"{road} {direction}"
    found = None
    if cross:
        found = gazetteer and gazetteer.intersection(road, cross)
        label = pretty_address(f"{road} & {cross}")
    elif block:
        found = gazetteer and gazetteer.block(block, road)
        label = pretty_address(f"{block} {road}")
    else:
        label = pretty_address(road)
    if found:
        return {"place": found["label"], "x": found["x"], "y": found["y"]}
    return {"place": label, "x": None, "y": None}


def load_calls(data_dir: Path, days: int) -> tuple[dict[str, dict], date | None]:
    """Every row from the last `days` days of the log, by incident number, and the day the log runs
    through. The window ends with the newest call anywhere in the city."""
    recent: dict[str, list[dict]] = {}          # day -> its rows; a day is dropped once the window moves past it
    newest = first = ""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()      # a mistyped year must not set the window
    for path in sorted(data_dir.glob(CALLS_GLOB)):
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            head = [h.strip().lower() for h in next(reader, [])]
            if missing := COLUMNS - set(head):
                print(f"  dispatch: {path.name} skipped, it has no {', '.join(sorted(missing))} column")
                continue
            when = head.index("date_time")
            for row in reader:
                if len(row) != len(head):
                    continue
                day = row[when][:10]
                if newest < day <= tomorrow:
                    try:
                        first = (date.fromisoformat(day) - timedelta(days=days - 1)).isoformat()
                    except ValueError:
                        continue
                    newest = day
                    for old in [d for d in recent if d < first]:
                        del recent[old]
                if first <= day <= newest:
                    recent.setdefault(day, []).append(dict(zip(head, row)))
    rows: dict[str, dict] = {}
    for day in sorted(recent):
        for row in recent[day]:
            rows.setdefault(row["incident_num"], row)
    return rows, date.fromisoformat(newest) if newest else None


def export(data_dir: Path, out_dir: Path, beats: list[int], days: int = config.DISPATCH_DAYS) -> dict[int, dict] | None:
    """Write dispatch/<beat>.json for each of `beats`: its possible-crime calls from the last `days`
    days of the log, newest first. Returns what was written, by beat, or None (writing nothing)
    when no call log has been fetched."""
    rows, through = load_calls(data_dir, days)
    if through is None:
        return None
    first = through - timedelta(days=days - 1)

    logged = dict.fromkeys(beats, 0)            # every call in the window, for scale
    kept: dict[int, list] = {beat: [] for beat in beats}
    for incident, row in rows.items():
        try:
            when = datetime.fromisoformat(row["date_time"].strip())
            beat = int(row["beat"])
        except ValueError:
            continue
        if beat not in kept:
            continue
        logged[beat] += 1
        known = describe(row["call_type"].strip().upper())
        disposition = row["disposition"].strip().upper()
        if known and disposition not in DROPPED:
            kept[beat].append((incident, when, known, OUTCOMES.get(disposition[:1]), row))

    written = {}
    for beat, found in kept.items():
        gazetteer = None
        hood_file = out_dir / "hood" / f"{beat}.json"
        if found and hood_file.exists():
            gazetteer = Gazetteer(json.loads(hood_file.read_text(encoding="utf-8"))["places"])
        calls = [{
            "id": incident,
            "date": when.date().isoformat(),
            "day": (when.date() - EPOCH).days,
            "time": when.strftime("%H:%M"),
            "what": what,
            "tag": tag,
            "outcome": outcome,
            **place(row, gazetteer),
        } for incident, when, (what, tag), outcome, row in found]
        calls.sort(key=lambda c: (c["date"], c["time"], c["id"]), reverse=True)
        written[beat] = {
            "beat": beat,
            "days": days,
            "first": first.isoformat(),
            "through": through.isoformat(),
            "logged": logged[beat],
            "calls": calls,
        }
        path = out_dir / "dispatch" / f"{beat}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(written[beat], separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return written
