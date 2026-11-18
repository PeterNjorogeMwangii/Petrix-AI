import uuid
from fastapi import APIRouter, Depends, Request, Response, Query
from sqlalchemy.orm import Session
from app import agent_service
from app.database import get_db
from app.routes.agents import access
from app.custom_fields import service
from app.custom_fields.schemas import FieldCreate, FieldUpdate, DeleteFields

router=APIRouter(prefix='/api/workspaces/{workspace_id}/custom-fields',tags=['Custom fields'])

@router.get('')
def list_fields(workspace_id:uuid.UUID,request:Request,scope:__import__("typing").Literal["conversation","person"]="conversation",db:Session=Depends(get_db)):
    _,_,role,_=access(request,db,workspace_id)
    from app.workspace_settings import permissions as p
    if p.has_custom(db,workspace_id):p.require(db,workspace_id,'manage_custom_fields',teamspace_id=p.default_space(db,workspace_id))
    return {'fields':[service.payload(field) for field in service.definitions(db,workspace_id,scope)],
        'can_manage':__import__('app.workspace_settings.permissions',fromlist=['allowed']).allowed(db,workspace_id,'manage_custom_fields',teamspace_id=__import__('app.workspace_settings.permissions',fromlist=['default_space']).default_space(db,workspace_id))}

@router.get('/options')
def field_options(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    access(request,db,workspace_id)
    return {'fields':[service.payload(field) for field in service.definitions(db,workspace_id) if field.type=='single_select_grouped']}

@router.post('',status_code=201)
def create_field(workspace_id:uuid.UUID,body:FieldCreate,request:Request,db:Session=Depends(get_db)):
    session,_,_,_=access(request,db,workspace_id,mutation=True)
    return service.create(db,workspace_id,session.user_id,body)

@router.post('/delete',status_code=204)
def delete_fields(workspace_id:uuid.UUID,body:DeleteFields,request:Request,db:Session=Depends(get_db)):
    session,_,_,_=access(request,db,workspace_id,mutation=True)
    service.remove(db,workspace_id,session.user_id,body)
    return Response(status_code=204)

@router.post('/{field_id}')
def update_field(workspace_id:uuid.UUID,field_id:uuid.UUID,body:FieldUpdate,request:Request,db:Session=Depends(get_db)):
    session,_,_,_=access(request,db,workspace_id,mutation=True)
    return service.update(db,workspace_id,session.user_id,field_id,body)
