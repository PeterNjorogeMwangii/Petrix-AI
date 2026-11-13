import uuid
from copy import deepcopy
from typing import Dict, List, Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from app.agent_schemas import LANGUAGE_CODES, StrictModel
from app.workflows.context import validate_variable_name

DEFAULT_REQUEST = "How would you rate your conversation?"
DEFAULT_SUBMISSION = "Thank you for your feedback!"


def feedback_variable(name):
    validate_variable_name(name)
    if name == "language": raise ValueError("Choose a feedback variable other than language.")
    return name


class FeedbackField(StrictModel):
    id: uuid.UUID
    reply_type: Literal["open_response", "number", "email", "phone", "choice"] = "open_response"
    required: StrictBool = True
    variable_name: str = Field(min_length=1, max_length=64)

    @field_validator("variable_name")
    @classmethod
    def valid_name(cls, value): return feedback_variable(value)


class FeedbackFieldTranslation(StrictModel):
    question: str = Field(min_length=1, max_length=1000)
    placeholder: str = Field(default="", max_length=200)
    choices: List[str] = Field(default_factory=list, max_length=13)

    @field_validator("choices")
    @classmethod
    def distinct_choices(cls, values):
        if any(not value or len(value) > 100 for value in values) or len(set(values)) != len(values):
            raise ValueError("Use up to 13 distinct, nonempty choices of 100 characters each.")
        return values


class FeedbackTranslation(StrictModel):
    webchat_rating_title: str = Field(default="", max_length=500)
    rating_request_message: str = Field(default="", max_length=4000)
    submission_text: str = Field(default="", max_length=4000)
    fields: Dict[uuid.UUID, FeedbackFieldTranslation] = Field(default_factory=dict, max_length=50)


class CollectFeedbackConfig(StrictModel):
    rating_enabled: StrictBool = True
    rating_variable_name: str = Field(default="feedback_rating", min_length=1, max_length=64)
    survey_enabled: StrictBool = False
    fields: List[FeedbackField] = Field(default_factory=list, max_length=50)
    translations: Dict[str, FeedbackTranslation] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def upgrade_rating_only_config(cls, value):
        # Version 0.12 documents gain defaults on save/use without a migration.
        if isinstance(value, dict) and not any(key in value for key in ("rating_variable_name", "survey_enabled", "fields")):
            value = deepcopy(value)
            translations = value.get("translations")
            if isinstance(translations, dict):
                for translation in translations.values():
                    if isinstance(translation, dict):
                        translation.setdefault("rating_request_message", DEFAULT_REQUEST)
                        translation.setdefault("submission_text", DEFAULT_SUBMISSION)
        return value

    @field_validator("rating_variable_name")
    @classmethod
    def valid_name(cls, value): return feedback_variable(value)

    @field_validator("translations")
    @classmethod
    def supported_languages(cls, values):
        if any(code not in LANGUAGE_CODES for code in values): raise ValueError("Choose a supported translation language.")
        return values

    @model_validator(mode="after")
    def complete_settings(self):
        ids = [field.id for field in self.fields]
        names = [field.variable_name for field in self.fields]
        if len(set(ids)) != len(ids): raise ValueError("Each feedback field must have a unique ID.")
        if self.rating_enabled: names.append(self.rating_variable_name)
        if len(set(names)) != len(names): raise ValueError("Use a different chat variable for the rating and each survey field.")
        for translation in self.translations.values():
            if set(translation.fields) - set(ids): raise ValueError("Survey translations must refer to an existing feedback field.")
        if self.survey_enabled and not self.fields: raise ValueError("Add at least one survey field or disable the survey.")
        if self.rating_enabled or self.survey_enabled:
            if not self.translations: raise ValueError("Configure at least one feedback language.")
            for translation in self.translations.values():
                if self.rating_enabled and (not translation.webchat_rating_title or not translation.rating_request_message):
                    raise ValueError("Enter a Webchat rating title and rating request message for each configured feedback language.")
                if not translation.submission_text: raise ValueError("Enter feedback submission text for each configured language.")
        if self.survey_enabled:
            for field in self.fields:
                texts = [translation.fields[field.id] for translation in self.translations.values() if field.id in translation.fields]
                if not texts: raise ValueError("Enter a question for each survey field in at least one language.")
                if field.reply_type == "choice" and any(not text.choices for text in texts):
                    raise ValueError("Add at least one choice for every translated multiple-choice field.")
        return self
