#!/usr/bin/env python3
"""
Build the public website (site/) from the latest forecast snapshots.

One page per section, sharing assets/style.css and assets/app.js:
  index.html        Overview: control odds for both chambers
  senate.html       Senate map, races to watch, every race
  house.html        Hexagon map of all 435 districts, race tables
  economy.html      What voters are feeling, and how it feeds the model
  trend.html        How the forecast has moved, run by run
  methodology.html  Where every number comes from, and how it is used

Each page embeds only the data it needs, so there are no fetches, no CDN
and no libraries: the site works from a web server or straight off disk.

Run: python3 build_site.py
"""
import html
import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
ITER_DIR = BASE / "iterations"
DATA_DIR = BASE / "data"
GEO_DIR = BASE / "geo"
TEMPLATE_DIR = BASE / "site_template"
OUT = BASE / "site"
SITE_URL = "https://gh0stmahn-ai.github.io"
# The Refresh control in the header points here: GitHub's own "Run workflow"
# page for the daily pipeline. One click there re-runs the forecast on the
# latest public data, with optional boxes for today's poll numbers.
RUN_URL = "https://github.com/Gh0stmahn-ai/cd-election-agent/actions/workflows/daily-forecast.yml"
ELECTION_DATE = date(2026, 11, 3)

NAV = [
    ("index.html", "Overview"),
    ("senate.html", "Senate"),
    ("house.html", "House"),
    ("economy.html", "Economy"),
    ("trend.html", "Trend"),
    ("methodology.html", "Methodology"),
]
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect"
           " width='32' height='32' rx='7' fill='%23f6f5f1'/%3E%3Crect x='5' y='8' width='10' height='18'"
           " rx='2' fill='%232a78d6'/%3E%3Crect x='17' y='8' width='10' height='18' rx='2' fill='%23c74845'/%3E%3C/svg%3E")


# ------------------------------------------------------------------ data
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
    data = json.loads((DATA_DIR / "house_districts_2026.json").read_text())
    return {d["id"]: {"state": d["state"], "held_by": d["held_by"], "incumbent": d.get("incumbent"),
                      "redrawn": d.get("redrawn_2026", False)} for d in data["districts"]}


def js(obj):
    return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")


# ----------------------------------------------------------------- shell
def page(slug, title, description, head_line, body, data, scripts):
    nav = "".join(
        '<a href="%s"%s>%s</a>' % (href, ' aria-current="page"' if href == slug else "", label)
        for href, label in NAV)
    url = f"{SITE_URL}/" + ("" if slug == "index.html" else slug)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta property="og:type" content="website">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{SITE_URL}/assets/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{SITE_URL}/assets/og.png">
<link rel="icon" href="{FAVICON}">
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<header class="site-head"><div class="bar">
  <a class="brand plain" href="index.html"><span class="mark"><i></i><i></i></span>2026 Midterm Forecast</a>
  <nav class="main" aria-label="Sections">{nav}</nav>
  <a class="refresh" href="{RUN_URL}" target="_blank" rel="noopener"
     title="Re-run the forecast now on the latest public data (opens GitHub Actions)">
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7"
         stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M14 8a6 6 0 1 1-1.8-4.3"></path><path d="M14 2v4h-4"></path>
    </svg>Refresh</a>
</div></header>

<div class="wrap">
<div class="page-head">
  <h1>{head_line[0]}</h1>
  <div class="sub">{head_line[1]}</div>
</div>
{body}
{prev_next(slug)}
</div>

<footer class="site"><div class="wrap" style="padding-bottom:0">
  <div id="foot-updated"></div>
  <div style="margin-top:6px">Rebuilt every day at noon Eastern, and any time someone
    presses <a href="{RUN_URL}">Refresh</a>.
    <a href="https://github.com/Gh0stmahn-ai/cd-election-agent">Source code and data on GitHub</a>.
    Forecast, not a prediction: read the <a href="methodology.html">methodology</a> first.</div>
</div></footer>

<script src="assets/app.js"></script>
<script>
const PAGE_DATA = {data};
FC.init();
(function () {{
  var s = PAGE_DATA.site;
  var days = Math.max(0, Math.ceil((new Date("{ELECTION_DATE}T12:00:00Z") - new Date(s.timestamp)) / 86400000));
  var col = s.status === "success" ? "#0ca30c" : s.status === "partial" ? "#fab219" : "#d03b3b";
  FC.$("foot-updated").innerHTML = "Forecast run " + FC.fmtDate(s.timestamp, true) + " · " + days +
    " days to Election Day, Nov 3, 2026 · " +
    '<span class="pill"><span class="dot" style="background:' + col + '"></span>Data refresh: ' + s.status + "</span>";
}})();
{scripts}
</script>
</body>
</html>
"""


def prev_next(slug):
    idx = [h for h, _ in NAV].index(slug)
    left = NAV[idx - 1] if idx > 0 else None
    right = NAV[idx + 1] if idx < len(NAV) - 1 else None
    a = f'<a href="{left[0]}">&#8592; {left[1]}</a>' if left else "<span></span>"
    b = f'<a href="{right[0]}">{right[1]} &#8594;</a>' if right else "<span></span>"
    return f'<div class="prev-next">{a}{b}</div>'


def site_meta(snap):
    return {"timestamp": snap["timestamp"], "status": snap.get("status", {}).get("state", "success")}


# ----------------------------------------------------------------- pages
def build_index(snap, meta):
    s, h, e, j = snap["senate"], snap["house"], snap["environment"], snap["joint"]
    body = """
<section>
  <div class="grid2">
    <div class="card" id="hero-senate"></div>
    <div class="card" id="hero-house"></div>
  </div>
  <div class="joint" id="joint"></div>
</section>

<section>
  <h2>Every seat, most likely Democratic to most likely Republican</h2>
  <p class="lede">One dot per seat, colored by its chance of going Democratic. The line marks the seat that
    decides the majority. Hover any dot for the race behind it.</p>
  <div class="grid2">
    <div class="card">
      <div class="chamber-label">Senate · 100 seats</div>
      <svg id="hemi-senate" viewBox="0 0 520 300" role="img" aria-label="Senate seats"></svg>
      <div class="legend" id="legend-senate"></div>
      <p class="note">65 seats are not on the ballot (hollow dots). <a href="senate.html">Senate map and races &#8594;</a></p>
    </div>
    <div class="card">
      <div class="chamber-label">House · 435 seats</div>
      <svg id="hemi-house" viewBox="0 0 520 300" role="img" aria-label="House seats"></svg>
      <div class="legend" id="legend-house"></div>
      <p class="note">All 435 seats are on the ballot. <a href="house.html">House map and races &#8594;</a></p>
    </div>
  </div>
</section>

<section>
  <h2>What is driving it</h2>
  <div class="card" id="environment"></div>
  <p class="note">Full detail on the readings behind this: <a href="economy.html">what voters are feeling &#8594;</a></p>
</section>
"""
    data = {"site": site_meta(snap), "senate": s, "house": h, "environment": e, "joint": j, "meta": meta}
    scripts = """
FC.onRender(function () {
  FC.renderHero("hero-senate", "Senate", PAGE_DATA.senate, 51, 100,
    "<span>50-50 tie (Vance breaks it for the GOP) <b>" + FC.pct(PAGE_DATA.senate.tie_prob) + "</b></span>");
  FC.renderHero("hero-house", "House", PAGE_DATA.house, PAGE_DATA.house.majority, 435,
    "<span>National environment <b>" + FC.margin(PAGE_DATA.environment.dem_margin) + "</b></span>");
  FC.renderJoint("joint", PAGE_DATA.joint);
  FC.renderHemicycle("hemi-senate", FC.senateSeatDots(PAGE_DATA.senate), 51, 9.2, 5, "legend-senate",
    '<span class="ring" style="border-color:' + FC.css("--c6") + '"></span><span class="ring" style="border-color:' +
    FC.css("--c0") + '"></span> Not up in 2026');
  FC.renderHemicycle("hemi-house", FC.houseSeatDots(PAGE_DATA.house, PAGE_DATA.meta), PAGE_DATA.house.majority, 4.6, 12, "legend-house");
  FC.renderEnvironment("environment", PAGE_DATA.environment);
});
"""
    return page("index.html", "2026 Midterm Forecast",
                "Who controls the House and Senate after November 3, 2026. Every Senate race and all 435 House "
                "districts, simulated 20,000 times a day.",
                ("Who controls Congress after November 3",
                 "Every Senate race and all 435 House districts, simulated 20,000 times a day with polling, "
                 "race ratings, and the economy voters are living through."),
                body, js(data), scripts)


def build_senate(snap):
    body = """
<section>
  <div class="card">
    <svg id="map-senate" viewBox="0 0 980 600" role="img" aria-label="Map of 2026 Senate races"></svg>
    <div class="legend" id="legend-senate-map"></div>
  </div>
</section>

<section>
  <h2>Races to watch</h2>
  <p class="lede">Every contest the model gives both sides a real chance in, closest first.</p>
  <div class="watch-grid" id="watch-senate"></div>
</section>

<section>
  <div class="grid2">
    <div class="card">
      <div class="chamber-label">The chamber, seat by seat</div>
      <svg id="hemi-senate" viewBox="0 0 520 300" role="img" aria-label="Senate seats"></svg>
      <div class="legend" id="legend-senate"></div>
    </div>
    <div class="card">
      <div class="chamber-label">How many seats Democrats end up with</div>
      <svg id="hist-senate" viewBox="0 0 520 170" role="img" aria-label="Distribution of Democratic Senate seats"></svg>
      <p class="note" id="senate-facts"></p>
    </div>
  </div>
</section>

<section>
  <h2>All 35 races</h2>
  <div class="card tbl-scroll"><table id="tbl-senate"></table></div>
</section>
"""
    data = {"site": site_meta(snap), "senate": snap["senate"], "geo": {"states": {
        k: {"d": v["d"], "cx": v["cx"], "cy": v["cy"]}
        for k, v in json.loads((GEO_DIR / "states.json").read_text())["states"].items()}}}
    scripts = """
FC.onRender(function () {
  var s = PAGE_DATA.senate;
  FC.renderSenateMap("map-senate", s, PAGE_DATA.geo, "legend-senate-map");
  FC.renderWatch("watch-senate", s, 8);
  FC.renderHemicycle("hemi-senate", FC.senateSeatDots(s), 51, 9.2, 5, "legend-senate",
    '<span class="ring" style="border-color:' + FC.css("--c6") + '"></span><span class="ring" style="border-color:' +
    FC.css("--c0") + '"></span> Not up in 2026');
  FC.renderHistogram("hist-senate", s.histogram, 51, "Democratic Senate seats", "Senate");
  FC.$("senate-facts").innerHTML = "Democrats hold " + s.not_up.D + " seats that are not on the ballot and need 51 " +
    "for control, because Vice President Vance breaks a 50-50 tie. Chance of exactly 50-50: <b>" +
    FC.pct(s.tie_prob) + "</b>. Average outcome: <b>D " + Math.round(s.mean_dem_seats) + " · R " +
    Math.round(100 - s.mean_dem_seats) + "</b>.";
  FC.renderSenateTable("tbl-senate", s);
});
"""
    return page("senate.html", "Senate forecast · 2026 Midterm Forecast",
                "All 35 Senate races in 2026, each shaded by its chance of going Democratic, with nominees, "
                "ratings and the full seat distribution.",
                ("The Senate",
                 "35 seats are on the ballot, including special elections in Florida and Ohio. Democrats need 51 "
                 "seats for control, because the Vice President breaks a 50-50 tie."),
                body, js(data), scripts)


def build_house(snap, meta):
    body = """
<section>
  <div class="card">
    <svg id="map-house" role="img" aria-label="Hexagon map of all 435 House districts"></svg>
    <div class="legend" id="legend-house-map"></div>
    <p class="note">One hexagon per district, so a city seat and a rural seat take the same space, which is how they
      count toward 218. Each state's districts sit near its real location, with its most Democratic-leaning seats in
      the middle and its most Republican around the edge; exact positions inside a state are schematic. Ten states
      (TX, CA, FL, OH, NC, MO, UT, TN, LA, AL) are voting on new 2026 lines.</p>
  </div>
</section>

<section>
  <div class="grid2">
    <div class="card">
      <div class="chamber-label">The chamber, seat by seat</div>
      <svg id="hemi-house" viewBox="0 0 520 300" role="img" aria-label="House seats"></svg>
      <div class="legend" id="legend-house"></div>
    </div>
    <div class="card">
      <div class="chamber-label">How many seats Democrats end up with</div>
      <svg id="hist-house" viewBox="0 0 520 170" role="img" aria-label="Distribution of Democratic House seats"></svg>
      <p class="note" id="house-facts"></p>
    </div>
  </div>
</section>

<section>
  <h2>The races</h2>
  <p class="lede">Cook Political Report ratings, with this model's win probability for each seat.</p>
  <div class="card">
    <div class="tabs" id="house-tabs" role="group" aria-label="Filter House races"></div>
    <div class="tbl-scroll"><table id="tbl-house"></table></div>
  </div>
</section>
"""
    data = {"site": site_meta(snap), "house": snap["house"], "meta": meta,
            "hex": json.loads((GEO_DIR / "house_hex.json").read_text())}
    scripts = """
FC.onRender(function () {
  var h = PAGE_DATA.house;
  FC.renderHouseMap("map-house", h, PAGE_DATA.hex, PAGE_DATA.meta, "legend-house-map");
  FC.renderHemicycle("hemi-house", FC.houseSeatDots(h, PAGE_DATA.meta), h.majority, 4.6, 12, "legend-house");
  FC.renderHistogram("hist-house", h.histogram, h.majority, "Democratic House seats", "House");
  FC.$("house-facts").innerHTML = "Democrats need <b>" + h.majority + "</b> of 435. Average outcome: <b>D " +
    Math.round(h.mean_dem_seats) + " · R " + Math.round(435 - h.mean_dem_seats) + "</b>. 80% of simulations land " +
    "between <b>" + h.percentiles["10"] + "</b> and <b>" + h.percentiles["90"] + "</b> Democratic seats.";
  FC.renderHouseTable("house-tabs", "tbl-house", h, PAGE_DATA.meta);
});
"""
    return page("house.html", "House forecast · 2026 Midterm Forecast",
                "All 435 House districts on a hexagon map, each colored by its chance of going Democratic, with "
                "Cook ratings and win probabilities.",
                ("The House",
                 "All 435 seats are on the ballot. Democrats need 218 for the majority, against maps that ten "
                 "states redrew mid-decade."),
                body, js(data), scripts)


def build_economy(snap):
    body = """
<section>
  <div class="card" id="environment"></div>
</section>

<section>
  <h2>How people judge the president</h2>
  <div class="grid3" id="political"></div>
</section>

<section>
  <h2>What things cost, and what people earn</h2>
  <p class="lede">Each reading is scored from -1 (hurts the party in power) to +1 (helps it) against a neutral
    benchmark, then weighted into the economic index above. Oil is shown for context only: its effect reaches
    the model through gas prices and inflation, so counting it again would double count.</p>
  <div class="grid3" id="indicators"></div>
</section>
"""
    data = {"site": site_meta(snap), "environment": snap["environment"],
            "display": snap["fundamentals_display"]}
    scripts = """
FC.onRender(function () {
  FC.renderEnvironment("environment", PAGE_DATA.environment);
  FC.renderIndicators("indicators", "political", PAGE_DATA.display, PAGE_DATA.environment);
});
"""
    return page("economy.html", "What voters are feeling · 2026 Midterm Forecast",
                "Gas, groceries, wages, mortgage rates, the stock market and presidential approval, and exactly how "
                "each one moves the 2026 forecast.",
                ("What voters are feeling",
                 "Prices at the pump and the checkout, paychecks, borrowing costs, retirement accounts, and the "
                 "verdict on the president. These set the fundamentals half of the national environment."),
                body, js(data), scripts)


def build_trend(snaps):
    runs = [{"timestamp": s["timestamp"],
             "senate": s["senate"]["dem_control_prob"], "senate_seats": s["senate"]["mean_dem_seats"],
             "house": s["house"]["dem_control_prob"], "house_seats": s["house"]["mean_dem_seats"],
             "environment": s["environment"]["dem_margin"],
             "generic_ballot": s["environment"]["generic_ballot"],
             "status": s.get("status", {}).get("state", "success")} for s in snaps]
    body = """
<section>
  <div class="grid2">
    <div class="card"><div class="chamber-label">Chance Democrats win the Senate</div>
      <svg id="trend-senate" viewBox="0 0 520 220" role="img" aria-label="Senate control probability over time"></svg></div>
    <div class="card"><div class="chamber-label">Chance Democrats win the House</div>
      <svg id="trend-house" viewBox="0 0 520 220" role="img" aria-label="House control probability over time"></svg></div>
  </div>
  <p class="note" id="trend-note"></p>
</section>

<section>
  <h2>Every run</h2>
  <p class="lede">One row per daily run. The national environment is the blend of polling and fundamentals that
    shifts every race.</p>
  <div class="card tbl-scroll"><table id="tbl-runs"></table></div>
</section>
"""
    data = {"site": site_meta(snaps[-1]), "runs": runs}
    scripts = """
FC.onRender(function () {
  var runs = PAGE_DATA.runs;
  FC.renderTrend("trend-senate", runs, "senate", "senate_seats");
  FC.renderTrend("trend-house", runs, "house", "house_seats");
  FC.$("trend-note").textContent = runs.length > 1
    ? runs.length + " runs so far. Each point is one daily pipeline run; hover for the exact numbers."
    : "This is the first run of the current model. A point is added after every daily run, so the lines build up from here.";
  var rows = runs.slice().reverse();
  FC.$("tbl-runs").innerHTML = "<thead><tr><th>Run</th><th class='num'>Senate D</th><th class='num'>Avg D seats</th>" +
    "<th class='num'>House D</th><th class='num'>Avg D seats</th><th class='num'>Environment</th>" +
    "<th class='num'>Generic ballot</th><th>Data refresh</th></tr></thead><tbody>" +
    rows.map(function (r) {
      return "<tr><td>" + FC.fmtDate(r.timestamp, true) + "</td><td class='num'>" + FC.pct(r.senate) +
        "</td><td class='num'>" + r.senate_seats.toFixed(1) + "</td><td class='num'>" + FC.pct(r.house) +
        "</td><td class='num'>" + r.house_seats.toFixed(1) + "</td><td class='num'>" + FC.margin(r.environment) +
        "</td><td class='num'>" + FC.margin(r.generic_ballot) + "</td><td>" + r.status + "</td></tr>";
    }).join("") + "</tbody>";
});
"""
    return page("trend.html", "How the forecast has moved · 2026 Midterm Forecast",
                "The chance each party wins the House and Senate, run by run, with the national environment behind "
                "each update.",
                ("How the forecast has moved",
                 "Every daily run is kept, so the forecast can be replayed rather than quietly rewritten."),
                body, js(data), scripts)


def build_methodology(snap):
    fund = json.loads((DATA_DIR / "fundamentals_2026.json").read_text())
    gb = json.loads((DATA_DIR / "generic_ballot_2026.json").read_text())
    sen = json.loads((DATA_DIR / "senate_races_2026.json").read_text())
    hou = json.loads((DATA_DIR / "house_districts_2026.json").read_text())
    atmo = json.loads((DATA_DIR / "atmospherics_2026.json").read_text())
    env = snap["environment"]

    def cite(name, url=""):
        """A source is a link when there's a real URL behind it, plain text otherwise.

        Sources arrive in three shapes: {"name","url"} from the AI refresh and
        the economy pull, a bare URL string, and a bare outlet name typed by
        hand. Only the first two can be linked."""
        url = (url or "").strip()
        if not url and str(name).startswith(("http://", "https://")):
            url, name = name, str(name).split("/")[2]
        name = html.escape(str(name))
        if not url.startswith(("http://", "https://")):
            return name
        return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{name}</a>'

    def srcs(items):
        out = []
        for s in items:
            out.append(cite(s["name"], s.get("url")) if isinstance(s, dict) else cite(s))
        return " · ".join(out) or "n/a"

    rows = [("Generic congressional ballot", "Average of 2 to 3 public trackers, Democratic minus Republican",
             "Every day", gb.get("as_of"), srcs(gb.get("sources", [])))]
    approval = next(p for p in fund["political"] if p["id"] == "approval")
    rows.append(("Presidential approval", "Average of public approval aggregates, net approval feeds the model",
                 "Every day", approval.get("as_of"), srcs(approval.get("sources", []))))
    for ind in fund["indicators"]:
        w = ind.get("score", {}).get("weight", 0)
        rows.append((ind["label"],
                     f"{ind.get('unit', '')}, weight {int(w * 100)}% of the economic index" if w
                     else "Context only, not scored",
                     "Daily where the source updates daily, otherwise at each release",
                     ind.get("as_of"), srcs(ind.get("sources", []))))
    rows.append(("Senate race ratings", f"{len(sen['races'])} races, Cook Political Report",
                 "Checked daily, changed only on a cited rating change", sen.get("as_of"),
                 '<a href="https://www.cookpolitical.com/ratings/senate-race-ratings" target="_blank" rel="noopener">cookpolitical.com</a>'))
    rows.append(("House race ratings", f"{len(hou['districts'])} districts, Cook Political Report",
                 "Checked daily, changed only on a cited rating change", hou.get("as_of"),
                 '<a href="https://www.cookpolitical.com/ratings/house-race-ratings" target="_blank" rel="noopener">cookpolitical.com</a>'))
    n_atmo = len(atmo.get("assessments", []))
    rows.append(("News momentum (Senate)", f"{n_atmo} toss-up and lean races, capped at +/-0.08 win probability",
                 "Every day", atmo.get("as_of"), "Cited in each race's entry in data/atmospherics_2026.json"))
    rows.append(("State and district geometry", "Natural Earth 1:50m state shapes; hexagon layout generated once",
                 "Fixed", "2026-09-11",
                 '<a href="https://www.naturalearthdata.com/" target="_blank" rel="noopener">naturalearthdata.com</a> (public domain)'))
    table = "".join(f"<tr><td><b>{r[0]}</b></td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td><td>{r[4]}</td></tr>"
                    for r in rows)

    score_rows = ""
    comps = {c["id"]: c for c in env["fundamentals"]["economic_components"]}
    for ind in fund["indicators"]:
        sc = ind.get("score", {})
        if not sc.get("weight"):
            continue
        c = comps.get(ind["id"], {})
        basis = "change vs a year ago" if sc.get("method") == "yoy_pct" else (
            "year-to-date %" if sc.get("field") == "compare_value" else "level")
        score_rows += (f"<tr><td>{ind['label']}</td><td>{ind['display']}</td><td>{basis}</td>"
                       f"<td class='num'>{sc['neutral']}</td><td class='num'>{int(sc['weight'] * 100)}%</td>"
                       f"<td class='num'>{c.get('score', 0):+.2f}</td>"
                       f"<td class='num'>{-c.get('contribution', 0) * 3:+.2f}</td></tr>")

    body = f"""
<section class="prose">
  <h2>Where the numbers come from</h2>
  <p>A scheduled job runs at noon Eastern, collects current numbers from public sources, passes every value
  through a validation layer, re-runs the model, and rebuilds and republishes this site. Pressing
  <a href="{RUN_URL}">Refresh</a> runs the same chain on demand. The steps below are the whole of it.</p>

  <div class="step"><div class="n">1</div><div>
    <h3>Collect the economy, from the agencies that publish it</h3>
    <p>All ten economic readings are pulled straight from the official series: consumer prices, wages, payrolls
    and unemployment from the Bureau of Labor Statistics, GDP from the Bureau of Economic Analysis, gasoline and
    oil from the Energy Information Administration, yields from the Treasury, the mortgage rate from Freddie Mac,
    consumer sentiment from the University of Michigan. Each is read from the St. Louis Fed's FRED mirror, which
    serves them as plain machine-readable files. Derived figures are computed here rather than copied from
    anywhere: inflation is the year-over-year change in the CPI index, real wage growth is earnings growth minus
    that, the year-to-date return is measured from the previous year's closing level. Each reading carries the
    date the agency published it, shown on the <a href="economy.html">economy page</a>, and a refresh never
    replaces a newer figure with an older one.</p>
  </div></div>

  <div class="step"><div class="n">2</div><div>
    <h3>Collect the politics, which no one publishes as data</h3>
    <p>Polling averages, rating changes and campaign news are not data series; they live on pages that have to be
    read. Two things can do that. An AI refresh, one Claude conversation with web search, is given the current
    contents of every data file and told to average two or three public trackers rather than lean on a single
    poll, to change a race rating only with a cited rating change from a named forecaster, and to run a search
    before any value it writes; its search budget and turn count are capped, so a confused run stops instead of
    looping. Failing that, the generic ballot and approval can be typed into the run form by hand. Either way the
    numbers land through the same validator, and the economy above refreshes regardless.</p>
  </div></div>

  <div class="step"><div class="n">3</div><div>
    <h3>Write through a validator, never directly</h3>
    <p>Nothing writes to a data file directly, not the economy pull, not the AI refresh, not a number typed by
    hand. Every value goes through one of the same small set of functions, each of which checks what it is given:
    ratings must be one of solid, likely, lean or toss-up; district and race ids must already exist; the generic
    ballot must fall between D+30 and R+30; the news-momentum nudge is clamped to +/-0.08; and every economic
    reading has a plausible range (gas $1.50 to $8.00, unemployment 2% to 15%, and so on) so a misread page cannot
    inject a nonsense number. Sources are required alongside each value, and they are what you see cited on the
    economy page. A rejected write comes back to the agent as an error it has to fix rather than silently landing.</p>
  </div></div>

  <div class="step"><div class="n">4</div><div>
    <h3>Turn readings into a national environment</h3>
    <p>Polls and fundamentals are blended: today the generic ballot carries
    {int(env['poll_weight'] * 100)}% and the fundamentals estimate {int((1 - env['poll_weight']) * 100)}%, with the
    poll share rising toward 95% by Election Day. The fundamentals estimate is the historical midterm penalty for
    the president's party, plus 0.2 points of margin for each point of net approval, plus up to 3 points from the
    economic index below.</p>
  </div></div>

  <div class="step"><div class="n">5</div><div>
    <h3>Simulate every race 20,000 times</h3>
    <p>Each race starts from its published rating, converted to an expected margin (Solid 18 points, Likely 9,
    Lean 4.5, Toss-up 0.5 toward the party that holds it). Ratings already reflect the environment when they were
    set, so a race is shifted only by how far the environment has moved since. Then 20,000 elections are simulated.
    Each one draws a single national polling miss shared by every race in both chambers, plus race-level noise of 5
    points in the House and 5.5 in the Senate. That shared miss is why the two chambers move together and why the
    seat ranges are wide rather than falsely precise.</p>
  </div></div>

  <div class="step"><div class="n">6</div><div>
    <h3>Publish, and keep the receipts</h3>
    <p>Every run is saved as a timestamped snapshot with a status block that flags any input that has gone stale,
    so a quiet data failure shows up instead of old numbers being served as fresh. Past runs are never overwritten,
    which is what the <a href="trend.html">trend page</a> replays. The whole repository, including every data file
    and every past run, is public.</p>
  </div></div>
</section>

<section>
  <h2>Every input, and where it comes from</h2>
  <div class="card tbl-scroll"><table>
    <thead><tr><th>Input</th><th>What it is</th><th>Refresh</th><th>As of</th><th>Sources</th></tr></thead>
    <tbody>{table}</tbody>
  </table></div>
</section>

<section>
  <h2>The economic index, line by line</h2>
  <p class="lede">Each reading is compared with a neutral benchmark, clipped to the range -1 to +1, and weighted.
  A negative score hurts the party in power. The last column is what that reading is currently worth, in points of
  Democratic margin.</p>
  <div class="card tbl-scroll"><table>
    <thead><tr><th>Reading</th><th>Current</th><th>Scored on</th><th class="num">Neutral</th><th class="num">Weight</th>
      <th class="num">Score</th><th class="num">Points of margin</th></tr></thead>
    <tbody>{score_rows}</tbody>
  </table></div>
</section>

<section class="prose">
  <h2>What this does not do</h2>
  <ul>
    <li><b>It does not poll districts.</b> District-level polling for 435 seats is not public, so the House forecast
      rests on published ratings plus the national environment, not on local surveys.</li>
    <li><b>It does not draw 2026 district lines.</b> No public source publishes boundaries for the ten states that
      redrew mid-decade, so the House map is a hexagon cartogram. Where a district sits inside its state is
      schematic, and Florida's district numbers for safe seats under its May 2026 map are best-available.</li>
    <li><b>It does not treat the AI as an oracle.</b> The agent chooses which published figures to copy and writes a
      capped, cited judgment about campaign momentum in Senate races. It cannot invent a rating change, move a
      number outside its plausible range, or touch the model's math.</li>
    <li><b>It is not a prediction.</b> A 70% chance still loses 3 times in 10, and the shared national error exists
      precisely so the forecast is not more confident than the evidence.</li>
    <li><b>It cannot guarantee fresh data.</b> Sources go down and releases slip. When an input ages, the run is
      marked partial and the reason appears in the footer of every page.</li>
  </ul>
</section>
"""
    return page("methodology.html", "Methodology · 2026 Midterm Forecast",
                "How the forecast collects data: the daily agent, the validation layer, every source, the economic "
                "scoring, the simulation, and the honest limits.",
                ("Data collection and methodology",
                 "What is collected, from where, how often, what is checked before it is written, and how it turns "
                 "into a probability."),
                body, js({"site": site_meta(snap)}), "")


def main():
    snaps = load_snapshots()
    if not snaps:
        raise SystemExit("No model-v2 snapshots in iterations/ - run run_pipeline.py first.")
    latest, meta = snaps[-1], house_meta()

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    shutil.copytree(TEMPLATE_DIR / "assets", OUT / "assets")
    shutil.copy(BASE / "assets" / "og.png", OUT / "assets" / "og.png")
    (OUT / ".nojekyll").write_text("")

    pages = {
        "index.html": build_index(latest, meta),
        "senate.html": build_senate(latest),
        "house.html": build_house(latest, meta),
        "economy.html": build_economy(latest),
        "trend.html": build_trend(snaps),
        "methodology.html": build_methodology(latest),
    }
    for name, html in pages.items():
        (OUT / name).write_text(html, encoding="utf-8")

    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"site/: {len(pages)} pages, {len(snaps)} run(s) embedded, {total / 1024:.0f} KB total")
    for name in pages:
        print(f"  {name:18} {(OUT / name).stat().st_size / 1024:6.0f} KB")


if __name__ == "__main__":
    main()
