"""Human ticket workspace requests. Workflow exports keep their existing schema."""
import uuid
import json
from typing import Optional, Literal
from pydantic import Field, StrictInt, field_validator, model_validator
from app.agent_schemas import StrictModel


class CreateBody(StrictModel):
    client_request_id: uuid.UUID
    subject: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=16000)
    sender_domain_id: Optional[uuid.UUID] = None
    user_id: Optional[uuid.UUID] = None
    team_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    teamspace_id: Optional[uuid.UUID] = None
    person_id: Optional[uuid.UUID] = None
    email: str = Field(default='', max_length=320)
    name: str = Field(default='', max_length=200)
    conversation_id: Optional[uuid.UUID] = None
    custom_fields: dict = Field(default_factory=dict)

    @field_validator('custom_fields')
    @classmethod
    def bounded_fields(cls, value):
        try:
            if len(json.dumps(value, allow_nan=False).encode()) > 131072: raise ValueError()
        except (TypeError, ValueError): raise ValueError('Use valid custom field values within 128 KiB.') from None
        return value

    @field_validator('subject', 'name')
    @classmethod
    def single_line(cls, value):
        value = value.strip()
        if any(ord(c) < 32 or ord(c) == 127 for c in value): raise ValueError('Use single-line text.')
        return value

    @field_validator('team_ids')
    @classmethod
    def unique_teams(cls, value):
        if len(value) != len(set(value)): raise ValueError('Select each team once.')
        return value

    @model_validator(mode='after')
    def complete(self):
        if not self.subject.strip(): raise ValueError('Enter a subject.')
        if not self.person_id and not self.email.strip() and not self.conversation_id: raise ValueError('Select a person or enter an email address.')
        return self


class DetailsBody(StrictModel):
    expected_revision: StrictInt = Field(ge=1)
    description: str = Field(default='', max_length=16000)
    status: Literal['open', 'pending', 'in_progress', 'overdue', 'resolved', 'closed']
    sender_domain_id: Optional[uuid.UUID] = None


class TransferBody(StrictModel):
    expected_revision: StrictInt = Field(ge=1)
    user_id: Optional[uuid.UUID] = None
    team_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)

    @field_validator('team_ids')
    @classmethod
    def unique_teams(cls, value):
        if len(value) != len(set(value)): raise ValueError('Select each team once.')
        return value


class NoteBody(StrictModel):
    expected_revision: Optional[StrictInt] = Field(default=None, ge=1)
    content: str = Field(min_length=1, max_length=8000)
    color: str = Field(default='#fff0b3', pattern=r'^#[0-9a-fA-F]{6}$')

    @field_validator('content')
    @classmethod
    def nonempty(cls, value):
        if not value.strip(): raise ValueError('Enter an internal note.')
        return value


class NoteRevision(StrictModel):
    expected_revision: StrictInt = Field(ge=1)


class DeleteBody(StrictModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    revisions: dict[uuid.UUID, StrictInt]

    @model_validator(mode='after')
    def selection(self):
        if len(set(self.ids)) != len(self.ids) or set(self.ids) != set(self.revisions) or any(v < 1 for v in self.revisions.values()):
            raise ValueError('Include the current revision of every selected ticket once.')
        return self
