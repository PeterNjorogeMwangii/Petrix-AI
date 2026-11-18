import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent_service import can_manage, workspace_for
from app.auth_service import current_session, require_csrf
from app.core.security import utcnow
from app.database import get_db
from app.database_connections import service
from app.database_connections.schemas import ConnectionCreate, ConnectionRevision, ConnectionUpdate
from app.models import DatabaseConnection

router = APIRouter(prefix="/api/workspaces/{workspace_id}/database-connections", tags=["Database connections"])


def access(request, db, workspace_id, mutation=False):
    session = current_session(request, db)
    if mutation: require_csrf(request, session)
    workspace, role = workspace_for(db, workspace_id, session.user_id, lock=mutation)
    from app.workspace_settings.permissions import require, membership
    member=membership(db,workspace_id,session.user_id)
    if member and not member.custom_role_id and role not in {"owner","admin"}:raise HTTPException(403,"Only workspace administrators or explicitly authorized custom roles can manage database connections.")
    require(db,workspace_id,"manage_db_connections")
    return workspace


@router.get("")
def list_connections(workspace_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    access(request, db, workspace_id)
    connections = db.scalars(select(DatabaseConnection).where(DatabaseConnection.workspace_id == workspace_id)
        .order_by(DatabaseConnection.created_at, DatabaseConnection.id)).all()
    return {"connections": [service.payload(item) for item in connections], "supported_types": [{"type": "postgresql", "name": "PostgreSQL"}]}


@router.get("/options")
def connection_options(workspace_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    session = current_session(request, db)
    _, role = workspace_for(db, workspace_id, session.user_id)
    if not __import__('app.workspace_settings.permissions',fromlist=['allowed']).allowed(db,workspace_id,'manage_db_connections'): raise HTTPException(403, "You need the Manage AI Agents permission.")
    connections = db.scalars(select(DatabaseConnection).where(DatabaseConnection.workspace_id == workspace_id,
        DatabaseConnection.status == "verified").order_by(DatabaseConnection.name)).all()
    return {"connections": [{"id": item.id, "name": item.name, "type": item.type} for item in connections]}


@router.post("", status_code=201)
def create_connection(workspace_id: uuid.UUID, body: ConnectionCreate, request: Request, db: Session = Depends(get_db)):
    access(request, db, workspace_id, mutation=True)
    count = db.scalar(select(func.count()).select_from(DatabaseConnection).where(DatabaseConnection.workspace_id == workspace_id))
    if count >= 20: raise HTTPException(422, "A workspace supports up to 20 database connections.")
    service.check_name(db, workspace_id, body.name)
    connection = DatabaseConnection(workspace_id=workspace_id, revision=1, status="unverified")
    service.update_fields(connection, body, request.app.state.settings)
    service.verify(connection, request.app.state.database_client, request.app.state.settings)
    db.add(connection); db.commit()
    return service.payload(connection)


@router.post("/{connection_id}")
def update_connection(workspace_id: uuid.UUID, connection_id: uuid.UUID, body: ConnectionUpdate, request: Request, db: Session = Depends(get_db)):
    access(request, db, workspace_id, mutation=True)
    connection = service.get_connection(db, workspace_id, connection_id, lock=True)
    service.check_revision(connection, body.expected_revision)
    service.check_name(db, workspace_id, body.name, connection.id)
    service.update_fields(connection, body, request.app.state.settings)
    connection.revision += 1
    service.verify(connection, request.app.state.database_client, request.app.state.settings)
    db.commit()
    return service.payload(connection)


@router.post("/{connection_id}/verify")
def verify_connection(workspace_id: uuid.UUID, connection_id: uuid.UUID, body: ConnectionRevision, request: Request, db: Session = Depends(get_db)):
    access(request, db, workspace_id, mutation=True)
    connection = service.get_connection(db, workspace_id, connection_id, lock=True)
    service.check_revision(connection, body.expected_revision)
    service.verify(connection, request.app.state.database_client, request.app.state.settings)
    connection.revision += 1
    db.commit()
    return service.payload(connection)


@router.post("/{connection_id}/delete")
def delete_connection(workspace_id: uuid.UUID, connection_id: uuid.UUID, body: ConnectionRevision, request: Request, db: Session = Depends(get_db)):
    access(request, db, workspace_id, mutation=True)
    connection = service.get_connection(db, workspace_id, connection_id, lock=True)
    service.check_revision(connection, body.expected_revision)
    if service.references(db, workspace_id, connection.id):
        raise HTTPException(409, "This connection is used by SQL Query actions. Update or remove those actions before deleting it.")
    db.delete(connection); db.commit()
    return {"deleted": True}
