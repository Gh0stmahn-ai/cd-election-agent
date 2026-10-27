"""The national environment on the day a set of race ratings was published.

The model moves every rating by how far the national environment has moved
since the rating was made (model.py: E - ratings_environment_dem_margin). So
when a fetcher reads ratings that Cook published on, say, October 20, the
reference has to be the environment as this model saw it on October 20, not
today's: otherwise a week of national movement is either counted twice or
not at all. Each daily run is saved in iterations/, so the answer is the
newest run made on or before that day.
"""
import json
import re
from datetime import date, datetime
from pathlib import Path

ITER = Path(__file__).resolve().parent.parent / "iterations"
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def rating_date(header_cell):
    """'{{small|Oct 20,<br />2026}}' or 'Oct. 23,<br>2026' -> date(2026, 10, 20)."""
    text = re.sub(r"<[^>]+>|\{\{\s*small\s*\||\}\}", " ", header_cell or "", flags=re.I)
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", text)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))


def environment_on(day):
    """The environment (Democratic margin) of the newest run on or before `day`,
    or None if no run that old is kept."""
    best = None
    for path in ITER.glob("20*.json"):
        try:
            stamp = datetime.fromisoformat(path.stem[:10]).date()
        except ValueError:
            continue
        if day is None or stamp <= day:
            if best is None or path.name > best.name:
                best = path
    if best is None:
        return None
    try:
        return json.loads(best.read_text())["environment"]["dem_margin"]
    except (OSError, ValueError, KeyError):
        return None


def wiki_text(title, tries=6):
    """An article's wikitext, waiting out Wikipedia's rate limit (HTTP 429)
    rather than failing the daily run over it."""
    import time
    import urllib.error
    import results_feeds as F
    for attempt in range(tries):
        try:
            return F.wiki_content(title)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == tries - 1:
                raise
            wait = int(e.headers.get("retry-after") or 20)
            time.sleep(min(max(wait, 5), 60) + 2)
