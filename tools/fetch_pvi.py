#!/usr/bin/env python3
"""
Cook Partisan Voting Index for all 435 districts. Free, no key.

PVI says how a district votes relative to the nation: R+17 means it ran 17
points more Republican than the country. It is the missing ingredient that
lets two seats with the same rating stop being interchangeable.

Using Cook's own index alongside Cook's own ratings is deliberate: the two are
built on the same view of the map, so the index differentiates within a rating
rather than arguing with it.

Writes data/district_pvi.json. Re-run when Cook republishes the index.
"""
import html
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
PAGE = "Cook_Partisan_Voting_Index"
URL = "https://en.wikipedia.org/api/rest_v1/page/html/" + PAGE
UA = {"User-Agent": "cd-election-agent/1.0 (https://github.com/Gh0stmahn-ai/cd-election-agent)"}

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


def text(fragment):
    fragment = re.sub(r"<sup.*?</sup>", " ", fragment, flags=re.S)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).strip()


def parse_pvi(value):
    """'R+17' -> -17.0, 'D+5' -> 5.0, 'EVEN' -> 0.0. Democratic is positive."""
    v = value.strip().upper().replace("−", "-")
    if v.startswith("EVEN") or v == "0":
        return 0.0
    m = re.match(r"([DR])\s*\+\s*(\d+(?:\.\d+)?)", v)
    if not m:
        return None
    n = float(m.group(2))
    return n if m.group(1) == "D" else -n


def district_id(name):
    """'Alabama 1' -> 'AL-1'; 'Alaska at-large' -> 'AK-AL'."""
    label = name.strip()
    m = re.match(r"^(.*?)\s+(\d+|at-large|At-large|AL)$", label)
    if not m:
        return None
    state, seat = m.group(1).strip(), m.group(2)
    code = STATE_CODE.get(state)
    if not code:
        return None
    return f"{code}-AL" if seat.lower() in ("at-large", "al") else f"{code}-{int(seat)}"


def main():
    req = urllib.request.Request(URL, headers=UA)
    page = urllib.request.urlopen(req, timeout=60).read().decode("utf-8")

    table = None
    for candidate in re.findall(r"<table.*?</table>", page, re.S):
        rows = re.findall(r"<tr.*?</tr>", candidate, re.S)
        if len(rows) < 400:
            continue
        header = text(rows[0]).lower()
        if "pvi" in header and "district" in header:
            table = rows
            break
    if table is None:
        print("Could not find the district PVI table on the page.", file=sys.stderr)
        return 1

    pvi, skipped = {}, []
    for row in table[1:]:
        cells = [text(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue
        seat, score = district_id(cells[0]), parse_pvi(cells[1])
        if seat is None or score is None:
            skipped.append(cells[0])
            continue
        pvi[seat] = score

    if len(pvi) < 400:
        print(f"Only parsed {len(pvi)} districts; refusing to overwrite.", file=sys.stderr)
        return 1

    payload = {
        "as_of": date.today().isoformat(),
        "source": "Cook Partisan Voting Index, via Wikipedia",
        "url": f"https://en.wikipedia.org/wiki/{PAGE}",
        "note": ("Democratic-positive points. A district's presidential lean relative to the "
                 "nation. Districts redrawn for 2026 may still carry their previous lines' "
                 "index, which is the same limitation the House map already documents."),
        "districts": dict(sorted(pvi.items())),
    }
    (DATA / "district_pvi.json").write_text(json.dumps(payload, indent=1))
    lean_d = sum(1 for v in pvi.values() if v > 0)
    print(f"Wrote {len(pvi)} district PVI scores ({lean_d} Democratic-leaning, "
          f"{len(pvi) - lean_d} Republican-leaning).")
    if skipped:
        print(f"Skipped {len(skipped)}: {', '.join(skipped[:5])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
