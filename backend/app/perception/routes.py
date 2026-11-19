import uuid
from datetime import date
from typing import Optional,Literal
from fastapi import APIRouter,Depends,Request,Query,HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.teamspace.routes import actor
from app.models import PerceptionSource as Source,PerceptionEntry as Entry
from app.perception import schemas as b,service as s,reports

router=APIRouter(prefix='/api/workspaces/{workspace_id}/perception',tags=['Perception'])

def access(request,db,workspace,space,manage=False):
    session=actor(request,db,workspace,manage);s.scope(db,workspace,space,session.user_id,manage);return session.user_id

@router.get('/options')
def options(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id)
    return s.options(db,workspace_id,session.user_id,request.app.state.perception_collector,request.app.state.support_ai)

@router.get('/{space_id}/settings')
def settings(workspace_id:uuid.UUID,space_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    access(request,db,workspace_id,space_id);return s.state(db,workspace_id,space_id)

@router.post('/{space_id}/settings')
def save_settings(workspace_id:uuid.UUID,space_id:uuid.UUID,body:b.ConfigBody,request:Request,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id,space_id,True);return s.save_config(db,workspace_id,space_id,user,body)

@router.get('/{space_id}/keywords')
def keywords(workspace_id:uuid.UUID,space_id:uuid.UUID,request:Request,kind:Literal['custom','team']='custom',q:str=Query('',max_length=256),status:Literal['all','enabled','disabled']='all',offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100),db:Session=Depends(get_db)):
    access(request,db,workspace_id,space_id);return s.keyword_list(db,workspace_id,space_id,kind,q,status,offset,limit)

@router.post('/{space_id}/keywords',status_code=201)
def create_keyword(workspace_id:uuid.UUID,space_id:uuid.UUID,body:b.KeywordBody,request:Request,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id,space_id,True);return s.save_keyword(db,workspace_id,space_id,user,body)

@router.post('/{space_id}/keywords/bulk')
def bulk(workspace_id:uuid.UUID,space_id:uuid.UUID,body:b.Selection,request:Request,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id,space_id,True);return s.bulk(db,workspace_id,space_id,user,body)

@router.post('/{space_id}/keywords/{identifier}')
def update_keyword(workspace_id:uuid.UUID,space_id:uuid.UUID,identifier:uuid.UUID,body:b.KeywordBody,request:Request,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id,space_id,True);return s.save_keyword(db,workspace_id,space_id,user,body,identifier)

@router.post('/{space_id}/teams/{team_id}')
def team_keyword(workspace_id:uuid.UUID,space_id:uuid.UUID,team_id:uuid.UUID,body:b.TeamBody,request:Request,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id,space_id,True);return s.save_team(db,workspace_id,space_id,user,team_id,body)

@router.get('/{space_id}/sources')
def sources(workspace_id:uuid.UUID,space_id:uuid.UUID,request:Request,platform:Literal['facebook','instagram','x','tiktok','youtube','app_store','play_store','news']='facebook',offset:int=Query(0,ge=0),limit:int=Query(10,ge=1,le=100),db:Session=Depends(get_db)):
    access(request,db,workspace_id,space_id);return s.sources(db,workspace_id,space_id,platform,offset,limit)

@router.post('/{space_id}/sources',status_code=201)
def create_source(workspace_id:uuid.UUID,space_id:uuid.UUID,body:b.SourceBody,request:Request,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id,space_id,True);return s.save_source(db,workspace_id,space_id,user,body)

@router.get('/{space_id}/sources/{identifier}')
def source_detail(workspace_id:uuid.UUID,space_id:uuid.UUID,identifier:uuid.UUID,request:Request,offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100),db:Session=Depends(get_db)):
    access(request,db,workspace_id,space_id);return s.source_detail(db,s.source_for(db,workspace_id,space_id,identifier),offset,limit)

@router.post('/{space_id}/sources/{identifier}')
def update_source(workspace_id:uuid.UUID,space_id:uuid.UUID,identifier:uuid.UUID,body:b.SourceBody,request:Request,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id,space_id,True);return s.save_source(db,workspace_id,space_id,user,body,identifier)

@router.post('/{space_id}/sources/{identifier}/recurring')
def recurring(workspace_id:uuid.UUID,space_id:uuid.UUID,identifier:uuid.UUID,body:b.ToggleSource,request:Request,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id,space_id,True);return s.toggle_source(db,workspace_id,space_id,user,identifier,body)

@router.post('/{space_id}/sources/{identifier}/delete')
def delete_source(workspace_id:uuid.UUID,space_id:uuid.UUID,identifier:uuid.UUID,body:b.Confirmation,request:Request,db:Session=Depends(get_db)):
    access(request,db,workspace_id,space_id,True);s.lock(db,workspace_id);row=s.source_for(db,workspace_id,space_id,identifier);s.check(row,body.expected_revision)
    if not body.confirmed:raise HTTPException(409,'Confirm source deletion.')
    db.delete(row);db.commit();return {'deleted':True}

@router.post('/{space_id}/sources/{identifier}/scrape',status_code=202)
def manual(workspace_id:uuid.UUID,space_id:uuid.UUID,identifier:uuid.UUID,body:b.Range,request:Request,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id,space_id,True);return s.manual(db,s.source_for(db,workspace_id,space_id,identifier),user,body)

@router.post('/{space_id}/sources/{identifier}/retry/{run_id}',status_code=202)
def retry(workspace_id:uuid.UUID,space_id:uuid.UUID,identifier:uuid.UUID,run_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id,space_id,True);return s.retry(db,s.source_for(db,workspace_id,space_id,identifier),user,run_id)

@router.post('/{space_id}/processing/retry')
def retry_processing(workspace_id:uuid.UUID,space_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    access(request,db,workspace_id,space_id,True);s.lock(db,workspace_id)
    if not request.app.state.support_ai.available:raise HTTPException(422,'Connect the configured AI service before retrying processing.')
    rows=list(db.scalars(select(Entry).where(Entry.workspace_id==workspace_id,Entry.teamspace_id==space_id,Entry.processing=='failed')))
    for row in rows:row.processing='pending';row.processing_error=''
    db.commit();return {'queued':len(rows)}

@router.get('/{space_id}/overview')
def overview(workspace_id:uuid.UUID,space_id:uuid.UUID,request:Request,start:Optional[date]=None,end:Optional[date]=None,charts:str=Query('{}',max_length=20000),db:Session=Depends(get_db)):
    access(request,db,workspace_id,space_id)
    try:controls=reports.Charts.model_validate_json(charts)
    except ValueError:raise HTTPException(422,'Choose valid Perception chart filters.') from None
    return reports.overview(db,workspace_id,space_id,start,end,controls)

@router.get('/{space_id}/entries')
def entries(workspace_id:uuid.UUID,space_id:uuid.UUID,request:Request,start:Optional[date]=None,end:Optional[date]=None,filters:str=Query('{}',max_length=12000),offset:int=Query(0,ge=0),limit:int=Query(40,ge=1,le=100),db:Session=Depends(get_db)):
    access(request,db,workspace_id,space_id)
    try:parsed=b.Filters.model_validate_json(filters)
    except ValueError:raise HTTPException(422,'Choose valid entry filters.') from None
    return reports.entries(db,workspace_id,space_id,start,end,parsed,offset,limit)
