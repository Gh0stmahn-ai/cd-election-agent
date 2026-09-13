#!/usr/bin/env python3
"""
Free economic refresh. No API key, no cost, no LLM.

Every economic reading on the site comes from a public series published by
a U.S. statistical agency and mirrored by the St. Louis Fed (FRED), which
serves each one as a plain CSV with no key and no registration:

    https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES_ID>

This script pulls those series, derives the figures the model wants
(year-over-year changes, real wages, year-to-date returns), and writes each
one through ingest.refresh_fundamental, so the same range checks apply as
when the AI refresh writes them.

What this CANNOT do, and why agent_run.py still exists: polling averages,
Cook rating changes and campaign news are not published as data series.
Reading those means reading pages, which needs the AI refresh (or a manual
entry through the workflow's optional inputs).

Run it directly:  python3 refresh_economy.py
"""
import csv
import io
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

import ingest

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
FRED_PAGE = "https://fred.stlouisfed.org/series/{}"
TIMEOUT = 30

# Who actually produces each number, for the "sources" line on the economy page.
PRODUCER = {
    "GASREGW": ("U.S. Energy Information Administration (via FRED)", "GASREGW"),
    "DCOILWTICO": ("U.S. Energy Information Administration (via FRED)", "DCOILWTICO"),
    "DCOILBRENTEU": ("U.S. Energy Information Administration (via FRED)", "DCOILBRENTEU"),
    "CPIAUCSL": ("Bureau of Labor Statistics (via FRED)", "CPIAUCSL"),
    "CPILFESL": ("Bureau of Labor Statistics (via FRED)", "CPILFESL"),
    "UMCSENT": ("University of Michigan (via FRED)", "UMCSENT"),
    "CES0500000003": ("Bureau of Labor Statistics (via FRED)", "CES0500000003"),
    "UNRATE": ("Bureau of Labor Statistics (via FRED)", "UNRATE"),
    "PAYEMS": ("Bureau of Labor Statistics (via FRED)", "PAYEMS"),
    "A191RL1Q225SBEA": ("Bureau of Economic Analysis (via FRED)", "A191RL1Q225SBEA"),
    "MORTGAGE30US": ("Freddie Mac (via FRED)", "MORTGAGE30US"),
    "DGS10": ("U.S. Treasury (via FRED)", "DGS10"),
    "DGS2": ("U.S. Treasury (via FRED)", "DGS2"),
    "SP500": ("S&P Dow Jones Indices (via FRED)", "SP500"),
}

_cache = {}


def series(series_id):
    """Fetch one FRED series as a list of (date, value), oldest first.

    FRED writes '.' for days with no observation (market holidays); those
    rows are dropped rather than carried forward as zero.
    """
    if series_id in _cache:
        return _cache[series_id]
    url = FRED_CSV.format(series_id)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"could not fetch {series_id}: {e}") from e

    rows = []
    for row in csv.DictReader(io.StringIO(body)):
        values = list(row.values())
        raw_date, raw_value = values[0], values[1]
        if raw_value in (".", "", None):
            continue
        try:
            rows.append((date.fromisoformat(raw_date.strip()), float(raw_value)))
        except ValueError:
            continue
    if not rows:
        raise RuntimeError(f"{series_id} came back empty")
    rows.sort()
    _cache[series_id] = rows
    return rows


def latest(series_id):
    return series(series_id)[-1]


def value_on_or_before(series_id, target):
    """The most recent observation at or before `target` (for year-ago compares)."""
    rows = series(series_id)
    prior = [r for r in rows if r[0] <= target]
    return prior[-1] if prior else rows[0]


def n_back(series_id, n):
    """The observation n periods before the latest (for evenly spaced monthly series)."""
    rows = series(series_id)
    return rows[-1 - n] if len(rows) > n else rows[0]


def yoy_percent(series_id):
    """Year-over-year percent change of an index level, e.g. CPI -> inflation rate."""
    d, now = latest(series_id)
    _, year_ago = n_back(series_id, 12)
    return round((now / year_ago - 1) * 100, 1), d


def sources_for(*series_ids):
    out = []
    for sid in series_ids:
        name, ref = PRODUCER[sid]
        out.append({"name": name, "url": FRED_PAGE.format(ref)})
    return out


def month_label(d):
    return f"{d.year}-{d.month:02d}"


def quarter_label(d):
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


# Each builder returns the kwargs for ingest.refresh_fundamental, or None to skip.

def gas_price():
    d, now = latest("GASREGW")
    _, year_ago = value_on_or_before("GASREGW", d - timedelta(days=365))
    change = (now / year_ago - 1) * 100
    return dict(
        indicator_id="gas_price", value=round(now, 3), display=f"${now:.2f}",
        as_of=d.isoformat(), compare_value=round(year_ago, 3),
        compare_display=f"${year_ago:.2f}",
        change_display=f"{change:+.0f}% vs a year ago",
        context="Weekly U.S. regular all-formulations retail average.",
        sources=sources_for("GASREGW"))


def oil_wti():
    d, wti = latest("DCOILWTICO")
    _, brent = latest("DCOILBRENTEU")
    _, year_ago = value_on_or_before("DCOILWTICO", d - timedelta(days=365))
    change = (wti / year_ago - 1) * 100
    return dict(
        indicator_id="oil_wti", value=round(wti, 2), display=f"${wti:.0f}",
        as_of=d.isoformat(), compare_value=round(brent, 2),
        compare_display=f"${brent:.0f} Brent",
        change_display=f"{change:+.0f}% vs a year ago",
        context="West Texas Intermediate spot price, with Brent for comparison.",
        sources=sources_for("DCOILWTICO", "DCOILBRENTEU"))


def cpi_inflation():
    headline, d = yoy_percent("CPIAUCSL")
    core, _ = yoy_percent("CPILFESL")
    return dict(
        indicator_id="cpi_inflation", value=headline, display=f"{headline:.1f}%",
        as_of=month_label(d), compare_value=core, compare_display=f"{core:.1f}% core",
        change_display=f"Core (excluding food and energy) {core:+.1f}%",
        context="Headline CPI, all urban consumers, versus the same month a year earlier.",
        sources=sources_for("CPIAUCSL", "CPILFESL"))


def consumer_sentiment():
    d, now = latest("UMCSENT")
    _, year_ago = n_back("UMCSENT", 12)
    change = (now / year_ago - 1) * 100
    return dict(
        indicator_id="consumer_sentiment", value=round(now, 1), display=f"{now:.1f}",
        as_of=month_label(d), compare_value=round(year_ago, 1),
        compare_display=f"{year_ago:.1f}",
        change_display=f"{change:+.0f}% vs a year ago",
        context="University of Michigan Index of Consumer Sentiment, 1966 = 100.",
        sources=sources_for("UMCSENT"))


def real_wages():
    nominal, d = yoy_percent("CES0500000003")
    prices, _ = yoy_percent("CPIAUCSL")
    real = round(nominal - prices, 1)
    direction = "Pay rising faster than prices" if real > 0 else "Pay rising slower than prices"
    return dict(
        indicator_id="real_wages", value=real, display=f"{real:+.1f}%",
        as_of=month_label(d), compare_value=nominal,
        compare_display=f"{nominal:+.1f}% nominal",
        change_display=direction,
        context="Average hourly earnings growth minus CPI inflation, both year over year.",
        sources=sources_for("CES0500000003", "CPIAUCSL"))


def unemployment():
    d, rate = latest("UNRATE")
    rows = series("PAYEMS")
    jobs = round(rows[-1][1] - rows[-2][1])
    twelve = round((rows[-1][1] - rows[-13][1]) / 12)
    return dict(
        indicator_id="unemployment", value=round(rate, 1), display=f"{rate:.1f}%",
        as_of=month_label(d), compare_value=jobs,
        compare_display=f"{jobs:+,}K jobs",
        change_display=f"Prior 12-month average: {twelve:+,}K a month",
        context="Unemployment rate with the latest month's change in nonfarm payrolls.",
        sources=sources_for("UNRATE", "PAYEMS"))


def gdp_growth():
    rows = series("A191RL1Q225SBEA")
    d, now = rows[-1]
    prior_d, prior = rows[-2]
    trend = "Slowing" if now < prior else "Picking up"
    return dict(
        indicator_id="gdp_growth", value=round(now, 1), display=f"{now:.1f}%",
        as_of=quarter_label(d), compare_value=round(prior, 1),
        compare_display=f"{prior:.1f}% in {quarter_label(prior_d).split('-')[1]}",
        change_display=trend,
        context="Real GDP, percent change from the preceding quarter at an annual rate.",
        sources=sources_for("A191RL1Q225SBEA"))


def mortgage_rate():
    d, now = latest("MORTGAGE30US")
    _, year_ago = value_on_or_before("MORTGAGE30US", d - timedelta(days=365))
    return dict(
        indicator_id="mortgage_rate", value=round(now, 2), display=f"{now:.2f}%",
        as_of=d.isoformat(), compare_value=round(year_ago, 2),
        compare_display=f"{year_ago:.2f}%",
        change_display=f"{now - year_ago:+.2f} pt vs a year ago",
        context="Freddie Mac Primary Mortgage Market Survey, 30-year fixed.",
        sources=sources_for("MORTGAGE30US"))


def treasury_10y():
    d, ten = latest("DGS10")
    _, two = latest("DGS2")
    spread = ten - two
    shape = "steep" if spread > 0 else "inverted"
    return dict(
        indicator_id="treasury_10y", value=round(ten, 2), display=f"{ten:.2f}%",
        as_of=d.isoformat(), compare_value=round(two, 2),
        compare_display=f"{two:.2f}% 2-yr",
        change_display=f"10s-2s spread {spread:+.2f} pt ({shape})",
        context="Constant-maturity Treasury yields; the 2-year tracks Fed expectations.",
        sources=sources_for("DGS10", "DGS2"))


def sp500():
    d, now = latest("SP500")
    _, start = value_on_or_before("SP500", date(d.year, 1, 1) - timedelta(days=1))
    ytd = round((now / start - 1) * 100, 1)
    return dict(
        indicator_id="sp500", value=round(now, 2), display=f"{now:,.0f}",
        as_of=d.isoformat(), compare_value=ytd, compare_display=f"{ytd:+.1f}% YTD",
        change_display=f"{ytd:+.1f}% since the {d.year - 1} close",
        context="S&P 500 closing level, with the year-to-date return.",
        sources=sources_for("SP500"))


BUILDERS = [gas_price, oil_wti, cpi_inflation, consumer_sentiment, real_wages,
            unemployment, gdp_growth, mortgage_rate, treasury_10y, sp500]


def on_file():
    """The as_of already recorded for each indicator, so a refresh can't move one backwards."""
    payload = ingest._read("fundamentals_2026.json")
    return {i["id"]: str(i.get("as_of", "")) for i in payload["indicators"]}


def is_newer(new_as_of, old_as_of):
    """True when new_as_of is at least as recent as old_as_of.

    Every as_of in this file is one of three zero-padded shapes (2026-09-07,
    2026-08, 2026-Q2), each of which sorts correctly as a plain string. A
    shape change means the source changed, and the new figure wins.
    """
    if not old_as_of or len(new_as_of) != len(old_as_of):
        return True
    return new_as_of >= old_as_of


def main():
    updated, failed, skipped = [], [], []
    current = on_file()
    for build in BUILDERS:
        name = build.__name__
        try:
            payload = build()
        except Exception as e:  # noqa: BLE001 - one bad series shouldn't sink the rest
            print(f"  ! {name}: could not build ({e})", file=sys.stderr)
            failed.append(name)
            continue
        was = current.get(payload["indicator_id"], "")
        if not is_newer(payload["as_of"], was):
            # Some series reach FRED later than they reach the press release the
            # AI refresh reads, so an older FRED stamp is not an update.
            print(f"  - {payload['indicator_id']}: keeping {was} (FRED has {payload['as_of']})")
            skipped.append(name)
            continue
        try:
            ingest.refresh_fundamental(**payload)
        except Exception as e:  # noqa: BLE001 - range check rejected it
            print(f"  ! {name}: rejected by ingest ({e})", file=sys.stderr)
            failed.append(name)
            continue
        print(f"  {payload['indicator_id']}: {payload['display']} (as of {payload['as_of']})")
        updated.append(name)

    print(f"\nRefreshed {len(updated)} of {len(BUILDERS)} indicators from public data.")
    if skipped:
        print(f"Already current (nothing newer published): {', '.join(skipped)}")
    if failed:
        print(f"Could not refresh: {', '.join(failed)}")
    print("Polling averages and race ratings are not data series; they come from "
          "agent_run.py or the workflow's manual inputs.")
    # A partial refresh is still a better forecast than no refresh, and a day
    # where nothing new was published is a success, not a failure.
    return 1 if failed and not updated and not skipped else 0


if __name__ == "__main__":
    sys.exit(main())
