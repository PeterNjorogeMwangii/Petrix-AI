import uuid
from typing import List

from pydantic import Field, field_validator, model_validator

from app.agent_schemas import StrictModel
from app.workflows.chat_script import parse_expression


class BranchCondition(StrictModel):
    id: uuid.UUID
    name: str = Field(min_length=1, max_length=100)
    chat_script: str = Field(min_length=1, max_length=2000)

    @field_validator("chat_script")
    @classmethod
    def valid_script(cls, value):
        parse_expression(value)
        return value


class BranchConfig(StrictModel):
    conditions: List[BranchCondition] = Field(min_length=1, max_length=10)
    else_path_id: uuid.UUID

    @model_validator(mode="after")
    def unique_paths(self):
        ids = [condition.id for condition in self.conditions] + [self.else_path_id]
        if len(set(ids)) != len(ids):
            raise ValueError("Each condition and Else path must have a unique ID.")
        return self
