import uuid
from fastapi import HTTPException
from sqlalchemy import select,update
from sqlalchemy.dialects.postgresql import insert
from app.models import User,UserProfile,UserAlias,ProfileAsset,ConversationPersona,SupportAgent
from app.profile.schemas import Notifications


def profile(db,user_id,lock=False):
 user=db.get(User,user_id)
 if not user:raise HTTPException(401,'Please sign in to continue.')
 parts=(user.full_name or '').strip().split(None,1)
 db.execute(insert(UserProfile).values(user_id=user_id,first_name=parts[0] if parts else '',last_name=parts[1] if len(parts)>1 else '',language='en',presence='online',theme='system',notifications=Notifications().model_dump(),multichat=False,revision=1).on_conflict_do_nothing())
 query=select(UserProfile).where(UserProfile.user_id==user_id)
 return db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)

def revision(row,expected):
 if row.revision!=expected:raise HTTPException(409,'These settings changed in another session. Reload before saving.')

def asset_url(identifier):return '/api/profile-assets/'+str(identifier) if identifier else None

def payload(db,user_id):
 value=profile(db,user_id);user=db.get(User,user_id)
 return {'user_id':str(user_id),'first_name':value.first_name,'last_name':value.last_name,'full_name':user.full_name,'email':user.email,'avatar_url':asset_url(value.avatar_asset_id),'language':value.language,'presence':value.presence,'theme':value.theme,'notifications':{**Notifications().model_dump(),**value.notifications},'multichat':value.multichat,'revision':value.revision}

def user_summary(db,user):
 value=profile(db,user.id)
 return {'id':str(user.id),'email':user.email,'full_name':user.full_name,'avatar_url':asset_url(value.avatar_asset_id),'presence':value.presence,'theme':value.theme,'language':value.language,'multichat':value.multichat}

def alias_payload(row):return {'id':str(row.id),'name':row.name,'avatar_url':asset_url(row.avatar_asset_id),'is_default':row.is_default,'signature_name':row.signature_name,'signature_content':row.signature_content,'revision':row.revision,'created_at':row.created_at}

def aliases(db,user_id):return list(db.scalars(select(UserAlias).where(UserAlias.user_id==user_id,UserAlias.archived.is_(False)).order_by(UserAlias.created_at,UserAlias.id)))

def alias_for(db,user_id,identifier,lock=False):
 query=select(UserAlias).where(UserAlias.id==identifier,UserAlias.user_id==user_id,UserAlias.archived.is_(False))
 row=db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
 if not row:raise HTTPException(404,'Alias not found.')
 return row

def add_asset(db,user_id,data):
 from app.workspace_settings.routes import image_bytes
 row=ProfileAsset(user_id=user_id,data=image_bytes(data,True));db.add(row);db.flush();return row.id

def selection(db,workspace_id,kind,record_id,user_id,lock=False):
 query=select(ConversationPersona).where(ConversationPersona.workspace_id==workspace_id,ConversationPersona.kind==kind,ConversationPersona.record_id==record_id,ConversationPersona.user_id==user_id)
 return db.scalar((query.with_for_update() if lock else query).execution_options(populate_existing=True))

def resolve(db,workspace_id,kind,record_id,user_id):
 current=selection(db,workspace_id,kind,record_id,user_id)
 if current:
  if current.alias_id is None:return None
  chosen=db.get(UserAlias,current.alias_id)
  if chosen and not chosen.archived and chosen.user_id==user_id:return chosen
 return db.scalar(select(UserAlias).where(UserAlias.user_id==user_id,UserAlias.is_default.is_(True),UserAlias.archived.is_(False)))

def snapshot(db,workspace_id,kind,record_id,user_id,alias_id=None,explicit=False):
 chosen=alias_for(db,user_id,alias_id) if explicit and alias_id else None if explicit else resolve(db,workspace_id,kind,record_id,user_id)
 account=profile(db,user_id)
 return {'alias_id':str(chosen.id) if chosen else None,'name':chosen.name if chosen else db.get(User,user_id).full_name or 'Support agent','avatar_url':asset_url(chosen.avatar_asset_id if chosen else account.avatar_asset_id),'signature_name':chosen.signature_name if chosen else db.get(User,user_id).full_name or 'Support agent','signature_content':chosen.signature_content if chosen else ''}

def remember(db,workspace_id,kind,record_id,user_id,alias_id):
 db.execute(insert(ConversationPersona).values(workspace_id=workspace_id,kind=kind,record_id=record_id,user_id=user_id,alias_id=alias_id,revision=1).on_conflict_do_update(index_elements=['workspace_id','kind','record_id','user_id'],set_={'alias_id':alias_id,'revision':ConversationPersona.revision+1}))
