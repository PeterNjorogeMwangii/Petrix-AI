import uuid

from app.agent_schemas import StrictModel


class JumpConfig(StrictModel):
    # UUID is the identity; display numbers and names may change independently.
    target_node_id: uuid.UUID
