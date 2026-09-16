"""Pull district-level House results and the PVI of the day from Wikipedia.

Each "YYYY United States House of Representatives elections" article carries, for
every state, a table with one row per district: the district id, the Cook PVI in
force that cycle, and the general-election percentages for each candidate. That
is everything the backtest needs -- a partisan baseline and an outcome -- in one
request per cycle.
"""
import json, os, re, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
UA = "cd-election-agent/1.0 (https://github.com/Gh0stmahn-ai/cd-election-agent; research)"
API = "https://en.wikipedia.org/w/api.php"

YEARS = [2012, 2014, 2016, 2018, 2020, 2022, 2024]


def wikitext(page, tries=6):
    cache = os.path.join(RAW, re.sub(r"\W+", "_", page) + ".wikitext")
    if os.path.exists(cache):
        return open(cache).read()
    url = f"{API}?action=parse&page={urllib.parse.quote(page)}&prop=wikitext&format=json&formatversion=2"
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            text = json.load(urllib.request.urlopen(req, timeout=90))["parse"]["wikitext"]
            os.makedirs(RAW, exist_ok=True)
            open(cache, "w").write(text)
            time.sleep(1.5)
            return text
        except Exception as exc:
            print(f"  retry {i + 1}: {exc}", file=sys.stderr)
            time.sleep(4 * (i + 1))
    raise SystemExit(f"could not fetch {page}")


PVI_RE = re.compile(r"\{\{\s*Shading PVI\s*\|([^}]*)\}\}", re.I)
USHR_RE = re.compile(r"\{\{\s*ushr\s*\|([^}|]+)\|([^}|]+)(?:\|[^}]*)?\}\}", re.I)
PCT_RE = re.compile(r"\(([A-Za-z .'\-]+?)\)\s*([0-9]+(?:\.[0-9]+)?)%")
REF_RE = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.I | re.S)


def strip_refs(text):
    """Footnotes sit between a candidate's party and their percentage.

    In several cycles the citation is written inline -- "(Republican)<ref
    name=...>...</ref> 64.0%" -- which puts arbitrary text between the two
    things PCT_RE needs adjacent. Dropping the refs first is what makes those
    years parse at all.
    """
    return REF_RE.sub("", text)
INC_PARTY_RE = re.compile(r"Party shading/(?:Text/(Democratic|Republican)|(Democratic|Republican)/Text)")
OPEN_WORDS = ("retir", "new seat", "open seat", "not seek", "resign", "died",
              "lost renomination", "lost in the primary", "lost primary",
              "vacant", "seat eliminated")

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


def parse_pvi(arg):
    """{{Shading PVI|R|15}} -> -15.0 ; {{Shading PVI|EVEN}} -> 0.0"""
    parts = [p.strip() for p in arg.split("|") if p.strip()]
    if not parts:
        return None
    head = parts[0].upper()
    if head in ("EVEN", "TIE", "E"):
        return 0.0
    if head not in ("D", "R"):
        return None
    if len(parts) < 2:
        return None
    try:
        size = float(re.sub(r"[^0-9.]", "", parts[1]))
    except ValueError:
        return None
    return size if head == "D" else -size


def parse_row(row):
    """One table row -> (district, pvi, dem_pct, rep_pct) or None."""
    row = strip_refs(row)
    m = USHR_RE.search(row)
    if not m:
        return None
    state = m.group(1).strip().upper()
    if len(state) != 2:
        state = STATE_CODE.get(m.group(1).strip())
    seat = m.group(2).strip().upper()
    if not state or not re.fullmatch(r"(AL|[0-9]+)", seat):
        return None
    district = f"{state}-{'AL' if seat == 'AL' else int(seat)}"

    pvi = None
    pm = PVI_RE.search(row)
    if pm:
        pvi = parse_pvi(pm.group(1))

    ip = INC_PARTY_RE.search(row)
    inc_party = (ip.group(1) or ip.group(2)) if ip else None

    # "Incumbent redistricted and re-elected" is not an open seat, so a bare
    # keyword scan is not enough: the incumbent running again always says
    # "re-elected", win or lose.
    low = row.lower()
    open_seat = "re-elected" not in low and "lost re-election" not in low \
        and any(w in low for w in OPEN_WORDS)

    dem = rep = None
    for label, pct in PCT_RE.findall(row):
        label = label.strip().lower()
        pct = float(pct)
        if label.startswith("democratic") or label.startswith("dfl") or label.startswith("democrat"):
            dem = pct if dem is None else max(dem, pct)
        elif label.startswith("republican"):
            rep = pct if rep is None else max(rep, pct)
    return district, pvi, dem, rep, inc_party, open_seat


def parse_year(year):
    """Keep the richest row per district.

    The article repeats districts in summary tables -- "closest races", "open
    seats" -- that carry no PVI and no percentages. Those rows come first in
    the page, so taking the first match silently threw away the real result for
    exactly the competitive seats the backtest cares most about. Score each row
    and keep the best one instead.
    """
    text = wikitext(f"{year} United States House of Representatives elections")
    best = {}
    for row in text.split("\n|-"):
        parsed = parse_row(row)
        if not parsed:
            continue
        district, pvi, dem, rep, inc_party, open_seat = parsed
        rec = {
            "district": district,
            "pvi": pvi,
            "dem_pct": dem,
            "rep_pct": rep,
            "incumbent_party": inc_party,
            "open_seat": open_seat,
            "margin": None if dem is None or rep is None else round(dem - rep, 2),
            "contested": dem is not None and rep is not None,
        }
        score = ((pvi is not None) * 2 + (dem is not None) + (rep is not None)
                 + (inc_party is not None))
        if district not in best or score > best[district][0]:
            best[district] = (score, rec)
    return [rec for _, rec in sorted(best.values(), key=lambda kv: kv[1]["district"])]


def main():
    out = {}
    for year in YEARS:
        rows = parse_year(year)
        contested = [r for r in rows if r["contested"]]
        withpvi = [r for r in rows if r["pvi"] is not None]
        print(f"{year}: {len(rows)} districts, {len(contested)} contested, {len(withpvi)} with PVI")
        out[str(year)] = rows
    path = os.path.join(HERE, "house_history.json")
    json.dump(out, open(path, "w"), indent=1)
    print("wrote", path)


if __name__ == "__main__":
    main()
