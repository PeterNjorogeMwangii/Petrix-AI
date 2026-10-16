from typing import Dict, List

from pydantic import Field, field_validator

from app.agent_schemas import LANGUAGE_CODES, StrictModel


class Translation(StrictModel):
    messages: List[str] = Field(min_length=1, max_length=10)
    quick_replies: List[str] = Field(default_factory=list, max_length=13)

    @field_validator("messages", "quick_replies")
    @classmethod
    def validate_text(cls, values, info):
        maximum = 4000 if info.field_name == "messages" else 100
        values = [value.strip() for value in values]
        if any(not value or len(value) > maximum for value in values):
            raise ValueError("Messages must contain 1–4,000 characters; quick replies 1–100 characters.")
        if info.field_name == "quick_replies" and len(set(values)) != len(values):
            raise ValueError("Quick replies must be unique.")
        return values


class SendMessageConfig(StrictModel):
    translations: Dict[str, Translation] = Field(min_length=1, max_length=20)

    @field_validator("translations")
    @classmethod
    def supported_languages(cls, values):
        if any(code not in LANGUAGE_CODES for code in values):
            raise ValueError("Choose a supported translation language.")
        return values
