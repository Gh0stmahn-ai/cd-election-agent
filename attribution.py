#!/usr/bin/env python3
"""
Why the forecast moved, measured rather than guessed.

The model is a deterministic function of its inputs, so the honest way to
explain a day's movement is not to write a story around it but to re-run the
model with one input at a time held back and measure what each change was
worth. That is what this module does.

For every driver (the generic ballot, approval, each economic reading, race
rating changes, Senate news momentum, and the shrinking distance to Election
Day) it computes:

    forward   what happens to yesterday's forecast if ONLY this input moves
    backward  what happens to today's forecast if ONLY this input is undone

and reports the average of the two. Taking both directions matters because
the drivers interact: a poll shift is worth more when the races are already
close. One direction alone systematically over- or under-credits whichever
input is evaluated first, and the average is the standard fix.

Every run uses the same random seed, so the differences between them contain
no simulation noise at all: they are the pure effect of the input.

Whatever the drivers fail to account for is reported as a residual rather
than quietly spread across them.

Every daily input is reconstructed from the stored snapshots rather than read
live, so an attribution computed today stays reproducible and can never be
rewritten by tomorrow's data. The two things read from the live files are
calibration tables rather than inputs -- the national environment each ratings
set was published against, and the partisan index -- and those are read once at
import rather than per run.
"""
import contextlib
import json
from pathlib import Path

import model

DATA_DIR = Path(__file__).parent / "data"

# Rating codes as written into each snapshot by model.run_simulation.
CODE_RATING = {"S": "solid", "L": "likely", "N": "lean", "T": "tossup"}

# Calibration constants, not daily inputs: the national environment each
# rating set was published against. Read once from the live files.
def _reference_margins():
    try:
        sen = json.loads((DATA_DIR / "senate_races_2026.json").read_text())
        hou = json.loads((DATA_DIR / "house_districts_2026.json").read_text())
        return (sen.get("ratings_environment_dem_margin"),
                hou.get("ratings_environment_dem_margin"),
                hou.get("majority", 218))
    except (OSError, ValueError):
        return (None, None, 218)


S_REF, H_REF, MAJORITY = _reference_margins()

# The partisan index is a calibration table, not a daily input: it is published
# once per map and nothing in the daily run touches it. Read once, so a
# re-run months from now uses the same table this run did.
try:
    DISTRICT_PVI = model.load_district_pvi()
except Exception:                                  # noqa: BLE001 - best effort
    DISTRICT_PVI = {}

DRIVERS = [
    ("generic_ballot", "Generic ballot"),
    ("approval", "Presidential approval"),
    ("economy", "The economy"),
    ("senate_ratings", "Senate rating changes"),
    ("house_ratings", "House rating changes"),
    ("senate_polls", "Senate state polling"),
    ("news", "Senate news momentum"),
    ("clock", "Election Day getting closer"),
]


def state_from_snapshot(snap):
    """Everything needed to re-run the model, pulled out of one snapshot."""
    env, sen, hou = snap["environment"], snap["senate"], snap["house"]
    fund = env["fundamentals"]
    return {
        "generic_ballot": env["generic_ballot"],
        "baseline_pts": fund["midterm_baseline_pts"],
        "approval_pts": fund["approval_pts"],
        "approval_net": fund["approval_net"],
        "economy_pts": fund["economic_pts"],
        "economy_index": fund["economic_index"],
        "economy_components": fund.get("economic_components", []),
        "clock": env["days_to_election"],
        "senate_seats": [dict(s) for s in sen["seats"]],
        "not_up": sen["not_up"],
        "news": {s["seat_id"]: s.get("atmospherics_adj", 0.0) for s in sen["seats"]},
        "house_ratings": dict(hou["district_ratings"]),
        # Each race's polling average as that run saw it. Without this the
        # model would read today's poll file while re-running yesterday, and
        # every poll move would land in whatever bucket is left over.
        "senate_polls": {s["seat_id"]: {"dem_margin": s["polling"]["poll_margin"],
                                        "n_used": s["polling"].get("n_used"),
                                        "weight": s["polling"].get("weight", 0.0)}
                         for s in sen["seats"]
                         if s.get("polling") and s["polling"].get("poll_margin") is not None},
    }


def _swap(state, driver, other):
    """A copy of `state` with one driver taken from `other`."""
    out = dict(state)
    if driver == "generic_ballot":
        out["generic_ballot"] = other["generic_ballot"]
    elif driver == "approval":
        out["approval_pts"] = other["approval_pts"]
        out["approval_net"] = other["approval_net"]
    elif driver == "economy":
        out["economy_pts"] = other["economy_pts"]
        out["economy_index"] = other["economy_index"]
        out["economy_components"] = other["economy_components"]
    elif driver == "senate_ratings":
        ratings = {s["seat_id"]: (s["rating"], s["lean"]) for s in other["senate_seats"]}
        seats = []
        for seat in state["senate_seats"]:
            seat = dict(seat)
            if seat["seat_id"] in ratings:
                seat["rating"], seat["lean"] = ratings[seat["seat_id"]]
            seats.append(seat)
        out["senate_seats"] = seats
    elif driver == "house_ratings":
        out["house_ratings"] = other["house_ratings"]
    elif driver == "senate_polls":
        out["senate_polls"] = other["senate_polls"]
    elif driver == "news":
        out["news"] = other["news"]
    elif driver == "clock":
        out["clock"] = other["clock"]
    return out


@contextlib.contextmanager
def _as(state):
    """Point the model at a reconstructed day instead of the live data files."""
    saved = (model.load_generic_ballot, model.fundamentals_prior, model.days_to_election,
             model.load_senate_races, model.load_house_districts, model.load_atmospherics,
             model.load_senate_polls, model.load_district_pvi, model.poll_weight)

    prior = {
        "midterm_baseline_pts": state["baseline_pts"],
        "approval_net": state["approval_net"],
        "approval_pts": state["approval_pts"],
        "economic_index": state["economy_index"],
        "economic_pts": state["economy_pts"],
        "economic_components": state["economy_components"],
        "prior_dem_margin": round(state["baseline_pts"] + state["approval_pts"]
                                  + state["economy_pts"], 2),
    }
    races = [dict(s) for s in state["senate_seats"]]
    districts = [{"id": k, "rating": CODE_RATING[v[0]], "lean": v[1]}
                 for k, v in state["house_ratings"].items()]

    model.load_generic_ballot = lambda: {"dem_margin_points": state["generic_ballot"]}
    model.fundamentals_prior = lambda fund: prior
    model.days_to_election = lambda today=None: state["clock"]
    model.load_senate_races = lambda: {
        "races": races, "not_up": state["not_up"],
        **({"ratings_environment_dem_margin": S_REF} if S_REF is not None else {})}
    model.load_house_districts = lambda: {
        "districts": districts, "majority": MAJORITY,
        **({"ratings_environment_dem_margin": H_REF} if H_REF is not None else {})}
    model.load_atmospherics = lambda: {
        k: {"adjustment_dem": v} for k, v in state["news"].items()}
    model.load_senate_polls = lambda: state["senate_polls"]
    model.load_district_pvi = lambda: DISTRICT_PVI

    def weight(entry, days=None):
        """The weight the real function would give, or the one that was used.

        Recomputing it keeps the clock driver honest -- a poll is worth more in
        late October than in July, and that shift belongs to the calendar, not
        to the polls. Runs saved before n_used was recorded fall back to the
        weight stored with them.
        """
        if entry.get("n_used") is not None:
            return saved[-1](entry, state["clock"] if days is None else days)
        return entry.get("weight", 0.0)

    model.poll_weight = weight
    try:
        yield
    finally:
        (model.load_generic_ballot, model.fundamentals_prior, model.days_to_election,
         model.load_senate_races, model.load_house_districts, model.load_atmospherics,
         model.load_senate_polls, model.load_district_pvi, model.poll_weight) = saved


def _probs(state, n_sims, seed):
    with _as(state):
        out = model.run_simulation(n_sims=n_sims, seed=seed)
    return {"senate": out["senate"]["dem_control_prob"],
            "house": out["house"]["dem_control_prob"]}


def _economy_split(prev, now, total_points):
    """Divide the economy's effect among the indicators that actually moved.

    The economic index is a weighted sum of per-indicator scores, so each
    indicator's share of the move is exactly its own weighted score change.
    """
    before = {c["id"]: c for c in prev["economy_components"]}
    parts = []
    for comp in now["economy_components"]:
        was = before.get(comp["id"])
        if not was or abs(comp["contribution"] - was["contribution"]) < 1e-9:
            continue
        parts.append({"id": comp["id"], "label": comp["label"],
                      "score_from": was["score"], "score_to": comp["score"],
                      "weighted_change": round(comp["contribution"] - was["contribution"], 4)})
    swing = sum(abs(p["weighted_change"]) for p in parts)
    for part in parts:
        share = abs(part["weighted_change"]) / swing if swing else 0
        part["points"] = {k: round(v * share * (1 if part["weighted_change"] >= 0 else 1), 3)
                          for k, v in total_points.items()}
    parts.sort(key=lambda p: -abs(p["weighted_change"]))
    return parts


def compare(prev_snap, now_snap, n_sims=100_000, seed=model.SEED if hasattr(model, "SEED") else 7):
    """Attribute the move between two snapshots to the inputs that caused it."""
    prev, now = state_from_snapshot(prev_snap), state_from_snapshot(now_snap)
    base, final = _probs(prev, n_sims, seed), _probs(now, n_sims, seed)

    # Yesterday's inputs are re-run under TODAY's code, so if the model itself
    # changed -- a constant refitted, a new adjustment added -- that shift is
    # invisible to every driver below and the explained total would quietly
    # disagree with the trend line it sits under. Measure it directly: the gap
    # between what was published yesterday and what yesterday's own inputs
    # produce now is the model change, and nothing else.
    published = {"senate": prev_snap["senate"]["dem_control_prob"],
                 "house": prev_snap["house"]["dem_control_prob"]}
    model_shift = {k: base[k] - published[k] for k in base}
    total = {k: final[k] - published[k] for k in base}

    drivers = []
    if any(abs(v) >= 0.0002 for v in model_shift.values()):
        drivers.append({"id": "model", "label": "Model recalibration",
                        "points": {k: round(v * 100, 3) for k, v in model_shift.items()}})
    for key, label in DRIVERS:
        forward = _probs(_swap(prev, key, now), n_sims, seed)
        backward = _probs(_swap(now, key, prev), n_sims, seed)
        points = {k: round(((forward[k] - base[k]) + (final[k] - backward[k])) / 2 * 100, 3)
                  for k in base}
        if all(abs(v) < 0.02 for v in points.values()):
            continue                      # nothing this driver did is visible
        entry = {"id": key, "label": label, "points": points}
        if key == "economy":
            entry["parts"] = _economy_split(prev, now, points)
        drivers.append(entry)

    explained = {k: sum(d["points"][k] for d in drivers) for k in base}
    return {
        "from": prev_snap["timestamp"], "to": now_snap["timestamp"],
        "total": {k: round(v * 100, 3) for k, v in total.items()},
        "drivers": sorted(drivers, key=lambda d: -max(abs(v) for v in d["points"].values())),
        "residual": {k: round(total[k] * 100 - explained[k], 3) for k in base},
        "facts": facts(prev_snap, now_snap),
        "races": race_moves(prev_snap, now_snap),
        "n_sims": n_sims,
    }


def facts(prev_snap, now_snap):
    """The plain-language list of what actually changed in the data."""
    prev, now = state_from_snapshot(prev_snap), state_from_snapshot(now_snap)
    out = []

    def margin(v):
        return ("D+" if v >= 0 else "R+") + f"{abs(v):.1f}"

    if abs(now["generic_ballot"] - prev["generic_ballot"]) >= 0.05:
        out.append({"kind": "poll", "text": f"Generic ballot moved {margin(prev['generic_ballot'])}"
                                            f" to {margin(now['generic_ballot'])}"})
    if abs(now["approval_net"] - prev["approval_net"]) >= 0.05:
        out.append({"kind": "poll", "text": f"Presidential approval went from net "
                                            f"{prev['approval_net']:+.1f} to {now['approval_net']:+.1f}"})

    before = {c["id"]: c for c in prev["economy_components"]}
    for comp in now["economy_components"]:
        was = before.get(comp["id"])
        if was and abs(comp["score"] - was["score"]) >= 0.05:
            direction = "better" if comp["score"] > was["score"] else "worse"
            out.append({"kind": "economy",
                        "text": f"{comp['label']} scored {direction} for the president's party "
                                f"({was['score']:+.2f} to {comp['score']:+.2f})"})

    prev_sen = {s["seat_id"]: (s["rating"], s["lean"], s.get("state", s["seat_id"][:2]))
                for s in prev["senate_seats"]}
    for seat in now["senate_seats"]:
        was = prev_sen.get(seat["seat_id"])
        if was and (was[0], was[1]) != (seat["rating"], seat["lean"]):
            out.append({"kind": "rating",
                        "text": f"{seat.get('state', seat['seat_id'][:2])} Senate moved from "
                                f"{_name(was[0], was[1])} to {_name(seat['rating'], seat['lean'])}"})

    moved_house = [k for k, v in now["house_ratings"].items()
                   if prev["house_ratings"].get(k) not in (None, v)]
    for district in moved_house[:6]:
        was, is_now = prev["house_ratings"][district], now["house_ratings"][district]
        out.append({"kind": "rating",
                    "text": f"{district} moved from {_name(CODE_RATING[was[0]], was[1])} to "
                            f"{_name(CODE_RATING[is_now[0]], is_now[1])}"})
    if len(moved_house) > 6:
        out.append({"kind": "rating",
                    "text": f"and {len(moved_house) - 6} more House districts re-rated"})

    for seat_id, entry in sorted(now["senate_polls"].items()):
        was = prev["senate_polls"].get(seat_id)
        if was and abs(entry["dem_margin"] - was["dem_margin"]) >= 0.25:
            out.append({"kind": "poll",
                        "text": f"{seat_id.split('-')[0]} Senate polling average moved "
                                f"{margin(was['dem_margin'])} to {margin(entry['dem_margin'])}"})
        elif not was:
            out.append({"kind": "poll",
                        "text": f"{seat_id.split('-')[0]} Senate has a polling average for the first "
                                f"time, at {margin(entry['dem_margin'])}"})

    for seat_id, value in now["news"].items():
        was = prev["news"].get(seat_id, 0.0)
        if abs(value - was) >= 0.005:
            out.append({"kind": "news",
                        "text": f"{seat_id.split('-')[0]} news momentum moved "
                                f"{was:+.2f} to {value:+.2f}"})
    return out


def _name(rating, lean):
    return {"solid": "Solid", "likely": "Likely", "lean": "Lean", "tossup": "Toss-up"}[rating] + \
        ("" if rating == "tossup" and not lean else f" {lean}")


def race_moves(prev_snap, now_snap, threshold=0.015):
    """Senate races whose odds moved enough to be worth naming."""
    before = {s["seat_id"]: s for s in prev_snap["senate"]["seats"]}
    out = []
    for seat in now_snap["senate"]["seats"]:
        was = before.get(seat["seat_id"])
        if not was:
            continue
        shift = seat["dem_win_prob"] - was["dem_win_prob"]
        if abs(shift) < threshold:
            continue
        reasons = []
        if (was["rating"], was["lean"]) != (seat["rating"], seat["lean"]):
            reasons.append(f"re-rated {_name(was['rating'], was['lean'])} to "
                           f"{_name(seat['rating'], seat['lean'])}")
        if abs(seat.get("atmospherics_adj", 0) - was.get("atmospherics_adj", 0)) >= 0.005:
            reasons.append("news momentum")
        if not reasons:
            reasons.append("the national environment")
        out.append({"seat_id": seat["seat_id"], "state": seat.get("state", seat["seat_id"][:2]),
                    "dem_candidate": seat.get("dem_candidate"),
                    "rep_candidate": seat.get("rep_candidate"),
                    "from": round(was["dem_win_prob"] * 100, 1),
                    "to": round(seat["dem_win_prob"] * 100, 1),
                    "shift": round(shift * 100, 1),
                    "why": " and ".join(reasons)})
    out.sort(key=lambda r: -abs(r["shift"]))
    return out
