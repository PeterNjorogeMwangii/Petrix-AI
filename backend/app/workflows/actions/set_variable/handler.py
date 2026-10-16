from app.workflows.chat_script import evaluate


def execute(config, context):
    value = evaluate(config["chat_script"], context.environment())
    context.assign(config["variable_name"], value)
    # Data-only action: the runtime must not create an assistant message.
    return None
