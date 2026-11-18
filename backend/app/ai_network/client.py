"""Authenticated, bounded HTTPS protocol. Credentials never enter URLs."""
import json
from fastapi import HTTPException
from app.workflows.actions.send_api.client import SendAPIClient
from app.ai_network.service import is_local


class PeerClient:
    def __init__(self,settings,json_transport=None,file_transport=None):
        hosts=settings.send_api_allowed_private_hosts.split(',') if settings.send_api_allowed_private_hosts else ()
        self.json_transport=json_transport or SendAPIClient(hosts,max_response_bytes=2*1024*1024,validate_response=False)
        self.file_transport=file_transport or SendAPIClient(hosts,max_response_bytes=30*1024*1024,validate_response=False)

    def call(self,request,domain,operation,identifier,credential,payload=None):
        if is_local(domain,request.app.state.settings):
            from app.ai_network import receiver
            with request.app.state.session_factory() as db:
                return receiver.local_call(operation,identifier,credential,payload,request,db)
        if operation in {'verify','start'}:
            path='/api/ai-network/agents/{}/{}'.format(identifier,'verify' if operation=='verify' else 'handoffs')
            header={'X-Petrix-Agent-Secret':credential}
        else:
            resource='files/'+str(payload['file_id']) if operation=='file' else {'event':'events','snapshot':'snapshot'}[operation]
            path='/api/ai-network/sessions/{}/{}'.format(identifier,resource)
            header={'Authorization':'Bearer '+credential}
        method='GET' if operation in {'snapshot','file'} else 'POST'
        transport=self.file_transport if operation=='file' else self.json_transport
        result=transport.send(method,domain+path,{'Content-Type':'application/json',**header},
            json.dumps(payload or {},ensure_ascii=False,allow_nan=False).encode() if method=='POST' else None,30)
        if result.outcome!='success' or not isinstance(result.body,dict):
            raise HTTPException(503,'The receiving AI agent is unavailable. The saved handoff/request can be retried safely.')
        return result.body
