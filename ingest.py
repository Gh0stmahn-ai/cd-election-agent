"""
Ingestion agent.

This is the piece that should actually run as a Claude-driven agent loop
in production: on a schedule, it searches for fresh generic ballot
numbers and race-rating changes, parses them, and writes updated JSON
into data/. This script is the deterministic scaffolding around that
loop - the parsing step (marked TODO) is where an LLM call with web
search belongs, because poll releases and rating-change pages don't
follow a fixed format.

Free/public sources used:
  - RealClearPolling generic ballot page (public, no login)
  - Silver Bulletin generic ballot page (public post, no login)
  - Cook Political Report rating CHANGES are announced in free news
    coverage (AP, The Hill, etc.) even though the underlying rating
    grid itself is subscriber-only - so rating changes should be
    ingested from news search, not scraped directly from Cook.

Respect robots.txt and each site's terms of use. This script fetches
pages you'd view in a browser anyway; it does not bypass paywalls or
authentication.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def refresh_generic_ballot(new_margin: float, source_notes: str, sources: list[str]):
    """
    Update data/generic_ballot_2026.json with a freshly computed
    generic-ballot average.

    In the full agent loop, `new_margin` is computed by:
      1. Web-searching "generic congressional ballot" + current month/year
      2. Fetching 2-3 tracker pages (RealClearPolling, Silver Bulletin,
         a pollster aggregator)
      3. Averaging the reported topline Dem-Rep margins
    That step needs an LLM in the loop because tracker pages report
    numbers in inconsistent formats - it's intentionally not automated
    as a fixed regex here.
    """
    path = DATA_DIR / "generic_ballot_2026.json"
    payload = {
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "dem_margin_points": new_margin,
        "note": source_notes,
        "sources": sources,
    }
    path.write_text(json.dumps(payload, indent=2))
    return payload


def refresh_senate_rating(seat_id: str, new_rating: str, note: str):
    """
    Update a single Senate race's rating in data/senate_races_2026.json.
    Called when the ingestion agent finds news of a rating change
    (e.g. "Cook moves Iowa Senate race to Toss-up").
    """
    path = DATA_DIR / "senate_races_2026.json"
    payload = json.loads(path.read_text())
    for race in payload["races"]:
        if race["seat_id"] == seat_id:
            race["rating"] = new_rating
            race["notes"] = note
            break
    else:
        raise ValueError(f"seat_id {seat_id} not found")
    payload["as_of"] = datetime.now(timezone.utc).date().isoformat()
    path.write_text(json.dumps(payload, indent=2))
    return payload


if __name__ == "__main__":
    # Manual demonstration run - replace with the live search+parse loop.
    print("This script defines the update functions the agent loop calls.")
    print("Run run_pipeline.py to execute ingest -> model -> snapshot.")
