from dataclasses import dataclass


@dataclass(frozen=True)
class HandoffWait:
    routing: dict
    eligible_ids: list
    assigned_id: object = None
    priority: bool = False
    priority_type: str = 'skip_queue'


@dataclass(frozen=True)
class HandoffUnavailable:
    path_id: str
    reason: str


def execute(config, context):
    if not context.handoff_client:
        return HandoffUnavailable(config['unavailable_path_id'], 'support_unavailable')
    offer, reason = context.handoff_client.offer(config, context)
    if not offer:
        return HandoffUnavailable(config['unavailable_path_id'], reason)
    return HandoffWait(**offer)
