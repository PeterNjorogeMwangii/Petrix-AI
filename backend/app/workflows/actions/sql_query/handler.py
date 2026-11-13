from dataclasses import dataclass

from app.database_connections.client import QueryResult
from app.database_connections.queries import prepare_query
from app.workflows.chat_script import ScriptError


@dataclass(frozen=True)
class QueryOutcome:
    path_id: str
    outcome: str
    row_count: int
    error: object


def execute(config, context):
    try:
        query, parameters = prepare_query(config["query"], config["query_mode"], context)
    except (ValueError, ScriptError, TypeError):
        result = QueryResult("error", error="invalid_query")
    else:
        context.assign(config["variable_name"], QueryResult("error", error="result_too_large").variable())
        if context.sql_calls >= 10:
            result = QueryResult("error", error="query_limit")
        elif context.sql_client is None:
            result = QueryResult("error", error="connection_unavailable")
        else:
            context.sql_calls += 1
            result = context.sql_client.query(config["connection_id"], query, parameters, config["timeout_seconds"])
    try: context.assign(config["variable_name"], result.variable())
    except ScriptError:
        result = QueryResult("error", error="variable_limit")
        context.assign(config["variable_name"], result.variable())
    return QueryOutcome(config[result.outcome + "_path_id"], result.outcome, len(result.rows), result.error)
