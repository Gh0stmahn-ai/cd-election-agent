"""Do polls and ratings miss along education, all in the same direction in a year?

In 2016 and 2020 the misses ran the same way in states with many white voters
without a college degree, wherever those states were. If that is a standing
pattern, the forecast should let close races in such states miss together;
if it is not, adding it would only add noise. This measures it.

    CENSUS_API_KEY=... python3 backtest/education.py

1. Each state's non-college white share of adults 25 and older, from the
   Census Bureau's American Community Survey (2023 5-year estimates):
   (C15002H_001E - C15002H_006E - C15002H_011E) / B15002_001E.
   Saved to backtest/state_education.json.
2. For every Senate and governor race of 2014-2024 with a Cook rating: the
   miss is the actual margin minus what the rating implies (the model's own
   RATING_MARGIN), with each cycle's average miss removed (that part is the
   national error, which the model already has).
3. Within each cycle, the slope of the miss on the state's (standardized)
   non-college white share, alone and holding Cook PVI fixed. A standing
   correlated factor shows up as slopes that differ from cycle to cycle by
   more than their own sampling error. The size of that excess is the factor
   the model would carry, in points per standard deviation of the share.

Writes backtest/education_fit.json.
"""
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import numpy as np            # noqa: E402
import model                  # noqa: E402
import results_feeds as F     # noqa: E402

ACS = "https://api.census.gov/data/2023/acs/acs5"
VARS = ["NAME", "C15002H_001E", "C15002H_006E", "C15002H_011E", "B15002_001E"]
CYCLES = ("2014", "2016", "2018", "2020", "2022", "2024")


def fetch_shares(key):
    url = ACS + "?" + urllib.parse.urlencode({"get": ",".join(VARS), "for": "state:*", "key": key})
    req = urllib.request.Request(url, headers={"User-Agent": "cd-election-agent/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        final, body = resp.geturl(), resp.read().decode("utf-8", "replace")
    try:
        rows = json.loads(body)
    except ValueError:
        # The Census API answers a bad or not-yet-activated key by redirecting
        # to an HTML page (invalid_key.html, missing_key.html) instead of an error.
        page = final.split("?")[0].rsplit("/", 1)[-1]
        raise SystemExit(f"The Census API did not return data (it sent {page}). "
                         "If that is invalid_key.html, the key has not been activated yet: "
                         "click the link in the Census email, then run this again.")
    head, out = rows[0], {}
    for row in rows[1:]:
        r = dict(zip(head, row))
        code = F.STATE_CODES.get(r["NAME"])
        if not code:
            continue                       # DC and Puerto Rico have no Senate or governor race here
        white, white_ba_m, white_ba_f, adults = (float(r[v]) for v in VARS[1:])
        out[code] = round((white - white_ba_m - white_ba_f) / adults, 4)
    return out


def misses():
    rows = []
    for fname, chamber in (("senate_history.json", "senate"), ("governor_history.json", "governor")):
        for year, races in json.load(open(os.path.join(HERE, fname))).items():
            if year not in CYCLES:
                continue
            for st, r in races.items():
                if r.get("rating") not in model.RATING_MARGIN[chamber] or r.get("margin") is None:
                    continue
                pvi = r.get("pvi")
                fav = r.get("lean") or r.get("held_by") or (("D" if pvi > 0 else "R") if pvi else None)
                if fav not in ("D", "R"):
                    continue
                expected = model.RATING_MARGIN[chamber][r["rating"]] * (1 if fav == "D" else -1)
                rows.append({"year": year, "chamber": chamber, "state": st, "rating": r["rating"],
                             "pvi": pvi, "miss": r["margin"] - expected})
    # The national part of each cycle's miss, per chamber, comes out first.
    for key in {(r["year"], r["chamber"]) for r in rows}:
        group = [r for r in rows if (r["year"], r["chamber"]) == key]
        m = sum(r["miss"] for r in group) / len(group)
        for r in group:
            r["miss"] -= m
    return rows


def slope(x, y, control=None):
    """OLS slope of y on x (optionally holding `control` fixed) and its standard error."""
    cols = [np.ones(len(x)), x] + ([control] if control is not None else [])
    X = np.column_stack(cols)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    dof = len(y) - X.shape[1]
    if dof <= 0:
        return None, None
    cov = (resid @ resid / dof) * np.linalg.inv(X.T @ X)
    return float(beta[1]), float(math.sqrt(cov[1, 1]))


def factor(slopes, ses):
    """Spread of the true slopes across cycles beyond their sampling error,
    and a chi-square test that the slopes differ at all."""
    b, s = np.array(slopes), np.array(ses)
    w = 1 / s ** 2
    pooled = float((w * b).sum() / w.sum())
    q = float((w * (b - pooled) ** 2).sum())
    k = len(b)
    # DerSimonian-Laird estimate of the between-cycle variance.
    tau2 = max(0.0, (q - (k - 1)) / (w.sum() - (w ** 2).sum() / w.sum()))
    from math import exp, lgamma
    # Upper tail of chi-square(k-1) at q, by series (no scipy needed).
    a, xx = (k - 1) / 2, q / 2
    term = summ = 1 / a
    n = 1
    while term > 1e-12 * summ and n < 500:
        term *= xx / (a + n)
        summ += term
        n += 1
    lower = summ * exp(-xx + a * math.log(xx) - lgamma(a)) if xx > 0 else 0.0
    return {"pooled_slope": round(pooled, 3), "q": round(q, 2), "df": k - 1,
            "p_slopes_differ": round(max(0.0, 1 - lower), 4), "factor_sd": round(math.sqrt(tau2), 3)}


def main():
    key = (os.environ.get("CENSUS_API_KEY") or "").strip().strip('"').strip("'").strip()
    if key:
        # The key itself is never printed; only whether it looks like one.
        # Census keys are 40 hexadecimal characters.
        looks = bool(re.fullmatch(r"[0-9a-fA-F]{40}", key))
        print(f"Census key: {len(key)} characters, {'looks like a Census key' if looks else 'NOT the usual 40 hex characters'}")
    path = os.path.join(HERE, "state_education.json")
    if key:
        shares = fetch_shares(key)
        json.dump({"source": "ACS 2023 5-year, C15002H and B15002, api.census.gov",
                   "measure": "non-Hispanic white adults 25+ without a bachelor's degree, share of all adults 25+",
                   "states": shares}, open(path, "w"), indent=1)
    else:
        shares = json.load(open(path))["states"]
    vals = np.array(list(shares.values()))
    mu, sd = float(vals.mean()), float(vals.std())
    rows = [r for r in misses() if r["state"] in shares]
    for r in rows:
        r["z"] = (shares[r["state"]] - mu) / sd
    report = {"share_mean": round(mu, 4), "share_sd": round(sd, 4), "sets": {}}
    for label, keep in (("competitive", lambda r: r["rating"] != "solid"), ("all", lambda r: True)):
        for control in (False, True):
            name = f"{label}{', holding PVI fixed' if control else ''}"
            per, slopes, ses = [], [], []
            for year in CYCLES:
                g = [r for r in rows if r["year"] == year and keep(r) and (not control or r["pvi"] is not None)]
                if len(g) < 10:
                    continue
                x = np.array([r["z"] for r in g])
                y = np.array([r["miss"] for r in g])
                c = np.array([r["pvi"] for r in g]) if control else None
                b, se = slope(x, y, c)
                if b is None:
                    continue
                per.append({"year": year, "n": len(g), "slope": round(b, 2), "se": round(se, 2)})
                slopes.append(b)
                ses.append(se)
            fit = factor(slopes, ses) if len(slopes) >= 3 else None
            report["sets"][name] = {"cycles": per, "fit": fit}
            print(f"\n{name}:")
            for p in per:
                print(f"  {p['year']}: n={p['n']:3d}  {p['slope']:+.2f} pts per SD (se {p['se']:.2f})")
            if fit:
                print(f"  slopes differ across cycles: p = {fit['p_slopes_differ']}; "
                      f"factor sd {fit['factor_sd']} pts per SD of the share "
                      f"(pooled slope {fit['pooled_slope']:+.2f})")
    json.dump(report, open(os.path.join(HERE, "education_fit.json"), "w"), indent=1)
    print("\nwrote backtest/education_fit.json")


if __name__ == "__main__":
    main()
