#!/usr/bin/env python3
"""
State legislative special elections: the one leading indicator on this site.

A generic-ballot poll asks people what they might do. A special election
records what they actually did, with real ballots, in a real district, at a
moment when almost nobody is watching. Comparing that result with how the
same district voted for president in 2024 gives a swing that does not depend
on anyone's turnout model, and in 2017-18 and 2021-22 that swing moved before
the polls did.

The results and the presidential baselines come from MultiState's public
tracker, which is also the source Wikipedia's own special-elections section
cites. The baseline is the part that cannot be had anywhere else for free:
matching a state legislative district to its presidential margin means
aggregating precinct results onto district lines, which is a GIS job rather
than a scrape.

This is published, and deliberately NOT fed into the forecast. Turning a
swing into a forecast needs a coefficient, and the only honest way to get one
is to fit it against past cycles. Until that is done, a number invented for it
would be exactly the kind of guess the backtest work went to some trouble to
remove.

Run it directly:  python3 refresh_specials.py
"""
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime

SOURCE = "https://www.multistate.us/elections/data/special_elections_2026.json"
PAGE = "https://www.multistate.us/elections/special-elections-2026.html"
UA = {"User-Agent": "cd-election-agent/1.0 (https://github.com/Gh0stmahn-ai/cd-election-agent)",
      "Accept-Encoding": "identity"}
OUT = "data/specials_2026.json"
TIMEOUT = 40
RETRIES = 3


def fetch(url):
    last = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                        timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"could not fetch {url}: {last}")


def signed(value):
    """'+30.5' or -46.8 -> float, or None."""
    if value is None:
        return None
    try:
        return float(str(value).replace("+", ""))
    except ValueError:
        return None


def contested(race):
    """Both major parties on the ballot, and a result to read.

    A same-party runoff and an uncontested seat both produce a margin, and
    neither says anything about the national environment.
    """
    parties = {c.get("party") for c in race.get("candidates") or []}
    return ("D" in parties and "R" in parties
            and race.get("margin") is not None
            and signed(race.get("pres_margin")) is not None)


def build():
    raw = fetch(SOURCE)
    races = [r for r in raw.get("races", []) if contested(r)]
    if len(races) < 8:
        raise RuntimeError(f"only {len(races)} contested races with a baseline")

    rows = []
    for race in races:
        pres = signed(race["pres_margin"])
        margin = float(race["margin"])
        rows.append({
            "seat": race.get("seat_label") or f"{race['state']} {race['chamber']} {race['district']}",
            "state": race["state"],
            "date": race["election_date"],
            "votes": race.get("total_votes") or 0,
            "pres_margin": round(pres, 1),
            "margin": round(margin, 1),
            "swing": round(margin - pres, 1),
            "flip": bool(race.get("flip")),
            "prev_party": race.get("prev_party"),
        })
    rows.sort(key=lambda r: r["date"])

    swings = [r["swing"] for r in rows]
    votes = [max(r["votes"], 1) for r in rows]
    weighted = sum(s * v for s, v in zip(swings, votes)) / sum(votes)

    # By quarter, because the 2018 precedent is that an early swing fades as
    # the general election gets closer, and a single median across the whole
    # year would hide exactly that.
    buckets = {}
    for row in rows:
        q = f"{row['date'][:4]} Q{(int(row['date'][5:7]) - 1) // 3 + 1}"
        buckets.setdefault(q, []).append(row["swing"])
    by_quarter = [{"period": q, "n": len(v), "median": round(statistics.median(v), 1)}
                  for q, v in sorted(buckets.items())]

    payload = {
        "as_of": date.today().isoformat(),
        "generated_at": raw.get("generated_at"),
        "n": len(rows),
        "median_swing": round(statistics.median(swings), 1),
        "mean_swing": round(statistics.fmean(swings), 1),
        "weighted_swing": round(weighted, 1),
        "dem_ahead": sum(1 for s in swings if s > 0),
        "flips_to_d": sum(1 for r in rows if r["flip"] and r["prev_party"] == "R"),
        "flips_to_r": sum(1 for r in rows if r["flip"] and r["prev_party"] == "D"),
        "total_races": raw.get("total_races"),
        "upcoming": raw.get("upcoming"),
        "by_quarter": by_quarter,
        "races": rows,
        "note": ("Swing is the special-election margin minus the same district's 2024 "
                 "presidential margin, both Democratic minus Republican. Shown on this site "
                 "and deliberately not fed into the forecast."),
        "sources": [{"name": "MultiState: 2026 special elections and swing analysis", "url": PAGE}],
    }
    return payload


def main():
    try:
        payload = build()
    except Exception as e:  # noqa: BLE001 - a display-only input never fails the run
        print(f"  ! special elections: {e}", file=sys.stderr)
        return 1
    with open(OUT, "w") as fh:
        json.dump(payload, fh, indent=1)
    print(f"  special elections: {payload['n']} contested, Democrats running "
          f"{payload['median_swing']:+.1f} ahead of the 2024 baseline (median), "
          f"{payload['weighted_swing']:+.1f} weighted by turnout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
