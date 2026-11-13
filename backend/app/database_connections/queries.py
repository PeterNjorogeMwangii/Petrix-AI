"""Conservative SELECT templates. Values are always bound by the driver."""
import re

from app.workflows.chat_script import ScriptError, evaluate, parse_expression
from app.workflows.templates import TOKEN, resolve

FORBIDDEN = {"insert", "update", "delete", "merge", "into", "create", "alter", "drop", "truncate",
    "copy", "call", "do", "execute", "prepare", "deallocate", "grant", "revoke", "vacuum", "analyze",
    "lock", "set", "reset", "discard", "commit", "rollback", "savepoint", "release", "begin", "start",
    "for", "set_config", "nextval", "setval", "pg_notify", "pg_cancel_backend", "pg_terminate_backend",
    "pg_reload_conf", "pg_rotate_logfile", "pg_switch_wal", "pg_log_backend_memory_contexts"}
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
DOLLAR = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def sql_source(source, mode):
    if mode == "expression":
        # A Python expression can build the static template, never interpolate
        # raw chat data into SQL. Chat values belong in bound {name.path} tokens.
        parse_expression(source)
        source = evaluate(source, {})
        if type(source) is not str:
            raise ValueError("The Python expression must return a SQL string. Use {variable} placeholders for chat values.")
    if type(source) is not str or not source.strip() or len(source) > 8000:
        raise ValueError("Enter a SELECT query of up to 8,000 characters.")
    return source.strip()


def compile_query(source, context=None):
    """Lex PostgreSQL strings/comments before checking statements or placeholders.

    Supports a single SELECT (including subqueries/unions). WITH, locking SELECTs,
    SELECT INTO, session changes and side-effect primitives are excluded. The
    server also enforces a read-only transaction; this lexer is not the boundary.
    """
    output, parameters, words = [], [], []
    position, terminated = 0, False

    def bind(match):
        value = None if context is None else resolve(match.group(1) or match.group(2), context)
        if value is not None and type(value) not in {str, int, float, bool}:
            raise ValueError("SQL parameters must be text, numbers, Boolean values, or null. Select a scalar variable field.")
        parameters.append(value)
        return "%s"

    while position < len(source):
        char = source[position]
        if char.isspace():
            output.append(char); position += 1; continue
        if source.startswith("--", position):
            end = source.find("\n", position + 2)
            position = len(source) if end == -1 else end
            output.append(" "); continue
        if source.startswith("/*", position):
            level, end = 1, position + 2
            while level and end < len(source):
                if source.startswith("/*", end): level += 1; end += 2
                elif source.startswith("*/", end): level -= 1; end += 2
                else: end += 1
            if level: raise ValueError("Close the SQL block comment.")
            position = end; output.append(" "); continue
        if terminated:
            raise ValueError("Only one SELECT statement is allowed.")
        if char == ";":
            terminated = True; position += 1; continue
        match = TOKEN.match(source, position)
        if match:
            output.append(bind(match)); position = match.end(); continue
        if char == "'":
            end = position + 1
            while end < len(source):
                if source[end] == "'":
                    if end + 1 < len(source) and source[end + 1] == "'": end += 2; continue
                    break
                if source[end] == "\\":
                    # Require standard strings; backslash semantics depend on
                    # server options and E strings are deliberately excluded.
                    raise ValueError("Use standard SQL string literals without backslash escapes.")
                end += 1
            if end == len(source): raise ValueError("Close the SQL string literal.")
            literal = source[position + 1:end]
            token = TOKEN.fullmatch(literal)
            if token:
                # Accept the screenshot's quoted '{{variable}}' form safely.
                output.append(bind(token))
            elif TOKEN.search(literal):
                raise ValueError("Place a variable in its own SQL value, not inside a longer string. Use SQL concatenation or LIKE with a bound value.")
            else: output.append(source[position:end + 1].replace("%", "%%"))
            position = end + 1; continue
        if char == '"':
            end, identifier = position + 1, ""
            while end < len(source):
                if source[end] == '"':
                    if end + 1 < len(source) and source[end + 1] == '"': identifier += '"'; end += 2; continue
                    break
                identifier += source[end]; end += 1
            if end == len(source): raise ValueError("Close the SQL identifier.")
            if "\\" in identifier: raise ValueError("Use SQL identifiers without Unicode escape sequences.")
            words.append(identifier.lower())
            output.append(source[position:end + 1].replace("%", "%%")); position = end + 1; continue
        dollar = DOLLAR.match(source, position)
        if dollar:
            end = source.find(dollar.group(), dollar.end())
            if end == -1: raise ValueError("Close the SQL dollar-quoted string.")
            literal = source[dollar.end():end]
            if TOKEN.search(literal): raise ValueError("Put SQL variables outside dollar-quoted strings.")
            end += len(dollar.group()); output.append(source[position:end].replace("%", "%%")); position = end; continue
        word = WORD.match(source, position)
        if word:
            words.append(word.group().lower()); output.append(word.group()); position = word.end(); continue
        if char in "{}\\\x00": raise ValueError("Use valid SQL and {variable.path} placeholders.")
        output.append("%%" if char == "%" else char); position += 1
    if not words or words[0] != "select":
        raise ValueError("Only a single SELECT query is allowed. WITH and write statements are not supported.")
    if any(word in FORBIDDEN or word.startswith(("dblink", "lo_", "pg_advisory_", "pg_write_", "pg_read_", "pg_ls_", "pg_stat_reset", "pg_logical_", "pg_replication_")) for word in words):
        raise ValueError("Use a read-only SELECT without writes, locking, session changes, or side-effect functions.")
    return "".join(output), parameters


def prepare_query(source, mode, context=None):
    try:
        return compile_query(sql_source(source, mode), context)
    except ScriptError as error:
        raise ValueError(str(error)) from None
