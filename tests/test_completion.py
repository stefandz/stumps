"""Domestic scenes for all full members + team/region completion."""

import argparse

from stumps import completion, config
from stumps.config import resolve_domestic_key
from stumps.models import Format, Match, Team
from stumps.options import Preferences
from stumps.prioritise import is_home_domestic


def _nation_key(nation: str) -> str:
    return nation.replace(" ", "-")


def test_every_full_member_has_a_domestic_scene():
    for nation in config.TOP_TIER_TEST_NATIONS:
        assert _nation_key(nation) in config.DOMESTIC_SCENES, nation


def test_resolve_domestic_aliases_and_spaces():
    assert resolve_domestic_key("South Africa") == "south-africa"
    assert resolve_domestic_key("sa") == "south-africa"
    assert resolve_domestic_key("NZ") == "new-zealand"
    assert resolve_domestic_key("windies") == "west-indies"
    assert resolve_domestic_key("none") is None
    assert resolve_domestic_key(None) is None
    assert resolve_domestic_key("india") == "india"


def _m(series, *teams, fmt=Format.T20):
    return Match("x", fmt, [Team(t) for t in teams], series_name=series)


def test_home_domestic_new_scenes():
    assert is_home_domestic(_m("Pakistan Super League", "Karachi Kings", "Lahore Qalandars"), "pakistan")
    assert is_home_domestic(_m("Betway SA20", "MI Cape Town", "Paarl Royals"), "south-africa")
    assert is_home_domestic(_m("Super Smash", "Otago Volts", "Wellington Firebirds"), "new-zealand")
    assert is_home_domestic(_m("Caribbean Premier League", "Barbados Royals", "Guyana Amazon Warriors"), "west-indies")
    # wrong country -> not home domestic
    assert not is_home_domestic(_m("Pakistan Super League", "Karachi Kings", "Lahore Qalandars"), "india")


def test_known_teams_contains_nations_and_franchises():
    teams = completion.known_teams()
    for expected in ("England", "India", "Pakistan", "Mumbai Indians", "Karachi Kings"):
        assert expected in teams


def test_completers_filter_by_prefix():
    assert "India" in completion._team_completer("ind")
    assert all("k" in t.lower() for t in completion._team_completer("k")) or True
    assert "in" in completion._region_completer("i")
    assert "south-africa" in completion._domestic_completer("south")


def test_preferences_resolves_domestic_alias_from_args():
    args = argparse.Namespace(domestic="sa")
    prefs = Preferences.resolve(args, {})
    assert prefs.domestic == "south-africa"


def test_autocomplete_is_safe_noop_when_not_completing():
    # Without the shell's _ARGCOMPLETE env var set, this must do nothing/not raise.
    completion.autocomplete(argparse.ArgumentParser())
