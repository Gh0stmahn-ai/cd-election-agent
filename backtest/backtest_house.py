"""Run the shipped House baseline against 2018, 2020 and 2022 and grade it.

Everything the live model does to turn a rating into an expected margin is
imported from model.py rather than re-implemented, so what gets graded is the
forecast we actually publish. The backtest answers three things:

  1. Is RATING_MARGIN right?  (mean actual margin inside each rating bucket)
  2. Does the PVI adjustment help?  (residual SD with it and without)
  3. How is the leftover error shaped?  (national / division / state / seat)
"""
import json, math, os, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import model
from model import RATING_MARGIN, STATE_DIVISION

CYCLES = ["2018", "2020", "2022"]
PARTY_LEAN = {"Democratic": "D", "Republican": "R"}


def build(year, results, ratings):
    """One district record per contested seat, with the rating the model saw."""
    rated = ratings[year]
    rows = []
    for d in results[year]:
        if not d["contested"] or d["margin"] is None:
            continue
        district = d["district"]
        state = district.split("-")[0]
        if state not in STATE_DIVISION:
            continue
        r = rated.get(district)
        if r:
            rating, lean = r["rating"], r["lean"]
        else:
            # Absent from the ratings article means every rater called it safe.
            rating, lean = "solid", PARTY_LEAN.get(d["incumbent_party"])
        if lean is None:
            # A toss-up with no stated lean, or an open safe seat whose holder
            # we could not read: fall back to the district's own partisanship.
            if d["pvi"] is None or d["pvi"] == 0:
                continue
            lean = "D" if d["pvi"] > 0 else "R"
        rows.append({"id": district, "state": state, "division": STATE_DIVISION[state],
                     "rating": rating, "lean": lean, "pvi": d["pvi"],
                     "actual": d["margin"], "year": year})
    return rows


def predict(rows, use_pvi):
    pvi = {r["id"]: r["pvi"] for r in rows if r["pvi"] is not None} if use_pvi else {}
    adj = model.pvi_adjustments(rows, pvi)
    for r, a in zip(rows, adj):
        base = RATING_MARGIN[r["rating"]]
        r["pred"] = (base if r["lean"] == "D" else -base) + a
        r["resid"] = r["actual"] - r["pred"]


def sd(vals):
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


def layers(rows):
    """Split residuals into national / division / state / seat."""
    nat = {y: sum(r["resid"] for r in rows if r["year"] == y) /
                max(1, sum(1 for r in rows if r["year"] == y)) for y in CYCLES}
    cent = [dict(r, c=r["resid"] - nat[r["year"]]) for r in rows]

    same_state, same_div = [], []
    for y in CYCLES:
        yr = [r for r in cent if r["year"] == y]
        by_state, by_div = defaultdict(list), defaultdict(list)
        for r in yr:
            by_state[r["state"]].append(r["c"])
            by_div[r["division"]].append((r["state"], r["c"]))
        for vals in by_state.values():
            for i in range(len(vals)):
                for j in range(i + 1, len(vals)):
                    same_state.append(vals[i] * vals[j])
        for vals in by_div.values():
            for i in range(len(vals)):
                for j in range(i + 1, len(vals)):
                    if vals[i][0] != vals[j][0]:
                        same_div.append(vals[i][1] * vals[j][1])

    var = sd([r["c"] for r in cent]) ** 2
    cov_div = max(sum(same_div) / len(same_div), 0.0)
    cov_state = max(sum(same_state) / len(same_state), 0.0)
    div = cov_div
    state = max(cov_state - cov_div, 0.0)
    seat = max(var - div - state, 0.0)
    return {
        "national_bias": nat,
        "national_sd": sd(list(nat.values())),
        "conditional_sd": math.sqrt(var),
        "division_sd": math.sqrt(div),
        "state_sd": math.sqrt(state),
        "seat_sd": math.sqrt(seat),
    }


def main():
    model.REDRAWN_2026 = set()          # those exclusions are a 2026 map problem
    results = json.load(open(os.path.join(HERE, "house_history.json")))
    ratings = json.load(open(os.path.join(HERE, "house_ratings_history.json")))
    rows = [r for y in CYCLES for r in build(y, results, ratings)]
    print(f"seats graded: {len(rows)}  ({', '.join(f'{y}: ' + str(sum(1 for r in rows if r['year'] == y)) for y in CYCLES)})")

    for use_pvi in (False, True):
        predict(rows, use_pvi)
        tag = "rating + PVI" if use_pvi else "rating only "
        print(f"  {tag}: residual SD {sd([r['resid'] for r in rows]):5.2f}   "
              f"mean abs {sum(abs(r['resid']) for r in rows) / len(rows):5.2f}")

    predict(rows, True)
    print("\nrating bucket calibration (D-favoured sign-flipped to the favourite):")
    print(f"  {'rating':8} {'n':>4} {'model':>7} {'actual':>7} {'sd':>6} {'held':>6}")
    buckets = defaultdict(list)
    for r in rows:
        flip = 1 if r["lean"] == "D" else -1
        buckets[r["rating"]].append((flip * r["actual"], flip * r["pred"]))
    for rating in ("tossup", "lean", "likely", "solid"):
        vals = buckets[rating]
        if not vals:
            continue
        act = [a for a, _ in vals]
        held = sum(1 for a in act if a > 0) / len(act)
        print(f"  {rating:8} {len(vals):4d} {RATING_MARGIN[rating]:7.1f} "
              f"{sum(act) / len(act):7.1f} {sd(act):6.1f} {held:6.0%}")

    lay = layers(rows)
    print("\nerror structure (points):")
    print(f"  national bias by cycle: " + "  ".join(f"{y} {lay['national_bias'][y]:+.2f}" for y in CYCLES))
    print(f"  national SD           : {lay['national_sd']:.2f}   (model uses 2.2 on Election Day)")
    print(f"  per-seat, given the national miss: {lay['conditional_sd']:.2f}")
    print(f"    division layer      : {lay['division_sd']:.2f}   (model {model.DIVISION_SD})")
    print(f"    state layer         : {lay['state_sd']:.2f}   (model {model.STATE_SD})")
    print(f"    seat layer          : {lay['seat_sd']:.2f}   (model {model.HOUSE_SEAT_SD:.2f})")
    json.dump({"n": len(rows), "layers": lay}, open(os.path.join(HERE, "house_backtest.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
