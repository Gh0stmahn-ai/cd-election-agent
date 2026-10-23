#!/usr/bin/env python3
"""
The 36 governor races of 2026, read from Wikipedia into data/governor_races_2026.json.

Wikipedia's "2026 United States gubernatorial elections" article carries the two
tables this needs:

  * Predictions: every state's Cook PVI, its governor, the last result, and each
    forecaster's current rating. Cook's is used, as it is for the Senate and the
    House, so the three chambers are rated by the same hand.
  * Race summary: the governor's status (term-limited, retiring, renominated)
    and the candidates on the November ballot with their parties.

Run it whenever the ratings move:  python3 tools/fetch_governors.py
Anything it cannot settle on its own (a state with two Republicans in a
top-four general, say) is written into the race's notes, never guessed.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import results_feeds as F  # noqa: E402  (shared Wikipedia reader and parsers)

OUT = Path(__file__).resolve().parent.parent / "data" / "governor_races_2026.json"
TITLE = "2026 United States gubernatorial elections"

RATING = re.compile(r"\{\{\s*USRaceRating\s*\|\s*([A-Za-z ]+?)\s*(?:\|\s*([DRI])\s*)?(?:\|[^}]*)?\}\}", re.I)
NORM = {"tossup": "tossup", "toss up": "tossup", "tilt": "lean", "lean": "lean", "leans": "lean",
        "likely": "likely", "solid": "solid", "safe": "solid"}
PVI = re.compile(r"\{\{\s*Shading PVI\s*\|\s*([DR]|EVEN)\s*(?:\|\s*(\d+))?", re.I)

# The fourteen governorships not on the ballot in 2026 and the party holding
# each going in (Wikipedia: 26 Republican and 24 Democratic governors, of whom
# 18 and 18 are up). Delaware, Kentucky, New Jersey, North Carolina, Virginia
# and Washington are Democratic; the rest Republican.
NOT_UP = {"DE": "D", "KY": "D", "NJ": "D", "NC": "D", "VA": "D", "WA": "D",
          "IN": "R", "LA": "R", "MS": "R", "MO": "R", "MT": "R", "ND": "R", "UT": "R", "WV": "R"}


def section(text, heading):
    m = re.search(r"^=+\s*" + heading + r"\s*=+\s*$", text, re.M)
    if not m:
        raise SystemExit(f"no '{heading}' section in the article")
    nxt = re.search(r"^==[^=].*?==\s*$", text[m.end():], re.M)
    return text[m.end(): m.end() + nxt.start() if nxt else len(text)]


def state_of(row):
    m = re.search(r"^!(?:[^|\n\[{]*\|)?\s*\[\[[^\]|]*\|([A-Za-z .]+?)\]\]", row.strip(), re.M)
    return F.STATE_CODES.get(m.group(1).strip()) if m else None


def name_in(cell):
    m = re.search(r"\{\{\s*sortname\s*\|([^|}]+)\|([^|}]+)", cell)
    if m:
        return f"{m.group(1).strip()} {m.group(2).strip()}"
    m = re.search(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", cell)
    return (m.group(2) or m.group(1)).strip() if m else None


def predictions(text):
    out = {}
    for row in section(text, "Predictions").split("\n|-"):
        st = state_of(row)
        if not st:
            continue
        row = F.REF.sub("", row)
        cook = re.search(r"<!--\s*Cook\s*-->\s*\|\s*(\{\{[^}]*\}\})", row)
        m = RATING.search(cook.group(1) if cook else row)
        if not m:
            continue
        kind = NORM.get(m.group(1).strip().lower())
        lean = (m.group(2) or "").upper() or None
        pv = PVI.search(row)
        pvi = None
        if pv:
            pvi = 0.0 if pv.group(1).upper() == "EVEN" else float(pv.group(2) or 0) * (1 if pv.group(1).upper() == "D" else -1)
        lines = row.split("\n")
        gov_line = next((ln for ln in lines if "sortname" in ln or "Party shading" in ln and "[[" in ln), "")
        shade = (re.search(r"Party shading/([^}|]+)", gov_line) or [None, ""])[1].lower()
        held = ("D" if "democrat" in shade or "farmer" in shade or "dfl" in shade
                else "R" if "republican" in shade else "I" if "independent" in shade else None)
        out[st] = {"rating": kind, "lean": lean, "pvi": pvi, "incumbent": name_in(gov_line), "held_by": held,
                   "term_limited": "term-limited" in row.lower() or "term limited" in row.lower()}
    return out


def summary(text):
    out = {}
    for row in section(text, "Race summary").split("\n|-"):
        st = state_of(row)
        if not st or st in out:
            continue
        cands = F.parse_wiki_candidates(row)
        row = F.REF.sub("", row)
        status = ""
        for cell in row.split("\n|"):
            low = cell.lower()
            if any(w in low for w in ("term-limited", "renominated", "retiring", "incumbent", "lost renomination")):
                status = re.sub(r"\{\{[^{}]*\}\}|\[\[|\]\]|'''", "", cell.split("|")[-1]).strip()
                break
        out[st] = {"candidates": cands, "status": status}
    return out


def main():
    text, revid, stamp = F.wiki_content(TITLE)
    pred, summ = predictions(text), summary(text)
    races, problems = [], []
    for st in sorted(pred):
        p = pred[st]
        s = summ.get(st, {"candidates": [], "status": ""})
        ds = [c["name"] for c in s["candidates"] if c["party"] == "D"]
        rs = [c["name"] for c in s["candidates"] if c["party"] == "R"]
        notes = []
        if len(ds) > 1:
            notes.append(f"{len(ds)} Democrats on the ballot")
        if len(rs) > 1:
            notes.append(f"{len(rs)} Republicans on the ballot (top-four general)" if st == "AK"
                         else f"{len(rs)} Republicans on the ballot")
        if not ds:
            notes.append("no Democrat on the ballot")
        if not rs:
            notes.append("no Republican on the ballot")
        lean = p["lean"] or p["held_by"]
        if p["rating"] is None:
            problems.append(f"{st}: no Cook rating")
            continue
        races.append({
            "race_id": f"{st}-GOV", "state": st, "held_by": p["held_by"], "rating": p["rating"],
            "lean": lean if lean in ("D", "R") else ("R" if (p["pvi"] or 0) < 0 else "D"),
            "pvi": p["pvi"], "incumbent": p["incumbent"],
            "open_seat": p["term_limited"] or "retir" in s["status"].lower() or "not seek" in s["status"].lower(),
            "status": s["status"],
            "dem_candidate": ", ".join(ds) or None, "rep_candidate": ", ".join(rs) or None,
            "notes": "; ".join(notes),
        })
    if len(races) != 36:
        problems.append(f"expected 36 races, read {len(races)}")
    up = {r["state"] for r in races}
    if up & set(NOT_UP):
        problems.append(f"states both on the ballot and listed as not up: {sorted(up & set(NOT_UP))}")
    # The national environment when these ratings were read, which is the
    # point the model moves them from as the environment shifts (the Senate
    # and House files carry the same field).
    try:
        env = json.loads((OUT.parent.parent / "iterations" / "latest.json").read_text())["environment"]["dem_margin"]
    except (OSError, ValueError, KeyError):
        env = None
    # Only a re-rating resets that point. If Cook has not moved a race since the
    # last read, the environment those ratings were made in has not changed
    # either, and the model keeps moving them by everything that has happened since.
    try:
        prev = json.loads(OUT.read_text())
        same = ({r["race_id"]: (r["rating"], r["lean"]) for r in prev["races"]}
                == {r["race_id"]: (r["rating"], r["lean"]) for r in races})
        if same and prev.get("ratings_environment_dem_margin") is not None:
            env = prev["ratings_environment_dem_margin"]
    except (OSError, ValueError, KeyError):
        pass
    payload = {
        "as_of": date.today().isoformat(),
        "ratings_environment_dem_margin": env,
        "ratings_source": f"Cook Political Report governor ratings, via Wikipedia (revision {revid}, {stamp[:10]})",
        "not_up": {"D": sum(1 for v in NOT_UP.values() if v == "D"),
                   "R": sum(1 for v in NOT_UP.values() if v == "R"), "states": NOT_UP},
        "races": races,
        "issues": problems,
    }
    if problems:
        # A half-read article must not replace a good file: the model would
        # silently drop the missing races. Keep yesterday's and say why.
        for p in problems:
            print("  PROBLEM:", p)
        print(f"  not written; {OUT.name} left as it was")
        return 1
    OUT.write_text(json.dumps(payload, indent=1))
    by = {}
    for r in races:
        by.setdefault(r["rating"], []).append(f"{r['state']}({r['lean']})")
    print(f"{len(races)} races written to {OUT.name}")
    for k in ("tossup", "lean", "likely", "solid"):
        print(f"  {k:7} {len(by.get(k, [])):2}  {' '.join(by.get(k, []))}")
    for r in races:
        if r["notes"]:
            print(f"  note {r['state']}: {r['notes']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
