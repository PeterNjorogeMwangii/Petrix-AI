from dataclasses import dataclass
from app.workflows.chat_script import ScriptError


@dataclass(frozen=True)
class AIAgentHandoff:
    connection_id:str


def execute(config,context):
    if not context.ai_handoff_client:raise ScriptError('AI Agent Handoff requires a verified receiving agent connection.')
    context.ai_handoff_client.prepare(config['connection_id'],context)
    return AIAgentHandoff(config['connection_id'])
