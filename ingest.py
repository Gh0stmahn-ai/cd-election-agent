"""
Ingestion: the deterministic write functions the daily agent calls.

agent_run.py runs a Claude conversation with web search; every change it
makes to data/ goes through one of these functions (never a raw file edit),
so each write is validated, capped, and logged.

Free/public sources the agent is pointed at:
  - Generic ballot: RealClearPolling, Silver Bulletin, other public averages
  - Race ratings: Cook Political Report (public print views), plus rating
    changes reported in free news coverage
  - Economy/markets: BLS (CPI, jobs), BEA (GDP), Freddie Mac (mortgage
    rates), AAA (gas), University of Michigan (sentiment), market closes
  - Approval: public polling averages

Respect robots.txt and each site's terms of use; nothing here bypasses
paywalls or authentication.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
RATINGS = ("solid", "likely", "lean", "tossup")

# Plausibility bounds so a misread page can't inject a nonsense number.
INDICATOR_BOUNDS = {
    "gas_price": (1.5, 8.0), "oil_wti": (20, 250), "cpi_inflation": (-3, 15),
    "consumer_sentiment": (30, 120), "real_wages": (-10, 10), "unemployment": (2, 15),
    "gdp_growth": (-15, 15), "mortgage_rate": (2, 12), "treasury_10y": (0, 10),
    "sp500": (2000, 20000),
}


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _read(name):
    return json.loads((DATA_DIR / name).read_text())


def _write(name, payload):
    (DATA_DIR / name).write_text(json.dumps(payload, indent=1))


def refresh_generic_ballot(new_margin: float, source_notes: str, sources: list[str]):
    """Overwrite the generic-ballot average (Dem minus Rep, points)."""
    if not -30 <= float(new_margin) <= 30:
        raise ValueError("generic ballot margin out of range")
    payload = {"as_of": _today(), "dem_margin_points": round(float(new_margin), 2),
               "note": source_notes, "sources": sources}
    _write("generic_ballot_2026.json", payload)
    return payload


def refresh_senate_rating(seat_id: str, new_rating: str, note: str, lean: str | None = None):
    """Change one Senate race's rating (and optionally which party it leans to)."""
    if new_rating not in RATINGS:
        raise ValueError(f"rating must be one of {RATINGS}")
    payload = _read("senate_races_2026.json")
    for race in payload["races"]:
        if race["seat_id"] == seat_id:
            race["rating"] = new_rating
            if lean in ("D", "R"):
                race["lean"] = lean
            elif new_rating == "tossup":
                race["lean"] = race["held_by"]
            race["notes"] = note
            break
    else:
        raise ValueError(f"seat_id {seat_id} not found")
    payload["as_of"] = _today()
    _write("senate_races_2026.json", payload)
    return {"seat_id": seat_id, "rating": new_rating, "lean": race["lean"]}


def refresh_house_rating(district_id: str, new_rating: str, lean: str, note: str):
    """Change one House district's rating, e.g. ('PA-7', 'lean', 'D', 'Cook moved ...')."""
    if new_rating not in RATINGS:
        raise ValueError(f"rating must be one of {RATINGS}")
    if lean not in ("D", "R"):
        raise ValueError("lean must be D or R")
    payload = _read("house_districts_2026.json")
    for d in payload["districts"]:
        if d["id"] == district_id:
            d["rating"] = new_rating
            d["lean"] = lean
            d["note"] = note
            break
    else:
        raise ValueError(f"district {district_id} not found (format like 'PA-7' or 'AK-AL')")
    payload["as_of"] = _today()
    _write("house_districts_2026.json", payload)
    return {"id": district_id, "rating": new_rating, "lean": lean}


def refresh_fundamental(indicator_id: str, value: float, display: str, as_of: str,
                        compare_value: float | None = None, compare_display: str | None = None,
                        change_display: str | None = None, context: str | None = None,
                        sources: list[dict] | None = None):
    """Update one economic/market indicator in data/fundamentals_2026.json."""
    lo, hi = INDICATOR_BOUNDS.get(indicator_id, (float("-inf"), float("inf")))
    if not lo <= float(value) <= hi:
        raise ValueError(f"{indicator_id}={value} is outside the plausible range {lo}..{hi}")
    payload = _read("fundamentals_2026.json")
    for ind in payload["indicators"]:
        if ind["id"] == indicator_id:
            ind["value"] = float(value)
            ind["display"] = display
            ind["as_of"] = as_of
            if compare_value is not None:
                ind["compare_value"] = float(compare_value)
            for key, val in (("compare_display", compare_display), ("change_display", change_display),
                             ("context", context)):
                if val:
                    ind[key] = val
            if sources:
                ind["sources"] = sources
            break
    else:
        ids = [i["id"] for i in payload["indicators"]]
        raise ValueError(f"unknown indicator {indicator_id}; valid ids: {ids}")
    payload["as_of"] = _today()
    _write("fundamentals_2026.json", payload)
    return {"id": indicator_id, "value": value, "display": display}


def refresh_approval(approve: float, disapprove: float, net: float, as_of: str,
                     context: str, sources: list[dict]):
    """Update the presidential approval average used in the fundamentals prior."""
    if not -80 <= float(net) <= 80:
        raise ValueError("net approval out of range")
    payload = _read("fundamentals_2026.json")
    for p in payload["political"]:
        if p["id"] == "approval":
            p.update({"approve": float(approve), "disapprove": float(disapprove),
                      "value_net": round(float(net), 1), "as_of": as_of, "context": context,
                      "display": f"{round(approve)}% / {round(disapprove)}%", "sources": sources})
            break
    payload["as_of"] = _today()
    _write("fundamentals_2026.json", payload)
    return {"approve": approve, "disapprove": disapprove, "net": net}


if __name__ == "__main__":
    print("This module defines the update functions the daily agent calls.")
    print("Run run_pipeline.py to execute model -> snapshot, then build_dashboard.py.")
