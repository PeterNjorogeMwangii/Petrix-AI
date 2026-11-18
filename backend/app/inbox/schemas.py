import uuid
from typing import List, Literal, Optional
from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator
from app.agent_schemas import StrictModel, LANGUAGE_CODES


class ProfileUpdate(StrictModel):
    expected_revision: StrictInt = Field(ge=0)
    available: StrictBool
    languages: List[str] = Field(min_length=1, max_length=20)

    @field_validator('languages')
    @classmethod
    def valid_languages(cls, values):
        if len(set(values)) != len(values) or any(value not in LANGUAGE_CODES for value in values):
            raise ValueError('Choose distinct supported languages.')
        return values


class AssignmentUpdate(StrictModel):
    expected_revision: StrictInt = Field(ge=1)
    mode: Literal['manual', 'round_robin', 'least_busy']
    max_active_chats: StrictInt = Field(ge=1, le=100)


class ChatMutation(StrictModel):
    expected_revision: StrictInt = Field(ge=1)


class ChatReply(ChatMutation):
    alias_id: Optional[uuid.UUID]=None
    content: str = Field(min_length=1, max_length=4000)
    client_message_id: uuid.UUID


class MemberAdd(StrictModel):
    email: str = Field(min_length=3, max_length=320)
