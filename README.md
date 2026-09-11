# 2026 midterm election forecasting agent

A working pipeline: ingest data -> model each race -> simulate outcomes ->
render on an interactive map that can replay how the forecast moved
across iterations.

## Run it

```bash
python3 run_pipeline.py
```

This reads `data/senate_races_2026.json` and `data/generic_ballot_2026.json`,
runs a 20,000-trial Monte Carlo simulation, and writes a timestamped
snapshot to `iterations/`. Run it again after `ingest.py` refreshes the
data to build up history for the dashboard slider.

Open `dashboard.html` in a browser — it's fully self-contained (the
iteration history is embedded, and the US map geometry loads from a
public CDN, so it works offline except for the map shapes themselves).

## What's real vs. what's a placeholder

**Real, sourced data (as of Sept 10, 2026):**
- All 35 Senate races, current ratings from Cook Political Report /
  270toWin (solid/likely/lean/toss-up), including the Aug 20, 2026 moves
  of Iowa and Texas to toss-up
- Generic congressional ballot average (D+5.5), from RealClearPolling,
  Silver Bulletin, and Morning Consult trackers

**Modeled, not fabricated:**
- Win probabilities per Senate seat: a documented rating-to-probability
  mapping, nudged by the national environment for toss-ups only, run
  through Monte Carlo simulation with correlated error across races
- House forecast: a **national swing model** from the generic ballot
  only, not a per-district forecast

**Known limitation — House district data:** Cook's district-level House
ratings (all 435 seats) are subscriber-only, and free per-district
polling/PVI data at that granularity isn't reliably available without a
paid source. Rather than invent specific district ratings, this pipeline
computes House control probability from the national vote-to-seat
relationship only. To get a real district-level House map, add a
`data/house_districts.json` file (same shape as the Senate file) from
a source you have access to, and `model.py`'s Monte Carlo function can
be extended to simulate it the same way as the Senate.

## Architecture

```
data sources (free/public) -> ingest.py -> data/*.json
                                              |
                                              v
                                          model.py
                                  (ratings -> probabilities
                                   -> Monte Carlo simulation)
                                              |
                                              v
                                     run_pipeline.py
                                (saves iterations/<timestamp>.json)
                                              |
                                              v
                                      dashboard.html
                            (US map colored by seat probability,
                             iteration slider, House gauge)
```

## Atmospherics layer (news/narrative sentiment)

`data/atmospherics_2026.json` holds a bounded, separately-tracked
adjustment (max +/-0.08 win probability) for toss-up/lean races only,
based on news coverage and campaign-trail reporting rather than polls.
It's applied on top of the polling+fundamentals number in `model.py`,
never blended invisibly - the dashboard and the JSON both show it as
its own line so you can always see how much it moved the forecast.

Two races (Georgia, Ohio) are live-assessed right now as a working
demonstration, with real sources cited in the file. `atmospherics.py`
defines the schema and update function; the actual "read the news and
judge momentum" step needs an LLM in the loop (see below) - it's not
something a fixed script can do well.

**Honest limits:** this reads news coverage and public campaign
reporting, not a live social media firehose - X/Twitter's and Reddit's
APIs both require paid access for structured data now, so "social
media" here means what search engines surface (viral moments picked up
by news outlets, some indexed forum discussion), not a comprehensive
social listening tool.

## Actioning this in Claude: the daily noon run

The realistic, current way to do this **inside Claude itself**, no
external server required, is **Claude Cowork's scheduled tasks**
feature (Settings > available in Cowork on Claude Desktop, Pro/Max/
Team/Enterprise). It runs a saved prompt on a cadence you set,
including web research, and can use connectors to read/write files.

Setup:
1. Put this whole project in a GitHub repo (or a connected Google
   Drive folder) so the scheduled task has somewhere persistent to
   read and write - Cowork sessions don't keep local files between
   runs on their own.
2. Connect the GitHub (or Drive) connector in Cowork.
3. Create a scheduled task with a cadence of **daily at 12:00 PM**
   and a prompt along these lines:

   > Pull the latest `election-agent` project from [repo/folder].
   > For each toss-up/lean Senate race in `data/senate_races_2026.json`,
   > web-search recent news and polling coverage, and update
   > `data/atmospherics_2026.json` following the schema and the +/-0.08
   > cap already defined there (use `atmospherics.py`'s
   > `update_atmospherics` contract). Also refresh
   > `data/generic_ballot_2026.json` from current generic-ballot
   > tracker pages. Then run `run_pipeline.py` to produce today's
   > snapshot, regenerate `dashboard.html`, and commit everything back.

4. Cowork notifies you when each run finishes so you can spot-check
   it rather than trusting it blindly.

**More robust alternative (if you want it running even when nothing's
open):** Claude Code Desktop's **remote routines** run in the cloud on
a real cron schedule and can trigger off GitHub events - this is the
better fit if you want zero-touch daily execution tied to a repo. Same
prompt, different scheduler.

**On "100% guaranteed" fresh data:** no pipeline that depends on
external websites can be literally guaranteed - a source can go down,
change its page structure, or simply not have published anything new
that day. What's realistic to guarantee is that the pipeline **fails
loudly**: have the daily task write a `status` field (success/partial/
failed, with what broke) into each day's snapshot, and check that
before trusting a number. I'd build that in as the next step rather
than pretend around the limitation.

## GitHub Actions automation (implemented)

`.github/workflows/daily-forecast.yml` runs the whole daily cycle on a
schedule, inside this repo, with no external server or Cowork session
required:

1. **`agent_run.py`** — one Claude conversation, given the current data
   files, with the built-in web-search tool plus three custom tools
   wired directly to `ingest.refresh_generic_ballot`,
   `ingest.refresh_senate_rating`, and `atmospherics.update_atmospherics`.
   Claude has to actually call `web_search` before it can call any of
   the data-changing tools — nothing gets written from memory. Rating
   changes only get written when Claude cites a specific, named-outlet
   source; every atmospherics call is capped at the same ±0.08 this
   README already documents. Bounded to 40 tool-use turns per run so a
   confused run can't loop forever.
2. **`run_pipeline.py`** — same Monte Carlo pipeline described above,
   run against whatever `agent_run.py` just wrote (or against
   yesterday's data, unchanged, if the agent step was skipped).
3. **`build_dashboard.py`** — folds every file in `iterations/` into
   `dashboard.html`'s embedded `ITERATIONS` array, so the dashboard's
   replay slider always reflects the full history in the repo.
4. Commits and pushes `data/`, `iterations/`, and `dashboard.html` back
   to `main` if anything changed.

**Setup required:**
- Add a repo secret named `ANTHROPIC_API_KEY` (Settings → Secrets and
  variables → Actions → New repository secret) with an API key from
  [console.anthropic.com](https://console.anthropic.com). Without it,
  `agent_run.py` prints a notice and exits cleanly — `run_pipeline.py`
  and `build_dashboard.py` still run against the last-known data, so a
  missing key degrades gracefully rather than breaking the whole run.
- The workflow's default `contents: write` permission is enough to
  push back to `main`; no extra token needed beyond the built-in
  `GITHUB_TOKEN`.
- Schedule is `0 16 * * *` (16:00 UTC = noon Eastern during EDT; see
  the comment in the workflow file about the EST drift). Change the
  cron line or trigger a one-off run from the Actions tab
  (`workflow_dispatch`) to test it without waiting for the clock.

**Cost/scope note:** each run does up to ~1-8 web searches per
toss-up/lean race plus a couple more for the generic ballot, all in one
conversation — cheap relative to a single day's polling-aggregator
subscription, but not free. If you'd rather not wire in an API key,
delete/disable the workflow and use the Cowork scheduled-task or
Claude Code remote-routine paths described above instead — same
`update_atmospherics`/`refresh_generic_ballot`/`refresh_senate_rating`
contracts either way.

## Caveats worth keeping in mind

- Polling misses have gone in different directions in different recent
  cycles (2016, 2020, 2022) — the correlated-error Monte Carlo is
  designed to reflect that uncertainty honestly rather than look more
  confident than the data supports.
- Treat every output as a probability from a stated, inspectable model,
  not a prediction of certainty.
