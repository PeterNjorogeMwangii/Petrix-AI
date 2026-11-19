import base64,hashlib,hmac,json
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException


def cipher(settings):
    key=hmac.new(settings.session_signing_secret.get_secret_value().encode(),b'petrix-whatsapp-v1',hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value,settings): return cipher(settings).encrypt(value.encode()).decode()


def decrypt(value,settings):
    try: return cipher(settings).decrypt(value.encode()).decode()
    except (InvalidToken,ValueError,UnicodeError): raise HTTPException(422,'WhatsApp credentials are unavailable. Re-enter the connection settings.') from None


def secrets(row,settings):
    try: return json.loads(decrypt(row.credentials,settings)) if row.credentials else {}
    except (ValueError,TypeError): raise HTTPException(422,'Re-enter the WhatsApp connection settings.') from None
