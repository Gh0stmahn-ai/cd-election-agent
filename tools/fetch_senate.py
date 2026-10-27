#!/usr/bin/env python3
"""
The 35 Senate races of 2026, read from Wikipedia into data/senate_races_2026.json.

Same source and method as tools/fetch_governors.py. Wikipedia's "2026 United
States Senate elections" article carries:

  * Predictions: each seat's current senator and every forecaster's current
    rating. Cook's is used, as for the House and the governors.
  * Race summary: the candidates on the November ballot with their parties.

The file used to be edited by hand and went stale: Lindsey Graham's death,
his sister's appointment, Cook's move of South Carolina to Lean R, two
incumbents losing their primaries and three Democratic nominees withdrawing
all happened after its last edit. Run daily so that cannot happen again:

    python3 tools/fetch_senate.py

Whoever carries the non-Republican side in a state with no Democrat on the
ballot (an independent) is named in MAIN_INDEPENDENT, checked by hand,
because the article lists every minor independent the same way. A state that
needs one and has none listed stops the write rather than guessing.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import results_feeds as F  # noqa: E402
from tools.ratings_env import environment_on, rating_date, wiki_text  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "senate_races_2026.json"
TITLE = "2026 United States Senate elections"

RATING = re.compile(r"\{\{\s*USRaceRating\s*\|\s*([A-Za-z ]+?)\s*(?:\|\s*([DRI])\s*)?(?:\|[^}]*)?\}\}", re.I)
NORM = {"tossup": "tossup", "toss up": "tossup", "tilt": "lean", "lean": "lean", "leans": "lean",
        "likely": "likely", "solid": "solid", "safe": "solid"}
LINK = re.compile(r"^!(?:[^|\n\[{]*\|)?\s*\[\[\s*2026 United States Senate (special )?election in ([A-Za-z .]+?)\s*(?:\||\]\])",
                  re.M)

# The independent who is the real challenger where no Democrat is on the
# ballot, or where Democrats stood aside for one (checked against the race
# articles and Kalshi's markets, Oct 27 2026). "caucus" is what each has said
# about organizing the Senate; an uncommitted independent is never counted as
# a Democratic seat.
MAIN_INDEPENDENT = {
    "NE": {"name": "Dan Osborn", "caucus": "independent",
           "note": "Challenger is independent Dan Osborn, who says he would not caucus with either party"},
    "ID": {"name": "Todd Achilles", "caucus": "independent",
           "note": "The Democratic nominee withdrew; the challenger is independent Todd Achilles"},
    "SD": {"name": "Brian Bengs", "caucus": "independent",
           "note": "The Democratic nominee withdrew; the challenger is independent Brian Bengs"},
    "MT": {"name": "Seth Bodnar", "caucus": "independent",
           "note": "The main challenger is independent Seth Bodnar; Democrat Alani Bankhead is also on the ballot"},
}

# Specials are their own seats in the model.
SPECIAL_IDS = {"FL": "FL-2026-special", "OH": "OH-2026-special"}


def section(text, heading):
    m = re.search(r"^=+\s*" + heading + r"\s*=+\s*$", text, re.M)
    if not m:
        raise SystemExit(f"no '{heading}' section in the article")
    nxt = re.search(r"^==[^=].*?==\s*$", text[m.end():], re.M)
    return text[m.end(): m.end() + nxt.start() if nxt else len(text)]


def seat_of(row):
    m = LINK.search(row.strip())
    if not m:
        return None, None
    st = F.STATE_CODES.get(m.group(2).strip())
    if not st:
        return None, None
    if m.group(1) or st in SPECIAL_IDS:
        return st, SPECIAL_IDS.get(st, f"{st}-2026-special")
    return st, f"{st}-2026"


def predictions(text):
    out = {}
    for row in section(text, "Predictions").split("\n|-"):
        st, seat = seat_of(row)
        if not st:
            continue
        row = F.REF.sub("", row)
        cook = re.search(r"<!--\s*Cook\s*-->\s*\|\s*(\{\{[^}]*\}\})", row)
        m = RATING.search(cook.group(1)) if cook else None
        if not m:
            continue
        kind = NORM.get(m.group(1).strip().lower())
        lean = (m.group(2) or "").upper() or None
        lines = row.split("\n")
        sen_line = next((ln for ln in lines if "Party shading/" in ln and ("sortname" in ln or "[[" in ln)), "")
        shade = (re.search(r"Party shading/([^}|]+)", sen_line) or [None, ""])[1].lower()
        held = ("D" if "democrat" in shade or "dfl" in shade or "farmer" in shade
                else "R" if "republican" in shade else "I" if "independent" in shade else None)
        m2 = re.search(r"\{\{\s*sortname\s*\|([^|}]+)\|([^|}]+)", sen_line)
        senator = f"{m2.group(1).strip()} {m2.group(2).strip()}" if m2 else None
        status = " ".join(lines).lower()
        out[seat] = {"state": st, "rating": kind, "lean": lean, "held_by": held, "senator": senator,
                     "retiring": any(w in status for w in ("retiring", "not seeking", "lost renomination",
                                                           "died", "resign"))}
    return out


def main():
    text, revid, stamp = wiki_text(TITLE)
    pred = predictions(text)
    summary = F.parse_wiki_senate(section(text, "Race summary"))
    try:
        prev = json.loads(OUT.read_text())
    except (OSError, ValueError):
        prev = {}
    prev_races = {r["seat_id"]: r for r in prev.get("races", [])}
    races, problems = [], []
    for seat in sorted(pred):
        p = pred[seat]
        st = p["state"]
        rows = summary.get(st) or []
        if len(rows) != 1:
            problems.append(f"{seat}: {len(rows)} candidate rows in the race summary")
            continue
        cands = rows[0]
        ds = [c["name"] for c in cands if c["party"] == "D"]
        rs = [c["name"] for c in cands if c["party"] == "R"]
        inds = [c["name"] for c in cands if c["party"] == "O"]
        indep = MAIN_INDEPENDENT.get(st)
        note = ""
        caucus = None
        dem = ds[0] if len(ds) == 1 else None
        if indep:
            if indep["name"] not in inds:
                problems.append(f"{seat}: {indep['name']} is not on the ballot any more ({', '.join(inds)})")
                continue
            dem, caucus, note = f"{indep['name']} (I)", indep["caucus"], indep["note"]
        elif not ds and inds:
            problems.append(f"{seat}: no Democrat on the ballot and no main independent named "
                            f"({', '.join(inds)}); add one to MAIN_INDEPENDENT")
            continue
        if len(ds) > 1 or len(rs) > 1:
            note = (note + "; " if note else "") + f"{len(ds)} Democrats and {len(rs)} Republicans on the ballot"
        rep = rs[0] if len(rs) == 1 else (", ".join(rs) or None)
        if st == "AK" and len(rs) > 1:
            # Alaska's top-four ballot lists every Republican who advanced;
            # the incumbent is the party's candidate.
            rep = p["senator"] if p["senator"] in rs else rs[0]
            note = f"Top-four ballot: {len(rs)} Republicans advanced; {rep} is the incumbent"
        incumbent = p["senator"] if p["senator"] in (dem or "", rep or "") or p["senator"] in ds + rs else None
        if incumbent is None and p["senator"]:
            note = (note + "; " if note else "") + f"Open: {p['senator']} not on the ballot"
        if seat in SPECIAL_IDS.values():
            note = (note + "; " if note else "") + "Special election"
        lean = p["lean"] or p["held_by"]
        race = {"state": st, "seat_id": seat, "held_by": p["held_by"], "rating": p["rating"],
                "lean": lean if lean in ("D", "R") else ("R" if p["held_by"] == "R" else "D"),
                "incumbent": incumbent, "dem_candidate": dem, "rep_candidate": rep, "notes": note}
        if caucus:
            race["challenger_caucus"] = caucus
        if p["rating"] is None:
            problems.append(f"{seat}: no Cook rating")
            continue
        races.append(race)
    if len(races) != 35:
        problems.append(f"expected 35 races, read {len(races)}")
    # The environment on the day Cook published these ratings (see ratings_env).
    head = section(text, "Predictions")
    cook_head = re.search(r"\[\[(?:The )?Cook Political Report\|Cook\]\][^\n]*", head)
    cook_day = rating_date(cook_head.group(0)) if cook_head else None
    env = environment_on(cook_day) if cook_day else None
    if env is None:
        env = prev.get("ratings_environment_dem_margin")
    if problems:
        for p in problems:
            print("  PROBLEM:", p)
        print(f"  not written; {OUT.name} left as it was")
        return 1
    payload = dict(prev)
    payload.update({
        "as_of": date.today().isoformat(),
        "ratings_source": (f"Cook Political Report Senate ratings"
                           f"{' of ' + cook_day.strftime('%b %d, %Y').replace(' 0', ' ') if cook_day else ''}, "
                           f"via Wikipedia (revision {revid}, {stamp[:10]})"),
        "ratings_date": cook_day.isoformat() if cook_day else None,
        "ratings_environment_dem_margin": env,
        "races": races,
    })
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    changed = [r for r in races if (prev_races.get(r["seat_id"], {}).get("rating"), prev_races.get(r["seat_id"], {}).get("lean"),
                                    prev_races.get(r["seat_id"], {}).get("dem_candidate"),
                                    prev_races.get(r["seat_id"], {}).get("rep_candidate"))
               != (r["rating"], r["lean"], r["dem_candidate"], r["rep_candidate"])]
    print(f"{len(races)} races written to {OUT.name}; {len(changed)} changed")
    for r in changed:
        o = prev_races.get(r["seat_id"], {})
        print(f"  {r['seat_id']}: {o.get('rating')} {o.get('lean')} {o.get('dem_candidate')} v {o.get('rep_candidate')}"
              f"  ->  {r['rating']} {r['lean']} {r['dem_candidate']} v {r['rep_candidate']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
