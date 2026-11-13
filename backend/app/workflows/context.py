"""Conversation-scoped data shared by individual action handlers."""
import json
import keyword
import re
from dataclasses import dataclass

from app.workflows.chat_script import FUNCTIONS, ScriptError, json_value

READ_ONLY = {"agent_id", "agent_name", "default_language", "conversation_id", "message", "attachment_name", "attachment_type", "system", "variables"}
NAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z", re.ASCII)


def validate_variable_name(name):
    if not NAME_PATTERN.fullmatch(name):
        raise ScriptError("Use 1–64 characters, starting with a letter, followed by letters, numbers, or underscores.")
    if keyword.iskeyword(name) or name in FUNCTIONS or name in READ_ONLY:
        raise ScriptError("This name is reserved or identifies a read-only system variable.")
    return name


@dataclass
class ActionContext:
    agent: object
    language: str
    event: dict
    state: dict
    api_client: object = None
    api_calls: int = 0
    sql_client: object = None
    sql_calls: int = 0
    attachment_store: object = None
    attachment_calls: int = 0
    carousel_calls: int = 0
    llm_client: object = None
    auto_fill_client: object = None

    @property
    def variables(self):
        return self.state.setdefault("variables", {})

    def system_values(self):
        attachment = self.event.get("attachment") or {}
        return {"agent_id": str(self.agent.id), "agent_name": self.agent.name,
            "default_language": self.agent.default_language, "language": self.language,
            "conversation_id": str(self.event.get("conversation_id") or ""), "message": self.event.get("content") or "",
            "feedback_rating": self.event.get("rating") if self.event.get("rating") is not None else self.state.get("csat_rating"), "attachment_name": attachment.get("name", ""),
            "attachment_type": attachment.get("media_type", ""), "custom_fields": self.state.get("custom_fields", {})}

    def environment(self):
        system = self.system_values()
        return {**system, **self.variables, "system": {**system, "intent": self.state.get("intent", {})}, "variables": self.variables}

    def assign(self, name, value):
        validate_variable_name(name)
        value = json_value(value)
        if name == "language":
            if type(value) is not str or value not in self.agent.languages:
                raise ScriptError("Set language to one of this agent's configured language codes.")
            self.language = value
            self.state["language"] = value
            return
        updated = {**self.variables, name: value}
        if len(updated) > 100 or len(json.dumps(updated, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 64 * 1024:
            raise ScriptError("A chat supports up to 100 variables using 64 KB in total.")
        self.state["variables"] = updated
