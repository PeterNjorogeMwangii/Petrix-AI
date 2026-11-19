import uuid
from typing import Dict, List, Literal, Optional
from pydantic import Field, StrictInt, model_validator
from app.agent_schemas import StrictModel

Status = Literal['open', 'pending', 'overdue', 'resolved', 'closed']

class DetailsBody(StrictModel):
    expected_revision: StrictInt = Field(ge=1)
    description: str = Field(max_length=4000)
    status: Status

class TransferBody(StrictModel):
    expected_revision: StrictInt = Field(ge=1)
    user_id: Optional[uuid.UUID] = None
    team_ids: List[uuid.UUID] = Field(default_factory=list, max_length=20)
    @model_validator(mode='after')
    def target(self):
        if not self.user_id and not self.team_ids:
            raise ValueError('Select a live agent or at least one team.')
        if len(set(self.team_ids)) != len(self.team_ids):
            raise ValueError('Select unique teams.')
        return self

class NoteBody(StrictModel):
    content: str = Field(min_length=1, max_length=4000)
    color: str = Field(default='#fff0b3', pattern=r'^#[0-9a-fA-F]{6}$')
    expected_revision: Optional[StrictInt] = Field(default=None, ge=1)

class NoteRevision(StrictModel):
    expected_revision: StrictInt = Field(ge=1)

class DeleteBody(StrictModel):
    ids: List[uuid.UUID] = Field(min_length=1, max_length=100)
    revisions: Dict[uuid.UUID, StrictInt]
    confirmation: Literal['DELETE']
    @model_validator(mode='after')
    def selection(self):
        if len(set(self.ids)) != len(self.ids) or set(self.ids) != set(self.revisions) or any(v < 1 for v in self.revisions.values()):
            raise ValueError('Send one current revision for each selected livechat.')
        return self
