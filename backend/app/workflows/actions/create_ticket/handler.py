from dataclasses import dataclass
from app.workflows.chat_script import ScriptError


@dataclass
class TicketCreated:
    id: str
    reference: str


def execute(config,context):
    if context.ticket_client is None:raise ScriptError('Ticket storage is unavailable.')
    result=context.ticket_client.create({"id":context.current_node_id,"config":config},context)
    context.state['last_ticket']=result
    return TicketCreated(result['id'],result['reference'])
