import hashlib,hmac,json,re,uuid
from urllib.parse import urlencode
from fastapi import APIRouter,Depends,Request,HTTPException
from starlette.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from app.database import get_db
from app.core.security import token_hash,utcnow
from app.models import WhatsAppChannel as Channel,WhatsAppInbound as Inbound
from app.whatsapp import service as s,credentials as crypto

router=APIRouter(prefix='/api/whatsapp/webhooks',tags=['WhatsApp Webhooks'])
MAX_BODY=2*1024*1024


@router.get('/{app_id}')
def challenge(app_id:str,request:Request,db:Session=Depends(get_db)):
    params=request.query_params
    try:
        token=params.get('hub.verify_token','');challenge=params.get('hub.challenge','')
        if params.get('hub.mode')!='subscribe' or not 1<=len(token)<=200 or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',challenge):raise HTTPException(403,'Webhook verification failed.')
        row=db.scalar(select(Channel).where(Channel.app_id==app_id,Channel.verify_token_hash==token_hash(token)).with_for_update())
        if not row or not hmac.compare_digest(row.verify_token_hash,token_hash(token)):raise HTTPException(403,'Webhook verification failed.')
        row.webhook_verified_at=utcnow();row.revision+=1;row.updated_at=utcnow();db.commit()
        return PlainTextResponse(challenge)
    finally:
        # Avoid placing the setup token in access-log query strings.
        request.scope['query_string']=urlencode([(k,'REDACTED' if k=='hub.verify_token' else v) for k,v in params.multi_items()]).encode()


@router.post('/{app_id}')
async def receive(app_id:str,request:Request,db:Session=Depends(get_db)):
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>MAX_BODY:raise HTTPException(413,'WhatsApp webhook payload is too large.')
    signature=request.headers.get('x-hub-signature-256','')
    if not re.fullmatch(r'sha256=[0-9a-fA-F]{64}',signature):raise HTTPException(403,'Webhook signature verification failed.')
    rows=list(db.scalars(select(Channel).where(Channel.app_id==app_id,Channel.credentials!='').limit(500)))
    valid={}
    for row in rows:
        try:secret=crypto.secrets(row,request.app.state.settings).get('app_secret','')
        except HTTPException:continue
        if secret not in valid:valid[secret]=hmac.new(secret.encode(),bytes(raw),hashlib.sha256).hexdigest()
        digest=valid[secret]
        if hmac.compare_digest(digest,signature[7:].lower()):valid[secret]=digest
    allowed={secret for secret,value in valid.items() if hmac.compare_digest(value,signature[7:].lower())}
    if not allowed:raise HTTPException(403,'Webhook signature verification failed.')
    try:data=json.loads(raw)
    except (ValueError,UnicodeError):raise HTTPException(400,'Invalid WhatsApp webhook JSON.') from None
    if not isinstance(data,dict) or data.get('object')!='whatsapp_business_account':return {'received':0}
    grouped={};count=0
    for entry in s.array(data.get('entry'))[:1000]:
        if not isinstance(entry,dict):continue
        for change in s.array(entry.get('changes'))[:1000]:
            if not isinstance(change,dict) or change.get('field')!='messages':continue
            value=s.mapping(change.get('value'));metadata=s.mapping(value.get('metadata'));phone=s.clean(metadata.get('phone_number_id'),32)
            grouped.setdefault(phone,[]).append(value)
            count+=len(s.array(value.get('messages')))+len(s.array(value.get('statuses')))
            if count>1000:raise HTTPException(413,'Use no more than 1,000 WhatsApp events per webhook.')
    received=0
    for row in sorted(rows,key=lambda r:str(r.id)):
        if row.phone_number_id not in grouped:continue
        row=db.scalar(select(Channel).where(Channel.id==row.id).with_for_update().execution_options(populate_existing=True))
        if not row or row.app_id!=app_id or crypto.secrets(row,request.app.state.settings).get('app_secret') not in allowed:continue
        for value in grouped[row.phone_number_id]:
            for message in s.array(value.get('messages')):
                if s.inactive_reason(db,row):continue
                normalized=s.normalize(message,value.get('contacts',[]))
                if not normalized:continue
                key=hashlib.sha256(('message:'+normalized['provider_id']).encode()).hexdigest()
                if db.scalar(select(Inbound.id).where(Inbound.channel_id==row.id,Inbound.event_key==key)):continue
                contact=s.contact(db,row,normalized)
                statement=insert(Inbound).values(id=uuid.uuid4(),channel_id=row.id,contact_id=contact.id,event_key=key,
                    provider_id=normalized['provider_id'],kind='message',payload=normalized,connection_revision=row.connection_revision,status='pending',error='',created_at=utcnow())
                if db.execute(statement.on_conflict_do_nothing(index_elements=['channel_id','event_key']).returning(Inbound.id)).scalar():received+=1
            for status in s.array(value.get('statuses')):
                if not isinstance(status,dict) or not isinstance(status.get('id'),str) or len(status['id'])>512 or not isinstance(status.get('status'),str) or status.get('status') not in {'sent','delivered','read','failed'}:continue
                clean={k:s.clean(status.get(k),512) for k in ['id','status','timestamp','recipient_id','recipient_user_id','biz_opaque_callback_data']}
                errors=s.array(status.get('errors'));code=s.mapping(errors[0]).get('code',0) if errors else 0
                clean['code']=code if type(code) is int else 0
                key=hashlib.sha256(('status:'+json.dumps(clean,sort_keys=True)).encode()).hexdigest()
                statement=insert(Inbound).values(id=uuid.uuid4(),channel_id=row.id,event_key=key,provider_id=status['id'],kind='status',payload=clean,connection_revision=row.connection_revision,status='pending',error='',created_at=utcnow())
                if db.execute(statement.on_conflict_do_nothing(index_elements=['channel_id','event_key']).returning(Inbound.id)).scalar():received+=1
    db.commit();return {'received':received}
