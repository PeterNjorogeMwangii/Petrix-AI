import uuid

from authlib.integrations.starlette_client import OAuth
from authlib.jose.errors import InvalidClaimError
from authlib.oidc.core import CodeIDToken

from app.core.config import Settings

PERSONAL_MICROSOFT_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"


def validate_audience(claims: dict, client_id: str):
    audience = claims.get("aud")
    values = [audience] if isinstance(audience, str) else audience
    if not isinstance(values, list) or client_id not in values:
        raise InvalidClaimError("aud")


def microsoft_claims_class(allowed_tenant: str):
    class MicrosoftIDToken(CodeIDToken):
        def validate(self, now=None, leeway=0):
            validate_audience(self, self.params.get("client_id"))
            super().validate(now=now, leeway=leeway)
            tenant = self.get("tid", "")
            try:
                canonical_tenant = str(uuid.UUID(tenant))
            except (ValueError, TypeError, AttributeError):
                raise InvalidClaimError("tid") from None
            if tenant.lower() != canonical_tenant:
                raise InvalidClaimError("tid")
            if self.get("iss") != f"https://login.microsoftonline.com/{canonical_tenant}/v2.0":
                raise InvalidClaimError("iss")
            if allowed_tenant == "consumers" and tenant != PERSONAL_MICROSOFT_TENANT:
                raise InvalidClaimError("tid")
            if allowed_tenant == "organizations" and tenant == PERSONAL_MICROSOFT_TENANT:
                raise InvalidClaimError("tid")
            if allowed_tenant not in {"common", "consumers", "organizations"} and tenant.lower() != allowed_tenant.lower():
                raise InvalidClaimError("tid")

    return MicrosoftIDToken


def create_oauth(settings: Settings) -> OAuth:
    oauth = OAuth()
    client_kwargs = {"scope": "openid email profile", "code_challenge_method": "S256", "timeout": 10}
    if settings.google_enabled:
        oauth.register(
            "google", client_id=settings.google_client_id, client_secret=settings.google_client_secret.get_secret_value(),
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration", client_kwargs=client_kwargs.copy()
        )
    if settings.microsoft_enabled:
        oauth.register(
            "microsoft", client_id=settings.microsoft_client_id, client_secret=settings.microsoft_client_secret.get_secret_value(),
            server_metadata_url=f"https://login.microsoftonline.com/{settings.microsoft_tenant}/v2.0/.well-known/openid-configuration",
            client_kwargs=client_kwargs.copy()
        )
    return oauth
