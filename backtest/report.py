"""Grade the shipped model on 2018-2022 and write the numbers the site shows."""
import json, math, os, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import model
from model import RATING_MARGIN, PVI_WEIGHT, STATE_DIVISION
import backtest_house as B

OLD_MARGIN = {"solid": 18.0, "likely": 9.0, "lean": 4.5, "tossup": 0.5}
OLD_WEIGHT = {k: 0.25 for k in OLD_MARGIN}


def senate_rows():
    d = json.load(open(os.path.join(HERE, "senate_history.json")))
    rows = []
    for year, races in d.items():
        for state, r in races.items():
            lean = r["lean"]
            if lean is None:                      # a toss-up carries no lean
                if not r["pvi"]:
                    continue
                lean = "D" if r["pvi"] > 0 else "R"
            code = state.split("-")[0]
            if code not in STATE_DIVISION:
                continue
            rows.append({"id": state, "state": code, "division": STATE_DIVISION[code],
                         "rating": r["rating"], "lean": lean, "pvi": r["pvi"],
                         "actual": r["margin"], "year": year, "chamber": "senate"})
    return rows


def score(rows, margin, weight):
    tot, cnt = defaultdict(float), defaultdict(int)
    for r in rows:
        if r["pvi"] is None:
            continue
        k = (r["chamber"], r["rating"], r["lean"])
        tot[k] += r["pvi"]
        cnt[k] += 1
    mean = {k: tot[k] / cnt[k] for k in tot}
    for r in rows:
        m = margin[r["rating"]]
        k = (r["chamber"], r["rating"], r["lean"])
        adj = weight[r["rating"]] * (r["pvi"] - mean[k]) if (r["pvi"] is not None and k in mean) else 0.0
        r["pred"] = (m if r["lean"] == "D" else -m) + adj
        r["resid"] = r["actual"] - r["pred"]
    return (B.sd([r["resid"] for r in rows]),
            sum(abs(r["resid"]) for r in rows) / len(rows))


def main():
    model.REDRAWN_2026 = set()
    results = json.load(open(os.path.join(HERE, "house_history.json")))
    ratings = json.load(open(os.path.join(HERE, "house_ratings_history.json")))
    rows = [dict(r, chamber="house") for y in B.CYCLES for r in B.build(y, results, ratings)]
    rows += senate_rows()

    old_sd, old_abs = score(rows, OLD_MARGIN, OLD_WEIGHT)
    new_sd, new_abs = score(rows, RATING_MARGIN, PVI_WEIGHT)

    buckets = defaultdict(list)
    for r in rows:
        flip = 1 if r["lean"] == "D" else -1
        buckets[r["rating"]].append(flip * r["actual"])

    table = []
    for rating in ("tossup", "lean", "likely", "solid"):
        act = buckets[rating]
        table.append({"rating": rating, "n": len(act),
                      "old": OLD_MARGIN[rating], "fitted": RATING_MARGIN[rating],
                      "actual": round(sum(act) / len(act), 1),
                      "sd": round(B.sd(act), 1),
                      "held": round(sum(1 for a in act if a > 0) / len(act), 3)})

    lay_all = B.layers(rows)
    comp = [r for r in rows if r["rating"] != "solid"]
    lay_comp = B.layers(comp)

    out = {
        "cycles": B.CYCLES,
        "races": len(rows),
        "house": sum(1 for r in rows if r["chamber"] == "house"),
        "senate": sum(1 for r in rows if r["chamber"] == "senate"),
        "mean_abs_error_before": round(old_abs, 2),
        "mean_abs_error_after": round(new_abs, 2),
        "sd_before": round(old_sd, 2), "sd_after": round(new_sd, 2),
        "buckets": table,
        "layers_all": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in lay_all.items()},
        "layers_competitive": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in lay_comp.items()},
    }
    json.dump(out, open(os.path.join(HERE, "backtest_summary.json"), "w"), indent=1)
    # the site reads it from data/, so keep the two copies in step
    json.dump(out, open(os.path.join(os.path.dirname(HERE), "data",
                                     "backtest_2018_2022.json"), "w"), indent=1)

    print(f"races graded: {out['races']}  (House {out['house']}, Senate {out['senate']})")
    print(f"mean absolute error: {old_abs:.2f} -> {new_abs:.2f} points")
    print(f"residual SD        : {old_sd:.2f} -> {new_sd:.2f} points\n")
    print(f"  {'rating':8}{'n':>5}{'old':>7}{'fitted':>8}{'actual':>8}{'sd':>7}{'held':>7}")
    for t in table:
        print(f"  {t['rating']:8}{t['n']:5d}{t['old']:7.1f}{t['fitted']:8.1f}"
              f"{t['actual']:8.1f}{t['sd']:7.1f}{t['held']:7.0%}")
    print("\nerror layers (competitive races):")
    for k in ("national_sd", "division_sd", "state_sd", "seat_sd"):
        print(f"  {k:14} {lay_comp[k]:.2f}")
    print("  national bias by cycle: " + "  ".join(
        f"{y} {lay_comp['national_bias'][y]:+.2f}" for y in B.CYCLES))


if __name__ == "__main__":
    main()
