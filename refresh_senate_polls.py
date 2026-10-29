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

HALF_LIFE_DAYS = 21      # a three-week-old poll counts half as much...
HALF_LIFE_FINAL = 10     # ...falling to ten days over the last month, when races move late
ELECTION_DAY = date(2026, 11, 3)
# A poll a party or campaign paid for (Wikipedia tags the sponsor "(R)" or
# "(D)") leans toward its sponsor: released internal polls have run several
# points friendlier to their side than independent ones. Each is moved this
# far toward the other party and counts half as much.
PARTISAN_SHIFT = 2.5
PARTISAN_WEIGHT = 0.5
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


def half_life(today):
    """Days for a poll's weight to halve: 21 until a month out, 10 in the last two weeks."""
    left = (ELECTION_DAY - today).days
    if left >= 30:
        return HALF_LIFE_DAYS
    if left <= 14:
        return HALF_LIFE_FINAL
    return HALF_LIFE_FINAL + (HALF_LIFE_DAYS - HALF_LIFE_FINAL) * (left - 14) / 16


def sponsor(label):
    """'R' or 'D' when the poll was paid for by a party or campaign."""
    m = re.search(r"\((R|D)\)", label or "")
    return m.group(1) if m else None


def average(entries, today):
    """Recency and sample-size weighted Democratic margin, with sponsored polls
    moved toward the other party and counted half, and each poll counted once
    (a pollster's table often repeats one survey with and without minor
    candidates)."""
    total_w, total_m, total_u, used, seen = 0.0, 0.0, 0.0, [], set()
    hl = half_life(today)
    for e in entries:
        if e["date"] is None:
            continue
        key = (e["label"], e["date"])
        if key in seen:
            continue
        seen.add(key)
        age = (today - e["date"]).days
        if age < 0 or age > MAX_AGE_DAYS:
            continue
        recency = 0.5 ** (age / hl)
        size = math.sqrt(e["n"]) / math.sqrt(800) if e["n"] else 1.0
        w = recency * min(size, 1.6)
        margin = e["dem"] - e["rep"]
        side = sponsor(e["label"])
        if side:
            w *= PARTISAN_WEIGHT
            margin += PARTISAN_SHIFT if side == "R" else -PARTISAN_SHIFT
        total_w += w
        total_m += w * margin
        total_u += w * max(0.0, 100 - e["dem"] - e["rep"])
        used.append({**e, "date": e["date"].isoformat(), "weight": round(w, 3),
                     "margin": round(margin, 1), "sponsor": side})
    if not total_w:
        return None, []
    used.sort(key=lambda e: e["date"], reverse=True)
    average.undecided = total_u / total_w
    return total_m / total_w, used


def race_average(page, dem, rep, today):
    """One race's polling average from its Wikipedia article.

    Where the article has both an aggregator table and the individual polls,
    the two are averaged: the aggregators treat sponsored polls differently
    (some include them as published), so our own adjusted average of the
    polls is half of the answer. Returns None or
    {margin, method, n_used, detail, undecided}."""
    tables = find_tables(page, dem.split()[-1], rep.split()[-1])
    agg = [t for t in tables if t["aggregation"]]
    polls = [t for t in tables if not t["aggregation"]]
    agg_margin, agg_used, agg_method, agg_u = None, [], None, None
    if agg:
        entries = read_rows(agg[0])
        named = [e for e in entries if not e["label"].lower().startswith("average")]
        avg_row = next((e for e in entries if e["label"].lower().startswith("average")), None)
        pick = [avg_row] if avg_row else named
        if pick:
            agg_margin = sum(e["dem"] - e["rep"] for e in pick) / len(pick)
            agg_u = sum(max(0.0, 100 - e["dem"] - e["rep"]) for e in pick) / len(pick)
            agg_method = "aggregator average" if avg_row else f"mean of {len(named)} aggregators"
            agg_used = [{"label": e["label"], "margin": round(e["dem"] - e["rep"], 1)} for e in named]
    poll_margin, poll_used = None, []
    if polls:
        entries = []
        for t in polls:
            entries.extend(read_rows(t))
        average.undecided = None
        poll_margin, poll_used = average(entries, today)
    poll_u = getattr(average, "undecided", None) if poll_used else None
    recent = [e for e in poll_used if (today - date.fromisoformat(e["date"])).days <= 30]
    if agg_margin is not None and poll_margin is not None and len(recent) >= 3:
        return {"margin": (agg_margin + poll_margin) / 2,
                "method": f"{agg_method}, averaged with {len(poll_used)} polls adjusted for sponsors",
                "n_used": max(len(agg_used), len(poll_used)),
                "aggregator_margin": round(agg_margin, 1), "poll_margin": round(poll_margin, 1),
                "detail": agg_used + poll_used[:8],
                "undecided": round(poll_u if poll_u is not None else agg_u, 1)}
    if agg_margin is not None:
        return {"margin": agg_margin, "method": agg_method, "n_used": len(agg_used),
                "aggregator_margin": round(agg_margin, 1), "detail": agg_used,
                "undecided": round(agg_u, 1) if agg_u is not None else None}
    if poll_margin is not None:
        return {"margin": poll_margin, "method": f"{len(poll_used)} polls, recency and sample weighted, "
                                                 "adjusted for sponsors",
                "n_used": len(poll_used), "poll_margin": round(poll_margin, 1), "detail": poll_used[:12],
                "undecided": round(poll_u, 1) if poll_u is not None else None}
    return None


def carry_forward(seat, dem, rep, path=None, max_days=3):
    """Yesterday's average for a race whose article could not be read today
    (Wikipedia rate-limits bursts), so one failed read does not drop a race's
    polls from the forecast. Only for the same two nominees, and only for a
    few days; marked as carried."""
    path = path or (DATA / "senate_polls_2026.json")
    try:
        prev = json.loads(Path(path).read_text())
        entry = prev["races"][seat]
        age = (datetime.now().astimezone() - datetime.fromisoformat(entry.get("read_at") or prev["as_of"])).days
    except (OSError, ValueError, KeyError):
        return None
    if age > max_days or entry.get("dem_candidate") != dem or entry.get("rep_candidate") != rep:
        return None
    return dict(entry, carried=True, read_at=entry.get("read_at") or prev["as_of"])


def article_titles(seat_id, state):
    name = STATE_NAMES.get(state, state)
    if "special" in seat_id:
        return [f"2026 United States Senate special election in {name}",
                f"2026 United States Senate election in {name}"]
    return [f"2026 United States Senate election in {name}"]


def main():
    races = json.loads((DATA / "senate_races_2026.json").read_text())["races"]
    # Likely races too, as for governors: a race Cook moves from Lean to
    # Likely (Georgia and North Carolina this October) should keep its polls.
    competitive = [r for r in races if r["rating"] in ("tossup", "lean", "likely")]
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
            kept = carry_forward(seat, dem, rep)
            if kept:
                out[seat] = kept
                print(f"  {seat}: no article read; kept the last average")
            else:
                print(f"  {seat}: no article")
            continue

        got = race_average(page, dem, rep, today)
        if got is None:
            issues.append(f"{seat}: no usable head-to-head polling")
            print(f"  {seat}: no usable polling")
            continue
        margin, method, used = got["margin"], got["method"], got["detail"]

        out[seat] = {"state": race["state"], "dem_candidate": dem, "rep_candidate": rep,
                     "dem_margin": round(margin, 1), "method": method,
                     "n_used": got["n_used"], "detail": used[:16],
                     "aggregator_margin": got.get("aggregator_margin"), "poll_margin": got.get("poll_margin"),
                     "undecided": got.get("undecided"),
                     "rating": race["rating"], "rating_lean": race["lean"],
                     "read_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        side = "D" if margin >= 0 else "R"
        print(f"  {seat}: {side}+{abs(margin):.1f} ({method})")

    if not out:
        print("No Senate polling could be read; leaving any previous file alone.", file=sys.stderr)
        return 1
    payload = {"as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
               "source": "Wikipedia per-race election articles",
               "half_life_days": round(half_life(today), 1), "max_age_days": MAX_AGE_DAYS,
               "partisan_shift": PARTISAN_SHIFT, "partisan_weight": PARTISAN_WEIGHT,
               "races": out, "issues": issues}
    (DATA / "senate_polls_2026.json").write_text(json.dumps(payload, indent=1))
    print(f"\n{len(out)} of {len(competitive)} races have a polling average.")
    if issues:
        print(f"({len(issues)} issue(s) recorded in the file.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
