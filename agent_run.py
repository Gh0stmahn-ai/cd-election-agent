#!/usr/bin/env python3
"""
Daily agentic update for the election forecasting pipeline.

Runs a single Claude conversation, given the current data files, with:
  - the built-in web_search tool
  - three custom tools that map directly onto the write functions already
    defined in ingest.py / atmospherics.py (no raw file edits happen here
    or in the model - every write is one of these three functions)

Claude is instructed to:
  1. Refresh the generic congressional ballot average
     (-> ingest.refresh_generic_ballot)
  2. Update a Senate race's rating ONLY when it finds specific, cited news
     of a change from a named outlet (-> ingest.refresh_senate_rating)
  3. Produce a fresh atmospherics read, capped at +/-0.08, for every
     current toss-up/lean race (-> atmospherics.update_atmospherics)

This is intentionally conservative: nothing is written that isn't routed
through one of those three functions, each call is logged to stdout for
the run log, and the whole thing is bounded by MAX_TURNS so a confused
run can't loop forever and run up cost.

Requires: ANTHROPIC_API_KEY in the environment. Run this before
run_pipeline.py in the daily workflow.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ingest
import atmospherics
from model import load_senate_races, load_generic_ballot

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
MAX_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "40"))

TOOLS = [
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 12},
    {
        "name": "refresh_generic_ballot",
        "description": (
            "Overwrite the current generic congressional ballot average. "
            "Call this once you've looked at 2-3 current tracker pages "
            "(RealClearPolling, Silver Bulletin, a pollster aggregator) and "
            "averaged their reported topline Dem-Rep margins."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "new_margin": {
                    "type": "number",
                    "description": "Dem-Rep margin in points; positive = Dem lead, e.g. 5.5 means D+5.5",
                },
                "source_notes": {"type": "string", "description": "1-2 sentences on how you computed it"},
                "sources": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["new_margin", "source_notes", "sources"],
        },
    },
    {
        "name": "refresh_senate_rating",
        "description": (
            "Change a single Senate race's rating. Only call this when you found "
            "SPECIFIC news of a rating change from a named outlet (AP, The Hill, "
            "Cook Political Report coverage, etc.), cited in the note. Do not call "
            "this speculatively - most days, no race changes rating."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "seat_id": {"type": "string", "description": "e.g. 'IA-2026'"},
                "new_rating": {
                    "type": "string",
                    "enum": ["solid", "likely", "lean", "tossup"],
                },
                "note": {"type": "string", "description": "What changed and the cited source"},
            },
            "required": ["seat_id", "new_rating", "note"],
        },
    },
    {
        "name": "update_atmospherics",
        "description": (
            "Record today's atmospherics adjustment for one toss-up/lean Senate "
            "race, after reading real news/campaign-trail coverage via web_search. "
            "adjustment_dem is a probability nudge in [-0.08, 0.08] applied on top "
            "of the polling/fundamentals number - positive favors the Dem "
            "candidate. Call this once per race that needs assessment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "seat_id": {"type": "string"},
                "adjustment_dem": {"type": "number", "description": "-0.08 to 0.08"},
                "rationale": {"type": "string", "description": "2-3 sentences citing what you read"},
                "sources": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["seat_id", "adjustment_dem", "rationale", "sources"],
        },
    },
]


def execute_tool(name: str, tool_input: dict) -> str:
    print(f"  -> tool call: {name}({json.dumps(tool_input)[:200]})")
    try:
        if name == "refresh_generic_ballot":
            result = ingest.refresh_generic_ballot(
                new_margin=tool_input["new_margin"],
                source_notes=tool_input["source_notes"],
                sources=tool_input["sources"],
            )
        elif name == "refresh_senate_rating":
            result = ingest.refresh_senate_rating(
                seat_id=tool_input["seat_id"],
                new_rating=tool_input["new_rating"],
                note=tool_input["note"],
            )
        elif name == "update_atmospherics":
            result = atmospherics.update_atmospherics(
                seat_id=tool_input["seat_id"],
                adjustment_dem=tool_input["adjustment_dem"],
                rationale=tool_input["rationale"],
                sources=tool_input["sources"],
            )
        else:
            return f"Unknown tool: {name}"
        return json.dumps(result)
    except Exception as e:
        return f"ERROR: {e}"


def build_prompt() -> str:
    races = load_senate_races()["races"]
    gb = load_generic_ballot()
    needs = atmospherics.races_needing_assessment()
    tossup_lean = [r for r in races if r["rating"] in ("tossup", "lean")]

    race_lines = "\n".join(
        f"  - {r['seat_id']} ({r['state']}): {r['incumbent']} ({r['held_by']}), "
        f"rating={r['rating']}, notes={r.get('notes', '')}"
        for r in tossup_lean
    )

    return f"""You are running the daily update for a Senate/House election
forecasting pipeline. Today's date matters - use it in your searches.

Current generic ballot: D+{gb['dem_margin_points']} (as of {gb['as_of']})

Current toss-up/lean Senate races (the ones atmospherics applies to):
{race_lines}

Do the following, in order, using web_search before every data-changing
tool call - never call a tool from memory alone:

1. Search for the current generic congressional ballot average from 2-3
   trackers (RealClearPolling, Silver Bulletin, a pollster aggregator).
   Average their topline Dem-Rep margins and call refresh_generic_ballot.

2. Search for news of any Senate race rating CHANGES (a race moving
   between solid/likely/lean/tossup) since {gb['as_of']}. Only call
   refresh_senate_rating if you find a specific, named-outlet citation of
   a change - do not guess.

3. For each toss-up/lean race listed above, search for recent
   poll/news/campaign-trail coverage and call update_atmospherics with a
   bounded adjustment (-0.08 to 0.08), citing the URLs you actually used.
   Seats you don't call this for will be treated as "not yet assessed" -
   only skip a seat if you genuinely found nothing new.

When you've made all the calls you need, reply with a short plain-text
summary of what you changed and why. Do not call the same tool for the
same seat_id twice."""


def main():
    import anthropic

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": build_prompt()}]

    for turn in range(MAX_TURNS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(block.text)

        if response.stop_reason != "tool_use":
            print(f"\nDone after {turn + 1} turn(s). stop_reason={response.stop_reason}")
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result_text = execute_tool(block.name, block.input)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result_text}
                )
        messages.append({"role": "user", "content": tool_results})
    else:
        print(f"\nStopped after hitting MAX_TURNS={MAX_TURNS}.")


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set - skipping agentic update (data files left as-is).")
        sys.exit(0)
    main()
