import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database import get_db
from app.models import WorkflowAsset
from app.routes.agents import access
from app.workflows.actions.send_attachment.files import MAX_FILE_BYTES, clean_name, validate_file
from app.workflows.assets import asset_ids, asset_metadata, file_response
from app.workflows.service import get_document

router = APIRouter(prefix="/api/workspaces/{workspace_id}/agents/{agent_id}/workflow-assets", tags=["Workflow files"])


def find_file(db, workspace_id, agent_id, asset_id):
    file = db.scalar(select(WorkflowAsset).where(WorkflowAsset.id == asset_id,
        WorkflowAsset.workspace_id == workspace_id, WorkflowAsset.agent_id == agent_id))
    if file is None:
        raise HTTPException(404, "Workflow file not found.")
    return file


def store_file(db, request, workspace_id, agent_id, name, data, file_format):
    access(request, db, workspace_id, mutation=True, agent_id=agent_id)
    count, size = db.execute(select(func.count(WorkflowAsset.id), func.coalesce(func.sum(WorkflowAsset.size_bytes), 0))
        .where(WorkflowAsset.agent_id == agent_id)).one()
    if count >= 100 or size + len(data) > 1024 * 1024 * 1024:
        raise HTTPException(422, "This agent's file storage is full (100 files or 1 GiB). Remove unused attachments before uploading more.")
    try:
        content_type = validate_file(name, data, file_format)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    file = WorkflowAsset(workspace_id=workspace_id, agent_id=agent_id, file_name=name,
        file_format=file_format, content_type=content_type, size_bytes=len(data), data=data)
    db.add(file); db.flush()
    result = asset_metadata(file, workspace_id, agent_id)
    db.commit()
    return result


@router.post("", status_code=201)
async def upload_file(workspace_id: uuid.UUID, agent_id: uuid.UUID, request: Request,
    file_format: Literal["image", "audio", "video", "document"] = Form(...),
    file: UploadFile = File(...), db: Session = Depends(get_db)):
    access(request, db, workspace_id, mutation=True, agent_id=agent_id, lock=False)
    try:
        data = await file.read(MAX_FILE_BYTES + 1)
    finally:
        await file.close()
    return await run_in_threadpool(store_file, db, request, workspace_id, agent_id, clean_name(file.filename), data, file_format)


@router.get("/{asset_id}")
def get_file(workspace_id: uuid.UUID, agent_id: uuid.UUID, asset_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    access(request, db, workspace_id, agent_id=agent_id)
    return asset_metadata(find_file(db, workspace_id, agent_id, asset_id), workspace_id, agent_id)


@router.get("/{asset_id}/file")
def download_file(workspace_id: uuid.UUID, agent_id: uuid.UUID, asset_id: uuid.UUID, request: Request,
    inline: bool = False, db: Session = Depends(get_db)):
    access(request, db, workspace_id, agent_id=agent_id)
    return file_response(find_file(db, workspace_id, agent_id, asset_id), request, inline=inline)


@router.post("/{asset_id}/delete")
def delete_file(workspace_id: uuid.UUID, agent_id: uuid.UUID, asset_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    access(request, db, workspace_id, mutation=True, agent_id=agent_id)
    file = find_file(db, workspace_id, agent_id, asset_id)
    if str(asset_id) in asset_ids(get_document(db, agent_id, lock=True).nodes):
        raise HTTPException(409, "This file is used by the saved workflow. Remove or replace it in every step before deleting it.")
    db.delete(file); db.commit()
    return {"deleted": True}
