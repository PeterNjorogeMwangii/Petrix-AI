from dataclasses import dataclass
from sqlalchemy.exc import SQLAlchemyError
from app.workflows.chat_script import ScriptError, evaluate
from app.workflows.templates import render


@dataclass(frozen=True)
class TeamMatchOutcome:
    path_id: str
    outcome: str
    error: object = None
    team_name: str = ''
    match_reason: str = ''


def execute(config, context):
    def outcome(status, error=None, team_name='', reason=''):
        key = 'match' if status == 'match_found' else status
        return TeamMatchOutcome(config[key + '_path_id'], status, error, team_name, reason)
    # Evaluate before clearing: the output may intentionally reuse the input variable.
    valid = True
    try:
        text = evaluate(config['input'], context.environment()) if config['input_mode'] == 'expression' else render(config['input'], context, maximum=16000, allow_empty=True)
        if type(text) is not str or len(text) > 16000:
            raise ScriptError('Team matching needs text of at most 16,000 characters.')
    except (ScriptError, ValueError, TypeError):
        valid = False
    try:
        context.assign(config['variable_name'], None)
    except ScriptError:
        return outcome('error', 'variable_limit')
    if not valid:
        return outcome('error', 'invalid_input')
    if not context.team_match_client:
        return outcome('error', 'teams_unavailable')
    try:
        result = context.team_match_client.match(text)
    except SQLAlchemyError:
        # The matcher isolates database reads in a savepoint.
        return outcome('error', 'teams_unavailable')
    if result.team is None:
        return outcome('no_match', reason=result.reason)
    try:
        context.assign(config['variable_name'], result.team)
    except ScriptError:
        return outcome('error', 'variable_limit')
    return outcome('match_found', team_name=result.team['name'], reason=result.reason)
