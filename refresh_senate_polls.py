#!/usr/bin/env python3
"""
State polling for the Senate races that decide control. Free, no key.

Until now the Senate side of this model contained no state polling at all.
Every competitive race was a rating bucket shifted by the national
environment, which is why the forecast put Iowa and Maine within a point of
each other while traders had them twenty-eight points apart. A rating says
which class a race is in; a poll says what is happening in it.

Wikipedia's per-race election articles carry two useful tables:

  a poll AGGREGATION table (RealClearPolitics, Silver Bulletin, 270toWin,
  FiftyPlusOne and others, each with its own average), and
  a table of INDIVIDUAL polls with pollster, dates, sample size and margin.

The aggregation table is preferred where it exists, for the same reason the
generic ballot uses one: those averages already handle house effects and
weighting better than a naive mean would. Where a race has no aggregation
table yet, individual polls are averaged here, weighted by recency and by
the square root of sample size.

Writes data/senate_polls_2026.json. The model blends this with the rating
baseline rather than replacing it, so a thinly polled race degrades
gracefully instead of lurching on one survey.

Run it directly:  python3 refresh_senate_polls.py
"""
import html
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

DATA = Path(__file__).parent / "data"
REST = "https://en.wikipedia.org/api/rest_v1/page/html/"
UA = {"User-Agent": "cd-election-agent/1.0 (https://github.com/Gh0stmahn-ai/cd-election-agent)"}
TIMEOUT = 60
RETRIES = 4
PAUSE = 2.0

HALF_LIFE_DAYS = 21      # a three-week-old poll counts half as much
MAX_AGE_DAYS = 120       # older than this and it is history, not news
MIN_POLLS = 2            # below this, report but let the model lean on the rating

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
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}

METADATA = ("poll source", "source of poll aggregation", "date", "sample size",
            "margin of error", "dates updated", "poll", "sponsor")


def fetch(title):
    url = REST + urllib.parse.quote(title.replace(" ", "_"), safe="")
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code != 429:
                raise
            last = e
            time.sleep(4 * (attempt + 1))
    raise last


def cells(row):
    out = []
    for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S):
        cell = re.sub(r"<sup.*?</sup>", " ", cell, flags=re.S)
        cell = re.sub(r"<[^>]+>", " ", cell)
        out.append(re.sub(r"\s+", " ", html.unescape(cell)).strip())
    return out


def last_percent(cell):
    """The trailing percentage in a cell.

    Wikipedia shades the leading candidate's cell, and the shading template
    leaves a wall of markup in front of the number. The value itself always
    comes last, so the last match is the reliable one.
    """
    found = re.findall(r"(\d+(?:\.\d+)?)\s*%", cell)
    return float(found[-1]) if found else None


def end_date(text):
    """The last date mentioned in a 'September 2-8, 2026' style range."""
    year = re.findall(r"\b(20\d\d)\b", text)
    if not year:
        return None
    y = int(year[-1])
    months = [(m.start(), m.group(1)) for m in re.finditer(r"\b(" + "|".join(MONTHS) + r")\b", text)]
    if not months:
        return None
    pos, month_name = months[-1]
    days = re.findall(r"\b(\d{1,2})\b", text[pos:])
    if not days:
        return None
    try:
        return date(y, MONTHS[month_name], min(int(days[-1]), 28))
    except ValueError:
        return None


def sample_size(text):
    m = re.search(r"([\d,]{3,})", text)
    return int(m.group(1).replace(",", "")) if m else None


def find_tables(page, dem_last, rep_last):
    """Tables that pit this race's two nominees against each other."""
    out = []
    for table in re.findall(r"<table.*?</table>", page, re.S):
        rows = re.findall(r"<tr.*?</tr>", table, re.S)
        if len(rows) < 2:
            continue
        header = cells(rows[0])
        d_idx = next((i for i, h in enumerate(header) if dem_last in h), None)
        r_idx = next((i for i, h in enumerate(header) if rep_last in h), None)
        if d_idx is None or r_idx is None:
            continue
        # A general-election table names exactly these two; a primary table
        # names several candidates from one party.
        named = [h for h in header if h and not any(m in h.lower() for m in METADATA)
                 and not re.match(r"(?i)^(other|undecided|margin|total|lead)", h)]
        if len(named) > 3:
            continue
        aggregation = any("aggregation" in h.lower() for h in header)
        out.append({"rows": rows, "header": header, "d": d_idx, "r": r_idx,
                    "aggregation": aggregation})
    return out


def read_rows(table):
    """(label, dem, rep, end date, sample size) for each usable row."""
    out = []
    width = len(table["header"])
    for row in table["rows"][1:]:
        c = cells(row)
        if len(c) != width:
            continue                       # continuation or event row
        dem, rep = last_percent(c[table["d"]]), last_percent(c[table["r"]])
        if dem is None or rep is None:
            continue
        joined = " ".join(c)
        size_cell = next((x for i, x in enumerate(c)
                          if "sample" in table["header"][i].lower()), "")
        out.append({"label": c[0], "dem": dem, "rep": rep,
                    "date": end_date(joined), "n": sample_size(size_cell)})
    return out


def average(entries, today):
    """Recency and sample-size weighted Democratic margin."""
    total_w, total_m, used = 0.0, 0.0, []
    for e in entries:
        if e["date"] is None:
            continue
        age = (today - e["date"]).days
        if age < 0 or age > MAX_AGE_DAYS:
            continue
        recency = 0.5 ** (age / HALF_LIFE_DAYS)
        size = math.sqrt(e["n"]) / math.sqrt(800) if e["n"] else 1.0
        w = recency * min(size, 1.6)
        total_w += w
        total_m += w * (e["dem"] - e["rep"])
        used.append({**e, "date": e["date"].isoformat(), "weight": round(w, 3),
                     "margin": round(e["dem"] - e["rep"], 1)})
    if not total_w:
        return None, []
    used.sort(key=lambda e: e["date"], reverse=True)
    return total_m / total_w, used


def article_titles(seat_id, state):
    name = STATE_NAMES.get(state, state)
    if "special" in seat_id:
        return [f"2026 United States Senate special election in {name}",
                f"2026 United States Senate election in {name}"]
    return [f"2026 United States Senate election in {name}"]


def main():
    races = json.loads((DATA / "senate_races_2026.json").read_text())["races"]
    competitive = [r for r in races if r["rating"] in ("tossup", "lean")]
    today = date.today()
    out, issues = {}, []

    print(f"Reading state polls for {len(competitive)} competitive Senate races...")
    for race in competitive:
        seat = race["seat_id"]
        dem = (race.get("dem_candidate") or "").replace("(I)", "").strip()
        rep = (race.get("rep_candidate") or "").strip()
        if not dem or not rep:
            issues.append(f"{seat}: both nominees are not settled yet")
            continue
        page = None
        for title in article_titles(seat, race["state"]):
            try:
                page = fetch(title)
            except Exception as e:  # noqa: BLE001
                issues.append(f"{seat}: {e}")
                page = None
            time.sleep(PAUSE)
            if page:
                break
        if not page:
            issues.append(f"{seat}: no article found")
            print(f"  {seat}: no article")
            continue

        tables = find_tables(page, dem.split()[-1], rep.split()[-1])
        agg = [t for t in tables if t["aggregation"]]
        polls = [t for t in tables if not t["aggregation"]]

        margin, used, method = None, [], None
        if agg:
            entries = read_rows(agg[0])
            named = [e for e in entries if not e["label"].lower().startswith("average")]
            avg_row = next((e for e in entries if e["label"].lower().startswith("average")), None)
            if avg_row:
                margin, method = avg_row["dem"] - avg_row["rep"], "aggregator average"
                used = [{"label": e["label"], "margin": round(e["dem"] - e["rep"], 1)}
                        for e in named]
            elif named:
                margin = sum(e["dem"] - e["rep"] for e in named) / len(named)
                method = f"mean of {len(named)} aggregators"
                used = [{"label": e["label"], "margin": round(e["dem"] - e["rep"], 1)}
                        for e in named]
        if margin is None and polls:
            entries = []
            for t in polls:
                entries.extend(read_rows(t))
            margin, used = average(entries, today)
            method = f"{len(used)} polls, recency and sample weighted" if used else None

        if margin is None:
            issues.append(f"{seat}: no usable head-to-head polling")
            print(f"  {seat}: no usable polling")
            continue

        out[seat] = {"state": race["state"], "dem_candidate": dem, "rep_candidate": rep,
                     "dem_margin": round(margin, 1), "method": method,
                     "n_used": len(used), "detail": used[:12],
                     "rating": race["rating"], "rating_lean": race["lean"]}
        side = "D" if margin >= 0 else "R"
        print(f"  {seat}: {side}+{abs(margin):.1f} ({method})")

    if not out:
        print("No Senate polling could be read; leaving any previous file alone.", file=sys.stderr)
        return 1
    payload = {"as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
               "source": "Wikipedia per-race election articles",
               "half_life_days": HALF_LIFE_DAYS, "max_age_days": MAX_AGE_DAYS,
               "races": out, "issues": issues}
    (DATA / "senate_polls_2026.json").write_text(json.dumps(payload, indent=1))
    print(f"\n{len(out)} of {len(competitive)} races have a polling average.")
    if issues:
        print(f"({len(issues)} issue(s) recorded in the file.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
