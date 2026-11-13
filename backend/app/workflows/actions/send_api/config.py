import json
import re
import uuid
from typing import List, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import Field, StrictInt, field_validator, model_validator

from app.agent_schemas import StrictModel
from app.workflows.chat_script import ScriptError, json_value, parse_expression
from app.workflows.context import validate_variable_name
from app.workflows.templates import TOKEN

HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z", re.ASCII)
CONTROLLED_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "proxy-connection",
    "proxy-authorization", "upgrade", "te", "trailer", "accept-encoding"}


def validate_endpoint(value):
    placeholder = TOKEN.sub("value", value)
    try:
        parts = urlsplit(placeholder)
        url = httpx.URL(placeholder)
        if not value.startswith("https://") or not parts.hostname or not url.raw_host:
            raise ValueError()
        if parts.username is not None or parts.password is not None or "#" in placeholder:
            raise ValueError()
        if any(character.isspace() or ord(character) < 32 for character in value) or any(character in placeholder for character in "\\{}"):
            raise ValueError()
        if "{" in urlsplit(value).netloc or "}" in urlsplit(value).netloc or "%" in parts.hostname:
            raise ValueError()
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError()
    except (ValueError, httpx.InvalidURL):
        raise ValueError("Use an HTTPS endpoint without credentials or fragments. Variables are supported in the path and query only.") from None
    return value


def validate_header(name, value):
    if not HEADER_NAME.fullmatch(name) or name.lower() in CONTROLLED_HEADERS:
        raise ValueError("Use a valid header name. Host, transport, proxy, and encoding headers are managed by Petrix.")
    if any(ord(character) < 32 or ord(character) > 126 for character in value):
        raise ValueError("Header values must use printable ASCII text without line breaks.")


def load_json(source):
    def invalid_constant(_):
        raise ValueError("Use finite JSON numbers.")
    try:
        return json_value(json.loads(source, parse_constant=invalid_constant))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("Enter valid JSON within the chat-variable size and nesting limits.") from None


class APIHeader(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    value: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def valid_header(self):
        validate_header(self.key, self.value)
        return self


class SendAPIConfig(StrictModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str = Field(min_length=1, max_length=2048)
    headers: List[APIHeader] = Field(default_factory=list, max_length=20)
    payload_mode: Literal["json", "expression"] = "json"
    payload: str = Field(default="", max_length=16000)
    variable_name: str = Field(min_length=1, max_length=64)
    timeout_seconds: StrictInt = Field(default=30, ge=1, le=60)
    success_path_id: uuid.UUID
    error_path_id: uuid.UUID
    timeout_path_id: uuid.UUID

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        return validate_endpoint(value)

    @field_validator("variable_name")
    @classmethod
    def custom_variable(cls, value):
        validate_variable_name(value)
        if value == "language":
            raise ScriptError("Use a custom variable name instead of the system language variable.")
        return value

    @model_validator(mode="after")
    def request_settings(self):
        if len({header.key.lower() for header in self.headers}) != len(self.headers):
            raise ValueError("Header names must be unique, regardless of case.")
        if len({self.success_path_id, self.error_path_id, self.timeout_path_id}) != 3:
            raise ValueError("Success, Error, and Timeout must have different path IDs.")
        if self.method != "GET" and self.payload.strip():
            if self.payload_mode == "json": load_json(self.payload)
            else: parse_expression(self.payload)
        return self
