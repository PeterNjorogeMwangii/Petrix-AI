import re, uuid
from typing import Optional, Literal
from pydantic import Field, SecretStr, field_validator
from app.agent_schemas import StrictModel


class Configuration(StrictModel):
    language: str = 'en'
    icon: Optional[uuid.UUID] = None
    auto_close: bool = False
    timeout_minutes: int = Field(default=15, ge=1, le=10080)


class Create(StrictModel):
    client_id: uuid.UUID
    name: str = Field(default='Default WhatsApp', min_length=1, max_length=100)

    @field_validator('name')
    @classmethod
    def clean(cls,value):
        value=value.strip()
        if not value or any(ord(c)<32 for c in value): raise ValueError('Enter a channel name without control characters.')
        return value


class Revision(StrictModel):
    expected_revision: int = Field(ge=1)


class Save(Revision):
    name: str = Field(min_length=1,max_length=100)
    enabled: bool
    config: Configuration

    @field_validator('name')
    @classmethod
    def clean(cls,value): return Create.clean(value)


class Connection(Revision):
    app_id: str
    phone_number_id: str
    app_secret: SecretStr = SecretStr('')
    access_token: SecretStr = SecretStr('')

    @field_validator('app_id','phone_number_id')
    @classmethod
    def numeric(cls,value):
        value=value.strip()
        if not re.fullmatch(r'[0-9]{3,32}',value): raise ValueError('Use the numeric Meta ID.')
        return value

    @field_validator('app_secret','access_token')
    @classmethod
    def secret(cls,value):
        raw=value.get_secret_value().strip()
        if len(raw)>8192 or any(ord(c)<32 or ord(c)==127 for c in raw): raise ValueError('Check the credential format.')
        return SecretStr(raw)


class Delete(Revision):
    confirmed: bool


class Review(StrictModel):
    confirmed: bool
    action: Literal['dismiss','retry','cancel']
