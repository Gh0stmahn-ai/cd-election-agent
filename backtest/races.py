"""Assemble every backtested race into one list.

Two tiers, because the sources support two different things.

FULL cycles are the ones where Wikipedia publishes a Cook PVI for all 435
districts, so the whole map can be rebuilt: the safe seats included. Those are
the only cycles that can say anything about the Solid bucket.

Every cycle has a ratings article, and a ratings article by construction lists
the competitive seats. So the wider set -- seven cycles rather than three --
covers exactly the races whose outcome was ever in doubt, which is where a
forecast lives and where the national miss is worth measuring.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from model import STATE_DIVISION

FULL = ["2018", "2020", "2022"]
ALL = ["2012", "2014", "2016", "2018", "2020", "2022", "2024"]
PARTY_LEAN = {"Democratic": "D", "Republican": "R"}


def _load(name):
    return json.load(open(os.path.join(HERE, name)))


def _lean(rating, lean, pvi, incumbent_party=None, open_seat=True):
    """A race's favoured party.

    Everything except a toss-up states one. Cook's toss-ups do not, and the
    convention the live data files use is the party that currently holds the
    seat, so that is the first fallback; the district's own partisanship is the
    second, for seats whose holder we could not read.
    """
    if lean in ("D", "R"):
        return lean
    if not open_seat and incumbent_party in PARTY_LEAN:
        return PARTY_LEAN[incumbent_party]
    if pvi:
        return "D" if pvi > 0 else "R"
    return None


def house_races():
    results, ratings = _load("house_history.json"), _load("house_ratings_history.json")
    rows = []
    for year in ALL:
        rated = ratings.get(year, {})
        by_id = {d["district"]: d for d in results.get(year, [])}
        ids = set(by_id) if year in FULL else set(rated)
        for district in sorted(ids):
            d = by_id.get(district)
            if not d or not d["contested"] or d["margin"] is None:
                continue
            state = district.split("-")[0]
            if state not in STATE_DIVISION:
                continue
            r = rated.get(district)
            rating = r["rating"] if r else "solid"
            pvi = (r or {}).get("pvi")
            if pvi is None:
                pvi = d["pvi"]
            lean = _lean(rating, (r or {}).get("lean"), pvi,
                         d["incumbent_party"], d["open_seat"])
            if lean is None:
                continue
            rows.append({"chamber": "house", "year": year, "id": district,
                         "state": state, "division": STATE_DIVISION[state],
                         "rating": rating, "lean": lean, "pvi": pvi,
                         "actual": d["margin"], "full": year in FULL,
                         "competitive": rating != "solid"})
    return rows


def senate_races():
    rows = []
    for year, races in _load("senate_history.json").items():
        for seat, r in races.items():
            state = seat.split("-")[0]
            if state not in STATE_DIVISION:
                continue
            lean = _lean(r["rating"], r["lean"], r["pvi"])
            if lean is None:
                continue
            rows.append({"chamber": "senate", "year": year, "id": seat,
                         "state": state, "division": STATE_DIVISION[state],
                         "rating": r["rating"], "lean": lean, "pvi": r["pvi"],
                         "actual": r["margin"], "full": year in FULL,
                         "competitive": r["rating"] != "solid"})
    return rows


def all_races():
    return house_races() + senate_races()


if __name__ == "__main__":
    rows = all_races()
    print(f"{len(rows)} races")
    for year in ALL:
        yr = [r for r in rows if r["year"] == year]
        print(f"  {year}: {len(yr):4d} total, {sum(1 for r in yr if r['competitive']):3d} competitive,"
              f" {sum(1 for r in yr if r['chamber'] == 'senate'):3d} senate,"
              f" {sum(1 for r in yr if r['pvi'] is not None):4d} with PVI")
