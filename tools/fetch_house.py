#!/usr/bin/env python3
"""
All 435 House races of 2026, read from Wikipedia into data/house_districts_2026.json.

Two articles:

  * "2026 United States House of Representatives election ratings": every
    seat any forecaster calls competitive, with Cook's current rating in its
    own column. A seat that is not listed is Solid for the party its 2026
    Cook PVI favours, as Cook itself treats it.
  * "2026 United States House of Representatives elections": a table per
    state with each district's PVI, its incumbent and party, whether the
    incumbent is running, and the candidates on the November ballot.

This replaces the one-time seed of August (tools/seed_house_ratings.py),
whose ratings were meant to be kept current by hand and were not. Run daily:

    python3 tools/fetch_house.py

The district ids must match the ones the map and the model already use; a
read that disagrees with them, or that finds fewer than 435 districts, is not
written.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import results_feeds as F  # noqa: E402
from tools.ratings_env import environment_on, rating_date, wiki_text  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "house_districts_2026.json"
RATINGS_TITLE = "2026 United States House of Representatives election ratings"
RACES_TITLE = "2026 United States House of Representatives elections"

USHR = re.compile(r"\{\{\s*ushr\s*\|\s*([A-Z]{2})\s*\|\s*([A-Za-z0-9]+)\s*\|", re.I)
RATING = re.compile(r"\{\{\s*USRaceRating\s*\|\s*([A-Za-z ]+?)\s*(?:\|\s*([DRI])\b[^}]*)?\}\}", re.I)
PVI = re.compile(r"\{\{\s*shading PVI\s*\|\s*([DR]|EVEN)\s*(?:\|\s*(\d+))?", re.I)
DELEGATES = {"AS", "DC", "GU", "MP", "PR", "VI"}     # non-voting, not modelled
NORM = {"tossup": "tossup", "toss up": "tossup", "tilt": "lean", "lean": "lean", "leans": "lean",
        "likely": "likely", "solid": "solid", "safe": "solid"}


def district_id(st, num):
    num = num.upper()
    return f"{st.upper()}-{'AL' if num in ('AL', '0', 'AT-LARGE') else str(int(num)) if num.isdigit() else num}"


def cook_ratings(text):
    """{district id: (rating, lean)} from the ratings article, plus Cook's date."""
    head = re.search(r"\[\[Cook Political Report\|Cook\]\][^\n]*", text)
    day = rating_date(head.group(0)) if head else None
    out = {}
    for row in text.split("\n|-"):
        m = USHR.search(row)
        if not m:
            continue
        cells = RATING.findall(F.REF.sub("", row))
        if not cells:
            continue
        kind, lean = cells[0]           # Cook's is the first ratings column
        kind = NORM.get(re.sub(r"\s+", " ", kind.strip().lower()))
        if kind:
            out[district_id(m.group(1), m.group(2))] = (kind, (lean or "").upper() or None)
    return out, day


def party_word(cell):
    low = cell.lower()
    if "democratic" in low or "dfl" in low:
        return "D"
    if "republican" in low:
        return "R"
    if "independent" in low:
        return "I"
    return None


def name_of(cell):
    m = re.search(r"\{\{\s*sortname\s*\|([^|}]+)\|([^|}]+)", cell)
    if m:
        return f"{m.group(1).strip()} {m.group(2).strip()}"
    m = re.search(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", cell)
    return (m.group(2) or m.group(1)).strip() if m else None


def races(text):
    """{district id: {...}} from the per-state tables of the main article."""
    first = re.search(r"\n==\s*Alabama\s*==", text)
    text = text[first.start():] if first else text
    out = {}
    for row in re.split(r"\n\|-", text):
        row = F.REF.sub("", row)
        head = row.strip().split("\n", 1)[0]
        if head.startswith("!") and USHR.search(head):
            m = USHR.search(head)
            if m.group(1).upper() in DELEGATES:
                continue
            did = district_id(m.group(1), m.group(2))
            pv = PVI.search(row)
            pvi = None
            if pv:
                pvi = 0.0 if pv.group(1).upper() == "EVEN" else float(pv.group(2) or 0) * (1 if pv.group(1).upper() == "D" else -1)
            cells = [c for c in row.split("\n|")[1:]]
            new_seat = "new seat" in row.lower()
            member = next((c for c in cells if "sortname" in c), "")
            party_cell = next((c for c in cells if re.search(r"\|\s*(Democratic|Republican|Independent|DFL)\s*$", c.strip())), "")
            cands = F.parse_wiki_candidates(row)
            running = not any(w in row.lower() for w in ("retiring", "lost renomination", "resign", "died",
                                                          "new representative to be elected", "to run for"))
            out[did] = {"pvi": pvi, "incumbent_name": None if new_seat else name_of(member),
                        "incumbent_party": None if new_seat else party_word(party_cell.split("|")[-1]),
                        "running": running and not new_seat,
                        "dem": [c["name"] for c in cands if c["party"] == "D"],
                        "rep": [c["name"] for c in cands if c["party"] == "R"],
                        "other": [c["name"] for c in cands if c["party"] == "O"]}
    return out


def main():
    rat_text, rat_rev, rat_stamp = wiki_text(RATINGS_TITLE)
    race_text, race_rev, race_stamp = wiki_text(RACES_TITLE)
    cook, cook_day = cook_ratings(rat_text)
    found = races(race_text)
    prev = json.loads(OUT.read_text())
    known = {d["id"]: d for d in prev["districts"]}
    problems = []
    missing = sorted(set(known) - set(found))
    extra = sorted(set(found) - set(known))
    if missing or extra:
        problems.append(f"district ids differ from the map's: missing {missing[:10]}, extra {extra[:10]}")
    if len(cook) < 40:
        problems.append(f"only {len(cook)} rated seats read from the ratings article")
    districts, changes = [], []
    for did, old in known.items():
        r = found.get(did)
        if not r:
            continue
        held = r["incumbent_party"] or old.get("held_by")
        if did in cook:
            kind, lean = cook[did]
            if kind == "tossup" or lean not in ("D", "R"):
                lean = held if held in ("D", "R") else ("R" if (r["pvi"] or 0) < 0 else "D")
        else:
            kind = "solid"
            lean = ("R" if r["pvi"] < 0 else "D") if r["pvi"] else (held if held in ("D", "R") else old["lean"])
        d = dict(old)
        d.update({"rating": kind, "lean": lean, "held_by": held if held in ("D", "R") else old.get("held_by"),
                  "incumbent": r["incumbent_name"] if r["running"] else None,
                  "dem_candidate": ", ".join(r["dem"]) or None, "rep_candidate": ", ".join(r["rep"]) or None,
                  "number_confidence": "high"})
        if r["pvi"] is not None:
            d["pvi_2026"] = r["pvi"]
        d.pop("note", None)
        if (old["rating"], old["lean"]) != (kind, lean):
            changes.append(f"{did}: {old['rating']} {old['lean']} -> {kind} {lean}")
        districts.append(d)
    if len(districts) != 435:
        problems.append(f"expected 435 districts, built {len(districts)}")
    if problems:
        for p in problems:
            print("  PROBLEM:", p)
        print(f"  not written; {OUT.name} left as it was")
        return 1
    env = environment_on(cook_day) if cook_day else None
    payload = dict(prev)
    payload.update({
        "as_of": date.today().isoformat(),
        "ratings_source": (f"Cook Political Report House ratings"
                           f"{' of ' + cook_day.strftime('%b %d, %Y').replace(' 0', ' ') if cook_day else ''}, via "
                           f"Wikipedia (revision {rat_rev}); unlisted seats Solid for the party the 2026 PVI favours"),
        "ratings_date": cook_day.isoformat() if cook_day else None,
        "ratings_environment_dem_margin": env if env is not None else prev.get("ratings_environment_dem_margin"),
        "ratings_environment_note": ("The national environment on the day these ratings were published; the "
                                     "model shifts every seat by how far it has moved since."),
        "candidates_source": f"Wikipedia, {RACES_TITLE} (revision {race_rev}, {race_stamp[:10]})",
        "districts": sorted(districts, key=lambda d: (d["state"], int(d["district"]) if d["district"].isdigit() else 0)),
    })
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    from collections import Counter
    tally = Counter((d["rating"], d["lean"]) for d in districts)
    print(f"435 districts written; {len(cook)} rated by Cook; {len(changes)} ratings changed")
    print("  " + ", ".join(f"{k[0]} {k[1]}: {v}" for k, v in sorted(tally.items())))
    for c in changes:
        print("  ", c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
