from datetime import timedelta

from sqlalchemy import func, select

from app.auth_service import resolve_provider_identity
from app.core.security import utcnow
from app.models import AuthSession, Identity, MagicLink, Membership, User, Workspace


def test_personal_email_signup_creates_owner_and_empty_workspace(client, app):
    response = client.post("/api/auth/magic-link", json={"email": "Chris@GMAIL.com"})
    assert response.status_code == 202
    assert "token" not in response.json()
    token = app.state.mailer.token
    response = client.post("/api/auth/verify", json={"token": token})
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "chris@gmail.com"
    assert body["workspace"]["role"] == "owner"
    assert body["workspace"]["setup_completed"] is False
    assert body["next_path"] == "/onboarding/workspace"
    assert client.get("/api/auth/me").status_code == 200
    with app.state.session_factory() as db:
        link = db.scalar(select(MagicLink))
        assert token not in link.token_hash
        assert link.used_at is not None
        assert db.scalar(select(func.count()).select_from(Membership)) == 1
    cookie = response.headers.get_list("set-cookie")
    assert any("petrix_session=" in item and "HttpOnly" in item for item in cookie)


def test_magic_link_is_single_use(client, app, signed_in):
    assert client.post("/api/auth/verify", json={"token": app.state.mailer.token}).status_code == 400


def test_expired_magic_link_does_not_create_account(client, app):
    client.post("/api/auth/magic-link", json={"email": "person@yahoo.com"})
    with app.state.session_factory() as db:
        link = db.scalar(select(MagicLink))
        link.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.post("/api/auth/verify", json={"token": app.state.mailer.token}).status_code == 400
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_returning_login_reuses_user_and_workspace(client, app, signed_in):
    first_id = signed_in["workspace"]["id"]
    client.post("/api/auth/magic-link", json={"email": "CHRIS@gmail.com"})
    response = client.post("/api/auth/verify", json={"token": app.state.mailer.token})
    assert response.json()["workspace"]["id"] == first_id
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(Workspace)) == 1


def test_invalid_and_blocked_addresses_rejected(client, app):
    assert client.post("/api/auth/magic-link", json={"email": "not-an-email"}).status_code == 422
    app.state.settings.blocked_email_domains = "blocked.example.com"
    assert client.post("/api/auth/magic-link", json={"email": "test@blocked.example.com"}).status_code == 400


def test_email_rate_limit_enforced(client, app):
    app.state.settings.email_limit_per_window = 2
    for _ in range(2):
        assert client.post("/api/auth/magic-link", json={"email": "user@gmail.com"}).status_code == 202
    response = client.post("/api/auth/magic-link", json={"email": "user@gmail.com"})
    assert response.status_code == 429
    assert "retry-after" in response.headers


def test_delivery_failure_does_not_issue_a_valid_link(client, app):
    def failure(*_):
        raise OSError("Mail server unavailable")
    app.state.mailer.send_magic_link = failure
    assert client.post("/api/auth/magic-link", json={"email": "user@gmail.com"}).status_code == 503
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(MagicLink)) == 0


def test_logout_requires_csrf_and_revokes_session(client, app, signed_in):
    old_session = client.cookies.get("petrix_session")
    assert client.post("/api/auth/logout").status_code == 403
    assert client.post("/api/auth/logout", headers={"X-Petrix-CSRF": client.cookies.get("petrix_csrf")}).status_code == 204
    client.cookies.set("petrix_session", old_session)
    assert client.get("/api/auth/me").status_code == 401


def test_expired_session_rejected(client, app, signed_in):
    with app.state.session_factory() as db:
        session = db.scalar(select(AuthSession))
        session.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_cross_origin_requests_rejected(client):
    response = client.post("/api/auth/magic-link", json={"email": "user@gmail.com"}, headers={"Origin": "https://untrusted.example.com"})
    assert response.status_code == 403


def test_google_gmail_links_existing_verified_account(app, client, signed_in):
    with app.state.session_factory() as db:
        user, ticket = resolve_provider_identity(db, app.state.settings, "google", {
            "iss": "https://accounts.google.com", "sub": "google-subject-1", "email": "chris@gmail.com", "email_verified": True
        })
        assert ticket is None
        assert str(user.id) == signed_in["user"]["id"]
        db.commit()
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(Identity)) == 1


def test_microsoft_requires_email_proof_before_linking(app, client, signed_in):
    with app.state.session_factory() as db:
        user, ticket = resolve_provider_identity(db, app.state.settings, "microsoft", {
            "iss": "https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0",
            "sub": "microsoft-subject-1", "email": "chris@gmail.com"
        })
        assert user is None and ticket
        assert db.scalar(select(func.count()).select_from(Identity)) == 0
    assert client.post("/api/auth/provider-email", json={"ticket": ticket, "email": "chris@gmail.com"}).status_code == 202
    response = client.post("/api/auth/verify", json={"token": app.state.mailer.token})
    assert response.status_code == 200
    assert response.json()["user"]["id"] == signed_in["user"]["id"]
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(Identity)) == 1


def test_google_third_party_email_needs_mailbox_proof(app):
    with app.state.session_factory() as db:
        user, ticket = resolve_provider_identity(db, app.state.settings, "google", {
            "iss": "https://accounts.google.com", "sub": "other-subject", "email": "person@yahoo.com", "email_verified": True
        })
        assert user is None and ticket
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_oauth_methods_unavailable_without_credentials(client):
    assert client.get("/api/auth/options").json()["google"] is False
    response = client.get("/api/auth/oauth/google/start", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("error=provider_unavailable")
