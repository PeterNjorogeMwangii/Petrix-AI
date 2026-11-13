from dataclasses import dataclass


@dataclass(frozen=True)
class CloseChat:
    pass


def execute(config, context):
    return CloseChat()
