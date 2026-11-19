import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select
from app import agent_service
from app.agent_schemas import LANGUAGE_CODES
from app.core.security import new_token, token_hash, utcnow
from app.models import Agent, AINetworkCredential, AITransferConnection, AIInboundSession, AIOutgoingSession, AgentMessage, Membership
from app.workflows.chat_script import ScriptError
from app.workflows.numbering import walk
from app.ai_network import privacy


def encrypt(value,settings):
    key=hmac.new(settings.session_signing_secret.get_secret_value().encode(),b'petrix-ai-network-v1',hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(key)).encrypt(value.encode()).decode()


def decrypt(value,settings):
    try:
        key=hmac.new(settings.session_signing_secret.get_secret_value().encode(),b'petrix-ai-network-v1',hashlib.sha256).digest()
        return Fernet(base64.urlsafe_b64encode(key)).decrypt(value.encode()).decode()
    except (InvalidToken,ValueError,UnicodeError):raise HTTPException(422,'Network credentials are unavailable. Reconnect using the receiving agent credentials.') from None


def canonical_domain(value,settings):
    return settings.api_base_url if value in {settings.api_base_url,settings.frontend_origin} else value


def is_local(value,settings):return canonical_domain(value,settings)==settings.api_base_url


def credential_payload(agent,row,settings):
    return {'agent_id':str(agent.id),'agent_name':agent.name,'domain':settings.api_base_url,
        'enabled':bool(row and row.enabled),'revision':row.revision if row else 0,'has_secret':bool(row)}


def update_credentials(db,agent,actor,body,settings):
    row=db.scalar(select(AINetworkCredential).where(AINetworkCredential.agent_id==agent.id).with_for_update())
    if (row.revision if row else 0)!=body.expected_revision:raise HTTPException(409,'Network credentials changed. Reload before saving.')
    secret=None
    if row is None:
        secret=new_token();row=AINetworkCredential(agent_id=agent.id,actor_id=actor,enabled=body.enabled,revision=1,
            secret_hash=token_hash(secret),secret_ciphertext=encrypt(secret,settings));db.add(row)
    else:
        if body.rotate:
            secret=new_token();row.secret_hash=token_hash(secret);row.secret_ciphertext=encrypt(secret,settings)
        if body.rotate or row.enabled!=body.enabled:row.revision+=1
        if body.rotate or row.enabled!=body.enabled:row.actor_id=actor
        row.enabled=body.enabled
    agent_service.record_change(db,agent,actor,'network_credentials','Incoming AI handoffs {}{}.'.format('enabled' if row.enabled else 'disabled','; secret rotated' if body.rotate else ''))
    db.commit();return {**credential_payload(agent,row,settings),**({'secret':secret} if secret else {})}


def authenticate_agent(db,agent_id,secret):
    agent=db.get(Agent,agent_id);row=db.get(AINetworkCredential,agent_id)
    valid=bool(row and row.enabled and row.actor_id and hmac.compare_digest(row.secret_hash,token_hash(secret or '')))
    if not agent or not valid:raise HTTPException(401,'Network authentication failed.')
    role=db.scalar(select(Membership.role).where(Membership.workspace_id==agent.workspace_id,Membership.user_id==row.actor_id))
    if not role or not agent_service.can_manage(db,agent.workspace_id,role):raise HTTPException(401,'Network authentication failed.')
    return agent,row


def connection_payload(row):
    return {'id':str(row.id),'source_id':str(row.source_id),'agent_id':str(row.target_id),'name':row.target_name,
        'domain':row.domain,'verified_at':row.verified_at.isoformat()}


def owned_connection(db,agent,identifier):
    row=db.scalar(select(AITransferConnection).where(AITransferConnection.id==identifier,AITransferConnection.source_id==agent.id,AITransferConnection.workspace_id==agent.workspace_id))
    if not row:raise HTTPException(422,'Choose a verified transfer connection belonging to this AI agent.')
    return row


def validate_references(db,agent,nodes):
    ids={str(value) for value in db.scalars(select(AITransferConnection.id).where(AITransferConnection.source_id==agent.id,AITransferConnection.workspace_id==agent.workspace_id))}
    for node in walk(nodes):
        if node['kind']=='action' and node['type']=='ai_agent_handoff' and node['config']['connection_id'] not in ids:
            raise HTTPException(422,'Choose a verified AI Agent Handoff connection belonging to this agent.')


@dataclass(frozen=True)
class NetworkActor:
    user_id:uuid.UUID
    conversation_id:uuid.UUID
    agent_id:uuid.UUID
    request_id:object=None


class TransferPlan:
    def __init__(self,db,conversation,settings):
        self.db=db;self.conversation=conversation;self.settings=settings;self.connection=None;self.payload=None

    def prepare(self,identifier,context):
        row=owned_connection(self.db,context.agent,uuid.UUID(identifier));domain=canonical_domain(row.domain,self.settings)
        incoming=self.db.scalar(select(AIInboundSession).where(AIInboundSession.conversation_id==self.conversation.id))
        trail=list(incoming.trail) if incoming else [{'agent_id':str(context.agent.id),'domain':self.settings.api_base_url}]
        if len(trail)>=8 or any(hop['agent_id']==str(row.target_id) and canonical_domain(hop['domain'],self.settings)==domain for hop in trail):
            raise ScriptError('AI handoff loop detected or the eight-agent transfer limit was reached.')
        if is_local(domain,self.settings):
            try:authenticate_agent(self.db,row.target_id,decrypt(row.secret_ciphertext,self.settings))
            except HTTPException:raise ScriptError('The receiving agent credentials changed or incoming handoffs were disabled. Verify the connection again.') from None
        variables=privacy.context(context.variables)
        recent=self.db.scalars(select(AgentMessage).where(AgentMessage.conversation_id==self.conversation.id).order_by(AgentMessage.sequence.desc()).limit(40)).all()
        history=[];size=0
        for message in recent:
            value=privacy.text(message.content)
            if message.role not in {'user','assistant'} or size+len(value.encode())>16384:continue
            history.append({'role':message.role,'content':value[:4000]});size+=len(value.encode())
        self.connection=row
        self.payload={'version':1,'context':variables,'history':list(reversed(history)),
            'language':context.language,'channel':context.state.get('channel','webchat'),'trail':trail}


def verified_agent(value,expected):
    if not isinstance(value,dict) or value.get('id')!=str(expected) or not isinstance(value.get('name'),str) or not 1<=len(value['name'])<=100:raise ValueError('Invalid receiving agent metadata.')
    languages=value.get('languages')
    if not isinstance(languages,list) or not 1<=len(languages)<=20 or any(not isinstance(code,str) or code not in LANGUAGE_CODES for code in languages) or value.get('default_language') not in languages:raise ValueError('Invalid receiving language metadata.')
    return value
