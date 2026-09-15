#!/usr/bin/env python3
"""
Who people are looking up. Free, no key, no LLM.

Wikipedia publishes daily pageview counts for every article. For a
competitive race that answers a narrow, honest question: of the people
bothering to read about these two candidates, how is that attention split,
and has either one suddenly spiked?

What this is NOT. Attention is not support. A candidate's traffic spikes
when they are in trouble just as readily as when they are winning, and a
nationally famous incumbent will always out-read a newcomer. So the useful
figure is not the raw count but the SHARE between the two candidates in the
same race, and the change against each candidate's own recent baseline.
Even then it is a measure of curiosity, not of votes, which is why nothing
here is fed into the forecast. It is displayed and labelled as what it is.

Two APIs, both keyless:
  en.wikipedia.org/w/api.php          resolve a name to its article
  wikimedia.org/api/rest_v1/metrics   daily pageviews for that article

Resolved titles are cached in the output file so the mapping is auditable
and a bad match is visible rather than buried.

Run it directly:  python3 refresh_attention.py
"""
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DATA = Path(__file__).parent / "data"
WIKI_API = "https://en.wikipedia.org/w/api.php?"
PAGEVIEWS = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
             "en.wikipedia/all-access/user/{title}/daily/{start}/{end}")
# Wikimedia asks automated clients to identify themselves with a contact.
UA = {"User-Agent": "cd-election-agent/1.0 (https://github.com/Gh0stmahn-ai/cd-election-agent)"}

WINDOW_DAYS = 45      # how much history to pull
RECENT_DAYS = 7       # the window treated as "now"
MIN_BASELINE = 14     # fewer baseline days than this and a surge means nothing
MIN_DAILY_VIEWS = 20  # below this, day-to-day noise swamps any signal
PAUSE = 2.0           # be a polite client; a shared runner IP gets throttled hard
RETRIES = 4           # pageviews returns 429 under load, and it clears quickly
TIMEOUT = 30

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming",
}

# Words that mark an article as being about a person rather than an election,
# a place or a disambiguation list.
PERSON_WORDS = ("politician", "senator", "governor", "representative", "congress",
                "attorney", "lawyer", "businessman", "businesswoman", "physician",
                "journalist", "epidemiologist", "activist", "judge", "mayor", "veteran")


def api(params):
    """One MediaWiki call, retrying through the same throttling as pageviews."""
    url = WIKI_API + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    last = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            last = e
            time.sleep(4 * (attempt + 1))
    raise last


def describe(titles):
    """{title: (description, is_disambiguation)} for many titles in one call."""
    if not titles:
        return {}
    data = api({"action": "query", "titles": "|".join(titles), "redirects": "1",
                "prop": "description|pageprops", "format": "json"})
    query = data.get("query", {})
    out = {}
    for page in query.get("pages", {}).values():
        desc = page.get("description") or ""
        disamb = ("disambiguation" in page.get("pageprops", {})
                  or "disambiguation" in desc.lower())
        out[page.get("title", "")] = (desc, disamb, "missing" in page)
    out["__redirects__"] = {r["from"]: r["to"] for r in query.get("redirects", [])}
    return out


def looks_like_person(title, desc):
    lowered = (desc or "").lower()
    if " election" in title.lower() or title[:2].isdigit():
        return False
    return any(word in lowered for word in PERSON_WORDS)


def search_person(name, state):
    """Fallback for a name that is a disambiguation page, e.g. 'Mike Rogers'.

    The state is the disambiguator: there are several Mike Rogers in American
    politics and only one of them is running in Michigan.
    """
    data = api({"action": "query", "list": "search",
                "srsearch": f'"{name}" {state} senate', "srlimit": 6, "format": "json"})
    titles = [hit["title"] for hit in data.get("query", {}).get("search", [])]
    if not titles:
        return None
    time.sleep(PAUSE)
    info = describe(titles)
    for title in titles:
        desc, disamb, missing = info.get(title, ("", False, True))
        if disamb or missing:
            continue
        if looks_like_person(title, desc):
            return title
    return None


def resolve(candidates):
    """[(seat, side, name, state)] -> {(seat, side): title}.

    One batched call covers every name that is already an unambiguous
    article, which is most of them; only the leftovers cost a search.
    """
    names = [c[2] for c in candidates]
    info = describe(names)
    redirects = info.pop("__redirects__", {})
    resolved, unresolved = {}, []
    for seat, side, name, state in candidates:
        title = redirects.get(name, name)
        desc, disamb, missing = info.get(title, ("", False, True))
        if not missing and not disamb and looks_like_person(title, desc):
            resolved[(seat, side)] = title
        else:
            unresolved.append((seat, side, name, state))
    for seat, side, name, state in unresolved:
        time.sleep(PAUSE)
        found = search_person(name, state)
        if found:
            resolved[(seat, side)] = found
        else:
            print(f"  ! no article found for {name} ({state})", file=sys.stderr)
    return resolved


def pageviews(title, start, end):
    """[(date, views)] daily, oldest first. A 404 means the article has no traffic record."""
    url = PAGEVIEWS.format(title=urllib.parse.quote(title.replace(" ", "_"), safe=""),
                           start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
    req = urllib.request.Request(url, headers=UA)
    last = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [(item["timestamp"][:8], int(item["views"]))
                    for item in data.get("items", [])]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []          # no traffic record for this article
            if e.code != 429:
                raise
            # Throttled. Back off and try again: this clears in seconds, and
            # giving up here would silently drop a race from the comparison.
            last = e
            time.sleep(4 * (attempt + 1))
    raise last


def summarise(series):
    """Recent daily mean, baseline daily mean, and the ratio between them."""
    if len(series) < MIN_BASELINE + RECENT_DAYS:
        return None
    values = [v for _, v in series]
    recent = statistics.mean(values[-RECENT_DAYS:])
    baseline = statistics.mean(values[:-RECENT_DAYS])
    if baseline < MIN_DAILY_VIEWS:
        return None
    return {"recent_daily": round(recent), "baseline_daily": round(baseline),
            "surge": round(recent / baseline, 2), "days": len(series)}


def competitive_races():
    races = json.loads((DATA / "senate_races_2026.json").read_text())["races"]
    return [r for r in races if r["rating"] in ("tossup", "lean")]


def main():
    races = competitive_races()
    candidates = []
    for race in races:
        state = STATE_NAMES.get(race["state"], race["state"])
        for side, key in (("D", "dem_candidate"), ("R", "rep_candidate")):
            name = (race.get(key) or "").replace("(I)", "").strip()
            if name:
                candidates.append((race["seat_id"], side, name, state))

    print(f"Resolving {len(candidates)} candidates to Wikipedia articles...")
    try:
        titles = resolve(candidates)
    except Exception as e:  # noqa: BLE001
        print(f"Could not reach Wikipedia: {e}", file=sys.stderr)
        return 1
    print(f"  resolved {len(titles)} of {len(candidates)}")

    end = date.today() - timedelta(days=1)   # yesterday is the last complete day
    start = end - timedelta(days=WINDOW_DAYS)
    out, issues = {}, []

    for race in races:
        seat = race["seat_id"]
        entry = {"state": race["state"], "sides": {}}
        for side, key in (("D", "dem_candidate"), ("R", "rep_candidate")):
            title = titles.get((seat, side))
            name = (race.get(key) or "").replace("(I)", "").strip()
            if not title:
                if name:
                    issues.append(f"{seat} {side}: no article for {name}")
                continue
            try:
                series = pageviews(title, start, end)
            except Exception as e:  # noqa: BLE001
                issues.append(f"{seat} {side} ({title}): {e}")
                continue
            finally:
                time.sleep(PAUSE)
            stats = summarise(series)
            entry["sides"][side] = {"candidate": name, "article": title,
                                    "url": "https://en.wikipedia.org/wiki/"
                                           + urllib.parse.quote(title.replace(" ", "_")),
                                    **(stats or {"recent_daily": None})}
        d, r = entry["sides"].get("D"), entry["sides"].get("R")
        if d and r and d.get("recent_daily") and r.get("recent_daily"):
            total = d["recent_daily"] + r["recent_daily"]
            entry["dem_share"] = round(d["recent_daily"] / total, 4)
            entry["total_daily"] = total
            print(f"  {seat}: {entry['dem_share'] * 100:.0f}% of attention on the Democrat "
                  f"({total:,}/day, surge D {d['surge']}x R {r['surge']}x)")
        else:
            print(f"  {seat}: not enough traffic to compare")
        out[seat] = entry

    usable = sum(1 for v in out.values() if "dem_share" in v)
    if not usable:
        print("No race had usable pageview data; leaving any previous file in place.",
              file=sys.stderr)
        return 1

    payload = {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {"start": start.isoformat(), "end": end.isoformat(),
                   "recent_days": RECENT_DAYS},
        "source": "Wikipedia daily pageviews (Wikimedia REST API)",
        "method": (f"Share is each candidate's mean daily article views over the last "
                   f"{RECENT_DAYS} days, as a fraction of the two candidates' combined "
                   f"views. Surge is that mean against the candidate's own earlier "
                   f"baseline in the same window."),
        "applied_to_forecast": False,
        "note": ("Attention is curiosity, not support: a candidate in trouble gets read "
                 "about too. Shown next to the forecast, never inside it."),
        "races": out, "issues": issues,
    }
    (DATA / "attention_2026.json").write_text(json.dumps(payload, indent=1))
    print(f"\n{usable} of {len(races)} races have a usable attention signal.")
    if issues:
        print(f"({len(issues)} lookup(s) failed; recorded in the file.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
