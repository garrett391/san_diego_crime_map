"""The command line: `refresh` has to run its steps in an order that works."""
from __future__ import annotations

import pipeline.build
import pipeline.chatter
import pipeline.fetch
from pipeline.__main__ import main


def test_refresh_builds_before_it_collects_chatter(monkeypatch, capsys):
    steps = []
    monkeypatch.setattr(pipeline.fetch, "fetch", lambda: steps.append("fetch") or {"pd_nibrs_2026_datasd.csv": "unchanged"})
    monkeypatch.setattr(pipeline.build, "build", lambda: steps.append("build"))
    monkeypatch.setattr(pipeline.chatter, "collect", lambda: steps.append("chatter"))
    assert main(["refresh"]) == 0
    assert steps == ["fetch", "build", "chatter"]        # chatter reads the neighborhoods' names from the build


def test_refresh_survives_a_failed_chatter_run(monkeypatch, capsys):
    def no_network():
        raise OSError("no network")
    monkeypatch.setattr(pipeline.fetch, "fetch", lambda: {})
    monkeypatch.setattr(pipeline.build, "build", lambda: None)
    monkeypatch.setattr(pipeline.chatter, "collect", no_network)
    assert main(["refresh"]) == 0
    assert "keeping what was already stored" in capsys.readouterr().out
