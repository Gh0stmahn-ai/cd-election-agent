"""
Election forecasting model.

Three layers, same approach used by public forecasters (538-style, The
Economist, Silver Bulletin):

1. Rating -> baseline win probability for the seat's current favorite
   (a documented, adjustable mapping - not a black box)
2. National environment adjustment from the generic ballot, applied more
   heavily to races with sparse polling (fundamentals-driven)
3. Monte Carlo simulation with CORRELATED error across races, because
   real polling misses tend to move similar states/districts together
   in the same direction - treating every race as an independent coin
   flip understates the true uncertainty in chamber control.
"""

import json
import random
import statistics
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
ITER_DIR = Path(__file__).parent / "iterations"

# Baseline win probability for the FAVORED party, by Cook-style rating.
# These are widely-used rough anchors, not derived from a proprietary model.
RATING_TO_PROB = {
    "solid": 0.98,
    "likely": 0.90,
    "lean": 0.72,
    "tossup": 0.52,  # slight edge to whichever party is coded as favorite
}

SENATE_SEATS_NOT_UP = 65  # 100 - 35 up in 2026
SENATE_CURRENT_R = 53
SENATE_CURRENT_D = 47


def load_senate_races():
    with open(DATA_DIR / "senate_races_2026.json") as f:
        return json.load(f)


def load_generic_ballot():
    with open(DATA_DIR / "generic_ballot_2026.json") as f:
        return json.load(f)


def load_atmospherics():
    path = DATA_DIR / "atmospherics_2026.json"
    if not path.exists():
        return {}
    with open(path) as f:
        payload = json.load(f)
    return {a["seat_id"]: a for a in payload.get("assessments", [])}


def seat_probability(race, generic_ballot_margin_dem, atmospherics=None):
    """
    Return probability the seat's currently-favored party holds it.
    Toss-ups get nudged by the national environment since there's no
    strong seat-specific signal to override it; solid/likely seats are
    left alone since local fundamentals dominate.

    atmospherics: optional dict from load_atmospherics(), keyed by
    seat_id. Applied ONLY to tossup/lean seats, capped at +/-0.08,
    as a probability-of-Dem-win adjustment - kept separate from and
    additive to the polling/fundamentals-based number, never silently
    blended in a way that hides how much it moved the forecast.
    """
    base = RATING_TO_PROB[race["rating"]]
    if race["rating"] == "tossup":
        shift = max(-0.15, min(0.15, generic_ballot_margin_dem * 0.006))
        held_by_dem = race["held_by"] == "D"
        base = base + shift if held_by_dem else base - shift

    prob_favorite_holds = max(0.03, min(0.97, base))

    if atmospherics and race["seat_id"] in atmospherics and race["rating"] in ("tossup", "lean"):
        adj_dem = atmospherics[race["seat_id"]]["adjustment_dem"]
        adj_dem = max(-0.08, min(0.08, adj_dem))
        held_by_dem = race["held_by"] == "D"
        prob_dem_wins = prob_favorite_holds if held_by_dem else (1 - prob_favorite_holds)
        prob_dem_wins = max(0.02, min(0.98, prob_dem_wins + adj_dem))
        prob_favorite_holds = prob_dem_wins if held_by_dem else (1 - prob_dem_wins)

    return prob_favorite_holds


def run_simulation(n_sims=20000, seed=None):
    """
    Monte Carlo simulation of all 35 Senate races with correlated error.
    A single national error draw (shared "polling miss") is combined with
    a race-specific error draw for each simulation, so misses in similar
    races tend to move together rather than canceling out.
    """
    if seed is not None:
        random.seed(seed)

    races = load_senate_races()["races"]
    gb = load_generic_ballot()
    margin = gb["dem_margin_points"]
    atmospherics = load_atmospherics()

    seat_results = {r["seat_id"]: [] for r in races}
    dem_seat_counts = []

    for _ in range(n_sims):
        # Shared national error: represents a systemic polling miss that
        # affects all races in the same direction that cycle.
        national_error = random.gauss(0, 0.05)
        dem_seats_won = SENATE_CURRENT_D - sum(
            1 for r in races if r["held_by"] == "D"
        )  # start from seats NOT up this cycle, held by Dems

        for r in races:
            p_favorite_holds = seat_probability(r, margin, atmospherics)
            # race-specific error on top of the shared national error
            race_error = random.gauss(0, 0.06)
            adjusted_p = max(0.01, min(0.99, p_favorite_holds + national_error + race_error))

            favorite_wins = random.random() < adjusted_p
            # translate "favorite holds" into "which party wins the seat"
            party_wins_dem = (r["held_by"] == "D") == favorite_wins
            seat_results[r["seat_id"]].append(1 if party_wins_dem else 0)
            if party_wins_dem:
                dem_seats_won += 1

        dem_seat_counts.append(dem_seats_won)

    seat_probs = {
        seat_id: sum(results) / len(results) for seat_id, results in seat_results.items()
    }
    dem_control_prob = sum(1 for c in dem_seat_counts if c >= 50) / n_sims
    mean_dem_seats = statistics.mean(dem_seat_counts)

    return {
        "seat_dem_win_prob": seat_probs,
        "dem_senate_control_prob": dem_control_prob,
        "mean_dem_seats": mean_dem_seats,
        "seat_distribution_sample": sorted(dem_seat_counts)[:: max(1, n_sims // 200)],
    }


def house_national_model():
    """
    National-level House forecast from the generic ballot only.

    Individual district-level ratings (Cook PVI, per-district polling)
    are paywalled at the source and are NOT fabricated here. This
    function implements a documented empirical relationship instead:
    historically, a party needs roughly a 1-2 point national vote margin
    just to win a bare majority of seats due to geographic sorting
    (the "efficiency gap"), and each additional point of margin beyond
    that swings a small, roughly linear number of seats.

    This is intentionally a rough national swing model, not a per-district
    forecast - wire in real Cook PVI / district polling data via
    data/house_districts.json (see README) to upgrade it.
    """
    gb = load_generic_ballot()
    margin = gb["dem_margin_points"]  # positive = Dem lead

    # Empirical anchor: Dems need roughly +3 national margin to reach 218
    # seats given current district lines (documented efficiency gap).
    # Historical swing rule of thumb: ~3 House seats change per 1 point
    # of national margin shift.
    effective_margin_for_majority = margin - 3.0
    mean_seats_above_218 = effective_margin_for_majority * 3.0

    # Uncertainty: national vote-to-seat translation has real variance
    # (redistricting quirks, candidate quality). Use a normal approx.
    sd_seats = 12
    from math import erf, sqrt

    z = (0 - mean_seats_above_218) / sd_seats
    dem_majority_prob = 1 - 0.5 * (1 + erf(z / sqrt(2)))

    return {
        "generic_ballot_dem_margin": margin,
        "mean_dem_seats_above_218": round(mean_seats_above_218, 1),
        "dem_house_majority_prob": round(dem_majority_prob, 3),
        "method": "national swing model - see docstring for limitations",
    }


if __name__ == "__main__":
    senate = run_simulation(n_sims=20000, seed=42)
    house = house_national_model()
    print("Senate: Dem control probability = %.1f%%" % (senate["dem_senate_control_prob"] * 100))
    print("Senate: mean Dem seats = %.1f" % senate["mean_dem_seats"])
    print("House: Dem majority probability = %.1f%%" % (house["dem_house_majority_prob"] * 100))
