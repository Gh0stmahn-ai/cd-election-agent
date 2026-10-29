# Backtest

Rebuilds seven cycles, 2012 through 2024, from the inputs the live model takes
-- the final Cook ratings published days before each election, and the Cook PVI
in force that cycle -- then grades the shipped baseline against what actually
happened.

Two tiers, because the sources support two different things. Every cycle has a
ratings article, and a ratings article by construction lists the seats whose
outcome was in doubt, so the competitive set spans all seven. A PVI for all 435
districts is published only for 2018, 2020 and 2022, so those are the only
cycles that can say anything about safe seats.

Everything here is pulled from Wikipedia, which keeps per-cycle articles with
each district's rating, index and result. Raw wikitext is cached in `raw/`
(gitignored) so a re-run costs nothing.

```
python3 fetch_history.py    # district results + PVI, 2012-2024   -> house_history.json
python3 fetch_ratings.py    # final Cook ratings, competitive seats -> house_ratings_history.json
python3 fetch_senate.py     # Senate ratings + results            -> senate_history.json
python3 races.py            # what the two tiers add up to, per cycle
python3 report.py           # grade the model -> backtest_summary.json, ../data/backtest_2018_2022.json
python3 fetch_governors.py  # governor ratings + results, 2010-2024 -> governor_history.json
python3 governors.py        # governor buckets against the Senate's -> governor_fit.json
```

`report.py` is the one that matters: it writes the summary the methodology
page renders, and it compares the current constants in `model.py` against the
pre-backtest ones, so editing a constant and re-running it shows immediately
whether the change was an improvement.

Two things to keep in mind when reading the output. Seven observations is still
seven, so the national error is a floor rather than a precise estimate. And the
ratings articles list only seats that some rater called competitive, so in the
three full cycles every unlisted seat is entered as Solid for the party that
held it -- true in almost every case, and wrong in about fifteen.

Governors are calibrated the same way from eight cycles (2010 to 2024, 177
rated races). `governors.py` measures where each Cook bucket lands for
governors against where it lands for the Senate, the spread around that once
each cycle's average miss is removed, and how far the cycle's average governor
miss follows the Senate's. Those three numbers set the governor constants in
`model.py`.

A correlated miss along state partisanship (polls or ratings missing the same
way in every red state, or every blue one, in a given year) was tested on the
2014-2024 Senate and governor races. Across six cycles the slope of the
cycle-adjusted miss on Cook PVI in competitive races varied by 0.17 per PVI
point against a sampling error of 0.16, an implied factor of 0.08 per point:
under a point of extra correlated error even in a state at PVI 10. Too small
and too uncertain to add, so the model does not carry it. The education
version of the same test (non-college white share) needs Census data that now
requires an API key, and was not run.
