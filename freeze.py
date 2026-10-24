#!/usr/bin/env python3
"""
The final forecast, sealed on the morning of Election Day.

A forecast is only worth grading if nobody could have touched it after the
first vote was counted. So the first pipeline run at or after FREEZE_AT
(5 am Eastern on November 3, before any poll opens) seals what it produced into
data/final_forecast.json, and from then on every run refuses to model again:
the daily workflow stops, run_pipeline.py exits, and the site says plainly
that the numbers on it are the final ones.

What is sealed is everything the scorecard will need and could not rebuild
later without trusting live files: the whole snapshot, the market prices at
the same moment, the rating-only probability for every race (the "what if you
had just read Cook" baseline), and a fingerprint of every input file.

    python3 freeze.py check   # prints live / final-run / late / frozen
    python3 freeze.py seal    # writes data/final_forecast.json from the latest run

FREEZE_AT can be overridden through the environment for testing. To undo a
freeze -- which should never be needed -- delete data/final_forecast.json.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
DATA = BASE / "data"
FINAL = DATA / "final_forecast.json"
LATEST = BASE / "iterations" / "latest.json"

# 10:00 UTC on November 3 is 5:00 am Eastern Standard Time (daylight saving
# ends on November 1). The earliest polls open at 6 am local time in the East.
FREEZE_AT = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)
# 11:00 UTC is 6 am Eastern, when the first polls open. No run that STARTS
# after this re-models: if the morning runs both failed, the seal takes the
# newest forecast that was made before it instead.
SEAL_DEADLINE = datetime(2026, 11, 3, 11, 0, tzinfo=timezone.utc)


def freeze_at():
    override = os.environ.get("FREEZE_AT")
    if override:
        return datetime.fromisoformat(override.replace("Z", "+00:00"))
    return FREEZE_AT


def now():
    override = os.environ.get("FREEZE_NOW")   # testing only
    if override:
        return datetime.fromisoformat(override.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)


def is_frozen():
    return FINAL.exists()


def seal_deadline():
    return freeze_at() + (SEAL_DEADLINE - FREEZE_AT)


def state():
    """live: model as usual. final-run: model once more, then seal.
    late: the polls have opened and nothing is sealed -- seal the last forecast
    made before they did, without modelling. frozen: do nothing."""
    if is_frozen():
        return "frozen"
    t = now()
    if t >= seal_deadline():
        return "late"
    if t >= freeze_at():
        return "final-run"
    return "live"


def last_before_deadline():
    """The newest stored forecast made before the polls opened."""
    best = None
    for path in sorted((BASE / "iterations").glob("2026-*.json")):
        try:
            snap = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        ts = datetime.fromisoformat(snap.get("timestamp", ""))
        if snap.get("schema_version", 1) >= 2 and ts < seal_deadline():
            if best is None or snap["timestamp"] > best["timestamp"]:
                best = snap
    return best


def load_final():
    try:
        return json.loads(FINAL.read_text())
    except (OSError, ValueError):
        return None


def _fingerprints():
    out = {}
    for path in sorted(DATA.glob("*.json")):
        if path.name == FINAL.name:
            continue
        out[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return out


def _market_record(snap):
    """Market prices at the moment of the freeze, in the shape the scorecard reads."""
    try:
        markets = json.loads((DATA / "markets_2026.json").read_text())
    except (OSError, ValueError):
        return None
    venues = markets.get("venues", {})
    kalshi, poly = venues.get("kalshi", {}), venues.get("polymarket", {})
    seats = {s["seat_id"]: s for s in snap["senate"]["seats"]}
    races = {}
    for seat_id, m in (poly.get("races") or {}).items():
        seat = seats.get(seat_id)
        if not seat:
            continue
        parties = m.get("parties", {})
        side = "I" if seat.get("challenger_caucus") == "independent" and "I" in parties else "D"
        prob = parties.get(side, {}).get("prob")
        if prob is not None:
            races[seat_id] = round(prob, 4)

    def prob(venue, key):
        entry = venue.get(key) or {}
        return entry.get("prob")

    return {
        "as_of": markets.get("as_of"),
        "senate_dem": {"kalshi": prob(kalshi, "senate_dem"), "polymarket": prob(poly, "senate_dem")},
        "house_dem": {"kalshi": prob(kalshi, "house_dem"), "polymarket": prob(poly, "house_dem")},
        "senate_races": races,
        "governor_races": {rid: m["dem"] for rid, m in (kalshi.get("governors") or {}).items()
                           if m.get("dem") is not None},
    }


def _house_rating_probs(snap):
    """What each House rating alone implies, with the same spread the model uses.

    This is the honest baseline: a reader who took Cook's rating, moved it by
    the national environment, and stopped. If the model cannot beat this, all
    the polling and the partisan index added nothing.
    """
    import model  # imported here so `check` stays dependency-free
    hou = model.load_house_districts()
    env = snap["environment"]
    shift = env["dem_margin"] - hou.get("ratings_environment_dem_margin", env["dem_margin"])
    days = env.get("days_to_election")
    out = {}
    for d in hou["districts"]:
        margin = model._baseline("house", d["rating"], d["lean"]) + shift
        out[d["id"]] = model._implied_prob(margin, "house", d["rating"], days)
    return out


def seal():
    if is_frozen():
        print("  already frozen; leaving data/final_forecast.json as it is")
        return 0
    mode = state()
    snap = last_before_deadline() if mode == "late" else json.loads(LATEST.read_text())
    if snap is None:
        print("  ! no forecast made before the polls opened; nothing to seal", file=sys.stderr)
        return 1
    record = {
        "frozen_at": now().isoformat(timespec="seconds"),
        "seal_mode": "late" if mode == "late" else "on time",
        "forecast_timestamp": snap["timestamp"],
        "rule": f"First forecast run at or after {freeze_at().isoformat()} (5 am Eastern on "
                "Election Day); if none ran before the polls opened, the newest forecast made before "
                "they did. Nothing after this point changes the forecast.",
        "commit": os.environ.get("GITHUB_SHA"),
        "forecast": snap,
        "markets": _market_record(snap),
        "baselines": {
            "senate_rating_probs": {s["seat_id"]: s.get("rating_prob") for s in snap["senate"]["seats"]},
            "house_rating_probs": _house_rating_probs(snap),
            "governor_rating_probs": {g["race_id"]: g.get("rating_prob")
                                      for g in (snap.get("governors") or {}).get("races", [])},
        },
        "inputs": _fingerprints(),
    }
    FINAL.write_text(json.dumps(record, indent=1))
    s, h = snap["senate"], snap["house"]
    print(f"  FROZEN: Senate D {s['dem_control_prob']:.1%}, House D {h['dem_control_prob']:.1%} "
          f"(run {snap['timestamp']})")
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "check":
        st = state()
        print(st)
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as fh:
                fh.write(f"state={st}\n")
        return 0
    if cmd == "seal":
        return seal()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
