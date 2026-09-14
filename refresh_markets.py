#!/usr/bin/env python3
"""
Prediction market prices. No API key, no cost.

Two exchanges run liquid markets on the 2026 midterms and both serve prices
over public endpoints:

  Kalshi      api.elections.kalshi.com  (CFTC-regulated US exchange)
  Polymarket  gamma-api.polymarket.com  (crypto-settled)

These prices are NOT an input to the model. They are collected so the site
can show model and market side by side, which is useful precisely when the
two disagree. Blending them would double count: a prediction market is
mostly a weighted digest of the same polls and race ratings the model
already reads, so averaging the two is averaging a thing with itself, while
quietly importing the market's own biases (thin books on minor contracts,
favorite-longshot bias, a single large trader moving a price).

Writes data/markets_2026.json. Every figure carries its venue and its
traded volume so a $12M market and a $60K market can be told apart.

Run it directly:  python3 refresh_markets.py
"""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).parent / "data"
TIMEOUT = 30
UA = {"User-Agent": "cd-election-agent/1.0 (+https://github.com/Gh0stmahn-ai/cd-election-agent)"}

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"

# Kalshi events we read. Tickers are stable; the contents are not.
K_SENATE_CONTROL = "CONTROLS-2026"
K_BALANCE = "KXBALANCEPOWERCOMBO-27FEB"
K_POPVOTE = "KXHOUSEPOPVOTEMARGIN-27NOV03"
K_HOUSE_SEATS = "KXDHOUSESEATS-27"
K_SENATE_SEATS = "KXDSENATESEATS-27"

# Polymarket per-state Senate races, mapped onto this model's seat ids.
PM_RACES = {
    "ME-2026": "maine-senate-election-winner",
    "TX-2026": "texas-senate-election-winner",
    "MI-2026": "michigan-senate-election-winner",
    "AK-2026": "alaska-senate-election-winner",
    "OH-2026-special": "ohio-senate-election-winner",
    "IA-2026": "iowa-senate-election-winner",
    "NE-2026": "nebraska-senate-election-winner",
}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def mid_price(m):
    """Mid of the bid/ask spread, falling back to the last trade.

    The mid is the honest read of a two-sided book: the last trade can sit
    on either side of a wide spread and look like news when it is not.
    """
    bid, ask = num(m.get("yes_bid_dollars")), num(m.get("yes_ask_dollars"))
    if bid is not None and ask is not None and ask > 0:
        return (bid + ask) / 2
    return num(m.get("last_price_dollars"))


def kalshi_event(ticker):
    data = get(f"{KALSHI}/markets?event_ticker={ticker}&limit=100")
    out = []
    for m in data.get("markets", []):
        price = mid_price(m)
        if price is None:
            continue
        out.append({
            "label": m.get("yes_sub_title") or m.get("ticker"),
            "prob": round(price, 4),
            "volume": round(num(m.get("volume_fp")) or 0),
        })
    return out


def normalized(rows):
    """Scale a set of mutually exclusive outcomes to sum to 1.

    Mids across a full set of contracts routinely sum to 1.02 or so: each
    one carries half a spread. Left alone that shows up as a distribution
    that doesn't add to 100%.
    """
    total = sum(r["prob"] for r in rows)
    if not total:
        return rows
    for r in rows:
        r["prob_normalized"] = round(r["prob"] / total, 4)
    return rows


def bin_midpoint(label):
    """Center of a 'Democrats, 8 to 10%' style bin, in points of D margin."""
    text = label.lower()
    if "republican" in text:
        return -2.0          # 'Republicans win': the mass just below zero
    digits = [float(t) for t in text.replace("%", " ").replace("to", " ").split()
              if t.replace(".", "", 1).isdigit()]
    if not digits:
        return None
    if "above" in text or "+" in text:
        return digits[0] + 1.0
    return sum(digits) / len(digits)


def implied_margin(bins):
    """Volume-free expected value of the House popular vote margin."""
    total = weighted = 0.0
    for b in bins:
        centre = bin_midpoint(b["label"])
        if centre is None:
            continue
        weighted += centre * b["prob"]
        total += b["prob"]
    return round(weighted / total, 1) if total else None


def polymarket_event(slug):
    data = get(f"{GAMMA}/events?slug={slug}")
    events = data if isinstance(data, list) else []
    if not events:
        return None
    event = events[0]
    outcomes = []
    for m in event.get("markets", []):
        prices = m.get("outcomePrices")
        if not prices:
            continue
        if isinstance(prices, str):
            prices = json.loads(prices)
        yes = num(prices[0])
        if yes is None:
            continue
        outcomes.append({
            "label": m.get("groupItemTitle") or m.get("question", ""),
            "prob": round(yes, 4),
            "volume": round(num(m.get("volume")) or 0),
        })
    return {"title": event.get("title"), "volume": round(num(event.get("volume")) or 0),
            "outcomes": outcomes}


def nominees():
    """Surname -> party, per race, from the race file this model already keeps.

    Some books tag outcomes '(D)' / '(R)'; others just list candidate names
    (Alaska quotes plain 'Mary Peltola' and 'Sen. Dan Sullivan'). Matching
    against the nominees on file covers the untagged ones without hardcoding
    a second list of candidates that would drift out of date.
    """
    races = json.loads((DATA / "senate_races_2026.json").read_text())["races"]
    table = {}
    for race in races:
        by_surname = {}
        for party, key in (("D", "dem_candidate"), ("R", "rep_candidate")):
            name = (race.get(key) or "").replace("(I)", "").strip()
            if name:
                by_surname[name.split()[-1].lower()] = party
        table[race["seat_id"]] = by_surname
    return table


def party_of(label, surnames=None):
    """D / R / I for one market outcome, by party tag or by candidate name."""
    text = (label or "").lower()
    if "(d)" in text or text.strip() == "democrat":
        return "D"
    if "(r)" in text or text.strip() == "republican":
        return "R"
    if "independent" in text:
        return "I"
    for surname, party in (surnames or {}).items():
        if surname in text:
            return party
    return None


def collect():
    out = {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Shown alongside the model, never blended into it.",
        "venues": {},
        "issues": [],
    }

    def attempt(name, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - one dead endpoint shouldn't sink the rest
            out["issues"].append(f"{name}: {e}")
            print(f"  ! {name}: {e}", file=sys.stderr)
            return None

    # ---- Kalshi ----
    kalshi = {}
    senate = attempt("kalshi senate control", lambda: kalshi_event(K_SENATE_CONTROL))
    if senate:
        dem = next((r for r in senate if "democrat" in r["label"].lower()), None)
        rep = next((r for r in senate if "republic" in r["label"].lower()), None)
        if dem and rep:
            kalshi["senate_dem"] = {
                "prob": round(dem["prob"] / (dem["prob"] + rep["prob"]), 4),
                "volume": dem["volume"] + rep["volume"]}

    balance = attempt("kalshi balance of power", lambda: kalshi_event(K_BALANCE))
    if balance:
        normalized(balance)
        kalshi["joint"] = balance
        kalshi["joint_volume"] = sum(r["volume"] for r in balance)
        # House control is the sum of the two combinations with a D House.
        d_house = sum(r["prob_normalized"] for r in balance if r["label"].startswith("D-House"))
        kalshi["house_dem"] = {"prob": round(d_house, 4), "volume": kalshi["joint_volume"]}

    popvote = attempt("kalshi house popular vote", lambda: kalshi_event(K_POPVOTE))
    if popvote:
        normalized(popvote)
        kalshi["generic_ballot"] = {
            "implied_dem_margin": implied_margin(popvote),
            "volume": sum(r["volume"] for r in popvote),
            "bins": popvote}

    for key, ticker in (("house_seats", K_HOUSE_SEATS), ("senate_seats", K_SENATE_SEATS)):
        rows = attempt(f"kalshi {key}", lambda t=ticker: kalshi_event(t))
        if rows:
            kalshi[key] = {"volume": sum(r["volume"] for r in rows),
                           "bins": normalized(rows)}
    if kalshi:
        kalshi["name"] = "Kalshi"
        kalshi["url"] = "https://kalshi.com"
        out["venues"]["kalshi"] = kalshi

    # ---- Polymarket ----
    poly = {}
    for key, slug in (("house_dem", "which-party-will-win-the-house-in-2026"),
                      ("senate_dem", "which-party-will-win-the-senate-in-2026")):
        event = attempt(f"polymarket {key}", lambda s=slug: polymarket_event(s))
        if not event:
            continue
        dem = next((o for o in event["outcomes"] if "democratic" in o["label"].lower()), None)
        rep = next((o for o in event["outcomes"] if "republican" in o["label"].lower()), None)
        if dem and rep and (dem["prob"] + rep["prob"]):
            poly[key] = {"prob": round(dem["prob"] / (dem["prob"] + rep["prob"]), 4),
                         "volume": event["volume"]}

    races = {}
    surnames = attempt("senate nominees", nominees) or {}
    for seat_id, slug in PM_RACES.items():
        event = attempt(f"polymarket {seat_id}", lambda s=slug: polymarket_event(s))
        if not event or not event["outcomes"]:
            continue
        by_party = {}
        for o in event["outcomes"]:
            party = party_of(o["label"], surnames.get(seat_id))
            if party:
                by_party.setdefault(party, {"prob": 0.0, "label": o["label"]})
                by_party[party]["prob"] += o["prob"]
        total = sum(p["prob"] for p in by_party.values())
        if not total:
            # Say so rather than dropping the race silently: an untagged book
            # with an unrecognised name is exactly the case worth noticing.
            out["issues"].append(
                f"polymarket {seat_id}: no outcome matched a party "
                f"(saw: {', '.join(o['label'] for o in event['outcomes'][:4])})")
            continue
        races[seat_id] = {
            "title": event["title"],
            "volume": event["volume"],
            "slug": slug,
            # Normalized so a book quoting 49.5/49.5 reads as a coin flip.
            "parties": {k: {"prob": round(v["prob"] / total, 4), "label": v["label"]}
                        for k, v in by_party.items()},
        }
    if races:
        poly["races"] = races
    if poly:
        poly["name"] = "Polymarket"
        poly["url"] = "https://polymarket.com"
        out["venues"]["polymarket"] = poly

    return out


def main():
    payload = collect()
    venues = payload["venues"]
    if not venues:
        print("No market data could be collected; leaving the previous file in place.",
              file=sys.stderr)
        return 1

    (DATA / "markets_2026.json").write_text(json.dumps(payload, indent=1))

    for key, v in venues.items():
        bits = []
        for chamber in ("house_dem", "senate_dem"):
            if chamber in v:
                bits.append(f"{chamber.split('_')[0].title()} D {v[chamber]['prob'] * 100:.0f}%")
        if "generic_ballot" in v and v["generic_ballot"]["implied_dem_margin"] is not None:
            bits.append(f"implied ballot D{v['generic_ballot']['implied_dem_margin']:+.1f}")
        if "races" in v:
            bits.append(f"{len(v['races'])} races")
        print(f"  {v.get('name', key)}: " + ", ".join(bits))
    if payload["issues"]:
        print(f"  ({len(payload['issues'])} endpoint(s) unavailable, kept what came back)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
