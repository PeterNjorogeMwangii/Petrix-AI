import hashlib
import secrets
from datetime import datetime, timezone

SESSION_COOKIE = "petrix_session"
CSRF_COOKIE = "petrix_csrf"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
