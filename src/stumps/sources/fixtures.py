"""Hand-built sample matches: a self-contained 'demo' data source.

This lets ``stumps --demo`` and the test suite exercise the whole pipeline
(prioritisation, figures, DLS par, win probability, summaries) without any
network — handy when nothing is live, when the unofficial APIs are blocked, or
when running offline. The data is invented but realistic.
"""

from __future__ import annotations

from stumps.config import Settings
from stumps.models import Batter, Bowler, Format, Innings, Match, Phase, Team
from stumps.sources.base import DataSource


def sample_matches() -> list[Match]:
    return [
        _england_test_at_stumps(),
        _england_women_t20i_live(),
        _world_cup_odi_chase_live(),
        _county_championship_live(),
        _t20_blast_live(),
        _other_international_live(),
        _recently_completed_odi(),
    ]


def _england_test_at_stumps() -> Match:
    return Match(
        match_id="demo-eng-aus-test",
        format=Format.TEST,
        series_name="The Ashes 2027",
        teams=[Team("Australia", "AUS"), Team("England", "ENG")],
        phase=Phase.STUMPS,
        day_number=2,
        total_days=5,
        session="Stumps, Day 2",
        venue="Lord's, London",
        status_text="Stumps, Day 2: England trail by 245 runs with 6 wickets in hand",
        source="demo",
        innings=[
            Innings(
                batting_team="Australia",
                bowling_team="England",
                number=1,
                runs=425,
                wickets=10,
                overs=128.4,
                all_out=True,
                closed=True,
            ),
            Innings(
                batting_team="England",
                bowling_team="Australia",
                number=2,
                runs=180,
                wickets=4,
                overs=52.0,
                batters=[
                    Batter("J Root", 78, 142, 9, 0, on_strike=True),
                    Batter("H Brook", 45, 61, 6, 1),
                    Batter("Z Crawley", 22, 40, 3, 0, not_out=False,
                           dismissal="c Carey b Cummins"),
                    Batter("B Duckett", 19, 28, 3, 0, not_out=False,
                           dismissal="lbw b Hazlewood"),
                ],
                bowlers=[
                    Bowler("P Cummins", 14.0, 4, 38, 2, bowling_now=True),
                    Bowler("J Hazlewood", 13.0, 5, 31, 1),
                    Bowler("M Starc", 12.4, 1, 55, 1),
                    Bowler("N Lyon", 12.0, 2, 40, 0),
                ],
            ),
        ],
    )


def _england_women_t20i_live() -> Match:
    return Match(
        match_id="demo-engw-ausw-t20i",
        format=Format.WT20I,
        series_name="Women's Ashes 2027 — T20I Series",
        teams=[Team("Australia Women", "AUS-W"), Team("England Women", "ENG-W")],
        phase=Phase.LIVE,
        venue="Edgbaston, Birmingham",
        status_text="England Women need 76 runs from 48 balls",
        source="demo",
        innings=[
            Innings(
                batting_team="Australia Women",
                bowling_team="England Women",
                number=1,
                runs=165,
                wickets=6,
                overs=20.0,
                closed=True,
            ),
            Innings(
                batting_team="England Women",
                bowling_team="Australia Women",
                number=2,
                runs=90,
                wickets=3,
                overs=12.0,
                target=166,
                batters=[
                    Batter("N Sciver-Brunt", 44, 31, 5, 1, on_strike=True),
                    Batter("A Capsey", 21, 18, 2, 0),
                ],
                bowlers=[
                    Bowler("A Gardner", 3.0, 0, 19, 1, bowling_now=True),
                    Bowler("M Schutt", 4.0, 0, 28, 1),
                ],
            ),
        ],
    )


def _world_cup_odi_chase_live() -> Match:
    return Match(
        match_id="demo-ind-nz-wc",
        format=Format.ODI,
        series_name="ICC Cricket World Cup 2027",
        teams=[Team("New Zealand", "NZ"), Team("India", "IND")],
        phase=Phase.LIVE,
        venue="Eden Gardens, Kolkata",
        status_text="India need 71 runs from 72 balls",
        source="demo",
        innings=[
            Innings(
                batting_team="New Zealand",
                bowling_team="India",
                number=1,
                runs=280,
                wickets=8,
                overs=50.0,
                closed=True,
            ),
            Innings(
                batting_team="India",
                bowling_team="New Zealand",
                number=2,
                runs=210,
                wickets=4,
                overs=38.0,
                target=281,
                batters=[
                    Batter("V Kohli", 92, 88, 7, 1, on_strike=True),
                    Batter("KL Rahul", 41, 49, 3, 0),
                ],
                bowlers=[
                    Bowler("T Boult", 9.0, 0, 48, 2, bowling_now=True),
                    Bowler("M Santner", 8.0, 0, 39, 1),
                ],
            ),
        ],
    )


def _county_championship_live() -> Match:
    return Match(
        match_id="demo-sur-lan-cc",
        format=Format.FIRST_CLASS,
        series_name="County Championship Division One",
        teams=[Team("Surrey", "SUR"), Team("Lancashire", "LAN")],
        phase=Phase.LIVE,
        day_number=3,
        total_days=4,
        session="Afternoon, Day 3",
        venue="The Oval, London",
        status_text="Lancashire lead by 88 runs with 7 second-innings wickets in hand",
        source="demo",
        innings=[
            Innings("Surrey", "Lancashire", 1, 312, 10, 95.2, all_out=True, closed=True),
            Innings("Lancashire", "Surrey", 2, 280, 10, 88.0, all_out=True, closed=True),
            Innings(
                "Surrey", "Lancashire", 3, 150, 10, 41.0, all_out=True, closed=True
            ),
            Innings(
                "Lancashire",
                "Surrey",
                4,
                118,
                3,
                28.0,
                target=183,
                batters=[
                    Batter("K Jennings", 64, 99, 8, 0, on_strike=True),
                    Batter("J Bohannon", 30, 41, 4, 0),
                ],
                bowlers=[Bowler("D Worrall", 9.0, 2, 34, 2, bowling_now=True)],
            ),
        ],
    )


def _t20_blast_live() -> Match:
    return Match(
        match_id="demo-som-ken-blast",
        format=Format.T20,
        series_name="Vitality Blast",
        teams=[Team("Somerset", "SOM"), Team("Kent", "KEN")],
        phase=Phase.LIVE,
        venue="Taunton",
        status_text="Kent need 54 runs from 30 balls",
        source="demo",
        innings=[
            Innings("Somerset", "Kent", 1, 188, 5, 20.0, closed=True),
            Innings(
                "Kent",
                "Somerset",
                2,
                135,
                4,
                15.0,
                target=189,
                batters=[Batter("D Bell-Drummond", 61, 40, 5, 3, on_strike=True)],
                bowlers=[Bowler("C Overton", 3.0, 0, 28, 1, bowling_now=True)],
            ),
        ],
    )


def _other_international_live() -> Match:
    return Match(
        match_id="demo-ban-zim-odi",
        format=Format.ODI,
        series_name="Zimbabwe tour of Bangladesh",
        teams=[Team("Bangladesh", "BAN"), Team("Zimbabwe", "ZIM")],
        phase=Phase.LIVE,
        venue="Mirpur, Dhaka",
        status_text="Zimbabwe are 145/5 after 32 overs",
        source="demo",
        innings=[
            Innings(
                "Zimbabwe",
                "Bangladesh",
                1,
                145,
                5,
                32.0,
                batters=[Batter("S Williams", 58, 71, 5, 0, on_strike=True)],
                bowlers=[Bowler("Mehidy Hasan", 7.0, 1, 22, 2, bowling_now=True)],
            ),
        ],
    )


def _recently_completed_odi() -> Match:
    return Match(
        match_id="demo-sa-pak-complete",
        format=Format.ODI,
        series_name="Pakistan tour of South Africa",
        teams=[Team("South Africa", "RSA"), Team("Pakistan", "PAK")],
        phase=Phase.COMPLETE,
        venue="The Wanderers, Johannesburg",
        status_text="South Africa won by 4 wickets",
        result_text="South Africa won by 4 wickets",
        source="demo",
        innings=[
            Innings("Pakistan", "South Africa", 1, 265, 10, 49.3, all_out=True, closed=True),
            Innings("South Africa", "Pakistan", 2, 266, 6, 48.1, target=266, closed=True),
        ],
    )


class DemoSource(DataSource):
    """A data source that serves the built-in sample matches (offline)."""

    name = "demo"

    def fetch_current_matches(self) -> list[Match]:
        return sample_matches()
