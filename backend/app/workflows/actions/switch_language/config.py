from pydantic import Field, field_validator

from app.agent_schemas import LANGUAGE_CODES, StrictModel


class SwitchLanguageConfig(StrictModel):
    target_language: str = Field(min_length=2, max_length=16)

    @field_validator("target_language")
    @classmethod
    def valid_language(cls, value):
        if value not in LANGUAGE_CODES:
            raise ValueError("Choose a supported language code configured in this agent's settings.")
        return value
