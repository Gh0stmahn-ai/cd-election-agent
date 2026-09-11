#!/usr/bin/env python3
"""
Daily agentic update for the election forecasting pipeline.

Runs one Claude conversation, given today's data files, with the built-in
web_search tool plus custom tools that map one-to-one onto the write
functions in ingest.py / atmospherics.py. Nothing is written except through
those functions, which validate ranges and ids.

Each day Claude is asked to:
  1. Refresh the generic congressional ballot average
  2. Refresh presidential approval
  3. Refresh the economic readings voters feel: gas, oil, CPI, consumer
     sentiment, real wages, unemployment, GDP, mortgage rates, the 10-year
     Treasury and the S&P 500 (only when a newer figure has been published)
  4. Apply Senate or House rating CHANGES, only with a named-outlet citation
  5. Write a fresh, capped (+/-0.08) news-momentum read for each toss-up /
     lean Senate race

Bounded by MAX_TURNS and a web-search budget so a confused run can't loop
forever or run up cost. Requires ANTHROPIC_API_KEY; without it the script
exits cleanly and the rest of the pipeline runs on the last-known data.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import atmospherics  # noqa: E402
import ingest  # noqa: E402
from model import load_fundamentals, load_generic_ballot, load_house_districts, load_senate_races  # noqa: E402

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
MAX_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "60"))
SEARCH_BUDGET = int(os.environ.get("AGENT_SEARCH_BUDGET", "30"))

SOURCE_ITEMS = {"type": "array", "items": {"type": "object", "properties": {
    "name": {"type": "string"}, "url": {"type": "string"}}, "required": ["name", "url"]}}

TOOLS = [
    {"type": "web_search_20250305", "name": "web_search", "max_uses": SEARCH_BUDGET},
    {
        "name": "refresh_generic_ballot",
        "description": "Overwrite the generic congressional ballot average after averaging 2-3 current public trackers.",
        "input_schema": {"type": "object", "properties": {
            "new_margin": {"type": "number", "description": "Dem minus Rep, points. 5.5 means D+5.5"},
            "source_notes": {"type": "string"},
            "sources": {"type": "array", "items": {"type": "string"}}},
            "required": ["new_margin", "source_notes", "sources"]},
    },
    {
        "name": "refresh_approval",
        "description": "Update the president's job approval average (average of 2+ public aggregates if available).",
        "input_schema": {"type": "object", "properties": {
            "approve": {"type": "number"}, "disapprove": {"type": "number"},
            "net": {"type": "number", "description": "approve minus disapprove; the model input"},
            "as_of": {"type": "string", "description": "YYYY-MM-DD"},
            "context": {"type": "string", "description": "1-2 sentences naming the averages used"},
            "sources": SOURCE_ITEMS},
            "required": ["approve", "disapprove", "net", "as_of", "context", "sources"]},
    },
    {
        "name": "refresh_fundamental",
        "description": (
            "Update one economic indicator when a NEWER figure than the one on file has been published. "
            "ids: gas_price ($/gal national average; compare_value = price a year ago), oil_wti ($/bbl; compare = Brent), "
            "cpi_inflation (headline CPI % y/y; compare = core % y/y), consumer_sentiment (UMich index; compare = year-ago "
            "value), real_wages (avg hourly earnings % y/y minus CPI % y/y; compare = nominal wage growth), unemployment "
            "(rate %; compare = monthly payroll change in thousands), gdp_growth (latest real GDP % annualized; compare = "
            "prior quarter), mortgage_rate (Freddie Mac 30-yr %; compare = year-ago rate), treasury_10y (10-yr yield %; "
            "compare = 2-yr yield), sp500 (index close; compare_value MUST be the year-to-date % change). "
            "display is the short headline value, e.g. '$4.30' or '3.4%'."),
        "input_schema": {"type": "object", "properties": {
            "indicator_id": {"type": "string"},
            "value": {"type": "number"}, "display": {"type": "string"},
            "as_of": {"type": "string", "description": "date or period of the figure, e.g. 2026-09-12 or 2026-08"},
            "compare_value": {"type": "number"}, "compare_display": {"type": "string"},
            "change_display": {"type": "string"}, "context": {"type": "string"},
            "sources": SOURCE_ITEMS},
            "required": ["indicator_id", "value", "display", "as_of", "sources"]},
    },
    {
        "name": "refresh_senate_rating",
        "description": ("Change one Senate race's rating. ONLY with specific news of a rating change from a named "
                        "forecaster (Cook, Sabato, Inside Elections), cited in the note. Most days nothing changes."),
        "input_schema": {"type": "object", "properties": {
            "seat_id": {"type": "string"}, "new_rating": {"type": "string", "enum": list(ingest.RATINGS)},
            "lean": {"type": "string", "enum": ["D", "R"], "description": "party favored (for toss-ups, the holder)"},
            "note": {"type": "string"}},
            "required": ["seat_id", "new_rating", "note"]},
    },
    {
        "name": "refresh_house_rating",
        "description": ("Change one House district's rating, ONLY with a cited rating change from Cook Political "
                        "Report (the model's reference rater). district_id format: 'PA-7', 'AK-AL'."),
        "input_schema": {"type": "object", "properties": {
            "district_id": {"type": "string"}, "new_rating": {"type": "string", "enum": list(ingest.RATINGS)},
            "lean": {"type": "string", "enum": ["D", "R"]}, "note": {"type": "string"}},
            "required": ["district_id", "new_rating", "lean", "note"]},
    },
    {
        "name": "update_atmospherics",
        "description": ("Record today's news-momentum adjustment for one toss-up/lean Senate race after reading real "
                        "coverage via web_search. adjustment_dem in [-0.08, 0.08]; positive favors the Democratic "
                        "(or anti-GOP) candidate."),
        "input_schema": {"type": "object", "properties": {
            "seat_id": {"type": "string"}, "adjustment_dem": {"type": "number"},
            "rationale": {"type": "string", "description": "2-3 sentences citing what you read"},
            "sources": {"type": "array", "items": {"type": "string"}}},
            "required": ["seat_id", "adjustment_dem", "rationale", "sources"]},
    },
]

FUNCS = {
    "refresh_generic_ballot": ingest.refresh_generic_ballot,
    "refresh_approval": ingest.refresh_approval,
    "refresh_fundamental": ingest.refresh_fundamental,
    "refresh_senate_rating": ingest.refresh_senate_rating,
    "refresh_house_rating": ingest.refresh_house_rating,
    "update_atmospherics": atmospherics.update_atmospherics,
}


def execute_tool(name: str, tool_input: dict) -> str:
    print(f"  -> tool call: {name}({json.dumps(tool_input)[:240]})")
    fn = FUNCS.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    try:
        result = fn(**tool_input)
        if name == "update_atmospherics":  # don't echo the whole file back into the conversation
            saved = next(a for a in result["assessments"] if a["seat_id"] == tool_input["seat_id"])
            result = {"ok": True, "seat_id": saved["seat_id"], "adjustment_dem": saved["adjustment_dem"]}
        return json.dumps(result)
    except Exception as e:  # noqa: BLE001 - surface the validation error to the model
        return f"ERROR: {e}"


def build_prompt() -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    races = load_senate_races()["races"]
    gb = load_generic_ballot()
    fund = load_fundamentals()
    house = load_house_districts()["districts"]
    tossup_lean = [r for r in races if r["rating"] in ("tossup", "lean")]
    race_lines = "\n".join(
        f"  - {r['seat_id']}: {r.get('dem_candidate') or 'D nominee'} vs {r.get('rep_candidate') or 'R nominee'}, "
        f"held by {r['held_by']}, Cook {r['rating']} {r['lean']}" for r in tossup_lean)
    ind_lines = "\n".join(
        f"  - {i['id']}: {i['display']} (as of {i['as_of']}; compare {i.get('compare_display', '')})"
        for i in fund["indicators"])
    approval = next(p for p in fund["political"] if p["id"] == "approval")
    competitive = [d for d in house if d["rating"] != "solid"]
    house_lines = ", ".join(f"{d['id']} {d['rating']}-{d['lean']}" for d in competitive)

    return f"""Today is {today}. You are running the daily data refresh for a 2026 midterm forecast.
Use web_search before every data-changing tool call; never write a number from memory. You have a
budget of about {SEARCH_BUDGET} searches, so combine lookups where one page covers several items.

CURRENT DATA ON FILE
Generic ballot: D{gb['dem_margin_points']:+} (as of {gb['as_of']})
Presidential approval: {approval['display']} net {approval['value_net']} (as of {approval['as_of']})
Economic indicators:
{ind_lines}
Toss-up/lean Senate races:
{race_lines}
Competitive House ratings on file (Cook): {house_lines}

TASKS, IN ORDER
1. Generic ballot: average the toplines of 2-3 current public trackers; call refresh_generic_ballot.
2. Approval: average 2+ public approval aggregates; call refresh_approval.
3. Economy: for each indicator, update ONLY if a newer figure than the one on file exists (daily
   market data like gas, oil, yields, mortgage rates, the S&P 500 usually will; monthly releases
   like CPI, jobs, sentiment, GDP only after their release dates). Call refresh_fundamental.
4. Ratings: search for Senate or House rating CHANGES by Cook Political Report since the dates on
   file. Only call refresh_senate_rating / refresh_house_rating with a specific cited change.
5. News momentum: for each toss-up/lean Senate race above, read recent polling/campaign coverage and
   call update_atmospherics with a bounded adjustment (-0.08 to 0.08), citing URLs you used.

Finish with a short plain-text summary of what changed and why. Never call the same tool twice
for the same indicator, race or district."""


def main():
    import anthropic

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": build_prompt()}]

    for turn in range(MAX_TURNS):
        response = client.messages.create(model=MODEL, max_tokens=4096, tools=TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": response.content})
        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(block.text)
        if response.stop_reason != "tool_use":
            print(f"\nDone after {turn + 1} turn(s). stop_reason={response.stop_reason}")
            break
        results = [{"type": "tool_result", "tool_use_id": b.id, "content": execute_tool(b.name, b.input)}
                   for b in response.content if b.type == "tool_use"]
        messages.append({"role": "user", "content": results})
    else:
        print(f"\nStopped after hitting MAX_TURNS={MAX_TURNS}.")


def validate_api_key(key: str) -> str:
    """Fail fast with a readable message instead of a cryptic httpx crash when
    the secret holds something other than a bare API key."""
    key = key.strip()
    if "\n" in key or "\r" in key or " " in key or not key.startswith("sk-ant-"):
        print(
            "ANTHROPIC_API_KEY doesn't look like a valid key - expected a single line starting with "
            "'sk-ant-' and nothing else. Fix: copy ONLY the key value from "
            "https://console.anthropic.com/settings/keys (create it inside a workspace), then update the "
            "ANTHROPIC_API_KEY secret at Settings -> Secrets and variables -> Actions in the repo.",
            file=sys.stderr,
        )
        sys.exit(1)
    return key


if __name__ == "__main__":
    raw_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not raw_key:
        print("ANTHROPIC_API_KEY not set - skipping agentic update (data files left as-is).")
        sys.exit(0)
    os.environ["ANTHROPIC_API_KEY"] = validate_api_key(raw_key)
    main()
