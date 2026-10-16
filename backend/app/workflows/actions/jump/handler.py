from dataclasses import dataclass


@dataclass(frozen=True)
class JumpTo:
    target_node_id: str


def execute(config, context):
    # A control transfer, never a chat message. The runtime owns the stack.
    return JumpTo(str(config["target_node_id"]))
