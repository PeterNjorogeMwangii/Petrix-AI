from pydantic import Field, field_validator

from app.agent_schemas import StrictModel
from app.workflows.chat_script import parse_expression
from app.workflows.context import validate_variable_name


class SetVariableConfig(StrictModel):
    variable_name: str = Field(min_length=1, max_length=64)
    chat_script: str = Field(min_length=1, max_length=2000)

    @field_validator("variable_name")
    @classmethod
    def valid_name(cls, value):
        return validate_variable_name(value)

    @field_validator("chat_script")
    @classmethod
    def valid_script(cls, value):
        parse_expression(value)
        return value
