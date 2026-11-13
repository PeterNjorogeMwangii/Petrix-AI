import uuid
from typing import List
from pydantic import Field, field_validator, model_validator
from app.agent_schemas import StrictModel
from app.workflows.context import validate_variable_name


class AutoFillFieldConfig(StrictModel):
    field_ids: List[uuid.UUID] = Field(min_length=1,max_length=20)
    variable_name: str = Field(min_length=1,max_length=64)
    success_path_id: uuid.UUID
    error_path_id: uuid.UUID

    @field_validator('variable_name')
    @classmethod
    def valid_name(cls,value):
        validate_variable_name(value)
        if value=='language':raise ValueError('Use a custom result variable instead of language.')
        return value

    @model_validator(mode='after')
    def distinct_ids(self):
        if len(set(self.field_ids))!=len(self.field_ids):raise ValueError('Select each custom field once.')
        if self.success_path_id==self.error_path_id:raise ValueError('Success and Error need separate path IDs.')
        return self
