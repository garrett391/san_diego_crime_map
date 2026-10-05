"""Command line entry point:  python -m pipeline <command>"""
from __future__ import annotations

import argparse
import sys

from . import config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="download the official SDPD files (offenses, dispatch log) into ./data")
    p.add_argument("--years", type=int, nargs="+", help="only these years (default: all since 2020)")
    p.add_argument("--force", action="store_true", help="download even if the local copy looks current")

    sub.add_parser("build", help="turn ./data into the JSON the dashboard reads")

    p = sub.add_parser("chatter", help="collect news and Reddit posts about every neighborhood (needs a build first)")
    p.add_argument("--offline", action="store_true", help="re-score what is already stored, fetch nothing")
    p.add_argument("--backfill", action="store_true",
                   help="also dig for older items: Google News by half-year since 2020, Reddit by neighborhood name (run once)")

    p = sub.add_parser("serve", help="open the dashboard at http://localhost:8000")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true", help="do not open a browser tab")

    sub.add_parser("refresh", help="fetch + build + chatter, in that order")

    sub.add_parser("census", help="make census/blocks_2020.csv again (it is in the repository; an 80 MB download)")

    args = parser.parse_args(argv)

    if args.command == "fetch":
        from .fetch import fetch
        results = fetch(years=args.years, force=args.force)
        return 1 if any(r.startswith("failed") for r in results.values()) else 0

    if args.command == "build":
        from .build import build
        build()
        return 0

    if args.command == "chatter":
        from .chatter import collect
        collect(offline=args.offline, backfill=args.backfill)
        return 0

    if args.command == "census":
        from .census import download
        download()
        return 0

    if args.command == "serve":
        from .serve import serve
        serve(args.port, open_browser=not args.no_browser)
        return 0

    if args.command == "refresh":
        from .build import build
        from .chatter import collect
        from .fetch import fetch
        print("Fetching official data...")
        results = fetch()
        failed = [n for n, r in results.items() if r.startswith("failed")]
        if failed:
            print(f"Some downloads failed ({', '.join(failed)}); building from the copies already on disk.")
        print("Building...")
        build()
        # After the build, which is what knows the neighborhoods' names; it has already put what
        # was stored on the page, and this adds what is new.
        print("Collecting chatter (several minutes)...")
        try:
            collect()
        except Exception as e:  # noqa: BLE001 - chatter is optional; never block the official data
            print(f"Chatter collection failed ({e}); keeping what was already stored.")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
