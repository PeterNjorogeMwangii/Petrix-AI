"""Unified Inbox requests; saved filters never grant record access."""
import uuid
from typing import List, Optional, Literal
from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator
from app.agent_schemas import StrictModel, LANGUAGE_CODES


class ViewFilters(StrictModel):
    kind: Literal['all', 'chats', 'tickets'] = 'all'
    statuses: List[Literal['open', 'pending', 'overdue', 'resolved', 'closed', 'ai', 'external']] = Field(default_factory=list, max_length=7)
    assigned: Literal['any', 'me', 'unassigned', 'teams'] = 'any'
    agent_id: Optional[uuid.UUID] = None
    team_id: Optional[uuid.UUID] = None
    tag_id: Optional[uuid.UUID] = None
    group_id: Optional[uuid.UUID] = None
    language: str = Field(default='', max_length=16)
    channel: Literal['', 'webchat', 'whatsapp', 'messaging', 'tester', 'email', 'ai_agent_handoff', 'bitrix24', 'africas_talking', 'facebook'] = ''
    priority: StrictBool = False

    @field_validator('language')
    @classmethod
    def language_code(cls, value):
        if value and value not in LANGUAGE_CODES: raise ValueError('Select a supported language.')
        return value


class ViewBody(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    scope: Literal['private', 'teamspace'] = 'private'
    teamspace_id: uuid.UUID
    filters: ViewFilters = Field(default_factory=ViewFilters)
    expected_revision: Optional[StrictInt] = Field(default=None, ge=1)

    @field_validator('name')
    @classmethod
    def name_value(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value): raise ValueError('Enter a single-line view name.')
        return value


class ViewRevision(StrictModel):
    expected_revision: StrictInt = Field(ge=1)


class Preferences(StrictModel):
    expected_revision: StrictInt = Field(ge=0)
    pinned: List[str] = Field(default_factory=list, max_length=50)
    sort: Literal['', 'created', 'latest', 'wait', 'handoff'] = ''
    direction: Literal['asc', 'desc'] = 'desc'

    @field_validator('pinned')
    @classmethod
    def unique_pins(cls, value):
        if len(value) != len(set(value)) or any(len(v) > 60 for v in value): raise ValueError('Pin each view once.')
        return value


class AssistantRequest(StrictModel):
    request_id: uuid.UUID
    mode: Literal['draft', 'ask', 'mediate', 'enhance', 'translate'] = 'draft'
    question: str = Field(default='', max_length=4000)
    draft: str = Field(default='', max_length=8000)
    style: Literal['shorten', 'lengthen', 'formal', 'casual'] = 'formal'
    language: str = Field(default='', max_length=16)
    mediate: StrictBool = False

    @model_validator(mode='after')
    def complete(self):
        if self.mode in {'ask', 'mediate'} and not self.question.strip(): raise ValueError('Enter a Copilot question.')
        if self.mode in {'enhance', 'translate'} and not self.draft.strip(): raise ValueError('Enter a reply draft first.')
        if self.mode == 'translate' and self.language not in LANGUAGE_CODES: raise ValueError('Select a supported target language.')
        return self
