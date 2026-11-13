import uuid
from typing import List, Literal
from pydantic import Field, StrictBool, field_validator, model_validator
from app.agent_schemas import StrictModel
from app.workflows.context import validate_variable_name


class LiveAgentHandoffConfig(StrictModel):
    availability_check: StrictBool = False
    assignment_mode: Literal['all', 'specific'] = 'all'
    user_ids: List[uuid.UUID] = Field(default_factory=list, max_length=50)
    team_ids: List[uuid.UUID] = Field(default_factory=list, max_length=20)
    team_variable: str = Field(default='', max_length=64)
    assign_by_language: StrictBool = False
    closed_path_id: uuid.UUID
    unavailable_path_id: uuid.UUID

    @field_validator('team_variable')
    @classmethod
    def valid_variable(cls, value):
        if value: validate_variable_name(value)
        return value

    @model_validator(mode='after')
    def valid_assignment(self):
        if self.closed_path_id == self.unavailable_path_id:
            raise ValueError('Handoff follow-up paths need distinct IDs.')
        if len(set(self.user_ids))!=len(self.user_ids) or len(set(self.team_ids))!=len(self.team_ids):
            raise ValueError('Select each agent or team once.')
        if self.assignment_mode=='specific' and not (self.user_ids or self.team_ids or self.team_variable):
            raise ValueError('Choose at least one live agent, team, or matched-team variable.')
        if self.assignment_mode=='all':
            self.user_ids=[];self.team_ids=[];self.team_variable=''
        return self
