"""Classify and rank matches by how much *this* fan cares, given preferences.

Priority policy:
  1. Your followed team(s) — any format, men's or women's — first.
  2. Then top-tier Test matches (two ICC full members) and premier ICC
     tournaments (World Cup, T20 World Cup, Champions Trophy, WTC final).
  3. Then your home domestic cricket (England by default; India/Australia/…).
  4. Everything else, last (and by default only if it's a live international
     involving a full-member nation; pure associate games need --all).

Within a tier: live games first, then ones paused at stumps / a break, then
recently finished (for end-of-day summaries), then upcoming. Filtering (formats,
gender, phase, series, tier floor) is applied here too, driven by Preferences.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from stumps import config
from stumps.models import Format, Match, Phase
from stumps.options import Preferences


class Tier(IntEnum):
    FOLLOWED = 0  # one of your followed teams
    PREMIER = 1  # top-tier Test or ICC tournament
    HOME_DOMESTIC = 2
    OTHER = 3


@dataclass
class Classification:
    tier: Tier
    is_followed: bool = False
    is_top_tier_test: bool = False
    is_icc_tournament: bool = False
    is_home_domestic: bool = False
    reasons: list[str] = field(default_factory=list)


def _lower_names(match: Match) -> list[str]:
    return [t.name.lower() for t in match.teams]


def _any_fragment(names: list[str], allow) -> bool:
    """True if any team name contains one of the allow-listed fragments."""
    return any(a in name for name in names for a in allow)


def is_followed(match: Match, followed_teams: list[str]) -> bool:
    if not followed_teams:
        return False
    names = _lower_names(match)
    return any(team in name for name in names for team in followed_teams)


def is_top_tier_test(match: Match) -> bool:
    if match.format not in {Format.TEST, Format.WTEST}:
        return False
    names = _lower_names(match)
    if len(names) < 2:
        return False
    return all(
        any(nation in name for nation in config.TOP_TIER_TEST_NATIONS)
        for name in names
    )


def is_icc_tournament(match: Match, *, include_warmups: bool = False) -> bool:
    series = match.series_name.lower()
    if not include_warmups and any(
        excl in series for excl in config.ICC_TOURNAMENT_EXCLUSIONS
    ):
        return False  # warm-up / qualifier / League Two — not the event itself
    return any(marker in series for marker in config.ICC_TOURNAMENT_MARKERS)


def is_minor_cricket(match: Match) -> bool:
    """Second XI, academy, age-group cricket — not the professional game."""
    haystack = (match.series_name.lower(), *_lower_names(match))
    return any(
        marker in field for field in haystack for marker in config.MINOR_CRICKET_MARKERS
    )


def is_home_domestic(match: Match, domestic: str | None = "england") -> bool:
    if domestic is None or match.format.is_international or is_minor_cricket(match):
        return False
    scene = config.DOMESTIC_SCENES.get(domestic)
    if scene is None:
        return False
    names = _lower_names(match)
    series = match.series_name.lower()
    if _any_fragment(names, scene.teams):
        return True
    return any(marker in series for marker in scene.series_markers)


def _involves_full_member(match: Match) -> bool:
    """True if at least one team is an ICC full-member nation. Keeps the
    'surface a live international' catch-all to internationals of real note,
    rather than associate-vs-associate games (Austria v Finland, Rwanda Women v
    Malawi Women) — which are still reachable via --all / --tier all."""
    return _any_fragment(_lower_names(match), config.TOP_TIER_TEST_NATIONS)


def is_womens(match: Match) -> bool:
    if match.format.is_womens:
        return True
    blob = (match.series_name + " " + " ".join(match.team_names)).lower()
    return "women" in blob


def classify(match: Match, prefs: Preferences | None = None) -> Classification:
    prefs = prefs or Preferences()
    followed = is_followed(match, prefs.followed_teams)
    top_test = is_top_tier_test(match)
    icc = is_icc_tournament(match, include_warmups=prefs.include_warmups)
    domestic = is_home_domestic(match, prefs.domestic)

    reasons: list[str] = []
    if followed:
        reasons.append("followed team")
    if top_test:
        reasons.append("top-tier Test")
    if icc:
        reasons.append("ICC tournament")
    if domestic:
        reasons.append("home domestic")

    if followed:
        tier = Tier.FOLLOWED
    elif top_test or icc:
        tier = Tier.PREMIER
    elif domestic:
        tier = Tier.HOME_DOMESTIC
    else:
        tier = Tier.OTHER

    return Classification(
        tier=tier,
        is_followed=followed,
        is_top_tier_test=top_test,
        is_icc_tournament=icc,
        is_home_domestic=domestic,
        reasons=reasons,
    )


# Lower rank = show sooner. Live first; then paused (stumps/break) which still
# want an at-a-glance state; then recently finished (summaries); then upcoming.
_PHASE_RANK = {
    Phase.LIVE: 0,
    Phase.STUMPS: 1,
    Phase.BREAK: 1,
    Phase.COMPLETE: 2,
    Phase.UPCOMING: 3,
    Phase.ABANDONED: 4,
    Phase.UNKNOWN: 5,
}

# A gentle nudge so that, all else equal, the longer/marquee formats sort first.
_FORMAT_RANK = {
    Format.TEST: 0,
    Format.WTEST: 0,
    Format.ODI: 1,
    Format.WODI: 1,
    Format.T20I: 2,
    Format.WT20I: 2,
    Format.FIRST_CLASS: 3,
    Format.LIST_A: 4,
    Format.T20: 5,
    Format.HUNDRED: 5,
    Format.OTHER: 9,
}


def _sort_key(item: tuple[Match, Classification]) -> tuple:
    match, cls = item
    return (
        int(cls.tier),
        _PHASE_RANK.get(match.phase, 5),
        match.starts_at if match.phase is Phase.UPCOMING else "",  # soonest first
        _FORMAT_RANK.get(match.format, 9),
        match.title,
    )


def _passes_tier(match: Match, cls: Classification, prefs: Preferences) -> bool:
    if prefs.tier_floor >= Tier.OTHER:  # "all"
        return True
    if int(cls.tier) <= prefs.tier_floor:
        return True
    # When the floor includes domestic, still surface a notable international
    # (full-member, not associate-vs-associate minnow games) that didn't
    # otherwise qualify — while it's live, AND for a little after it finishes, so
    # a game you were watching doesn't vanish the moment it ends (bounded by the
    # --results window). `--core-results` opts out, keeping history to your teams.
    if (
        prefs.tier_floor >= Tier.HOME_DOMESTIC
        and match.format.is_international
        and _involves_full_member(match)
    ):
        if match.phase.is_active_today:
            return True
        if match.phase is Phase.COMPLETE and not prefs.core_results_only:
            return True
    return False


def _passes_filters(match: Match, prefs: Preferences) -> bool:
    if prefs.formats is not None and match.format not in prefs.formats:
        return False
    if prefs.live_only and not match.phase.is_active_today:
        return False
    if not prefs.show_finished and match.phase in {Phase.COMPLETE, Phase.ABANDONED}:
        return False
    if not prefs.show_upcoming and match.phase is Phase.UPCOMING:
        return False
    if prefs.gender == "women" and not is_womens(match):
        return False
    if prefs.gender == "men" and is_womens(match):
        return False
    if prefs.series_filter and prefs.series_filter.lower() not in match.series_name.lower():
        return False
    return True


def prioritise(
    matches: list[Match], prefs: Preferences | None = None
) -> list[tuple[Match, Classification]]:
    """Return matches the fan cares about, most-relevant first, each paired with
    its :class:`Classification`, after applying their preferences' filters."""
    prefs = prefs or Preferences()
    kept = []
    for match in matches:
        cls = classify(match, prefs)
        if _passes_tier(match, cls, prefs) and _passes_filters(match, prefs):
            kept.append((match, cls))

    ordered = sorted(kept, key=_sort_key)
    if prefs.limit is not None:
        ordered = ordered[: prefs.limit]
    return ordered
