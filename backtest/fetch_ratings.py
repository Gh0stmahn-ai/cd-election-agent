"""Final Cook ratings for the competitive seats of 2018, 2020 and 2022.

Wikipedia keeps a separate "election ratings" article per cycle listing every
seat that any rater called something other than solid, with each rater's last
published call. That is the same input the live model takes, so it is what the
backtest has to run on -- a backtest against a different baseline would grade a
model we do not ship.
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_history import wikitext, USHR_RE, PVI_RE, parse_pvi, STATE_CODE

HERE = os.path.dirname(os.path.abspath(__file__))
CYCLES = [2018, 2020, 2022]

RATING_RE = re.compile(r"\{\{\s*USRaceRating\s*\|([^}|]+)(?:\|([^}|]+))?\s*\}\}", re.I)
NORM = {"tossup": "tossup", "toss up": "tossup", "tilt": "tossup",
        "lean": "lean", "leans": "lean", "likely": "likely",
        "solid": "solid", "safe": "solid"}


def parse(year):
    text = wikitext(f"{year} United States House of Representatives election ratings")
    out = {}
    for row in text.split("\n|-"):
        m = USHR_RE.search(row)
        if not m:
            continue
        state = m.group(1).strip().upper()
        if len(state) != 2:
            state = STATE_CODE.get(m.group(1).strip())
        seat = m.group(2).strip().upper()
        if not state or not re.fullmatch(r"(AL|[0-9]+)", seat):
            continue
        district = f"{state}-{'AL' if seat == 'AL' else int(seat)}"
        hits = RATING_RE.findall(row)
        if not hits:
            continue
        kind, lean = hits[0]                      # Cook is the first rating column
        kind = NORM.get(kind.strip().lower())
        if not kind:
            continue
        lean = (lean or "").strip().upper()
        if kind == "tossup":
            lean = lean if lean in ("D", "R") else None
        elif lean not in ("D", "R"):
            continue
        pm = PVI_RE.search(row)
        out[district] = {"rating": kind, "lean": lean,
                         "pvi": parse_pvi(pm.group(1)) if pm else None,
                         "raters": len(hits)}
    return out


def main():
    all_out = {}
    for year in CYCLES:
        r = parse(year)
        counts = {}
        for v in r.values():
            counts[v["rating"]] = counts.get(v["rating"], 0) + 1
        print(f"{year}: {len(r)} competitive seats  {counts}")
        all_out[str(year)] = r
    path = os.path.join(HERE, "house_ratings_history.json")
    json.dump(all_out, open(path, "w"), indent=1)
    print("wrote", path)


if __name__ == "__main__":
    main()
