import io
import uuid
import warnings
from typing import Optional

import pycountry
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth_service import current_session, require_csrf, workspace_payload
from app.core.security import utcnow
from app.database import get_db
from app.models import Membership, Workspace
from app.schemas import WorkspaceSetup

router = APIRouter(prefix="/api", tags=["Workspaces"])
INDUSTRIES = ["Technology", "Banking", "Financial Services", "Healthcare", "Education", "Retail", "Travel & Hospitality", "Government", "Other"]
MAX_AVATAR_BYTES = 2 * 1024 * 1024


def countries(settings):
    allowed = {code.strip().upper() for code in settings.supported_countries.split(",") if code.strip()}
    return sorted(
        [{"code": country.alpha_2, "name": country.name} for country in pycountry.countries if not allowed or country.alpha_2 in allowed],
        key=lambda item: item["name"]
    )


def workspace_access(db: Session, workspace_id: uuid.UUID, user_id: uuid.UUID):
    row = db.execute(select(Workspace, Membership.role).join(Membership, Membership.workspace_id == Workspace.id)
        .where(Workspace.id == workspace_id, Membership.user_id == user_id)).first()
    if not row:
        raise HTTPException(404, "Workspace not found.")
    return row


def sanitize_avatar(data: bytes) -> bytes:
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(422, "Choose an avatar smaller than 2 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data))
            if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 16_000_000:
                raise ValueError("Unsupported image")
            image.load()
            image = ImageOps.exif_transpose(image).convert("RGBA")
            image = ImageOps.fit(image, (256, 256), method=Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(422, "Upload a valid PNG, JPEG, or WebP image.") from None


@router.get("/onboarding/options")
def onboarding_options(request: Request, db: Session = Depends(get_db)):
    current_session(request, db)
    country_list = countries(request.app.state.settings)
    codes = {item["code"] for item in country_list}
    return {"countries": country_list, "industries": INDUSTRIES, "default_country": request.app.state.settings.default_country if request.app.state.settings.default_country in codes else None}


@router.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    session = current_session(request, db)
    workspace, role = workspace_access(db, workspace_id, session.user_id)
    return workspace_payload(workspace, role, db)


@router.post("/workspaces/{workspace_id}/setup")
def setup_workspace(
    workspace_id: uuid.UUID, request: Request,
    name: str = Form(...), country_code: str = Form(...), industry: str = Form("Other"),
    website: str = Form(""), avatar: Optional[UploadFile] = File(None), db: Session = Depends(get_db)
):
    session = current_session(request, db)
    require_csrf(request, session)
    workspace, role = workspace_access(db, workspace_id, session.user_id)
    if role != "owner":
        raise HTTPException(403, "Only the workspace owner can complete setup.")
    try:
        body = WorkspaceSetup(name=name, country_code=country_code, industry=industry, website=website.strip() or None)
    except ValidationError:
        raise HTTPException(422, "Check your workspace name, country, and website. Website addresses must begin with http:// or https://.") from None
    if body.country_code not in {item["code"] for item in countries(request.app.state.settings)}:
        raise HTTPException(422, "Choose a supported country.")
    if body.industry not in INDUSTRIES:
        raise HTTPException(422, "Choose an industry from the list.")
    if avatar:
        data = avatar.file.read(MAX_AVATAR_BYTES + 1)
        workspace.avatar_data = sanitize_avatar(data)
    workspace.name = body.name
    workspace.country_code = body.country_code
    workspace.industry = body.industry
    workspace.website = str(body.website) if body.website else None
    workspace.setup_completed_at = workspace.setup_completed_at or utcnow()
    db.commit()
    return {"workspace": workspace_payload(workspace, role, db), "next_path": "/app/agents"}


@router.get("/workspaces/{workspace_id}/avatar")
def workspace_avatar(workspace_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    session = current_session(request, db)
    workspace, _ = workspace_access(db, workspace_id, session.user_id)
    if not workspace.avatar_data:
        raise HTTPException(404, "No avatar has been uploaded.")
    return Response(workspace.avatar_data, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})
