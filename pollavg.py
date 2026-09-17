#!/usr/bin/env python3
"""
Our own generic-ballot average, built from the individual polls.

Until now the generic ballot came from Wikipedia's table of poll
AGGREGATORS: an average of RealClearPolitics, Silver Bulletin, Decision Desk
HQ and others. That is a decent number and a bad input. Three reasons:

  * It double counts. Every aggregator is reading mostly the same polls, so
    averaging six of them is averaging one set of polls six times with
    arbitrary weights, not pooling six independent readings.
  * It cannot be dated. Each aggregator updates on its own schedule, so the
    "average" mixes a number computed today with one computed last Tuesday.
  * It has no error bar. A model that needs to know how confident to be in
    its most important input was being handed a bare point estimate.

Wikipedia also maintains the underlying poll list -- pollster, field dates,
sample size, population, and both toplines -- for this cycle and the last.
This module reads that instead and builds the average here, where the
weighting is visible and the uncertainty comes out the other end.
"""
import html
import re
from datetime import date, datetime, timedelta

# How the weights are set, and why.
HALF_LIFE_DAYS = 21      # a poll is worth half as much three weeks later
MAX_AGE_DAYS = 60        # older than this and the race has moved on
HOUSE_EFFECT_DAYS = 500  # a house's lean is slow-moving, so measure it over years
SAMPLE_REFERENCE = 800   # sqrt(n) credit, normalised at a typical sample
SAMPLE_CAP = 3.0         # one enormous panel does not get to be ten polls
POPULATION_WEIGHT = {"LV": 1.0, "RV": 0.9, "A": 0.7, "": 0.85}
POLLSTER_CAP = 0.20      # no single pollster may be more than a fifth of it
MAX_TREND_PER_DAY = 0.25 # points a day; past this it is an event, not a trend
HOUSE_EFFECT_SHRINK = 4  # polls a pollster needs before its lean is trusted
HOUSE_EFFECT_CAP = 6.0   # points; beyond this it is a different question, not a lean

MONTHS = ("january february march april may june july august september "
          "october november december").split()


def _plain(fragment):
    """One table cell to readable text.

    Wikipedia's poll tables colour their cells through templates, which leave
    style fragments inside the cell's own text. Those fragments contain
    digits, so anything that reads numbers out of a whole row rather than out
    of a cleaned cell will eventually read a colour code as a percentage.
    """
    fragment = re.sub(r"<sup.*?</sup>", " ", fragment, flags=re.S)
    fragment = re.sub(r"<style.*?</style>", " ", fragment, flags=re.S)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(fragment)
    text = re.sub(r"\{[^{}]*\}|\[\[[^\]]*\]\]", " ", text)
    text = re.sub(r"(style|background-color|color|about|typeof|id)\s*=\s*\S+", " ", text)
    text = re.sub(r"#[0-9A-Fa-f]{3,8}", " ", text)
    text = text.replace("\\", " ")
    return re.sub(r"\s+", " ", text).strip()


def _cells(row_html):
    return [_plain(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S)]


def parse_dates(text, today=None):
    """The last day a poll was in the field.

    Field dates come in several shapes -- "September 13-15, 2026",
    "December 30, 2025 - January 1, 2026", "September 8, 2026" -- and what
    the weighting needs is simply when the poll stopped asking.
    """
    text = text.replace("–", "-").replace("—", "-")
    years = re.findall(r"\b(20\d\d)\b", text)
    if not years:
        return None
    year = int(years[-1])
    month_hits = [(m.start(), m.group(1).lower()) for m in
                  re.finditer(r"\b([A-Z][a-z]+)\b", text) if m.group(1).lower() in MONTHS]
    if not month_hits:
        return None
    month = MONTHS.index(month_hits[-1][1]) + 1
    tail = text[month_hits[-1][0]:]
    days = [int(d) for d in re.findall(r"\b(\d{1,2})\b(?!\d)", tail) if 1 <= int(d) <= 31]
    if not days:
        return None
    try:
        return date(year, month, days[-1] if len(days) > 1 else days[0])
    except ValueError:
        return None


def parse_sample(text):
    """('2,947 (RV)') -> (2947, 'RV')."""
    m = re.search(r"([\d,]{3,})", text)
    n = int(m.group(1).replace(",", "")) if m else None
    pop = ""
    p = re.search(r"\((LV|RV|A|V)\)", text, re.I)
    if p:
        pop = p.group(1).upper()
        pop = "A" if pop == "V" else pop
    return n, pop


def parse_table(table_html):
    """Every readable poll in one Wikipedia poll table.

    A pollster that reports two populations gets two rows, with the source
    cell spanning both, so a short row inherits the source above it. Rows are
    aligned from the RIGHT for that reason: the toplines are always the last
    columns, whatever is missing from the front.
    """
    rows = re.findall(r"<tr.*?</tr>", table_html, re.S)
    if not rows:
        return []
    header = [h.lower() for h in _cells(rows[0])]
    try:
        i_dem = header.index("democratic")
        i_rep = header.index("republican")
    except ValueError:
        return []
    width = len(header)
    out, last_source = [], None
    for row in rows[1:]:
        cells = _cells(row)
        if len(cells) < 3:
            continue
        offset = width - len(cells)          # leading cells lost to a rowspan
        def col(i):
            j = i - offset
            return cells[j] if 0 <= j < len(cells) else ""
        source = col(0) if offset == 0 else (last_source or "")
        if offset == 0:
            last_source = source
        dem, rep = _pct(col(i_dem)), _pct(col(i_rep))
        if dem is None or rep is None:
            continue
        # Rows are aligned from the right, which is correct when the missing
        # cells really are the rowspanned ones at the front and wrong when a
        # row omits a column in the middle. Two toplines that do not add up
        # to something like a generic ballot mean the alignment slipped.
        if not 70 <= dem + rep <= 105:
            continue
        end = parse_dates(col(1) if offset == 0 else "")
        if end is None and offset == 0:
            continue
        n, pop = parse_sample(col(2))
        out.append({"source": source.strip(" .,"), "end": end, "n": n,
                    "population": pop, "dem": dem, "rep": rep,
                    "margin": round(dem - rep, 1)})
    # A row that inherited its source inherited its field dates too, and
    # sometimes its sample size: a second population line often carries only
    # the population tag.
    for i, poll in enumerate(out):
        if i and poll["end"] is None:
            poll["end"] = out[i - 1]["end"]
        if i and poll["n"] is None:
            poll["n"] = out[i - 1]["n"]
    return [p for p in out if p["end"] is not None]


def _pct(text):
    m = re.search(r"(\d{1,2}(?:\.\d)?)\s*%", text)
    if not m:
        m = re.fullmatch(r"\s*(\d{1,2}(?:\.\d)?)\s*", text)
    if not m:
        return None
    value = float(m.group(1))
    return value if 10 <= value <= 80 else None


def _house_name(name):
    """A poll's house, with the sponsor and the partisan tag stripped.

    "Big Data Poll (R)" and "Big Data Poll/Public Polling Project" are the
    same house effect, and treating them as two pollsters would let one
    house count twice and dodge the per-pollster cap.
    """
    name = re.sub(r"\((R|D)\)", " ", name)
    name = re.split(r"[/(]", name)[0]
    return re.sub(r"\s+", " ", name).strip() or "Unknown"


def _pollster(name):
    return _house_name(name).lower()


PREFERRED_POPULATION = {"LV": 3, "RV": 2, "A": 1, "": 0}


def dedupe(polls):
    """One reading per pollster per field period.

    A house that reports both a likely-voter and a registered-voter topline
    is reporting one poll twice, and counting both would give it double
    weight for having been thorough. Likely voters win where both exist,
    because that is the population the election has.
    """
    best = {}
    for poll in polls:
        key = (_pollster(poll["source"]), poll["end"])
        rank = (PREFERRED_POPULATION.get(poll.get("population", ""), 0), poll.get("n") or 0)
        if key not in best or rank > best[key][0]:
            best[key] = (rank, poll)
    return [poll for _, poll in best.values()]


def weights(polls, today=None, half_life=None):
    """Recency, sample size and population, multiplied together.

    half_life is resolved at call time rather than bound as a default, so
    that changing the constant actually changes the answer -- a default
    argument would freeze whatever the module held at import.
    """
    today = today or date.today()
    half_life = HALF_LIFE_DAYS if half_life is None else half_life
    for poll in polls:
        age = max((today - poll["end"]).days, 0)
        recency = 0.5 ** (age / half_life)
        n = poll.get("n") or SAMPLE_REFERENCE
        size = min((n / SAMPLE_REFERENCE) ** 0.5, SAMPLE_CAP)
        poll["age_days"] = age
        poll["weight"] = recency * size * POPULATION_WEIGHT.get(poll.get("population", ""), 0.85)
    return polls


def house_effects(polls, today=None, rounds=4, window=HOUSE_EFFECT_DAYS):
    """Each pollster's standing lean against the rest of the field.

    Measured over years rather than over the six weeks the average itself
    uses. A house effect is a slow-moving property of a method -- who it
    calls, how it pushes undecideds, which population it screens to -- and
    estimating it from the same handful of recent polls that make up the
    average means a house with two polls in the window gets a "lean" that is
    really just those two polls, and then has it subtracted from those same
    two polls. Over five hundred days the well-known houses have dozens.

    Each poll is compared with what the rest of the field said AT THE SAME
    TIME, so a house that only polls during good months for one party is not
    credited with a lean it does not have. The pass is repeated so that a
    field containing several leaning houses does not bake their lean into
    the baseline they are measured against.
    """
    today = today or date.today()
    pool = [p for p in polls if (today - p["end"]).days <= window]
    if len(pool) < 12:
        return {}
    for poll in pool:
        n = poll.get("n") or SAMPLE_REFERENCE
        poll["he_weight"] = min((n / SAMPLE_REFERENCE) ** 0.5, SAMPLE_CAP)

    by_house = {}
    for poll in pool:
        by_house.setdefault(_pollster(poll["source"]), []).append(poll)
    effects = {h: 0.0 for h in by_house}

    for _ in range(rounds):
        updated = {}
        for house, own in by_house.items():
            deltas, weights_ = [], []
            for poll in own:
                # The field around this poll, excluding its own house, with
                # each rival poll's own lean already taken out.
                near = [(q, effects.get(_pollster(q["source"]), 0.0)) for q in pool
                        if _pollster(q["source"]) != house
                        and abs((q["end"] - poll["end"]).days) <= 30]
                total = sum(q["he_weight"] for q, _ in near)
                if total <= 0:
                    continue
                baseline = sum(q["he_weight"] * (q["margin"] - e) for q, e in near) / total
                deltas.append(poll["margin"] - baseline)
                weights_.append(poll["he_weight"])
            if not deltas:
                updated[house] = 0.0
                continue
            raw = sum(d * w for d, w in zip(deltas, weights_)) / sum(weights_)
            shrink = len(deltas) / (len(deltas) + HOUSE_EFFECT_SHRINK)
            updated[house] = max(-HOUSE_EFFECT_CAP, min(HOUSE_EFFECT_CAP, raw * shrink))
        effects = updated
    return {h: (v, len(by_house[h])) for h, v in effects.items()}


def average(polls, today=None):
    """The weighted average, with house effects removed and an error bar.

    The per-pollster cap is applied after the weights are set and before the
    average is taken, so a house that floods the field with cheap polls
    cannot become the average by volume.
    """
    today = today or date.today()
    all_polls = dedupe(polls)
    effects = house_effects(all_polls, today)
    polls = [p for p in all_polls if (today - p["end"]).days <= MAX_AGE_DAYS]
    if len(polls) < 4:
        return None
    weights(polls, today)

    for poll in polls:
        poll["house"] = _pollster(poll["source"])
        poll["house_effect"] = round(effects.get(poll["house"], (0.0, 0))[0], 2)
        poll["adjusted"] = round(poll["margin"] - poll["house_effect"], 2)

    total = sum(p["weight"] for p in polls)
    cap = total * POLLSTER_CAP
    by_house = {}
    for poll in polls:
        by_house.setdefault(poll["house"], []).append(poll)
    for group in by_house.values():
        share = sum(p["weight"] for p in group)
        if share > cap:
            for poll in group:
                poll["weight"] *= cap / share

    total = sum(p["weight"] for p in polls)
    flat = sum(p["weight"] * p["adjusted"] for p in polls) / total

    # A weighted mean answers "where was this a month ago", not "where is it
    # now". The weights centre on the middle of the window, so whenever the
    # race is moving, a flat average lags it by however far back that centre
    # sits -- a systematic bias, not a noise problem, and it does not go away
    # by shortening the half-life (that only trades the bias for variance).
    #
    # So fit a line through the polls and read it off at today. The slope is
    # shrunk by its own significance, so a field that is merely noisy stays
    # close to the flat average and only a field that is genuinely moving
    # gets moved; and it is capped, because a week of unusual polls is an
    # event to wait out rather than a trend to extrapolate.
    mean_age = sum(p["weight"] * p["age_days"] for p in polls) / total
    sxx = sum(p["weight"] * (p["age_days"] - mean_age) ** 2 for p in polls)
    sxy = sum(p["weight"] * (p["age_days"] - mean_age) * (p["adjusted"] - flat) for p in polls)
    slope = -sxy / sxx if sxx else 0.0          # points per day, forward in time
    if sxx:
        resid = (sum(p["weight"] * (p["adjusted"] - flat + slope * (p["age_days"] - mean_age)) ** 2
                     for p in polls) / total) ** 0.5
        slope_se = resid / sxx ** 0.5 * total ** 0.5 / max(
            (total ** 2 / sum(p["weight"] ** 2 for p in polls)), 1) ** 0.5
        t_stat = abs(slope) / slope_se if slope_se else 0.0
        slope *= t_stat ** 2 / (t_stat ** 2 + 1)
    else:
        slope_se = 0.0
    slope = max(-MAX_TREND_PER_DAY, min(MAX_TREND_PER_DAY, slope))
    mean = flat + slope * mean_age

    # Effective sample size in polls, not people: the usual Kish measure. A
    # field of twenty polls where two carry most of the weight is worth
    # rather fewer than twenty, and the error bar should say so.
    n_eff = total ** 2 / sum(p["weight"] ** 2 for p in polls)
    spread = (sum(p["weight"] * (p["adjusted"] - flat) ** 2 for p in polls) / total) ** 0.5
    # Reading the line off at its edge is less certain than reading a mean off
    # its middle, so the trend's own uncertainty over the extrapolation
    # distance is added in.
    stderr = ((spread ** 2 / max(n_eff, 1)) + (slope_se * mean_age) ** 2) ** 0.5

    display = {}
    for poll in all_polls:
        display.setdefault(_pollster(poll["source"]), _house_name(poll["source"]))
    lean = sorted(({"pollster": display.get(h, h),
                    "polls": effects.get(h, (0.0, 0))[1],
                    "in_average": len(g),
                    "effect": round(effects.get(h, (0.0, 0))[0], 2)}
                   for h, g in by_house.items()),
                  key=lambda e: -abs(e["effect"]))
    return {
        "margin": round(mean, 2),
        "flat": round(flat, 2),
        "trend_per_week": round(slope * 7, 2),
        "mean_age_days": round(mean_age, 1),
        "stderr": round(stderr, 2),
        "spread": round(spread, 2),
        "n_polls": len(polls),
        "n_effective": round(n_eff, 1),
        "n_pollsters": len(by_house),
        "newest": max(p["end"] for p in polls).isoformat(),
        "oldest": min(p["end"] for p in polls).isoformat(),
        "house_effects": [e for e in lean if abs(e["effect"]) >= 0.3][:8],
        "polls": sorted(polls, key=lambda p: -p["end"].toordinal()),
    }
