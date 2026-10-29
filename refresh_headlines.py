#!/usr/bin/env python3
"""
Trending midterm headlines, balanced by party and by outlet. Free, no key, no LLM.

Reads the politics feeds that news outlets publish for exactly this purpose
(RSS), keeps the stories about the 2026 midterms, groups the same story told
by different outlets, and ranks the groups by how widely the story is being
covered right now. Only each headline, its outlet, its time and a link are
kept and shown: the reader goes to the outlet for the story itself.

Two balance rules, both enforced when the list is chosen:

  * Party. Every story is sorted by which party it is about (its headline
    names Democrats or Democratic candidates, Republicans or Republican
    candidates, or both). The list always has the same number of stories
    about each party.
  * Outlet. The same number of left-leaning and right-leaning outlets are
    read (OUTLETS below), and the list shows the same number of headlines
    from each side, within each party's list as well as overall, so neither
    party is covered only by the press on the other side. Center outlets are
    not counted toward either.

"Trending" means covered by the most outlets in the last two days, with newer
stories ahead of older ones. Nothing here touches the forecast.

Writes data/headlines_2026.json.   Run it directly:  python3 refresh_headlines.py
"""
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

DATA = Path(__file__).parent / "data"
OUT = DATA / "headlines_2026.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; cd-election-agent/1.0; "
                    "+https://github.com/Gh0stmahn-ai/cd-election-agent)"}
TIMEOUT = 20
WINDOW_HOURS = 48
PER_PARTY = 6          # stories about each party
BOTH = 2               # plus up to this many about both parties at once

# Outlets read, with the lean used for the outlet-balance rule. Seven on each
# side, grouped as published media-bias ratings (AllSides, Ad Fontes) broadly
# place them; center outlets are read too and do not count toward either side.
OUTLETS = [
    # left-leaning
    ("NBC News", "L", "https://feeds.nbcnews.com/nbcnews/public/politics"),
    ("The New York Times", "L", "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml"),
    ("CNN", "L", "http://rss.cnn.com/rss/cnn_allpolitics.rss"),
    ("CBS News", "L", "https://www.cbsnews.com/latest/rss/politics"),
    ("ABC News", "L", "https://abcnews.go.com/abcnews/politicsheadlines"),
    ("NPR", "L", "https://feeds.npr.org/1014/rss.xml"),
    ("Politico", "L", "https://rss.politico.com/politics-news.xml"),
    # right-leaning
    ("Fox News", "R", "https://moxie.foxnews.com/google-publisher/politics.xml"),
    ("Washington Examiner", "R", "https://www.washingtonexaminer.com/section/politics/feed/"),
    ("New York Post", "R", "https://nypost.com/politics/feed/"),
    ("Daily Caller", "R", "https://dailycaller.com/feed/"),
    ("National Review", "R", "https://www.nationalreview.com/feed/"),
    ("Washington Free Beacon", "R", "https://freebeacon.com/feed/"),
    ("The Dispatch", "R", "https://thedispatch.com/feed/"),
    # center
    ("The Hill", "C", "https://thehill.com/homenews/campaign/feed/"),
    ("The Wall Street Journal", "C", "https://feeds.content.dowjones.io/public/rss/socialpoliticsfeed"),
    ("NewsNation", "C", "https://www.newsnationnow.com/politics/feed/"),
    ("Christian Science Monitor", "C", "https://www.csmonitor.com/feeds/politics"),
    ("BBC News", "C", "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"),
    ("Deseret News", "C", "https://www.deseret.com/arc/outboundfeeds/rss/category/politics/"),
]
LEAN_NAME = {"L": "left-leaning", "C": "center", "R": "right-leaning"}

# What makes a headline about the midterms, and about which party.
ELECTION = re.compile(
    r"\b(midterms?|elections?|electoral|campaign\w*|ballots?|voters?|voting|polls?|polling|"
    r"early voting|debates?|nominees?|candidates?|battleground|swing (?:state|district)s?|turnout|"
    r"redistricting|gerrymander\w*|senate race|house race|governor'?s race|gubernatorial|"
    r"senate (?:seat|majority|control)|house (?:seat|majority|control)|flip(?:s|ped)?|"
    r"incumbent|challenger|endors\w+|ads?\b|fundrais\w*|super pac|november)\b", re.I)
DEM = re.compile(r"\b(democrats?|democratic|dems?|dnc|dccc|dscc|dga|schumer|jeffries|obama|"
                 r"pelosi|kamala harris|harris|newsom|ken martin|clyburn|aoc|ocasio-cortez)\b", re.I)
REP = re.compile(r"\b(republicans?|gop|rnc|nrcc|nrsc|rga|maga|trump\w*|vance|thune|mcconnell|"
                 r"speaker johnson|mike johnson|desantis|scalise|emmer|barrasso)\b", re.I)
NOT_US = re.compile(r"\b(canada|canadian|ontario|quebec|britain|british|u\.k\.|labour|tory|tories|"
                    r"germany|japan|brazil|israeli election|australia)\b", re.I)
# Surnames too common to identify a candidate on their own.
COMMON = {"brown", "scott", "graham", "collins", "johnson", "james", "jackson", "green", "moore", "smith",
          "davis", "jones", "wilson", "hill", "king", "ford", "long", "love", "rogers", "white", "little",
          "marshall", "miller", "taylor", "allen", "young", "lee", "martin", "thomas", "carey", "price",
          "baker", "turner", "walker", "hall", "wells", "may", "bell", "rose", "street", "cooper", "booker",
          "warner", "reed", "barr", "murphy", "flood", "sand", "hill", "brooks", "lawler"}
STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "as", "at", "by", "with",
        "from", "after", "over", "into", "about", "says", "say", "said", "new", "his", "her", "their", "its",
        "this", "that", "be", "will", "it", "how", "why", "what", "who", "up", "out", "not", "but", "has",
        "have", "was", "were", "more", "than", "amid", "could", "would", "can", "just", "s"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def parse_feed(raw):
    """[(title, link, published datetime, summary)] from RSS 2.0 or Atom."""
    root = ET.fromstring(raw)
    out = []
    for item in root.iter():
        tag = item.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        fields = {}
        for child in item:
            name = child.tag.rsplit("}", 1)[-1]
            if name == "link" and child.get("href"):
                fields.setdefault("link", child.get("href"))
            elif name in ("title", "link", "pubDate", "published", "updated", "date", "description", "summary"):
                fields.setdefault(name, (child.text or "").strip())
        when = None
        for key in ("pubDate", "published", "updated", "date"):
            if fields.get(key):
                try:
                    when = parsedate_to_datetime(fields[key])
                except (TypeError, ValueError):
                    try:
                        when = datetime.fromisoformat(fields[key].replace("Z", "+00:00"))
                    except ValueError:
                        when = None
                if when:
                    break
        title, link = clean(fields.get("title")), (fields.get("link") or "").strip()
        if not title or not link.startswith("http") or when is None:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        out.append((title, link, when.astimezone(timezone.utc), clean(fields.get("description") or fields.get("summary"))))
    return out


def candidates():
    """{name or distinctive surname (lowercase): party} for every nominee on file."""
    names = {}
    sources = [("senate_races_2026.json", "races"), ("governor_races_2026.json", "races"),
               ("house_districts_2026.json", "districts")]
    for fname, key in sources:
        try:
            rows = json.loads((DATA / fname).read_text())[key]
        except (OSError, ValueError, KeyError):
            continue
        for r in rows:
            if key == "districts" and r.get("rating") == "solid":
                continue        # only the House races anyone is watching
            for party, field in (("D", "dem_candidate"), ("R", "rep_candidate")):
                for name in (r.get(field) or "").split(","):
                    name = name.replace("(I)", "").strip()
                    if not name or (party == "D" and "(I)" in (r.get(field) or "")):
                        continue
                    names[name.lower()] = party
    surnames = {}
    for name, party in names.items():
        last = name.split()[-1].strip(".")
        if len(last) >= 5 and last not in COMMON:
            surnames.setdefault(last, set()).add(party)
    for last, parties in surnames.items():
        if len(parties) == 1:
            names.setdefault(last, next(iter(parties)))
    return names


def party_focus(title, names):
    low = title.lower()
    d = len(DEM.findall(title))
    r = len(REP.findall(title))
    named = False
    for name, party in names.items():
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            named = True
            if party == "D":
                d += 1
            else:
                r += 1
    if d and r:
        return "both", named
    return ("D" if d else "R" if r else None), named


def words(title):
    return {w for w in re.findall(r"[a-z0-9']+", title.lower()) if w not in STOP and len(w) > 2}


def cluster(items):
    """Group headlines that tell the same story (shared words, single link)."""
    groups = []
    for it in sorted(items, key=lambda x: x["published"], reverse=True):
        best, best_sim = None, 0.0
        for g in groups:
            a, b = it["_words"], g["_words"]
            sim = len(a & b) / max(1, min(len(a), len(b)))
            if sim > best_sim:
                best, best_sim = g, sim
        if best is not None and best_sim >= 0.5 and len(it["_words"] & best["_words"]) >= 3:
            best["items"].append(it)
            best["_words"] |= it["_words"]
        else:
            groups.append({"items": [it], "_words": set(it["_words"])})
    return groups


def score(group, now):
    outlets = {i["outlet"] for i in group["items"]}
    leans = {i["lean"] for i in group["items"]}
    newest = max(i["published"] for i in group["items"])
    hours = (now - datetime.fromisoformat(newest)).total_seconds() / 3600
    # Breadth of coverage first, a bonus when both sides of the press carry
    # it, and a gentle decay so a story from this morning beats one from
    # two days ago that was equally wide.
    return (len(outlets) + (0.5 if {"L", "R"} <= leans else 0)) * (0.5 ** (hours / 24))


def _top(entries, n):
    """The best-ranked n entries with left- and right-leaning outlets level.

    Walks the ranking: a center headline is always taken, a left- or
    right-leaning one only while its side is not already ahead. If the sides
    still differ at the end, the side that is short gets its next-best
    headline (in place of the lowest center one if the list is full), or the
    surplus headline goes."""
    rank = {id(e): i for i, e in enumerate(entries)}
    chosen, cnt = [], {"L": 0, "R": 0}
    for e in entries:
        if len(chosen) >= n:
            break
        lean = e[1]["lean"]
        if lean in cnt:
            other = "R" if lean == "L" else "L"
            if cnt[lean] > cnt[other]:
                continue
            cnt[lean] += 1
        chosen.append(e)
    if cnt["L"] != cnt["R"]:
        short = "L" if cnt["L"] < cnt["R"] else "R"
        extra = next((e for e in entries if e not in chosen and e[1]["lean"] == short), None)
        centers = [e for e in chosen if e[1]["lean"] == "C"]
        if extra and len(chosen) < n:
            chosen.append(extra)
        elif extra and centers:
            chosen.remove(centers[-1])
            chosen.append(extra)
        else:
            surplus = "R" if short == "L" else "L"
            chosen.remove([e for e in chosen if e[1]["lean"] == surplus][-1])
    return sorted(chosen, key=lambda e: rank[id(e)])


def choose(groups, now):
    """The balanced list.

    Within each party's list the left-leaning and right-leaning outlets are
    level, so neither party is covered only by the press on the other side;
    and the two parties' lists are the same length."""
    ranked = sorted(groups, key=lambda g: score(g, now), reverse=True)
    by_focus = {"D": [], "R": [], "both": []}
    side = {k: {"L": 0, "R": 0} for k in by_focus}
    for g in ranked:
        focus = g["focus"]
        if focus not in by_focus:
            continue
        members = sorted(g["items"], key=lambda i: i["published"], reverse=True)
        center = [i for i in members if i["lean"] == "C"]
        counts = side[focus]
        behind = "L" if counts["L"] <= counts["R"] else "R"
        options = [i for i in members if i["lean"] == behind]
        # The side that is behind gets the headline, then a center outlet,
        # then the side that is ahead (the surplus is trimmed afterwards).
        rep = (options or center or members)[0]
        if rep["lean"] in counts:
            counts[rep["lean"]] += 1
        by_focus[focus].append((g, rep))
    d, r = _top(by_focus["D"], PER_PARTY), _top(by_focus["R"], PER_PARTY)
    while len(d) != len(r):            # the two parties' lists the same length
        if len(d) > len(r):
            d = _top(d, len(r))
        else:
            r = _top(r, len(d))
    both = _top(by_focus["both"], BOTH)
    picked = d + r + both
    picked.sort(key=lambda e: score(e[0], now), reverse=True)
    return picked


def main():
    now = datetime.now(timezone.utc)
    names = candidates()
    items, status = [], []
    for outlet, lean, url in OUTLETS:
        try:
            entries = parse_feed(get(url))
            status.append({"outlet": outlet, "lean": lean, "ok": True, "items": len(entries)})
        except (urllib.error.URLError, ET.ParseError, TimeoutError, OSError, ValueError) as e:
            status.append({"outlet": outlet, "lean": lean, "ok": False, "error": str(e)[:120]})
            print(f"  ! {outlet}: {e}", file=sys.stderr)
            continue
        time.sleep(0.3)
        for title, link, when, summary in entries:
            if now - when > timedelta(hours=WINDOW_HOURS) or when > now + timedelta(hours=1):
                continue
            if NOT_US.search(title):
                continue
            focus, named = party_focus(title, names)
            about_election = bool(ELECTION.search(title)) or named
            if not about_election or focus is None:
                continue
            items.append({"title": title, "link": link, "outlet": outlet, "lean": lean,
                          "published": when.isoformat(timespec="seconds"), "focus": focus,
                          "_words": words(title)})
    groups = cluster(items)
    for g in groups:
        # A story's party is the one most of its headlines are about.
        votes = {"D": 0, "R": 0, "both": 0}
        for i in g["items"]:
            votes[i["focus"]] += 1
        g["focus"] = max(votes, key=lambda k: (votes[k], k == "both"))
    picked = choose(groups, now)
    ok_l = sum(1 for s in status if s["ok"] and s["lean"] == "L")
    ok_r = sum(1 for s in status if s["ok"] and s["lean"] == "R")
    if len(picked) < 4:
        print(f"Only {len(picked)} balanced headlines could be chosen; leaving the previous file alone.",
              file=sys.stderr)
        return 1
    stories = []
    for g, rep in picked:
        others = sorted({i["outlet"] for i in g["items"]} - {rep["outlet"]})
        leans = [i["lean"] for i in g["items"]]
        stories.append({
            "title": rep["title"], "link": rep["link"], "outlet": rep["outlet"], "lean": rep["lean"],
            "published": rep["published"], "focus": g["focus"],
            "also": others[:6], "n_outlets": len({i["outlet"] for i in g["items"]}),
            "coverage": {"L": leans.count("L"), "C": leans.count("C"), "R": leans.count("R")},
        })
    payload = {
        "as_of": now.isoformat(timespec="seconds"),
        "window_hours": WINDOW_HOURS,
        "rules": ("Equal numbers of stories about each party, and equal numbers of headlines from "
                  "left-leaning and right-leaning outlets within each party's list; center outlets count "
                  "toward neither. Ranked by how many outlets are carrying the story, newer first."),
        "outlets": [{"outlet": o, "lean": l} for o, l, _ in OUTLETS],
        "feeds": status,
        "feeds_read": {"L": ok_l, "R": ok_r, "C": sum(1 for s in status if s["ok"] and s["lean"] == "C")},
        "candidates_considered": len(items),
        "stories": stories,
        "balance": {
            "D": sum(1 for s in stories if s["focus"] == "D"), "R": sum(1 for s in stories if s["focus"] == "R"),
            "both": sum(1 for s in stories if s["focus"] == "both"),
            "left_outlets": sum(1 for s in stories if s["lean"] == "L"),
            "right_outlets": sum(1 for s in stories if s["lean"] == "R"),
            "center_outlets": sum(1 for s in stories if s["lean"] == "C"),
        },
    }
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    b = payload["balance"]
    print(f"{len(stories)} headlines from {len(items)} midterm items "
          f"({ok_l} left / {payload['feeds_read']['C']} center / {ok_r} right feeds read): "
          f"D {b['D']} / R {b['R']} / both {b['both']}; outlets L {b['left_outlets']} / "
          f"C {b['center_outlets']} / R {b['right_outlets']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
