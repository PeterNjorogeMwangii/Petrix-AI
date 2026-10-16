import logging
import smtplib
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from app.auth_service import EMAIL_ACCEPTED, consume_link, current_session, eligible_email, establish_session, rate_limit, require_csrf, resolve_provider_identity, user_context
from app.core.security import CSRF_COOKIE, SESSION_COOKIE, new_token, token_hash, utcnow
from app.database import get_db
from app.models import MagicLink, PendingIdentity, User
from app.oauth import microsoft_claims_class, validate_audience
from app.schemas import EmailRequest, ProviderEmailRequest, VerifyRequest

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
logger = logging.getLogger(__name__)


@router.get("/options")
def options(request: Request):
    settings = request.app.state.settings
    return {"email": True, "google": settings.google_enabled, "microsoft": settings.microsoft_enabled, "magic_link_minutes": settings.magic_link_minutes}


def send_link(request: Request, db: Session, email: str, pending_id=None):
    settings = request.app.state.settings
    email = eligible_email(email, settings)
    rate_limit(db, settings, email, request.client.host if request.client else "unknown")
    raw = new_token()
    db.add(MagicLink(token_hash=token_hash(raw), email=email, pending_identity_id=pending_id, expires_at=utcnow() + timedelta(minutes=settings.magic_link_minutes)))
    db.flush()
    link = f"{settings.frontend_origin}/auth/verify#token={quote(raw, safe='')}"
    try:
        request.app.state.mailer.send_magic_link(email, link)
        db.commit()
    except (OSError, smtplib.SMTPException):
        db.rollback()
        # Never log recipient, link, token, or provider response bodies.
        logger.error("Magic-link email delivery failed")
        raise HTTPException(503, "We could not send the email right now. Please try again shortly.") from None
    return {"message": EMAIL_ACCEPTED, "expires_in_minutes": settings.magic_link_minutes}


@router.post("/magic-link", status_code=202)
def request_magic_link(body: EmailRequest, request: Request, db: Session = Depends(get_db)):
    return send_link(request, db, str(body.email))


@router.post("/verify")
def verify_link(body: VerifyRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    user = consume_link(db, body.token, request.app.state.settings)
    result = user_context(db, user)
    establish_session(db, user, request, response)
    return result


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    session = current_session(request, db)
    return user_context(db, db.get(User, session.user_id))


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    session = current_session(request, db)
    require_csrf(request, session)
    session.revoked_at = utcnow()
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.post("/provider-email/info")
def pending_info(body: VerifyRequest, db: Session = Depends(get_db)):
    pending = db.scalar(select(PendingIdentity).where(PendingIdentity.ticket_hash == token_hash(body.token), PendingIdentity.expires_at > utcnow(), PendingIdentity.requested_at.is_(None)))
    if not pending:
        raise HTTPException(400, "This sign-in request has expired or already been used. Please start again.")
    return {"provider": pending.provider, "email_hint": pending.email_hint}


@router.post("/provider-email", status_code=202)
def finish_provider_email(body: ProviderEmailRequest, request: Request, db: Session = Depends(get_db)):
    now = utcnow()
    pending_id = db.execute(update(PendingIdentity).where(
        PendingIdentity.ticket_hash == token_hash(body.ticket), PendingIdentity.expires_at > now, PendingIdentity.requested_at.is_(None)
    ).values(requested_at=now, expires_at=now + timedelta(minutes=request.app.state.settings.magic_link_minutes)).returning(PendingIdentity.id)).scalar_one_or_none()
    if not pending_id:
        raise HTTPException(400, "This sign-in request has expired or already been used. Please start again.")
    return send_link(request, db, str(body.email), pending_id)


@router.get("/oauth/{provider}/start")
async def oauth_start(provider: str, request: Request):
    client = request.app.state.oauth.create_client(provider) if provider in {"google", "microsoft"} else None
    if not client:
        return RedirectResponse(f"{request.app.state.settings.frontend_origin}/login?error=provider_unavailable", status_code=303)
    callback = f"{request.app.state.settings.api_base_url}/api/auth/oauth/{provider}/callback"
    return await client.authorize_redirect(request, callback, prompt="select_account")


@router.get("/oauth/{provider}/callback")
async def oauth_callback(provider: str, request: Request, db: Session = Depends(get_db)):
    settings = request.app.state.settings
    client = request.app.state.oauth.create_client(provider) if provider in {"google", "microsoft"} else None
    if not client:
        return RedirectResponse(f"{settings.frontend_origin}/login?error=provider_unavailable", status_code=303)
    try:
        # Authlib verifies state, PKCE, signature, audience, expiry, and nonce.
        kwargs = {}
        if provider == "microsoft":
            # common discovery uses an issuer template; validate the signed tid/iss pair explicitly.
            kwargs = {"claims_cls": microsoft_claims_class(settings.microsoft_tenant), "claims_options": {}}
        token = await client.authorize_access_token(request, **kwargs)
        claims = token.get("userinfo")
        if not claims:
            raise ValueError("Missing verified ID token")
        validate_audience(claims, client.client_id)
        user, ticket = resolve_provider_identity(db, settings, provider, dict(claims))
        if ticket:
            response = RedirectResponse(f"{settings.frontend_origin}/auth/provider-email#ticket={quote(ticket, safe='')}", status_code=303)
        else:
            result = user_context(db, user)
            response = RedirectResponse(f"{settings.frontend_origin}{result['next_path']}", status_code=303)
            establish_session(db, user, request, response)
        request.session.clear()
        return response
    except Exception:
        db.rollback()
        request.session.clear()
        logger.warning("OAuth sign-in could not be completed for provider %s", provider)
        return RedirectResponse(f"{settings.frontend_origin}/login?error=provider_failed", status_code=303)
