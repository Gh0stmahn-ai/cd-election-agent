#!/usr/bin/env python3
"""
State polling for the competitive governor races. Free, no key.

Exactly the method refresh_senate_polls.py uses -- the poll aggregation table
on each race's Wikipedia article where there is one, otherwise the individual
head-to-head polls weighted by recency and sample size -- applied to the
"2026 <State> gubernatorial election" articles. The model blends the result
with the rating baseline in the same way, so a thinly polled race keeps
leaning on its rating.

Writes data/governor_polls_2026.json.   Run it directly:  python3 refresh_governor_polls.py
"""
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

import refresh_senate_polls as S

DATA = Path(__file__).parent / "data"
RATINGS = ("tossup", "lean", "likely")


def main():
    gov = json.loads((DATA / "governor_races_2026.json").read_text())
    races = [r for r in gov["races"] if r["rating"] in RATINGS]
    today = date.today()
    out, issues = {}, []
    print(f"Reading state polls for {len(races)} competitive governor races...")
    for race in races:
        rid = race["race_id"]
        dem, rep = race.get("dem_candidate") or "", race.get("rep_candidate") or ""
        if not dem or not rep or "," in dem or "," in rep:
            issues.append(f"{rid}: no single Democrat-against-Republican pairing to poll")
            print(f"  {rid}: no single pairing")
            continue
        title = f"2026 {S.STATE_NAMES.get(race['state'], race['state'])} gubernatorial election"
        try:
            page = S.fetch(title)
        except Exception as e:  # noqa: BLE001
            issues.append(f"{rid}: {e}")
            page = None
        time.sleep(S.PAUSE)
        if not page:
            issues.append(f"{rid}: no article found")
            kept = S.carry_forward(rid, dem, rep, path=DATA / "governor_polls_2026.json")
            if kept:
                out[rid] = kept
                print(f"  {rid}: no article read; kept the last average")
            else:
                print(f"  {rid}: no article")
            continue
        tables = S.find_tables(page, dem.split()[-1], rep.split()[-1])
        agg = [t for t in tables if t["aggregation"]]
        polls = [t for t in tables if not t["aggregation"]]
        margin, used, method = None, [], None
        if agg:
            entries = S.read_rows(agg[0])
            named = [e for e in entries if not e["label"].lower().startswith("average")]
            avg_row = next((e for e in entries if e["label"].lower().startswith("average")), None)
            if avg_row:
                margin, method = avg_row["dem"] - avg_row["rep"], "aggregator average"
            elif named:
                margin = sum(e["dem"] - e["rep"] for e in named) / len(named)
                method = f"mean of {len(named)} aggregators"
            used = [{"label": e["label"], "margin": round(e["dem"] - e["rep"], 1)} for e in named]
        if margin is None and polls:
            entries = []
            for t in polls:
                entries.extend(S.read_rows(t))
            margin, used = S.average(entries, today)
            method = f"{len(used)} polls, recency and sample weighted" if used else None
        if margin is None:
            issues.append(f"{rid}: no usable head-to-head polling")
            print(f"  {rid}: no usable polling")
            continue
        out[rid] = {"state": race["state"], "dem_candidate": dem, "rep_candidate": rep,
                    "dem_margin": round(margin, 1), "method": method, "n_used": len(used),
                    "detail": used[:12], "rating": race["rating"], "rating_lean": race["lean"],
                    "read_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        print(f"  {rid}: {'D' if margin >= 0 else 'R'}+{abs(margin):.1f} ({method})")
    if not out:
        print("No governor polling could be read; leaving any previous file alone.", file=sys.stderr)
        return 1
    payload = {"as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
               "source": "Wikipedia per-race election articles",
               "half_life_days": S.HALF_LIFE_DAYS, "max_age_days": S.MAX_AGE_DAYS,
               "races": out, "issues": issues}
    (DATA / "governor_polls_2026.json").write_text(json.dumps(payload, indent=1))
    print(f"\n{len(out)} of {len(races)} races have a polling average.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
