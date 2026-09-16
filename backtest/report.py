"""Fit the model's constants on past elections, then grade the model on them.

Run this after changing anything in model.py: it compares the constants as they
stand against the ones the project started with, so an edit that makes things
worse says so immediately.
"""
import json, math, os, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import model
import races as R

RATINGS = ("tossup", "lean", "likely", "solid")
FLAT = {"solid": 18.0, "likely": 9.0, "lean": 4.5, "tossup": 0.5}
OLD_MARGIN = {"house": FLAT, "senate": FLAT}
OLD_WEIGHT = {k: 0.25 for k in RATINGS}


# ------------------------------------------------------------------ helpers
def mean(v):
    return sum(v) / len(v) if v else 0.0


def sd(v):
    if len(v) < 2:
        return 0.0
    m = mean(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / len(v))


def slope(rows, x="pvi", y="actual"):
    g = [r for r in rows if r[x] is not None]
    if len(g) < 8:
        return None, None, None
    mx, my = mean([r[x] for r in g]), mean([r[y] for r in g])
    sxx = sum((r[x] - mx) ** 2 for r in g)
    if not sxx:
        return None, None, None
    b = sum((r[x] - mx) * (r[y] - my) for r in g) / sxx
    before = sd([r[y] for r in g])
    after = math.sqrt(mean([(r[y] - my - b * (r[x] - mx)) ** 2 for r in g]))
    return b, before, after


def normal_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# ------------------------------------------------------------------ scoring
def predict(rows, margin, weight):
    """Give every race the baseline the model would have given it.

    The PVI adjustment is centred inside each (chamber, rating, lean) bucket,
    exactly as model.pvi_adjustments does, so a bucket's average stays where
    the rater put it and only the spread inside it is used.
    """
    tot, cnt = defaultdict(float), defaultdict(int)
    for r in rows:
        if r["pvi"] is None:
            continue
        k = (r["chamber"], r["rating"], r["lean"])
        tot[k] += r["pvi"]
        cnt[k] += 1
    centre = {k: tot[k] / cnt[k] for k in tot}
    for r in rows:
        k = (r["chamber"], r["rating"], r["lean"])
        m = margin[r["chamber"]][r["rating"]]
        adj = weight[r["rating"]] * (r["pvi"] - centre[k]) if (r["pvi"] is not None and k in centre) else 0.0
        r["pred"] = (m if r["lean"] == "D" else -m) + adj
        r["resid"] = r["actual"] - r["pred"]


def layers(rows, cycles):
    """Split residuals into a national layer, a state layer and a race layer."""
    bias = {}
    for y in cycles:
        yr = [r["resid"] for r in rows if r["year"] == y]
        if yr:
            bias[y] = mean(yr)
    cent = [(r, r["resid"] - bias[r["year"]]) for r in rows if r["year"] in bias]

    same_state, same_div = [], []
    for y in bias:
        by_state, by_div = defaultdict(list), defaultdict(list)
        for r, c in cent:
            if r["year"] != y:
                continue
            by_state[r["state"]].append(c)
            by_div[r["division"]].append((r["state"], c))
        for vals in by_state.values():
            for i in range(len(vals)):
                for j in range(i + 1, len(vals)):
                    same_state.append(vals[i] * vals[j])
        for vals in by_div.values():
            for i in range(len(vals)):
                for j in range(i + 1, len(vals)):
                    if vals[i][0] != vals[j][0]:
                        same_div.append(vals[i][1] * vals[j][1])

    var = mean([c ** 2 for _, c in cent])
    cov_div = max(mean(same_div), 0.0) if same_div else 0.0
    cov_state = max(mean(same_state), 0.0) if same_state else 0.0
    state = max(cov_state - cov_div, 0.0)
    return {
        "national_bias": {k: round(v, 2) for k, v in bias.items()},
        "national_sd": sd(list(bias.values())),
        "conditional_sd": math.sqrt(var),
        "division_sd": math.sqrt(cov_div),
        "state_sd": math.sqrt(state),
        "race_sd": math.sqrt(max(var - cov_div - state, 0.0)),
    }


def calibration(rows, bins=8):
    """Predicted win probability against how often it actually happened."""
    pts = []
    for r in rows:
        # A race's full predictive spread is the national miss, the state miss
        # and its own noise. race_sd() is only the last of those -- it is what
        # is left AFTER the state layer is taken out -- so the sigma here is
        # built from TOTAL_SD, which already contains the state layer.
        total = math.sqrt(model.national_error_sd(0) ** 2
                          + model.TOTAL_SD[r["chamber"]][r["rating"]] ** 2)
        p = normal_cdf(r["pred"] / total)             # chance the Democrat wins
        pts.append((p, 1.0 if r["actual"] > 0 else 0.0))
    pts = [(max(p, 1 - p), hit if p >= 0.5 else 1 - hit) for p, hit in pts]
    pts.sort()
    out, n = [], len(pts)
    for i in range(bins):
        chunk = pts[i * n // bins:(i + 1) * n // bins]
        if not chunk:
            continue
        out.append({"n": len(chunk),
                    "predicted": round(mean([p for p, _ in chunk]), 4),
                    "observed": round(mean([h for _, h in chunk]), 4)})
    brier = mean([(p - h) ** 2 for p, h in pts])
    return out, brier


# ------------------------------------------------------------------ main
def main():
    rows = R.all_races()
    comp = [r for r in rows if r["competitive"]]
    solid = [r for r in rows if not r["competitive"] and r["full"]]
    graded = comp + solid

    print(f"races: {len(graded)}  ({len(comp)} competitive across {len(R.ALL)} cycles,"
          f" {len(solid)} safe across {len(R.FULL)})")

    predict(graded, OLD_MARGIN, OLD_WEIGHT)
    old = (sd([r["resid"] for r in graded]), mean([abs(r["resid"]) for r in graded]))
    predict(graded, model.RATING_MARGIN, model.PVI_WEIGHT)
    new = (sd([r["resid"] for r in graded]), mean([abs(r["resid"]) for r in graded]))
    print(f"mean absolute error: {old[1]:.2f} -> {new[1]:.2f} points")
    print(f"residual SD        : {old[0]:.2f} -> {new[0]:.2f} points\n")

    # ---- bucket calibration
    table = []
    print(f"  {'rating':8}{'n':>5}{'old':>7}{'H':>6}{'S':>6}{'actual':>8}{'sd':>7}{'held':>7}")
    for rating in RATINGS:
        pool = solid if rating == "solid" else comp
        g = [r for r in pool if r["rating"] == rating]
        if not g:
            continue
        act = [(1 if r["lean"] == "D" else -1) * r["actual"] for r in g]
        table.append({"rating": rating, "n": len(g), "old": FLAT[rating],
                      "fitted_house": model.RATING_MARGIN["house"][rating],
                      "fitted_senate": model.RATING_MARGIN["senate"][rating],
                      "actual": round(mean(act), 1), "sd": round(sd(act), 1),
                      "held": round(mean([1.0 if a > 0 else 0.0 for a in act]), 3)})
        t = table[-1]
        print(f"  {rating:8}{t['n']:5d}{t['old']:7.1f}{t['fitted_house']:6.1f}"
              f"{t['fitted_senate']:6.1f}{t['actual']:8.1f}{t['sd']:7.1f}{t['held']:7.0%}")

    # ---- how much PVI is worth inside each bucket
    print("\n  PVI slope inside each bucket (spread before -> after):")
    slopes = {}
    for rating in RATINGS:
        pool = solid if rating == "solid" else comp
        parts = []
        for lean in ("D", "R"):
            g = [r for r in pool if r["rating"] == rating and r["lean"] == lean]
            b, before, after = slope(g)
            if b is not None:
                parts.append((len(g), b, before, after))
        if not parts:
            continue
        n = sum(p[0] for p in parts)
        b = sum(p[0] * p[1] for p in parts) / n
        slopes[rating] = round(b, 2)
        print(f"  {rating:8}{n:5d}  slope {b:5.2f}   "
              + "  ".join(f"{p[2]:.1f}->{p[3]:.1f}" for p in parts))

    # ---- error structure, on the races whose outcome was ever in doubt
    lay = layers(comp, R.ALL)
    print("\n  error layers, competitive races only:")
    print("   national bias by cycle: " + "  ".join(
        f"{y} {lay['national_bias'][y]:+.1f}" for y in R.ALL if y in lay["national_bias"]))
    for k in ("national_sd", "division_sd", "state_sd", "race_sd", "conditional_sd"):
        print(f"   {k:16} {lay[k]:.2f}")

    # ---- race-level spread per rating, net of the state layer
    print("\n  race-level SD by rating (net of the national and state layers):")
    per_rating = {}
    state_var = lay["state_sd"] ** 2 + lay["division_sd"] ** 2
    for rating in RATINGS:
        pool = solid if rating == "solid" else comp
        cycles = R.FULL if rating == "solid" else R.ALL
        g = [r for r in pool if r["rating"] == rating]
        if len(g) < 10:
            continue
        sub = layers(g, cycles)
        total = sub["conditional_sd"]
        per_rating[rating] = {"n": len(g), "conditional_sd": round(total, 2),
                              "race_sd": round(math.sqrt(max(total ** 2 - state_var, 0.0)), 2)}
        print(f"   {rating:8}{len(g):5d}  per-race {total:5.2f}"
              f"   net of state {per_rating[rating]['race_sd']:5.2f}"
              f"   (model {model.TOTAL_SD['house'][rating]:.2f} house,"
              f" {model.TOTAL_SD['senate'][rating]:.2f} senate)")

    curve, brier = calibration(comp)
    print(f"\n  calibration on {len(comp)} competitive races, Brier {brier:.4f}")
    for c in curve:
        print(f"   n={c['n']:4d}  said {c['predicted']:.0%}  happened {c['observed']:.0%}")

    out = {
        "cycles": R.ALL, "full_cycles": R.FULL,
        "races": len(graded), "competitive": len(comp), "safe": len(solid),
        "house": sum(1 for r in graded if r["chamber"] == "house"),
        "senate": sum(1 for r in graded if r["chamber"] == "senate"),
        "mean_abs_error_before": round(old[1], 2), "mean_abs_error_after": round(new[1], 2),
        "sd_before": round(old[0], 2), "sd_after": round(new[0], 2),
        "buckets": table, "pvi_slopes": slopes,
        "layers": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in lay.items()},
        "race_sd_by_rating": per_rating,
        "calibration": curve, "brier": round(brier, 4),
    }
    json.dump(out, open(os.path.join(HERE, "backtest_summary.json"), "w"), indent=1)
    json.dump(out, open(os.path.join(os.path.dirname(HERE), "data",
                                     "backtest_2018_2022.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
