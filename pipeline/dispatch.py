"""Police dispatch: what SDPD was called to in the home neighborhood over the last few weeks.

The city publishes every call for service (911 and non-emergency calls, plus stops officers start
themselves) as one CSV per year, rewritten daily and about two days behind. `fetch` downloads it
and `build` turns the home neighborhood's recent calls into dispatch.json.

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
from datetime import date, datetime, timedelta, timezone
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


def load_calls(data_dir: Path, beat: int) -> tuple[dict[str, dict], date | None]:
    """Every row logged for one beat, by incident number, and the day the log runs through."""
    rows: dict[str, dict] = {}
    newest = ""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()      # a mistyped year must not set the window
    for path in sorted(data_dir.glob(CALLS_GLOB)):
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            head = [h.strip().lower() for h in next(reader, [])]
            if missing := COLUMNS - set(head):
                print(f"  dispatch: {path.name} skipped, it has no {', '.join(sorted(missing))} column")
                continue
            when, where, key = head.index("date_time"), head.index("beat"), head.index("incident_num")
            for row in reader:
                if len(row) != len(head):
                    continue
                day = row[when][:10]
                if newest < day <= tomorrow:
                    try:
                        date.fromisoformat(day)
                    except ValueError:
                        continue
                    newest = day
                if row[where].strip() == str(beat):
                    rows.setdefault(row[key], dict(zip(head, row)))
    return rows, date.fromisoformat(newest) if newest else None


def export(data_dir: Path = config.DATA_DIR, out_dir: Path = config.OUT_DIR, home_beat: int = config.HOME_BEAT,
           days: int = config.DISPATCH_DAYS) -> dict | None:
    """Write dispatch.json: the home neighborhood's possible-crime calls from the last `days` days
    of the log, newest first. Returns None, and writes nothing, when no call log has been fetched."""
    rows, through = load_calls(data_dir, home_beat)
    if through is None:
        return None
    first = through - timedelta(days=days - 1)

    gazetteer = None
    hood_file = out_dir / "hood" / f"{home_beat}.json"
    if hood_file.exists():
        gazetteer = Gazetteer(json.loads(hood_file.read_text(encoding="utf-8"))["places"])

    calls, logged = [], 0
    for incident, row in rows.items():
        try:
            when = datetime.fromisoformat(row["date_time"].strip())
        except ValueError:
            continue
        if not first <= when.date() <= through:
            continue
        logged += 1
        known = describe(row["call_type"].strip().upper())
        disposition = row["disposition"].strip().upper()
        if not known or disposition in DROPPED:
            continue
        calls.append({
            "id": incident,
            "date": when.date().isoformat(),
            "day": (when.date() - EPOCH).days,
            "time": when.strftime("%H:%M"),
            "what": known[0],
            "tag": known[1],
            "outcome": OUTCOMES.get(disposition[:1]),
            **place(row, gazetteer),
        })
    calls.sort(key=lambda c: (c["date"], c["time"], c["id"]), reverse=True)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "beat": home_beat,
        "days": days,
        "first": first.isoformat(),
        "through": through.isoformat(),
        "logged": logged,           # every call in the window, for scale
        "calls": calls,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dispatch.json").write_text(json.dumps(result, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return result
