import secrets
import uuid
from datetime import timedelta
from typing import Optional

from fastapi import HTTPException, Request, Response
from pydantic import TypeAdapter, EmailStr
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import CSRF_COOKIE, SESSION_COOKIE, new_token, token_hash, utcnow
from app.models import AuthSession, Identity, MagicLink, Membership, PendingIdentity, RateLimit, User, Workspace

EMAIL_ACCEPTED = "If this email is eligible, a sign-in link will arrive shortly."


def eligible_email(email: str, settings: Settings) -> str:
    try:
        normalized = str(TypeAdapter(EmailStr).validate_python(email)).lower().strip()
    except ValueError:
        raise HTTPException(422, "Enter a valid email address.") from None
    blocked = {x.strip().lower() for x in settings.blocked_email_domains.split(",") if x.strip()}
    if normalized.rsplit("@", 1)[1] in blocked:
        raise HTTPException(400, "This email address is not eligible for registration.")
    # Case-insensitive account policy; Gmail dots and +tags are not rewritten.
    return normalized


def rate_limit(db: Session, settings: Settings, email: str, ip: str):
    now = utcnow()
    window_seconds = settings.rate_limit_minutes * 60
    bucket = int(now.timestamp()) // window_seconds
    for kind, value, maximum in (
        ("email", email, settings.email_limit_per_window),
        ("ip", ip, settings.ip_limit_per_window),
    ):
        key = token_hash(f"{kind}:{value}:{bucket}")
        statement = insert(RateLimit).values(key=key, count=1, expires_at=now + timedelta(seconds=window_seconds * 2))
        statement = statement.on_conflict_do_update(
            index_elements=[RateLimit.key], set_={"count": RateLimit.count + 1}
        ).returning(RateLimit.count)
        count = db.execute(statement).scalar_one()
        if count > maximum:
            db.commit()
            retry_after = window_seconds - int(now.timestamp()) % window_seconds
            raise HTTPException(429, "Too many requests. Please try again later.", headers={"Retry-After": str(retry_after)})


def create_user_workspace(db: Session, email: str, full_name: Optional[str] = None, settings: Optional[Settings] = None) -> User:
    user_id = uuid.uuid4()
    inserted = db.execute(
        insert(User).values(id=user_id, email=email, full_name=full_name, created_at=utcnow())
        .on_conflict_do_nothing(index_elements=[User.email]).returning(User.id)
    ).scalar_one_or_none()
    if inserted:
        workspace = Workspace(language_limit=settings.default_language_limit if settings else 3)
        db.add(workspace)
        db.flush()
        db.add(Membership(user_id=user_id, workspace_id=workspace.id, role="owner"))
        db.flush()
        from app.agent_service import initialize_workspace
        initialize_workspace(db, workspace, user_id)
        return db.get(User, user_id)
    return db.scalar(select(User).where(User.email == email))


def link_identity(db: Session, user: User, provider: str, issuer: str, subject: str):
    inserted = db.execute(
        insert(Identity).values(
            id=uuid.uuid4(), user_id=user.id, provider=provider, issuer=issuer, subject=subject, created_at=utcnow()
        ).on_conflict_do_nothing(constraint="uq_provider_identity").returning(Identity.user_id)
    ).scalar_one_or_none()
    linked_user = inserted or db.scalar(
        select(Identity.user_id).where(Identity.provider == provider, Identity.issuer == issuer, Identity.subject == subject)
    )
    if linked_user != user.id:
        raise HTTPException(409, "This sign-in identity is already linked to another account.")


def consume_link(db: Session, raw_token: str, settings: Settings) -> User:
    now = utcnow()
    # Conditional UPDATE is atomic: two requests cannot consume the same link.
    row = db.execute(
        update(MagicLink).where(
            MagicLink.token_hash == token_hash(raw_token), MagicLink.used_at.is_(None), MagicLink.expires_at > now
        ).values(used_at=now).returning(MagicLink.email, MagicLink.pending_identity_id)
    ).first()
    if not row:
        raise HTTPException(400, "This link is invalid, expired, or has already been used. Request a new link.")
    email = eligible_email(row.email, settings)
    pending = db.get(PendingIdentity, row.pending_identity_id) if row.pending_identity_id else None
    if row.pending_identity_id and (not pending or pending.expires_at <= now):
        raise HTTPException(400, "This sign-in request has expired. Please start again.")
    user = create_user_workspace(db, email, pending.full_name if pending else None, settings=settings)
    if pending:
        link_identity(db, user, pending.provider, pending.issuer, pending.subject)
    return user


def user_context(db: Session, user: User, workspace_id=None) -> dict:
    row = db.execute(
        select(Workspace, Membership.role).join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id).order_by((Workspace.id == workspace_id).desc() if workspace_id else Membership.created_at, Membership.created_at, Membership.id).limit(1)
    ).first()
    if not row:
        raise HTTPException(409, "This account has no workspace membership.")
    workspace, role = row
    return {
        "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name},
        "workspace": workspace_payload(workspace, role, db),
        "next_path": "/app/agents" if workspace.setup_completed_at else "/onboarding/workspace",
    }


def workspace_payload(workspace: Workspace, role: str, db: Optional[Session] = None) -> dict:
    from app.agent_service import can_manage
    return {
        "id": str(workspace.id), "name": workspace.name, "industry": workspace.industry,
        "country_code": workspace.country_code, "website": workspace.website, "role": role,
        "setup_completed": workspace.setup_completed_at is not None,
        "avatar_url": f"/api/workspaces/{workspace.id}/avatar" if workspace.avatar_data else None,
        "permissions": {"manage_ai_agents": can_manage(db, workspace.id, role) if db else role in {"owner", "admin"}, "manage_workspace_roles": role in {"owner", "admin"}},
        "subscription": {"plan_name": workspace.plan_name, "agent_limit": None, "language_limit": workspace.language_limit},
    }


def establish_session(db: Session, user: User, request: Request, response: Response):
    settings = request.app.state.settings
    previous = request.cookies.get(SESSION_COOKIE)
    if previous:
        db.execute(update(AuthSession).where(AuthSession.session_hash == token_hash(previous)).values(revoked_at=utcnow()))
    raw_session, raw_csrf = new_token(), new_token()
    db.add(AuthSession(
        user_id=user.id, session_hash=token_hash(raw_session), csrf_hash=token_hash(raw_csrf),
        expires_at=utcnow() + timedelta(hours=settings.session_hours)
    ))
    db.commit()
    cookie_options = {"secure": settings.secure_cookies, "samesite": "lax", "path": "/", "max_age": settings.session_hours * 3600}
    response.set_cookie(SESSION_COOKIE, raw_session, httponly=True, **cookie_options)
    response.set_cookie(CSRF_COOKIE, raw_csrf, httponly=False, **cookie_options)


def current_session(request: Request, db: Session) -> AuthSession:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(401, "Please sign in to continue.")
    session = db.scalar(select(AuthSession).where(
        AuthSession.session_hash == token_hash(raw), AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow()
    ))
    if not session:
        raise HTTPException(401, "Your session has expired. Please sign in again.")
    return session


def require_csrf(request: Request, session: AuthSession):
    header = request.headers.get("X-Petrix-CSRF", "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not header or not cookie or not secrets.compare_digest(header, cookie) or not secrets.compare_digest(token_hash(header), session.csrf_hash):
        raise HTTPException(403, "The security check failed. Refresh the page and try again.")


def resolve_provider_identity(db: Session, settings: Settings, provider: str, claims: dict):
    """Accept only claims already signature/issuer/audience/nonce validated by OIDC."""
    issuer, subject = claims.get("iss"), claims.get("sub")
    if not issuer or not subject or len(issuer) > 255 or len(subject) > 255:
        raise HTTPException(400, "The sign-in provider returned an invalid identity.")
    identity = db.scalar(select(Identity).where(Identity.provider == provider, Identity.issuer == issuer, Identity.subject == subject))
    if identity:
        return db.get(User, identity.user_id), None
    email = claims.get("email", "")
    try:
        normalized = eligible_email(email, settings)
    except HTTPException:
        normalized = None
    name = str(claims.get("name") or "")[:200] or None
    google_authoritative = provider == "google" and claims.get("email_verified") is True and normalized and (
        normalized.endswith("@gmail.com") or bool(claims.get("hd"))
    )
    if google_authoritative:
        user = create_user_workspace(db, normalized, name, settings=settings)
        link_identity(db, user, provider, issuer, subject)
        return user, None
    # Microsoft and non-authoritative Google emails require fresh mailbox proof.
    ticket = new_token()
    pending = PendingIdentity(
        ticket_hash=token_hash(ticket), provider=provider, issuer=issuer, subject=subject,
        full_name=name, email_hint=normalized, expires_at=utcnow() + timedelta(minutes=10)
    )
    db.add(pending)
    db.commit()
    return None, ticket
