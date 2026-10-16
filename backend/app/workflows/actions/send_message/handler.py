import secrets
from app.workflows.chat_script import ScriptError
from app.workflows.templates import render


def execute(config, context):
    translations = config["translations"]
    selected = translations.get(context.language) or translations.get(context.agent.default_language) or next(iter(translations.values()))
    content = render(secrets.choice(selected["messages"]), context)
    replies = [render(reply, context, maximum=100) for reply in selected.get("quick_replies", [])]
    if len(set(replies)) != len(replies):
        raise ScriptError("Quick replies must remain distinct after substituting variables.")
    return {"content": content, "quick_replies": replies}
