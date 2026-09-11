#!/usr/bin/env python3
"""
Build dashboard.html from dashboard_template.html + every forecast snapshot.

The output is ONE self-contained file: the forecast history, the
pre-projected state shapes (geo/states.json) and the House hex layout
(geo/house_hex.json) are embedded as JSON, and all charts and maps are drawn
with plain SVG + vanilla JS. No CDN, no fetch(), no libraries, so it renders
the same whether it's opened from disk, emailed, or served from GitHub Pages.

Only model-v2 snapshots are embedded (iterations written before the
forecast upgrade on Sept 11, 2026 used a different model and stay on disk
for the record but are not charted).

Run: python3 build_dashboard.py
"""
import json
from pathlib import Path

BASE = Path(__file__).parent
ITER_DIR = BASE / "iterations"
TEMPLATE = BASE / "dashboard_template.html"
OUT = BASE / "dashboard.html"
PLACEHOLDER = "/*__DATA__*/null"


def load_snapshots():
    snaps = []
    for path in sorted(ITER_DIR.glob("*.json")):
        if path.name == "latest.json":
            continue
        s = json.loads(path.read_text())
        if s.get("schema_version", 1) >= 2:
            snaps.append(s)
    snaps.sort(key=lambda s: s["timestamp"])
    return snaps


def house_meta():
    data = json.loads((BASE / "data" / "house_districts_2026.json").read_text())
    return [{"id": d["id"], "state": d["state"], "held_by": d["held_by"],
             "incumbent": d.get("incumbent"), "redrawn": d.get("redrawn_2026", False)}
            for d in data["districts"]]


def main():
    snaps = load_snapshots()
    if not snaps:
        raise SystemExit("No model-v2 snapshots in iterations/ - run run_pipeline.py first.")
    states = json.loads((BASE / "geo" / "states.json").read_text())
    states["states"] = {k: {"d": v["d"], "cx": v["cx"], "cy": v["cy"]} for k, v in states["states"].items()}
    payload = {
        "snapshots": snaps,
        "geo_states": states,
        "house_hex": json.loads((BASE / "geo" / "house_hex.json").read_text()),
        "house_meta": house_meta(),
    }
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise SystemExit(f"{PLACEHOLDER} not found in {TEMPLATE.name}")
    OUT.write_text(html.replace(PLACEHOLDER, blob, 1), encoding="utf-8")
    print(f"dashboard.html: {len(snaps)} snapshot(s) embedded, latest {snaps[-1]['timestamp']},"
          f" {OUT.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
