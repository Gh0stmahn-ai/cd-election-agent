#!/usr/bin/env python3
"""
Cook PVI for all 435 districts, on the lines they are actually being elected on.

The first version of this read Cook's published 2025 index, which describes
the maps as they stood before ten states redrew mid-decade. That left the
model with no partisan baseline at all for 181 districts -- a fifth of the
House, and disproportionately the interesting fifth, since a state does not
redraw a map it is happy with.

Wikipedia's per-state tables on the 2026 House elections article carry a
"2026 PVI" column: the index for the new lines, district by district,
including every redrawn state. That is what this reads now.

Writes data/district_pvi.json.  Run:  python3 tools/fetch_pvi.py
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import refresh_polls as RP

PAGE = "2026_United_States_House_of_Representatives_elections"
URL = f"https://en.wikipedia.org/wiki/{PAGE}"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "district_pvi.json")

STATE_CODE = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI",
    "Wyoming": "WY",
}
LOCATION_RE = re.compile(r"^([A-Z][a-z]+(?: [A-Z][a-z]+)*)\s+(\d{1,2}|at-large)\b", re.I)
PVI_RE = re.compile(r"^\s*(EVEN|E|([DR])\s*\+\s*(\d{1,2}))\s*$", re.I)


def cells(row_html):
    return [RP.plain(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S)]


def parse_pvi(text):
    """'R+17' -> -17.0, 'D+7' -> 7.0, 'EVEN' -> 0.0, anything else -> None."""
    m = PVI_RE.match(text or "")
    if not m:
        return None
    if m.group(1).upper() in ("EVEN", "E"):
        return 0.0
    size = float(m.group(3))
    return size if m.group(2).upper() == "D" else -size


def district_id(location):
    """'Alabama 1' -> 'AL-1'; 'Alaska at-large' -> 'AK-AL'."""
    m = LOCATION_RE.match(location.strip())
    if not m:
        return None
    code = STATE_CODE.get(m.group(1).strip())
    if not code:
        return None
    seat = m.group(2)
    return f"{code}-{'AL' if seat.lower() == 'at-large' else int(seat)}"


def harvest(page_html):
    """Every (district, PVI) pair on the page, read by column name.

    The tables do not agree on column order -- some lead with the member,
    some with the index, and the redistricting summary carries both the old
    and the new one -- so the PVI column is located by its heading on each
    table rather than by position. "New 2026 PVI" wins over "Original 2025
    PVI" where a table shows both, which is the whole point of this rewrite.
    """
    found = {}
    for table in re.findall(r"<table.*?</table>", page_html, re.S):
        rows = re.findall(r"<tr.*?</tr>", table, re.S)
        if len(rows) < 3:
            continue
        header, start = None, 0
        for i, row in enumerate(rows[:3]):
            head = [c.lower() for c in cells(row)]
            if any(h.startswith("location") for h in head) and any("pvi" in h for h in head):
                header, start = head, i + 1
                break
        if not header:
            continue

        # Only columns that say 2026. Several states' tables still carry the
        # 2025 index, which describes the old lines, and taking it because it
        # was the only one on offer is precisely the error this rewrite
        # exists to fix: Missouri's 5th reads D+12 under the old map and was
        # deliberately broken up under the new one.
        pvi_cols = [i for i, h in enumerate(header) if "pvi" in h and "2026" in h]
        if not pvi_cols:
            continue

        # The redistricting summary tables are keyed to the MEMBER, not the
        # seat -- "Texas 35, running in the 37th" -- so which district their
        # new index belongs to is ambiguous. They are identifiable by showing
        # the old index alongside, and they are skipped: the per-state tables
        # cover every district they would have contributed, and agree with
        # them everywhere the two overlap.
        if any("original" in h for h in header):
            continue

        i_loc = next(i for i, h in enumerate(header) if h.startswith("location"))
        i_pvi = pvi_cols[0]
        for row in rows[start:]:
            c = cells(row)
            if len(c) <= max(i_loc, i_pvi):
                continue
            district = district_id(c[i_loc])
            value = parse_pvi(c[i_pvi])
            if district and value is not None:
                found.setdefault(district, value)
    return found


def main():
    districts = harvest(RP.fetch(PAGE))
    if len(districts) < 400:
        raise SystemExit(f"only found {len(districts)} districts with a 2026 PVI; not writing")

    seats = json.load(open(os.path.join(os.path.dirname(OUT), "house_districts_2026.json")))
    missing = sorted(d["id"] for d in seats["districts"] if d["id"] not in districts)

    payload = {
        "as_of": RP.datetime.now().date().isoformat(),
        "index": "Cook Partisan Voting Index, 2026 lines",
        "note": ("Democratic minus Republican, in points. Read from the per-state tables on "
                 "Wikipedia's 2026 House elections article, which carry the index for the maps "
                 "actually being used this year. A district absent from this file has no "
                 "published index for its current lines, and the model gives it no partisan "
                 "adjustment rather than a wrong one."),
        "missing": missing,
        "sources": [URL],
        "districts": dict(sorted(districts.items())),
    }
    with open(OUT, "w") as fh:
        json.dump(payload, fh, indent=1)
    lean_d = sum(1 for v in districts.values() if v > 0)
    print(f"wrote {OUT}: {len(districts)} districts "
          f"({lean_d} lean Democratic, {len(districts) - lean_d} lean Republican or even)")
    if missing:
        states = sorted({d.split("-")[0] for d in missing})
        print(f"  no 2026 index published for {len(missing)} districts in {', '.join(states)}; "
              "those keep their rating alone")


if __name__ == "__main__":
    main()
