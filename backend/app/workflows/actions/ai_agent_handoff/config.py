import uuid
from app.agent_schemas import StrictModel


class AIAgentHandoffConfig(StrictModel):
    connection_id:uuid.UUID
