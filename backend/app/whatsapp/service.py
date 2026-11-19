import hashlib,hmac,json,re,uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime,timezone,timedelta
from fastapi import HTTPException
from sqlalchemy import select,func
from sqlalchemy.dialects.postgresql import insert
from app.core.security import new_token,token_hash,utcnow
from app.models import (Workspace,Membership,Agent,AgentConversation,AgentMessage,SupportPerson,
    WebchatAsset,WhatsAppChannel as Channel,WhatsAppContact as Contact,WhatsAppConversation as Link,
    WhatsAppInbound as Inbound,WhatsAppOutbox as Outbox)
from app.whatsapp import credentials as crypto
from app.whatsapp.schemas import Configuration


def eligible(db,workspace_id):
    workspace=db.get(Workspace,workspace_id)
    return bool(workspace and workspace.plan_name.lower().replace(' plan','').strip() in {'lite','pro','total','enterprise'})


def own(db,workspace_id,agent_id,identifier,lock=False):
    query=select(Channel).where(Channel.id==identifier,Channel.workspace_id==workspace_id,Channel.agent_id==agent_id)
    if lock:query=query.with_for_update().execution_options(populate_existing=True)
    row=db.scalar(query)
    if not row:raise HTTPException(404,'WhatsApp channel not found.')
    return row


def revision(row,expected):
    if row.revision!=expected:raise HTTPException(409,'This WhatsApp channel changed. Reload before saving.')


def freeze(db,agent):
    from app.webchat.service import freeze as capture
    return capture(db,agent,{'prechat':{'fields':[]}})


def checked(db,agent,config):
    if config.language not in agent.languages:raise HTTPException(422,'Choose a language configured for this AI agent.')
    if config.icon:
        asset=db.get(WebchatAsset,config.icon)
        if not asset or asset.agent_id!=agent.id:raise HTTPException(422,'Choose an icon uploaded for this AI agent.')
    return config.model_dump(mode='json')


def payload(db,row,settings):
    configured=bool(row.app_id and row.phone_number_id and row.credentials)
    connected=bool(configured and row.verified_revision==row.connection_revision)
    return {'id':str(row.id),'channel_type':'whatsapp','name':row.name,'enabled':row.enabled,
        'config':row.config,'revision':row.revision,'updated_at':row.updated_at,'app_id':row.app_id,
        'phone_number_id':row.phone_number_id or '','has_app_secret':bool(row.credentials),'has_access_token':bool(row.credentials),
        'verified':connected,'verified_at':row.verified_at,'webhook_verified_at':row.webhook_verified_at,
        'webhook_url':settings.api_base_url.rstrip('/')+'/api/whatsapp/webhooks/'+row.app_id if row.app_id else '',
        'setup_app_id':str(row.id),'display_phone':row.display_phone,'verified_name':row.verified_name,
        'last_error':row.last_error,'eligible':eligible(db,row.workspace_id),'workflow_revision':row.runtime.get('revision'),
        'graph_version':settings.whatsapp_graph_version}


def inactive_reason(db,row):
    if not row or not row.enabled:return 'The WhatsApp channel is disabled.'
    if not eligible(db,row.workspace_id):return 'WhatsApp publishing requires a Lite plan or above.'
    if row.verified_revision!=row.connection_revision or not row.webhook_verified_at:return 'Verify the WhatsApp connection and webhook first.'
    if not db.scalar(select(Membership.user_id).where(Membership.workspace_id==row.workspace_id,Membership.user_id==row.actor_id)):
        return 'An authorised workspace member must save and enable this channel again.'
    return ''


def can_enable(db,row,settings):
    if not eligible(db,row.workspace_id):raise HTTPException(403,'WhatsApp publishing requires a Lite plan or above.')
    if not row.phone_number_id or not row.credentials or row.verified_revision!=row.connection_revision:raise HTTPException(409,'Save and verify the Meta connection before enabling WhatsApp.')
    if not row.webhook_verified_at:raise HTTPException(409,'Verify the callback URL in Meta and subscribe to messages before enabling WhatsApp.')
    if settings.app_env!='test' and not settings.api_base_url.startswith('https://'):raise HTTPException(422,'Configure API_BASE_URL with your publicly reachable HTTPS API address, then verify the callback in Meta.')


def create(db,agent,actor,body,settings):
    row=db.scalar(select(Channel).where(Channel.agent_id==agent.id,Channel.client_id==body.client_id))
    if row:return row
    if (db.scalar(select(func.count()).select_from(Channel).where(Channel.agent_id==agent.id)) or 0)>=50:raise HTTPException(422,'Use up to 50 WhatsApp channels per agent.')
    secret=new_token()
    row=Channel(client_id=body.client_id,workspace_id=agent.workspace_id,agent_id=agent.id,actor_id=actor,
        name=body.name,config=Configuration(language=agent.default_language).model_dump(mode='json'),runtime=freeze(db,agent),
        setup_secret=crypto.encrypt(secret,settings),verify_token_hash=token_hash(secret))
    db.add(row);db.commit();return row


def contact(db,row,value):
    phone=value.get('from','');bsuid=value.get('bsuid','');key=bsuid or phone
    found=db.scalar(select(Contact).where(Contact.channel_id==row.id,Contact.phone_number_id==row.phone_number_id,Contact.identity_key==key).with_for_update())
    if not found and bsuid and phone:
        # Only migrate a legacy phone-only record; never merge distinct BSUIDs.
        found=db.scalar(select(Contact).where(Contact.channel_id==row.id,Contact.phone_number_id==row.phone_number_id,Contact.wa_id==phone,Contact.bsuid=='').with_for_update())
        if found:found.identity_key=bsuid;found.bsuid=bsuid
    if not found:
        found=Contact(channel_id=row.id,phone_number_id=row.phone_number_id,identity_key=key,wa_id=phone,bsuid=bsuid);db.add(found);db.flush()
    if phone:found.wa_id=phone
    if bsuid:found.bsuid=bsuid
    stamp=datetime.fromtimestamp(value['timestamp'],timezone.utc)
    stamp=min(stamp,utcnow())
    if not found.last_customer_at or stamp>found.last_customer_at:found.last_customer_at=stamp
    return found


def mapping(value):return value if isinstance(value,dict) else {}
def array(value):return value if isinstance(value,list) else []
def clean(value,maximum):return value.replace('\x00','')[:maximum] if isinstance(value,str) else ''


def normalize(message,contacts):
    if not isinstance(message,dict):return None
    identifier=clean(message.get('id'),512);phone=message.get('from','');bsuid=message.get('from_user_id','')
    if not identifier or identifier!=message.get('id'):return None
    phone=phone if isinstance(phone,str) and re.fullmatch(r'[1-9][0-9]{4,19}',phone) else ''
    bsuid=bsuid if isinstance(bsuid,str) and re.fullmatch(r'[A-Z]{2}\.(?:ENT\.)?[A-Za-z0-9]{1,128}',bsuid) else ''
    if not (phone or bsuid):return None
    try:
        stamp=int(message.get('timestamp',0))
        if stamp<1 or stamp>253402300000:return None
    except (ValueError,TypeError,OverflowError):return None
    kind=message.get('type','unsupported');kind=kind if isinstance(kind,str) else 'unsupported'
    text='';button='';media={}
    if kind=='text':text=clean(mapping(message.get('text')).get('body'),4000)
    elif kind=='button':
        value=mapping(message.get('button'));text=clean(value.get('text'),4000);button=clean(value.get('payload'),256)
    elif kind=='interactive':
        value=mapping(message.get('interactive'));choice=mapping(value.get('button_reply') or value.get('list_reply'))
        text=clean(choice.get('title'),4000);button=clean(choice.get('id'),256)
        if not choice:kind='unsupported'
    elif kind in {'image','document','audio','video','sticker'}:
        value=mapping(message.get(kind));media={'id':clean(value.get('id'),32),'name':clean(value.get('filename'),255)};text=clean(value.get('caption'),4000)
    elif kind=='location':
        value=mapping(message.get('location'));text='Shared location: '+str(value.get('latitude',''))[:30]+', '+str(value.get('longitude',''))[:30]
    else:kind='unsupported'
    name=''
    for value in array(contacts):
        if isinstance(value,dict) and ((bsuid and value.get('user_id')==bsuid) or (phone and value.get('wa_id')==phone)):
            name=clean(mapping(value.get('profile')).get('name'),200);break
    return {'provider_id':identifier,'from':phone,'bsuid':bsuid,'type':kind,'text':text,'button_id':button,'media':media,'timestamp':stamp,'name':name}


@dataclass
class WhatsAppActor:
    user_id:uuid.UUID
    agent_id:uuid.UUID
    conversation_id:uuid.UUID
    contact_id:uuid.UUID
    channel_id:uuid.UUID


def ensure_conversation(db,row,person,entry):
    from app.teamspace.records import is_banned
    if person.person_id:
        profile=db.get(SupportPerson,person.person_id)
        if is_banned(profile):raise HTTPException(403,'This person is banned.')
    else:
        teamspace=row.runtime.get('agent',{}).get('teamspace_id')
        profile=SupportPerson(workspace_id=row.workspace_id,teamspace_id=uuid.UUID(teamspace) if teamspace else None,
            identity_key='whatsapp:'+str(person.id),name=entry.payload.get('name',''),email='',phone='+'+person.wa_id if person.wa_id else '')
        db.add(profile);db.flush();person.person_id=profile.id
    conversation=db.get(AgentConversation,person.current_conversation_id) if person.current_conversation_id else None
    feedback=conversation.workflow_state.get('feedback_request') if conversation else None
    if conversation and conversation.workflow_state.get('closed') and (not feedback or conversation.workflow_state.get('feedback_submitted') or entry.payload.get('text','').strip().lower()=='/new'):
        conversation=None
    if not conversation:
        conversation=AgentConversation(workspace_id=row.workspace_id,agent_id=row.agent_id,user_id=row.actor_id,person_id=person.person_id,agent_name=row.runtime['agent']['name'],
            workflow_state={'channel':'whatsapp','whatsapp_name':row.name,'whatsapp_icon':row.config.get('icon')})
        db.add(conversation);db.flush()
        record=Link(conversation_id=conversation.id,channel_id=row.id,contact_id=person.id,phone_number_id=row.phone_number_id,runtime=deepcopy(row.runtime));db.add(record)
        person.current_conversation_id=conversation.id;db.flush()
    return conversation,db.get(Link,conversation.id)


def status_payload(row):
    return {'id':str(row.id),'status':row.status,'error':row.error,'created_at':row.created_at,'conversation_id':str(row.conversation_id) if row.conversation_id else None}
