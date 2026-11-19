import uuid
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

LANGUAGES = [
    ("en", "English"), ("sw", "Swahili"), ("fr", "French"), ("pt", "Portuguese"),
    ("ar", "Arabic"), ("es", "Spanish"), ("de", "German"), ("it", "Italian"),
    ("zh", "Chinese"), ("hi", "Hindi"), ("ja", "Japanese"), ("ko", "Korean"),
    ("nl", "Dutch"), ("ru", "Russian"), ("tr", "Turkish"), ("am", "Amharic"),
    ("rw", "Kinyarwanda"), ("lg", "Luganda"), ("zu", "Zulu"), ("af", "Afrikaans"),
]
LANGUAGE_CODES = {code for code, _ in LANGUAGES}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AgentName(StrictModel):
    name: str = Field(min_length=1, max_length=100)


class AgentCreate(AgentName):
    default_language: str = "en"

    @field_validator("default_language")
    @classmethod
    def valid_language(cls, value):
        if value not in LANGUAGE_CODES:
            raise ValueError("Choose a supported language.")
        return value


class Revision(StrictModel):
    expected_revision: int = Field(ge=1)


class Personalization(Revision):
    llm_enabled: StrictBool
    persona: str = Field(max_length=12000)


class Languages(Revision):
    languages: List[str] = Field(min_length=1, max_length=20)
    default_language: str

    @model_validator(mode="after")
    def valid_languages(self):
        if len(set(self.languages)) != len(self.languages) or any(code not in LANGUAGE_CODES for code in self.languages):
            raise ValueError("Select unique supported languages.")
        if self.default_language not in self.languages:
            raise ValueError("Default language must be one of the selected languages.")
        return self


class Voice(Revision):
    speech_to_text_enabled: Optional[StrictBool] = None
    speech_languages: Optional[List[str]] = Field(default=None,max_length=20)
    include_audio_transcript: Optional[StrictBool] = None
    voice_enabled: StrictBool
    voice_name: str = Field(max_length=100)
    voice_speed: float = Field(ge=0.5, le=2.0, allow_inf_nan=False)


class AgentConfiguration(AgentName):
    speech_to_text_enabled: StrictBool = False
    speech_languages: List[str] = Field(default_factory=list,max_length=20)
    include_audio_transcript: StrictBool = True
    languages: List[str] = Field(min_length=1, max_length=20)
    default_language: str
    llm_enabled: StrictBool = True
    persona: str = Field(default="", max_length=12000)
    voice_enabled: StrictBool = False
    voice_name: str = Field(default="", max_length=100)
    voice_speed: float = Field(default=1.0, ge=0.5, le=2.0, allow_inf_nan=False)
    avatar_base64: Optional[str] = Field(default=None, max_length=14000000)

    @model_validator(mode="after")
    def valid_languages(self):
        Languages(expected_revision=1, languages=self.languages, default_language=self.default_language)
        if any(v not in self.languages for v in self.speech_languages) or len(set(self.speech_languages))!=len(self.speech_languages):raise ValueError("Speech languages must be selected agent languages.")
        return self


class AgentImportWorkflows(StrictModel):
    nodes: List[dict] = Field(max_length=100)


class AgentImportPDF(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    filename: str = Field(min_length=1, max_length=250)
    data_base64: str = Field(min_length=1, max_length=14000000)
    enabled: StrictBool = True

    @field_validator('name', 'filename')
    @classmethod
    def printable(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError('Use printable names without control characters.')
        return value

    @field_validator('filename')
    @classmethod
    def pdf_filename(cls, value):
        if '/' in value or '\\' in value or not value.lower().endswith('.pdf'):
            raise ValueError('Use a PDF filename without a directory path.')
        return value


class AgentExport(StrictModel):
    format: Literal["petrix-agent"]
    schema_version: Literal[1]
    agent: AgentConfiguration
    workflows: Optional[AgentImportWorkflows] = None
    knowledge_files: List[AgentImportPDF] = Field(default_factory=list, max_length=5)


class AgentDelete(Revision):
    confirmation_name: str


class NetworkCreate(StrictModel):
    target_id: uuid.UUID


class RoleSettings(StrictModel):
    admin: StrictBool
    member: StrictBool


class TesterMessage(StrictModel):
    conversation_id: Optional[uuid.UUID] = None
    content: str = Field(min_length=1, max_length=4000)
