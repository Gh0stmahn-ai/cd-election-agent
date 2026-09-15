#!/usr/bin/env python3
"""
Poll averages, free and without an LLM.

The generic congressional ballot and presidential approval are the two
inputs that are not published as machine-readable series: they are averages
of averages, living on web pages. Until now they were either read by the AI
refresh (which costs API credits) or typed in by hand.

Wikipedia's election articles carry a maintained table of the major poll
AGGREGATORS for both, each with its own topline and a computed average row.
That is a better source than scraping any single aggregator: it is already
an average of RealClearPolitics, Silver Bulletin, Decision Desk HQ,
VoteHub, FiftyPlusOne and others, it is edited in public, and it comes over
Wikipedia's own REST API with no key.

Both numbers are written through the same validated functions in ingest.py
that every other source uses, so the usual range checks apply.

Run it directly:  python3 refresh_polls.py
"""
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

import ingest

REST = "https://en.wikipedia.org/api/rest_v1/page/html/"
# Wikimedia asks automated clients to identify themselves and give a contact.
UA = {"User-Agent": "cd-election-agent/1.0 (https://github.com/Gh0stmahn-ai/cd-election-agent)"}
TIMEOUT = 45
RETRIES = 4

BALLOT_PAGE = "2026_United_States_House_of_Representatives_elections"
APPROVAL_PAGE = "Opinion_polling_on_the_second_Trump_presidency"

BALLOT_URL = f"https://en.wikipedia.org/wiki/{BALLOT_PAGE}"
APPROVAL_URL = f"https://en.wikipedia.org/wiki/{APPROVAL_PAGE}"


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


def refresh_generic_ballot():
    table = find_table(fetch(BALLOT_PAGE), "aggregation", "republicans", "democrats")
    if table is None:
        raise RuntimeError("could not find the poll aggregation table on the House elections article")
    numbers, as_of, n = pick(parse_rows(table), 3)
    if not numbers:
        raise RuntimeError("poll aggregation table had no usable rows")
    rep, dem = numbers[0], numbers[1]
    margin = round(dem - rep, 1)
    # The stated margin column is ignored on purpose: deriving it from the
    # two toplines cannot pick up a stray sign or a mis-parsed minus.
    if not 25 <= rep <= 65 or not 25 <= dem <= 65:
        raise RuntimeError(f"implausible toplines R {rep} / D {dem}")
    ingest.refresh_generic_ballot(
        new_margin=margin,
        source_notes=(f"Average of {n} public poll aggregators (RealClearPolitics, Silver Bulletin, "
                      f"Decision Desk HQ, VoteHub and others) as collated on Wikipedia"
                      + (f", updated {as_of}" if as_of else "")),
        sources=[BALLOT_URL])
    return f"generic ballot: D{margin:+.1f} (R {rep}% / D {dem}%, {n} aggregators)"


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
