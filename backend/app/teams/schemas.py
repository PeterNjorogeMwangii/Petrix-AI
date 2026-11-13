import uuid
from typing import List, Optional
from pydantic import Field, StrictInt, model_validator
from app.agent_schemas import StrictModel
from app.teams.matching import normalize


class TeamCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)
    alternative_names: List[str] = Field(default_factory=list, max_length=50)
    member_ids: Optional[List[uuid.UUID]] = Field(default=None,max_length=200)

    @model_validator(mode='after')
    def valid_names(self):
        if self.member_ids is not None and len(set(self.member_ids))!=len(self.member_ids):
            raise ValueError("Select each team member once.")
        if not normalize(self.name):
            raise ValueError('Enter a team name containing letters or numbers.')
        names = [self.name] + self.alternative_names
        if any(not normalize(name) or len(name) > 100 for name in names):
            raise ValueError('Each alternative name needs 1–100 characters and must contain letters or numbers.')
        if len({normalize(name) for name in names}) != len(names):
            raise ValueError('Use distinct team and alternative names, ignoring case and punctuation.')
        return self


class TeamUpdate(TeamCreate):
    expected_revision: StrictInt = Field(ge=1)


class TeamDelete(StrictModel):
    expected_revision: StrictInt = Field(ge=1)
