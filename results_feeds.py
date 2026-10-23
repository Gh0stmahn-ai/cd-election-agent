#!/usr/bin/env python3
"""
Election-night results, read from the sources that publish them for free.

Two kinds of source, doing two different jobs:

  * OFFICIAL STATE FEEDS give live vote counts and how much of the vote is in.
    Seven states publish one that is machine-readable, keyless and open to
    automated reading: Georgia, North Carolina, Texas, Iowa, Nebraska, Alaska
    and Florida. Between them they cover seven Senate races and about a
    hundred House districts. Michigan, Ohio and Kansas sit behind bot walls
    that refuse automated readers; Maine and New Hampshire have no statewide
    election-night system at all. Nothing here tries to get around either.

  * WIKIPEDIA gives race CALLS for all 470 races, because its editors mark a
    winner (with {{Aye}}) as soon as the networks call a race, and the 2026
    Senate and House articles carry every race in one page each. It is slower
    than a wire service and occasionally wrong for a few minutes, which is why
    results.py only accepts a call that has stood for several minutes and only
    after the state's polls have closed.

No paid source (AP, Decision Desk) and no scraping of news sites, whose terms
forbid it. A free aggregator that was considered, CivicAPI, was left out after
it was found serving made-up Pennsylvania counts weeks before the election.

Every adapter returns a list of contests in one shape:

    {"race_id": "GA-2026" | "GA-5", "chamber": "senate" | "house", "state": "GA",
     "candidates": [{"name", "party" (D/R/O), "party_raw", "votes"}],
     "reporting": 0..1 or None, "reporting_label": "1,204 of 2,626 precincts",
     "as_of": ISO time or None, "source": "ga"}

or None when the source has not changed since the last read, so the caller can
skip the work. An adapter that fails raises; the caller records the error and
carries on with everything else.
"""
import base64
import csv
import hashlib
import io
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
UA = "cd-election-agent/1.0 (+https://github.com/Gh0stmahn-ai/cd-election-agent)"
TIMEOUT = 20   # a feed that has not answered in 20 s will not answer in 45
ELECTION_DAY = "2026-11-03"
AT_LARGE = {"AK", "DE", "ND", "SD", "VT", "WY"}

# Alaska's results server sends the wrong intermediate certificate, so a strict
# client cannot build a chain to a trusted root. The right intermediate is
# public: the results workflow downloads it from GlobalSign before the night
# starts, and adding it to the trust store lets the chain complete WITHOUT
# turning verification off. (It is only an intermediate: a chain through it
# still has to end at GlobalSign's root, which the system already trusts, so a
# tampered download could not make anything trusted that is not.)
EXTRA_CA = BASE / "certs" / "globalsign-gcc-r46-ov-tls-ca-2025.pem"


def _context():
    ctx = ssl.create_default_context()
    if EXTRA_CA.exists():
        ctx.load_verify_locations(cafile=str(EXTRA_CA))
    return ctx


SSL = _context()


def get(url, binary=False, headers=None):
    """GET with a named user agent, one retry on a rate limit, honest errors."""
    hdrs = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    hdrs.update(headers or {})
    for attempt in range(2):
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL) as resp:
                body = resp.read()
                # Some of these servers gzip without saying so.
                if resp.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
                    import gzip
                    body = gzip.decompress(body)
                return body if binary else body.decode(resp.headers.get_content_charset() or "utf-8",
                                                       errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                wait = min(int(e.headers.get("Retry-After") or 5), 10)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("unreachable")


def get_json(url, headers=None):
    text = get(url, headers=headers).lstrip("\ufeff")
    try:
        return json.loads(text)
    except ValueError:
        # Say what came back instead: a block page reads very differently from
        # a half-written file, and the error is shown on the Results page.
        snippet = " ".join(text[:90].split()) or "an empty response"
        raise ValueError(f"not JSON from {urllib.parse.urlsplit(url).netloc}: {snippet!r}") from None


def party_code(raw):
    r = re.sub(r"[^A-Z]", "", str(raw or "").upper())
    if r in ("DEM", "D", "DEMOCRATIC", "DEMOCRAT", "DFL"):
        return "D"
    if r in ("REP", "R", "REPUBLICAN", "GOP"):
        return "R"
    return "O"


def house_id(state, district):
    """'TX', '28' -> 'TX-28'; at-large states -> 'AK-AL'."""
    if state in AT_LARGE:
        return f"{state}-AL"
    n = int(re.sub(r"\D", "", str(district)) or 0)
    return f"{state}-{'AL' if n == 0 else n}"


def clean_name(name):
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    name = re.sub(r"\s*\((?:I|Rep|Dem|REP|DEM|Ind|IND|Lib|LIB|NON|UND|NOM|AIP|GRN)\)\s*", " ", name).strip()
    return name


# A governor's contest in a general-election file: "Governor", "GOVERNOR",
# "Governor / Lieutenant Governor", "For Governor and Lt. Governor". Not
# "Lieutenant Governor" on its own, not a primary's "Governor - Rep", and not
# Massachusetts's "Governor's Council".
GOV_RE = re.compile(r"^(?:for\s+)?governor\b(?!['\u2019]s)(?!\s*-\s*(?:rep|dem))", re.I)


def gov_id(state):
    return f"{state}-GOV"


def contest(race_id, chamber, state, candidates, reporting, label, as_of, source):
    if chamber == "governor":
        # Tickets are listed as "Jim Pillen and Joe Kelly" or "Name / Running mate".
        for c in candidates:
            c["name"] = re.split(r"\s+and\s+|\s*/\s*", c["name"])[0].strip()
    return {"race_id": race_id, "chamber": chamber, "state": state,
            "candidates": [c for c in candidates if c["name"] and "write" not in c["name"].lower()
                           or c["votes"]],
            "reporting": None if reporting is None else max(0.0, min(1.0, reporting)),
            "reporting_label": label, "as_of": as_of, "source": source}


def _fmt(n):
    return f"{int(n):,}"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ======================================================================= GA
GA_API = "https://results.sos.ga.gov/results/public/api"


def fetch_ga(cache, senate_seat):
    """Georgia's Enhanced Voting site. Poll the small metadata call, fetch the
    full payload only when its asOf moves (the site's own front end does the
    same, every two minutes)."""
    eid = cache.get("eid")
    if not eid:
        jur = get_json(f"{GA_API}/jurisdictions/Georgia")
        hits = [e for e in jur.get("elections", []) if str(e.get("electionDate", ""))[:10] == ELECTION_DAY]
        if not hits:
            raise LookupError("Georgia has not published the November 3 election yet")
        eid = cache["eid"] = hits[0]["publicElectionId"]
    meta = get_json(f"{GA_API}/elections/Georgia/{eid}")
    as_of = meta.get("asOf") or (meta.get("election") or {}).get("asOf")
    if as_of and as_of == cache.get("as_of"):
        return None
    data = get_json(f"{GA_API}/elections/Georgia/{eid}/data")
    cache["as_of"] = as_of
    return parse_ga(data, senate_seat)


def parse_ga(data, senate_seat):
    out = []
    as_of = (data.get("election") or {}).get("asOf")
    for item in data.get("ballotItems", []):
        name = item["name"][0]["text"].strip()
        if name == "US Senate":
            rid, chamber = senate_seat, "senate"
        elif GOV_RE.match(name):
            rid, chamber = gov_id("GA"), "governor"
        else:
            m = re.fullmatch(r"US House of Representatives - District (\d+)", name)
            if not m:
                continue
            rid, chamber = house_id("GA", m.group(1)), "house"
        if not rid:
            continue
        rs = item.get("reportingStatus") or {}
        units, total = rs.get("reportingUnits"), rs.get("totalUnits")
        cands = []
        for o in (item.get("summaryResults") or {}).get("ballotOptions", []):
            party = ((o.get("party") or {}).get("abbreviation")) or ""
            cands.append({"name": clean_name(o["name"][0]["text"]), "party": party_code(party),
                          "party_raw": party, "votes": int(o.get("voteCount") or 0)})
        out.append(contest(rid, chamber, "GA", cands,
                           (units / total) if total else None,
                           f"{units} of {total} counties" if total else None, as_of, "ga"))
    return out


# ======================================================================= NC
NC_BASE = "https://er.ncsbe.gov/enr"


def _nc_bust():
    t = datetime.now(timezone.utc)
    return f"?v={t.day}-{t.hour}-{t.minute}"


def fetch_nc(cache, senate_seat):
    """North Carolina's State Board publishes JSON (in .txt files) on S3. The
    statewide row of county.txt carries the publish time, so it is the cheap
    change detector; results_0.txt is the statewide file itself."""
    folder = ELECTION_DAY.replace("-", "")
    county = get_json(f"{NC_BASE}/{folder}/data/county.txt{_nc_bust()}")
    state_row = next((r for r in county if str(r.get("cid")) == "0"), {})
    stamp = state_row.get("imp")
    if stamp and stamp == cache.get("stamp"):
        return None
    rows = get_json(f"{NC_BASE}/{folder}/data/results_0.txt{_nc_bust()}")
    cache["stamp"] = stamp
    return parse_nc(rows, senate_seat, stamp)


def parse_nc(rows, senate_seat, stamp=None):
    as_of = None
    if stamp:
        try:
            # "November 3, 2026 8:59 pm", Eastern
            from zoneinfo import ZoneInfo
            as_of = (datetime.strptime(stamp, "%B %d, %Y %I:%M %p")
                     .replace(tzinfo=ZoneInfo("America/New_York")).isoformat())
        except ValueError:
            as_of = None
    groups = {}
    for r in rows:
        if r.get("ogl") != "FED":
            continue
        name = r.get("cnm", "")
        if name.startswith("US SENATE"):
            rid, chamber = senate_seat, "senate"
        else:
            m = re.match(r"US HOUSE OF REPRESENTATIVES DISTRICT (\d+)", name)
            if not m:
                continue
            rid, chamber = house_id("NC", m.group(1)), "house"
        if not rid:
            continue
        g = groups.setdefault(rid, {"chamber": chamber, "cands": [], "prt": 0, "ptl": 0})
        g["cands"].append({"name": clean_name(r.get("bnm")), "party": party_code(r.get("pty")),
                           "party_raw": r.get("pty"), "votes": int(r.get("vct") or 0)})
        g["prt"], g["ptl"] = int(r.get("prt") or 0), int(r.get("ptl") or 0)
    return [contest(rid, g["chamber"], "NC", g["cands"],
                    g["prt"] / g["ptl"] if g["ptl"] else None,
                    f"{_fmt(g['prt'])} of {_fmt(g['ptl'])} precincts" if g["ptl"] else None,
                    as_of, "nc")
            for rid, g in groups.items()]


# ======================================================================= TX
TX_API = "https://goelect.txelections.civixapps.com/api-ivis-system/api/s3/enr"


def _b64json(value):
    return json.loads(base64.b64decode(value)) if isinstance(value, str) else value


def fetch_tx(cache, senate_seat):
    """The Secretary of State's civix site. Each publish bumps Version, so an
    unchanged Version means nothing new. The payload is a 1.6 MB bundle with
    the federal races base64-encoded inside it."""
    eid = cache.get("eid")
    if not eid:
        consts = _b64json(get_json(f"{TX_API}/electionConstants")["upload"])
        ge = (consts.get("electionInfo", {}).get("2026", {}) or {}).get("GE", {})
        hits = [v for v in ge.values() if "NOVEMBER" in str(v.get("N", "")).upper()]
        if not hits:
            raise LookupError("Texas has not listed the November general")
        eid = cache["eid"] = hits[0]["ID"]
    bundle = get_json(f"{TX_API}/election/{eid}")
    version = bundle.get("Version")
    if version and version == cache.get("version"):
        return None
    cache["version"] = version
    return parse_tx(bundle, senate_seat)


def parse_tx(bundle, senate_seat):
    home = _b64json(bundle.get("Home")) or {}
    fed = _b64json(bundle.get("Federal")) or {}
    statewide = _b64json(bundle.get("StateWide")) or {}
    plr = (home.get("PollingReporting") or {})
    done, total = plr.get("PLR"), plr.get("PLT")
    as_of = None
    if home.get("LastUpdatedTime"):
        try:
            from zoneinfo import ZoneInfo
            as_of = (datetime.strptime(home["LastUpdatedTime"], "%b %d, %Y %H:%M:%S")
                     .replace(tzinfo=ZoneInfo("America/Chicago")).isoformat())
        except ValueError:
            pass
    out = []
    for race in fed.get("Races", []) + statewide.get("Races", []):
        name = " ".join(str(race.get("N", "")).split())
        if name == "U. S. SENATOR":
            rid, chamber = senate_seat, "senate"
        elif GOV_RE.match(name):
            rid, chamber = gov_id("TX"), "governor"
        else:
            m = re.fullmatch(r"U\. S\. REPRESENTATIVE DISTRICT (\d+)", name)
            if not m:
                continue
            rid, chamber = house_id("TX", m.group(1)), "house"
        if not rid:
            continue
        cands = [{"name": clean_name(c.get("N")), "party": party_code(c.get("P")),
                  "party_raw": c.get("P"), "votes": int(c.get("V") or 0)}
                 for c in race.get("Candidates", []) if str(c.get("P")).upper() != "W"]
        # Texas reports how many polling places are in statewide, not per race,
        # so every Texas race carries the statewide figure and says so.
        out.append(contest(rid, chamber, "TX", cands,
                           (done / total) if total else None,
                           f"{_fmt(done)} of {_fmt(total)} polling places statewide" if total else None,
                           as_of, "tx"))
    return out


# ======================================================================= IA
IA_BASE = "https://electionresults.iowa.gov/IA"


def fetch_ia(cache, senate_seat):
    """Iowa's self-hosted Clarity system: find the election, poll its version
    number, refetch the summary when the version moves."""
    eid = cache.get("eid")
    if not eid:
        elections = get_json(f"{IA_BASE}/elections.json")
        hits = [e for e in elections if "general" in str(e.get("ElectionName", "")).lower()
                and str(e.get("Date", "")).startswith("11/3/2026")]
        if not hits:
            raise LookupError("Iowa has not listed the November 3 general")
        eid = cache["eid"] = hits[0]["EID"]
    ver = get(f"{IA_BASE}/{eid}/current_ver.txt").strip()
    if ver and ver == cache.get("ver"):
        return None
    summary = get_json(f"{IA_BASE}/{eid}/{ver}/json/sum.json")
    cache["ver"] = ver
    return parse_ia(summary, senate_seat, now_iso())


def parse_ia(summary, senate_seat, as_of=None):
    out = []
    for c in summary.get("Contests", []):
        name = str(c.get("C", "")).strip()
        if name == "United States Senator":
            rid, chamber = senate_seat, "senate"
        elif GOV_RE.match(name):
            rid, chamber = gov_id("IA"), "governor"
        else:
            m = re.fullmatch(r"United States Representative District (\d+)", name)
            if not m:
                continue
            rid, chamber = house_id("IA", m.group(1)), "house"
        if not rid:
            continue
        cands = [{"name": clean_name(n), "party": party_code(p), "party_raw": p, "votes": int(v or 0)}
                 for n, p, v in zip(c.get("CH", []), c.get("P", []), c.get("V", []))]
        tp, pr = c.get("TP"), c.get("PR")
        out.append(contest(rid, chamber, "IA", cands, (pr / tp) if tp else None,
                           f"{pr} of {tp} counties" if tp else None, as_of, "ia"))
    return out


# ======================================================================= NE
NE_CSV = "https://electionresults.nebraska.gov/resultsCSV.aspx?text=All&type={t}&map=CTY"


def fetch_ne(cache, senate_seat):
    """Nebraska's CSV export. It carries no publish time of its own, so a hash
    of the two files is the change detector."""
    sw, cg = get(NE_CSV.format(t="SW")), get(NE_CSV.format(t="CG"))
    digest = hashlib.sha256((sw + cg).encode()).hexdigest()
    if digest == cache.get("digest"):
        return None
    cache["digest"] = digest
    return parse_ne(sw, cg, senate_seat, now_iso())


def parse_ne(sw, cg, senate_seat, as_of=None):
    groups = {}
    for text in (sw, cg):
        for row in csv.DictReader(io.StringIO(text)):
            name = " ".join(str(row.get("RaceName", "")).split())
            if name.startswith("For United States Senator"):
                rid, chamber = senate_seat, "senate"
            elif GOV_RE.match(name):
                rid, chamber = gov_id("NE"), "governor"
            elif name.startswith("For Representative in Congress"):
                rid, chamber = house_id("NE", row.get("AreaNum", "")), "house"
            else:
                continue
            if not rid:
                continue
            g = groups.setdefault(rid, {"chamber": chamber, "cands": [], "rep": None})
            g["cands"].append({"name": clean_name(row.get("CandidateName")),
                               "party": party_code(row.get("PartyCode")),
                               "party_raw": row.get("PartyCode"),
                               "votes": int(float(row.get("CandidateVotes") or 0))})
            m = re.search(r"(\d+)\s*/\s*(\d+)", str(row.get("PrecinctsReporting", "")))
            if m:
                g["rep"] = (int(m.group(1)), int(m.group(2)))
    out = []
    for rid, g in groups.items():
        done, total = g["rep"] or (None, None)
        out.append(contest(rid, g["chamber"], "NE", g["cands"],
                           (done / total) if total else None,
                           f"{_fmt(done)} of {_fmt(total)} precincts" if total else None, as_of, "ne"))
    return out


# ======================================================================= AK
# The live results map has lived at /enr26/ through the 2026 primary; the
# archive copy of a finished election sits under /results/<yy>GENR/. Both are
# tried, and only a file that says it is the 2026 general is accepted.
AK_URLS = ["https://www.elections.alaska.gov/enr26/results/statewide.js",
           "https://www.elections.alaska.gov/results/26GENR/map/results/statewide.js"]


def fetch_ak(cache, senate_seat):
    """Alaska's results map reads a JSON file (named .js). Only the general is
    accepted: until the state swaps it in, the same path holds the primary."""
    data, seen = None, []
    for url in ([cache["url"]] if cache.get("url") else []) + AK_URLS:
        try:
            candidate = get_json(f"{url}?d={int(time.time() * 1000)}")
        except (urllib.error.HTTPError, ValueError):
            continue
        name = str(candidate.get("name", ""))
        seen.append(name)
        if "general" in name.lower() and "2026" in name:
            data, cache["url"] = candidate, url
            break
    if data is None:
        raise LookupError(f"Alaska is not serving the 2026 general yet ({', '.join(seen) or 'nothing found'})")
    stamp = data.get("updated")
    if stamp and stamp == cache.get("stamp"):
        return None
    cache["stamp"] = stamp
    return parse_ak(data, senate_seat)


def parse_ak(data, senate_seat):
    names, cnames = data.get("contestNames", []), data.get("candidateNames", [])
    as_of = None
    if data.get("updated"):
        try:
            from zoneinfo import ZoneInfo
            as_of = (datetime.strptime(data["updated"], "%m/%d/%Y %I:%M %p")
                     .replace(tzinfo=ZoneInfo("America/Anchorage")).isoformat())
        except ValueError:
            pass
    out = []
    for t in data.get("totals", []):
        name = names[t["n"]] if t["n"] < len(names) else ""
        if name == "U.S. Senator":
            rid, chamber = senate_seat, "senate"
        elif GOV_RE.match(name):
            rid, chamber = gov_id("AK"), "governor"
        elif name == "U.S. Representative":
            rid, chamber = "AK-AL", "house"
        else:
            continue
        if not rid:
            continue
        cands = []
        for c in t.get("c", []):
            raw = cnames[c["n"]] if c["n"] < len(cnames) else ""
            m = re.search(r"\(([A-Z]{2,4})\)\s*$", raw)
            party = m.group(1) if m else ""
            # "Sullivan, Dan S. (REP)" -> "Dan S. Sullivan"
            base = re.sub(r"\s*\([A-Z]{2,4}\)\s*$", "", raw)
            if "," in base:
                last, first = base.split(",", 1)
                base = f"{first.strip()} {last.strip()}"
            cands.append({"name": clean_name(base), "party": party_code(party), "party_raw": party,
                          "votes": int(c.get("t") or 0)})
        p, r = t.get("p"), t.get("r")
        out.append(contest(rid, chamber, "AK", cands, (r / p) if p else None,
                           f"{r} of {p} precincts (first choices)" if p else None, as_of, "ak"))
    return out


# ======================================================================= FL
FL_URL = ("https://results.elections.myflorida.com/ResultsExtract.Asp?ElectionDate=11/3/2026"
          "&OfficialResults=N&PartyRaces=N&DataMode=&FormsButton2=Download")


def fetch_fl(cache, senate_seat):
    """Florida's tab-separated extract, one row per candidate per county. No
    timestamp inside, so the file hash is the change detector."""
    text = get(FL_URL)
    digest = hashlib.sha256(text.encode()).hexdigest()
    if digest == cache.get("digest"):
        return None
    cache["digest"] = digest
    return parse_fl(text, senate_seat, now_iso())


def parse_fl(text, senate_seat, as_of=None):
    groups = {}
    reader = csv.DictReader(io.StringIO(text.replace("\r\n", "\n")), delimiter="\t")
    for row in reader:
        code = (row.get("RaceCode") or "").strip()
        if code == "USS":
            rid, chamber = senate_seat, "senate"
        elif code == "GOV":
            rid, chamber = gov_id("FL"), "governor"
        elif code == "USR":
            rid, chamber = house_id("FL", row.get("Juris1num", "")), "house"
        else:
            continue
        if not rid:
            continue
        g = groups.setdefault(rid, {"chamber": chamber, "cands": {}, "counties": {}})
        name = " ".join(x for x in (row.get("CanNameFirst"), row.get("CanNameLast")) if x and x.strip() != "_")
        party = (row.get("PartyCode") or "").strip()
        if party == "WRI":
            continue
        key = (name, party)
        c = g["cands"].setdefault(key, {"name": clean_name(name), "party": party_code(party),
                                        "party_raw": party, "votes": 0})
        c["votes"] += int(row.get("CanVotes") or 0)
        # Precinct counts repeat on every candidate row for a county; keep one.
        g["counties"][row.get("CountyCode")] = (int(row.get("PrecinctsReporting") or 0),
                                                int(row.get("Precincts") or 0))
    out = []
    for rid, g in groups.items():
        done = sum(a for a, _ in g["counties"].values())
        total = sum(b for _, b in g["counties"].values())
        out.append(contest(rid, g["chamber"], "FL", list(g["cands"].values()),
                           (done / total) if total else None,
                           f"{_fmt(done)} of {_fmt(total)} precincts" if total else None, as_of, "fl"))
    return out


OFFICIAL = {
    # state: (adapter, seconds between reads)
    "GA": (fetch_ga, 60),
    "NC": (fetch_nc, 120),
    "TX": (fetch_tx, 120),
    "IA": (fetch_ia, 60),
    "NE": (fetch_ne, 120),
    "AK": (fetch_ak, 120),
    "FL": (fetch_fl, 120),
}

SOURCE_NAMES = {
    "ga": ("Georgia Secretary of State", "https://results.sos.ga.gov/results/public/Georgia"),
    "nc": ("North Carolina State Board of Elections", "https://er.ncsbe.gov/"),
    "tx": ("Texas Secretary of State", "https://goelect.txelections.civixapps.com/ivis-enr-ui/races"),
    "ia": ("Iowa Secretary of State", "https://electionresults.iowa.gov/IA/"),
    "ne": ("Nebraska Secretary of State", "https://electionresults.nebraska.gov/"),
    "ak": ("Alaska Division of Elections", "https://www.elections.alaska.gov/election-results/"),
    "fl": ("Florida Division of Elections", "https://results.elections.myflorida.com/"),
    "wikipedia": ("Wikipedia (race calls)", "https://en.wikipedia.org/wiki/2026_United_States_elections"),
}


# ================================================================ Wikipedia
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_PAGES = {
    "senate": "2026 United States Senate elections",
    "house": "2026 United States House of Representatives elections",
    "governor": "2026 United States gubernatorial elections",
}
STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
    "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}
STATE_NAMES_LONG = {v: k for k, v in STATE_CODES.items()}
AYE = re.compile(r"\{\{\s*(?:aye|yes|won)\s*\}\}", re.I)
REF = re.compile(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", re.S | re.I)
PARTY_TAIL = re.compile(r"\(([^()]*)\)\s*(?:(\d+(?:\.\d+)?)\s*%)?\s*$")


def _wiki_api(params):
    """One API call. A lag or rate error arrives as HTTP 200 with an error body,
    so it is checked for explicitly rather than parsed as an empty answer."""
    params = dict(params, format="json", formatversion="2", maxlag="5", redirects="1")
    data = get_json(f"{WIKI_API}?{urllib.parse.urlencode(params)}")
    if "error" in data:
        raise RuntimeError(f"Wikipedia API: {data['error'].get('code')}: {data['error'].get('info', '')[:120]}")
    return data


def wiki_revisions(titles):
    """Latest revision id per page: one cheap call that says whether to read on."""
    data = _wiki_api({"action": "query", "prop": "info", "titles": "|".join(titles)})
    norm = {n["to"]: n["from"] for n in data.get("query", {}).get("redirects", [])}
    return {norm.get(p["title"], p["title"]): p.get("lastrevid")
            for p in data.get("query", {}).get("pages", [])}


def wiki_content(title):
    data = _wiki_api({"action": "query", "prop": "revisions", "rvprop": "content|ids|timestamp",
                      "rvslots": "main", "titles": title})
    page = data["query"]["pages"][0]
    if page.get("missing") or not page.get("revisions"):
        raise LookupError(f"Wikipedia has no page '{title}'")
    rev = page["revisions"][0]
    return rev["slots"]["main"]["content"], rev["revid"], rev["timestamp"]


# A parse this much smaller than the last good one is a vandalised, blanked or
# reorganised page, not a change in the election: the last good parse is kept.
MIN_ROWS = {"senate": 30, "house": 400, "governor": 30}


def acceptable_parse(chamber, new_count, last_good):
    if new_count < MIN_ROWS[chamber]:
        return False
    return not last_good or new_count >= 0.9 * last_good


def _wiki_party(text):
    t = text.lower()
    if "democrat" in t or "dfl" in t:
        return "D"
    if "republican" in t:
        return "R"
    return "O"


def _wiki_name(line):
    m = re.search(r"'''\s*\[\[([^\]|]+)(?:\|([^\]]+))?\]\]\s*'''", line) or \
        re.search(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", line)
    if m:
        return (m.group(2) or m.group(1)).strip()
    text = re.sub(r"\{\{[^{}]*\}\}", "", line).lstrip("* ").strip()
    text = re.sub(r"'''", "", text)
    return text.split("(")[0].strip()


EFN = re.compile(r"\{\{\s*(?:efn|notetag|refn|nbsp)[^{}]*\}\}", re.I)


def parse_wiki_candidates(row):
    """Candidate lines of one table row: name, party, share if shown, called?

    A candidate line is a list bullet or, for an unopposed member, a bare cell
    carrying a party stripe. Footnote templates, a closing "}}" left on the
    last line of a list, and line breaks are stripped before the party is read,
    because a line that fails to parse silently would make a contested race
    look one-sided."""
    out = []
    # A list can start on the same line as its template ("{{Plainlist |*...").
    text = re.sub(r"(\{\{\s*plainlist\s*\|)\s*\*", r"\1\n*", REF.sub("", row), flags=re.I)
    for line in text.splitlines():
        line = line.strip()
        if not (line.startswith("*") or "party stripe" in line.lower()):
            continue
        line = EFN.sub("", line)
        line = re.sub(r"<br\s*/?>", " ", line, flags=re.I)
        line = re.sub(r"[\s}]+$", "", line)
        m = PARTY_TAIL.search(line)
        if not m:
            continue
        out.append({"name": _wiki_name(line), "party": _wiki_party(m.group(1)), "party_raw": m.group(1),
                    "pct": float(m.group(2)) if m.group(2) else None, "winner": bool(AYE.search(line))})
    return out


def parse_wiki_senate(text):
    """{state code: [candidate rows]} from the race summary tables."""
    out = {}
    for row in re.split(r"\n\|-", text):
        # "! [[...|Georgia]]", also with cell attributes: "!rowspan=2 | [[...|Ohio]]"
        m = re.search(r"^!(?:[^|\n\[{]*\|)?\s*\[\[[^\]|]*\|([A-Za-z .]+?)\]\]", row.strip(), re.M)
        if not m:
            continue
        code = STATE_CODES.get(m.group(1).strip())
        cands = parse_wiki_candidates(row)
        if code and cands:
            out.setdefault(code, []).append(cands)
    return out


def parse_wiki_governor(text):
    """{state code: [candidate rows]} from the race summary's States table.

    Only that table: the predictions table repeats every state (without
    candidates), and the territories table follows it."""
    m = re.search(r"^=+\s*States\s*=+\s*$", text, re.M)
    if m:
        end = re.search(r"^=+\s*(?:Territories|[A-Z][a-z]+)[^=\n]*=+\s*$", text[m.end():], re.M)
        text = text[m.end(): m.end() + end.start() if end else len(text)]
    return parse_wiki_senate(text)


def parse_wiki_house(text):
    """{district id: candidate rows} from the per-state tables.

    Only from the first state section on: above it sits the table of this
    year's special elections, whose winners are marked and whose shares are
    filled in, and which must never be read as November's results."""
    first = re.search(r"\n==\s*Alabama\s*==", text)
    if first:
        text = text[first.start():]
    out = {}
    for row in re.split(r"\n\|-", text):
        m = re.search(r"^!(?:[^|\n\[{]*\|)?\s*\{\{\s*ushr\s*\|\s*([A-Z]{2})\s*\|\s*([A-Za-z0-9]+)\s*\|",
                      row.strip(), re.M | re.I)
        if not m:
            continue
        cands = parse_wiki_candidates(row)
        if cands:
            out[house_id(m.group(1).upper(), m.group(2))] = cands
    return out


def fetch_wikipedia(cache):
    """Both articles, re-read only when their revision id has moved."""
    revs = wiki_revisions(list(WIKI_PAGES.values()))
    changed = False
    for chamber, title in WIKI_PAGES.items():
        if revs.get(title) and revs[title] == cache.get(f"rev_{chamber}") and cache.get(chamber):
            continue
        text, revid, stamp = wiki_content(title)
        parsed = {"senate": parse_wiki_senate, "house": parse_wiki_house,
                  "governor": parse_wiki_governor}[chamber](text)
        last_good = len((cache.get(chamber) or {}).get("rows") or {})
        if not acceptable_parse(chamber, len(parsed), last_good):
            cache[f"rev_{chamber}"] = revid     # do not re-read the same bad revision every minute
            raise ValueError(f"{chamber} article parsed to {len(parsed)} races (last good {last_good}); "
                             "keeping the last good read")
        cache[chamber] = {"rows": parsed, "revid": revid, "rev_time": stamp}
        cache[f"rev_{chamber}"] = revid
        changed = True
    return changed
