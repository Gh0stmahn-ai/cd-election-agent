# Backtest

Rebuilds 2018, 2020 and 2022 from the inputs the live model takes -- the final
Cook ratings published days before each election, and the Cook PVI in force
that cycle -- then grades the shipped baseline against what actually happened.

Everything here is pulled from Wikipedia, which keeps per-cycle articles with
each district's rating, index and result. Raw wikitext is cached in `raw/`
(gitignored) so a re-run costs nothing.

```
python3 fetch_history.py    # district results + PVI, 2014-2024   -> house_history.json
python3 fetch_ratings.py    # final Cook ratings, competitive seats -> house_ratings_history.json
python3 fetch_senate.py     # Senate ratings + results            -> senate_history.json
python3 backtest_house.py   # House-only diagnostics
python3 report.py           # grade the model -> backtest_summary.json, ../data/backtest_2018_2022.json
```

`report.py` is the one that matters: it writes the summary the methodology
page renders, and it compares the current constants in `model.py` against the
pre-backtest ones, so editing a constant and re-running it shows immediately
whether the change was an improvement.

Two things to keep in mind when reading the output. Three cycles is three
observations of the national error, so that figure is a floor rather than a
precise estimate. And the ratings articles list only seats that some rater
called competitive, so every unlisted seat is entered as Solid for the party
that held it -- true in almost every case, and wrong in about fifteen.
