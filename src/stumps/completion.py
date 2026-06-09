"""Shell tab-completion for ``--team`` and ``--region`` (via argcomplete).

Enable once in your shell:

    pip install 'stumps[completion]'      # if not already present
    eval "$(register-python-argcomplete stumps)"   # add to ~/.bashrc / ~/.zshrc

Then ``stumps --team <TAB>`` suggests nations and franchises, and
``stumps --region <TAB>`` suggests region codes. Completion is optional — the
CLI works fine without argcomplete installed.
"""

from __future__ import annotations

from stumps import config

try:  # optional dependency
    import argcomplete
except ImportError:  # pragma: no cover
    argcomplete = None

#: ESPN scoreboard region codes worth suggesting (region accepts others too).
REGIONS: dict[str, str] = {
    "gb": "United Kingdom",
    "in": "India",
    "au": "Australia",
    "us": "United States",
    "nz": "New Zealand",
    "za": "South Africa",
    "pk": "Pakistan",
    "lk": "Sri Lanka",
    "bd": "Bangladesh",
    "ae": "United Arab Emirates",
    "ie": "Ireland",
    "zw": "Zimbabwe",
    "ca": "Canada",
}


def known_teams() -> list[str]:
    """Nations + every domestic team fragment, title-cased, for completion."""
    nations = {n.title() for n in config.TOP_TIER_TEST_NATIONS}
    domestic = {
        frag.title()
        for scene in config.DOMESTIC_SCENES.values()
        for frag in scene.teams
    }
    return sorted(nations | domestic)


def domestic_keys() -> list[str]:
    return sorted(config.DOMESTIC_SCENES) + ["none"]


def _team_completer(prefix, **_kwargs):
    p = prefix.lower()
    return [t for t in known_teams() if p in t.lower()]


def _region_completer(prefix, **_kwargs):
    p = prefix.lower()
    return [code for code in REGIONS if code.startswith(p)]


def _domestic_completer(prefix, **_kwargs):
    p = prefix.lower()
    return [k for k in domestic_keys() if k.startswith(p)]


def attach(team_action, region_action, domestic_action=None) -> None:
    """Attach completers to the given argparse actions (harmless without
    argcomplete; it only reads ``.completer`` when active)."""
    team_action.completer = _team_completer
    region_action.completer = _region_completer
    if domestic_action is not None:
        domestic_action.completer = _domestic_completer


def autocomplete(parser) -> None:
    """Run argcomplete's completion hook if installed (no-op otherwise)."""
    if argcomplete is not None:
        argcomplete.autocomplete(parser)
