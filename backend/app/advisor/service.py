"""Private Advisor records and submission; no workspace action execution."""
import hashlib,json,uuid
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select,func
from sqlalchemy.orm import load_only
from app.core.security import utcnow
from app.models import (AdvisorConversation as Conversation,AdvisorMessage as Message,AdvisorAttachment as Attachment,
    Membership,Teamspace,User,WorkspaceSettings)
from app.workspace_settings import permissions as p

NAVIGATION={
 'agents':('AI agents','/app/agents',['view_ai_agents']),
 'teamspaces':('Teamspaces','/app/settings/workspace/teamspaces',['manage_teamspaces']),
 'fields':('Custom fields','/app/settings/teamspace/custom-fields',['manage_custom_fields']),
 'banning':('Banning','/app/settings/teamspace/banning',['view_bans','manage_bans']),
 'users':('Users','/app/settings/workspace/users',['manage_users']),
 'teams':('Teams','/app/settings/workspace/teams',['manage_teams']),
 'aliases':('My aliases','/app/settings/my-profile/aliases',[]),
 'account':('My account','/app/settings/my-profile/account',[]),
 'analytics':('Analytics','/app/analytics',['analytics_messaging','analytics_chats','analytics_csat','analytics_tickets','analytics_users_teams']),
 'perception':('Perception','/app/perception',['view_perception']),
 'inbox':('Inbox','/app/inbox',[f'view_{kind}_{scope}' for kind in ['livechats','tickets'] for scope in ['all','teams','own']])}


def spaces(db,workspace,user):return [v for v in db.scalars(select(Teamspace).where(Teamspace.workspace_id==workspace).order_by(Teamspace.name)) if str(v.id) in p.accessible_spaces(db,workspace,user)]
def can_space(db,workspace,user,space):
    if str(space) not in p.accessible_spaces(db,workspace,user):raise HTTPException(404,'Advisor teamspace not found.')
    row=db.scalar(select(Teamspace).where(Teamspace.workspace_id==workspace,Teamspace.id==space))
    if not row:raise HTTPException(404,'Advisor teamspace not found.')
    return row

def own(db,workspace,user,identifier,locked=False):
    query=select(Conversation).where(Conversation.id==identifier,Conversation.workspace_id==workspace,Conversation.user_id==user)
    if locked:query=query.with_for_update().execution_options(populate_existing=True)
    row=db.scalar(query)
    if not row:raise HTTPException(404,'Advisor conversation not found.')
    can_space(db,workspace,user,row.teamspace_id);return row

def navigation(db,workspace,user,space):
    return [{'id':key,'label':name,'url':url} for key,(name,url,grants) in NAVIGATION.items() if not grants or any(p.allowed(db,workspace,key,user,teamspace_id=space) for key in grants)]

def options(db,workspace,user,ai):
    available=spaces(db,workspace,user)
    if not available:raise HTTPException(403,'Your membership has no accessible teamspace for Advisor.')
    row=db.get(User,user);policy=db.get(WorkspaceSettings,workspace)
    from app.advisor.knowledge import manual
    return {'teamspaces':[{'id':str(v.id),'name':v.name,'is_default':v.is_default} for v in available],
        'name':(row.full_name or '').split(' ')[0] or 'there','timezone':policy.timezone if policy else 'Africa/Nairobi',
        'model_ready':ai.available,'vision_ready':ai.vision,'version':manual()['version'],
        'suggestions':['How do I test an AI agent?','What is a teamspace?','How do I add custom fields?','Can I ban customers?','How do I add team members?'],
        'limits':{'files_per_message':5,'file_bytes':10*1024*1024,'message_file_bytes':20*1024*1024,'question_characters':8000},
        'links':{'apis':'/app/developer-api','documentation':'/app/documentation','privacy':'/privacy','terms':'/terms'}}

def conversation_payload(row):return {k:getattr(row,k) for k in ['id','teamspace_id','title','revision','created_at','updated_at']}

def create(db,workspace,user,body):
    can_space(db,workspace,user,body.teamspace_id)
    # Serialize a user's idempotent conversation create and staged-upload quotas without touching idle clocks.
    db.scalar(select(Membership).where(Membership.workspace_id==workspace,Membership.user_id==user).with_for_update())
    row=db.scalar(select(Conversation).where(Conversation.workspace_id==workspace,Conversation.user_id==user,Conversation.client_id==body.client_id))
    if row:
        can_space(db,workspace,user,row.teamspace_id)
        if row.teamspace_id!=body.teamspace_id:raise HTTPException(409,'This new-chat ID was used in another teamspace.')
    else:
        row=Conversation(client_id=body.client_id,workspace_id=workspace,user_id=user,teamspace_id=body.teamspace_id);db.add(row);db.flush()
    db.commit();return conversation_payload(row)

def history(db,workspace,user,space,offset,limit):
    permitted=p.accessible_spaces(db,workspace,user)
    if space:can_space(db,workspace,user,space);permitted={str(space)}
    query=select(Conversation).where(Conversation.workspace_id==workspace,Conversation.user_id==user,Conversation.teamspace_id.in_([uuid.UUID(v) for v in permitted]))
    return {'items':[conversation_payload(v) for v in db.scalars(query.order_by(Conversation.updated_at.desc(),Conversation.id).offset(offset).limit(limit))],
        'total':db.scalar(select(func.count()).select_from(query.subquery())),'offset':offset,'limit':limit}

def attachment_payload(row,workspace):
    base='/api/workspaces/'+str(workspace)+'/advisor/attachments/'+str(row.id)
    return {'id':row.id,'name':row.filename,'mime_type':row.mime_type,'size':row.size,'is_image':row.mime_type.startswith('image/'),'note':row.note,'url':base,'preview_url':base+'/preview' if row.mime_type.startswith('image/') else None}

def metadata_query():return select(Attachment).options(load_only(Attachment.id,Attachment.workspace_id,Attachment.user_id,Attachment.message_id,Attachment.filename,Attachment.mime_type,Attachment.size,Attachment.note,Attachment.created_at))

def messages_payload(db,rows,workspace):
    grouped={v.id:[] for v in rows}
    for row in db.scalars(metadata_query().where(Attachment.message_id.in_(list(grouped)))):grouped[row.message_id].append(attachment_payload(row,workspace))
    return [{**{k:getattr(v,k) for k in ['id','role','content','status','mode','sources','navigation','notice','client_request_id','created_at','attempts']},'attachments':sorted(grouped[v.id],key=lambda a:str(a['id']))} for v in rows]

def detail(db,row,before,limit):
    query=select(Message).where(Message.conversation_id==row.id)
    if before:
        pivot=db.scalar(select(Message).where(Message.conversation_id==row.id,Message.id==before))
        if not pivot:raise HTTPException(404,'Advisor message cursor not found.')
        from sqlalchemy import or_,and_
        query=query.where(or_(Message.created_at<pivot.created_at,and_(Message.created_at==pivot.created_at,Message.id<pivot.id)))
    rows=list(db.scalars(query.order_by(Message.created_at.desc(),Message.id.desc()).limit(limit+1)));more=len(rows)>limit;rows=rows[:limit];rows.reverse()
    return {**conversation_payload(row),'messages':messages_payload(db,rows,row.workspace_id),'has_more':more,'busy':db.scalar(select(Message.id).where(Message.conversation_id==row.id,Message.status.in_(['pending','running'])).limit(1)) is not None}

def submit(db,row,user,body):
    row=own(db,row.workspace_id,user,row.id,True)
    digest=hashlib.sha256(json.dumps({'content':body.content,'attachments':sorted(map(str,body.attachment_ids))},sort_keys=True).encode()).hexdigest()
    prior=db.scalar(select(Message).where(Message.conversation_id==row.id,Message.client_request_id==body.client_request_id,Message.role=='user'))
    if prior:
        if prior.payload_hash!=digest:raise HTTPException(409,'This message ID was used with different content. Start a new send attempt.')
        return detail(db,row,None,40)
    if row.revision!=body.expected_revision:raise HTTPException(409,'This Advisor conversation changed. Reload before sending.')
    if db.scalar(select(Message.id).where(Message.conversation_id==row.id,Message.status.in_(['pending','running'])).limit(1)):raise HTTPException(409,'Wait for the current Advisor reply before sending another question.')
    # Lock the membership row as the rate-limit boundary across this user's conversations.
    db.scalar(select(Membership).where(Membership.workspace_id==row.workspace_id,Membership.user_id==user).with_for_update())
    count=db.scalar(select(func.count()).select_from(Message).join(Conversation,Conversation.id==Message.conversation_id).where(Conversation.workspace_id==row.workspace_id,Conversation.user_id==user,Message.role=='user',Message.created_at>=utcnow()-timedelta(minutes=1))) or 0
    if count>=20:raise HTTPException(429,'Advisor received too many questions. Try again in a minute.')
    files=list(db.scalars(metadata_query().where(Attachment.id.in_(body.attachment_ids),Attachment.workspace_id==row.workspace_id,Attachment.user_id==user).with_for_update()))
    if len(files)!=len(body.attachment_ids) or any(f.message_id is not None for f in files):raise HTTPException(422,'Choose unused attachments uploaded by your account in this workspace.')
    if sum(f.size for f in files)>20*1024*1024:raise HTTPException(422,'Attach at most 20 MiB in one message.')
    at=utcnow();question=Message(conversation_id=row.id,client_request_id=body.client_request_id,role='user',content=body.content,payload_hash=digest,created_at=at);db.add(question);db.flush()
    for file in files:file.message_id=question.id
    db.add(Message(conversation_id=row.id,client_request_id=body.client_request_id,role='assistant',status='pending',created_at=at+timedelta(microseconds=1)))
    if row.title=='New chat':row.title=(' '.join(body.content.split()) or ('Attachment: '+files[0].filename))[:150]
    row.revision+=1;row.updated_at=at;db.commit();return detail(db,row,None,40)

def retry(db,row,user,message_id):
    row=own(db,row.workspace_id,user,row.id,True);message=db.scalar(select(Message).where(Message.conversation_id==row.id,Message.id==message_id,Message.role=='assistant'))
    if not message:raise HTTPException(404,'Advisor reply not found.')
    if message.status!='failed':raise HTTPException(409,'Only an interrupted or failed reply can be retried.')
    if db.scalar(select(Message.id).where(Message.conversation_id==row.id,Message.status.in_(['pending','running'])).limit(1)):raise HTTPException(409,'Wait for the current Advisor reply.')
    latest=db.scalar(select(Message.id).where(Message.conversation_id==row.id,Message.role=='assistant').order_by(Message.created_at.desc(),Message.id.desc()).limit(1))
    if latest!=message.id:raise HTTPException(409,'Continue this discussion with a new question instead of retrying an earlier turn.')
    if message.attempts>=5:raise HTTPException(422,'This reply reached its retry limit. Send a new question.')
    message.status='pending';message.notice='';message.worker_token=None;message.lease_until=None;row.revision+=1;db.commit();return detail(db,row,None,40)
