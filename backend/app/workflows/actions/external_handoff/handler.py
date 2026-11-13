from dataclasses import dataclass
from app.workflows.chat_script import ScriptError


@dataclass(frozen=True)
class ExternalHandoff:
    platform: str


def execute(config, context):
    if not context.external_handoff_client:
        raise ScriptError('External Handoff requires a verified integration and a linked provider conversation.')
    context.external_handoff_client.prepare(config['platform'])
    return ExternalHandoff(config['platform'])
