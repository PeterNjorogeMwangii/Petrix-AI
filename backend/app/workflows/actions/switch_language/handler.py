"""A language change updates this chat only and emits no assistant message."""


def execute(config, context):
    # ActionContext checks agent configuration and persists the session language.
    # It is shared with Set variable's existing system-language assignment.
    context.assign("language", config["target_language"])
    return None
