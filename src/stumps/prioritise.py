"""Classify and rank matches by how much *this* cricket lover cares.

Priority policy (from the user):
  1. England — men's or women's, any format — ALWAYS first.
  2. Then top-tier Test matches (two ICC full members) and premier ICC
     tournaments (World Cup, T20 World Cup, Champions Trophy, WTC final).
  3. Then English domestic cricket, all formats.
  4. Everything else, last (and by default only if it's a live international).

Within a tier: live games first, then ones paused at stumps / a break, then
recently finished (for end-of-day summaries), then upcoming.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from stumps import config
from stumps.models import Format, Match, Phase


class Tier(IntEnum):
    ENGLAND = 0
    PREMIER = 1  # top-tier Test or ICC tournament
    ENGLISH_DOMESTIC = 2
    OTHER = 3


@dataclass
class Classification:
    tier: Tier
    is_england: bool = False
    is_top_tier_test: bool = False
    is_icc_tournament: bool = False
    is_english_domestic: bool = False
    reasons: list[str] = field(default_factory=list)


def _lower_names(match: Match) -> list[str]:
    return [t.name.lower() for t in match.teams]


def _any_nation(names: list[str], allow: frozenset[str]) -> bool:
    """True if any team name contains one of the allow-listed nation/team names
    (substring match handles 'England Women', 'Australia A', etc.)."""
    return any(a in name for name in names for a in allow)


def is_england_match(match: Match) -> bool:
    names = _lower_names(match)
    # Exclude domestic English counties (which never contain "england").
    return any("england" in n or n == "eng" for n in names)


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


def is_icc_tournament(match: Match) -> bool:
    series = match.series_name.lower()
    if any(excl in series for excl in config.ICC_TOURNAMENT_EXCLUSIONS):
        return False  # warm-up / qualifier / League Two — not the event itself
    return any(marker in series for marker in config.ICC_TOURNAMENT_MARKERS)


def is_minor_cricket(match: Match) -> bool:
    """Second XI, academy, age-group cricket — not the professional game."""
    haystack = (match.series_name.lower(), *_lower_names(match))
    return any(
        marker in field for field in haystack for marker in config.MINOR_CRICKET_MARKERS
    )


def is_english_domestic(match: Match) -> bool:
    if match.format.is_international or is_minor_cricket(match):
        return False
    names = _lower_names(match)
    series = match.series_name.lower()
    if _any_nation(names, config.ENGLISH_DOMESTIC_TEAMS):
        return True
    return any(marker in series for marker in config.ENGLISH_DOMESTIC_SERIES_MARKERS)


def classify(match: Match) -> Classification:
    england = is_england_match(match)
    top_test = is_top_tier_test(match)
    icc = is_icc_tournament(match)
    domestic = is_english_domestic(match)

    reasons: list[str] = []
    if england:
        reasons.append("England national side")
    if top_test:
        reasons.append("top-tier Test")
    if icc:
        reasons.append("ICC tournament")
    if domestic:
        reasons.append("English domestic")

    if england:
        tier = Tier.ENGLAND
    elif top_test or icc:
        tier = Tier.PREMIER
    elif domestic:
        tier = Tier.ENGLISH_DOMESTIC
    else:
        tier = Tier.OTHER

    return Classification(
        tier=tier,
        is_england=england,
        is_top_tier_test=top_test,
        is_icc_tournament=icc,
        is_english_domestic=domestic,
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
        _FORMAT_RANK.get(match.format, 9),
        match.title,
    )


def prioritise(
    matches: list[Match], *, include_all: bool = False
) -> list[tuple[Match, Classification]]:
    """Return matches the user cares about, most-relevant first, each paired
    with its :class:`Classification`.

    By default we keep tiers 0-2 (England, premier, English domestic) plus any
    *live* international from the catch-all tier. ``include_all=True`` keeps
    everything (useful with a ``--all`` flag).
    """
    classified = [(m, classify(m)) for m in matches]

    if not include_all:
        kept = []
        for match, cls in classified:
            if cls.tier != Tier.OTHER:
                kept.append((match, cls))
            elif match.phase.is_active_today and match.format.is_international:
                kept.append((match, cls))
        classified = kept

    return sorted(classified, key=_sort_key)
