"""Serve the dashboard locally. It is plain static files, so any web server works; this one just
saves typing and stops the browser from caching the data between rebuilds."""
from __future__ import annotations

import functools
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import config


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".js": "text/javascript", ".mjs": "text/javascript",
                      ".json": "application/json", ".geojson": "application/geo+json"}

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - quiet unless something fails
        if args and str(args[1])[:1] in "45":
            super().log_message(format, *args)


def serve(port: int = 8000, open_browser: bool = True) -> None:
    if not (config.OUT_DIR / "meta.json").exists():
        raise SystemExit("No data yet. Run:  python -m pipeline refresh")
    handler = functools.partial(Handler, directory=str(config.SITE_DIR))
    url = f"http://localhost:{port}"
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"Dashboard at {url}   (Ctrl+C to stop)")
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print()
