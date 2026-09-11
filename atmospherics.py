"""
Atmospherics assessment.

This is the piece that genuinely needs an LLM in the loop, not a fixed
script - "read this pile of news coverage and campaign-trail reporting,
judge whether momentum favors the Dem or the GOP candidate, and by how
much" is a language-understanding task, not something you can regex.

How this runs as a real Claude agent (see README "Actioning this in
Claude" for the full walkthrough):

  for each tossup/lean race in senate_races_2026.json:
      1. web_search("<candidate A> vs <candidate B> Senate poll news")
      2. web_search("<state> Senate race momentum September 2026")
      3. Claude reads the results and produces a structured judgment:
         - adjustment_dem: float in [-0.08, 0.08]
         - rationale: 2-3 sentences, citing what it read
         - sources: the URLs it actually used
      4. Write the result into data/atmospherics_2026.json via
         update_atmospherics() below

This module defines the STORAGE contract (the schema, the cap, how it
merges) so that whichever runner executes step 1-3 - a scheduled Claude
Cowork task, a Claude Agent SDK script, a manual chat session - writes
into the same place model.py already reads from.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
ATMOSPHERICS_PATH = DATA_DIR / "atmospherics_2026.json"

ADJUSTMENT_CAP = 0.08


def update_atmospherics(seat_id: str, adjustment_dem: float, rationale: str, sources: list[str]):
    """
    Write or overwrite one race's atmospherics assessment.
    Called once per tossup/lean race, each time the daily agent runs.
    """
    adjustment_dem = max(-ADJUSTMENT_CAP, min(ADJUSTMENT_CAP, adjustment_dem))

    payload = json.loads(ATMOSPHERICS_PATH.read_text()) if ATMOSPHERICS_PATH.exists() else {
        "as_of": None, "method": "", "assessments": [], "not_yet_assessed": []
    }

    assessments = [a for a in payload["assessments"] if a["seat_id"] != seat_id]
    assessments.append(
        {
            "seat_id": seat_id,
            "adjustment_dem": round(adjustment_dem, 3),
            "rationale": rationale,
            "sources": sources,
        }
    )
    payload["assessments"] = assessments
    payload["as_of"] = datetime.now(timezone.utc).date().isoformat()
    payload["not_yet_assessed"] = [
        s for s in payload.get("not_yet_assessed", []) if s != seat_id
    ]
    ATMOSPHERICS_PATH.write_text(json.dumps(payload, indent=2))
    return payload


def races_needing_assessment(max_age_days: int = 1):
    """
    Return the list of tossup/lean seat_ids that need a fresh atmospherics
    read today - i.e. every tossup/lean race, since this is meant to run
    daily and news moves fast during a campaign.
    """
    from model import load_senate_races

    races = load_senate_races()["races"]
    return [r["seat_id"] for r in races if r["rating"] in ("tossup", "lean")]


if __name__ == "__main__":
    print("Races needing a fresh atmospherics read today:")
    for seat_id in races_needing_assessment():
        print(" -", seat_id)
