from dataclasses import dataclass

from pydantic import ValidationError

from app.workflows.actions.collect_feedback.config import CollectFeedbackConfig
from app.workflows.chat_script import ScriptError
from app.workflows.templates import render


@dataclass
class FeedbackRequest:
    payload: dict


def execute(config, context):
    if not context.state.get("closed") or context.event["event"] not in {"chat_closed", "collect_feedback"}:
        raise ScriptError("Collect Feedback can only request feedback after chat closure.")
    if context.state.get("feedback_submitted") or context.state.get("feedback_request"): return None
    try: config = CollectFeedbackConfig.model_validate(config).model_dump(mode="json")
    except ValidationError: raise ScriptError("Check the saved Collect Feedback settings before starting a new conversation.") from None
    if not config["rating_enabled"] and not config["survey_enabled"]: return None
    translations = config["translations"]
    ordered = list(dict.fromkeys([context.language, context.agent.default_language] + list(translations)))
    selected = next(translations[code] for code in ordered if code in translations)
    channel = context.state.get("channel", "webchat")
    fields = []
    if config["survey_enabled"]:
        for field in config["fields"]:
            text = next(translations[code]["fields"][field["id"]] for code in ordered
                if code in translations and field["id"] in translations[code]["fields"])
            choices = [render(choice, context, maximum=100) for choice in text["choices"]] if field["reply_type"] == "choice" else []
            if len(choices) != len(set(choices)): raise ScriptError("Feedback choices must remain distinct after substituting variables.")
            fields.append({**field, "question": render(text["question"], context, maximum=1000),
                "placeholder": render(text["placeholder"], context, maximum=200) if text["placeholder"] else "", "choices": choices})
    return FeedbackRequest({"title": render(selected["webchat_rating_title"], context, maximum=500) if config["rating_enabled"] else "",
        "rating_enabled": config["rating_enabled"], "rating_variable_name": config["rating_variable_name"],
        "request_message": render(selected["rating_request_message"], context) if config["rating_enabled"] else "",
        "fields": fields, "channel": channel, "typed_rating": channel in {"bitrix24", "africas_talking"},
        "rating_buttons": [1,2,3,4,5] if channel in {"messaging","whatsapp"} and config["rating_enabled"] else [],
        "submission_template": selected["submission_text"]})
