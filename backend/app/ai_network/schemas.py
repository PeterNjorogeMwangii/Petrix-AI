import json
import uuid
from typing import List, Literal, Optional
from urllib.parse import urlsplit
from pydantic import Field, SecretStr, StrictBool, field_validator
from app.agent_schemas import StrictModel


def domain(value):
    value=value.strip()
    if '://' not in value:value='https://'+value
    parsed=urlsplit(value)
    if parsed.scheme not in {'https','http'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'}:
        raise ValueError('Use the receiving platform origin without a path, query, or credentials.')
    if parsed.scheme=='http' and parsed.hostname not in {'localhost','127.0.0.1','::1'}:
        raise ValueError('External platform domains require HTTPS.')
    _=parsed.port
    return value.rstrip('/')


class CredentialUpdate(StrictModel):
    expected_revision:int=Field(ge=0)
    enabled:StrictBool
    rotate:StrictBool=False


class ConnectAgent(StrictModel):
    agent_id:uuid.UUID
    secret:SecretStr
    domain:str=Field(max_length=250)

    @field_validator('domain')
    @classmethod
    def valid_domain(cls,value):return domain(value)

    @field_validator('secret')
    @classmethod
    def valid_secret(cls,value):
        raw=value.get_secret_value()
        if not 32<=len(raw)<=200 or not raw.isascii() or any(ord(c)<33 for c in raw):raise ValueError('Enter the receiving agent secret from Network settings.')
        return value


class Hop(StrictModel):
    domain:str=Field(max_length=250)
    agent_id:uuid.UUID
    @field_validator('domain')
    @classmethod
    def valid_domain(cls,value):return domain(value)


class HistoryMessage(StrictModel):
    role:Literal['user','assistant']
    content:str=Field(max_length=4000)


class StartHandoff(StrictModel):
    version:Literal[1]=1
    transfer_id:uuid.UUID
    context:dict
    history:List[HistoryMessage]=Field(default_factory=list,max_length=40)
    language:str=Field(max_length=16)
    channel:Literal['webchat','messaging','bitrix24','africas_talking']='webchat'
    trail:List[Hop]=Field(min_length=1,max_length=7)

    @field_validator('context')
    @classmethod
    def bounded_context(cls,value):
        if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode())>32768:raise ValueError('Transferred context must fit within 32 KiB.')
        return value


class PeerEvent(StrictModel):
    request_id:uuid.UUID
    event:dict
    files:List[dict]=Field(default_factory=list,max_length=10)
