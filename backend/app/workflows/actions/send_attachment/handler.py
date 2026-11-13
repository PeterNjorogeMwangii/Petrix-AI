from app.workflows.chat_script import ScriptError, evaluate


def execute(config, context):
    translations = config["translations"]
    selected = translations.get(context.language) or translations.get(context.agent.default_language) or next(iter(translations.values()))
    if context.attachment_store is None: raise ScriptError("Attachment storage is unavailable.")
    if context.attachment_calls >= 10: raise ScriptError("A turn can send up to 10 attachments.")
    context.attachment_calls += 1
    try:
        value = selected["asset_id"] if selected["source"] == "upload" else selected["url"] if selected["source"] == "link" else evaluate(selected["chat_script"], context.environment())
        if type(value) is not str: raise ValueError("The attachment expression must return a file URL string.")
        file = context.attachment_store.deliver(value, selected["file_format"], selected["source"])
    except ValueError as error:
        raise ScriptError(str(error)) from None
    return {"content": "", "quick_replies": [], "attachments": [file]}
