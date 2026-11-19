"""Weighted counts from durable messages, sent mail and action execution events.
No conversation text or customer identifiers are returned by these queries.
"""
import csv
import io
import json
import math
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, literal, case, func, union_all, cast, String, null
from sqlalchemy.dialects.postgresql import insert
from app.core.security import utcnow
from app.models import UsageEvent, AgentMessage, AgentConversation, Agent, SupportTicket, TicketEmail
CATEGORIES={'ai_messages':('Messages','AI messages'),'people_messages':('Messages','People messages'),'tts':('Voice','TTS'),'asr':('Voice','ASR'),'ai_emails':('Emails','AI emails'),'manual_emails':('Emails','Manual emails'),'auto_fill_field':('AI actions','Auto-fill'),'auto_team_match':('AI actions','Auto Team Match'),'llm_processing':('AI actions','LLM Processing'),'sql_query':('AI actions','SQL Query'),'summaries':('Inbox','Summaries'),'translations':('Inbox','Translations'),'copilot':('Inbox','Co-pilot'),'mediation':('Inbox','Mediation')}
DIMENSIONS={'interaction_type':'category','source':'source','teamspace':'teamspace_id','ai_agent':'agent_id','live_agent':'user_id','team':'team_id'}


def record_actions(db,workspace_id,agent_id,user_id,result,event_id=None):
 agent=db.get(Agent,agent_id);event_id=event_id or uuid.uuid4()
 from app.models import WorkflowDocument
 from app.workflows.numbering import walk
 document=db.get(WorkflowDocument,agent_id);local_nodes={n["id"] for n in walk(document.nodes)} if document else set()
 for index,entry in enumerate(result.get('trace',[])):
  kind=entry.get('type')
  if kind not in {'auto_fill_field','auto_team_match','llm_processing','sql_query'} or entry.get('node_id') not in local_nodes:continue
  # This wrapper is entered once per committed workflow event. Node index retains
  # repeated executions in a loop, and failed executions still consumed work.
  db.execute(insert(UsageEvent).values(id=uuid.uuid4(),workspace_id=workspace_id,event_key='action:'+str(workspace_id)+':'+str(event_id)+':'+str(entry.get('node_id'))+':'+str(index),category=kind,source='workflow',agent_id=agent_id,user_id=None,teamspace_id=agent.teamspace_id if agent else None,units=1).on_conflict_do_nothing())
 db.commit()


def weights(settings):
 try:values=json.loads(settings.usage_weights_json)
 except ValueError:values={}
 return {key:float(values.get(key,1)) for key in CATEGORIES}


def message_source_query(workspace_id,ids=None,exclude_ids=None):
 data=AgentMessage.message_data
 category=case((AgentMessage.role=='assistant',case((data['source'].as_string()=='live_agent','people_messages'),else_='ai_messages')),else_='people_messages')
 # A mirror has author_name without source=live_agent; transferred history is
 # explicitly marked. Source request IDs deduplicate receiving-side copies.
 unique=func.coalesce(data['source_request_id'].as_string(),cast(AgentMessage.id,String))
 messages=select(unique.label('identity'),AgentMessage.created_at.label('at'),category.label('category'),case((data['source'].as_string()=='live_agent','inbox'),else_='chat').label('source'),AgentConversation.agent_id.label('agent_id'),case((data['source'].as_string()=='live_agent',func.coalesce(AgentMessage.author_id,AgentConversation.live_assigned_user_id)),else_=null()).label('user_id'),Agent.teamspace_id.label('teamspace_id'),case((func.json_array_length(func.coalesce(AgentConversation.live_context['routing']['team_ids'],literal('[]').cast(__import__('sqlalchemy').JSON)))==1,AgentConversation.live_context['routing']['team_ids'][0].as_string()),else_=null()).label('team_id'),literal(1.0).label('units')).join(AgentConversation,AgentConversation.id==AgentMessage.conversation_id).outerjoin(Agent,Agent.id==AgentConversation.agent_id).where(AgentConversation.workspace_id==workspace_id,AgentMessage.role.in_(['user','assistant']),func.coalesce(data['transferred_history'].as_boolean(),False).is_(False),~((AgentMessage.role=='assistant') & data['author_name'].as_string().is_not(None) & (func.coalesce(data['source'].as_string(),'')!='live_agent'))).distinct(unique).order_by(unique,AgentMessage.created_at)
 if ids is not None:messages=messages.where(AgentConversation.id.in_(ids))
 if exclude_ids is not None:messages=messages.where(AgentConversation.id.not_in(exclude_ids))
 return messages


def source_query(workspace_id):
 m=message_source_query(workspace_id).subquery()
 msg=select(m.c.at,m.c.category,m.c.source,cast(m.c.agent_id,String).label('agent_id'),cast(m.c.user_id,String).label('user_id'),cast(m.c.teamspace_id,String).label('teamspace_id'),m.c.team_id,m.c.units)
 emails=select(TicketEmail.sent_at.label('at'),case((TicketEmail.kind=='reply','manual_emails'),else_='ai_emails').label('category'),literal('email').label('source'),cast(AgentConversation.agent_id,String).label('agent_id'),cast(case((TicketEmail.kind=='reply',TicketEmail.actor_id),else_=SupportTicket.creator_id),String).label('user_id'),cast(func.coalesce(SupportTicket.teamspace_id,Agent.teamspace_id),String).label('teamspace_id'),case((SupportTicket.assignee_type=='team',cast(SupportTicket.assignee_id,String)),else_=null()).label('team_id'),literal(1.0).label('units')).join(SupportTicket,SupportTicket.id==TicketEmail.ticket_id).outerjoin(AgentConversation,AgentConversation.id==SupportTicket.conversation_id).outerjoin(Agent,Agent.id==AgentConversation.agent_id).where(SupportTicket.workspace_id==workspace_id,TicketEmail.status=='sent',TicketEmail.sent_at.is_not(None))
 events=select(UsageEvent.created_at.label('at'),UsageEvent.category,UsageEvent.source,cast(UsageEvent.agent_id,String).label('agent_id'),cast(UsageEvent.user_id,String).label('user_id'),cast(UsageEvent.teamspace_id,String).label('teamspace_id'),cast(UsageEvent.team_id,String).label('team_id'),UsageEvent.units).where(UsageEvent.workspace_id==workspace_id)
 return union_all(msg,emails,events).subquery()


def aggregate(db,workspace_id,settings,start,end,group='day',segment='interaction_type',filters=None):
 from fastapi import HTTPException
 if start.tzinfo is None or end.tzinfo is None or end<=start or end-start>timedelta(days=366*5):raise HTTPException(422,'Choose a valid date range of up to five years, including time zones.')
 if group not in {'hour','day','week','month','quarter','year'} or segment not in DIMENSIONS:raise HTTPException(422,'Choose a supported grouping and segment.')
 raw=source_query(workspace_id);conditions=[raw.c.at>=start,raw.c.at<end]
 for key,value in (filters or {}).items():
  if value:conditions.append(raw.c[DIMENSIONS[key]]==value)
 weight=case(*[(raw.c.category==key,value) for key,value in weights(settings).items()],else_=0)*raw.c.units
 local=func.timezone('Africa/Nairobi',raw.c.at)
 col=func.coalesce(raw.c[DIMENSIONS[segment]],'Unassigned')
 query=select(func.date_trunc(group,local).label('period'),col.label('segment'),func.sum(weight).label('value')).where(*conditions).group_by('period','segment').order_by('period','segment').limit(2001)
 rows=[{'period':row.period.isoformat(),'segment':row.segment,'value':round(float(row.value),4)} for row in db.execute(query)]
 if len(rows)>2000:raise HTTPException(422,'This chart exceeds 2,000 points. Choose a larger grouping or a shorter date range.')
 breakdown={row.category:float(row.value) for row in db.execute(select(raw.c.category,func.sum(weight).label('value')).where(*conditions).group_by(raw.c.category))}
 total=sum(breakdown.values())
 def peak(unit):
  row=db.execute(select(func.date_trunc(unit,local).label('period'),func.sum(weight).label('value')).where(*conditions).group_by('period').order_by(func.sum(weight).desc(),'period').limit(1)).first()
  return {'period':row.period.isoformat(),'value':round(float(row.value),4)} if row else None
 return {'total':round(total,4),'average_per_day':round(total/max(1,(end-start).total_seconds()/86400),4),'peak_day':peak('day'),'peak_hour':peak('hour'),'timezone':'Africa/Nairobi','rows':rows,'breakdown':[{'key':key,'group':group_label,'label':label,'value':round(breakdown.get(key,0),4),'percentage':round(100*breakdown.get(key,0)/total,2) if total else 0} for key,(group_label,label) in CATEGORIES.items()],'start':start.isoformat(),'end':end.isoformat(),'weights':weights(settings)}
