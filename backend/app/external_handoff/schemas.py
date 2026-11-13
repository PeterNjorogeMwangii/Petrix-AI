import re
from typing import Literal, Optional
from urllib.parse import urlsplit
from pydantic import Field, SecretStr, StrictBool, field_validator, model_validator
from app.agent_schemas import StrictModel

Platform = Literal['zendesk', 'bitrix24']


class IntegrationUpdate(StrictModel):
    expected_revision: int = Field(ge=0)
    enabled: StrictBool = True
    origin: str = Field(max_length=250)
    app_id: str = Field(default='', max_length=100)
    switchboard_id: str = Field(default='', max_length=100)
    target_id: str = Field(max_length=100)
    key_id: str = Field(max_length=100)
    secret: Optional[SecretStr] = None
    bot_client_id: Optional[SecretStr] = None

    @field_validator('origin')
    @classmethod
    def valid_origin(cls, value):
        p = urlsplit(value)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.path not in ('', '/') or p.query or p.fragment or p.port not in (None, 443):
            raise ValueError('Enter an HTTPS portal origin without a path, credentials, or port.')
        if not re.fullmatch(r'[a-z0-9.-]+', p.hostname):
            raise ValueError('Enter a valid portal hostname.')
        return 'https://' + p.hostname.lower()

    @field_validator('app_id', 'switchboard_id', 'target_id', 'key_id')
    @classmethod
    def valid_id(cls, value):
        if value and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
            raise ValueError('Use a platform identifier containing letters, numbers, underscores, or hyphens.')
        return value


class Revision(StrictModel):
    expected_revision: int = Field(ge=1)


class LinkConversation(StrictModel):
    platform: Platform
    external_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')


class ReviewTransfer(Revision):
    resolution: Literal['transferred', 'not_transferred']
