import uuid
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from app.agent_schemas import StrictModel
from app.workflows.chat_script import parse_expression
from app.workflows.context import validate_variable_name


class LLMProcessingConfig(StrictModel):
    input: str = Field(min_length=1, max_length=16000)
    input_mode: Literal["template", "expression"] = "template"
    model: Literal["intent"] = "intent"
    temperature: float = Field(default=1.0, ge=0, le=2, allow_inf_nan=False)
    system_prompt: str = Field(default="You are a helpful assistant.", max_length=8000)
    system_prompt_mode: Literal["template", "expression"] = "template"
    use_knowledge_info: StrictBool = False
    output_format: Literal["text", "json"] = "text"
    variable_name: str = Field(min_length=1, max_length=64)
    timeout_seconds: StrictInt = Field(default=30, ge=1, le=120)
    success_path_id: uuid.UUID
    error_path_id: uuid.UUID
    timeout_path_id: uuid.UUID

    @field_validator("temperature", mode="before")
    @classmethod
    def numeric_temperature(cls, value):
        if type(value) not in {int, float}: raise ValueError("Use a number from 0.0 to 2.0 for temperature.")
        return value

    @field_validator("variable_name")
    @classmethod
    def valid_name(cls, value):
        validate_variable_name(value)
        if value == "language": raise ValueError("Use a custom response variable instead of language.")
        return value

    @model_validator(mode="after")
    def valid_paths_and_scripts(self):
        if len({self.success_path_id, self.error_path_id, self.timeout_path_id}) != 3:
            raise ValueError("Success, Error, and Timeout need different path IDs.")
        for source, mode in [(self.input, self.input_mode), (self.system_prompt, self.system_prompt_mode)]:
            if mode == "expression": parse_expression(source)
        return self
