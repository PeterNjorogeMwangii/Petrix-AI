"""Deterministic, whole-name matching; never guess between multiple teams."""
import unicodedata
from dataclasses import dataclass
from typing import Optional


def normalize(value):
    text = unicodedata.normalize('NFKC', value).casefold()
    return ' '.join(''.join(char if char.isalnum() else ' ' for char in text).split())


@dataclass(frozen=True)
class TeamMatch:
    team: Optional[dict] = None
    reason: str = 'no_match'
    matched_name: str = ''


def match_team(text, teams):
    value = normalize(text)
    if not value:
        return TeamMatch()
    exact, mentions = {}, {}
    for team in teams:
        for name in [team['name']] + team['alternative_names']:
            alias = normalize(name)
            if not alias:
                continue
            if alias == value:
                exact[team['id']] = (team, name)
            if ' ' + alias + ' ' in ' ' + value + ' ':
                # Keep the longest recognised alternative for the trace.
                previous = mentions.get(team['id'])
                if not previous or len(alias) > len(normalize(previous[1])):
                    mentions[team['id']] = (team, name)
    matches = exact or mentions
    if len(matches) != 1:
        return TeamMatch(reason='ambiguous' if matches else 'no_match')
    team, name = next(iter(matches.values()))
    return TeamMatch(team={'id': str(team['id']), 'name': team['name']},
        reason='exact' if exact else 'mention', matched_name=name)
