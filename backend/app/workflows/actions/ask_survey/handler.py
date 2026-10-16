"""Survey questions and typed answers; the workflow runtime owns continuation."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
import re
import uuid

from email_validator import EmailNotValidError, validate_email

from app.workflows.chat_script import ScriptError
from app.workflows.templates import render

NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z", re.ASCII)
PHONE = re.compile(r"\+?[0-9]{7,15}\Z", re.ASCII)


@dataclass(frozen=True)
class SurveyWait:
    pass


def execute(config, context):
    return SurveyWait()


def active_field(node, state):
    pending = state.get("survey") or {}
    position = pending.get("field_index")
    fields = node["config"]["fields"]
    if type(position) is not int or not 0 <= position < len(fields) or fields[position]["id"] != pending.get("field_id"):
        raise ScriptError("This survey changed. Restart the tester to begin again.")
    return fields[position]


def status(node, state):
    field = active_field(node, state)
    return {"node_id": node["id"], "node_number": node["number"], "field_id": field["id"],
        "question_number": state["survey"]["field_index"] + 1, "total_fields": len(node["config"]["fields"]),
        "answer_type": field["answer_type"], "variable_name": field["variable_name"],
        "enable_skip": field["enable_skip"], "answer_token": state["survey"]["answer_token"]}


def question(node, context, position=0):
    field = node["config"]["fields"][position]
    context.state["survey"] = {"node_id": node["id"], "field_index": position,
        "field_id": field["id"], "answer_token": str(uuid.uuid4())}
    return reply(node, context, field["question"])


def reply(node, context, text):
    field = active_field(node, context.state)
    answers = [render(value, context, maximum=100) for value in field["quick_answers"]]
    if len(set(answers)) != len(answers):
        raise ScriptError("Survey quick answers must remain distinct after substituting variables.")
    return {"content": render(text, context), "quick_replies": answers, "source": "ask_survey",
        "node_id": node["id"], "survey": status(node, context.state)}


def typed_answer(field, event):
    if field["answer_type"] in {"attachment", "attachments"}:
        files = event.get("attachments") or []
        if event["event"] != "attachment_received" or not 1 <= len(files) <= 10:
            raise ValueError()
        if field["answer_type"] == "attachment" and len(files) != 1:
            raise ValueError()
        return files[0] if field["answer_type"] == "attachment" else files
    if event["event"] != "message_received":
        raise ValueError()
    text = (event.get("content") or "").strip()
    if not text:
        raise ValueError()
    if field["answer_type"] == "text":
        return text
    if field["answer_type"] == "email":
        try:
            return validate_email(text, check_deliverability=False).normalized
        except EmailNotValidError:
            raise ValueError() from None
    if field["answer_type"] == "phone":
        value = re.sub(r"[ ().\-\t]", "", text)
        if not PHONE.fullmatch(value):
            raise ValueError()
        return value
    if len(text) > 100 or not NUMBER.fullmatch(text):
        raise ValueError()
    try:
        value = Decimal(text)
        if not value.is_finite() or value.adjusted() > 76:
            raise ValueError()
        if value == value.to_integral_value():
            integer = int(value)
            if integer.bit_length() > 256:
                raise ValueError()
            return integer
        number = float(value)
        if not math.isfinite(number) or (not number and value):
            raise ValueError()
        return number
    except (InvalidOperation, OverflowError):
        raise ValueError() from None


def answer(node, context):
    """Return (next message or None, outcome); None resumes the saved stack."""
    field = active_field(node, context.state)
    skipped = context.event["event"] == "message_received" and context.event.get("content", "").strip().casefold() == "skip" and field["enable_skip"]
    try:
        value = None if skipped else typed_answer(field, context.event)
    except ValueError:
        return reply(node, context, field["invalid_answer_reply"]), "invalid_answer"
    context.assign(field["variable_name"], value)
    next_position = context.state["survey"]["field_index"] + 1
    if next_position < len(node["config"]["fields"]):
        return question(node, context, next_position), "skipped" if skipped else "answered"
    context.state.pop("survey", None)
    return None, "completed_after_skip" if skipped else "completed"
