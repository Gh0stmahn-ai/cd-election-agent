#!/usr/bin/env python3
"""
Poll averages, free and without an LLM.

The generic congressional ballot and presidential approval are the two
inputs that are not published as machine-readable series: they live on web
pages. Until now they were either read by the AI refresh (which costs API
credits) or typed in by hand.

The generic ballot is now built HERE, from the individual polls, by
pollavg.py -- weighted by recency, sample size and population, with each
pollster's standing lean removed and a real standard error attached. The
table of AGGREGATORS that used to be the source is still read, but as a
cross-check to publish beside our own number and as the fallback when the
poll list cannot be parsed. Approval still comes from its aggregator table.

Everything is written through the validated functions in ingest.py that
every other source uses, so the usual range checks apply.

Run it directly:  python3 refresh_polls.py
"""
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import ingest
import pollavg

REST = "https://en.wikipedia.org/api/rest_v1/page/html/"
# Wikimedia asks automated clients to identify themselves and give a contact.
UA = {"User-Agent": "cd-election-agent/1.0 (https://github.com/Gh0stmahn-ai/cd-election-agent)"}
TIMEOUT = 45
RETRIES = 4

BALLOT_PAGE = "2026_United_States_House_of_Representatives_elections"
# The individual generic-ballot polls live on the cycle's own elections
# article, in two tables: this cycle and the run-up to it.
POLLS_PAGE = "2026_United_States_elections"
APPROVAL_PAGE = "Opinion_polling_on_the_second_Trump_presidency"

BALLOT_URL = f"https://en.wikipedia.org/wiki/{BALLOT_PAGE}"
POLLS_URL = f"https://en.wikipedia.org/wiki/{POLLS_PAGE}"
APPROVAL_URL = f"https://en.wikipedia.org/wiki/{APPROVAL_PAGE}"


API = "https://en.wikipedia.org/w/api.php"


def _api(url):
    """One call to the action API, retrying through Wikipedia's throttle."""
    req = urllib.request.Request(url, headers=UA)
    last = None
    for attempt in range(RETRIES + 2):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            last = e
            time.sleep(4 * (attempt + 1))
    raise last


def fetch_section(page, pattern):
    """The HTML of one section, found by its heading.

    The poll list sits inside an article that is nearly three megabytes.
    Pulling the whole thing several times a day is what was getting the run
    throttled, and the section that matters is a quarter of the size. The
    section is located by heading rather than by a hard-coded index so that
    an edit inserting a section above it does not silently fetch the wrong
    one -- or worse, one that parses.
    """
    page_q = urllib.parse.quote(page)
    sections = _api(f"{API}?action=parse&page={page_q}&prop=sections&format=json"
                    "&formatversion=2")["parse"]["sections"]
    for section in sections:
        if re.search(pattern, section["line"], re.I):
            body = _api(f"{API}?action=parse&page={page_q}&prop=text"
                        f"&section={section['index']}&format=json&formatversion=2")
            return body["parse"]["text"]
    raise RuntimeError(f"no section matching {pattern!r} on {page}")


def fetch(page):
    """Fetch one article, retrying through Wikipedia's rate limiting.

    A shared runner IP gets throttled, and without a retry a single 429
    silently costs the day's poll refresh and leaves yesterday's number in
    place. The throttle clears in seconds.
    """
    req = urllib.request.Request(REST + page, headers=UA)
    last = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            last = e
            time.sleep(4 * (attempt + 1))
    raise last


def plain(fragment):
    """HTML fragment to readable text, with citation markers dropped."""
    fragment = re.sub(r"<sup.*?</sup>", " ", fragment, flags=re.S)
    fragment = re.sub(r"<style.*?</style>", " ", fragment, flags=re.S)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).strip()


def find_table(page_html, *required):
    """The first table whose header row mentions every required word.

    Matching on the header rather than on a heading position means a new
    section or a reordered article does not silently select the wrong table.
    """
    for table in re.findall(r"<table.*?</table>", page_html, re.S):
        rows = re.findall(r"<tr.*?</tr>", table, re.S)
        if not rows:
            continue
        header = plain(rows[0]).lower()
        if all(word in header for word in required):
            return table
    return None


def parse_rows(table):
    """[(label, [percentages in column order]), ...] for the body rows.

    Percentages are read straight out of the row text rather than by column
    index, because the average row omits a date column and the cells carry
    a lot of template markup. Dates contain no percent sign, so the numbers
    that come back are the data columns, in order.
    """
    out = []
    for row in re.findall(r"<tr.*?</tr>", table, re.S)[1:]:
        text = plain(row)
        numbers = [float(n) for n in re.findall(r"(\d+(?:\.\d+)?)\s*%", text)]
        label = re.split(r"\s{2,}|\d", text, maxsplit=1)[0].strip(" ,|")
        if numbers:
            out.append((label or "?", numbers, text))
    return out


def updated_date(text):
    """'September 13, 2026' out of a row, as an ISO date."""
    m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def pick(rows, n_required):
    """The article's own average row, or the mean of the aggregator rows.

    Wikipedia computes an 'Average' row on both tables. When it is missing
    (an edit in progress, a renamed row) the aggregators are averaged here
    instead, so a good day and a bad day both produce a number.
    """
    usable = [r for r in rows if len(r[1]) >= n_required]
    if not usable:
        return None, None, 0
    for label, numbers, text in usable:
        if label.lower().startswith("average"):
            return numbers, updated_date(text), len(usable) - 1
    cols = list(zip(*[r[1][:n_required] for r in usable]))
    mean = [round(sum(c) / len(c), 1) for c in cols]
    dates = [d for d in (updated_date(r[2]) for r in usable) if d]
    return mean, (max(dates) if dates else None), len(usable)


def aggregator_ballot():
    """What the published aggregators say, as a cross-check. None if unreadable."""
    try:
        table = find_table(fetch_section(POLLS_PAGE, r"generic congressional ballot"),
                           "aggregation", "republicans", "democrats")
        if table is None:
            table = find_table(fetch(BALLOT_PAGE), "aggregation", "republicans", "democrats")
        if table is None:
            return None
        numbers, as_of, n = pick(parse_rows(table), 3)
        if not numbers:
            return None
        rep, dem = numbers[0], numbers[1]
        if not 25 <= rep <= 65 or not 25 <= dem <= 65:
            return None
        # The stated margin column is ignored on purpose: deriving it from
        # the two toplines cannot pick up a stray sign or a mis-parsed minus.
        return {"margin": round(dem - rep, 1), "rep": rep, "dem": dem,
                "n_aggregators": n, "as_of": as_of}
    except Exception as e:  # noqa: BLE001 - a cross-check is never worth failing over
        print(f"  (aggregator cross-check unavailable: {e})", file=sys.stderr)
        return None


def own_average():
    """Our own average, built from the individual polls."""
    page = fetch_section(POLLS_PAGE, r"^polling$")
    polls = []
    for table in re.findall(r"<table.*?</table>", page, re.S):
        rows = re.findall(r"<tr.*?</tr>", table, re.S)
        if not rows:
            continue
        header = plain(rows[0]).lower()
        if "poll source" in header and "democratic" in header and "republican" in header:
            polls.extend(pollavg.parse_table(table))
    if len(polls) < 20:
        raise RuntimeError(f"only {len(polls)} individual polls parsed")
    result = pollavg.average(polls)
    if not result:
        raise RuntimeError("no polls recent enough to average")
    return result


def refresh_generic_ballot():
    published = aggregator_ballot()
    try:
        avg = own_average()
    except Exception as e:  # noqa: BLE001 - fall back rather than serve nothing
        if not published:
            raise
        ingest.refresh_generic_ballot(
            new_margin=published["margin"],
            source_notes=(f"Average of {published['n_aggregators']} public poll aggregators as "
                          f"collated on Wikipedia. Our own average could not be built: {e}"),
            sources=[BALLOT_URL])
        return (f"generic ballot: D{published['margin']:+.1f} from {published['n_aggregators']} "
                f"aggregators (fallback: {e})")

    detail = {k: avg[k] for k in ("stderr", "spread", "flat", "trend_per_week", "mean_age_days",
                                  "n_polls", "n_effective", "n_pollsters", "newest", "oldest",
                                  "house_effects")}
    detail["method"] = ("Weighted by recency (21-day half-life), sample size and population, "
                        "with each pollster's standing lean removed and no pollster allowed "
                        "more than a fifth of the total weight. The average is then read off a "
                        "line fitted through the polls at today's date rather than taken flat, "
                        "because a weighted mean sits at the middle of its window and lags "
                        "whenever the race is moving.")
    detail["recent"] = [{"source": p["source"], "date": p["end"].isoformat(),
                         "n": p["n"], "population": p["population"],
                         "margin": p["margin"], "adjusted": p["adjusted"],
                         "weight": round(p["weight"], 3)}
                        for p in avg["polls"][:12]]
    if published:
        detail["aggregators"] = published

    note = (f"Our own average of {avg['n_polls']} individual polls from {avg['n_pollsters']} "
            f"pollsters, {avg['oldest']} to {avg['newest']}, plus or minus {avg['stderr']:.1f}")
    if published:
        note += f". Published aggregators say D{published['margin']:+.1f}"
    ingest.refresh_generic_ballot(new_margin=avg["margin"], source_notes=note,
                                  sources=[POLLS_URL, BALLOT_URL], detail=detail)
    cross = f", aggregators D{published['margin']:+.1f}" if published else ""
    return (f"generic ballot: D{avg['margin']:+.2f} +/- {avg['stderr']:.2f} "
            f"({avg['n_polls']} polls, {avg['n_pollsters']} pollsters{cross})")


def refresh_approval():
    table = find_table(fetch(APPROVAL_PAGE), "aggregator", "approve", "disapprove")
    if table is None:
        raise RuntimeError("could not find the approval aggregator table")
    numbers, as_of, n = pick(parse_rows(table), 2)
    if not numbers:
        raise RuntimeError("approval table had no usable rows")
    approve, disapprove = numbers[0], numbers[1]
    if not 10 <= approve <= 80 or not 10 <= disapprove <= 90:
        raise RuntimeError(f"implausible approval {approve}/{disapprove}")
    net = round(approve - disapprove, 1)
    ingest.refresh_approval(
        approve=approve, disapprove=disapprove, net=net,
        as_of=as_of or ingest._today(),
        context=f"Average of {n} public approval aggregators as collated on Wikipedia.",
        sources=[{"name": "Wikipedia: opinion polling on the second Trump presidency",
                  "url": APPROVAL_URL}])
    return f"approval: {approve}% / {disapprove}% (net {net:+.1f}, {n} aggregators)"


def main():
    done, failed = [], []
    for name, fn in (("generic ballot", refresh_generic_ballot), ("approval", refresh_approval)):
        try:
            print("  " + fn())
            done.append(name)
        except Exception as e:  # noqa: BLE001 - one dead table shouldn't sink the other
            print(f"  ! {name}: {e}", file=sys.stderr)
            failed.append(name)
    if failed:
        print(f"Could not refresh: {', '.join(failed)}. "
              "The previous value stays on file and its age is shown on the site.")
    # Only a total failure is worth a non-zero exit; the workflow soft-fails
    # this step anyway, and a stale number is visible on every page.
    return 1 if not done else 0


if __name__ == "__main__":
    sys.exit(main())
