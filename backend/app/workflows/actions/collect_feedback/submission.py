"""Validate issued feedback snapshots before consuming their one-use token."""
from copy import deepcopy

from fastapi import HTTPException

from app.workflows.actions.ask_survey.handler import typed_answer
from app.workflows.chat_script import ScriptError
from app.workflows.context import ActionContext


def accept_feedback(request, rating, answers, agent, state):
    if request.get("rating_enabled", True):
        if type(rating) is not int or not 1 <= rating <= 5: raise HTTPException(422, "Choose a rating from 1 to 5.")
    elif rating is not None:
        raise HTTPException(422, "Rating is disabled for this feedback request.")
    fields = request.get("fields", [])
    if set(answers) - {field["id"] for field in fields}: raise HTTPException(422, "Submit only fields from this feedback request.")
    updated = deepcopy(state)
    context = ActionContext(agent=agent, language=request.get("language") or agent.default_language,
        event={"event": "collect_feedback", "rating": rating}, state=updated)
    try:
        if request.get("rating_enabled", True): context.assign(request.get("rating_variable_name", "feedback_rating"), rating)
        for field in fields:
            text = answers.get(field["id"], "").strip()
            if not text:
                if field["required"]: raise HTTPException(422, "{}: This field is required.".format(field["question"]))
                value = None
            elif field["reply_type"] == "choice":
                if text not in field["choices"]: raise HTTPException(422, "{}: Choose one of the listed answers.".format(field["question"]))
                value = text
            else:
                try:
                    value = typed_answer({"answer_type": "text" if field["reply_type"] == "open_response" else field["reply_type"]},
                        {"event": "message_received", "content": text})
                except ValueError:
                    raise HTTPException(422, "{}: Enter a valid {} answer.".format(field["question"], field["reply_type"].replace("_", " "))) from None
            context.assign(field["variable_name"], value)
    except ScriptError as error:
        raise HTTPException(422, str(error)) from None
    return updated


def public_feedback(request):
    return {"rating_enabled": True, "fields": [], "channel": "webchat", "typed_rating": False,
        "rating_buttons": [], "rating_variable_name": "feedback_rating", "request_message": request.get("title", ""),
        **{key: value for key, value in request.items() if key not in {"continuation", "workflow_revision", "submission_template"}}}
