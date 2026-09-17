#!/usr/bin/env python3
"""
Build the public website (site/) from the latest forecast snapshots.

One page per section, sharing assets/style.css and assets/app.js:
  index.html        Overview: control odds for both chambers
  senate.html       Senate map, races to watch, every race
  house.html        Hexagon map of all 435 districts, race tables
  economy.html      What voters are feeling, and how it feeds the model
  trend.html        How the forecast has moved, run by run
  scenarios.html    What the forecast would say under a different environment
  methodology.html  Where every number comes from, and how it is used

Each page embeds only the data it needs, so there are no fetches, no CDN
and no libraries: the site works from a web server or straight off disk.

Run: python3 build_site.py
"""
import hashlib
import html
import json
import statistics
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
ET = ZoneInfo("America/New_York")
ELECTION_DATE = date(2026, 11, 3)

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming",
}

NAV = [
    ("index.html", "Overview"),
    ("senate.html", "Senate"),
    ("house.html", "House"),
    ("scenarios.html", "Scenarios"),
    ("economy.html", "Economy"),
    ("trend.html", "Trend"),
    ("markets.html", "Markets"),
    ("methodology.html", "Methodology"),
]
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect"
           " width='32' height='32' rx='7' fill='%23f6f5f1'/%3E%3Crect x='5' y='8' width='10' height='18'"
           " rx='2' fill='%232a78d6'/%3E%3Crect x='17' y='8' width='10' height='18' rx='2' fill='%23c74845'/%3E%3C/svg%3E")


# A short hash of the shared assets, appended to their URLs. Without it a
# browser holding yesterday's app.js will happily pair it with today's HTML,
# and the moment a page calls a function that only exists in the new file the
# whole page stops rendering. The pages and their assets have to ship as a set.
def _asset_version():
    h = hashlib.sha256()
    for name in ("app.js", "style.css"):
        path = TEMPLATE_DIR / "assets" / name
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:8]


ASSET_V = _asset_version()


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
<link rel="stylesheet" href="assets/style.css?v={ASSET_V}">
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

<script src="assets/app.js?v={ASSET_V}"></script>
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
def market_strip(snap, markets):
    """One line on the overview: does real money agree with the model today?

    Returns (html, data) so the overview simply omits the section when no
    prices have been collected yet.
    """
    venues = (markets or {}).get("venues", {})
    if not venues:
        return "", None
    rows = []
    for chamber, key in (("Senate", "senate_dem"), ("House", "house_dem")):
        prices = [v[key]["prob"] for v in venues.values() if v.get(key)]
        if not prices:
            continue
        rows.append({"chamber": chamber,
                     "model": round(snap[chamber.lower()]["dem_control_prob"], 4),
                     "low": round(min(prices), 4), "high": round(max(prices), 4)})
    if not rows:
        return "", None
    html = """
<section>
  <h2>Does the money agree?</h2>
  <p class="lede">The same two questions, priced by traders on Kalshi and Polymarket. Shown
    for comparison only: market prices are never blended into this forecast.</p>
  <div class="card" id="market-strip"></div>
  <p class="note">Where the two disagree, and why they are kept apart:
    <a href="markets.html">betting markets &#8594;</a></p>
</section>
"""
    return html, rows


def build_index(snap, meta, markets=None):
    s, h, e, j = snap["senate"], snap["house"], snap["environment"], snap["joint"]
    market_html, market_rows = market_strip(snap, markets)
    change = snap.get("change")
    body = """
<section>
  <div class="grid2">
    <div class="card" id="hero-senate"></div>
    <div class="card" id="hero-house"></div>
  </div>
  <div class="joint" id="joint"></div>
  <p class="note" id="what-moved"></p>
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
      <p class="note" id="tip-senate-line"></p>
      <p class="note">65 seats are not on the ballot (hollow dots). <a href="senate.html">Senate map and races &#8594;</a></p>
    </div>
    <div class="card">
      <div class="chamber-label">House · 435 seats</div>
      <svg id="hemi-house" viewBox="0 0 520 300" role="img" aria-label="House seats"></svg>
      <div class="legend" id="legend-house"></div>
      <p class="note" id="tip-house-line"></p>
      <p class="note">All 435 seats are on the ballot. <a href="house.html">House map and races &#8594;</a></p>
    </div>
  </div>
</section>

<section>
  <h2>What is driving it</h2>
  <div class="card" id="environment"></div>
  <p class="note">Full detail on the readings behind this: <a href="economy.html">what voters are feeling &#8594;</a></p>
</section>
""" + market_html
    data = {"site": site_meta(snap), "senate": s, "house": h, "environment": e, "joint": j, "meta": meta}
    if market_rows:
        data["marketStrip"] = market_rows
    if change:
        data["change"] = {"total": change["total"], "from": change["from"],
                          "drivers": change["drivers"][:1]}
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
  var tipLine = function (id, items, say, withSub) {
    var box = FC.$(id); if (!box || !items.length) return;
    var it = items[0];
    box.innerHTML = say(FC.esc(it.label) + (withSub && it.sub ? " (" + FC.esc(it.sub) + ")" : ""),
      FC.pct1(it.prob));
  };
  tipLine("tip-senate-line", FC.senateTipping(PAGE_DATA.senate, 1), function (name, p) {
    return "Control runs through <b>" + name + "</b> more often than anywhere else: it is the race that " +
      "delivers the 51st seat in " + p + " of simulations.";
  });
  tipLine("tip-house-line", FC.houseTipping(PAGE_DATA.house, PAGE_DATA.meta, 1), function (name, p) {
    return "No single district carries the House the way one state can carry the Senate. The likeliest " +
      "decider, <b>" + name + "</b>, delivers the " + PAGE_DATA.house.majority + "th seat in " + p +
      " of simulations.";
  }, true);
  FC.renderEnvironment("environment", PAGE_DATA.environment);
  if (PAGE_DATA.marketStrip) { FC.renderMarketStrip("market-strip", PAGE_DATA.marketStrip); }
  if (PAGE_DATA.change) {
    var c = PAGE_DATA.change, top = c.drivers[0];
    var say = function (k, name) {
      var v = c.total[k];
      return name + " " + (Math.abs(v) < 0.2 ? "barely moved" :
        (v > 0 ? "up " : "down ") + Math.abs(v).toFixed(1) + " pt");
    };
    FC.$("what-moved").innerHTML = "Since " + FC.fmtDate(c.from) + ": " +
      say("house", "House") + ", " + say("senate", "Senate") +
      (top ? ", mostly " + FC.esc(top.label.toLowerCase()) : "") +
      '. <a href="trend.html">What moved it, day by day &#8594;</a>';
  }
});
"""
    return page("index.html", "2026 Midterm Forecast",
                "Who controls the House and Senate after November 3, 2026. Every Senate race and all 435 House "
                "districts, simulated 100,000 times a day.",
                ("Who controls Congress after November 3",
                 "Every Senate race and all 435 House districts, simulated 100,000 times a day with polling, "
                 "race ratings, and the economy voters are living through."),
                body, js(data), scripts)


ATTENTION_SECTION = """
<section>
  <h2>Who people are looking up</h2>
  <p class="lede">How each race's two candidates split Wikipedia readership over the past week,
    and whether either one is being read about far more than usual. This is curiosity, not
    support: a candidate in trouble gets looked up too. It is shown here and is
    <b>not part of the forecast</b>.</p>
  <div class="card" id="attention"></div>
  <p class="note" id="attention-note"></p>
</section>
"""


def market_probs(markets, seats):
    """Polymarket's Democratic price per Senate race, keyed by seat.

    Nebraska's Democratic-caucusing candidate runs as an independent, so the
    comparable price there is the independent's rather than the Democrat's.
    """
    poly = (markets or {}).get("venues", {}).get("polymarket", {})
    by_id = {s["seat_id"]: s for s in seats}
    out = {}
    for seat_id, m in (poly.get("races") or {}).items():
        seat = by_id.get(seat_id)
        if seat is None:
            continue
        parties = m.get("parties", {})
        side = "I" if seat.get("challenger_caucus") == "independent" and "I" in parties else "D"
        prob = parties.get(side, {}).get("prob")
        if prob is not None:
            out[seat_id] = round(prob, 4)
    return out


def race_history(snaps, limit=30):
    """Each Senate race's odds across the runs stored so far.

    One number per race per run is cheap; the point is a shape, not a series
    anyone reads off. Capped so a year of daily runs does not end up inside
    every page.
    """
    recent = snaps[-limit:]
    if len(recent) < 3:
        return None
    series = {}
    for snap in recent:
        for seat in snap["senate"]["seats"]:
            series.setdefault(seat["seat_id"], []).append(round(seat["dem_win_prob"], 4))
    n = len(recent)
    series = {k: v for k, v in series.items() if len(v) == n}
    if not series:
        return None
    return {"from": recent[0]["timestamp"], "series": series}


def build_senate(snap, attention=None, markets=None, snaps=None):
    rows = attention_rows(attention, snap)
    body = """
<section>
  <div id="ribbon-senate"></div>
</section>

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
  <h2>Where control is actually decided</h2>
  <p class="lede">Not which race is closest, but which one the majority turns on.</p>
  <div class="card">
    <div id="tip-senate"></div>
    <p class="tipfoot" id="tip-senate-note"></p>
  </div>
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
""" + (ATTENTION_SECTION if rows else "")
    senate = json.loads(json.dumps(snap["senate"]))
    prices = market_probs(markets, senate["seats"])
    for seat in senate["seats"]:
        seat["market_prob"] = prices.get(seat["seat_id"])
    data = {"attention": rows, "site": site_meta(snap), "senate": senate,
            "history": race_history(snaps or []), "change": snap.get("change"), "geo": {"states": {
        k: {"d": v["d"], "cx": v["cx"], "cy": v["cy"]}
        for k, v in json.loads((GEO_DIR / "states.json").read_text())["states"].items()}}}
    scripts = """
FC.onRender(function () {
  var s = PAGE_DATA.senate;
  FC.renderSenateMap("map-senate", s, PAGE_DATA.geo, "legend-senate-map");
  FC.renderWatch("watch-senate", s, 8, PAGE_DATA.history);
  FC.renderRibbon("ribbon-senate", PAGE_DATA.change, "senate", 8);
  FC.renderHemicycle("hemi-senate", FC.senateSeatDots(s), 51, 9.2, 5, "legend-senate",
    '<span class="ring" style="border-color:' + FC.css("--c6") + '"></span><span class="ring" style="border-color:' +
    FC.css("--c0") + '"></span> Not up in 2026');
  FC.renderHistogram("hist-senate", s.histogram, 51, "Democratic Senate seats", "Senate");
  FC.$("senate-facts").innerHTML = "Democrats hold " + s.not_up.D + " seats that are not on the ballot and need 51 " +
    "for control, because Vice President Vance breaks a 50-50 tie. Chance of exactly 50-50: <b>" +
    FC.pct(s.tie_prob) + "</b>. Middle outcome: <b>D " + s.percentiles["50"] + " · R " +
    (100 - s.percentiles["50"]) + "</b>, with 80% of simulations between <b>" +
    s.percentiles["10"] + "</b> and <b>" + s.percentiles["90"] + "</b> Democratic seats. " +
    "Hover any bar for the chance of at least that many.";
  FC.renderTipping("tip-senate", FC.senateTipping(s, 10), "tip-senate-note",
    "In every simulated election the races are lined up from most Democratic to least, and the one that " +
    "delivers the 51st seat is the one control turned on. A race can be a coin flip and still rarely be " +
    "decisive, and a race can be leaning and still decide everything, because what matters is where it " +
    "sits in the order, not how close it is.");
  FC.renderSenateTable("tbl-senate", s);
  if (PAGE_DATA.attention && PAGE_DATA.attention.length) {
    FC.renderAttention("attention", PAGE_DATA.attention);
    var busiest = PAGE_DATA.attention[0];
    FC.$("attention-note").innerHTML =
      "Daily article views from Wikipedia, comparing the last 7 days with the 5 weeks before. " +
      "Busiest race right now: <b>" + FC.esc(busiest.state_name) + "</b> at " +
      Number(busiest.total_daily).toLocaleString() + " views a day across both candidates.";
  }
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
  <div id="ribbon-house"></div>
</section>

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
  <h2>Where control is actually decided</h2>
  <p class="lede">The districts that deliver the 218th seat, and how often each one is the one that does it.</p>
  <div class="card">
    <div id="tip-house"></div>
    <p class="tipfoot" id="tip-house-note"></p>
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
            "change": snap.get("change"),
            "hex": json.loads((GEO_DIR / "house_hex.json").read_text())}
    scripts = """
FC.onRender(function () {
  var h = PAGE_DATA.house;
  FC.renderHouseMap("map-house", h, PAGE_DATA.hex, PAGE_DATA.meta, "legend-house-map");
  FC.renderHemicycle("hemi-house", FC.houseSeatDots(h, PAGE_DATA.meta), h.majority, 4.6, 12, "legend-house");
  FC.renderHistogram("hist-house", h.histogram, h.majority, "Democratic House seats", "House");
  FC.$("house-facts").innerHTML = "Democrats need <b>" + h.majority + "</b> of 435. Middle outcome: <b>D " +
    h.percentiles["50"] + " · R " + (435 - h.percentiles["50"]) + "</b>, with 80% of simulations between <b>" +
    h.percentiles["10"] + "</b> and <b>" + h.percentiles["90"] + "</b> Democratic seats. " +
    "Hover any bar for the chance of at least that many.";
  FC.renderRibbon("ribbon-house", PAGE_DATA.change, "house", 10);
  FC.renderTipping("tip-house", FC.houseTipping(h, PAGE_DATA.meta, 12), "tip-house-note",
    "Spread thinner than the Senate's, and that is the finding rather than a flaw: with 435 seats and a " +
    "large field of near-identical toss-ups, no single district carries the majority the way one state can " +
    "carry the Senate. The order is what decides it, so a district that is safe for one side and a district " +
    "nobody is contesting are both, correctly, never decisive.");
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


def build_scenarios(snap, meta):
    """What the forecast would say if the national environment were different."""
    path = DATA_DIR / "scenarios.json"
    if not path.exists():
        return None
    grid = json.loads(path.read_text())
    body = """
<section>
  <div class="card">
    <div class="scn-val">
      <span>Drag to change the national environment</span>
      <span><b id="scn-gb"></b> generic ballot</span>
      <button type="button" class="scn-reset" id="scn-reset">Back to today</button>
    </div>
    <div class="scn-slider">
      <input type="range" id="scn-slider" aria-label="National generic-ballot margin">
    </div>
    <div class="scn-top">
      <div class="scn-read">
        <div class="cap">Democratic Senate</div>
        <div class="big" id="scn-senate"></div>
        <div class="seats" id="scn-senate-seats"></div>
      </div>
      <div class="scn-read">
        <div class="cap">Democratic House</div>
        <div class="big" id="scn-house"></div>
        <div class="seats" id="scn-house-seats"></div>
      </div>
    </div>
  </div>
</section>

<section>
  <div class="card">
    <div class="chamber-label">Chance of control, across the range</div>
    <svg id="scn-curve" viewBox="0 0 560 330" role="img"
      aria-label="Democratic chance of controlling each chamber, by national environment"></svg>
    <p class="note">Every point on both curves is a full run of the model with the same random
      draws, so the shape is the model's answer to the environment and nothing else. The faint
      dashed upright marks where the forecast sits today; the solid one follows the slider, and
      the two sit on top of each other until you move it.</p>
  </div>
</section>

<section>
  <h2>What changes hands</h2>
  <p class="lede">Races that fall on the other side of even, compared with where the forecast sits today.</p>
  <div class="card">
    <div class="scn-flips" id="scn-flips"></div>
    <p class="note" id="scn-flip-note"></p>
  </div>
</section>

<section class="prose">
  <h2>How to read this</h2>
  <p>{crossings}</p>
  <p>This is not a forecast of the generic ballot. It is the model answering a conditional: if the
  national environment were this instead of what it is, everything else being as it is today, here is
  what it would say. Ratings, state polls, the partisan index and the shape of the error are all held
  where they are; only the national number moves.</p>
  <p>The grid runs from R+{lo_lab} to D+{hi_lab} in one-point steps and the page interpolates between
  them, which is why the numbers move smoothly rather than in jumps. Each point is {sims:,} simulated
  elections rather than the {full:,} the published forecast uses: the comparison between points is what
  matters here, and using the same draws at every point means the difference between two points carries
  no simulation noise at all.</p>
</section>
"""
    def phrase(key, chamber):
        pts = grid["points"]
        for i in range(len(pts) - 1):
            a, b = pts[i][key], pts[i + 1][key]
            if (a - 0.5) * (b - 0.5) <= 0 and a != b:
                t = (0.5 - a) / (b - a)
                gb = pts[i]["generic_ballot"] + t * (pts[i + 1]["generic_ballot"] - pts[i]["generic_ballot"])
                side = "D+" if gb >= 0 else "R+"
                return (f"the {chamber} is a coin flip at a generic ballot of "
                        f"<b>{side}{abs(gb):.1f}</b>")
        lo_p, hi_p = pts[0][key], pts[-1][key]
        if lo_p > 0.5:
            return f"Democrats hold the {chamber} across the whole range"
        if hi_p < 0.5:
            return f"Democrats never reach even odds in the {chamber} anywhere on this range"
        return f"the {chamber} does not cross fifty cleanly on this range"

    today_gb = grid["today"]["generic_ballot"]
    crossings = (f"On today's inputs, {phrase('senate_prob', 'Senate')}, and "
                 f"{phrase('house_prob', 'House')}. The generic ballot is currently "
                 f"<b>{'D+' if today_gb >= 0 else 'R+'}{abs(today_gb):.1f}</b>.")
    body = body.format(crossings=crossings,
                       lo_lab=f"{abs(grid['points'][0]['generic_ballot']):.0f}",
                       hi_lab=f"{abs(grid['points'][-1]['generic_ballot']):.0f}",
                       sims=grid["n_sims"], full=snap.get("n_sims", 100000))

    data = {"site": site_meta(snap), "grid": grid,
            "senate": [{"seat_id": r["seat_id"], "rating": r["rating"], "state": r["state"]}
                       for r in snap["senate"]["seats"]],
            "meta": {k: {"state": v["state"]} for k, v in meta.items()}}
    scripts = """
FC.onRender(function () {
  FC.renderScenarios(PAGE_DATA.grid, PAGE_DATA.senate, PAGE_DATA.meta, {
    slider: "scn-slider", gb: "scn-gb", reset: "scn-reset", curve: "scn-curve",
    senProb: "scn-senate", houProb: "scn-house",
    senSeats: "scn-senate-seats", houSeats: "scn-house-seats",
    flips: "scn-flips", flipNote: "scn-flip-note"
  });
});
"""
    return page("scenarios.html", "Scenarios · 2026 Midterm Forecast",
                "What the forecast would say under a different national environment: drag the "
                "generic ballot and watch control probabilities and individual races move.",
                ("What would it take",
                 "The same model, re-run across a range of national environments. Drag the slider "
                 "and every race moves with it."),
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


def story_days(snaps):
    """One entry per Eastern-time day: the last run of that day, and its story.

    The pipeline can fire several times in a day. The forecast people care
    about is where it ended up, and the explanation attached to that run
    already covers the whole day, because it was measured against the last
    run of the day before.
    """
    by_day = {}
    for snap in snaps:
        day = datetime.fromisoformat(snap["timestamp"]).astimezone(ET).date()
        if day not in by_day or snap["timestamp"] > by_day[day]["timestamp"]:
            by_day[day] = snap
    days = []
    for day in sorted(by_day, reverse=True):
        snap = by_day[day]
        days.append({
            "label": day.strftime("%A, %B %-d"),
            "date": day.isoformat(),
            "timestamp": snap["timestamp"],
            "senate": snap["senate"]["dem_control_prob"],
            "house": snap["house"]["dem_control_prob"],
            "change": snap.get("change"),
        })
    if days:
        days[-1]["first"] = True    # nothing existed before this one to compare against
    return days


def build_trend(snaps):
    days = story_days(snaps)
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
  <h2>The story, day by day</h2>
  <p class="lede">Every day the model re-runs with one input held back at a time, so the change
    in the forecast can be split between the things that caused it. A driver that helped
    Democrats runs right in blue; one that helped Republicans runs left in red.</p>
  <div class="chamber-toggle" role="group" aria-label="Which chamber to explain">
    <button type="button" data-chamber="house" aria-pressed="true">House</button>
    <button type="button" data-chamber="senate" aria-pressed="false">Senate</button>
  </div>
  <div id="story"></div>
</section>

<section>
  <h2>Every run</h2>
  <p class="lede">One row per pipeline run. The national environment is the blend of polling and fundamentals that
    shifts every race.</p>
  <div class="card tbl-scroll"><table id="tbl-runs"></table></div>
</section>
"""
    data = {"site": site_meta(snaps[-1]), "runs": runs, "days": days}
    scripts = """
FC.onRender(function () {
  var runs = PAGE_DATA.runs;
  FC.renderTrend("trend-senate", runs, "senate", "senate_seats");
  FC.renderTrend("trend-house", runs, "house", "house_seats");
  FC.$("trend-note").textContent = runs.length > 1
    ? runs.length + " runs so far. Each point is one daily pipeline run; hover for the exact numbers."
    : "This is the first run of the current model. A point is added after every daily run, so the lines build up from here.";
  var chamber = "house";
  FC.renderStory("story", PAGE_DATA.days, chamber);
  [].forEach.call(document.querySelectorAll(".chamber-toggle button"), function (btn) {
    btn.addEventListener("click", function () {
      chamber = btn.getAttribute("data-chamber");
      [].forEach.call(document.querySelectorAll(".chamber-toggle button"), function (b) {
        b.setAttribute("aria-pressed", b === btn ? "true" : "false");
      });
      FC.renderStory("story", PAGE_DATA.days, chamber);
    });
  });

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


def load_attention():
    """Wikipedia readership per race, if refresh_attention.py has ever run."""
    path = DATA_DIR / "attention_2026.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return None


def attention_rows(attention, snap):
    """The races with a usable split, busiest first, ready for the renderer."""
    if not attention:
        return []
    names = {s["seat_id"]: s for s in snap["senate"]["seats"]}
    rows = []
    for seat, race in attention.get("races", {}).items():
        if "dem_share" not in race:
            continue
        dem, rep = race["sides"].get("D"), race["sides"].get("R")
        if not dem or not rep:
            continue

        def surge_note():
            """Name a surge only when it is big enough to mean something.

            When both candidates jump together it is the race that got hot,
            not either of them, and saying that is both shorter and truer
            than listing two nearly identical multiples.
            """
            d_up, r_up = dem.get("surge"), rep.get("surge")
            big = [f for f in (d_up, r_up) if f and f >= 1.8]
            if len(big) == 2 and min(big) / max(big) > 0.75:
                return f"whole race {statistics.mean(big):.1f}x"
            notes = []
            for side in (dem, rep):
                factor = side.get("surge")
                if factor and factor >= 1.8:
                    notes.append(f"{side['candidate'].split()[-1]} {factor:g}x")
            return " · ".join(notes)

        rows.append({
            "state_name": STATE_NAMES.get(race.get("state", seat[:2]), seat[:2]),
            "special": "special" in seat,
            "dem": dem["candidate"], "rep": rep["candidate"],
            "dem_last": dem["candidate"].split()[-1], "rep_last": rep["candidate"].split()[-1],
            "dem_share": race["dem_share"], "total_daily": race["total_daily"],
            "dem_views": dem.get("recent_daily"), "rep_views": rep.get("recent_daily"),
            "dem_surge": dem.get("surge"), "rep_surge": rep.get("surge"),
            "surge_note": surge_note(),
        })
    rows.sort(key=lambda r: -r["total_daily"])
    return rows


def load_markets():
    """Prediction market prices, if refresh_markets.py has ever run."""
    path = DATA_DIR / "markets_2026.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return None


def seat_bucket_bounds(label):
    """'226-229' -> (226, 229); 'Below 210' -> (None, 209); 'Above 249' -> (250, None)."""
    text = label.replace("%", "").strip()
    digits = [int(t) for t in text.replace("-", " ").split() if t.isdigit()]
    if not digits:
        return None
    low = text.lower()
    if low.startswith("below"):
        return (None, digits[0] - 1)
    if low.startswith("above"):
        return (digits[0] + 1, None)
    if len(digits) == 1:
        return (digits[0], digits[0])
    return (digits[0], digits[1])


def rebin(histogram, market_bins):
    """Fold the model's per-seat histogram into the market's buckets.

    The two are binned differently (the model counts every seat total, the
    market sells four-seat blocks), and overlaying mismatched bins is how a
    chart lies. Re-binning the model onto the market's own buckets is the
    only comparison that means anything.
    """
    rows = []
    for b in market_bins:
        bounds = seat_bucket_bounds(b["label"])
        if bounds is None:
            continue
        lo, hi = bounds
        share = sum(v for k, v in histogram.items()
                    if (lo is None or int(k) >= lo) and (hi is None or int(k) <= hi))
        rows.append({"label": b["label"], "lo": lo if lo is not None else -10 ** 6,
                     "model": round(share, 4),
                     "market": b.get("prob_normalized", b.get("prob"))})
    rows.sort(key=lambda r: r["lo"])
    for r in rows:
        r.pop("lo")
    return rows


def bin_midpoint_py(label):
    """Centre of a 'Democrats, 8 to 10%' band, in points of Democratic margin."""
    text = label.lower()
    if "republican" in text:
        return -2.0
    digits = [float(t) for t in text.replace("%", " ").replace("to", " ").replace(",", " ").split()
              if t.replace(".", "", 1).isdigit()]
    if not digits:
        return 0.0
    if "above" in text:
        return digits[0] + 1.0
    return sum(digits) / len(digits)


def build_markets(snap, markets):
    """Model against the betting markets. Shown, never blended in."""
    kalshi = (markets or {}).get("venues", {}).get("kalshi", {})
    poly = (markets or {}).get("venues", {}).get("polymarket", {})
    senate, house = snap["senate"], snap["house"]

    def venue_point(venue, key, label, vkey):
        entry = venue.get(key)
        if not entry:
            return None
        return {"key": vkey, "label": label, "prob": entry["prob"], "volume": entry.get("volume")}

    rows = []
    for chamber, model_prob, key in (("Senate", senate["dem_control_prob"], "senate_dem"),
                                     ("House", house["dem_control_prob"], "house_dem")):
        points = [{"key": "model", "label": "This model", "prob": round(model_prob, 4)}]
        for venue, vkey, label in ((kalshi, "kalshi", "Kalshi"), (poly, "polymarket", "Polymarket")):
            point = venue_point(venue, key, label, vkey)
            if point:
                points.append(point)
        rows.append({"chamber": chamber, "points": points})

    # Joint outcomes: the model's four-way split against Kalshi's.
    model_joint = snap.get("joint", {})
    joint_map = {"D-House, D-Senate": "dem_both", "D-House, R-Senate": "dem_house_rep_senate",
                 "R-House, D-Senate": "rep_house_dem_senate", "R-House, R-Senate": "rep_both"}
    joint_rows = []
    for b in kalshi.get("joint", []):
        model_key = joint_map.get(b["label"])
        if model_key is None:
            continue
        joint_rows.append({"label": b["label"].replace("-House, ", "H / ").replace("-Senate", "S"),
                           "model": model_joint.get(model_key),
                           "market": b.get("prob_normalized", b["prob"])})
    order = list(joint_map)
    joint_rows.sort(key=lambda r: order.index(
        r["label"].replace("H / ", "-House, ").replace("S", "-Senate")) if
        r["label"].replace("H / ", "-House, ").replace("S", "-Senate") in order else 9)

    senate_bins = rebin(senate["histogram"], kalshi.get("senate_seats", {}).get("bins", []))
    house_bins = rebin(house["histogram"], kalshi.get("house_seats", {}).get("bins", []))

    # Per-race: model probability against Polymarket's, Democratic side.
    race_rows = []
    seats_by_id = {s["seat_id"]: s for s in senate["seats"]}
    for seat_id, m in sorted((poly.get("races") or {}).items(),
                             key=lambda kv: -kv[1].get("volume", 0)):
        seat = seats_by_id.get(seat_id)
        dem = m["parties"].get("D", {}).get("prob")
        if seat is None or dem is None:
            continue
        # Nebraska's Democratic-caucusing candidate runs as an independent, so
        # the comparable market price is the independent's, not the Democrat's.
        if seat.get("challenger_caucus") == "independent" and "I" in m["parties"]:
            dem = m["parties"]["I"]["prob"]
        race_rows.append({"state": seat["state"],
                          "special": "special" in seat_id,
                          "model": round(seat["dem_win_prob"], 4),
                          "market": round(dem, 4),
                          "volume": m.get("volume", 0)})

    gb = kalshi.get("generic_ballot") or {}
    env = snap["environment"]
    total_volume = sum(v.get("volume", 0) for v in
                       [kalshi.get("senate_dem", {}), kalshi.get("house_dem", {}),
                        poly.get("senate_dem", {}), poly.get("house_dem", {})])

    data = {"site": site_meta(snap),
            "rows": rows, "joint": joint_rows, "senateBins": senate_bins, "houseBins": house_bins,
            "races": race_rows, "asOf": (markets or {}).get("as_of")}

    gb_block = ""  # noqa: F841 - rebuilt just below
    if gb.get("implied_dem_margin") is not None:
        def short_label(label):
            """'Democrats, 8 to 10%' -> '8-10'; 'Republicans win' -> 'R'."""
            if "republican" in label.lower():
                return "R"
            digits = [t for t in label.replace("%", " ").replace(",", " ").split() if t.isdigit()]
            if "above" in label.lower() and digits:
                return digits[0] + "+"
            return "-".join(digits) if digits else label

        data["genericBallot"] = {"polls": env["generic_ballot"],
                                 "market": gb["implied_dem_margin"]}
        data["marginBins"] = sorted(
            [{"label": b["label"], "short": short_label(b["label"]),
              "prob": b.get("prob_normalized", b["prob"]),
              "sort": bin_midpoint_py(b["label"])} for b in gb.get("bins", [])],
            key=lambda r: r["sort"])
        gb_block = f"""
  <div class="card">
    <div class="chamber-label">The generic ballot, as priced</div>
    <div class="eq">
      <div class="term"><b id="gb-polls"></b><span>Polling average</span></div>
      <div class="op">vs</div>
      <div class="term"><b id="gb-market"></b><span>Kalshi popular vote market</span></div>
    </div>
    <p class="note">Kalshi runs a market on the House popular vote margin itself, in two-point
    bands. The figure above is the average of those bands weighted by price: the margin the
    market expects. It is the cleanest like-for-like reading here, because it prices the same
    quantity the model takes from polls. ${gb['volume']:,.0f} traded.</p>
    <svg id="mkt-margin-bins" viewBox="0 0 520 150" role="img"
         aria-label="Priced probability of each House popular vote margin band"></svg>
  </div>"""

    body = f"""
<section>
  <div class="card">
    <div class="chamber-label">Chance Democrats win each chamber</div>
    <svg id="mkt-compare" viewBox="0 0 520 200" role="img"
         aria-label="Model and market probabilities for each chamber"></svg>
    <div class="legend" id="mkt-legend"></div>
    <p class="note">Filled circle is this model. Hollow shapes are real money: Kalshi is a
    CFTC-regulated US exchange, Polymarket settles in crypto. Shape rather than colour tells
    them apart, because on this site blue means Democratic and red means Republican.</p>
  </div>
</section>

<section>
  <h2>Where the model and the market disagree</h2>
  <p class="lede">A gap is not proof either side is wrong. It is a question worth asking: what
  does one of them know, or believe, that the other does not?</p>
  <div class="grid2">
    <div class="card"><div class="chamber-label">All four outcomes</div>
      <svg id="mkt-joint" viewBox="0 0 520 210" role="img"
           aria-label="Model and market probability for each combination of chambers"></svg>
      <div class="legend" id="mkt-joint-legend"></div>
      <p class="note">Kalshi prices all four combinations as one market, which is exactly
      what the model's simulations produce. H is the House, S the Senate.</p>
    </div>
    {gb_block}
  </div>
</section>

<section>
  <h2>Seat counts, on the market's own buckets</h2>
  <p class="lede">The model counts every possible seat total; the market sells blocks of them.
  To compare the two, the model's simulations are folded into the market's buckets.</p>
  <div class="grid2">
    <div class="card"><div class="chamber-label">Democratic Senate seats</div>
      <svg id="mkt-senate-seats" viewBox="0 0 520 240" role="img"
           aria-label="Model and market distribution of Democratic Senate seats"></svg>
      <div class="legend" id="mkt-senate-legend"></div></div>
    <div class="card"><div class="chamber-label">Democratic House seats</div>
      <svg id="mkt-house-seats" viewBox="0 0 520 240" role="img"
           aria-label="Model and market distribution of Democratic House seats"></svg>
      <div class="legend" id="mkt-house-legend"></div></div>
  </div>
</section>

<section>
  <h2>Race by race</h2>
  <p class="lede">Every Senate race with a traded market, Democratic win chance either way.
  Gaps of 10 points or more are picked out.</p>
  <div id="mkt-races"></div>
  <p class="note">Nebraska is compared on Dan Osborn's price: he runs as an independent, and
  the model counts him as neither party.</p>
</section>

<section class="prose">
  <h2>Why these numbers are not in the forecast</h2>
  <p>They are shown, not blended. A prediction market is mostly a weighted digest of the same
  polls and race ratings this model already reads, so averaging the two would be averaging a
  thing with itself, while quietly importing the market's own quirks: thin books on minor
  contracts, the long-standing tendency to overprice long shots, and the occasional single
  large trader who moves a price on their own.</p>
  <p>What a market does add is everything a ratings-and-polls model structurally cannot see:
  candidate quality, a scandal, how a redistricting case is likely to land, what turnout will
  actually look like. That is why the disagreement is worth showing rather than smoothing
  away. When the two drift apart, one of them is wrong, and which one is a question you can
  now watch rather than guess at. Prices are collected fresh on every run, and
  ${total_volume:,.0f} has been traded across the four chamber markets above.</p>
</section>
"""
    scripts = """
FC.onRender(function () {
  FC.renderMarketCompare("mkt-compare", PAGE_DATA.rows, "mkt-legend");
  if (PAGE_DATA.genericBallot) {
    FC.$("gb-polls").textContent = FC.margin(PAGE_DATA.genericBallot.polls);
    FC.$("gb-market").textContent = FC.margin(PAGE_DATA.genericBallot.market);
    FC.renderMarginBins("mkt-margin-bins", PAGE_DATA.marginBins);
  }
  if (PAGE_DATA.joint.length) {
    FC.renderPairedBars("mkt-joint", PAGE_DATA.joint, "mkt-joint-legend", "Chance of each outcome");
  }
  if (PAGE_DATA.senateBins.length) {
    FC.renderPairedBars("mkt-senate-seats", PAGE_DATA.senateBins, "mkt-senate-legend",
      "Democratic Senate seats");
  }
  if (PAGE_DATA.houseBins.length) {
    FC.renderPairedBars("mkt-house-seats", PAGE_DATA.houseBins, "mkt-house-legend",
      "Democratic House seats");
  }
  FC.renderMarketRaces("mkt-races", PAGE_DATA.races);
});
"""
    return page("markets.html", "Betting markets · 2026 Midterm Forecast",
                "What real money says about the 2026 midterms, next to what this model says, "
                "and why the two are never averaged together.",
                ("What the betting markets say",
                 "Two exchanges, real money, next to this model's answer. Shown side by side and "
                 "never blended into the forecast."),
                body, js(data), scripts)


BACKTEST_LABEL = {"tossup": "Toss-up", "lean": "Lean", "likely": "Likely", "solid": "Solid"}
BACKTEST_FILE = "backtest_2018_2022.json"


def backtest_data():
    """The backtest summary, or None on a clone that has never run it."""
    path = DATA_DIR / BACKTEST_FILE
    return json.loads(path.read_text()) if path.exists() else None


def backtest_section(bt):
    if not bt:
        return ""
    rows = ""
    for b in bt["buckets"]:
        rows += (f"<tr><td>{BACKTEST_LABEL[b['rating']]}</td>"
                 f"<td class='num'>{b['n']:,}</td>"
                 f"<td class='num'>{b['old']:.1f}</td>"
                 f"<td class='num'>{b['actual']:.1f}</td>"
                 f"<td class='num'>{b['sd']:.1f}</td>"
                 f"<td class='num'>{b['held']:.0%}</td>"
                 f"<td class='num'>{b['fitted_house']:.1f}</td>"
                 f"<td class='num'>{b['fitted_senate']:.1f}</td></tr>")
    lay = bt["layers"]
    span = f"{bt['cycles'][0]} to {bt['cycles'][-1]}"
    full = ", ".join(bt["full_cycles"][:-1]) + " and " + bt["full_cycles"][-1]
    vals = list(bt["layers"]["national_bias"].values())
    bias = ", ".join(f"{v:+.1f}" for v in vals[:-1]) + f" and {vals[-1]:+.1f}"
    return f"""
<section class="prose" id="backtest">
  <h2>Graded against seven past elections</h2>
  <p class="lede">Every constant in the simulation used to be a guess with a comment next to it saying so.
  They are now fitted on {bt['races']:,} real races, rebuilt from the final Cook ratings published days
  before each election and the results that followed. {bt['competitive']} of them are races some rater
  called competitive, covering every cycle from {span}; the other {bt['safe']:,} are the safe seats of
  {full}, the cycles where a partisan index exists for all 435 districts. Run the settings this project
  started with against those races and the average one is missed by
  <b>{bt['mean_abs_error_before']:.1f} points</b>. Run the current ones and it is
  <b>{bt['mean_abs_error_after']:.1f}</b>.</p>

  <div class="card tbl-scroll"><table>
    <thead><tr><th>Rating</th><th class="num">Races</th><th class="num">Old model</th>
      <th class="num">Actually won by</th><th class="num">Spread</th>
      <th class="num">Favourite held</th><th class="num">Now, House</th>
      <th class="num">Now, Senate</th></tr></thead>
    <tbody>{rows}</tbody>
  </table></div>

  <p>The expensive error was Solid. A Solid seat was given 18 points; safe House seats actually win by 33
  and safe Senate seats by 26, and the spread inside that bucket is sixteen points wide. The model was
  treating a Solid R district at R+33 and one at D+12 as equally out of reach. That costs almost no
  probability directly, because neither flips at 18 points or at 33, but it was what the site printed as a
  race's expected margin, and it was the reason the partisan index had to start doing real work: inside
  the Solid buckets a point of Cook index is worth {bt['pvi_slopes'].get('solid', 1.75):.2f} points of
  margin, and using it cuts the spread there from eighteen points to six.</p>

  <p>The quieter error was Toss-up and Lean, and that one moves probabilities. A toss-up favourite was
  given a 54% chance and really wins {bt['buckets'][0]['held']:.0%}; a Lean favourite was given 81% and
  really wins {bt['buckets'][1]['held']:.0%}. Both have been moved to what the record shows. This is not a
  thumb on the scale for either party, it says the party already holding a close seat wins it more often
  than a coin flip, but it does cut against the Democrats in this particular cycle, who are defending five
  of this year's toss-up House seats to the Republicans' sixteen.</p>

  <h3>Is it calibrated?</h3>
  <p>The test a forecast cannot talk its way around: of all the races where the model said the favourite
  had about a 70% chance, did the favourite win about 70% of the time? Each dot is
  {bt['calibration'][0]['n']} races. The whiskers are 95% intervals, which is the width at which seventy-odd
  races can actually be read.</p>
  <div class="card">
    <svg id="calibration" viewBox="0 0 520 330" role="img"
      aria-label="Predicted win probability against how often the favourite actually won"></svg>
  </div>

  <p>Two readings, and the second is the more useful one. The dots sit close to the line, which is the
  point of drawing it. But almost every dot above 60% sits slightly <i>above</i> the line, meaning
  favourites win a little more often than the model says they will. That is under-confidence, and it is the
  safer direction to be wrong in, but it is a real tilt rather than noise: it shows up in five bins running.
  The one bin that leans the other way is the leftmost, where the model calls a race a hair better than even
  and the favourite wins slightly less often than that. The Brier score, which is the average squared
  distance between what was said and what happened, is {bt['brier']:.3f}; always guessing 50% would score
  0.25.</p>

  <h3>What survived</h3>
  <p>The correlated-error structure was built on three guesses about how a polling miss clusters. Measured
  on the same races, a state's races share <b>{lay['state_sd']:.1f} points</b> of miss, against 2.5 assumed
  before anyone checked, and the per-race residual is {lay['race_sd']:.1f} points. The census-division
  layer measured <b>{lay['division_sd']:.1f}</b>, so it has been removed from the simulation entirely:
  regional misses are real, but every point of them lives at the state line.</p>

  <p>The national miss is the one that needed the extra cycles. Across the seven, the average competitive
  race beat its rating by {bias} points, a standard deviation of <b>{lay['national_sd']:.1f}</b>. That is
  now the Election-Day floor, and because misses of that kind have come in clusters rather than
  independently, the simulation draws it from a distribution with heavier tails than a bell curve. The
  centre is unchanged; a 2016-sized miss simply stops being treated as impossible.</p>

  <p>Two honest limits. Seven observations is still seven, so the national error is treated as a floor
  rather than a precise estimate, and the model does not correct for the average of those seven being
  slightly Republican, because at this sample size that average is indistinguishable from zero. And the
  ratings articles list only the seats some rater called competitive, so in the three full cycles every
  unlisted seat is entered as Solid for the party that held it. That is true in almost every case, and the
  exceptions are the safe seats in the table that the favourite did not hold.</p>
</section>
"""


def build_methodology(snap):
    bt = backtest_data()
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
    at = load_attention()
    if at:
        n_races = sum(1 for v in at.get("races", {}).values() if "dem_share" in v)
        rows.append(("Candidate attention",
                     f"Wikipedia article views for both candidates in {n_races} competitive "
                     "races. <b>Displayed only, never blended into the forecast</b>",
                     "Every run", str(at.get("as_of", ""))[:10],
                     '<a href="https://wikimedia.org/api/rest_v1/" target="_blank" '
                     'rel="noopener">Wikimedia REST API</a> &#183; '
                     '<a href="senate.html">see the split</a>'))
    mk = load_markets()
    if mk:
        venue_names = ", ".join(v.get("name", k) for k, v in mk.get("venues", {}).items())
        rows.append(("Betting market prices",
                     "Chamber control, seat counts, popular vote margin and Senate races. "
                     "<b>Displayed only, never blended into the forecast</b>",
                     "Every run", str(mk.get("as_of", ""))[:10],
                     f'{venue_names} &#183; <a href="markets.html">see the comparison</a>'))
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
    <h3>Simulate every race 100,000 times</h3>
    <p><b>Senate races follow their own polling.</b> For every competitive Senate race the model reads the
    state polling averages published on that race's Wikipedia article, collating RealClearPolitics,
    Silver Bulletin, Decision Desk HQ, 270toWin and others. The polling average and the rating are two
    rival estimates of the same thing, today's margin, so they are blended rather than stacked: stacking
    would count the national environment twice. How much of the blend is polling depends on how many
    aggregators cover the race and how close Election Day is, rising from half to 85% as November nears.
    Before this existed, every toss-up carried the identical D+1.3 baseline and the forecast literally
    could not tell Iowa from Maine.</p>

    <p><b>House seats are separated by partisan lean.</b> A rating puts a seat in a class; Cook's Partisan
    Voting Index says where inside that class it sits. Each district's baseline is nudged by how far its
    index is from the typical index of seats with the same rating, centred so a rating's average is left
    exactly where the rater put it. How hard that nudge pushes is now measured rather than guessed, and it
    turns out to depend entirely on the rating: among safe seats the index moves the margin 1.7 points per
    point of index, among competitive ones about 0.2, which is inside the noise. A rater watching a race closely has already priced its
    partisanship in; a rater who wrote a seat off as Solid has not. The ten states that redrew mid-decade
    are excluded, because the published index still describes their old lines.</p>

    <p><b>Each race starts from its published rating</b>, converted to an expected margin: in the House,
    Solid 33 points, Likely 11, Lean 7, Toss-up 0.8 toward the party that holds it; in the Senate, 26, 12.5,
    8.5 and 1.5. Those numbers are read off 1,572 real races rather than chosen, and the chambers are
    listed separately because they measure differently. The <a href="#backtest">backtest below</a> shows the
    working. Ratings already reflect the environment when they were set, so a race is shifted only by how
    far the environment has moved since.</p>

    <p><b>A polling miss is not one national number plus independent local noise</b>, so it is drawn in
    three nested layers: national, state, and the race itself. A state's Senate race shares its miss with
    its own House seats, which is why the two chambers move together. The national layer is drawn from a
    heavier-tailed distribution than a bell curve, because misses the size of 2016's and 2020's have
    happened twice in a decade and a bell curve would call that nearly impossible. The race layer depends on
    the rating: a race everyone is watching turns out to be more predictable than one nobody is, 4.8 points
    of spread against 6.5 for a safe House seat, because the watching is what produces the information.</p>

    <p><b>The same run is repeated across a range of national environments</b> and written out as a
    lookup table, which is what the <a href="scenarios.html">Scenarios page</a> reads. Every point on
    that grid uses the same draws as every other, so moving along it can only change an answer
    because the environment changed.</p>

    <p><b>Then 100,000 elections are simulated, using the same random draws every day.</b>
    Fixing the draws matters more than it sounds: with 20,000 fresh draws each run, two runs on
    identical data disagreed by up to 1.8 points, which is larger than most real daily moves, so
    part of the trend line was reporting luck. Re-using the draws and raising the count leaves
    under 0.2 points of self-noise, which is what makes the day-by-day explanation trustworthy. Each
    simulated election is also asked which race delivered the deciding seat, which is what the
    <a href="senate.html">Senate</a> and <a href="house.html">House</a> pages report as a tipping point.</p>
  </div></div>

  <div class="step"><div class="n">6</div><div>
    <h3>Check the whole thing against elections that already happened</h3>
    <p>Every constant above is fitted on seven past elections and then graded on them, including a
    calibration check: when the model says 70%, does it happen 70% of the time? What the grading changed,
    and by how much, is set out in full <a href="#backtest">below</a>.</p>
  </div></div>

  <div class="step"><div class="n">7</div><div>
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

{backtest_section(bt)}

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
    <li><b>It does not guess at why it moved.</b> The day-by-day explanation on the
      <a href="trend.html">trend page</a> is measured, not narrated: the model is re-run with each
      input held back in turn, and each driver is credited with the difference it makes. Every one
      of those runs uses the same random draws, so the comparison contains no simulation noise, and
      whatever the drivers fail to account for is published as a residual rather than spread
      quietly among them. Every daily input is rebuilt from the two snapshots being compared rather
      than read live, so a change made overnight can never be backdated into an old explanation, and
      when the model itself changes -- a constant refitted against the backtest, say -- that shows up
      as its own driver, <i>Model recalibration</i>, rather than being blamed on the data. What it cannot tell you is why the underlying number moved: it can
      say the generic ballot shifted a point and what that was worth, not what happened in the news
      to shift it.</li>
    <li><b>It does not mistake attention for support.</b> Wikipedia readership per candidate is
      collected every run and shown on the <a href="senate.html">Senate page</a>, because a race
      the country has suddenly started reading about is worth knowing. It is not an input: people
      look up a candidate who is in trouble just as readily as one who is winning, and a famous
      incumbent always out-reads a newcomer.</li>
    <li><b>It does not follow the betting markets.</b> Kalshi and Polymarket prices are collected every run and
      shown on the <a href="markets.html">markets page</a>, but nothing on this site is blended with them. A
      prediction market is mostly a weighted digest of the same polls and ratings this model already reads, so
      averaging the two would be averaging a thing with itself while importing the market's own quirks. Where the
      two disagree, both numbers are published and neither is quietly adjusted toward the other.</li>
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
                body, js({"site": site_meta(snap), "backtest": bt}),
                """
FC.onRender(function () {
  var bt = PAGE_DATA.backtest;
  if (bt) FC.renderCalibration("calibration", bt.calibration, bt.brier);
});
""")


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

    markets = load_markets()
    pages = {
        "index.html": build_index(latest, meta, markets),
        "senate.html": build_senate(latest, load_attention(), markets, snaps),
        "house.html": build_house(latest, meta),
        "economy.html": build_economy(latest),
        "trend.html": build_trend(snaps),
        "methodology.html": build_methodology(latest),
    }
    scenarios = build_scenarios(latest, meta)
    if scenarios:
        pages["scenarios.html"] = scenarios
    else:
        print("  (no data/scenarios.json yet - skipping the scenarios page)")
    if markets:
        pages["markets.html"] = build_markets(latest, markets)
    else:
        print("  (no data/markets_2026.json yet - skipping the markets page)")
    for name, html in pages.items():
        (OUT / name).write_text(html, encoding="utf-8")

    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"site/: {len(pages)} pages, {len(snaps)} run(s) embedded, {total / 1024:.0f} KB total")
    for name in pages:
        print(f"  {name:18} {(OUT / name).stat().st_size / 1024:6.0f} KB")


if __name__ == "__main__":
    main()
