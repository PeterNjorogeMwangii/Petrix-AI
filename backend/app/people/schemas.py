import uuid
from typing import Optional
from pydantic import Field, StrictInt, model_validator, field_validator
from app.agent_schemas import StrictModel

class NoteBody(StrictModel):
    content: str = Field(min_length=1, max_length=8000)
    color: str = Field(default='#fff0b3', pattern=r'^#[0-9a-fA-F]{6}$')
    expected_revision: Optional[StrictInt] = Field(default=None, ge=1)
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
        if len(self.ids) != len(set(self.ids)) or set(self.ids) != set(self.revisions) or any(v < 1 for v in self.revisions.values()):
            raise ValueError('Include the current revision of each selected person once.')
        return self
