"""Final Cook ratings and actual margins for governor races, 2010-2024.

Same idea as fetch_senate.py: each year's "United States gubernatorial
elections" article has a predictions table with every rater's last call and a
race summary with the result. Older years write a rating as plain shaded text
("Solid R") rather than a template, and mark the Cook column with an HTML
comment ("<!--Coo-->"), so both spellings are read. Cook's column is used
where it can be identified; otherwise the first rating in the row, which in
these tables is Cook's.
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fetch_history import wikitext, PVI_RE, parse_pvi, strip_refs, STATE_CODE  # noqa: E402
import results_feeds as F  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CYCLES = [2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024]

TEMPLATE = re.compile(r"\{\{\s*USRaceRating\s*\|\s*([A-Za-z ]+?)\s*(?:\|\s*([DRI])\b[^}]*)?\}\}", re.I)
TEXT = re.compile(r"\|\s*(Safe|Solid|Likely|Lean|Leans|Tilt|Toss[- ]?up)\s*([DRI])?\s*(?:\(flip\))?\s*$", re.I | re.M)
SORTED = re.compile(r"\{\{\s*sort\s*\|\s*\d+\s*\|\s*(Safe|Solid|Likely|Lean|Leans|Tilt|Toss ?-? ?up)\s*([DRI])?\s*\}\}", re.I)
NORM = {"tossup": "tossup", "toss up": "tossup", "toss-up": "tossup", "tilt": "lean", "lean": "lean",
        "leans": "lean", "likely": "likely", "solid": "solid", "safe": "solid"}
PVI_TEXT = re.compile(r"\b([DR])\+(\d+)\b")


def section(text, pattern):
    m = re.search(r"^=+ *(?:" + pattern + r") *=+\s*$", text, re.M)
    if not m:
        return ""
    nxt = re.search(r"^==[^=].*?==\s*$", text[m.end():], re.M)
    return text[m.end(): m.end() + nxt.start() if nxt else len(text)]


def state_of(row):
    m = re.search(r"^!(?:[^|\n\[{]*\|)?\s*\[\[[^\]|]*?\|?([A-Za-z .]+?)\]\]", row.strip(), re.M)
    if m:
        return STATE_CODE.get(m.group(1).strip())
    m = re.search(r"^!\s*([A-Z][A-Za-z .]+?)\s*$", row.strip(), re.M)     # 2010: a bare name
    return STATE_CODE.get(m.group(1).strip()) if m else None


def _one(chunk):
    for rx in (TEMPLATE, SORTED, TEXT):
        m = rx.search(chunk)
        if m:
            kind = NORM.get(re.sub(r"[\s-]+", " ", m.group(1).strip().lower()))
            if kind == "toss up" or (kind is None and "toss" in m.group(1).lower()):
                kind = "tossup"
            if kind:
                return kind, (m.group(2) or "").upper() or None
    return None


def cook_column(table):
    """Which of the raters' columns is Cook's, from the table's own header."""
    head = table.split("\n|-\n!", 1)[0] if "\n|-\n!" in table else table[:4000]
    names = [ln for ln in head.split("\n") if ln.startswith("!")]
    raters = [ln for ln in names if not re.search(r"(?i)\b(state|incumbent|governor|last|result|pvi|party|candidates)\b", ln)]
    for i, ln in enumerate(raters):
        if "cook" in ln.lower():
            return i
    return 0


def rating_of(row, cook_idx=0):
    """Cook's call in one predictions row: the column marked as Cook's where the
    table marks it, otherwise the Cook-numbered rating cell."""
    cook = re.search(r"<!--\s*Coo[^>]*-->(.*?)(?=<!--|\n\||\Z)", row, re.S)
    if cook:
        got = _one(cook.group(1))
        if got:
            return got
    cells = [c for c in re.split(r"\n\s*(?:<!--[^>]*-->)?\s*\|", row) if _one("|" + c.split("\n")[0])]
    if len(cells) > cook_idx:
        return _one("|" + cells[cook_idx].split("\n")[0])
    return None, None


def ratings(text):
    out = {}
    body = strip_refs(section(text, r"(?:Election )?[Pp]redictions"))
    cook_idx = cook_column(body)
    for row in body.split("\n|-"):
        st = state_of(row)
        if not st:
            continue
        kind, lean = rating_of(row, cook_idx) or (None, None)
        if not kind:
            continue
        pvi = None
        pm = PVI_RE.search(row)
        if pm:
            pvi = parse_pvi(pm.group(1))
        if pvi is None:
            tm = PVI_TEXT.search(row)
            if tm:
                pvi = float(tm.group(2)) * (1 if tm.group(1) == "D" else -1)
        inc = next((ln for ln in row.split("\n") if "[[" in ln and "hading" in ln and "#" not in ln), "")
        held = "D" if "shading/Democratic" in inc else "R" if "shading/Republican" in inc else None
        out[st] = {"rating": kind, "lean": lean if lean in ("D", "R") else None, "pvi": pvi, "held_by": held}
    return out


def results(text):
    out = {}
    body = section(text, r"States") or section(text, r"Race summary")
    for row in body.split("\n|-"):
        st = state_of(row)
        if not st or st in out:
            continue
        cands = F.parse_wiki_candidates(row)
        ds = [c["pct"] for c in cands if c["party"] == "D" and c["pct"] is not None]
        rs = [c["pct"] for c in cands if c["party"] == "R" and c["pct"] is not None]
        others = [c["pct"] for c in cands if c["party"] == "O" and c["pct"] is not None]
        if not ds or not rs:
            continue
        d, r = max(ds), max(rs)
        # A race an independent led or came second in is not a D-versus-R
        # contest, and its margin says nothing about the ratings being tested.
        if others and max(others) > min(d, r):
            continue
        out[st] = {"dem_pct": d, "rep_pct": r, "margin": round(d - r, 2)}
    return out


def main():
    out = {}
    for year in CYCLES:
        text = wikitext(f"{year} United States gubernatorial elections")
        rat, res = ratings(text), results(text)
        joined = {k: dict(rat[k], **res[k]) for k in rat if k in res}
        print(f"{year}: {len(rat)} rated, {len(res)} results, {len(joined)} joined"
              + (f"  (unjoined: {', '.join(sorted(set(rat) - set(res)))})" if set(rat) - set(res) else ""))
        out[str(year)] = joined
    path = os.path.join(HERE, "governor_history.json")
    json.dump(out, open(path, "w"), indent=1)
    print("wrote", path)


if __name__ == "__main__":
    main()
