#!/usr/bin/env python3
"""
Campaign money, from the FEC's own bulk file. Free, no key, no scraping.

Money is the one input here that leads rather than lags. A rater moves a seat
when a race has already become competitive; the money usually moved first,
because donors and the party committees are making the same judgement a few
months earlier and have to act on it with a cheque.

The FEC publishes "all candidates" summaries for the cycle as a pipe-delimited
file: receipts, disbursements, cash on hand, and the coverage date of the last
report each campaign filed. This reads it, takes the best-funded Democrat and
the best-funded Republican in each race, and writes what each side has in the
bank.

Like the markets, the attention figures and the special elections, this is
shown and NOT fed into the forecast. Turning a money advantage into a
probability needs a coefficient, and the only honest way to get one is to fit
it against past cycles.

Run it directly:  python3 refresh_fec.py
"""
import io
import json
import os
import sys
import re
import urllib.request
import zipfile
from datetime import date, datetime

SOURCE = "https://www.fec.gov/files/bulk-downloads/2026/weball26.zip"
PAGE = "https://www.fec.gov/data/browse-data/?tab=bulk-data"
UA = {"User-Agent": "cd-election-agent/1.0 (https://github.com/Gh0stmahn-ai/cd-election-agent)"}
BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "money_2026.json")
TIMEOUT = 120
MIN_RECEIPTS = 25_000     # below this it is a filing, not a campaign

# Column numbers from the FEC's own weball layout, one-based as published.
CAND_ID, NAME, ICI, PARTY = 0, 1, 2, 4
RECEIPTS, DISBURSED, COH_END, DEBTS = 5, 7, 10, 16
STATE, DISTRICT, COVERAGE = 18, 19, 27


def fetch():
    req = urllib.request.Request(SOURCE, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        blob = resp.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".txt"))
        return zf.read(name).decode("latin-1")


def number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def coverage(text):
    try:
        return datetime.strptime(text, "%m/%d/%Y").date().isoformat()
    except (TypeError, ValueError):
        return None


def surname(name):
    """Last name, lowercased, from either "Ken Paxton" or "PAXTON, KEN"."""
    name = (name or "").strip()
    if not name:
        return ""
    if "," in name:
        return name.split(",")[0].strip().lower()
    return name.split()[-1].strip().lower()


def tokens(name):
    """Name parts worth matching on, lowercased.

    "ARENHOLZ, ASHLEY HINSON" and "Ashley Hinson" are the same person filing
    under a legal surname, so a strict last-name test fails on exactly the
    races where the answer matters. Matching on any shared part is loose but
    the comparison is already inside one state and one party.
    """
    parts = re.split(r"[^A-Za-z]+", (name or "").lower())
    drop = {"", "jr", "sr", "ii", "iii", "iv", "mr", "mrs", "ms", "dr"}
    return {p for p in parts if p not in drop and len(p) > 2}


def senate_seats():
    """State -> the Senate races on this year's ballot there, with nominees.

    A state can have two: Ohio and Florida are both running a special
    alongside, so keying money by state alone would put Sherrod Brown's
    filings under a regular Ohio race that does not exist. The nominee names
    are what tell the two apart.
    """
    out = {}
    try:
        senate = json.load(open(os.path.join(BASE, "data", "senate_races_2026.json")))
    except (OSError, ValueError):
        return out
    for race in senate.get("races", []):
        # A challenger who would sit as an independent files as one, so there
        # is no Democratic filing to find and nothing has gone wrong.
        independent = (race.get("challenger_caucus") == "independent"
                       or "(I)" in (race.get("dem_candidate") or ""))
        out.setdefault(race["state"], []).append({
            "seat_id": race["seat_id"],
            "D": set() if independent else tokens(race.get("dem_candidate")),
            "R": tokens(race.get("rep_candidate")),
        })
    return out


def senate_key(seats, state, side, name):
    """Which Senate race a filing belongs to, and whether it names the nominee."""
    options = seats.get(state) or []
    who = tokens(name)
    for seat in options:
        if seat[side] and who & seat[side]:
            return seat["seat_id"], True
    if len(options) == 1:
        return options[0]["seat_id"], False
    if not options:
        return f"{state}-2026", False
    # Two races in the state and no name match: the regular one, not the special.
    regular = [s for s in options if "special" not in s["seat_id"]]
    return (regular[0]["seat_id"] if regular else options[0]["seat_id"]), False


def parse(text, seats=None):
    """The candidate in each race on each side, best-funded unless named.

    Best-funded stands in for "the nominee" wherever this project does not
    already hold a name. It is right almost everywhere by September, and
    wrong in exactly the cases the nominee list fixes: a committee that is
    still filing for a seat that is not on the ballot, or a self-funder who
    lost a primary. Each side's name is published with its number either way,
    so the substitution can be checked rather than trusted.
    """
    seats = seats or {}
    races = {}
    for line in text.splitlines():
        row = line.split("|")
        if len(row) < 28 or not row[CAND_ID]:
            continue
        office = row[CAND_ID][0].upper()
        if office not in ("H", "S"):
            continue
        party = (row[PARTY] or "").upper()
        side = "D" if party in ("DEM", "DFL") else "R" if party == "REP" else None
        if side is None:
            continue
        receipts = number(row[RECEIPTS])
        if receipts < MIN_RECEIPTS:
            continue
        state = (row[STATE] or "").upper()
        if len(state) != 2:
            continue
        named = False
        if office == "S":
            key, named = senate_key(seats, state, side, row[NAME])
        else:
            seat = (row[DISTRICT] or "").strip()
            if not seat.isdigit():
                continue
            key = f"{state}-{'AL' if int(seat) == 0 else int(seat)}"
        entry = {
            "name": row[NAME].strip().title(),
            "incumbent": row[ICI].strip().upper() == "I",
            "receipts": round(receipts),
            "spent": round(number(row[DISBURSED])),
            "cash": round(number(row[COH_END])),
            "debts": round(number(row[DEBTS])),
            "as_of": coverage(row[COVERAGE]),
        }
        race = races.setdefault(key, {"chamber": "senate" if office == "S" else "house",
                                      "state": state})
        entry["named"] = named
        current = race.get(side)
        if current is None:
            race[side] = entry
        elif entry["named"] and not current["named"]:
            race[side] = entry
        elif entry["named"] == current["named"] and entry["receipts"] > current["receipts"]:
            race[side] = entry
    return races


def summarise(races):
    out = {}
    for key, race in sorted(races.items()):
        dem, rep = race.get("D"), race.get("R")
        if not dem and not rep:
            continue
        d_cash, r_cash = (dem or {}).get("cash", 0), (rep or {}).get("cash", 0)
        total = d_cash + r_cash
        out[key] = {
            "chamber": race["chamber"], "state": race["state"],
            "dem": dem, "rep": rep,
            # Democratic share of the money in the bank, which is the form the
            # site can draw and compare across races of wildly different size.
            "dem_cash_share": round(d_cash / total, 4) if total > 0 else None,
            "cash_gap": round(d_cash - r_cash),
        }
    return out


def nominee_flags(races, seats):
    """Senate races where the money and this project's candidate list disagree.

    Useful in its own right: a named nominee with no filing above the floor,
    or a side whose best-funded candidate is not the one on record, usually
    means the candidate list has gone stale rather than that the FEC has.
    """
    out = []
    for state, options in sorted(seats.items()):
        for seat in options:
            race = races.get(seat["seat_id"])
            for side, key in (("D", "dem"), ("R", "rep")):
                if not seat[side]:
                    continue
                entry = (race or {}).get(key)
                if entry is None:
                    out.append({"seat_id": seat["seat_id"], "side": side,
                                "issue": "no filing found for the candidate on record"})
                elif not entry["named"]:
                    out.append({"seat_id": seat["seat_id"], "side": side,
                                "issue": f"best funded is {entry['name']}, "
                                         "who is not the candidate on record"})
    return out


def main():
    try:
        seats = senate_seats()
        races = summarise(parse(fetch(), seats))
    except Exception as e:  # noqa: BLE001 - a display-only input never fails the run
        print(f"  ! campaign finance: {e}", file=sys.stderr)
        return 1
    if len(races) < 100:
        print(f"  ! campaign finance: only {len(races)} races parsed", file=sys.stderr)
        return 1

    dates = sorted(c["as_of"] for r in races.values() for c in (r["dem"], r["rep"])
                   if c and c.get("as_of"))
    payload = {
        "as_of": date.today().isoformat(),
        # The median rather than the newest: a handful of monthly filers and
        # terminating committees carry coverage dates that run ahead of what
        # the field as a whole has reported, and quoting those would make the
        # money look fresher than it is.
        "latest_report": dates[len(dates) // 2] if dates else None,
        "n_races": len(races),
        "note": ("Best-funded Democrat and Republican in each race, from the FEC's bulk "
                 "candidate summaries. Cash on hand is as of each campaign's last filed "
                 "report, so races differ by a few weeks. Shown on this site and not fed "
                 "into the forecast."),
        "sources": [{"name": "Federal Election Commission bulk downloads", "url": PAGE}],
        "flags": nominee_flags(races, seats),
        "races": races,
    }
    with open(OUT, "w") as fh:
        json.dump(payload, fh, indent=1)
    senate = sum(1 for r in races.values() if r["chamber"] == "senate")
    print(f"  campaign finance: {len(races)} races ({senate} Senate, "
          f"{len(races) - senate} House), latest report {payload['latest_report']}")
    for flag in payload["flags"]:
        print(f"    ? {flag['seat_id']} {flag['side']}: {flag['issue']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
