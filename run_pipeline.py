"""
Orchestrates one full pipeline iteration:
  data/*.json (refreshed by agent_run.py)
  -> model.run_simulation (national environment + 35 Senate races + 435 House
     districts, 20,000 correlated Monte Carlo draws)
  -> iterations/<timestamp>.json (+ latest.json), which build_dashboard.py
     folds into dashboard.html.

Each snapshot carries a status block (success / partial / failed + issues)
so a stale or broken data refresh is visible instead of silently serving old
numbers as fresh.
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import attribution
import model

ET = ZoneInfo("America/New_York")

ITER_DIR = Path(__file__).parent / "iterations"
ITER_DIR.mkdir(exist_ok=True)

STALE_DAYS = 3


def _age_days(iso):
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
    return (datetime.now(timezone.utc).date() - d).days


def et_day(timestamp):
    """The Eastern-time date a run belongs to, which is how the site reads."""
    return datetime.fromisoformat(timestamp).astimezone(ET).date()


def previous_days_snapshot(today_et):
    """The last snapshot from an earlier day, for the day-over-day story.

    Comparing against the previous RUN would split one day's news across
    however many times the workflow happened to fire. Comparing against the
    last run of the previous day gives one clean story per day.
    """
    best = None
    for path in sorted(ITER_DIR.glob("2026-*.json")):
        try:
            snap = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if snap.get("schema_version") != model.MODEL_VERSION or "environment" not in snap:
            continue
        if et_day(snap["timestamp"]) < today_et:
            if best is None or snap["timestamp"] > best["timestamp"]:
                best = snap
    return best


def run_iteration(seed=None):
    status = {"state": "success", "issues": []}

    try:
        result = model.run_simulation(seed=seed) if seed is not None else model.run_simulation()
    except Exception as e:  # noqa: BLE001 - we want the message in the snapshot
        status = {"state": "failed", "issues": [f"simulation error: {e}"]}
        raise

    fund = model.load_fundamentals()
    gb = model.load_generic_ballot()

    # Freshness checks: daily inputs should be days old, not weeks.
    for label, as_of in (("generic ballot", gb.get("as_of")), ("fundamentals", fund.get("as_of"))):
        age = _age_days(as_of)
        if age is not None and age > STALE_DAYS:
            status["state"] = "partial"
            status["issues"].append(f"{label} data is {age} days old")
    for ind in fund["indicators"]:
        age = _age_days(ind.get("as_of", ""))
        # monthly releases (CPI, jobs, sentiment) legitimately lag ~6 weeks
        if age is not None and age > 45:
            status["issues"].append(f"{ind['label']} last updated {ind['as_of']}")

    snapshot = {
        "schema_version": model.MODEL_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        **result,
        "fundamentals_display": {
            "as_of": fund.get("as_of"),
            "indicators": [
                {k: ind.get(k) for k in ("id", "label", "category", "display", "as_of", "compare_display", "compare_label",
                                          "change_display", "context", "why", "sources", "value")}
                for ind in fund["indicators"]
            ],
            "political": fund["political"],
        },
    }

    # Why it moved, measured by re-running the model with one input held back
    # at a time. Computed here rather than at build time so the explanation is
    # fixed to the day it describes and can never be rewritten later.
    previous = previous_days_snapshot(et_day(snapshot["timestamp"]))
    if previous is not None:
        try:
            snapshot["change"] = attribution.compare(previous, snapshot)
        except Exception as e:  # noqa: BLE001 - a missing explanation is not a failed forecast
            status["issues"].append(f"could not attribute the change: {e}")
            print(f"  ! attribution skipped: {e}")

    out_path = ITER_DIR / f"{snapshot['timestamp'].replace(':', '-')}.json"
    out_path.write_text(json.dumps(snapshot, indent=1))
    (ITER_DIR / "latest.json").write_text(json.dumps(snapshot, indent=1))

    s, h, e = snapshot["senate"], snapshot["house"], snapshot["environment"]
    print(f"Iteration saved: {out_path.name} [status: {status['state']}]")
    for issue in status["issues"]:
        print(f"  ! {issue}")
    print(f"National environment: D{e['dem_margin']:+.1f}")
    print(f"Senate Dem control probability: {s['dem_control_prob'] * 100:.1f}% (mean {s['mean_dem_seats']:.1f} seats)")
    print(f"House Dem control probability: {h['dem_control_prob'] * 100:.1f}% (mean {h['mean_dem_seats']:.1f} seats)")
    change = snapshot.get("change")
    if change:
        print(f"Since {et_day(change['from'])}: senate {change['total']['senate']:+.1f} pt, "
              f"house {change['total']['house']:+.1f} pt")
        for driver in change["drivers"][:4]:
            print(f"    {driver['label']}: senate {driver['points']['senate']:+.1f}, "
                  f"house {driver['points']['house']:+.1f}")
    return snapshot


if __name__ == "__main__":
    run_iteration()
