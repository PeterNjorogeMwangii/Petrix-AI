import re
import uuid
from typing import List, Literal, Optional
import idna
from pydantic import Field, StrictBool, StrictInt, field_validator
from app.agent_schemas import StrictModel


class Revision(StrictModel):
    expected_revision: StrictInt = Field(ge=1)


class GroupBody(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=1000)


class GroupUpdate(GroupBody, Revision):
    pass


class TagBody(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    group_id: Optional[uuid.UUID] = None
    group_ids: Optional[List[uuid.UUID]] = Field(default=None,max_length=100)
    color: str = Field(default='#d19a70', pattern=r'^#[0-9a-fA-F]{6}$')


class TagUpdate(TagBody, Revision):
    pass


class DomainBody(StrictModel):
    domain: str = Field(min_length=3, max_length=253)
    from_name: str = Field(default='Petrix Support', min_length=1, max_length=100)
    local_part: str = Field(default='support', min_length=1, max_length=64)
    verification_method: Literal['cname','txt'] = 'cname'
    enabled: StrictBool = True

    @field_validator('domain')
    @classmethod
    def valid_domain(cls, value):
        try: value = idna.encode(value.rstrip('.').lower(), uts46=True).decode('ascii')
        except idna.IDNAError: raise ValueError('Enter a DNS domain without a scheme, path, or email address.') from None
        if len(value)>253 or '.' not in value or value.rsplit('.',1)[1].isdigit():
            raise ValueError('Enter a public email domain, such as mail.example.com.')
        return value

    @field_validator('local_part')
    @classmethod
    def valid_local(cls,value):
        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9._+-]{0,62}[A-Za-z0-9])?',value) or '..' in value:
            raise ValueError('Use a simple sender mailbox name, such as support.')
        return value

    @field_validator('from_name')
    @classmethod
    def no_headers(cls,value):
        if any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError('Email names cannot contain control characters.')
        return value


class DomainUpdate(DomainBody,Revision):
    pass


class TemplateBody(StrictModel):
    name: str = Field(min_length=1,max_length=100)
    description: str = Field(default='',max_length=1000)
    content: str = Field(min_length=1,max_length=65536)


class TemplateUpdate(TemplateBody,Revision):
    pass


class TicketUpdate(Revision):
    status: Literal['open','in_progress','resolved','closed','overdue']
    assignee_type: Literal['none','user','team'] = 'none'
    assignee_id: Optional[uuid.UUID] = None


class NoteBody(StrictModel):
    content: str = Field(min_length=1,max_length=8000)


class EmailRetry(Revision):
    confirm_not_sent: StrictBool = False
