"""
Orchestrates one full pipeline iteration:
  ingest (assumed already refreshed, or run as-is on seed data)
  -> model (Senate Monte Carlo + House national swing model)
  -> snapshot (timestamped JSON the dashboard reads to build its
     iteration history / slider)

Run this on a schedule (cron, GitHub Action, etc.) after ingest.py has
refreshed the data files, to build up the iteration history the map
dashboard visualizes.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from model import run_simulation, house_national_model, load_senate_races

ITER_DIR = Path(__file__).parent / "iterations"
ITER_DIR.mkdir(exist_ok=True)


def run_iteration():
    status = {"state": "success", "issues": []}

    try:
        senate = run_simulation(n_sims=20000)
    except Exception as e:
        status = {"state": "failed", "issues": [f"simulation error: {e}"]}
        raise

    try:
        house = house_national_model()
    except Exception as e:
        status["state"] = "partial"
        status["issues"].append(f"house model error: {e}")
        house = {"dem_house_majority_prob": None, "method": "unavailable this run"}

    races = load_senate_races()["races"]

    # Flag if the underlying data looks stale (helps catch a silently
    # broken ingestion step rather than serving old numbers as fresh)
    from datetime import date
    gb_age_days = (date.today() - date.fromisoformat(load_senate_races()["as_of"])).days
    if gb_age_days > 3:
        status["state"] = "partial" if status["state"] == "success" else status["state"]
        status["issues"].append(f"senate ratings data is {gb_age_days} days old")

    seat_detail = []
    for r in races:
        seat_detail.append(
            {
                "seat_id": r["seat_id"],
                "state": r["state"],
                "held_by": r["held_by"],
                "rating": r["rating"],
                "dem_win_prob": round(senate["seat_dem_win_prob"][r["seat_id"]], 3),
            }
        )

    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "senate": {
            "dem_control_prob": round(senate["dem_senate_control_prob"], 3),
            "mean_dem_seats": round(senate["mean_dem_seats"], 1),
            "seats": seat_detail,
        },
        "house": house,
    }

    out_path = ITER_DIR / f"{snapshot['timestamp'].replace(':', '-')}.json"
    out_path.write_text(json.dumps(snapshot, indent=2))

    # Also write/refresh a "latest.json" for easy dashboard loading
    (ITER_DIR / "latest.json").write_text(json.dumps(snapshot, indent=2))

    print(f"Iteration saved: {out_path.name} [status: {status['state']}]")
    if status["issues"]:
        for issue in status["issues"]:
            print(f"  ! {issue}")
    print(f"Senate Dem control probability: {snapshot['senate']['dem_control_prob'] * 100:.1f}%")
    print(f"House Dem majority probability: {snapshot['house']['dem_house_majority_prob'] * 100:.1f}%")
    return snapshot


if __name__ == "__main__":
    run_iteration()
