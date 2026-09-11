#!/usr/bin/env python3
"""
Regenerate dashboard.html's embedded iteration history from iterations/*.json.

dashboard.html is a single self-contained file with the iteration history
embedded as a JS literal (`const ITERATIONS = [...]`) so it works offline
without a server. Every time run_pipeline.py adds a new snapshot to
iterations/, run this to fold it into the dashboard.

Run: python3 build_dashboard.py
"""
import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).parent
ITER_DIR = BASE_DIR / "iterations"
DASHBOARD_PATH = BASE_DIR / "dashboard.html"


def load_iterations():
    snapshots = []
    for path in ITER_DIR.glob("*.json"):
        if path.name == "latest.json":
            continue
        snapshots.append(json.loads(path.read_text()))
    snapshots.sort(key=lambda s: s["timestamp"])
    return snapshots


def main():
    snapshots = load_iterations()
    if not snapshots:
        print("No iterations found in iterations/ - nothing to embed. Run run_pipeline.py first.")
        return

    html = DASHBOARD_PATH.read_text()
    new_line = "const ITERATIONS = " + json.dumps(snapshots, separators=(",", ":")) + ";"

    pattern = re.compile(r"const ITERATIONS = \[.*?\];", re.DOTALL)
    if not pattern.search(html):
        raise SystemExit("Could not find 'const ITERATIONS = [...];' in dashboard.html - template changed?")

    html = pattern.sub(new_line, html, count=1)
    DASHBOARD_PATH.write_text(html)
    print(f"Embedded {len(snapshots)} iteration(s) into dashboard.html "
          f"(latest: {snapshots[-1]['timestamp']})")


if __name__ == "__main__":
    main()
