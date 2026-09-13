#!/usr/bin/env python3
"""
Manual entry for the two numbers that aren't published as data series.

The economy refreshes itself for free from FRED (refresh_economy.py), but
the generic congressional ballot and presidential approval are polling
averages: they live on pages, not in a machine-readable feed. Reading those
automatically needs agent_run.py, which costs API credits.

So this is the free path: when you trigger the workflow from the Actions
tab, you can optionally type today's numbers into the two input boxes, and
they get written through the same validated functions the AI refresh uses.
Leave a box empty and that number is simply left alone.

Reads two environment variables, both optional:
  GENERIC_BALLOT   Democratic margin in points. "6.6", "D+6.6" and "+6.6"
                   all mean D+6.6; "R+2" and "-2" both mean R+2.
  APPROVAL         "approve/disapprove", e.g. "38/60".

Where to read them off: RealClearPolling, Silver Bulletin, VoteHub or any
public average. Two or three averaged is better than one.
"""
import os
import re
import sys

import ingest

SOURCE_NOTE = "Entered by hand when the forecast was re-run from the Actions tab."


def parse_margin(raw):
    """'D+6.6' -> 6.6, 'R+2' -> -2.0, '+6.6' -> 6.6, '-2' -> -2.0."""
    text = raw.strip().upper().replace(" ", "")
    match = re.fullmatch(r"([DR])\+?(-?\d+(?:\.\d+)?)", text)
    if match:
        party, number = match.groups()
        value = float(number)
        return value if party == "D" else -value
    try:
        return float(text)
    except ValueError:
        raise ValueError(
            f"could not read '{raw}' as a margin. Use a number like 6.6 or -2.1, "
            "or a label like D+6.6 or R+2.1.") from None


def parse_approval(raw):
    """'38/60' -> (38.0, 60.0). Also accepts '38 / 60' and '38-60'."""
    parts = re.split(r"[/\-\s]+", raw.strip())
    numbers = [p for p in parts if p]
    if len(numbers) != 2:
        raise ValueError(
            f"could not read '{raw}' as approval. Use approve/disapprove, e.g. 38/60.")
    try:
        approve, disapprove = (float(n) for n in numbers)
    except ValueError:
        raise ValueError(f"could not read '{raw}' as two numbers, e.g. 38/60.") from None
    for label, value in (("approve", approve), ("disapprove", disapprove)):
        if not 0 <= value <= 100:
            raise ValueError(f"{label} of {value} is not a percentage")
    return approve, disapprove


def main():
    ballot = os.environ.get("GENERIC_BALLOT", "").strip()
    approval = os.environ.get("APPROVAL", "").strip()

    if not ballot and not approval:
        print("No poll numbers entered; leaving the generic ballot and approval as they are.")
        return 0

    problems = []

    if ballot:
        try:
            margin = parse_margin(ballot)
            ingest.refresh_generic_ballot(
                new_margin=margin,
                source_notes=SOURCE_NOTE,
                sources=["Entered by hand from a public polling average"])
            side = "D" if margin >= 0 else "R"
            print(f"Generic ballot set to {side}+{abs(margin):.1f}")
        except Exception as e:  # noqa: BLE001 - report it, don't crash the run
            problems.append(f"generic ballot: {e}")

    if approval:
        try:
            approve, disapprove = parse_approval(approval)
            net = approve - disapprove
            ingest.refresh_approval(
                approve=approve, disapprove=disapprove, net=net,
                as_of=ingest._today(), context=SOURCE_NOTE,
                sources=[{"name": "Entered by hand from a public approval average"}])
            print(f"Approval set to {approve:.0f}% / {disapprove:.0f}% (net {net:+.1f})")
        except Exception as e:  # noqa: BLE001
            problems.append(f"approval: {e}")

    for problem in problems:
        print(f"  ! {problem}", file=sys.stderr)
    # A typo in one box shouldn't throw away the rest of the run; the model
    # still simulates on whatever was accepted.
    return 0


if __name__ == "__main__":
    sys.exit(main())
