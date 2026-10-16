import uuid
from typing import List, Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from app.agent_schemas import StrictModel
from app.workflows.chat_script import ScriptError
from app.workflows.context import validate_variable_name


class SurveyField(StrictModel):
    id: uuid.UUID
    question: str = Field(min_length=1, max_length=4000)
    answer_type: Literal["text", "number", "attachment", "attachments", "email", "phone"] = "text"
    invalid_answer_reply: str = Field(min_length=1, max_length=4000)
    variable_name: str = Field(min_length=1, max_length=64)
    quick_answers: List[str] = Field(default_factory=list, max_length=13)
    enable_skip: StrictBool = False

    @field_validator("variable_name")
    @classmethod
    def custom_name(cls, name):
        try:
            validate_variable_name(name)
            if name == "language":
                raise ScriptError("Use a custom variable name instead of the system language variable.")
        except ScriptError as error:
            raise ValueError(str(error)) from None
        return name

    @field_validator("quick_answers")
    @classmethod
    def validate_answers(cls, answers):
        values = [value.strip() for value in answers]
        if any(not value or len(value) > 100 for value in values):
            raise ValueError("Quick answers must contain 1–100 characters.")
        if len(set(values)) != len(values):
            raise ValueError("Quick answers must be unique.")
        return values


class AskSurveyConfig(StrictModel):
    fields: List[SurveyField] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def distinct_fields(self):
        if len({field.id for field in self.fields}) != len(self.fields):
            raise ValueError("Each survey field must have a unique ID.")
        if len({field.variable_name for field in self.fields}) != len(self.fields):
            raise ValueError("Use a different variable name for each survey field.")
        return self
