#!/usr/bin/env python3
"""
Offline checks for the election-night collector. No network: every fixture is
a trimmed copy of a real file from that source (2024 general or a 2026
primary/general shell), so a change in a parser shows up here before it shows
up on election night.

    python3 tests/test_results.py
"""
import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import results as R           # noqa: E402
import results_feeds as F     # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# ------------------------------------------------------------------ feeds
ga = {"election": {"asOf": "2024-11-06T03:00:00Z"}, "ballotItems": [
    {"name": [{"text": "US House of Representatives - District 1"}],
     "reportingStatus": {"reportingUnits": 12, "totalUnits": 15},
     "summaryResults": {"ballotOptions": [
         {"name": [{"text": 'Earl L. "Buddy" Carter (I) (Rep)'}], "voteCount": 220576, "party": {"abbreviation": "Rep"}},
         {"name": [{"text": "Patti Hewitt (Dem)"}], "voteCount": 135281, "party": {"abbreviation": "Dem"}}]}},
    {"name": [{"text": "US Senate"}], "reportingStatus": {"reportingUnits": 159, "totalUnits": 159},
     "summaryResults": {"ballotOptions": [
         {"name": [{"text": "Jon Ossoff (I) (Dem)"}], "voteCount": 10, "party": {"abbreviation": "Dem"}}]}},
    {"name": [{"text": "State Senate - District 1"}], "reportingStatus": {}, "summaryResults": {"ballotOptions": []}}]}
out = {c["race_id"]: c for c in F.parse_ga(ga, "GA-2026")}
check("GA: house district and senate seat mapped, state senate ignored", set(out) == {"GA-1", "GA-2026"}, set(out))
check("GA: names cleaned of (I) and party", out["GA-1"]["candidates"][0]["name"] == 'Earl L. "Buddy" Carter')
check("GA: reporting share", abs(out["GA-1"]["reporting"] - 0.8) < 1e-9)

nc_rows = [
    {"cnm": "US SENATE (VOTE FOR 1)", "bnm": "Roy Cooper", "pty": "DEM", "vct": "100", "prt": "10", "ptl": "20", "ogl": "FED"},
    {"cnm": "US SENATE (VOTE FOR 1)", "bnm": "Michael Whatley", "pty": "REP", "vct": "90", "prt": "10", "ptl": "20", "ogl": "FED"},
    {"cnm": "US HOUSE OF REPRESENTATIVES DISTRICT 01 (VOTE FOR 1)", "bnm": "Don Davis", "pty": "DEM", "vct": "5",
     "prt": "1", "ptl": "4", "ogl": "FED"},
    {"cnm": "NC HOUSE OF REPRESENTATIVES DISTRICT 001", "bnm": "X", "pty": "REP", "vct": "5", "prt": "1", "ptl": "4", "ogl": "NCH"}]
out = {c["race_id"]: c for c in F.parse_nc(nc_rows, "NC-2026", "November 3, 2026 8:59 pm")}
check("NC: federal contests only", set(out) == {"NC-2026", "NC-1"}, set(out))
check("NC: Eastern timestamp", out["NC-2026"]["as_of"].startswith("2026-11-03T20:59:00-05:00"), out["NC-2026"]["as_of"])

fed = {"Races": [{"N": "U. S. SENATOR ", "Candidates": [{"N": "KEN PAXTON", "P": "REP", "V": 3},
                                                        {"N": "JAMES TALARICO", "P": "DEM", "V": 4},
                                                        {"N": "WRITE IN", "P": "W", "V": 0}]},
                 {"N": "U. S. REPRESENTATIVE DISTRICT 28", "Candidates": [{"N": "HENRY CUELLAR (I)", "P": "DEM", "V": 1}]},
                 {"N": "STATE REPRESENTATIVE DISTRICT 28", "Candidates": []}]}
home = {"PollingReporting": {"PLR": 50, "PLT": 100}, "LastUpdatedTime": "Nov 03, 2026 20:10:00"}
bundle = {"Version": "enr/53815/2/", "Federal": base64.b64encode(json.dumps(fed).encode()).decode(),
          "Home": base64.b64encode(json.dumps(home).encode()).decode()}
out = {c["race_id"]: c for c in F.parse_tx(bundle, "TX-2026")}
check("TX: senate and district, write-ins dropped", set(out) == {"TX-2026", "TX-28"} and
      len(out["TX-2026"]["candidates"]) == 2, out.keys())
check("TX: incumbent marker removed", out["TX-28"]["candidates"][0]["name"] == "HENRY CUELLAR")

ia = {"Contests": [{"C": "United States Senator", "CH": ["A", "B"], "P": ["DEM", "REP"], "V": [5, 6], "TP": 99, "PR": 33},
                   {"C": "United States Representative District 1", "CH": ["C"], "P": ["REP"], "V": [1], "TP": 20, "PR": 20},
                   {"C": "United States Senator - Rep.", "CH": [], "P": [], "V": [], "TP": 1, "PR": 1}]}
out = {c["race_id"]: c for c in F.parse_ia(ia, "IA-2026")}
check("IA: general contests only", set(out) == {"IA-2026", "IA-1"}, set(out))

sw = ('RaceID,RaceName,PartyCode,AreaType,AreaNum,OfficeSeqNo,BallotOrder,CandidateID,CandidateName,CurrentDateTime,'
      'VoteFor,CandidateVotes,CandidatePercentage,PrecinctsReporting\n'
      '1,"For United States Senator - 6  Year Term","PET","SW","",1,,1,"Dan  Osborn ",x,1,120,0,="600/1322",\n'
      '1,"For United States Senator - 6  Year Term","REP","SW","",1,,2,"Pete  Ricketts ",x,1,100,0,="600/1322",\n')
cg = ('RaceID,RaceName,PartyCode,AreaType,AreaNum,OfficeSeqNo,BallotOrder,CandidateID,CandidateName,CurrentDateTime,'
      'VoteFor,CandidateVotes,CandidatePercentage,PrecinctsReporting\n'
      '2,"For Representative in Congress - 2  Year Term","DEM","CG","District 02",1,,3,"Denise  Powell ",x,1,9,0,="1/334",\n')
out = {c["race_id"]: c for c in F.parse_ne(sw, cg, "NE-2026")}
check("NE: senate and district 2", set(out) == {"NE-2026", "NE-2"}, set(out))
check("NE: independent kept as other party", out["NE-2026"]["candidates"][0]["party"] == "O")
check("NE: precinct share from the Excel-wrapped field", abs(out["NE-2026"]["reporting"] - 600 / 1322) < 1e-9)

ak = {"name": "Alaska 2026 General Election", "updated": "11/4/2026 1:30 AM",
      "contestNames": ["U.S. Senator", "U.S. Representative", "House District 1"],
      "candidateNames": ["Peltola, Mary (DEM)", "Sullivan, Dan S. (REP)", "Sullivan, Daniel J. Jr."],
      "totals": [{"n": 0, "p": 403, "r": 100, "c": [{"n": 0, "t": 10}, {"n": 1, "t": 9}, {"n": 2, "t": 1}]},
                 {"n": 2, "p": 1, "r": 1, "c": []}]}
out = {c["race_id"]: c for c in F.parse_ak(ak, "AK-2026")}
check("AK: senate only (state house ignored)", set(out) == {"AK-2026"}, set(out))
check("AK: party from the name, spoiler without one", [c["party"] for c in out["AK-2026"]["candidates"]] == ["D", "R", "O"])

fl = ("ElectionDate\tPartyCode\tPartyName\tRaceCode\tOfficeDesc\tCountyCode\tCountyName\tJuris1num\tJuris2num\tPrecincts\t"
      "PrecinctsReporting\tCanNameLast\tCanNameFirst\tCanNameMiddle\tCanVotes\n"
      "11/3/2026\tREP\tRepublican\tUSS\tUnited States Senator\tALA\tAlachua\t\t\t63\t30\tMoody\tAshley\t\t100\n"
      "11/3/2026\tDEM\tDemocrat\tUSS\tUnited States Senator\tALA\tAlachua\t\t\t63\t30\tNixon\tAngie\t\t120\n"
      "11/3/2026\tREP\tRepublican\tUSS\tUnited States Senator\tBAK\tBaker\t\t\t9\t9\tMoody\tAshley\t\t50\n"
      "11/3/2026\tWRI\tWrite-In\tUSS\tUnited States Senator\tBAK\tBaker\t\t\t9\t9\tX\tY\t\t1\n"
      "11/3/2026\tREP\tRepublican\tUSR\tUnited States Representative\tESC\tEscambia\t001\t\t80\t80\tPatronis\tJimmy\t\t5\n")
out = {c["race_id"]: c for c in F.parse_fl(fl, "FL-2026-special")}
check("FL: votes summed across counties", next(c["votes"] for c in out["FL-2026-special"]["candidates"]
                                                if c["party"] == "R") == 150)
check("FL: precincts counted once per county", abs(out["FL-2026-special"]["reporting"] - 39 / 72) < 1e-9)
check("FL: district 1", "FL-1" in out)

# -------------------------------------------------------------- wikipedia
senate_wiki = """
|-
! [[2026 United States Senate election in Georgia|Georgia]]
| {{Shading PVI|R|3}}
| nowrap | {{Plainlist |
*{{Party stripe|Democratic Party (US)}}{{Aye}} '''[[Jon Ossoff]]''' (Democratic) 52.1%<ref>x</ref>
*{{Party stripe|Republican Party (US)}}[[Mike Collins (politician)|Mike Collins]] (Republican) 46.9%
}}
|-
!rowspan=2 | [[2026 United States Senate special election in Ohio|Ohio]]<br />(Class 3)
| nowrap | {{Plainlist |
*{{Party stripe|Democratic Party (US)}}[[Sherrod Brown]] (Democratic)
*{{Party stripe|Republican Party (US)}}[[Jon Husted]] (Republican)<ref name="a" />
}}
"""
s = F.parse_wiki_senate(senate_wiki)
check("wiki senate: both states, attribute header", set(s) == {"GA", "OH"}, set(s))
ga_rows = s["GA"][0]
check("wiki senate: winner, party and share", ga_rows[0]["winner"] and ga_rows[0]["party"] == "D"
      and ga_rows[0]["pct"] == 52.1 and ga_rows[0]["name"] == "Jon Ossoff", ga_rows[0])
check("wiki senate: piped link name", ga_rows[1]["name"] == "Mike Collins")
house_wiki = """
|-
!rowspan=2 |{{ushr|AL|2|X}}
|nowrap |{{plainlist}}
*{{Party stripe|Democratic Party (US)}}[[Shomari Figures]] (Democratic)
*{{Party stripe|Republican Party (US)}}{{aye}} '''[[Rhett Marques]]''' (Republican) 50.2%
{{endplainlist}}
|-
!{{ushr|AK|AL|X}}
|nowrap |{{plainlist}}
*{{Party stripe|Independent (US)}}Bill Hill (Independent)
{{endplainlist}}
"""
h = F.parse_wiki_house(house_wiki)
check("wiki house: rowspan header and at-large", set(h) == {"AL-2", "AK-AL"}, set(h))
check("wiki house: lowercase aye", h["AL-2"][1]["winner"] and h["AL-2"][1]["party"] == "R")

# ------------------------------------------------------------- call rules
race = {"id": "GA-2026", "chamber": "senate", "state": "GA", "dem_candidate": "Jon Ossoff",
        "rep_candidate": "Mike Collins", "independent_side": False}
close = R.polls_close("GA")
rows_called = [dict(c) for c in ga_rows]
rows_open = [dict(c, winner=False, pct=None) for c in ga_rows]
marks, log = {}, []
R.update_call(race, rows_called, marks, close + timedelta(minutes=1), log)
R.update_call(race, rows_called, marks, close + timedelta(minutes=4), log)
check("call: not accepted before it has settled", "accepted" not in marks["GA-2026"])
R.update_call(race, rows_called, marks, close + timedelta(minutes=5, seconds=1), log)
check("call: accepted after four minutes", marks["GA-2026"].get("accepted", {}).get("party") == "D")
check("call: Democratic side recognised", marks["GA-2026"]["accepted"]["dem_side"] is True)
R.update_call(race, rows_open, marks, close + timedelta(minutes=10), log)
check("call: survives a brief removal", "accepted" in marks["GA-2026"])
R.update_call(race, rows_open, marks, close + timedelta(minutes=15), log)
check("call: withdrawn after four minutes gone", "accepted" not in marks["GA-2026"] and
      any(x["type"] == "retraction" for x in log))

marks, log = {}, []
stale = [dict(c, pct=None) for c in ga_rows]          # marked before close, no shares
R.update_call(race, stale, marks, close - timedelta(minutes=30), log)
R.update_call(race, stale, marks, close + timedelta(minutes=30), log)
check("call: a mark left from before the polls closed is not accepted", "accepted" not in marks.get("GA-2026", {}))
R.update_call(race, rows_called, marks, close + timedelta(minutes=31), log)
check("call: ...until vote shares appear beside it", marks["GA-2026"].get("accepted", {}).get("party") == "D")

ne = {"id": "NE-2026", "chamber": "senate", "state": "NE", "dem_candidate": "Dan Osborn (I)",
      "rep_candidate": "Pete Ricketts", "independent_side": True}
ne_rows = [{"name": "Dan Osborn", "party": "O", "party_raw": "Independent", "pct": None, "winner": True},
           {"name": "Pete Ricketts", "party": "R", "party_raw": "Republican", "pct": None, "winner": False}]
marks, log = {}, []
nc = R.polls_close("NE")
R.update_call(ne, ne_rows, marks, nc + timedelta(minutes=1), log)
R.update_call(ne, ne_rows, marks, nc + timedelta(minutes=6), log)
check("call: Nebraska's independent counts as the forecast's side", marks["NE-2026"]["accepted"]["dem_side"] is True)

ca = {"id": "CA-14", "chamber": "house", "state": "CA", "independent_side": False, "forecast": {"p": 1.0}}
two_dems = [{"name": "A", "party": "D", "party_raw": "Democratic", "pct": None, "winner": False},
            {"name": "B", "party": "D", "party_raw": "Democratic", "pct": None, "winner": False}]
marks, log = {}, []
R.update_call(ca, two_dems, marks, R.polls_close("CA") - timedelta(minutes=1), log)
check("one-party race: nothing before the polls close", "accepted" not in marks.get("CA-14", {}))
R.update_call(ca, two_dems, marks, R.polls_close("CA"), log)
check("one-party race: Democratic at the close", marks["CA-14"]["accepted"]["party"] == "D"
      and marks["CA-14"]["accepted"]["dem_side"] is True)

# A contested race that looks one-party only because a line failed to parse.
ma = {"id": "MA-2026", "chamber": "senate", "state": "MA", "independent_side": False, "forecast": {"p": 0.999}}
only_r = [{"name": "Deaton", "party": "R", "party_raw": "Republican", "pct": None, "winner": False}]
marks, log = {}, []
R.update_call(ma, only_r, marks, R.polls_close("MA") + timedelta(minutes=1), log)
check("one-party rule refuses a race the forecast has as certain for the other side",
      "accepted" not in marks.get("MA-2026", {}))

# A row missing from a read leaves the call alone (no mass withdrawal).
marks, log = {}, []
R.update_call(race, rows_called, marks, close + timedelta(minutes=1), log)
R.update_call(race, rows_called, marks, close + timedelta(minutes=6), log)
for minutes in (10, 20, 30):
    R.update_call(race, None, marks, close + timedelta(minutes=minutes), log)
check("a missing row never withdraws a call", marks["GA-2026"].get("accepted", {}).get("party") == "D"
      and not any(x["type"] == "retraction" for x in log))

warren = ("|-\n! [[2024 United States Senate election in Massachusetts|Massachusetts]]\n| nowrap | {{Plainlist |\n"
          "*{{Party stripe|Republican Party (US)}}John Deaton (Republican)\n"
          "*{{Party stripe|Democratic Party (United States)}}[[Elizabeth Warren]] (Democratic)<ref name=x>y</ref>}}\n")
w = F.parse_wiki_senate(warren)["MA"][0]
check("closing braces after the party are tolerated", [c["party"] for c in w] == ["R", "D"], w)
efn = "*{{Party stripe|Democratic Party (US)}}[[A B]] (Democratic){{efn|note}} 51.0%"
c = F.parse_wiki_candidates(efn)
check("footnote templates are tolerated", c and c[0]["party"] == "D" and c[0]["pct"] == 51.0, c)
bare = ("|-\n! {{ushr|AL|3|X}}\n| nowrap | {{Party stripe|Republican Party (US)}}{{Aye}} "
        "'''[[Mike Rogers (Alabama politician)|Mike Rogers]]''' (Republican) 100%\n")
h = F.parse_wiki_house(bare)
check("an unopposed member without a list bullet is read", h.get("AL-3", [{}])[0].get("winner") is True, h)
special = ("==Special elections==\n{|\n|-\n! {{ushr|GA|14|X}}\n| nowrap | {{Plainlist}}\n"
           "*{{Party stripe|Republican Party (US)}}{{Aye}} '''[[Clay Fuller]]''' (Republican) 55.9%\n"
           "*{{Party stripe|Democratic Party (US)}}[[X Y]] (Democratic) 44.1%\n{{endplainlist}}\n|}\n"
           "==Alabama==\n{|\n|-\n! {{ushr|AL|1|X}}\n| nowrap | {{Plainlist}}\n"
           "*{{Party stripe|Republican Party (US)}}[[A]] (Republican)\n{{endplainlist}}\n|}\n")
h = F.parse_wiki_house(special)
check("this year's special-election table is never read as November", "GA-14" not in h and "AL-1" in h, set(h))
check("a parse that shrinks sharply is refused", not F.acceptable_parse("house", 300, 435)
      and F.acceptable_parse("house", 433, 435) and not F.acceptable_parse("senate", 3, 0))

# -------------------------------------------------------------- governors
for name, want in [("Governor", True), ("GOVERNOR", True), ("Governor / Lieutenant Governor", True),
                   ("For Governor and Lt. Governor", True), ("Lieutenant Governor", False),
                   ("Governor - Rep", False), ("Governor - Dem.", False), ("Governor's Council", False)]:
    check(f"governor contest name: {name!r} -> {want}", bool(F.GOV_RE.match(name)) == want)
sw_tx = {"Races": [{"N": "GOVERNOR", "Candidates": [{"N": "GREG ABBOTT (I)", "P": "REP", "V": 7},
                                                    {"N": "GINA HINOJOSA", "P": "DEM", "V": 6}]},
                   {"N": "LIEUTENANT GOVERNOR", "Candidates": [{"N": "DAN PATRICK", "P": "REP", "V": 1}]}]}
bundle["StateWide"] = base64.b64encode(json.dumps(sw_tx).encode()).decode()
out = {c["race_id"]: c for c in F.parse_tx(bundle, "TX-2026")}
check("TX: governor read from the statewide races, lieutenant governor ignored",
      "TX-GOV" in out and out["TX-GOV"]["chamber"] == "governor" and len(out) == 3, set(out))
fl_gov = fl + ("11/3/2026\tREP\tRepublican\tGOV\tGovernor and Lieutenant Governor\tALA\tAlachua\t\t\t63\t30\t"
               "Donalds\tByron\t\t90\n")
out = {c["race_id"]: c for c in F.parse_fl(fl_gov, "FL-2026-special")}
check("FL: governor ticket mapped by race code", out.get("FL-GOV", {}).get("chamber") == "governor", set(out))
gov_wiki = ("==Predictions==\n{|\n|-\n! [[2026 Georgia gubernatorial election|Georgia]]\n| {{USRaceRating|Tossup}}\n|}\n"
            "==Race summary==\n===States===\n{|\n|-\n! [[2026 Georgia gubernatorial election|Georgia]]\n"
            "| [[Brian Kemp]] || Republican || 2018 || Incumbent term-limited.\n| nowrap | {{Plainlist|\n"
            "*{{Party stripe|Democratic Party (US)}}{{Aye}} [[Keisha Lance Bottoms]] (Democratic) 51.0%\n"
            "*{{Party stripe|Republican Party (US)}}[[Burt Jones]] (Republican) 48.0%}}\n"
            "|-\n! [[2026 Minnesota gubernatorial election|Minnesota]]\n| nowrap | {{Plainlist|\n"
            "*{{Party stripe|Minnesota Democratic–Farmer–Labor Party}}[[A B]] (DFL)\n"
            "*{{Party stripe|Republican Party (US)}}[[C D]] (Republican)}}\n|}\n"
            "===Territories===\n{|\n|-\n! [[2026 Guam gubernatorial election|Guam]]\n| nowrap | "
            "*{{Party stripe|Democratic Party (US)}}[[E F]] (Democratic)\n|}\n")
g = F.parse_wiki_governor(gov_wiki)
check("governors: only the States table is read (no predictions row, no territory)",
      set(g) == {"GA", "MN"}, set(g))
ga_rows = g.get("GA", [[]])[0]
check("governors: the called winner and shares are read",
      any(c["winner"] and c["party"] == "D" and c["pct"] == 51.0 for c in ga_rows), ga_rows)
check("governors: a Minnesota DFL nominee counts as the Democrat",
      [c["party"] for c in g.get("MN", [[]])[0]] == ["D", "R"], g.get("MN"))
check("governors: a governor article needs most states before it replaces a good read",
      not F.acceptable_parse("governor", 10, 36) and F.acceptable_parse("governor", 36, 36))

# ------------------------------------------------ governor research, markets
from datetime import date as _date
import model as M
notes, meta = M.load_governor_research(today=_date(2026, 10, 25))
check("research: adjustments never exceed the cap",
      all(abs(r["adjustment_applied"]) <= meta["cap_points"] for r in notes.values()), meta)
stale, meta_stale = M.load_governor_research(today=_date(2027, 1, 1))
check("research: adjustments lapse when the notes go stale, the notes stay",
      not meta_stale["live"] and all(r["adjustment_applied"] == 0 for r in stale.values()) and len(stale) == len(notes))
import refresh_markets as K
rows = [{"label": "Aaron Ford", "prob": 0.6, "volume": 10}, {"label": "Joe Lombardo", "prob": 0.3, "volume": 5},
        {"label": "Danielle Ford", "prob": 0.1, "volume": 1}]
split = K._gov_split(rows, [{"aaron ford": "D", "joe lombardo": "R"}, {"ford": "D", "lombardo": "R"}])
check("markets: a namesake independent is not counted for the Democrat",
      split and abs(split["D"]["prob"] - 0.6) < 1e-9, split)
check("markets: a book without both nominees is not used",
      K._gov_split(rows[:1], [{"aaron ford": "D", "joe lombardo": "R"}]) is None)

# --------------------------------------------- Senate and House race lists
from tools import fetch_senate as FS, fetch_house as FH
from tools.ratings_env import rating_date
check("senate: a special election maps to its own seat",
      FS.seat_of("! [[2026 United States Senate special election in Florida|Florida (special)]]") == ("FL", "FL-2026-special")
      and FS.seat_of("! [[2026 United States Senate election in South Carolina|South Carolina]]") == ("SC", "SC-2026"))
check("ratings: Cook's date is read from the column header",
      rating_date("''[[The Cook Political Report|Cook]]''<br />{{small|Oct 20,<br />2026}}") == _date(2026, 10, 20)
      and rating_date("[[Cook Political Report|Cook]]<br>{{small|Oct. 23,<br>2026}}") == _date(2026, 10, 23))
rat, day = FH.cook_ratings("[[Cook Political Report|Cook]]<br>{{small|Oct. 23,<br>2026}}\n|-\n!{{ushr|AL|2|X}}\n"
                           "|{{shading PVI|R|7}}\n|{{USRaceRating|Lean|R|Flip}}\n|{{USRaceRating|Tossup}}\n|-\n"
                           "!{{ushr|AK|AL|X}}\n|{{USRaceRating|Tossup}}\n|{{USRaceRating|Likely|R}}\n")
check("house: Cook's is the first rating in a row, at-large seats keep their id",
      rat == {"AL-2": ("lean", "R"), "AK-AL": ("tossup", None)} and day == _date(2026, 10, 23), rat)

import os
import freeze
os.environ["FREEZE_NOW"] = "2026-11-03T10:30:00Z"
st_on = "final-run" if freeze.is_frozen() else freeze.state()
os.environ["FREEZE_NOW"] = "2026-11-03T16:00:00Z"
st_late = "late" if freeze.is_frozen() else freeze.state()
del os.environ["FREEZE_NOW"]
check("freeze: 5:30 am Eastern is the final run, 11 am is too late to re-model",
      st_on == "final-run" and st_late == "late", (st_on, st_late))

check("poll close: Alaska's last close is 06:00 UTC on Nov 4",
      R.polls_close("AK") == datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc))
check("poll close: Georgia closes 00:00 UTC on Nov 4",
      R.polls_close("GA") == datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc))

print()
print(f"{len(FAILS)} failure(s)" if FAILS else "all checks passed")
sys.exit(1 if FAILS else 0)
