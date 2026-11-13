import uuid
from typing import Dict, Literal, Optional

from pydantic import Field, field_validator, model_validator

from app.agent_schemas import LANGUAGE_CODES, StrictModel
from app.workflows.actions.send_attachment.files import link_metadata
from app.workflows.chat_script import parse_expression


class AttachmentTranslation(StrictModel):
    file_format: Literal["image", "audio", "video", "document"] = "image"
    source: Literal["upload", "link", "script"] = "upload"
    asset_id: Optional[uuid.UUID] = None
    url: str = Field(default="", max_length=2048)
    chat_script: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def valid_source(self):
        if self.source == "upload":
            if self.asset_id is None: raise ValueError("Upload a file before saving this attachment.")
            self.url = ""; self.chat_script = ""
        elif self.source == "link":
            link_metadata(self.url, self.file_format)
            self.asset_id = None; self.chat_script = ""
        else:
            parse_expression(self.chat_script)
            self.asset_id = None; self.url = ""
        return self


class SendAttachmentConfig(StrictModel):
    translations: Dict[str, AttachmentTranslation] = Field(min_length=1, max_length=20)

    @field_validator("translations")
    @classmethod
    def supported_languages(cls, values):
        if any(code not in LANGUAGE_CODES for code in values): raise ValueError("Choose a supported translation language.")
        return values
