import uuid
from typing import Literal
from pydantic import Field, field_validator, model_validator
from app.agent_schemas import StrictModel
from app.workflows.chat_script import parse_expression
from app.workflows.context import validate_variable_name


class AutoTeamMatchConfig(StrictModel):
    input: str = Field(min_length=1, max_length=16000)
    input_mode: Literal['template', 'expression'] = 'template'
    variable_name: str = Field(min_length=1, max_length=64)
    match_path_id: uuid.UUID
    no_match_path_id: uuid.UUID
    error_path_id: uuid.UUID

    @field_validator('variable_name')
    @classmethod
    def valid_name(cls, value):
        validate_variable_name(value)
        if value == 'language':
            raise ValueError('Use a custom team result variable instead of language.')
        return value

    @model_validator(mode='after')
    def valid_settings(self):
        if not self.input.strip():
            raise ValueError('Enter the text to match or a chat variable.')
        if len({self.match_path_id, self.no_match_path_id, self.error_path_id}) != 3:
            raise ValueError('Match found, No match, and Error need separate path IDs.')
        if self.input_mode == 'expression':
            parse_expression(self.input)
        return self
