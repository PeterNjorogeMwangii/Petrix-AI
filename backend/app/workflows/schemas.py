import uuid
from typing import Dict, List, Literal, Optional

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from app.agent_schemas import LANGUAGE_CODES, StrictModel


class BranchPath(StrictModel):
    id: uuid.UUID
    children: List["WorkflowNode"] = Field(default_factory=list, max_length=100)


class WorkflowNode(StrictModel):
    id: uuid.UUID
    # Optional on input for older clients. Only the server assigns numbers.
    number: Optional[StrictInt] = Field(default=None, ge=1, le=2147483647)
    kind: Literal["trigger", "action"]
    type: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    enabled: StrictBool = True
    config: dict = Field(default_factory=dict)
    children: List["WorkflowNode"] = Field(default_factory=list, max_length=100)
    paths: List[BranchPath] = Field(default_factory=list, max_length=11)


class SaveWorkflows(StrictModel):
    expected_revision: int = Field(ge=1)
    nodes: List[WorkflowNode] = Field(max_length=100)


class MessageTriggerConfig(StrictModel):
    examples: Dict[str, List[str]] = Field(default_factory=dict, max_length=20)
    match_mode: Literal["approximate", "exact"] = "approximate"
    threshold: float = Field(default=0.65, ge=0.3, le=1.0, allow_inf_nan=False)

    @field_validator("examples")
    @classmethod
    def validate_examples(cls, values):
        for code, examples in values.items():
            if code not in LANGUAGE_CODES or len(examples) > 30 or any(not value.strip() or len(value) > 500 for value in examples):
                raise ValueError("Use supported languages and up to 30 non-empty examples of 500 characters per language.")
        return {code: list(dict.fromkeys(value.strip() for value in examples)) for code, examples in values.items()}


class AttachmentTriggerConfig(StrictModel):
    attachment_types: List[Literal["image", "audio", "video", "file"]] = Field(default_factory=lambda: ["image", "audio", "video", "file"], min_length=1, max_length=4)


class EmptyConfig(StrictModel):
    pass


class AttachmentEvent(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    media_type: Literal["image", "audio", "video", "file"]


class TesterEvent(StrictModel):
    client_event_id: Optional[uuid.UUID] = None
    event: Literal["chat_started", "message_received", "attachment_received", "chat_closed", "collect_feedback", "chat_timeout", "ai_timeout"]
    conversation_id: Optional[uuid.UUID] = None
    language: Optional[str] = None
    content: str = Field(default="", max_length=4000)
    attachment: Optional[AttachmentEvent] = None
    rating: Optional[StrictInt] = Field(default=None, ge=1, le=5)
    survey_answer_token: Optional[uuid.UUID] = None
    feedback_token: Optional[uuid.UUID] = None
    feedback_answers: Dict[uuid.UUID, str] = Field(default_factory=dict, max_length=50)
    channel: Optional[Literal["webchat", "messaging", "bitrix24", "africas_talking", "whatsapp"]] = None

    @field_validator("feedback_answers")
    @classmethod
    def bounded_feedback_answers(cls, values):
        if any(len(value) > 4000 for value in values.values()): raise ValueError("Use up to 4,000 characters per feedback answer.")
        return values

    @model_validator(mode="after")
    def event_fields(self):
        if self.event == "chat_started" and self.conversation_id:
            raise ValueError("Restart creates a new conversation.")
        if self.event != "chat_started" and not self.conversation_id:
            raise ValueError("Start a tester conversation first.")
        if self.event == "message_received" and not self.content:
            raise ValueError("Enter a message.")
        if self.event == "attachment_received" and not self.attachment:
            raise ValueError("Provide attachment metadata for this test event.")
        return self


WorkflowNode.model_rebuild()
BranchPath.model_rebuild()
