"""Download the official SDPD files from the City of San Diego open data portal.

The portal regenerates every yearly file daily, and old records do change (late reports,
reclassifications, deletions), so by default every year is refreshed. A download is written to a
temporary file first and only then swapped in, so a failed run never damages what is already there.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config

STATE_FILE = ".fetch_state.json"
CHUNK = 1 << 20


def sources(years: list[int] | None = None, today: date | None = None) -> list[tuple[str, str]]:
    """(local filename, url) for everything the build needs."""
    today = today or date.today()
    years = years or list(range(config.FIRST_YEAR, today.year + 1))
    files = [(f"pd_nibrs_{y}_datasd.csv", config.NIBRS_URL.format(year=y)) for y in years]
    files.append(("pd_beats_datasd.geojson", config.BEATS_GEOJSON_URL))
    files.append(("pd_beat_codes_list_datasd.csv", config.BEAT_CODES_URL))
    # The dispatch log: this year's file, and last year's while the dashboard's window still reaches
    # into it (the log runs a couple of days behind, hence the extra week).
    reach = today - timedelta(days=config.DISPATCH_DAYS + 7)
    files += [(f"pd_calls_for_service_{y}_datasd.csv", config.CALLS_URL.format(year=y))
              for y in range(reach.year, today.year + 1)]
    return files


def _request(url: str, method: str = "GET"):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": config.USER_AGENT})
    return urllib.request.urlopen(req, timeout=60)


def _load_state(data_dir: Path) -> dict:
    try:
        return json.loads((data_dir / STATE_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(data_dir: Path, state: dict) -> None:
    (data_dir / STATE_FILE).write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def is_current(local_size: int | None, known: dict, remote: dict) -> bool:
    """True when the local copy already matches what the server is offering."""
    if local_size is None or local_size == 0:
        return False
    if remote.get("etag") and known.get("etag"):
        return remote["etag"] == known["etag"] and local_size == known.get("bytes")
    # No record of a previous download (e.g. the file was saved by hand): compare sizes.
    return remote.get("bytes") is not None and remote["bytes"] == local_size


def fetch_one(name: str, url: str, data_dir: Path, state: dict, force: bool = False) -> str:
    """Bring one file up to date. Returns 'downloaded', 'unchanged' or 'missing'.

    Either way the file's entry in `state` gets `checked_at`: when the local copy was last known
    to match the city's. The dashboard counts from it to say how old its copy is (build.last_checked)."""
    dest = data_dir / name
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with _request(url, "HEAD") as r:
            length = r.headers.get("Content-Length")
            remote = {
                "etag": r.headers.get("ETag"),
                "last_modified": r.headers.get("Last-Modified"),
                "bytes": int(length) if length else None,
            }
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):       # the portal answers 403 for files that do not exist yet
            return "missing"
        raise

    local_size = dest.stat().st_size if dest.exists() else None
    if not force and is_current(local_size, state.get(name, {}), remote):
        state[name] = {**state.get(name, {}), **remote, "checked_at": now}
        return "unchanged"

    part = dest.with_name(dest.name + ".part")
    try:
        with _request(url) as r, open(part, "wb") as f:
            while chunk := r.read(CHUNK):
                f.write(chunk)
        size = part.stat().st_size
        if remote["bytes"] is not None and size != remote["bytes"]:
            raise IOError(f"{name}: expected {remote['bytes']} bytes, received {size}")
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)

    state[name] = {**remote, "bytes": size, "fetched_at": now, "checked_at": now}
    return "downloaded"


def fetch(data_dir: Path = config.DATA_DIR, years: list[int] | None = None, force: bool = False) -> dict[str, str]:
    data_dir.mkdir(parents=True, exist_ok=True)
    state = _load_state(data_dir)
    results: dict[str, str] = {}
    for name, url in sources(years):
        started = time.monotonic()
        for attempt in (1, 2, 3):
            try:
                results[name] = fetch_one(name, url, data_dir, state, force)
                break
            except (urllib.error.URLError, IOError, TimeoutError) as e:
                if attempt == 3:
                    results[name] = f"failed ({e})"
                else:
                    time.sleep(2 * attempt)
        _save_state(data_dir, state)
        size = (data_dir / name).stat().st_size / 1e6 if (data_dir / name).exists() else 0
        print(f"  {name:38} {results[name]:12} {size:7.1f} MB  {time.monotonic() - started:5.1f}s", flush=True)
    return results
