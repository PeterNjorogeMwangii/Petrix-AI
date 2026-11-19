import uuid
from datetime import date
from typing import List,Literal,Optional
from fastapi import APIRouter,Depends,Request,Query,HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.teamspace.routes import actor
from app.analytics import data,service,tracking
from app.analytics.schemas import Filters,ActivityBody

router=APIRouter(prefix='/api/workspaces/{workspace_id}/analytics',tags=['Analytics'])
Report=Literal['live','messaging','chats','csat','tickets','agents']
Dimension=Literal['hour','day','week','month','rating','status','chat_type','ai_agent','live_agent','team','channel','creator_type']
Segment=Literal['none','message_type','rating','status','chat_type','ai_agent','live_agent','team','channel','creator_type']

@router.post('/activity')
def activity(workspace_id:uuid.UUID,body:ActivityBody,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id,True);row=tracking.touch(db,workspace_id,session);db.commit()
    return {'state':row.state,'recorded_at':row.last_seen_at,'lease_until':row.lease_until}

@router.get('/options')
def options(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id)
    from app.workspace_settings.routes import policy
    policy(db,workspace_id);db.commit()
    return data.options(db,workspace_id,session.user_id)

@router.get('/{report}')
def report(workspace_id:uuid.UUID,report:Report,request:Request,start:Optional[date]=None,end:Optional[date]=None,
    filters:str=Query('{}',max_length=20000),group_by:Dimension='day',segment_by:Segment='none',space_id:Optional[uuid.UUID]=None,
    offset:int=Query(0,ge=0),limit:int=Query(40,ge=1,le=100),teams:List[uuid.UUID]=Query(default=[]),
    states:List[Literal['online','away','offline','logged_out']]=Query(default=[]),agent_id:Optional[uuid.UUID]=None,
    view:Literal['activity','agents','heatmap']='activity',conversation_type:Literal['all','chats','tickets']='all',columns:Literal['day','week','month']='day',rows_by:Literal['live_agent','team']='live_agent',
    db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id)
    try:parsed=Filters.model_validate_json(filters)
    except ValueError:raise HTTPException(422,'Choose valid Analytics filters.') from None
    if len(teams)>50 or len(states)>4:raise HTTPException(422,'Choose fewer user filters.')
    if report=='agents' and not start:
        start=data.bounds(db,workspace_id,live=True)[0];end=end or start
    if report=='agents' and view!='heatmap' and start and end and start!=end:raise HTTPException(422,'Choose one day for agent activity or the agents table; use the heatmap for a date range.')
    if report=='csat' and group_by=='day' and not request.query_params.get('group_by'):group_by='rating'
    return service.report(db,workspace_id,session.user_id,report,parsed,start,end,group_by,segment_by,space_id,offset,limit,list(map(str,teams)),states,agent_id,view,columns,rows_by,conversation_type)
