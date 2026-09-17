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
CANDIDATES = "https://www.fec.gov/files/bulk-downloads/2026/cn26.zip"
COMMITTEES = "https://www.fec.gov/files/bulk-downloads/2026/cm26.zip"
PAGE = "https://www.fec.gov/data/browse-data/?tab=bulk-data"
UA = {"User-Agent": "cd-election-agent/1.0 (https://github.com/Gh0stmahn-ai/cd-election-agent)"}
BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "money_2026.json")
TIMEOUT = 120
CYCLE = "2026"
MIN_RECEIPTS = 25_000     # below this it is a filing, not a campaign

# Column numbers from the FEC's own weball layout, one-based as published.
CAND_ID, NAME, ICI, PARTY = 0, 1, 2, 4
RECEIPTS, DISBURSED, COH_BOP, COH_END, DEBTS = 5, 7, 9, 10, 16
STATE, DISTRICT, COVERAGE = 18, 19, 27

# The candidate and committee master files, for the same cycle.
CN_ID, CN_PARTY, CN_YEAR, CN_PCC = 0, 2, 3, 9
CM_ID, CM_CAND = 0, 14


def fetch(url=SOURCE):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        blob = resp.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".txt"))
        return zf.read(name).decode("latin-1")


def live_candidates():
    """Which candidate id each campaign committee is actually filing for.

    A member who moves from the House to the Senate keeps the committee and
    gains a second candidate id, and the summary file reports the committee's
    money under both. Left alone that puts a Senate campaign's bank balance
    into a House district: Ben Ray Lujan's Senate account showed up as the
    Democratic money in New Mexico's third, and David Trone's House account
    showed up in a Maryland Senate race he is not running in. Which of the two
    is live is not a guess -- the committee master names the one candidate the
    committee is registered to today, so the other id is dropped.

    Returns None if either master file is unavailable, which means "keep
    everything": a duplicate is a worse answer than the alternative, but no
    money at all is worse than both.
    """
    try:
        master = {}
        for line in fetch(CANDIDATES).splitlines():
            row = line.split("|")
            if len(row) > CN_PCC and row[CN_ID]:
                master[row[CN_ID]] = {"pcc": row[CN_PCC],
                                      "year": row[CN_YEAR].strip(),
                                      "party": row[CN_PARTY].strip().upper()}
        linked = {}
        for line in fetch(COMMITTEES).splitlines():
            row = line.split("|")
            if len(row) > CM_CAND and row[CM_ID] and row[CM_CAND]:
                linked[row[CM_ID]] = row[CM_CAND]
    except Exception as e:  # noqa: BLE001
        print(f"    ? campaign finance: no committee linkage ({e})", file=sys.stderr)
        return None
    if not master or not linked:
        return None
    out = {}
    for cid, rec in master.items():
        out[cid] = {
            # Whether this id is the one its own committee is registered to.
            "live": linked.get(rec["pcc"], cid) == cid if rec["pcc"] else True,
            # The cycle this candidacy is for. A senator two years into a term
            # keeps a committee and keeps filing; the year is what says the
            # filing is not about this November.
            "year": rec["year"],
            # The party, for the filings whose summary row says only "UNK".
            "party": rec["party"],
        }
    return out


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


def person(name):
    """FEC shouting, turned back into a name.

    .title() alone gives "Mcdonald Rivet" and "Lalota", which look like
    misspellings on a published page rather than the formatting artefacts
    they are.
    """
    out = (name or "").strip().title()
    out = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), out)
    out = re.sub(r"\bMac([a-z]{3,})", lambda m: "Mac" + m.group(1).capitalize(), out)
    out = re.sub(r"\bO'([a-z])", lambda m: "O'" + m.group(1).upper(), out)
    out = re.sub(r"\b(Ii|Iii|Iv|Jr|Sr)\b", lambda m: m.group(1).upper()
                 if m.group(1).lower() in ("ii", "iii", "iv") else m.group(1), out)
    return out


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


def house_districts():
    """The 435 seats this project already knows about, by id."""
    try:
        data = json.load(open(os.path.join(BASE, "data", "house_districts_2026.json")))
    except (OSError, ValueError):
        return set()
    return {d["id"] for d in data.get("districts", [])}


def nominee_side(seats, state, name):
    """Which side a Senate filing is on, when only the nominee list knows."""
    who = tokens(name)
    hits = {side for seat in seats.get(state) or []
            for side in ("D", "R") if seat[side] and who & seat[side]}
    return hits.pop() if len(hits) == 1 else None


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
        # A Senate filing in a state with nothing on the ballot: a member two
        # years into a term, or an account left open from the last cycle.
        # Keyed as a race it would be a race that does not exist, so it is
        # only kept when the seat list itself failed to load.
        return (None if seats else f"{state}-2026"), False
    # Two races in the state and no name match: the regular one, not the special.
    regular = [s for s in options if "special" not in s["seat_id"]]
    return (regular[0]["seat_id"] if regular else options[0]["seat_id"]), False


def parse(text, seats=None, live=None, districts=None):
    """The candidate in each race on each side, best-funded unless named.

    Best-funded stands in for "the nominee" wherever this project does not
    already hold a name. It is right almost everywhere by September, and
    wrong in exactly the cases the nominee list fixes: a committee that is
    still filing for a seat that is not on the ballot, or a self-funder who
    lost a primary. Each side's name is published with its number either way,
    so the substitution can be checked rather than trusted.
    """
    seats = seats or {}
    districts = set(districts or ())
    races = {}
    for line in text.splitlines():
        row = line.split("|")
        if len(row) < 28 or not row[CAND_ID]:
            continue
        known = (live or {}).get(row[CAND_ID])
        if known:
            # Somebody else's committee filing under an id this candidate no
            # longer uses, or a candidacy for a different November. Either
            # way the money is real and the race it is filed under is not:
            # Mike Crapo's account, up in 2028, was standing in for Idaho's
            # actual Republican nominee.
            if not known["live"] or (known["year"] and known["year"] != CYCLE):
                continue
        office = row[CAND_ID][0].upper()
        if office not in ("H", "S"):
            continue
        # The summary file leaves the party as "UNK" for two dozen campaigns,
        # among them Idaho's Republican nominee and California's, so the
        # candidate master's own affiliation is the fallback rather than a
        # reason to drop them.
        party = (row[PARTY] or "").upper()
        if party not in ("DEM", "DFL", "REP") and known:
            party = known["party"]
        side = "D" if party in ("DEM", "DFL") else "R" if party == "REP" else None
        state = (row[STATE] or "").upper()
        if side is None and office == "S":
            # Both the summary file and the candidate master record Idaho's
            # Republican nominee as "UNK". A name on the nominee list is a
            # better answer than dropping a $3.4M campaign for want of a
            # party code, and it only fires when the name matches one side
            # and not the other.
            side = nominee_side(seats, state, row[NAME])
        if side is None:
            continue
        receipts = number(row[RECEIPTS])
        if receipts < MIN_RECEIPTS:
            continue
        # What a campaign started the cycle with, plus what it raised, minus
        # what it spent, is what it should have left. Eight filings out of two
        # thousand do not add up -- a Kentucky Republican who raised $5.0M and
        # spent $1.7M reports nothing in the bank, which would draw as a total
        # money shutout in a race that is nothing of the kind. An amount that
        # contradicts the rest of its own report is not a number to publish.
        expected = number(row[COH_BOP]) + receipts - number(row[DISBURSED])
        cash = number(row[COH_END])
        off = abs(cash - expected)
        broken = off > 0.05 * receipts and off > 100_000
        if len(state) != 2:
            continue
        named = False
        if office == "S":
            key, named = senate_key(seats, state, side, row[NAME])
            if key is None:
                continue
        else:
            seat = (row[DISTRICT] or "").strip()
            if not seat.isdigit():
                continue
            key = f"{state}-{'AL' if int(seat) == 0 else int(seat)}"
            # Delegate seats and the odd mistyped district number are filings
            # for something that is not one of the 435.
            if districts and key not in districts:
                continue
        entry = {
            "name": person(row[NAME]),
            "incumbent": row[ICI].strip().upper() == "I",
            "receipts": round(receipts),
            "spent": round(number(row[DISBURSED])),
            "cash": round(cash),
            "debts": round(number(row[DEBTS])),
            "as_of": coverage(row[COVERAGE]),
            "broken": broken,
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

    # A side whose leading campaign filed a balance that contradicts the rest
    # of its own report has no publishable number. Falling through to the
    # runner-up would be worse than showing nothing: Kentucky's sixth would
    # have read as a Democratic money edge built entirely out of leaving the
    # $5.0M Republican out of the arithmetic.
    for race in races.values():
        for side in ("D", "R"):
            entry = race.get(side)
            if entry is None:
                continue
            if entry.pop("broken", False):
                race[side] = None
    for race in races.values():
        for side in ("D", "R"):
            if isinstance(race.get(side), dict):
                race[side].pop("broken", None)
    return drop_double_filers(races)


def drop_double_filers(races):
    """One person, one office.

    A House member running for the Senate can hold two live committees at
    once, and then the summary file reports both -- Kevin Hern is his party's
    money in Oklahoma's first district and in its Senate race at the same
    time. Nobody is on the ballot for two federal offices.

    The test for "the same person" is the whole name and not a shared part of
    one: matching on any token made Michael Rogers of Michigan the same man as
    every other Michael filing in the state, and took his Senate campaign off
    the board. Between the two the nominee list decides where it can, then the
    campaign that filed most recently, and only then the size of the account.
    """
    by_person = {}
    for key, race in races.items():
        for side in ("D", "R"):
            entry = race.get(side)
            if not entry:
                continue
            name = frozenset(tokens(entry["name"]))
            if len(name) < 2:
                continue
            by_person.setdefault((race["state"], side, name), []).append((key, entry))
    for filings in by_person.values():
        if len(filings) < 2:
            continue
        if len({races[k]["chamber"] for k, _ in filings}) < 2:
            continue
        best = max(filings, key=lambda f: (bool(f[1].get("named")),
                                           f[1].get("as_of") or "",
                                           f[1]["cash"]))
        for key, entry in filings:
            if key == best[0]:
                continue
            for side in ("D", "R"):
                if races[key].get(side) is entry:
                    races[key][side] = None
    return races


def summarise(races):
    out = {}
    for key, race in sorted(races.items()):
        dem, rep = race.get("D"), race.get("R")
        if not dem and not rep:
            continue
        # An account can be overdrawn, and a few are. A negative balance is
        # still "nothing in the bank" as far as a share of the two is
        # concerned; left signed it makes the share itself negative, which is
        # not a thing a share can be.
        d_cash = max((dem or {}).get("cash", 0), 0)
        r_cash = max((rep or {}).get("cash", 0), 0)
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
        races = summarise(parse(fetch(), seats, live_candidates(), house_districts()))
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
