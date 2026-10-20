#!/usr/bin/env python3
"""
Election night: collect results as they come in, and grade the forecast.

    python3 results.py once                 # one read of every source, then write
    python3 results.py loop --minutes 340   # what the election-night workflow runs
    python3 results.py loop --minutes 10 --no-push   # a rehearsal

Reads the official state feeds and Wikipedia (results_feeds.py), keeps a
record per race, and writes three files:

  data/results_2026.json   what the site shows: every race with its forecast,
                           its count, its call, the seat tallies, and the
                           scorecard. Pages poll this file every minute.
  data/results_state.json  the collector's own memory between reads (feed
                           versions, how long a Wikipedia call has stood), so
                           a fresh job carries on where the last one stopped.
  data/results_log.jsonl   one line per call, retraction and change of
                           control, in order: the night as it happened.

The rules that keep a wrong number off the page:

  * Calls come only from Wikipedia, whose editors mark winners as the networks
    call them. A call is accepted only once it has stood for SETTLE minutes,
    and only after every poll in that state has closed. A mark that was there
    before the polls closed (a special-election winner left marked, say) is
    accepted only once vote shares appear beside it. If a call is taken down,
    it is withdrawn here too, and the withdrawal is logged.
  * Counts never make a call. The official feeds say how the count stands and
    how much of it is in; they do not say who won, and neither does this.
  * The forecast every race is graded against is the sealed final forecast,
    never anything later.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import results_feeds as F

BASE = Path(__file__).parent
DATA = BASE / "data"
RESULTS = DATA / "results_2026.json"
STATE = DATA / "results_state.json"
# The bulky working copies (feed payloads, parsed Wikipedia tables, feed
# versions) live in a file that is never committed: a fresh job simply reads
# every source again. Only what cannot be re-read -- how long each call has
# stood, the night's history -- goes in the committed state file.
CACHE = DATA / ".results_cache.json"
COMMITTED_KEYS = ("marks", "control", "history", "events")
LOG = DATA / "results_log.jsonl"
FINAL = DATA / "final_forecast.json"
LATEST = BASE / "iterations" / "latest.json"

SETTLE = timedelta(minutes=4)
# Counts this complete are treated as a result for margin scoring.
MARGIN_REPORTING = 0.95
WIKI_INTERVAL = 60

# When the LAST polls close in each state, Eastern time on November 3 (hours;
# 25 = 1 am on the 4th). Election Day is on standard time, so Eastern is UTC-5.
# Sources: Green Papers and 270toWin, 2026 general; where they differ (North
# Dakota) the later time is used, which can only delay a call, never rush one.
LAST_CLOSE_ET = {
    "AL": 20, "AK": 25, "AZ": 21, "AR": 20.5, "CA": 23, "CO": 21, "CT": 20, "DE": 20, "FL": 20,
    "GA": 19, "HI": 24, "ID": 23, "IL": 20, "IN": 19, "IA": 21, "KS": 21, "KY": 19, "LA": 21,
    "ME": 20, "MD": 20, "MA": 20, "MI": 21, "MN": 21, "MS": 20, "MO": 20, "MT": 22, "NE": 21,
    "NV": 22, "NH": 20, "NJ": 20, "NM": 21, "NY": 21, "NC": 19.5, "ND": 23, "OH": 19.5, "OK": 20,
    "OR": 23, "PA": 20, "RI": 20, "SC": 19, "SD": 21, "TN": 20, "TX": 21, "UT": 22, "VT": 19,
    "VA": 19, "WA": 23, "WV": 19.5, "WI": 21, "WY": 21,
}
DAY0_UTC = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)   # midnight Eastern, Nov 3
FIRST_CLOSE = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)


def polls_close(state):
    return DAY0_UTC + timedelta(hours=LAST_CLOSE_ET.get(state, 25))


def utcnow():
    override = os.environ.get("RESULTS_NOW")   # testing only
    if override:
        return datetime.fromisoformat(override.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)


def iso(t):
    return t.isoformat(timespec="seconds") if t else None


def parse_time(s):
    return datetime.fromisoformat(s) if s else None


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_json(path, obj, compact=False):
    Path(path).write_text(json.dumps(obj, separators=(",", ":")) if compact else json.dumps(obj, indent=1))


# ================================================================ the forecast
def load_forecast():
    """The sealed final forecast, or (before the seal, for rehearsals) the latest run."""
    final = load_json(FINAL, None)
    if final:
        return final["forecast"], final.get("markets") or {}, final.get("baselines") or {}, True
    snap = load_json(LATEST, None)
    if snap is None:
        raise SystemExit("No forecast found (data/final_forecast.json or iterations/latest.json).")
    try:
        import freeze
        baselines = {"senate_rating_probs": {s["seat_id"]: s.get("rating_prob") for s in snap["senate"]["seats"]},
                     "house_rating_probs": freeze._house_rating_probs(snap)}
        markets = freeze._market_record(snap) or {}
    except Exception:  # noqa: BLE001 - a rehearsal can live without baselines
        baselines, markets = {}, {}
    return snap, markets, baselines, False


def surname(name):
    parts = [p for p in str(name or "").replace(",", " ").split()
             if p.lower().strip(".") not in ("jr", "sr", "ii", "iii", "iv", "(i)")]
    return parts[-1].lower().strip(".") if parts else ""


def registry(snap, markets, baselines):
    """Every race on the ballot with what the forecast said about it."""
    races = {}
    sen_rating = baselines.get("senate_rating_probs", {})
    sen_market = (markets or {}).get("senate_races", {})
    for s in snap["senate"]["seats"]:
        races[s["seat_id"]] = {
            "id": s["seat_id"], "chamber": "senate", "state": s["state"],
            "label": F.STATE_NAMES_LONG.get(s["state"], s["state"]) + (" (special)" if "special" in s["seat_id"] else ""),
            "held_by": s.get("held_by"),
            "dem_candidate": s.get("dem_candidate"), "rep_candidate": s.get("rep_candidate"),
            "independent_side": s.get("challenger_caucus") == "independent",
            "forecast": {"p": s["dem_win_prob"], "margin": s.get("expected_margin"),
                         "rating_p": sen_rating.get(s["seat_id"]), "market_p": sen_market.get(s["seat_id"])},
        }
    h = snap["house"]
    margins = h.get("district_margins", {})
    house_rating = baselines.get("house_rating_probs", {})
    for rid, p in h["district_probs"].items():
        races[rid] = {
            "id": rid, "chamber": "house", "state": rid.split("-")[0], "label": rid,
            "rating": h.get("district_ratings", {}).get(rid),
            "forecast": {"p": p, "margin": margins.get(rid), "rating_p": house_rating.get(rid),
                         "market_p": None},
        }
    for r in races.values():
        r["polls_close"] = iso(polls_close(r["state"]))
    return races


# ================================================================== sides
def dem_side(race, candidates, key="party"):
    """The candidate the forecast's 'Democratic' probability is about.

    Nebraska's challenger runs as an independent, and the model's number there
    is the chance he wins; everywhere else it is the Democrat."""
    if race.get("independent_side"):
        target = surname((race.get("dem_candidate") or "").replace("(I)", ""))
        for c in candidates:
            if target and surname(c["name"]) == target:
                return c
    ds = [c for c in candidates if c.get(key) == "D"]
    return max(ds, key=lambda c: c.get("votes") or c.get("pct") or 0) if ds else None


def rep_side(candidates, key="party"):
    rs = [c for c in candidates if c.get(key) == "R"]
    return max(rs, key=lambda c: c.get("votes") or c.get("pct") or 0) if rs else None


# ================================================================== counts
def count_from_official(race, contest):
    cands = sorted(contest["candidates"], key=lambda c: -c["votes"])
    total = sum(c["votes"] for c in cands)
    d, r = dem_side(race, cands), rep_side(cands)
    margin = None
    if total > 0 and d and r:
        margin = round(100.0 * (d["votes"] - r["votes"]) / total, 2)
    return {
        "source": contest["source"], "total": total,
        "candidates": [{"name": c["name"], "party": c["party"], "votes": c["votes"],
                        "pct": round(100.0 * c["votes"] / total, 1) if total else None}
                       for c in cands[:4]],
        "dem_votes": d["votes"] if d else None, "rep_votes": r["votes"] if r else None,
        "margin": margin, "reporting": contest["reporting"], "reporting_label": contest["reporting_label"],
        "as_of": contest["as_of"],
    }


def count_from_wiki(race, rows):
    """Wikipedia shares, once editors have added them (usually after the call)."""
    if not rows or not any(c.get("pct") is not None for c in rows):
        return None
    d, r = dem_side(race, rows), rep_side(rows)
    margin = round(d["pct"] - r["pct"], 2) if d and r and d.get("pct") is not None and r.get("pct") is not None else None
    return {"source": "wikipedia", "total": None,
            "candidates": [{"name": c["name"], "party": c["party"], "votes": None, "pct": c.get("pct")}
                           for c in sorted(rows, key=lambda c: -(c.get("pct") or 0))[:4]],
            "dem_votes": None, "rep_votes": None, "margin": margin, "reporting": None,
            "reporting_label": "Shares as listed on Wikipedia", "as_of": None}


# =================================================================== calls
def wiki_rows_for(race, wiki):
    if race["chamber"] == "house":
        return (wiki.get("house") or {}).get("rows", {}).get(race["id"])
    options = (wiki.get("senate") or {}).get("rows", {}).get(race["state"]) or []
    if len(options) <= 1:
        return options[0] if options else None
    # More than one row for the state: the one naming this race's candidates.
    want = {surname(race.get("dem_candidate")), surname(race.get("rep_candidate"))} - {""}
    best = max(options, key=lambda rows: len(want & {surname(c["name"]) for c in rows}))
    return best


def update_call(race, rows, marks, now, log):
    """Advance one race's call by the rules in the module docstring."""
    rid = race["id"]
    mark = marks.setdefault(rid, {})
    if not rows:
        # No row for this race in this read: a parse problem, not a fact about
        # the race. Whatever was called stays called.
        return
    winner = next((c for c in rows if c.get("winner")), None)
    close = polls_close(race["state"])
    accepted = mark.get("accepted")

    # Nobody on the ballot but one party -- an unopposed member, or two
    # Democrats in a California top-two -- has only one possible result, and it
    # is certain the moment the polls close. No need to wait on an editor.
    # Only where the forecast agrees it is a foregone conclusion: a contested
    # race that merely LOOKS one-party because a candidate line failed to parse
    # must not be called on that alone.
    parties = {c["party"] for c in rows}
    p = (race.get("forecast") or {}).get("p")
    foregone = p is not None and ((parties == {"D"} and p >= 0.98) or (parties == {"R"} and p <= 0.02))
    if winner is None and len(parties) == 1 and foregone and now >= close and not accepted:
        only = parties.pop()
        names = [c["name"] for c in rows]
        accepted = {"party": only, "name": names[0] if len(names) == 1 else f"{len(names)} "
                    + ("Democrats" if only == "D" else "Republicans"),
                    "dem_side": only == "D" and not race.get("independent_side"),
                    "at": iso(now), "source": "one party on the ballot"}
        mark["accepted"] = accepted
        log.append({"t": iso(now), "type": "call", "race": rid, "winner": only, "name": accepted["name"],
                    "dem_side": accepted["dem_side"], "uncontested": True})
        return
    if accepted and accepted.get("source") == "one party on the ballot" and winner is None:
        return

    if winner is None:
        mark.pop("pending", None)
        if accepted:
            since = parse_time(mark.get("absent_since")) or now
            mark["absent_since"] = iso(since)
            if now - since >= SETTLE:
                log.append({"t": iso(now), "type": "retraction", "race": rid, "was": accepted})
                mark.pop("accepted", None)
                mark.pop("absent_since", None)
        return

    mark.pop("absent_since", None)
    party = winner["party"]
    d = dem_side(race, rows)
    is_dem_side = bool(d) and surname(d["name"]) == surname(winner["name"]) and d["party_raw"] == winner["party_raw"]
    key = {"party": party, "name": winner["name"], "dem_side": is_dem_side}
    pending = mark.get("pending")
    if not pending or {k: pending[k] for k in key} != key:
        pending = dict(key, first_seen=iso(now), pre_close=now < close)
        mark["pending"] = pending
    has_shares = any(c.get("pct") is not None for c in rows)
    settled = now - parse_time(pending["first_seen"]) >= SETTLE
    fresh = not pending["pre_close"] or has_shares
    if settled and now >= close and fresh:
        if not accepted or {k: accepted[k] for k in key} != key:
            accepted = dict(key, at=iso(now), source="wikipedia")
            mark["accepted"] = accepted
            log.append({"t": iso(now), "type": "call", "race": rid, "winner": party,
                        "name": winner["name"], "dem_side": is_dem_side})


# ================================================================== tallies
def tallies(races, snap):
    not_up = snap["senate"].get("not_up", {"D": 0, "R": 0})
    sen = {"D": not_up.get("D", 0), "R": not_up.get("R", 0), "I": 0, "uncalled": 0, "expected_D": 0.0}
    hou = {"D": 0, "R": 0, "O": 0, "uncalled": 0, "expected_D": 0.0}
    for r in races.values():
        call = r.get("call")
        t = sen if r["chamber"] == "senate" else hou
        if call is None:
            t["uncalled"] += 1
            if not (r["chamber"] == "senate" and r.get("independent_side")):
                t["expected_D"] += r["forecast"]["p"]
            continue
        if r["chamber"] == "senate":
            if call["party"] == "D":
                sen["D"] += 1
            elif call["party"] == "R":
                sen["R"] += 1
            else:
                sen["I"] += 1
        else:
            hou[call["party"] if call["party"] in ("D", "R") else "O"] += 1
    sen["expected_D"] = round(sen["D"] + sen["expected_D"], 1)
    hou["expected_D"] = round(hou["D"] + hou["expected_D"], 1)
    majority = snap["house"].get("majority", 218)
    sen["control"] = "D" if sen["D"] >= 51 else "R" if sen["R"] >= 50 else None
    hou["control"] = "D" if hou["D"] >= majority else "R" if hou["R"] >= majority else None
    hou["majority"] = majority
    return {"senate": sen, "house": hou}


# ================================================================ scorecard
def _clip(p):
    return min(max(p, 0.001), 0.999)


def _brier(pairs):
    return round(sum((p - y) ** 2 for p, y in pairs) / len(pairs), 4) if pairs else None


def _logloss(pairs):
    return (round(-sum(y * math.log(_clip(p)) + (1 - y) * math.log(1 - _clip(p)) for p, y in pairs) / len(pairs), 4)
            if pairs else None)


def _percentile(hist, value):
    """Share of the forecast's simulations at or below value, and strictly below."""
    below = sum(p for k, p in hist.items() if int(k) < value)
    at = sum(p for k, p in hist.items() if int(k) == value)
    return round(below, 4), round(below + at, 4)


def scorecard(races, tally, snap, markets):
    called = [r for r in races.values() if r.get("call")]
    pairs = {"model": [], "rating": [], "market": [], "model_on_market": []}
    by_chamber = {"senate": [], "house": []}
    bins = [(0, .1), (.1, .3), (.3, .5), (.5, .7), (.7, .9), (.9, 1.0001)]
    cal = [{"lo": lo, "hi": min(hi, 1), "n": 0, "p_sum": 0.0, "wins": 0} for lo, hi in bins]
    misses, margin_err = [], {"senate": [], "house": []}
    for r in called:
        y = 1 if r["call"]["dem_side"] else 0
        f = r["forecast"]
        p = f["p"]
        pairs["model"].append((p, y))
        by_chamber[r["chamber"]].append((p, y))
        if f.get("rating_p") is not None:
            pairs["rating"].append((f["rating_p"], y))
        if f.get("market_p") is not None:
            pairs["market"].append((f["market_p"], y))
            pairs["model_on_market"].append((p, y))
        for b in cal:
            if b["lo"] <= p < b["hi"] or (p == 1 and b["hi"] >= 1):
                b["n"] += 1
                b["p_sum"] += p
                b["wins"] += y
                break
        p_winner = p if y == 1 else 1 - p
        if p_winner < 0.5:
            misses.append({"id": r["id"], "label": r["label"], "chamber": r["chamber"],
                           "winner": r["call"]["party"], "name": r["call"]["name"], "p_winner": round(p_winner, 4)})
        c = r.get("count")
        if (c and c.get("margin") is not None and f.get("margin") is not None
                and (c["source"] == "wikipedia" or (c.get("reporting") or 0) >= MARGIN_REPORTING)):
            margin_err[r["chamber"]].append({"id": r["id"], "label": r["label"], "forecast": f["margin"],
                                             "actual": c["margin"], "error": round(c["margin"] - f["margin"], 2)})
    for b in cal:
        p_sum = b.pop("p_sum")
        b["mean_p"] = round(p_sum / b["n"], 4) if b["n"] else None
        b["rate"] = round(b["wins"] / b["n"], 4) if b["n"] else None

    def margin_summary(errs):
        if not errs:
            return None
        e = [x["error"] for x in errs]
        return {"n": len(e), "bias": round(sum(e) / len(e), 2), "mae": round(sum(abs(x) for x in e) / len(e), 2),
                "worst": sorted(errs, key=lambda x: -abs(x["error"]))[:8]}

    control = {}
    for chamber in ("senate", "house"):
        t = tally[chamber]
        p = snap[chamber]["dem_control_prob"]
        mk = (markets or {}).get(f"{chamber}_dem", {})
        entry = {"forecast_p": p, "outcome": t["control"],
                 "market_p": {k: v for k, v in mk.items() if v is not None}}
        if t["control"]:
            y = 1 if t["control"] == "D" else 0
            entry["p_outcome"] = round(p if y else 1 - p, 4)
        control[chamber] = entry

    seats = {}
    sen_hist = snap["senate"].get("histogram", {})
    hou_hist = snap["house"].get("histogram", {})
    if tally["senate"]["uncalled"] == 0:
        actual = tally["senate"]["D"]
        lo, hi = _percentile(sen_hist, actual)
        pc = snap["senate"]["percentiles"]
        seats["senate"] = {"actual_D": actual, "forecast_median": pc.get("50"), "below": lo, "at_or_below": hi,
                           "in_80": int(pc["10"]) <= actual <= int(pc["90"])}
    if tally["house"]["uncalled"] == 0:
        actual = tally["house"]["D"]
        lo, hi = _percentile(hou_hist, actual)
        pc = snap["house"]["percentiles"]
        seats["house"] = {"actual_D": actual, "forecast_median": pc.get("50"), "below": lo, "at_or_below": hi,
                          "in_80": int(pc["10"]) <= actual <= int(pc["90"])}

    return {
        "races_called": len(called),
        "brier": {"model": _brier(pairs["model"]), "rating": _brier(pairs["rating"]),
                  "coin_flip": 0.25 if pairs["model"] else None,
                  "senate": _brier(by_chamber["senate"]), "house": _brier(by_chamber["house"]),
                  "market": _brier(pairs["market"]), "model_on_market_races": _brier(pairs["model_on_market"]),
                  "n_market": len(pairs["market"])},
        "log_loss": {"model": _logloss(pairs["model"]), "rating": _logloss(pairs["rating"])},
        "calibration": cal,
        "misses": sorted(misses, key=lambda m: m["p_winner"])[:15],
        "n_misses": len(misses),
        "margins": {k: margin_summary(v) for k, v in margin_err.items()},
        "control": control,
        "seats": seats,
    }


# ===================================================================== merge
def phase(now, tally):
    if now < FIRST_CLOSE:
        return "before"
    if tally["senate"]["uncalled"] == 0 and tally["house"]["uncalled"] == 0:
        return "complete"
    return "counting"


def build(state, now, snap, markets, baselines, frozen, sources_status, previous=None):
    """Everything the site shows, from the collector's state.

    previous is the last published results file. A count is carried over from
    it when no source has supplied one in this job yet -- the first minutes
    after a relay handoff, or a feed that is down -- so a count never vanishes
    from the page just because a job restarted."""
    races = registry(snap, markets, baselines)
    wiki = state.get("wiki", {})
    marks = state.setdefault("marks", {})
    official = state.get("official", {})
    prev_races = (previous or {}).get("races", {})
    log = []
    for r in races.values():
        rows = wiki_rows_for(r, wiki)
        # Calls only move on a chamber whose article has been read in this job.
        # Until then the committed calls stand exactly as they were.
        if (wiki.get(r["chamber"]) or {}).get("rows"):
            update_call(r, rows, marks, now, log)
        accepted = marks.get(r["id"], {}).get("accepted")
        r["call"] = accepted
        contest = official.get(r["id"])
        r["count"] = count_from_official(r, contest) if contest else count_from_wiki(r, rows)
        if r["count"] is None and (prev_races.get(r["id"]) or {}).get("count"):
            r["count"] = prev_races[r["id"]]["count"]
        if r["call"]:
            r["status"] = "called"
        elif now >= polls_close(r["state"]):
            r["status"] = "counting"
        else:
            r["status"] = "waiting"
    t = tallies(races, snap)
    prev_control = state.get("control", {})
    for chamber in ("senate", "house"):
        if t[chamber]["control"] and prev_control.get(chamber) != t[chamber]["control"]:
            log.append({"t": iso(now), "type": "control", "chamber": chamber, "winner": t[chamber]["control"]})
    state["control"] = {c: t[c]["control"] for c in ("senate", "house")}

    events = state.setdefault("events", [])
    for line in log:
        r = races.get(line.get("race"), {})
        events.append(dict(line, label=r.get("label"), chamber=r.get("chamber"),
                           p=(r.get("forecast") or {}).get("p")))
    del events[:-60]

    history = state.setdefault("history", [])
    point = {"t": iso(now), "s": [t["senate"]["D"], t["senate"]["R"], t["senate"]["I"]],
             "h": [t["house"]["D"], t["house"]["R"]]}
    if not history or history[-1]["s"] != point["s"] or history[-1]["h"] != point["h"]:
        history.append(point)

    payload = {
        "updated": iso(now),
        "phase": phase(now, t),
        "frozen_forecast": frozen,
        "forecast_timestamp": snap["timestamp"],
        "first_close": iso(FIRST_CLOSE),
        "tally": t,
        "history": history,
        "events": events[::-1],
        "scorecard": scorecard(races, t, snap, markets),
        "sources": sources_status,
        "races": races,
        "rules": ("Calls come from Wikipedia's race tables and are accepted only after standing for "
                  f"{int(SETTLE.total_seconds() // 60)} minutes and after every poll in the state has closed; "
                  "a race with only one party on the ballot, which the forecast also had as certain, is called "
                  "when its polls close. Counts come from official state feeds where they exist; counts never "
                  "make a call."),
    }
    return payload, log


# ===================================================================== reads
def read_sources(state, now, senate_by_state, only_due=True):
    """Read whatever is due. Returns True if anything changed.

    Wikipedia goes first because it is the only source of calls; the official
    feeds are then read side by side, so one slow state costs one timeout, not
    seven of them in a row."""
    from concurrent.futures import ThreadPoolExecutor
    changed = False
    caches = state.setdefault("caches", {})
    status = state.setdefault("sources", {})
    official = state.setdefault("official", {})
    last = state.setdefault("last_read", {})

    def due(name, interval):
        t = parse_time(last.get(name))
        return not only_due or t is None or (now - t).total_seconds() >= interval - 5

    if due("wikipedia", WIKI_INTERVAL):
        last["wikipedia"] = iso(now)
        entry = status.setdefault("wikipedia", {})
        wiki = state.setdefault("wiki", {})
        try:
            if F.fetch_wikipedia(wiki):
                changed = True
                entry["last_change"] = iso(now)
            entry.update(ok=True, error=None, last_ok=iso(now),
                         contests=sum(len((wiki.get(k) or {}).get("rows") or {}) for k in ("senate", "house")),
                         revisions={k: (wiki.get(k) or {}).get("revid") for k in ("senate", "house")})
        except Exception as e:  # noqa: BLE001
            entry.update(ok=False, error=f"{type(e).__name__}: {e}"[:300], last_error=iso(now))

    jobs = {}
    for st, (fn, interval) in F.OFFICIAL.items():
        name = st.lower()
        if due(name, interval):
            last[name] = iso(now)
            jobs[name] = (fn, caches.setdefault(name, {}), senate_by_state.get(st))
    if jobs:
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {name: pool.submit(fn, cache, seat) for name, (fn, cache, seat) in jobs.items()}
        for name, fut in futures.items():
            entry = status.setdefault(name, {})
            try:
                out = fut.result()
                entry.update(ok=True, error=None, last_ok=iso(now))
                if out is not None:
                    for c in out:
                        official[c["race_id"]] = c
                    entry["last_change"] = iso(now)
                    entry["contests"] = len(out)
                    changed = True
                if not entry.get("contests") and now >= FIRST_CLOSE:
                    # Reachable but nothing in it after the polls have started
                    # closing: not a failure yet, not a working source either.
                    entry.update(ok=None, error="No federal contests in the file yet")
            except LookupError as e:
                # Reachable, but the state has not put the November file up yet.
                entry.update(ok=None, error=str(e)[:300], last_error=iso(now))
            except Exception as e:  # noqa: BLE001 - one dead feed never stops the night
                entry.update(ok=False, error=f"{type(e).__name__}: {e}"[:300], last_error=iso(now))
    return changed


def public_sources(state):
    out = {}
    for key, (label, url) in F.SOURCE_NAMES.items():
        s = state.get("sources", {}).get(key, {})
        out[key] = {"label": label, "url": url, "ok": s.get("ok"), "error": s.get("error"),
                    "last_ok": s.get("last_ok"), "last_change": s.get("last_change"),
                    "contests": s.get("contests")}
    return out


def step(state, only_due=True, now=None):
    """One read-merge-write. Returns (payload, log lines, anything changed?)."""
    now = now or utcnow()
    snap, markets, baselines, frozen = load_forecast()
    senate_by_state = {s["state"]: s["seat_id"] for s in snap["senate"]["seats"]}
    changed = read_sources(state, now, senate_by_state, only_due)
    payload, log = build(state, now, snap, markets, baselines, frozen, public_sources(state),
                         previous=load_json(RESULTS, None))
    return payload, log, changed or bool(log)


def load_state():
    state = load_json(CACHE, {})
    committed = load_json(STATE, {})
    for k in COMMITTED_KEYS:
        if k in committed:
            state[k] = committed[k]
    return state


def save(state, payload, log):
    state["marks"] = {k: v for k, v in state.get("marks", {}).items() if v}
    write_json(STATE, {k: state[k] for k in COMMITTED_KEYS if k in state}, compact=True)
    write_json(CACHE, {k: v for k, v in state.items() if k not in COMMITTED_KEYS}, compact=True)
    write_json(RESULTS, payload, compact=True)
    if log:
        with open(LOG, "a") as fh:
            for line in log:
                fh.write(json.dumps(line) + "\n")


# ===================================================================== git
GIT_ENV = dict(os.environ, GIT_HTTP_LOW_SPEED_LIMIT="1000", GIT_HTTP_LOW_SPEED_TIME="20",
               GIT_TERMINAL_PROMPT="0")
PUSH_FAILURES = 0


def git(*args, check=True):
    """git with a hard time limit: a stalled connection must cost one push, not
    the rest of the night."""
    try:
        return subprocess.run(["git", *args], cwd=BASE, check=check, capture_output=True, text=True,
                              timeout=120, env=GIT_ENV)
    except subprocess.TimeoutExpired:
        if check:
            raise
        return subprocess.CompletedProcess(args, 124, "", "timed out")


def push(message):
    global PUSH_FAILURES
    paths = [p for p in ("data/results_2026.json", "data/results_state.json", "data/results_log.jsonl", "site")
             if (BASE / p).exists()]
    git("add", "--", *paths)
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    git("commit", "-q", "-m", message)
    # RESULTS_BRANCH lets a rehearsal push somewhere harmless; the real night
    # pushes to main.
    branch = os.environ.get("RESULTS_BRANCH") or "main"
    for attempt in range(5):
        fetched = git("fetch", "-q", "origin", branch, check=False).returncode == 0
        if fetched:
            # Replaying our commit on top: on a conflict in a generated file,
            # ours (the commit being replayed) is the newer read, so it wins.
            rb = git("rebase", "-X", "theirs", f"origin/{branch}", check=False)
            if rb.returncode != 0:
                # Anything -X theirs cannot settle (a file deleted upstream,
                # say): every file here is regenerated each minute, so drop
                # back onto the remote and commit this read again.
                git("rebase", "--abort", check=False)
                git("reset", "--soft", f"origin/{branch}", check=False)
                git("add", "--", *[p for p in paths if (BASE / p).exists()], check=False)
                git("commit", "-q", "-m", message, check=False)
        if git("push", "-q", "origin", f"HEAD:refs/heads/{branch}", check=False).returncode == 0:
            PUSH_FAILURES = 0
            return True
        time.sleep(3 * (attempt + 1))
    PUSH_FAILURES += 1
    print("  ! push failed after 5 attempts", file=sys.stderr)
    if PUSH_FAILURES >= 3:
        # Shows up as an annotation on the run, not only in a log nobody reads.
        print(f"::error title=Results not publishing::{PUSH_FAILURES} pushes in a row have failed", flush=True)
    return False


def rebuild_site():
    r = subprocess.run([sys.executable, "build_site.py"], cwd=BASE, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ! site rebuild failed: {r.stderr.strip()[-400:]}", file=sys.stderr)


# ===================================================================== main
def summary_line(payload):
    t = payload["tally"]
    s, h = t["senate"], t["house"]
    return (f"Senate D{s['D']} R{s['R']}" + (f" I{s['I']}" if s["I"] else "") + f" ({s['uncalled']} uncalled), "
            f"House D{h['D']} R{h['R']} ({h['uncalled']} uncalled)")


def run_once(push_changes=False, rebuild=True):
    state = load_state()
    payload, log, changed = step(state, only_due=False)
    save(state, payload, log)
    print(f"{payload['updated']} {summary_line(payload)}")
    for name, s in payload["sources"].items():
        print(f"  {name:10} {'ok ' if s['ok'] else 'ERR' if s['ok'] is False else '-  '} "
              f"{s.get('contests') or ''} {s.get('error') or ''}")
    if rebuild:
        rebuild_site()
    if push_changes:
        push(f"Results: {summary_line(payload)}")


def run_loop(minutes, interval, push_changes, site_every):
    """Read, write and publish until the time is up. Built to be restarted:
    everything it needs is in the state file it commits."""
    deadline = time.monotonic() + minutes * 60
    last_site = 0.0
    while time.monotonic() < deadline:
        started = time.monotonic()
        try:
            state = load_state()
            payload, log, changed = step(state)
            save(state, payload, log)
            calls_moved = any(x["type"] in ("call", "retraction", "control") for x in log)
            if calls_moved or time.monotonic() - last_site >= site_every:
                rebuild_site()
                last_site = time.monotonic()
            print(f"{payload['updated']} {summary_line(payload)}"
                  + (f"  [{len(log)} event(s)]" if log else "") + ("" if changed else "  (no change)"), flush=True)
            if push_changes and (changed or calls_moved):
                push(f"Results: {summary_line(payload)}")
        except Exception:  # noqa: BLE001 - the loop outlives any one bad read
            traceback.print_exc()
        time.sleep(max(5.0, interval - (time.monotonic() - started)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("once")
    o.add_argument("--push", action="store_true")
    o.add_argument("--no-site", action="store_true")
    lp = sub.add_parser("loop")
    lp.add_argument("--minutes", type=float, default=340)
    lp.add_argument("--interval", type=float, default=60)
    lp.add_argument("--site-every", type=float, default=600, help="seconds between full site rebuilds")
    lp.add_argument("--no-push", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "once":
        run_once(push_changes=args.push, rebuild=not args.no_site)
    else:
        run_loop(args.minutes, args.interval, not args.no_push, args.site_every)


if __name__ == "__main__":
    main()
