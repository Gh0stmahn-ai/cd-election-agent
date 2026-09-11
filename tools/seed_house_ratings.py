#!/usr/bin/env python3
"""
One-time seed for data/house_districts_2026.json (all 435 seats).

After this seed, the daily agent (agent_run.py -> ingest.refresh_house_rating)
keeps ratings current; this script is kept for transparency about where the
starting numbers came from, not re-run daily.

Sources:
  * Competitive seats (Likely / Lean / Toss-up): Cook Political Report House
    race ratings, updated Aug 25, 2026 (public print view,
    https://www.cookpolitical.com/print/ratings/races/house).
  * Every seat Cook does not list as competitive is treated as Solid for the
    party that holds (or, in a redrawn state, is drawn to win) it. Party
    assignments for those seats come from the 119th Congress delegations,
    adjusted for the ten states that redrew their maps for 2026 (TX, CA, MO,
    NC, OH, UT, FL, TN, LA, AL - see the 2025-2026 redistricting summary on
    Wikipedia/Ballotpedia).
  * Cook's own tally is 181 Solid D / 177 Solid R with 77 rated races; the
    seed reproduces that split to within a few seats (see printout).

Known limitation: district NUMBERS for solid seats in redrawn states (notably
Florida's May 2026 map) are best-available and flagged "number_confidence":
"low"; the party split per state is what the model uses.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "house_districts_2026.json"

SEATS = {
    "AL": 7, "AK": 1, "AZ": 9, "AR": 4, "CA": 52, "CO": 8, "CT": 5, "DE": 1,
    "FL": 28, "GA": 14, "HI": 2, "ID": 2, "IL": 17, "IN": 9, "IA": 4, "KS": 4,
    "KY": 6, "LA": 6, "ME": 2, "MD": 8, "MA": 9, "MI": 13, "MN": 8, "MS": 4,
    "MO": 8, "MT": 2, "NE": 3, "NV": 4, "NH": 2, "NJ": 12, "NM": 3, "NY": 26,
    "NC": 14, "ND": 1, "OH": 15, "OK": 5, "OR": 6, "PA": 17, "RI": 2, "SC": 7,
    "SD": 1, "TN": 9, "TX": 38, "UT": 4, "VT": 1, "VA": 11, "WA": 10, "WV": 2,
    "WI": 8, "WY": 1,
}
REDRAWN_2026 = {"TX", "CA", "MO", "NC", "OH", "UT", "FL", "TN", "LA", "AL"}

# Cook Political Report, Aug 25, 2026: (district, rating, lean, held_by, name)
COOK = [
    # Likely D
    ("CA-21", "likely", "D", "D", "Costa"), ("CA-45", "likely", "D", "D", "Tran"),
    ("IN-1", "likely", "D", "D", "Mrvan"), ("MI-8", "likely", "D", "D", "McDonald Rivet"),
    ("MN-2", "likely", "D", "D", "OPEN (Craig)"), ("NH-1", "likely", "D", "D", "OPEN (Pappas)"),
    ("NJ-9", "likely", "D", "D", "Pou"), ("NV-1", "likely", "D", "D", "Titus"),
    ("NV-4", "likely", "D", "D", "Horsford"), ("NY-19", "likely", "D", "D", "Riley"),
    ("OH-13", "likely", "D", "D", "Sykes"), ("VA-7", "likely", "D", "D", "Vindman"),
    # Lean D
    ("CA-13", "lean", "D", "D", "Gray"), ("CA-48", "lean", "D", "R", "OPEN (Issa)"),
    ("NE-2", "lean", "D", "R", "OPEN (Bacon)"), ("NM-2", "lean", "D", "D", "Vasquez"),
    ("NV-3", "lean", "D", "D", "Lee"), ("NY-3", "lean", "D", "D", "Suozzi"),
    ("NY-4", "lean", "D", "D", "Gillen"), ("OH-1", "lean", "D", "D", "Landsman"),
    ("TX-28", "lean", "D", "D", "Cuellar"),
    # Toss-up (Cook files each under the holding party)
    ("FL-14", "tossup", "D", "D", "Castor"), ("FL-25", "tossup", "D", "D", "Moskowitz"),
    ("OH-9", "tossup", "D", "D", "Kaptur"), ("TX-34", "tossup", "D", "D", "Gonzalez"),
    ("WA-3", "tossup", "D", "D", "Perez"),
    ("AZ-1", "tossup", "R", "R", "OPEN (Schweikert)"), ("AZ-6", "tossup", "R", "R", "Ciscomani"),
    ("CA-22", "tossup", "R", "R", "Valadao"), ("CO-8", "tossup", "R", "R", "Evans"),
    ("IA-1", "tossup", "R", "R", "Miller-Meeks"), ("IA-3", "tossup", "R", "R", "Nunn"),
    ("MI-7", "tossup", "R", "R", "Barrett"), ("MI-10", "tossup", "R", "R", "OPEN (James)"),
    ("NJ-7", "tossup", "R", "R", "Kean Jr."), ("NY-17", "tossup", "R", "R", "Lawler"),
    ("OH-7", "tossup", "R", "R", "Miller"), ("PA-7", "tossup", "R", "R", "Mackenzie"),
    ("PA-8", "tossup", "R", "R", "Bresnahan"), ("PA-10", "tossup", "R", "R", "Perry"),
    ("VA-2", "tossup", "R", "R", "Kiggans"), ("WI-3", "tossup", "R", "R", "Van Orden"),
    # Lean R
    ("FL-22", "lean", "R", "D", "OPEN (Frankel)"), ("IA-2", "lean", "R", "R", "OPEN (Hinson)"),
    ("MI-4", "lean", "R", "R", "Huizenga"), ("NC-1", "lean", "R", "D", "Davis"),
    ("NC-11", "lean", "R", "R", "OPEN (Edwards)"), ("TX-15", "lean", "R", "R", "De La Cruz"),
    ("VA-1", "lean", "R", "R", "Wittman"),
    # Likely R
    ("AK-AL", "likely", "R", "R", "Begich III"), ("AL-2", "likely", "R", "D", "Figures"),
    ("AZ-2", "likely", "R", "R", "Crane"), ("CO-3", "likely", "R", "R", "Hurd"),
    ("CO-5", "likely", "R", "R", "Crank"), ("FL-7", "likely", "R", "R", "OPEN (Mills)"),
    ("FL-9", "likely", "R", "D", "Soto"), ("FL-13", "likely", "R", "R", "Luna"),
    ("FL-27", "likely", "R", "R", "Salazar"), ("KY-6", "likely", "R", "R", "OPEN (Barr)"),
    ("ME-2", "likely", "R", "D", "OPEN (Golden)"), ("MN-1", "likely", "R", "R", "Finstad"),
    ("MT-1", "likely", "R", "R", "OPEN (Zinke)"), ("OH-10", "likely", "R", "R", "Turner"),
    ("OH-15", "likely", "R", "R", "Carey"), ("PA-1", "likely", "R", "R", "Fitzpatrick"),
    ("SC-1", "likely", "R", "R", "OPEN (Mace)"), ("TX-23", "likely", "R", "R", "VACANT"),
    ("TX-35", "likely", "R", "D", "OPEN (Casar)"), ("WI-1", "likely", "R", "R", "Steil"),
]

# Seats NOT on Cook's competitive list that are Solid D; every other
# unlisted seat is Solid R.
SOLID_D = {
    "AL": [7], "AZ": [3, 4, 7], "CA": [n for n in range(1, 53) if n not in (5, 13, 20, 21, 22, 23, 40, 45, 48)],
    "CO": [1, 2, 6, 7], "CT": [1, 2, 3, 4, 5], "DE": ["AL"], "FL": [10, 20, 23, 24],
    "GA": [2, 4, 5, 6, 13], "HI": [1, 2], "IL": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 17],
    "IN": [7], "KS": [3], "KY": [3], "LA": [2], "ME": [1], "MD": [2, 3, 4, 5, 6, 7, 8],
    "MA": list(range(1, 10)), "MI": [3, 6, 11, 12, 13], "MN": [3, 4, 5], "MS": [2], "MO": [1],
    "NH": [2], "NJ": [1, 3, 5, 6, 8, 10, 11, 12], "NM": [1, 3],
    "NY": [5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 18, 20, 22, 25, 26], "NC": [2, 4, 12],
    "OH": [3, 11], "OR": [1, 3, 4, 5, 6], "PA": [2, 3, 4, 5, 6, 12, 17], "RI": [1, 2],
    "SC": [6], "TX": [7, 16, 18, 20, 29, 30, 33, 37], "UT": [1], "VT": ["AL"],
    "VA": [3, 4, 8, 10, 11], "WA": [1, 2, 6, 7, 8, 9, 10], "WI": [2, 4],
}


def main():
    cook = {c[0]: c for c in COOK}
    rows = []
    for st, n in SEATS.items():
        for i in range(1, n + 1):
            num = "AL" if n == 1 else i
            did = f"{st}-{num}"
            low = st in REDRAWN_2026 and st not in ("UT",)
            if did in cook:
                _, rating, lean, held, name = cook[did]
                rows.append({"id": did, "state": st, "district": str(num), "rating": rating,
                             "lean": lean, "held_by": held, "incumbent": name,
                             "redrawn_2026": st in REDRAWN_2026,
                             "number_confidence": "high"})
            else:
                lean = "D" if num in SOLID_D.get(st, []) else "R"
                rows.append({"id": did, "state": st, "district": str(num), "rating": "solid",
                             "lean": lean, "held_by": lean, "incumbent": None,
                             "redrawn_2026": st in REDRAWN_2026,
                             "number_confidence": "low" if low else "high"})
    missing = set(cook) - {r["id"] for r in rows}
    assert not missing, missing
    tally = {}
    for r in rows:
        k = f"{r['rating']} {r['lean']}" if r["rating"] != "tossup" else "tossup"
        tally[k] = tally.get(k, 0) + 1
    payload = {
        "as_of": "2026-08-25",
        "ratings_source": "Cook Political Report House race ratings (Aug 25, 2026); unlisted seats Solid for the favored party",
        "ratings_environment_dem_margin": 5.5,
        "ratings_environment_note": "Generic-ballot average when these ratings were set; the model shifts every seat by how far the current national environment has moved from this reference.",
        "majority": 218,
        "districts": rows,
    }
    OUT.write_text(json.dumps(payload, indent=1))
    print(len(rows), "districts;", dict(sorted(tally.items())))
    print("D-favored (solid+likely+lean D):", sum(1 for r in rows if r["lean"] == "D" and r["rating"] != "tossup"),
          " R-favored:", sum(1 for r in rows if r["lean"] == "R" and r["rating"] != "tossup"),
          " tossups:", tally.get("tossup"))


if __name__ == "__main__":
    main()
