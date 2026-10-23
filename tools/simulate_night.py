#!/usr/bin/env python3
"""
Rehearse election night without an election.

Draws one plausible outcome from the forecast's own error model, then plays the
night forward in five-minute steps: official counts filling in after each
state's polls close (with the early vote leaning one way and the day vote the
other), Wikipedia editors calling races at realistic delays, a wrong call that
is taken down three minutes later, another that stands for ten minutes before
it is withdrawn, and a stale pre-election mark. Each step goes through the real
results.build(), exactly as the collector would, and writes the same files.

    python3 tools/simulate_night.py --until 2026-11-04T04:00Z   # write the state at 11 pm ET
    python3 tools/simulate_night.py --check                      # play the whole night and verify it

Run it in a scratch copy of the repo: it writes data/results_2026.json.
"""
import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import results as R            # noqa: E402
import results_feeds as F      # noqa: E402

OFFICIAL_STATES = set(F.OFFICIAL)
START = datetime(2026, 11, 3, 22, 30, tzinfo=timezone.utc)
END = datetime(2026, 11, 4, 14, 0, tzinfo=timezone.utc)


def wiki_rows(race, d_name, r_name):
    return [{"name": d_name, "party": "O" if race.get("independent_side") else "D",
             "party_raw": "Independent" if race.get("independent_side") else "Democratic",
             "pct": None, "winner": False},
            {"name": r_name, "party": "R", "party_raw": "Republican", "pct": None, "winner": False}]


def draw_outcome(races, seed):
    rng = random.Random(seed)
    nat = rng.gauss(0, 3.0)
    states = {r["state"] for r in races.values()}
    st_err = {s: rng.gauss(0, 2.4) for s in sorted(states)}
    truth = {}
    for r in sorted(races.values(), key=lambda r: r["id"]):
        m = r["forecast"]["margin"]
        if m is None:
            m = 30 if r["forecast"]["p"] > 0.5 else -30
        actual = m + (0.8 if r["chamber"] == "senate" else 1.0) * nat + st_err[r["state"]] + rng.gauss(0, 4.0)
        close = R.polls_close(r["state"])
        gap = abs(actual)
        if gap > 15:
            delay = timedelta(minutes=rng.uniform(0, 8))
        elif gap > 6:
            delay = timedelta(minutes=rng.uniform(20, 150))
        elif gap > 2:
            delay = timedelta(minutes=rng.uniform(90, 480))
        else:
            delay = timedelta(minutes=rng.uniform(240, 1400))   # some run past the night
        truth[r["id"]] = {"margin": actual, "dem_wins": actual > 0, "call_at": close + delay,
                          "early_skew": rng.choice([-7, 7]),
                          "total": rng.randint(2_500_000, 5_000_000) if r["chamber"] == "senate"
                          else rng.randint(220_000, 380_000),
                          "hours": 6 if r["state"] == "AK" else rng.uniform(2.5, 4.5)}
    return truth


def official_contests(races, truth, now):
    out = {}
    for r in races.values():
        if r["state"] not in OFFICIAL_STATES:
            continue
        t = truth[r["id"]]
        close = R.polls_close(r["state"]) - timedelta(minutes=30 if r["state"] != "AK" else 0)
        frac = max(0.0, min(1.0, (now - close).total_seconds() / (t["hours"] * 3600)))
        margin_now = t["margin"] + t["early_skew"] * (1 - frac) ** 2
        votes = int(t["total"] * frac)
        d = int(votes * (0.49 + margin_now / 200))
        rr = int(votes * (0.49 - margin_now / 200))
        out[r["id"]] = {"race_id": r["id"], "chamber": r["chamber"], "state": r["state"],
                        "candidates": [{"name": "Dem Candidate", "party": "O" if r.get("independent_side") else "D",
                                        "party_raw": "DEM", "votes": d},
                                       {"name": "Rep Candidate", "party": "R", "party_raw": "REP", "votes": rr},
                                       {"name": "Third Party", "party": "O", "party_raw": "LIB", "votes": votes - d - rr}],
                        "reporting": frac, "reporting_label": f"{int(frac * 1000)} of 1,000 precincts",
                        "as_of": R.iso(now), "source": r["state"].lower()}
        if r.get("independent_side"):
            out[r["id"]]["candidates"][0]["name"] = (r.get("dem_candidate") or "Ind").replace(" (I)", "")
    return out


def wiki_state(races, truth, now, injected):
    senate, house, gov = {}, {}, {}
    for r in races.values():
        t = truth[r["id"]]
        d_name = (r.get("dem_candidate") or "Dem Candidate").replace(" (I)", "")
        r_name = r.get("rep_candidate") or "Rep Candidate"
        rows = wiki_rows(r, d_name, r_name)
        if now >= t["call_at"]:
            rows[0 if t["dem_wins"] else 1]["winner"] = True
            if now >= t["call_at"] + timedelta(hours=2):
                rows[0]["pct"] = round(49 + t["margin"] / 2, 1)
                rows[1]["pct"] = round(49 - t["margin"] / 2, 1)
        for rid, (start, minutes, wrong) in injected.items():
            if rid == r["id"] and start <= now < start + timedelta(minutes=minutes) and now < t["call_at"]:
                rows[1 if wrong["dem_wins"] else 0]["winner"] = True
        if r["chamber"] == "senate":
            senate[r["state"]] = [rows]
        elif r["chamber"] == "governor":
            gov[r["state"]] = [rows]
        else:
            house[r["id"]] = rows
    return {"senate": {"rows": senate, "revid": 1}, "house": {"rows": house, "revid": 1},
            "governor": {"rows": gov, "revid": 1}}


def play(until=None, check=False, seed=2026, step_minutes=5):
    snap, markets, baselines, frozen = R.load_forecast()
    races = R.registry(snap, markets, baselines)
    truth = draw_outcome(races, seed)
    # Two deliberately wrong calls on races the night has not called yet: one
    # that is fixed in three minutes and must never be accepted, one that stands
    # for ten and must be accepted and then withdrawn.
    close_ones = sorted((r for r in races.values() if r["chamber"] == "senate"),
                        key=lambda r: abs(truth[r["id"]]["margin"]))
    flash, stuck = close_ones[0]["id"], close_ones[1]["id"]
    injected = {
        flash: (R.polls_close(races[flash]["state"]) + timedelta(minutes=30), 3, truth[flash]),
        stuck: (R.polls_close(races[stuck]["state"]) + timedelta(minutes=40), 10, truth[stuck]),
    }
    state = {}
    now = START
    stop = until or END
    sources = {k: {"label": v[0], "url": v[1], "ok": True, "error": None} for k, v in F.SOURCE_NAMES.items()}
    all_log = []
    payload = None
    while now <= stop:
        state["official"] = official_contests(races, truth, now)
        state["wiki"] = wiki_state(races, truth, now, injected)
        payload, log = R.build(state, now, snap, markets, baselines, frozen, sources)
        all_log.extend(log)
        now += timedelta(minutes=step_minutes)
    R.save(state, payload, [])
    with open(R.LOG, "w") as fh:
        for line in all_log:
            fh.write(json.dumps(line) + "\n")
    print(f"simulated to {payload['updated']}: {R.summary_line(payload)}")
    if not check:
        return 0

    problems = []
    for rid, r in payload["races"].items():
        call = r.get("call")
        if call and call["dem_side"] != truth[rid]["dem_wins"]:
            problems.append(f"{rid}: called for the wrong side")
    calls = [x for x in all_log if x["type"] == "call"]
    retractions = [x for x in all_log if x["type"] == "retraction"]
    if any(x["race"] == flash and x["dem_side"] != truth[flash]["dem_wins"] for x in calls):
        problems.append(f"{flash}: a three-minute wrong call was accepted")
    if not any(x["race"] == stuck for x in retractions):
        problems.append(f"{stuck}: the ten-minute wrong call was never withdrawn")
    early = [x for x in calls if R.parse_time(x["t"]) < R.polls_close(races[x["race"]]["state"])]
    if early:
        problems.append(f"{len(early)} call(s) accepted before the polls closed")
    sc = payload["scorecard"]
    print(f"  calls {len(calls)}, retractions {len(retractions)}, uncalled at end "
          f"{payload['tally']['senate']['uncalled'] + payload['tally']['house']['uncalled']}")
    print(f"  Brier model {sc['brier']['model']} vs ratings {sc['brier']['rating']}; misses {sc['n_misses']}; "
          f"margins senate {sc['margins']['senate'] and sc['margins']['senate']['mae']} "
          f"house {sc['margins']['house'] and sc['margins']['house']['mae']}")
    print("  control:", {k: v["outcome"] for k, v in sc["control"].items()})
    for p in problems:
        print("  PROBLEM:", p)
    return 1 if problems else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--until")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args()
    until = datetime.fromisoformat(a.until.replace("Z", "+00:00")) if a.until else None
    sys.exit(play(until, a.check, a.seed))


if __name__ == "__main__":
    main()
