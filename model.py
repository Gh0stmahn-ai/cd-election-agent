"""
2026 midterm forecasting model (v2).

Four layers, each documented and inspectable:

1. National environment. A blend of the generic-ballot polling average and a
   fundamentals prior built from presidential approval, the historical
   midterm penalty, and an economic index (gas prices, inflation, consumer
   sentiment, real wages, jobs, growth, rates, stocks). The polls get more
   weight as Election Day approaches.

2. Race baselines. Every Senate race and all 435 House districts start from
   a published race rating (Cook Political Report), converted to an
   expected Democratic margin. Ratings already bake in the environment at
   the time they were set, so each seat is shifted only by how far the
   national environment has moved since then.

3. Atmospherics (Senate toss-up/lean races only). A bounded news-and-
   campaign-trail adjustment from data/atmospherics_2026.json, capped at
   +/-0.08 win probability, kept separate so its effect is visible.

4. Monte Carlo simulation with correlated error. Each of 20,000 simulated
   elections draws ONE national polling miss shared by every race in both
   chambers, plus independent race-level noise - so misses move similar
   races together instead of cancelling out, and House and Senate outcomes
   are correlated the way they are in reality.

Control rules: Democrats need 218 House seats; in the Senate they need 51
because Vice President Vance breaks a 50-50 tie. In Nebraska the challenger
is independent Dan Osborn, who says he would not caucus with either party,
so an Osborn win is counted as "not Republican" but not as a Democratic
seat for control purposes.
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

DATA_DIR = Path(__file__).parent / "data"
ITER_DIR = Path(__file__).parent / "iterations"

ELECTION_DATE = date(2026, 11, 3)
MODEL_VERSION = 2

# Common random numbers: every run draws the same dice.
#
# With 20,000 draws and a fresh seed each day, two runs on IDENTICAL data
# disagreed by up to 1.8 points, which is larger than most real daily moves.
# The trend line was partly reporting luck. Fixing the seed means a change in
# the forecast can only come from a change in the data, and raising the draw
# count shrinks what is left to under 0.2 points.
SEED = 20261103
N_SIMS = 100_000

# Expected margin (points) for the favoured party, by rating. Calibrated so a
# Lean seat wins ~3 in 4, Likely ~19 in 20, Solid essentially always, and a
# Toss-up tilts a hair toward the party Cook files it under (the holder).
RATING_MARGIN = {"solid": 18.0, "likely": 9.0, "lean": 4.5, "tossup": 0.5}

# How a polling miss is shaped.
#
# A miss is not one national number plus independent local noise. When a model
# is wrong in Iowa it tends to be wrong in Ohio the same direction, and a state
# that breaks late breaks late for its Senate race and all of its House seats
# at once. So the error is split into four nested layers: national, census
# division, state, and the seat itself.
#
# The layers are sized so each seat's TOTAL uncertainty is unchanged from the
# uncorrelated version (5.0 points for a district, 5.5 for a Senate race).
# Nothing here widens the error bars; it only makes the errors cluster the way
# real ones do, which is what moves a probability of CONTROL.
#
# These three splits are assumptions until the backtest fits them.
# How much of a seat's distance from its rating-mates' partisan lean carries
# into its expected margin. Unfitted until the backtest; 0.25 means a district
# ten points more Republican than its rating-mates starts 2.5 points worse.
# How far a well-polled Senate race is allowed to follow its polls rather than
# its rating. Reached only when several aggregators cover the race; a thinly
# polled one keeps leaning on the rating instead of lurching on one survey.
# Unfitted until the backtest.
SENATE_POLL_WEIGHT_FAR = 0.50    # 120+ days out, polls are informative, not final
SENATE_POLL_WEIGHT_NEAR = 0.85   # on Election Day, the polls are the story
SENATE_POLL_FULL_AT = 4          # sources needed for the full weight

PVI_WEIGHT = 0.25

# States that redrew their map mid-decade for 2026. Cook's index still
# describes the OLD lines there, so applying it would be worse than applying
# nothing: Missouri's 5th shows D+12 from a Kansas City seat that the new map
# deliberately broke up, and using that would pull a seat drawn to be
# Republican six points toward the Democrats. These states keep the rating
# alone until an index for the new lines exists.
REDRAWN_2026 = {"TX", "CA", "FL", "OH", "NC", "MO", "UT", "TN", "LA", "AL"}

DIVISION_SD = 1.5            # New England misses together, the Mountain West misses together
STATE_SD = 2.5               # one state's Senate race and its districts share a miss
HOUSE_TOTAL_SD = 5.0         # district-level noise (candidates, local factors)
SENATE_TOTAL_SD = 5.5        # statewide candidate effects are larger


def _residual_sd(total):
    """What is left for the seat itself once the shared layers are removed."""
    shared = DIVISION_SD ** 2 + STATE_SD ** 2
    if total ** 2 <= shared:
        raise ValueError("shared error layers exceed a seat's total uncertainty")
    return (total ** 2 - shared) ** 0.5


HOUSE_SEAT_SD = _residual_sd(HOUSE_TOTAL_SD)
SENATE_SEAT_SD = _residual_sd(SENATE_TOTAL_SD)

# Census divisions. Finer than the four regions and closer to how misses have
# actually clustered: the 2020 error was an upper-Midwest story, not a
# "Midwest" one.
STATE_DIVISION = {
    "CT": "New England", "ME": "New England", "MA": "New England",
    "NH": "New England", "RI": "New England", "VT": "New England",
    "NJ": "Mid-Atlantic", "NY": "Mid-Atlantic", "PA": "Mid-Atlantic",
    "IL": "E North Central", "IN": "E North Central", "MI": "E North Central",
    "OH": "E North Central", "WI": "E North Central",
    "IA": "W North Central", "KS": "W North Central", "MN": "W North Central",
    "MO": "W North Central", "NE": "W North Central", "ND": "W North Central",
    "SD": "W North Central",
    "DE": "South Atlantic", "FL": "South Atlantic", "GA": "South Atlantic",
    "MD": "South Atlantic", "NC": "South Atlantic", "SC": "South Atlantic",
    "VA": "South Atlantic", "WV": "South Atlantic", "DC": "South Atlantic",
    "AL": "E South Central", "KY": "E South Central", "MS": "E South Central",
    "TN": "E South Central",
    "AR": "W South Central", "LA": "W South Central", "OK": "W South Central",
    "TX": "W South Central",
    "AZ": "Mountain", "CO": "Mountain", "ID": "Mountain", "MT": "Mountain",
    "NV": "Mountain", "NM": "Mountain", "UT": "Mountain", "WY": "Mountain",
    "AK": "Pacific", "CA": "Pacific", "HI": "Pacific", "OR": "Pacific", "WA": "Pacific",
}
DIVISIONS = sorted(set(STATE_DIVISION.values()))
SENATE_ENV_SENSITIVITY = 0.8  # Senate races track the national mood a bit less
ATMOSPHERICS_CAP = 0.08
ATMOSPHERICS_PTS_PER_PROB = 15.0  # near 50%, 0.08 win prob ~= 1.2 pts of margin

# Fundamentals prior (Dem margin when the president is a Republican):
#   midterm baseline + approval term + economic term.
# Rough historical check (president's-party House vote margin): 2006, 2010,
# 2014, 2018 all land within ~2 pts; 2022 (Dobbs) is the known miss.
MIDTERM_BASELINE = 3.5        # out-party edge in a neutral midterm
APPROVAL_PTS_PER_NET = 0.20   # each point of net approval = 0.2 pts of margin
ECON_MAX_PTS = 3.0            # economic index of -1 = +3 pts for the out-party


def _load(name):
    with open(DATA_DIR / name) as f:
        return json.load(f)


def load_senate_races():
    return _load("senate_races_2026.json")


def load_generic_ballot():
    return _load("generic_ballot_2026.json")


def load_house_districts():
    return _load("house_districts_2026.json")


def load_fundamentals():
    return _load("fundamentals_2026.json")


def load_atmospherics():
    path = DATA_DIR / "atmospherics_2026.json"
    if not path.exists():
        return {}
    with open(path) as f:
        payload = json.load(f)
    return {a["seat_id"]: a for a in payload.get("assessments", [])}


def days_to_election(today=None):
    today = today or datetime.now(timezone.utc).date()
    return max((ELECTION_DATE - today).days, 0)


# ------------------------------------------------------------ fundamentals
def indicator_score(ind):
    """Score one indicator -1..+1: positive helps the president's party."""
    sc = ind.get("score", {})
    method = sc.get("method", "none")
    if method == "none" or not sc.get("weight"):
        return None
    if method == "yoy_pct":
        base = ind.get("compare_value") or 0
        if not base:
            return None
        x = (ind["value"] / base - 1) * 100
    else:
        x = ind.get(sc.get("field", "value"))
        if x is None:
            return None
    raw = (x - sc["neutral"]) / sc["scale"]
    return max(-1.0, min(1.0, raw))


def economic_index(fund):
    comps, total_w, total = [], 0.0, 0.0
    for ind in fund["indicators"]:
        s = indicator_score(ind)
        w = ind.get("score", {}).get("weight", 0.0)
        if s is None or not w:
            continue
        comps.append({"id": ind["id"], "label": ind["label"], "score": round(s, 3), "weight": w})
        total += s * w
        total_w += w
    index = total / total_w if total_w else 0.0
    for c in comps:
        c["contribution"] = round(c["score"] * c["weight"] / total_w, 3)
    return round(index, 3), comps


def fundamentals_prior(fund):
    sign = 1.0 if fund.get("president_party", "R") == "R" else -1.0
    approval = next(p for p in fund["political"] if p["id"] == "approval")
    net = approval["value_net"]
    index, comps = economic_index(fund)
    approval_pts = -APPROVAL_PTS_PER_NET * net * sign
    econ_pts = -index * ECON_MAX_PTS * sign
    baseline = MIDTERM_BASELINE * sign
    return {
        "midterm_baseline_pts": round(baseline, 2),
        "approval_net": net,
        "approval_pts": round(approval_pts, 2),
        "economic_index": index,
        "economic_pts": round(econ_pts, 2),
        "economic_components": comps,
        "prior_dem_margin": round(baseline + approval_pts + econ_pts, 2),
    }


def national_environment(today=None):
    gb = load_generic_ballot()
    fund = load_fundamentals()
    prior = fundamentals_prior(fund)
    days = days_to_election(today)
    w_poll = max(0.5, min(0.95, 1 - days / 300))
    env = w_poll * gb["dem_margin_points"] + (1 - w_poll) * prior["prior_dem_margin"]
    return {
        "generic_ballot": gb["dem_margin_points"],
        "generic_ballot_as_of": gb.get("as_of"),
        "poll_weight": round(w_poll, 3),
        "fundamentals": prior,
        "dem_margin": round(env, 2),
        "days_to_election": days,
        "national_error_sd": round(national_error_sd(days), 2),
    }


def national_error_sd(days):
    # ~2.2 pts irreducible polling miss on Election Day, growing to ~3.8
    # four months out as there is more time for the environment to move
    return 2.2 + 1.6 * min(days, 120) / 120


# -------------------------------------------------------------- simulation
def _baseline(rating, lean):
    m = RATING_MARGIN[rating]
    return m if lean == "D" else -m


def load_senate_polls():
    """State polling averages per Senate race, or an empty map if never fetched."""
    try:
        return _load("senate_polls_2026.json")["races"]
    except (FileNotFoundError, KeyError, ValueError):
        return {}


def poll_weight(entry, days=None):
    """How much of a race's baseline comes from polls rather than its rating.

    Two things govern it. How much polling there is, so one lonely survey
    cannot throw a race; and how close Election Day is, because a lead in
    September is a weaker claim about November than the same lead in late
    October. This mirrors how the generic ballot is already weighted against
    the fundamentals.
    """
    n = entry.get("n_used") or 0
    coverage = min(1.0, n / SENATE_POLL_FULL_AT)
    if days is None:
        days = days_to_election()
    span = min(max(days, 0), 120) / 120
    timing = SENATE_POLL_WEIGHT_NEAR - (SENATE_POLL_WEIGHT_NEAR - SENATE_POLL_WEIGHT_FAR) * span
    return coverage * timing


def load_district_pvi():
    """Cook PVI per district, or an empty map if it has never been fetched."""
    try:
        return _load("district_pvi.json")["districts"]
    except (FileNotFoundError, KeyError, ValueError):
        return {}


def pvi_adjustments(districts, pvi):
    """How far each seat sits from the typical partisan lean of its own rating.

    Centred inside each (rating, lean) bucket on purpose. A rater has already
    conditioned on partisan lean when choosing the rating, so the level of a
    bucket is information and must not be double counted; what the bucket does
    NOT capture is the spread inside it. Centring keeps every bucket's average
    exactly where the rating put it and only separates seats within it.

    This matters least where it looks like it should matter most. Cook's
    toss-ups span barely six points of PVI, so they barely move. The Solid
    buckets span forty-five, and the old model treated a Solid R seat at D+12
    as being exactly as safe as one at R+33. That is a wave-year error, and
    wave years are what a probability of control is mostly made of.
    """
    if not pvi:
        return [0.0] * len(districts)
    def usable(d):
        return (pvi.get(d["id"]) is not None
                and d["id"].split("-")[0] not in REDRAWN_2026)

    totals, counts = {}, {}
    for d in districts:
        if not usable(d):
            continue
        score = pvi[d["id"]]
        key = (d["rating"], d["lean"])
        totals[key] = totals.get(key, 0.0) + score
        counts[key] = counts.get(key, 0) + 1
    means = {k: totals[k] / counts[k] for k in totals}
    out = []
    for d in districts:
        key = (d["rating"], d["lean"])
        if not usable(d) or key not in means:
            out.append(0.0)
        else:
            out.append(PVI_WEIGHT * (pvi[d["id"]] - means[key]))
    return out


def run_simulation(n_sims=N_SIMS, seed=SEED):
    rng = np.random.default_rng(seed)
    env = national_environment()
    E = env["dem_margin"]
    nat = rng.normal(0.0, env["national_error_sd"], n_sims)

    # The shared layers of the miss, drawn once and reused by every seat that
    # belongs to them. This is what makes nearby races go wrong together.
    div_index = {name: i for i, name in enumerate(DIVISIONS)}
    div_err = rng.normal(0.0, DIVISION_SD, (n_sims, len(DIVISIONS)))
    state_index = {code: i for i, code in enumerate(sorted(STATE_DIVISION))}
    state_err = rng.normal(0.0, STATE_SD, (n_sims, len(state_index)))

    def shared_for(state_codes):
        """The division and state error columns lined up with a list of seats."""
        div_cols = [div_index[STATE_DIVISION[c]] for c in state_codes]
        st_cols = [state_index[c] for c in state_codes]
        return div_err[:, div_cols] + state_err[:, st_cols]

    # ---- Senate
    sen = load_senate_races()
    atmo = load_atmospherics()
    races = sen["races"]
    s_ref = sen.get("ratings_environment_dem_margin", E)
    polls = load_senate_polls()
    s_base = []
    s_atmo = []
    s_poll_used = []
    for r in races:
        # The rating's own estimate of today's margin: where the rating put the
        # race, moved by however far the national environment has travelled
        # since the rating was published.
        from_rating = _baseline(r["rating"], r["lean"]) + SENATE_ENV_SENSITIVITY * (E - s_ref)

        # A polling average is already a statement about today, so it is blended
        # in as a rival estimate of the same quantity rather than layered on top
        # of the environment shift, which would count the environment twice.
        entry = polls.get(r["seat_id"])
        w = poll_weight(entry, env["days_to_election"]) if entry else 0.0
        if w > 0:
            b = w * entry["dem_margin"] + (1 - w) * from_rating
        else:
            b = from_rating
        s_poll_used.append({"weight": round(w, 3),
                            "poll_margin": entry["dem_margin"] if entry else None,
                            "rating_margin": round(from_rating, 2)} if entry else None)

        adj = 0.0
        if r["seat_id"] in atmo and r["rating"] in ("tossup", "lean"):
            adj = max(-ATMOSPHERICS_CAP, min(ATMOSPHERICS_CAP, atmo[r["seat_id"]]["adjustment_dem"]))
        s_atmo.append(adj)
        s_base.append(b + adj * ATMOSPHERICS_PTS_PER_PROB)
    s_base = np.array(s_base)
    s_states = [r["state"] for r in races]
    s_margin = (s_base[None, :]
                + SENATE_ENV_SENSITIVITY * nat[:, None]
                + shared_for(s_states)
                + rng.normal(0, SENATE_SEAT_SD, (n_sims, len(races))))
    s_win = s_margin > 0
    caucus_mask = np.array([r.get("challenger_caucus") != "independent" for r in races])
    dem_senate = sen["not_up"]["D"] + (s_win & caucus_mask[None, :]).sum(axis=1)
    rep_senate = sen["not_up"]["R"] + (~s_win).sum(axis=1)

    # ---- House
    hou = load_house_districts()
    dists = hou["districts"]
    h_ref = hou.get("ratings_environment_dem_margin", E)
    pvi = load_district_pvi()
    h_adj = pvi_adjustments(dists, pvi)
    h_base = np.array([_baseline(d["rating"], d["lean"]) + a
                       for d, a in zip(dists, h_adj)]) + (E - h_ref)
    # A district shares its state's miss with that state's Senate race, which is
    # why the two chambers now move together more than they used to.
    h_states = [d["id"].split("-")[0] for d in dists]
    h_margin = (h_base[None, :]
                + nat[:, None]
                + shared_for(h_states)
                + rng.normal(0, HOUSE_SEAT_SD, (n_sims, len(dists))))
    h_win = h_margin > 0
    dem_house = h_win.sum(axis=1)
    majority = hou.get("majority", 218)

    d_sen_ctrl = dem_senate >= 51
    d_house_ctrl = dem_house >= majority

    def dist_summary(counts, lo, hi):
        vals, freq = np.unique(counts, return_counts=True)
        hist = {int(v): round(f / n_sims, 5) for v, f in zip(vals, freq) if lo <= v <= hi}
        pct = {str(p): int(np.percentile(counts, p)) for p in (5, 10, 25, 50, 75, 90, 95)}
        return hist, pct

    s_hist, s_pct = dist_summary(dem_senate, 30, 70)
    h_hist, h_pct = dist_summary(dem_house, 150, 290)

    senate_seats = []
    for i, r in enumerate(races):
        senate_seats.append({
            "seat_id": r["seat_id"], "state": r["state"], "held_by": r["held_by"],
            "rating": r["rating"], "lean": r["lean"],
            "dem_candidate": r.get("dem_candidate"), "rep_candidate": r.get("rep_candidate"),
            "incumbent": r.get("incumbent"), "notes": r.get("notes", ""),
            "challenger_caucus": r.get("challenger_caucus"),
            "atmospherics_adj": s_atmo[i],
            "polling": s_poll_used[i],
            "expected_margin": round(float(s_base[i]), 2),
            "dem_win_prob": round(float(s_win[:, i].mean()), 4),
        })

    house_probs = {d["id"]: round(float(h_win[:, i].mean()), 4) for i, d in enumerate(dists)}
    code = {"solid": "S", "likely": "L", "lean": "N", "tossup": "T"}
    house_ratings = {d["id"]: code[d["rating"]] + d["lean"] for d in dists}

    return {
        "environment": env,
        "senate": {
            "dem_control_prob": round(float(d_sen_ctrl.mean()), 4),
            "tie_prob": round(float((dem_senate == 50).mean()), 4),
            "mean_dem_seats": round(float(dem_senate.mean()), 2),
            "mean_rep_seats": round(float(rep_senate.mean()), 2),
            "not_up": sen["not_up"],
            "percentiles": s_pct,
            "histogram": s_hist,
            "seats": senate_seats,
            "ratings_as_of": sen.get("as_of"),
            "ratings_source": sen.get("ratings_source"),
        },
        "house": {
            "dem_control_prob": round(float(d_house_ctrl.mean()), 4),
            "mean_dem_seats": round(float(dem_house.mean()), 2),
            "majority": majority,
            "percentiles": h_pct,
            "histogram": h_hist,
            "district_probs": house_probs,
            "district_ratings": house_ratings,
            "ratings_as_of": hou.get("as_of"),
            "ratings_source": hou.get("ratings_source"),
        },
        "joint": {
            "dem_both": round(float((d_sen_ctrl & d_house_ctrl).mean()), 4),
            "dem_house_rep_senate": round(float((~d_sen_ctrl & d_house_ctrl).mean()), 4),
            "rep_house_dem_senate": round(float((d_sen_ctrl & ~d_house_ctrl).mean()), 4),
            "rep_both": round(float((~d_sen_ctrl & ~d_house_ctrl).mean()), 4),
        },
        "n_sims": n_sims,
    }


if __name__ == "__main__":
    out = run_simulation(seed=42)
    e = out["environment"]
    print(f"Environment: D{e['dem_margin']:+.1f} (poll D{e['generic_ballot']:+.1f} x {e['poll_weight']}, "
          f"fundamentals D{e['fundamentals']['prior_dem_margin']:+.1f}; econ index {e['fundamentals']['economic_index']})")
    print(f"Senate: Dem control {out['senate']['dem_control_prob']*100:.1f}% (tie {out['senate']['tie_prob']*100:.1f}%),"
          f" mean D seats {out['senate']['mean_dem_seats']:.1f}")
    print(f"House: Dem control {out['house']['dem_control_prob']*100:.1f}%, mean D seats {out['house']['mean_dem_seats']:.1f},"
          f" 90% range {out['house']['percentiles']['5']}-{out['house']['percentiles']['95']}")
    print("Joint:", out["joint"])
