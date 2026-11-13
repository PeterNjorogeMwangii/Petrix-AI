from dataclasses import dataclass
import json
from urllib.parse import quote
from typing import Optional

from app.workflows.actions.send_api.client import APIResult, SendAPIClient
from app.workflows.actions.send_api.config import load_json, validate_endpoint, validate_header
from app.workflows.chat_script import ScriptError, evaluate, json_value
from app.workflows.templates import TOKEN, render


@dataclass(frozen=True)
class APIOutcome:
    path_id: str
    outcome: str
    status_code: Optional[int]
    error: Optional[str]


def prepare_request(config, context):
    # Encode each substituted value, preserving the configured origin and URL
    # structure even when an answer contains slashes, query delimiters, or dots.
    url = TOKEN.sub(lambda match: quote(render(match.group(), context, maximum=8192), safe="").replace(".", "%2E"), config["url"])
    if len(url) > 8192:
        raise ScriptError("The rendered API endpoint is too long.")
    validate_endpoint(url)
    headers = {}
    for item in config["headers"]:
        value = render(item["value"], context, maximum=4000) if item["value"] else ""
        validate_header(item["key"], value)
        headers[item["key"]] = value
    if sum(len(key) + len(value) for key, value in headers.items()) > 16000:
        raise ScriptError("API headers can use up to 16 KB.")

    def substitute(value):
        if type(value) is str:
            return render(value, context, maximum=8192) if value else ""
        if type(value) is list:
            return [substitute(item) for item in value]
        if type(value) is dict:
            return {key: substitute(item) for key, item in value.items()}
        return value

    body = None
    if config["method"] != "GET" and config["payload"].strip():
        value = evaluate(config["payload"], context.environment()) if config["payload_mode"] == "expression" else substitute(load_json(config["payload"]))
        body = json.dumps(json_value(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if not any(key.lower() == "content-type" for key in headers):
            headers["Content-Type"] = "application/json"
    return url, headers, body


def execute(config, context):
    try:
        url, headers, body = prepare_request(config, context)
    except (ScriptError, ValueError, TypeError, UnicodeError):
        result = APIResult("error", error="request_error")
    else:
        # Check capacity before performing a potentially irreversible request.
        # Preserve the previous variable while evaluating its request inputs.
        context.assign(config["variable_name"], APIResult("error", error="response_too_large").variable())
        if context.api_calls >= 10:
            result = APIResult("error", error="request_limit")
        else:
            context.api_calls += 1
            client = context.api_client or SendAPIClient()
            result = client.send(config["method"], url, headers, body, config["timeout_seconds"])
    try:
        context.assign(config["variable_name"], result.variable())
    except ScriptError:
        # A bounded response can still exceed the whole conversation's remaining
        # variable capacity. Preserve the request outcome as a small error value.
        result = APIResult("error", result.status_code, error="variable_limit")
        context.assign(config["variable_name"], result.variable())
    return APIOutcome(config[result.outcome + "_path_id"], result.outcome, result.status_code, result.error)
