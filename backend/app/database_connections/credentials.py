import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken


def cipher(settings):
    configured = settings.database_connections_encryption_key.get_secret_value()
    # Domain separation keeps connection encryption independent of session tokens.
    key = configured.encode("ascii") if configured else base64.urlsafe_b64encode(hmac.new(
        settings.session_signing_secret.get_secret_value().encode("utf-8"),
        b"petrix-database-connections-v1", hashlib.sha256).digest())
    return Fernet(key)


def encrypt(password, settings):
    return cipher(settings).encrypt(password.encode("utf-8")).decode("ascii")


def decrypt(value, settings):
    try:
        return cipher(settings).decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        raise ValueError("credentials_unavailable") from None
