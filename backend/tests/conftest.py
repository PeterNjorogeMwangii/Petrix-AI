import os
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.main import create_app


class CaptureMailer:
    def __init__(self):
        self.messages = []

    def send_magic_link(self, email, link):
        self.messages.append((email, link))

    @property
    def token(self):
        return parse_qs(urlsplit(self.messages[-1][1]).fragment)["token"][0]


@pytest.fixture(scope="session")
def database_engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a PostgreSQL database to run integration tests.")
    schema = "petrix_test_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()
    engine = create_engine(url, pool_size=1, max_overflow=0, pool_pre_ping=True)

    @event.listens_for(engine, "connect")
    def set_schema(dbapi_connection, _):
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}"')
        dbapi_connection.commit()

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with engine.begin() as connection:
        assert connection.execute(text("SELECT current_schema()")).scalar() == schema
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    yield engine
    engine.dispose()
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture
def app(database_engine, monkeypatch):
    import app.main as main_module
    factory = sessionmaker(database_engine, expire_on_commit=False)
    monkeypatch.setattr(main_module, "create_database", lambda _: (database_engine, factory))
    # The session fixture owns this shared engine; individual app lifespans do not.
    monkeypatch.setattr(database_engine, "dispose", lambda: None)
    settings = Settings(
        _env_file=None, app_env="test",
        database_url=os.environ["TEST_DATABASE_URL"],
        session_signing_secret="test-only-signing-secret-with-more-than-32-characters",
        intent_semantic_enabled=False,  # Matching integration tests inject controlled embedding vectors.
    )
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE users, workspaces, auth_rate_limits, pending_identities CASCADE"))
    application = create_app(settings, mailer=CaptureMailer())
    yield application


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://localhost:4200") as test_client:
        yield test_client


@pytest.fixture
def signed_in(client, app):
    assert client.post("/api/auth/magic-link", json={"email": "chris@gmail.com"}).status_code == 202
    response = client.post("/api/auth/verify", json={"token": app.state.mailer.token})
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def csrf(client, signed_in):
    return {"X-Petrix-CSRF": client.cookies.get("petrix_csrf")}
