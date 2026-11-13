import uuid
from typing import Dict, List, Literal, Optional
from urllib.parse import urlsplit

from pydantic import Field, StrictBool, field_validator, model_validator

from app.agent_schemas import LANGUAGE_CODES, StrictModel
from app.workflows.actions.send_attachment.files import link_metadata


def validate_link(value):
    try:
        parts = urlsplit(value)
        if len(value) > 2048 or not value.lower().startswith(("http://", "https://")) or not parts.hostname:
            raise ValueError()
        if parts.username is not None or parts.password is not None or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError()
        if any(char in value for char in "\\{}"): raise ValueError()
        if parts.port is not None and not 1 <= parts.port <= 65535: raise ValueError()
        parts.hostname.encode("idna")
    except (ValueError, UnicodeError):
        raise ValueError("Enter a complete HTTP or HTTPS webpage URL without credentials or placeholders.") from None
    return value


class CarouselImage(StrictModel):
    source: Literal["upload", "link"]
    asset_id: Optional[uuid.UUID] = None
    url: str = Field(default="", max_length=2048)

    @model_validator(mode="after")
    def valid_image(self):
        if self.source == "upload":
            if self.asset_id is None: raise ValueError("Upload the card image or choose No image.")
            self.url = ""
        else:
            link_metadata(self.url, "image")
            self.asset_id = None
        return self


class CarouselButton(StrictModel):
    id: uuid.UUID
    label: str = Field(min_length=1, max_length=80)
    type: Literal["reply", "link"] = "reply"
    message: str = Field(default="", max_length=1000)
    url: str = Field(default="", max_length=2048)
    webview: StrictBool = False

    @model_validator(mode="after")
    def valid_action(self):
        if self.type == "reply":
            if not self.message: raise ValueError("Enter the message sent when this button is selected.")
            self.url = ""; self.webview = False
        else:
            validate_link(self.url)
            self.message = ""
        return self


class CarouselCard(StrictModel):
    id: uuid.UUID
    title: str = Field(min_length=1, max_length=80)
    subtitle: str = Field(default="", max_length=200)
    image: Optional[CarouselImage] = None
    buttons: List[CarouselButton] = Field(default_factory=list, max_length=3)


class CarouselTranslation(StrictModel):
    cards: List[CarouselCard] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [value for card in self.cards for value in [card.id, *[button.id for button in card.buttons]]]
        if len(ids) != len(set(ids)): raise ValueError("Each card and button needs its own unique ID within this language.")
        return self


class ShowCarouselConfig(StrictModel):
    translations: Dict[str, CarouselTranslation] = Field(min_length=1, max_length=20)

    @field_validator("translations")
    @classmethod
    def supported_languages(cls, values):
        if any(code not in LANGUAGE_CODES for code in values): raise ValueError("Choose a supported translation language.")
        return values
