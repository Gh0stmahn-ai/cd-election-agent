# 2026 midterm forecast

A daily, self-updating forecast of who controls Congress after November 3,
2026: all 35 Senate races and all 435 House districts, simulated 100,000
times a day, plus the economic and political readings voters carry into
the booth.

**Live site: https://gh0stmahn-ai.github.io/** (rebuilt and republished
automatically after every daily run; also mirrored at
https://gh0stmahn-ai.github.io/cd-election-agent/).

The site is built into `site/` as six pages that share one stylesheet and
one script, with each page embedding the data it needs, so it works from a
web server or straight off disk with no CDN, no fetches and no libraries:

| Page | What is on it |
|---|---|
| `index.html` | Control odds for both chambers, the four possible outcomes, seat charts, and how the national environment is built |
| `senate.html` | Map of all 35 races, races to watch, seat distribution, every race in a table |
| `house.html` | Hexagon map of all 435 districts, seat distribution, filterable race tables |
| `economy.html` | Every economic and political reading, with sources, and what each one does to the forecast |
| `trend.html` | How the forecast has moved, and a measured day-by-day account of what moved it |
| `markets.html` | Kalshi and Polymarket prices next to the model, and why they are never blended |
| `methodology.html` | Where every number comes from, how it is validated, and the honest limits |

## How the forecast works

1. **National environment** = generic-ballot polling average blended with
   a fundamentals estimate (midterm penalty + presidential approval + an
   economic index). Polls get more weight as Election Day nears (82% in
   mid-September, rising to 95%).
2. **Race baselines** come from Cook Political Report ratings, converted
   to expected margins (Solid 18 pts, Likely 9, Lean 4.5, Toss-up 0.5),
   shifted by how far the environment has moved since the rating was set.
   Competitive **Senate** races then blend that with their own state polling
   average, weighted by how many aggregators cover the race and how close
   Election Day is. **House** seats are separated within their rating by
   Cook PVI, centred so the rating's average is preserved.
4. **News momentum** (Senate toss-up/lean races only): a capped +/-0.08
   win-probability adjustment from an AI read of campaign coverage, kept
   separate and shown in each race's tooltip.
5. **Monte Carlo simulation** with one national polling miss shared by
   every race in both chambers plus race-level noise, so the chambers move
   together the way they do in reality.

Democrats need 218 House seats and 51 Senate seats (Vice President Vance
breaks a 50-50 tie). An independent win in Nebraska (Dan Osborn) is not
counted toward Democratic control.

### The economic index

Each reading is scored from -1 (hurts the president's party) to +1 (helps)
against a neutral benchmark, then weighted:

| Indicator | Weight | Neutral point |
|---|---|---|
| Gas price, change vs a year ago | 18% | flat |
| CPI inflation | 18% | 2.5% |
| Consumer sentiment (UMich) | 18% | 75 |
| Real wage growth | 10% | +1.0% |
| Unemployment | 10% | 4.5% |
| GDP growth | 8% | 2.0% |
| 30-yr mortgage rate | 7% | 6.0% |
| S&P 500, year to date | 5% | +5% |
| 10-yr Treasury yield | 3% | 4.0% |

Oil is shown for context but not scored (its effect already runs through
gas and inflation). An index of -1 adds 3 points to the out-party's margin
in the fundamentals estimate; approval adds 0.2 points per point of net
approval. Settings live at the top of `model.py` and in
`data/fundamentals_2026.json`.

## Refreshing it

**Press Refresh in the site header.** It opens this repo's
`Daily election forecast update` workflow; click **Run workflow** and the
whole chain runs in about two minutes. The same chain runs on its own every
day at 16:00 UTC (noon Eastern in daylight time).

The run form has three optional boxes:

| Box | What it does |
|---|---|
| Generic ballot | Type today's average, e.g. `D+6.6` or `R+2.1`. Blank leaves it alone. |
| Approval | Type `approve/disapprove`, e.g. `38/60`. Blank leaves it alone. |
| AI refresh | Off by default. **This is the only thing that costs money.** |

### What a run does

1. `refresh_economy.py` - **free, no API key.** Pulls all ten economic
   readings from the agencies that publish them (BLS, BEA, EIA, Treasury,
   Freddie Mac, University of Michigan), read through the St. Louis Fed's
   FRED mirror, which serves each series as keyless CSV. Derived figures
   (CPI inflation, real wages, year-to-date return) are computed here. It
   never replaces a newer figure with an older one.
2. `refresh_markets.py` - **free, no API key.** Kalshi and Polymarket
   prices for chamber control, seat counts, the House popular vote margin
   and every traded Senate race. These are **shown next to the model on the
   markets page and never blended into it**: a prediction market is mostly a
   weighted digest of the same polls and ratings the model already reads, so
   averaging the two would double count while importing the market's own
   biases. The disagreement is the useful part.
3. `set_polls.py` - writes the generic ballot and approval you typed, if
   any. Polling averages live on pages, not in a data feed, so this is the
   free way to keep them current.
4. `agent_run.py` - the AI refresh, and the only paid step. One Claude
   conversation with web search reads polling pages, Cook rating changes
   and Senate campaign coverage. Skipped unless you tick the box, or set a
   repository variable `AI_REFRESH=true` to let scheduled runs use it.
   Needs the `ANTHROPIC_API_KEY` secret and credit on that account.
5. `run_pipeline.py` - runs the model and saves `iterations/<timestamp>.json`
   with a status block that flags stale inputs.
6. `build_site.py` - rebuilds the seven pages in `site/` from
   `site_template/` and every snapshot.
7. Commits and pushes the results (rebasing and retrying if the branch
   moved during the run).
8. Publishes `site/` to GitHub Pages (`deploy` job).
   `.github/workflows/pages.yml` also republishes whenever a person pushes
   a change to `site/`.

Every write in steps 1 to 4 goes through a validated function in
`ingest.py` / `atmospherics.py` (range checks, known ids), never a raw file
edit. Steps 1, 2 and 4 soft-fail: if a source is unreachable the run still
simulates and republishes on the freshest data it has, and each reading
shows its own as-of date on the site.

## Hosting

Two free GitHub Pages sites serve the same `site/` folder:

- **https://gh0stmahn-ai.github.io/** comes from the separate repo
  `Gh0stmahn-ai.github.io`, whose only job is a scheduled workflow that
  clones this repo, copies `site/` and deploys it (12:40 ET daily, 40
  minutes after the forecast run). No tokens needed: this repo is public.
- **https://gh0stmahn-ai.github.io/cd-election-agent/** is this repo's own
  Pages deployment, as a mirror.

One-time setup in each repo: Settings -> Pages -> Build and deployment ->
Source: **GitHub Actions**.

No secrets are needed for a free run. `ANTHROPIC_API_KEY` (a
workspace-scoped key from console.anthropic.com) is only for the optional
AI refresh; without it, or without credit on it, that step is skipped and
everything else still runs.

## Files

```
data/
  generic_ballot_2026.json    generic ballot average
  fundamentals_2026.json      economic + political indicators, sources, scoring
  senate_races_2026.json      35 races: rating, lean, nominees, seats not up
  house_districts_2026.json   all 435 districts: rating, lean, holder
  atmospherics_2026.json      capped news-momentum reads (Senate)
geo/
  states.json                 state shapes, pre-projected to SVG paths
  house_hex.json              hexagon layout for 435 districts
  source/                     Natural Earth 1:50m states (public domain)
tools/
  build_geometry.py           one-time build of the two geo files
  seed_house_ratings.py       one-time seed of the 435-district file
site_template/
  assets/style.css            shared stylesheet
  assets/app.js               shared charts, maps and tables
site/                         the built website (committed, served by Pages)
assets/og.png                 link-preview image
model.py                      environment + simulation
run_pipeline.py               model -> iterations/ snapshot
build_site.py                 snapshots + templates -> site/
refresh_economy.py            free economic refresh from public data series
refresh_markets.py            free prediction market prices (shown, never blended)
refresh_polls.py              free generic ballot + approval from Wikipedia aggregators
refresh_senate_polls.py       free state polling for the competitive Senate races
tools/fetch_pvi.py            one-time pull of Cook PVI for all 435 districts
refresh_attention.py          free Wikipedia readership per candidate (shown, never blended)
attribution.py                re-runs the model per input to explain each day's move
set_polls.py                  manual generic-ballot / approval entry
agent_run.py                  optional AI refresh (ratings, Senate news)
ingest.py                     the validated write functions all three use
```

## Honest limits

- **House district shapes.** No public source publishes 2026 district
  boundaries for the ten states that redrew mid-decade (TX, CA, FL, OH, NC,
  MO, UT, TN, LA, AL), and every seat counts the same toward 218, so the
  House map is a hexagon cartogram rather than a geographic district map.
  Where a district sits inside its state on that map is schematic.
- **Solid seats.** Cook lists 77 competitive House races; every other seat
  is treated as Solid for the party that holds, or was drawn to win, it.
  Florida's district numbers for solid seats under its May 2026 map are
  best-available.
- **No district polling.** District-level polling at 435-seat scale isn't
  public; the House forecast rests on ratings plus the national
  environment.
- **Not a certainty.** A 70% chance loses 3 times in 10. The shared
  national error is there precisely so the forecast doesn't look more
  confident than the data supports.
- **Fresh data depends on outside sites.** The pipeline can't guarantee a
  source published something new today; it flags stale inputs in each
  snapshot's status and on the dashboard instead of hiding them.

Iterations written before the Sept 11, 2026 model upgrade used an earlier,
simpler model; they stay in `iterations/` for the record but aren't
charted.
