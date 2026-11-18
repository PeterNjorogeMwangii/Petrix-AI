"""Plain-text, single-pass variable substitution for messaging actions."""
import json
from html import escape
import re

from app.workflows.chat_script import ScriptError

TOKEN = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*|_transferred_context(?:\.[A-Za-z0-9_]+)*|_chatbot\.name)\s*\}\}|\{([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*|_transferred_context(?:\.[A-Za-z0-9_]+)*|_chatbot\.name)\}")


def resolve(path, context):
    environment = context.environment()
    aliases = {"_chatbot.name": context.agent.name, "agent.name": context.agent.name,
        "input.message": context.event.get("content") or "", "feedback.rating": context.event.get("rating")}

    if path in aliases:
        return aliases[path]
    parts = path.split(".")
    if parts[0] not in environment:
        raise ScriptError("Variable '{}' is not set.".format(parts[0]))
    value = environment[parts[0]]
    for key in parts[1:]:
        if type(value) is dict and key in value: value = value[key]
        elif type(value) is list and key.isdigit() and int(key) < len(value): value = value[int(key)]
        elif type(value) is str and key == "text": value = value
        elif type(value) in {int, float} and key == "norm": value = {"value": value}
        else: raise ScriptError("Variable path '{}' is not available.".format(path))
    return value


def render(source, context, maximum=4000, allow_empty=False, escape_values=False):

    def text(value):
        if value is None: return ""
        if type(value) is str: return value
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    chunks, position, size = [], 0, 0
    for match in TOKEN.finditer(source):
        literal = source[position:match.start()]
        replacement = text(resolve(match.group(1) or match.group(2), context))
        if escape_values: replacement = escape(replacement, quote=True)
        size += len(literal) + len(replacement)
        if size > maximum: raise ScriptError("The rendered message or quick reply is too long.")
        chunks.extend([literal, replacement]); position = match.end()
    chunks.append(source[position:])
    if size + len(chunks[-1]) > maximum: raise ScriptError("The rendered message or quick reply is too long.")
    result = "".join(chunks)
    if not result.strip() and not allow_empty: raise ScriptError("The rendered message or quick reply is empty.")
    return result
