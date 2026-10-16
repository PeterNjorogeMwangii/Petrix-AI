import time

import pytest
from fastapi import HTTPException
from authlib.jose.errors import ExpiredTokenError, InvalidClaimError
from pydantic import ValidationError

from app.core.config import Settings
from app.oauth import PERSONAL_MICROSOFT_TENANT, microsoft_claims_class
from app.routes.workspaces import sanitize_avatar


def claims(**changes):
    now = int(time.time())
    values = {"iss": f"https://login.microsoftonline.com/{PERSONAL_MICROSOFT_TENANT}/v2.0", "tid": PERSONAL_MICROSOFT_TENANT, "sub": "subject", "aud": "client-id", "exp": now + 60, "iat": now, "nonce": "expected-nonce"}
    values.update(changes)
    return values


def validate(values, tenant="common"):
    token = microsoft_claims_class(tenant)(values, {"alg": "RS256"}, {}, {"client_id": "client-id", "nonce": "expected-nonce"})
    token.validate()


def test_microsoft_common_valid_issuer():
    validate(claims())


@pytest.mark.parametrize("changed", [{"iss": "https://attacker.example.com"}, {"tid": "not-a-tenant"}, {"aud": "other-client"}, {"nonce": "wrong-nonce"}, {"exp": 1}])
def test_microsoft_rejects_invalid_identity(changed):
    with pytest.raises((InvalidClaimError, ExpiredTokenError)):
        validate(claims(**changed))


def test_tenant_restriction_enforced():
    with pytest.raises(InvalidClaimError):
        validate(claims(), tenant="organizations")


def test_wrong_audience_cannot_be_bypassed_by_authorized_party():
    with pytest.raises(InvalidClaimError):
        validate(claims(aud="other-client", azp="client-id"))


def test_production_requires_https_and_smtp_encryption():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", database_url="postgresql+psycopg://u:p@localhost/db", session_signing_secret="s" * 40)


def test_short_secret_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="postgresql+psycopg://u:p@localhost/db", session_signing_secret="short")


def test_large_avatar_rejected():
    with pytest.raises(HTTPException):
        sanitize_avatar(b"a" * (2 * 1024 * 1024 + 1))
