import uuid
from typing import Literal

from pydantic import Field, StrictInt, field_validator, model_validator

from app.agent_schemas import StrictModel
from app.database_connections.queries import prepare_query
from app.workflows.context import validate_variable_name


class SQLQueryConfig(StrictModel):
    connection_id: uuid.UUID
    query: str = Field(min_length=1, max_length=8000)
    query_mode: Literal["template", "expression"] = "template"
    timeout_seconds: StrictInt = Field(default=30, ge=1, le=120)
    variable_name: str = Field(min_length=1, max_length=64)
    success_path_id: uuid.UUID
    error_path_id: uuid.UUID
    timeout_path_id: uuid.UUID

    @field_validator("variable_name")
    @classmethod
    def custom_variable(cls, value):
        validate_variable_name(value)
        if value == "language": raise ValueError("Use a custom variable name instead of language.")
        return value

    @model_validator(mode="after")
    def valid_query(self):
        prepare_query(self.query, self.query_mode)
        if len({self.success_path_id, self.error_path_id, self.timeout_path_id}) != 3:
            raise ValueError("Success, Error, and Timeout must have different path IDs.")
        return self
