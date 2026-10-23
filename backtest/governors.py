"""What a Cook governor rating has meant, 2010-2024, and how governor races
move with the national mood. Produces the governor constants in model.py.

    python3 backtest/fetch_governors.py   # once: ratings and results into governor_history.json
    python3 backtest/governors.py

The measure is the favourite's margin by rating bucket (the favourite being the
rated party, or for a toss-up the party holding the seat, or failing that the
state's lean). Each cycle's average miss is then removed to leave the spread a
single race has around its bucket, and the cycle averages are set against the
Senate's to see how much of a national swing reaches a governor's race.
"""
import json, math, os

HERE = os.path.dirname(os.path.abspath(__file__))
BUCKETS = ("tossup", "lean", "likely", "solid")


def mean(v):
    return sum(v) / len(v) if v else float("nan")


def sd(v):
    m = mean(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / len(v)) if len(v) > 1 else float("nan")


def favourite(r):
    if r.get("lean") in ("D", "R"):
        return r["lean"]
    if r.get("held_by") in ("D", "R"):
        return r["held_by"]
    if r.get("pvi"):
        return "D" if r["pvi"] > 0 else "R"
    return None


def rows_for(path, key_state=lambda k: k):
    out = []
    for year, races in json.load(open(os.path.join(HERE, path))).items():
        for key, r in races.items():
            side = favourite(r)
            if side:
                out.append(dict(r, year=year, state=key_state(key), side=side,
                                signed=r["margin"] * (1 if side == "D" else -1)))
    return out


def yearly_bias(rows, means):
    bias = {}
    for y in sorted({r["year"] for r in rows}):
        res = [r["margin"] - means[r["rating"]] * (1 if r["side"] == "D" else -1)
               for r in rows if r["year"] == y and r["rating"] != "solid"]
        if res:
            bias[y] = mean(res)
    return bias


def main():
    gov = rows_for("governor_history.json")
    sen = rows_for("senate_history.json", lambda k: k.split("-")[0])
    gm = {b: mean([r["signed"] for r in gov if r["rating"] == b]) for b in BUCKETS}
    sm = {b: mean([r["signed"] for r in sen if r["rating"] == b]) for b in BUCKETS}
    print(f"{len(gov)} governor races, {len(sen)} Senate races\n")
    print("favourite's margin by Cook rating     governor            Senate")
    for b in BUCKETS:
        g = [r["signed"] for r in gov if r["rating"] == b]
        s = [r["signed"] for r in sen if r["rating"] == b]
        print(f"  {b:7}  n={len(g):3}  {mean(g):5.1f} (sd {sd(g):4.1f}, upsets {sum(1 for x in g if x < 0):2})"
              f"     n={len(s):3}  {mean(s):5.1f}")
    gb, sb = yearly_bias(gov, gm), yearly_bias(sen, sm)
    both = [y for y in gb if y in sb]
    mx, my = mean([sb[y] for y in both]), mean([gb[y] for y in both])
    sxx = sum((sb[y] - mx) ** 2 for y in both)
    slope = sum((sb[y] - mx) * (gb[y] - my) for y in both) / sxx
    corr = slope * math.sqrt(sxx / sum((gb[y] - my) ** 2 for y in both))
    print(f"\ncycle-average governor miss per point of Senate miss: {slope:.2f} (correlation {corr:.2f}, "
          f"{len(both)} cycles)")
    print("spread around the bucket once each cycle's average miss is removed:")
    for b in BUCKETS:
        res = [r["margin"] - gm[b] * (1 if r["side"] == "D" else -1) - gb.get(r["year"], 0)
               for r in gov if r["rating"] == b]
        print(f"  {b:7} {sd(res):4.1f}")
    json.dump({"governor_margin": gm, "senate_margin": sm, "slope_on_senate": slope, "corr": corr},
              open(os.path.join(HERE, "governor_fit.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
