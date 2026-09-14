#!/usr/bin/env python3
"""
News tone for the competitive Senate races. Free, no key, no LLM.

GDELT monitors world news and scores every article for tone. Its DOC 2.0
API returns a daily tone timeline for any query, which is enough to ask a
narrow question: has coverage of this candidate turned sharply more
negative than its OWN recent norm, and is there suddenly much more of it?

Why relative and not absolute. Lexicon tone on political news is close to
useless taken at face value: "slams", "attacks", "blasts" and "fight" all
read negative regardless of who benefits, and a candidate buried in hostile
coverage is often the one gaining, because the coverage is about their
opponent. Measuring each candidate against their own 30-day baseline
cancels most of that vocabulary bias, since the same words keep appearing
either way. What survives is a change in direction.

IMPORTANT: nothing here is fed into the forecast. The script records what a
capped adjustment WOULD be, so the signal can be watched against real
results before anyone lets it move a number. See data/news_2026.json.

Run it directly:  python3 refresh_news.py
"""
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).parent / "data"
API = "https://api.gdeltproject.org/api/v2/doc/doc"
UA = {"User-Agent": "cd-election-agent/1.0 (https://github.com/Gh0stmahn-ai/cd-election-agent)"}

TIMESPAN = "30d"
RECENT_DAYS = 3          # the window treated as "now"
MIN_BASELINE_DAYS = 10   # below this there is not enough history to compare against
PAUSE = 6.0              # GDELT asks for one request every 5 seconds
RETRIES = 2
TIMEOUT = 45
# GDELT throttles by IP, and a shared runner can be blocked outright. If the
# first few lookups all fail there is no point grinding through twenty more:
# give up quickly and say so, rather than burning workflow minutes.
GIVE_UP_AFTER = 3

# If the signal were ever switched on, this is the scale it would use: each
# point of relative tone swing is worth this much win probability, capped at
# the same +/-0.08 the AI news read already uses.
PROB_PER_TONE_POINT = 0.02
CAP = 0.08


def get(params):
    """One GDELT call, with backoff. Returns parsed JSON or raises."""
    url = API + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read().decode("utf-8", "replace")
            if body.lstrip().startswith("{"):
                return json.loads(body)
            # GDELT signals rate limiting with a plain-text body and a 200 or 429.
            last = body.strip()[:120]
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace').strip()[:120]}"
        except Exception as e:  # noqa: BLE001
            last = str(e)
        if attempt + 1 < RETRIES:
            time.sleep(PAUSE * (attempt + 2))
    raise RuntimeError(last or "no response")


def tone_series(name):
    """Daily average tone for one candidate over TIMESPAN."""
    data = get({"query": f'"{name}" sourcecountry:US sourcelang:english',
                "mode": "timelinetone", "timespan": TIMESPAN, "format": "json"})
    points = []
    for series in data.get("timeline", []):
        for p in series.get("data", []):
            if p.get("date") and p.get("value") is not None:
                points.append((str(p["date"]), float(p["value"])))
        if points:
            break
    return points


def split(points):
    """(recent mean, baseline mean, n_baseline) from a daily series."""
    if len(points) < MIN_BASELINE_DAYS + RECENT_DAYS:
        return None, None, len(points)
    values = [v for _, v in points]
    recent = values[-RECENT_DAYS:]
    baseline = values[:-RECENT_DAYS]
    return (round(statistics.mean(recent), 3),
            round(statistics.mean(baseline), 3), len(baseline))


def competitive_races():
    races = json.loads((DATA / "senate_races_2026.json").read_text())["races"]
    return [r for r in races if r["rating"] in ("tossup", "lean")]


def main():
    races, out, issues = competitive_races(), {}, []
    print(f"Reading news tone for {len(races)} competitive Senate races "
          f"(about {len(races) * 2 * PAUSE / 60:.0f} min, GDELT allows one call every 5s).")

    cache = {}

    def tone_for(name):
        if name not in cache:
            cache[name] = tone_series(name)
            time.sleep(PAUSE)
        return cache[name]

    consecutive_failures = 0
    for race in races:
        if consecutive_failures >= GIVE_UP_AFTER:
            issues.append(f"stopped after {consecutive_failures} consecutive failures; "
                          "GDELT is refusing this runner's requests")
            print(f"  Stopping: {consecutive_failures} lookups in a row failed.", file=sys.stderr)
            break
        seat, entry = race["seat_id"], {}
        for side, key in (("D", "dem_candidate"), ("R", "rep_candidate")):
            name = (race.get(key) or "").replace("(I)", "").strip()
            if not name:
                continue
            try:
                points = tone_for(name)
            except Exception as e:  # noqa: BLE001 - record and carry on
                issues.append(f"{seat} {side} ({name}): {e}")
                consecutive_failures += 1
                continue
            consecutive_failures = 0
            recent, baseline, n = split(points)
            entry[side] = {"candidate": name, "days": len(points),
                           "recent_tone": recent, "baseline_tone": baseline,
                           "shift": None if recent is None else round(recent - baseline, 3)}
        if "D" in entry and "R" in entry and entry["D"]["shift"] is not None \
                and entry["R"]["shift"] is not None:
            swing = entry["D"]["shift"] - entry["R"]["shift"]
            entry["swing_to_dem"] = round(swing, 3)
            entry["would_be_adjustment"] = round(
                max(-CAP, min(CAP, swing * PROB_PER_TONE_POINT)), 4)
        out[seat] = entry
        got = [s for s in ("D", "R") if s in entry and entry[s]["shift"] is not None]
        print(f"  {seat}: " + (f"swing {entry['swing_to_dem']:+.2f} tone points to the Democrat"
                               if "swing_to_dem" in entry
                               else f"not enough history ({'/'.join(got) or 'no data'})"))

    usable = sum(1 for v in out.values() if "swing_to_dem" in v)
    payload = {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "GDELT DOC 2.0 timelinetone",
        "method": (f"Mean tone over the last {RECENT_DAYS} days minus the mean over the "
                   f"preceding baseline, per candidate; the race figure is the Democratic "
                   f"candidate's shift minus the Republican's."),
        "applied_to_forecast": False,
        "note": ("Collected and displayed only. Tone measures how coverage reads, not who it "
                 "helps, so this is shown next to the forecast and never inside it."),
        "cap": CAP, "prob_per_tone_point": PROB_PER_TONE_POINT,
        "races": out, "issues": issues,
    }
    if not usable:
        print("No usable tone series came back; leaving any previous file in place.",
              file=sys.stderr)
        for line in issues[:3]:
            print(f"  ! {line}", file=sys.stderr)
        return 1
    (DATA / "news_2026.json").write_text(json.dumps(payload, indent=1))
    print(f"\n{usable} of {len(races)} races have a usable tone signal.")
    if issues:
        print(f"({len(issues)} lookup(s) failed; recorded in the file.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
