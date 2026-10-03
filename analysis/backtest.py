"""Why the dashboard compares periods the way it does: a replay of the past.

    python -m analysis.backtest

Reports reach the data days to months after an offense, so "this period vs the same period last
year" can be computed several ways. This script pretends, for the first of each month since 2022,
that the data ended that day (only reports approved by then exist), estimates the year-over-year
change each way, and compares the estimate with the change computed from everything known today.

  raw        current count / last year's count, both as they looked on the snapshot day
  same-age   current count / last year's count as it looked one year before the snapshot day

"Settle" is how many days before the snapshot the compared window ends. The dashboard uses
same-age with a 30-day settle (SETTLE_DAYS in pipeline/build.py).

Errors are in percentage points of the year-over-year change. Snapshots stop four months before
the newest data so that "everything known today" is close to final for them.
"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb

from pipeline import build, config

WINDOWS = (30, 90, 365)
SETTLES = (0, 30)


def snapshots(first: date, last: date) -> list[date]:
    out, d = [], first
    while d <= last:
        out.append(d)
        d = date(d.year + d.month // 12, d.month % 12 + 1, 1)
    return out


def main() -> None:
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    build.load_offenses(con, config.DATA_DIR)
    build.classify(con)
    build.load_beats(con, config.DATA_DIR)
    build.assign_beats(con)
    through = build.finalize(con)
    home = con.execute("SELECT name FROM beat_names WHERE beat = ?", [config.HOME_BEAT]).fetchone()[0]

    def errors(where: str, window: int, settle: int) -> tuple[list[float], list[float]]:
        raw, same_age = [], []
        for t0 in snapshots(date(2022, 4, 1), through - timedelta(days=120)):
            t, end = f"DATE '{t0}'", f"(DATE '{t0}' - {settle})"
            cur, prior_raw, prior_same, cur_true, prior_true = con.execute(f"""
                SELECT
                  count(*) FILTER (occurred > {end} - {window} AND occurred <= {end} AND approved_day <= {t}),
                  count(*) FILTER (occurred > {end} - {window + 365} AND occurred <= {end} - 365 AND approved_day <= {t}),
                  count(*) FILTER (occurred > {end} - {window + 365} AND occurred <= {end} - 365 AND approved_day <= {t} - 365),
                  count(*) FILTER (occurred > {end} - {window} AND occurred <= {end}),
                  count(*) FILTER (occurred > {end} - {window + 365} AND occurred <= {end} - 365)
                FROM final WHERE {where}
            """).fetchone()
            if min(prior_raw, prior_same, cur_true, prior_true) == 0:
                continue
            truth = cur_true / prior_true - 1
            raw.append(100 * (cur / prior_raw - 1 - truth))
            same_age.append(100 * (cur / prior_same - 1 - truth))
        return raw, same_age

    def describe(xs: list[float]) -> str:
        mean = sum(xs) / len(xs)
        return f"{mean:+6.1f} {sum(abs(x) for x in xs) / len(xs):6.1f} {max(abs(x) for x in xs):6.1f}"

    print(f"Data through {through}. Error of the estimated year-over-year change, in percentage points.")
    for scope, where in ((f"{home} (beat {config.HOME_BEAT})", f"beat = {config.HOME_BEAT}"), ("Whole city", "true")):
        print(f"\n{scope}")
        print(f"{'window':>7} {'settle':>7} | {'raw:   bias    avg    max':>26} | {'same-age: bias  avg    max':>26}")
        for window in WINDOWS:
            for settle in SETTLES:
                raw, same_age = errors(where, window, settle)
                print(f"{window:>6}d {settle:>6}d | {describe(raw):>26} | {describe(same_age):>26}")


if __name__ == "__main__":
    main()
