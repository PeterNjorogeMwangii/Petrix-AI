import uuid
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session
from app import agent_service
from app.database import get_db
from app.routes.agents import access
from app.teams import service
from app.teams.schemas import TeamCreate, TeamUpdate, TeamDelete

router = APIRouter(prefix='/api/workspaces/{workspace_id}/teams', tags=['Teams'])


@router.get('')
def list_teams(workspace_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    _, _, role, _ = access(request, db, workspace_id)
    return {'teams': [service.payload(team) for team in service.definitions(db, workspace_id)],
        'can_manage': agent_service.can_manage(db, workspace_id, role)}


@router.post('', status_code=201)
def create_team(workspace_id: uuid.UUID, body: TeamCreate, request: Request, db: Session = Depends(get_db)):
    session, _, _, _ = access(request, db, workspace_id, mutation=True)
    return service.create(db, workspace_id, session.user_id, body)


@router.post('/{team_id}')
def update_team(workspace_id: uuid.UUID, team_id: uuid.UUID, body: TeamUpdate, request: Request, db: Session = Depends(get_db)):
    session, _, _, _ = access(request, db, workspace_id, mutation=True)
    return service.update(db, workspace_id, session.user_id, team_id, body)


@router.post('/{team_id}/delete', status_code=204)
def delete_team(workspace_id: uuid.UUID, team_id: uuid.UUID, body: TeamDelete, request: Request, db: Session = Depends(get_db)):
    session, _, _, _ = access(request, db, workspace_id, mutation=True)
    service.remove(db, workspace_id, session.user_id, team_id, body)
    return Response(status_code=204)
