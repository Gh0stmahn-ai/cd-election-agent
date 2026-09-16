"""Final Cook ratings and actual margins for Senate races, 2018-2022.

Both live in the year article: a "Predictions" table with every rater's last
call, and an "Elections leading to the next Congress" table with the result.
Key on the state (plus "special" where a state ran two races) so the two tables
can be joined.
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_history import wikitext, PVI_RE, PCT_RE, parse_pvi, STATE_CODE

HERE = os.path.dirname(os.path.abspath(__file__))
CYCLES = [2018, 2020, 2022]

RATING_RE = re.compile(r"\{\{\s*USRaceRating\s*\|([^}|]+)(?:\|([^}|]+))?", re.I)
NORM = {"tossup": "tossup", "toss up": "tossup", "tilt": "tossup",
        "lean": "lean", "leans": "lean", "likely": "likely", "favored": "likely",
        "solid": "solid", "safe": "solid"}
PVI_TEXT_RE = re.compile(r"\b([DR])\+(\d+)\b")


def key_for(row, year):
    """State code for the race this row describes, '-S' suffixed for specials."""
    m = re.search(r"\[\[(?:#)?(?:%d United States Senate (special )?election(?:s)? in )?([A-Za-z ]+?)(?:\|([^\]]+))?\]\]" % year, row)
    if not m:
        return None
    name = (m.group(2) or m.group(3) or "").strip()
    special = bool(m.group(1)) or "special" in row[:400].lower()
    code = STATE_CODE.get(name.split("(")[0].strip())
    if not code:
        return None
    return code + ("-S" if special else "")


def ratings_table(text, year):
    for head in ("== Predictions ==", "== Final pre-election predictions =="):
        start = text.find(head)
        if start >= 0:
            break
    else:
        return {}
    end = text.find("\n== ", start + 10)
    out = {}
    for row in text[start:end].split("\n|-"):
        hits = RATING_RE.findall(row)
        if not hits:
            continue
        key = key_for(row, year)
        if not key:
            continue
        kind, lean = hits[0]
        kind = NORM.get(kind.strip().lower())
        if not kind:
            continue
        lean = (lean or "").strip().upper()
        pvi = None
        pm = PVI_RE.search(row)
        if pm:
            pvi = parse_pvi(pm.group(1))
        if pvi is None:
            tm = PVI_TEXT_RE.search(row)
            if tm:
                pvi = float(tm.group(2)) * (1 if tm.group(1) == "D" else -1)
        out[key] = {"rating": kind, "lean": lean if lean in ("D", "R") else None, "pvi": pvi}
    return out


def results_table(text, year):
    out = {}
    for marker in ("=== Elections leading to the next Congress ===",
                   "=== Special elections during the preceding Congress ==="):
        start = text.find(marker)
        if start < 0:
            continue
        end = text.find("\n== ", start + 10)
        for row in text[start:end].split("\n|-"):
            key = key_for(row, year)
            if not key:
                continue
            dem = rep = None
            for label, pct in PCT_RE.findall(row):
                label, pct = label.strip().lower(), float(pct)
                if label.startswith(("democratic", "dfl", "democrat")):
                    dem = pct if dem is None else max(dem, pct)
                elif label.startswith("republican"):
                    rep = pct if rep is None else max(rep, pct)
            if dem is None or rep is None:
                continue
            out.setdefault(key, {"dem_pct": dem, "rep_pct": rep, "margin": round(dem - rep, 2)})
    return out


def main():
    out = {}
    for year in CYCLES:
        text = wikitext(f"{year} United States Senate elections")
        rat, res = ratings_table(text, year), results_table(text, year)
        joined = {k: dict(rat[k], **res[k]) for k in rat if k in res}
        print(f"{year}: {len(rat)} rated, {len(res)} results, {len(joined)} joined")
        missing = sorted(set(rat) - set(res))
        if missing:
            print("   unjoined:", ", ".join(missing))
        out[str(year)] = joined
    path = os.path.join(HERE, "senate_history.json")
    json.dump(out, open(path, "w"), indent=1)
    print("wrote", path)


if __name__ == "__main__":
    main()
