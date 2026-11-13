import base64
from copy import deepcopy
import hashlib
import hmac
import json
import re
import uuid
from urllib.parse import urlsplit
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.core.security import utcnow
from app.models import ExternalIntegration, ExternalConversation, AgentChange
from app.workflows.chat_script import ScriptError
from app.workflows.numbering import walk

PLATFORMS = {'zendesk': 'Zendesk', 'bitrix24': 'Bitrix24'}
BLOCKED = {'sending', 'transferred', 'uncertain'}


def cipher(settings):
    key = hmac.new(settings.session_signing_secret.get_secret_value().encode(), b'petrix-external-integrations-v1', hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def credentials(row, settings):
    try:
        return json.loads(cipher(settings).decrypt(row.credentials_ciphertext.encode()))
    except (InvalidToken, ValueError, UnicodeError):
        raise HTTPException(422, 'Integration credentials are unavailable. Re-enter them in External integrations.') from None


def integration(db, workspace_id, platform, lock=False):
    statement = select(ExternalIntegration).where(ExternalIntegration.workspace_id == workspace_id, ExternalIntegration.platform == platform)
    return db.scalar(statement.with_for_update().execution_options(populate_existing=True) if lock else statement)


def active(row):
    return bool(row and row.enabled and row.verified_at)


def payload(row, platform):
    return {'platform':platform, 'name':PLATFORMS[platform], 'revision':row.revision if row else 0,
        'enabled':bool(row and row.enabled), 'active':active(row), 'verified_at':row.verified_at.isoformat() if row and row.verified_at else None,
        'config':row.config if row else {}, 'has_secret':bool(row), 'has_bot_client_id':bool(row and platform == 'bitrix24')}


def account_key(row):
    return hashlib.sha256((row.config['origin'] + '/' + row.config.get('app_id', '')).encode()).hexdigest()


def public_status(db, conversation):
    row = db.get(ExternalConversation, conversation.id)
    return {'platform':row.platform,'name':PLATFORMS[row.platform],'external_id':row.external_id,'status':row.status,'error':row.error,
        'revision':row.revision,'updated_at':row.updated_at.isoformat()} if row else None


def save(db, workspace_id, platform, body, settings):
    row = integration(db, workspace_id, platform, lock=True)
    if (row.revision if row else 0) != body.expected_revision:
        raise HTTPException(409, 'Integration changed. Reload before saving.')
    config = body.model_dump(exclude={'expected_revision','enabled','secret','bot_client_id'})
    if platform == 'zendesk':
        if not urlsplit(config['origin']).hostname.endswith('.zendesk.com') or not all(config[key] for key in ('app_id','switchboard_id','target_id','key_id')):
            raise HTTPException(422, 'Zendesk requires your zendesk.com portal, app, switchboard, target integration and key IDs.')
    elif not all(config[key].isdigit() and int(config[key]) > 0 for key in ('key_id','target_id')):
        raise HTTPException(422, 'Bitrix24 requires a positive webhook user ID and destination employee ID.')
    else:
        config['app_id'] = ''; config['switchboard_id'] = ''
    # A retained secret may only be reused for the same account, to avoid sending it
    # to a newly entered host. New credentials are required when switching accounts.
    old = credentials(row, settings) if row and (body.secret is None or platform == 'bitrix24' and body.bot_client_id is None) else {}
    if row and (config['origin'],config['app_id']) != (row.config['origin'],row.config.get('app_id','')) and body.secret is None:
        raise HTTPException(422, 'Enter new credentials when changing the external account.')
    values = {'secret':body.secret.get_secret_value() if body.secret else old.get('secret','')}
    if platform == 'bitrix24':
        values['bot_client_id'] = body.bot_client_id.get_secret_value() if body.bot_client_id else old.get('bot_client_id','')
    if any(not value or len(value)>2000 or any(ord(c)<32 for c in value) for value in values.values()):
        raise HTTPException(422, 'Enter all required integration credentials (up to 2,000 characters each).')
    if platform == 'bitrix24' and not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', values['secret']):
        raise HTTPException(422, 'Enter the Bitrix24 webhook secret only, without its URL.')
    if row is None:
        row=ExternalIntegration(workspace_id=workspace_id,platform=platform,config=config,credentials_ciphertext='',enabled=body.enabled,revision=1);db.add(row)
    else:
        row.revision+=1
    row.config=config;row.enabled=body.enabled;row.verified_at=None
    row.credentials_ciphertext=cipher(settings).encrypt(json.dumps(values).encode()).decode()
    db.commit()
    return payload(row,platform)


def link(db, conversation, body, settings, provider):
    if conversation.live_status in {'pending','active'} or conversation.workflow_state.get('closed'):
        raise HTTPException(409, 'Link an open AI conversation before starting any handoff.')
    old=db.get(ExternalConversation,conversation.id)
    if old:
        if old.platform==body.platform and old.external_id==body.external_id:return public_status(db,conversation)
        raise HTTPException(409, 'This conversation is already linked. Start a new test to use another provider chat.')
    row=integration(db,conversation.workspace_id,body.platform,lock=True)
    if not active(row):raise HTTPException(422, 'Verify and enable this external integration first.')
    if body.platform=='bitrix24' and (not body.external_id.isdigit() or not 0<int(body.external_id)<=2147483647):
        raise HTTPException(422, 'Enter a positive Bitrix24 chat ID.')
    if not provider.conversation_exists(body.platform,row.config,credentials(row,settings),body.external_id):
        raise HTTPException(422, 'The provider could not confirm this conversation. Check the ID, account, and access permissions.')
    db.add(ExternalConversation(conversation_id=conversation.id,workspace_id=conversation.workspace_id,platform=body.platform,
        external_id=body.external_id,account_key=account_key(row),status='linked',error='',revision=1,updated_at=utcnow()))
    try:db.commit()
    except IntegrityError:
        db.rollback();raise HTTPException(409,'This provider conversation is already linked to another Petrix AI chat.') from None
    return public_status(db,conversation)


def validate_references(db,workspace_id,nodes):
    available={row.platform for row in db.scalars(select(ExternalIntegration).where(ExternalIntegration.workspace_id==workspace_id)) if active(row)}
    for node in walk(nodes):
        if node['kind']=='action' and node['type']=='external_handoff' and node['enabled'] and node['config']['platform'] not in available:
            raise HTTPException(422,'External Handoff requires an enabled, verified integration in this workspace.')


class HandoffService:
    def __init__(self,db,conversation,settings,provider):
        self.db=db;self.conversation=conversation;self.settings=settings;self.provider=provider;self.prepared=None

    def prepare(self,platform):
        row=integration(self.db,self.conversation.workspace_id,platform,lock=True)
        binding=self.db.get(ExternalConversation,self.conversation.id)
        if not active(row):raise ScriptError('Verify and enable the selected platform in External integrations.')
        if not binding or binding.platform!=platform or binding.account_key!=account_key(row):
            raise ScriptError('This chat is not linked to a conversation in the selected external account. Link a provider test conversation first.')
        if binding.status in BLOCKED:raise ScriptError('This conversation has already left AI processing. External handoffs cannot be replayed.')
        self.prepared=(row,binding,credentials(row,self.settings))

    def dispatch(self,state,actor_id):
        row,binding,secrets=self.prepared
        binding.status='sending';binding.error='';binding.attempt_id=uuid.uuid4();binding.revision+=1;binding.updated_at=utcnow();self.conversation.live_updated_at=binding.updated_at
        state.update({'closed':True,'closed_at':utcnow().isoformat(),'close_source':'external_handoff','stack':[]})
        state.pop('survey',None);state.pop('feedback_request',None)
        state['external_handoff']=public_status(self.db,self.conversation)
        self.conversation.workflow_state=deepcopy(state)
        # The no-replay marker and preceding messages are durable BEFORE a network
        # request. A process crash leaves a reviewable sending state, never a replay.
        self.db.commit()
        from app.external_handoff.client import TransferResult
        try:result=self.provider.transfer(binding.platform,row.config,secrets,binding.external_id)
        except Exception:result=TransferResult('uncertain','confirmation_unavailable')
        binding.status=result.status;binding.error=result.error;binding.revision+=1;binding.updated_at=utcnow();self.conversation.live_updated_at=binding.updated_at
        if result.status=='failed':
            state['closed']=False;state.pop('closed_at',None);state.pop('close_source',None)
        self.conversation.workflow_state=deepcopy(state)
        self.db.add(AgentChange(workspace_id=self.conversation.workspace_id,agent_id=self.conversation.agent_id,agent_name=self.conversation.agent_name,
            actor_id=actor_id,action='external_handoff_'+result.status,summary='External handoff to {}: {}.'.format(PLATFORMS[binding.platform],result.status)))
        self.db.commit()
        return result
